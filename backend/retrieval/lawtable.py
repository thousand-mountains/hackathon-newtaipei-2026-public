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
            if article is None:
                # 條號無法無歧義解析：不猜一個數字去查表（猜錯就是「誤讀→綠燈」）。
                hits.append(
                    Hit(
                        id=f"L-{law}-?",
                        title=display,
                        score=0.0,
                        source="無法解析（條號寫法無法無歧義判讀）",
                        origin="retrieval",
                        verified=False,
                        note=f"抓到 {display} 這筆引用，但條號寫法無法無歧義解析，系統不猜；請人工確認條號。",
                        payload={"law": law, "article": None, "in_snapshot_law": self.known_law(law)},
                    )
                )
            elif not known_law:
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


# ════════════════════════════════════════════════════════════════════
# 國字數字解析（P0-1 的結構性修法）
# ════════════════════════════════════════════════════════════════════
#
# **結構性規則**：中文數字有兩套彼此互斥的書寫體系，解析前必須先判定屬於哪一套，
# 不能用同一條掃描規則硬吃：
#
#   1. 單位式（positional-by-unit）：靠「十百千」標出位值。九百九十九 = 999、一百零五 = 105。
#      文法嚴格：單位必須嚴格遞減，數字後面要嘛接單位、要嘛是最後一位（個位）。
#   2. 數字串式（digit-string）：逐字對位，沒有任何單位字。九九九 = 999、一〇五 = 105、七三 = 73。
#
#   判別鍵 = 字串裡有沒有單位字元。有 → 單位式；沒有 → 數字串式。
#
# **失敗即 None**：任何違反上述文法、混用兩套體系、或含未知字元的字串一律回 `None`，
# 由呼叫端把它標成「無法解析」交人工，**絕不猜一個整數**。
# 這一條是這次修法的核心——舊版對「九九九」猜出 9，讓捏造的條號命中了真實存在的低條號，
# 把「漏抓→黃燈」（安全失敗）換成「誤讀→綠燈」（系統對捏造條號主動背書）。

# 變體字正規化：大寫數字、異體單位、零的各種寫法，全部收斂到一組標準字元。
# 廿／卅／卌 是「二十／三十／四十」的合字，先展開再解析。
_CN_VARIANTS = {
    "壹": "一", "貳": "二", "貮": "二", "弍": "二", "參": "三", "叁": "三", "叄": "三",
    "肆": "四", "伍": "五", "陸": "六", "陆": "六", "柒": "七", "捌": "八", "玖": "九",
    "兩": "二", "两": "二",
    "拾": "十", "佰": "百", "陌": "百", "仟": "千", "阡": "千",
    "廿": "二十", "卅": "三十", "卌": "四十",
    # 零的各種寫法（法律文書、OCR、全形輸入都會出現）
    "零": "〇", "○": "〇", "◯": "〇", "Ｏ": "〇", "O": "〇", "o": "〇", "ｏ": "〇",
}
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_ZERO = "〇"

# regex 字元類：正規化前的原始字元也必須進得來，否則「一Ｏ五」在 regex 階段就被切成「一」。
_CN_ALPHABET = "".join(sorted(set(
    "".join(_CN_DIGITS) + "".join(_CN_UNITS) + _CN_ZERO + "".join(_CN_VARIANTS)
)))
CN_NUMERAL_CHARS = _CN_ALPHABET

# 條號上限：超過這個值一定不是條號，寧可回 None 交人工也不吐一個離譜的整數。
MAX_ARTICLE_VALUE = 9999

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _normalize_cn(s: str) -> str:
    """展開變體字與合字，收斂成 {一..九, 〇, 十, 百, 千} 的標準字集。"""
    return "".join(_CN_VARIANTS.get(c, c) for c in s)


def _parse_digit_string(s: str) -> int | None:
    """數字串式：逐字對位。七三=73、九九九=999、一〇五=105、一二三=123。"""
    if s.startswith(_CN_ZERO):
        return None  # 「〇五」不是合法寫法，前導零一律視為無法解析
    out = 0
    for c in s:
        if c == _CN_ZERO:
            out = out * 10
        elif c in _CN_DIGITS:
            out = out * 10 + _CN_DIGITS[c]
        else:
            return None
    return out or None


def _parse_unit_style(s: str) -> int | None:
    """單位式：靠十百千標位值，文法嚴格，違反即 None。

    合法：七十三、九百九十九、十四、二十、一百零五、一千二百三十四、三百十三
    非法（回 None，不猜）：十十、百五、三四十、一百二百、**一百五**、一千八

    「一百五」為什麼必須是 None：中文有「尾數單位省略式」——口語裡「一百五」是 150、
    「三千八」是 3800，但在條號脈絡也可能有人當成 105 寫。同一串字有兩種讀法，
    就是**歧義**，歧義一律不猜。這條是對抗覆核打出來的：舊版把百／千後面的裸數字
    一律當個位，「建築法第一百五條」（人讀第 150 條，該法只到 105 條、應紅燈）
    被讀成 105 → ✓ 在庫 → 綠燈放行，跟上一輪「九九九→9」是同一個病灶換個字串。

    規則：**個位裸數字只有在「前一個單位是十」或「前面出現過〇跳級佔位符」時才合法。**
    七十三（前一單位是十）✓、一百零五（有〇）✓、三百十三（前一單位是十）✓、
    一百五（前一單位是百、又沒有〇）✗。
    """
    total = 0
    last_unit: int | None = None  # 前一個用過的單位，必須嚴格遞減
    zero_seen = False  # 是否出現過〇跳級佔位符（「一百零五」）
    # 〇 宣告「跳過至少一個位級」，因此它後面的餘數必須**小於下一個位級**：
    # 一千零二十 → 餘數 20 < 100 ✓；一百零五十 → 餘數 50 不小於 10 ✗（那是 150 的壞寫法）。
    zero_limit: int | None = None
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c in _CN_DIGITS:
            d = _CN_DIGITS[c]
            if i + 1 < n and s[i + 1] in _CN_UNITS:
                unit = _CN_UNITS[s[i + 1]]
                if last_unit is not None and unit >= last_unit:
                    return None  # 單位沒有遞減：「一百二百」這類寫法無法無歧義解讀
                if zero_limit is not None and unit >= zero_limit:
                    return None  # 「一百零五十」：〇 宣告跳級，後面卻沒真的跳
                total += d * unit
                last_unit = unit
                zero_seen = False  # 跳級佔位符只授權「緊接其後」的個位，隔一個單位就失效
                zero_limit = None
                i += 2
                continue
            # 後面沒有單位 → 只能是最後一個字（個位）
            if i != n - 1:
                return None  # 「三四十」這種數字連寫又帶單位，混用兩套體系
            if last_unit is not None and last_unit <= 1:
                return None
            if last_unit is not None and last_unit > 10 and not zero_seen:
                return None  # 「一百五」：150 還是 105？歧義，不猜
            total += d
            last_unit = 1
            i += 1
            continue
        if c == _CN_ZERO:
            # 「一百零五」的零是**跳級佔位符**：只有在剛用完百／千之後才有意義
            #（十位之後沒有可跳的級，「三百十〇三」是壞字串，不是 313）。
            # 前後都必須還有東西。
            if i == 0 or i == n - 1 or last_unit is None or last_unit < 100:
                return None
            if zero_seen:
                return None  # 「一千零零五」：連續兩個跳級佔位符無法無歧義解讀
            zero_seen = True
            zero_limit = last_unit // 10
            i += 1
            continue
        if c in _CN_UNITS:
            unit = _CN_UNITS[c]
            if unit != 10:
                return None  # 百／千沒有前導數字一律不合法（「百五」無法無歧義解讀）
            if i == 0:
                if last_unit is not None:
                    return None
            elif last_unit is None or last_unit <= 10:
                return None  # 「十十」「三四十」：十位已用過或前面根本沒有更高的位
            if zero_limit is not None and 10 >= zero_limit:
                return None  # 〇 宣告跳級後又只降一級，等於沒跳
            # 句首的「十」（十四）與百／千之後省略前導一的「三百十三」都是標準寫法
            total += 10
            last_unit = 10
            zero_seen = False
            i += 1
            continue
        return None
    return total or None


def cn_to_int(s: str) -> int | None:
    """國字數字 → 整數。**無法無歧義解析一律回 None，絕不猜。**

    支援單位式（九百九十九＝999）與數字串式（九九九＝999、一〇五＝105、一Ｏ五＝105），
    以及 廿／卅／佰／仟／拾／壹貳參… 等變體字。兩套體系混用即視為無法解析。
    """
    s = _normalize_cn(s.strip().translate(_FULLWIDTH_DIGITS))
    if not s:
        return None
    if s.isdigit():  # 已是（或已被正規化成）阿拉伯數字
        v = int(s)
        return v if 0 < v <= MAX_ARTICLE_VALUE else None
    if any(c not in _CN_DIGITS and c not in _CN_UNITS and c != _CN_ZERO for c in s):
        return None
    has_unit = any(c in _CN_UNITS for c in s)
    v = _parse_unit_style(s) if has_unit else _parse_digit_string(s)
    if v is None or not (0 < v <= MAX_ARTICLE_VALUE):
        return None
    return v


def parse_number(s: str) -> int | None:
    """任意寫法（半形／全形／國字）的數字 → 整數；無法解析回 None。"""
    if s is None:
        return None
    t = s.strip().translate(_FULLWIDTH_DIGITS)
    if t.isdigit():
        v = int(t)
        return v if v >= 0 else None
    return cn_to_int(t)


def normalize_digits(s: str) -> str | None:
    """條號字串正規化成阿拉伯數字。**無法解析回 `None`**（呼叫端要標「無法解析」）。"""
    t = s.strip().translate(_FULLWIDTH_DIGITS)
    if t.isdigit():
        return t
    n = cn_to_int(t)
    return str(n) if n is not None else None


# 條號可以是阿拉伯數字、全形數字或國字數字（三種在決定書裡都會出現）
_NUM = rf"(?:[0-9０-９]+|[{re.escape(_CN_ALPHABET)}]+)"


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


def extract_law_refs(text: str, law_names: list[str]) -> list[tuple[str | None, str, str]]:
    """只抓快照涵蓋的法規（查表用）。回傳 [(法規名, 條號 key, 顯示字串)]。"""
    return [(law, key, disp) for law, key, disp, known in extract_all_law_refs(text, law_names) if known]


def extract_all_law_refs(text: str, law_names: list[str]) -> list[tuple[str, str | None, str, bool]]:
    """抓全部法條引用，含快照範圍外的。

    回傳 [(法規名, 條號 key, 顯示字串, 是否為快照涵蓋的法規)]，**依在文中出現的位置排序**
    （L1、L2… 的編號要 deterministic，不能因為兩輪掃描而亂序）。

    條號 key 為 `None` 代表「抓到一筆引用，但條號無法無歧義解析」——這一態必須傳下去，
    不能就地猜一個數字，也不能把整筆引用丟掉（丟掉＝系統回「本句未附引用」＝綠燈放行）。
    """
    found: list[tuple[int, str, str | None, str, bool]] = []
    known_ends: set[int] = set()

    def _key_display(law: str, g_art: str, g_sub: str | None) -> tuple[str | None, str]:
        art = normalize_digits(g_art)
        sub = normalize_digits(g_sub) if g_sub else None
        if art is None or (g_sub and sub is None):
            # 無法解析：顯示字串保留原文（讓人看得到系統看到了什麼），key 回 None
            raw = f"{law}第{g_art}條" + (f"之{g_sub}" if g_sub else "")
            return None, raw
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
