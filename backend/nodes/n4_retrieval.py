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


def build_query(state: CaseState) -> str:
    """案例導向查詢句：案型 + 命中法規 + 77 條款。"""
    cls = state.classification.get("class") or {}
    art77 = state.screen.get("art77") or {}
    parts = [
        cls.get("case_type") or "",
        *(cls.get("law_hits") or []),
        f"訴願法 {art77.get('clause')}" if art77.get("clause") else "",
    ]
    return "；".join(p for p in parts if p)


def run(state: CaseState, ctx: NodeCtx, cited_laws: list[str] | None = None) -> NodeResult:
    started = time.perf_counter()
    snapshot = ctx.snapshot or {}
    lawtable = LawTableRetriever(snapshot)
    similar = UnavailableRetriever("similar_cases", SIMILAR_CASE_UNAVAILABLE_REASON)

    # ── 通道 A：法規查表（真實）──────────────────────────────────
    # 查詢來源：草稿即將引用的法條清單（由編排層自 fixture 草稿的 basis 蒐集）
    query_text = " ".join(cited_laws or []) or build_query(state)
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
                "out": f"命中 {len(laws)} 筆法規依據，其中 {verified_n} 筆條號可對回快照。",
                "logs": [
                    [f"查表來源：laws-snapshot.json（{len(snapshot.get('laws', {}))} 部法規，產製於 {snapshot.get('generated')}）", ""],
                    ["條文原文未附：快照只索引條號，不代為補寫條文文字", "y"],
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
