#!/usr/bin/env python3
"""抓每個 KB 文件的**檔頭**（不是全文），供側檔產生器讀分類欄位。

    set -a; . ./.env; set +a
    uv run --with boto3 -- python3 scripts/fetch_kb_headers.py \
        --keys data/local/kb3/keys.txt --out data/local/kb3/headers.json

這批 corpus 的每個 txt 開頭都有一段結構化檔頭，後面接一行 `====…` 分隔線
（2026-09-12 四個批次各抽 40 筆實測，100% 都有）：

    案號：1111031300
    標題：因違反空氣污染防制法事件提起訴願
    公布日期：112.02.09（2023-02-09）
    類別：空氣污染防制法
    原處分機關：新北市政府環境保護局
    主文結果：駁回
    法條依據：79-1
    ========================================

**只取前 HEAD_BYTES 個位元組**（S3 Range GET）。理由是分類欄位全在檔頭，
抓全文要多搬 100 MB 以上，而我們一個字的內文都不會寫進側檔（CONSTITUTION §6）。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import sys
import threading

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None

HEAD_BYTES = 1500
_local = threading.local()


def _s3(region: str):
    c = getattr(_local, "c", None)
    if c is None:
        c = _local.c = boto3.client("s3", region_name=region)
    return c


def head_of(key: str, *, bucket: str, region: str) -> tuple[str, str]:
    body = _s3(region).get_object(Bucket=bucket, Key=key,
                                  Range=f"bytes=0-{HEAD_BYTES - 1}")["Body"].read()
    return key, body.decode("utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=32)
    a = ap.parse_args()
    if boto3 is None:
        print("缺 boto3：uv run --with boto3 -- python3 ...", file=sys.stderr)
        return 2
    bucket, region = os.environ.get("S3_KB_BUCKET"), os.environ.get("AWS_REGION")
    if not (bucket and region):
        print("缺 S3_KB_BUCKET 或 AWS_REGION", file=sys.stderr)
        return 2
    keys = [k.strip() for k in pathlib.Path(a.keys).read_text(encoding="utf-8").splitlines()
            if k.strip().endswith(".txt")]
    out: dict[str, str] = {}
    failed: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(head_of, k, bucket=bucket, region=region): k for k in keys}
        for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            try:
                k, text = fut.result()
                out[k] = text
            except Exception as exc:  # noqa: BLE001 — 單檔失敗不中斷整批
                failed.append(f"{futs[fut]}: {type(exc).__name__}: {exc}")
            if i % 2000 == 0:
                print(f"  …{i}/{len(keys)}", flush=True)
    pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"檔頭抓取完成 {len(out)}/{len(keys)}，失敗 {len(failed)}")
    for f in failed[:10]:
        print("  ", f, file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
