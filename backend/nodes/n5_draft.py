"""N5 主筆節點（LLM 位置：fixture 模板重播 / bedrock 即時生成 兩條分支）。

紅線（CONSTITUTION §1）：這個節點永遠不准把兩條分支的來源講混。

- `fixture`：重播合成案例檔的 `draft_fixture` 槽位。每一句都是模板，不是模型即時
  生成的，所以 `degraded=True`、`generation_mode="fixture_template_replay"`，
  `narrative` 也把這件事寫出來。
- `bedrock`：把 N1/N3/N4 的結果組成 context，交給
  `backend.llm.client.draft_sentences`。失敗直接往上拋 `LLMError`，
  **不吞掉改吐 fixture**——降級成重播而不講，就是最典型的分層誠實違規。
- 其餘（如 `local`）：仍未實作，`ctx.require_fixture()` 明確 raise NotImplementedError。

結構性封鎖（architecture §4.3）：
`requires_human_conclusion=true` 時，`conclusion` **根本不進 slots 陣列**。
不是「叫模型別寫」——prompt 是請求，schema 是約束。欄位不存在，組裝時就沒有位置放。
程式端還有第二道：`_take()` 只取 `slots` 內的槽位，模型多回的 `conclusion` 一律丟掉。

引用來源（CONSTITUTION §2）：模型能引的 id 只有 N4 給的 `laws[].id` ∪ `cases[].id`
∪ `retrieve_refs` 工具當次命中的 id，白名單由 client 端把關。本節點的工具只准查
`REF_PREFIXES` 兩個前綴（行政函釋、司法院釋字及行政判解），不准拿它去撈別的東西。

模型只寫句子，不寫「這句可不可信」：本節點不產 `l`（燈號）、不產 `why`、
不產爭點 ref（`I*`），那三樣由 N6 依規則生成。

`backend.llm.client` 在模組頂層 import（spec 2026-09-07 D8）：它自己用 try/except
守住 strands，沒裝也 import 得動，所以本檔不必把 import 藏進 bedrock 分支。

註：N5 依賴 backend.llm 是設計如此；**零 LLM 依賴的紅線管的是 N2/N3/N4/N6**
（CONSTITUTION §4，由 run_all 的 scan_llm_import_graph 把關）。
"""
from __future__ import annotations

import time
from typing import Any

from backend.config import settings
from backend.llm import client as llm_client
from backend.orchestrator.narrative import build_doc_skeleton
from backend.orchestrator.state import CaseState, NodeCtx, NodeResult

MAX_SENTENCES_PER_SLOT = 12

# retrieve_refs 工具只准查函釋與判解兩類前綴：它們是「可以引用的來源」，
# 決定書、卷證等不在此列（那些走 N4 的檢索通道，並且要另外做引用驗證）。
#
# 白名單的**唯一事實來源是 `settings.ref_prefixes()`**（目錄名跟著 corpus 走：
# 第三方 corpus 的函釋在 `行政函釋_全量/`，寫死 `行政函釋/` 只看得到 29 份）。
# 這裡與 `retrieval/kb.py` 都**不留模組層常數**：留一份就會有人拿它當真。
#
# 聊天層（`backend/llm/chat.py`）不得 import `backend.nodes.*`，但要用同一份前綴
# ——它直接叫 `settings.ref_prefixes()`。`backend.config.settings` 比兩者都底層，
# 誰都可以依賴它，不必為了共用而把常數留在檢索層。
#
# 這條通道要抓多深**不在這裡設**：撈取深度是檢索器的事，由 `retrieval/kb.py` 的
# `REF_FETCH_DEPTH` 決定（那裡有為什麼是 50 的實測依據）。節點層不再傳倍數。


def resolve_slots(requires_human_conclusion: bool) -> list[str]:
    """結論段在需人工判斷時，從 slots 陣列直接刪掉。"""
    slots = ["reasoning"]
    if not requires_human_conclusion:
        slots.append("conclusion")
    return slots


def run(state: CaseState, ctx: NodeCtx, case_fixture: dict[str, Any] | None = None) -> NodeResult:
    started = time.perf_counter()
    needs_human = bool(state.screen.get("requires_human_conclusion"))
    slots = resolve_slots(needs_human)

    if ctx.run_mode == "fixture":
        if case_fixture is None:
            raise ValueError("N5 fixture 模式需要 case_fixture（合成案例檔內容）")
        fixture_draft = case_fixture.get("draft_fixture")
        if not fixture_draft:
            raise ValueError(f"合成案例 {case_fixture.get('id')!r} 缺 draft_fixture 區塊")
        produced, over_limit = _take(fixture_draft, slots)
        dropped_conclusion = needs_human and bool(fixture_draft.get("conclusion"))
        generation = {
            "generation_mode": "fixture_template_replay",
            "generation_note": "離線重播：句子取自合成案例檔的模板槽位，非模型即時生成。",
            # 沒有真的呼叫模型，就不給 token 數字、不給 model id、不給工具紀錄（不腦補）
            "usage": None,
            "model_id": None,
            "tool_calls": [],
            "refs": [],
        }
        degraded, degrade_reason = True, "fixture 檔位：草稿為模板重播，非模型即時生成"
        mode_log = ["離線重播（fixture）：句子來自合成案例模板，非模型生成", "y"]
        extra_logs: list[list[str]] = []
        # fixture 的 cite_ids 是寫測資的人在 N4 跑之前手填的，語意無效（見 narrative
        # 模組頂端說明）→ 不帶進 doc[]，維持 AC1 的零變化。
        carry_cite_ids = False
        conclusion_source = "模板"
    elif ctx.run_mode == "bedrock":
        refs: list[dict[str, Any]] = []
        retrieve_fn = None
        if ctx.retriever is not None:

            def retrieve_fn(query: str) -> list[dict[str, Any]]:
                hits = ctx.retriever.search(query, filters={"prefix": settings.ref_prefixes()}, top_k=5)
                out: list[dict[str, Any]] = []
                for h in hits:
                    # id **一律由 N5 重新編號**，跨多次工具呼叫連續遞增。沿用檢索器的 id 會撞：
                    # 第一次補號成 R1 的命中，跟第二次檢索器自帶的 R1 會共用同一個 id，
                    # refs 兩筆不同來源對到一個號碼，模型引 R1 時對不出是哪一筆
                    # （引用必可驗，CONSTITUTION §2）。原始 id 留在 src_id 供追回檢索器那端。
                    out.append({
                        "id": f"R{len(refs) + len(out) + 1}", "src_id": h.id,
                        "t": h.title, "src": h.source,
                        "text": (h.payload or {}).get("text", ""),
                        "score": h.score, "origin": "retrieval",
                    })
                refs.extend(out)
                return out

        context = {
            "intake": state.intake,
            "facts_excerpt": state.facts_excerpt,
            "screen": {k: state.screen.get(k) for k in ("deadline", "art77", "requires_human_conclusion")},
            "laws": [{k: law.get(k) for k in ("id", "t", "law", "article")} for law in state.retrieval.get("laws", [])],
            "cases": [{k: c.get(k) for k in ("id", "t", "outcome", "d")} for c in state.retrieval.get("cases", [])],
        }
        out = llm_client.draft_sentences(context, slots, retrieve_fn)  # LLMError 往上拋，不吞
        raw_slots = out["slots"]
        produced, over_limit = _take(raw_slots, slots)
        dropped_conclusion = needs_human and bool(raw_slots.get("conclusion"))
        generation = {
            "generation_mode": "bedrock_live",
            "generation_note": f"模型即時生成：{out['model_id']}",
            "usage": out["usage"],
            "model_id": out["model_id"],
            "tool_calls": out["tool_calls"],
            "refs": refs,
        }
        degraded, degrade_reason = False, None
        mode_log = [f"模型即時生成（{out['model_id']}），工具呼叫 {len(out['tool_calls'])} 次", ""]
        # 上游檢索全空時，白名單是空集合，模型引什麼都會被 client 清掉並標 unsupported。
        # 那個結果本身是誠實的，但畫面上看不出原因出在上游，所以這裡把原因寫出來。
        extra_logs = [] if (context["laws"] or context["cases"]) else [
            ["上游檢索結果為空：模型的所有引用都會被清空並標 unsupported", "y"]
        ]
        # bedrock 分支的 cite_ids 是模型看著 N4 的候選清單標的，語意有效
        # （architecture §6.2），而 client 端清掉的引用要以 `unsupported` 帶到 N6 判紅
        # （spec §5.3）。這個開關不翻，那道結構層防線就是空的（2026-09-07 覆核 I-3）。
        carry_cite_ids = True
        conclusion_source = "模型"
    else:
        ctx.require_fixture("N5 主筆節點")
        raise AssertionError("unreachable")

    doc = build_doc_skeleton(
        intake=state.intake,
        facts_excerpt=state.facts_excerpt,
        deadline_result=state.screen.get("deadline") or {},
        draft_slots=produced,
        requires_human_conclusion=needs_human,
        carry_draft_cite_ids=carry_cite_ids,
    )

    state.draft = {
        "template": "decision_v1",
        "slots_requested": slots,
        "slots": produced,
        "doc_skeleton": doc,
        "conclusion_dropped": dropped_conclusion,
        **generation,
    }

    sentence_n = sum(len(v) for v in produced.values())
    elapsed = int((time.perf_counter() - started) * 1000)
    return NodeResult(
        ok=True,
        data=state.draft,
        degraded=degraded,
        degrade_reason=degrade_reason,
        elapsed_ms=elapsed,
        narrative={
            "draft": {
                "out": f"組出 {sentence_n} 句草稿（槽位：{'、'.join(slots)}）。",
                "logs": [
                    mode_log,
                    *extra_logs,
                    [
                        "結論段已自 slots 陣列移除（結構性封鎖，非 prompt 請求）" if needs_human
                        # 依模式二選一：fixture 檔位沒有模型，寫「模型／模板」是文案倒退
                        # （5261f3c 前的原文就是「由模板組出」）。M-3。
                        else f"結論段由{conclusion_source}組出，待守門驗證",
                        "r" if needs_human else "",
                    ],
                    ["本節點不產燈號、不產 why、不產爭點 ref——那三樣歸守門", ""],
                    *[[f"槽位 {s} 超過 {MAX_SENTENCES_PER_SLOT} 句上限，已截斷", "y"] for s in over_limit],
                ],
            }
        },
    )


def _take(source: dict[str, Any], slots: list[str]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """只取要求的 slot，各截到上限。來源多回的 slot（例如封鎖時的 conclusion）一律不取。"""
    produced: dict[str, list[dict[str, Any]]] = {}
    over_limit: list[str] = []
    for slot in slots:
        sentences = list(source.get(slot, []))[:MAX_SENTENCES_PER_SLOT]
        if len(source.get(slot, [])) > MAX_SENTENCES_PER_SLOT:
            over_limit.append(slot)
        produced[slot] = sentences
    return produced, over_limit
