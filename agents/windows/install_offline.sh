#!/usr/bin/env bash
# HEAXHub Linux Worker Agent — offline systemd installer
#
# Installs the already-published self-contained Linux binary at
# bin/linux-x64/HeaxAgent into /opt/heaxhub-agent and registers it as a
# systemd service. Reads HEAX_HUB_URL / HEAX_AGENT_TOKEN / HEAX_AGENT_POOL
# from /etc/default/heaxhub-agent.
#
# Usage:
#   sudo ./install_offline.sh                  # install + enable + start
#   sudo INSTALL_USER=heaxhub ./install_offline.sh
#
# Idempotent: re-running upgrades the binary in place and restarts the unit.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_BIN="${SCRIPT_DIR}/bin/linux-x64/HeaxAgent"
SRC_APPSETTINGS="${SCRIPT_DIR}/bin/linux-x64/appsettings.json"

INSTALL_DIR="${INSTALL_DIR:-/opt/heaxhub-agent}"
INSTALL_USER="${INSTALL_USER:-heaxhub}"
INSTALL_GROUP="${INSTALL_GROUP:-${INSTALL_USER}}"
SERVICE_NAME="${SERVICE_NAME:-heaxhub-agent}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="/etc/default/${SERVICE_NAME}"
WORK_ROOT="${WORK_ROOT:-/var/lib/heaxhub-agent/work}"

log() { printf '[install_offline] %s\n' "$*"; }
die() { printf '[install_offline] ERROR: %s\n' "$*" >&2; exit 1; }

# --- sudo gate ---------------------------------------------------------------
if [[ ${EUID} -ne 0 ]]; then
    die "must run as root (try: sudo $0)"
fi

# --- preflight ---------------------------------------------------------------
[[ -f "${SRC_BIN}" ]] || die "self-contained binary not found at ${SRC_BIN} — run dotnet publish first"
command -v systemctl >/dev/null 2>&1 || die "systemctl not found; this installer requires systemd"

# --- service user ------------------------------------------------------------
if ! id -u "${INSTALL_USER}" >/dev/null 2>&1; then
    log "creating system user ${INSTALL_USER}"
    useradd --system --no-create-home --shell /usr/sbin/nologin "${INSTALL_USER}"
fi

# --- install dirs ------------------------------------------------------------
log "installing into ${INSTALL_DIR}"
install -d -m 0755 "${INSTALL_DIR}"
install -d -m 0750 -o "${INSTALL_USER}" -g "${INSTALL_GROUP}" "${WORK_ROOT}"

# --- copy binary (idempotent) ------------------------------------------------
# stop service if it is currently running so we can overwrite the binary
if systemctl is-active --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
    log "stopping running ${SERVICE_NAME}.service before upgrade"
    systemctl stop "${SERVICE_NAME}.service"
fi

install -m 0755 "${SRC_BIN}" "${INSTALL_DIR}/HeaxAgent"
if [[ -f "${SRC_APPSETTINGS}" ]]; then
    install -m 0644 "${SRC_APPSETTINGS}" "${INSTALL_DIR}/appsettings.json"
fi
chown -R "${INSTALL_USER}:${INSTALL_GROUP}" "${INSTALL_DIR}"

# --- env file (do not clobber existing values) ------------------------------
if [[ ! -f "${ENV_FILE}" ]]; then
    log "writing default ${ENV_FILE} (edit before starting in production)"
    cat >"${ENV_FILE}" <<'EOF'
# HEAXHub agent environment.
# Set these before starting the service.
HEAX_HUB_URL=https://hub.company.com
HEAX_AGENT_TOKEN=
HEAX_AGENT_POOL=default
EOF
    chmod 0640 "${ENV_FILE}"
    chown root:"${INSTALL_GROUP}" "${ENV_FILE}"
else
    log "${ENV_FILE} already exists — leaving untouched"
fi

# --- systemd unit ------------------------------------------------------------
log "writing ${SERVICE_FILE}"
cat >"${SERVICE_FILE}" <<EOF
[Unit]
Description=HEAXHub Worker Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${INSTALL_USER}
Group=${INSTALL_GROUP}
EnvironmentFile=-${ENV_FILE}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/HeaxAgent
Restart=on-failure
RestartSec=5

# Hardening (best-effort)
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
ReadWritePaths=${WORK_ROOT}

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "${SERVICE_FILE}"

# --- activate ----------------------------------------------------------------
log "reloading systemd"
systemctl daemon-reload

log "enabling + starting ${SERVICE_NAME}.service"
systemctl enable --now "${SERVICE_NAME}.service"

log "done. status:"
systemctl --no-pager --full status "${SERVICE_NAME}.service" || true
