#!/usr/bin/env python3
"""把兩批來源整理成 KB 入庫用的 txt 與 manifest（spec 2026-09-07 §6.2–6.3）。

    python3 scripts/build_manifest.py --official "/path/資料集" --crawl "/path/cases.jsonl" \
        --out data/manifest.json --stage data/local/kb

manifest 只記路徑、來源、sha256、案號、結果；**不含內容、不含人名**，可進 git。

**這支產的是「我們要上傳什麼」，不是「庫裡有什麼」**（2026-09-13 標註）：
它只認得賽方資料集與爬蟲那兩批（有本機檔、算得出 sha256），而 S3 上另有四批
第三方整理、從未經過本機 stage 目錄的語料。要報「庫裡實際有什麼」請用
`scripts/build_kb_inventory.py`（產 `data/kb-inventory.json`，settings 報筆數讀那份）。
把 S3 那四批塞進本檔的輸出會讓 `ingest_kb.py` 每次報一萬多個「缺本機檔」。
stage 目錄與賽方資料不進 git（CONSTITUTION §6，`.gitignore` 的 `data/local/`）。

隱私（CONSTITUTION §6）：爬蟲那批的 `appellant`／`title`／`summary` 一律不寫進 manifest。
manifest 是會進 git 的檔，只放「哪個檔、哪來的、內容 hash 是多少」這種可公開的中介資訊。

需要 poppler 的 pdftotext（brew install poppler）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

# True = 入 KB。`相關法規` **2026-09-13 由 False 改為 True**（Claire 拍板）。
#
# 原本標 False 的理由是「走查表」：法規只需要驗條號，而條號查表
# （`backend/data/laws-snapshot.json` + `retrieval/lawtable.py`）純本機、deterministic，
# 不需要 KB。那個理由在「只驗條號」的前提下是對的，而前提變了——決定書理由段要寫
# 「按訴願法第 77 條第 7 款規定：『……』」，需要**條文原文**，而原文只能來自真的檔案
# （叫模型背法條是 CONSTITUTION §3 的紅線）。
#
# **查表那條路不變**：N4 通道 A 仍然只讀本機快照、仍然不打網路。入 KB 是為了讓條文原文
# 有一個可重建的來源（`scripts/build_law_articles.py` 從 `kb/official/相關法規/` 拉下來
# 建「條→項→款」索引），順帶讓右欄的 `search_statutes()` 拿得到完整的官方版——
# S3 上原有的 `kb/public/相關法規_全量/` 那批**每條都缺最後一項／款**
# （2026-09-13 實測：訴願法 §77 只有七款、§14 只有 3 項、§79 只有 2 項；
# 訴願法 101 條裡 57 條、行政程序法 176 條裡 107 條內容短少），拿它引條文會引出缺漏的法條。
OFFICIAL_DIRS = {"歷史訴願決定書": True, "行政函釋": True, "司法院釋字及行政判解": True, "相關法規": True}
CJK = re.compile(r"[一-鿿]")
OUTCOME = re.compile(r"(駁回|撤銷|不受理)")


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pdf_to_txt(pdf: pathlib.Path, out: pathlib.Path) -> tuple[bool, float]:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftotext", "-layout", str(pdf), str(out)], check=True)
    text = out.read_text(encoding="utf-8", errors="ignore")
    letters = [c for c in text if not c.isspace()]
    ratio = (sum(1 for c in letters if CJK.match(c)) / len(letters)) if letters else 0.0
    return ratio >= 0.6, round(ratio, 3)


def normalize_name(name: str) -> str:
    return name.replace(" 的副本", "").replace(".pdf", "").strip() + ".txt"


def safe_segment(s: str) -> str:
    """把值裡的路徑分隔字元換掉，免得它在檔名裡長出一層目錄。

    爬蟲的 `outcome` 有 21 筆長這樣：`部分不受理/駁回`、`不受理/駁回`、`部分不受理/撤銷`。
    直接拼進檔名，S3 上會變成 `…/1141060373_不受理/駁回.txt`——多一層 key，
    而 `retrieval/kb.py` 取檔名是 `rsplit("/", 1)[-1]`，拿到的是 `駁回.txt`，
    **案號整個掉了**，相似案卡會出現一張標題只寫「駁回」、看不出是哪件案子的卡片。
    """
    return s.replace("/", "、").replace("\\", "、").strip()


def roc_year_of(case_no: str) -> str | None:
    """爬蟲資料**沒有** `year` 欄位（2026-09-07 實測）。案號前三碼是民國年（例 `109`）。

    抓不到就給 None——manifest 寧可缺欄位，也不要放一個猜出來的年度。
    """
    head = case_no[:3]
    return head if len(head) == 3 and head.isdigit() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--official", required=True)
    ap.add_argument("--crawl", default=None, help="爬蟲 cases.jsonl（選填）")
    ap.add_argument("--out", default="data/manifest.json")
    ap.add_argument("--stage", default="data/local/kb")
    a = ap.parse_args()
    stage = pathlib.Path(a.stage)
    entries: list[dict] = []
    report: list[str] = []

    for sub, into_kb in OFFICIAL_DIRS.items():
        for pdf in sorted((pathlib.Path(a.official) / sub).rglob("*.pdf")):
            if not into_kb:
                report.append(f"SKIP（走查表）{sub}/{pdf.name}")
                continue
            rel = pathlib.Path(sub) / pdf.relative_to(pathlib.Path(a.official) / sub).parent / normalize_name(pdf.name)
            out = stage / "official" / rel
            # pdftotext 單檔失敗不該讓整批重跑：記進報告、跳過該筆（entries 少一筆比 manifest 不存在好）
            try:
                ok, ratio = pdf_to_txt(pdf, out)
            except (OSError, subprocess.SubprocessError) as e:
                report.append(f"FAIL（pdftotext）{rel}：{e}")
                continue
            if not ok:
                report.append(f"LOW-CJK {ratio} {rel}")
            m = OUTCOME.search(pdf.name)
            entries.append({"path": f"kb/official/{rel.as_posix()}", "provenance": "official", "sha256": sha256(out),
                            "source_pdf": pdf.name, "outcome": m.group(1) if m else None, "cjk_ratio": ratio})

    if a.crawl:
        with open(a.crawl, encoding="utf-8") as fh_in:
            for line in fh_in:
                r = json.loads(line)
                case_no = str(r.get("case_no") or r.get("eano") or "").strip()
                outcome = (r.get("outcome") or "").strip()
                if not case_no or not r.get("full_text"):
                    continue
                rel = pathlib.Path("新北訴願決定書_全量") / f"{safe_segment(case_no)}_{safe_segment(outcome) or '未知'}.txt"
                out = stage / "public" / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(r["full_text"], encoding="utf-8")
                entries.append({"path": f"kb/public/{rel.as_posix()}", "provenance": "public_crawl", "sha256": sha256(out),
                                "case_no": case_no, "outcome": outcome or None, "year": roc_year_of(case_no),
                                "category": r.get("category")})

    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.out).write_text(json.dumps({"generated_by": "scripts/build_manifest.py", "entries": entries},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    (stage / "ingest_report.md").write_text("\n".join(report) or "（無需人工處理）", encoding="utf-8")
    print(f"manifest：{len(entries)} 筆 → {a.out}；報告 → {stage / 'ingest_report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
