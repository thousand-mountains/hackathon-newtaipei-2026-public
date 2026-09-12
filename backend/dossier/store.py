"""卷宗書籤持久化：`backend/output/cases/{case_id}/manifest.json`，一案一檔。

形狀（契約 v2 §4.0，四份清單放**同一個**物件）：

    {"case_id", "name", "created_at", "latest_run_id",
     "files":      [{id,name,ext,note,readable}],
     "laws":       [{id,t,src,note,verified,relevance,body_cached}],
     "references": [{id,t,src,note,score,provenance,doc_kind,full_cached}],
     "artifacts":  [{id,name,kind,note,created_at,run_id}]}

為什麼單檔不拆四檔：開案首載本來就要一次全拿（`GET /api/cases/{id}` 彙整版），
單檔一次讀完最省。增刪＝read-modify-write。

**三條已知限制。寫在這裡，不要讓人以為它是資料庫**（2026-09-12 Ci 拍板接受）：

1. **不原子——這一條已修。** 本檔一律 tmp + `os.replace`（見 `_atomic_write_json`）。
   `orchestrator/runstore.py:36` 的 `p.write_text` 是**既有的**債，**刻意不在這次動它**：
   那是已驗綠的續跑路徑，決賽期間不為了一個一般性的改善去動它。
2. **多副本會分裂。** 兩個 process 各自 read-modify-write 同一份 manifest，後寫的贏。
   chat session 本來就是進程內字典（`backend/api/chat.py`）、ECS 現在單台，不是新債。
3. **容器重啟就沒了。** `backend/output/` 是容器本地磁碟。`runs` 已經是同一個問題，
   **manifest 與 runs 一起接受，不搬 S3**——現在改儲存層等於在已驗綠的路徑上動刀。

`case_id` 走白名單 regex 才拼路徑（沿用 `runstore._path` 的做法）：這個值會從 HTTP path
進來，不擋就等於讓呼叫端指定任意檔案路徑（`../../etc/x`）。格式不合直接 `ValueError`，
**不做「清洗後照樣讀」**——清洗是猜對方想讀什麼，拒絕才是誠實。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
from typing import Any, Callable

from backend.config.settings import CASES_DIR, SYNTHETIC_DIR
from backend.dossier import runlink
from backend.intake.documents import route_documents
from backend.intake.uploads import UPLOADS_DIR

#: 只認本系統自己產的兩種前綴（與 `orchestrator.graph.load_case` 同一條紅線：
#: 真實競賽資料不由本流程讀取，連 id 都不接受）。
CASE_ID_RE = re.compile(r"^(?:upload-[0-9a-f]{12}|synthetic-[A-Za-z0-9_\-]{1,64})$")

#: 四組卷宗成員。**順序就是右欄群組的順序**，不要在別處再寫一份。
GROUPS = ("files", "laws", "references", "artifacts")


class CaseManifestNotFound(FileNotFoundError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def case_dir(case_id: str, cases_dir: pathlib.Path | None = None) -> pathlib.Path:
    if not CASE_ID_RE.match(case_id or ""):
        raise ValueError(
            f"案件 id 格式不合法：{case_id!r}。只接受 upload-<12 碼 hex> 或 synthetic-<名稱>。"
        )
    return (cases_dir or CASES_DIR) / case_id


def manifest_path(case_id: str, cases_dir: pathlib.Path | None = None) -> pathlib.Path:
    return case_dir(case_id, cases_dir) / "manifest.json"


def _atomic_write_json(
    path: pathlib.Path,
    obj: Any,
    dump: Callable[[Any, Any], None] = json.dump,
) -> pathlib.Path:
    """tmp + `os.replace` 落檔。**中途失敗不留半份檔。**

    `p.write_text(json.dumps(...))` 為什麼不夠：那是「先整份序列化成字串、再一次寫」，
    序列化失敗的確不會弄髒檔案，但**寫入本身失敗（磁碟滿、被砍、序列化器邊寫邊爆）
    會把目標檔截成半份**，而且下一次讀會得到 `JSONDecodeError`——那時卷宗就沒了。
    tmp + `os.replace` 讓「舊檔完好」與「新檔完整」之間沒有第三種狀態：
    `os.replace` 在同一個檔案系統上是 atomic rename。

    `dump` 是**測試接縫**：正式路徑永遠是 `json.dump`，測試注入一個「寫一半就爆」的
    假 dump，用來證明上一段講的事真的成立（見 `backend/tests/test_dossier.py`）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            dump(obj, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # 清掉半份的 tmp。**不要讓它留在目錄裡**：下次 mkstemp 會再開一個新的，
        # 殘骸會越積越多，而且看起來很像「有備份」，其實是半份壞檔。
        tmp.unlink(missing_ok=True)
        raise
    return path


def _dump_pretty(obj: Any, f: Any) -> None:
    json.dump(obj, f, ensure_ascii=False, indent=1)


def _file_entry(name: str, ext: str, note: str, readable: bool) -> dict[str, Any]:
    """卷證一筆。`id` 由檔名 hash 而來：檔名可能含中文、空白、括號，
    直接當 URL path segment 會在不同層（前端 encode／ALB／FastAPI）各解一次，
    很容易對不起來。hash 短、穩定、path-safe，而顯示用的原名在 `name` 裡。"""
    return {
        "id": "f-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10],  # noqa: S324 - 非安全用途
        "name": name,
        "ext": ext,
        "note": note,
        "readable": readable,
    }


#: `route_documents` 的 kind → 是否讀得到。`unreadable` 以外都讀得到。
#: 有文字層、讀得出字的 kind。**`pdf_visual` 不在裡面，這是刻意的。**
#:
#: `pdf_visual` ＝掃描影像 PDF，`pdftotext` 一個字都抽不出來。管線仍然讀得到它
#: （整份 PDF 餵給模型視覺讀取），但契約 §4.1 與 §6 兩處都要求
#: **掃描影像回 `readable:false`、UI 標「無法辨讀」**，理由是「目前不做 OCR，
#: 不要寫成支援」。標成 readable 會讓上傳文案變成「已上傳，可直接改」
#: ——那是在暗示一個我們沒有的能力。
#:
#: 兩件事分開講、不合成一句：`readable` 說的是**有沒有文字層**，
#: `note` 說的是**為什麼**（`route_documents` 給的原因，例「中文比例 0.0 < 0.6，
#: 改以視覺讀取」）。承辦人要看得到這份卷證是「模型看圖說話」讀來的。
_TEXT_LAYER_KINDS = ("txt", "docx_text", "pdf_text")


def _readable(kind: str) -> bool:
    return kind in _TEXT_LAYER_KINDS


def _files_from_case(case_id: str, uploads_dir: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """從實體卷證推 `files[]`。**推不出來就回空，不編造。**

    上傳案：讀 `case.json` 的 `files[]`，再用 `route_documents` 判 readable 與原因。
    合成案：讀合成測資檔的 `files[]`（`{n,s,x}`）。合成案的卷證文字是**內嵌**在測資裡的，
    沒有實體檔可以路由，所以 `readable` 一律 True 並在 `note` 標明是合成卷證——
    **不拿 `route_documents` 去掃一個不存在的目錄**，那會回空、把兩份卷證靜默吃掉。
    """
    if not case_id.startswith("upload-"):
        p = SYNTHETIC_DIR / f"{case_id}.json"
        if not p.exists():
            return []
        meta = json.loads(p.read_text(encoding="utf-8"))
        out: list[dict[str, Any]] = []
        for f in meta.get("files") or []:
            n = str(f.get("n") or "")
            if not n:
                continue
            ext = pathlib.Path(n).suffix.lower().lstrip(".")
            out.append(_file_entry(n, ext, str(f.get("x") or "合成測資內嵌卷證"), True))
        return out
    d = (uploads_dir or UPLOADS_DIR) / case_id
    if not (d / "case.json").exists():
        return []
    out: list[dict[str, Any]] = []
    for doc in route_documents(d):
        ext = pathlib.Path(doc.n).suffix.lower().lstrip(".")
        readable = _readable(doc.kind)
        note = "；".join(doc.notes)
        if not readable and not note:
            # **`readable:false` 一定要說得出原因。** 只說「無法辨讀」而不說為什麼，
            # 承辦人會以為是系統壞了，然後重傳三次同一份檔（proposal B2.2）。
            note = f"沒有可抽取的文字層（route_documents 判為 {doc.kind}）。"
        out.append(_file_entry(doc.n, ext, note, readable))
    return out


def _case_label(case_id: str, uploads_dir: pathlib.Path | None = None) -> tuple[str, str]:
    """回 `(name, created_at)`。讀不到就用 case_id 當名字、當下時間當建立時間，
    **並且不假裝那是原始建立時間**——`created_at` 只有從 `case.json` 讀到 `uploaded_at`
    時才是真的建立時間，其餘情況它就是「這份 manifest 被建出來的時間」。
    """
    if case_id.startswith("upload-"):
        p = (uploads_dir or UPLOADS_DIR) / case_id / "case.json"
        if p.exists():
            meta = json.loads(p.read_text(encoding="utf-8"))
            return str(meta.get("label") or case_id), str(meta.get("uploaded_at") or _now())
        return case_id, _now()
    p = SYNTHETIC_DIR / f"{case_id}.json"
    if p.exists():
        meta = json.loads(p.read_text(encoding="utf-8"))
        return str(meta.get("label") or meta.get("title") or case_id), _now()
    return case_id, _now()


def default_manifest(
    case_id: str,
    uploads_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    """從既有案件推一份 manifest。**只推得出來的欄位才填**，其餘留空 list／None。"""
    name, created_at = _case_label(case_id, uploads_dir)
    return {
        "case_id": case_id,
        "name": name,
        "created_at": created_at,
        "latest_run_id": None,
        "files": _files_from_case(case_id, uploads_dir),
        "laws": [],
        "references": [],
        "artifacts": [],
    }


def _normalise(raw: dict[str, Any], case_id: str) -> dict[str, Any]:
    """舊版／手改過的檔案缺欄位時補齊，**不丟棄任何既有內容**。"""
    m = dict(raw)
    m["case_id"] = case_id
    m.setdefault("name", case_id)
    m.setdefault("created_at", _now())
    m.setdefault("latest_run_id", None)
    for g in GROUPS:
        v = m.get(g)
        m[g] = list(v) if isinstance(v, list) else []
    return m


def load(
    case_id: str,
    cases_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    """讀回 manifest。**不存在就 raise**，要「不存在就建」請用 `ensure`。"""
    p = manifest_path(case_id, cases_dir)
    if not p.exists():
        raise CaseManifestNotFound(f"找不到案件卷宗 {case_id}（{p}）")
    return _normalise(json.loads(p.read_text(encoding="utf-8")), case_id)


def save(
    manifest: dict[str, Any],
    cases_dir: pathlib.Path | None = None,
    dump: Callable[[Any, Any], None] = _dump_pretty,
) -> pathlib.Path:
    case_id = str(manifest.get("case_id") or "")
    return _atomic_write_json(manifest_path(case_id, cases_dir), manifest, dump=dump)


def exists(case_id: str, cases_dir: pathlib.Path | None = None) -> bool:
    return manifest_path(case_id, cases_dir).exists()


def ensure(
    case_id: str,
    cases_dir: pathlib.Path | None = None,
    uploads_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    """有就讀、沒有就依既有案件推一份並落地。建案與首次開案都走這裡。"""
    try:
        return load(case_id, cases_dir)
    except CaseManifestNotFound:
        m = default_manifest(case_id, uploads_dir)
        save(m, cases_dir)
        return m


def list_manifests(cases_dir: pathlib.Path | None = None) -> dict[str, dict[str, Any]]:
    """所有已落地的 manifest，key 是 case_id。壞掉的檔案**跳過但不靜默**——
    回傳裡不會有它，呼叫端看得到 case 少一筆；這裡不 raise 是因為一份壞檔
    不該讓整個案件清單打不開。"""
    d = cases_dir or CASES_DIR
    if not d.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for sub in sorted(d.iterdir()):
        if not sub.is_dir() or not CASE_ID_RE.match(sub.name):
            continue
        try:
            out[sub.name] = load(sub.name, cases_dir)
        except (json.JSONDecodeError, OSError):
            continue
    return out


# ── 變更操作：全部 read-modify-write，全部走 `save`（＝原子寫） ──────────────


def rename(case_id: str, name: str, cases_dir: pathlib.Path | None = None) -> dict[str, Any]:
    if not (name or "").strip():
        raise ValueError("案件名稱不得為空白")
    m = ensure(case_id, cases_dir)
    m["name"] = name.strip()
    save(m, cases_dir)
    return m


class CaseNotDeletable(ValueError):
    """這個案子不能刪（合成測資）。與「找不到」不是同一件事。"""


def delete_case(case_id: str, cases_dir: pathlib.Path | None = None,
                uploads_dir: pathlib.Path | None = None) -> bool:
    """刪掉這個案子：manifest 目錄 ＋ `output/uploads/{case_id}` 的實體卷證。

    **為什麼卷證也要刪**（2026-09-12 實跑抓到）：原本只刪 manifest，理由寫的是
    「移出卷宗 ≠ 銷毀證據」。那句話對的是**單一卷證的移除**（`DELETE …/files/{id}`），
    套到整個案子上就錯了——`list_cases()` 是掃 `output/uploads/` 列出來的，
    只刪 manifest 的話案子會在下一次 `GET /api/cases` **原地復活**，
    而且名字變回預設值。使用者按了刪除、東西還在，那不是「保守」，那是壞掉。

    **不刪 runs**：那是執行紀錄、以 run_id 為鍵、不在 UI 的任何清單裡，
    而且刪掉會讓已經匯出的草稿再也查不回來源。

    合成測資（`synthetic-`）**拒絕刪除**並說明理由：它是進 git 的測試案例，
    刪了會讓 `run_all.py` 紅，而且下次 `git checkout` 又回來——
    做一個註定失效的動作比直接說不能刪更糟。
    """
    if case_id.startswith("synthetic-"):
        raise CaseNotDeletable(
            f"{case_id} 是合成測資（進 git 的測試案例），不提供刪除。"
            f"要清掉它的卷宗內容請逐項移出，或換一個上傳案操作。"
        )
    d = case_dir(case_id, cases_dir)          # 也順便驗 case_id 格式
    up = (uploads_dir or UPLOADS_DIR) / case_id
    if not (d.exists() or up.exists()):
        return False
    if d.exists():
        shutil.rmtree(d)
    if up.exists():
        shutil.rmtree(up)
    return True


def add_items(
    case_id: str,
    group: str,
    items: list[dict[str, Any]],
    cases_dir: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    """加入清單，**依 `id` 去重**（重複加入同一份法規不會變成兩筆）。回傳「這次真的新增的」。

    已存在的 id 一律保留舊的那筆，不覆蓋：舊的可能已經被使用者加過 `note`，
    而新的那筆只是搜尋結果，覆蓋等於把人寫的東西吃掉。
    """
    if group not in GROUPS:
        raise ValueError(f"未知的卷宗群組 {group!r}，只有 {', '.join(GROUPS)}")
    m = ensure(case_id, cases_dir)
    have = {str(x.get("id")) for x in m[group]}
    added = [x for x in items if str(x.get("id")) not in have]
    # 同一次請求裡也可能帶重複的 id
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for x in added:
        i = str(x.get("id"))
        if i in seen:
            continue
        seen.add(i)
        deduped.append(x)
    if deduped:
        m[group] = m[group] + deduped
        save(m, cases_dir)
    return deduped


def remove_item(
    case_id: str,
    group: str,
    item_id: str,
    cases_dir: pathlib.Path | None = None,
) -> bool:
    """移出一筆。回 False 代表本來就不在清單裡（呼叫端翻成 404）。"""
    if group not in GROUPS:
        raise ValueError(f"未知的卷宗群組 {group!r}，只有 {', '.join(GROUPS)}")
    m = ensure(case_id, cases_dir)
    kept = [x for x in m[group] if str(x.get("id")) != item_id]
    if len(kept) == len(m[group]):
        return False
    m[group] = kept
    save(m, cases_dir)
    return True


def set_latest_run(
    case_id: str,
    run_id: str,
    cases_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    """記下最後一次成功的 run。chat 要接哪一份 run payload 看這個欄位（契約 §4.0）。"""
    m = ensure(case_id, cases_dir)
    m["latest_run_id"] = run_id
    save(m, cases_dir)
    return m


def artifact_id_for(run_id: str) -> str:
    """草稿 artifact 的 id **由 run_id 決定，不是隨機**。

    同一次 run 重複登記要落在同一筆，否則右欄會出現三份一模一樣的
    「訴願決定書草稿」而它們其實是同一份。抽成具名函式是因為 `record_run` 也要算它
    ——兩處各寫一次 sha1 就會有兩種 id。
    """
    return "art-" + hashlib.sha1(run_id.encode("utf-8")).hexdigest()[:10]  # noqa: S324


def _mark_law_retrieval(case_id: str, payload: dict[str, Any],
                        cases_dir: pathlib.Path | None = None) -> None:
    """B4.3：把本案 `laws[]` 逐筆標上「檢索命中／未命中」（契約 §3.5.2 末段）。

    手動挑的法規是當**查詢詞**餵回 N4 的，所以「挑了」不等於「會進草稿」。
    契約原文是「右欄該項標『檢索未命中，未進入草稿』」——**右欄**，
    表示關掉對話再打開狀態還要在，所以寫進 manifest，而不是只在 chat 回合裡講一次
    （chat 那半是 `llm/chat.py:unmatched_picks`，兩半都要）。
    比對規則不在這裡，見 `backend/dossier/runlink.py`。

    接在 `record_run` 裡而不是讓兩個呼叫端各自呼叫：理由與 `record_run` 本身
    下沉到這裡是同一個——複製的那份遲早分岔，而分岔的時候沒有症狀。
    """
    m = ensure(case_id, cases_dir)
    marked = runlink.classify_law_retrieval(m["laws"], list((payload or {}).get("laws") or []))
    if marked != m["laws"]:
        m["laws"] = marked
        save(m, cases_dir)


def record_run(
    case_id: str,
    payload: dict[str, Any],
    title: str = "訴願決定書草稿",
    cases_dir: pathlib.Path | None = None,
) -> str | None:
    """把一次**成功的** run 記進卷宗：`latest_run_id` ＋（真的有句子時）一筆 artifact。

    回傳登記到的 `artifact_id`；沒有草稿可登記就 `None`。

    **兩條路徑共用這一支**：`POST /cases/{id}/runs`（`backend/api/app.py`）與 chat 的
    pipeline 工具（`backend/orchestrator/chat_bridge.py`）。2026-09-12 整合時發現
    登記原本只接在 HTTP 那條路上，而契約 §0.1 說**前端只打 chat、永遠不會打 `/runs`**
    ——唯一會登記的路徑正好是前端不會走的那一條。右欄「答辯書與產出」因此永遠是空的。
    修法是把本體下沉到這裡讓兩邊都呼叫，**不是在 chat 那側複製一份**：
    複製的那份遲早會跟這裡分岔，而分岔的時候沒有症狀。

    收 `payload`（`build_payload()` 的輸出）而不是 `CaseState`：卷宗層不該把編排層
    拉進 import 圖，而兩個呼叫端本來就都已經有 payload 在手上。

    **判準是「`doc[]` 裡真的有句子」，不是「這個案子有沒有被封鎖」**（2026-09-12 實測更正）：
    原本寫的是「C 型案不作成草稿所以不登記」，那是錯的——六節點的 `run_case`
    對 `synthetic-blocked-01` 一樣產出事實／理由／期間計算／主文四段，
    差別在 `submit_allowed=false` 與 `blockers[]`，不在有沒有文件。
    （不作成結論的是 chat 的 `generate_decision_draft` 工具那條路徑，不是這裡。）
    被封鎖的草稿**要**登記：承辦人正是要讀它、接手完成結論。藏起來才是幫倒忙。
    真正要防的只有「沒有任何句子卻登記一筆」——右欄長出一份點開是空的草稿。

    寫檔失敗不往上丟：run 本身已經成功而且已經存進 runstore，
    讓一次書籤寫入失敗把執行結果說成失敗是本末倒置。失敗要印出來，不吞。
    """
    run_id = str((payload or {}).get("run_id") or "")
    if not run_id:
        return None
    try:
        set_latest_run(case_id, run_id, cases_dir)
        _mark_law_retrieval(case_id, payload, cases_dir)
        doc = (payload or {}).get("doc") or []
        if not any(b.get("ss") for b in doc):
            return None
        record_draft_artifact(case_id, run_id, title,
                              note=f"由 {run_id} 產出", cases_dir=cases_dir)
        # `record_draft_artifact` 對已登記過的同一個 run 回 None（冪等），
        # 但呼叫端要的是「這份草稿的 id」而不是「這次有沒有新增」——
        # id 由 run_id 決定，所以照樣算得出來，不必分兩種回傳。
        return artifact_id_for(run_id)
    except Exception as e:  # noqa: BLE001 — 見 docstring：書籤寫失敗不等於 run 失敗
        print(f"[warn] 卷宗登記 run {run_id} 失敗：{type(e).__name__}: {e}",
              file=sys.stderr)
        return None


def record_draft_artifact(
    case_id: str,
    run_id: str,
    title: str,
    note: str = "",
    cases_dir: pathlib.Path | None = None,
) -> dict[str, Any] | None:
    """把一次 run 產出的草稿登記成 artifact。**同一個 run 只登記一次。**

    `id` 由 run_id 決定（不是隨機）：同一次 run 重複登記要落在同一筆，
    否則右欄會出現三份一模一樣的「訴願決定書草稿 v1」而它們其實是同一份。
    """
    art_id = artifact_id_for(run_id)
    m = ensure(case_id, cases_dir)
    if any(str(x.get("id")) == art_id for x in m["artifacts"]):
        return None
    entry = {
        "id": art_id,
        "name": title,
        "kind": "draft",
        "note": note,
        "created_at": _now(),
        "run_id": run_id,
    }
    m["artifacts"] = m["artifacts"] + [entry]
    save(m, cases_dir)
    return entry
