# HWAXAgent E2E 예제 — Koo Preprocessor 1.2.0 시나리오

> HWAXAgent(Tauri 2 트레이 상주 에이전트) 개발자가 HEAXHub와 통합할 때 한 번 읽으면 끝나도록 만든 단일 진입점 예제. manifest 등록 → DB 행 변환 → 에이전트 manifest fetch → 12단계 설치 → 4가지 실패 → 진짜 롤백 → 5종 audit. 모든 JSON 페이로드는 `contracts/hwax-agent/*.schema.json` 으로 자동 검증된다.

| 항목 | 내용 |
|---|---|
| 문서 종류 | 개발자 온보딩 E2E 예제 |
| 대상 독자 | HWAXAgent 통합 개발자, HEAXHub 백엔드 |
| 대상 OS | Windows 10 21H2+ / 11 22H2+ (x64) |
| 에이전트 스택 | Tauri 2 (Rust) + React 18 |
| 스키마 출처 | `contracts/hwax-agent/{manifest,install-report,audit-event}.schema.json` |
| 검증 스크립트 | `scripts/validate-e2e-examples.py` |
| 작성일 | 2026-06-05 |

---

## §1. 들어가며

이 문서는 HWAXAgent 통합 개발자를 위한 **단일 E2E 예제**다. "Koo Preprocessor 1.2.0" 이라는 가상 모듈 하나를 manifest 등록부터 실제 설치/실패/롤백/audit 전송까지 끝까지 따라간다.

**중요 규칙 — 스키마가 진실이다.**

- 본 문서의 모든 JSON 코드 블록은 `contracts/hwax-agent/*.schema.json` 에 대해 자동 검증된다.
- 검증 마커: 각 JSON 블록 바로 위에 `<!-- validates: <name>.schema.json -->` 주석이 붙어 있다.
- 검증 명령:
  ```
  cd backend && .venv/bin/python ../scripts/validate-e2e-examples.py
  ```
- **스키마를 수정하면 본 문서의 예제도 함께 수정해야 한다.** 반대 방향(문서를 보고 스키마를 추정)은 금지.

본 문서는 그 외 다음을 전제한다.

- HEAXHub 백엔드는 `main` 브랜치 (FastAPI :4040, Caddy :4180).
- HWAXAgent 는 `hwax-launcher-plan-v2.md` 의 Tauri 2 결정과 §6/§8 State Machine 을 따른다.
- `agent_id` 는 UUID v7 문자열. 본 문서 전체에서 동일한 ID 를 사용:
  `018f5a3b-4c2d-7e1f-9a8b-1234567890ab`

---

## §2. integrations/koo-preprocessor/.portal/manifest.yaml

서버 측 입력 manifest. `backend/app/services/integrations_scanner.py` 가 주기적으로 읽어 `apps` / `app_versions` / `installer_packages` 세 테이블을 갱신한다.

```yaml
schema_version: 3
id: koo_preprocessor
name: Koo Preprocessor
version: 1.2.0
app_type: windows_gui
execution_target: local_pc
description: |
  STEP/IGES 임포트, 메시 품질 평가, LS-DYNA keyword 변환을 지원하는
  사내 CAE 전처리 GUI 도구.
owner:
  team: cae-platform
  contact: cae-platform@heax.example.com
permissions:
  visibility: company
  allowed_groups: [cae_engineers, cae_admins]
source:
  type: zip
  url: file:///srv/heax/var/installer-cache/koo-preprocessor-1.2.0-win-x64.zip
  ref: 1.2.0
windows_install:
  package_type: zip
  entry:
    executable: bin/KooPreprocessor.exe
    args_template: ["--workspace", "{workspace}"]
    working_dir: ""
  requirements:
    requires_admin: false
    min_windows: "10.0.19045"
  lifecycle:
    post_install_check:
      executable: bin/KooPreprocessor.exe
      args: ["--selftest", "--json"]
      expected_stdout_regex: "^Koo Preprocessor 1\\.2\\.0"
    rollback_on_failure: true
  ui:
    color_accent: "#f59e0b"
    show_in_tray: true
  size_bytes: 188743680
  sha256: "9f1a5c1b2c3d4e5f60718293a4b5c6d7e8f9001122334455667788991011aabb"
release_notes_url: https://wiki.heax.example.com/koo-preprocessor/1.2.0
```

### 두 manifest 의 구분 (footnote)

이 yaml 의 `schema_version` 과 §4 에이전트 응답의 `schema_version` 은 **서로 다른 스키마**다. 헷갈리기 쉬우니 명시적으로 분리한다.

| 구분 | `.portal/manifest.yaml` | `GET /api/v1/launcher-agents/manifest` 응답 |
|---|---|---|
| 역할 | 서버 사이드 입력. 설치 가능 앱 1건을 기술 | 에이전트용 출력. 한 에이전트에 보일 모든 앱을 모은 카탈로그 |
| 스키마 | 서버 내부 (현재 v2, 본 예제는 미래 v3 가정) | `contracts/hwax-agent/manifest.schema.json` (`schema_version: 1` 고정) |
| 파일 수 | 앱별 1개 | 응답 1건에 N 개 앱 |
| 검증자 | `integrations_scanner` | `agent_manifest_builder` |

서버는 N 개 `.portal/manifest.yaml` 을 읽어 단일 에이전트 매니페스트로 집계한다. 본 문서의 yaml 은 검증 대상이 아니다(에이전트 측 스키마가 아니라 서버 내부 스키마). 그래서 위에는 `validates:` 마커가 없다.

---

## §3. HEAXHub 측 변환 — DB 3개 행

scanner 가 위 yaml 을 보고 만드는 행. ORM 모델(`App` / `AppVersion` / `InstallerPackage`) 의 실제 컬럼명만 사용한다.

```text
# apps (App.id 는 String PK)
id=koo_preprocessor  name="Koo Preprocessor"  app_type=windows_gui
execution_target=local_pc  status=stable  visibility=company
owner_user_id=<uuid>  upstream_repo_url=git@.../koo-preprocessor.git
tags=["cae","windows","internal"]  workspace_path=/srv/heax/var/workspaces/koo_preprocessor
extra={"windows_install": { ...§2 yaml 의 windows_install 블록 그대로... }}

# app_versions (AppVersion)
id=<uuid v4>  app_id=koo_preprocessor  version=1.2.0
git_commit_hash=6a91c4f  git_tag=v1.2.0
manifest_snapshot={...§2 yaml...}  build_status=success
released_at=2026-06-04T12:00:00Z

# installer_packages (InstallerPackage)
id=7c1f0a30-0e9d-4b8f-9c61-f1c2a0b3d401  app_id=koo_preprocessor  version=1.2.0
os=windows-x64
installer_url=s3://heax-installers/koo-preprocessor/koo-preprocessor-1.2.0-win-x64.zip
sha256=9f1a5c1b2c3d4e5f60718293a4b5c6d7e8f9001122334455667788991011aabb
size_bytes=188743680  signed=true  uploaded_at=2026-06-04T12:00:00Z
```

핵심:

- `App.extra.windows_install` 가 §2 yaml 의 windows_install 블록을 그대로 보관 (v2 plan §7 "DB 신규 컬럼 0 개" 원칙).
- 컬럼명은 `installer_url` 이다(`download_url` 이 아니다).
- 에이전트에 노출될 때는 `/api/v1/installers/{id}/download` 302 리다이렉트를 통과한 presigned URL 이 된다.

---

## §4. 에이전트 manifest fetch

### 4.1 요청

```http
GET /api/v1/launcher-agents/manifest?os=windows-x64 HTTP/1.1
Host: heaxhub.internal
Authorization: Bearer <access_token>
If-None-Match: "W/sha256-prevmanifestetag"
```

- `Authorization` 의 audience claim 은 `hwax-agent`, sub 는 §1 의 agent_id.
- `os` 는 **쿼리 파라미터**다. 응답 body 안에는 `os` 필드가 없다(스키마 상 금지).
- 204 가 아니라 200 + ETag 변경 시 body 갱신. 변화 없으면 304 (본 예제 외).

### 4.2 200 응답 — 검증된 예제

<!-- validates: manifest.schema.json -->
```json
{
  "schema_version": 1,
  "generated_at": "2026-06-05T10:00:00Z",
  "programs": [
    {
      "id": "koo_preprocessor",
      "name": "Koo Preprocessor",
      "version": "1.2.0",
      "description": "STEP/IGES 임포트, 메시 품질 평가, LS-DYNA keyword 변환.",
      "category": "preprocessor",
      "released_at": "2026-06-04T12:00:00Z",
      "package": {
        "type": "zip",
        "url": "https://heaxhub.internal/api/v1/installers/7c1f0a30-0e9d-4b8f-9c61-f1c2a0b3d401/download",
        "sha256": "9f1a5c1b2c3d4e5f60718293a4b5c6d7e8f9001122334455667788991011aabb",
        "size_bytes": 188743680
      },
      "entry": {
        "executable": "bin/KooPreprocessor.exe",
        "args_template": ["--workspace", "{workspace}"],
        "working_dir": ""
      },
      "requirements": {
        "requires_admin": false,
        "min_windows": "10.0.19045"
      },
      "lifecycle": {
        "post_install_check": {
          "executable": "bin/KooPreprocessor.exe",
          "args": ["--selftest", "--json"],
          "expected_stdout_regex": "^Koo Preprocessor 1\\.2\\.0"
        },
        "rollback_on_failure": true
      },
      "ui": {
        "color_accent": "#f59e0b",
        "show_in_tray": true
      },
      "tags": ["cae", "windows", "internal"],
      "visibility": "company"
    }
  ]
}
```

스키마와 어긋나면 안 되는 항목 (이전 잘못된 예제에서 자주 실수했던 부분):

- 최상위는 `{schema_version, generated_at, programs}` 만 허용. `pool` / `os` / `installer_type` 등은 전부 금지(`additionalProperties: false`).
- 각 program 의 `id` 는 `^[a-z0-9][a-z0-9_-]*$` 패턴 (예: `koo_preprocessor`).
- 모듈 zip 무결성은 `package.sha256` 1 가지로만 검증한다. Phase 1~2 는 ed25519 모듈 사인 없음 — 에이전트 본체 자동 업데이트만 `tauri-plugin-updater` 의 ed25519 사인을 사용한다.

---

## §5. 설치 흐름 — 12 스텝 (State Machine)

`hwax-launcher-plan-v2.md` §6/§8 의 State Machine 을 그대로 따른다. State 진행: idle → checking → outdated → downloading → verifying → extracting → swapping → installed → (running) → (stopped).

```
1. idle         사용자가 트레이에서 "Koo Preprocessor 1.2.0 설치" 클릭
2. checking     manifest 의 program.version 과 modules/koo_preprocessor/current.json 비교
3. outdated     로컬 미설치 또는 구버전이면 다음으로
4. downloading  GET package.url → cache/downloads/koo_preprocessor-1.2.0.zip.partial 로 스트림 저장
5. verifying    SHA-256 계산, manifest 의 package.sha256 와 비교
6. extracting   modules/koo_preprocessor/1.2.0.staging/ 로 zip slip 방어하며 압축 해제
7. (post_install_check)  bin/KooPreprocessor.exe --selftest --json 실행, expected_stdout_regex 매칭
8. swapping     rename(1.2.0.staging → 1.2.0). 같은 볼륨이라 Windows 의 rename 은 원자적
9. current.json 원자적 swap (tempfile + rename): {"version":"1.2.0","previous_version":"1.1.0"}
10. GC          keep_last_n_versions=3 정책으로 오래된 디렉토리 정리
11. installed   상태 표시 + 트레이 토스트
12. running     사용자가 "실행" 누르면 동일 manifest 의 entry.executable 만 spawn (화이트리스트)
```

각 전이마다 `tracing` 로그 1줄과 `install:progress` 이벤트가 React UI 로 emit 된다. 실패는 §6 으로.

---

## §6. 실패 시나리오 4가지

각 시나리오마다 (a) 진행 중 멈춘 위치 → (b) `POST /api/v1/launcher-agents/installs` 페이로드 → (c) 별도 `POST /api/v1/launcher-agents/audit` 페이로드 순서로 보인다. install-report 의 `status` 와 audit 의 `kind` 는 의도적으로 분리된 enum 이다 (status = 1 회 시도의 종착, kind = 사건 분류).

### 6.1 SHA-256 mismatch (스텝 5 실패)

다운로드는 끝났지만 해시가 manifest 와 다르다 → 즉시 .partial 삭제 → 실패.

<!-- validates: install-report.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "status": "failed",
  "started_at": "2026-06-05T10:11:00Z",
  "finished_at": "2026-06-05T10:13:42Z",
  "sha256_verified": false,
  "error": "sha256 mismatch",
  "log_excerpt": "expected=9f1a5c1b... actual=11aabbcc...\nstream bytes=188743680"
}
```

수반 audit:

<!-- validates: audit-event.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "kind": "sha256_mismatch",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "occurred_at": "2026-06-05T10:13:42Z",
  "severity": "error",
  "payload": {
    "expected": "9f1a5c1b2c3d4e5f60718293a4b5c6d7e8f9001122334455667788991011aabb",
    "actual":   "11aabbccddeeff00112233445566778899aabbccddeeff00112233445566ffff",
    "size_bytes": 188743680,
    "source_url": "https://heaxhub.internal/api/v1/installers/7c1f0a30-0e9d-4b8f-9c61-f1c2a0b3d401/download"
  },
  "client_meta": {
    "os": "windows",
    "os_version": "10.0.22631",
    "agent_version": "1.0.0",
    "hostname": "WS-CAE-014"
  }
}
```

### 6.2 post_install_check 실패 — swap 전에 발생

스텝 7 에서 selftest 가 비정상 종료(예: DLL 누락). **아직 current.json 을 안 건드렸으니 `rolled_back` 이 아니다. 그냥 `failed`.**

<!-- validates: install-report.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "status": "failed",
  "exit_code": 1,
  "started_at": "2026-06-05T11:02:00Z",
  "finished_at": "2026-06-05T11:04:18Z",
  "sha256_verified": true,
  "error": "post_install_check failed: selftest exited 1 before swap",
  "log_excerpt": "KooPreprocessor.exe --selftest --json\n[ERR] missing dep: vcruntime140.dll\nProcess exit=1"
}
```

수반 audit — `kind: install` + `payload.outcome` 로 결과를 표현:

<!-- validates: audit-event.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "kind": "install",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "occurred_at": "2026-06-05T11:04:18Z",
  "severity": "error",
  "payload": {
    "outcome": "failed",
    "stage": "post_install_check",
    "swap_performed": false,
    "previous_version": "1.1.0"
  },
  "client_meta": {
    "os": "windows",
    "os_version": "10.0.22631",
    "agent_version": "1.0.0",
    "hostname": "WS-CAE-014"
  }
}
```

### 6.3 다운로드 타임아웃 (스텝 4)

네트워크 stall, reqwest read timeout. partial 삭제.

<!-- validates: install-report.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "status": "failed",
  "started_at": "2026-06-05T12:00:00Z",
  "finished_at": "2026-06-05T12:05:30Z",
  "sha256_verified": false,
  "error": "download timeout",
  "log_excerpt": "GET https://heaxhub.internal/api/v1/installers/7c1f0a30.../download\nbytes_read=24117248/188743680 t=300s\nreqwest::Error(Timeout)"
}
```

수반 audit — 전용 `download_failed` kind:

<!-- validates: audit-event.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "kind": "download_failed",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "occurred_at": "2026-06-05T12:05:30Z",
  "severity": "warn",
  "payload": {
    "reason": "read_timeout",
    "bytes_read": 24117248,
    "size_bytes": 188743680,
    "elapsed_sec": 330
  },
  "client_meta": {
    "os": "windows",
    "os_version": "10.0.22631",
    "agent_version": "1.0.0",
    "hostname": "WS-CAE-014"
  }
}
```

### 6.4 디스크 풀 (스텝 4 ~ 6 사이)

다운로드는 시작됐는데 도중 ENOSPC. 일부만 디스크에 떨어졌으므로 `partial`. (`failed` 와 구분하는 이유: 운영팀이 "디스크 정리 후 재시도" 를 다른 대시보드 컬럼으로 보고 싶다.)

<!-- validates: install-report.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "status": "partial",
  "started_at": "2026-06-05T13:21:00Z",
  "finished_at": "2026-06-05T13:24:11Z",
  "sha256_verified": false,
  "error": "ENOSPC",
  "log_excerpt": "write cache/downloads/koo_preprocessor-1.2.0.zip.partial\nOSError 28 No space left on device\nfree=4194304 bytes, needed >=188743680"
}
```

수반 audit — kind 는 그대로 `install` (granular 한 디스크풀 kind 는 스키마에 없음), severity `error`:

<!-- validates: audit-event.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "kind": "install",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "occurred_at": "2026-06-05T13:24:11Z",
  "severity": "error",
  "payload": {
    "outcome": "partial",
    "stage": "downloading",
    "errno": "ENOSPC",
    "free_bytes": 4194304,
    "required_bytes": 188743680
  },
  "client_meta": {
    "os": "windows",
    "os_version": "10.0.22631",
    "agent_version": "1.0.0",
    "hostname": "WS-CAE-014"
  }
}
```

### 6.5 4 가지 요약 표

| # | 멈춘 위치 | install-report.status | error | audit.kind | swap 됐나? |
|---|---|---|---|---|---|
| 6.1 | 스텝 5 verifying | `failed` | `sha256 mismatch` | `sha256_mismatch` | X |
| 6.2 | 스텝 7 check | `failed` | `post_install_check failed: ...` | `install` (outcome=failed) | X |
| 6.3 | 스텝 4 download | `failed` | `download timeout` | `download_failed` | X |
| 6.4 | 스텝 4 ~ 6 | `partial` | `ENOSPC` | `install` (outcome=partial) | X |

핵심 — 6.2 가 `failed` 인 이유는 **swap 이전 단계에서 멈췄기 때문**이다. 사용자 PC 의 활성 버전(`current.json`)은 그대로 1.1.0 이다. 진짜 `rolled_back` 은 §7 처럼 사용자가 명시적으로 돌릴 때만 발생한다.

---

## §7. 진짜 롤백 — `status: rolled_back`

가정 — 1.2.0 이 swap 까지 끝나서 일단 설치는 됐다. 그런데 사용자가 실제로 실행해 보니 모종의 문제(예: 카스퍼스키가 첨부 DLL 을 검역). 트레이 → 상세 → "이전 버전으로" 클릭.

State 흐름:

```
installed(1.2.0) → user clicks "이전 버전으로"
                 → write_atomic_json(current.json, {"version":"1.1.0", "previous_version":"1.2.0", ...})
                 → installed(1.1.0)        # 디렉토리는 1.0.0/1.1.0/1.2.0/ 셋 다 남아 있다
                 → POST /api/v1/launcher-agents/installs (status=rolled_back, previous_version="1.1.0")
                 → POST /api/v1/launcher-agents/audit (kind=rollback)
```

`previous_version` 의 의미는 **"롤백해서 도달한 버전"** 이다(스키마 설명에 명시). 즉 "1.2.0 에서 1.1.0 으로 갔다" = `version: "1.2.0"` + `previous_version: "1.1.0"`.

<!-- validates: install-report.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "status": "rolled_back",
  "started_at": "2026-06-05T14:30:00Z",
  "finished_at": "2026-06-05T14:30:02Z",
  "sha256_verified": true,
  "error": null,
  "previous_version": "1.1.0",
  "log_excerpt": "user_action=rollback target=1.1.0\nswap current.json 1.2.0 -> 1.1.0\nrolled_back_from=1.2.0"
}
```

수반 audit (§8.4 와 같은 인스턴스가 여기에서 발생). exit_code 는 정상 종료가 아니라 "사용자가 롤백 동작을 취했다" 라는 의미라서 null 이 자연스럽고, 스키마는 exit_code 를 optional 로 둔다.

---

## §8. Audit 트레이스 — 5종

`POST /api/v1/launcher-agents/audit` 의 단건 페이로드. 모두 `audit-event.schema.json` 으로 검증된다. `additionalProperties: false` 가 엄격하게 적용되니 `event_id` / `ts` / `category` 같이 스키마에 없는 키는 절대로 넣지 않는다.

### 8.1 enrollment — 페어링 성공

<!-- validates: audit-event.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "kind": "enrollment",
  "occurred_at": "2026-06-05T09:00:00Z",
  "severity": "info",
  "payload": {
    "hostname": "WS-CAE-014",
    "enrolled_by_user": "alice@heax.example.com"
  },
  "client_meta": {
    "os": "windows",
    "os_version": "10.0.22631",
    "agent_version": "1.0.0",
    "hostname": "WS-CAE-014"
  }
}
```

### 8.2 install — 성공

<!-- validates: audit-event.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "kind": "install",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "occurred_at": "2026-06-05T10:09:55Z",
  "severity": "info",
  "payload": {
    "outcome": "success",
    "duration_ms": 132480,
    "package_size_bytes": 188743680,
    "previous_version": "1.1.0"
  },
  "client_meta": {
    "os": "windows",
    "os_version": "10.0.22631",
    "agent_version": "1.0.0",
    "hostname": "WS-CAE-014"
  }
}
```

### 8.3 install — 실패 (§6.2 의 audit 와 동일 인스턴스, 여기엔 요약 형태로 다시)

<!-- validates: audit-event.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "kind": "install",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "occurred_at": "2026-06-05T11:04:18Z",
  "severity": "error",
  "payload": {
    "outcome": "failed",
    "stage": "post_install_check",
    "swap_performed": false
  },
  "client_meta": {
    "os": "windows",
    "os_version": "10.0.22631",
    "agent_version": "1.0.0",
    "hostname": "WS-CAE-014"
  }
}
```

### 8.4 rollback

<!-- validates: audit-event.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "kind": "rollback",
  "app_id": "koo_preprocessor",
  "version": "1.1.0",
  "occurred_at": "2026-06-05T14:30:02Z",
  "severity": "warn",
  "payload": {
    "rolled_back_from": "1.2.0",
    "rolled_back_to": "1.1.0",
    "trigger": "user_click",
    "reason": "av suspected 1.2.0 dll"
  },
  "client_meta": {
    "os": "windows",
    "os_version": "10.0.22631",
    "agent_version": "1.0.0",
    "hostname": "WS-CAE-014"
  }
}
```

### 8.5 av_block — EDR 차단

<!-- validates: audit-event.schema.json -->
```json
{
  "agent_id": "018f5a3b-4c2d-7e1f-9a8b-1234567890ab",
  "kind": "av_block",
  "app_id": "koo_preprocessor",
  "version": "1.2.0",
  "occurred_at": "2026-06-05T14:25:11Z",
  "severity": "error",
  "payload": {
    "av_product": "Kaspersky Endpoint Security",
    "detection_name": "HEUR:Trojan.Win32.Generic",
    "quarantined_path": "C:/Users/koo/AppData/Local/HWAXAgent/modules/koo_preprocessor/1.2.0/bin/KooPreprocessor.exe",
    "action": "quarantine"
  },
  "client_meta": {
    "os": "windows",
    "os_version": "10.0.22631",
    "agent_version": "1.0.0",
    "hostname": "WS-CAE-014"
  }
}
```

---

## §9. 보안 체크리스트 (8 항목)

배포 전에 PR 코드 리뷰에서 강제로 확인.

1. ■ `Bearer` 토큰 audience 가 `hwax-agent`, sub 가 `agent_id` 와 일치하는지 (서버 측 미들웨어 단정).
2. ■ `installer_url` 은 화이트리스트 도메인(`heaxhub.internal` 등) 만 허용. config.json 의 `allowed_origins` 와 정확 매칭.
3. ■ 다운로드 후 SHA-256 검증 실패 시 즉시 `.partial` 삭제 + `install-report.sha256_verified: false`.
4. ■ zip 압축 해제는 zip slip 방어. 각 entry path 를 canonicalize 후 staging 하위 검증.
5. ■ staging → final 전환은 같은 볼륨 rename 으로만 (Windows atomic 보장).
6. ■ device_jwt / refresh_token 은 Credential Manager 보관, 파일 평문 금지.
7. ■ Tauri `tauri.conf.json` allowlist 최소화 — `shell.open: false`, `http.scope` 는 단일 도메인만.
8. ■ 실행은 `manifest.entry.executable` 화이트리스트 외 절대 금지. 사용자 입력 인자 없음.

---

## §10. 개발자 셋업 — 로컬에서 끝까지 돌리기

전제 — HEAXHub 가 `http://localhost:4180` (Caddy) 으로 이미 떠 있고, HWAXAgent 통합 개발자가 자기 PC 에서 백엔드 + 에이전트를 모두 띄워 한 사이클 돌린다.

1. **백엔드 확인** — `curl -s http://localhost:4180/api/v1/healthz | jq .`
2. **enrollment_token 발급** — `POST /api/v1/admin/agents` (관리자 토큰 필요, body `{"name":"dev-laptop","pool":"hwax-launcher"}`). 응답 `enrollment_token` 저장.
3. **integrations/ 에 모의 앱 등록** — `integrations/koo-preprocessor/.portal/manifest.yaml` 에 §2 yaml 저장 + 모의 zip 1개 (`dd if=/dev/urandom of=... bs=1M count=1` 후 `sha256sum` 으로 manifest 갱신). scanner 가 자동으로 DB 3개 행 생성.
4. **(Windows PC) Tauri 2 (Rust) 개발 빌드** — `cd HWAXAgent/apps/agent && pnpm install && pnpm tauri dev`. **NOT** `dotnet run` — v2 plan §1 의 Tauri 2 결정에 따른다.
5. **페어링** — HWAXAgent UI 에 enrollment_token 붙여넣기 → `POST /api/v1/launcher-agents/enroll` → device JWT 수령 → Credential Manager 저장.
6. **manifest 도달 확인** — 트레이 → "지금 동기화" → `GET /api/v1/launcher-agents/manifest?os=windows-x64` → 응답 `programs[0].id == "koo_preprocessor"` 가 §4.2 와 동일 모양인지 시각 확인.
7. **설치 12 스텝 관찰** — Koo Preprocessor → "설치". `install:progress` 이벤트가 download → verify → extract → check → swap 순으로 흐르고, 백엔드 로그에 `POST /api/v1/launcher-agents/installs (status=success)` 가 찍힌다.

이 일곱 단계를 한 번 완수하면 본 문서의 모든 페이로드가 실측 데이터로 한 번씩 흘러간 셈이다.

---

## §11. 검증 게이트

```
$ cd /home/koopark/claude/HEAXHub
$ backend/.venv/bin/python scripts/validate-e2e-examples.py
checked 15 JSON block(s) in /home/koopark/claude/HEAXHub/docs/hwax-agent-e2e-example.md
  - audit-event.schema.json: 9
  - install-report.schema.json: 5
  - manifest.schema.json: 1
all blocks validated against their declared schema.
```

CI 에 본 명령을 게이트로 걸어 두면 스키마와 문서가 어긋나는 순간을 PR 단계에서 잡을 수 있다.

— 끝.
