# heax-demo-fastapi-react

HEAXHub `fastapi_react` 스택 데모 — TypeScript + Vite + React 프런트엔드와
FastAPI 백엔드를 단일 SIF 로 묶어 서비스한다.

## 무엇을 보여주는가

- 같은 origin 으로 SPA + JSON API 가 동시에 제공된다 (CORS 불필요).
- Caddy 가 `/apps/heax_demo_fastapi_react/` 를 base path 로 프록시해도
  Vite 의 `base: "./"` + 프런트의 상대경로 fetch + FastAPI 의
  `--root-path $ROOT_PATH` 조합으로 서브패스 마운트가 그대로 동작한다.
- 빌드 결과(`frontend/dist/`) 를 FastAPI `StaticFiles` 가 마운트하므로
  런타임에는 Python 만 필요하다.

## 구성

- 프런트엔드: `frontend/` (React 18, Vite 5, TypeScript 5)
- 백엔드: `backend/app/main.py` (FastAPI, in-memory tasks CRUD)
- 매니페스트: `.portal/manifest.yaml` — `build.stack: fastapi_react`

## 진입

브라우저에서 `https://<heaxhub-host>/apps/heax_demo_fastapi_react/` 로 접속하면
React UI 가 뜨고, 같은 origin 의 `api/tasks` 를 통해 할 일 CRUD 를 수행한다.

소스 코드는 `var/local-demo-repos/heax-demo-fastapi-react.git` 의 `main` 브랜치
에 있다 (HEAXHub 통합 스캐너가 자동으로 동기화).
