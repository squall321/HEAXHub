# heaxhub_toolchain_nodejs20

Apptainer toolchain image used by `integration_builder` to compile Node.js
stacks (`nextjs`, `nodejs_express`, …) in a clean, version-pinned environment.

## What's inside

| Component           | Version                  | Source                      |
| ------------------- | ------------------------ | --------------------------- |
| Base image          | `node:20-bookworm-slim`  | Docker Hub (official)       |
| Node.js             | 20.x                     | base image                  |
| npm                 | shipped with Node 20     | base image                  |
| pnpm                | 9.15.0 (pinned)          | `corepack prepare`          |
| git                 | distro                   | `apt-get`                   |
| ca-certificates     | distro                   | `apt-get`                   |
| curl                | distro                   | `apt-get`                   |

Workspace bind point: `/workspace` (mode `0777`, world-writable so any UID
running `apptainer exec` can build into it).

Default env:

- `PATH` — standard sbin/bin order
- `LANG=C.UTF-8`, `LC_ALL=C.UTF-8`
- `PNPM_HOME=/workspace/.pnpm` (cache stays inside the bound workspace)
- `NODE_ENV=production`

## Build

From this directory (online host with docker pull access):

```bash
apptainer build heaxhub_toolchain_nodejs20.sif toolchain_nodejs20.def
```

With proxy:

```bash
HTTPS_PROXY=http://proxy:8080 HTTP_PROXY=http://proxy:8080 \
  apptainer build heaxhub_toolchain_nodejs20.sif toolchain_nodejs20.def
```

The build runs `node --version`, `npm --version`, `pnpm --version`,
`git --version` in `%post`, so a missing tool fails the build immediately.

## Test it after build

Smoke test — versions only:

```bash
apptainer exec heaxhub_toolchain_nodejs20.sif bash -lc \
  'node --version && npm --version && pnpm --version && git --version'
```

Expected output (versions may vary in patch level):

```
v20.x.x
10.x.x
9.15.0
git version 2.x.x
```

End-to-end test against a real workspace:

```bash
mkdir -p /tmp/heax-node-test && cd /tmp/heax-node-test
cat > package.json <<'EOF'
{ "name": "smoke", "version": "0.0.1", "private": true,
  "scripts": { "build": "echo built" } }
EOF

apptainer exec --cleanenv \
  --bind /tmp/heax-node-test:/workspace \
  heaxhub_toolchain_nodejs20.sif \
  bash -lc 'cd /workspace && pnpm install && pnpm build'
```

Expected: `pnpm install` resolves (empty deps), `pnpm build` prints `built`.

## Deployment

Place the built `.sif` at the path picked up by
`backend/app/services/toolchain_resolver.py`:

- Production: `$HEAXHUB_TOOLCHAIN_SIF_DIR/heaxhub_toolchain_nodejs20.sif`
  (defaults to `deploy/apptainer/`)
- Dev fallback: `$HOME/serviceApptainers/heaxhub_toolchain_nodejs20.sif`

The resolver re-stats each call, so dropping the file in is picked up on
the next build invocation without a worker restart.

## Notes

- `corepack enable` activates the package-manager shim; `corepack prepare
  pnpm@9.15.0 --activate` pins pnpm so reproducible builds don't drift on
  upstream pnpm releases.
- `--cleanenv` is the recommended exec flag — the builder strips host env
  to avoid `NODE_PATH` / `npm_config_*` leakage. Proxy vars come in via
  `APPTAINERENV_HTTPS_PROXY` etc. when set in the worker env.
- The image is offline-portable: once built it has no further network
  dependency for `pnpm install` *if* a populated pnpm store is bind-mounted.
