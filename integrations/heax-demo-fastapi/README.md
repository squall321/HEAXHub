# heax-demo-fastapi

HEAXHub FastAPI 스택 데모. 간단 메모 CRUD API + Swagger UI 를 제공한다.

## 구조

- `app/main.py` — FastAPI 앱 본체. 메모리 dict 기반 메모 CRUD.
- `.portal/manifest.yaml` — HEAXHub 포털 매니페스트 (schema v2).
- `pyproject.toml` — fastapi, uvicorn[standard] 의존성 선언.

## 엔드포인트

- `GET /` → 헬로 메시지와 현재 `root_path` 반환
- `GET /health` → 헬스체크
- `POST /memos` → 메모 생성. body: `{title, body}`
- `GET /memos` → 메모 목록
- `GET /memos/{id}` → 단건 조회 (없으면 404)
- `DELETE /memos/{id}` → 삭제 (성공 시 204)

## 로컬 실행

```bash
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Swagger UI: <http://localhost:8000/docs>

## HEAXHub 에서의 실행

매니페스트의 `launch.command` 가 아래와 같이 정의되어 있어,
HEAXHub 런너가 `$PORT` 와 `$ROOT_PATH` 를 주입한다.

```
uvicorn app.main:app --host 0.0.0.0 --port $PORT --root-path $ROOT_PATH
```

Caddy 가 `/apps/{id}/` 를 base path 로 잡고 reverse proxy 하므로,
`--root-path` 덕분에 Swagger UI / OpenAPI 경로도 자동 보정된다.
