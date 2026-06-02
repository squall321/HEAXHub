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
