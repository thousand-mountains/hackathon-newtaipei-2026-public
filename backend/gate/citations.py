"""引用四態守門（architecture §8.1）——真邏輯，零 LLM，可單元測試。

四態與燈號：

| 狀態          | 意思                               | lamp | 阻擋送出 |
|---------------|------------------------------------|------|----------|
| `ok`          | ✓ 在庫，可對回 laws-snapshot        | g    | 否       |
| `amended`     | ⚠ 已修正（條次異動），附新舊條號    | y    | 否       |
| `out_of_scope`| ◇ 庫外，未驗證（超出資料集範圍）    | y    | 否       |
| `missing`     | ✗ 查無此號（庫內查無或格式不成立）  | r    | **是**   |

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

from backend.retrieval.lawtable import extract_all_law_refs

STATE_OK = "ok"
STATE_AMENDED = "amended"
STATE_OUT_OF_SCOPE = "out_of_scope"
STATE_MISSING = "missing"

STATE_TO_LAMP = {
    STATE_OK: "g",
    STATE_AMENDED: "y",
    STATE_OUT_OF_SCOPE: "y",
    STATE_MISSING: "r",
}

STATE_TO_MARK = {
    STATE_OK: "✓ 在庫",
    STATE_AMENDED: "⚠ 已修正",
    STATE_OUT_OF_SCOPE: "◇ 庫外，未驗證",
    STATE_MISSING: "✗ 查無此號",
}

BLOCKING_STATES = (STATE_MISSING,)

# 判解字號 regex，沿用 prototype/static/app.js:61 的模式，並補上 v0 漏掉的字別。
# 字別漏一個就等於那種引用完全隱形（抽不到 → 系統回「本句未附引用」→ 綠燈放行）。
PREC_TYPES = "簡上|裁聲|抗|判|裁|訴|上|簡|聲|再|更"
PREC_RE = re.compile(
    r"(最高行政法院|臺北高等行政法院|高雄高等行政法院|臺中高等行政法院|臺灣新北地方法院)?"
    rf"\s*([0-9０-９]{{1,3}})\s*年?\s*度?\s*({PREC_TYPES})\s*字\s*第\s*([0-9０-９]+)\s*號"
)
INTERP_RE = re.compile(r"釋字第\s*([0-9０-９]+)\s*號")

# 函釋：CONSTITUTION §2 明列「每一個法條、判解字號、**函釋**」都必須可驗。
# 快照沒有函釋白名單，所以本系統一律無法驗證——但必須讓它在畫面上是黃的，不是隱形的。
# 典型形式：「內政部112年5月1日台內營字第1120801234號函」。
DIRECTIVE_RE = re.compile(
    r"([一-龥]{2,12}?(?:部|署、|署|局|府|會|委員會))?\s*"
    r"(?:[0-9０-９]{2,3}\s*年\s*[0-9０-９]{1,2}\s*月\s*[0-9０-９]{1,2}\s*日\s*)?"
    r"([一-龥]{2,8}字)\s*第\s*([0-9０-９]+)\s*號\s*(?:函釋|函|令)"
)


def _int(s: str) -> int:
    """int() 本身吃全形數字，這層只是把意圖寫明白。"""
    return int(s.translate(str.maketrans("０１２３４５６７８９", "0123456789")))


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "kind": self.kind,
            "state": self.state,
            "mark": STATE_TO_MARK[self.state],
            "lamp": self.lamp,
            "resolved_id": self.resolved_id,
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
    def check_law(self, law: str, article: str, raw: str) -> Citation:
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
    def check_precedent(self, court: str | None, year: int, typ: str, no: int, raw: str) -> Citation:
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
    def check_interpretation(self, no: int, raw: str) -> Citation:
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
    def check_text(self, text: str) -> list[Citation]:
        """抽出全部引用並逐一定狀態。同一段文字重複的引用不去重（逐句守門要逐筆對應）。"""
        out: list[Citation] = []
        for law, key, display, _known in extract_all_law_refs(text, self._law_names):
            out.append(self.check_law(law, key, display))
        # 函釋先掃，並記下佔用區間——「台內營字第1120801234號函」裡的
        # 「112年5月1日」會被判解 regex 誤讀成年度，不排除會產生幽靈判解引用。
        directive_spans: list[tuple[int, int]] = []
        for m in DIRECTIVE_RE.finditer(text):
            directive_spans.append((m.start(), m.end()))
            out.append(self.check_directive(m.group(1), m.group(2), m.group(3), m.group(0)))
        for m in PREC_RE.finditer(text):
            if any(s <= m.start() < e for s, e in directive_spans):
                continue
            court, year, typ, no = m.group(1), _int(m.group(2)), m.group(3), _int(m.group(4))
            out.append(self.check_precedent(court, year, typ, no, m.group(0)))
        for m in INTERP_RE.finditer(text):
            out.append(self.check_interpretation(_int(m.group(1)), m.group(0)))
        return out


def summarize(citations: list[Citation]) -> dict[str, int]:
    counts = {STATE_OK: 0, STATE_AMENDED: 0, STATE_OUT_OF_SCOPE: 0, STATE_MISSING: 0}
    for c in citations:
        counts[c.state] = counts.get(c.state, 0) + 1
    return counts
