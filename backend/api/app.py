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
| POST | `/api/cases`                | 上傳卷證建案，回 `case_id`（**不限格式**，單檔 20 MB；讀不到的標 unreadable）|
| POST | `/api/cases/{case_id}/runs` | 跑六節點：fixture 同步 200；bedrock 202 + run_id |
| GET  | `/api/runs/{run_id}`        | 輪詢執行結果：200／409 執行中／502 失敗／404 |
| GET  | `/api/runs/{run_id}/events` | SSE 節點事件流（bedrock 檔位的加值層）      |
| POST | `/api/cases/{case_id}/submit` | 送出審議：後端重算後 200／409（§6.1 #9）  |
| POST | `/api/cases/{case_id}/chat`   | 聊天追問（SSE；`?stream=0` 一次性 JSON）。**只在 bedrock 檔位**，否則 503 |
| POST | `/api/deadline`             | 期間計算（沿用 prototype/app.py 的契約）    |

紅線：
- 前端不得直連基礎模型，一律過後端（CONSTITUTION 約束二）。
- 本檔不讀任何憑證、不呼叫任何雲端 API。`RUN_MODE != "fixture"` 時節點會自己 raise，
  這裡把它翻成 501 並照實說原因，不假裝服務正常。
- `/api/health` 不得無條件回 ok：它必須真的把 laws-snapshot 與合成案例讀起來，
  讀不動就照實回 503。健康檢查說謊比沒有健康檢查更糟。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import sys
import uuid
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import anyio.to_thread  # noqa: E402
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
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
from backend.api.chat import router as chat_router  # noqa: E402
from backend.api.dossier import router as dossier_router  # noqa: E402
from backend.api.events import BUS  # noqa: E402
from backend.api.export import router as export_router  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.dossier import store  # noqa: E402
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

# 前端建置產物。由 `npm run build`（Vite）產生在 `frontend/dist/`。
#
# 2026-09-12 從 `prototype/dist` 切到 `frontend/dist`：`prototype/` 已退役。
# **切換不是只改這個路徑**——Vite 產物的 index.html 引用的是 `/assets/index-*.js`，
# 而舊版只掛 `/static`，所以光改指向會得到「畫面全白但 /api/health 的
# frontend_served 照樣回 true」。掛載點必須一起改（見本檔末的 mount）。
FRONTEND_DIST = ROOT / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"
#: Vite 把 JS/CSS 都放在 dist/assets/ 並以絕對路徑 /assets/… 引用。
FRONTEND_ASSETS = FRONTEND_DIST / "assets"

#: index.html 裡形如 src="/assets/x.js" / href="/assets/x.css" 的絕對路徑引用。
_ASSET_REF_RE = re.compile(r'(?:src|href)="(/assets/[^"]+)"')


def _pdftotext_check() -> dict[str, Any]:
    """pdftotext 在不在 PATH。**缺它不會報錯，只會靜默降級**——每份 PDF 都改走
    視覺讀取，慢、貴、準確度低，而畫面上看不出差別。所以要在健檢講出來。

    非 blocking：沒有它系統仍能運作（走 pdf_visual），只是品質下降。
    """
    exe = shutil.which("pdftotext")
    return {
        "name": "pdftotext",
        "ok": bool(exe),
        "detail": (
            f"{exe}（PDF 走文字層抽取）" if exe else
            "不在 PATH——**每份上傳 PDF 都會退到視覺讀取**（慢、貴、準確度低）。"
            "映像檔請裝 poppler-utils。"
        ),
        "blocking": False,
    }


def _frontend_check() -> dict[str, Any]:
    """前端建置產物是否**完整**——不只 index.html 在不在。

    把 index.html 引用的每個 `/assets/…` 逐一對到實體檔案。少一個就回 ok=False，
    因為那正是「頁面回 200 但畫面全白」的成因，而只檢查 index.html 的舊版看不見它。
    """
    if not FRONTEND_INDEX.exists():
        return {
            "name": "frontend_dist",
            "ok": False,
            "detail": "未建置，請在 frontend/ 跑 `npm ci && npm run build`",
            "blocking": False,
        }
    try:
        refs = set(_ASSET_REF_RE.findall(FRONTEND_INDEX.read_text(encoding="utf-8")))
    except OSError as e:
        return {"name": "frontend_dist", "ok": False, "detail": f"index.html 讀取失敗：{e}",
                "blocking": False}
    missing = sorted(r for r in refs if not (FRONTEND_DIST / r.lstrip("/")).is_file())
    if missing:
        return {
            "name": "frontend_dist",
            "ok": False,
            "detail": f"index.html 引用了 {len(refs)} 個資源，其中 {len(missing)} 個不存在："
                      f"{', '.join(missing)}。建置產物不完整，畫面會是空白的。",
            "blocking": False,
        }
    return {
        "name": "frontend_dist",
        "ok": True,
        "detail": f"{FRONTEND_INDEX.relative_to(ROOT)}（引用 {len(refs)} 個資源，全部存在）",
        "blocking": False,
    }


#: anyio 預設執行緒池上限。**40 是 anyio 的預設值，不是量出來的**，所以這裡調高。
#:
#: 誰會長時間佔著 token（2026-09-13 盤點）：`?stream=0` 的聊天一次性模式（10–72 秒）、
#: `GET /api/runs/{id}/events` 的同步 SSE generator、上傳與匯出。
#: 聊天的 SSE 已經改成 async generator，**那條路現在用 0 個 token**。
#:
#: 為什麼是 128 而不是更大：一個 token ＝ 一條 OS thread。task 是 1 vCPU／2048 MiB，
#: Python 執行緒的 stack 是惰性提交的，128 條實際佔用約數 MB——相對便宜。
#: 但**不無上限**：1 vCPU 上掛幾百條只會 context switch 互打，而且沒有上限就沒有
#: 背壓訊號，塞爆的時候會安靜地變慢而不是明確地排隊。
#:
#: ⚠️ **調高只是把懸崖往後推，不是拆掉那條鏈。** 真正拆掉鏈的是
#: `/api/health` 走自己的池（見 `_HEALTH_POOL`）：健康檢查不跟它監測的負載搶資源。
THREADPOOL_LIMIT = int(os.environ.get("BACKEND_THREADPOOL_LIMIT", "128"))


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    """啟動時把 anyio 的預設執行緒池上限調高（見 `THREADPOOL_LIMIT`）。

    只能在 async context 裡拿得到那個 limiter，所以放在 lifespan 而不是模組層。
    拿不到就不調——**不要讓一個調校動作變成服務起不來的理由**，並印出來，不吞。
    """
    try:
        anyio.to_thread.current_default_thread_limiter().total_tokens = THREADPOOL_LIMIT
    except Exception as e:  # noqa: BLE001 — 見 docstring
        print(f"[warn] 調整執行緒池上限失敗（維持預設值）：{type(e).__name__}: {e}",
              file=sys.stderr)
    yield


app = FastAPI(
    lifespan=_lifespan,
    title="訴願案件審理 AI 輔助（v2 六節點 + 五步動線）",
    description="六節點 deterministic pipeline，同一個 process 也 serve 五步動線前端。執行檔位由 RUN_MODE 決定（fixture 離線重播／bedrock 即時推論），實際值見 GET /api/health。",
    docs_url="/api/docs",
)

# 聊天追問（spec 2026-09-12-chat-honesty-lamps）。閘門與事件都在該檔，
# 這裡只掛一行——回滾就是把這行拿掉。
app.include_router(chat_router)
# 卷宗與母庫的一次性端點（契約 v2 §1、§4）。**掛在 chat 之後、CORS 之前**，
# 位置沒有特別含意，只是讓兩個 router 的掛載讀起來在一起。
app.include_router(dossier_router)

# 草稿匯出（契約 §1.5 #23、§4.4）。同樣只掛一行——回滾就是把這行拿掉。
app.include_router(export_router)

# 本機開發用 CORS：只放行 localhost／127.0.0.1 的任意 port。
# 不用 allow_origins=["*"]——那會讓任何網站都能打這支 API。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Any, exc: RequestValidationError) -> JSONResponse:
    """FastAPI 預設的 422 把 `detail` 送成**物件陣列**，這裡把它改成字串。

    為什麼要改（2026-09-13 雲上實測）：其餘所有端點手寫的 `detail` 都是字串，
    前端統一照字串渲染（`frontend/src/api/http.js:8` 的
    `super((body && body.detail) || ...)` → `store/app.js:1135` 的 `errText`）。
    物件陣列進去，承辦人畫面上看到的是 **`[object Object]`** ——一句話都沒有。
    實際打得到的兩處：`POST /api/cases` 少 `files`、`PATCH /api/cases/{id}` 少 `name`。

    **狀態碼仍是 422，結構化細節也不丟**（挪到 `errors`）：改狀態碼會動到契約，
    丟細節會讓呼叫端少一份可程式判讀的資料。這裡只換 `detail` 的型別。
    """
    return JSONResponse(
        status_code=422,
        content={"detail": _validation_message(exc.errors()), "errors": _safe_errors(exc.errors())},
    )


def _validation_message(errors: list[dict[str, Any]]) -> str:
    """把 pydantic 的錯誤清單攤成一句人看得懂的話。**逐條都講**，不只講第一條
    ——少了兩個必填欄位卻只說一個，使用者會補完再送一次再被擋一次。"""
    parts: list[str] = []
    for err in errors or []:
        loc = ".".join(str(x) for x in (err.get("loc") or ()) if x != "body")
        msg = str(err.get("msg") or "").strip()
        parts.append(f"{loc}：{msg}" if loc else msg)
    if not parts:
        return "請求內容不符合這支端點的規格。"
    return "請求內容不符合這支端點的規格——" + "；".join(parts)


def _safe_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只留可序列化的三個鍵。pydantic 的 `ctx` 可能夾帶例外物件（不可 JSON 序列化），
    原樣丟進 `JSONResponse` 會把 422 變成 500——那比原本的 `[object Object]` 更糟。"""
    return [
        {"loc": [str(x) for x in (e.get("loc") or ())],
         "msg": str(e.get("msg") or ""),
         "type": str(e.get("type") or "")}
        for e in errors or []
    ]


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
    #
    # **只檢查 index.html 在不在是不夠的。** Vite 產物的 index.html 引用
    # `/assets/index-<hash>.js`，少了那些檔案頁面會回 200 但畫面全白——而舊版的
    # 這項檢查照樣回 true。所以這裡改成把 index.html 引用的每個 /assets/ 路徑
    # 逐一對到實體檔案。擋不了「檔案被手改」，但擋得住「建置產物不完整就部署」，
    # 而後者才是會讓人打開網址看到空白畫面的那一種。
    checks.append(_frontend_check())
    checks.append(_pdftotext_check())

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


#: 健康檢查專用的執行緒池。**刻意不共用 anyio 的預設池**（2026-09-13，實測驅動）。
#:
#: starlette 迭代同步 generator 會佔住預設池的一個 token，**阻塞多久就佔多久**，
#: 而聊天一輪 10–72 秒、池子預設上限 40。實測 41 條並行 SSE 時 `/api/health`
#: 整個打不開、50 條要 12 秒。接下來是 ALB（`interval 30s`／`timeout 10s`／
#: `unhealthyThresholdCount 3`）判 task 不健康 → `desiredCount: 1` 的 task 被換掉 →
#: `backend/output/` 是容器本地磁碟，**上傳的卷證與 manifest 全部消失**。
#:
#: **健康檢查不該跟它要監測的負載搶同一個資源。** 給它自己的池，SSE 再多也搶不走。
#: 兩條就夠：ALB 每 30 秒打一次，這裡一次 0.2–0.8 ms。
_HEALTH_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="health")


@app.get("/api/health")
async def health() -> JSONResponse:
    """健康檢查。**`async def` + 專用執行緒池**，兩件事缺一不可。

    只改 `async def` 會把 `_health_checks()` 的實際讀檔搬到 event loop 上，
    變成阻塞所有人而不是只佔一個 token——**那比不改更糟**。所以實際工作仍在
    執行緒裡跑，只是跑在自己的池子上。

    紅線不變（見本檔檔頭）：它**真的**去讀 laws-snapshot 與合成案例，
    讀不動就回 503。這裡沒有加任何快取——快取過的健康檢查會在磁碟剛壞掉時
    照樣說 ok，那就是健康檢查說謊。
    """
    return await asyncio.get_running_loop().run_in_executor(_HEALTH_POOL, _health_body)


def _health_body() -> JSONResponse:
    """`/api/health` 的實際工作（會讀檔，所以不在 event loop 上跑）。"""
    mode = run_mode()
    checks = _health_checks()
    ok = all(c["ok"] for c in checks if c.get("blocking", True))
    case_ids = list_synthetic_cases() if ok else []
    body = {
        "ok": ok,
        "checks": checks,
        "run_mode": mode,
        "fixture_only": mode == "fixture",
        # 這兩個欄位一度被寫死成 Phase 0 的值，`RETRIEVER=kb` 也照樣回報「沒開」
        # （2026-09-12 實測）。健康檢查說的話必須是它真的知道的事（CONSTITUTION §1）。
        "kb_backend": settings.retriever_kind(),
        "similar_case_backend": retrieval_kb.describe_similar_case_backend(settings.retriever_kind()),
        # 重排是**安靜地開或不開**：沒設 `BEDROCK_RERANK_MODEL_ID` 就不重排、不報錯，
        # 而檢索的分數門檻又是跟著它一起變寬的（settings.kb_min_score）。
        # ECS 上漏設這個變數，表現是「相似案卡混進語意無關的命中」——沒有人會發現。
        # 這裡只報開關與門檻，**不報 model id 的值**（CONSTITUTION §7）。
        "rerank": settings.rerank_state(),
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
    """左欄案件清單。`cases[]` 每筆是 `{id, name, created_at, latest_run_id, kind}`
    （契約 v2 §1.1 #2）。

    **形狀變更（2026-09-12）**：`cases` 原本是 case_id 字串陣列，左欄只拿得到 id，
    畫不出案名與建立時間。`synthetic`／`uploaded` 兩個鍵**維持字串陣列不動**，
    既有呼叫端（`scripts/run_eval.py`、`scripts/live_acceptance.py`）不受影響。

    四個欄位全部從**同一份 manifest**（`m`）來，沒有 manifest 的案子在這裡
    **順手建一份**（`store.ensure`）——不是為了寫檔，是因為推導 name 要讀
    case.json／測資檔，讀都讀了就落地，下次列表就不必再推一次。
    這裡的 `cid` 來自 `list_cases()`（掃的是真實來源），所以 `ensure` 在這支是
    對的語意，**跟卷宗唯讀端點那邊不一樣**（那邊改走 `dossier._read`：
    來源不存在就 404，不替一個不存在的案子建空案）。

    **`latest_run_id`（2026-09-13 補）**：前端 `store/app.js` 的 `bootAsync` 讀它，
    `api/mock.js` 的 `caseSummary` 也一直有回——只有真後端漏掉，於是 mock 跑起來正常、
    真後端缺一個鍵，開發時完全看不出來。症狀平常被 `loadCase`（打彙整版）蓋掉，
    但**在彙整版回來之前送出的 chat 會缺 `run_id`**，而缺 `run_id` 時 `read_case`
    會回「卷內是空的」（契約 §2.1 ③）。

    值與 `GET /api/cases/{id}` 彙整版是**同一個來源同一個值**（都是這份 manifest 的
    `latest_run_id`），不是在這裡另算一份。成本也是零：這個迴圈本來就已經
    逐件讀過 manifest 了（上一行的 `store.ensure`），原本只是把這個鍵丟掉不用。

    manifest 壞掉走 except 分支時 `latest_run_id` 一樣**帶 `None` 而不是省略鍵**：
    省略會讓前端拿到 `undefined` 而不是 `null`，跟契約 §2.3「值為 null 也要送」同理。
    """
    lst = list_cases()
    items = []
    for kind, ids in (("synthetic", lst["synthetic"]), ("uploaded", lst["uploaded"])):
        for cid in ids:
            try:
                m = store.ensure(cid)
                items.append({"id": cid, "name": m["name"], "created_at": m["created_at"],
                              "latest_run_id": m["latest_run_id"], "kind": kind})
            except Exception:  # noqa: BLE001 — 一個案子的 manifest 壞掉不該讓整個清單打不開
                items.append({"id": cid, "name": cid, "created_at": None,
                              "latest_run_id": None, "kind": kind})
    return {
        "cases": items,
        "synthetic": lst["synthetic"],
        "uploaded": lst["uploaded"],
        "note": "synthetic- 為合成測資；upload- 為承辦人上傳，僅存於本服務 output/ 目錄，不進 git。",
    }


@app.post("/api/cases", status_code=201)
async def create_case(files: list[UploadFile] = File(...)) -> dict:
    """上傳卷證建案（spec 2026-09-07 §5.8）。**不限制格式**，單檔 20 MB。

    2026-09-12 Ci 拍板拿掉副檔名白名單。收下不等於讀得到：讀不讀得到由
    `intake/documents.route_documents` 決定，讀不到的會產出 kind="unreadable"
    並在 notes 說明原因，**不會靜默跳過**。

    回 `case_id` 供 `POST /api/cases/{case_id}/runs` 使用。上傳案沒有可重播的 fixture，
    **只能在 RUN_MODE=bedrock 跑**；fixture 檔位下打 runs 會拿到 400 並附上原因。
    檔案落在 `backend/output/uploads/`（gitignored），不外送任何第三方（CONSTITUTION §6）。
    """
    payload = [(f.filename or "file", await f.read()) for f in files]
    try:
        meta = save_upload(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # 建案就把 manifest 落地（契約 §1.1「上傳卷證即建案」）。寫失敗不該讓建案失敗
    # ——卷證已經存進去了，回 500 會讓使用者以為要重傳一次。
    try:
        store.ensure(meta["case_id"])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 建案 {meta['case_id']} 的 manifest 寫入失敗：{type(e).__name__}: {e}",
              file=sys.stderr)
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
    # `from_node != n1` 卻沒有 `base_run_id` 是錯誤（沒有上游可沿用）。
    # 這條規則 `run_case()` 也有（graph.py，那裡才是權威），但**在 bedrock 檔位
    # 它來不及**：run_case 跑在背景，202 早就回出去了，前端要輪詢到 run_failed
    # 才知道自己送錯——而那是個 502，看起來像系統壞了，不像參數錯了。
    # 錯在請求就該回 4xx，而且要在發 202 之前（2026-09-12 AC8b 實測：實際回 202）。
    if body.from_node and body.from_node != "n1" and not body.base_run_id:
        raise ValueError("from_node 不是 n1 時必須提供 base_run_id（沒有上游可沿用）")
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
        state = run_case(case_id, on_event=lambda k, d: BUS.push(rid, k, d), run_id=rid, **kwargs)
        # 登記的本體在 `backend/dossier/store.py`，**chat 那條路徑也呼叫同一支**
        # （2026-09-12：原本只接在這裡，而契約 §0.1 說前端只打 chat，
        # 於是唯一會登記的路徑正好是前端不會走的那一條）。
        store.record_run(case_id, build_payload(state))
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
        # payload 已經在手上，不重算一次（登記本體見 `backend/dossier/store.py`）
        store.record_run(case_id, payload)
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
    SSE **阻塞多久就佔著一個 threadpool token 多久**。

    **上一句原本接的是「（預設 40），demo 量級夠用」，那句話被實測推翻了**
    （2026-09-13）：41 條並行的長 SSE 就會把預設池塞滿，其他同步端點
    （`/api/cases`）直接 timeout。同一段話的複本原本也在 `backend/api/chat.py`，
    那邊的 SSE 已經改成 async generator、threadpool 用量歸零。

    **這一支還沒改，而且目前不急**：前端契約不打它（`docs/handoff/2026-09-12-frontend-contract-v2.md` §1.6），
    實際使用者是 `verify.sh` 與除錯，並行度個位數。另外兩道防線也已經在：
    `/api/health` 走專用執行緒池（見 `_HEALTH_POOL`，所以 ALB 不會因此判 task 不健康），
    預設池上限也從 40 提到 `THREADPOOL_LIMIT`。
    真要改的話**照 `backend/api/chat.py` 的 `gen()`**：工作丟 `threading.Thread`、
    generator 改 `async def` 只 await 佇列。

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
        # **拒絕一定要說得出為什麼。** 流程若停在 N1（NEEDS_INPUT），守門節點根本沒跑到，
        # `blockers` 會是空的——於是前端收到一個 409 卻拿不到任何理由，畫面只能顯示
        # 「被擋」。2026-09-12 實測：上傳一份沒有文字層的 PDF 就會走到這裡。
        # 理由其實一直存在於 `run_meta.degraded`，只是沒有被翻成 blocker。
        blockers = list(payload["blockers"])
        if not blockers:
            reasons = [
                d.get("reason") or "" for d in (payload.get("run_meta") or {}).get("degraded", [])
            ]
            blockers = [{
                "level": "P0",
                "code": "run_incomplete",
                "node": "n1",
                "why": (
                    f"本次執行停在 {payload.get('state')}，六節點沒有全部跑完，"
                    "因此沒有可供送出的完整結果。"
                    + ("　原因：" + "；".join(r for r in reasons if r) if any(reasons) else "")
                ),
                "how_to_clear": (
                    "請在收文欄位確認面板補齊必填欄位後按「我已核對」重跑。"
                    "若卷證是掃描件或無文字層的 PDF，抽取器讀不到內容，欄位需要人工輸入。"
                ),
            }]
        return JSONResponse(
            {**common, "blockers": blockers, "accepted": False}, status_code=409
        )

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


# ── 前端：同一個 process serve 工作台 ────────────────────────────
@app.get("/")
def index() -> FileResponse:
    if not FRONTEND_INDEX.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                f"前端尚未建置：找不到 {FRONTEND_INDEX.relative_to(ROOT)}。"
                "請先在 `frontend/` 跑 `npm ci && npm run build`。"
            ),
        )
    # 前端建置產物每次 build 都會變，不讓瀏覽器快取住舊版
    return FileResponse(FRONTEND_INDEX, headers={"Cache-Control": "no-store"})


# **`/assets` 是必要的，不是相容用的。** Vite 產物的 index.html 以絕對路徑引用
# `/assets/index-<hash>.js`；少了這個 mount，頁面會回 200 但畫面全白，而
# `/api/health` 的 `frontend_served`（只看 index.html 在不在）照樣回 true——
# 靜態健檢看不見「資源載不載得到」。切換前端時這兩件事必須一起做。
if FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="assets")


if __name__ == "__main__":
    if uvicorn is None:
        raise SystemExit(
            "uvicorn 未安裝。官方啟動指令見本檔開頭的 DEPLOY 說明："
            'uv run --with fastapi --with "uvicorn[standard]" --with pydantic '
            "--with python-multipart -- python -m uvicorn backend.api.app:app"
        )
    uvicorn.run(app, host="127.0.0.1", port=8080)
