"""法條查表檢索（真實邏輯，非 fixture）。

資料來源：`backend/data/laws-snapshot.json`（11 部法規的條號陣列 + 最大條號）。
這條通道是**可驗的**：查得到就查得到，查不到就明說查不到，不做語意猜測。
"""
from __future__ import annotations

import re
from typing import Any

from backend.retrieval.base import Hit

# 條文原文不在 snapshot 內（snapshot 只有條號索引），所以本模組只回「條號存在性」，
# 不回條文文字。要顯示條文原文需要條文全文庫，Phase 0 沒有，於是誠實留白。
ARTICLE_TEXT_AVAILABLE = False


class LawTableRetriever:
    """key-value 查表：法規名 + 條號 → 是否在庫。"""

    name = "lawtable"

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.laws: dict[str, dict[str, Any]] = snapshot.get("laws", {})
        # 長名優先，避免「行政程序法」被「行政法」之類的短名搶先命中
        self._names = sorted(self.laws.keys(), key=len, reverse=True)

    # ── 基礎查詢 ────────────────────────────────────────────────────
    def has_article(self, law: str, article: str) -> bool:
        entry = self.laws.get(law)
        if not entry:
            return False
        return article in entry.get("articles", [])

    def max_article(self, law: str) -> str | None:
        entry = self.laws.get(law)
        return str(entry["max"]) if entry and "max" in entry else None

    def known_law(self, law: str) -> bool:
        return law in self.laws

    # ── Retriever 介面 ──────────────────────────────────────────────
    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[Hit]:
        """從查詢句抽出法條引用，逐一查表回 Hit。

        score 固定為 1.0（查表是二元命中，不是相似度）——刻意不假造相似度分數。
        """
        hits: list[Hit] = []
        for law, article, display in extract_law_refs(query, self._names):
            in_lib = self.has_article(law, article)
            hits.append(
                Hit(
                    id=f"L-{law}-{article}",
                    title=display,
                    score=1.0 if in_lib else 0.0,
                    source=f"laws-snapshot.json／{law}",
                    origin="retrieval",
                    verified=in_lib,
                    note=(
                        f"條號存在於快照（該法最大條號 {self.max_article(law)}）"
                        if in_lib
                        else f"快照中{law}無第 {article} 條（最大條號 {self.max_article(law)}）"
                    ),
                    payload={"law": law, "article": article},
                )
            )
            if len(hits) >= top_k:
                break
        return hits

    def meta(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": True,
            "laws_indexed": len(self.laws),
            "article_text_available": ARTICLE_TEXT_AVAILABLE,
            "note": "快照只索引條號，不含條文原文；條文原文欄位在 Phase 0 一律留白，不由系統補寫。",
        }


def build_law_regex(law_names: list[str]) -> re.Pattern[str]:
    names = sorted(law_names, key=len, reverse=True)
    joined = "|".join(re.escape(n) for n in names)
    return re.compile(rf"({joined})\s*第\s*(\d+)\s*條(?:\s*之\s*(\d+))?")


def extract_law_refs(text: str, law_names: list[str]) -> list[tuple[str, str, str]]:
    """回傳 [(法規名, 條號 key, 顯示字串)]，條號 key 沿用 snapshot 的 `12之1` 格式。"""
    out: list[tuple[str, str, str]] = []
    for m in build_law_regex(law_names).finditer(text):
        law, art, sub = m.group(1), m.group(2), m.group(3)
        key = f"{art}之{sub}" if sub else art
        display = f"{law}第{art}條" + (f"之{sub}" if sub else "")
        out.append((law, key, display))
    return out
