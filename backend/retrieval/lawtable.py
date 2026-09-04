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
        for law, article, display, known_law in extract_all_law_refs(query, self._names):
            if not known_law:
                # 快照涵蓋範圍外的法規：能抓到、但驗不了，誠實標「庫外，未驗證」
                hits.append(
                    Hit(
                        id=f"L-{law}-{article}",
                        title=display,
                        score=0.0,
                        source="庫外（不在 laws-snapshot.json 涵蓋的法規內）",
                        origin="retrieval",
                        verified=False,
                        note=f"{law}不在快照涵蓋的 {len(self.laws)} 部法規內，本系統無法驗證條號，請人工查全國法規資料庫。",
                        payload={"law": law, "article": article, "in_snapshot_law": False},
                    )
                )
            else:
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
                        payload={"law": law, "article": article, "in_snapshot_law": True},
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


# 泛用法規名：中文字 + 法規常見結尾。用來抓**快照涵蓋範圍以外**的法規引用，
# 讓它們能被標成「庫外，未驗證」而不是被整個漏掉。
# 這條很重要：Claire 量測決定書引用的 479 個法條有 17% 對不回資料集
#（政府資訊公開法、行政訴訟法、檔案法…），只認 11 部法規等於這 17% 全部靜默消失。
GENERIC_LAW_RE = re.compile(
    r"([一-龥]{2,14}?(?:法|條例|準則|辦法|細則|規則|通則))\s*第\s*(\d+)\s*條(?:\s*之\s*(\d+))?"
)

# 泛用比對會把前導虛詞一起吃進法規名（例：「依民事訴訟法」），逐字剝掉。
STOP_PREFIX = set("依按查據參另及與暨爰本該之以由自如逾違反同前上並且或者其惟至揆諸準用適用核符即則故是有無得應照")
MIN_GENERIC_NAME_LEN = 3


def _trim_law_name(name: str) -> str:
    while len(name) > MIN_GENERIC_NAME_LEN and name[0] in STOP_PREFIX:
        name = name[1:]
    return name


def extract_law_refs(text: str, law_names: list[str]) -> list[tuple[str, str, str]]:
    """只抓快照涵蓋的法規（查表用）。回傳 [(法規名, 條號 key, 顯示字串)]。"""
    return [(law, key, disp) for law, key, disp, known in extract_all_law_refs(text, law_names) if known]


def extract_all_law_refs(text: str, law_names: list[str]) -> list[tuple[str, str, str, bool]]:
    """抓全部法條引用，含快照範圍外的。

    回傳 [(法規名, 條號 key, 顯示字串, 是否為快照涵蓋的法規)]，**依在文中出現的位置排序**
    （L1、L2… 的編號要 deterministic，不能因為兩輪掃描而亂序）。
    """
    found: list[tuple[int, str, str, str, bool]] = []
    known_ends: set[int] = set()

    if law_names:
        for m in build_law_regex(law_names).finditer(text):
            law, art, sub = m.group(1), m.group(2), m.group(3)
            key = f"{art}之{sub}" if sub else art
            display = f"{law}第{art}條" + (f"之{sub}" if sub else "")
            found.append((m.start(), law, key, display, True))
            known_ends.add(m.end())

    for m in GENERIC_LAW_RE.finditer(text):
        if m.end() in known_ends:
            continue  # 同一筆引用已由已知法規名精準命中
        law = _trim_law_name(m.group(1))
        if law in law_names:
            continue  # 保險：剝完前綴後其實是已知法規
        art, sub = m.group(2), m.group(3)
        key = f"{art}之{sub}" if sub else art
        display = f"{law}第{art}條" + (f"之{sub}" if sub else "")
        found.append((m.start(), law, key, display, False))

    found.sort(key=lambda x: x[0])
    return [(law, key, disp, known) for _, law, key, disp, known in found]
