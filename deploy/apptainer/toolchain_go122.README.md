# heaxhub_toolchain_go122

Apptainer SIF that provides the Go 1.22 build toolchain used by HEAXHub's
`integration_builder` when serving stack `go_service`.

## Contents

- `go 1.22` (from `golang:1.22-bookworm` base image)
- `git`
- `ca-certificates`
- `make`

## Build

From the repo root (requires `apptainer` on the build host, online access,
and proxy env vars if behind a corporate proxy):

```bash
apptainer build deploy/apptainer/heaxhub_toolchain_go122.sif \
                deploy/apptainer/toolchain_go122.def
```

For offline servers, build on an online machine and `scp` the resulting
`.sif` to `$HEAXHUB_TOOLCHAIN_SIF_DIR` (default: `deploy/apptainer/`).

## Bind

The integration_builder mounts the per-submission workspace:

```
--bind $WORKSPACE:/workspace
```

## Usage

Direct invocation (mirrors what the builder runs internally):

```bash
apptainer exec \
  --cleanenv \
  --bind "$WORKSPACE:/workspace" \
  deploy/apptainer/heaxhub_toolchain_go122.sif \
  bash -lc 'cd /workspace && go build -o {slug} .'
```

The builder appends `--cleanenv` to strip host env (no `GOPATH`/`GOROOT`
leakage) and forwards proxy env explicitly via `APPTAINERENV_HTTPS_PROXY`
(and `APPTAINERENV_HTTP_PROXY`, `APPTAINERENV_NO_PROXY`) when the worker
process has them set.

## Resolution order

`backend/app/services/toolchain_resolver.py` looks for
`heaxhub_toolchain_go122.sif` in this order:

1. `$HEAXHUB_TOOLCHAIN_SIF_DIR` (if set)
2. `$SIF_DIR` (if set)
3. `deploy/apptainer/` (repo default)
4. `$HOME/serviceApptainers/` (developer fallback)

If none of those contain the file, the builder falls back to host `PATH`
(requires `go`, `git`, `make` installed on the host).

## Environment inside the SIF

```
PATH=/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GOPATH=/tmp/go
GOCACHE=/tmp/go-cache
GOFLAGS=-mod=mod
CGO_ENABLED=0
```

`GOPATH` and `GOCACHE` point at `/tmp` so the SIF stays read-only and
each build gets a clean module cache (or the operator can bind a host
cache dir at `/tmp/go-cache` for speed).

## Labels

```
org.heaxhub.role         = toolchain
org.heaxhub.toolchain    = go122
org.heaxhub.go.version   = 1.22
org.heaxhub.base         = golang:1.22-bookworm
```

Inspect at runtime with:

```bash
apptainer inspect deploy/apptainer/heaxhub_toolchain_go122.sif
apptainer run-help deploy/apptainer/heaxhub_toolchain_go122.sif
```
