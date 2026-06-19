"""HEAXHub fastapi_react demo backend.

FastAPI 가 /api/* JSON 엔드포인트를 노출하고, 그 뒤에 Vite/React 빌드 산출물
(frontend/dist/) 을 StaticFiles 로 "/" 에 마운트한다. FastAPI 는 등록 순서대로
라우트를 매칭하므로 /api/* 가 반드시 정적 마운트보다 먼저 선언되어야 한다.

Caddy 가 /apps/<slug>/ 를 base path 로 잡고 reverse proxy 하기 때문에,
uvicorn 실행 시 --root-path $ROOT_PATH 가 주입되어 app.root_path 에 들어간다.
프런트엔드는 모든 fetch URL 을 상대경로("api/tasks")로 호출하고, Vite 도
base: "./" 로 빌드해 자산 URL 을 상대화하므로 서브패스 마운트가 그대로 동작한다.
"""

from __future__ import annotations

from itertools import count
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

app = FastAPI(
    title="my-fullstack-app",
    description="HEAXHub fastapi_react 스택 데모. TS+Vite+React + FastAPI 풀스택.",
    version="0.1.0",
)


class TaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class TaskPatch(BaseModel):
    done: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)


class TaskOut(BaseModel):
    id: int
    title: str
    done: bool


_tasks: Dict[int, TaskOut] = {}
_id_seq = count(1)


# ── /api/* 는 정적 마운트보다 먼저 선언 ──────────────────────────────────────


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/tasks", response_model=list[TaskOut])
def list_tasks() -> list[TaskOut]:
    return list(_tasks.values())


@app.post("/api/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskIn) -> TaskOut:
    new_id = next(_id_seq)
    task = TaskOut(id=new_id, title=payload.title, done=False)
    _tasks[new_id] = task
    return task


@app.patch("/api/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, patch: TaskPatch) -> TaskOut:
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if patch.done is not None:
        task = task.model_copy(update={"done": patch.done})
    if patch.title is not None:
        task = task.model_copy(update={"title": patch.title})
    _tasks[task_id] = task
    return task


# ── 정적 프런트엔드 마운트 ───────────────────────────────────────────────────
# 빌드 산출물 경로는 소스 트리에서 실행하든 SIF 내부에서 실행하든 동일하게
# backend/app/main.py 기준으로 ../../frontend/dist 를 가리킨다.

_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(_FRONTEND_DIST), html=True),
        name="frontend",
    )
