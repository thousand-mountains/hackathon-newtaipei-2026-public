"""N6 守門節點（純程式，零 LLM——CONSTITUTION §4）。**這是真邏輯，不是 fixture。**

做五件事：

1. **引用四態比對**：逐句抽引用，對 laws-snapshot 定 ok／amended／out_of_scope／missing。
2. **燈號規則**：句子燈號 = 它所有引用狀態裡最嚴重的那一個；引擎句與卷證直錄句直接綠燈。
3. **cite_ids 解析**：草稿標的 `L*` 要能對回 N4 的檢索結果，對不到即進 blockers。
4. **C 型結論封鎖的事後覆核**：`requires_human_conclusion=true` 卻出現模型生成的結論句
   → 直接進 blockers 並記為 P0 訊號。
5. **爭點 ref 補掛與交接卡**：用 N3 的 `fact_issues` 關鍵詞比對到句子上補 `I*`；
   封鎖結論時產出至少 3 個具體交接問題（US-8 AC-8.2）。

`blockers` 非空 → 送出端點回 409（阻擋送出，不只警告）。
"""
from __future__ import annotations

import time
from typing import Any

from backend.config.origin_registry import tier_of
from backend.gate.citations import (
    STATE_AMENDED,
    STATE_MISSING,
    STATE_OK,
    STATE_OUT_OF_SCOPE,
    STATE_UNPARSEABLE,
    CitationChecker,
)
from backend.gate.lamps import (
    WHY_CONCLUSION_LEAK,
    WHY_UNSOURCED_WHILE_BLOCKED,
    attach_issue_refs,
    citation_states_for,
    detect_conclusion_like,
    lamp_for_states,
    lamp_stats,
    tier_for_sentence,
    why_for,
)
from backend.orchestrator.state import CaseState, NodeCtx, NodeResult

HANDOFF_BASE_QUESTIONS = [
    "本件實體認定與法律障礙的轉折點落在哪一個爭點？卷內事證是否足以支持該認定？",
    "擬採之處理結果（撤銷另處／駁回／不受理）為何？語氣需保留至何種程度？",
    "有無函釋、判解或機關內部見解支持所採之法律見解？請附字號供覆核。",
]


def _collect_law_ref_index(retrieval: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {l["id"]: l for l in retrieval.get("laws", [])}


def count_states(citations: list[dict[str, Any]]) -> dict[str, int]:
    counts = {STATE_OK: 0, STATE_AMENDED: 0, STATE_OUT_OF_SCOPE: 0, STATE_MISSING: 0}
    for c in citations:
        counts[c["state"]] = counts.get(c["state"], 0) + 1
    return counts


def run(state: CaseState, ctx: NodeCtx) -> NodeResult:
    started = time.perf_counter()
    snapshot = ctx.snapshot or {}
    checker = CitationChecker(snapshot)

    doc: list[dict[str, Any]] = state.draft.get("doc_skeleton") or []
    fact_issues: list[dict[str, Any]] = state.screen.get("fact_issues") or []
    needs_human = bool(state.screen.get("requires_human_conclusion"))
    law_index = _collect_law_ref_index(state.retrieval)

    all_citations: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []

    for block in doc:
        for s in block.get("ss", []):
            origin = s.get("origin")
            text = s.get("t") or ""
            basis = s.get("basis") or ""

            # 引用抽自句子本文與 basis 欄位（basis 常是「訴願法 14 I」這類法源標註）。
            # 同一句裡本文與 basis 標同一個引用時只算一次，避免 blockers 重複列。
            cites = []
            seen_raw: set[tuple[str, str]] = set()
            for c in checker.check_text(f"{text}\n{basis}"):
                key = (c.kind, c.raw)
                if key in seen_raw:
                    continue
                seen_raw.add(key)
                cites.append(c)
            states = citation_states_for(cites)
            # 「有出處」必須是**可查證**的出處。`out_of_scope`（庫外法規、任何函釋字號、
            # 白名單外判解）是系統明說「我驗不了」的東西，拿它當出處等於自己給自己背書——
            # 第三輪覆核用一個捏造的函釋字號就示範了這件事。
            usable_cites = [c for c in cites if c.state in (STATE_OK, STATE_AMENDED)]

            # cite_ids 解析：草稿標的 L* 必須對得回 N4 的檢索結果
            unresolved = [cid for cid in s.get("cite_ids", []) if cid.startswith("L") and cid not in law_index]

            if origin in ("engine", "record", "human_required"):
                # 可驗算層與卷證直錄層不吃引用燈號規則：引擎句本身就是算式，卷證句是原文
                lamp = "r" if origin == "human_required" else "g"
            else:
                lamp = lamp_for_states(states)
                if unresolved:
                    lamp = "r"

            s["l"] = lamp
            s["why"] = why_for(lamp, origin, states, unresolved=bool(unresolved))
            s["tier"] = (
                tier_of("human_required")
                if origin == "human_required"
                else tier_for_sentence(lamp, origin, states, has_citations=bool(cites))
            )
            s["citations"] = [c.as_dict() for c in cites]
            s["src"] = basis or (state.retrieval.get("retrieval_meta", {}) or {}).get("kb_snapshot_date")

            for c in cites:
                d = c.as_dict()
                d["sentence_id"] = s["id"]
                all_citations.append(d)
                if c.state in (STATE_MISSING,):
                    blockers.append(
                        {
                            "sentence_id": s["id"],
                            "reason": "citation_missing",
                            "detail": f"{c.raw}：{c.note}",
                        }
                    )
            for cid in unresolved:
                blockers.append(
                    {
                        "sentence_id": s["id"],
                        "reason": "cite_id_unresolved",
                        "detail": f"句子標註引用 {cid}，但檢索結果中不存在該編號。",
                    }
                )

            # ── C 型事後覆核 ────────────────────────────────────────
            # 結構性規則：封鎖狀態下，**任何槽位**出現模型生成的主文型語句都算繞過封鎖。
            # slot 是模型自己標的欄位，把它當成判準等於讓被管制的一方決定自己受不受管制。
            if needs_human and origin == "llm" and s.get("slot") == "conclusion" and not s.get("placeholder"):
                blockers.append(
                    {
                        "sentence_id": s["id"],
                        "reason": "conclusion_generated_while_blocked",
                        "detail": "requires_human_conclusion=true 卻存在模型生成的結論段（US-8 AC-8.3，P0）。",
                        "severity": "P0",
                    }
                )
            # 句子層註記（**不是**送出的守門，見下方 case 層封鎖）。
            #
            # 三輪對抗覆核的共同教訓：**只要最後一道防線是在比對字串，就一定有盲點。**
            # 第一輪 30 句主文穿過 29 句；第二輪 90 句穿過 55 句；第三輪又找到 §83 情況決定、
            # §93 停止執行、§84 損害賠償、「當事人請求X，本會同意」等一整批新寫法。
            # 每一輪修完都「這次總算窮舉了」，每一輪都被推翻。
            #
            # 所以這一層**不再承擔阻擋責任**，它的工作是「把看起來像主文的句子標紅給人看」。
            # 阻擋改由 case 層負責（`conclusion_requires_human`）：C 型案件一律不得送出，
            # 那條判準完全不看句子寫了什麼，因此沒有任何寫法能繞過。
            #
            # 這個分工也把誠實性擺正了：片語層漏抓時，後果是「少標一個紅」，不是「放行一份
            # 系統沒看過的法律結論」。
            if needs_human and not s.get("placeholder") and origin not in ("engine", "rule", "static"):
                rules = detect_conclusion_like(text)
                if rules:
                    s["l"] = "r"
                    s["why"] = WHY_CONCLUSION_LEAK
                    s["tier"] = tier_of("human_required")
                    blockers.append(
                        {
                            "sentence_id": s["id"],
                            "reason": "conclusion_like_text_outside_conclusion_slot",
                            "detail": (
                                f"slot={s.get('slot')}／origin={origin} 的句子命中主文型結構"
                                f"（{'；'.join(rules)}）。實質結論不得因為換個槽位或換個寫法就繞過封鎖。"
                            ),
                            "severity": "P0",
                        }
                    )
                elif origin == "llm" and not usable_cites:
                    # 沒命中片語層、又指不出可查證的出處：不宣稱它是主文，但也不能說它有出處。
                    s["tier"] = tier_of("human_required")
                    s["why"] = WHY_UNSOURCED_WHILE_BLOCKED

    # ── case 層封鎖：C 型案件一律不得送出 ────────────────────────────
    # **這是這份守門層唯一真正扛得住的判準，因為它不看句子寫了什麼。**
    #
    # 判準：`requires_human_conclusion=true`（由 N2/N3 的規則算出，見 lamps.requires_human_conclusion）
    # ⇒ 這份草稿沒有結論段、而結論需要人來下 ⇒ 它本來就不是一份可逕行送出的決定書。
    #
    # 為什麼是一條 case 層的紀錄、而不是每句一條：第三輪覆核量到句子層的「無出處即擋」
    # 會擋掉 22 句真實理由段裡的 20 句，blockers 清單被正常敘述句塞滿，真訊號反而看不見。
    # 送出與否是**案件**的性質，不是逐句累加出來的。
    if needs_human:
        blockers.append(
            {
                "sentence_id": None,
                "reason": "conclusion_requires_human",
                "detail": (
                    "本案結論涉及法律判斷，系統不生成結論段（C 型封鎖），草稿不得逕行送出；"
                    "請承辦人依交接卡認定後自行完成結論。"
                ),
                "severity": "P0",
            }
        )

    issue_refs = attach_issue_refs(doc, fact_issues)

    # 交接卡：封鎖結論時必須給至少 3 個具體問題 + 偵測訊號清單
    handoff: dict[str, Any] = {"questions": [], "signals": []}
    if needs_human:
        handoff["questions"] = [i["q"] for i in fact_issues if i.get("q")]
        for q in HANDOFF_BASE_QUESTIONS:
            if len(handoff["questions"]) >= 3:
                break
            handoff["questions"].append(q)
        handoff["signals"] = list(state.screen.get("human_conclusion_signals") or [])
        handoff["note"] = "系統偵測到結論涉及法律判斷，已停止生成結論段。以下問題供承辦人核對卷證後自行認定。"

    # ── 覆寫 N4 檢索結果的 lamp（檢索只給候選，燈號歸守門）───────────
    # 結構性規則：跨模組比對一律用**結構化的穩定鍵**（法規名｜條號），不用顯示字串。
    # `resolved_id`（`L-建築法-73`）與 `laws[].id`（`L1`）是兩個命名空間、交集為空；
    # 舊版靠 `raw == laws[].t` 的字串巧合對上，顯示格式一改就靜默失效還不報錯，
    # 然後走 else 用 N4 自己的 `verified` 填燈號——那等於檢索替守門發燈。
    # 對不到就**明講對不到**：黃燈 + 具名 tag + 進 blockers，絕不預設綠。
    cite_by_key: dict[str, dict[str, Any]] = {}
    for c in all_citations:
        key = c.get("ref_key")
        if key and key not in cite_by_key:
            cite_by_key[key] = c
    for law in state.retrieval.get("laws", []):
        law_name, article = law.get("law"), law.get("article")
        key = f"{law_name}|{article}" if law_name and article else None
        matched = cite_by_key.get(key) if key else None
        if matched:
            law["lamp"] = matched["lamp"]
            law["tag"] = matched["mark"]
            law["gate_ref_key"] = key
        else:
            law["lamp"] = "y"
            law["tag"] = "⚠ 未能對回守門結果"
            law["gate_ref_key"] = key
            blockers.append(
                {
                    "sentence_id": law["id"],
                    "reason": "retrieval_law_not_matched_by_gate",
                    "detail": (
                        f"檢索結果 {law['id']}（{law.get('t')}）的穩定鍵 {key!r} 對不到任何守門過的引用，"
                        f"燈號無法由守門認定。不預設燈號，請確認檢索與草稿引用是否脫節。"
                    ),
                    "severity": "P1",
                }
            )

    stats = lamp_stats(doc)
    counts = count_states(all_citations)

    state.gate = {
        "doc": doc,
        "citations": all_citations,
        "citation_counts": counts,
        "lamp_stats": stats,
        "blockers": blockers,
        "issue_refs": issue_refs,
        "handoff": handoff,
        "submit_allowed": not blockers,
    }
    state.assert_verified_invariant()

    elapsed = int((time.perf_counter() - started) * 1000)
    return NodeResult(
        ok=not blockers,
        data=state.gate,
        degraded=False,
        degrade_reason=None,
        elapsed_ms=elapsed,
        narrative={
            "qc": {
                "out": (
                    f"驗證 {len(all_citations)} 個引用（在庫 {counts.get('ok', 0)}／"
                    f"已修正 {counts.get('amended', 0)}／庫外未驗證 {counts.get('out_of_scope', 0)}／"
                    f"查無 {counts.get('missing', 0)}）。燈號 綠 {stats['g']}／黃 {stats['y']}／紅 {stats['r']}。"
                ),
                "logs": [
                    [
                        f"阻擋送出：{len(blockers)} 項" if blockers else "無阻擋項，可送出人工覆核",
                        "r" if blockers else "",
                    ],
                    *[[f"{b['reason']}｜{b['sentence_id']}：{b['detail']}", "r"] for b in blockers],
                    [
                        f"結論段已封鎖，交接卡列 {len(handoff['questions'])} 個問題" if needs_human else "結論段未封鎖",
                        "r" if needs_human else "",
                    ],
                    [f"爭點 ref 補掛 {len(issue_refs)} 處（規則比對，非模型認定）", ""],
                    ["「庫外，未驗證」代表本系統無法驗證，不代表該字號不存在", "y"],
                ],
            }
        },
    )
