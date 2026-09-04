"""FastAPI 服務：把六節點流程掛成 HTTP 端點。

跑法（沿用 v0 的慣例，見 prototype/app.py）：

    uv run --with fastapi --with uvicorn backend/api/app.py    → http://127.0.0.1:8788

或容器內：

    uvicorn backend.api.app:app --host 0.0.0.0 --port 8080

端點（architecture §6.1 的子集，Phase 0 先做四支）：

| 方法 | 路徑                        | 說明                                   |
|------|-----------------------------|----------------------------------------|
| GET  | `/api/health`               | 健康檢查：run_mode／kb 狀態／model ids |
| GET  | `/api/cases`                | 列出可用的合成案例                     |
| POST | `/api/cases/{case_id}/runs` | 跑完六節點，回完整 CASE payload        |
| POST | `/api/deadline`             | 期間計算（沿用 prototype/app.py 的契約）|

紅線：
- 前端不得直連基礎模型，一律過後端（CONSTITUTION 約束二）。
- 本檔不讀任何憑證、不呼叫任何雲端 API。`RUN_MODE != "fixture"` 時節點會自己 raise，
  這裡把它翻成 501 並照實說原因，不假裝服務正常。
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend.config.settings import PROVENANCE, run_mode  # noqa: E402
from backend.engine.deadline import compute  # noqa: E402
from backend.orchestrator.graph import (  # noqa: E402
    CaseNotFound,
    build_payload,
    list_synthetic_cases,
    run_case,
)

app = FastAPI(
    title="訴願案件審理 AI 輔助（Phase 0 backend）",
    description="六節點 deterministic pipeline。目前僅支援 RUN_MODE=fixture（離線重播）。",
    docs_url="/api/docs",
)


class DeadlineIn(BaseModel):
    """沿用 prototype/app.py:21-27 的契約，欄位名不改。"""

    method: str
    service: dt.date
    filing: dt.date | None = None
    transit: int = 0
    interested: bool = False


@app.get("/api/health")
def health() -> dict:
    mode = run_mode()
    return {
        "ok": True,
        "run_mode": mode,
        "fixture_only": mode == "fixture",
        "kb_backend": "lawtable_only",
        "similar_case_backend": "unavailable",
        # 沒有呼叫任何基礎模型就不報 model id
        "model_ids": None,
        "model_ids_note": "fixture 檔位未呼叫任何基礎模型。",
        "cases_available": list_synthetic_cases(),
        "provenance": PROVENANCE,
    }


@app.get("/api/cases")
def cases() -> dict:
    return {"cases": list_synthetic_cases(), "note": "只提供合成測資，真實競賽資料不由本服務讀取。"}


@app.post("/api/cases/{case_id}/runs")
def create_run(case_id: str) -> dict:
    """啟動狀態機並同步跑完六節點，回完整 CASE payload。

    Phase 0 是同步執行（fixture 檔位全程毫秒級，沒有阻塞疑慮）。
    接上真實模型後要改成 202 + SSE 事件流（architecture §6.1 的 2a/2b）。
    """
    try:
        state = run_case(case_id)
    except CaseNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except AssertionError as e:
        # 不變式違反是 P0，照實回 500 並帶原因，不吞掉
        raise HTTPException(status_code=500, detail=f"不變式違反（P0）：{e}") from e

    payload = build_payload(state)
    if payload["origin_violations"]:
        raise HTTPException(status_code=500, detail={"origin_violations": payload["origin_violations"]})
    return payload


@app.post("/api/deadline")
def api_deadline(body: DeadlineIn) -> dict:
    try:
        return compute(
            service_method=body.method,
            service_date=body.service,
            filing_date=body.filing,
            transit_days=body.transit,
            interested_party=body.interested,
        ).as_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8788)
