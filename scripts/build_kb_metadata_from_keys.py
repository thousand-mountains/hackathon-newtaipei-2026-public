#!/usr/bin/env python3
"""從 S3 key 列表產生 Bedrock KB 的 metadata 側檔（沒有 manifest 的 corpus 用）。

    aws s3 ls "s3://$S3_KB_BUCKET/kb/" --recursive | awk '{$1=$2=$3="";sub(/^ +/,"");print}' > keys.txt
    python3 scripts/build_kb_metadata_from_keys.py --keys keys.txt --out data/local/kb3/sidecars

跟 `build_kb_metadata.py` 的差別：那支從 `data/manifest.json` 的 entry 產（欄位齊全，
是我們自己整理過的 corpus）；**這支只有路徑與檔名可用**，因為第三方整理的 corpus
沒有附 manifest。推導不出來的欄位就**留空，不猜**（CONSTITUTION §1）。

`--category-from` 可選：拿一份既有 manifest 用 `case_no` 對接案型。
只對得上一部分是正常的，對不上的就沒有 `category`——那是誠實的缺欄位，
不是錯誤；相似案卡在那種情況下顯示檔名，不會顯示一個猜出來的案型。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from backend.config.settings import normalize_case_type  # noqa: E402 — 要先設好 sys.path

# 目錄 → 文件種類。這是**路徑決定**的，不是內容判讀。
DOC_KIND = {
    "歷史訴願決定書": "decision",
    "新北訴願決定書_環保局全量": "decision",
    "新北訴願決定書_全量": "decision",
    "行政函釋": "ref_letter",
    "行政函釋_全量": "ref_letter",
    "司法院釋字及行政判解": "court_ruling",
    "新北裁判書_環保局全量": "court_ruling",
    "相關法規": "statute",
    "相關法規_全量": "statute",
}

# 賽方決定書檔名：`04.114年-違反廢棄物清理法事件-77(2)-訴願逾期-不受理`
OFFICIAL_DECISION = re.compile(r"^\d+\.(\d{2,3})年-(.+)$")
# 公開決定書檔名：`1141050994_駁回`、`1000353526%20_駁回`（原檔名有空格，key 被二次編碼）
PUBLIC_DECISION = re.compile(r"^(\d{6,})(?:%20|\s)*_(.+)$")
# 裁判書檔名：`CHDM_104_訴_314_20211230_3`
JUDGMENT = re.compile(r"^[A-Z]+_(\d{2,3})_")
# 民國日期：`112.02.09（2023-02-09）`、`110.05.20`。取小數點前的 2–3 碼當年度。
ROC_DATE = re.compile(r"^(\d{2,3})[.\-/]")
# 案型有三種寫法混在同一個庫裡（2026-09-12 實測）：賽方檔名「違反空氣汙染防制法事件」、
# 公開檔頭「空氣污染防制法」、少數檔頭「空氣污染防制法事件」。要能跨批篩就得收斂成
# 同一個詞彙表：異體字交給 settings.normalize_case_type，再把「違反」前綴與「事件」
# 後綴**各自獨立**剝掉（不能只認完整的「違反…事件」外殼，那會漏掉第三種）。
# 非法規名的案型（社會救助事件、都市更新事件）同樣去掉後綴，全庫一致。
CASE_TYPE_PREFIX = re.compile(r"^違反")
CASE_TYPE_SUFFIX = re.compile(r"事件$")

# 各批次的檔頭欄位 → 側檔欄位。**這是讀檔頭既有的結構化欄位，不是從內文推論**
# （四批各抽 40 筆實測，欄位覆蓋率 100%）。內文一個字都不進側檔。
HEADER_MAP = {
    "decision":     {"類別": "category", "主文結果": "outcome", "法條依據": "clause",
                     "案號": "case_no", "公布日期": "_date", "原處分機關": "agency"},
    "ref_letter":   {"分類": "category"},
    "court_ruling": {"裁判案由": "category", "裁判字號": "case_no", "裁判日期": "_date"},
    "statute":      {"分類": "category", "最新修正日期": "_date"},
}


def canonical_case_type(s: str) -> str:
    """案型收斂成單一詞彙：異體字統一，再各自剝掉「違反」前綴與「事件」後綴。"""
    raw = normalize_case_type(s.strip())
    out = CASE_TYPE_SUFFIX.sub("", CASE_TYPE_PREFIX.sub("", raw)).strip()
    return out or raw          # 全被剝光就保留原值，寧可不正規化也不要空字串


def header_fields(head: str, doc_kind: str) -> dict[str, str]:
    """從檔頭抽分類欄位。抽不到就沒有那個欄位——不猜。"""
    out: dict[str, str] = {}
    body = head.split("=" * 20, 1)[0]      # 分隔線之後是內文，不碰
    for name, field in HEADER_MAP.get(doc_kind, {}).items():
        m = re.search(rf"^{name}[:：]\s*(.+?)\s*$", body, re.M)
        if not m:
            continue
        v = m.group(1).strip()
        if not v:
            continue
        if field == "_date":
            d = ROC_DATE.match(v)
            if d:
                out["year"] = d.group(1)
        elif field == "category":
            out["category"] = canonical_case_type(v)
        else:
            out[field] = v
    return out


def attributes_for(key: str, category_by_case: dict[str, str],
                   heads: dict[str, str] | None = None) -> dict[str, str]:
    """一個 S3 key → 該進側檔的分類欄位。只放可公開的分類資訊，不含內文與當事人。"""
    rel = key.removeprefix("kb/")
    provenance = "official" if rel.startswith("official/") else "public_crawl"
    rel = rel.split("/", 1)[-1]                      # 去掉 official/ 或 public/
    top = rel.split("/", 1)[0]
    stem = urllib.parse.unquote(rel.rsplit("/", 1)[-1]).rsplit(".", 1)[0]

    attrs: dict[str, str] = {"provenance": provenance}
    kind = DOC_KIND.get(top)
    if kind:
        attrs["doc_kind"] = kind

    # 檔頭是文件自己聲明的分類，優先於檔名推導與跨 manifest 對接
    head = (heads or {}).get(key)
    if head and kind:
        attrs.update(header_fields(head, kind))

    m = OFFICIAL_DECISION.match(stem)
    if m and kind == "decision":
        year, rest = m.groups()
        seg = rest.split("-")
        attrs.setdefault("year", year)
        attrs.setdefault("category", canonical_case_type(seg[0]))
        attrs.setdefault("outcome", seg[-1])
        if len(seg) >= 2:
            attrs.setdefault("clause", seg[1])
        return attrs

    m = PUBLIC_DECISION.match(stem)
    if m and kind == "decision":
        case_no, outcome = m.groups()
        attrs.setdefault("case_no", case_no)
        attrs.setdefault("outcome", outcome.strip())
        # 案號前三碼是民國年（`build_manifest.roc_year_of` 同一套判準）
        if len(case_no) >= 3 and case_no[:3].isdigit():
            attrs.setdefault("year", case_no[:3])
        # 檔頭沒給案型時，才退回用既有 manifest 的 case_no 對接。對不上就沒有這個欄位。
        if case_no in category_by_case:
            attrs.setdefault("category", canonical_case_type(category_by_case[case_no]))
        return attrs

    m = JUDGMENT.match(stem)
    if m:
        attrs.setdefault("year", m.group(1))
    return attrs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", required=True, help="S3 key 列表，一行一個")
    ap.add_argument("--out", required=True, help="側檔輸出目錄（依 key 路徑展開）")
    ap.add_argument("--headers", default=None,
                    help="scripts/fetch_kb_headers.py 產的 key→檔頭 JSON")
    ap.add_argument("--category-from", default=None, help="既有 manifest，用 case_no 對接 category")
    ap.add_argument("--rename-pdf-to-txt", action="store_true",
                    help="key 是 .pdf 時，側檔跟著轉出的 .txt 命名（PDF 轉檔後用）")
    a = ap.parse_args()

    category_by_case: dict[str, str] = {}
    if a.category_from:
        for e in json.loads(pathlib.Path(a.category_from).read_text(encoding="utf-8"))["entries"]:
            if e.get("case_no") and e.get("category"):
                category_by_case[str(e["case_no"])] = str(e["category"])

    heads: dict[str, str] = {}
    if a.headers:
        heads = json.loads(pathlib.Path(a.headers).read_text(encoding="utf-8"))

    out_root = pathlib.Path(a.out)
    keys = [k.strip() for k in pathlib.Path(a.keys).read_text(encoding="utf-8").splitlines() if k.strip()]
    written = skipped = 0
    stats: dict[str, int] = {}
    for key in keys:
        if key.endswith(".metadata.json"):
            skipped += 1
            continue
        target = key
        if a.rename_pdf_to_txt and key.lower().endswith(".pdf"):
            target = key[: -len(".pdf")] + ".txt"
        attrs = attributes_for(key, category_by_case, heads)
        for k in attrs:
            stats[k] = stats.get(k, 0) + 1
        side = out_root / (target + ".metadata.json")
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_text(json.dumps({"metadataAttributes": attrs}, ensure_ascii=False),
                        encoding="utf-8")
        written += 1
    print(f"側檔產出 {written} 筆（略過既有側檔 {skipped} 筆）")
    print("欄位覆蓋率：")
    for k, c in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {k:12} {c:6} / {written}  ({c / written * 100:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
