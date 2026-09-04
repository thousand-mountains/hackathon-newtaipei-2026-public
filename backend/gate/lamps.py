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
WHY_CONCLUSION_LEAK = "結論段已封鎖，但本句出現主文型語句。實質結論不得因為換個槽位就繞過封鎖，已阻擋送出。"


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

    # Fail-safe：**案型辨識不出來、而且程序上也沒有可直接算出的不受理事由時，一律封鎖結論。**
    #
    # 這是對抗審查打出來的洞：原本只要把 case_type 換成系統不認得的值，
    # requires_human_conclusion 就從 True 變 False——「分類失敗」反而變成「解除封鎖」。
    # 方向完全相反：分類不出來代表系統**更**不了解這個案子，應該更保守。
    #
    # 為什麼要加「程序上沒有不受理事由」這個條件：逾期不受理（77-2）是期間引擎直接算出來的，
    # 屬可驗算層，那種案子不需要靠案型判斷就能寫結論（synthetic-ordinary-01 就是）。
    unknown_type = case_type not in substantive_types
    procedurally_resolved = bool(art77.get("clause"))
    if unknown_type and not procedurally_resolved:
        signals.append(
            f"案型「{case_type or '（空白）'}」不在已知需事實認定型清單內，"
            f"且程序上未命中可直接算出的不受理事由——系統無法判斷是否需實體審查，"
            f"保守封鎖結論段交人工。"
        )
        return True, signals

    return (substantive or bool(high)), signals


# 決定書主文型語句。C 型封鎖只把 `conclusion` 槽位刪掉，擋不住「把主文寫進理由段」——
# 模型（接上 Bedrock 後）完全可能在理由段末尾寫「綜上，原處分應予撤銷」，
# 那實質上就是結論，卻因為 slot 標成 reasoning 而整個穿過封鎖。
# 這裡做的是**全槽位**的主文語句偵測，不限 slot（純字串比對，刻意保守：寧可多攔）。
CONCLUSION_PHRASES = (
    "原處分撤銷",
    "原處分應予撤銷",
    "應予撤銷",
    "撤銷原處分",
    "訴願駁回",
    "訴願不受理",
    "應不受理",
    "駁回訴願",
    "另為適法之處分",
    "另為適法處分",
    "由原處分機關另為",
)


def detect_conclusion_like(text: str) -> list[str]:
    """回傳句中命中的主文型語句（空 list = 不像主文）。"""
    return [p for p in CONCLUSION_PHRASES if p in text]


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
