# python-webapp 템플릿

HEAXHub에 등록할 **상시 운영 웹 서비스 (web_app, service 모드)** 의 기본 양식이다.
FastAPI + uvicorn 기반이며, 포탈이 이 앱을 상시 프로세스로 띄우고 **`/apps/<slug>/` 서브경로**로 리버스 프록시해 하나의 웹으로 합친다.

## 서브경로 오케스트레이션 (핵심)

포탈은 앱을 자기 도메인의 `/apps/<slug>/` 뒤에 둔다. 충돌 없이 합쳐지려면 두 가지 규약만 지키면 되고, 이 템플릿은 그걸 코드로 박아 두었다:

| 주입(자동) | 앱이 해야 할 일 (템플릿에 반영됨) |
|---|---|
| `$PORT` | 그 포트로 listen — 고정 포트 금지. (`run.sh` / manifest command) |
| `$ROOT_PATH` (= `/apps/<slug>`) | `--root-path $ROOT_PATH` 로 받기. FastAPI가 `app.root_path`로 인식해 Swagger·OpenAPI·리다이렉트를 서브경로 기준으로 자동 보정. (`src/app/main.py`) |

→ 라우트는 평소처럼 `/` 기준으로 짜면 되고, 절대경로 링크를 직접 만들 때만 `request.scope["root_path"]`를 앞에 붙인다.

## 디렉터리 구조

```
python-webapp/
├─ README.md
├─ pyproject.toml         # fastapi + uvicorn
├─ src/app/main.py        # hello + /health, root_path 인식 내장
├─ .portal/
│   ├─ manifest.yaml      # build.stack=fastapi, launch.mode=service, $PORT/$ROOT_PATH
│   └─ run.sh             # 포어그라운드 uvicorn (편의 진입점)
└─ .gitignore
```

## 로컬 개발

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 단독 실행 (서브경로 없음 — root_path 빈 값)
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

# 서브경로 흉내 (포탈과 동일)
PORT=8080 ROOT_PATH=/apps/my_python_webapp bash .portal/run.sh
#   → http://localhost:8080/apps/my_python_webapp/docs 에서 Swagger 가 서브경로로 뜸
```

## 포탈 등록

```bash
scripts/register-repo.sh my-webapp <git-url> fastapi
#   → clone → SIF 빌드 → /apps/my_python_webapp/ 서빙
```

`health_check.path` (`/health`) 가 200을 주면 서빙이 시작된다.

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/` | hello + 현재 `root_path` 반환 (서브경로 확인용) |
| GET | `/health` | 헬스체크 200/OK |
