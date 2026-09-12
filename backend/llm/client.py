"""唯一允許 import strands 的檔案（spec 2026-09-07 D1）。

對外只有三個函式：extract_intake()、draft_sentences()、model_ids()。
strands／pydantic 的 import 全部在**模組頂層**，用 try/except ImportError 守衛
（spec 2026-09-07 D8）：一台只裝了 python3 的機器仍然 import 得動本檔，缺席的名字
變成 None，由呼叫點吐 LLMError 說清楚缺什麼。**不准把 import 藏回函式內**——
那樣讀檔案的人看不出這個模組到底依賴什麼，錯誤也要跑到某條分支才炸。
測試接縫是 _invoke_structured()：節點測試與本檔測試都只 monkeypatch 它。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import threading
import time
from typing import Any, Callable

from backend.config import settings

try:
    from strands import Agent, tool
    from strands.models.bedrock import BedrockModel
except ImportError:  # 未安裝 strands：fixture 模式與測試路徑不需要它，缺時於呼叫點 raise LLMError
    Agent = tool = BedrockModel = None
try:
    from strands.models.openai import OpenAIModel
except ImportError:  # openai 分支只供開發期調 prompt，缺 extra 也不該擋住整個模組
    OpenAIModel = None
try:
    from backend.llm import schemas
except ImportError:  # pydantic 未安裝
    schemas = None

_STRANDS_MISSING = (
    "strands 未安裝：RUN_MODE=bedrock 需要 pip install strands-agents（見 requirements.txt）"
)

PROMPTS = pathlib.Path(__file__).parent / "prompts"
INTAKE_FIELDS = (
    "no", "type", "person", "org", "d1", "d2", "d3", "agent", "note",
    "service_method", "transit_days", "interested_party",
)
SERVICE_METHODS = ("personal", "deposit", "public")
# 文字欄＝十二欄扣掉兩個有容錯轉換的（transit_days／interested_party）。
# service_method 留在裡面：它的白名單檢查也擋得住非字串，但先擋型別的訊息更精確。
TEXT_INTAKE_FIELDS = tuple(f for f in INTAKE_FIELDS if f not in ("transit_days", "interested_party"))
MAX_SENTENCES_PER_SLOT = 12

# Bedrock 的 document 內容區塊對 name 有字元限制（英數、空白、連字號、括號、方括號）。
# 中文檔名一律會被打回，所以送出前先過濾；名字只是給模型看的標籤，改掉不影響引用驗證
# （引用驗證看的是 facts_excerpt 的 quote_ref，那裡保留原始檔名）。
_DOC_NAME_FORBIDDEN = re.compile(r"[^A-Za-z0-9\-\(\)\[\] ]")
_DOC_NAME_MAXLEN = 60


class LLMError(RuntimeError):
    """模型呼叫失敗（重試耗盡、schema 不符、輸出違反值域）。呼叫端不得吞掉改吐 fixture。"""


# 賽方規範要求 Bedrock 請求壓在 1 RPS 以下（team-brain〈2026-09-12 決賽環境規範〉）。
# MIN_INTERVAL_S 是可歸零的旋鈕：None＝讀環境變數（正式路徑），設 0＝關閉節流。
# 測試把它設 0 以免整套多跑好幾分鐘——做法與 `retrieval/kb.py` 的 RETRIEVE_INTERVAL_S 一致。
MIN_INTERVAL_S: float | None = None
_RATE_LOCK = threading.Lock()
_last_call_at = 0.0


def _throttle() -> None:
    """送出 Bedrock 請求前，等到與上一次至少隔 `settings.bedrock_min_interval_s()` 秒。

    為什麼要主動節流而不是靠 `_with_retries`：退避只在**已經被打回來之後**才生效，
    擋不住第一次就超速。評審面前吃 throttle 的代價遠大於多等一秒。

    **兩個已知限制，不要當成全域 1 RPS 的保證**：
    1. 這個閘只管本模組每一次 `_invoke_structured` 送出的請求。Strands 的 agent loop
       在**一次**呼叫內可能因工具往返而多次打模型，那些內部往返不經過這裡。
    2. `backend/retrieval/kb.py` 的 `RETRIEVE_INTERVAL_S` 是另一個獨立的閘。兩者相加
       仍可能超過 1 RPS——真要嚴格全域限速，得把兩邊併進同一個節流器。

    provider 不是 bedrock（openai 開發路徑）時不節流：那不受賽方規範約束。
    """
    if settings.model_provider() != "bedrock":
        return
    interval = MIN_INTERVAL_S if MIN_INTERVAL_S is not None else settings.bedrock_min_interval_s()
    if interval <= 0:
        return
    global _last_call_at
    # 鎖握著睡：多執行緒時要的就是序列化，否則各自算各自的間隔照樣會併發送出。
    with _RATE_LOCK:
        wait = _last_call_at + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


def model_ids() -> dict[str, str | None]:
    """回報**真的被呼叫的那個模型**，不是設定檔裡剛好有值的那個。

    provider 是 openai 時 `_load_model` 讀的是 `OPENAI_MODEL_ID`，`BEDROCK_MODEL_ID_*`
    一次都沒被用到；照報等於在 payload 上掛一個從未呼叫過的 AWS model id
    （CONSTITUTION §1 分層誠實）。openai 只有一個 model id，抽取與主筆共用。
    """
    provider = settings.model_provider()
    if provider != "bedrock":
        mid = settings.openai_model_id()
        return {"provider": provider, "extract": mid, "draft": mid}
    return {
        "provider": provider,
        "extract": settings.bedrock_model_id("extract"),
        "draft": settings.bedrock_model_id("draft"),
    }


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def _safe_doc_name(name: str) -> str:
    """把任意檔名壓成 Bedrock document 區塊收得下的 name。空字串退回 "doc"。"""
    return _DOC_NAME_FORBIDDEN.sub("_", name)[:_DOC_NAME_MAXLEN] or "doc"


def _load_model(model_kind: str):
    """MODEL_PROVIDER=bedrock（預設）| openai。region 與 model id 一律顯式帶入。

    **設定檢查一律排在碰 strands 之前**：這樣在沒裝 strands 的機器上，
    設定漏了會吐「缺哪個環境變數」，而不是一句無關的「strands 未安裝」。
    """
    provider = settings.model_provider()
    if provider == "openai":
        model_id = settings.openai_model_id()
        if not model_id:
            # 刻意不給預設 model id：程式不得出現任何 model id 的實際值，
            # 而且 openai 只供開發期調 prompt，寫死一個預設等於幫它偷偷上路。
            raise LLMError("MODEL_PROVIDER=openai 需要環境變數 OPENAI_MODEL_ID（本檔不預設任何 model id）")
        if not os.environ.get("OPENAI_API_KEY"):
            raise LLMError("MODEL_PROVIDER=openai 需要環境變數 OPENAI_API_KEY")

        if OpenAIModel is None:
            raise LLMError(_STRANDS_MISSING)
        return OpenAIModel(
            client_args={"api_key": os.environ["OPENAI_API_KEY"]},
            model_id=model_id,
            params={"max_tokens": 8000, "temperature": 0},
        )
    model_id = settings.bedrock_model_id(model_kind)
    region = settings.aws_region()
    if not model_id or not region:
        raise LLMError(f"缺 BEDROCK_MODEL_ID_{model_kind.upper()} 或 AWS_REGION（見 .env.example）")

    if BedrockModel is None:
        raise LLMError(_STRANDS_MISSING)
    return BedrockModel(model_id=model_id, region_name=region, temperature=0.0, max_tokens=8000)


def _invoke_structured(system: str, user: str, schema_name: str, tools: list | None = None,
                       model_kind: str = "extract",
                       attachments: list[tuple[str, bytes]] | None = None) -> tuple[dict, dict | None]:
    """一次 Strands 呼叫 → (dict, usage)。**測試接縫**：測試只 monkeypatch 這個函式。

    attachments 是 [(檔名, PDF bytes)]；有值時 prompt 改成內容區塊清單，讓模型直接讀頁面
    （掃描件沒有文字層，只能走視覺讀）。
    """
    if Agent is None or schemas is None:
        raise LLMError(_STRANDS_MISSING if Agent is None else "pydantic 未安裝：結構化輸出 schema 載不起來")
    schema = getattr(schemas, schema_name)
    agent = Agent(model=_load_model(model_kind), system_prompt=system, tools=tools or [], callback_handler=None)
    prompt: Any = user
    if attachments:
        prompt = [{"text": user}] + [
            {"document": {"format": "pdf", "name": _safe_doc_name(n), "source": {"bytes": b}}}
            for n, b in attachments
        ]
    _throttle()
    result = agent(prompt, structured_output_model=schema)
    obj = result.structured_output
    if obj is None:
        raise LLMError(f"模型未回傳符合 {schema_name} 的結構化輸出")
    usage = None
    metrics = getattr(result, "metrics", None)
    acc = getattr(metrics, "accumulated_usage", None) if metrics else None
    if acc:
        usage = {"input_tokens": acc.get("inputTokens"), "output_tokens": acc.get("outputTokens")}
    return obj.model_dump(), usage


def _with_retries(fn: Callable[[], tuple[dict, dict | None]], retries: int, backoff_s: float) -> tuple[dict, dict | None]:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 — 任何供應商錯誤都算一次失敗
            last = e
            if attempt < retries - 1:
                time.sleep(backoff_s * (attempt + 1))
    raise LLMError(f"模型呼叫失敗（重試 {retries} 次）：{type(last).__name__}: {last}")


def extract_intake(document_text: str, *, pdf_documents: list[tuple[str, bytes]] | None = None,
                   retries: int = 3, backoff_s: float = 1.5) -> dict:
    """卷證原文 → 12 個收文欄位（含逐欄信心值與原文片段）。

    pdf_documents 由 Task 3b 的上傳路由傳入：掃描件抽不出文字層，PDF 原件直接交給模型讀頁面。
    """
    system = _prompt("n1_extract")
    head = "若附有 PDF 文件，請直接閱讀其頁面內容（可能是掃描件）。\n\n" if pdf_documents else ""
    body = document_text.strip() or (
        "（無文字層，請閱讀附件 PDF）" if pdf_documents else "（卷證未提供文字內容）"
    )
    user = f"{head}以下是卷證原文（訴願書全文與原處分書）。請依 schema 抽出欄位。\n\n【訴願書全文】\n{body}"
    raw, usage = _with_retries(
        lambda: _invoke_structured(system, user, "ExtractionResult", None, "extract", attachments=pdf_documents),
        retries, backoff_s)
    intake: dict[str, Any] = {}
    conf: dict[str, float] = {}
    quotes: dict[str, str] = {}
    for f in INTAKE_FIELDS:
        fv = raw.get(f) or {}
        v = fv.get("value")
        if f in TEXT_INTAKE_FIELDS and v is not None:
            # 第二道（schema 已收窄，這裡擋供應商不理 schema 的情形）。
            # bool 先判：Python 的 isinstance(True, int) 是 True。
            if isinstance(v, bool):
                # 不 str() 混過去：「True」當成案由會進 N2 案型分類的 haystack，
                # 而且承辦人在畫面上看到的是一個沒有意義的字面值。
                raise LLMError(
                    f"抽取結果 {f}={v!r} 是布林值；文字欄位不可能是 true/false"
                )
            if isinstance(v, int):
                v = str(v)  # 案號寫成數字是無損轉換，不必炸
        if f == "service_method" and v is not None and v not in SERVICE_METHODS:
            raise LLMError(f"抽取結果 service_method={v!r} 不在 {SERVICE_METHODS}")
        if f == "transit_days":
            # 值域錯誤是「模型輸出不合格」，跟 throttling 那種暫時性失敗不同：
            # 重試三次也只會拿到同一份壞資料。跟 service_method 一樣直接 LLMError，
            # 訊息帶欄位名，不讓原生 ValueError 帶著 "invalid literal for int()" 浮上去。
            if v in (None, ""):
                v = 0
            else:
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise LLMError(f"抽取結果 transit_days={v!r} 不是整數") from None
        if f == "interested_party":
            v = bool(v) if v is not None else False
        intake[f] = v
        conf[f] = float(fv.get("conf", 0.0))
        if fv.get("quote"):
            quotes[f] = str(fv["quote"])
    return {
        "intake": intake,
        "conf": conf,
        "quotes": quotes,
        "facts_excerpt": list(raw.get("facts_excerpt") or []),
        "usage": usage,
        "model_id": model_ids()["extract"],
    }


def draft_sentences(context: dict, slots: list[str], retrieve_fn: Callable[[str], list[dict]] | None = None,
                    *, retries: int = 3, backoff_s: float = 1.5) -> dict:
    """context 由 N5 組好；retrieve_fn(query) -> [{"id": "R1", "src": ..., "text": ...}]。

    模型只能引用 context.laws[].id ∪ context.cases[].id ∪ 工具命中的 id；其他 cite_ids 一律清空並標 unsupported。"""
    base_allowed = {law["id"] for law in context.get("laws", [])} | {c["id"] for c in context.get("cases", [])}
    allowed = set(base_allowed)
    tool_calls: list[dict] = []
    tools: list = []
    if retrieve_fn is not None:
        if tool is None:
            raise LLMError(_STRANDS_MISSING)

        @tool
        def retrieve_refs(query: str) -> str:
            """查詢行政函釋與司法院釋字／行政判解的原文段落。只在需要引用函釋或判解時使用。

            Args:
                query: 用訴願人主張或爭點的關鍵字，繁體中文。
            """
            hits = retrieve_fn(query)
            tool_calls.append({"query": query, "hit_ids": [h["id"] for h in hits]})
            allowed.update(h["id"] for h in hits)
            if not hits:
                return "查無結果。請勿引用任何函釋或判解。"
            return "\n".join(f"[{h['id']}] {h.get('src','')}\n{h.get('text','')}" for h in hits)

        tools = [retrieve_refs]

    system = _prompt("n5_draft")
    payload = {k: context.get(k) for k in ("intake", "facts_excerpt", "screen", "laws", "cases")}
    user = (
        f"slots：{json.dumps(slots, ensure_ascii=False)}\n\n"
        f"【輸入】\n{json.dumps(payload, ensure_ascii=False, indent=1)}"
    )

    def attempt() -> tuple[dict, dict | None]:
        """一次嘗試。**每次都從乾淨的工具狀態開始**：`tool_calls` 與 `allowed` 是
        本函式的閉包狀態，由工具就地累加；不重設的話重試回來的 `tool_calls` 會混進
        上一次失敗嘗試的紀錄（報告虛報查了幾次），白名單也會留著上一輪命中的 id——
        等於放行一批「這次沒查到」的引用，違反引用必可驗（CONSTITUTION §2）。"""
        tool_calls.clear()
        allowed.clear()
        allowed.update(base_allowed)
        return _invoke_structured(system, user, "DraftResult", tools, "draft")

    raw, usage = _with_retries(attempt, retries, backoff_s)
    out_slots: dict[str, list[dict]] = {}
    for slot in slots:
        sentences = []
        for s in list(raw.get(slot) or [])[:MAX_SENTENCES_PER_SLOT]:
            asked = list(s.get("cite_ids") or [])
            kept = [c for c in asked if c in allowed]
            item = {"t": s["t"], "cite_ids": kept, "basis": s.get("basis"),
                    "source_kind": s.get("source_kind", "law")}
            if len(kept) != len(asked):
                item["unsupported"] = True
                item["dropped_cite_ids"] = [c for c in asked if c not in allowed]
            sentences.append(item)
        out_slots[slot] = sentences
    return {"slots": out_slots, "tool_calls": tool_calls, "usage": usage, "model_id": model_ids()["draft"]}
