# HWAXAgent Contracts Changelog

All notable changes to the HWAXAgent contract surface (JSON schemas, OpenAPI,
design tokens) are recorded here. The contracts are versioned independently
from HEAXHub and from HWAXAgent itself, following SemVer.

## [Unreleased]

Reported from the HWAXAgent side (Tauri 2 launcher) after building the client
against contracts v0.2.0. See the PR description for the full feedback list.

### Fixed
- Removed stale **C# / WinUI3 / .NET** references that contradicted the confirmed
  stack (**Tauri 2 + Rust + React**, per `docs/hwax-launcher-plan-v2.md`). The
  agent's client is Rust (`reqwest`) + TS types — never C#/`Heax.Agent.Api`.
  An HWAXAgent-side LLM was misled by these into starting a WinUI3/.NET build.
  - `openapi.yaml` (info.description), `README.md`, `tokens.css` comment.
- `install-report.schema.json` description: corrected `/api/v1/agents/installs`
  → `/api/v1/launcher-agents/installs` (the v0.2.0 renamed prefix; the bare
  `/api/v1/agents/*` is the pre-existing service-agent API).

### Added
- `openapi.yaml`: `GET /api/v1/installers/{app_id}/latest` — the **Tauri updater
  feed** (static-JSON manifest with per-platform Ed25519 `signature` + `url`,
  `204` when current) that the agent's self-update polls (v2 §18). New
  `TauriUpdaterManifest` schema. **Backend implementation is a HEAXHub follow-up.**

> SemVer: the de-stale/prefix edits are PATCH (docs only); the new endpoint is
> MINOR. Final version is the maintainer's call at merge.

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
