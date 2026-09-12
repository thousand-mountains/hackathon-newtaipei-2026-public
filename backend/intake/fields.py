"""收文欄位的中文標籤。**零依賴（stdlib only），任何節點都能 import。**

## 為什麼獨立成一個模組，而不是放在 `backend/nodes/n1_extract.py`

標籤本來寫在 N1 旁邊（欄位是它抽出來的）。但 N3 也要講「缺的是送達日」——
而 `n1_extract` 頂層 `import backend.llm.client`，**N3 一旦 import 它就違反
CONSTITUTION §4「N2/N3/N4/N6 零 LLM 依賴」**（`run_all.py` 的
`scan_llm_import_graph` 是 AST 遞迴，會抓到）。

所以這裡只放「欄位叫什麼中文名」這件事實——它跟抽取、跟模型都沒有關係。

## 為什麼這些字串不能只活在前端

降級原因（`run_meta.degraded[].reason`）會端到承辦人面前。原本寫的是
`缺漏：['no']`——承辦人看到只會覺得系統壞了，**不會知道要去補案號**
（CONSTITUTION §1 分層誠實）。

## 這是第二份副本，而且刻意是

第一份在前端 `ToolOut.vue` 的 `INTAKE_LABEL`（畫收文表格用）。合併成一份要
跨語言共用資料檔、動到前端建置，2026-09-13 判定不值得。改由
`backend/tests/test_live_plumbing.py` 的
`test_intake_field_labels_agree_across_the_stack` 在共同鍵上逐字比對，
只改一邊就紅。
"""
from __future__ import annotations

FIELD_LABELS = {
    "no": "案號",
    "type": "案件類型",
    "person": "訴願人",
    "org": "原處分機關",
    "d1": "原處分日",
    "d2": "送達日",
    "d3": "收文日",
    "agent": "代理人",
    "service_method": "送達方式",
    "transit_days": "在途期間",
    "respondent_name": "原處分相對人",
}


def field_labels(fields: list[str] | tuple[str, ...]) -> str:
    """欄位鍵名 → 中文標籤的頓號串；空清單回「無」。

    查不到標籤時**原樣留下鍵名**，不猜一個中文出來——有人新增必填欄位卻忘了補
    `FIELD_LABELS` 時，訊息會難看（出現 `foo`），但不會是假的。
    """
    return "、".join(FIELD_LABELS.get(f, f) for f in fields) or "無"


__all__ = ["FIELD_LABELS", "field_labels"]
