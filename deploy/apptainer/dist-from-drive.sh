#!/usr/bin/env bash
# Pull HEAXHub fallback artifacts from Google Drive (rclone) and place them: frontend dist →
# frontend/dist, vendored runtimes (apptainer.deb/python.tar.gz) → deploy/apptainer/cache/,
# base SIFs (base_*.sif) + service SIFs → ~/serviceApptainers. Lets a Drive-reachable but
# Docker-Hub/PyPI/GitHub-blocked server set up + build without those upstreams.
#
# Needs in .env:  HEAX_DRIVE_REMOTE=HeaxDrive:HEAXHub/dist
# After this:  bash deploy/apptainer/start.sh   (frontend/dist present → install_all skips the build)
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
# Read ONLY the keys we need from .env (don't `source` it — a value with an unquoted space would
# run as a command, e.g. `Admin: command not found`).
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed 's/^["'"'"']//; s/["'"'"']$//'; }
HEAX_DRIVE_REMOTE="${HEAX_DRIVE_REMOTE:-$(env_get HEAX_DRIVE_REMOTE)}"
SIF_DIR="${SIF_DIR:-$(env_get SIF_DIR)}"

command -v rclone >/dev/null 2>&1 || { echo "✗ rclone not installed (https://rclone.org/install/)"; exit 1; }
REMOTE="${HEAX_DRIVE_REMOTE:-}"
[ -n "$REMOTE" ] || { echo "✗ HEAX_DRIVE_REMOTE not set in .env (e.g. HeaxDrive:HEAXHub/dist)"; exit 1; }
REMOTE="${REMOTE%/}"

SRC="$REMOTE/latest"
# 파이프+조기종료(grep -q)는 pipefail 아래서 SIGPIPE 오판을 만든다 — 목록을 먼저 받는다
# (오판하면 latest/ 를 건너뛰고 **조용히 낡은 dist** 로 떨어진다).
_listing="$(rclone lsf "$SRC/" 2>/dev/null || true)"
case $'\n'"$_listing"$'\n' in
  *$'\n'frontend-dist.tar.gz$'\n'*) _have_dist=1 ;;
  *) _have_dist=0 ;;
esac
if [ "$_have_dist" = "0" ]; then
  NEWEST="$(rclone lsf --dirs-only "$REMOTE/" 2>/dev/null | sed 's#/$##' | grep -E '^dist-' | sort | tail -n 1 || true)"
  [ -n "$NEWEST" ] || { echo "✗ no dist on $REMOTE. Push from an online host: ./deploy/apptainer/dist-to-drive.sh"; exit 1; }
  SRC="$REMOTE/$NEWEST"
fi
echo "→ source: $SRC"

# 같은 내용이면 손대지 않는다 — 살아 있는 apptainer 인스턴스 밑의 SIF 를 덮어쓰면 squashfs 가 깨지고, cp 는 mtime 을 보존해(-p) 포털 update-all 의
# 재기동 판정(지문: 이름·크기·mtime)이 안 바뀐 파일에서는 매번 달라지지 않는다. 영구 캐시(rclone 이 안 바뀐 파일을 건너뛴다)와 짝이다. HWAXPortal docs/update-all-skip-unchanged.
#
# 설치는 원자적이다 — 대상 옆 임시 파일에 cp -p 로 받고 mv 로 이름을 바꾼다. 제자리 덮어쓰기(옛 cp -p 가 대상을 열어 그 위에 덮는 것)는 그 SIF 로
# 도는 apptainer 인스턴스 밑에서 squashfs 를 바꿔 EIO/Bus error 를 낸다 — mv 로 이름을 바꾸면 도는 프로세스는 옛 inode 를 쥔 채 그대로 돌고, 다음
# 기동이 새 파일을 연다. 끊기면(신호) 임시 파일을 지운다(서브셸 EXIT trap). 임시 파일은 대상과 같은 확장자로 끝나 `*.sif` gitignore 글롭에도
# 걸린다(본보기: HWAXPortal infra/scripts/lib/change-detect.sh 의 hwax_install_if_changed, cae00 운영 수정요청 2026-10-27 HH-02).
_install_if_changed() {  # $1=src $2=dst — 0=설치/동일 · 2=실패
  if [ -f "$2" ] && cmp -s "$1" "$2"; then echo "  · $(basename "$2") 같음 — 그대로"; return 0; fi
  local dir base ext tmp rc=0
  dir="$(dirname -- "$2")"; base="$(basename -- "$2")"
  case "$base" in *.*) ext=".${base##*.}" ;; *) ext="" ;; esac
  ( tmp=""
    trap '[ -n "$tmp" ] && rm -f -- "$tmp"' EXIT
    tmp="$(mktemp "$dir/.$base.XXXXXX$ext")" || exit 1
    cp -p -- "$1" "$tmp" || exit 1
    mv -fT -- "$tmp" "$2" || exit 1
    tmp=""
  ) || rc=$?
  [ "$rc" = 0 ] && return 0
  echo "  ✗ $base 설치 실패 — $2 에 쓸 수 없다(권한·소유자·디스크·끊김). 옛 파일이 그대로다" >&2
  return 2
}
# 영구 캐시 — 임시 디렉터리면 rclone 이 비교할 것이 없어 매번 전량 전송이다(앱 SIF 여럿, Drive ~2MB/s). 캐시에 받으면 안 바뀐 파일은 전송 0.
STAGE="${HEAX_DRIVE_CACHE:-$ROOT_DIR/deploy/apptainer/cache/.drive-dist}"; mkdir -p "$STAGE"
# 이번에 옮긴 파일을 가리려고 sync 앞뒤로 캐시의 (이름 · 크기 · 수정시각) 을 찍는다(SE-8).
_snap() { find "$STAGE" -maxdepth 1 -type f -printf '%f\t%s\t%T@\n' | sort; }
_before="$(_snap)"
# sync — 원격에서 뺀 파일이 캐시에 남아 옛 SIF 가 되살아나지 않게. --progress 는 운영 로그에 줄을 쏟아 한 줄 통계로 바꿨다.
rclone sync --stats-one-line --stats-log-level NOTICE --stats 30s "$SRC/" "$STAGE/"
CHANGED="$(comm -13 <(printf '%s\n' "$_before") <(_snap) | cut -f1)"
# 앞 실행이 검사 · 풀기 전에 끊겼거나 검사에 실패했으면 그때 옮긴 파일은 이번엔 '안 바뀜'으로 보인다 — 적어 둔 목록을 더한다.
# 표시 파일들은 캐시 밖(형제)에 둔다 — 캐시 안에 두면 다음 sync 가 원격에 없는 파일로 보고 지운다.
PENDING="${STAGE%/}.pending"
CHANGED="$( { printf '%s\n' "$CHANGED"; cat "$PENDING" 2>/dev/null || true; } | sed '/^$/d' | sort -u)"
printf '%s\n' "$CHANGED" > "$PENDING"

# 무결성 — 13GB 캐시 전체에 sha256sum -c 를 매번 돌던 것을 이번에 옮긴 파일만으로 줄이고, 전체 검사는 주 1회 한다(SE-8).
FULL_MARK="${STAGE%/}.full-check"
if [ -f "$STAGE/SHA256SUMS" ]; then
  if [ ! -f "$FULL_MARK" ] || [ -n "$(find "$FULL_MARK" -mmin +$((7 * 24 * 60)) 2>/dev/null)" ]; then
    ( cd "$STAGE" && sha256sum -c --quiet SHA256SUMS ) || { echo "✗ checksum failed"; exit 1; }
    touch "$FULL_MARK"; echo "  ✓ checksums OK(전체 — 주 1회)"
  else
    _sums="$(awk -v list="$CHANGED" 'BEGIN { n = split(list, a, "\n"); for (i = 1; i <= n; i++) if (a[i] != "") want[a[i]] = 1 }
                                    { f = $2; sub(/^\*/, "", f); if (f in want) print }' "$STAGE/SHA256SUMS")"
    if [ -n "$_sums" ]; then
      # 실패하면 다음 실행이 전체 검사를 하게 표시를 지운다(옮긴 목록도 PENDING 에 남는다).
      ( cd "$STAGE" && printf '%s\n' "$_sums" | sha256sum -c --quiet - ) || { rm -f "$FULL_MARK"; echo "✗ checksum failed"; exit 1; }
      echo "  ✓ checksums OK(이번에 옮긴 $(printf '%s\n' "$_sums" | wc -l)개)"
    else
      echo "  · 옮긴 파일 없음 — checksum 생략(전체 검사는 주 1회)"
    fi
  fi
fi

# Restore frontend/dist (있을 때만 — 런타임/base 만 올라온 푸시도 지원). 바뀐 때만 푼다(SE-8).
if [ -f "$STAGE/frontend-dist.tar.gz" ]; then
  case $'\n'"$CHANGED"$'\n' in *$'\n'frontend-dist.tar.gz$'\n'*) _fd_changed=1 ;; *) _fd_changed=0 ;; esac   # 위 _listing 과 같은 까닭으로 파이프+grep -q 를 안 쓴다
  if [ "$_fd_changed" = 1 ] || [ ! -f "$ROOT_DIR/frontend/dist/index.html" ]; then
    ( cd "$ROOT_DIR/frontend" && tar -xzf "$STAGE/frontend-dist.tar.gz" )
    echo "  ✓ extracted frontend/dist"
  else
    echo "  · frontend-dist 그대로 — 풀기 생략"
  fi
else
  echo "  · frontend-dist 없음 — 런타임/base 만 반입"
fi
rm -f "$PENDING"   # 검사 · 풀기까지 끝났다 — 아래 SIF 설치는 cmp 로 스스로 가린다

# Service SIFs (postgres/redis/caddy/mailhog) — cae00 can't pull/build them, so stage whatever was
# shipped into ~/serviceApptainers (create the dir; start.sh expects it there).
SIFDIR="${SIF_DIR:-$HOME/serviceApptainers}"
shopt -s nullglob
sifs=("$STAGE"/heaxhub_*.sif)
if [ ${#sifs[@]} -gt 0 ]; then
  mkdir -p "$SIFDIR"
  for s in "${sifs[@]}"; do _install_if_changed "$s" "$SIFDIR/$(basename "$s")"; echo "  ✓ staged $(basename "$s") → $SIFDIR"; done
fi
shopt -u nullglob

# 벤더링 런타임 → deploy/apptainer/cache/ (install-apptainer/install-python 가 .tools 로 추출)
mkdir -p "$ROOT_DIR/deploy/apptainer/cache"
shopt -s nullglob
for v in "$STAGE"/apptainer_*.deb "$STAGE"/python-*-x86_64-linux.tar.gz; do
  _install_if_changed "$v" "$ROOT_DIR/deploy/apptainer/cache/$(basename "$v")"; echo "  ✓ staged $(basename "$v") → deploy/apptainer/cache/"
done
# base image SIF → SIFDIR (builder 가 localimage 로 사용)
for b in "$STAGE"/base_*.sif; do
  mkdir -p "$SIFDIR"; _install_if_changed "$b" "$SIFDIR/$(basename "$b")"; echo "  ✓ staged $(basename "$b") → $SIFDIR"
done
shopt -u nullglob

# per-app SIFs → var/sifs/  (heaxhub_*/base_* 아닌 *.sif = 등록 앱 SIF).
# 폐쇄망 서버가 git·빌드 없이 이 SIF 로 앱을 바로 띄운다(.sif.hash 도 함께 = 스캔이
# 커밋 일치로 인식해 재빌드 스킵). start.sh/스캔이 var/sifs/<slug>.sif 를 그대로 사용.
mkdir -p "$ROOT_DIR/var/sifs"
shopt -s nullglob
for s in "$STAGE"/*.sif; do
  case "$(basename "$s")" in heaxhub_*|base_*) continue;; esac
  _install_if_changed "$s" "$ROOT_DIR/var/sifs/$(basename "$s")"; echo "  ✓ app SIF $(basename "$s") → var/sifs/"
  [ -f "$s.hash" ] && cp -p "$s.hash" "$ROOT_DIR/var/sifs/"
done
shopt -u nullglob

echo
echo "✓ dist ready — now run:  bash deploy/apptainer/start.sh   (Caddy serves it; no build)"
