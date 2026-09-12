#!/usr/bin/env python3
"""換 corpus／換 KB 之後跑一次：設定要的前綴與 KB 裡實際有的目錄對不對得上。

    set -a; . ./.env; set +a
    uv run --with boto3 -- python3 scripts/preflight_kb.py

## 它防的是什麼

前綴比對是**後過濾**：`retrieve` 撈回來之後，我們自己按路徑前綴篩。前綴設錯一個字，
篩完就是空的——**回 0 筆、不報錯**。畫面上「相似案：無」，跟 KB 掛掉、跟真的沒有
相似案，長得一模一樣。`kb.py` 的 `_relative_path` 說明記著同一類問題咬過一次。

這不是假想：`DEFAULT_SIMILAR_CASE_QUOTA` 寫的是 `新北訴願決定書_全量/`，
而第三方 corpus 的目錄叫 `新北訴願決定書_環保局全量/`。忘了設 `.env` 的人
拿到的不是錯誤訊息，是安靜的空結果。

`REF_DOC_KINDS` 有防呆（篩空就退回不篩），但那個防呆製造了新盲點：
**值打錯與這個 KB 沒有側檔，表現成同一件事**。`ref_letter` 打成 `ref_letters`，
伺服器端過濾帶來的那個 0→16 筆改善會無聲消失。本腳本把這兩種分開。

## 它不做斷言，只做對照

取樣撈不到某個前綴，**不代表設定錯**——也可能是這五句探針剛好不碰那類文件。
所以輸出是「設定要的」與「KB 實際有的」並排，撈不到時附上最接近的實際目錄名。
判斷留給看的人；替他決定「這是錯的」會在探針覆蓋不足時誤報。

## 為什麼不掛在 N4 每案跑

每個探針一次 retrieve，1 RPS 下五次五秒多。相似案通道端到端已經 5.8 秒、
硬上限 90 秒（`architecture.md:587`）。這是換 corpus 時跑一次的檢查，
不是每件案子都要付的成本。

exit code：有前綴撈不到、或 doc_kind 不是 ok，回 1；全部對得上回 0。
"""
from __future__ import annotations

import os
import pathlib
import sys

try:
    import boto3
except ImportError:  # pragma: no cover - 沒裝 boto3 的環境走到
    boto3 = None

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))          # 直接跑腳本時讓 `import backend...` 找得到

from backend.retrieval import kb as kb_module  # noqa: E402 — 要先設好 sys.path

STATE_LABEL = {
    "ok": "✅ 正常",
    "missing": "⚠ 取樣裡沒有這個值——值打錯，或該類文件不在這個 KB",
    "no_sidecar": "❌ 這個 KB 的取樣完全沒有 doc_kind 側檔，REF_DOC_KINDS 不該開",
}


def main() -> int:
    if boto3 is None:
        print("缺 boto3：uv run --with boto3 -- python3 scripts/preflight_kb.py", file=sys.stderr)
        return 2
    kb_id, region = os.environ.get("BEDROCK_KB_ID"), os.environ.get("AWS_REGION")
    if not (kb_id and region):
        print("缺 BEDROCK_KB_ID 或 AWS_REGION（先 `set -a; . ./.env; set +a`）", file=sys.stderr)
        return 2
    client = boto3.client("bedrock-agent-runtime", region_name=region)
    r = kb_module.check_channels(client, kb_id)

    print(f"## 取樣：{r['probes']} 句探針 × 深度 50，共 {r['sampled']} 筆"
          f"（搜尋設定鍵 `{r['search_key']}`）\n")

    print("## KB 裡實際有的第一層目錄\n")
    if r["kb_dirs"]:
        for d, n in r["kb_dirs"].items():
            print(f"  {n:>4}  {d}")
    else:
        print("  （取樣沒撈到任何東西——KB 是空的，或查詢與語料完全不相干）")

    print("\n## 設定要的前綴\n")
    print("| 前綴 | 來自 | 取樣命中 | 判讀 |")
    print("|---|---|---|---|")
    bad = typos = 0
    for p in r["prefixes"]:
        src = "、".join(p["sources"])
        if p["sampled_hits"]:
            note = "✅"
        else:
            bad += 1
            typos += bool(p["closest"])
            note = "⚠ 取樣沒撈到"
            if p["closest"]:
                note += f"。KB 裡最接近的是 **`{p['closest']}`** ← 疑似打錯字"
            else:
                # 沒有相近名字（或相近的那個本身也在設定裡）＝ 這個 KB 沒有這個目錄。
                # 多半是別的 corpus 留下的設定，無害；但也可能該類文件真的沒進庫。
                note += "。這個 KB 沒有這個目錄——多半是別的 corpus 留下的設定"
        print(f"| `{p['prefix']}` | {src} | {p['sampled_hits']} | {note} |")

    print("\n## REF_DOC_KINDS\n")
    if not r["doc_kinds"]:
        print("（沒設，伺服器端不篩 doc_kind——這是預設值）")
    else:
        print(f"KB 取樣看到的 doc_kind：{r['kb_doc_kinds'] or '（一個都沒有）'}\n")
        for k in r["doc_kinds"]:
            if k["state"] != "ok":
                bad += 1
            line = f"- `{k['doc_kind']}`（取樣 {k['sampled_hits']} 筆）：{STATE_LABEL[k['state']]}"
            if k["closest"]:
                line += f"　KB 裡最接近的是 **`{k['closest']}`**"
            print(line)

    if typos:
        # 打錯字跟「這個 KB 沒有這個目錄」的嚴重性差一個數量級，總結不能混為一談：
        # 前者是通道靜默失效，後者多半只是殘留設定。措辭要跟著實際情況走，
        # 否則決賽現場讀的人會為了無害的那種去查一個不存在的 bug。
        print(f"\n**{bad} 項對不上，其中 {typos} 項疑似打錯字。** 打錯字幾乎一定是真的問題"
              "——那正是通道靜默回 0 筆的樣子，照上表的「最接近」改 `.env` 再跑一次。")
    elif bad:
        print(f"\n**{bad} 項取樣沒撈到，但沒有一項像打錯字。** 多半是別的 corpus 留下的設定"
              "（無害），也可能是那類文件沒進這個 KB、或五句探針剛好不碰它。"
              "確認該類文件在這個 KB 裡本來就該有，再決定要不要理。")
    else:
        print("\n**全部對得上。**")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
