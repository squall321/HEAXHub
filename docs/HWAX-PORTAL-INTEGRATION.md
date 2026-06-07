# Serving HEAX Hub behind the HWAX Portal (sub-path)

> Context for future work: this app can run **standalone** (at `/`) OR **behind the HWAX Portal**
> (`hwax.sec.samsung.net`), which reverse-proxies it under the sub-path **`/heax-hub/`** and
> **STRIPS that prefix** before forwarding to Caddy. Wired up 2026-06-07. Standalone behaviour
> is unchanged (base defaults to `/`).

## Key idea (why this is a small change)

HEAX Hub already builds the SPA (`pnpm build` → `frontend/dist`) and serves it statically with the
**Caddy** Apptainer instance (`deploy/apptainer/caddy.def`, `caddy_bootstrap.json`: `file_server`
from `/srv/web` + `reverse_proxy /api,/ws,/docs → :4040`). Caddy serves at the **root** (`/`,
`/api`, `/assets`).

The portal **strips** `/heax-hub/` before hitting Caddy, so Caddy needs **no change**. The only
requirement is that the built assets' URLs carry the prefix — done by **building with the base**:

```
HEAX_BASE_PATH=/heax-hub/   →   VITE_BASE_PATH=/heax-hub/ pnpm build
```

Flow behind the portal:
- `/heax-hub/` → portal strips → `/` → Caddy → `index.html` (whose asset URLs are `/heax-hub/...`).
- `/heax-hub/assets/x` → strip → `/assets/x` → Caddy file_server → `dist/assets/x`. ✓
- `/heax-hub/api/y` → strip → `/api/y` → Caddy reverse_proxy → `:4040`. ✓
- `/heax-hub/ws/...`, `/heax-hub/docs` → strip → `/ws`, `/docs` → Caddy. ✓
- deep link `/heax-hub/jobs/123` → strip → `/jobs/123` → Caddy try_files → `index.html` → client
  router (basename `/heax-hub`) takes over. ✓

## One env var: `HEAX_BASE_PATH`

- Empty / unset → standalone, base `/` (original behaviour).
- `HEAX_BASE_PATH=/heax-hub/` (in `.env`) → built for the portal sub-path.

`deploy/apptainer/install_all.sh` and `Makefile` read it and pass `VITE_BASE_PATH` to `pnpm build`.

## Files changed (for reference)

- `frontend/vite.config.ts` — `base = VITE_BASE_PATH || "/"`; dev proxy matches base-prefixed
  `/…/api` + `/…/ws` and strips back (dev only; prod is Caddy).
- `frontend/src/App.tsx` — TanStack `createRouter` `basepath = BASE_URL` (trailing slash trimmed).
- `frontend/src/lib/api/client.ts`, `lib/api/jobs.ts`, `components/layout/Footer.tsx` — API base
  default `BASE_URL + "api/v1"`; `/docs` link uses `BASE_URL`.
- `frontend/src/lib/ws/useJobLogs.ts` — WS base default `BASE_URL + "ws"`.
- `deploy/apptainer/install_all.sh`, `Makefile` — `VITE_BASE_PATH=$HEAX_BASE_PATH` at build.
- `.env.example` — `HEAX_BASE_PATH` documented.

## Run behind the portal

```bash
# in .env:  HEAX_BASE_PATH=/heax-hub/
bash deploy/apptainer/install_all.sh   # rebuilds frontend/dist with base /heax-hub/
bash deploy/apptainer/start.sh         # Caddy serves dist (unchanged) on :4180
```

## Deploying to cae00 (corporate network — NO npm, NO Docker Hub)

cae00 cannot reach npm (`UNABLE_TO_VERIFY_LEAF_SIGNATURE`) or Docker Hub, so it must NOT build.
Build the dist ONLINE and ship it via Google Drive (rclone), exactly like HWAXPortal ships its sifs:

```bash
# ONLINE host (can reach npm):
HEAX_BASE_PATH=/heax-hub/ pnpm --dir frontend build      # base baked into frontend/dist
./deploy/apptainer/dist-to-drive.sh                      # → HEAX_DRIVE_REMOTE (+ HEAX_DRIVE_WITH_CADDY=1 once)

# cae00 (no build):
#   .env:  HEAX_DRIVE_REMOTE=HeaxDrive:HEAXHub/dist,  HEAX_NO_BUILD=1
./deploy/apptainer/dist-from-drive.sh                    # pulls frontend/dist (+ caddy sif), sha256-verified
bash deploy/apptainer/start.sh                           # Caddy serves dist; install_all sees dist → skips build
```

`HEAX_NO_BUILD=1` makes `install_all.sh` refuse to attempt a pnpm build (fails with instructions)
so cae00 can never accidentally hit npm. Caddy serves the pulled `frontend/dist` unchanged; the
portal strips `/heax-hub/` and Caddy serves at root.

The portal reverse-proxies `https://hwax.sec.samsung.net/heax-hub/` → `127.0.0.1:4180` **with the
prefix stripped** (in the portal's `routes.env`: `heax-hub=http://localhost:4180/` — trailing slash).

## Gotchas

- `HEAX_BASE_PATH` must be set at **build time** — the base is baked into the static assets.
- The portal **strips** the prefix for this service (Caddy serves at root). Contrast MX White Paper,
  which uses `vite preview` (serves UNDER the base) and is NOT stripped.
- If you change which layer strips, the Caddy config / portal `routes.env` must change together.
