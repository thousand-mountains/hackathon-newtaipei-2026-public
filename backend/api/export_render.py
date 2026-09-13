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
from docx.enum.text import WD_LINE_SPACING
from docx.shared import Pt, RGBColor

from fpdf import FPDF

from backend.config import settings
from backend.orchestrator.artifact_sections import citation_lines, inline_marks

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MEDIA_TYPE = "application/pdf"

CITATION_HEADING = "引註對照"
NO_CITATION_NOTE = "本草稿沒有任何可回溯的引註。"
SCOPE_PREFIX = "※ "

#: 待填欄位的行首記號。**顏色之外一定要有文字**：這份文件會被印成黑白紙本，
#: 紅字影印出來跟黑字沒兩樣。顏色給螢幕上看，記號給紙上看，兩個都要。
PLACEHOLDER_MARK = "【待填】"
#: 待填欄位的字色（紅）。與範本決定書用紅字標重點是同一個慣例。
PLACEHOLDER_RGB = (0xC0, 0x00, 0x00)

#: 每一頁頁尾都印的那一句。**這是揭露最後的落腳處**：查核裝置與語料清單都移出
#: 匯出檔之後，「這是 AI 生成、未經承辦人確認」就只剩它。
#:
#: 放頁尾而不是文末，是因為 2026-09-13 實測：擺文末時它被換頁擠成第 3 頁的唯一一行，
#: 整頁空白——列印出來只會被當成印壞的紙抽掉，等於沒有揭露。頁尾每頁都有、不佔版面。
DRAFT_FOOTER_NOTE = "AI 輔助草稿・未經承辦人確認・不得逕行對外核發"

# 正文首行縮排。中文公文縮排兩個字，用**全形空白**而不是版面屬性：
# `.docx` 設得了 `first_line_indent`，`fpdf2` 的 `multi_cell` 設不了——
# 兩邊各用各的做法，同一份草稿印出來會差兩個字。統一用字元，兩邊必然一致。
BODY_INDENT = "　　"

# ── 匯出檔**不印**什麼（2026-09-13 Claire 拍板）──────────────────
#
# 下面三樣東西從匯出檔移除，只留在工作台畫面上：
#
#   行內引註標記 `[L5]`   段尾掛一串編號，公文裡沒有這種東西
#   文末「引註對照」清單   同上，那是系統的查核表不是決定書的一部分
#   「期間計算」附錄       規則引擎的逐步算式，正式決定書沒有這一段
#   檢索來源揭露           十幾行語料筆數，把標題與主文推到第一頁下半部
#
# **為什麼可以拿掉**：`.docx`／`.pdf` 是要送簽的公文，不是系統報告。查核痕跡
# （燈號、引註、算式、語料範圍）在工作台上一樣都沒有少，承辦人是在畫面上覆核完
# 才按匯出的。印在公文裡只會讓收文的人看不懂這份文件是什麼。
#
# **不能一起拿掉的是 `banner`**：一份抬頭寫著「訴願決定書」的檔案一旦離開系統，
# 「這是 AI 生成、未經承辦人確認」這件事就只剩紙上那一行撐著（CONSTITUTION §1）。
# 所以它保留，但縮成頁尾一行，不佔抬頭。
EXPORT_OMITS_CITATION_APPARATUS = True
#: 不印進匯出檔的 section role。
SKIP_ROLES = ("appendix",)

# **兩個不同的概念，不要合成一個。**
#
# `INDENT_ROLES`：首行縮排兩字的段落。公文只有主文／事實／理由三段縮排；
#   抬頭引導句、落款、教示條款一律頂格（照參考決定書的排法）。
# `ASIDE_ROLES`  ：不是決定書正文、要縮小另排的段落。目前只有期間計算附錄——
#   它是我們加的可驗算層，正式決定書沒有這一段。
#
# 2026-09-13 第一版把兩者併成一個 `BODY_ROLES`，結果引導句、落款與教示條款
# 全被當成附錄縮成灰色小字——那三段是決定書的一部分，只是不縮排而已。
INDENT_ROLES = ("main_text", "facts", "reasoning")
ASIDE_ROLES = ("appendix",)
#: 沒有 role 的 section（舊 run 的 payload、或手工組的 view）當成正文處理。
DEFAULT_ROLE = "reasoning"

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
    parts.extend(view.get("source_notes") or [])
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
#
# **不用 `add_heading()`。** `python-docx` 的 `Title`／`Heading 1` 內建樣式在 Word 裡是
# 藍色無襯線大字加底線——那是簡報樣式，不是公文。決定書的段名（「主　文」）是
# **置中、與內文同級的粗體**，抬頭是置中粗體大字。所以這裡自己組段落，
# 只用 `Normal` 樣式加明確的對齊與字級。
#
# 一樣不設字型名稱：承辦人機器上的 Word 會用系統預設中文字型，
# 指定一個對方沒有的字型只會換來一次字型替換。

_DOCX_BODY_PT = 12.0
_DOCX_TITLE_PT = 18.0
_DOCX_HEADING_PT = 13.0
_DOCX_NOTE_PT = 9.0
_DOCX_LINE_SPACING = 1.5


def _docx_para(
    document: Any,
    text: str,
    *,
    size_pt: float = _DOCX_BODY_PT,
    bold: bool = False,
    italic: bool = False,
    align: Any = None,
    space_before: float = 0.0,
    space_after: float = 4.0,
    grey: bool = False,
    rgb: tuple[int, int, int] | None = None,
) -> Any:
    """一個段落。**所有段落都走這支**——散在各處自己 `add_paragraph()` 再調屬性，
    漏調一個就是那一段行距與其他段不一樣，而那種錯要印出來才看得見。"""
    p = document.add_paragraph()
    fmt = p.paragraph_format
    fmt.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    fmt.line_spacing = _DOCX_LINE_SPACING
    fmt.space_before = Pt(space_before)
    fmt.space_after = Pt(space_after)
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.italic = italic
    if rgb is not None:
        run.font.color.rgb = RGBColor(*rgb)
    elif grey:
        run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    return p


def _docx_note(document: Any, text: str, size_pt: float = _DOCX_NOTE_PT, italic: bool = False) -> None:
    _docx_para(document, text, size_pt=size_pt, italic=italic, grey=True, space_after=2.0)


def render_docx(view: dict[str, Any]) -> bytes:
    """`sections[]` → OOXML bytes（REQ-EXPORT-001）。

    版面依 `sections[].role` 分三種（2026-09-13）：正文（主文／事實／理由）縮排兩字、
    附錄縮小另排、落款與教示條款不縮排。沒有 role 的當正文——舊的 run 存下來的
    payload 沒有這個鍵，不該因此排版壞掉。
    """
    document = Document()
    # `Normal` 是所有段落的底。在這裡設一次字級，比每個 run 各設一次可靠：
    # 承辦人在 Word 裡新增的段落也會沿用它，續編出來的字不會忽大忽小。
    normal = document.styles["Normal"].font
    normal.size = Pt(_DOCX_BODY_PT)

    # 每一頁的頁尾都印揭露（理由見 `DRAFT_FOOTER_NOTE`）。用 Word 真正的頁尾，
    # 不是文末一個段落——文末那種會被換頁擠成獨立一頁，而且承辦人續編時很容易刪掉。
    footer_p = document.sections[0].footer.paragraphs[0]
    footer_run = footer_p.add_run(DRAFT_FOOTER_NOTE)
    footer_run.font.size = Pt(_DOCX_NOTE_PT)
    footer_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    # **一律靠左**（2026-09-13 Claire 指定）：標題、案號、段名全部不置中、不靠右。
    _docx_para(
        document,
        view.get("title") or "訴願決定書草稿",
        size_pt=_DOCX_TITLE_PT,
        bold=True,
        space_after=10.0,
    )
    case_no = view.get("case_no")
    if case_no:
        # 案號靠右，排在標題底下（公文格式）。
        _docx_para(document, f"{settings.META_CASE_NO}：{case_no}",
                   size_pt=_DOCX_BODY_PT, space_after=2.0)
    for line in view.get("meta") or []:
        _docx_para(document, f"{BODY_INDENT}{line}", size_pt=_DOCX_BODY_PT, space_after=1.0)

    for sec in view.get("sections") or []:
        role = sec.get("role") or DEFAULT_ROLE
        if role in SKIP_ROLES:
            continue
        heading = sec.get("h") or ""
        if heading:
            # 正文段名置中（「主　文」），附錄那種長標題靠左——置中的長標題會
            # 斷在中間，看起來像排版壞了。
            body = role not in ASIDE_ROLES
            _docx_para(
                document,
                heading,
                size_pt=_DOCX_HEADING_PT if body else _DOCX_NOTE_PT + 1,
                bold=True,
                space_before=10.0,
                space_after=4.0,
                grey=not body,
            )
        for block in sec.get("blocks") or []:
            text = block.get("text") or ""
            indent = BODY_INDENT if role in INDENT_ROLES else ""
            aside = role in ASIDE_ROLES
            # 待填欄位（主文佔位、落款空格）**要看得出是空的**：與正文同樣式印出來，
            # 列印分發之後沒有人會發現那一格還沒填（2026-09-13 看實際 PDF 發現）。
            # 畫面上有紅燈撐著，紙上沒有，所以紙上要自己講。
            placeholder = bool(block.get("placeholder"))
            paragraph = _docx_para(
                document,
                f"{indent}{PLACEHOLDER_MARK if placeholder else ''}{text}",
                size_pt=_DOCX_NOTE_PT + 1 if aside else _DOCX_BODY_PT,
                grey=aside,
                bold=placeholder,
                rgb=PLACEHOLDER_RGB if placeholder else None,
            )
            del paragraph   # 行內引註標記不進匯出檔（見檔頭 EXPORT_OMITS_CITATION_APPARATUS）

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# ── .pdf ─────────────────────────────────────────────────────────
def _para(pdf: FPDF, height: float, text: str, align: str = "L") -> None:
    """一段文字，**寫完一定回到左邊界**，而且**逐字斷行**。

    兩件實測踩過的事：

    1. fpdf2 的 `multi_cell` 預設 `new_x=RIGHT`：寫完游標停在該段右端。
       下一個 `multi_cell(w=0, …)` 於是只剩幾 mm 可用，直接丟
       `FPDFException: Not enough horizontal space to render a single character`。
       2026-09-12 實測踩到（第二段 notice 就炸）。

    2. **`wrapmode="CHAR"` 不能拿掉。** 預設是 `WORD`——以空白為斷點，
       那是英文的斷行規則。中文段落裡唯一的空白是「第 77 條第 2 款」這種
       數字兩側的，於是 fpdf2 會在那裡斷，把整段排成

           按訴願法第 77 條第 2
           款規定：「訴願事件有左列各款情形之一者……

       半行空白掛在右邊，而「第 2」與「款」被拆開（2026-09-13 實測）。
       中文本來就是逐字斷行，`CHAR` 才是對的規則。

    所有段落一律走這支，不要在別處直接呼叫 `multi_cell`——漏一處就是那一段
    自己用另一套規則排版，而那種錯要印出來才看得見。
    """
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    for line in _wrap_cjk(pdf, text, usable):
        # 用 `cell` 而不是 `multi_cell`：行已經自己斷好了，再讓 fpdf2 斷一次，
        # 懸掛在邊界外的那個標點會被它當成超寬又折一行下去（只有一個標點的孤行）。
        pdf.cell(0, height, line, align=align, new_x="LMARGIN", new_y="NEXT")


#: 不得出現在行首的標點（避頭點）。中文公文裡逗號掉到行首非常刺眼，
#: 而 fpdf2 的 `wrapmode="CHAR"` 只管寬度、不管禁則。
_NO_LINE_START = "，。、；：？！）」』》】〉．·…‧％」’”]"
#: 不得出現在行尾的標點（避尾點）。
_NO_LINE_END = "（「『《【〈‘“["  # `[` 是行內引註標記 `[L1]` 的開頭，不能單獨掛在行尾
#: 不拆開的字元：數字、拉丁字母與它們之間的連接符號。
#: 「113」被拆成「11／3」兩行的話，日期就讀錯了。
_ATOMIC = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-/:_")


def _tokens(text: str) -> list[str]:
    """中文逐字、數字與拉丁字成串。斷行的最小單位。"""
    out: list[str] = []
    for ch in text:
        if ch in _ATOMIC and out and out[-1][-1] in _ATOMIC:
            out[-1] += ch
        else:
            out.append(ch)
    return out


def _wrap_cjk(pdf: FPDF, text: str, width: float) -> list[str]:
    """中文斷行 ＋ 避頭尾。回傳每一行的字串。

    為什麼不用 fpdf2 自己的斷行：

    - 預設 `wrapmode="WORD"` 以空白為斷點，而中文段落裡唯一的空白是
      「第 77 條第 2 款」這種數字兩側的，結果整段排成半行（2026-09-13 實測）。
    - `wrapmode="CHAR"` 寬度對了，但**不管禁則**：逗號、句號會掉到行首。

    這裡的作法是逐 token 填滿一行，然後把落在行首的標點**懸掛**回上一行
    （標點懸掛是中文排版的標準作法，不是把版面撐破）。
    """
    lines: list[str] = []
    cur = ""
    for tok in _tokens(text):
        if not cur:
            cur = tok
            continue
        if pdf.get_string_width(cur + tok) <= width:
            cur += tok
            continue
        # 這一行滿了。避頭點：標點不另起一行，掛在上一行尾巴。
        if tok and tok[0] in _NO_LINE_START:
            lines.append(cur + tok)
            cur = ""
            continue
        # 避尾點：上一行以開引號結尾的話，把它帶到下一行去。
        if cur and cur[-1] in _NO_LINE_END:
            lines.append(cur[:-1])
            cur = cur[-1] + tok
            continue
        lines.append(cur)
        cur = tok
    if cur:
        lines.append(cur)
    return lines or [""]


class _DraftPDF(FPDF):
    """頁尾放頁碼與『草稿』字樣。

    每一頁都標「草稿」是刻意的：PDF 最可能被印出來分發，而印出來之後
    「這是 AI 輔助產生、未經承辦人確認」這件事只剩紙上這一行撐著。
    """

    footer_note = ""

    def footer(self) -> None:  # noqa: D102 — fpdf2 的 hook 名字，不是我取的
        self.set_y(-14)
        self.set_font(PDF_FONT_KEY, size=_PDF_SMALL_PT)
        self.set_text_color(0x66, 0x66, 0x66)
        self.cell(0, 5, DRAFT_FOOTER_NOTE, new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 5, f"{self.footer_note}　第 {self.page_no()} 頁")
        self.set_text_color(0, 0, 0)


def render_pdf(view: dict[str, Any]) -> tuple[bytes, list[str]]:
    """`sections[]` → PDF bytes ＋ 缺字清單（REQ-EXPORT-002、003）。

    回傳兩個值而不是一個，是為了讓端點能把缺字放進 `X-Export-Warning`——
    把警告吞在函式裡就等於沒有警告。

    版面與 `.docx` 那邊一一對應（2026-09-13）：抬頭置中、段名置中、正文縮排兩字、
    附錄與落款另排。兩邊各排各的話，同一份草稿存成兩種格式會長得不一樣，
    而承辦人通常兩種都會下載。
    """
    font_path = resolve_cjk_font()  # 找不到就 raise，絕不降級成拉丁字型

    pdf = _DraftPDF(orientation="P", unit="mm", format="A4")
    pdf.footer_note = view.get("title") or "訴願決定書草稿"
    pdf.set_auto_page_break(auto=True, margin=_PDF_MARGIN_MM)
    pdf.set_margins(_PDF_MARGIN_MM, _PDF_MARGIN_MM, _PDF_MARGIN_MM)
    pdf.add_font(PDF_FONT_KEY, "", str(font_path))
    pdf.add_page()

    pdf.set_font(PDF_FONT_KEY, size=_PDF_TITLE_PT)
    _para(pdf, 10, view.get("title") or "訴願決定書草稿")
    pdf.ln(3)

    pdf.set_font(PDF_FONT_KEY, size=_PDF_BODY_PT)
    case_no = view.get("case_no")
    if case_no:
        _para(pdf, 6.5, f"{settings.META_CASE_NO}：{case_no}")
    for line in view.get("meta") or []:
        _para(pdf, 6.5, f"{BODY_INDENT}{line}")
    pdf.ln(3)

    for sec in view.get("sections") or []:
        role = sec.get("role") or DEFAULT_ROLE
        if role in SKIP_ROLES:
            continue
        body = role not in ASIDE_ROLES
        heading = sec.get("h") or ""
        if heading:
            # 正文段名置中且大一級（「主　文」）；附錄那種長標題靠左縮小，
            # 置中的長標題會斷在中間，看起來像排版壞了。
            pdf.ln(2)
            pdf.set_font(PDF_FONT_KEY, size=_PDF_HEADING_PT if body else _PDF_SMALL_PT + 1)
            _para(pdf, 9, heading)
            pdf.ln(1)
        pdf.set_font(PDF_FONT_KEY, size=_PDF_BODY_PT if body else _PDF_SMALL_PT + 1)
        for block in sec.get("blocks") or []:
            marks = ""   # 行內引註標記不進匯出檔（見檔頭 EXPORT_OMITS_CITATION_APPARATUS）
            text = block.get("text") or ""
            placeholder = bool(block.get("placeholder"))
            if placeholder:
                text = f"{PLACEHOLDER_MARK}{text}"   # 理由同 `.docx` 那邊
                pdf.set_text_color(*PLACEHOLDER_RGB)
            indent = BODY_INDENT if role in INDENT_ROLES else ""
            # `fpdf2` 的 `multi_cell` 沒有首行縮排屬性，所以縮排靠全形空白——
            # 跟 `.docx` 那邊用同一個常數，兩種格式印出來才對得上。
            # **一律靠左，不要 justify。** `fpdf2` 的 `align="J"` 在中文段落裡只找得到
            # 數字與拉丁字旁邊的斷點，於是把那幾個空白撐到整行寬——
            # 「按訴願法第　　　77　　　條第　　　2」就是這樣來的（2026-09-13 實測）。
            # 中文本來就不需要兩端對齊，靠左是正確的排法，不是退讓。
            _para(pdf, 8.0 if body else 6.0, f"{indent}{text} {marks}".rstrip())
            if placeholder:
                pdf.set_text_color(0, 0, 0)   # 用完立刻收回，不要讓紅色漏到下一段
        pdf.ln(2)

    missing = missing_glyphs(pdf, _all_text(view))
    return bytes(pdf.output()), missing
