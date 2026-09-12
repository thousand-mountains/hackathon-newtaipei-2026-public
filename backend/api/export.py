"""草稿匯出端點（US-C3 / REQ-EXPORT-001～004）。

契約：`docs/handoff/2026-09-12-frontend-contract-v2.md` §1.5 #23、§4.4

    GET /api/cases/{case_id}/artifacts/{artifact_id}/export?format=pdf|docx
      → 200 檔案（`Content-Disposition: attachment`）

**這個 router 掛在 `backend/api/app.py` 只佔一行**（`include_router`）——回滾就是
把那行拿掉。Epic A／B 同時在改 `app.py` 與 `chat.py`，所以本功能全部是新檔。

## 三個刻意的設計

1. **`format` 只收 `pdf`／`docx`，其餘 400。** 契約 §1.5 ⑧ 已拍板不降級成 `.md`，
   所以這裡不留「其他格式就給純文字」的後路——留了就會有人在 demo 當天
   拿到一份副檔名對、內容是純文字的檔案，然後以為功能做完了。

2. **`artifact_id` → `run_id` 有兩條解析，順序固定。**
   - 主路徑：`backend/output/cases/{case_id}/manifest.json` 的 `artifacts[]`
     （契約 §4.0，由 case-dossier-crud 產生）。**本檔只讀不寫。**
   - 後備：`artifact_id` 本身就是 `run-…`。manifest 還沒落地時匯出仍可用。
     **這是寫在 OpenAPI 說明裡的公開行為，不是隱藏後門**；manifest 一旦有了，
     它永遠優先。

3. **草稿還沒生成 → 409，不回空白檔。** run 存在但 `doc[]` 沒有任何句子時，
   回一份只有抬頭的 `.docx` 是「看起來很像成功的失敗」——承辦人會以為
   AI 真的認為本案無話可說。

## 路徑穿越

`case_id` 與 `artifact_id` 都會被拼進檔案路徑，兩者一律走白名單 regex
（比照 `backend/orchestrator/runstore.py:RUN_ID_RE` 的既有作法）。
不合格就 400，**不做「清洗後照樣讀」**——清洗是猜對方想讀什麼，拒絕才是誠實。
"""
from __future__ import annotations

import json
import pathlib
import re
import urllib.parse
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from backend.api import export_render
from backend.config.settings import OUTPUT_DIR
from backend.orchestrator.artifact_sections import build_sections, has_body
from backend.orchestrator.graph import build_payload
from backend.orchestrator.runstore import RunNotFound, load_run

router = APIRouter()

# `load_case()`（graph.py:107-117）只接受這兩種前綴，這裡跟它對齊。
CASE_ID_RE = re.compile(r"^(?:synthetic|upload)-[A-Za-z0-9_\-]+$")
# manifest 的 artifact id（契約 §4.0 的 `art-…`），或後備的 `run-…`。
ARTIFACT_ID_RE = re.compile(r"^(?:art|run)-[A-Za-z0-9_\-]+$")

FORMATS = ("docx", "pdf")

_MEDIA_TYPES = {
    "docx": export_render.DOCX_MEDIA_TYPE,
    "pdf": export_render.PDF_MEDIA_TYPE,
}

# 一份案子的書籤檔（契約 §4.0）。這裡只讀，寫由 case-dossier-crud 負責。
CASES_DIR = OUTPUT_DIR / "cases"
MANIFEST_NAME = "manifest.json"


def _manifest_path(case_id: str) -> pathlib.Path:
    return CASES_DIR / case_id / MANIFEST_NAME


def _run_id_from_manifest(case_id: str, artifact_id: str) -> str | None:
    """manifest 裡這個 artifact 對應的 run_id；manifest 或該筆不存在回 None。

    manifest 讀不動（壞掉的 JSON）時也回 None 走後備，而不是 500：
    書籤檔壞掉不該讓「我只是想下載草稿」整條路斷掉。
    """
    p = _manifest_path(case_id)
    if not p.is_file():
        return None
    try:
        manifest = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    for art in manifest.get("artifacts") or []:
        if str(art.get("id")) == artifact_id:
            run_id = art.get("run_id")
            return str(run_id) if run_id else None
    return None


def resolve_run_id(case_id: str, artifact_id: str) -> str:
    """manifest 優先，`run-…` 後備。兩條都不成立就 404。"""
    run_id = _run_id_from_manifest(case_id, artifact_id)
    if run_id:
        return run_id
    if artifact_id.startswith("run-"):
        return artifact_id
    raise HTTPException(
        status_code=404,
        detail=(
            f"案件 {case_id} 的產出 {artifact_id} 找不到對應的執行紀錄。"
            f"（已查 {_manifest_path(case_id)}；artifact_id 若直接帶 run-… 亦可匯出）"
        ),
    )


def _filename(view: dict[str, Any], fmt: str) -> str:
    """檔名用案號而不是 artifact id：承辦人的下載目錄裡要看得出是哪一案。"""
    stem = (view.get("case_id") or view.get("run_id") or "訴願決定書草稿").strip()
    return f"{stem}-訴願決定書草稿.{fmt}"


def _encode_warning(text: str) -> str:
    """`X-Export-Warning` 的值。**百分比編碼，因為 HTTP 標頭只吃 latin-1。**

    2026-09-12 實測：把中文直接放進標頭，`Response(...)` 在**建構時**就丟
    `UnicodeEncodeError`——整支匯出變 500。而觸發條件是「字型缺字」，
    正常語料下永遠不會發生，所以這個 bug 在測試裡是隱形的
    （`test_export_warning_header_survives_chinese` 現在把它釘住了）。

    ⚠️ **契約 §4.4 寫的是「人類可讀的警語（可空）｜直接顯示」**，沒說要解碼。
    中文不編碼在 HTTP 上根本送不出去，所以前端要 `decodeURIComponent()` 再顯示。
    **這一句契約沒有，已回報請契約擁有者補**——不是我自行改契約（CONSTITUTION §9）。
    """
    return urllib.parse.quote(text or "", safe="")


def _content_disposition(filename: str) -> str:
    """RFC 5987。檔名含中文，只給 `filename=` 的話多數瀏覽器會存成亂碼或 `download`。"""
    quoted = urllib.parse.quote(filename, safe="")
    return f"attachment; filename=\"draft.{filename.rsplit('.', 1)[-1]}\"; filename*=UTF-8''{quoted}"


@router.get(
    "/api/cases/{case_id}/artifacts/{artifact_id}/export",
    summary="匯出草稿為 .docx 或 .pdf",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}, export_render.DOCX_MEDIA_TYPE: {}}},
        400: {"description": "format 不在 pdf/docx，或 id 不合白名單"},
        404: {"description": "找不到對應的執行紀錄"},
        409: {"description": "該 run 尚未生成草稿（doc[] 為空）"},
        503: {"description": "容器內找不到 CJK 字型，PDF 匯出中止（不產出豆腐字）"},
    },
)
def export_artifact(
    case_id: str,
    artifact_id: str,
    format: str = Query("docx", description="docx｜pdf"),  # noqa: A002 — 契約定的查詢參數名
) -> Response:
    """契約 §1.5 #23。

    `artifact_id` 取自 `GET /api/cases/{id}/artifacts`（契約 §4.4）。
    **manifest 尚未落地時，`artifact_id` 可直接帶 `run-…`**，匯出該次 run 的草稿；
    manifest 存在時一律以 manifest 為準。

    `.docx` 用 `python-docx` 直接組段落（可續編），`.pdf` 內嵌 CJK 字型。
    找不到 CJK 字型時回 **503 而非 200**：沒有字型的 PDF 是整片豆腐字且不報錯。
    """
    fmt = (format or "").strip().lower()
    if fmt not in FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"format 只接受 {'／'.join(FORMATS)}，收到 {format!r}。契約 §1.5 ⑧ 已拍板不降級成其他格式。",
        )
    if not CASE_ID_RE.match(case_id or ""):
        raise HTTPException(status_code=400, detail=f"case_id 格式不合法：{case_id!r}")
    if not ARTIFACT_ID_RE.match(artifact_id or ""):
        raise HTTPException(status_code=400, detail=f"artifact_id 格式不合法：{artifact_id!r}")

    run_id = resolve_run_id(case_id, artifact_id)
    try:
        state = load_run(run_id)
    except RunNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:  # runstore 的 run_id 白名單
        raise HTTPException(status_code=400, detail=str(e)) from e

    view = build_sections(build_payload(state), artifact_id=artifact_id)
    if not has_body(view):
        raise HTTPException(
            status_code=409,
            detail=(
                f"執行紀錄 {run_id} 還沒有草稿內容（doc[] 沒有任何句子），"
                f"請先生成草稿再匯出。匯出一份只有抬頭的空白檔會被誤讀成「本案無話可說」。"
            ),
        )

    headers = {
        "Content-Disposition": _content_disposition(_filename(view, fmt)),
        # 契約 §4.4 標頭表。三個一律都帶（含值為 0 或空字串的情況）：
        # 省略鍵會讓前端拿到 undefined 而不是 0／""，跟 §2.3 的「其餘為 null 也要送」同理。
        "X-Cite-Count": str(view.get("cite_count", 0)),
        # **是引註「數」不是 id 清單**（契約：「對不回本案 laws／references 的引註數」，
        # 前端「非 0 要警示」）。id 清單改放 X-Export-Warning——那裡才是人看的。
        "X-Unresolved-Cites": str(view.get("unresolved_count", 0)),
    }

    warnings: list[str] = []
    if view.get("unresolved"):
        # 對不回來的引用在匯出檔裡會印成「L9（未解析）」。這裡具名再講一次，
        # 免得只有打開檔案的人才知道是哪幾個編號。
        warnings.append("下列引註對不回本案卷宗：" + "、".join(view["unresolved"]))

    if fmt == "docx":
        body = export_render.render_docx(view)
    else:
        try:
            body, missing = export_render.render_pdf(view)
        except export_render.CJKFontMissing as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        if missing:
            warnings.append("字型缺少下列字元，PDF 中會顯示為空白或豆腐字：" + "".join(missing))

    headers["X-Export-Warning"] = _encode_warning("；".join(warnings))
    return Response(content=body, media_type=_MEDIA_TYPES[fmt], headers=headers)
