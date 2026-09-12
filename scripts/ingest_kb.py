#!/usr/bin/env python3
"""冪等入庫：manifest → S3（只傳 hash 不同或不存在的）→ start_ingestion_job → 等完成。

    S3_KB_BUCKET=... BEDROCK_KB_ID=... AWS_REGION=... [AWS_PROFILE=...] \
    python3 scripts/ingest_kb.py --manifest data/manifest.json --stage data/local/kb

換帳號搬遷 = 換環境變數重跑本腳本。需要 boto3（uv run --with boto3 -- python3 scripts/ingest_kb.py ...）。

**2026-09-12：上傳改平行**。序列版實測 2 分鐘只傳 50/2477（推估全量約 100 分鐘）；
32 執行緒版 2477 檔 32 秒完成。冪等語意不變：sha256 相同就 skip，第二次跑應該「上傳 0」
（AC12）。`--workers 1` 可退回序列行為。

**本腳本的 ingestion 段（start_ingestion_job 之後）仍未在真帳號實跑過。**
manifest 與 stage 目錄由 `scripts/build_manifest.py` 產出並驗證。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import sys
import threading
import time

# 第三方套件缺席用模組頂層守衛表達（spec D8）：缺什麼、缺了會怎樣，寫在檔案開頭。
try:
    import boto3
except ImportError:  # pragma: no cover - 只在沒裝 boto3 的環境走到
    boto3 = None


# boto3 的 client **不是** thread-safe，多執行緒共用會偶發炸。每個 worker 執行緒
# 各自持有一個，用 threading.local 存著重複利用（每檔都新建會慢很多）。
_local = threading.local()


def _s3_client(region: str):
    client = getattr(_local, "s3", None)
    if client is None:
        client = _local.s3 = boto3.client("s3", region_name=region)
    return client


class MissingLocalFile(RuntimeError):
    """stage 目錄缺檔。跟上傳失敗分開：這是資料沒備齊，重試不會好。"""


def _sync_one(entry: dict, *, bucket: str, region: str, stage: str) -> bool:
    """同步一個檔。回傳 True＝有上傳、False＝sha256 相同略過。

    冪等靠 S3 物件的 `sha256` metadata 比對，與序列版完全相同。
    """
    s3 = _s3_client(region)
    local = pathlib.Path(stage) / entry["path"].removeprefix("kb/")
    if not local.exists():
        raise MissingLocalFile(str(local))
    try:
        head = s3.head_object(Bucket=bucket, Key=entry["path"])
        if head.get("Metadata", {}).get("sha256") == entry["sha256"]:
            return False
    except s3.exceptions.ClientError:
        pass
    s3.upload_file(str(local), bucket, entry["path"],
                   ExtraArgs={"Metadata": {"sha256": entry["sha256"], "provenance": entry["provenance"]},
                              "ContentType": "text/plain; charset=utf-8"})
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="data/manifest.json")
    ap.add_argument("--stage", default="data/local/kb")
    ap.add_argument("--no-ingest", action="store_true", help="只同步 S3，不啟動 ingestion job")
    ap.add_argument("--workers", type=int, default=32,
                    help="S3 同步的並行執行緒數（預設 32；設 1 退回序列行為）")
    a = ap.parse_args()
    if boto3 is None:
        print("缺 boto3：pip install boto3，或 uv run --with boto3 -- python3 scripts/ingest_kb.py ...",
              file=sys.stderr)
        return 2
    bucket, kb_id, region = os.environ.get("S3_KB_BUCKET"), os.environ.get("BEDROCK_KB_ID"), os.environ.get("AWS_REGION")
    if not (bucket and region):
        print("缺 S3_KB_BUCKET 或 AWS_REGION", file=sys.stderr)
        return 2
    entries = json.loads(pathlib.Path(a.manifest).read_text(encoding="utf-8"))["entries"]
    uploaded = skipped = 0
    missing: list[str] = []
    failed: list[str] = []
    workers = max(1, a.workers)
    # 一個檔失敗不中斷其餘：全部跑完再一次報告，否則 2477 檔跑到一半停掉、
    # 下次重跑又得重新 head_object 一遍。冪等讓重跑是安全的。
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_sync_one, e, bucket=bucket, region=region, stage=a.stage): e
                   for e in entries}
        for fut in concurrent.futures.as_completed(futures):
            key = futures[fut]["path"]
            try:
                if fut.result():
                    uploaded += 1
                else:
                    skipped += 1
            except MissingLocalFile as exc:
                missing.append(str(exc))
            except Exception as exc:  # noqa: BLE001 — 任何供應商錯誤都記下來，不吞
                failed.append(f"{key}: {type(exc).__name__}: {exc}")
    print(f"S3 同步完成：上傳 {uploaded}、略過 {skipped}、並行 {workers}")
    if missing:
        print(f"缺本機檔 {len(missing)} 個：", file=sys.stderr)
        for m in missing[:20]:
            print(f"  {m}", file=sys.stderr)
        if len(missing) > 20:
            print(f"  …另外 {len(missing) - 20} 個", file=sys.stderr)
        return 3
    if failed:
        print(f"上傳失敗 {len(failed)} 個：", file=sys.stderr)
        for f in failed[:20]:
            print(f"  {f}", file=sys.stderr)
        if len(failed) > 20:
            print(f"  …另外 {len(failed) - 20} 個", file=sys.stderr)
        return 4
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
