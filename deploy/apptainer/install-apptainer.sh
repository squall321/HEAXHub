#!/usr/bin/env bash
# HEAXHub — 핀 apptainer 설치 (시스템 무손상).
#
# 왜:
#   서버에 시스템 apptainer 가 이미 있어도 (또는 없어도) HEAXHub 는 검증된
#   핀버전(기본 1.3.6)으로 돌려야 한다. apt 로 깔면 시스템 버전과 충돌하거나
#   root 권한이 필요하므로, .deb 를 dpkg-deb -x 로 프로젝트 로컬 prefix
#   (.tools/apptainer-<ver>/) 에 "풀기만" 한다 — root 불필요, 시스템 무손상.
#
# 시스템 apptainer 가 있어도 _common.sh 의 apptainer() 함수가 핀버전을
# 우선 라우팅하므로 alias / PATH 설정이 없어도 그대로 동작한다.
#
# 사용:
#   bash deploy/apptainer/install-apptainer.sh
#   APPT_VERSION=1.3.6 bash deploy/apptainer/install-apptainer.sh
#   bash deploy/apptainer/install-apptainer.sh --deb /path/apptainer.deb
#   bash deploy/apptainer/install-apptainer.sh --force
set -euo pipefail

# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env 2>/dev/null || true
export_proxy 2>/dev/null || true

VER="${APPT_VERSION:-1.3.6}"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
PREFIX="$TOOLS_DIR/apptainer-${VER}"
BIN="$PREFIX/usr/bin/apptainer"
DEB_OVERRIDE=""
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deb)   DEB_OVERRIDE="${2:-}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *) err "unknown arg: $1"; exit 2 ;;
  esac
done

echo "================================================================"
echo " 핀 apptainer 설치  v${VER} (${ARCH})"
echo "  prefix : $PREFIX"
echo "  시스템 : $(command -v apptainer >/dev/null 2>&1 \
                  && (command apptainer --version 2>/dev/null) \
                  || echo '(없음)')   ← 무손상 유지"
echo "================================================================"

# 0) 이미 설치돼 있으면 skip
if [[ -x "$BIN" && $FORCE -eq 0 ]]; then
  ok "이미 설치됨: $("$BIN" --version 2>&1)"
  note "재설치하려면 --force"
  exit 0
fi

# 1) .deb 확보 우선순위: --deb > cache/ > infra/packages/deb/ > 다운로드
DEB=""
DEB_NAME="apptainer_${VER}_${ARCH}.deb"
SEARCH_DIRS=(
  "$CACHE_DIR"
  "$ROOT_DIR/infra/packages/deb"
  "$ROOT_DIR/infra/packages"
  "$ROOT_DIR/infra/deb"
  "$ROOT_DIR"
)

if [[ -n "$DEB_OVERRIDE" ]]; then
  [[ -f "$DEB_OVERRIDE" ]] || { err "--deb 파일 없음: $DEB_OVERRIDE"; exit 1; }
  DEB="$DEB_OVERRIDE"
  note "→ 로컬 .deb 사용: $DEB"
else
  for d in "${SEARCH_DIRS[@]}"; do
    [[ -d "$d" ]] || continue
    for f in "$d/$DEB_NAME" "$d/apptainer_${VER}-"*"_${ARCH}.deb"; do
      [[ -f "$f" ]] && { DEB="$f"; break 2; }
    done
  done
  if [[ -n "$DEB" ]]; then
    note "→ 캐시된 .deb 사용: $DEB"
  fi
fi

# 1b) 캐시 미스 → Drive 폴백 (서버가 Drive 는 닿고 GitHub 는 막힌 경우)
if [[ -z "$DEB" ]]; then
  if drive_fetch "$DEB_NAME" "$CACHE_DIR/$DEB_NAME"; then
    DEB="$CACHE_DIR/$DEB_NAME"
    ok "→ Drive 폴백에서 .deb 받음: $DEB"
  fi
fi

# 2) 캐시·Drive 미스 → 다운로드
if [[ -z "$DEB" ]]; then
  mkdir -p "$CACHE_DIR"
  DEB="$CACHE_DIR/$DEB_NAME"
  URL="https://github.com/apptainer/apptainer/releases/download/v${VER}/${DEB_NAME}"
  note "→ 다운로드: $URL"
  if ! curl -fL --retry 6 --retry-delay 5 --retry-all-errors \
            --connect-timeout 30 --max-time 600 \
            "$URL" -o "$DEB"; then
    rm -f "$DEB"
    err ".deb 다운로드 실패. 오프라인이면 .deb 를 직접 받아서 다음 위치에 두세요:"
    for d in "${SEARCH_DIRS[@]}"; do echo "    $d/$DEB_NAME"; done
    err "또는: bash $0 --deb /path/to/${DEB_NAME}"
    exit 1
  fi
  ok "캐시 저장: $DEB"
fi

# 3) .deb 를 .tools/ prefix 에 추출 (시스템 무손상)
mkdir -p "$PREFIX"
note "→ dpkg-deb -x $DEB $PREFIX"
dpkg-deb -x "$DEB" "$PREFIX"

# 3b) 런타임 트리 완성 — .deb 는 설정을 etc/apptainer/ 에 담고 usr/bin 만 넣는다. apptainer 바이너리는
#     usr/etc/apptainer·usr/var 를 찾으므로(relocated prefix 설치) 심링크와 var 를 직접 만든다.
#     이게 없으면 exec/instance 가 'capability.json/usr/var: no such file' → starter exit 255 로 죽는다.
#     이전 부분설치로 usr/etc·usr/var 가 실디렉토리로 남아있으면(심링크 아님) 지우고 다시 건다
#     — 설정 원본은 etc/ 에 안전히 있으므로 usr/etc 실디렉토리 제거는 무해하다.
for L in etc var; do
  [[ -L "$PREFIX/usr/$L" || ! -e "$PREFIX/usr/$L" ]] || rm -rf "$PREFIX/usr/$L"
done
mkdir -p "$PREFIX/var"
ln -sfn ../etc "$PREFIX/usr/etc"
ln -sfn ../var "$PREFIX/usr/var"

# 4) 검증
if [[ ! -x "$BIN" ]]; then
  err "추출 후에도 $BIN 가 실행 가능하지 않습니다."
  err "디렉터리 내용:"
  find "$PREFIX" -name apptainer -type f 2>&1 | head -10 >&2
  exit 1
fi

# apptainer 가 정상 동작하는지 확인 — 일부 1.3.x .deb 는 libsubid 등 의존성 필요
VER_OUT="$("$BIN" --version 2>&1 || true)"
if [[ -z "$VER_OUT" ]]; then
  warn "$BIN --version 빈 결과. ldd 진단:"
  ldd "$BIN" 2>&1 | grep -E "not found|=>" | head -10 >&2 || true
  err "핀 apptainer 가 실행되지 않습니다. 시스템 의존성을 설치하세요:"
  err "  sudo apt install -y libsubid4 fuse2fs squashfs-tools-ng uidmap"
  exit 1
fi

# 설정 트리(usr/etc/apptainer 심링크 경유 capability.json)가 실제로 잡히는지 확인
[[ -e "$PREFIX/usr/etc/apptainer/capability.json" && -e "$PREFIX/usr/var" ]] \
  || warn "런타임 트리 미완성(usr/etc/apptainer/capability.json 또는 usr/var) — 재설치/재점검 필요"

ok "설치 완료: $VER_OUT"
ok "binary    : $BIN"
note "_common.sh 가 자동으로 이 바이너리를 사용합니다 (HEAXHUB_APPT_BIN 환경변수로 override 가능)."
