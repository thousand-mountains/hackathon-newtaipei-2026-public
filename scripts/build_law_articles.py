"""把 KB 上的「相關法規」解析成**條號 → 條文原文**索引。

    set -a; . ./.env; set +a
    uv run --with boto3 -- python3 scripts/build_law_articles.py
      → backend/data/local/law-articles.json

## 為什麼要有這支

`backend/data/laws-snapshot.json` 只有**條號**（`{"articles": ["1","2",…]}`），
所以 N4 檢索回來的每一條法規 `q` 欄位一律留白、`q_note` 寫「條文原文不在快照內」。
結果是理由段只能標 `[L3]`，寫不出訴願決定書該有的

    一、按訴願法第 77 條第 7 款規定：「訴願事件有左列各款情形之一者，應為不受理
        之決定：七、對已決定或已撤回之訴願事件重行提起訴願者。」。

而那一句是決定書理由段的標準開頭。沒有原文就叫模型把條文寫出來，等於請它背法條，
那是 CONSTITUTION §3 的紅線——所以條文原文只能來自真的檔案。

## 為什麼從 S3 抓，卻在本機建索引

**來源是 S3（`kb/official/相關法規/`），查詢是本機。** 兩件事分開的理由：

- S3 上是**一部法規一個檔**（`訴願法.pdf` 是整部法）。理由段要引的是
  「第 77 條**第 7 款**」，拿整部法回來還是得切到條／項／款——切法只能有一份，
  放在這裡（build 時切一次），不放在 N4（每次生成草稿都切一次，還會跟這裡漂掉）。
- N4 通道 A 的承諾是**同輸入必同輸出、零網路**（`retrieval/lawtable.py`，
  CONSTITUTION 規則引擎零 LLM）。把 S3 呼叫塞進那條路，等於讓「這個條號存不存在」
  的答案取決於當下 KB 通不通。
- 另一條 KB 法規通道已經存在（`dossier/corpus.search_statutes()` → `GET /api/laws?q=`），
  那條服務的是右欄卷宗查找，而且它自己標了 `verified: False`——KB 全文沒有經過
  條號查表驗證。**兩條通道不合併**：一條負責「查得到什麼」，一條負責「驗得了什麼」。

## 來源優先序

1. `s3://$S3_KB_BUCKET/kb/official/相關法規/*.pdf`（預設，需要 boto3 與 `.env`）
2. `data/local/kb3/pdf/相關法規/*.pdf`（本機資料集副本）
3. `data/local/kb3/txt/相關法規/*.txt`（已抽好的文字）

PDF 一律用 `pdftotext -layout` 抽字。**`-layout` 不能拿掉**：這份解析器完全靠
全國法規資料庫列印版的固定欄寬（條號欄、項次欄、內文欄）分辨「項次」與「折行」，
不保留版面的話 `1` 跟內文會黏成一行，項次全部解析不出來。
實測 1 與 3 兩條路徑產出的文字**逐字相同**（現有的 txt 本來就是這樣抽的）。

## 產物不進 git（CONSTITUTION §6）

賽方資料集僅供競賽之用，產物只落在 gitignored 的 `backend/data/local/`。
**不要把輸出併進 `backend/data/laws-snapshot.json`**——那一份在 git 裡。

產物不在時全流程照跑，只是 `q` 回到留白（見 `backend/retrieval/law_articles.py`）。
缺資料要看得出來是缺資料，不是靠一份 commit 進去的副本蓋過去。

## 來源格式（全國法規資料庫列印版，固定欄寬）

    第 77 條       訴願事件有左列各款情形之一者，應為不受理之決定：

                 一、訴願書不合法定程式不能補正或經通知補正逾期不補正者。

    第 79 條   1   訴願無理由者，受理訴願機關應以決定駁回之。

             2   原行政處分所憑理由雖屬不當，但依其他理由認為正當者，應以訴願
                 為無理由。

三件實測過、決定了解析寫法的事：

1. **條號是阿拉伯數字，章節是國字**（`第 一 章 總則`）。所以 `第\\s*\\d+\\s*條`
   不會誤收章節標題；章節標題另外由 `DIVISION_RE` 擋掉，否則它會被當成折行，
   接到前一條的條文尾巴裡（實測：行政程序法 §101 尾端接上「第 二 節 陳述意見及聽證」）。
2. **本文裡的條號引用一律是國字**（「未於第五十七條但書所定期間」），不帶空格，
   同樣不會被誤判成新的一條。但跨頁重印的頁首仍可能出現阿拉伯數字條號，
   所以再加一道**單調遞增**的守門：條號沒有變大就不當作新條開始。
3. **換行是欄寬折行，不是段落**。折行處直接相接（中文不補空白）；
   `一、`／`二、` 這種款次才是真正的分段。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

try:
    import boto3
except ImportError:  # pragma: no cover — 只在沒裝 boto3 的環境走到（會退回本機來源）
    boto3 = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: S3 上賽方資料集的法規目錄（`scripts/ingest_kb.py` 上傳的那批）。
S3_PREFIX = "kb/official/相關法規/"
#: 本機後備。1 = 資料集的 PDF 副本，2 = 已抽好的文字。
LOCAL_PDF_DIR = ROOT / "data" / "local" / "kb3" / "pdf" / "相關法規"
LOCAL_TXT_DIR = ROOT / "data" / "local" / "kb3" / "txt" / "相關法規"
OUTPUT = ROOT / "backend" / "data" / "local" / "law-articles.json"

# `第 77 條` / `第 22-1 條`。條號一律阿拉伯數字（章節用國字，見檔頭第 1 點）。
ARTICLE_RE = re.compile(r"^(\s*)第\s*(\d+)(?:\s*[-之]\s*(\d+))?\s*條\s*(.*)$")
# 項次：行首的孤立阿拉伯數字 + 兩格以上空白 + 內容。
PARAGRAPH_RE = re.compile(r"^\s*(\d{1,2})\s{2,}(\S.*)$")
# 款次：`一、`…`二十、`。國字數字後面緊跟頓號。
ITEM_RE = re.compile(r"^\s*([一二三四五六七八九十]{1,4})、\s*(.*)$")
# 檔頭中繼資料，逐行丟棄。
SKIP_PREFIXES = ("列印時間：", "所有條文", "法規名稱：", "修正日期：", "公布日期：", "法規類別：")
# 編／章／節／款／目 標題（國字序數）。**一定要在折行處理之前擋掉**：它們夾在兩條之間，
# 當成折行接上去就會長在前一條的條文尾巴裡（實測：行政程序法 §101 第 2 項尾端
# 被接上「第 二 節 陳述意見及聽證」，而那份文字會原封不動被引進決定書理由段）。
DIVISION_RE = re.compile(r"^\s*第\s*[一二三四五六七八九十百]+\s*[編章節款目]\s")


def _article_sort_key(no: str) -> tuple[int, int]:
    """`22之1` 排在 `22` 之後、`23` 之前。單調守門與輸出排序共用同一把尺。"""
    head, _, tail = no.partition("之")
    return int(head), int(tail or 0)


def parse_law(text: str) -> dict[str, Any]:
    """一部法規的純文字 → `{條號: {paragraphs: [...], text: "..."}}`。

    `paragraphs[]` 保留「項 → 柱書 ＋ 款」的結構，因為決定書引述時要引得出
    「第 77 條**第 7 款**」——只存一整塊字串的話，引一款就得在別處再切一次，
    而那個切法會跟這裡漂掉。
    """
    articles: dict[str, Any] = {}
    cur_no: str | None = None
    cur_paras: list[dict[str, Any]] = []
    last_key = (-1, -1)

    def flush() -> None:
        if cur_no is not None:
            articles[cur_no] = {"paragraphs": _clean(cur_paras)}

    def open_paragraph(n: str | None, lead: str) -> dict[str, Any]:
        para: dict[str, Any] = {"n": n, "lead": lead, "items": []}
        cur_paras.append(para)
        return para

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith(SKIP_PREFIXES):
            continue
        if DIVISION_RE.match(line):
            continue

        m = ARTICLE_RE.match(line)
        if m:
            # 條號格式**必須與 `laws-snapshot.json` 一致**（`22之1`，不是 `22-1`）：
            # 兩份索引是用 (法規名, 條號) 這組鍵對接的，格式差一個字就全部查不到，
            # 而查不到的下場是條文原文靜靜留白，不會有人收到錯誤。
            no = m.group(2) + (f"之{m.group(3)}" if m.group(3) else "")
            rest = m.group(4).strip()
            key = _article_sort_key(no)
            if key > last_key:  # 單調守門：跨頁頁首重印不會開新的一條
                flush()
                cur_no, last_key = no, key
                cur_paras = []
                if rest:
                    pm = PARAGRAPH_RE.match(rest) if re.match(r"^\d{1,2}\s{2,}", rest) else None
                    if pm:
                        open_paragraph(pm.group(1), pm.group(2).strip())
                    else:
                        open_paragraph(None, rest)
                continue

        if cur_no is None:  # 第 1 條之前的章節標題與檔頭
            continue

        pm = PARAGRAPH_RE.match(line)
        if pm:
            open_paragraph(pm.group(1), pm.group(2).strip())
            continue

        im = ITEM_RE.match(line)
        if im:
            if not cur_paras:
                open_paragraph(None, "")
            cur_paras[-1]["items"].append({"n": im.group(1), "t": im.group(2).strip()})
            continue

        # 純折行：接到最後一個開著的容器尾巴。中文折行不補空白。
        chunk = line.strip()
        if not cur_paras:
            open_paragraph(None, chunk)
        elif cur_paras[-1]["items"]:
            cur_paras[-1]["items"][-1]["t"] += chunk
        else:
            cur_paras[-1]["lead"] += chunk

    flush()
    return {no: articles[no] for no in sorted(articles, key=_article_sort_key)}


def _clean(paras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for p in paras:
        lead = p["lead"].strip()
        items = [{"n": i["n"], "t": i["t"].strip()} for i in p["items"] if i["t"].strip()]
        if lead or items:
            out.append({"n": p["n"], "lead": lead, "items": items})
    return out


def pdf_to_text(pdf: pathlib.Path) -> str:
    """`pdftotext -layout`。**`-layout` 不能拿掉**（理由見檔頭「來源優先序」）。

    抽不出字就硬失敗，不回空字串：一份空的法規解析出 0 條，而 0 條在輸出 JSON 裡
    跟「這部法規沒有條文」長得一模一樣，不會有人發現。
    """
    if not shutil.which("pdftotext"):
        raise RuntimeError(
            "找不到 pdftotext（poppler）。macOS：brew install poppler；"
            "Debian/Ubuntu：apt install poppler-utils。"
            "或改用 --from local-txt 直接吃已抽好的文字。"
        )
    out = subprocess.run(  # noqa: S603 — 引數全為本地路徑，無 shell
        ["pdftotext", "-layout", str(pdf), "-"],
        capture_output=True,
        check=True,
    )
    text = out.stdout.decode("utf-8", errors="replace")
    if not text.strip():
        raise RuntimeError(f"{pdf.name} 抽不出任何文字（可能是掃描件）。")
    return text


def load_from_s3(bucket: str, region: str | None) -> dict[str, str]:
    """S3 `kb/official/相關法規/` → `{法規名: 純文字}`。"""
    if boto3 is None:
        raise RuntimeError("需要 boto3：uv run --with boto3 -- python3 scripts/build_law_articles.py")
    s3 = boto3.client("s3", region_name=region)
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=S3_PREFIX):
        keys.extend(o["Key"] for o in page.get("Contents") or [])
    if not keys:
        raise RuntimeError(f"s3://{bucket}/{S3_PREFIX} 是空的——KB 還沒 ingest？")

    out: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for key in sorted(keys):
            name = pathlib.Path(key).stem
            suffix = pathlib.Path(key).suffix.lower()
            if suffix not in (".pdf", ".txt"):
                continue
            local = pathlib.Path(tmp) / pathlib.Path(key).name
            s3.download_file(bucket, key, str(local))
            out[name] = pdf_to_text(local) if suffix == ".pdf" else local.read_text(encoding="utf-8")
    return out


def load_from_local(kind: str) -> dict[str, str]:
    if kind == "local-pdf":
        if not LOCAL_PDF_DIR.is_dir():
            raise RuntimeError(f"找不到 {LOCAL_PDF_DIR}")
        return {p.stem: pdf_to_text(p) for p in sorted(LOCAL_PDF_DIR.glob("*.pdf"))}
    if not LOCAL_TXT_DIR.is_dir():
        raise RuntimeError(f"找不到 {LOCAL_TXT_DIR}")
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(LOCAL_TXT_DIR.glob("*.txt"))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--from",
        dest="source",
        choices=("s3", "local-pdf", "local-txt"),
        default="s3",
        help="來源。預設 s3（讀 $S3_KB_BUCKET）；取不到時自動退回本機副本。",
    )
    ap.add_argument("--bucket", default=os.environ.get("S3_KB_BUCKET"))
    ap.add_argument("--region", default=os.environ.get("AWS_REGION"))
    a = ap.parse_args()

    # 退回本機是**明講的降級**，不是靜默的等價替代：兩者的文字實測逐字相同，
    # 但「這份索引是從哪裡建的」會寫進輸出 JSON 的 `source`，之後查得到。
    source, raw = a.source, {}
    try:
        if source == "s3":
            if not a.bucket:
                raise RuntimeError("沒有 $S3_KB_BUCKET（set -a; . ./.env; set +a）")
            raw = load_from_s3(a.bucket, a.region)
            origin = f"s3://{a.bucket}/{S3_PREFIX}"
        else:
            raw = load_from_local(source)
            origin = str((LOCAL_PDF_DIR if source == "local-pdf" else LOCAL_TXT_DIR).relative_to(ROOT))
    except Exception as e:  # noqa: BLE001 — 任何取不到來源的理由都要具名印出來再降級
        if source != "s3":
            print(f"取不到來源：{e}", file=sys.stderr)
            return 1
        print(f"⚠ S3 取不到（{e}），改用本機資料集副本。", file=sys.stderr)
        for fallback in ("local-pdf", "local-txt"):
            try:
                raw = load_from_local(fallback)
                origin = f"{fallback}（S3 不可用時的後備）"
                break
            except RuntimeError as e2:
                print(f"  {fallback}：{e2}", file=sys.stderr)
        else:
            print("本機也沒有資料集副本，無法建索引。", file=sys.stderr)
            return 1

    laws: dict[str, Any] = {}
    for name, text in sorted(raw.items()):
        parsed = parse_law(text)
        if not parsed:
            print(f"⚠ {name}：解析出 0 條，已跳過（來源格式可能不是列印版）。", file=sys.stderr)
            continue
        laws[name] = parsed
        print(f"{name}：{len(parsed)} 條")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "source": origin,
                "note": "條文原文（全國法規資料庫列印版）。僅供競賽之用，不進 git（CONSTITUTION §6）。",
                "laws": laws,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n→ {OUTPUT.relative_to(ROOT)}（{len(laws)} 部法規，來源 {origin}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
