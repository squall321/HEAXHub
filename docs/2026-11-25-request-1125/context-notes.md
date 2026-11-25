<!-- request-1125 HEAXHub 몫 작업 중 내린 결정과 까닭 -->
# request-1125 — 결정 노트

## 2026-11-25 착수

- **범위(사용자 결정)** — HH-18 → HP-71 → SE-8 차례로 한다. IR-24 는 등록 사본을 DocForge 정본 릴리스에 맞추는 일이라
  그 릴리스(IR-23 뒤)가 오면 doc_forge SIF 다시 짓기와 함께 한다.
- **5차 동결 중** — 모두 로컬 커밋까지만. push · Drive 는 hwaxportal-87 해제 알림 뒤 이 창의 사용자 확인을 받아 한다.

### HH-18
- **.env 에서 지운 키(사용자 결정)** — `APPTAINERENV_*` 만 뺀다. 그 키들은 .env 에서만 온다. 프록시 · LLM · 검색 키는
  셸 프로필에서 export 한 박스도 있을 수 있어, .env 에 없으면 프로세스 값을 그대로 쓴다.
- **따옴표 해석은 python-dotenv** — 백엔드 설정(pydantic-settings)이 .env 를 읽는 것과 같은 파서다. start.sh 는
  bash `source` 로 읽으니 아주 특이한 줄(명령 치환 등)은 둘이 갈릴 수 있지만, KEY=VAL · 따옴표 · export · 주석은 같다.
- **.env 가 없으면 프로세스 env 를 그대로 쓴다.** .env 없이 도는 박스에서 APPTAINERENV_* 를 모두 지우면 오히려 놀란다.
- **도우미 자리는 새 모듈 `app/services/host_env.py`.** 런처와 apt_runner 둘 다 쓴다. apt_runner 는 apptainer CLI 의
  하위 프로세스 env 를 만들 때 `APPTAINERENV_*` 를 호스트 env 에서 그대로 물려준다(--cleanenv 여도 이 접두어는 들어간다).
  그래서 런처만 고치면 옛 APPTAINERENV_* 가 그대로 남는다.
- **`backend/app/runners/apptainer_runner.py`(제출물 잡 실행)는 범위 밖이다.** 앱 기동 길이 아니다.
- **CA 번들 경로(HEAX_APP_CA_BUNDLE)도 같은 env 에서 읽는다.** 요청 문구는 APPTAINERENV_* · 프록시 · LLM · 검색이지만,
  CA 번들도 .env 로 바꾸는 호스트 → 컨테이너 값이라 같은 틈이 난다. 런처 안에서 `os.environ` 을 읽는 자리는 이제 없다.
- 시험 — 도우미 5개(따옴표 · export · 빈 값 · 지운 키 · .env 없음 · apt start/exec), 런처 SIF 경로 1개. 백엔드 전체는
  실패 14개가 변경 전 묶음과 같다. 호스트 모드 기동은 같은 도우미를 쓰므로 따로 시험하지 않았다.

### IR-24 담당 바뀜(11-25)
- 포털이 IR-24 를 HEAXHub 몫에서 뺐다. 등록 사본은 DocForge 배치가 release 를 옮긴 바로 뒤 그 파일 하나만 커밋한다.
  두 세션이 같은 줄을 고치면 커밋이 겹치고, 차례가 어긋나면 스캐너가 옛 판 숫자로 SIF 를 한 번 더 짓는다.
- **빌드 틀(.def)은 건드리지 않는다.** 틀은 모든 허브 앱의 SIF 캐시 열쇠에 들어가서, 바뀌면 cae00 에서 앱 전부가 열쇠
  어긋남으로 뜨지 않는다. HH-18 은 런처 · apt_runner 만, SE-8 은 받기 스크립트만 바꾼다.

### SE-8(11-25)
- **앱 데이터 tar 는 영구 캐시(`deploy/apptainer/cache/.drive-appdata`)에 받는다.** rclone 이 안 바뀐 tar 를 건너뛴다. 풀기는 매번
  임시 디렉터리에 한다(로컬이라 싸다). 그래서 **DB 밖 심기는 tar 가 같아도 지금처럼 계속 돈다** — 요청의 '먼저' 결정(건너뛸 때 심기도
  멈출지)이 필요 없다. 건너뛰는 것은 안전백업 · DB 병합뿐이다(병합은 멱등이라 다시 돌아도 0건, 사본만 쌓였다).
- **건너뛰는 기준은 '지난번에 병합까지 끝낸 tar 의 sha256'** 이다(`cache/.drive-appdata/merged.sha256`). 병합이 하나라도 실패하면
  적지 않아 다음 실행이 다시 병합한다.
- **안전백업은 DB 마다 최근 3개(HEAX_APPDATA_PREMERGE_KEEP)** — 0 · 숫자 아님은 3 으로 둔다(하나는 늘 남는다). 건너뛰는 실행에서도
  정리는 돈다 — cae00 에 쌓인 348개는 이 판을 받은 첫 실행에서 줄어든다.
- **models MANIFEST** — 올리는 쪽에는 그런 파일이 없었다. models-to-drive 가 올린 뒤 Drive 목록(경로;크기;수정시각)을 `MANIFEST` 로
  올리고, 받는 쪽은 그 파일 하나만 받아 지난번 값과 같고 모델 디렉터리가 비어 있지 않으면 건너뛴다. 지난번 값은 앱 모델 디렉터리가
  아니라 `cache/.drive-models/<app>.MANIFEST` 에 둔다(앱이 /data/models 를 훑을 때 모르는 파일을 보지 않게). MANIFEST 가 없는 옛
  업로드는 전체 비교(--fast-list)로 돈다. 그래서 효과는 dev 에서 models-to-drive 를 한 번 돌린 뒤부터다.
- **dist 무결성** — sync 앞뒤로 캐시의 (이름 · 크기 · 수정시각) 을 찍어 옮긴 파일만 sha256sum -c 한다. 전체 검사는 표시 파일이 없거나
  7일 넘으면 한다. 표시 · 대기 목록은 캐시 밖 형제(`.drive-dist.full-check` · `.drive-dist.pending`)에 둔다 — 캐시 안에 두면 다음
  sync 가 지운다. **대기 목록**은 검사가 실패하거나 끊겼을 때 다음 실행이 그 파일을 '안 바뀜'으로 보고 넘어가는 틈을 막는다. 검사에
  실패하면 표시 파일도 지워 다음 실행이 전체 검사를 한다(가짜 rclone 실측 — 손상이 다음 실행에서도 잡혔다).
- frontend-dist 는 옮긴 목록에 있거나 `frontend/dist/index.html` 이 없을 때만 푼다. 판정에 `printf | grep -q` 를 쓰지 않는다 —
  pipefail 아래 SIGPIPE 오판(같은 파일 24줄의 경고).
- 시험 하나가 전체 묶음에서만 실패했다 — 시험의 tar 를 실행마다 새로 만들어 gzip 머리 시각이 초 경계를 넘으면 해시가 달라졌다.
  두 번째 실행이 첫 바이트를 그대로 쓰게 고쳤다(운영은 Drive 의 같은 파일을 받으니 해당 없음).
