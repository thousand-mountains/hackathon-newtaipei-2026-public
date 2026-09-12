"""聊天追問端點：`POST /api/cases/{case_id}/chat`（SSE，或 `?stream=0` 一次性 JSON）。

契約唯一真實來源：`docs/spec/2026-09-12-chat-honesty-lamps.md`。

## 這一層的職責：取資料

spec §4.0「payload 由呼叫端提供」——`backend/llm/chat.py` 是純 agent 模組，**不碰
runstore 與 orchestrator**（之後要能整份搬上託管服務，那個容器裡沒有 runstore）。
所以 `load_run` → `build_payload` → 切分區這條路只出現在這個檔，切好的 dict
當參數餵給 `build_chat_agent()`。

## 503 閘門在開串流「之前」

半開一條串流再道歉，前端會先渲染出一個空白對話泡——那就是在演一段沒發生的對話。
閘門擋在前面，前端才可以安全地假設「拿到 `text/event-stream` 就一定至少有一個
`done` 或 `error`」。錯誤回應**全部是 JSON，不是 SSE**（spec §2.3）。

## 為什麼是同步 `def` 而不是 `async def`

照抄既有 `run_events()` 的做法：starlette 會把同步 generator 丟到 threadpool 迭代，
所以 generator 裡的阻塞呼叫（模型、檢索）不會卡住 event loop。代價是每條開著的 SSE
佔一個 threadpool thread（預設 40），demo 量級夠用，**不是通用方案**。

## `?stream=0` 是契約的一部分，不是臨場才寫的備援

賽場網路讓 SSE 斷流時前端切過來用它，**而不是**在本地用 CSS 演一段沒發生的串流。
它跟 SSE 走同一條產生路徑（同一個 `_run_turn`），所以兩邊不會長歪。
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.api.events import BUS
from backend.config import settings
from backend.config.settings import load_snapshot, run_mode
from backend.llm.chat import RefBook, build_chat_agent, classify_answer
from backend.orchestrator.graph import build_payload
from backend.orchestrator.runstore import RunNotFound, load_run
from backend.retrieval.kb import build_retriever

router = APIRouter()

#: 餵給 agent 的卷內分區（`read_case` 的值域，見 `backend/llm/chat.py` 的 CASE_SECTIONS）。
PAYLOAD_SECTIONS = ("intake", "facts_excerpt", "screen", "laws", "cases")

#: 進程內的對話記憶。**ECS 擴到多台就會失憶**（spec §8.2）——目前單台，不處理，
#: 寫在這裡以免日後被當成新發現的 bug。
_SESSIONS: dict[str, list[dict[str, str]]] = {}

#: 一個 session 最多留幾輪。超過就砍最舊的，並在 `done.session_truncated` 標出來——
#: 靜默砍掉會讓承辦人以為系統還記得前面講過的話。
_MAX_TURNS = 12


class ChatIn(BaseModel):
    """spec §2.1 的請求。`case_id` 走路徑參數，不在 body 裡。

    **刻意不用 pydantic 的 `min_length` 擋空字串**：那會回 422，而 spec §2.3 說
    「`message` 空」是 **400**。契約凍結了、前端照 400 寫分支，所以空值檢查移到
    `_validate()` 手動做。422 留給結構壞掉的 body（少鍵、型別不對），那不在 spec 的
    表裡，回 422 不違反契約。
    """

    run_id: str
    message: str
    session_id: str | None = None
    context: dict[str, Any] | None = None


def _validate(body: ChatIn) -> None:
    """spec §2.3 的 400。空白字元只有空白也算空——前端誤送一個空格不該被當成問題。"""
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="message 不得為空")
    if not body.run_id.strip():
        raise HTTPException(status_code=400, detail="run_id 不得為空")


def _live_gate() -> JSONResponse | None:
    """spec §2.3 的 503。**在開串流之前**呼叫，回 None 表示可以往下走。"""
    mode = run_mode()
    missing = settings.missing_live_settings()
    if mode == "bedrock" and not missing:
        return None
    return JSONResponse(
        {
            "detail": "聊天需要即時模型檔位；目前不可用。",
            "run_mode": mode,
            "missing": missing,
            "why": ("目前是離線重播檔位（RUN_MODE=fixture），聊天沒有可重播的腳本，"
                    "不會假裝有一段對話。" if mode != "bedrock"
                    else "即時檔位缺少必要環境變數，設好就會有。"),
        },
        status_code=503,
    )


def _load_case_payload(case_id: str, run_id: str) -> dict[str, Any]:
    """讀 run、組 payload、切分區。**這是本檔存在的理由**（spec §4.0）。

    狀態碼沿用 `GET /api/runs/{id}` 的語義，不另發明一套：
    409 還在跑、502 失敗、404 不存在、400 run 不屬於這個 case。
    """
    st = BUS.status(run_id)
    if st and st.get("status") == "running":
        raise HTTPException(status_code=409, detail=json.dumps(
            {"run_id": run_id, "status": "running"}, ensure_ascii=False))
    if st and st.get("status") == "failed":
        raise HTTPException(status_code=502, detail=json.dumps(
            {"run_id": run_id, "status": "failed",
             "node": st.get("node"), "error": st.get("error")}, ensure_ascii=False))
    try:
        state = load_run(run_id)
    except (RunNotFound, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    payload = build_payload(state)
    if payload.get("case_id") != case_id:
        # 不是防禦性檢查：run_id 猜得到，拿 A 案的 run 去問 B 案會讓回答引用另一件卷。
        raise HTTPException(
            status_code=400,
            detail=f"run_id {run_id} 屬於案件 {payload.get('case_id')}，不是 {case_id}",
        )
    return {k: payload.get(k) for k in PAYLOAD_SECTIONS}


def _user_message(body: ChatIn, history: list[dict[str, str]]) -> str:
    """把問題、選取句與歷史組成一則 user message。

    `context` 只做一件事：放進結構化區塊供模型參考。**它不改變燈號規則**——
    §3 規則 2 只看有沒有用過 `refine_text`，不看 `context`（spec §2.1）。
    """
    parts: list[str] = []
    if history:
        lines = [f"{h['role']}：{h['text']}" for h in history]
        parts.append("【先前對話】\n" + "\n".join(lines))
    ctx = body.context or {}
    if ctx.get("text"):
        parts.append("【承辦人選取的原文】\n"
                     f"scope={ctx.get('scope', 'all')} sent_id={ctx.get('sent_id')}\n"
                     f"{ctx['text']}")
    parts.append("【問題】\n" + body.message)
    return "\n\n".join(parts)


def _run_turn(case_id: str, body: ChatIn, emit: Any,
              case_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """跑一回合，逐筆呼叫 `emit(event_name, data)`，回傳 `done` 的內容。

    SSE 與 `?stream=0` **共用這個函式**，所以兩條路徑不會長歪：一次性模式只是把
    `emit` 收集起來而不是立刻送出。
    """
    started = time.monotonic()
    turn_id = f"turn-{uuid.uuid4().hex[:12]}"
    session_id = body.session_id or f"sess-{uuid.uuid4().hex[:12]}"
    history = _SESSIONS.get(session_id, [])
    truncated = False

    # 串流路徑已經在開流之前讀過一次了（讓 404/409 以 JSON 回），不重讀。
    if case_payload is None:
        case_payload = _load_case_payload(case_id, body.run_id)
    refbook = RefBook()
    retriever = build_retriever(settings.retriever_kind(), exclude_case=case_id)
    snapshot = load_snapshot()

    agent, tools = build_chat_agent(case_payload, refbook, retriever, snapshot, emit)
    answer = str(agent(_user_message(body, history)))

    # 模型講完才判燈。判定的輸入只有四樣，沒有一樣是問模型「你這句可不可信」。
    verdict = classify_answer(body.message, answer, refbook,
                              refine_used=tools.refine_used)

    history = history + [{"role": "承辦人", "text": body.message},
                         {"role": "助理", "text": answer}]
    if len(history) > _MAX_TURNS * 2:
        history = history[-_MAX_TURNS * 2:]
        truncated = True
    _SESSIONS[session_id] = history

    done = {
        "turn_id": turn_id,
        "session_id": session_id,
        "answer": answer,
        "memory": "on",
        "session_truncated": truncated,
        "model_id": settings.bedrock_model_id("draft"),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        **verdict.as_dict(),
    }
    return done


@router.post("/api/cases/{case_id}/chat")
def chat(case_id: str, body: ChatIn, request: Request):
    """承辦人在一份跑完的案子上追問。SSE；`?stream=0` 回一次性 JSON。

    **閘門順序是契約的一部分**：503（檔位）→ 讀 run（404/409/400/502）→ 才開串流。
    讀 run 也放在開串流之前，理由同 503——一個不存在的 run 應該回 404 JSON，
    不是開一條串流再丟一個 `error` 事件讓前端畫出一個空對話泡。
    """
    gate = _live_gate()
    if gate is not None:
        return gate
    _validate(body)

    stream = request.query_params.get("stream", "1") != "0"
    events: list[dict[str, Any]] = []
    seq = {"n": 0}

    def record(name: str, data: dict[str, Any]) -> dict[str, Any]:
        ev = {"event": name, "data": {"seq": seq["n"], **data}}
        seq["n"] += 1
        return ev

    if not stream:
        # 一次性模式：先把事件收集起來，全部跑完才回。沒有中間狀態。
        collected: list[dict[str, Any]] = []

        def emit(name: str, data: dict[str, Any]) -> None:
            collected.append(record(name, data))

        try:
            done = _run_turn(case_id, body, emit)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — 失敗照實回，不包成一則假回答
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
        done_ev = record("done", done)
        # `events[]` 是本來會逐筆發出的 tool_call／tool_result／token，**不含 done**
        # 本身（done 的欄位就攤在 body 頂層）。不得夾帶 SSE 的 `event:` 框架字串。
        return JSONResponse({**done_ev["data"], "events": collected})

    # 讀 run 的錯誤要在開串流之前浮出來：讀一次，讓 404/409/400/502 以 JSON 回，
    # 並把結果帶進 gen()——重讀一次不只是浪費，還可能讀到不同的狀態。
    case_payload = _load_case_payload(case_id, body.run_id)

    def gen():
        pending: list[dict[str, Any]] = []

        def emit(name: str, data: dict[str, Any]) -> None:
            pending.append(record(name, data))

        try:
            done = _run_turn(case_id, body, emit, case_payload)
        except Exception as e:  # noqa: BLE001
            # spec §4.6：失敗必須有一個**與 done 不同**的形狀。用 done 包一句道歉，
            # 前端會把它當一則正常回答並標燈——那正是 CONSTITUTION §1 要防的。
            for ev in pending:
                yield _frame(ev)
            yield _frame(record("error", {
                "turn_id": None,
                "stage": _stage_of(e),
                "error": f"{type(e).__name__}: {e}",
            }))
            return
        for ev in pending:
            yield _frame(ev)
        yield _frame(record("done", done))

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        # no-store 擋瀏覽器與中間層快取；X-Accel-Buffering 擋 nginx 把事件緩衝成一坨。
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _frame(ev: dict[str, Any]) -> str:
    """SSE wire 格式，與既有 `GET /api/runs/{id}/events` 完全相同。"""
    return f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"


def _stage_of(e: Exception) -> str:
    """spec §4.6 的四個 stage。**分錯會讓畫面把一種失敗說成另一種。**

    用類名／模組名比對而不 import `backend.llm`——api 層不該把模型客戶端拉進 import 圖
    （比照 `backend/api/app.py` 的 `_translate()` 既有做法）。

    **認不出來的例外歸 `internal`，不歸 `tool`。** 原版的預設值是 `tool`，2026-09-12
    實測踩到：把 `BEDROCK_MODEL_ID_DRAFT` 指到不存在的 model id，botocore 拋
    `ValidationException`（不是 `LLMError`、也不在下面任何一條），落到預設值變成 `tool`
    ——依 spec §4.6 的表，前端會顯示「查詢來源失敗」，**但壞的是模型**。
    猜一個具體的 stage 比誠實說「內部錯誤」更糟：前者把人帶去查檢索，後者至少讓人去看
    原文。`internal` 的前端行為就是照實顯示 `error` 原文，那正是未知失敗該有的處置。

    工具失敗其實大多到不了這裡——`ChatTools._search()` 自己攔下檢索例外並回一段說明給
    模型（見 `backend/llm/chat.py`）。所以 `tool` 只留給明確判定得出來的情況。
    """
    name = type(e).__name__
    module = type(e).__module__ or ""
    if name == "LLMError":
        return "model"
    if isinstance(e, (ConnectionError, TimeoutError)):
        return "transport"
    # botocore／boto3 的例外從 agent 這條路上來就是模型呼叫失敗：檢索的例外在工具層
    # 就被攔掉了，不會冒到這裡。ValidationException／ThrottlingException／
    # AccessDeniedException 都屬於這一類。
    if module.startswith("botocore") or module.startswith("boto3"):
        return "model"
    if isinstance(e, (KeyError, ValueError, AttributeError, TypeError)):
        return "internal"
    return "internal"
