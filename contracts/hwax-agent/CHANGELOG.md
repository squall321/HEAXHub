# HWAXAgent Contracts Changelog

All notable changes to the HWAXAgent contract surface (JSON schemas, OpenAPI,
design tokens) are recorded here. The contracts are versioned independently
from HEAXHub and from HWAXAgent itself, following SemVer.

## [Unreleased]

- (nothing yet)

## [0.3.0] - 2026-06-08 — MINOR

The contract surface is now fully backed by a running HEAXHub backend.

### Backend implementation landed (HEAXHub follow-up)
- `POST /api/v1/launcher-agents/enroll` — implemented (mints access+refresh).
- `POST /api/v1/launcher-agents/refresh` — implemented (rotates refresh; reuse → 401).
- `GET  /api/v1/launcher-agents/manifest` — implemented with **ETag / If-None-Match
  → 304** support (PR #2 G-line requirement).
- `POST /api/v1/launcher-agents/installs` — Phase 1 stub (202 Accepted, logged).
- `POST /api/v1/launcher-agents/audit` — Phase 1 stub (202 Accepted, logged).
- `POST /api/v1/launcher-agents/heartbeat` — implemented (204; updates last_seen,
  agent_version, hostname, modules in capabilities JSON).
- `GET  /api/v1/installers/{id}/download` — implemented (bearer aud='hwax-agent',
  302 to installer_url + `X-Sha256` header).
- `GET  /api/v1/installers/{app_id}/latest` — implemented (Tauri updater feed;
  204 when no installer registered). `signature` emitted as `""` until the
  Ed25519 signing pipeline is wired (Phase 2 TODO).

### Fixed (rolled up from the previous Unreleased)
- Removed stale **C# / WinUI3 / .NET** references that contradicted the
  confirmed stack (Tauri 2 + Rust + React, per `docs/hwax-launcher-plan-v2.md`).
- `install-report.schema.json` description: corrected `/api/v1/agents/installs`
  → `/api/v1/launcher-agents/installs`.

### Added (rolled up)
- `openapi.yaml`: `GET /api/v1/installers/{app_id}/latest` schema + new
  `TauriUpdaterManifest` component.

### Migration notes for HEAXHub deployers
Two new Alembic revisions are required:
- `0006_windows_agents_device_kind` — adds `windows_agents.device_kind` column
  (values: `launcher | service | NULL`).
- `0007_agent_refresh_tokens` — creates the sibling table for launcher refresh
  tokens (kept separate from the user-FK'd `refresh_tokens`).
Run `alembic upgrade head` before starting the new backend.

## [0.2.0] - 2026-06-05 — BREAKING

- Renamed launcher endpoint prefix from `/api/v1/agents/*` to `/api/v1/launcher-agents/*`
  to avoid collision with the pre-existing service-agent endpoints (which use a
  different body shape — e.g. `POST /api/v1/agents/heartbeat` already takes
  `{ status, agent_version? }` and would double-register otherwise).
  Affected endpoints:
    - `/api/v1/launcher-agents/enroll`
    - `/api/v1/launcher-agents/refresh`
    - `/api/v1/launcher-agents/manifest`
    - `/api/v1/launcher-agents/installs`
    - `/api/v1/launcher-agents/audit`
    - `/api/v1/launcher-agents/heartbeat`
  `/api/v1/installers/{id}/download` is unchanged.

## [0.1.0] - 2026-06-05

- Initial contract surface for HWAXAgent integration.
  - `manifest.schema.json` — program catalog delivered to the agent.
  - `install-report.schema.json` — per-attempt install outcome report.
  - `audit-event.schema.json` — agent-emitted audit events.
  - `openapi.yaml` — HTTP surface (`/api/v1/agents/*`, `/api/v1/installers/{id}/download`).
  - `tokens.css` — dark + amber design tokens.
