"""N1 抽取節點（LLM 位置，Phase 0 為 fixture 重播）。

紅線（CONSTITUTION §1）：
`RUN_MODE != "fixture"` 時**明確 raise NotImplementedError**，絕不靜默回一份
看起來像模型抽出來、其實是寫死的資料。fixture 模式下每個欄位都標 `origin="llm"`
並附信心值，讓下游與 UI 知道這是「模型讀出來的」而不是「人填的」。

fixture 模式的誠實限制：這個檔位是重播，`conf` 是 fixture 裡寫好的數字，
不是本次執行實際量到的信心，`narrative` 會把這件事寫出來。
"""
from __future__ import annotations

import time
from typing import Any

from backend.orchestrator.state import CaseState, NodeCtx, NodeResult

# 必填欄位與信心門檻（architecture §3.1）。門檻 0.80 是假設值，待真實資料實測校準。
REQUIRED_FIELDS = ("no", "type", "d2", "service_method")
CONF_THRESHOLD = 0.80


def run(state: CaseState, ctx: NodeCtx, case_fixture: dict[str, Any] | None = None) -> NodeResult:
    started = time.perf_counter()
    ctx.require_fixture("N1 抽取節點")

    if case_fixture is None:
        raise ValueError("N1 fixture 模式需要 case_fixture（合成案例檔內容）")
    extraction = case_fixture.get("extraction")
    if not extraction:
        raise ValueError(f"合成案例 {case_fixture.get('id')!r} 缺 extraction 區塊")

    intake: dict[str, Any] = dict(extraction.get("intake", {}))
    conf: dict[str, float] = dict(extraction.get("conf", {}))
    quotes: dict[str, Any] = dict(extraction.get("quotes", {}))
    facts_excerpt: list[dict[str, Any]] = list(extraction.get("facts_excerpt", []))

    low_conf = [f for f in REQUIRED_FIELDS if conf.get(f, 0.0) < CONF_THRESHOLD]
    missing = [f for f in REQUIRED_FIELDS if f not in intake or intake.get(f) in (None, "")]

    state.intake = intake
    state.intake_conf = conf
    # 每欄同時寫 origin。人工改過的欄位由 /api/cases/{id}/intake 覆寫為 "human"。
    state.intake_origin = {k: "llm" for k in intake}
    state.low_conf_fields = low_conf
    state.facts_excerpt = facts_excerpt

    degraded = bool(low_conf or missing)
    reason = None
    if degraded:
        reason = (
            f"必填欄位信心不足或缺漏（低信心：{low_conf or '無'}；缺漏：{missing or '無'}），"
            f"需人工表單補齊後才能續跑"
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    return NodeResult(
        ok=not degraded,
        data={
            "intake": intake,
            "conf": conf,
            "quotes": quotes,
            "low_conf_fields": low_conf,
            "facts_excerpt": facts_excerpt,
            # 事實段的唯一生產者是卷證原文摘錄；抓不到就留空交人工，不由 N5 生成
            "facts_excerpt_available": bool(facts_excerpt),
        },
        degraded=degraded,
        degrade_reason=reason,
        elapsed_ms=elapsed,
        narrative={
            "clerk": {
                "out": f"自卷證擷取 {len(intake)} 個欄位，事實段原文 {len(facts_excerpt)} 段。",
                "logs": [
                    [f"必填欄位 {len(REQUIRED_FIELDS)} 項，信心門檻 {CONF_THRESHOLD:.2f}", ""],
                    [
                        f"低信心欄位：{'、'.join(low_conf) if low_conf else '無'}",
                        "y" if low_conf else "",
                    ],
                    ["離線重播（fixture）：信心值為合成案例既有標註，非本次實測", "y"],
                ],
            }
        },
    )
