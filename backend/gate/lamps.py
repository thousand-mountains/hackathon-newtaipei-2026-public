"""燈號規則與 C 型結論封鎖（architecture §4.3、§8.1）——真邏輯，零 LLM。

燈號三色對應 CONSTITUTION §1 的三層誠實：

| lamp | 三層         | 什麼句子會拿到                                       |
|------|--------------|------------------------------------------------------|
| `g`  | 可驗算／有出處 | 期間引擎算出的句子、卷證直錄的事實段、引用全部在庫的句子 |
| `y`  | 有出處（需覆核）| 引用有條次異動或庫外未驗證、無引用之涵攝句             |
| `r`  | 請人工判斷    | 引用查無此號（阻擋送出）、結論段佔位（拒絕生成）        |

紅線：燈號永遠由本模組（規則）產出，模型只寫句子不寫「這句可不可信」。
"""
from __future__ import annotations

from typing import Any

from backend.config.origin_registry import TIER_HUMAN, TIER_SOURCED, TIER_VERIFIABLE
from backend.gate.citations import (
    STATE_AMENDED,
    STATE_MISSING,
    STATE_OK,
    STATE_OUT_OF_SCOPE,
    Citation,
)

WHY_ENGINE = "期間由日期規則直接驗算，攤開算式可逐步覆核，無詮釋空間。"
WHY_RECORD = "卷證原文直錄，未經改寫或生成。"
WHY_ALL_OK = "所引法條／判解全部可對回資料集，字號已驗。"
WHY_AMENDED = "引用之條次已異動，新舊條號並陳，請確認適用版本。"
WHY_OUT_OF_SCOPE = "引用超出資料集涵蓋範圍，本系統無法驗證，請人工查證後再採用。"
WHY_MISSING = "引用在資料集內查無此號，可能為誤植，已阻擋送出。"
WHY_NO_CITE = "本句未附引用，屬涵攝或評價語句，請承辦人確認其法律依據。"
WHY_UNRESOLVED_REF = "本句標註的引用編號在檢索結果中解析不到，已阻擋送出。"
WHY_PLACEHOLDER = "結論涉及法律判斷，系統不生成，僅提供交接問題清單。"


def lamp_for_states(states: list[str]) -> str:
    """一句話的燈號 = 它所有引用狀態裡最嚴重的那一個。"""
    if STATE_MISSING in states:
        return "r"
    if STATE_AMENDED in states or STATE_OUT_OF_SCOPE in states:
        return "y"
    if states and all(s == STATE_OK for s in states):
        return "g"
    return "y"


def tier_for_lamp(lamp: str, origin: str) -> str:
    if lamp == "r":
        return TIER_HUMAN
    if origin in ("engine", "rule", "static"):
        return TIER_VERIFIABLE
    return TIER_SOURCED


def why_for(lamp: str, origin: str, states: list[str], unresolved: bool = False) -> str:
    if origin == "human_required":
        return WHY_PLACEHOLDER
    if origin == "engine":
        return WHY_ENGINE
    if origin == "record":
        return WHY_RECORD
    if unresolved:
        return WHY_UNRESOLVED_REF
    if not states:
        return WHY_NO_CITE
    if STATE_MISSING in states:
        return WHY_MISSING
    if STATE_AMENDED in states:
        return WHY_AMENDED
    if STATE_OUT_OF_SCOPE in states:
        return WHY_OUT_OF_SCOPE
    return WHY_ALL_OK


def requires_human_conclusion(
    art77: dict[str, Any],
    case_type: str,
    fact_issues: list[dict[str, Any]],
    substantive_types: tuple[str, ...],
) -> tuple[bool, list[str]]:
    """C 型結論封鎖的結構性開關（architecture §4.3）。

    只吃 N2 與 N3 的輸出（兩者都在 N5 之前跑），沒有循環依賴。
    回傳 (是否封鎖, 觸發訊號清單)。
    """
    signals: list[str] = []
    substantive = bool(art77.get("requires_substantive_review")) and case_type in substantive_types
    if substantive:
        signals.append(f"程序合法且須進入實體審查（案型：{case_type}）")
    high = [i for i in fact_issues if i.get("severity") == "high"]
    for i in high:
        signals.append(f"存在高風險事實認定爭點 {i['id']}：{i['t']}")
    return (substantive or bool(high)), signals


def lamp_stats(doc: list[dict[str, Any]]) -> dict[str, int]:
    stats = {"r": 0, "y": 0, "g": 0}
    for block in doc:
        for s in block.get("ss", []):
            lamp = s.get("l")
            if lamp in stats:
                stats[lamp] += 1
    return stats


def attach_issue_refs(
    doc: list[dict[str, Any]], fact_issues: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """N6 用 N3 的 fact_issues 關鍵詞比對到句子上，事後補掛 I* ref。

    為什麼由 N6 補而不是 N5 產：讓模型認定「這句涉及哪個事實爭點」等於讓它做法律涵攝，
    那屬「請人工判斷」級（architecture §3.1 N6 說明）。比對不到就不掛，不視為錯誤。
    """
    refs: list[dict[str, Any]] = []
    for block in doc:
        for s in block.get("ss", []):
            text = s.get("t") or ""
            for issue in fact_issues:
                for kw in issue.get("matched_keywords", []) or issue.get("any_of", []):
                    if kw and kw in text:
                        s.setdefault("refs", [])
                        if issue["id"] not in s["refs"]:
                            s["refs"].append(issue["id"])
                        refs.append(
                            {"sentence_id": s.get("id"), "issue_id": issue["id"], "matched_by": f"keyword:{kw}"}
                        )
                        break
    return refs


def citation_states_for(cites: list[Citation]) -> list[str]:
    return [c.state for c in cites]
