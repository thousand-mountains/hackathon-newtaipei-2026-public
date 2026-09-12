#!/usr/bin/env python3
"""從 manifest 產生 Bedrock KB 的 metadata 側檔（spec 2026-09-07 §6.3 第 4 點）。

    python3 scripts/build_kb_metadata.py --manifest data/manifest.json --stage data/local/kb

每個 `x.txt` 旁邊生一個 `x.txt.metadata.json`，內容是 Bedrock 認得的
`{"metadataAttributes": {...}}`。側檔跟著 `ingest_kb.py` 一起上 S3。

**為什麼要這個**：KB 回的 `_document_title` 只有檔名。公開爬蟲那批的檔名是
`1141050994_駁回.txt`——**案型不在裡面**，於是：

1. 相似案卡在畫面上只顯示「1141050994_駁回」，承辦人看不出那是什麼案子；
2. AC7 的 recall 量測拿案型字串比對檔名，公開那批永遠比不中
   （2026-09-12 實測：5 筆命中全是空污案，卻判「同案型=0」）。

兩個問題同一個根：**案型只存在於 manifest，沒有跟著文件進 KB**。

側檔**不含內容、不含人名**，只有分類欄位——跟 manifest 同一個隱私標準
（CONSTITUTION §6）。

需要先跑過 `scripts/build_manifest.py`。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

# 賽方決定書檔名：`04.114年-違反廢棄物清理法事件-77(2)-訴願逾期-不受理.txt`
# = `{序}.{年}年-{案型}-{條款}-{理由}-{結果}.txt`。101/101 筆都符合這個前綴格式，
# 但「年-」之後的段數**不是固定的**：100 筆四段、1 筆三段（少了理由段，2026-09-12 實測）。
# 所以不寫死段數，改用位置取——首段是案型、次段是條款、末段是結果，中間有沒有理由都不影響。
OFFICIAL_DECISION = re.compile(r"^\d+\.(\d{2,3})年-(.+)\.txt$")

DOC_KIND = {
    "歷史訴願決定書": "decision",
    "新北訴願決定書_全量": "decision",
    "行政函釋": "ref_letter",
    "司法院釋字及行政判解": "court_ruling",
}


def attributes_for(entry: dict) -> dict[str, str]:
    """一筆 manifest entry → 該進側檔的分類欄位。

    只放**可公開的分類資訊**：來源、文件種類、案型、結果、年度。
    決定書內文、當事人、地址一律不進——那是文件本身的事，側檔不重複。
    """
    rel = entry["path"].removeprefix("kb/").split("/", 1)[-1]
    top = rel.split("/", 1)[0]
    fname = rel.rsplit("/", 1)[-1]
    attrs: dict[str, str] = {"provenance": entry["provenance"]}
    if top in DOC_KIND:
        attrs["doc_kind"] = DOC_KIND[top]

    if entry["provenance"] == "public_crawl":
        # 爬蟲那批的分類欄位 manifest 已經有了，直接用
        for key in ("category", "outcome", "year"):
            if entry.get(key):
                attrs[key] = str(entry[key])
        if entry.get("case_no"):
            attrs["case_no"] = str(entry["case_no"])
        return attrs

    m = OFFICIAL_DECISION.match(fname)
    if m:
        year, rest = m.groups()
        seg = rest.split("-")
        attrs.update({"year": year, "category": seg[0], "outcome": seg[-1]})
        if len(seg) >= 2:
            attrs["clause"] = seg[1]
    elif entry.get("outcome"):
        # 函釋與判解沒有案型，只有 manifest 從檔名抓到的結果字樣（可能是 None）
        attrs["outcome"] = str(entry["outcome"])
    return attrs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="data/manifest.json")
    ap.add_argument("--stage", default="data/local/kb")
    a = ap.parse_args()
    stage = pathlib.Path(a.stage)
    entries = json.loads(pathlib.Path(a.manifest).read_text(encoding="utf-8"))["entries"]
    written = missing = 0
    for e in entries:
        local = stage / e["path"].removeprefix("kb/")
        if not local.exists():
            print(f"缺本機檔，略過側檔：{local}", file=sys.stderr)
            missing += 1
            continue
        side = local.with_name(local.name + ".metadata.json")
        side.write_text(json.dumps({"metadataAttributes": attributes_for(e)}, ensure_ascii=False),
                        encoding="utf-8")
        written += 1
    print(f"側檔產出 {written} 筆；缺本機檔 {missing} 筆")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
