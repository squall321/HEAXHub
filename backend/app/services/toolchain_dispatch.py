"""Toolchain dispatch — pick a heaxhub_toolchain_*.sif for a given stack.

The integration builder used to run ``pip`` / ``pnpm`` / ``go`` / ``mvn`` /
``cargo`` directly off the host PATH. That works on a developer laptop where
every toolchain is conveniently installed, but it does not work on the
locked-down operator boxes HEAXHub actually deploys to — they ship a small,
audited host PATH and expect heavy toolchains to live inside Apptainer SIF
images instead.

This module is the single mapping ``stack_name -> sif_filename`` plus the
filesystem probe that turns that filename into an absolute path. It is
deliberately stateless: ``resolve_sif`` re-stats the disk on every call, so an
operator can drop a new SIF into ``HEAXHUB_TOOLCHAIN_SIF_DIR`` and the next
build picks it up without a worker restart. Removing a SIF mid-runtime
causes the next build to fall back to host PATH for the same reason.

Probe order:
  1. ``HEAXHUB_TOOLCHAIN_SIF_DIR`` (Settings.toolchain_sif_dir)
  2. ``SIF_DIR`` (Settings.sif_dir, if defined — currently optional)
  3. ``$HOME/serviceApptainers/`` (dev fallback)

Returning ``None`` means "no SIF applies — let the builder use host PATH".
The five no-toolchain stacks (static_html, r_script, cpp_executable,
apptainer_sif, the external_* family, windows_local) are simply absent from
``STACK_TO_SIF``, so ``resolve_sif`` returns ``None`` for them by construction.
"""
from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.core.logger import get_logger

logger = get_logger(__name__)


# Single source of truth for stack -> toolchain SIF filename.
# Naming rule: heaxhub_toolchain_<key>.sif where <key> ∈
# {nodejs20, python312, go122, polyglot}. Keys correspond to the .def files
# shipped under infra/packages/toolchains/defs/.
STACK_TO_SIF: dict[str, str] = {
    # Python ecosystem — one SIF covers pip/setuptools/wheel + python3.12.
    "python_cli": "heaxhub_toolchain_python312.sif",
    "streamlit": "heaxhub_toolchain_python312.sif",
    "fastapi": "heaxhub_toolchain_python312.sif",
    "flask": "heaxhub_toolchain_python312.sif",
    "dash_plotly": "heaxhub_toolchain_python312.sif",
    "shiny_for_python": "heaxhub_toolchain_python312.sif",
    # Node.js ecosystem — node 20 + corepack + pnpm.
    "nextjs": "heaxhub_toolchain_nodejs20.sif",
    "nodejs_express": "heaxhub_toolchain_nodejs20.sif",
    # Go.
    "go_service": "heaxhub_toolchain_go122.sif",
    # Polyglot SIF bundles dotnet 8 SDK + JDK 17 + Maven + cargo/rustup, since
    # building all three separately would triple the offline bundle size for
    # very little marginal isolation benefit.
    "dotnet_aspnet": "heaxhub_toolchain_polyglot.sif",
    "java_springboot": "heaxhub_toolchain_polyglot.sif",
    "rust_actix": "heaxhub_toolchain_polyglot.sif",
}


def resolve_sif(stack_name: str) -> Path | None:
    """Return absolute Path to the toolchain SIF for ``stack_name`` if found.

    Returns ``None`` when:
      - the stack is not in ``STACK_TO_SIF`` (e.g. static_html, r_script,
        external_link — these never need a toolchain SIF), or
      - none of the candidate directories actually contains the file (operator
        hasn't dropped it in yet → builder falls back to host PATH).

    Stateless on purpose: stats the disk on every call so adding/removing a
    SIF takes effect immediately.
    """
    fname = STACK_TO_SIF.get(stack_name)
    if not fname:
        return None
    for dir_ in _candidate_dirs():
        p = Path(dir_).expanduser() / fname
        if p.is_file():
            return p
    return None


def _candidate_dirs() -> list[str]:
    """Ordered list of directories to probe for toolchain SIFs.

    Settings may not (yet) define ``sif_dir`` — we use ``getattr`` with a
    default so this module stays decoupled from that field's lifecycle.
    """
    s = get_settings()
    cands: list[str] = []
    tc = getattr(s, "toolchain_sif_dir", "") or ""
    if tc:
        cands.append(tc)
    sif = getattr(s, "sif_dir", "") or ""
    if sif:
        cands.append(str(sif))
    cands.append(str(Path.home() / "serviceApptainers"))
    return cands
