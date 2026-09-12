"""N2 分類節點（純程式，零 LLM——CONSTITUTION §4）。

**誠實聲明（寫在程式碼裡也寫進輸出）**：這是 v0 fallback。
architecture §3.1 規劃的是「kNN 投票 + 與 N1 抽取結果交叉比對」，
但 kNN 需要賽方的 101 份歷史決定書向量庫，那份資料不在本機、也不進 git。
所以 Phase 0 只做**規則式分類**（法規名關鍵詞 → 案型），並且：

- `knn` 一律回空 list，`knn_backend="unavailable"`
- `agreement.simulated=true`、`agreement.match=null`——交叉比對這道安全網
  **此刻沒有實際執行**，UI 與 demo 都必須明講（architecture §4.4）
- `expected_outcome_prior` **不輸出**：那是資料集分布，本機沒有資料集，
  給不出數字就不給，不用「常見比例」腦補（CONSTITUTION §8）
"""
from __future__ import annotations

import time
from typing import Any

from backend.orchestrator.state import CaseState, NodeCtx, NodeResult

# 法規名 → 案型。鍵取自 laws-snapshot.json 的 11 部法規名，不是自由發明的分類體系。
LAW_TO_CASE_TYPE = {
    "空氣污染防制法": "違反空氣污染防制法事件",
    "廢棄物清理法": "違反廢棄物清理法事件",
    "噪音管制法": "違反噪音管制法事件",
    "建築法": "違反建築法事件",
    "洗錢防制法": "違反洗錢防制法事件",
}

UNKNOWN_TYPE = "未能分類"


def classify_by_rule(intake: dict[str, Any], digest: str) -> tuple[str, str, list[str]]:
    """回傳 (案型, 判準說明, 命中的法規名)。純字串比對，不做語意判斷。"""
    # `str()` 不是多餘的：`or ""` 是 falsy 守衛，不是型別守衛（`True or ""` → `True`
    # → `.strip()` → AttributeError）。N1 已擋掉布林值，但 `confirmed_intake` 也餵得
    # 進來，而同一函式裡 note／org 本來就包了 str()——只有這行漏掉。
    declared = str(intake.get("type") or "").strip()
    haystack = " ".join(
        [
            declared,
            str(intake.get("note") or ""),
            str(intake.get("org") or ""),
            digest or "",
        ]
    )
    hits = [law for law in LAW_TO_CASE_TYPE if law in haystack]
    if declared in LAW_TO_CASE_TYPE.values():
        return declared, f"採 N1 抽取之案由欄位（{declared}）", hits
    if len(hits) == 1:
        return LAW_TO_CASE_TYPE[hits[0]], f"卷證文字命中單一法規名「{hits[0]}」", hits
    if len(hits) > 1:
        return UNKNOWN_TYPE, f"卷證文字命中多部法規（{'、'.join(hits)}），規則無法判別，交人工", hits
    return UNKNOWN_TYPE, "卷證文字未命中任何已知法規名，規則無法判別，交人工", hits


def run(state: CaseState, ctx: NodeCtx, digest: str = "") -> NodeResult:
    started = time.perf_counter()
    intake = state.intake
    case_type, basis, law_hits = classify_by_rule(intake, digest)

    # 交叉比對安全網：kNN 通道兩個檔位都沒接上（fixture 是重播同一份資料，
    # bedrock 也沒有相似案投票來源），所以這道安全網是「模擬的」，
    # 不是真的在比對（architecture §4.4）。note 不提檔位——這句話與 RUN_MODE 無關。
    agreement = {
        "llm_type": intake.get("type"),
        "knn_type": None,
        "match": None,
        "simulated": True,
        "note": "kNN 通道不可用，LLM 判讀 vs kNN 投票之交叉比對未實際執行。",
    }

    degraded = case_type == UNKNOWN_TYPE
    classification = {
        "class": {
            "case_type": case_type,
            "basis": basis,
            "method": "rule_v0_fallback",
            "method_note": "v0 fallback：規則式法規名比對。真正的 kNN 分類需要賽方 101 份歷史決定書向量庫，本機無此資料，待資料集到位後替換。",
            "law_hits": law_hits,
            # expected_outcome_prior 刻意不輸出：沒有資料集就沒有分布，不腦補數字。
            "expected_outcome_prior": None,
            "expected_outcome_prior_reason": "需歷史決定書分布統計，本機無資料集，不推估。",
        },
        "knn": [],
        "knn_backend": "unavailable",
        "agreement": agreement,
    }
    state.classification = classification

    elapsed = int((time.perf_counter() - started) * 1000)
    return NodeResult(
        ok=not degraded,
        data=classification,
        degraded=degraded,
        degrade_reason="規則式分類無法判定案型，需人工指定" if degraded else None,
        elapsed_ms=elapsed,
        narrative={
            "clf": {
                "out": f"分類為「{case_type}」（{basis}）。",
                "logs": [
                    ["分類方法：規則式法規名比對（v0 fallback，非 kNN）", "y"],
                    ["交叉比對安全網未實際執行：kNN 通道無資料集", "y"],
                    [f"命中法規名：{'、'.join(law_hits) if law_hits else '無'}", ""],
                ],
            }
        },
    )
