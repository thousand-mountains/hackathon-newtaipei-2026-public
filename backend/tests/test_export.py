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
import pathlib
import shutil
import subprocess
import tempfile
import zipfile

from backend.orchestrator.artifact_sections import (
    UNRESOLVED_SUFFIX,
    build_sections,
    citation_lines,
    dataset_scope,
    has_body,
    inline_marks,
)
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
    for expected in ("事實", "理由", "決定主文"):
        if expected not in headings:
            _fail(f"section 標題少了 {expected!r}，實得 {headings}")
    body = [b["text"] for s in v["sections"] for b in s["blocks"]]
    if not body:
        _fail("sections 裡一句正文都沒有——句子在 block['ss'][*]['t']，不是 block['text']")
    if any(not t.strip() for t in body):
        _fail("有空白 block 混進來")


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


def test_docx_carries_citations() -> None:
    """REQ-EXPORT-003 AC1／AC2：行內 `[L3]` ＋ 文末引註對照，兩處都要有。"""
    render = _render()
    v = _view()
    text = "\n".join(p.text for p in render.Document(io.BytesIO(render.render_docx(v))).paragraphs)
    rid, label = citation_lines(v)[0]
    if f"[{rid}]" not in text:
        _fail(f"文件裡找不到行內標註 [{rid}]——匯出成白文等於把出處丟了")
    if render.CITATION_HEADING not in text:
        _fail("文件裡沒有「引註對照」段落")
    if label not in text:
        _fail(f"引註對照沒有列出 {label}")
    if "合成測資" not in text:
        _fail("出處揭露沒有進到 .docx")


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
    if f"[{rid}]" not in text:
        _fail(f"PDF 裡找不到行內標註 [{rid}]")
    if render.CITATION_HEADING not in text.replace(" ", ""):
        _fail("PDF 裡沒有「引註對照」")


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
