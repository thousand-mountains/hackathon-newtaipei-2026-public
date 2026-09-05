"""N4 檢索節點（純檢索，零 LLM 生成）。

兩條通道，誠實程度不同，**必須分開講**：

**通道 A：法規查表（真實邏輯）**
從 `laws-snapshot.json` 查條號存在性。查得到就是查得到，`verified=true`。
但快照只索引條號、不含條文原文，所以 `q`（條文原文）欄位一律留空並附說明——
**不由系統補寫條文文字**，那會變成編造。

**通道 B：相似歷史案（誠實回空）**
CONSTITUTION §2 的直接要求。賽方 101 份歷史決定書不在本機、不進 git、S3 不公開，
所以這條通道在 Phase 0 **回空 list**，並標「庫外，未驗證」。
不編造任何案號、案由、相似度分數——沒有就是沒有。
"""
from __future__ import annotations

import re
import time
from typing import Any

from backend.orchestrator.state import CaseState, NodeCtx, NodeResult
from backend.retrieval.base import UnavailableRetriever
from backend.retrieval.lawtable import LawTableRetriever

SIMILAR_CASE_UNAVAILABLE_REASON = (
    "相似歷史案檢索需要賽方資料集（歷史訴願決定書）。該資料集僅供競賽之用，"
    "不進 git、不在本機，本次執行無任何相似案可回。此通道回空不是查無相似案，"
    "而是本系統目前無法檢索——標「庫外，未驗證」。"
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


def run(state: CaseState, ctx: NodeCtx, cited_laws: list[str] | None = None) -> NodeResult:
    """`cited_laws`：**正式管線已不再使用**（2026-09-05 Ci 拍板改獨立檢索）。

    以前編排層會把 fixture 草稿即將引用的法條蒐集起來當查詢句，那等於
    「照著答案要引用什麼，去查什麼」——檢索永遠命中，但它佐證的是自己。
    現在 `graph.run_case()` 不再傳這個參數，查詢句一律由 `build_query()` 從
    N1／N2／N3 的結果組出來。

    參數保留的唯一理由是單元測試與未來的「人工補正查詢句」（承辦人手動加一條法規再查一次）。
    **不要把它接回草稿。**
    """
    started = time.perf_counter()
    snapshot = ctx.snapshot or {}
    lawtable = LawTableRetriever(snapshot)
    similar = UnavailableRetriever("similar_cases", SIMILAR_CASE_UNAVAILABLE_REASON)
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

    # ── 通道 B：相似歷史案（誠實回空）────────────────────────────
    cases = similar.search(query_text, top_k=3)
    assert cases == [], "相似案通道在 Phase 0 必須回空"

    retrieval_meta = {
        "backend": "lawtable_only",
        "law_channel": lawtable.meta(),
        "similar_case_channel": similar.meta(),
        "recall_at5_last_eval": None,
        "recall_note": "檢索評測需要 leave-one-out 的歷史決定書資料集，本機無資料，未量測。",
        "kb_snapshot_date": snapshot.get("generated"),
        "query_text": query_text,
        # 查詢句由哪些案情訊號組成，逐項可查（回歸測試 test_e2e 會驗這裡沒有草稿來源）
        "query_sources": query_sources,
        "query_independence": (
            "查詢句只由 N1 卷證抽取、N2 案型、N3 程序結果組成，不含 N5 草稿的任何內容。"
            "檢索是獨立佐證，不是照著草稿要引用什麼去查什麼。"
        ),
        "substantive_article_gap": SUBSTANTIVE_ARTICLE_UNKNOWN,
    }
    state.retrieval = {"laws": laws, "cases": [], "retrieval_meta": retrieval_meta}

    elapsed = int((time.perf_counter() - started) * 1000)
    verified_n = sum(1 for l in laws if l["verified"])
    return NodeResult(
        ok=True,
        # 相似案通道不可用屬已知限制，不算節點失敗；但一定要 degraded 外顯
        degraded=True,
        degrade_reason="相似歷史案通道不可用（無資料集），僅法規查表通道有結果",
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
                "out": "相似歷史案：0 筆（庫外，未驗證）。",
                "logs": [
                    [SIMILAR_CASE_UNAVAILABLE_REASON, "r"],
                    ["本節點不編造任何案號或相似度分數（CONSTITUTION §2）", ""],
                ],
            },
        },
    )
