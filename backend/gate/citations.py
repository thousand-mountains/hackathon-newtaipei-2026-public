"""引用四態守門（architecture §8.1）——真邏輯，零 LLM，可單元測試。

四態與燈號：

| 狀態          | 意思                               | lamp | 阻擋送出 |
|---------------|------------------------------------|------|----------|
| `ok`          | ✓ 在庫，可對回 laws-snapshot        | g    | 否       |
| `amended`     | ⚠ 已修正（條次異動），附新舊條號    | y    | 否       |
| `out_of_scope`| ◇ 庫外，未驗證（超出資料集範圍）    | y    | 否       |
| `missing`     | ✗ 查無此號（庫內查無或格式不成立）  | r    | **是**   |
| `unparseable` | ？ 無法解析（號碼寫法讀不懂）        | y    | 否（但整句降「請人工判斷」層） |

第五態 `unparseable` 是本輪加固新增的（architecture §8.1 原本只寫四態）：
「讀不懂這個號碼」跟「這個號碼不存在」是兩件事，混在一起就是編造。
它必須存在，否則解析失敗只剩兩條爛路——猜一個數字（誤讀→綠燈）或整筆丟掉（漏抓→綠燈）。

為什麼判解也是四態不是二態：白名單只有 17 筆，而真實決定書引用的判解幾乎必然超出
白名單——二態設計會讓系統用自己的正確輸出把送出鈕鎖死（architecture §8.1）。

限制（demo 必須說出口）：「庫外，未驗證」的意思是「本系統無法驗證」，
不是「這個字號不存在」。判斷字號真偽仍需承辦人查全國法規資料庫或司法院系統。
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from backend.retrieval.lawtable import CN_NUMERAL_CHARS, extract_all_law_refs, parse_number

STATE_OK = "ok"
STATE_AMENDED = "amended"
STATE_OUT_OF_SCOPE = "out_of_scope"
STATE_MISSING = "missing"
# 第五態（本輪加固新增）：抓得到這筆引用，但**號碼寫法無法無歧義解析**。
# 為什麼不併進既有四態：
#   - 併進 `missing`（紅、阻擋）＝宣稱「這個號碼不存在」，那是系統沒有的知識 → 誤攔。
#   - 併進 `out_of_scope`（黃）＝宣稱「格式成立但庫外」，同樣是編造事實。
#   - 靜默丟掉 ＝ 系統回「本句未附引用」→ 綠燈放行，正是本次要修掉的失敗模式。
# 正確語意是「系統讀不懂這個號碼」：黃燈、不阻擋、但整句降到「請人工判斷」層。
STATE_UNPARSEABLE = "unparseable"

STATE_TO_LAMP = {
    STATE_OK: "g",
    STATE_AMENDED: "y",
    STATE_OUT_OF_SCOPE: "y",
    STATE_MISSING: "r",
    STATE_UNPARSEABLE: "y",
}

STATE_TO_MARK = {
    STATE_OK: "✓ 在庫",
    STATE_AMENDED: "⚠ 已修正",
    STATE_OUT_OF_SCOPE: "◇ 庫外，未驗證",
    STATE_MISSING: "✗ 查無此號",
    STATE_UNPARSEABLE: "？ 無法解析",
}

BLOCKING_STATES = (STATE_MISSING,)

UNPARSEABLE_NOTE = (
    "號碼寫法無法無歧義解析（可能是罕見國字寫法、OCR 誤字或排版斷字），"
    "系統**不猜**一個數字去比對，請人工確認正確號碼後再採用。"
)

_CN = re.escape(CN_NUMERAL_CHARS)
# 號碼字元類：半形／全形／國字三種寫法都要進得來。抓不進來 = 那種寫法整個隱形。
_NUMTOK = rf"(?:[0-9０-９]+|[{_CN}]+)"

# 判解字號 regex，沿用 prototype/static/app.js:61 的模式，並補上 v0 漏掉的字別與國字寫法。
# 字別漏一個、或國字年度／號數抓不進來，就等於那種引用完全隱形
#（抽不到 → 系統回「本句未附引用」→ 綠燈放行）。
PREC_TYPES = "簡上|裁聲|抗|判|裁|訴|上|簡|聲|再|更"
PREC_RE = re.compile(
    r"(最高行政法院|臺北高等行政法院|高雄高等行政法院|臺中高等行政法院|臺灣新北地方法院)?"
    rf"\s*({_NUMTOK})\s*年?\s*度?\s*({PREC_TYPES})\s*字\s*第\s*({_NUMTOK})\s*號"
)
INTERP_RE = re.compile(rf"釋字第\s*({_NUMTOK})\s*號")

# ── 函釋 ────────────────────────────────────────────────────────────
# CONSTITUTION §2 明列「每一個法條、判解字號、**函釋**」都必須可驗。
# 快照沒有函釋白名單，所以本系統一律無法驗證——但必須讓它在畫面上是黃的，不是隱形的。
#
# **結構性規則**：函釋引用的辨識鍵是「**發文字號結構**」＝（機關名｜發文日期）＋「X字第N號」，
# 而**不是**結尾那個「函」字。結尾詞（函／函釋／令／書函／公告／釋示）只是可選後綴，
# 少一個後綴就整筆隱形，正是舊版漏掉「…號書函」與括號內無「函」字者的原因。
#
# 與判解字號的區辨也是結構性的、不是靠關鍵字：
#   判解 = 「<年度>年度<字別>字第 N 號」（年度，沒有月日）
#   函釋 = 「<機關><年月日>X字第 N 號」（完整發文日期，或緊鄰機關名）
# 兩者在結構上互斥，所以「無後綴」的形式只在**有完整年月日**或**緊鄰機關名**時才認列。
_AGENCY = r"[一-龥]{2,12}?(?:委員會|部|署|局|府|會|處|廳|司|院|中心)"
# 發文日期兩種寫法都要吃：「112年5月1日」與公文常見的點式「88.5.10」。
_DATE = (
    r"(?:[0-9０-９]{2,3}\s*年\s*[0-9０-９]{1,2}\s*月\s*[0-9０-９]{1,2}\s*日"
    r"|[0-9０-９]{2,3}\s*[.．]\s*[0-9０-９]{1,2}\s*[.．]\s*[0-9０-９]{1,2})"
)
# 發文字別（台內營字、府授環字…）**不含「年」「度」**。這是結構性區辨，不是關鍵字黑名單：
# 判解字號長成「一一二年度判字第123號」，若字別容許含年／度，`_AGENCY` 會吃掉「最高行政法院」、
# `_WORD` 吃掉「年度判字」，整筆判解就被誤判成函釋，畫面上還會對評審顯示
# 「函釋不在本系統驗證範圍」這句與事實不符的說明。
# 末字不得是「釋」：「釋字第747號解釋」是司法院解釋，不是機關發文字號——
# 若不排除，`釋字` 會被當成發文字別而讓同一筆引用同時算成函釋與釋字兩筆。
_WORD = r"(?:(?![年度])[一-龥]){1,7}(?:(?![年度釋])[一-龥])字"
# 號數：半形／全形／國字都要進得來（國字號數的函釋原本整筆隱形），可帶「-1」「之1」尾綴。
_DNO = rf"(?:[0-9０-９]+|[{_CN}]+)(?:\s*[-－之]\s*[0-9０-９]+)?"
_SUFFIX = r"(?:函釋|書函|函|令|公告|釋示|解釋)"

# 三條路徑，依序掃描、重疊者只取第一條命中的（避免同一筆算兩次）
DIRECTIVE_PATTERNS = (
    # 1) 有明確後綴（含 v0 漏掉的「書函」「公告」「釋示」）
    re.compile(rf"({_AGENCY})?\s*(?:{_DATE}\s*)?({_WORD})\s*第\s*({_DNO})\s*號\s*{_SUFFIX}"),
    # 2) 無後綴，但有完整發文日期（年月日或點式）——判解只有「年度」，不會有月日，故不會誤收
    re.compile(rf"({_AGENCY})?\s*{_DATE}\s*({_WORD})\s*第\s*({_DNO})\s*號"),
    # 3) 無後綴、無日期，但機關名緊鄰字別（「內政部台內營字第…號」）
    re.compile(rf"({_AGENCY})\s*({_WORD})\s*第\s*({_DNO})\s*號"),
)
# 對外仍保留單一名稱（v0 有引用），指向主要路徑
DIRECTIVE_RE = DIRECTIVE_PATTERNS[0]


# 去重鍵正規化用：把字別與號數裡的空白（含全形）壓掉再比。
_WS_STRIP_RE = re.compile(r"[\s　]+")

# 機關名比對會把前導虛詞一起吃進去（「參內政部…」「本件參照內政部…」），剝掉再顯示。
DIRECTIVE_STOP_PREFIX = set("依按查據參另及與暨爰本該之以由自如見並且或者其惟至揆諸準用適用核符即則故是有無得應照又此件案系爭前開上開")

# 只由 `_AGENCY` 這個文法本身定義「什麼還算是一個機關名」——不另外維護一份機關清單。
_AGENCY_ONLY_RE = re.compile(rf"^{_AGENCY}$")


def strip_directive_prefix(agency: str) -> tuple[str, int]:
    """把機關名前面的虛詞剝掉，回 (剝完的機關名, 剝掉幾個字)。

    **兩個條件同時成立才剝**：被剝掉的那個字是已知虛詞，**而且**剝完之後剩下的
    仍然是一個合法的機關名（用 `_AGENCY` 自己的文法驗，不另建清單）。

    為什麼不用「取最短的合法後綴」那種寫法：那會把「新北市政府警察局」剝成「警察局」——
    後綴本身也是合法機關名。寧可**少剝**（raw 多帶一個雜字）也不能多剝（丟掉機關）。

    誠實說明它的極限：虛詞是列舉的，列舉一定有漏，所以 `raw` 仍可能帶到雜字。
    這件事現在不影響計數——同一筆函釋重複與否改由 `Citation.dedup_key` 的
    結構化鍵決定，不看 `raw`（見那個 property 的說明）。
    """
    dropped = 0
    while len(agency) > 2 and agency[0] in DIRECTIVE_STOP_PREFIX:
        candidate = agency[1:]
        if not _AGENCY_ONLY_RE.fullmatch(candidate):
            break
        agency = candidate
        dropped += 1
    return agency, dropped


def find_directives(text: str) -> list[tuple[int, int, str | None, str, str, str]]:
    """回傳 [(start, end, 機關, 字別, 號數, 原文)]，重疊區間只留先命中的那條。"""
    out: list[tuple[int, int, str | None, str, str, str]] = []
    taken: list[tuple[int, int]] = []
    for pattern in DIRECTIVE_PATTERNS:
        for m in pattern.finditer(text):
            if any(not (m.end() <= s or m.start() >= e) for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            start, agency = m.start(), m.group(1)
            if agency:
                agency, dropped = strip_directive_prefix(agency)
                start += dropped
            out.append((start, m.end(), agency, m.group(2), m.group(3), text[start:m.end()]))
    out.sort(key=lambda x: x[0])
    return out


def _int(s: str) -> int | None:
    """半形／全形／國字寫法的號碼 → 整數；**無法解析回 None，不猜**。"""
    return parse_number(s)


def current_roc_year(today: dt.date | None = None) -> int:
    """民國年 = 西元年 - 1911。用系統日期算，不寫死。"""
    return (today or dt.date.today()).year - 1911


@dataclass
class Citation:
    raw: str
    kind: str  # law | precedent | interpretation
    state: str
    lamp: str
    resolved_id: str | None = None
    note: str = ""
    blocking: bool = False
    # 分層誠實：state 永遠由規則產出，不准是模型寫的（origin_registry §6.4）
    state_origin: str = "rule"
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def ref_key(self) -> str | None:
        """跨模組比對用的**穩定鍵**：法條 = 「法規名|條號」。

        為什麼需要它：`resolved_id`（`L-建築法-73`）與 N4 `laws[].id`（`L1`）是兩個
        命名空間，交集是空集合；舊版靠 `raw == laws[].t` 這種顯示字串巧合對上，
        字串格式一變就靜默失效還不報錯。這個鍵直接來自結構化欄位，不經過顯示層。
        """
        law = self.payload.get("law")
        article = self.payload.get("article")
        if self.kind != "law" or not law or not article:
            return None
        return f"{law}|{article}"

    @property
    def dedup_key(self) -> tuple:
        """同一筆引用的**結構化**識別。去重、計數一律用它，不用 `raw`。

        用 `raw` 去重的問題：前導虛詞剝不乾淨時，「本件參照內政部台內營字第123號函」
        與「內政部台內營字第123號函」會被當成兩筆不同的引用，同一個函釋計成兩次，
        畫面上的「引用查核 N 筆」就多算。結構化欄位不受顯示層雜字影響。

        **函釋刻意不把機關名放進鍵裡。** 機關名正好是會沾到雜字的那一段
        （虛詞清單是列舉的，一定有漏——「經內政部…」的「經」就不在清單裡），
        把它放進鍵等於讓去重繼續受顯示層影響。字別（「台內營字」）本身就編碼了發文機關，
        所以 (字別, 號數) 已足以識別一筆函釋。
        代價講明白：**兩個不同機關若用了相同的字別與號數會被併成一筆**——
        字別是機關專屬的編碼，實務上不會撞，但這是一個取捨不是定理。

        payload 缺欄位時退回 `(kind, raw)`——退回是為了不漏，不是為了正確去重。
        """
        pl = self.payload
        if self.kind == "law" and pl.get("law"):
            return ("law", pl.get("law"), pl.get("article"))
        if self.kind == "precedent" and pl.get("no") is not None:
            return ("precedent", pl.get("year"), pl.get("type"), pl.get("no"))
        if self.kind == "interpretation" and pl.get("no") is not None:
            return ("interpretation", pl.get("no"))
        if self.kind == "directive" and pl.get("no") is not None:
            word = _WS_STRIP_RE.sub("", str(pl.get("word") or ""))
            raw_no = _WS_STRIP_RE.sub("", str(pl.get("no")))
            n = _int(raw_no)
            return ("directive", word, str(n) if n is not None else raw_no)
        return (self.kind, self.raw)

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "kind": self.kind,
            "state": self.state,
            "mark": STATE_TO_MARK[self.state],
            "lamp": self.lamp,
            "resolved_id": self.resolved_id,
            "ref_key": self.ref_key,
            "note": self.note,
            "blocking": self.blocking,
            "state_origin": self.state_origin,
        }


class CitationChecker:
    """laws-snapshot.json 是唯一真實來源（SSOT）。"""

    def __init__(self, snapshot: dict[str, Any], today: dt.date | None = None) -> None:
        self.snapshot = snapshot
        self.laws: dict[str, Any] = snapshot.get("laws", {})
        self.amendments: list[dict[str, Any]] = snapshot.get("amendments", [])
        self.precedents: list[dict[str, Any]] = snapshot.get("precedents", [])
        self.interpretations: list[int] = snapshot.get("interpretations", [])
        self._law_names = list(self.laws.keys())
        self._max_roc_year = current_roc_year(today)

    # ── 法條 ────────────────────────────────────────────────────────
    def check_law(self, law: str, article: str | None, raw: str) -> Citation:
        if article is None:
            # 條號無法解析：不比對、不猜、不宣稱存在或不存在（黃燈交人工）
            return Citation(
                raw=raw,
                kind="law",
                state=STATE_UNPARSEABLE,
                lamp=STATE_TO_LAMP[STATE_UNPARSEABLE],
                note=f"抓到「{raw}」，但{UNPARSEABLE_NOTE}",
                payload={"law": law, "article": None},
            )
        amend = next((a for a in self.amendments if a.get("law") == law and a.get("old") == article), None)
        if amend:
            return Citation(
                raw=raw,
                kind="law",
                state=STATE_AMENDED,
                lamp=STATE_TO_LAMP[STATE_AMENDED],
                resolved_id=f"L-{law}-{amend['new']}",
                note=f"已改列第 {amend['new']} 條（{amend['date']}）。{amend.get('note', '')}".strip(),
                payload={"law": law, "article": article, "new": amend["new"]},
            )
        entry = self.laws.get(law)
        if entry is None:
            return Citation(
                raw=raw,
                kind="law",
                state=STATE_OUT_OF_SCOPE,
                lamp=STATE_TO_LAMP[STATE_OUT_OF_SCOPE],
                note=f"{law}不在快照涵蓋的 {len(self.laws)} 部法規內，本系統無法驗證條號，請人工查全國法規資料庫。",
                payload={"law": law, "article": article},
            )
        if article in entry.get("articles", []):
            return Citation(
                raw=raw,
                kind="law",
                state=STATE_OK,
                lamp=STATE_TO_LAMP[STATE_OK],
                resolved_id=f"L-{law}-{article}",
                note=f"條號存在於快照（該法最大條號 {entry.get('max')}）。",
                payload={"law": law, "article": article},
            )
        return Citation(
            raw=raw,
            kind="law",
            state=STATE_MISSING,
            lamp=STATE_TO_LAMP[STATE_MISSING],
            note=f"快照中{law}無第 {article} 條（最大條號 {entry.get('max')}），請人工查證是否誤植。",
            blocking=True,
            payload={"law": law, "article": article},
        )

    # ── 判解 ────────────────────────────────────────────────────────
    def check_precedent(self, court: str | None, year: int | None, typ: str, no: int | None, raw: str) -> Citation:
        if year is None or no is None:
            # 年度或號數讀不懂（罕見國字寫法、OCR 誤字）：不猜、不靜默丟掉
            return Citation(
                raw=raw,
                kind="precedent",
                state=STATE_UNPARSEABLE,
                lamp=STATE_TO_LAMP[STATE_UNPARSEABLE],
                note=f"抓到判解字號「{raw}」，但{UNPARSEABLE_NOTE}",
                payload={"year": year, "type": typ, "no": no},
            )
        # 格式不成立：年度超出可能範圍（未來年度）或號數為 0
        if year < 1 or year > self._max_roc_year or no < 1:
            return Citation(
                raw=raw,
                kind="precedent",
                state=STATE_MISSING,
                lamp=STATE_TO_LAMP[STATE_MISSING],
                note=(
                    f"字號格式不成立：{year} 年度超出可能範圍（現為民國 {self._max_roc_year} 年）"
                    if year > self._max_roc_year
                    else "字號格式不成立：年度或號數不合法。"
                ),
                blocking=True,
                payload={"year": year, "type": typ, "no": no},
            )
        hit = next(
            (
                p
                for p in self.precedents
                if p.get("year") == year
                and p.get("type") == typ
                and p.get("no") == no
                and (not court or p.get("court") == court)
            ),
            None,
        )
        if hit:
            return Citation(
                raw=raw,
                kind="precedent",
                state=STATE_OK,
                lamp=STATE_TO_LAMP[STATE_OK],
                resolved_id=f"P-{hit['court']}-{year}-{typ}-{no}",
                note=f"資料集判解白名單命中（{hit['court']}）。",
                payload={"year": year, "type": typ, "no": no},
            )
        return Citation(
            raw=raw,
            kind="precedent",
            state=STATE_OUT_OF_SCOPE,
            lamp=STATE_TO_LAMP[STATE_OUT_OF_SCOPE],
            note=(
                f"不在 {len(self.precedents)} 筆判解白名單內。字號格式成立，"
                f"但本系統無法驗證其存在與現行效力，送出前請人工查司法院系統。"
            ),
            payload={"year": year, "type": typ, "no": no},
        )

    # ── 釋字 ────────────────────────────────────────────────────────
    def check_interpretation(self, no: int | None, raw: str) -> Citation:
        if no is None:
            return Citation(
                raw=raw,
                kind="interpretation",
                state=STATE_UNPARSEABLE,
                lamp=STATE_TO_LAMP[STATE_UNPARSEABLE],
                note=f"抓到釋字「{raw}」，但{UNPARSEABLE_NOTE}",
                payload={"no": None},
            )
        if no in self.interpretations:
            return Citation(
                raw=raw,
                kind="interpretation",
                state=STATE_OK,
                lamp=STATE_TO_LAMP[STATE_OK],
                resolved_id=f"I-{no}",
                note="資料集白名單命中。",
                payload={"no": no},
            )
        return Citation(
            raw=raw,
            kind="interpretation",
            state=STATE_OUT_OF_SCOPE,
            lamp=STATE_TO_LAMP[STATE_OUT_OF_SCOPE],
            note="非資料集內釋字，本系統無法驗證，請人工查司法院大法官解釋。",
            payload={"no": no},
        )

    # ── 函釋 ────────────────────────────────────────────────────────
    def check_directive(self, agency: str | None, word: str, no: str, raw: str) -> Citation:
        """快照沒有函釋白名單，所以一律「庫外，未驗證」——黃燈、不擋、但必須可見。

        刻意不做「看起來合理就通過」：本系統從來沒有函釋資料，任何宣稱都是假的。
        """
        return Citation(
            raw=raw,
            kind="directive",
            state=STATE_OUT_OF_SCOPE,
            lamp=STATE_TO_LAMP[STATE_OUT_OF_SCOPE],
            note=(
                "函釋不在本系統的驗證範圍（laws-snapshot.json 沒有函釋白名單），"
                "無法確認其存在、發文日期與現行有效性，請人工向發文機關或法規資料庫查證。"
            ),
            payload={"agency": agency, "word": word, "no": no},
        )

    # ── 全文掃描 ────────────────────────────────────────────────────
    def check_text(self, text: str, context: str = "") -> list[Citation]:
        """抽出全部引用並逐一定狀態。同一段文字重複的引用不去重（逐句守門要逐筆對應）。

        `context`：同一個 block 裡排在 `text` 前面的文字，**只拿來替「同法／本法」
        找先行詞**（2026-09-13 加）。`context` 裡的引用不會被檢查、不會回傳——
        它已經在它自己那一句被檢查過了，重複回傳會讓同一筆引用被算兩次。

        只有法條回指吃 `context`：函釋、判解、釋字沒有回指寫法，
        所以底下三段掃描一律只看 `text`。
        """
        out: list[Citation] = []
        for law, key, display, _known in extract_all_law_refs(text, self._law_names, context):
            out.append(self.check_law(law, key, display))
        # 函釋先掃，並記下佔用區間——「台內營字第1120801234號函」裡的
        # 「112年5月1日」會被判解 regex 誤讀成年度，不排除會產生幽靈判解引用。
        directive_spans: list[tuple[int, int]] = []
        for start, end, agency, word, no, raw in find_directives(text):
            directive_spans.append((start, end))
            out.append(self.check_directive(agency, word, no, raw))
        for m in PREC_RE.finditer(text):
            if any(s <= m.start() < e for s, e in directive_spans):
                continue
            court, year, typ, no = m.group(1), _int(m.group(2)), m.group(3), _int(m.group(4))
            out.append(self.check_precedent(court, year, typ, no, m.group(0)))
        for m in INTERP_RE.finditer(text):
            out.append(self.check_interpretation(_int(m.group(1)), m.group(0)))
        return out


def summarize(citations: list[Citation]) -> dict[str, int]:
    counts = {STATE_OK: 0, STATE_AMENDED: 0, STATE_OUT_OF_SCOPE: 0, STATE_MISSING: 0,
              STATE_UNPARSEABLE: 0}
    for c in citations:
        counts[c.state] = counts.get(c.state, 0) + 1
    return counts
