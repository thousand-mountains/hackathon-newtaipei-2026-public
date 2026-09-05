"""分層誠實的機器強制：JSON path → origin 對照表（architecture §6.4）。

三層誠實（CONSTITUTION §1）與 origin 的對應：

| 層級         | origin 值                | 意思                               |
|--------------|--------------------------|------------------------------------|
| 可驗算       | `rule` / `engine`        | 規則引擎算出來的，攤開算式可覆核   |
| 有出處       | `retrieval` / `record`   | 從資料集或卷證檢索/直錄，附來源     |
| 請人工判斷   | `human_required`         | 系統拒絕生成，只給風險提示          |
| （中介）     | `llm`                    | 模型寫的句子，必須經 N6 守門才出場   |
| （中介）     | `static`                 | 設定檔常數（聲明、卡片名稱）        |

`llm` 永遠不准出現在燈號 `l`、`why`、`citations[].state`、`deadline.*` 這四個位置。

**這份檔案目前實際強制到什麼程度（誠實說明，別看名字就以為都做了）**：

| 東西 | 狀態 |
|---|---|
| `check_payload()` | ✅ **真的有跑**，掛在 `graph.build_payload()` 與 API／CLI 上。逐句檢查 origin 是否存在、是否在值域內，以及 `l_origin`／`why_origin`／`citations[].state_origin` 有沒有被標成 `llm`。 |
| `ORIGIN`（JSON path 對照表） | 🟡 **頂層已被測試釘住，深層仍是文件**。`registered_origin()` + `backend/tests/test_contract.py` 強制 payload 的每個**頂層** key 都要在這裡註冊（新增欄位忘了註冊會紅）。但巢狀的逐欄 JSON path（`doc[].ss[].t` 這種）還沒有程式按 path 比對，等 `scripts/contract_check.py`（qa-legal 的工作包）。 |
| `LLM_FORBIDDEN_PATHS` | ⚠️ **同上，目前未被引用**。`check_payload()` 是用 `*_origin` 欄位做等價檢查，不是走這份 path 清單。 |

不把它們刪掉是因為介面已經對外講定（architecture §6.4），刪了下一個人會重新發明；
但也不能讓 docstring 宣稱「會擋下來」而實際沒接上——那正是這個系統要防的那種謊。
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
    "llm_derived": TIER_SOURCED,  # 規則算出來的，但輸入是模型輸出——不算可驗算層
    "static": TIER_VERIFIABLE,
}

ORIGIN = {
    "case_id": "static",
    "run_id": "rule",  # 編排層產生的執行識別碼
    "state": "rule",
    "provenance.*": "static",
    "files[]": "static",  # 卷證上傳中繼資料（§6.2）
    "intake.*": "llm",  # 人工修改後由 intake_origin[field] 覆寫為 human
    "intake.auto_fields": "rule",  # 編排層：conf ≥ 門檻 的欄位名清單（§6.2）
    "intake.auto_toast": "rule",  # 模板填數字，不寫死（§6.2）
    "intake_conf.*": "llm",
    "intake_origin.*": "rule",
    "facts_excerpt[].text": "record",
    # ⚠ 標 rule 是「分類**演算法**是規則式」的意思，但它的輸入 `intake.type` 來自 N1（llm）。
    # 也就是說 case_type 實際上是 llm 衍生值，而 case_type 又餵給
    # `screen.requires_human_conclusion`（C 型結論封鎖的開關）。
    # 換句話說：**封鎖開關的上游有一個未經人工確認的模型輸出**。
    # 正確做法是接上 US-10 的人工確認表單後把 intake_origin[type] 轉成 human 再往下傳；
    # Phase 0 還沒有那道表單，所以這裡誠實標成 llm_derived，不假裝它是純規則。
    "classification.*": "rule",
    "screen.*": "rule",
    "retrieval.*": "retrieval",
    "doc[]": "static",  # 骨架是模板；句子的 origin 逐句標在 doc[].ss[].origin
    "citations[]": "rule",
    "classification.class.case_type": "llm_derived",
    "classification.class.method": "rule",
    "classification.class.law_hits": "rule",
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
    # §6.2 的頂層視圖（與 retrieval.* 同一份資料，前端左欄三分頁直接吃）
    "laws[]": "retrieval",
    "laws[].lamp": "rule",  # 檢索只給候選，燈號歸守門
    "laws[].tag": "rule",
    "cases[]": "retrieval",
    "issues[]": "rule",  # N3 事實認定爭點偵測
    "issues[].lamp": "rule",
    "issues[].tag": "rule",
    "agents[]": "static",  # 卡片名稱與角色來自 settings.AGENTS_NARRATIVE
    "agents[].out": "rule",  # 節點 NodeResult.narrative，模板填數字（§3.2）
    "agents[].logs[]": "rule",
    "token_note": "static",
    "tiers.*": "rule",  # 三層誠實視圖：依 origin 分桶，不改內容
    "history[]": "rule",  # 狀態機轉換紀錄
    "lamp_stats.*": "rule",
    "citation_counts.*": "rule",
    "issue_refs[]": "rule",
    "submit_allowed": "rule",
    "facts_excerpt[]": "record",
    "origin_violations[]": "rule",
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


def registered_origin(key: str) -> str | None:
    """查一個**頂層** payload key 在 `ORIGIN` 註冊了什麼 origin，沒註冊回 None。

    比對三種寫法：`key`（純量）、`key[]`（陣列）、`key.*`（物件）。
    `backend/tests/test_contract.py` 用它強制「payload 的每個頂層欄位都要有 origin 註冊」——
    也就是說 `ORIGIN` 從這個 commit 起**不再只是文件**，頂層那一層已經被測試釘住了
    （更深的逐欄 JSON path 比對仍待 qa-legal 的 `scripts/contract_check.py`）。
    """
    for candidate in (key, f"{key}[]", f"{key}.*"):
        if candidate in ORIGIN:
            return ORIGIN[candidate]
    return None


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
