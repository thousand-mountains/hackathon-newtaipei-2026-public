"""N4 檢索節點（純檢索，零 LLM 生成）。

兩條通道，誠實程度不同，**必須分開講**：

**通道 A：法規查表（真實邏輯）**
從 `laws-snapshot.json` 查條號存在性。查得到就是查得到，`verified=true`。
但快照只索引條號、不含條文原文，所以 `q`（條文原文）欄位一律留空並附說明——
**不由系統補寫條文文字**，那會變成編造。

**通道 B：相似歷史案（有檢索器才有內容）**
CONSTITUTION §2 的直接要求。編排層有注入 `ctx.retriever`（`RETRIEVER=kb`）時，
這條通道回 Managed KB 的命中，`outcome` 照檔名／主文抄、`verified` 一律 False；
沒有注入時**回空 list** 並標「庫外，未驗證」——不編造任何案號、案由、相似度分數。
兩種情形的說法不同，畫面上要分得出來是「查無」還是「查不了」。
"""
from __future__ import annotations

import re
import time
from collections import Counter as _count
from typing import Any

from backend.config import settings
from backend.orchestrator.state import CaseState, NodeCtx, NodeResult
from backend.retrieval.base import UnavailableRetriever
from backend.retrieval.lawtable import LawTableRetriever

SIMILAR_CASE_UNAVAILABLE_REASON = (
    "相似歷史案檢索需要已建索引的決定書庫（賽方資料集之歷史訴願決定書，與市府公開之"
    "新北訴願決定書全量）。本次執行未接上該庫（RETRIEVER 非 kb），無任何相似案可回。"
    "此通道回空不是查無相似案，而是本系統目前無法檢索——標「庫外，未驗證」。"
)

# KB 呼叫本身炸掉（throttle／權限／網路）。**三種說法必須分得出來**（spec §7 第 2 列）：
#   「無資料集」  = 本機沒有可檢索的庫，根本沒查（SIMILAR_CASE_UNAVAILABLE_REASON）
#   「查無相似案」= 查了、KB 回 0 筆
#   「檢索失敗」  = 查了、呼叫炸了，本次執行不知道有沒有相似案
# 吞成前兩種任何一種都是對「發生過什麼」說謊（CONSTITUTION §1）。
SIMILAR_CASE_KB_FAILED_REASON = (
    "相似案檢索失敗（KB 不可用）：{error}。"
    "這不是「查無相似案」（那代表查過、KB 回 0 筆），也不是「無資料集」"
    "（那代表本機沒有可檢索的庫）——是檢索呼叫本身失敗，本次執行無從得知有無相似案。"
    "法條查表通道（通道 A）不受影響，照常有結果。"
)

ARTICLE_TEXT_UNAVAILABLE = "條文原文不在快照內（快照只索引條號），本系統不代為補寫條文文字，請對照全國法規資料庫。"


SUBSTANTIVE_ARTICLE_UNKNOWN = (
    "實體法條號無法由案情自動判定：N2 只認得出案型對應的法規『名稱』（例如建築法），"
    "條號要由原處分書的裁處依據判定，而 Phase 0 沒有 PDF 視覺抽取、卷證摘錄裡也沒有條號。"
    "本節點**不猜條號**——所以檢索結果會以程序面法條為主，實體面只列得出法規名。"
)

PRECEDENT_CHANNEL_NOTE = (
    "期間引擎的依據字串裡若含判解字號（例『最高行 108 判 531 意旨』），本節點不檢索——"
    "法條查表通道只查條號，判解白名單的比對在守門節點（N6）做。"
)


# 結果分布那一句：說清楚它是描述而不是預測。前端照這句畫，不自己另外編說法。
OUTCOME_SUMMARY_NOTE = (
    "以上為檢索到的相似案與知識庫同案型的**決定結果件數**（規則計數，零 LLM）。"
    "系統不就本案結果作推估，也不提供撤銷機率——那屬法律判斷，由承辦人認定。"
)


def _normalize_basis_citations(text: str, law_names: list[str]) -> list[str]:
    """把規則引擎宣告的法源字串正規化成可查表的引用形式。

    期間引擎的 `basis` 寫的是「行政程序法 74；最高行 108 判 531 意旨」「訴願法 17 → 民法 122」
    這種簡寫，查表用的 regex 認的是「法規名第N條」。這裡做的是**格式轉換，不是推論**：
    只有當「已知法規名 + 緊接著的數字」同時出現才轉，數字不會被搬到別的法規名底下，
    也不會替沒有數字的法規名補一個條號。轉不了的（判解字號、「以上各步」）就丟掉。
    """
    if not law_names:
        return []
    joined = "|".join(re.escape(n) for n in sorted(law_names, key=len, reverse=True))
    pattern = re.compile(rf"({joined})\s*(?:第\s*)?([0-9]+)(?:\s*之\s*([0-9]+))?")
    out: list[str] = []
    for m in pattern.finditer(text):
        cite = f"{m.group(1)}第{m.group(2)}條" + (f"之{m.group(3)}" if m.group(3) else "")
        if cite not in out:
            out.append(cite)
    return out


def build_query_sources(state: CaseState, law_names: list[str] | None = None) -> list[dict[str, Any]]:
    """組成查詢句的每一個來源，逐項標明它從哪個節點來（透明度用，也是回歸測試的鉤子）。

    **這裡列出的每一項都必須是「案情決定的」**——N1 抽取結果、N2 案型、N3 程序結果。
    草稿（N5）不在清單裡，也不准進來：拿草稿要引用什麼去查什麼，檢索就不是在佐證，
    而是在替答案背書（HANDOFF 五點五節的循環佐證問題）。
    """
    names = law_names or []
    cls = state.classification.get("class") or {}
    screen = state.screen or {}
    art77 = screen.get("art77") or {}
    deadline = screen.get("deadline") or {}
    intake = state.intake or {}

    sources: list[dict[str, Any]] = []

    case_type = cls.get("case_type") or ""
    if case_type:
        sources.append({"from": "n2.classification.class.case_type", "kind": "案型", "terms": [case_type]})
    if cls.get("law_hits"):
        sources.append(
            {
                "from": "n2.classification.class.law_hits",
                "kind": "案型對應法規名（無條號）",
                "terms": list(cls["law_hits"]),
                "note": SUBSTANTIVE_ARTICLE_UNKNOWN,
            }
        )

    if art77.get("clause"):
        # clause 形如 "77-2"：款次不是條號，查表只查第 77 條本身
        sources.append(
            {
                "from": "n3.screen.art77.clause",
                "kind": "程序不受理事由",
                "terms": ["訴願法第77條"],
                "note": f"命中款次 {art77['clause']}，款次由期間引擎算出。",
            }
        )

    basis_blob = "；".join(str(s.get("basis") or "") for s in (deadline.get("steps") or []))
    basis_cites = _normalize_basis_citations(basis_blob, names)
    if basis_cites:
        sources.append(
            {
                "from": "n3.screen.deadline.steps[].basis",
                "kind": "期間引擎逐步援引的法源",
                "terms": basis_cites,
                "note": PRECEDENT_CHANNEL_NOTE,
            }
        )

    # 卷證原文（N1）：真實案件的原處分裁處依據常寫在事實段裡，抓得到就查得到。
    record_blob = " ".join(
        [str(intake.get("note") or "")]
        + [str(x.get("text") or "") for x in (state.facts_excerpt or [])]
    ).strip()
    if record_blob:
        sources.append(
            {
                "from": "n1.facts_excerpt + intake.note",
                "kind": "卷證原文（原文照抄進查詢句，不改寫）",
                "terms": [record_blob],
            }
        )

    return sources


def build_query(state: CaseState, law_names: list[str] | None = None) -> str:
    """案例導向查詢句（architecture §7.2）。**只吃案情，不吃草稿。**"""
    parts: list[str] = []
    for src in build_query_sources(state, law_names):
        parts.extend(t for t in src["terms"] if t)
    return "；".join(parts)


def run(
    state: CaseState,
    ctx: NodeCtx,
    cited_laws: list[str] | None = None,
    extra_case_terms: list[str] | None = None,
) -> NodeResult:
    """`cited_laws`：**正式管線已不再使用**（2026-09-05 Ci 拍板改獨立檢索）。

    以前編排層會把 fixture 草稿即將引用的法條蒐集起來當查詢句，那等於
    「照著答案要引用什麼，去查什麼」——檢索永遠命中，但它佐證的是自己。
    現在 `graph.run_case()` 不再傳這個參數，查詢句一律由 `build_query()` 從
    N1／N2／N3 的結果組出來。

    參數保留的唯一理由是單元測試與未來的「人工補正查詢句」（承辦人手動加一條法規再查一次）。
    **不要把它接回草稿。**

    `extra_case_terms`：承辦人在「重新檢索」卡指定的查詢詞，**附加**在通道 B 的
    `case_query` 尾端（spec §5.5）。畫面上那個輸入框就在相似案旁邊，只讓它進通道 A
    等於功能名稱與實際行為不符（2026-09-07 覆核 I-5）。附加而不取代：案情組出來的
    查詢句仍然是主體，人指定的詞是補撈，不是換一個查法。
    """
    started = time.perf_counter()
    snapshot = ctx.snapshot or {}
    lawtable = LawTableRetriever(snapshot)
    similar = ctx.retriever if ctx.retriever is not None else UnavailableRetriever(
        "similar_cases", SIMILAR_CASE_UNAVAILABLE_REASON
    )
    law_names = list((snapshot.get("laws") or {}).keys())

    # ── 通道 A：法規查表（真實）──────────────────────────────────
    query_sources = build_query_sources(state, law_names)
    if cited_laws:
        # 呼叫端明確給的查詢詞（單元測試／人工補正）。標明來源，不混進案情來源裡。
        query_sources = [
            {"from": "caller.cited_laws", "kind": "呼叫端明確指定的查詢詞", "terms": list(cited_laws)}
        ] + query_sources
    query_text = "；".join(t for src in query_sources for t in src["terms"] if t)
    hits = lawtable.search(query_text, top_k=50)
    # 同一條法條在草稿中重複引用只列一次（id 依首次出現順序編號，deterministic）
    deduped: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for h in hits:
        key = (h.payload.get("law", ""), h.payload.get("article", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)

    laws: list[dict[str, Any]] = []
    for i, h in enumerate(deduped, start=1):
        laws.append(
            {
                "id": f"L{i}",
                "t": h.title,
                # 條文原文留白，不補寫
                "q": None,
                "q_note": ARTICLE_TEXT_UNAVAILABLE,
                "src": h.source,
                "origin": "retrieval",
                "verified": h.verified,
                "note": h.note,
                # lamp 由 N6 覆寫，檢索只給候選（architecture §6.2）
                "lamp": None,
                "tag": None,
                "law": h.payload.get("law"),
                "article": h.payload.get("article"),
            }
        )

    # ── 通道 B：相似歷史案 ────────────────────────────────────────
    # 查詢句只用卷證原文（事實段 + intake.note）+ 案型；不用改寫句（改寫句會漏撤銷案，AppealAssist ask 模式實測）。
    record_terms = [str(x.get("text") or "") for x in (state.facts_excerpt or [])] + [
        str((state.intake or {}).get("note") or "")
    ]
    case_query = "；".join(
        [t for t in record_terms if t]
        + ([(state.classification.get("class") or {}).get("case_type") or ""] if state.classification else [])
        # 承辦人指定的查詢詞附在尾端，標明它是人加的（logs 會寫出來）
        + [t for t in (extra_case_terms or []) if t]
    )
    # KB 呼叫失敗只降級通道 B，通道 A 照常（spec §7 第 2 列）。
    # 不包 try/except 的話 boto3 的 throttle／權限例外會一路冒到 run_case → run_failed → 502，
    # 「KB 抖一下」就等於整份分析失敗——而通道 A 明明已經算完了。
    case_hits: list[Any] = []
    kb_error: str | None = None
    if case_query or query_text:
        try:
            # 不傳 prefix：收哪些前綴由 `settings.similar_case_quota()` 單點決定
            # （這裡再寫一份就會與它漂移）。N4 只說「我要相似案」，不說去哪撈。
            case_hits = similar.search(case_query or query_text, top_k=5)
        except Exception as e:  # noqa: BLE001 — 檢索器可能是 boto3，例外型別由 SDK 決定
            kb_error = f"{type(e).__name__}: {e}"
    cases: list[dict[str, Any]] = []
    for i, h in enumerate(case_hits, start=1):
        p = h.payload or {}
        cases.append(
            {
                "id": f"C{i}",
                "t": h.title,
                "sim": int(round(h.score * 100)),
                "tag": None,          # 歸 N6
                "d": None,            # 同/異說明模板：Phase S
                "src": h.source,
                "origin": "retrieval",
                "verified": h.verified,
                "note": h.note or "KB 命中，未對資料集實檔驗證（manifest 對檔為加值層 Task 9）",
                "outcome": p.get("outcome"),
                "provenance": p.get("provenance"),
                # 案型：公開爬蟲那批的檔名只有「案號_結果」，卡片標題看不出是什麼案子，
                # 這個欄位是唯一的來源（來自 KB 側檔）。沒有就 None，不猜。
                "category": p.get("category"),
                "text": p.get("text", "")[:600],
                "lamp": None,
            }
        )
    # 有注入檢索器但呼叫炸了，就不是「可用」——available 必須說的是這一次的實情
    similar_available = ctx.retriever is not None and kb_error is None
    similar_meta: dict[str, Any] = dict(similar.meta())
    # 兩批各命中幾筆，payload 自己說得出來（「賽方資料集用在哪」不該靠人去數 cases[]）。
    # 從實際回來的命中數，不是從配額設定值算——配額是上限，實際可能少於它。
    hits_by_provenance: dict[str, int] = {}
    for c in cases:
        key = c["provenance"] or "unknown"
        hits_by_provenance[key] = hits_by_provenance.get(key, 0) + 1
    similar_meta["hits_by_provenance"] = hits_by_provenance
    if kb_error is not None:
        similar_meta.update(
            {
                "available": False,
                "label": "檢索失敗，未驗證",
                "reason": SIMILAR_CASE_KB_FAILED_REASON.format(error=kb_error),
                "error": kb_error,
                "hits": 0,
                "hits_by_provenance": {},
                "verified": False,
            }
        )

    retrieval_meta = {
        "backend": (
            "lawtable+bedrock_kb"
            if similar_available
            else "lawtable_only（相似案通道呼叫失敗）"
            if kb_error is not None
            else "lawtable_only"
        ),
        "law_channel": lawtable.meta(),
        "similar_case_channel": similar_meta,
        "recall_at5_last_eval": None,
        "recall_note": "檢索評測需要 leave-one-out 的歷史決定書資料集，本機無資料，未量測。",
        "kb_snapshot_date": snapshot.get("generated"),
        "query_text": query_text,
        "case_query_text": case_query,
        # 人指定的查詢詞要跟案情組出來的部分分得開（誰加的，看得見）
        "case_query_extra_terms": [t for t in (extra_case_terms or []) if t],
        # 查詢句由哪些案情訊號組成，逐項可查（回歸測試 test_e2e 會驗這裡沒有草稿來源）
        "query_sources": query_sources,
        "query_independence": (
            "查詢句只由 N1 卷證抽取、N2 案型、N3 程序結果組成，不含 N5 草稿的任何內容。"
            "檢索是獨立佐證，不是照著草稿要引用什麼去查什麼。"
        ),
        "substantive_article_gap": SUBSTANTIVE_ARTICLE_UNKNOWN,
    }
    # ── 檢索到的這幾件，結果各是什麼（逐件計數，不算比率）─────────────
    #
    # 「相似案撤銷率 X%」是最容易被誤讀的一個數字：分母只有 top-K（通常 5），
    # 一件撤銷就是 20%、兩件就是 40%——那是雜訊不是統計；而且承辦人幾乎一定會
    # 把它讀成「本案有 X% 機率被撤銷」，那是系統對案件結果的預測
    # （CONSTITUTION：結論涉及法律判斷，不代為認定）。
    #
    # 所以這裡只給**計數**與**分母**，讓分母留在畫面上；要換算比率是讀的人的判斷。
    # `corpus` 那塊是同案型在整個知識庫裡的分布，逐批分開（兩批差一個數量級，
    # 見 settings.outcome_counts 的說明），附警語。
    run_case_type = ((state.classification or {}).get("class") or {}).get("case_type") or ""
    # 幕僚敘述那行「結果分布」與 payload 的 outcome_summary **算同一份**。
    # 分開算兩次的話，標籤（「未標示」）與計數遲早會漂，畫面與 payload 就會各說各話。
    retrieved_outcomes = dict(sorted(_count(c["outcome"] or "未標示" for c in cases).items()))
    outcome_summary = {
        "retrieved": {"n": len(cases), "counts": retrieved_outcomes},
        "corpus": settings.outcome_counts(run_case_type or None),
        "note": OUTCOME_SUMMARY_NOTE,
        "origin": "rule",
    }
    state.retrieval = {
        "laws": laws,
        "cases": cases,
        "retrieval_meta": retrieval_meta,
        "outcome_summary": outcome_summary,
    }

    elapsed = int((time.perf_counter() - started) * 1000)
    verified_n = sum(1 for l in laws if l["verified"])
    operator_term_logs = (
        [[f"含承辦人指定查詢詞：{'、'.join(t for t in extra_case_terms if t)}（附加於案情查詢句尾端）", ""]]
        if [t for t in (extra_case_terms or []) if t]
        else []
    )
    return NodeResult(
        ok=True,
        # 相似案通道不可用屬已知限制，不算節點失敗；但一定要 degraded 外顯
        degraded=not similar_available,
        degrade_reason=(
            None
            if similar_available
            else SIMILAR_CASE_KB_FAILED_REASON.format(error=kb_error)
            if kb_error is not None
            else "相似歷史案通道不可用（無資料集），僅法規查表通道有結果"
        ),
        data=state.retrieval,
        elapsed_ms=elapsed,
        narrative={
            "law": {
                "out": (
                    f"以案情獨立檢索命中 {len(laws)} 筆法規依據，其中 {verified_n} 筆條號可對回快照。"
                    f"（查詢句來自案型與程序審查結果，未參考草稿內容）"
                ),
                "logs": [
                    [f"查表來源：laws-snapshot.json（{len(snapshot.get('laws', {}))} 部法規，產製於 {snapshot.get('generated')}）", ""],
                    [f"查詢句來源：{'、'.join(s['kind'] for s in query_sources) or '（無）'}", ""],
                    ["條文原文未附：快照只索引條號，不代為補寫條文文字", "y"],
                    [SUBSTANTIVE_ARTICLE_UNKNOWN, "y"],
                    ["草稿實際引用了哪些法條，由守門節點逐句查核（見「草稿實際引用」清單），與本清單各自獨立", ""],
                ],
            },
            "case": {
                "out": (
                    f"相似歷史案：{len(cases)} 筆（{', '.join(sorted({c['provenance'] or '?' for c in cases})) or '無'}）。"
                    if similar_available
                    else "相似歷史案：檢索失敗，本次執行無結果（KB 不可用，非查無）。"
                    if kb_error is not None
                    else "相似歷史案：0 筆（庫外，未驗證）。"
                ),
                "logs": (
                    [
                        [f"查詢句：{case_query[:80]}…", ""],
                        *operator_term_logs,
                        [
                            # outcome 可能是 None（檔名讀不出主文），跟字串混在一起 sorted() 會炸，
                            # 所以在這裡補一個明講「讀不出來」的標籤，不假裝它是某個結果。
                            f"結果分布：{'；'.join(f'{k} {v} 件' for k, v in retrieved_outcomes.items())}",
                            "",
                        ],
                        ["決定結果照檔名／主文，不由模型推測（CONSTITUTION §2）", ""],
                        ["相似案為 KB 命中，尚未對資料集實檔逐筆驗證；燈號歸 N6", "y"],
                    ]
                    if similar_available
                    else [
                        [SIMILAR_CASE_KB_FAILED_REASON.format(error=kb_error), "r"],
                        [f"查詢句：{case_query[:80]}…（已送出，呼叫失敗）", ""],
                        *operator_term_logs,
                        ["本節點不編造任何案號或相似度分數（CONSTITUTION §2）", ""],
                    ]
                    if kb_error is not None
                    else [
                        [SIMILAR_CASE_UNAVAILABLE_REASON, "r"],
                        ["本節點不編造任何案號或相似度分數（CONSTITUTION §2）", ""],
                    ]
                ),
            },
        },
    )
