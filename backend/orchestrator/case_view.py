"""彙整版（`GET /api/cases/{id}`）要從最後一次 run 帶出來的四塊（契約 v2 §3.3）。

## 為什麼在這裡而不是在 `backend/api/dossier.py`

`backend/api/*` 頂層 import fastapi，而 `backend/tests/run_all.py` 要能在一台
**只有 stdlib 的 python** 上跑起來（`backend/tests/harness.py` 檔頭的 Phase 0 紅線）。
邏輯留在 api 層的話，測試只能用 AST 讀原始碼——而 AST 讀不出「這個對應關係算得對不對」。
2026-09-13 實際踩到：測試 import 了 `backend.api.dossier`，**整支 `run_all.py` 在
沒有 fastapi 的直譯器上 import 就崩**，不是某條測試紅，是整套跑不起來。
這跟 `artifact_sections.py` 放在 orchestrator 而不是 api 層是同一個理由。

## 這四塊是什麼、為什麼漏掉會出事

契約 §3.3：跑完 extract 之後，前端打彙整版取
案由（`intake`）／事實摘錄（`facts_excerpt`）／爭點（`issues`）／程序審查（`screen`）。

漏掉的後果是**一顆死鈕**：使用者問期限時後端回 `redirect`，它的 CTA 要把畫面捲去
「程序審查算式」——而那個算式（`screen.deadline.steps`）沒有被送到前端，
畫面上根本沒有東西可捲。**那顆 CTA 是 CONSTITUTION §4 的唯一出口**
（期限不給模型算的天數，改給規則引擎逐步算出來的），四塊不送，紅線的下半截就是空的。
"""
from __future__ import annotations

import sys
from typing import Any

from backend.orchestrator.graph import build_payload
from backend.orchestrator.runstore import load_run

#: 彙整版要帶的四塊。**唯一的事實來源**，api 層與測試都讀這一份。
RUN_BLOCKS = ("intake", "facts_excerpt", "issues", "screen")


def blocks_from_latest_run(run_id: str | None) -> dict[str, Any]:
    """從 `latest_run_id` 取那四塊。**取不到就四個都 `None`，不讓彙整版整支失敗。**

    為什麼不 raise：右欄（卷證／法規／案例／產出）的資料在 manifest 裡，與 run 無關。
    run 讀不到就讓整支 500 的話，使用者連自己挑進卷宗的東西都看不到了——
    用一個區塊的失敗換掉整個畫面，不划算。

    **`SCREENED` 的 run 沒有草稿，但這四塊都有**（實測：`to_node="n3"` 的 payload
    `intake`／`facts_excerpt`／`issues`／`screen` 齊全，只有 `doc`／`citations` 是空 list）。
    所以**不要因為取不到 `doc` 就整組回空**——那會把「還沒生草稿」誤演成「什麼都沒有」，
    而程序審查的算式正好就在 `screen` 裡。

    **鍵一律都在，值可能是 `null`**（與契約 §2.3「值為 null 也要送」、§4.4 匯出標頭
    「三個標頭一律都帶，包含 0 與空字串」同一條紀律）：省略鍵會讓前端拿到 `undefined`
    而不是 `null`，兩者在 JS 裡要寫不同分支。

    **「還沒跑過」與「跑過但讀不到」怎麼分**：看 `case.latest_run_id`。
    它是 `null` ＝ 還沒跑過（正常）；它有值而這四塊是 `null` ＝ run 存在但讀不回來
    （異常，伺服器端會印警告）。不另外加鍵，因為這個區分本來就推導得出來。
    """
    empty: dict[str, Any] = {k: None for k in RUN_BLOCKS}
    if not run_id:
        return empty
    try:
        payload = build_payload(load_run(run_id))
    except Exception as e:  # noqa: BLE001 — 見 docstring：不讓它弄掉整個彙整版
        print(f"[warn] 彙整版讀不到 run {run_id}：{type(e).__name__}: {e}", file=sys.stderr)
        return empty
    return {k: payload.get(k) for k in RUN_BLOCKS}


__all__ = ["RUN_BLOCKS", "blocks_from_latest_run"]
