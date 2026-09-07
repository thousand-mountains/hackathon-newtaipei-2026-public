#!/usr/bin/env python3
"""冪等入庫：manifest → S3（只傳 hash 不同或不存在的）→ start_ingestion_job → 等完成。

    S3_KB_BUCKET=... BEDROCK_KB_ID=... AWS_REGION=... [AWS_PROFILE=...] \
    python3 scripts/ingest_kb.py --manifest data/manifest.json --stage data/local/kb

換帳號搬遷 = 換環境變數重跑本腳本。需要 boto3（uv run --with boto3 -- python3 scripts/ingest_kb.py ...）。

**2026-09-07 狀態：本腳本尚未實跑過**——本機沒有 AWS 憑證，也沒有 bucket／KB id。
manifest 與 stage 目錄已由 `scripts/build_manifest.py` 產出並驗證；上傳與建索引
（brief Step 5）待憑證到手後執行，第二次跑應該「上傳 0」（AC12 冪等）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

# 第三方套件缺席用模組頂層守衛表達（spec D8）：缺什麼、缺了會怎樣，寫在檔案開頭。
try:
    import boto3
except ImportError:  # pragma: no cover - 只在沒裝 boto3 的環境走到
    boto3 = None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="data/manifest.json")
    ap.add_argument("--stage", default="data/local/kb")
    ap.add_argument("--no-ingest", action="store_true", help="只同步 S3，不啟動 ingestion job")
    a = ap.parse_args()
    if boto3 is None:
        print("缺 boto3：pip install boto3，或 uv run --with boto3 -- python3 scripts/ingest_kb.py ...",
              file=sys.stderr)
        return 2
    bucket, kb_id, region = os.environ.get("S3_KB_BUCKET"), os.environ.get("BEDROCK_KB_ID"), os.environ.get("AWS_REGION")
    if not (bucket and region):
        print("缺 S3_KB_BUCKET 或 AWS_REGION", file=sys.stderr)
        return 2
    s3 = boto3.client("s3", region_name=region)
    entries = json.loads(pathlib.Path(a.manifest).read_text(encoding="utf-8"))["entries"]
    uploaded = skipped = 0
    for e in entries:
        local = pathlib.Path(a.stage) / e["path"].removeprefix("kb/")
        if not local.exists():
            print(f"缺本機檔：{local}", file=sys.stderr)
            return 3
        try:
            head = s3.head_object(Bucket=bucket, Key=e["path"])
            if head.get("Metadata", {}).get("sha256") == e["sha256"]:
                skipped += 1
                continue
        except s3.exceptions.ClientError:
            pass
        s3.upload_file(str(local), bucket, e["path"],
                       ExtraArgs={"Metadata": {"sha256": e["sha256"], "provenance": e["provenance"]},
                                  "ContentType": "text/plain; charset=utf-8"})
        uploaded += 1
    print(f"S3 同步完成：上傳 {uploaded}、略過 {skipped}")
    if a.no_ingest:
        return 0
    if not kb_id:
        print("缺 BEDROCK_KB_ID，略過 ingestion", file=sys.stderr)
        return 0
    agent = boto3.client("bedrock-agent", region_name=region)
    ds = agent.list_data_sources(knowledgeBaseId=kb_id)["dataSourceSummaries"][0]["dataSourceId"]
    job = agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds)["ingestionJob"]
    while job["status"] in ("STARTING", "IN_PROGRESS"):
        time.sleep(10)
        job = agent.get_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds, ingestionJobId=job["ingestionJobId"])["ingestionJob"]
        print("ingestion:", job["status"])
    print(json.dumps(job.get("statistics", {}), ensure_ascii=False))
    return 0 if job["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
