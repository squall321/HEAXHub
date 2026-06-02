# HEAXHub Architecture

이 문서는 HEAXHub 시스템의 모든 컴포넌트가 어떻게 맞물려 돌아가는지 한눈에 보여주는 운영자용 참고서다. 자세한 설계 근거는 [`PROJECT_PLAN.md`](../PROJECT_PLAN.md)를 참고한다.

## 1. 5계층 구조

```text
[브라우저]
   │ HTTPS / WebSocket
   ▼
[Presentation] frontend/ (React + Vite + TS + shadcn/ui)
   │ /api/v1/*  /ws/jobs/{id}/logs
   ▼
[API] backend/app/api/v1/   (FastAPI)
   │
   ▼
[Domain Services] backend/app/services/   (Pydantic + 비즈니스 로직)
   │
   ├──▶ [Celery Workers] backend/app/workers/
   │      └──▶ [Runners] backend/app/runners/  (Local/Slurm/Apptainer/Windows/External)
   │
   ▼
[Storage]
   ├─ PostgreSQL (메타데이터, 8개 + 2개 추가 테이블)
   ├─ Redis (큐, 캐시, 실시간 로그 pubsub)
   ├─ app_workspaces/{app_id}/  (upstream + overlay + venv/SIF)
   └─ job_storage/{Y}/{M}/{job_id}/ (입력·결과·로그)
```

## 2. 컴포넌트 책임

### Frontend
- 상태는 백엔드를 단일 소스로 사용. TanStack Query가 캐시·재시도·invalidation 담당.
- 인증은 Zustand 스토어에 access/refresh 토큰 저장 (localStorage). 401 응답 자동 refresh.
- 실시간 로그는 `/ws/jobs/{id}/logs` 구독. 연결 끊김 시 자동 재시도.

### API
- FastAPI route 파일은 검증과 권한만 처리. 비즈니스 로직은 services에 둔다.
- 모든 라우트는 `app.deps`의 `CurrentUser`, `AdminUser`, `get_app_or_404`로 사전 검사.

### Services
- `auth_service`: 자체 가입(local), refresh token rotation, revoke.
- `submission_service`: 신청 수명주기.
- `app_lifecycle`: 승인 → 워크스페이스 프로비저닝 트리거.
- `job_orchestrator`: job_id 발급 → 파일 저장 → Celery enqueue.
- `permission_service`: visibility (`private/team/department/company`) + 명시적 ACL.
- `manifest_validator`: `schemas/manifest.schema.json` 기준 검증.

### Workers (Celery)
- `sync_tasks.clone_upstream` — 승인된 신청 워크스페이스 생성 + 빌드 enqueue
- `sync_tasks.refresh_upstream` — 운영자가 upstream 변경 승인 시 호출
- `sync_tasks.check_upstream_updates` — 주기적 폴링 (스케줄링은 운영자가 cron 또는 celery beat 설정)
- `build_tasks.build_python_venv` — `python -m venv` + pip install
- `build_tasks.build_apptainer_sif` — `scripts/build_apptainer_sif.sh` 위임
- `job_tasks.run_job` — Runner 선택, 실행, 결과 수집
- `webhook_tasks.handle_github_tag` — webhook 수신 시 sync 큐 적재

### Runners
- 추상 `BaseRunner`: `start`, `stream_logs`, `cancel`, `collect_results`.
- `LocalRunner`만 완전 구현. 다른 runner는 확장 phase에서 채움.
- `registry.py`가 `execution_target` → Runner 클래스 매핑.

## 3. 데이터 흐름 — 신청부터 실행까지

```text
사용자 ──POST /submissions──▶ submissions row (pending)
                            │
운영자 ──PATCH /submissions/{id} status=approved──▶
                            │
                  app_lifecycle.approve_and_provision
                            │
                  sync_tasks.clone_upstream.delay()
                            │
        git clone → overlay manifest 복사 → upstream.lock 작성
                            │
                  build_tasks.build_python_venv.delay()
                            │
                  AppVersion build_status: building → success
                            │
                  Submission status: built
                            │
운영자 ──PATCH /submissions/{id} status=published──▶
                            │
                  apps.current_version_id = version_id
                            │
사용자 ──POST /apps/{id}/run──▶ job row (queued)
                            │
                  job_orchestrator.submit_job()
                            │
                  job_tasks.run_job.delay()
                            │
                  Runner 선택 → subprocess → 실시간 로그 → result.json
```

## 4. 디렉터리 빠른 참조

| 위치 | 용도 |
|---|---|
| `frontend/src/routes/` | 페이지 (TanStack Router 파일 기반) |
| `frontend/src/components/` | UI · 도메인 · 레이아웃 컴포넌트 |
| `frontend/src/lib/api/` | 백엔드 API 호출 모듈 |
| `backend/app/api/v1/` | REST 엔드포인트 |
| `backend/app/services/` | 비즈니스 로직 |
| `backend/app/runners/` | 실행 어댑터 |
| `backend/app/workers/` | Celery 태스크 |
| `backend/alembic/versions/` | DB 마이그레이션 |
| `app_workspaces/{id}/` | 등록 앱의 코드 + venv + SIF |
| `job_storage/{Y}/{M}/{job}/` | 실행 결과 |
| `templates/` | 신규 앱용 기본 양식 |
| `scripts/` | 빌드·정리·헬스체크 셸 스크립트 |

## 5. 환경별 설정

| 환경 | DB | Redis | SMTP |
|---|---|---|---|
| dev | docker-compose db (:5732) | docker-compose redis (:6479) | mailhog (:8125/:8126) |
| staging | 사내 postgres | 사내 redis | 사내 SMTP |
| prod | 사내 postgres (HA) | 사내 redis (cluster) | 사내 SMTP |

`.env` 차이는 환경별 별도 secret으로 관리. `AUTH_MODE=local`만 1단계에서 사용, 2단계 진입 시 `sso`로 전환.

## 6. 외부 의존성 가용성 정책

| 의존성 | 다운 시 |
|---|---|
| PostgreSQL | 전체 다운 (재시작 외에 fallback 없음) |
| Redis | 큐·실시간 로그 불가, API 일부만 사용 가능 |
| GitHub | 신청·빌드 불가, 카탈로그·실행 정상 |
| SMTP | 가입 인증 메일 지연 — 운영자 수동 인증으로 우회 가능 |
| Slurm / Apptainer | 해당 runner 사용 앱만 실패, 나머지 정상 |
| Windows Worker | `windows_worker` 앱만 영향 |
