#!/usr/bin/env python3
"""量相似案通道的品質，並讓多個 KB 並排比較。

    set -a; . ./.env; set +a
    uv run --with boto3 -- python3 scripts/measure_similar_case.py --kb A=IAYQZH0W4J --kb M=ZFTUBWNG0O

**走 `KBRetriever.search()` 的正式路徑**（配額 → 後過濾 → 去重 → rerank），
不是自己另寫一套檢索——量的必須是生產行為，否則量出來的數字沒有用。

## 量什麼

- **回傳筆數**：rerank 門檻之後還剩幾筆。太少代表候選池或門檻有問題
- **rerank 分數**：min / median。貼著門檻代表候選品質差
- **同案型率**：top-5 裡與查詢案案型相同的比例，golden 取自側檔的 `category`
- **相異來源數**：偵測同一份文件的多個 chunk 佔掉版面（承辦人看到 5 張卡其實幾件）
- **負控制**：語意無關的查詢必須回 0 筆

## 查詢怎麼來

用 `data/local/kb3/sample.json`（公開決定書的全文）反推出**生產形態的短查詢**：
取檔頭的「標題」＋內文前 120 字，長度與 `case_digest` 相當（實測 101–107 字）。
查詢對應的那篇會用 `exclude_case` 排除，否則等於問「跟自己最像的是誰」。

**不用 leave-one-out 的長查詢**（內文前 800 字）：那不是 N4 實際送出的形態，
而 2026-09-12 的量測顯示查詢長度會顯著改變分數分布。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings          # noqa: E402
from backend.retrieval.kb import KBRetriever  # noqa: E402

NEG = (("商標", "訴願人主張系爭商標與據以異議商標構成近似，有致相關消費者混淆誤認之虞。"),
       ("海關", "訴願人不服海關就進口貨物完稅價格之核定，主張應以交易價格為準。"),
       ("專利", "本件涉及專利申請案之進步性判斷，引證文件已揭露系爭申請專利範圍之技術特徵。"),
       ("閒聊", "今天天氣很好，我想去公園散步，順便買一杯咖啡。"))


def header(text: str, name: str) -> str | None:
    m = re.search(rf"^{name}[:：]\s*(.+?)\s*$", text[:600], re.M)
    return m.group(1) if m else None


def build_queries(n: int, seed: int) -> list[dict]:
    """從樣本反推生產形態的短查詢。"""
    path = ROOT / "data/local/kb3/sample.json"
    if not path.exists():
        raise SystemExit(f"缺 {path}（由檔頭抓取那一步產生）")
    items = [(k, v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()
             if "環保局全量" in k]
    random.seed(seed)
    out = []
    for k, t in random.sample(items, min(n, len(items))):
        body = re.sub(r"\s+", " ", t.split("=" * 20, 1)[-1])
        # **刻意不放檔頭的「標題」**：它寫的是「因違反空氣污染防制法事件提起訴願」，
        # 等於把 golden 答案抄進題目裡，同案型率會虛高到 100%。
        # 真實的卷證摘要不會逐字覆述案型（見 synthetic-*.json 的 case_digest）。
        out.append({"case_no": header(t, "案號"), "cat": header(t, "類別"),
                    "q": body[:150]})
    return [x for x in out if x["case_no"] and x["cat"]]


def measure(label: str, kb_id: str, region: str, queries: list[dict]) -> dict:
    rows, negs = [], []
    for item in queries:
        r = KBRetriever(kb_id=kb_id, region=region, exclude_case=item["case_no"])
        hits = r.search(item["q"], top_k=5)
        cats = [(h.payload or {}).get("category") for h in hits]
        rows.append({"n": len(hits),
                     "scores": [h.score for h in hits],
                     "same": sum(1 for c in cats if c == item["cat"]),
                     "distinct": len({h.source for h in hits})})
        print(".", end="", flush=True)
    for _, q in NEG:
        negs.append(len(KBRetriever(kb_id=kb_id, region=region).search(q, top_k=5)))
        print("n", end="", flush=True)
    allsc = [s for r in rows for s in r["scores"]]
    tot = sum(r["n"] for r in rows)
    return {"label": label,
            "hits_median": statistics.median(r["n"] for r in rows),
            "empty": sum(1 for r in rows if r["n"] == 0),
            "score_min": min(allsc) if allsc else None,
            "score_median": statistics.median(allsc) if allsc else None,
            "same_rate": sum(r["same"] for r in rows) / tot if tot else 0.0,
            "dup": tot - sum(r["distinct"] for r in rows),
            "neg_total": sum(negs)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", action="append", required=True, help="標籤=KB_ID，可重複")
    ap.add_argument("-n", type=int, default=12, help="查詢筆數")
    ap.add_argument("--seed", type=int, default=20260912)
    a = ap.parse_args()
    region = os.environ.get("AWS_REGION")
    if not region:
        raise SystemExit("缺 AWS_REGION（先 `set -a; . ./.env; set +a`）")
    queries = build_queries(a.n, a.seed)
    print(f"查詢 {len(queries)} 筆（生產形態短查詢）＋ 負控制 {len(NEG)} 筆")
    print(f"rerank 門檻 {settings.rerank_min_score()}、KB_MIN_SCORE {settings.kb_min_score()}\n")
    results = []
    for spec in a.kb:
        label, _, kb_id = spec.partition("=")
        print(f"{label}：", end="", flush=True)
        results.append(measure(label, kb_id, region, queries))
        print()
    print("\n| KB | 回傳筆數 median | 撈空 | rerank min | rerank median | 同案型率 | 重複來源 | 負控制殘存 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in results:
        print(f"| {r['label']} | {r['hits_median']:.0f}/5 | {r['empty']}/{len(queries)} | "
              f"{r['score_min']:.3f} | {r['score_median']:.3f} | {r['same_rate']*100:.0f}% | "
              f"{r['dup']} | {r['neg_total']} |")
    print("\n負控制殘存應為 0；重複來源＝同一份文件被不同 chunk 佔掉的席次數。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
