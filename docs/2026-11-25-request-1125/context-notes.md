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
