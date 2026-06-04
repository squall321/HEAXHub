#!/usr/bin/env bash
# scripts/prepare_offline_bundle.sh
#
# 온라인 staging 박스에서 실행해 오프라인 Ubuntu 24.04 타깃으로 옮길
# heaxhub-bundle-<VERSION>.tar.gz 를 만든다.
#
# 번들 구성:
#   heaxhub-bundle-<VERSION>/
#     wheels/         pip download 결과 (모든 백엔드 의존성)
#     sifs/           ~/serviceApptainers/*.sif 의 심볼릭 링크
#     agents/         linux-x64 / win-x64 self-contained 바이너리
#     frontend-dist/  pnpm build 산출물
#     config/         interpreters.yaml / sif_registry.yaml / .env.template
#     scripts/        install_offline.sh 및 런타임 스크립트
#     README_OFFLINE.md
#     offline_bundle.json   메타데이터 매니페스트
#
# 옵션:
#   --dry-run          실제로 만들지 않고 무엇이 들어갈지만 출력
#   --version <v>      번들 버전 명시 (기본: git describe 또는 timestamp)
#   --skip-frontend    프론트엔드 빌드 생략 (이미 dist/ 가 있을 때)
#   --skip-agent       .NET 에이전트 빌드 생략
#   --skip-wheels      pip download 생략
#   --with-toolchains  deploy/apptainer/heaxhub_toolchain_*.sif 도 번들에 포함
#                      (기본 미포함 — 4개 합쳐 ~2 GB 라서 명시적으로 켜야 함)
#   --output-dir <d>   최종 tar.gz 저장 위치 (기본: ./dist-bundle)
#
# 사용 예:
#   bash scripts/prepare_offline_bundle.sh
#   bash scripts/prepare_offline_bundle.sh --dry-run 2>&1 | head -40
#
set -euo pipefail

# ─── 기본 경로 ──────────────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT}/backend"
FRONTEND_DIR="${ROOT}/frontend"
AGENT_DIR="${ROOT}/agents/windows"
CONFIG_DIR="${ROOT}/config"
SCRIPTS_DIR="${ROOT}/scripts"
SIF_SOURCE="${SIF_SOURCE:-${HOME}/serviceApptainers}"

DRY_RUN=0
SKIP_FRONTEND=0
SKIP_AGENT=0
SKIP_WHEELS=0
WITH_TOOLCHAINS=0
OUTPUT_DIR="${ROOT}/dist-bundle"
VERSION=""

# ─── 인자 파싱 ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)        DRY_RUN=1; shift ;;
    --skip-frontend)  SKIP_FRONTEND=1; shift ;;
    --skip-agent)     SKIP_AGENT=1; shift ;;
    --skip-wheels)    SKIP_WHEELS=1; shift ;;
    --with-toolchains) WITH_TOOLCHAINS=1; shift ;;
    --version)        VERSION="$2"; shift 2 ;;
    --output-dir)     OUTPUT_DIR="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
    *)
      echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  if VERSION="$(git -C "$ROOT" describe --tags --always --dirty 2>/dev/null)"; then
    :
  else
    VERSION="$(date +%Y%m%d-%H%M%S)"
  fi
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BUNDLE_NAME="heaxhub-bundle-${VERSION}"
STAGE="${OUTPUT_DIR}/${BUNDLE_NAME}"
TARBALL="${OUTPUT_DIR}/${BUNDLE_NAME}-${TIMESTAMP}.tar.gz"
MANIFEST="${STAGE}/offline_bundle.json"

log()  { echo "[bundle] $*"; }
warn() { echo "[bundle][WARN] $*" >&2; }
err()  { echo "[bundle][ERR] $*" >&2; }

run() {
  # dry-run 시 명령 출력만, 실제 실행하지 않음
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  DRY: $*"
  else
    eval "$@"
  fi
}

mkstage() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  DRY: mkdir -p $1"
  else
    mkdir -p "$1"
  fi
}

# ─── 0) 사전 점검 ───────────────────────────────────────────────────────────
log "version       = ${VERSION}"
log "bundle name   = ${BUNDLE_NAME}"
log "stage dir     = ${STAGE}"
log "output tar    = ${TARBALL}"
log "dry-run       = ${DRY_RUN}"

[[ -f "${BACKEND_DIR}/pyproject.toml" ]] || { err "backend/pyproject.toml not found"; exit 1; }
[[ -d "${BACKEND_DIR}/.venv" ]] || warn "backend/.venv not found — wheels step will try system pip"

# ─── 1) stage 디렉터리 준비 ─────────────────────────────────────────────────
log "preparing stage tree under ${STAGE}"
if [[ "$DRY_RUN" -eq 0 && -d "$STAGE" ]]; then
  rm -rf "$STAGE"
fi
mkstage "${STAGE}/wheels"
mkstage "${STAGE}/sifs"
mkstage "${STAGE}/agents/linux-x64"
mkstage "${STAGE}/agents/win-x64"
mkstage "${STAGE}/frontend-dist"
mkstage "${STAGE}/config"
mkstage "${STAGE}/scripts"

# ─── 2) wheels (pip download) ───────────────────────────────────────────────
WHEEL_COUNT=0
if [[ "$SKIP_WHEELS" -eq 1 ]]; then
  log "skip wheels (per flag)"
else
  log "downloading wheels via pip…"
  PIP="${BACKEND_DIR}/.venv/bin/pip"
  PY="${BACKEND_DIR}/.venv/bin/python"
  if [[ ! -x "$PIP" ]]; then
    PIP="$(command -v pip3 || command -v pip || true)"
    PY="$(command -v python3 || command -v python || true)"
  fi
  [[ -x "$PIP" ]] || { err "pip not found"; exit 1; }

  # pip freeze 결과를 임시 파일로 떠서 download 입력으로 사용
  FREEZE_TMP="$(mktemp)"
  trap 'rm -f "$FREEZE_TMP"' EXIT
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  DRY: $PIP freeze > $FREEZE_TMP"
    echo "  DRY: $PIP download -d ${STAGE}/wheels -r $FREEZE_TMP"
    # dry-run 에서도 카운트 추정을 위해 freeze는 실제로 한 번 시도
    "$PIP" freeze > "$FREEZE_TMP" 2>/dev/null || true
    WHEEL_COUNT=$(grep -cve '^\s*$' "$FREEZE_TMP" 2>/dev/null || echo 0)
  else
    "$PIP" freeze > "$FREEZE_TMP"
    # -e (editable) 항목은 download가 못 푸니 제거
    sed -i '/^-e /d; /^# /d' "$FREEZE_TMP"
    "$PIP" download -d "${STAGE}/wheels" -r "$FREEZE_TMP" \
      --no-build-isolation || warn "pip download had errors — check ${STAGE}/wheels"
    # 추가: 백엔드 자체도 source dist로 한 번 더 떠둠 (egg/sdist)
    (cd "$BACKEND_DIR" && "$PY" -m pip wheel . -w "${STAGE}/wheels" --no-deps) \
      || warn "could not wheel backend itself — will fall back to -e install"
    WHEEL_COUNT=$(find "${STAGE}/wheels" -maxdepth 1 -type f \
                  \( -name '*.whl' -o -name '*.tar.gz' \) | wc -l)
  fi
  log "wheels staged: ${WHEEL_COUNT}"
fi

# ─── 3) SIFs (symlink to staging) ───────────────────────────────────────────
log "linking SIFs from ${SIF_SOURCE}"
SIF_LIST=(
  heaxhub_postgres.sif
  heaxhub_redis.sif
  heaxhub_mailhog.sif
  heaxhub_caddy.sif
  KooSimulationPython313.sif
)
SIF_COUNT=0
SIF_MISSING=()
for sif in "${SIF_LIST[@]}"; do
  src="${SIF_SOURCE}/${sif}"
  dst="${STAGE}/sifs/${sif}"
  if [[ -f "$src" ]]; then
    run "ln -sf '${src}' '${dst}'"
    SIF_COUNT=$((SIF_COUNT+1))
  else
    SIF_MISSING+=("$sif")
    warn "missing SIF: ${src}"
  fi
done
log "SIFs linked   : ${SIF_COUNT}/${#SIF_LIST[@]}"
[[ ${#SIF_MISSING[@]} -gt 0 ]] && warn "missing list: ${SIF_MISSING[*]}"

# ─── 3b) toolchain SIFs (opt-in via --with-toolchains) ──────────────────────
TOOLCHAIN_SRC="${ROOT}/deploy/apptainer"
TOOLCHAIN_LIST=(
  heaxhub_toolchain_nodejs20.sif
  heaxhub_toolchain_python312.sif
  heaxhub_toolchain_go122.sif
  heaxhub_toolchain_polyglot.sif
)
TOOLCHAIN_COUNT=0
TOOLCHAIN_MISSING=()
if [[ "$WITH_TOOLCHAINS" -eq 1 ]]; then
  log "including toolchain SIFs from ${TOOLCHAIN_SRC}"
  for sif in "${TOOLCHAIN_LIST[@]}"; do
    src="${TOOLCHAIN_SRC}/${sif}"
    dst="${STAGE}/sifs/${sif}"
    if [[ -f "$src" ]]; then
      run "ln -sf '${src}' '${dst}'"
      TOOLCHAIN_COUNT=$((TOOLCHAIN_COUNT+1))
    else
      TOOLCHAIN_MISSING+=("$sif")
      warn "missing toolchain SIF: ${src} (먼저 bash deploy/apptainer/build-toolchains.sh)"
    fi
  done
  log "toolchains    : ${TOOLCHAIN_COUNT}/${#TOOLCHAIN_LIST[@]}"
  [[ ${#TOOLCHAIN_MISSING[@]} -gt 0 ]] && warn "missing toolchains: ${TOOLCHAIN_MISSING[*]}"
else
  log "skip toolchain SIFs (use --with-toolchains to include ~2 GB of heaxhub_toolchain_*.sif)"
fi

# ─── 4) HeaxAgent (dotnet publish) ─────────────────────────────────────────
AGENT_LINUX_OUT="${STAGE}/agents/linux-x64"
AGENT_WIN_OUT="${STAGE}/agents/win-x64"
AGENT_LINUX_BIN=""
AGENT_WIN_BIN=""

if [[ "$SKIP_AGENT" -eq 1 ]]; then
  log "skip agent build (per flag)"
elif [[ ! -f "${AGENT_DIR}/HeaxAgent.csproj" ]]; then
  warn "agent csproj not found at ${AGENT_DIR} — skipping"
else
  if command -v dotnet >/dev/null 2>&1; then
    log "publishing HeaxAgent (linux-x64)"
    run "(cd '${AGENT_DIR}' && dotnet publish -c Release -r linux-x64 \
          --self-contained true -p:PublishSingleFile=true \
          -o '${AGENT_LINUX_OUT}' >/dev/null)"
    log "publishing HeaxAgent (win-x64)"
    run "(cd '${AGENT_DIR}' && dotnet publish -c Release -r win-x64 \
          --self-contained true -p:PublishSingleFile=true \
          -o '${AGENT_WIN_OUT}' >/dev/null)"
    if [[ "$DRY_RUN" -eq 0 ]]; then
      AGENT_LINUX_BIN="${AGENT_LINUX_OUT}/HeaxAgent"
      AGENT_WIN_BIN="${AGENT_WIN_OUT}/HeaxAgent.exe"
    else
      AGENT_LINUX_BIN="${AGENT_LINUX_OUT}/HeaxAgent (would be built)"
      AGENT_WIN_BIN="${AGENT_WIN_OUT}/HeaxAgent.exe (would be built)"
    fi
  else
    warn "dotnet not installed — skipping agent build"
  fi
fi
log "agent linux   : ${AGENT_LINUX_BIN:-<none>}"
log "agent windows : ${AGENT_WIN_BIN:-<none>}"

# ─── 5) frontend dist ───────────────────────────────────────────────────────
FRONTEND_SIZE="0"
if [[ "$SKIP_FRONTEND" -eq 1 ]]; then
  log "skip frontend build (per flag)"
elif [[ ! -f "${FRONTEND_DIR}/package.json" ]]; then
  warn "frontend/package.json not found — skipping"
else
  if command -v pnpm >/dev/null 2>&1; then
    log "building frontend via pnpm…"
    run "(cd '${FRONTEND_DIR}' && pnpm install --frozen-lockfile >/dev/null && pnpm build >/dev/null)"
  else
    warn "pnpm not installed — assuming frontend/dist already built"
  fi
  if [[ -d "${FRONTEND_DIR}/dist" ]]; then
    run "cp -r '${FRONTEND_DIR}/dist/.' '${STAGE}/frontend-dist/'"
    if [[ "$DRY_RUN" -eq 0 ]]; then
      FRONTEND_SIZE="$(du -sh "${STAGE}/frontend-dist" 2>/dev/null | awk '{print $1}')"
    else
      FRONTEND_SIZE="$(du -sh "${FRONTEND_DIR}/dist" 2>/dev/null | awk '{print $1}')"
    fi
  else
    warn "frontend dist/ missing after build"
  fi
fi
log "frontend dist : ${FRONTEND_SIZE}"

# ─── 6) config / scripts ────────────────────────────────────────────────────
log "copying config + scripts"
if [[ -f "${CONFIG_DIR}/interpreters.yaml" ]]; then
  run "cp '${CONFIG_DIR}/interpreters.yaml' '${STAGE}/config/interpreters.yaml'"
fi
# sif_registry.yaml: 번들에 들어간 SIF 목록을 운영용 레지스트리로 떠둠
if [[ "$DRY_RUN" -eq 0 ]]; then
  {
    echo "# auto-generated by prepare_offline_bundle.sh @ ${TIMESTAMP}"
    echo "sifs:"
    for sif in "${SIF_LIST[@]}"; do
      [[ -L "${STAGE}/sifs/${sif}" || -f "${STAGE}/sifs/${sif}" ]] || continue
      key="${sif%.sif}"
      echo "  ${key}: \"\${SIF_DIR}/${sif}\""
    done
  } > "${STAGE}/config/sif_registry.yaml"
else
  echo "  DRY: write ${STAGE}/config/sif_registry.yaml"
fi
# .env.template: 기존 .env.example 을 가져와 비밀값을 비워둠
if [[ -f "${ROOT}/.env.example" ]]; then
  if [[ "$DRY_RUN" -eq 0 ]]; then
    sed -E 's/^(JWT_SECRET|SMTP_PASSWORD|ANTHROPIC_API_KEY|OPENAI_API_KEY)=.*/\1=/' \
        "${ROOT}/.env.example" > "${STAGE}/config/.env.template"
  else
    echo "  DRY: derive ${STAGE}/config/.env.template from .env.example"
  fi
fi

# install_offline.sh + 런타임 스크립트 복사
run "cp '${SCRIPTS_DIR}/install_offline.sh' '${STAGE}/scripts/install_offline.sh'"
for s in build_apptainer_sif.sh build_python_venv.sh healthcheck.sh \
         provision_workspace.sh rotate_job_storage.sh watchdog.sh; do
  [[ -f "${SCRIPTS_DIR}/${s}" ]] && run "cp '${SCRIPTS_DIR}/${s}' '${STAGE}/scripts/${s}'"
done
# 부팅 autostart 설치 스크립트도 같이 보냄
[[ -f "${ROOT}/install_autostart.sh" ]] && \
  run "cp '${ROOT}/install_autostart.sh' '${STAGE}/scripts/install_autostart.sh'"

# ─── 7) README_OFFLINE.md ───────────────────────────────────────────────────
if [[ "$DRY_RUN" -eq 0 ]]; then
  cat > "${STAGE}/README_OFFLINE.md" <<EOF
# HEAXHub 오프라인 번들 — ${BUNDLE_NAME}

생성 시각: ${TIMESTAMP}
SIF 원본 : ${SIF_SOURCE}

## 빠른 설치

\`\`\`bash
tar xzf ${BUNDLE_NAME}-${TIMESTAMP}.tar.gz
cd ${BUNDLE_NAME}
bash scripts/install_offline.sh
\`\`\`

자세한 운영자 가이드는 \`docs/OFFLINE_DEPLOY.md\` 참고.
EOF
else
  echo "  DRY: write ${STAGE}/README_OFFLINE.md"
fi

# ─── 8) offline_bundle.json (manifest) ──────────────────────────────────────
if [[ "$DRY_RUN" -eq 0 ]]; then
  python3 - "$STAGE" "$VERSION" "$TIMESTAMP" "$WHEEL_COUNT" "$SIF_COUNT" \
                 "$FRONTEND_SIZE" "$AGENT_LINUX_BIN" "$AGENT_WIN_BIN" \
                 "${SIF_LIST[@]}" <<'PY' > "${MANIFEST}"
import json, os, sys
stage, version, ts, wheels, sif_count, fsize, alin, awin, *sifs = sys.argv[1:]
m = {
  "bundle":   os.path.basename(stage),
  "version":  version,
  "built_at": ts,
  "wheels":   {"count": int(wheels), "dir": "wheels/"},
  "sifs":     {"count": int(sif_count), "list": sifs, "dir": "sifs/"},
  "agents":   {"linux_x64": alin, "win_x64": awin, "dir": "agents/"},
  "frontend": {"size": fsize, "dir": "frontend-dist/"},
  "config":   ["interpreters.yaml", "sif_registry.yaml", ".env.template"],
}
print(json.dumps(m, indent=2, ensure_ascii=False))
PY
fi

# ─── 9) tar ─────────────────────────────────────────────────────────────────
if [[ "$DRY_RUN" -eq 0 ]]; then
  log "creating tarball ${TARBALL}"
  (cd "${OUTPUT_DIR}" && tar czf "$(basename "$TARBALL")" "${BUNDLE_NAME}")
  BUNDLE_SIZE="$(du -sh "$TARBALL" | awk '{print $1}')"
  FILE_COUNT="$(find "$STAGE" | wc -l)"
  log "tarball size  : ${BUNDLE_SIZE}"
  log "file count    : ${FILE_COUNT}"
else
  log "DRY: would tar -> ${TARBALL}"
fi

# ─── 10) 요약 ───────────────────────────────────────────────────────────────
echo ""
echo "================ bundle summary ================"
echo " version         : ${VERSION}"
echo " bundle dir      : ${STAGE}"
echo " wheels count    : ${WHEEL_COUNT}"
echo " sifs count      : ${SIF_COUNT} (of ${#SIF_LIST[@]})"
echo " toolchains      : ${TOOLCHAIN_COUNT} (of ${#TOOLCHAIN_LIST[@]}, --with-toolchains=${WITH_TOOLCHAINS})"
echo " agent linux-x64 : ${AGENT_LINUX_BIN:-<skipped>}"
echo " agent win-x64   : ${AGENT_WIN_BIN:-<skipped>}"
echo " frontend dist   : ${FRONTEND_SIZE}"
echo " dry-run         : ${DRY_RUN}"
echo "================================================"
