"""唯一允許 import strands 的檔案（spec 2026-09-07 D1）。

對外只有三個函式：extract_intake()、draft_sentences()、model_ids()。
strands／pydantic／boto3 的 import 全部在函式內——fixture 模式與測試路徑永遠不會觸碰它們，
所以一台只裝了 python3 的機器也 import 得動本檔（模組頂層只有 stdlib 與 backend.config.settings）。
測試接縫是 _invoke_structured()：節點測試與本檔測試都只 monkeypatch 它。
"""
from __future__ import annotations

import json
import pathlib
import re
import time
from typing import Any, Callable

from backend.config import settings

PROMPTS = pathlib.Path(__file__).parent / "prompts"
INTAKE_FIELDS = (
    "no", "type", "person", "org", "d1", "d2", "d3", "agent", "note",
    "service_method", "transit_days", "interested_party",
)
SERVICE_METHODS = ("personal", "deposit", "public")
MAX_SENTENCES_PER_SLOT = 12

# Bedrock 的 document 內容區塊對 name 有字元限制（英數、空白、連字號、括號、方括號）。
# 中文檔名一律會被打回，所以送出前先過濾；名字只是給模型看的標籤，改掉不影響引用驗證
# （引用驗證看的是 facts_excerpt 的 quote_ref，那裡保留原始檔名）。
_DOC_NAME_FORBIDDEN = re.compile(r"[^A-Za-z0-9\-\(\)\[\] ]")
_DOC_NAME_MAXLEN = 60


class LLMError(RuntimeError):
    """模型呼叫失敗（重試耗盡、schema 不符、輸出違反值域）。呼叫端不得吞掉改吐 fixture。"""


def model_ids() -> dict[str, str | None]:
    return {
        "provider": settings.model_provider(),
        "extract": settings.bedrock_model_id("extract"),
        "draft": settings.bedrock_model_id("draft"),
    }


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def _safe_doc_name(name: str) -> str:
    """把任意檔名壓成 Bedrock document 區塊收得下的 name。空字串退回 "doc"。"""
    return _DOC_NAME_FORBIDDEN.sub("_", name)[:_DOC_NAME_MAXLEN] or "doc"


def _load_model(model_kind: str):
    """MODEL_PROVIDER=bedrock（預設）| openai。region 與 model id 一律顯式帶入。"""
    provider = settings.model_provider()
    if provider == "openai":
        import os

        from strands.models.openai import OpenAIModel

        model_id = os.environ.get("OPENAI_MODEL_ID")
        if not model_id:
            raise LLMError("MODEL_PROVIDER=openai 需要 OPENAI_MODEL_ID（見 .env.example）")
        return OpenAIModel(
            client_args={"api_key": os.environ["OPENAI_API_KEY"]},
            model_id=model_id,
            params={"max_tokens": 8000, "temperature": 0},
        )
    from strands.models.bedrock import BedrockModel

    model_id = settings.bedrock_model_id(model_kind)
    region = settings.aws_region()
    if not model_id or not region:
        raise LLMError(f"缺 BEDROCK_MODEL_ID_{model_kind.upper()} 或 AWS_REGION（見 .env.example）")
    return BedrockModel(model_id=model_id, region_name=region, temperature=0.0, max_tokens=8000)


def _invoke_structured(system: str, user: str, schema_name: str, tools: list | None = None,
                       model_kind: str = "extract",
                       attachments: list[tuple[str, bytes]] | None = None) -> tuple[dict, dict | None]:
    """一次 Strands 呼叫 → (dict, usage)。**測試接縫**：測試只 monkeypatch 這個函式。

    attachments 是 [(檔名, PDF bytes)]；有值時 prompt 改成內容區塊清單，讓模型直接讀頁面
    （掃描件沒有文字層，只能走視覺讀）。
    """
    from strands import Agent

    from backend.llm import schemas

    schema = getattr(schemas, schema_name)
    agent = Agent(model=_load_model(model_kind), system_prompt=system, tools=tools or [], callback_handler=None)
    prompt: Any = user
    if attachments:
        prompt = [{"text": user}] + [
            {"document": {"format": "pdf", "name": _safe_doc_name(n), "source": {"bytes": b}}}
            for n, b in attachments
        ]
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
        if f == "service_method" and v is not None and v not in SERVICE_METHODS:
            raise LLMError(f"抽取結果 service_method={v!r} 不在 {SERVICE_METHODS}")
        if f == "transit_days":
            v = int(v) if v not in (None, "") else 0
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
    allowed = {law["id"] for law in context.get("laws", [])} | {c["id"] for c in context.get("cases", [])}
    tool_calls: list[dict] = []
    tools: list = []
    if retrieve_fn is not None:
        from strands import tool

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
    raw, usage = _with_retries(lambda: _invoke_structured(system, user, "DraftResult", tools, "draft"),
                               retries, backoff_s)
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
