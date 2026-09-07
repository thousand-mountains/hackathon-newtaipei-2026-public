"""N1／N5 的 structured output schema（Pydantic）。

只有 backend/llm/ 可以 import 本檔（pydantic 是第三方套件，run_all.py 對 llm/ 具名豁免）。
schema 是約束不是請求：N5 的 DraftSentence **沒有** lamp／why／verified 欄位，模型想寫也沒位置。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

INTAKE_FIELDS = (
    "no", "type", "person", "org", "d1", "d2", "d3", "agent", "note",
    "service_method", "transit_days", "interested_party",
)
SERVICE_METHODS = ("personal", "deposit", "public")  # 對齊 backend/engine/deadline.py 的 SERVICE_METHODS


class FieldValue(BaseModel):
    """**文字欄位**（案號／案由／當事人／機關／日期／代理人／備註／送達方式）。

    `value` 刻意只收 `str | None`。舊版是 `str | int | bool | None`——那個聯集是為了
    `transit_days`（int）與 `interested_party`（bool）開的，卻套用在全部十二欄上，
    於是模型可以在 `type` 這種文字欄回一個 `True`：schema 收得下、N1 不檢查，
    一路流到 N2 的 `(intake.get("type") or "").strip()` 才炸 AttributeError
    （2026-09-08 第一次真模型呼叫實測到的）。

    schema 是**約束不是請求**：把型別收窄，模型看到的欄位定義本身就不再允許布林值。
    """

    value: str | None = Field(description="欄位值（字串）；卷證未載明時為 null")
    conf: float = Field(ge=0.0, le=1.0, description="0–1 信心值；抓不到就給低值，不要猜")
    quote: str | None = Field(default=None, description="卷證中支撐此值的原文片段（原文照抄）")


class LooseFieldValue(BaseModel):
    """`transit_days` 與 `interested_party` 專用：型別保持寬鬆。

    `client.extract_intake` 對這兩欄有**容錯轉換**（`"3"` → 3、任何值 → bool），
    收窄成 `int` / `bool` 反而會讓「模型把 3 寫成字串」這種無損情形變成驗證失敗、
    白白重試三次。寬鬆在這裡是刻意的，而且下游有明確的轉換規則接著。
    """

    value: str | int | bool | None = Field(description="欄位值；卷證未載明時為 null")
    conf: float = Field(ge=0.0, le=1.0, description="0–1 信心值；抓不到就給低值，不要猜")
    quote: str | None = Field(default=None, description="卷證中支撐此值的原文片段（原文照抄）")


class FactsExcerpt(BaseModel):
    text: str = Field(description="事實段原文，逐字照抄，不改寫不摘要")
    page: int | None = None
    quote_ref: str | None = Field(default=None, description="檔名#p頁碼")


class ExtractionResult(BaseModel):
    no: FieldValue
    type: FieldValue
    person: FieldValue
    org: FieldValue
    d1: FieldValue
    d2: FieldValue
    d3: FieldValue
    agent: FieldValue
    note: FieldValue
    service_method: FieldValue
    transit_days: LooseFieldValue
    interested_party: LooseFieldValue
    facts_excerpt: list[FactsExcerpt] = Field(default_factory=list)


class DraftSentence(BaseModel):
    t: str = Field(description="一句決定書文字，繁體中文")
    cite_ids: list[str] = Field(default_factory=list, description="只准引用 context 提供的 L*/C*/R* id")
    basis: str | None = Field(default=None, description="法源簡寫，例：訴願法第14條")
    source_kind: Literal["law", "case", "ref", "record"] = "law"


class DraftResult(BaseModel):
    reasoning: list[DraftSentence] = Field(default_factory=list)
    conclusion: list[DraftSentence] = Field(default_factory=list)
