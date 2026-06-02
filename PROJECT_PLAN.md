# HEAXHub — AI 자동화 통합 포탈 개발 계획서

**작성일**: 2026-05-26
**작성자**: CAE Automation Part
**문서 버전**: v1.0

---

## 0. 문서 개요

본 문서는 사내 흩어진 자동화 프로그램을 한 곳에서 검색·실행·관리할 수 있는 통합 포탈 **HEAXHub** 의 개발 계획서이다. 이미 합의된 운영 표준안([ai_automation_portal_standard.html](ai_automation_portal_standard.html))을 기반으로 실제 시스템을 구현하기 위한 아키텍처, 디렉터리 구조, 데이터 모델, 자동화 파이프라인, 화면 명세, 단계별 일정을 정의한다.

### 0.1 핵심 설계 결정 (사전 합의)

| 항목 | 결정 |
|---|---|
| 등록 흐름 | 신청 → 운영자 승인 → 빌드 → 공개 |
| 격리 환경 | 파이썬은 venv, 그 외는 Apptainer (SIF) |
| 인증 (1단계) | **포탈 자체 회원가입** — 이름·조직(그룹/랩)·이메일·비밀번호 |
| 인증 (2단계) | 사내 SSO (OIDC) 연동, 이메일로 기존 계정 매핑 |
| 프론트엔드 | React 18 + TypeScript + Vite + shadcn/ui + Tailwind |
| 백엔드 | FastAPI (Python 3.11+) |
| DB | PostgreSQL 16 |
| 큐 | Celery + Redis |
| 코드 보관 | 사내 GitHub Enterprise 또는 GitLab |
| 빌드/실행 자동화 | 본 프로젝트 내장 (`app_workspaces/` 폴더 기반) |

### 0.2 문서가 다루지 않는 것

- 인프라 프로비저닝 상세 (별도 `INFRA_PLAN.md` 예정)
- 보안 감사 항목 상세 (별도 `SECURITY_REVIEW.md` 예정)
- 운영 매뉴얼 (Phase 9 이후 산출)

---

## 1. 시스템 컨텍스트

### 1.1 행위자 (Actors)

```
┌─ 일반 사용자 (User)          → 등록된 앱을 검색·실행, 결과 다운로드
├─ 앱 신청자 (Submitter)       → GitHub 주소로 새 앱 등록 신청
├─ 앱 개발자 (Owner)           → Upstream Repo 관리, 버전 발표
├─ 포탈 운영자 (Admin)         → 신청 검토, manifest 보완, 승인/공개
└─ 인프라 관리자 (Infra)       → 서버, 큐, 스토리지 운영
```

### 1.2 외부 시스템

```
┌─ 사내 SSO (LDAP / OIDC)
├─ 사내 GitHub / GitLab        → Upstream Repos
├─ Slurm 클러스터              → 대규모 계산 작업
├─ Windows Worker Agents       → 윈도우 EXE 대리 실행
├─ NAS / MinIO                 → 파일 저장
└─ 사내 Apptainer Registry      → SIF 이미지 보관 (선택)
```

### 1.3 컨텍스트 다이어그램

```
                   [SSO IdP]
                       │
                       ▼
[브라우저] ── HTTPS ──▶ [HEAXHub Portal]
                       │   ├─ React Frontend (SPA)
                       │   ├─ FastAPI Backend
                       │   ├─ Worker (Celery)
                       │   ├─ PostgreSQL
                       │   └─ Redis
                       │
                       ├──▶ [GitHub] (clone, webhook 수신)
                       ├──▶ [File Storage] (NAS / MinIO)
                       ├──▶ [Slurm Cluster]
                       └──▶ [Windows Worker Agents]
```

---

## 2. 아키텍처 개요

### 2.1 컴포넌트 계층

```
┌────────────────────────────────────────────────────────────────┐
│ Presentation                                                   │
│   React 18 + TypeScript + Vite + shadcn/ui + Tailwind          │
│   Routing: TanStack Router  /  State: Zustand + TanStack Query │
└────────────────────────────────────────────────────────────────┘
                            │  REST + WebSocket (실시간 로그)
                            ▼
┌────────────────────────────────────────────────────────────────┐
│ API Gateway                                                    │
│   FastAPI · Pydantic v2 · OAuth2/OIDC middleware               │
│   /api/v1/* + /ws/jobs/{id}/logs                               │
└────────────────────────────────────────────────────────────────┘
            │                  │                    │
            ▼                  ▼                    ▼
┌──────────────────┐  ┌─────────────────┐  ┌────────────────────┐
│ Domain Services  │  │ Job Orchestrator │  │ App Lifecycle      │
│ - users          │  │ - submit         │  │ - intake (신청)    │
│ - apps           │  │ - dispatch       │  │ - workspace 생성   │
│ - permissions    │  │ - track          │  │ - clone / sync     │
│ - submissions    │  │ - cancel         │  │ - build (venv/SIF) │
│ - audit          │  │                  │  │ - publish          │
└──────────────────┘  └─────────────────┘  └────────────────────┘
            │                  │                    │
            └────────┬─────────┴────────┬───────────┘
                     ▼                  ▼
            ┌──────────────────┐  ┌─────────────────────┐
            │ Celery Workers   │  │ Runners (executors) │
            │ - build_worker   │  │ - LocalRunner       │
            │ - sync_worker    │  │ - SlurmRunner       │
            │ - publish_worker │  │ - ApptainerRunner   │
            └──────────────────┘  │ - WindowsAgentClient│
                                  │ - ExternalLinkRunner│
                                  └─────────────────────┘
                     │                  │
                     ▼                  ▼
            ┌──────────────────────────────────────┐
            │ Storage Layer                        │
            │ - PostgreSQL (메타데이터)            │
            │ - Redis (큐, 캐시, 세션)             │
            │ - NAS/MinIO (job 입출력 파일)        │
            │ - app_workspaces/ (소스코드 + venv)  │
            └──────────────────────────────────────┘
```

### 2.2 단방향 데이터 흐름 원칙

- 프론트엔드는 **GET/POST**만 사용, 상태는 백엔드를 단일 소스로 사용 (`TanStack Query` 캐시 동기화)
- 도메인 서비스는 **DB → 비즈니스 로직 → 응답** 의 단방향
- 비동기 작업은 **API → Celery 큐 → Worker → DB 갱신 → WebSocket 푸시**
- 실행 결과 파일은 **Job ID 디렉터리에 격리**, DB에는 경로만 보관

---

## 3. 프로젝트 디렉터리 구조

루트는 모노레포 형태. 프론트엔드·백엔드·앱 워크스페이스가 한 곳에 있어 개발·배포 일관성을 확보한다.

```
HEAXHub/
├─ frontend/                          # React + Vite SPA
│   ├─ src/
│   │   ├─ main.tsx
│   │   ├─ App.tsx
│   │   ├─ routes/                    # TanStack Router 파일 라우팅
│   │   │   ├─ __root.tsx
│   │   │   ├─ index.tsx              # 메인 포탈
│   │   │   ├─ apps/
│   │   │   │   ├─ index.tsx          # 앱 카탈로그
│   │   │   │   ├─ $appId.tsx         # 앱 상세
│   │   │   │   └─ $appId/run.tsx     # 실행 폼
│   │   │   ├─ jobs/
│   │   │   │   ├─ index.tsx          # 실행 이력
│   │   │   │   └─ $jobId.tsx         # 작업 상세 + 실시간 로그
│   │   │   ├─ submit/
│   │   │   │   └─ index.tsx          # 새 앱 신청
│   │   │   └─ admin/
│   │   │       ├─ submissions.tsx    # 신청 큐
│   │   │       ├─ users.tsx
│   │   │       └─ system.tsx
│   │   ├─ components/
│   │   │   ├─ ui/                    # shadcn/ui 생성물
│   │   │   ├─ layout/                # Header, Sidebar, Footer
│   │   │   ├─ apps/                  # AppCard, ManifestForm, RunForm
│   │   │   ├─ jobs/                  # JobTable, LogViewer, StatusBadge
│   │   │   └─ common/                # ErrorBoundary, Toast 등
│   │   ├─ lib/
│   │   │   ├─ api/                   # OpenAPI 자동 생성 클라이언트
│   │   │   ├─ auth/                  # SSO 토큰 핸들링
│   │   │   ├─ ws/                    # WebSocket 클라이언트
│   │   │   └─ utils/
│   │   ├─ hooks/
│   │   ├─ stores/                    # Zustand 스토어
│   │   └─ styles/
│   │       └─ globals.css            # Tailwind 진입
│   ├─ public/
│   ├─ index.html
│   ├─ vite.config.ts
│   ├─ tailwind.config.ts
│   ├─ tsconfig.json
│   ├─ components.json                # shadcn 설정
│   └─ package.json
│
├─ backend/                           # FastAPI 백엔드
│   ├─ app/
│   │   ├─ main.py                    # FastAPI 진입
│   │   ├─ config.py                  # 환경 변수 (Pydantic Settings)
│   │   ├─ deps.py                    # 의존성 주입
│   │   ├─ api/
│   │   │   └─ v1/
│   │   │       ├─ router.py
│   │   │       ├─ auth.py
│   │   │       ├─ apps.py
│   │   │       ├─ jobs.py
│   │   │       ├─ submissions.py
│   │   │       ├─ admin.py
│   │   │       └─ ws.py              # WebSocket 핸들러
│   │   ├─ core/
│   │   │   ├─ security.py            # OIDC, JWT
│   │   │   ├─ logger.py
│   │   │   └─ errors.py
│   │   ├─ db/
│   │   │   ├─ session.py
│   │   │   ├─ base.py
│   │   │   └─ models/                # SQLAlchemy 모델
│   │   │       ├─ user.py
│   │   │       ├─ app.py
│   │   │       ├─ app_version.py
│   │   │       ├─ job.py
│   │   │       ├─ submission.py
│   │   │       └─ permission.py
│   │   ├─ schemas/                   # Pydantic 스키마
│   │   ├─ services/                  # 비즈니스 로직
│   │   │   ├─ app_lifecycle.py
│   │   │   ├─ submission_service.py
│   │   │   ├─ job_orchestrator.py
│   │   │   ├─ workspace_manager.py
│   │   │   ├─ manifest_validator.py
│   │   │   └─ permission_service.py
│   │   ├─ runners/                   # 실행 어댑터
│   │   │   ├─ base.py
│   │   │   ├─ local_runner.py
│   │   │   ├─ slurm_runner.py
│   │   │   ├─ apptainer_runner.py
│   │   │   ├─ windows_agent_client.py
│   │   │   └─ external_link_runner.py
│   │   ├─ workers/                   # Celery 태스크
│   │   │   ├─ celery_app.py
│   │   │   ├─ build_tasks.py
│   │   │   ├─ sync_tasks.py
│   │   │   ├─ job_tasks.py
│   │   │   └─ webhook_tasks.py
│   │   └─ tests/
│   ├─ alembic/                       # DB 마이그레이션
│   ├─ pyproject.toml
│   └─ Dockerfile
│
├─ app_workspaces/                    # ◀ 핵심: 등록 앱들의 작업 폴더
│   └─ {app_id}/                      # 신청 승인 시 자동 생성
│       ├─ upstream/                  # git clone 결과 (read-only)
│       ├─ overlay/                   # manifest, run.sh wrapper 등
│       │   ├─ .portal/
│       │   │   ├─ manifest.yaml
│       │   │   ├─ run.sh
│       │   │   └─ params.schema.json
│       │   └─ upstream.lock          # 핀된 버전
│       ├─ venv/                      # 파이썬 앱일 때 격리 가상환경
│       ├─ sif/                       # 비파이썬 앱일 때 SIF 이미지
│       ├─ build/
│       │   ├─ build.log
│       │   └─ status.json            # 마지막 빌드 결과
│       └─ README.md                  # 운영자가 작성하는 사용 안내
│
├─ job_storage/                       # 실행 결과 저장 (job_id 단위)
│   └─ {YYYY}/{MM}/{job_id}/
│       ├─ input/
│       ├─ work/
│       ├─ output/
│       │   ├─ result.json
│       │   ├─ report.html
│       │   └─ output.zip
│       ├─ logs/
│       └─ params.json
│
├─ templates/                         # 신규 앱용 기본 양식
│   ├─ python-cli/                    # portal-app-template (Python CLI)
│   ├─ python-webapp/
│   ├─ cpp-cli/
│   └─ windows-gui/
│
├─ schemas/                           # JSON Schema 정의
│   ├─ manifest.schema.json
│   ├─ params.schema.json
│   └─ result.schema.json
│
├─ scripts/                           # 운영 스크립트
│   ├─ provision_workspace.sh
│   ├─ build_python_venv.sh
│   ├─ build_apptainer_sif.sh
│   ├─ rotate_job_storage.sh
│   └─ healthcheck.sh
│
├─ docs/
│   ├─ ARCHITECTURE.md
│   ├─ MANIFEST_SPEC.md
│   ├─ RUNNER_PROTOCOL.md
│   ├─ SECURITY_REVIEW.md
│   └─ API_REFERENCE.md
│
├─ deploy/
│   ├─ docker-compose.yml
│   ├─ docker-compose.prod.yml
│   └─ systemd/
│       ├─ heaxhub-api.service
│       ├─ heaxhub-worker.service
│       └─ heaxhub-frontend.service
│
├─ .env.example
├─ Makefile
├─ README.md
└─ PROJECT_PLAN.md   ◀ 본 문서
```

---

## 4. 데이터 모델

### 4.1 ER 다이어그램 (개념)

```
users ──▶ permissions ◀── apps ──▶ app_versions
   │                       │            │
   │                       ▼            │
   └──▶ submissions ──▶ jobs ◀──────────┘
                          │
                          ▼
                      job_logs
                      job_artifacts
```

### 4.2 주요 테이블 (SQLAlchemy 기준)

#### `users`

SSO 연동 전후 모두 같은 테이블로 운영한다. 1단계에서는 `password_hash`로 로그인하고, 2단계 SSO 도입 시 같은 `email`을 키로 기존 사용자를 매핑한 뒤 `sso_subject`를 채우는 식으로 점진 전환한다.

| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | UUID PK | |
| email | CITEXT UNIQUE | **매핑 키** — 로그인 ID, 추후 SSO 매핑에도 이 값을 기준으로 사용 |
| display_name | String | 사람 이름 (예: "박정호") |
| organization | String | 조직명 — 그룹/랩 단위 (예: "CAE자동화파트", "구조해석랩") |
| password_hash | String | nullable — SSO 전환 후 NULL 처리 (argon2 또는 bcrypt) |
| auth_source | Enum(`local`, `sso`) | 현재 인증 경로. SSO 전환 시 `sso`로 갱신 |
| sso_subject | String UNIQUE NULL | OIDC `sub` — 1단계에서는 NULL, 2단계 매핑 후 채워짐 |
| email_verified | Boolean | 1단계: 가입 시 이메일 인증 토큰. SSO 전환 시 강제 true |
| status | Enum(`pending_verification`, `active`, `disabled`) | |
| role | Enum(`admin`, `owner`, `user`, `viewer`) | |
| ldap_groups | JSONB | nullable — SSO 도입 후 권한 매핑용 (1단계에는 비어 있음) |
| last_login_at | Timestamp | |
| created_at, updated_at | Timestamp | |

**매핑 규칙 (2단계 SSO 도입 시)**

1. SSO 로그인 콜백에서 받은 ID Token의 `email` 과 동일한 `users.email` 행을 찾는다.
2. 있으면: `sso_subject = sub`, `auth_source = 'sso'`, `password_hash = NULL`, `ldap_groups = (그룹 정보)` 로 갱신. **id는 그대로 유지**되므로 기존 jobs/submissions/permissions FK는 끊기지 않는다.
3. 없으면: SSO 클레임으로 신규 user 생성 (`auth_source = 'sso'`).
4. 이메일 충돌 방지: 1단계 회원가입 폼에서 `email`은 **회사 도메인** (`@company.com`)만 허용 — SSO의 이메일과 충돌 없도록.

#### `apps`
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | String PK | manifest의 `id` |
| name | String | |
| description | Text | |
| owner_user_id | FK users | |
| current_version | FK app_versions | nullable (빌드 전) |
| app_type | Enum(7종) | cli_tool / web_app / windows_gui / remote_app / external_link / slurm_job / container_app |
| execution_target | Enum(6종) | linux_runner / slurm / apptainer / windows_worker / external_url / local_pc |
| status | Enum(`draft`, `beta`, `stable`, `deprecated`, `archived`) | |
| visibility | Enum(`private`, `team`, `department`, `company`) | |
| upstream_repo_url | String | git URL |
| overlay_repo_url | String | nullable (overlay 별도 시) |
| tags | JSONB (Array) | |
| workspace_path | String | `app_workspaces/{id}/` |
| created_at, updated_at | Timestamp | |

#### `app_versions`
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | UUID PK | |
| app_id | FK apps | |
| version | String | semver |
| git_commit_hash | String | |
| git_tag | String | nullable |
| manifest_snapshot | JSONB | 그 시점 manifest 전체 |
| build_status | Enum(`pending`, `building`, `success`, `failed`) | |
| build_log_path | String | |
| sif_path | String | nullable |
| venv_path | String | nullable |
| released_at | Timestamp | |
| released_by | FK users | |

#### `submissions` (신청서)
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | UUID PK | |
| submitter_user_id | FK users | |
| proposed_app_id | String | manifest에서 추출하거나 신청자가 제시 |
| name | String | |
| description | Text | |
| upstream_repo_url | String | |
| proposed_manifest | JSONB | 신청 시점 manifest 사본 (선택) |
| status | Enum(`pending`, `under_review`, `approved`, `rejected`, `built`, `published`) | |
| review_notes | Text | 운영자 코멘트 |
| reviewer_user_id | FK users | nullable |
| created_at, reviewed_at, published_at | Timestamp | |

#### `jobs`
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | String PK | `job_YYYYMMDD_NNNN` |
| app_id | FK apps | |
| app_version_id | FK app_versions | |
| executor_user_id | FK users | |
| status | Enum(`queued`, `running`, `success`, `failed`, `canceled`) | |
| execution_target | Enum | jobs마다 capture (앱 정의 변할 수 있음) |
| params_json | JSONB | |
| input_files | JSONB (Array of paths) | |
| storage_path | String | `job_storage/{Y}/{M}/{job_id}/` |
| result_summary | JSONB | result.json 핵심 필드 캐시 |
| started_at, finished_at | Timestamp | |
| duration_sec | Integer | |

#### `permissions`
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | UUID PK | |
| app_id | FK apps | |
| principal_type | Enum(`user`, `group`, `role`) | |
| principal_id | String | user_id 또는 group_name |
| permission | Enum(`view`, `execute`, `manage`) | |

#### `audit_log`
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | BigInt PK | |
| actor_user_id | FK users | nullable (system action) |
| action | String | e.g. `app.approve`, `job.cancel` |
| target_type | String | |
| target_id | String | |
| meta | JSONB | |
| ip_address | String | |
| created_at | Timestamp | |

### 4.3 인덱스 정책

- `apps`: (status, visibility), (owner_user_id), GIN on tags
- `jobs`: (app_id, started_at DESC), (executor_user_id, started_at DESC), (status)
- `submissions`: (status, created_at)
- `audit_log`: (actor_user_id, created_at DESC), (target_type, target_id)

---

## 5. 앱 라이프사이클 — 핵심 자동화 파이프라인

본 프로젝트의 가장 중요한 차별점은 **신청만 받으면 워크스페이스 생성 · 클론 · 빌드 · 공개가 자동화** 된다는 것이다.

### 5.1 단계별 흐름

```
[신청] ──▶ [검토] ──▶ [워크스페이스 프로비저닝] ──▶ [빌드] ──▶ [검증] ──▶ [공개]
   │         │              │                          │          │          │
 사용자    운영자         자동                      자동       운영자     자동
                       (Celery worker)            (Celery)
```

### 5.2 각 단계 상세

#### 5.2.1 신청 (Intake)

- 사용자가 `/submit` 페이지에서 입력:
  - 앱 이름, 한 줄 설명
  - **Upstream Git URL** (필수)
  - 제안 `app_type`, `execution_target`
  - manifest 초안 (선택, 양식으로 시작했다면 자동)
- 백엔드: `submissions` 테이블에 `pending` 상태로 row 생성
- 자동 사전 검증:
  - Git URL 도달 가능성 확인 (HEAD 요청)
  - 사내 GitHub 도메인 화이트리스트 검증
  - 동일 `app_id` 중복 확인
- 운영자에게 알림 (Slack 또는 이메일, 사내 환경에 맞춰 설정)

#### 5.2.2 검토 (Review)

- 운영자는 `/admin/submissions`에서 신청 목록 확인
- 각 신청에 대해:
  - manifest 자동 검증 결과 표시 (스키마 위반, 보안 플래그)
  - "shallow clone 미리보기" 버튼: 임시로 클론해서 디렉터리 구조 확인
  - 승인 / 반려 / 보류 결정
- 승인 시 다음 단계로 자동 진행

#### 5.2.3 워크스페이스 프로비저닝 (Provisioning)

```python
# 의사코드
def provision_workspace(submission):
    app_id = submission.proposed_app_id
    workspace = Path(f"app_workspaces/{app_id}")
    workspace.mkdir(parents=True)

    # 1. upstream clone
    subprocess.run([
        "git", "clone", "--depth=1",
        submission.upstream_repo_url,
        workspace / "upstream"
    ], check=True)

    # 2. overlay 초기화
    overlay = workspace / "overlay" / ".portal"
    overlay.mkdir(parents=True)

    # manifest가 upstream에 .portal/manifest.yaml로 있으면 복사
    upstream_manifest = workspace / "upstream" / ".portal" / "manifest.yaml"
    if upstream_manifest.exists():
        shutil.copy(upstream_manifest, overlay / "manifest.yaml")
    else:
        # 운영자에게 manifest 작성 요청 상태로 전환
        submission.status = "manifest_required"
        return

    # 3. upstream.lock 작성
    commit = git_get_head_commit(workspace / "upstream")
    write_lock(workspace / "overlay" / "upstream.lock",
               url=submission.upstream_repo_url, commit=commit)

    # 4. DB에 app, app_version row 생성 (build_status=pending)
    create_app_records(app_id, submission, commit)

    # 5. 빌드 큐에 적재
    celery.send_task("build_tasks.build_app", args=[app_id, version_id])
```

#### 5.2.4 빌드 (Build)

빌드는 `execution_target`에 따라 분기:

**파이썬 앱 (linux_runner, slurm — Python script)**
```bash
# scripts/build_python_venv.sh {app_id} {version}
WORKSPACE=app_workspaces/$1
python3.11 -m venv $WORKSPACE/venv
source $WORKSPACE/venv/bin/activate
pip install --upgrade pip wheel

# upstream에 있는 requirements.txt 또는 pyproject.toml 자동 인식
if [ -f $WORKSPACE/upstream/requirements.txt ]; then
    pip install -r $WORKSPACE/upstream/requirements.txt
elif [ -f $WORKSPACE/upstream/pyproject.toml ]; then
    pip install $WORKSPACE/upstream
fi

# 빌드 결과 status.json 작성
```

**비파이썬 / 복잡한 환경 (container_app, slurm with native binary)**
```bash
# scripts/build_apptainer_sif.sh {app_id} {version}
# upstream에 Apptainer.def 또는 Dockerfile이 있어야 함
WORKSPACE=app_workspaces/$1

if [ -f $WORKSPACE/upstream/Apptainer.def ]; then
    apptainer build $WORKSPACE/sif/app.sif $WORKSPACE/upstream/Apptainer.def
elif [ -f $WORKSPACE/upstream/Dockerfile ]; then
    # docker → SIF 변환
    apptainer build $WORKSPACE/sif/app.sif docker-daemon://...
fi
```

**Windows GUI (windows_worker)**
- 빌드 자체는 윈도우 측에서 수행
- 포탈은 Windows Agent에 "이 git URL을 v1.2.0으로 pull하고 EXE를 준비하라" 명령
- Agent가 완료 신호를 보내면 `build_status = success`

**Web App / External Link (external_url, remote_app)**
- 별도 빌드 없음. URL 유효성만 검증 (HEAD 200)
- `build_status = success` 즉시 처리

**빌드 격리 원칙**
- 빌드는 **Celery worker가 시스템 전체와 분리된 unprivileged user**로 실행
- 빌드 timeout: 기본 30분, manifest에서 override 가능
- 빌드 실패 시 `build_log_path`에 stdout/stderr 보관, 운영자에게 알림

#### 5.2.5 검증 (Verification, 선택적 — 운영자 수동)

- 운영자가 `/admin/submissions/{id}/test-run` 클릭
- 작은 샘플 입력으로 한 번 실행 → 정상 작동 확인
- 통과 시 다음 단계

#### 5.2.6 공개 (Publish)

```python
def publish_app(app_id, version_id):
    app = db.get(Apps, app_id)
    app.current_version_id = version_id
    app.status = "stable"  # 또는 manifest의 status 따름
    db.commit()

    # 캐시 무효화 (TanStack Query 측 자동 갱신용 ETag)
    cache.invalidate(f"apps/{app_id}")

    # 사용자에게 알림 (선택)
    notify_subscribers(app_id, version_id)

    # 감사 로그
    audit_log("app.publish", app_id, actor=current_user)
```

### 5.3 후속 자동 동기화 (Continuous Sync)

GitHub webhook 또는 polling으로:

```python
# workers/sync_tasks.py
@celery.task
def check_upstream_updates():
    for app in db.query(Apps).filter(status="stable"):
        latest = git_ls_remote_tag(app.upstream_repo_url)
        current = read_lock(app.workspace_path / "overlay/upstream.lock")

        if latest != current.commit:
            # 운영자에게 검토 요청 알림 생성
            create_update_proposal(app, latest)
            # 자동 빌드는 하지 않음 (등록 흐름 = 신청-승인-빌드-공개와 동일)
```

업데이트 검토는 운영자가 `/admin/updates` 에서 보고 승인 시 동일 빌드 파이프라인 재실행.

---

## 6. Job Orchestrator — 실행 분배

### 6.1 실행 요청 흐름

```
사용자 RunForm 제출
     │
     ▼
POST /api/v1/apps/{app_id}/run
     │
     ├─ 권한 검사 (permissions 테이블)
     ├─ 입력 파일 업로드 → job_storage/{job_id}/input/
     ├─ params.json 작성
     ├─ jobs row 생성 (status=queued)
     ▼
Celery task `job_tasks.run_job(job_id)` 적재
     │
     ▼
Worker가 app.execution_target 확인 → 적절한 Runner 선택
     │
     ├─ LocalRunner          : 로컬 프로세스 (venv 활성화)
     ├─ SlurmRunner          : sbatch 명령
     ├─ ApptainerRunner      : apptainer exec
     ├─ WindowsAgentClient   : Agent에 REST/Queue 명령
     └─ ExternalLinkRunner   : 클릭 추적만, 외부에서 실행
     │
     ▼
Runner가 실행 → stdout/stderr 스트리밍 → WebSocket 푸시
     │
     ▼
종료 시 output/ 정리, status 갱신, result.json 캐시
```

### 6.2 Runner 인터페이스 (추상 베이스)

```python
# backend/app/runners/base.py
class BaseRunner(ABC):
    @abstractmethod
    async def start(self, job: Job) -> None:
        """job 실행 시작 (비동기). job_id로 상태 추적"""

    @abstractmethod
    async def stream_logs(self, job_id: str) -> AsyncIterator[str]:
        """실시간 로그 스트림"""

    @abstractmethod
    async def cancel(self, job_id: str) -> bool: ...

    @abstractmethod
    async def collect_results(self, job: Job) -> JobResult: ...
```

각 Runner는 이를 구현. 새 실행 환경 추가는 `runners/` 에 클래스 하나 추가하는 것으로 끝.

### 6.3 venv 활용 예시 (LocalRunner)

```python
class LocalRunner(BaseRunner):
    async def start(self, job: Job) -> None:
        app = job.app
        workspace = Path(f"app_workspaces/{app.id}")
        venv_python = workspace / "venv/bin/python"

        env = os.environ.copy()
        env["PYTHONPATH"] = str(workspace / "upstream")
        env["JOB_INPUT"] = str(job.storage_path / "input")
        env["JOB_OUTPUT"] = str(job.storage_path / "output")
        env["JOB_PARAMS"] = str(job.storage_path / "params.json")

        # overlay/.portal/run.sh 가 표준 진입점
        cmd = [
            "bash",
            str(workspace / "overlay/.portal/run.sh"),
            str(job.storage_path / "input"),
            str(job.storage_path / "output"),
            str(job.storage_path / "params.json"),
        ]

        # venv를 PATH 앞에 둠
        env["PATH"] = f"{workspace}/venv/bin:{env['PATH']}"

        process = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=workspace / "upstream",
        )
        # process 등록, 로그 펌프 시작 …
```

### 6.4 실시간 로그 (WebSocket)

- `/ws/jobs/{job_id}/logs` 채널에 worker가 로그 라인 push
- 프론트의 `<LogViewer>` 컴포넌트가 구독, 자동 스크롤
- 연결 끊김 대비: 디스크 로그 파일(`logs/stdout.log`)을 GET으로도 받을 수 있음

---

## 7. 보안 · 권한

### 7.1 인증

**1단계 (현재): 자체 인증**

- 이메일 + 비밀번호 가입, 회사 도메인 화이트리스트
- 비밀번호 해시: argon2id (passlib)
- 이메일 인증 토큰 (24시간 유효) 후 `active` 상태
- 백엔드 JWT 발급: access 1시간 + refresh 7일 (DB에 저장된 refresh token rotation)
- 운영자(`admin`)는 시드 스크립트로 최초 1명 생성

**2단계 (추후): SSO 전환**

- 사내 OIDC IdP로 전환, ID Token 검증 후 내부 JWT 발급
- 기존 `users.email`을 키로 자동 매핑 (`4.2 users 매핑 규칙` 참조)
- 전환 후 `/auth/login`(비밀번호 로그인)은 비활성화, `auth_source='local'` 잔여 사용자는 운영자가 일괄 마이그레이션

### 7.2 권한 모델

```text
Role (글로벌)        Permission (앱별)
├─ admin             ├─ view     (앱 정보 조회)
├─ owner             ├─ execute  (앱 실행)
├─ user              └─ manage   (manifest 수정, 삭제)
└─ viewer
```

**Role 부여 방식**

- 1단계: 가입 시 기본 `user`. `admin`/`owner`는 운영자가 `/admin/users`에서 수동 승격
- 2단계: SSO 도입 후 LDAP 그룹 → Role 매핑 테이블 적용 (자동 갱신 가능)

**가시성(visibility) 해석 — 1단계 기준**

`apps.visibility` + `permissions` 테이블 조합:

- `private`: `permissions` 테이블에 명시된 사용자만
- `team`: owner의 **`organization`** 값과 동일한 사용자 (1단계 핵심 분기 기준)
- `department`: 운영자가 정의한 조직 그룹 (`organization_groups` 테이블, 추후 도입)
- `company`: 인증된 모든 사용자 (`active` 상태)

2단계 SSO 도입 후에는 `team`/`department` 해석이 LDAP 그룹 기반으로 자연스럽게 확장된다 — `organization` 필드는 그대로 보존되어 SSO가 들어오기 전에 가입한 사용자들의 소속 정보로 계속 활용된다.

### 7.3 격리

| 위험 | 대응 |
|---|---|
| 악의적 코드가 시스템 침투 | 빌드/실행은 unprivileged user, 워크스페이스만 쓰기 가능 |
| 의존성 충돌로 다른 앱 망가짐 | 앱마다 venv / SIF 격리 |
| 거대한 출력으로 디스크 차오름 | job 단위 quota, 자동 아카이브 |
| 외부 git URL의 비밀 파일 노출 | 사내 도메인만 화이트리스트 |
| 무한 루프 / 폭주 | runner마다 timeout, 메모리 cgroup |
| Webhook으로 임의 빌드 트리거 | webhook secret 검증, IP 화이트리스트 |

### 7.4 감사

- 모든 상태 변경 액션(`app.publish`, `submission.approve`, `job.cancel`)은 `audit_log` 기록
- `/admin/audit`에서 검색·필터링

---

## 8. 프론트엔드 화면 명세

### 8.1 라우팅 트리

```
/                                메인 포탈 (랜딩 + 추천 + 검색)
/apps                            앱 카탈로그 (필터·정렬)
/apps/{appId}                    앱 상세 (설명·버전·이력)
/apps/{appId}/run                실행 폼 (params·파일 업로드)
/jobs                            내 실행 이력
/jobs/{jobId}                    작업 상세 + 실시간 로그
/submit                          새 앱 신청
/login                           SSO 시작
/admin                           관리자 대시보드
  /admin/submissions             신청 큐
  /admin/updates                 업스트림 갱신 검토
  /admin/users
  /admin/system                  시스템 상태
  /admin/audit                   감사 로그
```

### 8.2 메인 포탈 (`/`) — "아주 세련되게"

`living_memory.html`의 시각 언어(다크 그라데이션 cover, 라이트 본문, 카테고리 별 색 변수)를 React 컴포넌트로 옮긴다.

레이아웃:

```
┌────────────────────────────────────────────────────────────┐
│ [Header: 로고 · 검색 · 알림 · 프로필]                       │
├────────────────────────────────────────────────────────────┤
│ ┌─ Hero ─────────────────────────────────────────────────┐ │
│ │ "흩어진 자동화 프로그램을 한 곳에서"                   │ │
│ │ 큰 검색바 (앱 이름·태그·설명 인덱스 통합 검색)         │ │
│ │ [추천 카테고리 칩] CAE · Pre · Post · 데이터 · ...     │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                            │
│ ┌─ 빠른 시작 ────────────┐ ┌─ 내 최근 실행 ────────────┐  │
│ │ - 자주 쓰는 앱 4개      │ │ - 마지막 5개 작업          │  │
│ │ - 즐겨찾기              │ │ - 상태 배지                │  │
│ └────────────────────────┘ └────────────────────────────┘  │
│                                                            │
│ ┌─ 둘러보기 ──────────────────────────────────────────┐    │
│ │ 카테고리별 추천 카드 그리드                           │    │
│ │ (app_type 필터 토글, 정렬 옵션)                       │    │
│ └────────────────────────────────────────────────────┘    │
│                                                            │
│ ┌─ 통계 (관리자만) ────────────────────────────────────┐   │
│ │ 오늘 실행 건수 · 활성 사용자 · 빌드 큐                │   │
│ └──────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

디자인 토큰:

```ts
// frontend/src/styles/tokens.ts
export const colors = {
  brand:    { 50: '#eff6ff', 500: '#4338ca', 900: '#1e1b4b' },
  accent:   { gold: '#fcd34d', amber: '#d97706' },
  category: {
    cli:     '#0891b2',
    web:     '#16a34a',
    gui:     '#7c3aed',
    remote:  '#0d9488',
    link:    '#64748b',
    slurm:   '#d97706',
    container: '#db2777',
  },
};
```

- 폰트: Pretendard (사내 표준) + JetBrains Mono (코드)
- 다크/라이트 모드 토글 (시스템 따름 기본)
- 미세 인터랙션: framer-motion으로 카드 hover, 페이지 전환

### 8.3 앱 카탈로그 (`/apps`)

- 좌측: 필터 사이드바 (app_type, status, tags, visibility)
- 우측: 카드 그리드 또는 테이블 토글
- 카드: 이름 · 설명 · 버전 · 상태 배지 · "실행" / "상세" 버튼
- 무한 스크롤 + 검색 디바운스

### 8.4 앱 상세 (`/apps/{appId}`)

- 헤더: 이름 · 설명 · 만든이 · 현재 버전 · 상태
- 탭:
  1. **사용법** — manifest의 description, params 설명
  2. **실행** — RunForm (params.schema.json 기반 자동 생성 폼)
  3. **이력** — 이 앱의 최근 실행 목록
  4. **변경 이력** — app_versions 목록 + CHANGELOG
  5. **문서** — overlay의 README 렌더링

### 8.5 실행 폼 (`/apps/{appId}/run`)

- `params.schema.json` (JSON Schema)을 `react-jsonschema-form` 또는 자체 폼 생성기로 자동 렌더링
- 파일 업로드는 chunked + 진행률 표시
- "이전 실행에서 가져오기" 버튼 (params 복사)
- 제출 시 → 즉시 `/jobs/{jobId}` 로 이동

### 8.6 작업 상세 + 실시간 로그 (`/jobs/{jobId}`)

- 상단: 상태 배지 (queued/running/success/failed/canceled)
- 좌측: 작업 메타 (입력 파일, params, 환경, 버전)
- 우측: 실시간 로그 뷰어 (xterm.js)
- 완료 시: 결과 파일 다운로드 버튼, report.html 인라인 뷰어
- "재실행" 버튼: 같은 params로 새 job 생성

### 8.7 새 앱 신청 (`/submit`)

- 단계형 wizard:
  1. **기본 정보** — 앱 이름, 한 줄 설명
  2. **Git URL** — 사내 도메인 검증
  3. **분류** — app_type, execution_target (셀렉트)
  4. **manifest** — 자동 감지된 manifest 미리보기 or 작성 폼
  5. **검토** — 운영자에게 전달될 내용 확인
- 신청 후 상태 추적 화면

### 8.8 관리자 — 신청 큐 (`/admin/submissions`)

- 테이블 + 우측 슬라이드 패널
- 각 행: 신청자 · 앱 이름 · Git URL · 자동 검증 결과 · 상태
- 선택 시 우측 패널에서 manifest preview, clone 시뮬레이션, 승인/반려 버튼

### 8.9 컴포넌트 라이브러리

shadcn/ui로 다음 컴포넌트를 생성·커스터마이즈:

- Button, Input, Select, Checkbox, RadioGroup
- Card, Sheet, Dialog, Tabs, Toast
- Table (TanStack Table 통합)
- Form (react-hook-form 통합)
- Command (검색 팔레트, `Cmd+K`)

자체 컴포넌트:

- `<AppCard>`, `<ManifestPreview>`, `<RunForm>`, `<LogViewer>`,
  `<StatusBadge>`, `<JobTimeline>`, `<DiffViewer>` (manifest 비교)

---

## 9. API 명세 (요약)

### 9.1 인증

**1단계: 자체 회원가입 (Local Auth)**

```
POST   /api/v1/auth/register       # 가입: name, organization, email, password
POST   /api/v1/auth/verify-email   # 이메일 인증 토큰 확인
POST   /api/v1/auth/login          # email + password → JWT 발급
POST   /api/v1/auth/refresh        # refresh token → 새 access token
POST   /api/v1/auth/logout
GET    /api/v1/auth/me             # 현재 사용자 정보
POST   /api/v1/auth/password/reset-request   # 비밀번호 재설정 메일
POST   /api/v1/auth/password/reset            # 토큰으로 비밀번호 변경
PATCH  /api/v1/users/me                       # 이름·조직 수정 (이메일은 별도)
```

**2단계: SSO 전환 (추후)**

기존 엔드포인트는 유지하고 다음을 추가. `auth_source = 'sso'` 사용자는 `/login`, `/register`가 거부됨.

```
GET    /api/v1/auth/sso/start      # OIDC redirect 시작
GET    /api/v1/auth/sso/callback   # OIDC 콜백 + 자동 매핑
POST   /api/v1/admin/auth/migrate-to-sso   # 운영자: 사용자 일괄 매핑 트리거
```

**가입 폼 (`/auth/register`) 요청 스키마**

```json
{
  "display_name": "박정호",
  "organization": "CAE자동화파트",
  "email": "jhpark@company.com",
  "password": "********",
  "password_confirm": "********"
}
```

검증:
- 이메일은 회사 도메인 화이트리스트 (`config.ALLOWED_EMAIL_DOMAINS`) 만 허용
- 비밀번호: 최소 10자, 대소문자·숫자·특수문자 중 3종 이상
- 가입 직후 상태는 `pending_verification`, 이메일 인증 후 `active`
- 비밀번호는 argon2id 해시로 저장 (passlib)

### 9.2 앱

```
GET    /api/v1/apps                 # 카탈로그 (필터·정렬·페이지네이션)
GET    /api/v1/apps/{app_id}        # 상세
GET    /api/v1/apps/{app_id}/manifest
GET    /api/v1/apps/{app_id}/versions
POST   /api/v1/apps/{app_id}/run    # 실행 요청 (multipart for files)
```

### 9.3 작업

```
GET    /api/v1/jobs                 # 내 작업 (관리자는 전체)
GET    /api/v1/jobs/{job_id}
GET    /api/v1/jobs/{job_id}/logs   # 전체 로그 (스트림 아님)
GET    /api/v1/jobs/{job_id}/files  # 결과 파일 목록
GET    /api/v1/jobs/{job_id}/files/{path}  # 다운로드
POST   /api/v1/jobs/{job_id}/cancel
WS     /ws/jobs/{job_id}/logs       # 실시간 스트림
```

### 9.4 신청

```
POST   /api/v1/submissions
GET    /api/v1/submissions          # 내 신청 (관리자는 전체)
GET    /api/v1/submissions/{id}
PATCH  /api/v1/submissions/{id}     # 운영자 승인/반려
POST   /api/v1/submissions/{id}/test-run
```

### 9.5 관리자

```
GET    /api/v1/admin/system/health
GET    /api/v1/admin/audit
GET    /api/v1/admin/users
PATCH  /api/v1/admin/users/{id}/role
GET    /api/v1/admin/updates        # 업스트림 갱신 알림 목록
POST   /api/v1/admin/updates/{id}/approve
```

### 9.6 Webhook 수신

```
POST   /api/v1/webhooks/github      # tag push 이벤트
POST   /api/v1/webhooks/windows-agent  # Agent 상태 보고
```

OpenAPI 스펙은 FastAPI 자동 생성, 프론트엔드는 `openapi-typescript-codegen`으로 타입+클라이언트 생성.

---

## 10. 환경 변수 및 설정

`.env.example` 발췌:

```bash
# 기본
APP_ENV=production
APP_PORT=8000
APP_HOST=0.0.0.0

# DB
DATABASE_URL=postgresql+asyncpg://heaxhub:***@db:5432/heaxhub
REDIS_URL=redis://redis:6379/0

# 인증 (1단계: 자체 가입)
AUTH_MODE=local                            # local → sso 로 추후 전환
ALLOWED_EMAIL_DOMAINS=company.com,corp.company.com
JWT_SECRET=***                             # access/refresh 서명 키
ACCESS_TOKEN_TTL_SECONDS=3600
REFRESH_TOKEN_TTL_SECONDS=604800
PASSWORD_MIN_LENGTH=10
EMAIL_VERIFY_TOKEN_TTL_HOURS=24

# 메일 발송 (가입 인증, 비밀번호 재설정)
SMTP_HOST=mail.company.com
SMTP_PORT=587
SMTP_USER=heaxhub-noreply
SMTP_PASSWORD=***
MAIL_FROM=heaxhub-noreply@company.com

# 인증 (2단계: SSO 전환 시 채워짐)
# OIDC_ISSUER=https://sso.company.com
# OIDC_CLIENT_ID=heaxhub
# OIDC_CLIENT_SECRET=***
# OIDC_REDIRECT_URI=https://hub.company.com/api/v1/auth/sso/callback

# 파일 저장
JOB_STORAGE_ROOT=/data/heaxhub/job_storage
WORKSPACE_ROOT=/data/heaxhub/app_workspaces
USE_MINIO=false
MINIO_ENDPOINT=...

# Git 정책
ALLOWED_GIT_HOSTS=git.company.com,github.com/{org}

# 빌드 정책
PYTHON_BUILD_PATH=/usr/local/bin/python3.11
APPTAINER_BIN=/usr/bin/apptainer
BUILD_TIMEOUT_SECONDS=1800

# Slurm
SLURM_SUBMIT_HOST=slurm-login.company.com

# Windows Agent
WINDOWS_AGENT_QUEUE=windows-cae-tools
WINDOWS_AGENT_TOKEN=***

# Webhook
GITHUB_WEBHOOK_SECRET=***
```

---

## 11. 개발 환경

### 11.1 로컬 개발

```bash
# 한 줄 시작
make dev

# 내부적으로:
# - docker compose up postgres redis -d
# - cd backend && uvicorn app.main:app --reload (port 8000)
# - cd frontend && pnpm dev (port 5173, proxy /api → 8000)
# - cd backend && celery -A app.workers.celery_app worker --loglevel=info
```

### 11.2 권장 도구

- Python: `uv` (의존성 관리), `ruff` (lint+format), `mypy`
- TS: `pnpm`, `biome` (lint+format), `tsc --noEmit`
- pre-commit: ruff, biome, prettier, gitleaks
- 테스트: `pytest`, `pytest-asyncio`, `vitest`, `playwright`

### 11.3 컨테이너화

`docker-compose.yml`로 로컬 통합 실행:

```yaml
services:
  db:        # postgres:16
  redis:     # redis:7-alpine
  api:       # backend Dockerfile
  worker:    # backend Dockerfile, CMD celery worker
  frontend:  # 빌드 산출물 nginx 서빙 (또는 dev는 pnpm dev)
```

---

## 12. 테스트 전략

| 레이어 | 도구 | 커버리지 목표 |
|---|---|---|
| 백엔드 단위 | pytest | 70% (services, runners) |
| 백엔드 통합 | pytest + testcontainers (postgres, redis) | 주요 시나리오 |
| 프론트엔드 단위 | vitest + Testing Library | 핵심 컴포넌트 |
| E2E | Playwright | 메인 플로우 (로그인 → 검색 → 실행 → 결과) |
| 빌드 파이프라인 | 샘플 앱 fixture로 실제 빌드 | CI에서 매일 |

샘플 앱 픽스처: `tests/fixtures/sample-apps/` 에 5개 (cli, web, slurm, container, external_link) 두고, 통합 테스트에서 신청부터 실행까지 자동 수행.

---

## 13. 배포 전략

### 13.1 환경 분리

| 환경 | 용도 | 위치 |
|---|---|---|
| `dev` | 개발자 로컬 | docker-compose |
| `staging` | 통합 테스트, UAT | 사내 단일 서버 |
| `prod` | 실 운영 | 사내 클러스터 (확장 시 K8s) |

### 13.2 CI/CD (GitHub Actions 또는 사내 Jenkins)

```yaml
on: [push, pull_request]
jobs:
  lint:        # ruff, biome
  test:        # pytest, vitest
  build:       # docker images
  e2e:         # Playwright (PR only)
  deploy:      # staging on main push, prod on tag
```

### 13.3 마이그레이션

- DB: alembic, `make migrate`
- 워크스페이스 디렉터리 호환성: `app_workspaces/` 의 구조 버전을 `_meta.json`으로 기록, 마이그레이션 스크립트로 자동 업그레이드

---

## 14. 단계별 개발 일정 (PDCA 9 phase 매핑)

각 phase는 2주 sprint 기준. 총 16주 (4개월) MVP, +8주로 확장 기능.

### Phase 1 — Schema 정의 (1주차)
- manifest.schema.json, params.schema.json, result.schema.json 작성
- ER 다이어그램 확정, alembic 초기 마이그레이션
- **산출**: `schemas/`, `backend/alembic/versions/0001_init.py`

### Phase 2 — Convention (1주차 후반)
- 코딩 규칙 (ruff, biome 설정)
- 디렉터리 구조 동결
- **산출**: `pyproject.toml`, `biome.json`, `.pre-commit-config.yaml`

### Phase 3 — Mockup (2주차)
- shadcn/ui로 메인 포탈, 카탈로그, 상세 페이지 정적 mockup
- Figma 또는 HTML로 빠르게
- **산출**: `frontend/src/routes/index.tsx` 정적 버전

### Phase 4 — Backend API (3-6주차)
- **사용자 인증 (자체 가입)** — register, verify-email, login, JWT, 비밀번호 재설정
- 운영자 시드 스크립트 (`scripts/create_admin.py`)
- 앱 CRUD, manifest validator
- Submission workflow
- Workspace provisioner + builder (Python venv 먼저)
- Job orchestrator + LocalRunner
- **산출**: `backend/app/api/v1/`, `services/`, `runners/local_runner.py`, `services/auth_local.py`

### Phase 5 — Design System (3주차 병행)
- 디자인 토큰, shadcn 컴포넌트 커스터마이즈
- 다크/라이트 테마
- **산출**: `frontend/src/styles/`, `components/ui/`

### Phase 6 — UI Integration (5-8주차)
- 모든 라우트 구현, API 연동
- 실시간 로그 WebSocket
- 실행 폼 자동 생성기
- **산출**: 전체 `frontend/src/routes/`, `components/`

### Phase 7 — SEO & Security (9-10주차)
- 사내용이라 SEO보다는 접근성·보안 위주
- OWASP Top 10 점검
- 빌드/실행 격리 강화 (cgroup, ulimit)
- **산출**: `docs/SECURITY_REVIEW.md`, 보안 패치

### Phase 8 — Review & Gap (11-12주차)
- 운영 표준안 문서와의 gap 분석
- 운영자 시범 사용 (3명), 피드백 반영
- **산출**: `docs/GAP_ANALYSIS.md`, 수정 PR들

### Phase 9 — Deployment (13-14주차)
- staging 배포, UAT
- prod 배포 룬북
- 운영자 교육
- **산출**: `deploy/`, `docs/RUNBOOK.md`

### 확장 phase (15-22주차)
- SlurmRunner, ApptainerRunner, WindowsAgentClient 단계적 추가
- 자동 동기화 (webhook + polling)
- **SSO 전환 (별도 mini-phase)** — `auth/sso/start`·`auth/sso/callback` 구현, 이메일 매핑 마이그레이션 스크립트, `auth_source` 플래그 기반 동시 운영 기간 (2~4주), 이후 local 로그인 차단
- Mood Map 같은 갤러리식 탐색 (선택)

---

## 15. 위험 요인 및 대응

| 위험 | 영향 | 대응 |
|---|---|---|
| 사내 SSO 응답 지연 | 로그인 불가 | 짧은 TTL 캐시, fallback 페이지 |
| Apptainer 빌드 시간이 너무 김 | 배포 지연 | 빌드 캐시, 점진적 빌드 (이전 layer 재사용) |
| 대용량 입출력 파일 디스크 차오름 | 시스템 정지 | quota, 자동 아카이브, 모니터링 알림 |
| 악성 git URL 클론 | 보안 침해 | 도메인 화이트리스트, 빌드 sandbox |
| Windows Agent 다운 | 윈도우 작업 불가 | health check, 대기 큐 표시, 사용자에게 명확한 메시지 |
| manifest 스펙 변경 | 기존 앱 깨짐 | manifest schema_version, 자동 migration tool |
| Celery 큐 폭주 | 실행 지연 | 우선순위 큐 (관리자/일반), worker autoscaling |
| 동일 앱 동시 빌드 충돌 | 워크스페이스 깨짐 | redis 분산 락 (`app_id` 단위) |

---

## 16. 미해결 / 추후 결정 항목

다음 항목들은 본 계획서에서 일단 한 방향을 가정했지만, 첫 sprint 시작 전 재논의가 필요할 수 있다.

1. **사내 GitHub vs GitLab** — 어느 쪽이 표준인지에 따라 webhook 포맷, 인증 방식 조정
2. **Windows Agent 통신 방식** — REST polling / Redis queue / SMB 공유폴더 중 운영 환경 적합도 평가
3. **백엔드 비동기 vs 동기** — FastAPI는 async, 그러나 DB 라이브러리(SQLAlchemy) async 지원 성숙도. 초기엔 sync도 허용
4. **모노레포 도구** — pnpm workspaces? Nx? Turborepo? 일단 docker-compose + Makefile로 충분
5. **메인 포탈 디자인 최종 결정** — Phase 3 mockup 단계에서 사용자 그룹 인터뷰 후 확정
6. **다국어 (i18n)** — 영문 화면 필요 여부. 일단 한국어 단일
7. **SSO 전환 시점** — 1단계 운영 안정화 후 6~12개월 내 예상. IdP 측 클라이언트 등록 작업이 선행되어야 일정 확정 가능
8. **이메일 변경 정책** — `users.email`은 SSO 매핑 키이므로 1단계에서 사용자가 자유롭게 바꾸면 매핑이 깨질 수 있음. 변경은 운영자 승인 또는 막아두는 방향 검토

---

## 17. 부록

### A. manifest.yaml 예시 (`schemas/manifest.schema.json` 준수)

```yaml
schema_version: 1
id: lsdyna_kfile_checker
name: LS-DYNA K File Checker
version: 1.2.0
owner: cae-automation
status: stable
app_type: cli_tool
execution_target: linux_runner

description: |
  LS-DYNA k 파일의 part, contact, material, timestep 위험 요소를 검사한다.

launch:
  mode: job_runner
  command: ./run.sh input output params.json

inputs:
  - name: k_file
    type: file
    required: true
    extensions: [".k", ".key"]
  - name: check_contact
    type: boolean
    default: true

outputs:
  - name: report
    type: file
    path: output/report.html

permissions:
  visibility: team
  executable_by: ["cae_engineer", "admin"]

resources:
  cpu: 4
  memory_gb: 8
  gpu: false

build:
  type: python_venv
  python_version: "3.11"
  requirements_file: requirements.txt
```

### B. 표준 run.sh (`templates/python-cli/.portal/run.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail

INPUT_DIR="$1"
OUTPUT_DIR="$2"
PARAMS_FILE="$3"

# venv는 포탈이 PATH에 자동 주입 (Runner가 처리)
exec python -m mytool.main \
    --input "$INPUT_DIR" \
    --output "$OUTPUT_DIR" \
    --params "$PARAMS_FILE"
```

### C. 표준 result.json 스키마 (`schemas/result.schema.json` 발췌)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["status", "summary"],
  "properties": {
    "status":   { "enum": ["success", "warning", "failed"] },
    "summary":  { "type": "object" },
    "warnings": { "type": "array", "items": {"type": "string"} },
    "errors":   { "type": "array", "items": {"type": "string"} },
    "outputs":  { "type": "object" }
  }
}
```

### D. 개발 시작 체크리스트

**1단계 시작 시점**

- [ ] PostgreSQL · Redis 인프라 확보
- [ ] **사내 SMTP 발송 권한** (가입 이메일 인증, 비밀번호 재설정용)
- [ ] **허용 이메일 도메인 목록 확정** (`ALLOWED_EMAIL_DOMAINS`)
- [ ] **초기 admin 사용자 계정 정보** (시드 스크립트 입력용)
- [ ] 사내 GitHub 조직 + 봇 계정 발급 (clone 권한)
- [ ] Apptainer 설치, NAS / 작업 디스크 마운트
- [ ] HTTPS 인증서 (사내 CA)
- [ ] 도메인 결정 (`hub.company.com`)

**확장 phase 시점 (필요 시)**

- [ ] Slurm submit host 접근 권한
- [ ] Windows Worker 1대 시범

**2단계 SSO 전환 시점 (추후)**

- [ ] 사내 SSO 클라이언트 등록 (OIDC redirect URL `/api/v1/auth/sso/callback`)
- [ ] SSO IdP가 발급하는 이메일 클레임 포맷 확인 (이메일 매핑 키와 일치하는지)
- [ ] LDAP 그룹 → Role 매핑표 합의

---

**문서 끝.**

다음 단계 권장: `/pdca plan heaxhub` 로 PDCA Plan 단계 진입, 또는 Phase 1 (Schema 정의)부터 즉시 착수.
