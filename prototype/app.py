"""本地伺服器：serve dist/ 靜態頁 + /api/deadline（Python 引擎，與頁內 JS 引擎同測試集鎖定）。

跑法：uv run --with fastapi --with uvicorn app.py  → http://127.0.0.1:8787
"""
from __future__ import annotations

import datetime as dt
import pathlib

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine.deadline import compute

ROOT = pathlib.Path(__file__).parent
app = FastAPI(title="訴願決定書 AI 輔助撰擬系統", docs_url="/api/docs")


class DeadlineIn(BaseModel):
    method: str
    service: dt.date
    filing: dt.date | None = None
    transit: int = 0
    interested: bool = False


@app.post("/api/deadline")
def api_deadline(body: DeadlineIn) -> dict:
    return compute(
        service_method=body.method,
        service_date=body.service,
        filing_date=body.filing,
        transit_days=body.transit,
        interested_party=body.interested,
    ).as_dict()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "dist" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "dist"), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787)
