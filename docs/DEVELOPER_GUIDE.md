# HEAXHub 개발자 가이드 (사내 개발자용)

내 프로그램을 HEAXHub 카탈로그에 올려 사내에 서비스하는 방법.

---

## ■ 개요

HEAXHub는 사내 자동화 도구·웹앱·외부 사이트를 **한 포탈에서 검색·실행·접속**하게 묶는 카탈로그다.
GitHub(또는 사내 git) 저장소를 등록하면 시스템이 자동으로 **clone → SIF 빌드 → `/apps/<slug>/` 서브경로 서빙**까지 처리한다.
이미 돌아가는 사이트라면 빌드 없이 **URL/IP만 등록**해도 된다.

등록 단위는 디렉터리 하나다:

```
integrations/<slug>/.portal/manifest.yaml
```

이 파일만 있으면 5분 주기 스캐너가 자동으로 카탈로그에 올린다. 아래 등록 스크립트는 이 파일을 대신 만들어 준다.

---

## ▶ 5분 빠른 시작

세 가지 등록 경로가 있다. 상황에 맞는 걸 고른다.

### (a) 이미 돌아가는 서버/사이트를 URL·IP로 등록 — 빌드 없음

```bash
scripts/register-url.sh <slug> <주소> [url|proxy|iframe]

# 예
scripts/register-url.sh wiki 10.0.0.5:8080            # 새 탭 바로가기 (기본 url)
scripts/register-url.sh grafana 10.0.0.5:3000 proxy   # 포탈 하위경로로 프록시
scripts/register-url.sh report https://report.intra iframe   # 포탈 안에 임베드
```

| 방식 | 동작 | 언제 |
|---|---|---|
| `url` | 클릭하면 **그 주소를 새 탭**으로 연다 | 가장 단순한 바로가기 |
| `proxy` | `/apps/<slug>/`로 **리버스 프록시** (포탈 도메인 안으로 흡수) | SSO·동일 도메인이 필요할 때 |
| `iframe` | 포탈 페이지 **안에 임베드** | 포탈 안에서 바로 보고 싶을 때 (대상이 `X-Frame-Options`로 막으면 안 뜸) |

### (b) GitHub 저장소 + 스택(포맷)으로 등록 — 자동 빌드·서빙

```bash
scripts/register-repo.sh <slug> <git-url> <stack> [ref]

# 예
scripts/register-repo.sh mytool https://github.com/org/mytool fastapi
scripts/register-repo.sh dash1  https://github.com/org/dash   dash_plotly main
```

스캐너가 clone → SIF 빌드(첫 빌드는 수 분) → `/apps/<slug>/` 서빙까지 한다.
`stack` 값은 아래 §지원 스택 표에서 고른다.

> 사설(private) 저장소는 현재 토큰 인증 미지원 — 공개 repo 또는 사내 미러를 사용한다.

### (c) CSV 일괄 등록 — 여러 개를 한 번에

```bash
scripts/register-from-csv.sh <csv파일> --scan
```

CSV 포맷 (첫 줄은 헤더이므로 무시, 둘째 줄부터):

```
그룹,파트명,Agent이름,프로그램이름,URL,설명
디지털트윈AI,전처리파트,MeshAgent,,http://10.0.0.5:8080,메시 자동 생성
디지털트윈AI,후처리파트,,KooPreprocessor,http://10.0.0.6:9090
```

- `Agent이름`이 채워지면 → `tags:[agent]` 분류, `프로그램이름`이 채워지면 → `tags:[program]` 분류 (둘 중 하나만).
- 마지막 URL → `mode: url` 링크. 6번째 열(설명)은 선택 — 적으면 `소속: 그룹 / 파트 · 설명`으로 들어간다.
- `--scan` 생략 시 5분 주기 스캔을 기다린다. `--dry-run`으로 미리보기 가능.

등록 후 이름/설명/담당자는 `integrations/<slug>/.portal/manifest.yaml`을 열어 언제든 수정한다.

---

## ◇ 지원 스택 전체 (22종)

`config/stacks.yaml`에 정의된 값. manifest의 `build.stack`에 그대로 쓴다.

### 서비스형 (web_app · 상시 구동, `/apps/<slug>/` 서빙)

| stack | 용도 | 엔트리포인트(기본) | 헬스경로 |
|---|---|---|---|
| `fastapi` | FastAPI/uvicorn API·웹 | `uvicorn app.main:app --port $PORT --root-path $ROOT_PATH` | `/health` |
| `fastapi_react` | FastAPI + React(Vite) 풀스택 | `uvicorn app.main:app ...` | `/api/health` |
| `flask` | Flask/gunicorn | `gunicorn --bind 0.0.0.0:$PORT ...` | `/health` |
| `streamlit` | Streamlit 대시보드 | `streamlit run app.py --server.port $PORT ...` | `/_stcore/health` |
| `dash_plotly` | Plotly Dash | `gunicorn ... app:server` | `/` |
| `shiny_for_python` | Shiny for Python | `shiny run --port $PORT --root-path ...` | `/` |
| `nextjs` | Next.js (React SSR) | `pnpm start -- --port $PORT` | `/` |
| `nodejs_express` | Node/Express | `node dist/server.js` | `/health` |
| `go_service` | Go HTTP 서버 | `./bin/server` | `/healthz` |
| `rust_actix` | Rust Actix-web | `./app/server` | `/health` |
| `dotnet_aspnet` | ASP.NET Core | `dotnet publish/app.dll --urls ...` | `/health` |
| `java_springboot` | Spring Boot | `java -jar target/app.jar ...` | `/actuator/health` |

> **바인드는 loopback(127.0.0.1) 전용 — 포트를 외부에 노출하지 말 것.**
> 앱은 `0.0.0.0`이 아니라 `127.0.0.1`(또는 주입되는 `$HOST`)에만 들어야 한다. 그래야
> Caddy 리버스 프록시(`/apps/<slug>/`)가 유일한 진입점이 되고, `<host>:<port>` 직타로
> **인증 게이트(SEC-03)를 우회**하는 일이 막힌다. 내장 스택(fastapi/streamlit/flask/
> nextjs/shiny/dotnet 등)은 런처가 자동으로 `127.0.0.1`에 바인드한다. **앱 코드가 직접
> 바인드하는 경우(dash `app.run`, Go `ListenAndServe` 등)는 반드시 `$HOST`를 읽어
> `127.0.0.1`에 바인드**하라. (모든 스택을 앱 협조 없이 강제 차단하려면 네트워크
> 네임스페이스 격리가 필요 — 후속 과제.)

### 정적형 (static · 빌드 산출물을 파일서버로)

| stack | 용도 |
|---|---|
| `static_html` | 정적 HTML 디렉터리 |
| `mkdocs_static` | MkDocs 빌드 산출물 |

### 잡 실행형 (cli_tool · 입력받아 1회 실행)

| stack | 용도 |
|---|---|
| `python_cli` | Python CLI (입력→실행→출력) |
| `cpp_executable` | C++ 빌드 바이너리 |
| `r_script` | R 스크립트 |
| `apptainer_sif` | 미리 빌드된 SIF 직접 실행 |

### 외부형 (external_link · 빌드 없음)

| stack | 모드 |
|---|---|
| `external_link` | `url` (새 탭) |
| `external_iframe` | `iframe` (임베드) |
| `external_proxy` | `proxy` (리버스 프록시) |

### 기타

| stack | 용도 |
|---|---|
| `windows_local` | Windows GUI (사용자 PC 설치형) |

---

## ◇ manifest.yaml 작성법

### 필수 필드

| 필드 | 설명 |
|---|---|
| `schema_version` | 현재 `2` |
| `id` | 소문자 snake_case, 카탈로그 고유 ID (`^[a-z][a-z0-9_]{2,63}$`) |
| `name` | 카탈로그에 보일 이름 |
| `app_type` | `web_app` / `cli_tool` / `external_link` / `windows_gui` 등 (스택이 결정) |
| `execution_target` | `linux_runner` / `external_url` / `apptainer` 등 |
| `build.stack` | 위 표의 스택 값 |
| `launch.mode` | `service` / `static` / `job_runner` / `url` / `iframe` / `proxy` |
| `permissions.visibility` | `company` / `department` / `team` / `private` |

### 스택 유형별 핵심 차이

- **service** (fastapi/flask/streamlit/…): `health_check.path` 를 정확히 지정해야 헬스 통과 → 서빙된다. 앱은 `$PORT`로 listen, `$ROOT_PATH`(=`/apps/<slug>`)를 root-path로 받아야 자산·링크가 안 깨진다.
- **static** (static_html/mkdocs): `build.root`에 정적 산출물 디렉터리 지정(기본 `.`).
- **job_runner** (cli/cpp/r): `inputs[]`에 입력 폼 정의(파일/숫자/불리언). 실행 시 폼이 자동 생성된다.

### 실제 예시 — service (FastAPI)

```yaml
schema_version: 2
id: heax_demo_fastapi
name: "Demo · Memo CRUD (FastAPI)"
owner: heaxhub-demo
status: stable
app_type: web_app
execution_target: linux_runner
description: "간단 메모 CRUD API + Swagger UI."
build:
  stack: fastapi
launch:
  mode: service
  command: uvicorn app.main:app --host 0.0.0.0 --port $PORT --root-path $ROOT_PATH
health_check:
  path: /health
restart_policy:
  policy: on_failure
  max_retries: 3
source:
  type: git
  url: https://github.com/org/mytool       # 또는 file:///.../repo.git
  ref: main
permissions:
  visibility: company
```

### 실제 예시 — job_runner (Python CLI)

```yaml
schema_version: 2
id: heax_demo_cli
name: "Demo · CSV Row Counter"
app_type: cli_tool
execution_target: linux_runner
build:
  stack: python_cli
launch:
  mode: job_runner
  command: ./.portal/run.sh
  runtime: python_venv
inputs:
  - name: csv_file
    type: file
    label: "입력 CSV 파일"
    required: true
    accept: [".csv", ".txt"]
  - name: skip_header
    type: boolean
    default: true
source:
  type: git
  url: https://github.com/org/mytool
  ref: main
permissions:
  visibility: company
```

### 실제 예시 — url (외부 링크)

```yaml
schema_version: 2
id: my_wiki
name: "사내 위키"
app_type: external_link
execution_target: external_url
build:
  stack: external_link
launch:
  mode: url
  url: http://10.0.0.5:8080
  open_in: new_tab
permissions:
  visibility: company
```

---

## ◇ 내 프로그램을 스택에 맞추기 (서비스형)

상시 구동 웹앱은 두 가지 규약만 지키면 된다.

1. **`$PORT` 환경변수로 listen** — 고정 포트 쓰지 말 것. 포탈이 포트를 할당해 `$PORT`로 넘긴다.
2. **`$ROOT_PATH`(=`/apps/<slug>`) 를 base/root-path로 받기** — 안 그러면 서브경로에서 정적 자산·링크가 깨진다.
   - FastAPI: `uvicorn --root-path $ROOT_PATH`
   - Flask: `SCRIPT_NAME` 또는 gunicorn 설정
   - Next.js/Vite: `basePath` / `base` 를 `$ROOT_PATH` 기준으로
   - Streamlit/Dash/Shiny: 각 `--server.baseUrlPath` / `requests_pathname_prefix` / `--root-path`

시작 템플릿이 `templates/`에 있다 — `python-webapp`(FastAPI service), `fastapi-react`(Vite+React+FastAPI 풀스택), `streamlit-hello`, `python-cli`, `cpp-cli`, `windows-gui`. 복사해서 시작하면 위 규약이 이미 반영돼 있다.

> **서브경로에서 가장 안 깨지는 형태**가 필요하면 `templates/fastapi-react`를 쓴다. Vite `base: "./"`(자산 상대경로) + `fetch("api/...")`(API 상대경로) + FastAPI StaticFiles 마운트로, `/apps/<slug>/` prefix가 무엇이든 자산·API가 그대로 동작한다(빌드 검증 완료).

---

## ◇ 등록 후 확인

```bash
# 즉시 카탈로그 반영(스캔 1회 트리거) — 5분 기다리기 싫을 때
cd backend && .venv/bin/python -c \
  'from app.workers.integration_tasks import scan_integrations_periodic as s; print(s()["by_action"])'

# 서비스 접속
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:4180/apps/<slug>/
```

- 빌드 진행/실패는 카탈로그의 앱 상세에서 상태로 보이며, 실패 시 운영자에게 메일이 간다.
- 빌드 로그: `var/logs/sif_build_<slug>.log`, 서비스 로그: `var/logs/integration_<slug>.log`.

---

## ◇ 자주 막히는 것

| 증상 | 원인 / 해결 |
|---|---|
| 빌드 실패 (Node) | `pnpm-lock.yaml` 없거나 버전 불일치. lockfile을 repo에 커밋. |
| 헬스 404로 서빙 안 됨 | `health_check.path`가 실제 앱 헬스 경로와 다름. 앱이 그 경로에 200을 줘야 한다. |
| 서브경로에서 CSS/JS 깨짐 | `$ROOT_PATH`(base path) 미적용. 위 §스택 맞추기 참고. |
| `/apps/<slug>/`가 포탈 홈으로 흡수 | 과거 Caddy 라우트 휘발 이슈 — 현재 reconcile 루프가 자동 복구한다(30초 주기). 수동: `POST /api/v1/admin/integrations/proxy-sync`. |
| job 실행이 "No file found" | job_runner 입력 파일을 안 넘김. `inputs[]`의 `file` 타입은 실제 파일 업로드가 필요. |

---

## ◇ 공개 범위 (`permissions.visibility`)

| 값 | 노출 |
|---|---|
| `company` | 전사 (로그인한 모든 사용자) |
| `department` | 같은 부서 |
| `team` | 같은 팀 |
| `private` | 소유자만 |

---

관련 문서: [MANIFEST_SPEC.md](MANIFEST_SPEC.md) (필드 전체 스펙), [ARCHITECTURE.md](ARCHITECTURE.md) (시스템 구조), 코드베이스 기여는 루트 [CONTRIBUTING.md](../CONTRIBUTING.md).
