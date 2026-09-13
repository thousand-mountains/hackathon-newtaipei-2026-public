"""條文原文查表：`(法規名, 條號)` → 條文文字。

資料來源是 `backend/data/local/law-articles.json`，由
`scripts/build_law_articles.py` 從賽方資料集的「相關法規」純文字解析而來。
**那份 JSON 不進 git**（賽方資料集僅供競賽之用，CONSTITUTION §6），
所以本模組的第一條規則是：

> **檔案不在時，一切照跑，`q` 回到留白。**

不是丟例外、也不是塞一份 commit 進去的副本。缺資料要在畫面上看得出來是缺資料
（`q_note` 會照實寫「條文原文索引未建置」），而不是被一份來路不明的字串蓋過去。

## 為什麼要有條文原文

訴願決定書的理由段第一句是固定寫法：

> 一、按訴願法第 77 條第 7 款規定：「訴願事件有左列各款情形之一者，應為不受理之
>     決定：七、對已決定或已撤回之訴願事件重行提起訴願者。」。

沒有這份索引，系統只寫得出 `[L3]` 這種編號標註——那不是決定書的寫法。
而叫模型把條文「背」出來是 CONSTITUTION §3 的紅線，所以原文只能來自真的檔案。

## `quote()` 為什麼要能只引一款

上面那句引的是**柱書 ＋ 第 7 款**，不是整條八款。整條貼上去，理由段會被
七款不相干的文字淹掉，承辦人反而看不出本案命中的是哪一款。
條號 → 款次的切法只有這裡一份，`narrative.py` 不自己切。
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

#: 產物路徑。`backend/data/local/` 已在 `.gitignore` 內（見該檔「§77-3 驗證語料」那條）。
ARTICLES_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "local" / "law-articles.json"

#: 索引不在時寫進 `laws[].q_note` 的說明。**要指名重建方式**：
#: 只說「沒有原文」的話，下一個人得先反推這份資料是哪來的。
NOT_BUILT_NOTE = (
    "條文原文索引未建置（backend/data/local/law-articles.json 不存在），本系統不代為補寫條文文字，"
    "請執行 scripts/build_law_articles.py 重建，或對照全國法規資料庫。"
)
#: 索引在、但查無這一條時的說明。跟「索引沒建」要分得開——
#: 前者是環境沒準備好，後者是這一條真的不在涵蓋的 11 部法規內。
NOT_FOUND_NOTE = "條文原文不在索引涵蓋的法規內，本系統不代為補寫條文文字，請對照全國法規資料庫。"

_CLAUSE_NUMERALS = "一二三四五六七八九十"


def clause_numeral(n: int) -> str | None:
    """款次 `7` → `"七"`。超出 1–10 回 None（本系統涵蓋的法規沒有第 11 款以上）。"""
    return _CLAUSE_NUMERALS[n - 1] if 1 <= n <= len(_CLAUSE_NUMERALS) else None


class ArticleTextStore:
    """條文原文查表。**建構不會失敗**：檔案不在就是一個空的 store。"""

    def __init__(self, path: pathlib.Path | None = None) -> None:
        self.path = path or ARTICLES_PATH
        self.laws: dict[str, Any] = {}
        self.loaded = False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.laws = raw.get("laws") or {}
        self.loaded = bool(self.laws)

    def article(self, law: str, article: str) -> dict[str, Any] | None:
        return (self.laws.get(law) or {}).get(article)

    def text(self, law: str, article: str) -> str | None:
        """整條原文。多項時在每一項句末標 `（第 N 項）`，與決定書引述慣例一致。"""
        art = self.article(law, article)
        if not art:
            return None
        paras = art.get("paragraphs") or []
        multi = len(paras) > 1
        out = [self._paragraph_text(p, mark_number=multi) for p in paras]
        return "".join(t for t in out if t) or None

    def quote(self, law: str, article: str, clause: str | None = None) -> str | None:
        """理由段要引的那一段。

        `clause` 給了（`"七"`）而且找得到那一款時，回**柱書 ＋ 該款**；
        找不到就退回整條原文——不是回 None。退回整條的引述仍然是真的條文，
        而回 None 會讓整句引述消失，那才是資訊損失。
        """
        if clause:
            for p in (self.article(law, article) or {}).get("paragraphs") or []:
                for item in p.get("items") or []:
                    if item.get("n") == clause:
                        lead = (p.get("lead") or "").strip()
                        return f"{lead}{clause}、{item['t']}" if lead else f"{clause}、{item['t']}"
        return self.text(law, article)

    @staticmethod
    def _paragraph_text(para: dict[str, Any], mark_number: bool) -> str:
        lead = (para.get("lead") or "").strip()
        body = lead + "".join(f"{i['n']}、{i['t']}" for i in para.get("items") or [])
        if not body:
            return ""
        n = para.get("n")
        return f"{body}（第 {n} 項）" if (mark_number and n) else body


_default: ArticleTextStore | None = None


def default_store() -> ArticleTextStore:
    """行程內共用一份（1 MB JSON，每次 N4 都重讀太浪費）。測試要換掉就自己 new 一個。"""
    global _default
    if _default is None:
        _default = ArticleTextStore()
    return _default
