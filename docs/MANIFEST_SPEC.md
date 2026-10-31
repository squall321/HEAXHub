# Manifest Spec

각 앱은 워크스페이스 안에 `overlay/.portal/manifest.yaml`를 둔다. JSON Schema 원본은 [`schemas/manifest.schema.json`](../schemas/manifest.schema.json), 실제 예시는 `templates/*/.portal/manifest.yaml`을 참고한다.

## 1. 필수 필드

| 필드 | 타입 | 비고 |
|---|---|---|
| `schema_version` | `int` (현재 `1`) | 양식이 바뀌면 증가 |
| `id` | `string` | `^[a-z][a-z0-9_]{2,63}$` (소문자 snake_case) |
| `name` | `string` | 사람이 읽는 이름 |
| `version` | `string` | SemVer (예: `1.2.0`) |
| `owner` | `string` | 조직 또는 사용자 식별자 |
| `status` | enum | `draft / beta / stable / deprecated / archived` |
| `app_type` | enum | 7종 — 아래 §2 |
| `execution_target` | enum | 6종 — 아래 §3 |
| `launch` | object | 실행 모드 (§4) |

## 2. app_type 7종

| 값 | 의미 |
|---|---|
| `cli_tool` | 명령어형 도구 (Python/C++/Shell) |
| `web_app` | 브라우저로 여는 사내 페이지 |
| `windows_gui` | 창이 뜨는 윈도우 EXE |
| `remote_app` | 원격 접속해 쓰는 도구 |
| `external_link` | 이미 운영 중인 외부 URL |
| `slurm_job` | HPC 클러스터 작업 |
| `container_app` | Apptainer/Docker 컨테이너 앱 |

## 3. execution_target 6종

| 값 | 실행 위치 |
|---|---|
| `linux_runner` | 포탈이 직접 호스트에서 실행 |
| `slurm` | 사내 Slurm 클러스터 |
| `apptainer` | SIF 컨테이너 안 |
| `windows_worker` | Windows Agent가 대리 실행 |
| `external_url` | 외부 서비스 (포탈은 클릭 추적만) |
| `local_pc` | 사용자 PC에 설치된 앱 |

## 4. launch.mode

| 값 | 함께 둘 필드 |
|---|---|
| `job_runner` | `command` (필수). 표준 진입점 `./run.sh input output params.json` |
| `url` | `url`, `open_in` (`new_tab`/`iframe`), `auth_mode` (`none`/`sso`/`token`) |
| `remote_agent` | `agent_pool`, `command` |
| `local_protocol` | `protocol`, `installer_url` |

## 5. inputs / outputs

`inputs[]`는 사용자 실행 폼을 자동 생성하기 위한 정의다. 각 항목:

```yaml
inputs:
  - name: k_file        # 폼 필드 이름
    type: file           # file / folder / string / number / integer / boolean / enum
    required: true
    extensions: [".k", ".key"]
    label: "K 파일"
    description: "LS-DYNA 입력 파일"
```

`outputs[]`는 결과 다운로드 메뉴 자동 생성용:

```yaml
outputs:
  - name: report
    type: file
    path: output/report.html
```

## 6. permissions

```yaml
permissions:
  visibility: team           # private | team | department | company
  executable_by: ["cae", "admin"]
```

## 7. resources

```yaml
resources:
  cpu: 4
  memory_gb: 8
  gpu: false
  timeout_seconds: 1800
```

## 8. build

| build.type | 효과 |
|---|---|
| `python_venv` | `app_workspaces/{id}/venv/` 생성, requirements.txt/pyproject 설치 |
| `apptainer` | `app_workspaces/{id}/sif/app.sif` 생성. `apptainer_def` 또는 upstream의 `.portal/Apptainer.def` 사용 |
| `none` | 빌드 단계 생략 (external_link 등) |
| `external` | 빌드는 외부 (Windows Agent에서) 처리 — 메타데이터만 |

## 9. 검증

신청 또는 업데이트가 들어오면 `services.manifest_validator.validate(data)`가 호출돼 schema 위반을 사전에 거부한다. CLI에서 직접 검증:

```bash
python -m json.tool schemas/manifest.schema.json > /dev/null  # schema syntax check
python - <<'PY'
import json, yaml, jsonschema, pathlib
schema = json.loads(pathlib.Path("schemas/manifest.schema.json").read_text())
data = yaml.safe_load(pathlib.Path("templates/python-cli/.portal/manifest.yaml").read_text())
jsonschema.Draft7Validator(schema).validate(data)
print("OK")
PY
```

## 10. 박스 전용 덮어쓰기 — `manifest.local.yaml` (2026-10-31, HH-15)

통합 앱(`integrations/<폴더>/`)의 추적 매니페스트는 git 이 정본이라 박스에서 고치면 다음 당기기(update-all · deploy-all 의 `git reset --hard`)가 되돌린다. 같은 앱을 박스마다 다르게 돌려야 하면(운영 박스는 운영 노드로 넘기고 dev 는 여기서 서비스로 띄우는 것 같은) 그 차이만 박스 전용 파일에 적는다.

| 항목 | 내용 |
|---|---|
| 파일 | `integrations/<폴더>/.portal/manifest.local.yaml` — 추적 `manifest.yaml` 바로 옆. gitignore 라 커밋되지 않고 deploy-all 의 `git stash -u` 도 가져가지 않는다. `bundle.sh` 도 싣지 않는다. |
| 병합 | 추적 `manifest.yaml` 을 읽은 뒤 RFC 7386(JSON Merge Patch)으로 얹는다. 매핑은 키마다 재귀로 합치고, `null` 은 그 키를 지우고, 목록 · 글자 · 숫자는 통째로 바꾼다(목록끼리 이어 붙이지 않는다). |
| 못 바꾸는 것 | `id` — `/apps/<id>` · 상태 파일 · 인스턴스 이름(`heax_app_<id>`)의 원천이다. 로컬 파일의 `id` 는 버리고 WARNING 을 남긴 뒤 나머지만 얹는다. 추적 `manifest.yaml` 이 없는 폴더는 로컬 파일만으로 앱이 생기지 않는다. |
| 깨졌을 때 — 보류 | 로컬 파일이 **있는데** 얹지 못하면 그 앱을 보류한다 — 스캐너 · reconcile 이 그 앱을 건너뛰어 라우트 · 인스턴스 · 스냅숏 · 빌드를 마지막 상태 그대로 두고, `redeploy-app.sh` 는 멈추기 전에 rc 2 로 끝난다. **추적 값으로 돌지 않는다** — 덮어쓰기는 추적 값이 이 박스에 맞지 않아서 둔 것이라, 오타 하나로 추적 값을 따르면 그 앱이 끊기고(dev 의 step_forge → 없는 16110) 모드가 바뀐 앱의 서비스가 내려간다. 얹지 못하는 경우는 YAML 오류 · 최상위가 매핑이 아님(빈 파일 포함) · 읽히지 않음 · 대상이 없는 심링크, 그리고 얹은 결과의 모양이 틀린 것이다(아래). 로그에 WARNING 한 줄(같은 내용이면 한 시간에 한 번만 다시)과 상태 화면의 `held` · `error` 가 남는다. 고치면 다음 패스에 따른다. 보류 중에는 그 앱의 라우트 재등록 · 죽은 인스턴스 재기동도 하지 않는다 — 그 사이 Caddy 가 다시 뜨면 고칠 때까지 그 앱이 끊기므로 경고를 보면 바로 고친다. |
| 모양 규칙 | YAML 로는 멀쩡한 실수도 보류한다. ① `launch` · `build` · `permissions` · `resources` · `source` · `mcp` 를 적으면 매핑이어야 한다(`launch: http://…` 처럼 블록을 글자로 적지 않는다). ② 추적 매니페스트에 있는 `launch` · `build` 블록을 지우거나(`null` — 짧은 덮어쓰기에서 들여쓰기를 하나 빠뜨리면 `launch:` 가 null 로 읽힌다) `launch.mode` · `build.stack` 을 지우지 못한다. ③ `launch` 나 `build` 를 바꾸면 효과 매니페스트의 `build.stack` 과 `launch.mode` 가 `config/stacks.yaml` 의 `launch_mode` 로 맞아야 한다 — 모드를 바꾸면 스택도 함께 바꾼다(서비스 앱을 이 박스에서만 넘기려면 `build: { stack: external_proxy }` 도 적는다). 쓰는 도중에 읽혀 build 만 바뀐 파일이 이것으로 걸린다. |
| 고칠 때 | 같은 폴더에 임시 이름으로 다 쓴 뒤 `mv` 로 바꾼다(`cp` · 편집기의 제자리 저장은 읽는 쪽이 빈 파일이나 덜 쓰인 파일을 볼 수 있다). 파일이 **없어지면**(지움 · `git clean -x` · 새 clone) 덮어쓰기를 걷은 것으로 보고 추적 값으로 돈다 — 지운 것과 사라진 것은 구분할 수 없다. |
| SIF 를 보내는 박스 | SIF 캐시 열쇠(`var/sifs/<slug>.sif.hash`)는 효과 매니페스트 전체를 해시한다 — 덮어쓰기에 `launch.env` 하나만 있어도 SIF 내용은 같은데 열쇠가 바뀐다. 그래서 SIF 를 지어 다른 박스로 보내는 박스(dev)가 덮어쓴 앱의 열쇠는 받는 박스(추적 매니페스트로 셈한다)와 맞지 않는다 — 받는 박스는 캐시 미스로 보고 다시 지으려 하고(cae00 은 짓지 못해 빌드 실패로 그 회차 기동을 건너뛴다), `drive-drift.sh` 는 '열쇠가 맞지 않는다' 를 내는데 dev 에서 다시 지어도 같은 열쇠가 나온다. **다른 박스로 보내는 앱은 빌드 박스에서 덮어쓰지 않는다.** `dist-to-drive.sh` 는 덮어쓴 앱의 SIF 를 기본으로 싣지 않고 까닭을 찍는다(`HEAX_DRIVE_APP_SIFS` 로 이름을 대면 경고와 함께 싣는다) — 운영 전환 뒤 dev 가 계속 짓는 `step_forge.sif` 가 cae00 에 깔려 허브를 다시 띄우지 않게 하는 것도 이것이다. |
| 언제 반영되나 | 스캐너(카탈로그 · 스냅숏 · MCP 레지스트리 · SIF 빌드 해시) · reconcile(라우트 · 기동) · 런처 · `redeploy-app.sh` · 상태 화면 · `scripts/diagnose-app.sh` 가 모두 같은 로더(`backend/app/services/integration_manifest.py`)로 읽는다. 더하거나 고치거나 지우면 다음 reconcile(45초 안)에 라우트 · 기동이, 다음 스캔(5분 안)에 카탈로그 · MCP 레지스트리가 따른다. 허브 재기동은 필요 없다. 도는 서비스의 env 같은 값은 추적 파일을 고칠 때와 같이 `redeploy-app.sh` 로 다시 띄워야 들어간다. |
| 보는 법 | `GET /api/v1/admin/integrations/builtin` 의 행에 `local_override`(`file` · `applied` · `changed_keys` · `rejected_keys` · `error` · `held`)가 붙는다 — 로컬 파일이 있는 앱에만. 보류 중인 행의 표 값은 추적 파일 것이다(허브는 마지막으로 얹은 값으로 돈다). `scripts/diagnose-app.sh <앱 id>` 도 효과 값을 보여 준다. 로그는 `var/logs/worker.log` · `backend.log` 의 `manifest.local.yaml` 줄이다. |
| 모드가 바뀌면 | 효과 매니페스트의 `launch.mode` 가 service 에서 다른 모드(proxy · static · url · iframe · job_runner · installer)로 바뀌면(로컬 파일로든 추적 파일로든) 허브가 그 앱 id 로 띄워 둔 `heax_app_<id>` 를 다음 reconcile 이 새 라우트를 건 뒤 내린다(HH-16). 모드가 없거나 모르는 값(오타 · launch 블록 삭제)이면 내리지 않고 WARNING 만 남긴다. 상태 파일의 이름 · 명령줄이 그 앱의 것으로 확인되지 않으면 건드리지 않고 기록 · 포트도 그대로 둔다. |
| 앱을 지울 때 | 로컬 파일도 같이 지운다 — 무시되는 파일이라 `git rm` 이 남긴다. |

예 — 운영 전환(HH-14)으로 추적 매니페스트의 `step_forge` 가 운영 노드 proxy(`127.0.0.1:16110`)가 된 뒤에도 dev 는 StepForge 를 이 허브가 빌드해 서비스로 띄운다. 아래를 얹은 효과 매니페스트는 전환 전 추적 매니페스트와 같다(스냅숏 · 빌드 해시가 그대로라 다시 빌드 · 기동하지 않는다).

```yaml
# integrations/step_forge/.portal/manifest.local.yaml — dev 박스 전용
execution_target: linux_runner
build:
  stack: fastapi
  type: python_venv
  python_version: "3.12"
launch:
  mode: service
  upstream: null          # proxy 갈래의 키는 지운다
  strip_prefix: null
  health_check: { type: http, path: /health }
  restart_policy: { policy: on_failure, max_attempts: 3 }
  env: { STEPFORGE_DATA_DIR: /data }
resources:
  cpu: 2
  memory_gb: 16
  gpu: false
source:
  type: git
  url: https://github.com/squall321/StepForge.git
  ref: main
```

proxy 앱의 업스트림만 바꿀 때는 그 키 하나면 된다(예 — dev 의 `kooremapper` · `kooremapper-api` 는 `launch: { upstream: http://127.0.0.1:8700 }`, `kooremapper-mcp` 는 `:8701`).

⚠ 순서 — 무시 목록은 git 이 당기기 전에 읽는다. 이 기능이 들어간 판을 먼저 받고(무시 목록 · 로더) 로컬 파일을 둔 뒤에, 그 앱의 추적 매니페스트를 바꾸는 판을 받는다. 판을 받기 전에 로컬 파일부터 두어야 하면 `.git/info/exclude` 에 같은 줄을 먼저 넣는다 — 안 그러면 deploy-all 의 `git stash -u` 가 그 파일을 가져간다.
