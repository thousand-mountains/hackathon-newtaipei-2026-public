"""N3 程序審查節點（純程式，零 LLM——CONSTITUTION §4）。

三件事：
1. **期間計算**：薄殼包一層 `engine.deadline.compute`，一行計算邏輯都不在這裡實作。
   引擎是唯一規則來源（architecture §8.3），本節點只負責把 intake 的欄位翻譯成引擎參數。
2. **訴願法 77 條款篩選**：只判定「可由日期直接算出」的 77-2（逾期不受理）。
   當事人不適格（77-3）等其他款屬法律判斷，**不自動判**，列為人工項。
3. **fact_issues 事實認定爭點偵測**：純字串比對，刻意保守（寧可多攔）。
   漏攔一個 C 型案是 P0，多攔一個只是多一段紅區佔位（architecture §4.3）。

`requires_human_conclusion` 在這裡求值，不在 N6——因為它必須在組 N5 的 prompt 之前
就知道結論段要不要拿掉，而 N6 跑在 N5 之後（循環依賴問題，architecture §4.3）。

檔名說明：architecture §10 寫的是 `n3_screen.py`，本 Phase 依 plan 的模組清單命名為
`n3_procedure.py`，職責完全相同。
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Any

from backend.config.settings import SUBSTANTIVE_TYPES, load_fact_issue_signals
from backend.engine.deadline import compute
from backend.gate.lamps import requires_human_conclusion
from backend.orchestrator.state import CaseState, NodeCtx, NodeResult


def _date(v: Any) -> dt.date | None:
    if not v:
        return None
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def screen_art77(deadline_result: dict[str, Any]) -> dict[str, Any]:
    """只判定可由日期直接算出的 77-2；其餘款不自動判。"""
    overdue = deadline_result.get("overdue")
    if overdue is True:
        return {
            "screened": ["77-2"],
            "hits": ["77-2"],
            "clause": "77-2",
            "basis": "訴願法 77 條第 2 款：提起訴願逾法定期間者，應為不受理之決定。逾期由期間引擎直接算出，屬可驗算層。",
            "requires_substantive_review": False,
            "not_auto_screened": ["77-1", "77-3", "77-4", "77-5", "77-6", "77-7", "77-8"],
            "not_auto_screened_reason": "其餘各款（當事人不適格、非行政處分、標的消滅等）屬法律判斷，本系統不自動判定，請承辦人審認。",
        }
    return {
        "screened": ["77-2"],
        "hits": [],
        "clause": None,
        "basis": "期間未逾越（或無法判定），77 條第 2 款不成立；是否有其他不受理事由需人工審認。",
        "requires_substantive_review": True,
        "not_auto_screened": ["77-1", "77-3", "77-4", "77-5", "77-6", "77-7", "77-8"],
        "not_auto_screened_reason": "其餘各款屬法律判斷，本系統不自動判定，請承辦人審認。",
    }


def detect_fact_issues(
    case_type: str, art77_clause: str | None, digest: str, note: str, signals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """對每條訊號先過 applies_to 濾網，再對卷證摘要 + intake.note 做純字串包含比對。"""
    haystack = f"{digest or ''}\n{note or ''}"
    issues: list[dict[str, Any]] = []
    idx = 0
    for sig in signals:
        applies = sig.get("applies_to", {})
        types = applies.get("case_type") or []
        if types and case_type not in types:
            continue
        clauses = applies.get("art77_clause") or []
        if clauses and art77_clause not in clauses:
            continue
        matched = [kw for kw in sig.get("any_of", []) if kw and kw in haystack]
        if not matched:
            continue
        idx += 1
        issues.append(
            {
                "id": f"I{idx}",
                "signal_id": sig["id"],
                "t": f"爭點{idx}：{sig['title']}",
                "q": sig["question"],
                "severity": sig["severity"],
                "matched_keywords": matched,
                "src": "AI 不得代為認定，請承辦人核對卷證",
                "origin": "rule",
            }
        )
    return issues


def run(state: CaseState, ctx: NodeCtx, digest: str = "") -> NodeResult:
    started = time.perf_counter()
    intake = state.intake

    method = intake.get("service_method") or "unknown"
    if method not in ("personal", "deposit", "public"):
        # 送達方式不明時不猜——引擎會 raise，這裡先擋下來給明確理由
        deadline_result = {
            "effective_date": None,
            "deadline": None,
            "overdue": None,
            "steps": [],
            "caveats": [f"送達方式為 {method!r}，無法判定送達生效日，期間計算不執行，請人工確認送達方式。"],
        }
        degraded = True
        degrade_reason = "送達方式不明，期間計算未執行"
    else:
        result = compute(
            service_method=method,
            service_date=_date(intake.get("d2")),
            filing_date=_date(intake.get("d3")),
            transit_days=int(intake.get("transit_days") or 0),
            interested_party=bool(intake.get("interested_party")),
        )
        deadline_result = result.as_dict()
        degraded = deadline_result["deadline"] is None
        degrade_reason = "期間引擎拒答（公示送達等情形），交人工確認" if degraded else None

    art77 = screen_art77(deadline_result)
    case_type = (state.classification.get("class") or {}).get("case_type", "")
    signals = load_fact_issue_signals()
    fact_issues = detect_fact_issues(
        case_type, art77["clause"], digest, str(intake.get("note") or ""), signals
    )
    needs_human, block_signals = requires_human_conclusion(
        art77, case_type, fact_issues, SUBSTANTIVE_TYPES
    )

    state.screen = {
        "deadline": deadline_result,
        "art77": art77,
        "fact_issues": fact_issues,
        "requires_human_conclusion": needs_human,
        "human_conclusion_signals": block_signals,
    }
    state.assert_screened_invariant()

    high = [i for i in fact_issues if i["severity"] == "high"]
    elapsed = int((time.perf_counter() - started) * 1000)
    return NodeResult(
        ok=True,
        data=state.screen,
        degraded=degraded,
        degrade_reason=degrade_reason,
        elapsed_ms=elapsed,
        narrative={
            "proc": {
                "out": (
                    f"期滿日 {deadline_result['deadline'] or '未能計算'}，"
                    f"{'逾期' if deadline_result['overdue'] else ('未逾期' if deadline_result['overdue'] is False else '逾期與否未判定')}。"
                    f"偵測到 {len(fact_issues)} 個事實認定爭點（高風險 {len(high)} 個）。"
                ),
                "logs": [
                    [f"期間計算共 {len(deadline_result['steps'])} 個步驟，每步附法源", ""],
                    [f"訴願法 77 條自動篩選：{art77['clause'] or '無命中'}", ""],
                    [
                        f"結論段{'封鎖（由承辦人判斷）' if needs_human else '可由模板組稿'}",
                        "r" if needs_human else "",
                    ],
                    *[[c, "y"] for c in deadline_result["caveats"]],
                ],
            }
        },
    )
