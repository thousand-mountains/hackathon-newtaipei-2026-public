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

## ⚠️ 共用的是**解析**，不是**回應碼**——兩端刻意不一樣，不要「順手對齊」

`run` 存在但 `doc[]` 沒有任何句子時：

| 端點 | 回應 | 依據 |
|---|---|---|
| `…/artifacts/{id}/export` | **409** | 契約 §1.5 #23 錯誤碼表明文：「回 200 空檔＝失敗看起來像成功」 |
| `…/artifacts/{id}`（JSON 檢視 #21） | **200** ＋ 空的 `sections[]` | 契約 §4.4 #21 **沒有**寫這條規則 |

**這是刻意的，2026-09-13 team-lead 拍板維持現狀。** 看到兩個地方行為不一致的人很容易
「順手對齊」，然後踩掉一個刻意的設計——所以這段話寫在這裡。

為什麼不值得對齊：這個分歧**只有拿裸 `run-…` 打詳情端點時才出現**，而那條路
只有本檔的過渡後備會走。前端兩個呼叫 `#21` 的地方帶的都是 manifest 來的 `art-…`：
`store/app.js:1209` 的 `viewArtifactFull` 吃的是 `c.docs.out`（`app.js:287` 從彙整版
的 `r.artifacts` 來，＝manifest）；`app.js:773` 的 `loadDraftSections` 吃的是
chat `tool_result.artifact_id`，而那個值是 `store.record_run()` 回的——**沒有草稿時
它回 `None`**，JS 那側 `if (!out.artifactId) return` 就先擋掉了。
所以**畫面上畫不出「一份空草稿」**，沒有使用者看得到的症狀。

真要對齊的話動的是契約（要在 §4.4 #21 補一條回應碼規則），不是在這裡改實作。

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
