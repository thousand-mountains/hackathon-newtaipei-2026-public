"""卷證文字路由（spec 2026-09-07 §5.8）。不裝 OCR 套件。

  .txt/.md/.csv/.json         → kind=txt
  .docx                       → kind=docx_text（stdlib zipfile 解 word/document.xml，零新依賴）
  .pdf 且中文比例 ≥ 0.60       → kind=pdf_text（pdftotext -layout）
  .pdf 且中文比例 < 0.60       → kind=pdf_visual（整份 PDF 以 document 區塊餵模型視覺讀）
  其餘任何格式                 → kind=unreadable（**收下但明說讀不到**，不送模型）

**2026-09-12 起不再用副檔名白名單擋上傳**（Ci 拍板：不限制 input 格式）。
但「不擋」不等於「讀得到」——舊版這個函式只認 .txt/.pdf，其他副檔名會被
**靜默跳過**：上傳回 201、執行成功、而那份卷證從頭到尾沒被讀過，`documents` 是空的。
那是最壞的一種失敗（形式具備、實質不具備），所以現在讀不到的一律產出
kind=unreadable 的 Document 並在 notes 說明原因，讓 N1 的 input_route 與畫面
都看得到「這份我沒讀到」。

找不到 pdftotext 時 PDF 一律走 pdf_visual，並在 `notes` 標明原因。哪一層、比例多少，
N1 的 narrative 會逐檔寫出來——**承辦人要看得到哪一份卷證是「模型看圖說話」讀來的**。
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from typing import Callable

CJK_RATIO_THRESHOLD = 0.60
#: 直接當純文字讀的副檔名。
TEXT_SUFFIXES = (".txt", ".md", ".csv", ".json", ".text", ".log")
#: 已知讀不到、但**要明說**的常見格式（訊息會針對格式給具體建議）。
_UNREADABLE_HINTS = {
    ".doc": "舊版 Word 二進位格式（.doc）無法解析，請另存為 .docx 或 .pdf 後重新上傳。",
    ".pages": "Pages 檔無法解析，請匯出為 .pdf 後重新上傳。",
    ".png": "影像檔目前不做 OCR，請改上傳含文字層的 PDF，或人工補欄位。",
    ".jpg": "影像檔目前不做 OCR，請改上傳含文字層的 PDF，或人工補欄位。",
    ".jpeg": "影像檔目前不做 OCR，請改上傳含文字層的 PDF，或人工補欄位。",
    ".tif": "影像檔目前不做 OCR，請改上傳含文字層的 PDF，或人工補欄位。",
    ".tiff": "影像檔目前不做 OCR，請改上傳含文字層的 PDF，或人工補欄位。",
    ".zip": "壓縮檔不會自動解開，請解壓後個別上傳。",
}
_CJK = re.compile(r"[一-鿿]")
PDF_VISUAL_MAX_BYTES = 4_500_000  # Bedrock Converse document 區塊單檔上限


@dataclass
class Document:
    n: str
    kind: str
    text: str
    cjk_ratio: float
    path: pathlib.Path | None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {
            "n": self.n,
            "kind": self.kind,
            "text": self.text,
            "cjk_ratio": self.cjk_ratio,
            "path": str(self.path) if self.path else None,
            "notes": self.notes,
        }
        if self.kind == "pdf_visual" and self.path:
            d["bytes"] = self.path.read_bytes()
        return d


def cjk_ratio(text: str) -> float:
    """中文字佔非空白字元的比例。空白與換頁字元不計入分母（掃描件常只抽到一堆 \\x0c）。"""
    letters = [c for c in text if not c.isspace() and c != "\x0c"]
    if not letters:
        return 0.0
    return round(sum(1 for c in letters if _CJK.match(c)) / len(letters), 3)


def _docx_text(p: pathlib.Path) -> str:
    """用標準庫解 .docx（它就是一個 zip，正文在 word/document.xml）。零新依賴。

    只取 `<w:t>` 的文字，`</w:p>` 當換行。不處理表格結構、頁首頁尾與追蹤修訂——
    抽出來的是純文字流，夠 N1 用，但**不要當成版面忠實還原**。
    """
    with zipfile.ZipFile(p) as z:
        if "word/document.xml" not in z.namelist():
            raise RuntimeError("這個 .docx 沒有 word/document.xml，可能不是有效的 Word 檔")
        xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, flags=re.S)
    text = "".join(parts)
    # 上一步只取 <w:t> 內容，換行標記已被吃掉——改用整份去標籤後的版本補回段落
    stripped = re.sub(r"<[^>]+>", "", xml)
    return text if len(text) >= len(stripped.strip()) else stripped


def _pdftotext(p: pathlib.Path) -> str:
    if shutil.which("pdftotext") is None:
        raise FileNotFoundError("pdftotext 不在 PATH（brew install poppler）")
    r = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"pdftotext 失敗：{r.stderr.strip()[:200]}")
    return r.stdout


def route_documents(
    case_dir: pathlib.Path, text_extractor: Callable[[pathlib.Path], str] | None = None
) -> list[Document]:
    """把一個案件目錄裡的卷證分流成 txt／pdf_text／pdf_visual。

    `text_extractor` 是測試接縫：注入之後不必依賴機器上有沒有 pdftotext。
    """
    extractor = text_extractor or _pdftotext
    out: list[Document] = []
    for p in sorted(case_dir.iterdir()):
        if p.name == "case.json" or p.name.startswith("."):
            continue
        suffix = p.suffix.lower()
        if suffix in TEXT_SUFFIXES:
            t = p.read_text(encoding="utf-8", errors="ignore")
            out.append(Document(p.name, "txt", t, cjk_ratio(t), p))
        elif suffix == ".docx":
            try:
                t = _docx_text(p)
            except (zipfile.BadZipFile, RuntimeError, OSError) as e:
                out.append(Document(
                    p.name, "unreadable", "", 0.0, p,
                    [f"這份 .docx 解不開（{e}），未送入模型。請另存為 .pdf 後重新上傳。"],
                ))
                continue
            notes = [] if t.strip() else ["這份 .docx 解出來是空的（可能內容都在圖片或文字方塊裡），未送入模型。"]
            out.append(Document(p.name, "docx_text" if t.strip() else "unreadable", t, cjk_ratio(t), p, notes))
        elif suffix == ".pdf":
            notes: list[str] = []
            try:
                t = extractor(p)
            except (FileNotFoundError, RuntimeError, OSError, subprocess.SubprocessError) as e:
                t, notes = "", [f"文字抽取不可用：{e}"]
            ratio = cjk_ratio(t)
            if ratio >= CJK_RATIO_THRESHOLD:
                out.append(Document(p.name, "pdf_text", t, ratio, p, notes))
            elif p.stat().st_size > PDF_VISUAL_MAX_BYTES:
                # 太大送不進 document 區塊，也沒有文字層——**如實說抽不到，不假裝讀過**
                notes.append(f"PDF 超過 {PDF_VISUAL_MAX_BYTES} bytes，無法視覺讀取；請人工補欄位")
                out.append(Document(p.name, "pdf_text", t, ratio, p, notes))
            else:
                notes.append(f"中文比例 {ratio} < {CJK_RATIO_THRESHOLD}，改以視覺讀取")
                out.append(Document(p.name, "pdf_visual", "", ratio, p, notes))
        else:
            # **收下但明說讀不到。** 舊版在這裡是 `continue`——那份卷證會從
            # `documents` 裡整個消失，而上傳回 201、執行成功，承辦人不會發現
            # 系統其實沒看過它。寧可產出一個講清楚的空文件。
            hint = _UNREADABLE_HINTS.get(
                suffix, f"目前無法從 {suffix or '無副檔名'} 抽取文字，未送入模型。"
            )
            out.append(Document(p.name, "unreadable", "", 0.0, p, [hint]))
    return out
