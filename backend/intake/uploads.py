"""上傳案件：`backend/output/uploads/upload-<id>/{原檔…, case.json}`。output/ 已 gitignored（CONSTITUTION §6）。

上傳案**沒有** extraction／draft_fixture／case_digest：它只能在 bedrock 模式跑，
fixture 模式會被 `run_case` 明確拒絕，不會靜默回一份重播結果冒充現場抽取。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re

from backend.config.settings import OUTPUT_DIR
from backend.intake.documents import route_documents

UPLOADS_DIR = OUTPUT_DIR / "uploads"
ALLOWED_SUFFIXES = (".pdf", ".txt")
MAX_BYTES = 20 * 1024 * 1024
_SAFE = re.compile(r"[^\w.\-（）()]+")

PROVENANCE_UPLOADED = {
    "kind": "uploaded",
    "note": "本案卷證由承辦人上傳，抽取結果為模型即時產出，未經人工確認前不得用於解除結論封鎖。",
    "banner": "上傳案件：卷證來自使用者上傳，內容未進 git、未離開本服務所在環境。",
}


def _safe_name(n: str) -> str:
    """只取 basename（擋 `../` 逃逸），再把路徑不友善的字元換掉。中文屬 \\w，會原樣保留。"""
    n = pathlib.Path(n).name
    return _SAFE.sub("_", n) or "file"


def save_upload(files: list[tuple[str, bytes]], uploads_dir: pathlib.Path | None = None) -> dict:
    """存檔並寫 case.json，回傳中繼資料（含 case_id）。

    case_id 由「檔名＋內容」的 sha256 前 12 碼組成：同一批卷證重複上傳會落在同一個案件，
    不會每按一次就多一個目錄。
    """
    if not files:
        raise ValueError("至少要上傳一個檔案")
    h = hashlib.sha256()
    for name, data in files:
        if pathlib.Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"只接受 {ALLOWED_SUFFIXES}，實得 {name!r}")
        if len(data) > MAX_BYTES:
            raise ValueError(f"{name!r} 超過 {MAX_BYTES} bytes")
        h.update(name.encode("utf-8"))
        h.update(data)
    case_id = f"upload-{h.hexdigest()[:12]}"
    d = (uploads_dir or UPLOADS_DIR) / case_id
    d.mkdir(parents=True, exist_ok=True)
    meta_files = []
    for name, data in files:
        p = d / _safe_name(name)
        p.write_bytes(data)
        meta_files.append({"n": p.name, "s": f"{len(data)} bytes", "x": "承辦人上傳"})
    meta = {
        "case_id": case_id,
        "id": case_id,
        "label": f"上傳案件 {case_id}",
        "provenance": PROVENANCE_UPLOADED,
        "files": meta_files,
        "uploaded_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    }
    (d / "case.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def load_upload_case(case_id: str, uploads_dir: pathlib.Path | None = None) -> dict:
    """讀回 case.json 並即時路由卷證，形狀對齊合成案例檔（少了 fixture 專屬區塊）。

    id 走白名單 regex：這條路徑會被拿去組檔案路徑，不驗格式就等於開一個目錄穿越。
    """
    if not re.match(r"^upload-[0-9a-f]{12}$", case_id or ""):
        raise ValueError(f"上傳案 id 格式不合法：{case_id!r}")
    d = (uploads_dir or UPLOADS_DIR) / case_id
    if not (d / "case.json").exists():
        raise FileNotFoundError(f"找不到上傳案 {case_id}")
    meta = json.loads((d / "case.json").read_text(encoding="utf-8"))
    meta["documents"] = [doc.as_dict() for doc in route_documents(d)]
    return meta


def list_upload_cases(uploads_dir: pathlib.Path | None = None) -> list[str]:
    d = uploads_dir or UPLOADS_DIR
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "case.json").exists())
