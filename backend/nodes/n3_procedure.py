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

from backend.config.settings import (
    BLOCK_DECISION_INPUT_FIELDS,
    SUBSTANTIVE_TYPES,
    load_fact_issue_signals,
)
from backend.engine.deadline import compute
from backend.engine.overdue_ref import TimelinessInput as RefInput
from backend.engine.overdue_ref import _is_rest_day
from backend.engine.overdue_ref import check_timeliness as ref_check
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


#: 本系統的送達方式 → 第二意見引擎的用語。補充送達（行程§73）生效日＝送達動作日，
#: 與本人簽收同語意，故同映射到 personal 那一側。
_METHOD_TO_REF = {"personal": "本人", "deposit": "寄存", "public": "公示"}


def cross_check_deadline(
    intake: dict[str, Any], deadline_result: dict[str, Any]
) -> dict[str, Any]:
    """把期間計算交給第二套獨立引擎再算一次，兩套不一致就標出來給承辦人。

    **不是用來取代 `deadline.py`**，也不用它的結果覆寫任何欄位——`screen.deadline`
    永遠是本系統引擎的輸出。這裡只回報「另一套怎麼算、跟我們差在哪」。

    為什麼並存而不是合併（2026-09-12 決定，實測依據見
    `docs/2026-09-12-deadline-parity-251.md`）：兩套在 251 件真實決定書上的
    14 件屆滿日差異裡，6 件是本系統未納入國定假日順延、8 件是在途期間取值不同。
    **那些差異本身是要給承辦人看的資訊**，合併成單一答案就消失了。
    """
    method = _METHOD_TO_REF.get(intake.get("service_method") or "")
    if method is None or not intake.get("d2"):
        # 三個分流欄位一律回滿，即使是空的。**早退路徑少給 key 會讓前端讀到
        # undefined**，而「送達方式不明」是常見情境（251 件實測有 67 件），
        # 不是罕見的邊界。契約的形狀不該因為走哪條分支而變。
        return {
            "agree": None,
            "engines": {},
            "disagreement": [],
            "not_comparable": [],
            "refusal_asymmetry": [],
            "note": "送達方式或送達日不足，第二意見引擎未執行——與本系統一樣不猜。",
        }

    ref_out = ref_check(
        RefInput(
            delivery_date=str(intake.get("d2")),
            delivery_method=method,
            appeal_filed_date=str(intake["d3"]) if intake.get("d3") else None,
        )
    )

    ours_deadline = deadline_result.get("deadline")
    ours_overdue = deadline_result.get("overdue")
    diffs: list[dict[str, Any]] = []
    not_comparable: list[dict[str, Any]] = []
    #: 一方拒答、另一方給了答案。**這不是「一致」，也不是「分歧」**——分歧是兩個
    #: 答案打架，這裡是只有一個答案。獨立成類，因為它的風險方向跟分歧相反：
    #: 分歧會讓承辦人警覺，而「拒答 vs 有答案」若被折算成 agree=True，
    #: 反而會讓那個唯一的答案看起來獲得背書。
    refusal_asymmetry: list[dict[str, Any]] = []

    if ours_overdue is None and ref_out.is_overdue is not None:
        refusal_asymmetry.append({
            "field": "overdue",
            "ours": None,
            "second_opinion": ref_out.is_overdue,
            "why": (
                "**本系統就此案型拒絕自動判定，第二意見卻給了確定答案——這不構成佐證。**"
                "本系統拒答的理由（例：公示送達的生效日涉及公告方式與刊登日，須人工認定），"
                "正是第二意見引擎逕自以送達日代替生效日所繞過的前提。"
                "**不得以第二意見補上本系統刻意留白之處**，請承辦人自行認定生效日後重新輸入。"
            ),
        })
    elif ours_overdue is not None and ref_out.is_overdue is None:
        refusal_asymmetry.append({
            "field": "overdue",
            "ours": ours_overdue,
            "second_opinion": None,
            "why": (
                "第二意見引擎因欄位不足未能判定（見 `missing`），本系統則已算出結論。"
                "此時第二意見**未對本系統的結論表示任何意見**，不得讀作背書。"
            ),
        })
    elif ours_overdue is not None and ref_out.is_overdue is not None:
        if ours_overdue != ref_out.is_overdue:
            diffs.append({
                "field": "overdue",
                "ours": ours_overdue,
                "second_opinion": ref_out.is_overdue,
                "why": "兩套引擎對是否逾期的結論不同，請承辦人以卷證認定。",
            })
    if ours_deadline and ref_out.deadline_date and ours_deadline != ref_out.deadline_date:
        transit_ours = int(intake.get("transit_days") or 0)
        if ref_out.in_transit_days != transit_ours:
            # **輸入不對等，不是判斷分歧。** 第二意見引擎的 TimelinessInput 只收
            # 住居所縣市、沒有在途天數欄位，而本系統的卷證沒有擷取縣市——兩邊在這一維
            # 本來就餵不到同一個值。報成 disagreement 會讓真正的分歧（國定假日）被
            # 雜訊淹沒：251 件實測有 25 件屬此類、僅 6 件是真分歧。
            not_comparable.append({
                "field": "deadline",
                "ours": ours_deadline,
                "second_opinion": ref_out.deadline_date,
                "why": (
                    f"在途期間輸入不對等（本系統採承辦人確認的 {transit_ours} 日，"
                    f"第二意見引擎依住居所查表得 {ref_out.in_transit_days} 日）。"
                    "第二意見引擎不接受在途天數輸入、本系統未擷取住居所縣市，"
                    "**這一維無法對等比較，不代表任一方算錯**。"
                ),
            })
        elif _is_rest_day(dt.date.fromisoformat(ours_deadline)):
            diffs.append({
                "field": "deadline",
                "ours": ours_deadline,
                "second_opinion": ref_out.deadline_date,
                "why": (
                    "本系統的屆滿日落在國定假日而未順延（v0 僅處理週六日），"
                    "第二意見引擎含國定假日表。**此時第二意見較可能正確**，"
                    "請承辦人對照行政機關辦公日曆確認。"
                ),
            })
        else:
            diffs.append({
                "field": "deadline",
                "ours": ours_deadline,
                "second_opinion": ref_out.deadline_date,
                "why": "屆滿日不同，成因未歸因——請承辦人對照兩套算式逐步說明。",
            })

    return {
        # 一方拒答時 agree 必須是 None（未知），不能是 True。**True 代表「兩套都算過
        # 且結論相同」**，而拒答的那一方根本沒有結論可比。
        "agree": None if refusal_asymmetry else not diffs,
        "not_comparable": not_comparable,
        "refusal_asymmetry": refusal_asymmetry,
        "engines": {
            "primary": {
                "name": "backend/engine/deadline.py",
                "deadline": ours_deadline,
                "overdue": ours_overdue,
            },
            "second_opinion": {
                "name": "backend/engine/overdue_ref.py（外部已驗證引擎，251 件真實決定書）",
                "deadline": ref_out.deadline_date,
                "overdue": ref_out.is_overdue,
                "verdict": ref_out.verdict,
                "steps": ref_out.steps,
                "legal_refs": ref_out.legal_refs,
                "missing": ref_out.missing,
            },
        },
        "disagreement": diffs,
        "note": (
            "兩套引擎獨立計算，本系統不自動採信任一方；"
            "`screen.deadline` 一律是本系統引擎的輸出，第二意見僅供承辦人對照。"
            "`agree` 只反映 `disagreement`（真正的判斷分歧）；"
            "`not_comparable` 是兩邊輸入不對等造成的差異，不計入是否一致；"
            "`refusal_asymmetry` 是一方拒答而另一方有答案，此時 `agree` 為 null（未知），"
            "**不得把有答案的那一方當成另一方的佐證**。"
        ),
    }


def derive_procedure_conclusion(art77: dict[str, Any]) -> dict[str, Any]:
    """程序審查結論：只在「程序不合 → 不受理」這一種由規則直接給出。

    訴願有無理由（駁回／撤銷）屬實體法律判斷，**本系統不代為認定**，
    所以 value 只可能是 "A" 或 None，永遠不會出現 B／C／D。
    """
    if art77.get("clause") == "77-2":
        return {
            "value": "A",
            "by": "rule",
            "basis": art77.get("basis") or "",
            "why": (
                "逾期由期間引擎直接算出（純規則、零模型參與、同輸入必同輸出），"
                "屬可驗算層，故程序審查結論由系統給出。"
            ),
        }
    return {
        "value": None,
        "by": "unavailable",
        "basis": "",
        "why": (
            "本系統只自動判定訴願法 §77 第 2 款（逾期）這一種程序不合事由。"
            "其餘各款與訴願有無理由屬法律判斷，**本系統不代為認定**，"
            "請承辦人自行審認——此處留白不是失敗，是刻意不猜。"
        ),
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
    # 判斷卡 7：期間輸入欄位有沒有經過承辦人確認，決定「程序上已可直接算出不受理事由」
    # 這件事能不能拿來解除結論封鎖。未確認 = 不能。
    unconfirmed = tuple(
        f for f in BLOCK_DECISION_INPUT_FIELDS if state.intake_origin.get(f, "llm") != "human"
    )
    inputs_confirmed = not unconfirmed
    needs_human, block_signals = requires_human_conclusion(
        art77,
        case_type,
        fact_issues,
        SUBSTANTIVE_TYPES,
        procedural_inputs_confirmed=inputs_confirmed,
        unconfirmed_fields=unconfirmed,
    )

    state.screen = {
        "deadline": deadline_result,
        "art77": art77,
        "fact_issues": fact_issues,
        "requires_human_conclusion": needs_human,
        "human_conclusion_signals": block_signals,
        # 第二意見：獨立引擎再算一次，不覆寫 deadline，只回報差異（US-1）
        "deadline_cross_check": cross_check_deadline(intake, deadline_result),
        # 程序審查結論：只有「程序不合→不受理」由規則給出，實體判斷一律留白（US-4）
        "procedure_conclusion": derive_procedure_conclusion(art77),
        # 稽核用：這一次的程序判斷建立在哪些未確認欄位上
        "procedural_inputs_confirmed": inputs_confirmed,
        "unconfirmed_procedural_fields": list(unconfirmed),
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
                    [
                        (
                            f"封鎖判斷的輸入欄位未經承辦人確認：{'、'.join(unconfirmed)}"
                            "——不得用程序結果解除結論封鎖"
                            if unconfirmed
                            else "封鎖判斷的輸入欄位（期間五欄＋補充說明）已由承辦人確認"
                        ),
                        "y" if unconfirmed else "",
                    ],
                    *[[c, "y"] for c in deadline_result["caveats"]],
                ],
            }
        },
    )
