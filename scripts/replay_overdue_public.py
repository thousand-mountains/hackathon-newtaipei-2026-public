#!/usr/bin/env python3
r"""從爬蟲 cases.jsonl 產生期間引擎的公開回放集（只留日期與案號，去識別）。

    python3 scripts/replay_overdue_public.py --crawl /path/cases.jsonl \
        --out backend/data/replay/overdue-public.jsonl

篩選：outcome 含「不受理」且 legal_basis 或 related_laws 含「77」與「2」款（77-2 逾期）。
欄位：case_no, service_date, filing_date, service_method（無則 personal）, expected_overdue=true, source。
**不寫** appellant／title／full_text——回放集會進 git，只放日期與案號（CONSTITUTION §6）。

沒有送達日或收文日的案子跳過並計數——**不猜日期**（CONSTITUTION §3）。

日期擷取（2026-09-07 依實測資料修正 brief 的兩處）：
1. pdftotext 版面會在數字後補空白（`109  年 8  月 3  日`），原 regex `(\d{2,3})年` 全數落空
   （251 件 77-2 案抓到 0 件）。改成允許 `\s*`。
2. 決定書的寫法是「**日期在前、關鍵詞在後**」：「109 年 8 月 3 日**送達**至…」、
   「109 年 11 月 4 日**始提起訴願**」。原本只看日期**前** 12 字，方向剛好相反。
   改成看日期後方小窗（送達／寄存 8 字、提起訴願／收受訴願書 10 字），
   另外收「遲至 <日期>」這個前置寫法。窗開得小是為了精準——
   「其訴願期間應於 109 年 9 月 7 日屆滿。惟訴願人遲至…」的 9/7 是**期滿日不是提起日**，
   窗一放寬就會把它當成提起日。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys

ROC = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
SERVICE_AFTER = re.compile(r"送達|寄存")
FILING_AFTER = re.compile(r"提起訴願|收受訴願書")
FILING_BEFORE = "遲至"
ARTICLE_77_2 = re.compile(r"77\s*條?\s*第?\s*2\s*款|77\(2\)|77-2")


def to_iso(m: re.Match[str] | None) -> str | None:
    """民國年 match → ISO 日期字串。日期本身不合法（例 2 月 30 日）就回 None，不修正。"""
    if not m:
        return None
    y, mo, d = int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def find_dated(blob: str, after: re.Pattern[str], before: str | None = None, window: int = 8) -> str | None:
    """回傳第一個「後方小窗命中關鍵詞」的日期（或前方 6 字命中 `before` 的日期）。"""
    for m in ROC.finditer(blob):
        if after.search(blob[m.end():m.end() + window]):
            return to_iso(m)
        if before and before in blob[max(0, m.start() - 6):m.start()]:
            return to_iso(m)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl", required=True)
    ap.add_argument("--out", default="backend/data/replay/overdue-public.jsonl")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    matched = kept = skipped = inconsistent = 0
    with out.open("w", encoding="utf-8") as fh, open(a.crawl, encoding="utf-8") as src:
        for line in src:
            r = json.loads(line)
            if "不受理" not in (r.get("outcome") or ""):
                continue
            basis = " ".join(str(r.get(k) or "") for k in ("legal_basis", "related_laws"))
            if not ARTICLE_77_2.search(basis):
                continue
            matched += 1
            sections = r.get("sections") or {}
            blob = " ".join(str(v) for v in sections.values()) if isinstance(sections, dict) else str(sections)
            service = find_dated(blob, SERVICE_AFTER, window=8)
            filing = find_dated(blob, FILING_AFTER, before=FILING_BEFORE, window=10)
            if not (service and filing):
                skipped += 1
                continue
            if filing <= service:
                # 提起日不晚於送達日＝擷取錯了（不是真的有這種案子）。丟棄而不是修正。
                inconsistent += 1
                continue
            method = "deposit" if "寄存" in blob else "personal"
            fh.write(json.dumps({"case_no": r.get("case_no"), "service_date": service, "filing_date": filing,
                                 "service_method": method, "expected_overdue": True, "source": "public_crawl"},
                                ensure_ascii=False) + "\n")
            kept += 1
    print(f"77-2 不受理案 {matched} 件；回放集 {kept} 件 → {out}")
    print(f"因缺日期跳過 {skipped} 件、因日期順序不合（擷取錯誤）丟棄 {inconsistent} 件（不猜日期）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
