"""案件卷宗與母庫的一次性端點（契約 v2 §1、§4）。**這裡沒有串流、也沒有輪詢。**

三類資源，權限不同，分得很清楚：

- **母庫（`/api/laws`、`/api/decisions`）對使用者唯讀。** 系統裡不能改一條法律或
  一份決定書，所以只有 `GET`。
- **卷宗成員（`/api/cases/{id}/{files|laws|references}`）是「本案挑進來的清單」**，
  有 C/R/D（加入、讀、移出），**沒有 U**——編輯母庫內容這件事不存在。
- **產出（`/api/cases/{id}/artifacts`）由 agent 生成**，所以只有 R/D。

`lawId`／`decisionId` 是 **S3 相對 key**（含 `/`），所以路由用 `{law_id:path}` 收整段。
兩個 id 進來都要過 `corpus.safe_key()`——它是這一層的安全邊界，見該函式的說明。

**boto3 client 在本檔建**（`backend/api/` 是紅線掃描的具名豁免之一），
`backend/dossier/corpus.py` 只吃注入的 client，維持零外部依賴。
"""
from __future__ import annotations

import pathlib
import sys
from typing import Any

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, ConfigDict

from backend.config import settings
from backend.dossier import artifact_ref, corpus, runlink, store
from backend.dossier.redact import redact
from backend.intake.uploads import MAX_BYTES, UPLOADS_DIR, _safe_name
from backend.orchestrator.artifact_sections import build_sections
from backend.orchestrator.case_view import blocks_from_latest_run
from backend.orchestrator.graph import build_payload
from backend.orchestrator.runstore import RunNotFound, load_run

try:  # 與 retrieval/kb.py 同一個做法：缺席要看得見，不是隱藏開關
    import boto3
except ImportError:  # pragma: no cover - 有裝 boto3 的環境走不到
    boto3 = None

router = APIRouter()

#: 母庫查一次回幾筆。前端的搜尋對話框一頁就這麼多。
SEARCH_LIMIT = 10


# ── client 工廠：只有這一層碰 boto3 ────────────────────────────────


def _client(service: str) -> Any:
    if boto3 is None:
        raise HTTPException(
            status_code=503,
            detail="母庫需要 boto3（容器內由 requirements.txt 帶；本機用 uv run --with boto3）",
        )
    return boto3.client(service, region_name=settings.aws_region())


def _kb_client() -> Any:
    return _client("bedrock-agent-runtime")


def _s3_client() -> Any:
    return _client("s3")


def _translate(e: Exception) -> HTTPException:
    """卷宗層的例外 → HTTP。**每一種都說得出是哪一種**，不包成一句「系統忙碌中」。

    每一條出口都過 `redact()`——包含我們自己丟的那幾種。它們現在不含雲端識別資訊，
    但「現在不含」不是可以依賴的性質（`CorpusUnavailable` 的訊息就長在會提到設定的地方）。
    """
    if isinstance(e, (corpus.OutOfScope, store.CaseNotDeletable)):
        # 「存在但本期不供應／不可刪」≠「找不到」。回 404 會讓人去找一份其實存在的東西。
        return HTTPException(status_code=400, detail=redact(str(e), settings.kb_bucket))
    if isinstance(e, (store.CaseManifestNotFound, corpus.DocumentNotFound, RunNotFound,
                      artifact_ref.ArtifactRunNotFound)):
        return HTTPException(status_code=404, detail=redact(str(e), settings.kb_bucket))
    if isinstance(e, corpus.CorpusUnavailable):
        return HTTPException(status_code=503, detail=redact(str(e), settings.kb_bucket))
    if isinstance(e, ValueError):
        return HTTPException(status_code=400, detail=redact(str(e), settings.kb_bucket))
    # 到這裡的是**沒有被分類過的**例外（雲端 SDK 的原始錯誤多半落在這裡）。
    # 原文一律不出去，但要進伺服器 log——那裡可以有帳號資訊，而且我們需要它：
    # 真正的權限故障若只剩一句「上游錯誤」，我們自己會查不下去。
    print(f"[warn] 卷宗端點未分類的例外（原文只進 log，不進 HTTP 回應）："
          f"{type(e).__name__}: {e}", file=sys.stderr)
    return HTTPException(
        status_code=502,
        detail=(f"母庫這次沒有回應（{type(e).__name__}）。這不是你的操作問題，"
                f"請稍後再試；若持續發生請把時間點告訴維運，詳細錯誤在伺服器紀錄裡。"),
    )


def _read(case_id: str) -> dict[str, Any]:
    """讀卷宗，**不無中生有**。所有唯讀端點走這支，不要直接呼叫 `store.ensure()`。

    `ensure()` 是「有就讀、沒有就依來源推一份**並落地**」——那是建案與加入項目
    要的語意，套到 GET 上就變成「打錯字的 case_id 回 200 加一個空案」，
    而且在磁碟上留下一份永遠沒人清的 manifest（2026-09-13 雲上實測）。
    契約 §1.1 附註：**沒有「先建空案」這個狀態**。

    為什麼這裡還是呼叫 `ensure()` 而不是 `load()`：manifest 是**推導出來的書籤**，
    合成案（`synthetic-blocked-01` 等）第一次被讀到時本來就沒有 manifest，
    改成 `load()` 會讓 demo 的主要案例整個讀不到。所以判準是**案件來源在不在**
    （`store.source_exists`），不是 manifest 在不在——來源在就照舊推一份，
    來源不在就 404。

    `store.exists()` 先跑是刻意的：它會驗 `case_id` 格式並在不合法時丟 `ValueError`
    （→ 400「格式不合法」），跟這裡的 404（「格式對但沒這個案子」）是兩種回答。
    """
    if not store.exists(case_id) and not store.source_exists(case_id):
        raise store.CaseManifestNotFound(
            f"找不到案件 {case_id}。它不在合成測資裡，也沒有對應的上傳卷證。"
        )
    return store.ensure(case_id)


class RenameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class AddLawsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    law_ids: list[str]


class AddReferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_ids: list[str]


# ── 1.1 案件 ──────────────────────────────────────────────────────


@router.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    """開案首載的**彙整版**：一次回卷宗五鍵 ＋ 最後一次 run 的四塊（契約 §4、§3.3）。

    右欄四個群組合起來畫，分四支打會多三趟往返，而右欄是開案就要整片出現的；
    中欄的工具卡與左欄的程序審查則要 `RUN_BLOCKS` 那四塊（見上面的說明）。
    """
    try:
        m = _read(case_id)
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e
    return {
        "case": {"id": m["case_id"], "name": m["name"], "created_at": m["created_at"],
                 "latest_run_id": m["latest_run_id"]},
        "files": m["files"],
        "laws": m["laws"],
        "references": m["references"],
        "artifacts": m["artifacts"],
        # 既有五鍵的形狀一個字都沒動——前端已經接好了（team-lead 2026-09-13 交代）。
        **blocks_from_latest_run(m["latest_run_id"]),
    }


@router.patch("/api/cases/{case_id}")
def rename_case(case_id: str, body: RenameIn) -> dict:
    try:
        m = store.rename(case_id, body.name)
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e
    return {"id": m["case_id"], "name": m["name"], "created_at": m["created_at"]}


@router.delete("/api/cases/{case_id}", status_code=204)
def delete_case(case_id: str) -> Response:
    """刪案＝連實體卷證一起刪（見 `store.delete_case` 的說明）。**不刪 runs。**

    只刪 manifest 是不夠的：`list_cases()` 掃的是 `output/uploads/`，案子會在下一次
    `GET /api/cases` 原地復活，而且名字變回預設值。合成測資拒絕刪除（400）。
    """
    try:
        ok = store.delete_case(case_id)
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"找不到案件 {case_id}")
    return Response(status_code=204)


# ── 1.2 卷證檔案 ──────────────────────────────────────────────────


@router.get("/api/cases/{case_id}/files")
def list_files(case_id: str) -> dict:
    try:
        return {"files": _read(case_id)["files"]}
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e


@router.post("/api/cases/{case_id}/files", status_code=201)
async def add_files(case_id: str, files: list[UploadFile] = File(...)) -> dict:
    """把檔案加進**既有**案件。`POST /api/cases` 是「上傳即建案」，這支是「再補幾份」。

    只接受上傳案：合成案的卷證是測資內嵌的，沒有目錄可以放新檔——
    **拒絕比默默存到一個永遠讀不到的地方好**。
    """
    if not case_id.startswith("upload-"):
        raise HTTPException(
            status_code=400,
            detail=f"{case_id} 是合成案例，卷證內嵌於測資、沒有可寫入的卷證目錄。"
                   f"要補卷證請改用 POST /api/cases 建一個上傳案。",
        )
    d = UPLOADS_DIR / case_id
    if not (d / "case.json").exists():
        raise HTTPException(status_code=404, detail=f"找不到上傳案 {case_id}")
    payload = [(f.filename or "file", await f.read()) for f in files]
    if not payload:
        raise HTTPException(status_code=400, detail="至少要上傳一個檔案")
    written: list[str] = []
    for name, data in payload:
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=400, detail=f"{name!r} 超過 {MAX_BYTES} bytes")
        safe = _safe_name(name)
        p = d / safe
        if p.exists():
            raise HTTPException(
                status_code=409,
                detail=f"卷證 {safe!r} 已存在。清理後同名的檔案會互相覆蓋，請改名後再上傳。",
            )
        p.write_bytes(data)
        written.append(safe)
    # manifest 重推一次（`route_documents` 會判新檔讀不讀得到），再把使用者原有的
    # note 貼回去——重推是為了拿 readable 與原因，不是為了把人寫的東西洗掉。
    m = store.ensure(case_id)
    notes = {f["id"]: f.get("note") for f in m["files"]}
    fresh = store.default_manifest(case_id)["files"]
    for f in fresh:
        if notes.get(f["id"]):
            f["note"] = notes[f["id"]]
    m["files"] = fresh
    store.save(m)
    return {"files": [f for f in fresh if f["name"] in written]}


@router.delete("/api/cases/{case_id}/files/{file_id}", status_code=204)
def remove_file(case_id: str, file_id: str) -> Response:
    """移出卷證。**實體檔也一起刪。**

    只從 manifest 移除是不夠的：`store.default_manifest` 會用 `route_documents`
    掃卷證目錄重推 `files[]`，下次補上傳新檔時那份被「移出」的卷證就會復活。
    刪磁碟檔才是使用者按下移除時真正期待的行為。
    """
    try:
        m = _read(case_id)
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e
    hit = next((f for f in m["files"] if f["id"] == file_id), None)
    if hit is None:
        raise HTTPException(status_code=404, detail=f"案件 {case_id} 沒有這份卷證：{file_id}")
    if case_id.startswith("upload-"):
        p = UPLOADS_DIR / case_id / pathlib.Path(str(hit["name"])).name
        p.unlink(missing_ok=True)
    store.remove_item(case_id, "files", file_id)
    return Response(status_code=204)


# ── 1.3／1.4 母庫查（唯讀） ───────────────────────────────────────


@router.get("/api/laws")
def search_laws(q: str = "") -> dict:
    """查母庫法規。**查無回 `{results:[]}`，那不是錯誤。**"""
    if not q.strip():
        return {"results": []}
    try:
        return {"results": corpus.search_statutes(q, limit=SEARCH_LIMIT, kb=_kb_client())}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e


@router.get("/api/laws/{law_id:path}")
def get_law(law_id: str) -> dict:
    try:
        return corpus.get_statute(law_id, s3=_s3_client())
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e


@router.get("/api/decisions")
def search_decisions(q: str = "") -> dict:
    if not q.strip():
        return {"results": []}
    try:
        return {"results": corpus.search_decisions(q, limit=SEARCH_LIMIT, kb=_kb_client())}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e


@router.get("/api/decisions/{decision_id:path}")
def get_decision(decision_id: str) -> dict:
    try:
        return corpus.get_decision(decision_id, s3=_s3_client())
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e


# ── 1.3／1.4 本案清單 ─────────────────────────────────────────────


@router.get("/api/cases/{case_id}/laws")
def list_case_laws(case_id: str) -> dict:
    try:
        return {"laws": _read(case_id)["laws"]}
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e


@router.post("/api/cases/{case_id}/laws", status_code=201)
def add_case_laws(case_id: str, body: AddLawsIn) -> dict:
    """把母庫法規加進本案。**加入時就把全文快取進 `body_cached`**（契約 §4.0）：
    demo 當下不依賴 KB／S3 還活著。

    `verified` 恆 False、`relevance` 恆 unknown——這是 KB 全文通道的真實保證等級，
    與 `laws-snapshot.json` 查表通道不是同一件事，畫面上要分得出來（契約 §4.2）。
    """
    s3 = _s3_client()
    items: list[dict[str, Any]] = []
    for law_id in body.law_ids:
        try:
            doc = corpus.get_statute(law_id, s3=s3)
        except Exception as e:  # noqa: BLE001
            raise _translate(e) from e
        items.append({
            "id": doc["id"], "t": doc["t"], "src": doc["src"], "note": "",
            "verified": False, "relevance": "unknown", "body_cached": doc["body"],
            # 剛加入時是「還沒查過」，**不是「查不到」**（proposal B4.3）。兩者合成一個值的話，
            # 使用者一按加入就會看到「檢索未命中」，那是還沒發生的事。
            "retrieval_status": runlink.RETRIEVAL_UNKNOWN, "retrieval_note": "",
        })
    try:
        store.add_items(case_id, "laws", items)
        return {"laws": store.load(case_id)["laws"]}
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e


@router.delete("/api/cases/{case_id}/laws/{law_id:path}", status_code=204)
def remove_case_law(case_id: str, law_id: str) -> Response:
    try:
        ok = store.remove_item(case_id, "laws", law_id)
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"案件 {case_id} 的法規清單沒有 {law_id}")
    return Response(status_code=204)


@router.get("/api/cases/{case_id}/references")
def list_case_references(case_id: str) -> dict:
    try:
        return {"references": _read(case_id)["references"]}
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e


@router.post("/api/cases/{case_id}/references", status_code=201)
def add_case_references(case_id: str, body: AddReferencesIn) -> dict:
    """把母庫訴願決定加進本案。`score` 留 `None`——**分數是某一次查詢的相似度，
    不是這份決定書的屬性**，加進卷宗之後它就沒有對應的查詢了，填一個舊分數
    會讓人以為那是「這份跟本案的相似度」。前端要顯示相似度請用搜尋結果那一份。

    法院裁判書在 `corpus.get_decision` 被擋（→ 400），不會走到這裡。
    """
    s3 = _s3_client()
    items: list[dict[str, Any]] = []
    for decision_id in body.decision_ids:
        try:
            doc = corpus.get_decision(decision_id, s3=s3)
        except Exception as e:  # noqa: BLE001
            raise _translate(e) from e
        items.append({
            "id": doc["id"], "t": doc["t"], "src": doc["src"], "note": "",
            "score": None, "provenance": doc["provenance"], "doc_kind": doc["doc_kind"],
            "verdict": doc["verdict"], "category": doc["category"], "full_cached": doc["full"],
        })
    try:
        store.add_items(case_id, "references", items)
        return {"references": store.load(case_id)["references"]}
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e


@router.delete("/api/cases/{case_id}/references/{ref_id:path}", status_code=204)
def remove_case_reference(case_id: str, ref_id: str) -> Response:
    try:
        ok = store.remove_item(case_id, "references", ref_id)
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"案件 {case_id} 的案例清單沒有 {ref_id}")
    return Response(status_code=204)


# ── 1.5 產出 ──────────────────────────────────────────────────────


@router.get("/api/cases/{case_id}/artifacts")
def list_artifacts(case_id: str) -> dict:
    try:
        return {"artifacts": _read(case_id)["artifacts"]}
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e




@router.get("/api/cases/{case_id}/artifacts/{artifact_id}")
def get_artifact(case_id: str, artifact_id: str) -> dict:
    """一份產出的 JSON 檢視（契約 §4.4 #21）。

    **`artifactId → runId` 的解析與匯出端（#23）共用同一支**
    （`backend/dossier/artifact_ref.resolve_run_id`）——契約 §4.4 明講這個轉換
    全系統只能有一份實作，含「manifest 還不存在時允許 artifactId 直接帶 `run-…`」
    這條過渡規則。2026-09-13 之前只有匯出端做了後備，於是同一個 `run-…`
    打匯出回 409（認得出它是 run）、打這支回 404（沒認出來）。

    ⚠️ **共用的是解析，不是回應碼。** run 存在但還沒有草稿時，匯出回 409
    （契約 §1.5 #23 明文），**這支刻意回 200 ＋ 空 `sections[]`**（§4.4 #21
    沒有這條規則）。2026-09-13 team-lead 拍板維持現狀，理由與實測見
    `backend/dossier/artifact_ref.py` 檔頭「共用的是解析，不是回應碼」那一段。
    **不要為了「兩邊一致」在這裡補一個 409**——那會改到契約，而且畫面上畫不到：
    前端兩個呼叫本端點的地方帶的都是 manifest 來的 `art-…`。
    """
    try:
        m = _read(case_id)
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e
    hit = next((a for a in m["artifacts"] if a["id"] == artifact_id), None)
    try:
        run_id = artifact_ref.resolve_run_id(case_id, artifact_id)
        view = build_sections(build_payload(load_run(run_id)), artifact_id=artifact_id)
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e
    # `title` 優先用 manifest 記的產出名稱（`hit["name"]`），不是 doc 裡的抬頭：
    # 那是承辦人在右欄看到的那個名字，換掉會讓清單與詳情對不起來。
    # manifest 還沒記到這筆（走 `run-…` 後備）時才退回 doc 的抬頭。
    # `meta`（訴願人／原處分機關）與 `case_no` 是**文件抬頭**，與 sections[] 平行（契約 §4.4）。
    # 案號單獨一個鍵而不是 meta 的一行：公文把它排在標題那一行，與其餘抬頭欄位不同排法。
    # **2026-09-13 補 `case_no`**：在那之前它從 meta 移出去了卻沒補進回應，
    # 於是匯出檔有案號、聊天室與右欄沒有——同一份草稿兩個樣子。
    # 2026-09-13 補上：在那之前只回 sections[]，於是聊天室與右欄畫出來的草稿沒有抬頭，
    # 承辦人看不出那是哪一案的決定書——而匯出的 .docx／.pdf 有。同一份草稿兩種樣子。
    return {"artifact_id": artifact_id, "title": (hit or {}).get("name") or view["title"],
            "run_id": run_id, "meta": view["meta"], "case_no": view["case_no"],
            "sections": view["sections"], "cite_count": view["cite_count"]}


@router.delete("/api/cases/{case_id}/artifacts/{artifact_id}", status_code=204)
def remove_artifact(case_id: str, artifact_id: str) -> Response:
    """從卷宗移除這份產出。**不刪 run**（契約 §4.4）：run 是執行紀錄，
    使用者把草稿從右欄移掉不代表那次執行沒發生過。"""
    try:
        ok = store.remove_item(case_id, "artifacts", artifact_id)
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"案件 {case_id} 沒有這份產出：{artifact_id}")
    return Response(status_code=204)


__all__ = ["router"]
