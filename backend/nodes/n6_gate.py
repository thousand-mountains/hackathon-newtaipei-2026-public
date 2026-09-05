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
            # 兜底（第三層，對抗覆核後新增）：C 型封鎖下，**模型寫的、一個可查證引用都沒有的
            # 句子**一律交人工並阻擋送出。
            #
            # 為什麼需要這一層：主文偵測不管寫得多好，本質上都是在比對字串，一定有盲點——
            # 覆核用 30 句真實主文打穿了 29 句。但**那 9 種繞法沒有一句帶引用**，
            # 因為主文本來就不引法條。這一層不看字串，所以沒有寫法能繞過它：
            # 「這個案子已經需要人來下結論了，而這句話又指不出任何可查證的依據」
            # ——這兩件事同時成立時，唯一安全的行為就是交人工。
            # 「有引用」不能只看抓到幾個 token：讀不懂的號碼（unparseable）與查無此號
            # （missing）都不是出處。覆核實測用一個捏造的函釋字號就讓兜底層失效。
            usable_cites = [c for c in cites if c.state not in (STATE_UNPARSEABLE, STATE_MISSING)]
            # 已經因為「查無此號」被擋的句子不重複列一條——同一句在畫面上出現兩個 blocker
            # 只會讓人以為是兩個問題。安全性不變：它本來就已經擋住了。
            already_blocked = any(c.state == STATE_MISSING for c in cites)
            if (
                needs_human
                and origin == "llm"
                and not s.get("placeholder")
                and not usable_cites
                and not already_blocked
            ):
                s["l"] = "r"
                s["tier"] = tier_of("human_required")
                s["why"] = WHY_UNSOURCED_WHILE_BLOCKED
                blockers.append(
                    {
                        "sentence_id": s["id"],
                        "reason": "unsourced_sentence_while_conclusion_blocked",
                        "detail": (
                            f"結論段已封鎖（本案需人工判斷），但 slot={s.get('slot')} 的模型生成句"
                            f"未附任何可查證的引用，系統無法確認它不是實質結論，已交人工。"
                        ),
                        "severity": "P0",
                    }
                )
            # 主文語句偵測（第一、二層）：**不限 slot、不限 origin**（引擎算式句與佔位句除外）。
            # 為什麼連 record（卷證直錄）也查：模型若把主文包裝成「引述原處分」就照樣穿過。
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
                                f"結論段已封鎖，但 slot={s.get('slot')}／origin={origin} 的句子命中主文型結構"
                                f"（{'；'.join(rules)}）。實質結論不得因為換個槽位或換個寫法就繞過封鎖。"
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
