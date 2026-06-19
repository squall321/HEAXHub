# fastapi-react 템플릿 (Vite+React + FastAPI 풀스택)

HEAXHub에 등록할 **TypeScript Vite+React 프론트엔드 + FastAPI 백엔드** 풀스택 앱의 기본 양식.
하나의 SIF로 빌드되어 `/apps/<slug>/` 서브경로에서 서빙되며, **서브경로가 무엇이든 자산·API가 깨지지 않는** 가장 견고한 구성이다.

## 왜 안 깨지나 — 상대경로 전략 (핵심)

서브경로 오케스트레이션에서 정적 자산이 깨지는 흔한 원인은 자산/API URL이 절대경로(`/assets/...`, `/api/...`)로 박히는 것이다. 이 템플릿은 전부 **상대경로**로 풀어 그 문제를 원천 차단한다:

| 위치 | 전략 | 효과 |
|---|---|---|
| `frontend/vite.config.ts` | `base: "./"` | 번들된 JS/CSS 자산 URL이 전부 상대경로 → 서브경로 prefix 무관 |
| `frontend/src/api.ts` | `fetch("api/tasks")` (선행 슬래시 없음) | 현재 페이지 기준 상대 → 자동으로 `/apps/<slug>/api/tasks` |
| `backend/app/main.py` | `StaticFiles`를 `/`에 마운트, `/api/*`를 그 **앞에** 선언 | 같은 origin·같은 프로세스에서 SPA + API 동시 서빙 |

런처가 `$PORT`/`$ROOT_PATH`를 주입하고 `fastapi_react` 스택이 `pnpm build`(프론트) + `pip install`(백엔드)를 한 SIF로 묶는다. FastAPI는 `--root-path $ROOT_PATH`를 받아 OpenAPI/리다이렉트만 보정하면 되고, 자산·fetch는 이미 상대경로라 손댈 게 없다.

## 디렉터리 구조

```
fastapi-react/
├─ README.md
├─ .gitignore
├─ .portal/
│   └─ manifest.yaml           # build.stack=fastapi_react, launch.mode=service
├─ backend/
│   ├─ pyproject.toml          # fastapi + uvicorn
│   └─ app/main.py             # /api/* + frontend/dist StaticFiles 마운트
└─ frontend/
    ├─ package.json            # react + vite + typescript
    ├─ pnpm-lock.yaml          # frozen install (재현성)
    ├─ .npmrc                  # shamefully-hoist (vite 의 esbuild/rollup 해결)
    ├─ vite.config.ts          # base: "./"
    ├─ tsconfig*.json
    ├─ index.html
    └─ src/{main.tsx, App.tsx, api.ts, index.css}
```

## 로컬 개발

프론트와 백엔드를 따로 띄워 개발한다:

```bash
# 백엔드 (터미널 1)
cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -e .
uvicorn app.main:app --reload --port 8000

# 프론트 (터미널 2) — vite dev 가 /api 를 백엔드로 프록시하도록 설정하거나,
# 빌드 후 백엔드의 StaticFiles 로 통합 확인
cd frontend && pnpm install && pnpm dev
```

통합(서브경로) 동작을 한 번에 확인하려면:

```bash
cd frontend && pnpm install && pnpm build      # → frontend/dist
cd ../backend && . .venv/bin/activate
PORT=8000 ROOT_PATH=/apps/my_fullstack_app \
  uvicorn app.main:app --port $PORT --root-path $ROOT_PATH
# http://localhost:8000/apps/my_fullstack_app/ 에서 SPA + /api/* 동작
```

## 포탈 등록

```bash
scripts/register-repo.sh my-fullstack-app <git-url> fastapi_react
#   → clone → pnpm build + pip install (한 SIF) → /apps/my_fullstack_app/ 서빙
```

`health_check.path` (`/api/health`) 가 200을 주면 서빙이 시작된다.

## 내 앱으로 바꾸기

- **API 추가**: `backend/app/main.py`에서 `/api/*` 라우트 추가 (반드시 StaticFiles 마운트보다 위에).
- **화면**: `frontend/src/App.tsx` 수정. 새 API 호출은 `frontend/src/api.ts`에 **상대경로**로 추가(`fetch("api/...")`, 선행 슬래시 금지).
- **이름**: `manifest.yaml`의 `id`/`name`, `backend/pyproject.toml`, `frontend/package.json`의 name.
- lockfile(`pnpm-lock.yaml`)은 의존성 바꿀 때 `pnpm install`로 갱신해 함께 커밋.

## 엔드포인트 (예시 — Task CRUD)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/health` | 헬스체크 200 |
| GET | `/api/tasks` | 목록 |
| POST | `/api/tasks` | 생성 |
| PATCH | `/api/tasks/{id}` | 수정(완료 토글) |
| GET | `/` 외 | React SPA (frontend/dist) |
