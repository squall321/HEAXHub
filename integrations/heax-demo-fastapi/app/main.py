"""HEAXHub FastAPI demo app.

Caddy 가 /apps/{id}/ 를 base path 로 잡고 reverse proxy 하기 때문에,
실행 시 --root-path $ROOT_PATH 로 받은 값이 app.root_path 가 된다.
Swagger UI / OpenAPI 경로도 root_path 기준으로 자동 보정된다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import count
from typing import Dict

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

app = FastAPI(
    title="heax-demo-fastapi",
    description="HEAXHub FastAPI 스택 데모용 간단 메모 CRUD API.",
    version="0.1.0",
)


class MemoIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field("", max_length=10_000)


class MemoOut(MemoIn):
    id: int
    created_at: str


_memos: Dict[int, MemoOut] = {}
_id_seq = count(1)


@app.get("/")
def root() -> dict:
    return {"hello": "heax-demo-fastapi", "root_path": app.root_path}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/memos", response_model=MemoOut, status_code=status.HTTP_201_CREATED)
def create_memo(memo: MemoIn) -> MemoOut:
    new_id = next(_id_seq)
    created = MemoOut(
        id=new_id,
        title=memo.title,
        body=memo.body,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _memos[new_id] = created
    return created


@app.get("/memos", response_model=list[MemoOut])
def list_memos() -> list[MemoOut]:
    return list(_memos.values())


@app.get("/memos/{memo_id}", response_model=MemoOut)
def get_memo(memo_id: int) -> MemoOut:
    memo = _memos.get(memo_id)
    if memo is None:
        raise HTTPException(status_code=404, detail="memo not found")
    return memo


@app.delete("/memos/{memo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memo(memo_id: int) -> Response:
    if memo_id not in _memos:
        raise HTTPException(status_code=404, detail="memo not found")
    del _memos[memo_id]
    return Response(status_code=status.HTTP_204_NO_CONTENT)
