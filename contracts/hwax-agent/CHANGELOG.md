# HWAXAgent Contracts Changelog

All notable changes to the HWAXAgent contract surface (JSON schemas, OpenAPI,
design tokens) are recorded here. The contracts are versioned independently
from HEAXHub and from HWAXAgent itself, following SemVer.

## [Unreleased]

- (nothing yet)

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
