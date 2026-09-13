"""草稿匯出（US-C3 / REQ-EXPORT-001～004）單元測試。

契約：`docs/handoff/2026-09-12-frontend-contract-v2.md` §1.5 #23、§4.4
規格：`.prospec/changes/draft-and-export/delta-spec.md`

## 這支測試最在意的那一條

**沒有 CJK 字型時，PDF 產出的是整片豆腐字（□□□），而且不丟例外、HTTP 照樣 200。**
所以 `test_pdf_without_font_is_hard_failure` 與 `test_endpoint_503_when_font_missing`
是全檔最重要的兩條：它們驗的不是「有字型時會成功」，而是**沒字型時會失敗**。
只驗前者的話，把字型從映像檔拿掉，這支測試會全綠，而承辦人會拿到一份廢紙。

## 為什麼組檔層是「模組頂層 try/except ImportError」

`backend/api/export_render.py` 需要 `python-docx` 與 `fpdf2`，`backend/api/export.py`
需要 `fastapi`，而 `backend/tests/run_all.py` 必須能在**一台只有 python3 的機器**上
跑起來（`backend/requirements.txt` 抬頭）。兩條紅線同時管著這件事：

- **零第三方依賴**（`run_all.py:319`）：`backend/tests/` 不得 import `docx`／`fpdf`／
  `fastapi`。所以這裡只 import `backend.api.*`——第三方套件留在
  `backend/api/`（具名豁免目錄）那一側，測試透過 `export_render.Document`、
  `export_api.HTTPException` 取用，不自己 import。
- **import 一律在模組頂層**（`run_all.py:499`，spec D8）：套件缺席要用
  **模組頂層的 try/except ImportError 守衛**表達，缺什麼、缺了會怎樣寫在檔案開頭。

套件缺席時組檔層的測試 `TestSkipped`——**略過不計入 passed**（`harness.py:19`），
跑完單獨列出。**略過不是通過**，這點靠 harness 本身擋著；取材層那批零依賴，一律真的跑。
"""
from __future__ import annotations

import io
import json
import ast
import pathlib
import shutil
import subprocess
import tempfile
import urllib.parse
import zipfile

from backend.orchestrator import chat_bridge
from backend.config import settings
from backend.orchestrator.artifact_sections import (
    UNRESOLVED_SUFFIX,
    build_sections,
    citation_lines,
    dataset_scope,
    has_body,
    inline_marks,
)
from backend.api.export_render import DRAFT_FOOTER_NOTE
from backend.orchestrator.narrative import article_quotes
from backend.orchestrator.graph import build_payload, run_case
from backend.tests.harness import TestFailure, TestSkipped

# 第三方套件守衛（spec D8）。缺席時這兩個是 None，組檔／傳輸層的測試會 TestSkipped。
# **不要改成函式內 import**：那會同時違反 D8 與「相依看不出來」這條理由本身。
try:
    from backend.api import export as export_api
    from backend.api import export_render
except ImportError as _e:  # pragma: no cover — 只有裸機（未裝 requirements）會走到
    export_api = None
    export_render = None
    IMPORT_ERROR = str(_e)
else:
    IMPORT_ERROR = ""

# `app` 另外守衛：它多依賴一個 `python-multipart`，跟匯出無關。
# 併在上面那個 try 裡的話，少裝 multipart 會讓整批匯出測試一起略過，
# 而那批其實跑得動——略過的理由要精確，否則「略過」會變成看不見的洞。
try:
    from backend.api.app import app as fastapi_app
except ImportError as _e2:  # pragma: no cover
    fastapi_app = None
    APP_IMPORT_ERROR = str(_e2)
else:
    APP_IMPORT_ERROR = ""

# JSON 檢視端（契約 §4.4 #21）。跟匯出共用同一支 sections 轉換，
# `test_json_view_and_export_share_one_sections_implementation` 靠它比對。
try:
    from backend.api import dossier as dossier_api
except ImportError as _e3:  # pragma: no cover
    dossier_api = None
    DOSSIER_IMPORT_ERROR = str(_e3)
else:
    DOSSIER_IMPORT_ERROR = ""

ROOT = pathlib.Path(__file__).resolve().parents[2]

ORDINARY = "synthetic-ordinary-01"


def _fail(msg: str) -> None:
    raise TestFailure(msg)


def _payload(case_id: str = ORDINARY, persist: bool = False) -> dict:
    return build_payload(run_case(case_id, mode="fixture", persist=persist))


def _view(case_id: str = ORDINARY) -> dict:
    return build_sections(_payload(case_id), artifact_id="art-test")


def _render():
    """組檔層。套件不在就略過（不是通過）——理由見本檔抬頭。"""
    if export_render is None:  # pragma: no cover
        raise TestSkipped(
            f"組檔層需要 fastapi／python-docx／fpdf2（{IMPORT_ERROR}）。"
            f"安裝：pip install -r backend/requirements.txt"
        )
    return export_render


def _endpoint():
    """傳輸層。套件不在就略過。"""
    if export_api is None:  # pragma: no cover
        raise TestSkipped(f"傳輸層需要 fastapi（{IMPORT_ERROR}）。")
    return export_api


def _http_error():
    """`HTTPException` 從 `backend.api.export` 取，不在測試裡 import fastapi
    （那會踩到「測試路徑零第三方依賴」）。"""
    return _endpoint().HTTPException


# ── 取材層：doc[] → sections[]（REQ-EXPORT-003）────────────────────
def test_sections_have_headings_and_body() -> None:
    """`ty=="h"` 開 section、句子在 `ss[].t`。

    **正文在 `ss` 不在 `text`**：`ty=="p"` 的 `text` 實測一律是空字串。
    只讀 `text` 會匯出一份完全空白卻不報錯的檔案，這條就是釘住它的。
    """
    v = _view()
    headings = [s["h"] for s in v["sections"]]
    # 段名走 `settings`（`主　文` 是全形空白，不是「決定主文」）。寫死字面量的話，
    # 改一次段名就要記得改三個 renderer 加這裡，而漏掉的那個不會有人發現。
    for expected in (settings.SECTION_MAIN_TEXT, settings.SECTION_FACTS, settings.SECTION_REASONING):
        if expected not in headings:
            _fail(f"section 標題少了 {expected!r}，實得 {headings}")
    body = [b["text"] for s in v["sections"] for b in s["blocks"]]
    if not body:
        _fail("sections 裡一句正文都沒有——句子在 block['ss'][*]['t']，不是 block['text']")
    if any(not t.strip() for t in body):
        _fail("有空白 block 混進來")


def test_the_system_does_not_quote_an_article_the_model_already_quoted() -> None:
    """同一條法規的原文不得在理由段出現兩次（2026-09-13 迴歸）。

    條文引述句原本無條件插在理由段最前面，而模型本來就會自己寫
    「按『……』○○法第 N 條定有明文」。真實卷證實測時三條法規全部重複
    （廢清法 §27、§50、行程法 §9），讀起來像系統把同一段話講了兩遍。
    """
    body = "在指定清除地區內嚴禁有下列行為：一、隨地吐痰、檳榔汁、檳榔渣，拋棄紙屑、煙蒂。"
    laws = [{"id": "L1", "law": "廢棄物清理法", "article": "27", "q": body}]

    fresh = article_quotes([{"t": "卷查本案訴願人於現場丟棄煙蒂。", "basis": "廢棄物清理法第27條"}],
                           laws, {})
    if len(fresh) != 1:
        _fail(f"模型沒抄原文時，系統該補一句條文引述，實得 {len(fresh)} 句")

    echoed = article_quotes(
        [{"t": f"按「{body}」廢棄物清理法第27條第1款定有明文。", "basis": "廢棄物清理法第27條"}],
        laws, {})
    if echoed:
        _fail(f"模型已經抄過原文，系統不得再貼一次：{[q['text'][:30] for q in echoed]}")


def test_placeholder_blocks_are_marked_so_paper_shows_them_too() -> None:
    """待填欄位在**匯出檔**裡要看得出來。

    畫面上有紅燈撐著，紙上沒有：「（結論段由承辦人判斷後填寫）」與正文同字級
    印出來，列印分發之後沒有人會發現那一格還沒填。
    """
    v = _view()
    placeholders = [b for sec in v["sections"] for b in sec["blocks"] if b.get("placeholder")]
    if not placeholders:
        _fail("測資裡沒有任何佔位句，這條測試的前提不成立（落款兩行本來就是佔位）")
    render = _render()
    text = "\n".join(p.text for p in render.Document(io.BytesIO(render.render_docx(v))).paragraphs)
    for b in placeholders:
        if f"{render.PLACEHOLDER_MARK}{b['text']}" not in text:
            _fail(f"佔位句在 .docx 裡沒有標記：{b['text'][:30]}")


def test_title_and_meta_are_not_sections() -> None:
    """`ty=="title"` / `ty=="meta"` 是文件抬頭，不是 section。
    當成 section 會在匯出檔裡長出兩個空標題。"""
    v = _view()
    if not v["title"]:
        _fail("title 沒有被取出來")
    if not v["meta"]:
        _fail("meta 抬頭行沒有被取出來（案號／案由／訴願人會整個消失）")
    for s in v["sections"]:
        if s["h"] == v["title"]:
            _fail("title 被當成 section 了")
        if s["h"] in v["meta"]:
            _fail("meta 被當成 section 了")


def test_cite_count_is_counted_not_hardcoded() -> None:
    """REQ-EXPORT-003 AC4：`cite_count` 是現數的。

    設計稿寫死「引註 14 處」。這條用**兩個案子的實際數字不相等**釘住它——
    只斷言「等於某個數」的話，換一個寫死的實作照樣會過。
    """
    a = _view(ORDINARY)
    b = _view("synthetic-blocked-01")
    manual = sum(len(bl["cites"]) for s in a["sections"] for bl in s["blocks"])
    if a["cite_count"] != manual:
        _fail(f"cite_count {a['cite_count']} 與逐一數出來的 {manual} 不一致")
    if a["cite_count"] == b["cite_count"]:
        _fail(
            f"兩個案子的 cite_count 都是 {a['cite_count']}——寫死的常數也會通過這個形狀，"
            f"請確認它真的來自 payload"
        )
    if a["cite_count"] == 14 and b["cite_count"] == 14:
        _fail("cite_count 看起來還是設計稿寫死的 14")


def test_cites_carry_resolvable_labels() -> None:
    """`refs[]` 的 `L3` 要對得回 `laws[].t`。對不回來只給編號的話，
    承辦人看得到 `L3`、查不到它是哪一條（CONSTITUTION §2）。"""
    v = _view()
    labels = {c["id"]: c["label"] for s in v["sections"] for b in s["blocks"] for c in b["cites"]}
    if not labels:
        _fail("整份草稿一個引註都沒有，取材層應該掉了 refs")
    if v["unresolved"]:
        _fail(f"有 ref 對不回 label：{v['unresolved']}")
    if not any("訴願法" in lb for lb in labels.values()):
        _fail(f"label 看起來不是法條原文：{labels}")


def test_unresolved_cite_is_shown_not_dropped() -> None:
    """REQ-EXPORT-003 AC3：label 查不到時照實印 id ＋「（未解析）」。

    靜默省略的話，匯出檔裡那一句看起來就像「本來就沒有引用」——
    把一個已知的缺陷偽裝成一個乾淨的事實。
    """
    p = _payload()
    # 把 laws 清掉但保留 doc[].ss[].refs：模擬 label 對不回來。
    p["laws"] = []
    v = build_sections(p)
    ids = [c["id"] for s in v["sections"] for b in s["blocks"] for c in b["cites"]]
    if not any(i.startswith("L") for i in ids):
        _fail("laws 清空後，L* 引用被靜默丟掉了")
    labels = [c["label"] for s in v["sections"] for b in s["blocks"] for c in b["cites"]]
    if not any(lb.endswith(UNRESOLVED_SUFFIX) for lb in labels):
        _fail(f"未解析的引用沒有被標出來：{labels}")
    if not v["unresolved"]:
        _fail("unresolved 清單是空的，前端就無從判斷『每個 cite 是否可回溯』")


def test_citation_lines_dedupe_in_first_seen_order() -> None:
    v = _view()
    lines = citation_lines(v)
    ids = [i for i, _ in lines]
    if len(ids) != len(set(ids)):
        _fail(f"引註對照有重複：{ids}")
    flat = [c["id"] for s in v["sections"] for b in s["blocks"] for c in b["cites"]]
    first_seen = list(dict.fromkeys(flat))
    if ids != first_seen:
        _fail(f"引註對照順序不是首次出現順序：{ids} vs {first_seen}")


def test_inline_marks_format() -> None:
    if inline_marks({"cites": [{"id": "L3", "label": "x"}, {"id": "I1", "label": "y"}]}) != "[L3] [I1]":
        _fail("行內標註格式不是 `[L3] [I1]`")
    if inline_marks({"cites": []}) != "":
        _fail("沒有引用時不該產生標註")


def test_provenance_notice_rides_along() -> None:
    """匯出檔一定要帶著出處揭露。

    一份寫著「訴願決定書」的 `.docx` 離開系統之後，畫面上那條「合成測資／
    fixture 重播」橫幅就不在了——分層誠實只在系統裡成立等於沒有成立
    （CONSTITUTION §1、§3）。
    """
    v = _view()
    if not v["notices"]:
        _fail("notices 是空的，匯出檔會沒有任何出處揭露")
    joined = "\n".join(v["notices"])
    if "合成測資" not in joined:
        _fail(f"合成測資的揭露不見了：{v['notices']}")
    if not dataset_scope(_payload()):
        _fail("dataset_scope 沒帶出來，引註對照會看起來像每條都經過完整查核")


def test_has_body_false_when_draft_not_generated() -> None:
    """REQ-EXPORT-004 AC4：還沒生成草稿 ≠ 草稿是空的。
    分不開的話會匯出一份只有抬頭的檔案，被讀成「本案無話可說」。"""
    p = _payload()
    p["doc"] = [{"ty": "h", "text": "事實", "ind": 0, "ss": []}]
    if has_body(build_sections(p)):
        _fail("沒有任何句子時 has_body 仍為 True")
    if not has_body(_view()):
        _fail("正常草稿被判成沒有內容")


# ── 組檔層：.docx（REQ-EXPORT-001）─────────────────────────────────
def test_docx_is_real_ooxml() -> None:
    """REQ-EXPORT-001 AC2：是 zip 且含 `word/document.xml`。
    「把 PDF 改副檔名」在這一條就會紅。"""
    blob = _render().render_docx(_view())
    if not blob.startswith(b"PK"):
        _fail("產出的不是 zip（OOXML 一定是 zip）")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        if "word/document.xml" not in z.namelist():
            _fail(f"zip 裡沒有 word/document.xml：{z.namelist()[:10]}")


def test_docx_is_editable() -> None:
    """REQ-EXPORT-001 AC3：讀得回來、加得了段落、存得回去＝可續編。

    本機沒有 Word／LibreOffice，這是**替代驗法**：用 `python-docx` 讀回結構。
    它驗得到「檔案是合法 OOXML 且段落樹可操作」，**驗不到**「Word 開起來版面好不好看」。
    """
    render = _render()
    doc = render.Document(io.BytesIO(render.render_docx(_view())))
    if len(doc.paragraphs) < 5:
        _fail(f"段落太少（{len(doc.paragraphs)}），草稿內容大概沒進去")
    doc.add_paragraph("承辦人補充：")
    out = io.BytesIO()
    doc.save(out)  # 存不回去就不是可續編
    again = render.Document(io.BytesIO(out.getvalue()))
    if not any("承辦人補充：" in p.text for p in again.paragraphs):
        _fail("續編後存檔，改動不見了")


def test_docx_omits_the_citation_apparatus_but_keeps_the_disclosure() -> None:
    """匯出檔**不印**行內 `[L3]` 與文末「引註對照」，但**一定要印**出處揭露。

    2026-09-13 Claire 拍板：`.docx`／`.pdf` 是要送簽的公文，不是系統報告。
    行內編號與查核清單在公文裡沒有這種東西，查核痕跡留在工作台畫面上——
    承辦人是在畫面上覆核完才按匯出的。

    **揭露不在此列。** 一份抬頭寫著「訴願決定書」的檔案一旦離開系統流傳，
    「這是 AI 生成、未經承辦人確認」這件事就只剩紙上那一行撐著。
    這條同時釘住「拿掉什麼」與「不准拿掉什麼」——只釘前者的話，
    下一個人為了版面乾淨會把揭露也一起清掉，而測試照樣綠。
    """
    render = _render()
    v = _view()
    text = "\n".join(p.text for p in render.Document(io.BytesIO(render.render_docx(v))).paragraphs)
    rid, label = citation_lines(v)[0]
    if f"[{rid}]" in text:
        _fail(f"文件裡出現行內標註 [{rid}]")
    if render.CITATION_HEADING in text:
        _fail("文件裡出現「引註對照」段落")
    if label in text:
        _fail(f"文件裡出現引註對照的內容：{label}")
    if "合成測資" not in text:
        _fail("出處揭露沒有進到 .docx——匯出檔可以不印查核裝置，但不能不講自己是什麼")


def test_export_omits_the_calculation_appendix() -> None:
    """期間計算附錄不進匯出檔：正式決定書沒有這一段，它是規則引擎的算式。

    **`doc[]` 裡仍然保留**（工作台要畫它，CONSTITUTION §1 的可驗算層靠它），
    只是 renderer 不印。這條釘住「兩邊不同步」——哪天有人把 role 改名，
    附錄會默默重新出現在公文裡。
    """
    render = _render()
    v = _view()
    appendix = [sec for sec in v["sections"] if sec.get("role") in render.SKIP_ROLES]
    if not appendix:
        _fail("測資裡沒有附錄 section，這條測試的前提不成立")
    heading = appendix[0]["h"]
    text = "\n".join(p.text for p in render.Document(io.BytesIO(render.render_docx(v))).paragraphs)
    if heading and heading in text:
        _fail(f"附錄段標題印進 .docx 了：{heading}")
    for b in appendix[0]["blocks"]:
        if b["text"] in text:
            _fail(f"附錄內容印進 .docx 了：{b['text'][:30]}")


# ── 組檔層：.pdf（REQ-EXPORT-002）─────────────────────────────────
def test_pdf_embeds_cjk_font() -> None:
    """REQ-EXPORT-002 AC1／AC2。"""
    blob, missing = _render().render_pdf(_view())
    if not blob.startswith(b"%PDF"):
        _fail("產出的不是 PDF")
    if b"NotoSansTC" not in blob:
        _fail("PDF 沒有內嵌 NotoSansTC——沒有 CJK 字型就是整片豆腐字")
    if missing:
        _fail(f"字型缺字：{''.join(missing)}")


def test_pdf_text_layer_is_chinese_not_tofu() -> None:
    """REQ-EXPORT-002 AC3：抽回來的文字要是中文原句。

    需要 `pdftotext`（poppler）。沒有就 `TestSkipped`——**留紅不假綠**：
    「HTTP 200 且有 bytes」完全不能證明畫面上不是 □□□。
    """
    if not shutil.which("pdftotext"):
        raise TestSkipped(
            "本機沒有 pdftotext（poppler），無法驗 PDF 文字層是不是中文。"
            "容器內有（Dockerfile 已裝 poppler-utils）。安裝：brew install poppler"
        )
    render = _render()
    v = _view()
    blob, _missing = render.render_pdf(v)
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "draft.pdf"
        p.write_bytes(blob)
        text = subprocess.run(
            ["pdftotext", "-layout", str(p), "-"],
            capture_output=True, text=True, check=True,
        ).stdout
    sample = v["sections"][0]["blocks"][0]["text"][:12]
    if sample not in text.replace(" ", "").replace("\n", "") and sample not in text:
        _fail(f"PDF 文字層找不到中文原句 {sample!r}（抽到的開頭：{text[:80]!r}）")
    if "�" in text or "□" in text:
        _fail("PDF 文字層出現替代字元／豆腐字")
    rid, _label = citation_lines(v)[0]
    if f"[{rid}]" in text:
        _fail(f"PDF 裡出現行內標註 [{rid}]——匯出檔不印查核裝置（2026-09-13 拍板）")
    if render.CITATION_HEADING in text.replace(" ", ""):
        _fail("PDF 裡出現「引註對照」清單——匯出檔不印查核裝置")
    # **但揭露不能一起消失。** 抬頭寫著「訴願決定書」的檔案離開系統之後，
    # 「這是 AI 生成、未經承辦人確認」只剩紙上這一行撐著（CONSTITUTION §1）。
    # 它在**每一頁的頁尾**（`DRAFT_FOOTER_NOTE`），不在文末——文末那種會被換頁
    # 擠成獨立一頁的孤行，列印出來會被當成印壞的紙抽掉。
    flat = text.replace(" ", "").replace("\n", "")
    if DRAFT_FOOTER_NOTE.replace(" ", "") not in flat:
        _fail("PDF 頁尾找不到草稿揭露")
    pages = text.count("\f") or 1
    if flat.count(DRAFT_FOOTER_NOTE.replace(" ", "")) < pages:
        _fail(f"草稿揭露沒有出現在每一頁（{pages} 頁）")


def test_pdf_without_font_is_hard_failure() -> None:
    """**全檔最重要的一條**（REQ-EXPORT-002 AC4）。

    把字型搜尋路徑清空 → 必須 raise，不得產出一份 200 的豆腐字 PDF。
    這條紅了就代表「字型沒帶進容器」會變成靜默失敗。
    """
    render = _render()
    original = render.font_search_paths
    render.font_search_paths = lambda: []
    try:
        try:
            render.render_pdf(_view())
        except render.CJKFontMissing as e:
            if "查找" not in str(e):
                _fail(f"例外訊息沒有具名列出找過哪些路徑：{e}")
            return
        _fail("字型不存在時竟然成功產出 PDF——那份 PDF 是整片豆腐字")
    finally:
        render.font_search_paths = original


def test_bundled_font_is_present_and_single_weight() -> None:
    """字型是必需資產。它被 `.dockerignore` 或 CDK exclude 擋掉時，
    **雲上會變豆腐字而不是報錯**，本機有系統字型撐著測不出來。"""
    render = _render()
    fonts_dir = ROOT / "backend" / "assets" / "fonts"
    bundled = fonts_dir / render.BUNDLED_FONT_NAME
    if not bundled.is_file():
        _fail(f"隨庫字型不在：{bundled}")
    weights = sorted(p.name for p in fonts_dir.glob("*.[ot]tf"))
    if len(weights) != 1:
        _fail(f"字型目錄應該只有單一 weight，實得 {weights}（整包字型家族會把 build context 撐大）")
    if not (fonts_dir / "LICENSE.txt").is_file():
        _fail("字型缺 LICENSE.txt（SIL OFL 1.1 要求隨附授權）")


# ── 傳輸層（REQ-EXPORT-004）───────────────────────────────────────
def _persisted_run_id(case_id: str = ORDINARY) -> str:
    return run_case(case_id, mode="fixture", persist=True).run_id


def test_endpoint_exports_docx_with_attachment_headers() -> None:
    """REQ-EXPORT-001 AC1。`filename*=UTF-8''…` 是必要的：檔名含中文，
    只給 `filename=` 的話多數瀏覽器會存成亂碼。"""
    api = _endpoint()
    rid = _persisted_run_id()
    resp = api.export_artifact(ORDINARY, rid, format="docx")
    if resp.status_code != 200:
        _fail(f"預期 200，實得 {resp.status_code}")
    if resp.media_type != api.export_render.DOCX_MEDIA_TYPE:
        _fail(f"media_type 不對：{resp.media_type}")
    cd = resp.headers["content-disposition"]
    if not cd.startswith("attachment;") or "filename*=UTF-8''" not in cd:
        _fail(f"Content-Disposition 不是 RFC 5987 的附件下載：{cd}")
    if resp.headers.get("x-cite-count", "0") == "0":
        _fail("X-Cite-Count 沒帶或是 0")
    if not resp.body.startswith(b"PK"):
        _fail("body 不是 OOXML")


def test_unresolved_cites_header_is_a_count_not_an_id_list() -> None:
    """契約 §4.4：`X-Unresolved-Cites` 是「對不回本案 laws／references 的**引註數**」，
    前端「非 0 要警示」。送 id 清單的話前端拿 `"L9,L12"` 去比 0 會永遠成立。

    **計數單位是出現次數不是相異 id**，跟 `X-Cite-Count` 同一個計法，兩個數字要能直接比。
    """
    api = _endpoint()
    rid = _persisted_run_id()
    resp = api.export_artifact(ORDINARY, rid, format="docx")
    raw = resp.headers["x-unresolved-cites"]
    if not raw.isdigit():
        _fail(f"X-Unresolved-Cites 應該是數字，實得 {raw!r}")
    for key in ("x-cite-count", "x-unresolved-cites", "x-export-warning"):
        if key not in resp.headers:
            _fail(f"契約 §4.4 的 {key} 沒帶——省略鍵會讓前端拿到 undefined 而不是 0／空字串")
    # 相異 id 數會把「同一個壞編號被引三次」算成 1，警示強度被稀釋。
    v = _view()
    v_ids = len(v["unresolved"])
    if v["unresolved_count"] < v_ids:
        _fail(f"unresolved_count（{v['unresolved_count']}）比相異 id 數（{v_ids}）還小，計數單位錯了")


def test_export_warning_header_survives_chinese() -> None:
    """**這條擋的是一個真的 500。**

    HTTP 標頭只吃 latin-1，中文直接塞進去會在 `Response(...)` **建構時**就丟
    `UnicodeEncodeError`——整支匯出變 500。而觸發條件是「字型缺字」或「引註對不回來」，
    正常語料下都不會發生，所以這個 bug 在測試裡是隱形的（2026-09-12 實際踩到）。
    """
    api = _endpoint()
    rid = _persisted_run_id()
    payload = build_payload(run_case(ORDINARY, mode="fixture", persist=False))
    payload["laws"] = []          # 讓 L* 對不回來 → 警語一定有中文
    view = build_sections(payload, artifact_id="art-test")
    if not view["unresolved"]:
        _fail("測試前提沒成立：清空 laws 之後仍然沒有對不回來的引註")

    original = api.build_sections
    api.build_sections = lambda *a, **kw: view
    try:
        resp = api.export_artifact(ORDINARY, rid, format="docx")   # 建構標頭時就會炸
    finally:
        api.build_sections = original
    warn = resp.headers["x-export-warning"]
    decoded = urllib.parse.unquote(warn)
    if "對不回本案卷宗" not in decoded:
        _fail(f"警語沒有帶出來：{warn!r} → {decoded!r}")
    if resp.headers["x-unresolved-cites"] == "0":
        _fail("有對不回來的引註，X-Unresolved-Cites 卻是 0")
    warn.encode("latin-1")        # 編不過就是還會 500


def test_endpoint_exports_pdf() -> None:
    api = _endpoint()
    resp = api.export_artifact(ORDINARY, _persisted_run_id(), format="pdf")
    if resp.status_code != 200 or not resp.body.startswith(b"%PDF"):
        _fail(f"PDF 匯出失敗：status={resp.status_code}")
    if resp.media_type != "application/pdf":
        _fail(f"media_type 不對：{resp.media_type}")


def test_endpoint_rejects_unknown_format() -> None:
    """REQ-EXPORT-004 AC1。契約 §1.5 ⑧ 已拍板不降級成 `.md`，
    所以這裡不留「其他格式給純文字」的後路。"""
    api = _endpoint()
    for bad in ("md", "txt", "", "DOCX; rm -rf /"):
        try:
            api.export_artifact(ORDINARY, _persisted_run_id(), format=bad)
        except _http_error() as e:
            if e.status_code != 400:
                _fail(f"format={bad!r} 預期 400，實得 {e.status_code}")
            continue
        _fail(f"format={bad!r} 竟然通過了")


def test_endpoint_rejects_path_traversal() -> None:
    """REQ-EXPORT-004 AC2。`case_id` 與 `artifact_id` 都會被拼進檔案路徑。"""
    api = _endpoint()
    for case_id in ("../../etc", "synthetic-../x", "evil-01", ""):
        try:
            api.export_artifact(case_id, "run-x", format="docx")
        except _http_error() as e:
            if e.status_code != 400:
                _fail(f"case_id={case_id!r} 預期 400，實得 {e.status_code}")
            continue
        _fail(f"case_id={case_id!r} 竟然通過了")
    for art in ("../../../etc/passwd", "art-../x", "draft", ""):
        try:
            api.export_artifact(ORDINARY, art, format="docx")
        except _http_error() as e:
            if e.status_code != 400:
                _fail(f"artifact_id={art!r} 預期 400，實得 {e.status_code}")
            continue
        _fail(f"artifact_id={art!r} 竟然通過了")


def test_endpoint_404_for_unknown_run() -> None:
    api = _endpoint()
    try:
        api.export_artifact(ORDINARY, "run-does-not-exist-000", format="docx")
    except _http_error() as e:
        if e.status_code != 404:
            _fail(f"預期 404，實得 {e.status_code}")
        return
    _fail("不存在的 run 竟然匯得出來")


def test_endpoint_404_for_artifact_without_manifest() -> None:
    """`art-…` 而 manifest 不存在 → 404，不要當成 run id 亂猜。"""
    api = _endpoint()
    try:
        api.export_artifact(ORDINARY, "art-no-such-manifest-entry", format="docx")
    except _http_error() as e:
        if e.status_code != 404:
            _fail(f"預期 404，實得 {e.status_code}")
        return
    _fail("manifest 不存在時竟然解析出了 run")


def test_manifest_takes_priority_over_run_id_fallback() -> None:
    """契約 §4.0 的 manifest 一旦存在就是主路徑，`run-…` 只是它還沒落地時的後備。"""
    api = _endpoint()
    rid = _persisted_run_id()
    case_dir = api.CASES_DIR / ORDINARY
    manifest = case_dir / api.MANIFEST_NAME
    existed = manifest.exists()
    backup = manifest.read_text(encoding="utf-8") if existed else None
    case_dir.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"case_id": ORDINARY, "artifacts": [{"id": "art-x1", "run_id": rid}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        if api.resolve_run_id(ORDINARY, "art-x1") != rid:
            _fail("manifest 裡的 artifact 沒有解析到它的 run_id")
        resp = api.export_artifact(ORDINARY, "art-x1", format="docx")
        if resp.status_code != 200:
            _fail(f"manifest 路徑匯出失敗：{resp.status_code}")
    finally:
        if backup is None:
            manifest.unlink(missing_ok=True)
        else:
            manifest.write_text(backup, encoding="utf-8")


def test_endpoint_503_when_font_missing() -> None:
    """REQ-EXPORT-002 AC4 的 HTTP 面：**503，不是 200**。"""
    api = _endpoint()
    render = api.export_render
    original = render.font_search_paths
    render.font_search_paths = lambda: []
    try:
        try:
            api.export_artifact(ORDINARY, _persisted_run_id(), format="pdf")
        except _http_error() as e:
            if e.status_code != 503:
                _fail(f"預期 503，實得 {e.status_code}")
            return
        _fail("沒有字型時 PDF 端點竟然回了 200——那份檔案是整片豆腐字")
    finally:
        render.font_search_paths = original


def test_json_view_and_export_share_one_sections_implementation() -> None:
    """**契約 §4.4：這個轉換全系統只能有一份實作。**

    同一個 `run_id`，JSON 檢視（`GET …/artifacts/{id}`）與匯出（#23）拿到的
    `sections[]` 必須**逐欄相同**。不同的話，承辦人在畫面上看到的草稿跟他下載到的
    檔案內容不一樣——而那種不一致沒有任何燈會亮。

    2026-09-13 去重前確實是兩份（`backend/dossier/artifacts.py` 與
    `backend/orchestrator/artifact_sections.py`），對 `title`／`meta` 的處理就不同。
    這條釘的是「日後不准再分岔」，不是「現在剛好一樣」。
    """
    api = _endpoint()
    if dossier_api is None:  # pragma: no cover
        raise TestSkipped(f"需要 fastapi（{DOSSIER_IMPORT_ERROR}）。")

    rid = _persisted_run_id()
    payload = build_payload(run_case(ORDINARY, mode="fixture", persist=False))

    # 匯出端走的那一支（`backend/api/export.py` import 的）
    export_sections = api.build_sections(payload, artifact_id="art-x")["sections"]
    # JSON 檢視端走的那一支（`backend/api/dossier.py` import 的）
    json_sections = dossier_api.build_sections(payload, artifact_id="art-x")["sections"]

    if export_sections != json_sections:
        _fail(
            "JSON 檢視與匯出的 sections[] 不一致——契約 §4.4 要求只能有一份實作。\n"
            f"  JSON  ：{[s.get('h') for s in json_sections]}\n"
            f"  匯出  ：{[s.get('h') for s in export_sections]}"
        )
    if api.build_sections is not dossier_api.build_sections:
        _fail("兩端指到不同的函式物件——就算現在輸出剛好一樣，下次有人改一邊就會分岔")
    if not export_sections:
        _fail(f"run {rid} 轉出來是空的，這條比對就沒有意義")


def test_the_two_ends_share_the_resolution_but_deliberately_differ_on_an_empty_draft() -> None:
    """**這條釘的是一個刻意的不一致，不是一個 bug。**

    同一個 `run-…`，run 存在但 `doc[]` 沒有任何句子時：
      - 匯出（#23）→ **409**（契約 §1.5 #23 錯誤碼表明文：回 200 空檔＝失敗看起來像成功）
      - JSON 檢視（#21）→ **200 ＋ 空 `sections[]`**（§4.4 #21 沒有這條規則）

    2026-09-13 team-lead 拍板維持現狀。會寫這條測試是因為**看到兩處行為不一致的人
    很容易「順手對齊」**，而對齊會踩掉 409 那條擋「空白檔被讀成本案無話可說」的守門。
    要改的話先改契約（§4.4 #21 補一條回應碼規則），不是先改實作——
    這條紅了就代表有人動了其中一邊，請先去讀
    `backend/dossier/artifact_ref.py` 檔頭「共用的是解析，不是回應碼」那一段。

    **同時也釘住「解析是共用的」**：兩端都要認得出這是一個 run id
    （2026-09-13 修掉的分岔正是「匯出認得、詳情不認得」）。
    """
    api = _endpoint()
    if dossier_api is None:  # pragma: no cover
        raise TestSkipped(f"需要 fastapi（{DOSSIER_IMPORT_ERROR}）。")

    # `to_node="n3"` 停在 SCREENED——這是聊天「解析卷證」工具走的同一條路，
    # 產出的 run 有狀態但沒有草稿。不是為了測試捏造的形狀。
    rid = run_case(ORDINARY, mode="fixture", to_node="n3", persist=True).run_id
    if has_body(build_sections(build_payload(run_case(
            ORDINARY, mode="fixture", to_node="n3", persist=False)), artifact_id=rid)):
        _fail("挑來當「沒有草稿」的 run 其實有草稿，這條比對就沒有意義")

    try:
        api.export_artifact(ORDINARY, rid, format="docx")
    except _http_error() as e:
        if e.status_code != 409:
            _fail(f"匯出端預期 409（沒有草稿不給空白檔），實得 {e.status_code}")
    else:
        _fail("沒有草稿的 run 竟然匯出成功了——那份檔案只有抬頭")

    out = dossier_api.get_artifact(ORDINARY, rid)
    if out["run_id"] != rid:
        _fail("JSON 檢視端沒有認出直接帶進來的 run id——解析又分岔了")
    if out["sections"]:
        _fail(f"這個 run 不該有 sections：{out['sections']!r}")


def test_endpoint_mounted_on_app_with_contract_path() -> None:
    """契約 §1.5 #23 的路徑要真的掛上去。

    不驗這條的話，端點函式全綠、前端打過來 404——測試綠而功能死。
    """
    if fastapi_app is None:  # pragma: no cover
        raise TestSkipped(f"需要 fastapi 與 python-multipart（{APP_IMPORT_ERROR}）。")
    # 用 OpenAPI schema 而不是走訪 `app.routes`：FastAPI 較新版本把
    # `include_router` 掛成 `_IncludedRouter` 包裝節點，`app.routes` 只列得到
    # 頂層那幾支（2026-09-12 實測，chat 的 route 也一樣看不到）。
    # 走訪 routes 的版本會在「路由其實掛好了」的情況下誤報，等於一條假失敗。
    paths = set(fastapi_app.openapi().get("paths", {}))
    want = "/api/cases/{case_id}/artifacts/{artifact_id}/export"
    if want not in paths:
        _fail(f"app 上沒有 {want}（已掛：{sorted(q for q in paths if 'artifact' in q)}）")


def test_chat_tool_result_reports_the_same_cite_count_as_the_json_view_and_the_export() -> None:
    """**同一份草稿，三處的 `cite_count` 必須是同一個數字。**

    前端 e2e（2026-09-13）抓到後端自己對不起來：`tool_result.cite_count` 報一個值，
    `GET …/artifacts/{id}` 與匯出的 `X-Cite-Count` 報另一個。畫面上不會出錯
    （前端兩處都用後者），但下一個人看到兩個數字會不知道信哪個。

    根因是**數的東西不一樣**，不是算錯：
      - `payload["citations"]` 是 N6 的逐句驗證紀錄，**含對不回來、被清掉的那些**。
      - `sections[].blocks[].cites` 是實際帶進文件、使用者在匯出檔裡看得到的引註。
    這個欄位的語意是後者（契約 §4.4：這個轉換全系統只能有一份實作）。

    兩個案子一起驗**不是為了覆蓋率**：`ordinary` 兩種算法剛好都是 4，
    只驗它的話換回舊實作照樣綠。`blocked` 才是分歧點（新 2、舊 3），
    而且兩案的值不相等（4 vs 2），所以這條也抓得到「比較了兩個常數」的假測試。
    """
    api = _endpoint()
    if dossier_api is None:  # pragma: no cover
        raise TestSkipped(f"需要 fastapi（{DOSSIER_IMPORT_ERROR}）。")

    seen: dict[str, int] = {}
    for case_id in (ORDINARY, "synthetic-blocked-01"):
        payload = build_payload(run_case(case_id, mode="fixture", persist=False))
        with tempfile.TemporaryDirectory() as tmp:
            run_pipeline = chat_bridge.pipeline_adapter(
                case_id, pathlib.Path(tmp), mode="fixture")
            chat_cite = run_pipeline(to_node="n6")["cite_count"]

        json_cite = dossier_api.build_sections(payload, artifact_id="art-x")["cite_count"]
        export_cite = api.build_sections(payload, artifact_id="art-x")["cite_count"]
        if not (chat_cite == json_cite == export_cite):
            _fail(
                f"{case_id} 的 cite_count 三處對不起來——契約 §4.4 只能有一份實作。\n"
                f"  tool_result（chat）      ：{chat_cite}\n"
                f"  GET …/artifacts/{{id}}    ：{json_cite}\n"
                f"  X-Cite-Count（匯出）      ：{export_cite}"
            )
        seen[case_id] = chat_cite

        # 分歧點：`blocked` 的舊算法（數 `citations[]`）會多算被清掉的那一筆。
        # 這條讓「改回 len(payload['citations'])」一定紅。
        old_way = len(payload.get("citations") or [])
        if case_id == "synthetic-blocked-01" and chat_cite == old_way:
            _fail(
                f"blocked 案的 cite_count 又回到數 citations[] 了（{old_way}）——"
                f"那會把 N6 清掉、沒有進到草稿裡的引註也算進去"
            )

    if len(set(seen.values())) < 2:
        _fail(f"兩個案子的 cite_count 相同（{seen}），這條比對抓不到「比了兩個常數」")


def test_the_chat_bridge_does_not_count_citations_itself() -> None:
    """反向：`chat_bridge` 不得自己數，必須指到同一個函式物件。

    輸出剛好一樣不算數——下次有人改一邊就會分岔，而分岔的時候沒有任何燈會亮。
    """
    if dossier_api is None:  # pragma: no cover
        raise TestSkipped(f"需要 fastapi（{DOSSIER_IMPORT_ERROR}）。")
    if chat_bridge.build_sections is not dossier_api.build_sections:
        _fail("chat_bridge 與 JSON 檢視端指到不同的 build_sections")

    tree = ast.parse(
        (ROOT / "backend" / "orchestrator" / "chat_bridge.py").read_text(encoding="utf-8"))
    adapter = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "run_pipeline")
    for node in ast.walk(adapter):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "len"):
            continue
        arg = ast.unparse(node.args[0]) if node.args else ""
        if "citations" in arg:
            _fail(f"chat_bridge 又自己數引註了：len({arg})")
