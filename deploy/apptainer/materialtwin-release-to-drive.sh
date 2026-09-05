#!/usr/bin/env bash
# MaterialTwin 물성 카탈로그 하나만 떼어 Drive 의 **공개용 경로**로 올린다(소스 + DB).
#
# appdata-to-drive.sh 와 갈라 두는 이유가 이것이다 — 그쪽 `app-data.tar.gz` 는 `var/app_data` 를
# 통째로 담아 **앱 17개 데이터가 한 tarball 에 들어간다**(hwax_risk 의 risk_review.db 포함).
# 복원용이라 그게 맞지만, 그 파일을 "링크 있는 모든 사용자" 로 돌리면 카탈로그 하나 나눠 주려다
# 나머지 열여섯을 같이 공개하게 된다. 그래서 목적지도 tarball 도 따로 만든다.
#
#   보내는 곳   $HEAX_DRIVE_REMOTE 의 형제 폴더 `public/materialtwin/`  (app-data/ 와 분리)
#   담는 것     git 추적 소스(HEAD) + materialtwin.db 원자적 스냅샷
#   안 담는 것  .git · node_modules · scratchpad · 죽은 .pre-* 백업 · 다른 앱 데이터
#
# 사용:
#   ./materialtwin-release-to-drive.sh              # 소스 + DB
#   ./materialtwin-release-to-drive.sh --db-only    # DB 만
#   ./materialtwin-release-to-drive.sh --no-brief   # 수집 브리프 코퍼스 제외
#
# 업로드 뒤 **공개 전환은 Drive 쪽 작업이다** — rclone 은 파일을 올릴 뿐 공유 설정을 안 바꾼다.
# 폴더 우클릭 → 공유 → 일반 액세스 → 「링크가 있는 모든 사용자」 → 뷰어.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"     # HEAXHub
cd "$ROOT_DIR"
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed 's/^["'"'"']//; s/["'"'"']$//'; }

SRC_REPO="${MATERIALTWIN_REPO:-$HOME/claude/MaterialTwinWeb}"
LIVE_DB="$ROOT_DIR/var/app_data/materialtwin_web/materialtwin.db"

DB_ONLY=0; WITH_BRIEF=1
for a in "$@"; do
  case "$a" in
    --db-only)  DB_ONLY=1 ;;
    --no-brief) WITH_BRIEF=0 ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "✗ 모르는 인자: $a"; exit 2 ;;
  esac
done

REMOTE="${HEAX_DRIVE_REMOTE:-$(env_get HEAX_DRIVE_REMOTE)}"
[ -n "$REMOTE" ] || { echo "✗ HEAX_DRIVE_REMOTE 미설정 (.env, 예: ApptainerImages:HEAXHub/dist)"; exit 1; }
REMOTE="${REMOTE%/}"; REMOTE="${REMOTE%/dist}"          # dist 형제로 public/ 사용
DEST="$REMOTE/public/materialtwin"
RETAIN="${HEAX_DRIVE_RETAIN:-$(env_get HEAX_DRIVE_RETAIN)}"; RETAIN="${RETAIN:-5}"

RCLONE="$(command -v rclone || true)"
[ -n "$RCLONE" ] || { echo "✗ rclone 미설치 (https://rclone.org/install/)"; exit 1; }
[ -f "$LIVE_DB" ] || { echo "✗ 라이브 DB 없음: $LIVE_DB"; exit 1; }

TS="$(date -u +%Y%m%d-%H%M%SZ)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/pkg"; mkdir -p "$PKG"

# ── DB — WAL 때문에 cp 는 체크포인트 이전 상태를 복사한다(63차 VD 실측). backup API 로 뜬다.
python3 - "$LIVE_DB" "$PKG/materialtwin.db" <<'PY'
import sys, sqlite3
s = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
d = sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()
PY
echo "  · DB 스냅샷 $(du -h "$PKG/materialtwin.db" | cut -f1)"

# ── 소스 — git 추적 파일만. .git·node_modules·scratchpad 가 자동으로 빠진다.
if [ "$DB_ONLY" -eq 0 ]; then
  [ -d "$SRC_REPO/.git" ] || { echo "✗ 저장소 없음: $SRC_REPO (MATERIALTWIN_REPO 로 지정)"; exit 1; }
  # `[ ... ] && arr+=(...)` 를 쓰면 조건이 거짓일 때 목록의 종료상태가 1 이라 set -e 가 죽인다.
  # 빈 배열 전개도 `"${EXCL[@]:-}"` 로 쓰면 빈 문자열 인자 하나가 되어 git 이 빈 pathspec 로 받는다.
  EXCL=()
  if [ "$WITH_BRIEF" -eq 0 ]; then EXCL+=(':!docs/COLLECTION_BRIEF_CORPUS.md'); fi
  git -C "$SRC_REPO" archive --format=tar --prefix=MaterialTwinWeb/ \
    -o "$PKG/source.tar" HEAD -- . ${EXCL[@]+"${EXCL[@]}"}
  gzip -9 "$PKG/source.tar"
  NOTE=""; if [ "$WITH_BRIEF" -eq 0 ]; then NOTE=" (브리프 제외)"; fi
  echo "  · 소스 $(du -h "$PKG/source.tar.gz" | cut -f1)$NOTE"
fi

# ── 무엇이 들어 있는지 받는 쪽이 알게 한다. 값은 DB 에서 직접 센다.
python3 - "$PKG/materialtwin.db" "$SRC_REPO" "$TS" > "$PKG/README.txt" <<'PY'
import sys, sqlite3, subprocess
db, repo, ts = sys.argv[1], sys.argv[2], sys.argv[3]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
one = lambda q: c.execute(q).fetchone()[0]
try:
    rev = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip() or "?"
except Exception:
    rev = "?"
print(f"""MaterialTwin 물성 카탈로그 — 스냅샷 {ts}
커밋 {rev}

담긴 것
  materialtwin.db   SQLite. 물성값 {one('select count(*) from property_value'):,}건 ·
                    재료 {one('select count(*) from material'):,}종 ·
                    출처 {one('select count(*) from source'):,}건 ·
                    물성 정의 {one('select count(*) from property_definition'):,}종
  source.tar.gz     git 추적 소스(backend/frontend/docs). .git·node_modules·scratchpad 제외

여는 법
  tar xzf source.tar.gz
  sqlite3 materialtwin.db "select property_key, value_num, unit from property_value limit 5"

값을 읽을 때
  등급(quality_tier) 1=그 제품에 인쇄된 값 · 2=핸드북/규격 · 3=2차인용·클래스대표 ·
  4=계산·추정·가정. **4는 근거가 없는 값이므로 그대로 해석에 넣지 말 것.**
  조건(conditions)이 없는 값은 온도·방향·율속이 원문에 없다는 뜻이다.
  빈칸에는 사유가 붙어 있다(material.attributes 의 core_* 키).

이 묶음에 다른 앱의 데이터는 들어 있지 않다.""")
PY

tar -czf "$STAGE/materialtwin-$TS.tar.gz" -C "$PKG" .
SZ="$(du -h "$STAGE/materialtwin-$TS.tar.gz" | cut -f1)"

echo "→ 업로드 $DEST/  [$SZ]"
"$RCLONE" copy "$STAGE/materialtwin-$TS.tar.gz" "$DEST/"
"$RCLONE" copyto "$STAGE/materialtwin-$TS.tar.gz" "$DEST/materialtwin-latest.tar.gz"

# 보존정책 — 최신 RETAIN 개만 유지(latest 는 항상 남긴다)
mapfile -t OLD < <("$RCLONE" lsf --files-only "$DEST/" 2>/dev/null \
                   | grep '^materialtwin-2' | sort | head -n "-${RETAIN}") || true
for f in "${OLD[@]:-}"; do [ -n "$f" ] && "$RCLONE" deletefile "$DEST/$f" 2>/dev/null || true; done

echo "✓ materialtwin → Drive ($TS)"
echo
echo "  받는 경로   $DEST/materialtwin-latest.tar.gz"
echo "  공개 전환   Drive 에서 폴더 'materialtwin' 우클릭 → 공유 →"
echo "              일반 액세스 → 「링크가 있는 모든 사용자」 → 뷰어"
echo "              (rclone 은 파일만 올린다 — 공유 설정은 안 바꾼다)"
