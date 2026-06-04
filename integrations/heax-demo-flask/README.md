# heax-demo-flask

HEAXHub Flask + gunicorn 서비스 데모.

## 라우트

- `GET /` — `{"hello": "flask"}` 형태의 데모 응답 (Jinja 템플릿 렌더링 포함)
- `GET /health` — 헬스 체크. `200 {"status": "ok"}`
- `POST /echo` — 폼 입력 에코
- `GET /api/info` — Python / Flask 버전 정보

## 로컬 실행

```bash
pip install -e .
gunicorn app.main:app --bind 0.0.0.0:8000
```

또는 개발용:

```bash
python -m app.main
```

## HEAXHub launch

`.portal/manifest.yaml` 의 `launch.command` 가 gunicorn 으로 서비스 모드로 기동합니다.
헬스 체크는 `/health` 경로를 사용합니다.
