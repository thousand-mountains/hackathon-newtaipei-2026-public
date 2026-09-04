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


# 條號可以是阿拉伯數字、全形數字或國字數字（三種在決定書裡都會出現）
_NUM = r"(?:[0-9０-９]+|[〇零一二兩三四五六七八九十百千]+)"


def build_law_regex(law_names: list[str]) -> re.Pattern[str]:
    names = sorted(law_names, key=len, reverse=True)
    joined = "|".join(re.escape(n) for n in names)
    return re.compile(rf"({joined})\s*第\s*({_NUM})\s*條(?:\s*之\s*({_NUM}))?")


# 泛用法規名：中文字 + 法規常見結尾。用來抓**快照涵蓋範圍以外**的法規引用，
# 讓它們能被標成「庫外，未驗證」而不是被整個漏掉。
# 這條很重要：Claire 量測決定書引用的 479 個法條有 17% 對不回資料集
#（政府資訊公開法、行政訴訟法、檔案法…），只認 11 部法規等於這 17% 全部靜默消失。
GENERIC_LAW_RE = re.compile(
    rf"([一-龥]{{2,14}}?(?:法|條例|準則|辦法|細則|規則|通則))\s*第\s*({_NUM})\s*條(?:\s*之\s*({_NUM}))?"
)

# 「同法／本法／該法／前法」是決定書引用第二條以後的標準寫法，指的是前文最近提到的法規。
# 不做回指解析的話，「又同法第999條」會被當成一部叫「又同法」的未知法規 → 只拿黃燈不擋，
# 等於給了編造條號一條後門（把假條號寫成「同法第X條」就繞過紅燈）。
ANAPHORA_RE = re.compile(rf"(同法|本法|該法|前開法律)\s*第\s*({_NUM})\s*條(?:\s*之\s*({_NUM}))?")

# 泛用比對會把前導虛詞一起吃進法規名（例：「依民事訴訟法」），逐字剝掉。
STOP_PREFIX = set("依按查據參另及與暨爰本該之以由自如逾違反同前上並且或者其惟至揆諸準用適用核符即則故是有無得應照")
MIN_GENERIC_NAME_LEN = 3


def _trim_law_name(name: str) -> str:
    while len(name) > MIN_GENERIC_NAME_LEN and name[0] in STOP_PREFIX:
        name = name[1:]
    return name


# 全形數字 → 半形。法規 PDF 轉出來的文字常帶全形數字（「訴願法第１４條」），
# 不正規化的話快照比對會查不到，把**正確的引用誤判成查無此號**並阻擋送出——
# 誤攔比漏抓更常見也更難察覺（architecture §8.1 明確警告過誤攔問題）。
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# 國字數字 → 阿拉伯數字。**訴願決定書大量使用國字條號**（「建築法第七十三條」），
# 只吃 `\d+` 等於守門對最常見的引用寫法全盲：抽不到引用 → 系統回「本句未附引用」
# → 綠燈放行。那是把「沒抓到」講成「沒有引用」，比漏抓本身更糟。
_CN_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
CN_NUMERAL_CHARS = "".join(_CN_DIGITS) + "".join(_CN_UNITS)


def cn_to_int(s: str) -> int | None:
    """把國字數字轉成整數（支援到四位數，足夠涵蓋所有條號）。轉不了回 None。"""
    s = s.strip()
    if not s or any(c not in _CN_DIGITS and c not in _CN_UNITS for c in s):
        return None
    total = 0
    section = 0
    last_digit: int | None = None
    for c in s:
        if c in _CN_DIGITS:
            last_digit = _CN_DIGITS[c]
            section = section * 10 + last_digit if last_digit == 0 else last_digit
        else:
            unit = _CN_UNITS[c]
            # 「十四」這種省略前導一的寫法
            section = (last_digit if last_digit is not None else 1) * unit
            total += section
            section = 0
            last_digit = None
    total += section if last_digit is not None else 0
    return total if total > 0 else None


def normalize_digits(s: str) -> str:
    """全形轉半形；純國字數字則轉成阿拉伯數字。已是阿拉伯數字者原樣回傳。"""
    s = s.translate(_FULLWIDTH_DIGITS)
    if s.isdigit():
        return s
    n = cn_to_int(s)
    return str(n) if n is not None else s


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

    def _key_display(law: str, g_art: str, g_sub: str | None) -> tuple[str, str]:
        art = normalize_digits(g_art)
        sub = normalize_digits(g_sub) if g_sub else None
        return (f"{art}之{sub}" if sub else art), (f"{law}第{art}條" + (f"之{sub}" if sub else ""))

    if law_names:
        for m in build_law_regex(law_names).finditer(text):
            law = m.group(1)
            key, display = _key_display(law, m.group(2), m.group(3))
            found.append((m.start(), law, key, display, True))
            known_ends.add(m.end())

    # ── 回指解析：「同法／本法／該法第X條」綁到前文最近一次出現的法規名 ──
    # **必須跑在泛用比對之前**，否則「又同法第999條」會先被當成一部叫「又同法」的
    # 未知法規吃掉，只拿到黃燈——那等於給編造條號開了後門（假條號寫成「同法第X條」
    # 就繞過紅燈）。
    for m in ANAPHORA_RE.finditer(text):
        if m.end() in known_ends:
            continue
        antecedent = None
        for pos, law, _k, _d, known in sorted(found, key=lambda x: x[0]):
            if pos < m.start() and known:
                antecedent = law
        key, display = _key_display(antecedent or m.group(1), m.group(2), m.group(3))
        if antecedent is None:
            # 找不到前行詞：不猜是哪部法，但也不能靜靜放過。標成未知法規讓它至少是黃的。
            found.append((m.start(), m.group(1), key, display, False))
        else:
            found.append((m.start(), antecedent, key, display, True))
        known_ends.add(m.end())

    for m in GENERIC_LAW_RE.finditer(text):
        if m.end() in known_ends:
            continue  # 同一筆引用已由已知法規名或回指解析命中
        law = _trim_law_name(m.group(1))
        if law in law_names:
            continue  # 保險：剝完前綴後其實是已知法規
        key, display = _key_display(law, m.group(2), m.group(3))
        found.append((m.start(), law, key, display, False))
        known_ends.add(m.end())

    # ── 安全網：法規名被空白／換行拆開（PDF 抽取常見）──────────────
    # 只在壓縮空白後的副本上再掃一次，位置對不回原文，所以一律排到最後。
    # 這一輪只會**增加**偵測，不會移除任何既有結果。
    squeezed = re.sub(r"\s+", "", text)
    if squeezed != text:
        seen = {(law, key) for _, law, key, _d, _k in found}
        for law, key, display, known in extract_all_law_refs(squeezed, law_names):
            if (law, key) not in seen:
                seen.add((law, key))
                found.append((len(text), law, key, display, known))

    found.sort(key=lambda x: x[0])
    return [(law, key, disp, known) for _, law, key, disp, known in found]
