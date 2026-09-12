#!/usr/bin/env python3
"""量 `KB_MIN_SCORE` 該設多少：三組查詢 → 各門檻的存活筆數與同案型率。

    set -a; . ./.env; set +a
    uv run --with boto3 -- python3 scripts/measure_kb_min_score.py

結果與結論見 `docs/evidence/2026-09-12-bedrock-live/kb-min-score.md`。

## 為什麼要三組

單看「調高門檻命中會變少」不足以決定門檻，因為那不分辨**少掉的是雜訊還是正確命中**。
所以三組一起看：

- **A 組 leave-one-out**：拿決定書自己的內文當查詢、排除自己，golden 取 manifest 的
  `category`。量的是「門檻對 precision 有沒有貢獻」。
- **B 組 真實 case_query**：合成案的卷證摘錄（N4 通道 B 真正送出去的那種長查詢）。
  量的是生產實況——A 組的查詢形態跟生產不一樣，不能直接外推。
- **C 組 負控制**：語料裡**完全不存在**的案型（商標、海關、專利）加一句閒聊。
  這組是決定性的：門檻若壓不下無關查詢的分數，它就沒有鑑別力，
  「調到剛好」這個想法本身不成立。

2026-09-12 的實測結論是 C 組推翻了門檻的前提——閒聊句拿 0.731，
比 A 組任何一筆真實同案型命中（median 0.206）都高。

## 不做什麼

- **不改任何設定**。只讀 KB、印表格，要不要改由人看完決定。
- **不產生 golden**。A 組的 golden 是 manifest 既有的 `category` 欄位，不是這支腳本判的。
- **不印個資**。只印檔名、分類欄位與分數；決定書內文不進輸出。

需要 boto3 與可打 Bedrock 的憑證，以及本機的 `data/local/kb`（賽方資料集不進 git）。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import statistics
import sys
import time
import urllib.parse

try:
    import boto3
except ImportError:  # pragma: no cover - 沒裝 boto3 的環境走到
    boto3 = None

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))          # 直接跑腳本時讓 `import backend...` 找得到

from backend.retrieval import kb as kb_module  # noqa: E402 — 要先設好 sys.path

# 門檻候選要**橫跨兩種 KB 的分數尺度**：同一個查詢，MANAGED KB 給 0.397、
# S3 Vectors 給 0.691（2026-09-12 實測）。只列到 0.35 的話，在 S3 Vectors 上
# 每一格都是「全數存活」，表格看起來很漂亮但什麼都沒量到。
THRESHOLDS = (0.0, 0.15, 0.25, 0.35, 0.50, 0.60, 0.70,
              0.72, 0.74, 0.76, 0.78, 0.80, 0.82, 0.85, 0.90)
CASE_PREFIXES = ("歷史訴願決定書/", "新北訴願決定書_全量/")
SAMPLE_N = 40
# 這四句刻意挑語料裡沒有的領域。最後一句不是玩笑：它是「完全不相干的自然語言」
# 這個極端，門檻若連它都擋不掉，就證明分數沒有絕對意義。
NEGATIVE_CONTROLS = (
    "訴願人主張系爭商標與據以異議商標構成近似，有致相關消費者混淆誤認之虞。",
    "訴願人不服海關就進口貨物完稅價格之核定，主張應以交易價格為準。",
    "本件涉及專利申請案之進步性判斷，引證文件已揭露系爭申請專利範圍之技術特徵。",
    "今天天氣很好，我想去公園散步，順便買一杯咖啡。",
)


def relative_path(uri: str) -> str:
    """S3 URI → `kb/{official,public}/` 之後的相對路徑。

    解析規則跟 `backend/retrieval/kb.py:_relative_path` 同一套（Managed KB 回的是
    percent-encoded 的 https URI，不是 `s3://`）。這裡只取相對路徑、不取 official/public，
    因為量測只按前綴分通道。
    """
    path = urllib.parse.unquote(uri)
    if path.startswith("s3://"):
        path = path.split("/", 3)[-1]
    elif path.startswith(("http://", "https://")):
        host, _, tail = path.split("://", 1)[1].partition("/")
        path = tail.split("/", 1)[-1] if host.split(".", 1)[0] == "s3" else tail
    m = re.match(r"^kb/(?:official|public)/(.+)$", path)
    return m.group(1) if m else path


def main() -> int:
    if boto3 is None:
        print("缺 boto3：uv run --with boto3 -- python3 scripts/measure_kb_min_score.py", file=sys.stderr)
        return 2
    kb_id, region = os.environ.get("BEDROCK_KB_ID"), os.environ.get("AWS_REGION")
    if not (kb_id and region):
        print("缺 BEDROCK_KB_ID 或 AWS_REGION（先 `set -a; . ./.env; set +a`）", file=sys.stderr)
        return 2
    client = boto3.client("bedrock-agent-runtime", region_name=region)
    last_call = 0.0

    def retrieve(text: str, n: int = 50) -> list[dict]:
        # 搜尋設定鍵由 `kb.py` 決定，**這裡不自己寫一份**——腳本與正式路徑寫不同的鍵，
        # 量出來的東西就不是生產行為（2026-09-12 這個坑踩過一次）。
        # 節流：賽方規範要求 Bedrock 壓在 1 RPS 以下，而本腳本會連打 40＋ 次。
        nonlocal last_call
        wait = kb_module.RETRIEVE_INTERVAL_S - (time.monotonic() - last_call)
        if wait > 0:
            time.sleep(wait)
        resp = kb_module.retrieve_raw(client, kb_id, text, n)
        last_call = time.monotonic()
        out = []
        for x in resp.get("retrievalResults", []):
            md = x.get("metadata") or {}
            uri = md.get("_source_uri") or ((x.get("location") or {}).get("s3Location") or {}).get("uri", "")
            out.append({"score": float(x.get("score", 0.0)), "cat": md.get("category"),
                        "rel": relative_path(uri), "is_pdf": md.get("_file_type") == "PDF"})
        return out

    def usable(hits: list[dict], t: float, exclude: str | None = None) -> list[dict]:
        """套用跟 `kb.py` 一樣的後過濾：門檻、前綴、PDF、排除自己、同源去重。"""
        seen: set[str] = set()
        out = []
        for h in hits:
            if h["score"] < t or h["is_pdf"]:
                continue
            if not any(h["rel"].startswith(p) for p in CASE_PREFIXES):
                continue
            if exclude and exclude in h["rel"]:
                continue
            if h["rel"] in seen:
                continue
            seen.add(h["rel"])
            out.append(h)
        return out

    manifest = ROOT / "data/manifest.json"
    if not manifest.exists():
        print(f"缺 {manifest}（先跑 scripts/build_manifest.py）", file=sys.stderr)
        return 3
    entries = json.loads(manifest.read_text(encoding="utf-8"))["entries"]
    pub = [e for e in entries if e["provenance"] == "public_crawl" and e.get("category")]
    if len(pub) < SAMPLE_N:
        print(f"公開決定書只有 {len(pub)} 筆，不足 {SAMPLE_N} 筆樣本", file=sys.stderr)
        return 3

    # ── A 組 ──────────────────────────────────────────────
    print(f"## A 組：leave-one-out（{SAMPLE_N} 筆公開決定書，查詢＝內文前 800 字，排除自己）\n")
    step = len(pub) // SAMPLE_N
    rows = []
    for i in range(SAMPLE_N):
        e = pub[i * step]
        local = ROOT / "data/local/kb" / e["path"].removeprefix("kb/")
        if not local.exists():
            print(f"缺本機檔：{local}（賽方資料集不進 git，見 CLAUDE.md）", file=sys.stderr)
            return 3
        text = re.sub(r"\s+", " ", local.read_text(encoding="utf-8"))[:800]
        rows.append({"cat": e["category"], "self": local.name.rsplit(".", 1)[0],
                     "hits": retrieve(text)})
        print(".", end="", flush=True)
    print("\n")
    print("| 門檻 | 撈滿 5 筆 | 平均可用筆數 | top5 同案型率 |")
    print("|---|---|---|---|")
    for t in THRESHOLDS:
        full = avail = same = tot = 0
        for r in rows:
            s = usable(r["hits"], t, exclude=r["self"])
            avail += len(s)
            full += len(s) >= 5
            for h in s[:5]:
                tot += 1
                same += bool(h["cat"] and (h["cat"] in r["cat"] or r["cat"] in h["cat"]))
        print(f"| {t:.2f} | {full}/{SAMPLE_N} | {avail/len(rows):.1f} | "
              f"{same}/{tot} = {100*same/max(tot,1):.0f}% |")
    scores = [h["score"] for r in rows for h in r["hits"] if r["self"] not in h["rel"]]
    print(f"\n非自身命中的 score：max={max(scores):.3f} "
          f"p90={statistics.quantiles(scores, n=10)[8]:.3f} "
          f"median={statistics.median(scores):.3f} min={min(scores):.3f}")

    # ── B 組 ──────────────────────────────────────────────
    print("\n## B 組：真實 case_query（合成案卷證摘錄，N4 通道 B 實際送出的形態）\n")
    for path in sorted((ROOT / "backend/data/synthetic").glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        query = "；".join(d["text"] for d in case.get("documents", []))
        if not query:
            continue
        hits = retrieve(query, n=15)   # overfetch=3 × top_k=5，跟 N4 一致
        line = "  ".join(f"{t:.2f}→{len(usable(hits, t, case.get('exclude_case')))}" for t in THRESHOLDS)
        print(f"### {case['id']}（查詢 {len(query)} 字，抓 15 筆）\n  各門檻可用筆數：{line}")
        for h in usable(hits, 0.0, case.get("exclude_case"))[:6]:
            print(f"   {h['score']:.4f}  cat={h['cat']!r:22} {h['rel'][:58]}")
        print()

    # ── C 組 ──────────────────────────────────────────────
    print("\n## C 組：負控制（語料裡沒有這些案型，分數應該要明顯低於 A／B 組）\n")
    for q in NEGATIVE_CONTROLS:
        s = [h["score"] for h in usable(retrieve(q, n=15), 0.0)]
        if not s:
            print(f"「{q[:26]}…」→ 0 筆")
            continue
        print(f"「{q[:26]}…」\n   {len(s)} 筆；max={max(s):.4f} median={statistics.median(s):.4f}  "
              + "  ".join(f"{t:.2f}→{sum(1 for x in s if x >= t)}" for t in (0.70, 0.74, 0.76, 0.78, 0.80)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
