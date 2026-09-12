"""`artifactId` → `runId` 的解析。**契約 §4.4 說這個轉換全系統只能有一份實作**，
這裡就是那一份。零依賴（stdlib only），所以匯出端（`backend/api/export.py`）與
JSON 檢視端（`backend/api/dossier.py`）都能用同一支。

## 為什麼要獨立一個模組

2026-09-13 雲上實測抓到的分岔：同一個 `run-…` 打匯出回 409（＝認得出它是 run id、
只是那次還沒草稿），打 JSON 詳情回 404（＝根本沒認出來）。原因是後備解析只寫在
`export.py` 裡，`dossier.get_artifact()` 只翻 `manifest["artifacts"]`。
**同一個 id 在兩支端點上得到不同的答案**，而這是使用者當場看得到的不一致。

修法不是在 `dossier.py` 再抄一份——那正是契約禁止的事——而是把它抽到這裡。
放 `backend/dossier/` 而不是 `backend/api/`：`backend/tests/` 不得 import 第三方套件，
`backend/api/*` 頂層 import fastapi，放那邊就只能靠「裝了 fastapi 才驗得到」
（`redact.py` 的檔頭是同一個理由）。

## 兩條解析，順序固定

1. **主路徑**：`backend/output/cases/{case_id}/manifest.json` 的 `artifacts[]`（契約 §4.0）。
2. **後備**：`artifact_id` 本身就是 `run-…`。manifest 還沒落地時仍能檢視／匯出。
   **這是寫在契約與 OpenAPI 說明裡的公開行為，不是隱藏後門**；manifest 一旦有了，
   它永遠優先。

## 錯誤訊息不吐容器內路徑

訊息裡要指得出「查過哪一份檔」，但吐 `/app/backend/output/cases/…` 等於把部署佈局
印在承辦人螢幕上。所以對外一律用 `describe_manifest()` 的相對描述
（`cases/{case_id}/manifest.json`），絕對路徑只留在伺服器 log。
"""
from __future__ import annotations

import json
import pathlib

from backend.config.settings import CASES_DIR

MANIFEST_NAME = "manifest.json"


class ArtifactRunNotFound(FileNotFoundError):
    """這份產出對不到任何執行紀錄。

    與「run 存在但 `doc[]` 還沒有句子」（那是 409）**不是同一件事**，
    兩者合成一句的話使用者分不出「打錯 id」與「還沒生成草稿」。
    """


def manifest_path(case_id: str, cases_dir: pathlib.Path | None = None) -> pathlib.Path:
    return (cases_dir or CASES_DIR) / case_id / MANIFEST_NAME


def describe_manifest(case_id: str) -> str:
    """對外訊息用的檔案描述。**相對路徑**——理由見檔頭最後一段。"""
    return f"cases/{case_id}/{MANIFEST_NAME}"


def run_id_from_manifest(
    case_id: str,
    artifact_id: str,
    cases_dir: pathlib.Path | None = None,
) -> str | None:
    """manifest 裡這個 artifact 對應的 run_id；manifest 或該筆不存在回 None。

    manifest 讀不動（壞掉的 JSON）時也回 None 走後備，而不是 500：
    書籤檔壞掉不該讓「我只是想看／下載草稿」整條路斷掉。
    """
    p = manifest_path(case_id, cases_dir)
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


def resolve_run_id(
    case_id: str,
    artifact_id: str,
    cases_dir: pathlib.Path | None = None,
) -> str:
    """manifest 優先，`run-…` 後備。兩條都不成立就 `ArtifactRunNotFound`。"""
    run_id = run_id_from_manifest(case_id, artifact_id, cases_dir)
    if run_id:
        return run_id
    if artifact_id.startswith("run-"):
        return artifact_id
    raise ArtifactRunNotFound(
        f"案件 {case_id} 的產出 {artifact_id} 找不到對應的執行紀錄。"
        f"（已查 {describe_manifest(case_id)}；artifact_id 若直接帶 run-… 亦可）"
    )


__all__ = [
    "ArtifactRunNotFound",
    "MANIFEST_NAME",
    "describe_manifest",
    "manifest_path",
    "resolve_run_id",
    "run_id_from_manifest",
]
