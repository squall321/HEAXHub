"""Process resource-limit helpers for runner subprocesses.

`build_preexec(limits)` returns a callable suitable for `subprocess.Popen(preexec_fn=...)`
that (a) places the child in a new process group via `os.setsid` so cancellation
can target the entire group, and (b) applies `RLIMIT_CPU / RLIMIT_AS / RLIMIT_FSIZE`
based on a manifest-derived `limits` dict.

The `resource` stdlib module is Linux-only. On non-Linux platforms (or when the
module isn't available for any reason) the preexec_fn still calls `setsid` but
skips the rlimit calls — so the runner behaves the same, just without the
hardening.

Manifest input shape (`limits` dict):
    {
        "cpu_seconds": int,        # wall-allocated CPU time (RLIMIT_CPU)
        "memory_gb":   int|float,  # address space cap     (RLIMIT_AS)
        "file_size_gb": int|float, # max single-file write (RLIMIT_FSIZE)
    }
Missing keys fall back to safe defaults.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from app.core.logger import get_logger

logger = get_logger(__name__)


# Sensible defaults — every value can be overridden by the manifest.
DEFAULT_CPU_SECONDS = 3600           # 1 hour of CPU time
DEFAULT_MEMORY_GB = 8                # 8 GB address space
DEFAULT_FILE_SIZE_GB = 5             # 5 GB max single file


try:  # pragma: no cover — exercised only on Linux
    import resource as _resource  # type: ignore[import-not-found]

    _HAS_RESOURCE = True
except Exception:  # noqa: BLE001
    _resource = None  # type: ignore[assignment]
    _HAS_RESOURCE = False


def _gb_to_bytes(gb: float | int) -> int:
    return int(float(gb) * 1024 * 1024 * 1024)


def _coerce_limits(limits: dict[str, Any] | None) -> dict[str, int]:
    """Normalize the manifest resources dict into concrete rlimit values."""
    src = limits or {}

    # Accept both flat and nested manifests:
    #   {"cpu_seconds": ..., "memory_gb": ...}
    #   {"resources": {"cpu_seconds": ..., "memory_gb": ...}}
    if "resources" in src and isinstance(src["resources"], dict):
        src = {**src["resources"], **{k: v for k, v in src.items() if k != "resources"}}

    cpu = int(src.get("cpu_seconds") or DEFAULT_CPU_SECONDS)
    mem_gb = float(src.get("memory_gb") or DEFAULT_MEMORY_GB)
    fsize_gb = float(src.get("file_size_gb") or DEFAULT_FILE_SIZE_GB)

    # Clamp pathological values rather than passing them through.
    cpu = max(1, cpu)
    mem_gb = max(0.25, mem_gb)
    fsize_gb = max(0.01, fsize_gb)

    return {
        "RLIMIT_CPU": cpu,
        "RLIMIT_AS": _gb_to_bytes(mem_gb),
        "RLIMIT_FSIZE": _gb_to_bytes(fsize_gb),
    }


def build_preexec(limits: dict[str, Any] | None) -> Callable[[], None]:
    """Return a callable for `subprocess.Popen(preexec_fn=...)`.

    The returned function runs *inside the forked child* before `exec()` and
    therefore must be small and side-effect free. It always calls `setsid` so
    `cancel()` can `killpg(SIGTERM)` the whole group. RLIMIT_* calls are
    best-effort: a failure to apply a single limit is logged-and-skipped so
    portability problems never block the actual job.
    """
    normalized = _coerce_limits(limits)

    def _preexec() -> None:
        # 1. process group isolation — always safe on POSIX.
        try:
            os.setsid()
        except Exception:  # noqa: BLE001 — child must not crash here
            pass

        # 2. resource limits — Linux only.
        if not _HAS_RESOURCE or _resource is None:
            return
        for name, value in normalized.items():
            const = getattr(_resource, name, None)
            if const is None:
                continue
            try:
                _resource.setrlimit(const, (value, value))
            except (ValueError, OSError):
                # Some sandboxes (containers without CAP_SYS_RESOURCE) can't
                # set certain rlimits. We must not abort the child.
                pass

    return _preexec


def apply_limits(p: Any, limits: dict[str, Any] | None) -> None:
    """Compatibility hook: no-op for already-spawned processes.

    rlimits must be applied *before* `exec()`, so use `build_preexec()` instead
    of this. This shim exists only to satisfy the documented interface — it
    records the intended limits to the logger so operators can audit what the
    runner *would* have enforced.
    """
    normalized = _coerce_limits(limits)
    pid = getattr(p, "pid", None)
    logger.info(
        "apply_limits noop (rlimits must be set via preexec_fn) pid=%s limits=%s",
        pid,
        normalized,
    )


__all__ = ["build_preexec", "apply_limits", "DEFAULT_CPU_SECONDS",
           "DEFAULT_MEMORY_GB", "DEFAULT_FILE_SIZE_GB"]
