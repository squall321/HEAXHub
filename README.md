# HEAXHub

사내 흩어진 자동화 프로그램을 한 곳에서 검색·실행·관리하는 통합 포탈.

- 운영 표준안: [ai_automation_portal_standard.html](./ai_automation_portal_standard.html)
- 쉬운 설명: [ai_automation_portal_easy.html](./ai_automation_portal_easy.html)
- 개발 계획서: [PROJECT_PLAN.md](./PROJECT_PLAN.md)

## 빠른 시작

### 개발 모드 (로컬 docker compose 단축키)

> 운영은 Apptainer 인스턴스로 돌린다(아래 § "운영 모드"). 이 섹션은 로컬 개발자가
> postgres/redis/mailhog만 빠르게 띄우기 위한 편의 경로다. compose 파일은
> [`deploy/dev-host/docker-compose.yml`](./deploy/dev-host/docker-compose.yml)이며,
> 포트는 Apptainer 운영 스택(5732/6479/8125/8126)과 일치시켜 `.env` 변경 없이
> 두 모드를 오갈 수 있다.

```bash
# 1. 인프라 (postgres + redis + mailhog) 띄우기
make docker-up

# 2. 백엔드 의존성 설치 + 마이그레이션 + 초기 admin 생성
cp .env.example .env
cd backend && pip install -e ".[dev]"
make migrate
make seed     # 내부적으로 backend/scripts/create_admin.py 실행

# 3. 프론트엔드 의존성 설치
cd ../frontend && pnpm install

# 4. 개별 터미널에서 실행
make backend    # FastAPI :4040  (vite proxy 대상)
make frontend   # Vite     :4173
make worker     # Celery worker
make beat       # Celery beat (스케줄러)
```

- 프론트엔드: <http://localhost:4173>
- 백엔드 API: <http://localhost:4040>
- 초기 관리자 계정: `.env`의 `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`
- 수동으로 admin 재생성이 필요할 때: `cd backend && .venv/bin/python -m scripts.create_admin`

### 운영 모드 (Apptainer + Caddy)

오프라인/사내 배포는 SIF 인스턴스로 띄운다. 자세한 launch 시퀀스는
[`deploy/apptainer/start.sh`](./deploy/apptainer/start.sh) 참고.

```bash
deploy/apptainer/start.sh      # postgres/redis/mailhog/caddy + backend/worker/beat/frontend 기동
deploy/apptainer/stop.sh       # 전체 종료
```

- 통합 진입점 (Caddy): <http://localhost:4180>
- 메일 확인용 MailHog UI: <http://localhost:8126> (SMTP는 :8125)

## 디렉터리 구조

상세는 [PROJECT_PLAN.md §3](./PROJECT_PLAN.md) 참고.

```text
HEAXHub/
├─ frontend/            React + Vite + TS + shadcn/ui
├─ backend/             FastAPI + Celery
├─ app_workspaces/      등록된 앱마다 한 폴더 (clone + venv/SIF)
├─ job_storage/         실행 결과 (job_id 단위)
├─ templates/           신규 앱 기본 양식
├─ schemas/             manifest/params/result JSON Schema
├─ scripts/             빌드·운영 스크립트
└─ deploy/              apptainer/ (운영), dev-host/ (개발용 docker compose), systemd/ 유닛
```

## 인증 모드

- **1단계 (현재)**: 자체 회원가입 — 이름·조직·이메일·비밀번호
- **2단계 (추후)**: 사내 SSO 연동, 이메일을 키로 기존 계정 매핑

## 라이선스

내부 사용.
