"""卷證文字路由（spec 2026-09-07 §5.8）。不裝 OCR 套件。

  .txt                        → kind=txt
  .pdf 且中文比例 ≥ 0.60       → kind=pdf_text（pdftotext -layout）
  .pdf 且中文比例 < 0.60       → kind=pdf_visual（整份 PDF 以 document 區塊餵模型視覺讀）

找不到 pdftotext 時 PDF 一律走 pdf_visual，並在 `notes` 標明原因。哪一層、比例多少，
N1 的 narrative 會逐檔寫出來——**承辦人要看得到哪一份卷證是「模型看圖說話」讀來的**。
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable

CJK_RATIO_THRESHOLD = 0.60
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
        if suffix == ".txt":
            t = p.read_text(encoding="utf-8", errors="ignore")
            out.append(Document(p.name, "txt", t, cjk_ratio(t), p))
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
    return out
