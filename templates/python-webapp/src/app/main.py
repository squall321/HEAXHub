"""Minimal FastAPI app for the HEAXHub python-webapp template."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI

app = FastAPI(title="my_python_webapp", version="0.1.0")


@app.get("/")
def index() -> dict:
    return {
        "service": "my_python_webapp",
        "message": "hello from HEAXHub python-webapp template",
        "job_id": os.environ.get("JOB_ID", "(local)"),
        "now": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
