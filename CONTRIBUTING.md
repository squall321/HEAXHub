# HEAXHub 기여 가이드 (코드베이스 개발자용)

HEAXHub **시스템 자체**(backend/frontend/인프라)를 수정·확장하는 사람을 위한 문서.
내 프로그램을 카탈로그에 *올리는* 방법은 [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)를 본다.

---

## ■ 기술 스택

| 영역 | 스택 |
|---|---|
| Backend | FastAPI · SQLAlchemy · Alembic · Celery(worker+beat) · Postgres · Redis |
| Frontend | React 18 · Vite · TanStack Router(파일 기반)/Query · Tailwind |
| 통합 실행 | Apptainer SIF(컨테이너) + Caddy(서브경로 리버스 프록시) |
| 런처 연동 | `contracts/hwax-agent/` (JSON Schema + OpenAPI 계약) |

핵심 흐름: **git repo → 스캔 → fetch(clone) → SIF 빌드 → Caddy `/apps/<slug>/` 서빙**. 상시 reconcile 루프가 라우트·인스턴스를 유지한다.

---

## ■ 레포 구조

```
HEAXHub/
├─ backend/
│  ├─ app/
│  │  ├─ api/v1/        # FastAPI 라우터 (apps, jobs, submissions, agents, installers, launcher_agents, admin, auth, ws …)
│  │  ├─ services/      # 비즈니스 로직 (integrations_scanner, integration_fetcher,
│  │  │                 #   integration_sif_builder, integration_launcher, proxy_manager,
│  │  │                 #   agent_service, app_lifecycle, audit_service …)
│  │  ├─ db/models/     # SQLAlchemy ORM
│  │  ├─ workers/       # Celery 태스크 (integration_tasks, job_tasks, build_tasks, service_tasks …)
│  │  ├─ runners/       # 잡 실행 런너
│  │  ├─ schemas/       # Pydantic 입출력 스키마
│  │  ├─ core/          # 보안/에러/로거/설정 공통
│  │  ├─ deps.py        # FastAPI 의존성 (DbSession, CurrentUser, AdminUser …)
│  │  └─ main.py        # 앱 부트스트랩 + lifespan(기동 시 reconcile 1회)
│  ├─ alembic/versions/ # DB 마이그레이션 (0001 → 0011)
│  └─ tests/            # pytest
├─ frontend/            # React + Vite (src/routes 파일 기반 라우팅)
├─ deploy/apptainer/    # 로컬/배포 기동 스크립트 (start.sh, stop.sh, install_all.sh …)
├─ integrations/        # 등록된 앱들의 manifest-only 디렉터리
├─ config/stacks.yaml   # 지원 스택 22종 정의 (단일 진실)
├─ contracts/hwax-agent/# 런처와의 계약 (스키마/OpenAPI)
├─ scripts/             # register-*.sh, watchdog.sh 등 운영 스크립트
├─ docs/                # 문서
└─ Makefile             # 모든 개발 명령의 진입점
```

---

## ■ 로컬 개발 환경

`Makefile`이 모든 명령의 진입점이다. `make help`로 목록 확인.

```bash
# 1) 의존성 설치 (backend venv + frontend node_modules)
make install

# 2) 인프라(Postgres/Redis/Caddy/MailHog) + DB 마이그레이션 + 시드
make migrate
make seed

# 3) 개발 기동
make dev          # backend + worker + beat + frontend 한 번에
#   또는 따로:
make backend      # uvicorn (FastAPI :4040)
make worker       # celery worker
make beat         # celery beat (스캐너/reconcile 스케줄)
make frontend     # vite dev (:5173)
```

전체 스택(인프라 인스턴스 포함)은 `bash deploy/apptainer/start.sh` / `stop.sh`로 띄우고 내린다.
환경변수는 `.env`(루트)에서 읽는다. backend 가상환경은 `backend/.venv`.

> Apptainer 주의: `start.sh`는 시스템 apptainer 대신 **로컬 추출본**을 우선 사용한다(시스템판은 rootless cgroup/D-Bus 문제로 인스턴스 기동 실패). 자세한 건 스크립트 상단 주석 참고.

---

## ■ 핵심 흐름의 코드 경로

| 단계 | 담당 |
|---|---|
| 5분 주기 스캔 | `workers/integration_tasks.py::scan_integrations_periodic` → `services/integrations_scanner.py` |
| git clone | `services/integration_fetcher.py` (file:// / https) → `var/integration_workspaces/<slug>/upstream` |
| SIF 빌드 | `services/integration_sif_builder.py` (stack 별 `.def` 렌더 → apptainer build, 원자적 교체) |
| 서빙(런치) | `services/integration_launcher.py` (service=프로세스+Caddy proxy, static=file_server, proxy/url/iframe) |
| Caddy 등록 | `services/proxy_manager.py` (admin API, `_ensure_spa_last`로 SPA catch-all을 항상 마지막에) |
| 자동 복구 | `workers/integration_tasks.py::reconcile_integrations` (30초 beat + `main.py` lifespan 1회) — 죽은 인스턴스 재기동 + 라우트 재등록 |

빌드 신뢰성: `build_status`/`sif_path`/`git_commit_hash`를 정직하게 기록, 실패 시 이전 SIF 보존 + 운영자 메일.

---

## ■ 새 스택 추가하기

3곳을 건드린다 (fastapi_react 추가 사례 = git에서 `535f803` 참고):

1. **`config/stacks.yaml`** — 스택 정의 추가:
   ```yaml
   my_stack:
     app_type: web_app
     launch_mode: service        # service | static | job_runner | url | iframe | proxy | installer
     entrypoint: "my-cmd --port $PORT --root-path $ROOT_PATH"
     health_path: /health
   ```
2. **`backend/app/services/sif_templates/<my_stack>.def`** — apptainer `.def` 빌드 템플릿. 기존 템플릿(`fastapi.def`, `nextjs.def`) 복사해서 시작. Node 계열은 `corepack prepare pnpm@9.15.0 --activate`로 pnpm 버전 고정 필수.
3. **(필요 시) `services/integration_launcher.py::_sif_argv_for`** — stacks.yaml의 `entrypoint`로 표현 안 되는 비표준 실행 인자만 추가. 대부분은 stacks.yaml로 충분.

검증: 데모 manifest 하나 만들어 `integrations/`에 넣고 스캔 → SIF 빌드 → `/apps/<slug>/` 200 확인.

---

## ■ DB 마이그레이션

```bash
cd backend && set -a && . ../.env && set +a
.venv/bin/alembic revision -m "설명"          # 새 리비전 (수동 작성)
.venv/bin/alembic upgrade head                # 적용
.venv/bin/alembic downgrade -1                # 롤백 (양방향 검증 권장)
```

주의점 (실제 사례):
- **Postgres enum 값 추가**는 같은 트랜잭션에서 그 값을 바로 쓸 수 없다. enum 추가(0010)와 그 값을 쓰는 시드(0011)를 별 리비전으로 분리하고, `env.py`에 `transaction_per_migration=True`를 둔다.
- 리비전 체인(`down_revision`)을 끊지 말 것. 머지 시 번호 충돌이 나면 renumber + chain 수정(예: 0008/0009 → 0010/0011).
- 컬럼 추가 시 ORM 모델(`db/models/`)도 같은 PR에서 동기화.

---

## ■ 테스트

```bash
make test                 # 단위 테스트 (backend pytest)
make test-integration     # 통합 테스트
make test-all             # 전체

# 개별 실행
cd backend && .venv/bin/pytest app/tests/test_xxx.py -x --tb=short
```

- DB 의존 테스트는 savepoint 기반 트랜잭션 fixture(`ctx`)를 쓴다 — 테스트 후 자동 롤백.
- 라이브 시스템(http://localhost:4180)에 의존하는 검증은 백엔드가 떠 있어야 한다.

---

## ■ 프론트엔드

- 라우팅은 **파일 기반**(`frontend/src/routes/*.tsx`). 새 페이지는 파일 추가 → `vite build`가 `routeTree.gen.ts` 재생성.
- `make frontend`로 dev 서버(:5173), `cd frontend && pnpm build`로 빌드(`dist/`).
- **포탈 서브경로 대응**: 프로덕션에서 `/heax-hub/` 하위로 서빙되므로 `VITE_BASE_PATH`를 통해 base/router basepath/API base가 모두 그 prefix를 받는다. `/api`·`/ws`를 하드코딩하지 말고 `import.meta.env.BASE_URL`에서 파생. 자세히는 [docs/HWAX-PORTAL-INTEGRATION.md](docs/HWAX-PORTAL-INTEGRATION.md).

---

## ■ 커밋 / PR 규약

- 메시지 스타일(실제 git log 기준): `feat(agents): ...`, `fix(installers): ...`, 또는 한 줄 요약 + 본문. 명령형, 무엇을·왜.
- 브랜치: `main`에 직접 push 금지 — 기능별 브랜치 → PR. (현재 main이 기본)
- **런처 계약(`contracts/hwax-agent/`) 변경 시**: 스키마/OpenAPI를 함께 수정하고 [docs/hwax-agent-pr-protocol.md](docs/hwax-agent-pr-protocol.md)의 양방향 PR 절차를 따른다. 계약은 런처 레포와 공유하는 단일 진실.

---

## ■ 운영 주의사항

| 항목 | 내용 |
|---|---|
| cae00(프로덕션) 빌드 불가 | 사내 TLS 차단망이라 npm/Docker Hub 접근 불가. dist를 온라인에서 빌드해 Google Drive(rclone)로 운반(`deploy/apptainer/dist-to-drive.sh` → `dist-from-drive.sh`). `HEAX_NO_BUILD=1`이면 빌드 시도 거부. 상세: [docs/HWAX-PORTAL-INTEGRATION.md](docs/HWAX-PORTAL-INTEGRATION.md), [AGENTS.md](AGENTS.md). |
| Caddy 라우트 영속성 | admin API 메모리 전용이라 재시작 시 휘발. reconcile 루프(30초) + lifespan 1회 복원이 자동 처리. 수동: `POST /api/v1/admin/integrations/proxy-sync`. |
| watchdog | `scripts/watchdog.sh`가 인프라 인스턴스 헬스를 감시. 오탐 방지를 위해 재검증+백오프 적용됨 — 헬스체크 로직 수정 시 false positive 주의. |

---

관련 문서: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/API_REFERENCE.md](docs/API_REFERENCE.md) · [docs/RUNBOOK.md](docs/RUNBOOK.md) · [docs/PIPELINE_ROADMAP.md](docs/PIPELINE_ROADMAP.md) (남은 개선 항목).
