# HWAXAgent 백엔드 통합 계획

본 문서는 HEAXHub 백엔드에 **HWAXAgent**(Windows 트레이 런처) 통합을 단계적으로 도입하기 위한 작업 계획서입니다. 기존 `WindowsAgent` 모델·`/api/v1/agents/*` 라우터·`InstallerPackage` 자원을 그대로 활용하되, 런처 전용 흐름(enrollment → manifest → install report → audit)을 신규로 얹는 것이 핵심입니다. 산출물은 `contracts/hwax-agent/` 에 묶어 SemVer 로 버전 관리합니다.

대상 독자: 백엔드 담당자, 운영자, HWAXAgent 클라이언트 개발자.

---

## §1. Phase 1 — 부트스트랩 (이번 스프린트, 약 1주)

목표: **런처 한 대를 등록하고, 매니페스트를 받아 첫 프로그램을 한 번 설치하기까지의 최소 경로**를 닫는다. 통계·정책·웹소켓은 의도적으로 제외한다.

### 1.1 Alembic 마이그레이션 `0006_windows_agents_device_kind` + ORM 동시 동기화

- 대상 테이블: `windows_agents`
- 추가 컬럼: `device_kind VARCHAR(16) NULL`
  - 값: `'launcher'`(HWAXAgent), `'service'`(기존 Windows Worker Agent), `NULL`(이전 데이터)
  - 인덱스 불필요(카디널리티 ≤ 3, 풀스캔 비용 무시 가능)
- 기존 행은 `NULL` 로 남겨두고, Phase 2 에서 일괄 `'service'` 로 백필한다(이번 스프린트 범위 밖).
- 다운그레이드는 컬럼 DROP.

**중요: 같은 PR에서 ORM도 동시 갱신해야 한다.** 마이그레이션만 들어가고 ORM(`backend/app/db/models/windows_agent.py`) 이 갱신되지 않으면 `agent_service.issue_enrollment_token` 이 `device_kind='launcher'` 를 INSERT 할 길이 없다(raw SQL 강제). 추가 매핑:

```python
# windows_agent.py
device_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
```

또한 `POST /api/v1/admin/agents` 의 `AgentRegisterIn` Pydantic 모델도 같은 PR에서 `device_kind: str | None = None` 을 받도록 보강. 그렇지 않으면 어드민이 보낸 `device_kind` 가 Pydantic 단계에서 조용히 드롭된다.

### 1.2 신규 서비스 `backend/app/services/agent_service.py`

`agent_registry.py` 와 역할을 분리한다. `agent_registry.py` 는 **기존 서비스 에이전트(폴링형)** 용으로 남기고, `agent_service.py` 는 **런처(JWT 발급형)** 전용 로직을 담는다.

공개 함수(최소):

- `issue_enrollment_token(operator: User, *, name: str, pool: str) -> tuple[WindowsAgent, str]`
  - `device_kind='launcher'` 로 `WindowsAgent` 행을 만들고, `auth_token_hash` 에 SHA-256 해시를 적는 점은 동일. 반환되는 평문이 곧 enrollment_token.
- `redeem_enrollment_token(token: str, *, hostname: str | None, agent_version: str | None) -> EnrollmentResult`
  - 토큰 검증 → device JWT 쌍(access 1h / refresh 30d) 발급.
  - access 토큰의 audience claim = `"hwax-agent"`, sub = `WindowsAgent.id`. 기존 사용자 JWT(`"access"` 타입)와 디코더 단에서 분리한다(아래 §5 참고).
- `rotate_refresh(refresh_token: str) -> RefreshResult` — 회전 동시에 이전 jti 를 `revoked_at` 처리하고 `replaced_by_jti` 를 채운다. 신규 sibling 테이블 `agent_refresh_tokens` 를 사용한다(§5 참고. 기존 `refresh_tokens` 는 `users.id` FK 라서 그대로 못 씀).

### 1.3 신규 서비스 `backend/app/services/agent_manifest_builder.py`

`programs.json`(= `manifest.schema.json` 준수 페이로드) 직렬화 한 점만 담당한다. 데이터 소스는 다음 셋의 교집합이다.

1. `apps` 행 중 `app_type='windows_gui'` 이고 `disabled=False`.
2. `installer_packages` 행 중 `os='windows-x64'` 이고 `app_id` 가 위 (1) 에 속하는 최신 버전(=`uploaded_at` 기준 desc).
3. (옵션) `apps.extra` 에 들어 있는 `windows_install` 블록(있으면 entry/lifecycle 을 보강, 없으면 기본값으로 채움).

결과 JSON 은 그대로 `/api/v1/launcher-agents/manifest` 응답이 된다.

### 1.4 라우터 — 신규 `backend/app/api/v1/agents.py` 확장

기존 파일에 라우트 4개만 추가한다(파일 분리는 Phase 2 의 reorg 때).

- `POST /api/v1/launcher-agents/enroll` — enrollment_token → device JWT 쌍
- `POST /api/v1/launcher-agents/refresh` — refresh 회전
- `GET  /api/v1/launcher-agents/manifest` — 매니페스트
- (Phase 1 은 installs/audit/heartbeat 는 *스텁*만, 본격 로직은 Phase 2)

기존 `/api/v1/agents/heartbeat|poll|log|files|status` 와 경로가 충돌하지 않도록, 신규 라우트는 모두 본 파일의 같은 prefix(`/api/v1`) 아래에 `/agents/enroll` 식으로 inline 선언한다.

### 1.5 라우터 — 신규 `backend/app/api/v1/installers.py` 확장

기존 파일은 `/api/v1/apps` prefix 에 마운트되어 있어 본질적으로 *앱 메타에 딸린 인스톨러 목록* 라우터다. 런처가 쓰는 다운로드 진입점은 **사용자 인증과 무관**하므로 별도 prefix `/api/v1/installers` 로 신규 라우터를 추가한다.

- `GET /api/v1/installers/{id}/download`
  - 보안: `bearerAuth` (audience=`hwax-agent`).
  - 응답: 302 `Location: <presigned URL>`. presigned 발급은 `services/installer_packages.py` 에 위임.
  - 실패 코드는 단순화한다: `404`(없거나 disabled), `401`(토큰 검증 실패), 그 외는 5xx.

### 1.6 Pydantic 스키마 `backend/app/schemas/agent.py`

OpenAPI 와 1:1 로 매칭되는 DTO 만:

```text
EnrollmentIn      { enrollment_token, hostname?, agent_version? }
EnrollmentResult  { agent_id, access_token, refresh_token, expires_in }
RefreshIn         { refresh_token }
RefreshResult     { access_token, refresh_token, expires_in }
ManifestOut       (= contracts/hwax-agent/manifest.schema.json)
```

`ManifestOut` 은 Pydantic 으로 짜되, **테스트는 `jsonschema` 라이브러리로 contracts 의 JSON Schema 와 직접 비교**해서 두 정의가 어긋나지 않도록 가드한다.

### 1.7 테스트

3개로 충분하다. 모두 `backend/app/tests/` 아래에 신규 추가.

- `test_agents_enroll.py` — enrollment → JWT 발급 → 같은 토큰 두 번 사용 시 401 (single-use)
- `test_agents_manifest.py` — `windows_gui` 앱 2개 + 인스톨러 1개 만든 뒤 `GET /manifest` 가 contracts JSON Schema 를 통과하는지, 그리고 인스톨러 없는 앱은 누락되는지
- `test_installers_download.py` — 정상 302 / 잘못된 토큰 401 / 모르는 id 404

### 1.8 파일 변경 요약

스프린트가 끝났을 때 git diff 가 어떻게 보여야 하는지 미리 못박는다.

```text
backend/
├── alembic/versions/0006_windows_agents_device_kind.py   (new)
├── app/api/v1/agents.py        (modified — 3 routes added)
├── app/api/v1/installers.py    (modified — /api/v1/installers/{id}/download added)
├── app/api/v1/router.py        (modified — include 신규 installers prefix)
├── app/schemas/agent.py        (new)
├── app/services/agent_service.py            (new)
├── app/services/agent_manifest_builder.py   (new)
└── app/tests/
    ├── test_agents_enroll.py        (new)
    ├── test_agents_manifest.py      (new)
    └── test_installers_download.py  (new)

contracts/hwax-agent/  (already created in this PR — referenced from tests)
```

기존 파일 중 손대지 않는 것:

- `app/services/agent_registry.py` (서비스 에이전트용, 그대로)
- `app/db/models/windows_agent.py` (컬럼 추가는 alembic 만으로 처리하고 ORM 은 다음 PR 에서 동기화)
- 모든 통합 데모(`integrations/heax-demo-*`)

### 1.9 시퀀스 — 첫 등록과 첫 설치

```text
운영자                          HEAXHub                         HWAXAgent
  │                               │                               │
  │ POST /admin/agents            │                               │
  │   {name, pool, kind=launcher} │                               │
  ├──────────────────────────────►│                               │
  │                               │ INSERT windows_agents          │
  │                               │   auth_token_hash=sha256(t)    │
  │ 200 {agent, token=t}          │                               │
  │◄──────────────────────────────┤                               │
  │                                                                │
  │ (운영자가 t 를 런처 설치 시 입력)                                │
  │                                                                │
  │                               │   POST /api/v1/launcher-agents/enroll  │
  │                               │     {enrollment_token=t}      │
  │                               │◄──────────────────────────────┤
  │                               │ verify_token → mark redeemed   │
  │                               │ issue access(1h)+refresh(30d)  │
  │                               │ 200 {agent_id, tokens}        │
  │                               ├──────────────────────────────►│
  │                                                                │
  │                               │   GET /api/v1/launcher-agents/manifest │
  │                               │     Bearer <access>           │
  │                               │◄──────────────────────────────┤
  │                               │ build_manifest()              │
  │                               │ 200 programs.json             │
  │                               ├──────────────────────────────►│
  │                                                                │
  │                               │   GET /installers/{id}/download
  │                               │◄──────────────────────────────┤
  │                               │ 302 Location: <presigned>     │
  │                               ├──────────────────────────────►│
  │                                                                │
  │                                                                │ install,
  │                                                                │ verify sha256
  │                                                                │ (Phase 2: report 송신)
```

---

## §2. Phase 2 — 데이터 수집 (다음 스프린트)

목표: **운영자가 대시보드에서 “이 에이전트가 살아있고, 무엇을 깔았는지” 를 본다.**

- `POST /api/v1/launcher-agents/installs`
  - 신규 테이블 `install_reports`(id, agent_id, app_id, version, status, started_at, finished_at, sha256_verified, exit_code, error, log_excerpt, previous_version, created_at)
  - JSON 본문은 `install-report.schema.json` 으로 검증.
- `POST /api/v1/launcher-agents/audit`
  - 기존 `audit_log` 테이블을 재사용. **실제 컬럼 구조 주의**: `audit_log` 는 `actor_user_id UUID NULLABLE` 만 존재하고 `actor` 문자열 컬럼은 없다. 런처 이벤트는 `actor_user_id = NULL` 로 두고 `meta JSONB` 안에 `{"agent_id": "<UUID>", "actor": "system:hwax-agent", "kind": <enum>}` 를 넣는다. `target_type='windows_agent'`, `target_id=<agent_id>`, `action=<kind>`.
- `POST /api/v1/launcher-agents/heartbeat`
  - 30분 주기. `WindowsAgent.last_seen=now()`, `agent_version`/`hostname` 업데이트, `modules` 는 `capabilities` 에 머지. 기존 `/api/v1/agents/heartbeat`(폴링형 서비스 에이전트) 와 prefix 분리되어 충돌 없음.
- `jsonschema` 라이브러리(v4.x) 도입. `contracts/hwax-agent/*.schema.json` 을 `Validator(schema).validate(body)` 형태로 라우터 진입점에서 호출.
- 운영자 UI(추가 작업, 본 문서 범위 밖): `/admin/agents/{id}` 에 “설치 이력” 탭.

### 2.1 회귀 영향

- 기존 `/api/v1/agents/heartbeat` 는 폴링형 서비스 에이전트가 쓰던 엔드포인트다. 디바이스 종류로 분기해야 한다.
  - `WindowsAgent.device_kind` 가 `'service'` 또는 `NULL` 이면 기존 경로(폴링 큐 갱신).
  - `'launcher'` 면 신규 경로(`last_seen` + `modules` 머지만).

---

## §3. Phase 3 — 매니페스트 통합 (v2 → v3)

목표: **개별 통합 매니페스트(`integrations/<slug>/.portal/manifest.yaml`) 가 `windows_install` 블록을 정식으로 가질 수 있도록**.

- `schema_version` 을 `2` 에서 `3` 으로 올린다.
- 신규 블록(모두 옵션) 예시는 아래와 같다.

```yaml
windows_install:
  package:
    type: zip      # zip|exe|msi|msix
    url:  https://...
    sha256: <64hex>
  entry:
    executable: bin/Foo.exe
    args_template: ["--user", "{user_id}"]
  requirements: { requires_admin: false }
  lifecycle:    { rollback_on_failure: true }
  ui:           { icon_url, color_accent }
```

- `services/integrations_scanner.py` 에 `_upgrade_v2_to_v3(doc: dict) -> dict` 를 추가. v2 매니페스트는 `windows_install` 이 *없는 채로* 그대로 통과시킨다(현 데모 14개 + 추가 1개 = 15개 무영향).
- `agent_manifest_builder.py` 는 v3 의 `windows_install` 이 있으면 그것을 우선, 없으면 `installer_packages` 테이블에서 채운다.

### 3.1 회귀 방지

회귀 테스트 두 개를 추가한다.

- `test_integrations_scanner_v2_compat.py` — 현 `integrations/heax-demo-*` 매니페스트 15개를 모두 로드해서 예외가 없고, 결과 `schema_version` 이 `3` 으로 정규화되며 `windows_install` 이 빠져 있는지.
- `test_agent_manifest_builder_no_windows_install.py` — `windows_install` 이 없는 앱은 `programs.json` 에서 제외되는지.

---

## §4. Phase 4 — 옵션 (스프린트 확정 전)

- `WebSocket /ws/agent/{agent_id}` — manifest 변경 즉시 푸시. 현재 hub 의 `ws.py` 패턴을 따라 같은 마운트 사이트(`app.include_router(ws_module.router)`) 에 추가.
- 사용자/조직별 allow-list 정책. `permissions.py` 의 정책 엔진 재사용, 단 *매니페스트 빌드 시점* 에 필터링한다(런처 측이 알 필요 없음).
- 자동 업데이트 채널(beta/stable). 매니페스트의 `version` 외에 `channel` 필드 도입은 별도 RFC 가 필요하다.

---

## §5. 권한 모델

런처와 사람-사용자는 **동일한 JWT 인프라**(같은 secret/algorithm)를 쓰되 **audience 로 격리**한다.

- 사용자 access 토큰: payload `type='access'`, audience 미설정(현행 유지).
- 런처 access 토큰: payload `type='access'`, audience `'hwax-agent'`, sub = `WindowsAgent.id`.
- 디코더는 라우터 의존성 단에서 다음을 강제한다.
  - `/api/v1/launcher-agents/(installs|audit|heartbeat|manifest|refresh)` → audience 가 `'hwax-agent'` 가 *아니면* 401.
  - `/api/v1/users/me` 같은 기존 사용자 라우트 → audience 가 `'hwax-agent'` 면 401.
- refresh 토큰: **신규 sibling 테이블 `agent_refresh_tokens` 를 권장**(Phase 1 또는 Phase 2 마이그레이션 0007). 기존 `refresh_tokens` 는 `user_id` 가 `users.id` 로 FK 걸려있으므로, 런처 발급 refresh 토큰(`subject=WindowsAgent.id`)을 거기에 넣으면 FK 위반. 대안으로 `refresh_tokens.subject_kind VARCHAR + user_id NULLABLE` 추가도 가능하지만, 사용자 인증 흐름에 회귀 가능성 있어 sibling 테이블이 안전. 컬럼은 `refresh_tokens` 와 동일(`id/agent_id/jti/issued_at/expires_at/revoked_at/replaced_by_jti/user_agent/ip_address`), `user_id` 대신 `agent_id UUID FK windows_agents.id`. 회전 정책(`replaced_by_jti`)·TTL 30일 동일.
- 감사 로그상 actor 표기:
  - 운영자가 enrollment 토큰을 발급할 때 → `actor=<user_id>`
  - 런처가 audit 이벤트를 올릴 때 → `actor_user_id=NULL`, `meta={"actor":"system:hwax-agent","agent_id":"<UUID>","kind":<enum>}` (위 §2 audit 항목 참조).

### 5.1 토큰 클레임 비교표

| claim     | 사용자 access     | 런처 access         | 런처 refresh         |
|-----------|-------------------|---------------------|----------------------|
| `type`    | `access`          | `access`            | `refresh`            |
| `aud`     | (미설정)          | `hwax-agent`        | `hwax-agent`         |
| `sub`     | `User.id`         | `WindowsAgent.id`   | `WindowsAgent.id`    |
| `jti`     | 미사용            | 미사용              | 필수(회전 추적)      |
| TTL       | 3600 s            | 3600 s              | 2,592,000 s          |
| 갱신 경로 | `/auth/refresh`   | `/agents/refresh`   | (자기 자신을 회전)   |

`aud` 검증은 `decode_token(..., expected_audience='hwax-agent')` 헬퍼 한 줄로 끝낸다. 기존 `decode_token` 의 시그니처에는 keyword-only 인자로 `expected_audience` 만 추가하면 되며, 기존 호출부는 영향 없음.

### 5.2 enrollment 토큰의 수명

- 만료(TTL): 없음(운영자가 명시적으로 무효화하기 전까지 유효).
- 단일 사용(single-use): 한 번 redeem 되면 `windows_agents.auth_token_hash` 를 새로 채우거나 `disabled=True` 로 마감.
- 운영 가이드: 발급 후 24시간 내 사용하지 않은 enrollment 는 운영자가 재발급(=새 row 생성) 으로 다룬다.

---

## §6. 매니페스트 변환 흐름

```text
integrations/<slug>/.portal/manifest.yaml  (schema_version 2 or 3)
              │
              ▼
   integrations_scanner._upgrade_v2_to_v3()   ──► 정규화된 dict
              │
              ├──► App 행 (apps 테이블)
              │       extra.windows_install (v3 의 블록을 그대로 보관)
              │
              ├──► InstallerPackage 행 (installer_packages 테이블)
              │       installer_url / sha256 / size_bytes
              │
              ▼
   agent_manifest_builder.build_manifest(agent: WindowsAgent)
              │
              │   (1) apps WHERE app_type='windows_gui' AND NOT disabled
              │   (2) ⨝ installer_packages WHERE os='windows-x64'
              │   (3) extra.windows_install 우선, 없으면 (2) 로 채움
              │   (4) presigned 발급은 미루고, manifest 의 package.url 은
              │       /api/v1/installers/{id}/download 절대 URL 로 채움
              │
              ▼
   programs.json  ──► HWAXAgent (Tauri 2 + Rust + React launcher)
                            │
                            ▼
                     install / audit / heartbeat
                            │
                            ▼
                  POST /api/v1/agents/*
```

핵심은 hub 가 presigned URL 을 *매니페스트에 넣지 않는다* 는 점이다. 매니페스트 캐시 수명과 presigned 만료가 어긋나 “설치 직전 403” 이 나는 사고를 막기 위함이다. presigned 는 `/installers/{id}/download` 호출 시점에 새로 발급한다.

---

## §7. 회귀 방지 체크리스트

기존 자산을 망가뜨리지 않기 위해 매 PR 마다 다음을 확인한다.

- [ ] `integrations/heax-demo-*` 15개 매니페스트 로드 → 예외 없음.
- [ ] `apps` 테이블의 기존 행 중 `app_type ≠ 'windows_gui'` 인 행은 `programs.json` 에 절대 나타나지 않음.
- [ ] 기존 사용자 JWT(audience 없음)로 `/api/v1/launcher-agents/manifest` 호출 시 401.
- [ ] 런처 JWT(audience=`hwax-agent`)로 `/api/v1/users/me` 호출 시 401.
- [ ] `WindowsAgent.device_kind=NULL` 인 기존 서비스 에이전트가 `/api/v1/agents/heartbeat` 를 호출했을 때 폴링 큐 갱신 경로가 여전히 동작.
- [ ] Alembic `downgrade` 가 깨끗하게 컬럼을 떨어뜨림(테스트 DB 상에서 검증).
- [ ] `contracts/hwax-agent/*.schema.json` 과 `app/schemas/agent.py` 의 필드 목록이 일치(스키마 비교 테스트 1개로 가드).
- [ ] OpenAPI 의 `$ref` 가 실제 파일 경로와 일치(yaml 로딩 단계의 단순 import 테스트).

---

## 부록 A. 운영자 / 런처용 curl 예제

운영자가 enrollment 토큰을 발급:

```bash
curl -X POST https://heaxhub.local/api/v1/admin/agents \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -H "Content-Type: application/json" \
  -d '{"name":"lab-pc-01","pool":"win-launcher","device_kind":"launcher"}'
# → {"agent": {...}, "token": "<enrollment_token>"}
```

런처가 부트 시 enrollment → access/refresh 교환:

```bash
curl -X POST https://heaxhub.local/api/v1/launcher-agents/enroll \
  -H "Content-Type: application/json" \
  -d '{"enrollment_token":"<t>","hostname":"lab-pc-01","agent_version":"0.1.0"}'
# → {"agent_id":"...","access_token":"...","refresh_token":"...","expires_in":3600}
```

매니페스트 조회:

```bash
curl https://heaxhub.local/api/v1/launcher-agents/manifest \
  -H "Authorization: Bearer $ACCESS"
```

인스톨러 다운로드(302 추적):

```bash
curl -L -o pkg.zip \
  https://heaxhub.local/api/v1/installers/<installer_id>/download \
  -H "Authorization: Bearer $ACCESS"
sha256sum pkg.zip   # 매니페스트 package.sha256 과 비교
```

## 부록 B. 계약 파일 목록

본 문서가 가리키는 계약 파일은 모두 `contracts/hwax-agent/` 에 있다.

- `manifest.schema.json` — 런처가 받는 프로그램 카탈로그.
- `install-report.schema.json` — 설치 결과 1건.
- `audit-event.schema.json` — 감사 이벤트 1건.
- `openapi.yaml` — HTTP 표면(`/api/v1/agents/*`, `/api/v1/installers/{id}/download`).
- `tokens.css` — 다크 + 앰버 디자인 토큰.

변경은 SemVer 규칙(`contracts/hwax-agent/README.md` 참고) 을 따른다.
