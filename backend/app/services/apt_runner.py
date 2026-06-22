"""Apptainer binary resolver + thin subprocess wrapper.

This is the single place in the backend that decides *which* ``apptainer``
binary to run. The resolution order mirrors
``deploy/apptainer/_common.sh::resolve_apptainer`` exactly so the CLI
bootstrap and Python services agree on the binary in use:

1. ``HEAXHUB_APPT_BIN`` env var (operator override) if executable.
2. Newest ``deploy/apptainer/.tools/apptainer-*/usr/bin/apptainer`` (the
   pinned install produced by ``install-apptainer.sh``).
3. ``/usr/local/bin/apptainer`` if executable.
4. ``shutil.which("apptainer")`` — host PATH fallback for dev workstations.

When nothing resolves, :func:`local_apptainer_path` raises
``FileNotFoundError`` so callers fail loud instead of silently shelling out
to a non-existent command.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Mapping

from app.core.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


# Project root = three levels up from backend/app/services/. Same convention
# as integration_launcher / integrations_scanner.
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_TOOLS_DIR: Path = _REPO_ROOT / "deploy" / "apptainer" / ".tools"


# Natural-sort key for ``apptainer-1.3.6`` style directory names so 1.3.10
# sorts above 1.3.6 (lexical sort would invert that).
_NUM_RE = re.compile(r"(\d+)")


def _version_key(p: Path) -> list[object]:
    # Operate on the dir name (e.g. "apptainer-1.3.6"). Split into [str, int,
    # str, int, ...] tuples so numeric chunks compare numerically.
    parts = _NUM_RE.split(p.name)
    out: list[object] = []
    for chunk in parts:
        if chunk.isdigit():
            out.append((1, int(chunk)))
        else:
            out.append((0, chunk))
    return out


def _executable(p: Path | str) -> bool:
    try:
        return Path(p).is_file() and os.access(str(p), os.X_OK)
    except OSError:
        return False


def local_apptainer_path() -> str:
    """Resolve the apptainer binary to use.

    Returns its absolute path as a string. Raises ``FileNotFoundError`` if
    none of the four candidates resolve to an executable file.
    """
    # 1) explicit operator override
    override = os.environ.get("HEAXHUB_APPT_BIN", "").strip()
    if override and _executable(override):
        return str(Path(override).resolve())

    # 2) pinned install under deploy/apptainer/.tools/apptainer-*/usr/bin/
    if _TOOLS_DIR.is_dir():
        candidates: list[Path] = []
        # Each pinned install lives at .tools/apptainer-<ver>/usr/bin/apptainer.
        for child in _TOOLS_DIR.iterdir():
            if not child.is_dir() or not child.name.startswith("apptainer-"):
                continue
            cand = child / "usr" / "bin" / "apptainer"
            if _executable(cand):
                candidates.append(child)
        if candidates:
            newest = sorted(candidates, key=_version_key)[-1]
            return str((newest / "usr" / "bin" / "apptainer").resolve())

    # 3) /usr/local/bin/apptainer
    if _executable("/usr/local/bin/apptainer"):
        return "/usr/local/bin/apptainer"

    # 4) host PATH
    via_path = shutil.which("apptainer")
    if via_path:
        return via_path

    raise FileNotFoundError(
        "apptainer binary not found. Run "
        "`bash deploy/apptainer/install-apptainer.sh` to install a pinned "
        "version into deploy/apptainer/.tools/, install apptainer 1.3.x "
        "system-wide, or set HEAXHUB_APPT_BIN to an absolute path."
    )


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run ``apptainer <args...>`` via :func:`subprocess.run`.

    Extra kwargs (cwd, env, timeout, capture_output, check, ...) are
    forwarded as-is. Returns the ``CompletedProcess``. Callers handle
    non-zero returncodes — we don't ``check=True`` by default so the caller
    can inspect stderr.
    """
    binary = local_apptainer_path()
    cmd = [binary, *args]
    logger.debug("apt_runner.run: %s", cmd)
    return subprocess.run(cmd, **kwargs)


def run_build(
    sif_out: Path | str,
    def_in: Path | str,
    *,
    fakeroot: bool = True,
    force: bool = True,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Build a SIF from a definition file.

    Equivalent to ``apptainer build [--force] [--fakeroot] <sif_out> <def_in>``.
    ``--fakeroot`` lets unprivileged users build; ``--force`` overwrites an
    existing SIF in-place.
    """
    args: list[str] = ["build"]
    if force:
        args.append("--force")
    if fakeroot:
        args.append("--fakeroot")
    args.extend([str(sif_out), str(def_in)])
    return run(args, **kwargs)


def _bind_flags(binds: Iterable[tuple[str, str]]) -> list[str]:
    """Convert ``[(host, container), ...]`` into ``["--bind", "h:c", ...]``."""
    out: list[str] = []
    for host, container in binds:
        out.extend(["--bind", f"{host}:{container}"])
    return out


def instance_start(
    sif: Path | str,
    name: str,
    binds: Iterable[tuple[str, str]] = (),
    *,
    cleanenv: bool = True,
    env: Mapping[str, str] | None = None,
    memory: str | None = None,
    cpus: str | None = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Start a persistent apptainer instance.

    Equivalent to::

        APPTAINERENV_<K>=<V> apptainer instance start [--cleanenv] \
            [--memory <bytes>] [--cpus <n>] [--bind host:container ...] \
            <sif> <name>

    Env vars in ``env`` are prefixed with ``APPTAINERENV_`` so they land
    inside the container at startup (this is how apptainer forwards env
    when ``--cleanenv`` is active).

    SRV-04: ``memory`` (e.g. ``"1024m"``) and ``cpus`` (e.g. ``"1.5"``) apply a
    cgroup limit so one runaway app can't take the shared host down. Only passed
    when ``settings.enforce_instance_limits`` is on — the locally-extracted
    apptainer runs with ``systemd cgroups = no`` on some hosts where these flags
    are a no-op/error, so gating keeps the default deploy safe.
    """
    from app.config import get_settings  # noqa: PLC0415 — avoid import cycle

    args: list[str] = ["instance", "start"]
    if cleanenv:
        args.append("--cleanenv")
    if get_settings().enforce_instance_limits:
        if memory:
            args.extend(["--memory", str(memory)])
        if cpus:
            args.extend(["--cpus", str(cpus)])
    args.extend(_bind_flags(binds))
    args.extend([str(sif), name])

    # Merge APPTAINERENV_* into the subprocess env.
    sub_env = dict(os.environ)
    if "env" in kwargs and kwargs["env"] is not None:
        # Caller-supplied env takes precedence over our merge.
        sub_env = dict(kwargs.pop("env"))
    if env:
        for k, v in env.items():
            sub_env[f"APPTAINERENV_{k}"] = str(v)
    kwargs["env"] = sub_env
    return run(args, **kwargs)


def instance_stop(name: str, **kwargs) -> subprocess.CompletedProcess:
    """Stop a running apptainer instance by name (best-effort)."""
    return run(["instance", "stop", name], **kwargs)


def instance_list(**kwargs) -> list[str]:
    """Return the names of currently-running apptainer instances.

    Parses ``apptainer instance list`` output, which has a one-line header
    followed by ``<name>  <pid>  <image>`` rows. On any failure (apptainer
    not installed, parse error, etc.) we return an empty list — callers
    treat that as "no known instances" and cold-start, which is safe.
    """
    try:
        proc = run(
            ["instance", "list"],
            capture_output=True,
            text=True,
            timeout=kwargs.pop("timeout", 5),
            **kwargs,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    if proc.returncode != 0:
        return []
    names: list[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip header line ("INSTANCE NAME    PID    IP    IMAGE").
        if line.upper().startswith("INSTANCE"):
            continue
        first = line.split()[0]
        names.append(first)
    return names


def instance_exec(
    name: str,
    argv: list[str],
    env: Mapping[str, str] | None = None,
    *,
    cleanenv: bool = True,
    cwd: str | Path | None = None,
    **kwargs,
) -> subprocess.Popen:
    """Spawn a command inside a running instance.

    Returns a :class:`subprocess.Popen` so the caller can keep the handle
    for liveness checks / kill semantics — matches how
    ``integration_launcher`` already manages host-mode services.
    """
    args: list[str] = [local_apptainer_path(), "exec"]
    if cleanenv:
        args.append("--cleanenv")
    # Default the in-container working directory to /app (the SIF templates
    # COPY upstream/ → /app, so this matches where source files live).
    # The host-side ``cwd`` kwarg only sets the subprocess cwd before exec,
    # not the in-container pwd; apptainer needs --pwd for that.
    args.extend(["--pwd", "/app"])
    args.append(f"instance://{name}")
    args.extend(argv)

    sub_env = dict(os.environ)
    if env:
        for k, v in env.items():
            sub_env[f"APPTAINERENV_{k}"] = str(v)
            # Also expose unprefixed for callers that pre-merged.
            sub_env.setdefault(k, str(v))

    logger.debug("apt_runner.instance_exec: %s", args)
    return subprocess.Popen(
        args,
        env=sub_env,
        cwd=str(cwd) if cwd else None,
        **kwargs,
    )
