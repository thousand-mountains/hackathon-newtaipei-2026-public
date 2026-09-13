#!/usr/bin/env python3
"""對著**真實的** bucket 掃一遍：快照那 11 部法規，母庫有沒有、檔名對不對得上、幾條。

    S3_KB_BUCKET=... [BEDROCK_KB_ID=...] [AWS_PROFILE=...] \
    uv run --with boto3 -- python3 scripts/check_statute_corpus.py

**為什麼要有這支腳本**：`backend/dossier/statute_text.corpus_key()` 把法規名直接組成
`kb/public/相關法規_全量/{法規名}.txt`，而那條規則**是從 `plans/2026-09-12-third-kb-pdf-
and-sidecars.md:23` 的目錄勘查推出來的，沒有對著個別檔名驗過**（寫這支腳本的機器上
沒有 AWS 憑證）。規則不對時的下場不是壞掉，是**條文永遠切不出來而畫面上只多一句
「母庫沒有這部法的全文」**——所以要有一個 30 秒跑得完的方式去確認。

輸出三欄：這部法在不在母庫（直接組的 key）／後備（拿法規名去 KB 搜、標題完全相等）
找到的 key／全文裡有幾個**行首**條次標題。第三欄是關鍵：它若遠小於快照的條數，
代表那份檔不是以行分條的，`slice_article` 會（刻意地）一條都切不出來。

**唯讀**：只有 `get_object` 與 KB `Retrieve`，不寫任何東西。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import boto3  # noqa: E402

from backend.config.settings import load_snapshot  # noqa: E402
from backend.dossier import corpus, statute_text  # noqa: E402


def main() -> int:
    snapshot = load_snapshot()
    laws = snapshot.get("laws", {})
    s3 = boto3.client("s3")
    kb = None
    print(f"{'法規名':<24}{'快照條數':>8}  {'直接 key':<8}{'後備找到的 key':<40}{'行首標題數':>10}")
    print("-" * 100)
    missing = []
    for name, entry in sorted(laws.items()):
        want = len(entry.get("articles") or []) or entry.get("max")
        key = statute_text.corpus_key(name)
        body, found_key, direct = None, "", "✗"
        try:
            body = corpus.fetch_text(key, s3)
            direct, found_key = "✓", key
        except Exception:  # noqa: BLE001 — 找不到就走後備，這支腳本的重點就是分辨這件事
            try:
                if kb is None:
                    kb = boto3.client("bedrock-agent-runtime")
                for h in corpus.search_statutes(name, limit=10, kb=kb) or []:
                    t = str(h.get("t") or "")
                    if t.replace(" ", "").replace("　", "") == name:
                        found_key = str(h.get("id") or "")
                        body = corpus.fetch_text(found_key, s3)
                        break
            except Exception as e:  # noqa: BLE001
                found_key = f"（後備失敗：{type(e).__name__}）"
        heads = len(statute_text.article_headings(body)) if body else 0
        if not body:
            missing.append(name)
        print(f"{name:<24}{want!s:>8}  {direct:<8}{found_key:<40}{heads:>10}")
    print("-" * 100)
    print(f"母庫抓不到全文的：{'、'.join(missing) if missing else '（無）'}")
    print("行首標題數遠小於快照條數的那幾部，`slice_article` 會一條都切不出來"
          "——那不是壞掉，是刻意不猜（見 backend/dossier/statute_text.py 檔頭）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
