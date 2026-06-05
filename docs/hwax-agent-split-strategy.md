# HWAXAgent 별도 레포 분리 — 협업 전략

작성일: 2026-06-05
상태: 합의 완료, 첫 PR 대기
대상 독자: HEAXHub 메인테이너, HWAXAgent 윈도우 측 개발자

---

## §1. 결정 요약 (5줄)

1. HWAXAgent 는 별도 GitHub 레포에서 Windows 전용으로 개발한다. Tauri(WinUI3 백업 옵션) 기반 로컬 런처/에이전트.
2. HEAXHub(이 레포) 는 서버 통합 인터페이스 — REST/WS, DB, 매니페스트, installer URL 발급 — 만 책임진다.
3. 본 Linux 환경에서는 윈도우 측 코드·스크립트(Tauri config, Cargo.toml, GitHub Actions Windows runner yml, .ps1 등)를 만들지 않는다. 윈도우 PC 에서 직접 부트스트랩 한다.
4. 두 레포는 `contracts/hwax-agent/` 단일 디렉터리(이 HEAXHub 레포가 소유)를 통해 계약 — JSON Schema, OpenAPI fragment, design token — 만 공유한다.
5. 일상 협업은 **PR 양방향 교환**. HWAXAgent 가 계약 변경을 요청할 땐 HEAXHub 로 PR 을 직접 보내고, 반대 방향도 동일.

---

## §2. 책임 분담표

| HEAXHub 책임 | HWAXAgent 책임 | 공유 계약물 (contracts/hwax-agent/) |
| --- | --- | --- |
| `/api/v1/agents/*` REST 엔드포인트 구현 | Tauri shell, WebView, system tray | `openapi.yaml` (agents + installers fragment) |
| `/api/v1/admin/agents` 운영자 API | `programs.json` 로컬 머신 인벤토리 생성/관리 | `manifest.schema.json` |
| `windows_agents` 테이블 스키마 + Alembic | enrollment_token 입력 UI + 보관(Windows Credential Manager) | `install-report.schema.json` |
| `installer_packages` 메타 + presigned URL | 다운로드/실행/Setup 자동화 (MSI/EXE/Inno) | `audit-event.schema.json` |
| JWT 발급 (audience=`hwax-agent`) + 회전 | heartbeat/poll 워커 | `tokens.css` (디자인 토큰) |
| 매니페스트 YAML 검증 (`schema_version: 2`) | install-report POST 보낼 payload 구성 | `CHANGELOG.md` (SemVer) |
| `installer_url` (SHA256 동봉) 발급 | SHA256 검증 + 무결성 확인 | `README.md` (사용 가이드) |
| 감사 로그 수신/저장 | 감사 이벤트 송신 (foreground/background) | `examples/` (샘플 payload) |
| 운영자 UI (앱 등록·승인) | 사용자 UI (실행/업데이트/제거) | — |
| 본 문서 + `hwax-launcher-plan*.md` 유지 | HWAXAgent README, 빌드 가이드, 사이닝 가이드 | — |

---

## §3. 별도 레포로 분리하는 5가지 이유

1. **Windows CI 분리** — HEAXHub 는 Linux 컨테이너(Apptainer) 기반으로 GitHub Actions Linux runner 또는 사내 Slurm에서 빌드된다. HWAXAgent 는 Windows runner + MSVC + MSI 패키징 단계가 필요해 워크플로 성격이 완전히 다르다. 한 레포에 두면 양쪽 CI 가 서로의 cache·secret 을 침범한다.
2. **권한 분리** — HEAXHub 코드베이스에 접근하는 사내 풀스택 개발자와, HWAXAgent 빌드 키·코드 사인 인증서를 다루는 릴리스 엔지니어는 권한 등급이 다르다. GitHub team/repo permission 으로 깔끔하게 끊는 가장 단순한 방법은 레포 분리다.
3. **릴리스 사이클 독립** — HEAXHub 는 매니페스트·앱 등록 흐름 위주로 주 단위 배포 빈도가 높다. HWAXAgent 는 Windows 클라이언트 특성상 분기 단위 릴리스 + 회귀 테스트가 무겁다. 릴리스 태그/체인지로그가 섞이면 양쪽 모두 노이즈다.
4. **코드 사인 키 보관 분리** — HWAXAgent 빌드 잡은 Azure Key Vault(또는 사내 HSM)의 코드 사인 인증서를 short-lived OIDC token 으로 가져온다. HEAXHub 빌드는 이 키에 접근할 이유가 전혀 없다. 같은 레포에 두면 `secrets.*` 가 의도치 않게 노출될 위험이 생긴다.
5. **양쪽 PR 충돌 0** — 같은 레포에서 윈도우 작업과 서버 작업이 동시에 진행되면 `package.json`, `pyproject.toml`, lockfile, CI yml 에서 머지 충돌이 끊임없이 발생한다. 분리하면 충돌 표면이 `contracts/hwax-agent/` 디렉터리 한 곳으로 줄어든다 — 그리고 이 디렉터리는 변경 빈도가 낮다.

---

## §4. 공유 계약물 (Contract Surface)

위치: `HEAXHub/contracts/hwax-agent/` (이 HEAXHub 레포가 source of truth)
HWAXAgent 측은 git submodule 또는 `pnpm fetch-schemas` 스크립트로 특정 tag 를 가져와 사용한다.

```
contracts/hwax-agent/
├── README.md                  사용 가이드, 버전 호환 표, 동기화 방법
├── CHANGELOG.md               SemVer 단위 계약 변경 이력
├── manifest.schema.json       programs.json 항목 JSON Schema (draft 2020-12)
├── install-report.schema.json POST /api/v1/agents/install-report payload
├── audit-event.schema.json    POST /api/v1/agents/audit payload
├── openapi.yaml               OpenAPI 3.1 fragment — agents/* + /apps/{id}/installers/{ver}/download
├── tokens.css                 디자인 토큰 (CSS custom properties) — HWAXAgent webview 가 사용
└── examples/
    ├── manifest.sample.json
    ├── install-report.sample.json
    └── audit-event.sample.json
```

### 4.1 manifest.schema.json — 핵심 필드 (요약)

- `id` (string, slug, `^[a-z0-9_-]+$`) — HEAXHub `apps.id` 와 1:1
- `name` (string)
- `version` (string, SemVer)
- `app_type` — enum: `cli_tool | web_app | windows_gui | remote_app | external_link | slurm_job | container_app`
- `execution_target` — enum: `linux_runner | slurm | apptainer | windows_worker | external_url | local_pc`
- `installer` (object): `os`, `installer_url`, `sha256`, `size_bytes`, `signed`
- `permissions.visibility` — enum: `private | team | department | company`
- `device_kind` (string, **신규**) — `desktop | laptop | vdi | kiosk`

### 4.2 install-report.schema.json

- `agent_id` (uuid) — `windows_agents.id`
- `app_id` (string)
- `version` (string)
- `status` — `installed | failed | rolled_back`
- `installed_at` (datetime, RFC3339)
- `installer_sha256` (string, 64hex) — `installer_packages.sha256` 와 검증
- `exit_code` (int, nullable)
- `log_tail` (string, ≤ 4KiB)

### 4.3 audit-event.schema.json

- `agent_id` (uuid)
- `event_type` — `launch | uninstall | update | crash | heartbeat_drop`
- `app_id` (string, nullable — heartbeat_drop 의 경우 null)
- `occurred_at` (datetime)
- `payload` (object, free-form, ≤ 2KiB)

### 4.4 openapi.yaml — 포함 endpoint

본 HEAXHub 레포 `backend/app/api/v1/agents.py` 와 `installers.py` 에서 실제 구현되는 경로만 표기:

- `POST /api/v1/agents/heartbeat`
- `POST /api/v1/agents/poll`
- `POST /api/v1/agents/jobs/{job_id}/log`
- `POST /api/v1/agents/jobs/{job_id}/files`
- `POST /api/v1/agents/jobs/{job_id}/status`
- `POST /api/v1/agents/install-report` (**신규**, §11 참고)
- `POST /api/v1/agents/audit` (**신규**, §11 참고)
- `GET  /api/v1/apps/{app_id}/installers/{version}/download` (installer URL, presigned)
- `POST /api/v1/admin/agents` (운영자 — enrollment_token plaintext 1회 반환)
- `GET  /api/v1/admin/agents`
- `DELETE /api/v1/admin/agents/{agent_id}` (disable)

### 4.5 tokens.css

CSS custom properties 만 export. HEAXHub frontend 와 같은 룩&필을 HWAXAgent webview 에 강제. 변경 시 SemVer minor bump.

### 4.6 CHANGELOG.md / SemVer 규칙

- MAJOR: 기존 필드 의미 변경, 필수 필드 추가
- MINOR: 선택 필드 추가, enum 값 추가
- PATCH: 문서/예제만 변경

---

## §5. 양 레포 README 상호 링크 + 라이센스 + 책임자

### 5.1 HEAXHub README 에 추가할 섹션 (요지)

```
## Companion: HWAXAgent (Windows local launcher)
HWAXAgent 는 별도 레포에서 관리됩니다.
- Repo: https://github.com/<org>/HWAXAgent
- 계약물: ./contracts/hwax-agent/ (이 레포가 source of truth)
- 책임자: @koopark (HEAXHub) / @<windows-lead> (HWAXAgent)
```

### 5.2 HWAXAgent README 에 들어갈 섹션 (윈도우 측 메인테이너에게 위임 — 본 Linux 환경에서 작성하지 않음)

링크 가이드만 명시: HEAXHub repo URL, contracts 경로, 책임자 GitHub handle.

### 5.3 라이센스

- HEAXHub: 사내 비공개 / 사내 OSS 정책에 따름
- HWAXAgent: 동일 정책 — 단, 코드 사인 인증서·MSI 빌드 산출물은 어떤 경우에도 공개 레포에 두지 않는다.
- `contracts/hwax-agent/` 는 양 레포가 모두 인용할 수 있도록 가장 관대한 사내 정책(예: Apache-2.0 사내 변형) 으로 표기.

### 5.4 책임자 표

| 영역 | 책임자 | Backup |
| --- | --- | --- |
| HEAXHub 서버/API | HEAXHub 메인테이너 (@koopark) | 풀스택 팀 |
| HWAXAgent 윈도우 빌드/사이닝 | (윈도우 릴리스 엔지니어) | (백업 1명) |
| contracts/ schema 변경 승인 | HEAXHub 메인테이너 + HWAXAgent 리드 합의 (양쪽 approval 필수) | — |
| 보안 인시던트 | 보안팀 / 양 메인테이너 즉시 합류 | — |

---

## §6. ▶ PR 협업 워크플로 — 양방향 다이어그램

### 6.1 HWAXAgent → HEAXHub 방향 (계약 추가 요청)

```
[HWAXAgent dev]
   │
   │ (1) HWAXAgent main 에 issue 생성
   │     label: needs-heaxhub-change
   │     본문: 어떤 endpoint/필드가 왜 필요한지
   ▼
[HWAXAgent issue]
   │
   │ (2) 같은 사람이 HEAXHub 레포로 fork → branch
   │     contracts/hwax-agent/openapi.yaml + schema.json 수정 PR
   │     PR 본문 1줄 = HWAXAgent issue URL 링크
   ▼
[HEAXHub PR (contract)]
   │
   │ (3) HEAXHub 리뷰어가
   │     - 계약 수정만 먼저 머지하거나
   │     - 같은 PR 에서 backend/app/api/v1/agents.py 구현까지 같이
   │       (선택은 리뷰어가 결정)
   ▼
[HEAXHub main]
   │
   │ (4) HEAXHub release tag
   │     contracts/CHANGELOG.md 에 SemVer bump
   ▼
[contracts vX.Y.Z]
   │
   │ (5) HWAXAgent 측이
   │     - git submodule update --remote, 또는
   │     - pnpm fetch-schemas (스크립트로 raw.githubusercontent 의 특정 tag 가져옴)
   │     로 동기화 → 자기 PR 마무리
   ▼
[HWAXAgent main 머지 → release]
```

### 6.2 HEAXHub → HWAXAgent 방향 (서버가 매니페스트 필드 추가 등)

```
[HEAXHub dev]
   │ (1) HEAXHub 레포에서 contracts/hwax-agent/ 수정 PR 작성
   │     - schema 변경 + CHANGELOG bump + examples 갱신
   ▼
[HEAXHub PR (contract)]
   │ (2) HEAXHub 메인테이너 + HWAXAgent 리드 둘 다 approve (CODEOWNERS 강제)
   ▼
[HEAXHub main 머지]
   │ (3) 머지와 동시에 HWAXAgent 레포에 issue 자동 생성
   │     (GitHub Actions: contracts 디렉터리 변경 감지 → gh issue create --repo <org>/HWAXAgent)
   ▼
[HWAXAgent issue: contracts vX.Y.Z 대응 필요]
   │ (4) HWAXAgent dev 가 client-side 대응 PR 을 자기 레포에 작성
   │     - 새 필드 파싱, UI 반영, 회귀 테스트
   ▼
[HWAXAgent main 머지]
```

### 6.3 머지 게이트 (양 방향 공통)

- contracts MAJOR bump 는 반드시 양 레포 메인테이너 둘 다 approve.
- contracts MINOR/PATCH 는 단일 메인테이너 approve 로 가능 — 단, HWAXAgent 리드에게 mention 통지.
- HWAXAgent release 가 contracts vX.Y.Z 를 요구하면, HEAXHub 도 그 이상의 contracts 를 deploy 한 상태여야 한다 (§10 호환 매트릭스 참고).

---

## §7. ▶ PR/이슈 템플릿 (HEAXHub 측 신규 파일)

### 7.1 `.github/pull_request_template.md`

```markdown
## 요약
- 변경 1줄 요약:
- 영향 범위: [ ] backend  [ ] frontend  [ ] contracts/hwax-agent  [ ] docs  [ ] infra

## 관련 이슈/링크
- HWAXAgent issue (해당 시):
- HEAXHub issue:

## contracts/hwax-agent 변경 체크 (해당 시만)
- [ ] CHANGELOG.md SemVer bump 완료
- [ ] manifest.schema.json / install-report.schema.json / audit-event.schema.json 갱신
- [ ] openapi.yaml 의 path/schema 일치
- [ ] examples/*.json 동작 확인
- [ ] HWAXAgent 리드 mention 완료: @<windows-lead>

## 검증
- [ ] 로컬 `pytest backend/app/tests` 통과
- [ ] `bash deploy/apptainer/start.sh` 로 로컬 기동 OK
- [ ] (계약 변경 시) 샘플 payload 가 schema 에 valid

## 보안
- [ ] secret/token plaintext 가 코드에 포함되지 않음
- [ ] 사인 키·인증서 파일이 staging 되지 않음
```

### 7.2 `.github/ISSUE_TEMPLATE/hwax-agent-feedback.md`

```markdown
---
name: HWAXAgent 측 피드백 / 요청
about: HWAXAgent 개발 중 HEAXHub 에 endpoint·매니페스트·계약 변경이 필요할 때
title: "[hwax-agent] "
labels: hwax-agent, needs-triage
assignees: ''
---

## 무엇이 필요한가
(예: install-report 에 `rollback_reason` 필드 추가)

## 왜 필요한가 — 사용자 시나리오
- Windows PC 에서 어떤 상황에서 발생하는가
- 현재 우회 방법이 있는가

## 제안하는 contracts 변경
- [ ] manifest.schema.json
- [ ] install-report.schema.json
- [ ] audit-event.schema.json
- [ ] openapi.yaml
- [ ] tokens.css
- [ ] (없음 — 구현만 필요)

## SemVer 영향 추정
- [ ] MAJOR  [ ] MINOR  [ ] PATCH

## HWAXAgent 측 트래킹 issue
- URL:
```

### 7.3 `.github/CODEOWNERS` (요지)

```
/contracts/hwax-agent/   @<heaxhub-lead> @<windows-lead>
/backend/app/api/v1/agents.py    @<heaxhub-lead>
/backend/app/api/v1/installers.py @<heaxhub-lead>
/docs/hwax-*             @<heaxhub-lead> @<windows-lead>
```

---

## §8. 권한 / 보안 분리

### 8.1 코드 사인

- **HEAXHub** 는 사내 코드 사인 인증서를 **보관하지 않는다**. CI secret 에도 등록하지 않는다.
- `installer_packages.sha256` 컬럼에 빌드 시점에 알려진 SHA256 만 기록 (`installer_packages` 모델 확인 — `signed: bool` 컬럼도 그대로 사용).
- HEAXHub 가 발급하는 installer URL 은 단순 presigned URL — 서명은 이미 MSI 자체에 박혀 있음.

### 8.2 HWAXAgent 측 (윈도우 PC / 윈도우 CI)

- 빌드 시 Azure Key Vault (또는 사내 HSM) 에서 short-lived OIDC token 으로 사인 인증서 fetch.
- **release 빌드만** 사인. dev 빌드는 self-signed test cert 로 충분.
- 사인 키 파일은 어떤 경우에도 git 에 들어가지 않는다 — `.gitignore` 강제, pre-commit hook 으로 차단 (HWAXAgent 측 책임).

### 8.3 JWT 분리

- HEAXHub `app/core/security.py` 의 `create_access_token` (line 57) 은 audience claim 을 받지 않는다. 본 작업에서 **HWAXAgent 용 토큰만 `aud="hwax-agent"`** 를 추가하도록 helper 한 줄 신설 (예: `create_agent_token(agent_id) → token`).
  - rationale: 일반 user access token (subject=user.id) 과 agent token (subject=agent.id) 가 토큰 디코드 시 명확히 구분되어야 한다.
- 기존 `windows_agents.auth_token_hash` 메커니즘(SHA256, `secrets.token_urlsafe(32)`, plaintext 1회 노출)은 enrollment 단계에서 유지.
- enrollment 이후 HWAXAgent 는 plaintext enrollment_token 으로 `/api/v1/agents/exchange` (**신규**) 를 호출해 audience=`hwax-agent` 인 JWT 를 받는다 — 이후 모든 호출에서 JWT 사용. (rotation 은 refresh_token 모델 재사용 검토)

### 8.4 운영자 통제

- `POST /api/v1/admin/agents` 의 token plaintext 는 응답으로 **단 1회** 반환 (이미 구현). 사내 1password / vault 에 즉시 저장하라는 안내 문구 운영자 UI 에 명시.
- `DELETE /api/v1/admin/agents/{id}` 는 `disabled=True` 로만 마킹 (이미 구현). 행 삭제 금지 — 감사 로그 보존.

---

## §9. ◇ 로컬 개발 시 양쪽 동시 실행

### 9.1 HEAXHub 측 (Linux 서버 / 개발자 워크스테이션)

```bash
bash deploy/apptainer/start.sh
# Postgres, Redis, FastAPI, worker, frontend 가 한 번에 기동
# 외부 노출 URL 확인: <heaxhub-host>:8000
```

### 9.2 HWAXAgent 측 (Windows PC — 본 Linux 환경에서는 절대 만들지 않음)

윈도우 PC 에서 다음 흐름을 **사용자가 직접** 수행한다. 본 레포는 가이드만 제공:

1. 윈도우 PC 에 Tauri 부트스트랩 — pnpm + Rust toolchain + WebView2 Runtime.
2. HWAXAgent repo 클론.
3. `contracts/` 폴더는 HEAXHub 레포 특정 tag 를 submodule 로 끌어옴.
4. HWAXAgent 의 `config.json` (또는 동등한 설정 파일) 에 서버 URL 입력: 예 `https://<heaxhub-host>:8000`.
5. HWAXAgent 기동 (pnpm tauri dev — 명령 자체는 윈도우 측에서 입력).

### 9.3 가상 에이전트 페어링 (양쪽 처음 연결)

```
[HEAXHub Linux]                            [HWAXAgent Windows PC]
   │                                                  │
   │ (a) 운영자가 HEAXHub UI 또는 curl 로                │
   │     POST /api/v1/admin/agents                    │
   │     body: { name, pool, hostname?, device_kind } │
   │     ← 응답: { agent, token }  (token plaintext)  │
   │                                                  │
   │              token plaintext 전달 (사내 채널)        │
   │ ────────────────────────────────────────────────▶│
   │                                                  │ (b) HWAXAgent UI 에 enrollment_token 입력
   │                                                  │     Windows Credential Manager 저장
   │                                                  │
   │ (c) POST /api/v1/agents/heartbeat                │
   │     Authorization: Bearer <plaintext>            │
   │ ◀────────────────────────────────────────────────│
   │                                                  │
   │ (d) verify_token() → status=online, last_seen 갱신 │
```

### 9.4 디버깅 팁

- 네트워크 차단된 사내 환경: HWAXAgent 가 HEAXHub 도메인에 직접 도달 가능한지 먼저 `curl /healthz` 확인.
- token 분실: 행 삭제하지 말고 disable → 새 row 등록 → 새 token 발급.

---

## §10. 양 레포 버전 호환 매트릭스

contracts 가 source of truth — HWAXAgent / HEAXHub release 노트에 적용된 contracts 버전 범위를 박는다.

| contracts | HWAXAgent (min) | HEAXHub (min) | 비고 |
| --- | --- | --- | --- |
| 0.1.x | 0.1.0 | 0.x (현재 main) | 최초 stub. enroll + heartbeat 만 |
| 0.2.x | 0.2.0 | 0.y | install-report 도입 (§11 참고) |
| 0.3.x | 0.3.0 | 0.z | audit-event 도입, device_kind 필드 |
| 1.0.0 | 1.0.0 | 1.0.0 | 안정화 — production 승인 |

규칙:
- HWAXAgent 가 contracts vA 를 요구할 때, HEAXHub 는 **vA 이상**을 deploy 한 상태여야 한다.
- 그 반대(HEAXHub 가 HWAXAgent 신버전 요구)는 발생시키지 않는다 — 서버가 클라이언트보다 항상 더 관대해야 한다 (forward compatibility).
- MAJOR 차이 (예: 1.x vs 2.x) 는 양쪽 모두 동시 업그레이드 — 어느 한쪽이 다운그레이드하면 HEAXHub 가 `400 contracts_version_mismatch` 로 거부.

---

## §11. ◆ 다음 액션 — HEAXHub 측만, 5개

각 항목은 별도 PR. 의존 순서 명시.

1. **`contracts/hwax-agent/` 폴더 생성 PR**
   - `README.md`, `CHANGELOG.md (0.1.0 entry)`, `manifest.schema.json` (현 `MANIFEST_SPEC.md` 를 JSON Schema 로 옮김), `install-report.schema.json`, `audit-event.schema.json`, `openapi.yaml` (현재 agents.py / installers.py 라우트 그대로 fragment), `tokens.css` (frontend 의 현행 토큰 export), `examples/*` 샘플 3종.
   - CI 추가: schema 가 자체적으로 valid 한지 (`ajv compile`) + examples 가 schema 에 valid 한지 검증.

2. **Alembic migration — `windows_agents.device_kind` 컬럼 추가 PR**
   - `device_kind: String(16) | None` (`desktop | laptop | vdi | kiosk`).
   - `windows_agent.py` 모델 동기화. `POST /api/v1/admin/agents` 요청 스키마에 optional 추가.
   - 의존: 1번이 schema 에 `device_kind` 를 포함한 뒤 진행.

3. **`/api/v1/agents/` 라우터 stub 확장 PR — enroll/exchange/install-report/audit**
   - `POST /api/v1/agents/exchange` (enrollment_token plaintext → JWT aud=`hwax-agent`).
   - `POST /api/v1/agents/install-report` (`install-report.schema.json` 와 정확히 매칭, DB 저장만 — 처리 워커는 후속).
   - `POST /api/v1/agents/audit` (감사 로그 INSERT, 워커 없음).
   - 핸들러는 모두 thin stub: 입력 검증 + DB persist 만, 비즈니스 로직은 TODO.
   - 의존: 1, 2번.

4. **PR/이슈 템플릿 + CODEOWNERS PR**
   - `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/hwax-agent-feedback.md`, `.github/CODEOWNERS` (§7 내용 그대로).
   - 의존 없음 — 1번과 병렬 가능.

5. **`hwax-agent-*` docs 3종 commit PR**
   - 본 문서(`docs/hwax-agent-split-strategy.md`) + 기존 `hwax-launcher-plan*.md` 3건을 docs index 에 링크 추가.
   - HEAXHub README 에 §5.1 의 Companion 섹션 삽입.
   - 의존 없음 — 가장 먼저 머지 가능.

비고: 본 액션 목록 어디에도 윈도우 측 코드/스크립트/CI yml/Tauri config 생성은 없다. HWAXAgent 레포의 부트스트랩은 윈도우 PC 의 메인테이너가 직접 수행한다.

---

부록 A — 본 문서가 가리키는 HEAXHub 내 실재 파일 (확인 완료, 2026-06-05 기준)

- `backend/app/db/models/windows_agent.py` — `windows_agents` 테이블 정의
- `backend/app/db/models/installer_package.py` — `installer_packages`, URL 컬럼명은 `installer_url`
- `backend/app/db/models/app.py` — `AppType.WINDOWS_GUI`, `ExecutionTarget.LOCAL_PC` 존재
- `backend/app/api/v1/agents.py` — agents 라우터 (heartbeat/poll/log/files/status) + admin_router (admin/agents)
- `backend/app/api/v1/installers.py` — `/api/v1/apps` 하위에 mount, 네임스페이스 공유 주의
- `backend/app/services/agent_registry.py` — `_generate_token` (line 27-28), `verify_token` (line 62)
- `backend/app/core/security.py` — `create_access_token` (line 57), `decode_token` (line 115)
- `docs/MANIFEST_SPEC.md` — manifest.schema.json 변환 소스
- `integrations/heax-demo-fastapi-react/.portal/manifest.yaml` — schema_version: 2 샘플
