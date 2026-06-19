"""Minimal FastAPI app for the HEAXHub python-webapp template.

서브경로 오케스트레이션 규약
---------------------------
HEAXHub는 이 앱을 ``/apps/<slug>/`` 서브경로 뒤에서 서빙한다. 런처가 실행 시
``$ROOT_PATH`` (= ``/apps/<slug>``) 와 ``$PORT`` 를 주입한다. uvicorn 진입점이
``--root-path $ROOT_PATH`` 로 그 값을 받으므로(.portal/run.sh 참고), FastAPI는
``app.root_path`` 를 자동으로 인식해 Swagger/OpenAPI/리다이렉트 경로를 서브경로
기준으로 보정한다. 즉 라우트는 ``/`` 기준으로 짜되, 절대경로 링크를 직접 만들 때만
``root_path`` 를 앞에 붙이면 된다. 고정 포트를 쓰지 말 것 — 항상 ``$PORT`` 로 listen.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, Request

# root_path 는 uvicorn --root-path 로도 들어오지만, 코드에서도 환경변수를 읽어
# 명시적으로 넘겨 두면 로컬 단독 실행/다른 ASGI 서버에서도 동일하게 동작한다.
ROOT_PATH = os.environ.get("ROOT_PATH", "")

app = FastAPI(title="my_python_webapp", version="0.1.0", root_path=ROOT_PATH)


@app.get("/")
def index(request: Request) -> dict:
    return {
        "service": "my_python_webapp",
        "message": "hello from HEAXHub python-webapp template",
        "job_id": os.environ.get("JOB_ID", "(local)"),
        # root_path 가 비어있지 않으면 서브경로 뒤에서 서빙되고 있다는 뜻.
        "root_path": request.scope.get("root_path", ""),
        "now": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
