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
    transit_days: FieldValue
    interested_party: FieldValue
    facts_excerpt: list[FactsExcerpt] = Field(default_factory=list)


class DraftSentence(BaseModel):
    t: str = Field(description="一句決定書文字，繁體中文")
    cite_ids: list[str] = Field(default_factory=list, description="只准引用 context 提供的 L*/C*/R* id")
    basis: str | None = Field(default=None, description="法源簡寫，例：訴願法第14條")
    source_kind: Literal["law", "case", "ref", "record"] = "law"


class DraftResult(BaseModel):
    reasoning: list[DraftSentence] = Field(default_factory=list)
    conclusion: list[DraftSentence] = Field(default_factory=list)
