# HEAXHub 다음 단계 액션 플랜

문서 상태: Draft v1
작성일: 2026-06-05
대상 독자: HEAXHub 백엔드 담당자(본인), 협업하는 풀스택 메인테이너
관련 문서:
- `docs/hwax-agent-backend-plan.md` — 서버측 작업 상세 계획
- `docs/hwax-agent-split-strategy.md` §11 — 분리 전략과 첫 액션
- `docs/hwax-agent-pr-protocol.md` — 양 레포 PR 흐름 규약
- `contracts/hwax-agent/openapi.yaml` — 7개 endpoint 명세 (v0.2.0)
- `contracts/hwax-agent/CHANGELOG.md` — SemVer 변경 이력

---

## §0. 한 줄 현황

■ 완료된 것
- HWAXAgent 통합 계약 (`contracts/hwax-agent/`, v0.2.0) 푸시 완료. JSON Schema 3종 + OpenAPI + tokens.css + CHANGELOG.
- 서버측 상세 작업 계획서 (`docs/hwax-agent-backend-plan.md`) 푸시 완료.
- PR 협업 규약 (`docs/hwax-agent-pr-protocol.md`) 정의 완료.
- 라이브 서비스(통합 데모 15개) 정상 동작 중.

▶ 다음 (이번 스프린트, 1~2주)
- backend 측 0006 마이그레이션, `agent_service.py`, `agent_manifest_builder.py`, `/api/v1/launcher-agents/*` 라우터, `/api/v1/installers/{id}/download` 추가.

◇ blocker 없음
- HWAXAgent 측은 별도 윈도우 PC 에서 Tauri 2 부트스트랩 진행 중. 본 레포의 작업은 그쪽 진행과 독립적으로 머지 가능 (계약이 양 레포의 동기점).

---

## §1. 우선순위 매트릭스

| # | 작업 | 의존성 | 추정 공수 | 우선순위 | 담당 |
|---|---|---|---|---|---|
| 2.1 | Alembic 0006 + ORM `device_kind` 동기화 | — | 0.5d | **P0** | 백엔드 |
| 2.2 | `agent_service.py` + alembic 0007 (`agent_refresh_tokens`) | 2.1 | 1d | **P0** | 백엔드 |
| 2.3 | `agent_manifest_builder.py` | 2.1 | 1d | **P0** | 백엔드 |
| 2.4 | `launcher_agents.py` 라우터 (3 실구현 + 3 stub) | 2.2, 2.3 | 1d | **P0** | 백엔드 |
| 2.5 | `/api/v1/installers/{id}/download` | 2.2 | 0.5d | **P0** | 백엔드 |
| 2.6 | contracts 정식 채택 + 첫 git tag | 2.1~2.5 머지 | 0.25d | **P0** | 메인테이너 |
| 3.1 | installs / audit / heartbeat 실구현 (alembic 0008) | 2.4 | 2d | P1 | 백엔드 |
| 3.2 | 매니페스트 schema v2 → v3 마이그레이션 경로 | 3.1 | 1d | P1 | 백엔드 |
| 3.3 | 운영자 대시보드 "설치 이력" 탭 | 3.1 | 1d | P1 | 풀스택 |
| 4.x | WebSocket push + 사용자/그룹 정책 | 3.x | TBD | P2 | TBD |
| 6.x | 운영 정리 (var/ 디스크, docs/archive/, GC 잡) | — | 병행 | P1 | 서브에이전트 |

합계 P0: **6건**, 총 추정 약 4.25d.

---

## §2. HEAXHub 측 즉시 착수 작업 (이번 스프린트)

각 절은 독립 PR 한 건에 대응. PR 제목 prefix 는 `[hwax-agent]` 권장 (`docs/hwax-agent-pr-protocol.md` §3 참고).

> **상태 (2026-06-08): §2.1 ~ §2.6 모두 DONE.** 한 commit 으로 통합 머지됨.
> PR #2 G1 (Tauri updater feed `/api/v1/installers/{app_id}/latest`) 도 같이 구현.
> Live smoke: enrollment → JWT → manifest(ETag/304) → refresh rotation → heartbeat(204) →
> installs/audit(202 stub) → installer download(404 no row) → updater feed(204 no pkg) 13/13 PASS.
> 15 데모 regression 0 건. Contracts `v0.3.0` MINOR 릴리스 표지로 묶음.
>
> **PR #3 (`docs/hwax-agent-server-fixes-and-portal-install.md`) 대응 상태**:
> F1 (launcher-agents P0) ✅, F2 (경로 불일치 — `/api/v1/installers/{id}/download` 신규
> 라우트로 해소) ✅, F3 (updater feed Tauri JSON `/api/v1/installers/{app_id}/latest`) ✅,
> F5 (installer publish — 기존 `/api/v1/apps/{app_id}/installers` 재사용) ✅.
> 남은 것: F4 (WS push, Phase 4), §2 포털-에서-런처-설치 SPA 설계, Q1~Q5 의사결정.

### §2.1 Alembic 0006 + ORM `device_kind` 동기화 (P0, 0.5d)

#### 변경 대상
- `backend/alembic/versions/0006_windows_agents_device_kind.py` **(신규)**
- `backend/app/db/models/windows_agent.py` **(수정)**
- `backend/app/schemas/agent.py` **(신규)** — `AgentRegisterIn` 정의 (현재는 `agents.py` 안 inline)
- `backend/app/api/v1/agents.py` **(수정)** — admin 등록 핸들러가 `device_kind` 전달
- `backend/app/tests/test_admin_agent_register_with_device_kind.py` **(신규)**

#### 작업 내용
1. Alembic 신규 리비전 생성. `down_revision='0005_submission_source_config'`.
   ```sql
   -- upgrade
   ALTER TABLE windows_agents ADD COLUMN IF NOT EXISTS device_kind VARCHAR(16);
   -- downgrade
   ALTER TABLE windows_agents DROP COLUMN IF EXISTS device_kind;
   ```
2. ORM 매핑:
   ```python
   # backend/app/db/models/windows_agent.py
   device_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
   ```
3. Pydantic 스키마 `AgentRegisterIn` 에 `device_kind: Literal["launcher","service"] | None = None` 추가. 같은 PR 에서 처리하지 않으면 Pydantic 단계에서 조용히 드롭된다 (`docs/hwax-agent-backend-plan.md` §1.1 경고).
4. admin 라우터 (`/api/v1/admin/agents` POST) 핸들러에서 `WindowsAgent(..., device_kind=payload.device_kind)`.

#### 테스트
- `test_admin_agent_register_with_device_kind.py` (신규):
  - `device_kind="launcher"` 로 POST → 응답 200, DB 행 `device_kind == "launcher"`.
  - `device_kind` 미지정 POST → DB 행 `device_kind IS NULL` (기존 흐름 호환).
  - 잘못된 값 (`"foo"`) → 422.
- 회귀 점검: `test_agents_*.py` (기존) 가 그대로 통과해야 함.

#### 마이그레이션 검증
```bash
# heaxhub-backend 컨테이너 안에서
.venv/bin/alembic upgrade head
.venv/bin/alembic downgrade -1
.venv/bin/alembic upgrade head
```
양방향이 깨끗하게 떨어져야 머지. 기존 행이 NULL 로 남는지도 SELECT 한 번으로 확인.

---

### §2.2 신규 서비스 `agent_service.py` (P0, 1d)

`agent_registry.py` 와 명확히 분리. `agent_registry.py` 는 **폴링형 서비스 에이전트** (기존 `/api/v1/agents/heartbeat`) 그대로 유지. 신규 `agent_service.py` 는 **JWT 기반 런처** 전용.

#### 변경 대상
- `backend/app/services/agent_service.py` **(신규)**
- `backend/app/db/models/agent_refresh_token.py` **(신규)**
- `backend/alembic/versions/0007_agent_refresh_tokens.py` **(신규)**
- `backend/app/core/security.py` **(수정)** — `decode_token(..., expected_audience: str | None = None)` keyword-only 인자 추가
- `backend/app/tests/test_agent_enroll.py` **(신규)**
- `backend/app/tests/test_agent_refresh.py` **(신규)**

#### 공개 함수 (최소)
```python
def issue_enrollment_token(operator: User, *, name: str, pool: str, device_kind: str = "launcher") -> tuple[WindowsAgent, str]: ...
def redeem_enrollment_token(db, token: str, *, hostname: str | None, agent_version: str | None) -> EnrollmentResult: ...
def rotate_refresh(db, refresh_token: str) -> RefreshResult: ...
def verify_agent_jwt(db, access_token: str) -> WindowsAgent: ...  # audience="hwax-agent" 강제
```

#### 토큰 클레임 정책 (`docs/hwax-agent-backend-plan.md` §5.1)
| claim | 사용자 access | 런처 access | 런처 refresh |
|---|---|---|---|
| `aud` | (미설정) | `hwax-agent` | `hwax-agent` |
| `sub` | `User.id` | `WindowsAgent.id` | `WindowsAgent.id` |
| `jti` | 미사용 | 미사용 | 필수 (회전 추적) |
| TTL | 3600s | 3600s | 2,592,000s (30d) |

#### Alembic 0007 — sibling 테이블
```sql
CREATE TABLE agent_refresh_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID NOT NULL REFERENCES windows_agents(id) ON DELETE CASCADE,
  jti UUID NOT NULL UNIQUE,
  issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ NULL,
  replaced_by_jti UUID NULL,
  user_agent VARCHAR(256) NULL,
  ip_address INET NULL
);
CREATE INDEX ix_agent_refresh_tokens_agent ON agent_refresh_tokens(agent_id);
```
기존 `refresh_tokens` 테이블은 `users.id` FK 라서 그대로 못 씀. sibling 테이블이 사용자 인증 회귀를 막는 가장 안전한 선택지 (`hwax-agent-backend-plan.md` §5).

#### 테스트
- `test_agent_enroll.py`: 1회용 토큰 redeem 성공 → 같은 토큰 두 번째 호출 시 401.
- `test_agent_refresh.py`: 회전 직후 새 refresh 정상, 이전 refresh 의 `revoked_at` 채워졌는지, 이전 refresh 로 회전 재시도 시 401.

---

### §2.3 신규 서비스 `agent_manifest_builder.py` (P0, 1d)

#### 변경 대상
- `backend/app/services/agent_manifest_builder.py` **(신규)**
- `backend/app/tests/test_agent_manifest_builder.py` **(신규)**

#### 데이터 소스 (교집합)
1. `apps` WHERE `app_type='windows_gui'` AND `disabled=False`.
2. `installer_packages` WHERE `os='windows-x64'` AND `app_id ∈ (1)`. 최신 버전 (`uploaded_at` desc).
3. `App.extra.windows_install` 블록이 있으면 entry/lifecycle/ui 보강. 없으면 기본값.

`installer_packages.installer_url` (`backend/app/db/models/installer_package.py:28`) 컬럼명 주의.

#### 캐시
- 30s 메모이즈 (`functools.lru_cache` + 만료 timestamp 변수). 매니페스트 빈도는 낮으나 manifest 호출 1회당 DB hit 2회를 피하기 위함. 캐시 키는 *없음* (모든 런처가 동일 매니페스트를 받음 — 정책 분기는 Phase 4).

#### Programs URL 채우는 방식
매니페스트 안 `package.url` 은 **presigned URL 이 아니라** `/api/v1/installers/{id}/download` 절대 URL 로. 이유는 `hwax-agent-backend-plan.md` §6 — 매니페스트 캐시 수명과 presigned 만료가 어긋나 "설치 직전 403" 사고가 나기 때문.

#### 테스트
- `test_agent_manifest_builder.py`:
  - windows_gui 앱 2개 (인스톨러 있음/없음) → 인스톨러 없는 앱은 매니페스트에서 빠짐.
  - `disabled=True` 앱 → 매니페스트에서 빠짐.
  - 결과 JSON 이 `contracts/hwax-agent/manifest.schema.json` 통과 (`jsonschema.validate`).

---

### §2.4 신규 라우터 `launcher_agents.py` (P0, 1d)

#### 변경 대상
- `backend/app/api/v1/launcher_agents.py` **(신규)** — 기존 `agents.py` 와 분리, 혼선 방지
- `backend/app/api/v1/router.py` **(수정)** — include 신규 라우터, prefix `/api/v1/launcher-agents`
- `backend/app/tests/test_launcher_agents_enroll.py`, `..._refresh.py`, `..._manifest.py` **(신규 3개)**

#### Endpoint 매트릭스 (Phase 1 범위)
| Method | Path | Phase 1 동작 | 참조 OpenAPI |
|---|---|---|---|
| POST | `/api/v1/launcher-agents/enroll` | **실구현** (agent_service.redeem) | openapi §enroll |
| POST | `/api/v1/launcher-agents/refresh` | **실구현** (agent_service.rotate) | openapi §refresh |
| GET  | `/api/v1/launcher-agents/manifest` | **실구현** (manifest_builder) | openapi §manifest |
| POST | `/api/v1/launcher-agents/installs` | 501 stub | openapi §installs |
| POST | `/api/v1/launcher-agents/audit` | 501 stub | openapi §audit |
| POST | `/api/v1/launcher-agents/heartbeat` | 501 stub | openapi §heartbeat |

#### 의존성 주입
```python
def get_launcher_agent(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> WindowsAgent:
    # audience="hwax-agent" 강제, 기존 사용자 JWT 거부
    ...
LauncherAuth = Annotated[WindowsAgent, Depends(get_launcher_agent)]
```
`agents.py` 의 `get_agent_from_token` (`agent_registry.verify_token`) 과 **분리**된 별도 dep. 기존 서비스 에이전트는 그대로 폴링 토큰을 쓰고, 런처는 JWT 를 쓴다.

#### 테스트
- `test_launcher_agents_enroll.py`: enrollment_token → access/refresh 쌍 발급.
- `test_launcher_agents_refresh.py`: 회전 정상.
- `test_launcher_agents_manifest.py`:
  - 정상: contracts schema 통과.
  - 사용자 JWT (audience 없음) 로 호출 → 401 (`hwax-agent-backend-plan.md` §7 회귀 체크리스트).
  - 런처 JWT 로 `/api/v1/users/me` 호출 → 401 (반대 방향 가드).

---

### §2.5 Installer download endpoint (P0, 0.5d)

#### 변경 대상
- `backend/app/api/v1/installers.py` **(수정 또는 신규 분리)** — 현재는 `/api/v1/apps` 하위에 mount 되어 있음. 신규 라우트는 **별도 prefix `/api/v1/installers`** 로 마운트 (사용자 인증과 무관, 런처 JWT 만 받음).
- `backend/app/api/v1/router.py` **(수정)** — 신규 prefix include.
- `backend/app/tests/test_installers_download.py` **(신규)**

#### 라우트 동작
- `GET /api/v1/installers/{id}/download` (보안: bearer aud=`hwax-agent`)
  - `InstallerPackage` 조회. 없거나 disabled → 404.
  - `installer_url` 이 절대 URL 이면 302 redirect.
  - 사내 object storage 가 presigned 미지원이면 stream fallback (`StreamingResponse`). 우선은 절대 URL 케이스만 구현하고, fallback 은 Phase 2 로 미뤄도 됨 (현재 InstallerPackage 운용 예가 절대 URL 위주라면).
  - 토큰 audience 가 `hwax-agent` 가 아니면 401.

#### 테스트
- `test_installers_download.py`:
  - 정상 런처 JWT → 302, Location 헤더에 `installer_url`.
  - 사용자 JWT → 401.
  - 없는 id → 404.

---

### §2.6 contracts 정식 채택 + 첫 git tag (P0, 0.25d)

마지막 머지 후 단발성 작업.

#### 체크리스트
- [ ] `.github/workflows/contracts-validate.yml` (또는 동등) CI 가 schema/openapi 둘 다 통과.
- [ ] `contracts/hwax-agent/CHANGELOG.md` v0.2.0 row 의 날짜·BREAKING 표기 최종 확인 (현재 `2026-06-05`).
- [ ] `git tag contracts-hwax-agent-v0.2.0 -m "..."` 별도 태그 네임스페이스 (앱 자체 릴리스 태그와 섞이지 않도록).
- [ ] HWAXAgent 측에 알림: tag 명, contracts diff 요약, 호환성 노트.

---

## §3. Phase 2 (다음 스프린트, 2주 후 ~)

### §3.1 installs / audit / heartbeat 본 구현

- `POST /api/v1/launcher-agents/installs`
  - 신규 테이블 `install_reports(id, agent_id, app_id, version, status, started_at, finished_at, sha256_verified, exit_code, error, log_excerpt, previous_version, created_at)` — alembic **0008**.
  - 요청 본문은 `contracts/hwax-agent/install-report.schema.json` 으로 jsonschema 검증.
- `POST /api/v1/launcher-agents/audit`
  - 기존 `audit_log` 테이블 재사용. **컬럼 주의**: `actor_user_id` 만 있고 `actor` 문자열 컬럼 없음. 런처 이벤트는 `actor_user_id=NULL`, `meta={"agent_id":<uuid>,"actor":"system:hwax-agent","kind":<enum>}`, `target_type='windows_agent'`, `target_id=<agent_id>`.
- `POST /api/v1/launcher-agents/heartbeat`
  - 30분 주기. `WindowsAgent.last_seen=now()`, `capabilities` 머지.
  - 기존 `/api/v1/agents/heartbeat` 와 prefix 분리로 충돌 없음. 단 운영자 대시보드가 두 종류 heartbeat 를 모두 통계에 반영해야 한다면 별도 작업.
- `jsonschema` 라이브러리 (v4.x) 의존성 추가, `pyproject.toml` 갱신.

### §3.2 매니페스트 스키마 v2 → v3 마이그레이션 경로

- `integrations/<slug>/.portal/manifest.yaml` 에 `schema_version: 3` 옵션 추가. v2 는 그대로 통과시킴.
- `backend/app/services/integrations_scanner.py` 에 `_upgrade_v2_to_v3(doc) -> dict` 추가.
- v3 의 `windows_install` 블록 (`package/entry/requirements/lifecycle/ui`) 을 `App.extra.windows_install` 에 보관.
- `agent_manifest_builder.py` 가 v3 의 `windows_install` 을 우선, 없으면 `installer_packages` 테이블로 폴백.

#### 회귀 무영향 검증 (필수)
- `test_integrations_scanner_v2_compat.py` — 현 데모 15개 매니페스트 (`integrations/heax-demo-*`) 가 예외 없이 로드, `schema_version` 이 3 으로 정규화, `windows_install` 미존재.
- `test_agent_manifest_builder_no_windows_install.py` — `windows_install` 없는 앱이 매니페스트에서 빠지는지.

### §3.3 운영자 대시보드 "설치 이력" 탭

- `/admin/agents/{id}` 페이지 (frontend) 에 탭 추가, `install_reports` 표시.
- 백엔드 API: `GET /api/v1/admin/agents/{id}/installs` (페이지네이션, 필터: status, app_id).
- frontend 추가 작업이므로 풀스택 담당.

---

## §4. Phase 3 (옵션, 1개월 후)

| 작업 | 메모 |
|---|---|
| WebSocket `/ws/agent/{agent_id}` | manifest 변경 즉시 푸시. 현재 `backend/app/api/v1/ws.py` 패턴 재사용. |
| 사용자/그룹별 allow/deny 매니페스트 | `permissions.py` 정책 엔진 재사용. **매니페스트 빌드 시점**에 필터링 (런처는 자기 권한을 알 필요 없음). |
| 자동 업데이트 채널 (beta/stable) | 매니페스트에 `channel` 필드 도입 — 별도 RFC 필요. |
| mTLS 옵션 | `contracts/SECURITY.md` 신규 추가 시점. |

---

## §5. HWAXAgent 측 진행 상태 추적

별도 GitHub 레포 (**`squall321/HWAXLauncher`**) 에서 개발. **현황(2026-06-08): 빌드·패키징·
실행검증 완료** — Tauri 2 + Rust + React, 코어 40 테스트 통과, NSIS 설치파일 생성 + 트레이 기동 실행
검증 완료. **서버측 P0 (§2.1 ~ §2.6) 모두 완료**되어 실서버 페어링/설치 1사이클 차단 해제. 갭 3건
(`docs/hwax-agent-client-status-and-server-gaps.md`) 중 **G1 (updater feed `/latest`) 완료**, G2
(WS 푸시 메시지 스키마) + G3 (installer publish) 는 Phase 2/4 로 미룸.

### 본 레포가 받는 신호
- GitHub Issue 라벨: `hwax-agent`, `needs-heaxhub-change`, `contracts`.
- PR from HWAXAgent maintainer fork into `contracts/hwax-agent/` (PR 협업 규약 §2 의 시나리오 A/B).
- HWAXAgent 측 release 태그 발표 시 contracts 호환 버전 명시되는지 확인.

### 양쪽 동기화 체크리스트 (주 1회, 매주 월요일 권장)
- [ ] `contracts/hwax-agent/CHANGELOG.md` 두 레포에서 동일 최신 entry 인지.
- [ ] HWAXAgent 의 `Cargo.toml` (또는 schemas 동기화 스크립트) 가 contracts SemVer 와 호환되는지.
- [ ] HWAXAgent 측 issue 중 `needs-heaxhub-change` 라벨 미처리 건 점검.
- [ ] HEAXHub backend release 로그에 contracts 의존성 변동 사항이 있으면 HWAXAgent 측에 ping.

### 상호 PR 충돌 회피
- contracts 변경 PR 은 양 레포 동시 머지 정책 (PR 협업 규약 §6 권장 — 현재 명문화 미흡, §7 리스크 항목 참고).

---

## §6. 운영 정리 작업 (병행)

본 스프린트와 무관하게 진행 중인 정리 작업들.

| 작업 | 진행 주체 | 상태 |
|---|---|---|
| `var/` 디스크 정리 | 별도 서브에이전트 | 진행 중 |
| `docs/archive/` 재정렬 | 별도 서브에이전트 | 진행 중 |
| `backend/celerybeat-schedule` git ignore 확인 | 백엔드 | 점검 필요 (현재 modified 상태로 노출됨) |
| 정기 GC (월 1회): SIF 압축, `job_storage` 오래된 파일 압축, 만료된 enrollment_token row 정리 | celery beat | Phase 2 에서 자동화 |

### celerybeat-schedule 즉시 조치
`git status` 에 `M backend/celerybeat-schedule` 가 계속 떠 있음. `.gitignore` 에 라인 추가 + 트래킹 제거를 별도 1줄 PR 로 처리. 별 일 아니지만 매 PR 마다 노이즈가 됨.

---

## §7. 기술 부채 / 리스크

| # | 항목 | 영향 | 대응 |
|---|---|---|---|
| 1 | `agent_registry.py` 가 기존 폴링형과 신규 launcher 둘 다 다룬다는 오해 여지 | 향후 모듈 혼란 | §2.2 에서 `agent_service.py` 신설로 명확히 분리. `agent_registry.py` 는 폴링형 전용 주석 강화. |
| 2 | `audit_log` 테이블 PK 타입 (BigInt? UUID?) 미확인 | launcher audit 폭증 시 partitioning 결정 못 함 | Phase 2 진입 전 `backend/app/db/models/audit_log.py` 확인 → 필요 시 0008 마이그레이션에 partitioning 도입. |
| 3 | contracts SemVer breaking 시 양 레포 동시 머지 정책 미명문화 | breaking PR 머지 후 HWAXAgent 측 빌드 깨질 위험 | `docs/hwax-agent-pr-protocol.md` §6 에 "breaking 변경은 contracts 태그 + HWAXAgent main 머지가 동시 또는 24h 이내" 명문화 권장. |
| 4 | `installer_packages.installer_url` 이 절대 URL 가정 | 사내 object storage 가 presigned 만 지원할 경우 fallback 필요 | §2.5 에 stream fallback 적기. 운영 환경 확인 후 Phase 2 에 정식 구현. |
| 5 | enrollment_token 만료 정책 없음 (단일 사용만 보장) | 발급 후 방치 토큰 누적 | Phase 2 의 GC 잡에 "발급 후 30일 미사용 enrollment 자동 disable" 추가. |
| 6 | 매니페스트 30s 캐시가 멀티 워커 환경에서 instance-local | 런처가 받는 매니페스트가 워커마다 다를 수 있음 | 영향 미미 (30s 윈도우). 신경 쓰이면 Redis 캐시로 승격 — Phase 3. |

---

## §8. 한 줄 요약 + 첫 PR 후보

### 첫 PR (이번 주 안에 머지 목표)
**"0006 마이그레이션 + ORM `device_kind` + `AgentRegisterIn` 보강 (§2.1, P0, 0.5d)"**
- 단일 책임, 회귀 위험 최소, 후속 PR 의 전제. 가장 먼저 머지.

### 두 번째 PR
**"`agent_service.py` + 0007 `agent_refresh_tokens` 테이블 + audience aware `decode_token` (§2.2, P0, 1d)"**
- JWT 발급/회전 인프라. 라우터 추가의 전제.

### 세 번째 PR
**"`launcher_agents.py` 라우터 + `agent_manifest_builder.py` + `/api/v1/installers/{id}/download` (§2.3 + §2.4 + §2.5, P0, 2.5d)"**
- 엔드포인트 3개 실구현 + 3개 stub + 인스톨러 다운로드. 한 PR 로 묶어도 무방 (의존성이 강함). 분할이 필요하면 2.3/2.4 를 한 PR, 2.5 를 별도 PR.

### 마무리 PR
**"contracts v0.2.0 정식 태깅 + CHANGELOG 확인 (§2.6, P0, 0.25d)"**
- 별도 git tag `contracts-hwax-agent-v0.2.0` + HWAXAgent 측 통보.

### 첫 스프린트 종료 시 git diff 의 모습
```text
backend/
├── alembic/versions/
│   ├── 0006_windows_agents_device_kind.py        (new)
│   └── 0007_agent_refresh_tokens.py              (new)
├── app/
│   ├── api/v1/
│   │   ├── agents.py                             (modified — AgentRegisterIn device_kind)
│   │   ├── installers.py                         (modified — download route)
│   │   ├── launcher_agents.py                    (new)
│   │   └── router.py                             (modified — include 2 new prefixes)
│   ├── core/security.py                          (modified — expected_audience kwarg)
│   ├── db/models/
│   │   ├── agent_refresh_token.py                (new)
│   │   └── windows_agent.py                      (modified — device_kind)
│   ├── schemas/agent.py                          (new)
│   ├── services/
│   │   ├── agent_service.py                      (new)
│   │   └── agent_manifest_builder.py             (new)
│   └── tests/
│       ├── test_admin_agent_register_with_device_kind.py  (new)
│       ├── test_agent_enroll.py                  (new)
│       ├── test_agent_refresh.py                 (new)
│       ├── test_agent_manifest_builder.py        (new)
│       ├── test_launcher_agents_enroll.py        (new)
│       ├── test_launcher_agents_refresh.py       (new)
│       ├── test_launcher_agents_manifest.py      (new)
│       └── test_installers_download.py           (new)
```

신규 11개 파일, 수정 6개 파일, 테스트 8개. 합계 약 P0 4.25d.

---

## 부록 A — 매 PR 회귀 점검 체크리스트

`docs/hwax-agent-backend-plan.md` §7 에서 그대로 가져옴. 매 PR 머지 직전 마지막 점검.

- [ ] `integrations/heax-demo-*` 15개 매니페스트 로드 → 예외 없음.
- [ ] `apps` 테이블의 기존 행 중 `app_type ≠ 'windows_gui'` 인 행은 `programs.json` 에 절대 나타나지 않음.
- [ ] 기존 사용자 JWT (audience 없음) 로 `/api/v1/launcher-agents/manifest` 호출 시 401.
- [ ] 런처 JWT (audience=`hwax-agent`) 로 `/api/v1/users/me` 호출 시 401.
- [ ] `WindowsAgent.device_kind=NULL` 인 기존 서비스 에이전트의 `/api/v1/agents/heartbeat` 폴링 큐 갱신 경로가 여전히 동작.
- [ ] Alembic `downgrade` 가 깨끗하게 컬럼/테이블을 떨어뜨림 (테스트 DB 상에서 검증).
- [ ] `contracts/hwax-agent/*.schema.json` 과 `app/schemas/agent.py` 의 필드 목록이 일치.
- [ ] OpenAPI 의 `$ref` 가 실제 파일 경로와 일치 (yaml 로딩 단계의 단순 import 테스트).

---

## 부록 B — 본 문서가 인용하는 실재 파일

(2026-06-05 기준 확인 완료)

- `backend/app/db/models/windows_agent.py` — `windows_agents` 테이블 ORM
- `backend/app/db/models/installer_package.py` — `installer_packages` ORM, URL 컬럼명 `installer_url`
- `backend/app/db/models/app.py` — `AppType.WINDOWS_GUI`, `ExecutionTarget.LOCAL_PC`
- `backend/app/db/models/audit_log.py` — `audit_log` 테이블 (컬럼 구조 §3.1 에서 재확인)
- `backend/app/api/v1/agents.py` — 기존 agents 라우터 (heartbeat/poll/log/files/status) + admin
- `backend/app/api/v1/installers.py` — 현재 `/api/v1/apps` 하위 mount (§2.5 에서 별도 prefix 분리)
- `backend/app/api/v1/router.py` — 라우터 mount 지점
- `backend/app/services/agent_registry.py` — 기존 폴링형 토큰 발급/검증
- `backend/app/core/security.py` — `create_access_token`, `decode_token`
- `backend/alembic/versions/0005_submission_source_config.py` — 가장 최근 마이그레이션 (down_revision 의 출발점)
- `contracts/hwax-agent/openapi.yaml` — v0.2.0 명세
- `contracts/hwax-agent/manifest.schema.json` — manifest builder 검증 대상
- `contracts/hwax-agent/install-report.schema.json` — Phase 2 installs 입력 검증
- `contracts/hwax-agent/audit-event.schema.json` — Phase 2 audit 입력 검증
- `contracts/hwax-agent/CHANGELOG.md` — SemVer 이력
- `docs/hwax-agent-backend-plan.md` — 서버측 상세 계획 (본 문서의 모(母)문서)
- `docs/hwax-agent-split-strategy.md` — 분리 전략 (본 문서 §5 의 근거)
- `docs/hwax-agent-pr-protocol.md` — PR 흐름 규약 (§7 리스크 3 의 강화 대상)
