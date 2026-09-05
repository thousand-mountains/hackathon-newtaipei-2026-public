"""FastAPI 服務：一個 process 同時 serve 五步動線前端與六節點 API。

跑法（**唯一的官方啟動指令**，見 `backend/DEPLOY.md`）：

    uv run --with fastapi --with "uvicorn[standard]" --with pydantic -- \
        python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8080

    → http://127.0.0.1:8080/   五步動線 UI（live 接後端）
    → http://127.0.0.1:8080/api/docs   OpenAPI

或容器內：

    uvicorn backend.api.app:app --host 0.0.0.0 --port 8080

端點（architecture §6.1 的子集）：

| 方法 | 路徑                        | 說明                                       |
|------|-----------------------------|--------------------------------------------|
| GET  | `/`                         | 五步動線 UI（`prototype/dist/index.html`） |
| GET  | `/api/health`               | 健康檢查：**實際**載入快照與合成案例       |
| GET  | `/api/cases`                | 列出可用的合成案例                         |
| POST | `/api/cases/{case_id}/runs` | 跑完六節點，回 `run_id` + 完整 CASE payload |
| POST | `/api/cases/{case_id}/submit` | 送出審議：後端重算後 200／409（§6.1 #9）  |
| POST | `/api/deadline`             | 期間計算（沿用 prototype/app.py 的契約）    |

紅線：
- 前端不得直連基礎模型，一律過後端（CONSTITUTION 約束二）。
- 本檔不讀任何憑證、不呼叫任何雲端 API。`RUN_MODE != "fixture"` 時節點會自己 raise，
  這裡把它翻成 501 並照實說原因，不假裝服務正常。
- `/api/health` 不得無條件回 ok：它必須真的把 laws-snapshot 與合成案例讀起來，
  讀不動就照實回 503。健康檢查說謊比沒有健康檢查更糟。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.config.settings import PROVENANCE, load_snapshot, run_mode  # noqa: E402
from backend.engine.deadline import compute  # noqa: E402
from backend.orchestrator.graph import (  # noqa: E402
    CaseNotFound,
    build_payload,
    list_synthetic_cases,
    load_case,
    run_case,
)

# 五步動線前端的建置產物。由 `python3 prototype/build.py` 產生（單檔全內嵌）。
FRONTEND_DIST = ROOT / "prototype" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"

app = FastAPI(
    title="訴願案件審理 AI 輔助（v2 六節點 + 五步動線）",
    description="六節點 deterministic pipeline，同一個 process 也 serve 五步動線前端。目前僅支援 RUN_MODE=fixture（離線重播）。",
    docs_url="/api/docs",
)

# 本機開發用 CORS：只放行 localhost／127.0.0.1 的任意 port。
# 不用 allow_origins=["*"]——那會讓任何網站都能打這支 API。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class RunIn(BaseModel):
    """`POST /runs` 的選填 body（判斷卡 7）。

    `confirmed_intake` 裡的欄位代表**承辦人在收文頁看過**（可能改過、也可能原樣採用）。
    這些欄位的 `intake_origin` 會記成 `human`，而且只有它們齊全時，
    期間結果才可以用來解除結論封鎖。不給 body ＝ 沒有人確認過。
    """

    confirmed_intake: dict[str, Any] | None = None


class DeadlineIn(BaseModel):
    """沿用 prototype/app.py:21-27 的契約，欄位名不改。"""

    method: str
    service: dt.date
    filing: dt.date | None = None
    transit: int = 0
    interested: bool = False


def _health_checks() -> list[dict]:
    """真的去做的檢查——每一項都實際讀檔／解析，不是回報一個常數。

    回傳每項的 `{name, ok, detail}`。任何一項 not ok，`/api/health` 就回 503。
    """
    checks: list[dict] = []

    # 1. 引用驗證的唯一真實來源必須載得起來（architecture §8.1）
    try:
        snap = load_snapshot()
        laws = snap.get("laws") or {}
        if not laws:
            raise ValueError("laws-snapshot.json 的 laws 區塊是空的")
        checks.append(
            {
                "name": "laws_snapshot",
                "ok": True,
                "detail": f"{len(laws)} 部法規、快照日期 {snap.get('generated')}",
            }
        )
    except Exception as e:  # noqa: BLE001 — 健康檢查要照實回報任何失敗原因
        checks.append({"name": "laws_snapshot", "ok": False, "detail": f"{type(e).__name__}: {e}"})

    # 2. 合成案例必須列得出來、而且每一份都真的解析得動
    try:
        case_ids = list_synthetic_cases()
        if not case_ids:
            raise ValueError("找不到任何 synthetic-*.json 合成案例")
        for cid in case_ids:
            load_case(cid)  # 解析失敗會直接丟出來
        checks.append({"name": "synthetic_cases", "ok": True, "detail": f"{len(case_ids)} 個：{', '.join(case_ids)}"})
    except Exception as e:  # noqa: BLE001
        case_ids = []
        checks.append({"name": "synthetic_cases", "ok": False, "detail": f"{type(e).__name__}: {e}"})

    # 3. 前端建置產物在不在（不在也還能跑 API，所以這項失敗不擋整體 ok）
    checks.append(
        {
            "name": "frontend_dist",
            "ok": FRONTEND_INDEX.exists(),
            "detail": str(FRONTEND_INDEX.relative_to(ROOT)) if FRONTEND_INDEX.exists() else "未建置，請跑 python3 prototype/build.py",
            "blocking": False,
        }
    )
    return checks


@app.get("/api/health")
def health() -> JSONResponse:
    mode = run_mode()
    checks = _health_checks()
    ok = all(c["ok"] for c in checks if c.get("blocking", True))
    case_ids = list_synthetic_cases() if ok else []
    body = {
        "ok": ok,
        "checks": checks,
        "run_mode": mode,
        "fixture_only": mode == "fixture",
        "kb_backend": "lawtable_only",
        "similar_case_backend": "unavailable",
        # 沒有呼叫任何基礎模型就不報 model id
        "model_ids": None,
        "model_ids_note": "fixture 檔位未呼叫任何基礎模型。",
        "cases_available": case_ids,
        "frontend_served": FRONTEND_INDEX.exists(),
        "provenance": PROVENANCE,
    }
    return JSONResponse(body, status_code=200 if ok else 503)


@app.get("/api/cases")
def cases() -> dict:
    return {"cases": list_synthetic_cases(), "note": "只提供合成測資，真實競賽資料不由本服務讀取。"}


@app.post("/api/cases/{case_id}/runs")
def create_run(case_id: str, body: RunIn | None = None) -> dict:
    """啟動狀態機並同步跑完六節點，回 `run_id` + 完整 CASE payload。

    architecture §6.1 的 2a 規定回 `{run_id}`；Phase 0 是同步執行
    （fixture 檔位全程毫秒級，沒有阻塞疑慮），所以把完整 payload 一起回，
    前端不必再打一次 `GET /api/cases/{id}`。`run_id` 在 payload 頂層與 `run_meta` 各有一份。
    接上真實模型後要改成 202 + SSE 事件流（architecture §6.1 的 2b）。
    """
    try:
        state = run_case(case_id, confirmed_intake=(body.confirmed_intake if body else None))
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


# 送出紀錄。**本機檔案，沒有任何外部整合**——沒有寄信、沒有排議程、沒有打任何外部系統。
# 這件事必須寫在回應裡（`external_effect: "none"`），因為 UI 上「已送出」四個字
# 很容易被讀成「已經送到訴願審議委員會了」。
SUBMISSION_LOG = ROOT / "backend" / "output" / "submissions.jsonl"


@app.post("/api/cases/{case_id}/submit")
def submit_case(case_id: str, body: RunIn | None = None) -> JSONResponse:
    """送出審議（architecture §6.1 #9）。

    **後端自己重跑一次六節點再判斷，完全不信前端送來的 `submit_allowed`。**
    前端的送出鈕只是一個按鈕，改 DOM 或直接打這支 API 都繞得過它；
    唯一有意義的守門點是這裡。這也是「已標記為不得逕行送出」能不能改口說成
    「後端會拒絕」的前提——在這支端點接上之前，那句話是不實的。

    - 阻擋 → **409** ＋ 完整 blockers（要說得出為什麼擋，不能只回一個 false）
    - 允許 → 200 ＋ 本機收據。收據裡明寫 `external_effect: "none"`。
    """
    try:
        state = run_case(case_id, confirmed_intake=(body.confirmed_intake if body else None))
    except CaseNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except AssertionError as e:
        raise HTTPException(status_code=500, detail=f"不變式違反（P0）：{e}") from e

    payload = build_payload(state)
    common = {
        "case_id": case_id,
        "run_id": payload["run_id"],
        "recomputed_by": "backend",
        "recompute_note": "本回應的 submit_allowed 由後端重跑六節點得出，未採信前端送來的任何判斷。",
        "submit_allowed": payload["submit_allowed"],
        "blockers": payload["blockers"],
        "lamp_stats": payload["lamp_stats"],
        "intake_confirmed": payload["intake_confirmed"],
    }
    if not payload["submit_allowed"]:
        return JSONResponse({**common, "accepted": False}, status_code=409)

    receipt = {
        **common,
        "accepted": True,
        "recorded_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "external_effect": "none",
        "external_effect_note": (
            "本 demo 只把這筆紀錄寫進本機檔案，沒有任何外部整合："
            "沒有寄送任何郵件、沒有排入任何議程、沒有呼叫任何外部系統。"
        ),
        "record_path": str(SUBMISSION_LOG.relative_to(ROOT)),
    }
    try:
        SUBMISSION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SUBMISSION_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, ensure_ascii=False) + "\n")
    except OSError as e:  # 寫不進去就照實說，不要回一個假的成功
        return JSONResponse(
            {**common, "accepted": False, "record_error": f"{type(e).__name__}: {e}"},
            status_code=500,
        )
    return JSONResponse(receipt, status_code=200)


def _roc(d: dt.date | None) -> str:
    """西元 → 民國。純算術，不涉判斷。"""
    return f"{d.year - 1911}年{d.month}月{d.day}日" if d else "—"


def _deadline_verdict(result: dict, body: DeadlineIn) -> dict:
    """期間判定的燈號與說法（origin=rule）。

    **為什麼燈號要由後端給**：前端在收文頁改日期時會即時重算期間，
    如果讓前端自己決定「逾期就轉紅」，燈號就變成前端產出的——
    CONSTITUTION §1 說燈號不是模型產出，它同樣不該是前端產出。
    這裡把「逾期 → 紅燈」這條規則連同說法一起回給前端，前端只負責畫。

    純日期規則，零 LLM，同輸入必同輸出。
    """
    deadline = result.get("deadline")
    overdue = result.get("overdue")
    days = (body.filing - body.service).days if body.filing else None

    if deadline is None or overdue is None or body.filing is None:
        tpl = settings.DEADLINE_VERDICT_UNDECIDABLE
    elif overdue:
        tpl = settings.DEADLINE_VERDICT_OVERDUE
    else:
        tpl = settings.DEADLINE_VERDICT_IN_TIME

    fields = {
        "service_roc": _roc(body.service),
        "filing_roc": _roc(body.filing),
        "deadline_roc": _roc(dt.date.fromisoformat(deadline)) if deadline else "—",
        "deadline": deadline or "—",
        "filing": body.filing.isoformat() if body.filing else "—",
        "days": days if days is not None else "—",
    }
    return {
        "lamp": tpl["lamp"],
        "text": tpl["text"].format(**fields),
        "why": tpl["why"].format(**fields),
        "basis": settings.DEADLINE_VERDICT_BASIS,
        "origin": "rule",
        "l_origin": "rule",
        "why_origin": "rule",
        "days": days,
    }


@app.post("/api/deadline")
def api_deadline(body: DeadlineIn) -> dict:
    """期間計算（§6.1 #5）。

    回傳沿用 `Result.as_dict()` 的欄位不改（`effective_date`／`deadline`／`overdue`／
    `steps`／`caveats`），**額外加一個 `verdict` 區塊**——那是「逾期 → 紅燈」這條規則
    的判定結果與說法。§6.1 沒有寫這一塊；加它的理由見 `_deadline_verdict()` 的說明，
    是為了讓前端即時重算時不必自己判斷燈號。既有欄位一個都沒動，舊呼叫端不受影響。
    """
    try:
        result = compute(
            service_method=body.method,
            service_date=body.service,
            filing_date=body.filing,
            transit_days=body.transit,
            interested_party=body.interested,
        ).as_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    result["verdict"] = _deadline_verdict(result, body)
    return result


# ── 前端：同一個 process serve 五步動線 ────────────────────────────
# 沿用 v0 `prototype/app.py:40-45` 的慣例（FileResponse `/` + StaticFiles `/static`）。
# dist/index.html 是單檔全內嵌（CSS/JS/fixture 都在裡面），所以 `/static` 掛著是為了
# 跟 v0 的路徑相容，不是頁面渲染的必要條件。
@app.get("/")
def index() -> FileResponse:
    if not FRONTEND_INDEX.exists():
        raise HTTPException(
            status_code=503,
            detail=f"前端尚未建置：找不到 {FRONTEND_INDEX.relative_to(ROOT)}。請先跑 `python3 prototype/build.py`。",
        )
    # 前端建置產物每次 build 都會變，不讓瀏覽器快取住舊版
    return FileResponse(FRONTEND_INDEX, headers={"Cache-Control": "no-store"})


if FRONTEND_DIST.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIST), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)
