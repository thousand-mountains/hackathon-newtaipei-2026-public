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
import datetime as dt
import hashlib
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


def _sha_matches(s3, bucket: str, key: str, sha: str) -> bool:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except s3.exceptions.ClientError:
        return False
    return head.get("Metadata", {}).get("sha256") == sha


def _sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _upload_sidecar(s3, local: pathlib.Path, bucket: str, key: str, sha: str) -> None:
    """側檔也帶 `sha256` metadata，冪等比對才有東西可比（見 `_sync_one`）。"""
    s3.upload_file(str(local), bucket, key,
                   ExtraArgs={"Metadata": {"sha256": sha},
                              "ContentType": "application/json; charset=utf-8"})


def _sync_one(entry: dict, *, bucket: str, region: str, stage: str) -> tuple[bool, int]:
    """同步一個檔。回傳 (本文有沒有上傳, 側檔上傳數 0/1)。

    冪等靠 S3 物件的 `sha256` metadata 比對，與序列版完全相同。

    側檔跟著本文走：`x.txt` 的分類欄位在 `x.txt.metadata.json`（build_kb_metadata.py 產）。
    它不進 manifest（它不是獨立文件，是這一筆的屬性），但**冪等要自己算一份**
    ——側檔的 sha 跟本文的 sha 是兩件事（2026-09-12 覆核補）。

    原本只檢查「S3 上有沒有側檔」，缺了才補。那在側檔第一次生出來時是對的，
    但**改側檔產生器之後就會靜默留著舊版**：本文一個字都沒變（sha 相同）→ 略過 →
    S3 上還是舊的 `doc_kind`／`category`。而側檔現在是 `outcome`／`category` 的
    **優先來源**，也是 `REF_DOC_KINDS` 伺服器端 filter 的依據，用到舊值的表現是
    「欄位都在、值是錯的」——比缺欄位難查得多。所以改成比側檔自己的 sha256。

    沒有側檔（還沒跑過產生器）就跳過，不報錯。
    """
    s3 = _s3_client(region)
    local = pathlib.Path(stage) / entry["path"].removeprefix("kb/")
    if not local.exists():
        raise MissingLocalFile(str(local))
    side_local = local.with_name(local.name + ".metadata.json")
    side_key = entry["path"] + ".metadata.json"
    side_sha = _sha256_of(side_local) if side_local.exists() else None
    body_same = _sha_matches(s3, bucket, entry["path"], entry["sha256"])
    if not body_same:
        s3.upload_file(str(local), bucket, entry["path"],
                       ExtraArgs={"Metadata": {"sha256": entry["sha256"],
                                               "provenance": entry["provenance"]},
                                  "ContentType": "text/plain; charset=utf-8"})
    # 側檔獨立判斷：本文重傳時一起重傳；本文沒變也要在側檔是新的／改過時補上去。
    if side_sha and not _sha_matches(s3, bucket, side_key, side_sha):
        _upload_sidecar(s3, side_local, bucket, side_key, side_sha)
        return not body_same, 1
    return not body_same, 0


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
    uploaded = skipped = sidecars = 0
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
                did_upload, side = fut.result()
            except MissingLocalFile as exc:
                missing.append(str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 — 任何供應商錯誤都記下來，不吞
                failed.append(f"{key}: {type(exc).__name__}: {exc}")
                continue
            if did_upload:
                uploaded += 1
            else:
                skipped += 1
            sidecars += side
    print(f"S3 同步完成：上傳 {uploaded}、略過 {skipped}、側檔 {sidecars}、並行 {workers}")
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
    stats = job.get("statistics", {})
    print(json.dumps(stats, ensure_ascii=False))
    if job["status"] != "COMPLETE":
        return 1

    # 把「向量庫實際索引了幾份」寫進本機紀錄，給 `/api/health` 的 retrieval_note 用。
    # 沒有這一步，對外就只剩 manifest 的數字可報——而 manifest 是「打算入庫什麼」，
    # 不是「真的索引了什麼」。2026-09-12 那次兩者差了 4520 筆，對外卻報成一個數。
    #
    # 用 numberOfDocumentsScanned 當「已索引」：它是本次 job 在 data source 掃到的
    # 文件總數，也就是向量庫現在涵蓋的範圍。新增／修改那兩個欄位只講「這次動了幾份」，
    # 冪等重跑時會是 0，拿它當總數會報成 0 筆。**整包 statistics 一併存檔**，
    # 日後要換算法時看得到原始數字，不用回頭猜當初是怎麼算的。
    scanned = stats.get("numberOfDocumentsScanned")
    if not isinstance(scanned, int):
        print("ingestion 完成但 statistics 沒有 numberOfDocumentsScanned，"
              "不寫 index-state.json（寧可讓對外說『沒有紀錄』，也不寫一個猜的數字）",
              file=sys.stderr)
        return 0
    state_path = pathlib.Path(a.manifest).parent / "index-state.json"
    state_path.write_text(json.dumps({
        "completed_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "documents_indexed": scanned,
        "manifest_entries": len(entries),
        "manifest_path": a.manifest,
        "statistics": stats,
        "note": ("documents_indexed 取自 ingestion job 的 numberOfDocumentsScanned"
                 "（本次 job 在 data source 掃到的文件總數）。"
                 "本檔不含 KB id 與 bucket 名——那兩個只活在 .env。"),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已寫入 {state_path}：已索引 {scanned} 筆、清單 {len(entries)} 筆"
          + ("（一致）" if scanned == len(entries) else f"（**不一致，差 {len(entries) - scanned} 筆**）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
