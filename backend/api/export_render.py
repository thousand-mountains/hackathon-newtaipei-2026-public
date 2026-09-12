"""把契約 §4.4 的產出視圖組成 `.docx` / `.pdf` 位元組（US-C3、REQ-EXPORT-001～003）。

**這個檔放在 `backend/api/` 是刻意的**：`api/` 是 `backend/tests/run_all.py`
`DEPENDENCY_EXEMPT_DIRS` 的具名豁免目錄，`python-docx` 與 `fpdf2` 只能在這裡 import。
取材那一層（`doc[]` → `sections[]`）在 `backend/orchestrator/artifact_sections.py`，
零第三方依賴、受靜態掃描管轄。兩層分開是因為它們的失敗方式完全不同：
取材壞掉是「引註掉光」，組檔壞掉是「豆腐字」。

---

## 這個檔最重要的一件事：豆腐字不會報錯

沒有 CJK 字型時，PDF 產出的是整片 `□□□`，而 **fpdf2 不丟例外、HTTP 照樣 200**。
只看「回 200、檔案有 bytes」的驗收會完全通過，交到承辦人手上才發現是一份廢紙。

所以：

- `resolve_cjk_font()` 找不到任何 CJK 字型 → raise `CJKFontMissing`（端點翻成 **503**），
  **不降級成拉丁字型**。降級的代價落在拿到檔案的人身上，失敗的代價落在這裡，
  這個不對稱要靠硬失敗擺正。
- `missing_glyphs()` 再查一次實際字元覆蓋率：字型在、但某些字不在字型裡時，
  那幾個字仍然會是豆腐。這種零星缺字**不阻擋匯出**（為一個罕用字擋掉整份文件不划算），
  但會由端點放進 `X-Export-Warning` 標頭具名列出。

## 為什麼 `.docx` 不走 HTML 轉檔

`python-docx` 直接組段落、只用內建樣式（`Title` / `Heading 1` / `Normal`）。
HTML→docx 轉出來的是一堆行內樣式與巢狀表格，承辦人在 Word 裡改一個字會整段跑版——
而「能拿回去改後送簽」正是 `.docx` 存在的唯一理由（契約 §1.5 ⑧）。
"""
from __future__ import annotations

import io
import os
import pathlib
from typing import Any

from docx import Document
from docx.shared import Pt
from fpdf import FPDF

from backend.orchestrator.artifact_sections import citation_lines, inline_marks

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MEDIA_TYPE = "application/pdf"

CITATION_HEADING = "引註對照"
NO_CITATION_NOTE = "本草稿沒有任何可回溯的引註。"
SCOPE_PREFIX = "※ "

# 字型檔名寫死成常數：它同時出現在 README、Dockerfile 說明與測試斷言裡，
# 散在字串字面量裡改一處漏三處。
BUNDLED_FONT_NAME = "NotoSansTC-Regular.otf"
FONT_ENV_VAR = "APPEAL_EXPORT_CJK_FONT"
PDF_FONT_KEY = "cjk"

# 隨庫字型：`backend/assets/fonts/`。`Dockerfile` 的 `COPY backend/` 已含這個目錄。
_BUNDLED_FONT = pathlib.Path(__file__).resolve().parent.parent / "assets" / "fonts" / BUNDLED_FONT_NAME

# 系統字型後備。**只列 `.ttf` / `.otf` 單檔**：apt 的 `fonts-noto-cjk` 裝出來是
# `.ttc` 集合檔，fpdf2 要另外指定 `collection_font_number` 才讀得到，
# 多一個只會在雲上炸的變數。這幾條是為了「忘了帶隨庫字型時本機仍跑得動」，
# 部署一律以隨庫那份為準。
_SYSTEM_FONT_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/opentype/noto/NotoSansTC-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansTC-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
)

# 版面（mm / pt）
_PDF_MARGIN_MM = 20.0
_PDF_TITLE_PT = 16.0
_PDF_HEADING_PT = 13.0
_PDF_BODY_PT = 11.5
_PDF_SMALL_PT = 9.0


class CJKFontMissing(RuntimeError):
    """找不到任何可用的 CJK 字型。端點把它翻成 503。

    訊息一定要具名列出**找過哪些路徑**：只說「找不到字型」的話，
    在容器裡除錯的人得先反推程式去哪裡找過。
    """

    def __init__(self, searched: list[str]) -> None:
        self.searched = searched
        super().__init__(
            "找不到可用的 CJK 字型，PDF 匯出中止（不產出豆腐字檔案）。"
            f"已依序查找：{'、'.join(searched) or '（無候選路徑）'}。"
            f"可用環境變數 {FONT_ENV_VAR} 指定字型檔，或確認 "
            f"backend/assets/fonts/{BUNDLED_FONT_NAME} 有隨映像檔帶上去。"
        )


def font_search_paths() -> list[pathlib.Path]:
    """依序：環境變數 → 隨庫字型 → 系統後備。抽成函式是為了讓測試能
    monkeypatch 成空清單，實際驗「沒有字型時真的會 503」。"""
    paths: list[pathlib.Path] = []
    env = os.environ.get(FONT_ENV_VAR, "").strip()
    if env:
        paths.append(pathlib.Path(env))
    paths.append(_BUNDLED_FONT)
    paths.extend(pathlib.Path(p) for p in _SYSTEM_FONT_CANDIDATES)
    return paths


def resolve_cjk_font() -> pathlib.Path:
    """回傳第一個存在的 CJK 字型檔。全部落空就 raise，**不回 None、不降級**。"""
    searched: list[str] = []
    for p in font_search_paths():
        searched.append(str(p))
        if p.is_file():
            return p
    raise CJKFontMissing(searched)


def _all_text(view: dict[str, Any]) -> str:
    parts = [view.get("title") or ""]
    parts.extend(view.get("meta") or [])
    parts.extend(view.get("notices") or [])
    for sec in view.get("sections") or []:
        parts.append(sec.get("h") or "")
        for b in sec.get("blocks") or []:
            parts.append(b.get("text") or "")
            parts.append(inline_marks(b))
    parts.append(CITATION_HEADING)
    for rid, label in citation_lines(view):
        parts.append(f"{rid} {label}")
    parts.append(view.get("dataset_scope") or "")
    return "\n".join(parts)


def missing_glyphs(pdf: FPDF, text: str) -> list[str]:
    """這份字型**實際缺哪些字**。

    `resolve_cjk_font()` 只保證「有一個 CJK 字型」，不保證它涵蓋這份文件的每個字：
    罕用字、異體字、全形符號都可能落在字型外，而那幾個字一樣會是豆腐、一樣不報錯。
    回傳排序後的字元清單供端點具名揭露；**不在這裡阻擋匯出**——
    為一個罕用字擋掉整份文件，代價比一個標註過的缺字大。
    """
    font = pdf.fonts.get(PDF_FONT_KEY)
    cmap = getattr(font, "cmap", None)
    if not cmap:
        return []
    missing = {ch for ch in text if ch not in "\r\n\t " and ord(ch) not in cmap}
    return sorted(missing)


# ── .docx ────────────────────────────────────────────────────────
def _docx_note(document: Any, text: str, size_pt: float = 9.0, italic: bool = False) -> None:
    p = document.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(size_pt)
    run.italic = italic


def render_docx(view: dict[str, Any]) -> bytes:
    """`sections[]` → OOXML bytes（REQ-EXPORT-001）。

    只用 `python-docx` 的內建樣式。不設字型名稱：承辦人機器上的 Word 會自己
    用系統預設中文字型，指定一個對方沒有的字型只會換來一次字型替換。
    """
    document = Document()

    document.add_heading(view.get("title") or "訴願決定書草稿", level=0)
    for line in view.get("meta") or []:
        _docx_note(document, line, size_pt=10.0)

    # 出處揭露擺在抬頭下方、正文之前——擺在文末的話，列印前兩頁的人看不到。
    for notice in view.get("notices") or []:
        _docx_note(document, notice, italic=True)

    for sec in view.get("sections") or []:
        heading = sec.get("h") or ""
        if heading:
            document.add_heading(heading, level=1)
        for block in sec.get("blocks") or []:
            paragraph = document.add_paragraph()
            paragraph.add_run(block.get("text") or "")
            marks = inline_marks(block)
            if marks:
                run = paragraph.add_run(f" {marks}")
                run.font.size = Pt(9.0)

    document.add_heading(CITATION_HEADING, level=1)
    lines = citation_lines(view)
    if lines:
        for rid, label in lines:
            p = document.add_paragraph(style="List Bullet")
            p.add_run(f"{rid}　{label}")
    else:
        _docx_note(document, NO_CITATION_NOTE, size_pt=10.0)

    scope = view.get("dataset_scope")
    if scope:
        _docx_note(document, f"{SCOPE_PREFIX}{scope}")

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# ── .pdf ─────────────────────────────────────────────────────────
def _para(pdf: FPDF, height: float, text: str, align: str = "L") -> None:
    """一段文字，**寫完一定回到左邊界**。

    fpdf2 的 `multi_cell` 預設 `new_x=RIGHT`：寫完游標停在該段右端。
    下一個 `multi_cell(w=0, …)` 於是只剩幾 mm 可用，直接丟
    `FPDFException: Not enough horizontal space to render a single character`。
    2026-09-12 實測踩到（第二段 notice 就炸）。所有段落一律走這支，
    不要在別處直接呼叫 `multi_cell`——漏一處就是整份匯出 500。
    """
    pdf.multi_cell(0, height, text, align=align, new_x="LMARGIN", new_y="NEXT")


class _DraftPDF(FPDF):
    """頁尾放頁碼與『草稿』字樣。

    每一頁都標「草稿」是刻意的：PDF 最可能被印出來分發，而印出來之後
    「這是 AI 輔助產生、未經承辦人確認」這件事只剩紙上這一行撐著。
    """

    footer_note = ""

    def footer(self) -> None:  # noqa: D102 — fpdf2 的 hook 名字，不是我取的
        self.set_y(-14)
        self.set_font(PDF_FONT_KEY, size=_PDF_SMALL_PT)
        self.cell(0, 6, f"{self.footer_note}　第 {self.page_no()} 頁", align="C")


def render_pdf(view: dict[str, Any]) -> tuple[bytes, list[str]]:
    """`sections[]` → PDF bytes ＋ 缺字清單（REQ-EXPORT-002、003）。

    回傳兩個值而不是一個，是為了讓端點能把缺字放進 `X-Export-Warning`——
    把警告吞在函式裡就等於沒有警告。
    """
    font_path = resolve_cjk_font()  # 找不到就 raise，絕不降級成拉丁字型

    pdf = _DraftPDF(orientation="P", unit="mm", format="A4")
    pdf.footer_note = view.get("title") or "訴願決定書草稿"
    pdf.set_auto_page_break(auto=True, margin=_PDF_MARGIN_MM)
    pdf.set_margins(_PDF_MARGIN_MM, _PDF_MARGIN_MM, _PDF_MARGIN_MM)
    pdf.add_font(PDF_FONT_KEY, "", str(font_path))
    pdf.add_page()

    pdf.set_font(PDF_FONT_KEY, size=_PDF_TITLE_PT)
    _para(pdf, 10, view.get("title") or "訴願決定書草稿", align="C")
    pdf.ln(2)

    pdf.set_font(PDF_FONT_KEY, size=_PDF_SMALL_PT + 1)
    for line in view.get("meta") or []:
        _para(pdf, 6, line)
    for notice in view.get("notices") or []:
        _para(pdf, 5, notice)
    pdf.ln(3)

    for sec in view.get("sections") or []:
        heading = sec.get("h") or ""
        if heading:
            pdf.set_font(PDF_FONT_KEY, size=_PDF_HEADING_PT)
            _para(pdf, 9, heading)
        pdf.set_font(PDF_FONT_KEY, size=_PDF_BODY_PT)
        for block in sec.get("blocks") or []:
            marks = inline_marks(block)
            text = block.get("text") or ""
            _para(pdf, 7.5, f"{text} {marks}".rstrip())
            pdf.ln(1)
        pdf.ln(2)

    pdf.set_font(PDF_FONT_KEY, size=_PDF_HEADING_PT)
    _para(pdf, 9, CITATION_HEADING)
    pdf.set_font(PDF_FONT_KEY, size=_PDF_SMALL_PT + 1)
    lines = citation_lines(view)
    if lines:
        for rid, label in lines:
            _para(pdf, 6, f"{rid}　{label}")
    else:
        _para(pdf, 6, NO_CITATION_NOTE)

    scope = view.get("dataset_scope")
    if scope:
        pdf.ln(2)
        pdf.set_font(PDF_FONT_KEY, size=_PDF_SMALL_PT)
        _para(pdf, 5, f"{SCOPE_PREFIX}{scope}")

    missing = missing_glyphs(pdf, _all_text(view))
    return bytes(pdf.output()), missing
