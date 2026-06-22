#!/usr/bin/env bash
#
# backup.sh — HEAXHub 데이터 백업 (프로덕션 durability).
#
# 백업 대상:
#   1. Postgres 전체 덤프 (필수 — 사용자/앱/제출/잡 메타 등 모든 상태)
#   2. integrations/  매니페스트 (앱 등록의 source-of-truth; git 외 보존)
#   3. (옵션 --secrets) .env  — 시크릿 포함, 별도 권한 보관 필요
#
# SIF(var/sifs)·job_storage·app_workspaces 는 소스에서 재생성 가능하므로
# 기본 백업에서 제외(필요 시 --full 로 SIF 포함).
#
# 사용법:
#   scripts/backup.sh [출력디렉터리] [--full] [--secrets] [--keep N]
#     기본 출력: var/backups/
#     --full    : var/sifs 도 포함 (대용량)
#     --secrets : .env 포함 (민감)
#     --keep N  : 최근 N개만 남기고 오래된 백업 삭제 (기본 7)
#
# cron 예 (매일 03:30):
#   30 3 * * * cd /path/HEAXHub && scripts/backup.sh >> var/logs/backup.log 2>&1
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OUT_DIR="$REPO_ROOT/var/backups"
FULL=0
WITH_SECRETS=0
KEEP=7
for arg in "$@"; do
  case "$arg" in
    --full)    FULL=1 ;;
    --secrets) WITH_SECRETS=1 ;;
    --keep=*)  KEEP="${arg#--keep=}" ;;
    --keep)    : ;;  # value handled below if numeric follows — keep simple: use --keep=N
    *)         OUT_DIR="$arg" ;;
  esac
done

# .env 로드 (DATABASE_URL 파싱용)
[ -f .env ] && set -a && . ./.env && set +a

# 타임스탬프 (Date 불가 환경 대비: date 사용)
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$OUT_DIR/heaxhub-$TS"
mkdir -p "$DEST"

echo "[backup] → $DEST"

# ── 1. Postgres 덤프 ────────────────────────────────────────────────────────
# DATABASE_URL=postgresql+psycopg://user:pass@host:port/db 파싱
DBURL="${DATABASE_URL:-}"
if [ -z "$DBURL" ]; then
  echo "[backup] 오류: DATABASE_URL 미설정" >&2; exit 1
fi
# psycopg 드라이버 접미사 제거 → 표준 libpq URL
PGURL="$(echo "$DBURL" | sed -E 's#postgresql\+[a-z0-9]+://#postgresql://#')"

APT="$REPO_ROOT/deploy/apptainer/.tools/apptainer-1.3.6/usr/bin/apptainer"
[ -x "$APT" ] || APT="$(command -v apptainer || true)"

# pg_dump 은 postgres 인스턴스 안에서 실행 (호스트에 미설치일 수 있음).
# 인스턴스가 host network 라 localhost:port 로 자기 자신에 붙는다.
if [ -n "$APT" ] && $APT instance list 2>/dev/null | grep -q heax-pg; then
  $APT exec instance://heax-pg pg_dump "$PGURL" --no-owner --format=custom \
    > "$DEST/postgres.dump"
elif command -v pg_dump >/dev/null 2>&1; then
  pg_dump "$PGURL" --no-owner --format=custom > "$DEST/postgres.dump"
else
  echo "[backup] 오류: pg_dump 를 찾을 수 없음 (heax-pg 인스턴스도 없음)" >&2; exit 1
fi
DB_SIZE="$(du -h "$DEST/postgres.dump" | cut -f1)"
echo "[backup] postgres.dump ($DB_SIZE)"

# ── 2. integrations 매니페스트 ──────────────────────────────────────────────
tar czf "$DEST/integrations.tgz" -C "$REPO_ROOT" integrations 2>/dev/null && \
  echo "[backup] integrations.tgz ($(du -h "$DEST/integrations.tgz" | cut -f1))"

# ── 3. (옵션) .env ──────────────────────────────────────────────────────────
if [ "$WITH_SECRETS" = "1" ] && [ -f .env ]; then
  cp .env "$DEST/.env"
  chmod 600 "$DEST/.env"
  echo "[backup] .env 포함 (chmod 600)"
fi

# ── 4. (옵션 --full) SIF ────────────────────────────────────────────────────
if [ "$FULL" = "1" ] && [ -d var/sifs ]; then
  tar czf "$DEST/sifs.tgz" -C "$REPO_ROOT/var" sifs 2>/dev/null && \
    echo "[backup] sifs.tgz ($(du -h "$DEST/sifs.tgz" | cut -f1))"
fi

# 메타데이터
cat > "$DEST/MANIFEST.txt" <<EOF
HEAXHub backup
created: $TS
full: $FULL  secrets: $WITH_SECRETS
db: postgres.dump (pg_restore --clean -d <url> postgres.dump)
integrations: integrations.tgz
EOF

# ── 5. 보존 정책 (최근 KEEP개만) ────────────────────────────────────────────
cd "$OUT_DIR"
ls -1dt heaxhub-* 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -rf "$old" && echo "[backup] 오래된 백업 삭제: $old"
done

echo "[backup] 완료: $DEST"
echo "[restore] 복원: pg_restore --clean --no-owner -d \"\$PGURL\" $DEST/postgres.dump"
