#!/usr/bin/env python3
"""對著**真實的** bucket 掃一遍：快照那 11 部法規，母庫有沒有、檔名對不對得上、幾條。

    S3_KB_BUCKET=... [BEDROCK_KB_ID=...] [AWS_PROFILE=...] \
    uv run --with boto3 -- python3 scripts/check_statute_corpus.py

**為什麼要有這支腳本**：`backend/dossier/statute_text.corpus_key()` 把法規名直接組成
`kb/public/相關法規_全量/{法規名}.txt`，而那條規則**是從 `plans/2026-09-12-third-kb-pdf-
and-sidecars.md:23` 的目錄勘查推出來的，沒有對著個別檔名驗過**（寫這支腳本的機器上
沒有 AWS 憑證）。規則不對時的下場不是壞掉，是**條文永遠切不出來而畫面上只多一句
「母庫沒有這部法的全文」**——所以要有一個 30 秒跑得完的方式去確認。

輸出：快照 `articles[]` 的條數／**實際切得出來幾條**／行首條次標題數／來源（直接或後備），
最後加一份「條次標題前導字元」的普查。**切得出來幾條**才是驗收指標——
行首標題數對得上不代表每一條都切得出來（同一條出現兩次之類也會被拒）。

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


#: 這一版多印兩樣東西（2026-09-13 第一次實跑之後補的）：
#:
#: 1. **切得出來的條數要對著快照的 `articles[]` 比，不是 `max`。**
#:    `articles[]` 含「97之1」這種之一條文，`max` 不含。建築法 `max=105`／
#:    `articles=123`，拿 `1..max` 去比會得出「有一批條文不見了」的錯結論
#:    （team-lead 2026-09-13 自己更正過這一點）。
#:
#: 2. **條次標題前面實際出現過哪些字元。** 第一次實掃靠人眼看到
#:    `\x0c`（換頁符，PDF 轉文字留下的），而它讓「建築法第25條」——demo 案子的
#:    核心法條——整條切不出來。BOM／不換行空白之類還沒被排除，
#:    所以這裡直接把實際出現過的前導字元列出來，**不要靠猜**。
def _lead_chars(text: str) -> dict[str, int]:
    """每一行「行首到第一個 `第` 之間」出現過的字元與次數。只看看起來像條次的行。

    **切行用 `split("\n")` 不用 `splitlines()`**：`splitlines()` 把 `\x0c`
    也當成換行，於是換頁符永遠不會出現在「前導字元」裡——而它正是這支普查要找的東西
    （2026-09-13 寫這支時自己踩到）。`\r` 一併吃掉，避免 CRLF 檔案多算一個字元。
    """
    out: dict[str, int] = {}
    for line in (text or "").split("\n"):
        line = line.rstrip("\r")
        i = line.find("第")
        if i <= 0 or not line[i:i + 12].lstrip("第").lstrip().startswith(tuple("0123456789")):
            continue
        for ch in line[:i]:
            out[ch] = out.get(ch, 0) + 1
    return out


def main() -> int:
    snapshot = load_snapshot()
    laws = snapshot.get("laws", {})
    s3 = boto3.client("s3")
    kb = None
    print(f"{'法規名':<24}{'快照 articles':>14}{'切得出來':>10}{'行首標題':>10}  來源")
    print("-" * 104)
    missing: list[str] = []
    lead_census: dict[str, int] = {}
    for name, entry in sorted(laws.items()):
        articles = [str(a) for a in (entry.get("articles") or [])]
        key = statute_text.corpus_key(name)
        body, found_key, origin = None, "", ""
        try:
            body, found_key, origin = corpus.fetch_text(key, s3), key, "直接"
        except Exception:  # noqa: BLE001 — 找不到就走後備，分辨這件事正是本腳本的重點
            try:
                if kb is None:
                    kb = boto3.client("bedrock-agent-runtime")
                for h in corpus.search_statutes(name, limit=10, kb=kb) or []:
                    if str(h.get("t") or "").replace(" ", "").replace("\u3000", "") == name:
                        found_key = str(h.get("id") or "")
                        body, origin = corpus.fetch_text(found_key, s3), "後備"
                        break
            except Exception as e:  # noqa: BLE001
                found_key, origin = f"（後備失敗：{type(e).__name__}）", "—"
        if not body:
            missing.append(name)
            print(f"{name:<24}{len(articles):>14}{'—':>10}{'—':>10}  {origin} {found_key}")
            continue
        for ch, n in _lead_chars(body).items():
            lead_census[ch] = lead_census.get(ch, 0) + n
        heads = len(statute_text.article_headings(body))
        sliced = sum(1 for a in articles if statute_text.slice_article(body, a))
        flag = "" if sliced == len(articles) else f"  ← 缺 {len(articles) - sliced} 條"
        print(f"{name:<24}{len(articles):>14}{sliced:>10}{heads:>10}  {origin} {found_key}{flag}")
    print("-" * 104)
    print(f"母庫抓不到全文的：{'、'.join(missing) if missing else '（無）'}")
    print("\n條次標題的前導字元（實際出現過的，依次數排序）：")
    for ch, n in sorted(lead_census.items(), key=lambda kv: -kv[1]):
        allowed = "允許" if ch in " \t\x0c\u3000" else "**尚未允許**"
        print(f"  U+{ord(ch):04X} {ch!r:<10} {n:>7} 次   {allowed}")
    print("「尚未允許」的字元要加進 `backend/dossier/statute_text.py` 的 `_LINE_LEAD`，"
          "\n否則那些行的條文切不出來（而畫面上只會多一句「切不出第 N 條」）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
