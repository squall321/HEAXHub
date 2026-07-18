"""Idempotent build of an integrations/<slug>/ workspace.

The :mod:`integrations_scanner` decides *what* to register. This module owns
*how* to make a registered integration runnable — installs Python venvs, runs
``pnpm install && pnpm build`` for Node services, etc.

Design notes
------------
* **Idempotent.** Each builder probes for a sentinel (``.heaxhub_build_ok``)
  whose contents are the SHA-256 hash of the manifest's source inputs
  (``pyproject.toml`` + ``requirements*.txt`` for python; ``package.json`` +
  lockfile for node). A rebuild is triggered only when those hashes change,
  so a stray ``touch`` of pyproject.toml does NOT cause a rebuild.
* **No subprocess spawned at request time.** Builders may take minutes; the
  scanner calls :func:`build` in a Celery worker, never inline in uvicorn.
* **Best-effort.** A build failure is logged and surfaced via the return
  value; the caller (scanner) MUST not crash. The App row stays in DB and
  the operator can re-trigger the build.
* **Reads stack from** ``manifest.build.stack`` first (authoritative), then
  falls back to the global ``config/stacks.yaml`` for the runtime/install
  template.
* **Operator-friendly logs.** Each build emits a per-integration log file
  at ``var/logs/build_<slug>.log``; the failing tail is also folded into
  ``BuildResult.error`` so the operator can diagnose without SSH'ing in.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.logger import get_logger
from app.services.stack_resolver import StackSpec, load_stacks
from app.services.toolchain_dispatch import resolve_sif

logger = get_logger(__name__)

# Sentinel file we drop in the integration workspace once a build finishes
# cleanly. Its CONTENT is a SHA-256 hash of the build inputs; we compare the
# stored hash to the freshly-computed one to decide whether a rebuild is
# needed. This avoids the false rebuilds you'd get from a plain mtime check
# when a file is touched but unchanged.
_SENTINEL = ".heaxhub_build_ok"

# Bound build time per integration so a runaway pnpm install doesn't park
# the worker forever. 30 minutes covers a cold pnpm install + next build on
# slow networks (the 10-minute default was too tight in practice). Tweak
# per-host via HEAXHUB_BUILD_TIMEOUT_SECONDS.
_BUILD_TIMEOUT = int(os.environ.get("HEAXHUB_BUILD_TIMEOUT_SECONDS", "1800"))

# Retry policy for transient network failures during pip/pnpm install. The
# delays are deliberately long because most flakes here are upstream registry
# rate-limits, not local issues — short delays just spend retries faster.
_INSTALL_RETRY_DELAYS = (5, 15, 45)

# Per-integration build log lives here. We open it append-binary so concurrent
# rebuilds across workers don't truncate each other's tails.
_REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_DIR: Path = _REPO_ROOT / "var" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# How many trailing bytes of build output to include in BuildResult.error.
# 4 KiB is enough for a stack trace + a few install lines without overwhelming
# the API response that surfaces this string.
_ERROR_TAIL_BYTES = 4096


@dataclass(slots=True)
class BuildResult:
    """Outcome of :func:`build` for a single integration."""

    slug: str
    action: str  # "skipped" | "built" | "failed"
    stack: str | None
    duration_seconds: float
    error: str | None = None
    log_path: str | None = None


def build(workspace: Path, *, manifest: dict[str, Any]) -> BuildResult:
    """Ensure the workspace has its install artifacts ready.

    Returns a :class:`BuildResult` — never raises. The caller decides what to
    do with failure (typically: log + leave App row stable, retry next scan).
    """
    slug = workspace.name
    started = time.monotonic()
    log_path = LOG_DIR / f"build_{slug}.log"

    build_section = manifest.get("build") or {}
    stack_name = (
        build_section.get("stack")
        or build_section.get("type")
        or "unknown"
    )
    spec: StackSpec | None = load_stacks().get(stack_name)
    if spec is None:
        return BuildResult(
            slug=slug,
            action="failed",
            stack=stack_name,
            duration_seconds=time.monotonic() - started,
            error=f"unknown stack '{stack_name}' (see config/stacks.yaml)",
            log_path=str(log_path),
        )

    try:
        if spec.runtime == "python_venv":
            changed = _build_python(workspace, spec, build_section, log_path)
        elif spec.runtime == "nodejs":
            changed = _build_nodejs(workspace, spec, build_section, log_path)
        elif spec.runtime == "native_binary" and spec.builder == "go_toolchain":
            changed = _build_go(workspace, spec, build_section, log_path)
        elif spec.runtime == "native_binary" and spec.builder == "cmake_make":
            changed = _build_cpp(workspace, spec, build_section, log_path)
        elif spec.runtime == "apptainer":
            # SIF images are produced offline; the builder only verifies the
            # declared .sif path exists so a typo / missing image surfaces
            # before the user submits their first job.
            changed = _build_apptainer_sif(workspace, spec, build_section, log_path)
        elif spec.runtime == "r_runtime":
            changed = _build_r(workspace, spec, build_section, log_path)
        elif spec.builder == "dotnet":
            # Heavy host toolchain — refuse to auto-install. Detect dotnet 8 SDK
            # on PATH and surface a clear operator instruction if missing.
            changed = _build_dotnet(workspace, spec, build_section, log_path)
        elif spec.builder == "java_maven":
            # Same policy as dotnet: detect ./mvnw or system mvn + JDK 17, never
            # download Maven/JDK ourselves.
            changed = _build_java_maven(workspace, spec, build_section, log_path)
        elif spec.builder == "rust_cargo":
            # cargo is the entry point; rustup typically provides it. We never
            # bootstrap rustup — just refuse with an explicit instruction.
            changed = _build_rust_cargo(workspace, spec, build_section, log_path)
        elif spec.runtime == "caddy_static":
            # No compile / install — only verify the configured static root
            # directory exists. The launcher registers a Caddy file_server.
            changed = _build_static(workspace, spec, build_section, log_path)
        elif spec.runtime == "windows_agent":
            # Windows installers are produced offline; nothing to build live.
            changed = False
        elif spec.runtime == "noop":
            # External stacks (external_link / external_iframe / external_proxy)
            # have no source to build and no environment to provision. The
            # builder is a deliberate no-op — manifest validation already happened
            # in the scanner, so we just report 'skipped' to mean 'ready'.
            changed = False
        else:
            return BuildResult(
                slug=slug,
                action="failed",
                stack=stack_name,
                duration_seconds=time.monotonic() - started,
                error=f"unsupported runtime '{spec.runtime}' for live build",
                log_path=str(log_path),
            )
    except subprocess.TimeoutExpired as exc:
        return BuildResult(
            slug=slug,
            action="failed",
            stack=stack_name,
            duration_seconds=time.monotonic() - started,
            error=f"build timeout after {_BUILD_TIMEOUT}s: {exc.cmd}",
            log_path=str(log_path),
        )
    except subprocess.CalledProcessError as exc:
        # Prefer the on-disk log tail (which captures everything across retries)
        # over the single exception's stdout, since we re-run failing commands.
        tail = _read_log_tail(log_path) or (exc.stderr or exc.stdout or b"")[-_ERROR_TAIL_BYTES:]
        return BuildResult(
            slug=slug,
            action="failed",
            stack=stack_name,
            duration_seconds=time.monotonic() - started,
            error=f"exit={exc.returncode} cmd={exc.cmd!r}\n--- tail ---\n{_decode(tail)}",
            log_path=str(log_path),
        )
    except _BuildError as exc:
        return BuildResult(
            slug=slug,
            action="failed",
            stack=stack_name,
            duration_seconds=time.monotonic() - started,
            error=str(exc),
            log_path=str(log_path),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("unexpected build error for %s", slug)
        return BuildResult(
            slug=slug,
            action="failed",
            stack=stack_name,
            duration_seconds=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}",
            log_path=str(log_path),
        )

    return BuildResult(
        slug=slug,
        action="built" if changed else "skipped",
        stack=stack_name,
        duration_seconds=time.monotonic() - started,
        log_path=str(log_path),
    )


# ---------------------------------------------------------------------------
# Python venv builder
# ---------------------------------------------------------------------------


class _BuildError(RuntimeError):
    """Raised for operator-visible build failures (clear error message)."""


def _build_python(
    workspace: Path,
    spec: StackSpec,
    build_section: dict[str, Any],
    log_path: Path,
) -> bool:
    """Create .venv + ``pip install -e .``. Returns True when work was done."""
    pyproject = workspace / "pyproject.toml"
    if not pyproject.exists():
        # Nothing to install — caller may still launch a bare script.
        return False

    venv = workspace / ".venv"
    interp = _pick_python(spec, build_section)
    interp_marker = venv / ".heaxhub_python"

    # Refuse if pyproject declares a requires-python the picked interpreter
    # cannot satisfy. We only compare major.minor — patch-level constraints
    # would require a full PEP 440 specifier parser which is overkill here.
    _enforce_python_constraint(pyproject, interp)

    current_hash = _hash_python_inputs(workspace)
    stored_hash = _read_sentinel_hash(workspace / _SENTINEL)
    needs_rebuild = (
        not (venv / "bin" / "python").exists()
        or not interp_marker.exists()
        or interp_marker.read_text(encoding="utf-8").strip() != interp
        or stored_hash != current_hash
    )
    if not needs_rebuild:
        logger.info("python build skipped (up-to-date): %s", workspace)
        return False

    # Recreate the venv on interpreter change to avoid mixed binaries — but
    # only AFTER we've verified the new interpreter actually exists, so we
    # don't eagerly delete a working venv when the operator typo'd a version.
    if venv.exists() and (
        not interp_marker.exists()
        or interp_marker.read_text(encoding="utf-8").strip() != interp
    ):
        if shutil.which(interp) is None:
            raise _BuildError(
                f"refusing to rebuild venv: requested interpreter '{interp}' "
                f"not on PATH; keeping existing .venv at {venv}"
            )
        shutil.rmtree(venv)

    # Probe for a toolchain SIF (heaxhub_toolchain_python312.sif). When
    # present, pip runs *inside* the SIF; when absent, we fall back to host
    # PATH so dev workstations without the SIF still work.
    sif = resolve_sif(spec.name)

    logger.info("creating venv with %s for %s", interp, workspace.name)
    # Venv creation itself uses the host interpreter so the resulting
    # .venv/bin/python is a stable host binary — embedding the SIF's
    # python would break the venv as soon as the SIF is moved/removed.
    _run([interp, "-m", "venv", str(venv)], cwd=workspace, log_path=log_path)
    interp_marker.write_text(interp)

    pip = str(venv / "bin" / "pip")
    _run_with_retry(
        [pip, "install", "--quiet", "--upgrade", "pip"],
        cwd=workspace, log_path=log_path, sif=sif,
    )
    _run_with_retry(
        [pip, "install", "--quiet", "-e", "."],
        cwd=workspace, log_path=log_path, sif=sif,
    )

    _write_sentinel_hash(workspace / _SENTINEL, current_hash)
    return True


def _pick_python(spec: StackSpec, build_section: dict[str, Any]) -> str:
    """Pick the Python interpreter command to use for ``python -m venv``."""
    wanted = (
        build_section.get("python_version")
        or (spec.extra or {}).get("python_version")
        or ""
    )
    candidates: list[str] = []
    if wanted:
        candidates.append(f"python{wanted}")
        major_minor = wanted.split(".")
        if len(major_minor) >= 2:
            candidates.append(f"python{major_minor[0]}.{major_minor[1]}")
    candidates += ["python3.12", "python3.11", "python3"]
    for cand in candidates:
        if shutil.which(cand):
            return cand
    return "python3"


_PY_VERSION_RE = re.compile(r"python\s*([0-9]+)\.([0-9]+)", re.IGNORECASE)


def _enforce_python_constraint(pyproject: Path, interp: str) -> None:
    """If ``requires-python = ">=X.Y"`` is set, refuse incompatible interps.

    We deliberately implement only the common ``>=`` form (which covers ~99%
    of real-world pyprojects) and silently accept anything we can't parse.
    """
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return
    m = re.search(
        r'requires-python\s*=\s*"\s*(?:>=|>)\s*([0-9]+)\.([0-9]+)',
        text,
    )
    if not m:
        return
    want_major, want_minor = int(m.group(1)), int(m.group(2))
    interp_m = _PY_VERSION_RE.search(interp)
    if not interp_m:
        # bare 'python3' — can't tell, let pip catch it.
        return
    have_major, have_minor = int(interp_m.group(1)), int(interp_m.group(2))
    if (have_major, have_minor) < (want_major, want_minor):
        raise _BuildError(
            f"interpreter '{interp}' (python {have_major}.{have_minor}) does "
            f"not satisfy pyproject requires-python >= {want_major}.{want_minor}"
        )


# ---------------------------------------------------------------------------
# Node.js builder
# ---------------------------------------------------------------------------


def _build_nodejs(
    workspace: Path,
    spec: StackSpec,
    build_section: dict[str, Any],
    log_path: Path,
) -> bool:
    """Run ``pnpm install`` + (optional) ``pnpm build``. Returns True when work
    was done."""
    pkg_json = workspace / "package.json"
    if not pkg_json.exists():
        return False

    sentinel = workspace / _SENTINEL
    current_hash = _hash_nodejs_inputs(workspace)
    if (
        _read_sentinel_hash(sentinel) == current_hash
        and (workspace / "node_modules").exists()
    ):
        logger.info("nodejs build skipped (up-to-date): %s", workspace)
        return False

    pnpm = shutil.which("pnpm") or shutil.which("npm")
    if pnpm is None:
        raise FileNotFoundError(
            "pnpm/npm not on PATH — install with `corepack enable && "
            "corepack prepare pnpm@latest --activate`"
        )
    use_pnpm = pnpm.endswith("pnpm")

    install_cmd: list[str]
    if use_pnpm:
        install_cmd = [pnpm, "install", "--frozen-lockfile"]
        if not (workspace / "pnpm-lock.yaml").exists():
            # No lockfile yet — do a regular install so we don't fail hard,
            # but WARN loudly: missing lockfile means non-reproducible builds
            # and is a known supply-chain risk vector.
            logger.warning(
                "SECURITY: %s has no pnpm-lock.yaml; falling back to "
                "non-reproducible `pnpm install`. Commit a lockfile to pin "
                "transitive deps.",
                workspace.name,
            )
            install_cmd = [pnpm, "install"]
    else:
        install_cmd = (
            [pnpm, "ci"]
            if (workspace / "package-lock.json").exists()
            else [pnpm, "install"]
        )

    sif = resolve_sif(spec.name)

    logger.info("running %s in %s", install_cmd, workspace.name)
    _run_with_retry(install_cmd, cwd=workspace, log_path=log_path, sif=sif)

    scripts = json.loads(pkg_json.read_text(encoding="utf-8")).get("scripts", {})
    if "build" in scripts:
        build_cmd = [pnpm, "build"] if use_pnpm else [pnpm, "run", "build"]
        # 앱은 /apps/{id} 로 프록시되고 strip_prefix 로 루트에서 서빙되므로, 프론트 자산은
        # 반드시 '상대 base' 여야 서브패스든 루트든 index.html 기준으로 해석돼 404가 안 난다.
        # vite/rolldown 은 `-- --base=./` 를 항상 존중하고, --cleanenv SIF 안에서도 argv 는
        # 살아남으므로(env 는 안 들어감) base 를 CLI 로 강제한다. 자체 base 를 굽는 스택
        # (Next/Streamlit 등)은 애초 이 노드 빌더가 아닌 strip_prefix=False 경로라 무관.
        if _is_vite_app(workspace, scripts):
            build_cmd = build_cmd + ["--", "--base=./"]
            logger.info("vite app — forcing relative base (--base=./): %s", workspace.name)
        logger.info("running %s in %s", build_cmd, workspace.name)
        _run(build_cmd, cwd=workspace, log_path=log_path, sif=sif)
        _assert_relative_base(workspace, log_path)

    _write_sentinel_hash(sentinel, current_hash)
    return True


def _is_vite_app(workspace: Path, scripts: dict[str, Any]) -> bool:
    """vite(또는 rolldown-vite) 프론트인지 — base 강제 대상 판별. 자체 base 를 굽는
    Next.js/Streamlit 등은 제외(그 스택은 strip_prefix=False 로 전체 경로를 받는다)."""
    if (workspace / "vite.config.ts").exists() or (workspace / "vite.config.js").exists():
        return True
    build_script = str(scripts.get("build", ""))
    if "vite" in build_script:
        return True
    if "next" in build_script or "streamlit" in build_script:
        return False
    # package.json deps 에 vite 가 있으면 vite 로 간주
    try:
        pkg = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        return any(k == "vite" or k.endswith("-vite") or "vite" in k for k in deps)
    except Exception:  # noqa: BLE001
        return False


def _assert_relative_base(workspace: Path, log_path: Path) -> None:
    """빌드 산출물이 루트 절대경로(/assets…)를 참조하면 규약 위반 — 빌드를 실패시켜
    (sentinel 미기록) 조용한 자산-404 배포를 차단한다. dist 위치는 흔한 후보를 탐색."""
    for dist in ("dist", "build", "frontend/dist", "web/dist", "app/dist"):
        idx = workspace / dist / "index.html"
        if not idx.exists():
            continue
        html = idx.read_text(encoding="utf-8", errors="replace")
        # 루트 절대경로 자산 참조가 있으면 위반. 상대(./assets) / 데이터URI 는 허용.
        if re.search(r'(?:src|href)\s*=\s*["\']/(?:assets|static)/', html):
            msg = (f"app build produced ROOT-absolute asset paths in {dist}/index.html — "
                   f"must be relative (./). vite 앱이면 빌더가 --base=./ 를 주입한다; "
                   f"커스텀 빌드면 상대 base 로 설정하라.")
            try:
                with log_path.open("ab") as logf:
                    logf.write(f"\n✗ {msg}\n".encode())
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(msg)
        return  # 첫 dist 검증 통과


# ---------------------------------------------------------------------------
# Go toolchain builder
# ---------------------------------------------------------------------------


def _build_go(
    workspace: Path,
    spec: StackSpec,
    build_section: dict[str, Any],
    log_path: Path,
) -> bool:
    """Run ``go build`` to produce ``bin/server``. Returns True when work was done."""
    if not (workspace / "go.mod").exists():
        # Nothing to build — operator must commit go.mod.
        return False

    sentinel = workspace / _SENTINEL
    current_hash = _hash_go_inputs(workspace)
    bin_path = workspace / "bin" / "server"
    if _read_sentinel_hash(sentinel) == current_hash and bin_path.exists():
        logger.info("go build skipped (up-to-date): %s", workspace)
        return False

    # Probe for a go122 toolchain SIF first; when present, the host doesn't
    # need `go` on PATH.
    sif = resolve_sif(spec.name)
    if sif is None and shutil.which("go") is None:
        raise FileNotFoundError(
            "go not on PATH — install Go 1.22+ to build go_service stacks"
        )

    # Default build follows the stack template; override via manifest.build.command.
    build_cmd_str = (
        build_section.get("command")
        or spec.build
        or "go build -trimpath -ldflags='-s -w' -o ./bin/server ./cmd/server"
    )
    # Run through /bin/sh so the operator's shell-style ldflags string survives.
    (workspace / "bin").mkdir(parents=True, exist_ok=True)
    env_extras = {"CGO_ENABLED": "0", "GOFLAGS": "-mod=readonly"}
    if (build_section.get("features") or {}).get("cgo"):
        env_extras["CGO_ENABLED"] = "1"
    logger.info("running go build for %s", workspace.name)
    _run_shell(
        build_cmd_str, cwd=workspace, log_path=log_path,
        env_extras=env_extras, sif=sif,
    )

    _write_sentinel_hash(sentinel, current_hash)
    return True


def _hash_go_inputs(workspace: Path) -> str:
    """Stable hash over the files that determine a Go build."""
    return _hash_files(workspace, ["go.mod", "go.sum"])


# ---------------------------------------------------------------------------
# R / renv builder
# ---------------------------------------------------------------------------


def _build_r(
    workspace: Path,
    spec: StackSpec,
    build_section: dict[str, Any],
    log_path: Path,
) -> bool:
    """Restore an R renv project. Returns True when work was done.

    ``renv::restore()`` is best-effort: if Rscript is not on PATH (e.g. the
    operator hasn't installed R yet) we surface a clear _BuildError instead of
    crashing the scanner. Without a ``renv.lock`` file we treat the integration
    as having no install step — ``run.sh`` will still get invoked at job time.
    """
    renv_lock = workspace / "renv.lock"
    if not renv_lock.exists():
        # No renv lockfile — nothing to restore. The job runner can still call
        # the entrypoint; system-wide R packages will be used.
        logger.info("r build skipped (no renv.lock): %s", workspace)
        return False

    sentinel = workspace / _SENTINEL
    current_hash = _hash_r_inputs(workspace)
    library_dir = workspace / "renv" / "library"
    if _read_sentinel_hash(sentinel) == current_hash and library_dir.exists():
        logger.info("r build skipped (up-to-date): %s", workspace)
        return False

    rscript = shutil.which("Rscript")
    if rscript is None:
        raise _BuildError(
            "Rscript not on PATH — install R 4.4+ to build r_script stacks"
        )

    install_cmd = (
        build_section.get("command")
        or spec.install
        or "Rscript -e 'renv::restore(prompt = FALSE)'"
    )
    logger.info("running renv::restore for %s", workspace.name)
    _run_shell(install_cmd, cwd=workspace, log_path=log_path)

    _write_sentinel_hash(sentinel, current_hash)
    return True


def _hash_r_inputs(workspace: Path) -> str:
    """Stable hash over the files that determine an R build."""
    return _hash_files(workspace, ["renv.lock", ".Rprofile", "DESCRIPTION"])


# ---------------------------------------------------------------------------
# Heavy-runtime builders: dotnet / java_maven / rust_cargo
#
# Each of these has a multi-hundred-MB toolchain. We deliberately DO NOT try
# to install them automatically — operators on shared hosts often have curated
# OS-package versions and would be unhappy if HEAXHub started fetching SDK
# tarballs. Instead, the builder detects the required tool on PATH and, if
# missing, raises ``_BuildError`` with a clear instruction (e.g. "install
# dotnet 8 SDK on the host") which surfaces in BuildResult.error.
# ---------------------------------------------------------------------------


def _build_dotnet(
    workspace: Path,
    spec: StackSpec,
    build_section: dict[str, Any],
    log_path: Path,
) -> bool:
    """Run ``dotnet publish -c Release -o publish/``. Returns True when work was done.

    Sentinel hashes ``*.csproj`` + ``*.sln`` so adding a new package reference
    triggers a rebuild but a cosmetic .cs edit does not (publish itself is
    incremental enough for that case).
    """
    csprojs = sorted(workspace.glob("*.csproj"))
    if not csprojs:
        # Also accept a nested project (common monorepo layout: src/App/App.csproj).
        csprojs = sorted(workspace.glob("**/*.csproj"))
    if not csprojs:
        # Nothing to build — operator hasn't committed a project yet.
        return False

    sentinel = workspace / _SENTINEL
    current_hash = _hash_dotnet_inputs(workspace)
    publish_dir = workspace / "publish"
    if (
        _read_sentinel_hash(sentinel) == current_hash
        and publish_dir.exists()
        and any(publish_dir.glob("*.dll"))
    ):
        logger.info("dotnet build skipped (up-to-date): %s", workspace)
        return False

    # Resolve a toolchain SIF first; when present, the polyglot image already
    # carries dotnet-sdk-8 so we can skip the host PATH check.
    sif = resolve_sif(spec.name)
    if sif is None and shutil.which("dotnet") is None:
        raise _BuildError(
            "dotnet not on PATH — operator must install .NET 8 SDK on the host "
            "(e.g. `apt-get install -y dotnet-sdk-8.0` on Ubuntu, or follow "
            "https://learn.microsoft.com/dotnet/core/install/linux). HEAXHub "
            "does not auto-install heavy SDKs."
        )

    install_cmd = (
        build_section.get("command")
        or spec.install
        or "dotnet publish -c Release -o publish"
    )
    logger.info("running dotnet publish for %s", workspace.name)
    _run_shell(install_cmd, cwd=workspace, log_path=log_path, sif=sif)

    _write_sentinel_hash(sentinel, current_hash)
    return True


def _hash_dotnet_inputs(workspace: Path) -> str:
    """Stable hash over the files that determine a dotnet build.

    We include ``*.csproj`` and the (single) solution file — these are the
    only inputs that actually change MSBuild's restore graph. .cs source edits
    are handled by ``dotnet publish``'s own incremental build.
    """
    names: list[str] = []
    for p in sorted(workspace.glob("*.csproj")):
        names.append(p.name)
    for p in sorted(workspace.glob("*.sln")):
        names.append(p.name)
    # Also probe one level down for monorepo layouts (src/App/App.csproj).
    for p in sorted(workspace.glob("**/*.csproj")):
        rel = p.relative_to(workspace).as_posix()
        if rel not in names:
            names.append(rel)
    return _hash_files(workspace, names)


def _build_java_maven(
    workspace: Path,
    spec: StackSpec,
    build_section: dict[str, Any],
    log_path: Path,
) -> bool:
    """Run ``./mvnw package`` (or ``mvn package`` if wrapper missing). Returns
    True when work was done.

    Prefers the Maven wrapper because it pins the exact Maven version per
    project. Falls back to system ``mvn`` when no wrapper is present. Either
    way the host MUST already have JDK 17; we never download a JDK.
    """
    pom = workspace / "pom.xml"
    if not pom.exists():
        # Nothing to build — operator must commit pom.xml.
        return False

    sentinel = workspace / _SENTINEL
    current_hash = _hash_java_inputs(workspace)
    target_dir = workspace / "target"
    jars_present = target_dir.exists() and any(target_dir.glob("*.jar"))
    if _read_sentinel_hash(sentinel) == current_hash and jars_present:
        logger.info("maven build skipped (up-to-date): %s", workspace)
        return False

    # When a polyglot toolchain SIF is available, JDK + Maven both live inside
    # the image so the host doesn't need them. Probe for SIF first; only fall
    # back to the host PATH check when no SIF is present.
    sif_probe = resolve_sif(spec.name)
    if sif_probe is None and shutil.which("java") is None:
        # We deliberately don't parse `java -version` — too fragile across
        # vendors. Operators with the wrong JDK will see Maven's own error
        # on the actual compile step.
        raise _BuildError(
            "java not on PATH — operator must install JDK 17 (Temurin recommended) "
            "on the host. HEAXHub does not auto-install JDKs."
        )

    wrapper = workspace / "mvnw"
    if wrapper.exists():
        # Make sure the wrapper is executable; git checkouts on Windows
        # occasionally lose the +x bit.
        try:
            os.chmod(wrapper, 0o755)
        except OSError:
            pass
        install_cmd = (
            build_section.get("command")
            or spec.install
            or "./mvnw -B -DskipTests package"
        )
    else:
        if sif_probe is None and shutil.which("mvn") is None:
            raise _BuildError(
                "neither ./mvnw nor system mvn found — commit the Maven wrapper "
                "(./mvnw + .mvn/) or install Maven on the host (apt-get install "
                "maven). HEAXHub does not auto-install Maven."
            )
        install_cmd = "mvn -B -DskipTests package"

    sif = sif_probe
    logger.info("running maven build for %s", workspace.name)
    _run_shell(install_cmd, cwd=workspace, log_path=log_path, sif=sif)

    _write_sentinel_hash(sentinel, current_hash)
    return True


def _hash_java_inputs(workspace: Path) -> str:
    """Stable hash over the files that determine a maven build.

    pom.xml is authoritative; the wrapper config (.mvn/wrapper/) pins the
    Maven version. We don't hash source files — incremental compilation in
    Maven handles those without our help.
    """
    return _hash_files(
        workspace,
        [
            "pom.xml",
            ".mvn/wrapper/maven-wrapper.properties",
            ".mvn/maven.config",
        ],
    )


def _build_rust_cargo(
    workspace: Path,
    spec: StackSpec,
    build_section: dict[str, Any],
    log_path: Path,
) -> bool:
    """Run ``cargo build --release``. Returns True when work was done."""
    cargo_toml = workspace / "Cargo.toml"
    if not cargo_toml.exists():
        return False

    sentinel = workspace / _SENTINEL
    current_hash = _hash_rust_inputs(workspace)
    release_dir = workspace / "target" / "release"
    # Heuristic: any executable file in target/release/ signals a prior build.
    # We don't parse Cargo.toml's [[bin]] name here — that'd require a TOML
    # parse in the hot path for marginal benefit.
    has_release_binary = release_dir.exists() and any(
        p.is_file() and os.access(p, os.X_OK) and not p.name.endswith(".d")
        for p in release_dir.iterdir()
    )
    if _read_sentinel_hash(sentinel) == current_hash and has_release_binary:
        logger.info("cargo build skipped (up-to-date): %s", workspace)
        return False

    # Polyglot SIF carries cargo/rustup — skip the host PATH check when set.
    sif = resolve_sif(spec.name)
    if sif is None and shutil.which("cargo") is None:
        raise _BuildError(
            "cargo not on PATH — operator must install the Rust toolchain "
            "(rustup recommended: https://rustup.rs) on the host. HEAXHub "
            "does not bootstrap rustup."
        )

    install_cmd = (
        build_section.get("command")
        or spec.install
        or "cargo build --release"
    )
    logger.info("running cargo build for %s", workspace.name)
    _run_shell(install_cmd, cwd=workspace, log_path=log_path, sif=sif)

    _write_sentinel_hash(sentinel, current_hash)
    return True


def _hash_rust_inputs(workspace: Path) -> str:
    """Stable hash over the files that determine a cargo build."""
    return _hash_files(workspace, ["Cargo.toml", "Cargo.lock", "rust-toolchain.toml"])


# ---------------------------------------------------------------------------
# Static (no-build) verifier
# ---------------------------------------------------------------------------


def _build_static(
    workspace: Path,
    spec: StackSpec,
    build_section: dict[str, Any],
    log_path: Path,
) -> bool:
    """Verify the static root directory + index file exist. No compile step.

    Static stacks (``static_html``, ``mkdocs_static``) ship pre-built assets;
    HEAXHub never invokes a Python/Node/Hugo toolchain here. We just refuse
    to register a stack whose configured root is missing — otherwise Caddy
    would return 404 for every request and the operator would have a much
    harder time diagnosing it.

    The manifest's ``build.static_root`` (or stack default) is resolved
    relative to the workspace. ``build.index_file`` is also checked since
    Caddy's file_server returns a directory listing when the index is
    missing — almost certainly not what the operator intended.
    """
    extra = spec.extra or {}
    # Accept both ``static_root`` (canonical) and ``root`` (shorter alias used
    # in early demos) so authors don't trip over the longer name. Manifest
    # wins over the stack default.
    root_rel = (
        build_section.get("static_root")
        or build_section.get("root")
        or extra.get("static_root")
        or "public"
    )
    index_file = (
        build_section.get("index_file")
        or extra.get("index_file")
        or "index.html"
    )

    root_abs = (workspace / root_rel).resolve()
    # Guard against ``static_root: ../../etc`` escaping the workspace.
    try:
        root_abs.relative_to(workspace.resolve())
    except ValueError:
        raise _BuildError(
            f"static_root '{root_rel}' resolves outside workspace {workspace}"
        )

    if not root_abs.is_dir():
        raise _BuildError(
            f"static_root '{root_rel}' not found at {root_abs} — commit the "
            f"pre-built site into this directory, or set build.static_root in "
            f"the manifest."
        )

    index_abs = root_abs / index_file
    if not index_abs.is_file():
        raise _BuildError(
            f"index file '{index_file}' missing under static_root {root_abs}"
        )

    current_hash = _hash_files(workspace, [f"{root_rel}/{index_file}"])
    sentinel = workspace / _SENTINEL
    if _read_sentinel_hash(sentinel) == current_hash:
        logger.info("static build skipped (up-to-date): %s", workspace)
        return False

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as logf:
        logf.write(
            f"\n[static_copy] verified root={root_abs} index={index_file}\n".encode()
        )
    _write_sentinel_hash(sentinel, current_hash)
    return True


# ---------------------------------------------------------------------------
# C/C++ (CMake / Make) builder
# ---------------------------------------------------------------------------


def _build_cpp(
    workspace: Path,
    spec: StackSpec,
    build_section: dict[str, Any],
    log_path: Path,
) -> bool:
    """Build a C/C++ project via CMake (preferred) or plain Make.

    The stack is job-runner mode (no service spawned at build time). We just
    need a compiled binary on disk before the user submits a job. Detection
    order:

      1. ``CMakeLists.txt`` → ``cmake -S . -B build -DCMAKE_BUILD_TYPE=Release``
         followed by ``cmake --build build -j``.
      2. ``Makefile`` (and no CMakeLists.txt) → ``make -j``.
      3. Nothing → skip; caller may still launch a pre-built binary at job time.

    The sentinel hash covers CMakeLists.txt + Makefile so unrelated edits
    don't trigger a full reconfigure. CMake/make already do incremental
    builds from source mtimes, so re-invoking on a no-op change is cheap.
    """
    cmakelists = workspace / "CMakeLists.txt"
    makefile = workspace / "Makefile"
    if not cmakelists.exists() and not makefile.exists():
        logger.info(
            "cpp build skipped (no CMakeLists.txt or Makefile): %s", workspace
        )
        return False

    sentinel = workspace / _SENTINEL
    current_hash = _hash_cpp_inputs(workspace)
    binary_rel = (
        build_section.get("binary_path")
        or (spec.extra or {}).get("binary_path")
        or "build/bin/solver"
    )
    binary_abs = workspace / binary_rel
    if _read_sentinel_hash(sentinel) == current_hash and binary_abs.exists():
        logger.info("cpp build skipped (up-to-date): %s", workspace)
        return False

    if cmakelists.exists():
        if shutil.which("cmake") is None:
            raise _BuildError(
                "cmake not on PATH — install cmake to build cpp_executable stacks"
            )
        configure_cmd = (
            build_section.get("configure_command")
            or "cmake -S . -B build -DCMAKE_BUILD_TYPE=Release"
        )
        build_cmd = (
            build_section.get("build_command")
            or "cmake --build build -j"
        )
        logger.info("running cmake configure for %s", workspace.name)
        _run_shell(configure_cmd, cwd=workspace, log_path=log_path)
        logger.info("running cmake build for %s", workspace.name)
        _run_shell(build_cmd, cwd=workspace, log_path=log_path)
    else:
        if shutil.which("make") is None:
            raise _BuildError(
                "make not on PATH — install build-essential to build cpp_executable stacks"
            )
        make_cmd = build_section.get("build_command") or "make -j"
        logger.info("running make for %s", workspace.name)
        _run_shell(make_cmd, cwd=workspace, log_path=log_path)

    _write_sentinel_hash(sentinel, current_hash)
    return True


def _hash_cpp_inputs(workspace: Path) -> str:
    """Stable hash over the files that determine a C/C++ build."""
    return _hash_files(workspace, ["CMakeLists.txt", "Makefile"])


# ---------------------------------------------------------------------------
# Apptainer SIF builder (offline image — only verifies the file)
# ---------------------------------------------------------------------------


def _build_apptainer_sif(
    workspace: Path,
    spec: StackSpec,
    build_section: dict[str, Any],
    log_path: Path,
) -> bool:
    """Verify the declared SIF file exists. No actual build happens here.

    The SIF image is produced offline (apptainer build / pulled from a
    registry); HEAXHub never runs ``apptainer build`` itself because it
    typically needs root or fakeroot privileges the worker doesn't have.

    The manifest's ``build.sif_path`` (or stack default) is resolved
    relative to the workspace — refusing to register an integration whose
    image path is wrong avoids a confusing ``apptainer exec`` failure at
    job time.
    """
    extra = spec.extra or {}
    sif_rel = (
        build_section.get("sif_path")
        or build_section.get("sif")
        or extra.get("sif_path")
        or "image.sif"
    )
    sif_abs = (workspace / sif_rel).resolve()
    # Guard against ``sif_path: ../../etc/passwd`` escaping the workspace.
    try:
        sif_abs.relative_to(workspace.resolve())
    except ValueError:
        raise _BuildError(
            f"sif_path '{sif_rel}' resolves outside workspace {workspace}"
        )

    if not sif_abs.is_file():
        raise _BuildError(
            f"sif file '{sif_rel}' not found at {sif_abs} — copy or symlink "
            f"the .sif image into the workspace, or override build.sif_path "
            f"in the manifest."
        )

    current_hash = _hash_files(workspace, [sif_rel])
    sentinel = workspace / _SENTINEL
    if _read_sentinel_hash(sentinel) == current_hash:
        logger.info("apptainer_sif build skipped (up-to-date): %s", workspace)
        return False

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as logf:
        logf.write(
            f"\n[apptainer_sif] verified sif={sif_abs}\n".encode()
        )
    _write_sentinel_hash(sentinel, current_hash)
    return True


def _run_shell(
    cmd_str: str,
    *,
    cwd: Path,
    log_path: Path,
    env_extras: dict[str, str] | None = None,
    sif: Path | None = None,
) -> None:
    """Run a shell command string. Like ``_run`` but goes through /bin/sh -c.

    Used when the operator's build command contains shell-style quoting
    (e.g. ``-ldflags='-s -w'``) that would be mangled by an argv list.

    When ``sif`` is provided, dispatches via ``apptainer exec ... bash -lc``
    inside the toolchain image with the workspace bound at ``/workspace``.
    ``env_extras`` are forwarded as ``APPTAINERENV_<KEY>`` so they survive
    ``--cleanenv``, matching how the apptainer runner propagates proxy vars.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if env_extras:
        env.update(env_extras)
    if sif is None:
        argv = ["/bin/sh", "-c", cmd_str]
        original = argv
    else:
        # --cleanenv strips host env inside the container; re-inject env_extras
        # via APPTAINERENV_<KEY> so e.g. CGO_ENABLED=1 actually reaches `go`.
        if env_extras:
            for k, v in env_extras.items():
                env[f"APPTAINERENV_{k}"] = v
        inner = f"cd /workspace && {cmd_str}"
        argv = [
            "apptainer", "exec", "--cleanenv",
            "--bind", f"{cwd}:/workspace",
            str(sif),
            "bash", "-lc", inner,
        ]
        original = ["/bin/sh", "-c", cmd_str]
        logger.info("running in toolchain SIF %s: %s", sif.name, cmd_str)
    with log_path.open("ab") as logf:
        logf.write(f"\n$ {' '.join(argv)}\n".encode())
        logf.flush()
        proc = subprocess.run(  # noqa: S603
            argv,
            cwd=str(cwd),
            check=False,
            timeout=_BUILD_TIMEOUT,
            capture_output=True,
            env=env,
        )
        if proc.stdout:
            logf.write(proc.stdout)
        if proc.stderr:
            logf.write(proc.stderr)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, original, proc.stdout, proc.stderr
        )


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _wrap_for_toolchain(
    cmd: list[str], *, cwd: Path, sif: Path | None
) -> list[str]:
    """Return ``cmd`` unchanged, or wrapped in ``apptainer exec`` if ``sif`` set.

    The wrapped form binds the workspace to ``/workspace`` inside the
    container, runs through ``bash -lc`` (so login-profile PATH adjustments —
    e.g. pnpm shims dropped in by corepack — survive), and changes into
    ``/workspace`` before invoking the original command. ``--cleanenv`` is
    deliberate: we do NOT want host ``PYTHONPATH`` / ``NODE_PATH`` leaking
    into the build, which is the whole point of dispatching to a SIF.
    """
    if sif is None:
        return cmd
    inner = "cd /workspace && " + " ".join(shlex.quote(c) for c in cmd)
    return [
        "apptainer", "exec", "--cleanenv",
        "--bind", f"{cwd}:/workspace",
        str(sif),
        "bash", "-lc", inner,
    ]


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    log_path: Path,
    sif: Path | None = None,
) -> None:
    """Run a command, raising on non-zero exit, with our timeout cap.

    Stdout+stderr are appended to ``log_path`` so operators can tail it. We
    also keep the bytes around in the raised CalledProcessError so callers
    can include a tail in the BuildResult.error string.

    When ``sif`` is provided, the command is wrapped in ``apptainer exec`` so
    pip/pnpm/etc. run inside the toolchain image. The original argv is still
    used for the CalledProcessError so the operator-visible error mentions
    the tool they asked for, not the apptainer wrapper.
    """
    original_cmd = cmd
    effective_cmd = _wrap_for_toolchain(cmd, cwd=cwd, sif=sif)
    if sif is not None:
        logger.info("running in toolchain SIF %s: %s", sif.name, " ".join(original_cmd))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as logf:
        logf.write(f"\n$ {' '.join(effective_cmd)}\n".encode())
        logf.flush()
        proc = subprocess.run(  # noqa: S603
            effective_cmd,
            cwd=str(cwd),
            check=False,
            timeout=_BUILD_TIMEOUT,
            capture_output=True,
        )
        if proc.stdout:
            logf.write(proc.stdout)
        if proc.stderr:
            logf.write(proc.stderr)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, original_cmd, proc.stdout, proc.stderr
        )


def _run_with_retry(
    cmd: list[str],
    *,
    cwd: Path,
    log_path: Path,
    sif: Path | None = None,
) -> None:
    """Retry transient pip/pnpm install failures with backoff.

    Most install flakes here are upstream registry timeouts; one retry is
    almost always enough but we cap at three to stay within the build timeout.
    """
    last: Exception | None = None
    delays = (0,) + _INSTALL_RETRY_DELAYS[:-1]  # 0s, 5s, 15s, 45s
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            logger.info("retrying %s after %ds (attempt %d)", cmd[0], delay, attempt)
            time.sleep(delay)
        try:
            _run(cmd, cwd=cwd, log_path=log_path, sif=sif)
            return
        except subprocess.CalledProcessError as exc:
            last = exc
            with log_path.open("ab") as logf:
                logf.write(
                    f"\n[retry] attempt {attempt} of {len(delays)} failed: "
                    f"exit={exc.returncode}\n".encode()
                )
            continue
    assert last is not None
    raise last


def _hash_python_inputs(workspace: Path) -> str:
    """Stable hash over the files that determine a python build."""
    return _hash_files(
        workspace,
        ["pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg", "setup.py"],
    )


def _hash_nodejs_inputs(workspace: Path) -> str:
    """Stable hash over the files that determine a nodejs build."""
    return _hash_files(
        workspace,
        ["package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock"],
    )


def _hash_files(workspace: Path, names: list[str]) -> str:
    h = hashlib.sha256()
    for name in names:
        p = workspace / name
        if not p.exists():
            continue
        h.update(name.encode())
        h.update(b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            continue
        h.update(b"\0\0")
    return h.hexdigest()


def _read_sentinel_hash(sentinel: Path) -> str | None:
    if not sentinel.exists():
        return None
    try:
        return sentinel.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_sentinel_hash(sentinel: Path, value: str) -> None:
    sentinel.write_text(value, encoding="utf-8")


def _read_log_tail(log_path: Path) -> bytes:
    try:
        with log_path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _ERROR_TAIL_BYTES))
            return f.read()
    except OSError:
        return b""


def _decode(blob: bytes) -> str:
    try:
        return blob.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover
        return repr(blob)
