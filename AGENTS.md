# Agent notes — HEAX Hub

## HWAX Portal integration (2026-06-07) — READ FIRST if touching frontend build / deploy

This app is federated by the **HWAX Portal** (`hwax.sec.samsung.net`), reverse-proxied under the
sub-path **`/heax-hub/`** (the portal **strips** the prefix). Two consequences for any frontend or
deploy work:

1. **Base path.** The SPA must be built for the sub-path: `HEAX_BASE_PATH=/heax-hub/` →
   `VITE_BASE_PATH` drives Vite `base`, the TanStack router `basepath`, the api/ws base. Empty =
   standalone (`/`), unchanged. Don't hardcode `/api`, `/ws`, `/docs` — derive from
   `import.meta.env.BASE_URL`.

2. **No build on cae00.** The production server (cae00) is on a corporate TLS-intercept network where
   **npm/corepack and Docker Hub are unreachable**. NEVER assume a build can run there. Build the
   dist ONLINE and ship it via **Google Drive (rclone)**:
   `deploy/apptainer/dist-to-drive.sh` (online) → `dist-from-drive.sh` (cae00). `HEAX_NO_BUILD=1`
   makes `install_all.sh` refuse to attempt a build.

Full details: **`docs/HWAX-PORTAL-INTEGRATION.md`**.
