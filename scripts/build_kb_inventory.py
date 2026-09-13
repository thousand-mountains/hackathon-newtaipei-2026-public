#!/usr/bin/env python3
"""列出 **S3 上實際有什麼**，產出 `data/kb-inventory.json`（庫存清單）。

    python3 scripts/build_kb_inventory.py --out data/kb-inventory.json \
        --headers data/local/kb3/headers.json --category-from data/manifest.json
    # 或不打網路，吃一份既有的 key 列表：
    python3 scripts/build_kb_inventory.py --keys data/local/kb3/keys.txt --out data/kb-inventory.json

**為什麼不是直接改 `build_manifest.py`**（2026-09-13，本檔存在的唯一理由）：
那支產的 `data/manifest.json` 是**入庫清單**——「我們打算把哪些本機檔送上去」，
`ingest_kb.py` 逐筆比對 sha256 上傳，缺本機檔就 exit 3。而 S3 上還有**第三方整理、
從來沒經過本機 stage 目錄**的四批（環保局全量、行政函釋_全量、裁判書、法規全量，
2026-09-12 匯入）。把它們塞進 manifest 會讓 ingest 每次都報 16,000 個「缺本機檔」。

所以拆成兩份，因為它們本來就是兩件事：

    data/manifest.json       我們要上傳什麼（有本機檔、有 sha256）→ ingest_kb.py 讀
    data/kb-inventory.json   庫裡實際有什麼（S3 現況）          → settings 報數字時讀
    data/index-state.json    向量庫真的索引了幾筆（入庫結果）    → settings 的主述

**混用這三者就是 2026-09-13 那個事故**：對外報的「資料來源」表印的是 manifest 的
2,488 筆（舊 corpus），而 `.env` 早就指向 8,486 筆的環保局全量那批——
少報自己有什麼跟多報一樣是失真（CONSTITUTION §1）。

欄位推導與側檔共用 `build_kb_metadata_from_keys.attributes_for`：**同一個 key
不該在側檔與庫存清單裡推出兩套答案**。推不出來的欄位留空，不猜。
沒有 sha256——我們沒有那些檔的內容，寫一個假的 hash 比沒有更糟。

隱私與 §7：本檔輸出**不含 bucket 名、不含 KB id、不含內文、不含人名**，
只有 key 路徑與分類欄位（與 manifest、側檔同一個標準），可進 git。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from scripts.build_kb_metadata_from_keys import attributes_for  # noqa: E402 — 要先設好 sys.path

try:
    import boto3
except ImportError:                                   # 只有 --keys 模式時不需要
    boto3 = None


def list_s3_keys(bucket: str, prefix: str, region: str | None) -> list[str]:
    """列 bucket 底下所有 key。分頁要走完——只取第一頁就會漏掉 99%。"""
    if boto3 is None:
        raise SystemExit("缺 boto3：uv run --with boto3 -- python3 scripts/build_kb_inventory.py …")
    s3 = boto3.client("s3", region_name=region)
    keys: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(o["Key"] for o in page.get("Contents", []))
    return keys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/kb-inventory.json")
    ap.add_argument("--keys", default=None,
                    help="既有的 key 列表（一行一個）。不給就即時列 S3_KB_BUCKET")
    ap.add_argument("--prefix", default="kb/", help="只列這個前綴底下的（預設 kb/）")
    ap.add_argument("--headers", default=None,
                    help="scripts/fetch_kb_headers.py 產的 key→檔頭 JSON，用來補 category／outcome")
    ap.add_argument("--category-from", default=None,
                    help="既有 manifest，用 case_no 對接 category")
    a = ap.parse_args()

    if a.keys:
        keys = [k.strip() for k in pathlib.Path(a.keys).read_text(encoding="utf-8").splitlines() if k.strip()]
        source = f"key 列表 {a.keys}"
    else:
        bucket, region = os.environ.get("S3_KB_BUCKET"), os.environ.get("AWS_REGION")
        if not bucket:
            print("缺 S3_KB_BUCKET（或改用 --keys 吃既有列表）", file=sys.stderr)
            return 2
        keys = list_s3_keys(bucket, a.prefix, region)
        source = "S3 即時列表"        # bucket 名不寫進輸出（CONSTITUTION §7）

    category_by_case: dict[str, str] = {}
    if a.category_from:
        for e in json.loads(pathlib.Path(a.category_from).read_text(encoding="utf-8"))["entries"]:
            if e.get("case_no") and e.get("category"):
                category_by_case[str(e["case_no"])] = str(e["category"])

    heads: dict[str, str] = {}
    if a.headers:
        heads = json.loads(pathlib.Path(a.headers).read_text(encoding="utf-8"))

    entries: list[dict] = []
    skipped: dict[str, int] = {}
    for key in sorted(keys):
        # 側檔不是文件，是文件的屬性；非 .txt 的（殘留 pdf 等）也不是 KB 會索引的東西。
        # 兩者都要**記進報告**而不是默默丟掉——數字對不上時要看得出少掉的是什麼。
        if key.endswith(".metadata.json") or not key.endswith(".txt"):
            kind = "側檔" if key.endswith(".metadata.json") else key.rsplit(".", 1)[-1]
            skipped[kind] = skipped.get(kind, 0) + 1
            continue
        entries.append({"path": key, **attributes_for(key, category_by_case, heads)})

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_by": "scripts/build_kb_inventory.py",
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "note": "S3 上實際存在的文件清單，非入庫清單（data/manifest.json）、非已索引筆數（data/index-state.json）。",
        "entries": entries,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    groups: dict[str, int] = {}
    for e in entries:
        groups["/".join(e["path"].split("/")[:3])] = groups.get("/".join(e["path"].split("/")[:3]), 0) + 1
    print(f"庫存清單：{len(entries)} 筆 → {a.out}（來源：{source}）")
    for g, n in sorted(groups.items(), key=lambda x: -x[1]):
        print(f"  {n:6}  {g}")
    if skipped:
        print("略過：" + "、".join(f"{k} {v} 個" for k, v in sorted(skipped.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
