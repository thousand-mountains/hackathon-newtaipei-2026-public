"""分層誠實的機器強制：JSON path → origin 對照表（architecture §6.4）。

三層誠實（CONSTITUTION §1）與 origin 的對應：

| 層級         | origin 值                | 意思                               |
|--------------|--------------------------|------------------------------------|
| 可驗算       | `rule` / `engine`        | 規則引擎算出來的，攤開算式可覆核   |
| 有出處       | `retrieval` / `record`   | 從資料集或卷證檢索/直錄，附來源     |
| 請人工判斷   | `human_required`         | 系統拒絕生成，只給風險提示          |
| （中介）     | `llm`                    | 模型寫的句子，必須經 N6 守門才出場   |
| （中介）     | `static`                 | 設定檔常數（聲明、卡片名稱）        |

`llm` 永遠不准出現在燈號 `l`、`why`、`citations[].state`、`deadline.*` 這四個位置——
`check_payload()` 會把它擋下來。
"""
from __future__ import annotations

from typing import Any

# 三層誠實的正式標籤（輸出 JSON 用這三個 key 分層）
TIER_VERIFIABLE = "可驗算"
TIER_SOURCED = "有出處"
TIER_HUMAN = "請人工判斷"

ORIGIN_TO_TIER = {
    "rule": TIER_VERIFIABLE,
    "engine": TIER_VERIFIABLE,
    "retrieval": TIER_SOURCED,
    "record": TIER_SOURCED,
    "human_required": TIER_HUMAN,
    "llm": TIER_SOURCED,  # 模型句子必須帶引用才出得去；沒帶引用的會被 N6 打紅燈
    "static": TIER_VERIFIABLE,
}

ORIGIN = {
    "provenance.*": "static",
    "intake.*": "llm",  # 人工修改後由 intake_origin[field] 覆寫為 human
    "intake_conf.*": "llm",
    "facts_excerpt[].text": "record",
    "classification.class.*": "rule",
    "classification.knn[]": "retrieval",
    "classification.agreement.*": "rule",
    "screen.deadline.*": "rule",
    "screen.art77.*": "rule",
    "screen.fact_issues[]": "rule",
    "screen.requires_human_conclusion": "rule",
    "retrieval.laws[].q": "retrieval",
    "retrieval.laws[].lamp": "rule",
    "retrieval.cases[]": "retrieval",
    "retrieval.retrieval_meta.*": "rule",
    "draft.slots[].t": "llm",
    "doc[].ss[].t": "llm",
    "doc[].ss[].l": "rule",
    "doc[].ss[].why": "rule",
    "doc[].ss[].refs[]": "rule",
    "citations[].state": "rule",
    "handoff.*": "rule",
    "blockers[]": "rule",
    "run_meta.*": "rule",
}

# 這四個 JSON path 永遠不准是模型產的（architecture §6.4）
LLM_FORBIDDEN_PATHS = (
    "doc[].ss[].l",
    "doc[].ss[].why",
    "citations[].state",
    "screen.deadline",
)


def tier_of(origin: str) -> str:
    """把 origin 映射到三層誠實其中一層。未知 origin 一律降級到「請人工判斷」。"""
    return ORIGIN_TO_TIER.get(origin, TIER_HUMAN)


def check_payload(payload: dict[str, Any]) -> list[str]:
    """掃輸出 payload，回傳違規清單（空 list = 通過）。

    檢查兩件事：
    1. 每個句子都有 origin，而且 origin 在已知值域內。
    2. 燈號 `l` 與 `why` 不得標為 llm 產出。
    """
    violations: list[str] = []
    for block in payload.get("doc", []):
        for s in block.get("ss", []):
            sid = s.get("id", "?")
            origin = s.get("origin")
            if origin is None:
                violations.append(f"doc[].ss[{sid}] 缺 origin 標記")
                continue
            if origin not in ORIGIN_TO_TIER:
                violations.append(f"doc[].ss[{sid}] origin={origin!r} 不在已知值域")
            if s.get("l_origin") == "llm":
                violations.append(f"doc[].ss[{sid}].l 標為 llm 產出（燈號永遠不准是模型產的）")
            if s.get("why_origin") == "llm":
                violations.append(f"doc[].ss[{sid}].why 標為 llm 產出")
    for c in payload.get("citations", []):
        if c.get("state_origin") == "llm":
            violations.append(f"citations[{c.get('raw')}].state 標為 llm 產出")
    return violations
