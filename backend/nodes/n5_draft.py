"""N5 主筆節點（LLM 位置，Phase 0 為 fixture 模板組稿）。

紅線（CONSTITUTION §1）：
`RUN_MODE != "fixture"` 時**明確 raise NotImplementedError**。fixture 檔位下輸出的
每一句都來自合成案例檔的模板槽位，不是模型即時生成的，`narrative` 會把這件事寫出來。

結構性封鎖（architecture §4.3）：
`requires_human_conclusion=true` 時，`conclusion` **根本不進 slots 陣列**。
不是「叫模型別寫」——prompt 是請求，schema 是約束。欄位不存在，組裝時就沒有位置放。

模型只寫句子，不寫「這句可不可信」：本節點不產 `l`（燈號）、不產 `why`、
不產爭點 ref（`I*`），那三樣由 N6 依規則生成。
"""
from __future__ import annotations

import time
from typing import Any

from backend.orchestrator.narrative import build_doc_skeleton
from backend.orchestrator.state import CaseState, NodeCtx, NodeResult

MAX_SENTENCES_PER_SLOT = 12


def resolve_slots(requires_human_conclusion: bool) -> list[str]:
    """結論段在需人工判斷時，從 slots 陣列直接刪掉。"""
    slots = ["reasoning"]
    if not requires_human_conclusion:
        slots.append("conclusion")
    return slots


def run(state: CaseState, ctx: NodeCtx, case_fixture: dict[str, Any] | None = None) -> NodeResult:
    started = time.perf_counter()
    ctx.require_fixture("N5 主筆節點")

    if case_fixture is None:
        raise ValueError("N5 fixture 模式需要 case_fixture（合成案例檔內容）")
    fixture_draft = case_fixture.get("draft_fixture")
    if not fixture_draft:
        raise ValueError(f"合成案例 {case_fixture.get('id')!r} 缺 draft_fixture 區塊")

    needs_human = bool(state.screen.get("requires_human_conclusion"))
    slots = resolve_slots(needs_human)

    produced: dict[str, list[dict[str, Any]]] = {}
    over_limit: list[str] = []
    for slot in slots:
        sentences = list(fixture_draft.get(slot, []))[:MAX_SENTENCES_PER_SLOT]
        if len(fixture_draft.get(slot, [])) > MAX_SENTENCES_PER_SLOT:
            over_limit.append(slot)
        produced[slot] = sentences

    dropped_conclusion = needs_human and bool(fixture_draft.get("conclusion"))

    doc = build_doc_skeleton(
        intake=state.intake,
        facts_excerpt=state.facts_excerpt,
        deadline_result=state.screen.get("deadline") or {},
        draft_slots=produced,
        requires_human_conclusion=needs_human,
    )

    state.draft = {
        "template": "decision_v1",
        "slots_requested": slots,
        "slots": produced,
        "doc_skeleton": doc,
        "conclusion_dropped": dropped_conclusion,
        "generation_mode": "fixture_template_replay",
        "generation_note": "離線重播：句子取自合成案例檔的模板槽位，非模型即時生成。",
        # 沒有真的呼叫模型，就不給 token 數字（不腦補）
        "usage": None,
    }

    sentence_n = sum(len(v) for v in produced.values())
    elapsed = int((time.perf_counter() - started) * 1000)
    return NodeResult(
        ok=True,
        data=state.draft,
        degraded=True,
        degrade_reason="fixture 檔位：草稿為模板重播，非模型即時生成",
        elapsed_ms=elapsed,
        narrative={
            "draft": {
                "out": f"組出 {sentence_n} 句草稿（槽位：{'、'.join(slots)}）。",
                "logs": [
                    ["離線重播（fixture）：句子來自合成案例模板，非模型生成", "y"],
                    [
                        "結論段已自 slots 陣列移除（結構性封鎖，非 prompt 請求）" if needs_human else "結論段由模板組出，待守門驗證",
                        "r" if needs_human else "",
                    ],
                    ["本節點不產燈號、不產 why、不產爭點 ref——那三樣歸守門", ""],
                    *[[f"槽位 {s} 超過 {MAX_SENTENCES_PER_SLOT} 句上限，已截斷", "y"] for s in over_limit],
                ],
            }
        },
    )
