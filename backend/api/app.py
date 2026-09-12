"""FastAPI 服務：一個 process 同時 serve 五步動線前端與六節點 API。

跑法（**唯一的官方啟動指令**，見 `backend/DEPLOY.md`）：

    uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart -- \
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
| GET  | `/api/cases`                | 列出可用案例（合成 + 承辦人上傳）          |
| POST | `/api/cases`                | 上傳卷證建案，回 `case_id`（只收 .pdf／.txt）|
| POST | `/api/cases/{case_id}/runs` | 跑六節點：fixture 同步 200；bedrock 202 + run_id |
| GET  | `/api/runs/{run_id}`        | 輪詢執行結果：200／409 執行中／502 失敗／404 |
| GET  | `/api/runs/{run_id}/events` | SSE 節點事件流（bedrock 檔位的加值層）      |
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
import uuid
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402

try:  # spec D8：import 一律模組頂層。uvicorn 只有「直接執行本檔」才用得到，
    # 缺它不該讓 `import backend.api.app` 失敗（官方啟動指令走 `python -m uvicorn`）。
    import uvicorn  # noqa: E402
except ImportError:
    uvicorn = None

import backend.llm.client as llm_client  # noqa: E402  健康檢查用：只看 Agent 在不在，不呼叫任何模型
import backend.retrieval.kb as retrieval_kb  # noqa: E402  健康檢查用：只看 boto3 在不在，不打任何 API
from backend.api.events import BUS  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.config.settings import load_snapshot, provenance, run_mode  # noqa: E402
from backend.engine.deadline import compute  # noqa: E402
from backend.intake.uploads import save_upload  # noqa: E402
from backend.orchestrator.graph import (  # noqa: E402
    CaseNotFound,
    build_payload,
    list_cases,
    list_synthetic_cases,
    load_case,
    run_case,
)
from backend.orchestrator.runstore import RunNotFound, load_run  # noqa: E402

# 五步動線前端的建置產物。由 `python3 prototype/build.py` 產生（單檔全內嵌）。
FRONTEND_DIST = ROOT / "prototype" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"

app = FastAPI(
    title="訴願案件審理 AI 輔助（v2 六節點 + 五步動線）",
    description="六節點 deterministic pipeline，同一個 process 也 serve 五步動線前端。執行檔位由 RUN_MODE 決定（fixture 離線重播／bedrock 即時推論），實際值見 GET /api/health。",
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
    """`POST /runs` 的選填 body。

    - `confirmed_intake`：承辦人在收文頁看過的欄位（判斷卡 7）。裡面的欄位代表
      **承辦人看過**（可能改過、也可能原樣採用），`intake_origin` 會記成 `human`，
      而且只有它們齊全時期間結果才可以用來解除結論封鎖。不給 body ＝ 沒有人確認過。
      只能搭配 `from_node` 為 n1 或 n2。
    - `base_run_id` + `from_node`：從某次執行的指定節點往下重跑到 N6（spec 2026-09-07 §5.5）。
    - `overrides`：白名單只有 `n4_query`（重新檢索時附加查詢詞）。

    **安全邊界：本模型永遠不得新增 `base_state` 欄位。** 續跑的上游狀態一律由後端拿
    `base_run_id` 去 `load_run()` 讀回來；若允許 request body 直接夾帶 state，
    呼叫端就能偽造一份「已人工確認、無 blocker」的狀態送進來，
    把 C 型案的結論封鎖（判斷卡 7／CONSTITUTION §1）整個關掉。

    `extra="forbid"`：未知欄位一律 422，不靜默忽略（M-12）。上面那條安全邊界如果只寫在
    docstring 裡，帶 `base_state` 的請求會安安靜靜地被丟掉——呼叫端以為它生效了，
    看到的結果卻是後端自己讀回來的 base。拒絕比忽略誠實。
    """

    model_config = ConfigDict(extra="forbid")

    confirmed_intake: dict[str, Any] | None = None
    base_run_id: str | None = None
    from_node: str = "n1"
    overrides: dict[str, Any] | None = None


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

    # 4. live 檔位真的跑得起來嗎：缺環境變數、或缺第三方套件，都要在這裡就說出來。
    #    套件檢查不能省——`missing_live_settings()` 只看環境變數，設定齊全但沒裝
    #    strands-agents／boto3 的機器一樣一打就炸，健康檢查卻回 ok，那就是說謊。
    #    兩個模組都有頂層 try/except 守衛，沒裝套件也 import 得動，這裡只看名字在不在。
    missing = settings.missing_live_settings()
    if settings.retriever_kind() == "kb" and retrieval_kb.boto3 is None:
        missing.append("boto3（pip install boto3）")
    if run_mode() == "bedrock" and llm_client.Agent is None:
        missing.append("strands-agents（pip install strands-agents）")
    checks.append(
        {
            "name": "live_settings",
            "ok": not missing,
            "detail": (
                "fixture 模式，無需雲端設定"
                if run_mode() == "fixture" and settings.retriever_kind() != "kb"
                else ("齊全" if not missing else f"缺：{', '.join(missing)}（見 .env.example）")
            ),
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
        "kb_backend": settings.retriever_kind(),
        "similar_case_backend": "kb" if settings.retriever_kind() == "kb" else "unavailable",
        # fixture 檔位沒有呼叫任何基礎模型就不報 model id；live 檔位報環境變數設定的那組，
        # 但那是「設定值」不是「這次真的呼叫過」——逐次執行的實際值在 run_meta.model_ids。
        "model_ids": None if mode == "fixture" else llm_client.model_ids(),
        "model_ids_note": (
            "fixture 檔位未呼叫任何基礎模型。"
            if mode == "fixture"
            else f"{mode} 檔位的設定值；某一次執行實際呼叫了哪些模型，見該 run 的 run_meta.model_ids。"
        ),
        "cases_available": case_ids,
        "frontend_served": FRONTEND_INDEX.exists(),
        "provenance": provenance(),
    }
    return JSONResponse(body, status_code=200 if ok else 503)


@app.get("/api/cases")
def cases() -> dict:
    """兩種來源分開列。`cases` 是合併後的相容清單（舊呼叫端仍讀這個鍵）。"""
    lst = list_cases()
    return {
        "cases": lst["synthetic"] + lst["uploaded"],
        "synthetic": lst["synthetic"],
        "uploaded": lst["uploaded"],
        "note": "synthetic- 為合成測資；upload- 為承辦人上傳，僅存於本服務 output/ 目錄，不進 git。",
    }


@app.post("/api/cases", status_code=201)
async def create_case(files: list[UploadFile] = File(...)) -> dict:
    """上傳卷證建案（spec 2026-09-07 §5.8）。只收 .pdf／.txt，單檔 20 MB。

    回 `case_id` 供 `POST /api/cases/{case_id}/runs` 使用。上傳案沒有可重播的 fixture，
    **只能在 RUN_MODE=bedrock 跑**；fixture 檔位下打 runs 會拿到 400 並附上原因。
    檔案落在 `backend/output/uploads/`（gitignored），不外送任何第三方（CONSTITUTION §6）。
    """
    payload = [(f.filename or "file", await f.read()) for f in files]
    try:
        meta = save_upload(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "case_id": meta["case_id"],
        "files": meta["files"],
        "provenance": meta["provenance"],
        "next": f"/api/cases/{meta['case_id']}/runs",
    }


def _translate(e: Exception) -> HTTPException:
    """把 pipeline 的例外翻成 HTTP 狀態碼。

    **同一份對照表給所有端點用**，不讓 `/runs` 與 `/submit` 各寫一份而漸漸長歪。
    落到最後一行的未知例外一律 502 並帶上原始類型與訊息——寧可把錯誤原樣端出來，
    也不要包成一句「系統忙碌中」。
    """
    if isinstance(e, (CaseNotFound, RunNotFound, FileNotFoundError)):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, ValueError):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, NotImplementedError):
        return HTTPException(status_code=501, detail=str(e))
    if isinstance(e, AssertionError):
        # 不變式違反是 P0，照實回 500 並帶原因，不吞掉
        return HTTPException(status_code=500, detail=f"不變式違反（P0）：{e}")
    if type(e).__name__ == "LLMError":
        # 用類名比對而不 import backend.llm：api 層不該把模型客戶端拉進 import 圖
        return HTTPException(status_code=502, detail=f"模型呼叫失敗：{e}")
    return HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")


def _run_kwargs(body: RunIn | None) -> dict[str, Any]:
    """把 body 翻成 `run_case()` 的具名參數。

    **續跑的上游狀態只能從這裡長出來**：body 只帶得動一個 `base_run_id`，
    真正的 `base_state` 是後端拿它去 `load_run()` 讀回來的（見 `RunIn` 的安全邊界說明）。
    """
    body = body or RunIn()
    kw: dict[str, Any] = {
        "confirmed_intake": body.confirmed_intake,
        "from_node": body.from_node,
        "overrides": body.overrides,
    }
    if body.base_run_id:
        kw["base_state"] = load_run(body.base_run_id)
    return kw


def _run_in_background(case_id: str, rid: str, kwargs: dict[str, Any]) -> None:
    """202 之後在背景把六節點跑完，進度與結果分別走 BUS 與 runstore。"""
    try:
        run_case(case_id, on_event=lambda k, d: BUS.push(rid, k, d), run_id=rid, **kwargs)
    except Exception as e:  # noqa: BLE001 — graph 已發 run_failed；這裡只確保狀態收斂
        # 沒有這一段，`run_case` 進到節點之前就炸掉（例如案例不存在）時
        # BUS 會永遠停在 running，前端就永遠輪詢下去。
        st = BUS.status(rid)
        if st and st["status"] == "running":
            BUS.push(rid, "run_failed", {"node": None, "error": f"{type(e).__name__}: {e}"})


@app.post("/api/cases/{case_id}/runs")
def create_run(case_id: str, background: BackgroundTasks, body: RunIn | None = None):
    """啟動狀態機跑六節點。兩個檔位的回應形狀不同：

    - fixture：同步跑完，200 ＋ 完整 CASE payload（全程毫秒級，沒有阻塞疑慮）。
      `run_id` 在 payload 頂層與 `run_meta` 各有一份。
    - bedrock：202 ＋ `{run_id, status, result_url, events_url}`，實際執行在背景，
      前端拿 `result_url` 輪詢 `GET /api/runs/{id}`（architecture §6.1 的 2b），
      或接 `events_url` 的 SSE 逐節點更新（加值層，斷了就退回輪詢）。
    """
    try:
        kwargs = _run_kwargs(body)
    except Exception as e:  # noqa: BLE001 — 交給 _translate 決定狀態碼
        raise _translate(e) from e

    if run_mode() != "bedrock":
        try:
            state = run_case(case_id, **kwargs)
        except Exception as e:  # noqa: BLE001
            raise _translate(e) from e
        payload = build_payload(state)
        if payload["origin_violations"]:
            raise HTTPException(status_code=500, detail={"origin_violations": payload["origin_violations"]})
        return payload

    # 202 之前先把案例讀起來。理由有兩個，都不是為了效能：
    # 一是「案例不存在」該回 404，不該先發一張 202 再讓前端輪詢半天換到 502；
    # 二是 rid 會被 `save_run()` 拼成檔名，而 `load_case()` 已經把 case_id 限死在
    # synthetic-／upload- 兩種前綴（graph.py:107-117），先過這一關才拼 id 比較安全。
    try:
        load_case(case_id)
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e

    rid = f"run-{case_id}-{uuid.uuid4().hex[:12]}"
    BUS.start(rid)
    background.add_task(_run_in_background, case_id, rid, kwargs)
    return JSONResponse(
        {
            "run_id": rid,
            "status": "running",
            "result_url": f"/api/runs/{rid}",
            "events_url": f"/api/runs/{rid}/events",
        },
        status_code=202,
    )


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    """輪詢一次執行的結果（bedrock 檔位 202 之後用）。

    - 還在跑 → **409** `{status:"running"}`。用 409 而不是 200＋狀態欄位，
      是為了讓前端沒辦法把「還沒跑完」誤讀成一份空的分析結果。
    - 失敗 → **502** ＋ 失敗的 `node` 與原始錯誤字串。**不回任何替代草稿**：
      模型呼叫失敗時拿 fixture 頂上去，就是拿假的當真的（CONSTITUTION §1）。
    - 完成 → 200 ＋ 完整 CASE payload（從 runstore 讀終態重建）。
    """
    st = BUS.status(run_id)
    if st and st["status"] == "running":
        return JSONResponse({"run_id": run_id, "status": "running"}, status_code=409)
    if st and st["status"] == "failed":
        return JSONResponse(
            {"run_id": run_id, "status": "failed", "node": st.get("node"), "error": st.get("error")},
            status_code=502,
        )
    try:
        state = load_run(run_id)
    except (RunNotFound, ValueError) as e:
        raise _translate(e) from e
    return build_payload(state)


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str) -> StreamingResponse:
    """SSE 節點事件流（spec 2026-09-07 §5.6）。**加值層，不是唯一取得結果的路徑。**

    事件序是 `node_start`／`node_done` 成對出現（六節點全跑完才會有六對；
    中途失敗就停在該節點），收尾 `run_done` 或 `run_failed`，閒置過久發
    `timeout` 後關流。`data` 一律是一個 JSON 物件。

    本端點是同步 `def`：starlette 會把同步 generator 丟到 threadpool 迭代，
    所以 `stream()` 裡的 `time.sleep` 不會卡住 event loop——代價是每條開著的
    SSE 佔一個 threadpool thread（預設 40），demo 量級夠用，不是通用方案。

    事件只活在這個 process 的記憶體裡（`RunEvents`），沒有持久化也沒有回放：
    process 重啟後事件就沒了，但結果還在 runstore，所以 404 的說明要指回
    `GET /api/runs/{run_id}`——前端不該因為事件流斷了就以為執行結果不見了。
    """
    if BUS.status(run_id) is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"沒有這個 run 的事件流：{run_id}"
                f"（process 重啟後事件不保留，結果請打 GET /api/runs/{run_id}）"
            ),
        )
    return StreamingResponse(
        BUS.stream(run_id),
        media_type="text/event-stream",
        # no-store 擋瀏覽器與中間層快取；X-Accel-Buffering 擋 nginx 把事件緩衝成一坨。
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


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
        state = run_case(case_id, **_run_kwargs(body))
    except Exception as e:  # noqa: BLE001 — 對照表在 _translate，跟 /runs 共用一份
        raise _translate(e) from e

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
    if uvicorn is None:
        raise SystemExit(
            "uvicorn 未安裝。官方啟動指令見本檔開頭的 DEPLOY 說明："
            'uv run --with fastapi --with "uvicorn[standard]" --with pydantic '
            "--with python-multipart -- python -m uvicorn backend.api.app:app"
        )
    uvicorn.run(app, host="127.0.0.1", port=8080)
