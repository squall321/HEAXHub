# python-webapp 템플릿

HEAXHub에 등록할 **상시 운영 웹 서비스 (web_app)** 의 기본 양식이다.
FastAPI + uvicorn 기반의 가벼운 예시이며, 한 번 빌드되면 포탈은 이 앱을 백그라운드 서비스로 띄운 뒤 사용자에게 URL을 안내한다.

## 디렉터리 구조

```
python-webapp/
├─ README.md
├─ pyproject.toml         # fastapi + uvicorn
├─ src/app/main.py        # 최소 hello 엔드포인트
├─ .portal/
│   ├─ manifest.yaml      # app_type=web_app, launch.mode=url
│   └─ run.sh             # uvicorn 백그라운드 기동 + 접속 URL 기록
└─ .gitignore
```

## 로컬 개발 흐름

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

# 직접 띄우기
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

# 포탈과 동일한 진입점
bash .portal/run.sh /tmp/input /tmp/output /tmp/params.json
# → /tmp/output/result.json 에 url 이 기록됨
```

## 운영 모드

- `app_type: web_app` + `execution_target: linux_runner`
- `launch.mode: url` — 포탈은 사용자에게 카드 클릭 시 "열기" 버튼을 노출
- 포탈은 `run.sh` 가 출력한 `result.json.outputs.url` 을 잡아서 사용자에게 안내한다.
- 장시간 동작하는 서비스이므로 systemd / supervisord 등록을 권장 (배포 시 별도 안내).

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/` | 인덱스 (hello world) |
| GET | `/health` | 헬스체크. 200/OK 반환 |
