"""N6 守門節點（純程式，零 LLM——CONSTITUTION §4）。**這是真邏輯，不是 fixture。**

做五件事：

1. **引用四態比對**：逐句抽引用，對 laws-snapshot 定 ok／amended／out_of_scope／missing。
2. **燈號規則**：句子燈號 = 它所有引用狀態裡最嚴重的那一個；引擎句與卷證直錄句直接綠燈
   （⚠️ 卷證直錄的綠燈代表「來源是卷證、非模型撰寫」，**不代表已與來源文件逐字比對**——
   本階段沒有這個控制，見 `WHY_RECORD` 與 HANDOFF-GATE.md 判斷卡 8）。
3. **cite_ids 解析**：草稿標的 `L*` 要能對回 N4 的檢索結果，對不到即進 blockers
   （`cite_id_unresolved`）；模型引了檢索結果之外的來源、已被 `llm/client.py` 白名單
   清掉的句子（`unsupported` 旗標）一律判紅並匯總成 `cite_id_unsupported`
   （spec §5.3，2026-09-07 新增）。**本節點只讀 doc 上的旗標，不 import backend.llm。**
4. **C 型結論封鎖的事後覆核**：`requires_human_conclusion=true` 卻出現模型生成的結論句
   → 直接進 blockers 並記為 P0 訊號。
5. **爭點 ref 補掛與交接卡**：用 N3 的 `fact_issues` 關鍵詞比對到句子上補 `I*`；
   封鎖結論時產出至少 3 個具體交接問題（US-8 AC-8.2）。

✅ **`submit_allowed` 現在有執行點了（2026-09-05）。** `POST /api/cases/{id}/submit`
會**重新跑一次六節點**再判斷，不採信前端送來的任何值；不通過就回 409 並附 blockers。
所以「後端會以 409 拒絕送出」是可查證的事實陳述，不再是不實的宣稱
（第四輪覆核抓到的舊版問題是：那時候根本沒有那支端點）。

它的**邊界**仍要講清楚：前端那顆送出鈕的 disabled 只是提示，改 DOM 或直接打 API 都繞得過；
唯一有意義的守門點是那支端點。而端點擋的是「這一次重算的結果」，
不是「這份草稿的內容正確」——非 C 型案件的結論段內容系統擋不了（見下方說明）。

⚠️ **C 型封鎖的開關上游有模型輸出。** `requires_human_conclusion` 由規則函式算出，
但它的輸入（`case_type`、`art77.clause`）來自 N1／N2 的抽取結果，`origin_registry`
已標明 `classification.class.case_type` 是 `llm_derived`。覆核實測：只要改掉 N1 抽的
一個日期欄位讓案件被判逾期，這個開關就會關掉、case 層封鎖不觸發。
**所以不能說「沒有任何寫法能繞過」——正確說法是「不能靠改草稿文字繞過，但可以靠
上游抽取錯誤繞過」。** 修法屬 C 型判準設計，見 HANDOFF-GATE.md 判斷卡 7。
"""
from __future__ import annotations

import time
from typing import Any

from backend.config.origin_registry import tier_of
from backend.config.settings import DEADLINE_INPUT_FIELDS, UNCONFIRMED_DEADLINE_INPUT_WHY
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
    WHY_UNSUPPORTED_CITATION,
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


# 檢索到、草稿未引用的法規卡標籤。中性措辭：不是燈號、不是警告，就是一個事實陳述。
RETRIEVED_NOT_CITED_TAG = "檢索到，草稿未引用"


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
    # 模型引了檢索結果以外的來源、被 client 端清掉的句子（spec §5.3）。
    # N6 **只讀 doc 上的旗標**，不 import backend.llm——旗標由 N5 帶進來，
    # 這個節點仍然零 LLM 依賴（CONSTITUTION §4）。
    unsupported_hits: list[tuple[str, list[str]]] = []
    # 期間算式的輸入裡，哪幾欄還沒被承辦人確認（N3 已經算好放在 screen 上，
    # 這裡只是讀）。`BLOCK_DECISION_INPUT_FIELDS` 比期間輸入多一個 `note`——
    # note 影響的是事實爭點偵測，不進算式，所以要跟 `DEADLINE_INPUT_FIELDS` 取交集，
    # 否則會在算式旁邊掛一個跟算式無關的欄位名。
    unconfirmed_deadline_inputs = [
        f for f in (state.screen.get("unconfirmed_procedural_fields") or [])
        if f in DEADLINE_INPUT_FIELDS
    ]

    for block in doc:
        for s in block.get("ss", []):
            origin = s.get("origin")
            text = s.get("t") or ""
            basis = s.get("basis") or ""

            # 引用抽自句子本文與 basis 欄位（basis 常是「訴願法 14 I」這類法源標註）。
            # 同一句裡本文與 basis 標同一個引用時只算一次，避免 blockers 重複列。
            # 去重用**結構化鍵**（`Citation.dedup_key`），不用 `raw`：
            # 前導虛詞剝不乾淨時，同一筆函釋會以兩種 raw 出現而被算成兩筆。
            cites = []
            seen_keys: set[tuple] = set()
            for c in checker.check_text(f"{text}\n{basis}"):
                key = c.dedup_key
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                cites.append(c)
            states = citation_states_for(cites)
            # 「有出處」必須是**可查證**的出處。`out_of_scope`（庫外法規、任何函釋字號、
            # 白名單外判解）是系統明說「我驗不了」的東西，拿它當出處等於自己給自己背書——
            # 第三輪覆核用一個捏造的函釋字號就示範了這件事。
            usable_cites = [c for c in cites if c.state in (STATE_OK, STATE_AMENDED)]

            # cite_ids 解析：草稿標的 L* 必須對得回 N4 的檢索結果
            unresolved = [cid for cid in s.get("cite_ids", []) if cid.startswith("L") and cid not in law_index]
            # 白名單外的引用已被清掉（`llm/client.py`），這裡讀旗標判紅。
            # 清掉之後那句話就沒有出處了，不能因為「欄位是空的」而靜靜當作無引用句。
            unsupported = bool(s.get("unsupported"))
            dropped = [str(c) for c in (s.get("dropped_cite_ids") or [])]

            if origin in ("engine", "record", "human_required"):
                # 可驗算層與卷證直錄層不吃引用燈號規則：引擎句本身就是算式，卷證句是原文
                lamp = "r" if origin == "human_required" else "g"
            else:
                lamp = lamp_for_states(states)
                if unresolved:
                    lamp = "r"
            # 不分 origin 一律往紅的方向走：旗標只會出現在草稿句上，
            # 萬一它出現在別的 origin 上，保守判紅也是對的方向。
            if unsupported:
                lamp = "r"

            s["l"] = lamp
            s["why"] = (
                WHY_UNSUPPORTED_CITATION.format(dropped="、".join(dropped) or "未記錄")
                if unsupported
                else why_for(lamp, origin, states, unresolved=bool(unresolved))
            )
            # HACK-S-17：期間算式的輸入若未經承辦人確認，由算式自己講出來。
            # 只掛在 origin=engine 的句子上——它們是畫面上最像「已驗證」的東西，
            # 而且它們的值**完全**由那些未確認欄位決定。燈號與層級不動：
            # 算式仍然可逐步覆核，改燈會把「算式有疑義」和「輸入沒確認」混為一談。
            if origin == "engine" and unconfirmed_deadline_inputs:
                s["why"] = (s["why"] or "") + " " + UNCONFIRMED_DEADLINE_INPUT_WHY.format(
                    fields="、".join(unconfirmed_deadline_inputs)
                )
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
            if unsupported:
                unsupported_hits.append((s["id"], dropped))
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
            # 那條判準完全不看句子寫了什麼，因此**不能靠改草稿文字繞過**
            #（但它的開關上游有 N1／N2 的抽取結果，抽錯仍會讓封鎖不觸發——見檔首說明）。
            #
            # 這個分工也把誠實性擺正了：片語層漏抓時，後果是「少標一個紅」，不是「放行一份
            # 系統沒看過的法律結論」。
            # 非 C 型案件也要跑偵測，但**語意不同**：那種案子本來就該有結論，
            # 主文出現在 `conclusion` 槽位是正常的。所以非 C 型只在「主文型語句出現在
            # 結論槽位以外」時留下 `conclusion_like` 註記供覆核，不改燈號、不擋送出。
            #
            # 誠實說明其極限：非 C 型案件**無法**用文字判準區分「合法的結論」與
            # 「捏造的結論」——兩者長得一樣。這裡給的是提示，不是保證。
            # 第四輪覆核量到非 C 型下 26/26 捏造主文全綠，那個數字的根因是這件事，
            # 不是少了幾條規則。
            if (
                not needs_human
                and not s.get("placeholder")
                and origin not in ("engine", "rule", "static")
                and s.get("slot") != "conclusion"
            ):
                rules = detect_conclusion_like(text)
                if rules:
                    s["conclusion_like"] = rules

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
                elif origin == "llm" and not usable_cites and not unsupported:
                    # 沒命中片語層、又指不出可查證的出處：不宣稱它是主文，但也不能說它有出處。
                    #
                    # `unsupported` 的句子排除在外：它的 why 已由上面寫成「模型引了 X、
                    # 已清除」，那是更具體的同一件事。用封鎖文案蓋掉會讓承辦人看不出
                    # 可查證性的破口在哪。燈號（r）與層級（請人工判斷）在主路徑已經定了，
                    # 這裡不接手也不會放寬。
                    s["tier"] = tier_of("human_required")
                    s["why"] = WHY_UNSOURCED_WHILE_BLOCKED

    # ── 模型引用了檢索結果之外的來源 ─────────────────────────────────
    # spec §5.3 的「結構層第二道」。文字層的 `CitationChecker` 抓的是「這個法條號碼
    # 在不在快照裡」，抓不到「模型指了一個 N4 從來沒給過的 id」——後者是引用可驗性
    # 的另一種破口（CONSTITUTION §2）。匯總成**一條** blocker：這是同一件事的多個
    # 實例，逐句一條會把清單塞滿而讓真訊號看不見（第三輪覆核的教訓）。
    if unsupported_hits:
        dropped_all = sorted({c for _sid, ids in unsupported_hits for c in ids})
        blockers.append(
            {
                "sentence_id": None,
                "sentence_ids": [sid for sid, _ids in unsupported_hits],
                "reason": "cite_id_unsupported",
                "detail": (
                    f"{len(unsupported_hits)} 句（{'、'.join(sid for sid, _ in unsupported_hits)}）"
                    f"引用了檢索結果之外的來源（{'、'.join(dropped_all) or '未記錄'}），"
                    f"已由白名單清除。引用被清掉之後那些句子就指不出任何出處，不得逕行送出。"
                ),
                "severity": "P0",
            }
        )

    # ── 空草稿不得標成可送出 ─────────────────────────────────────────
    # `submit_allowed = not blockers` 只看 blockers，不看文件裡有沒有東西。
    # 第四輪覆核：把 reasoning／conclusion／facts 全清空 → 0 blockers → submit_allowed=True，
    # 畫面上還同時掛著一句紅燈的「未擷取到事實段」佔位句。空文件不是通過，是沒東西可審。
    # 只算「實質內容」：期間引擎的算式句不算——它們是規則自動產生的，
    # 一份只有算式、沒有任何事實段與理由段的文件，等於沒有可審查的標的。
    content_sentences = [
        s
        for block in doc
        for s in block.get("ss", [])
        if s.get("origin") in ("llm", "record")
        and not s.get("placeholder")
        and (s.get("t") or "").strip()
    ]
    if not content_sentences:
        blockers.append(
            {
                "sentence_id": None,
                "reason": "empty_draft",
                "detail": "草稿沒有任何實質內容句（全為佔位或空字串），無可審查之標的，不得標為可送出。",
                "severity": "P0",
            }
        )

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
    # 然後走 else 用 N4 自己的 `verified` 填燈號——**那等於檢索替守門發燈**。
    # 這條防線保留：對不到的一律不給燈號，絕不回頭去看 N4 的 `verified`。
    #
    # 但「對不到」有兩種，2026-09-05 N4 改成獨立檢索之後必須分開處理
    # （合併前這兩種是同一種，因為舊設計的 laws[] 永遠等於草稿引用，所以從不觸發）：
    #
    #   (a) 有穩定鍵、但草稿沒引用它 → **獨立檢索的正當結果，不是缺陷**。
    #       查詢句由案情組成，本來就會查到草稿沒寫的條文（例如期間引擎援引的民法 120）。
    #       這種卡片不給燈號——燈號屬於草稿裡的句子與引用，這張卡沒有對應的草稿引用，
    #       就沒有東西可以發燈。標成中性狀態，不進 blockers。
    #
    #   (b) 連穩定鍵都組不出來（缺 law／article）→ **真的是缺陷**。
    #       這種卡片永遠不可能被守門比對到，放著就是一個永久的比對盲區，
    #       正是覆核發現②那類 silent default 的溫床。大聲失敗。
    #
    # 草稿那一側的對應防線在下面的 `citation_not_keyed`。
    cite_by_key: dict[str, dict[str, Any]] = {}
    for c in all_citations:
        key = c.get("ref_key")
        if key and key not in cite_by_key:
            cite_by_key[key] = c
    for law in state.retrieval.get("laws", []):
        law_name, article = law.get("law"), law.get("article")
        key = f"{law_name}|{article}" if law_name and article else None
        matched = cite_by_key.get(key) if key else None
        law["gate_ref_key"] = key
        if matched:
            law["lamp"] = matched["lamp"]
            law["tag"] = matched["mark"]
            law["gate_status"] = "cited_and_gated"
            law["gate_note"] = "草稿有引用此條，燈號為守門逐句查核的結果。"
        elif key:
            # (a) 檢索到、草稿未引用
            law["lamp"] = None
            law["tag"] = RETRIEVED_NOT_CITED_TAG
            law["gate_status"] = "retrieved_not_cited"
            law["gate_note"] = (
                "本條由 N4 依案情獨立檢索命中，草稿並未引用它，因此沒有可供守門查核的句子——"
                "不給燈號（燈號只屬於草稿裡的句子與引用），也不代表草稿漏引或引用有誤。"
            )
        else:
            # (b) 組不出穩定鍵：這張卡永遠不可能被守門比對到
            law["lamp"] = None
            law["tag"] = "⚠ 無結構化鍵，無法對回守門"
            law["gate_status"] = "unkeyed"
            law["gate_note"] = "檢索結果缺 law／article 結構化欄位，跨模組比對無法進行。"
            blockers.append(
                {
                    "sentence_id": law["id"],
                    "reason": "retrieval_law_unkeyed",
                    "detail": (
                        f"檢索結果 {law['id']}（{law.get('t')}）缺結構化欄位（law／article），"
                        f"組不出穩定鍵，永遠無法對回守門結果。這是檢索輸出的缺陷，不是資料落差。"
                    ),
                    "severity": "P1",
                }
            )

    # ── 草稿側的對應防線：引用抽出來了、卻組不出穩定鍵 ────────────────
    # 這才是覆核發現②真正要防的東西：草稿裡有一個法條引用，但守門拿不到
    # 「法規名｜條號」這組結構化鍵，於是任何跨模組比對都只能「當作沒對到」而靜靜過去。
    # 條號解析不出來時 `check_law()` 已經給黃燈並寫明原因（那部分是誠實的），
    # 但**它不能只停在句子層**：一個無法被穩定比對的引用要被具名列出來，
    # 不能讓後面的人以為「沒出現在 blockers = 已經查過了」。
    for c in all_citations:
        if c.get("kind") == "law" and not c.get("ref_key"):
            blockers.append(
                {
                    "sentence_id": c.get("sentence_id"),
                    "reason": "citation_not_keyed",
                    "detail": (
                        f"草稿引用 {c.get('raw')!r} 抽得到、卻組不出穩定鍵（法規名｜條號），"
                        f"守門無法把它對回快照或檢索結果。不預設它是對的。"
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
                        f"不得送出：{len(blockers)} 項（送出端點會以 409 拒絕）" if blockers
                        else "無阻擋項；送出端點會重新判斷一次後放行",
                        "r" if blockers else "",
                    ],
                    *[
                        [
                            f"{b['reason']}｜"
                            f"{b['sentence_id'] or '、'.join(b.get('sentence_ids') or []) or '（全案）'}"
                            f"：{b['detail']}",
                            "r",
                        ]
                        for b in blockers
                    ],
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
