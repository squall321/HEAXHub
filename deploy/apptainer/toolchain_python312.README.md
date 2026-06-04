# heaxhub_toolchain_python312.sif

Python 3.12 build toolchain SIF used by `integration_builder` when a workspace's
stack maps to `python312` (see `backend/app/services/toolchain_resolver.py`).

## Stacks that use this SIF

From `STACK_TO_TOOLCHAIN`:

- `python_cli`
- `streamlit`
- `fastapi`
- `flask`
- `dash_plotly`
- `shiny_for_python`

## Contents

- Base: `python:3.12-slim-bookworm`
- Python 3.12 with `pip`, `setuptools`, `wheel` pre-upgraded
- `venv` module pre-warmed (`python -m ensurepip --upgrade`)
- `uv` (Astral) pinned `>= 0.4` for fast installs
- `build-essential` (gcc, g++, make) for pyproject builds that need C extensions
- `git`, `ca-certificates`, `curl`

## Build

```bash
# Online builder host (with apptainer 1.3+ and outbound HTTPS):
cd deploy/apptainer
apptainer build heaxhub_toolchain_python312.sif toolchain_python312.def

# Or via the bundled wrapper which also writes sha256sums.txt:
infra/packages/toolchains/build-toolchain.sh --only python312
```

To honor a corporate proxy during build:

```bash
export HTTPS_PROXY=http://proxy.example:3128
export HTTP_PROXY=http://proxy.example:3128
apptainer build heaxhub_toolchain_python312.sif toolchain_python312.def
```

## Install location

Resolution order used by `toolchain_resolver.resolve_sif_dir()`:

1. `$HEAXHUB_TOOLCHAIN_SIF_DIR/heaxhub_toolchain_python312.sif`
2. `$SIF_DIR/heaxhub_toolchain_python312.sif`
3. `deploy/apptainer/heaxhub_toolchain_python312.sif` (repo default)
4. `$HOME/serviceApptainers/heaxhub_toolchain_python312.sif` (dev fallback)

If none of these exist, the builder falls back to the host `PATH` Python.

## Usage

The builder invokes the SIF roughly like:

```bash
apptainer exec --cleanenv \
    --bind "$WORKSPACE":/workspace \
    heaxhub_toolchain_python312.sif \
    bash -lc 'cd /workspace && python -m venv .venv && .venv/bin/pip install -e .'
```

Notes:

- `--cleanenv` strips host env to prevent `PYTHONPATH`/`PIP_*` leakage.
- Only the workspace is bind-mounted (at `/workspace`).
- Proxy env is forwarded explicitly by the builder via `APPTAINERENV_HTTPS_PROXY`
  when set in the worker environment.

## Verification

```bash
apptainer exec heaxhub_toolchain_python312.sif python --version
apptainer exec heaxhub_toolchain_python312.sif pip --version
apptainer exec heaxhub_toolchain_python312.sif uv --version
apptainer exec heaxhub_toolchain_python312.sif git --version
```
