"""N3 程序審查節點（純程式，零 LLM——CONSTITUTION §4）。

三件事：
1. **期間計算**：薄殼包一層 `engine.deadline.compute`，一行計算邏輯都不在這裡實作。
   引擎是唯一規則來源（architecture §8.3），本節點只負責把 intake 的欄位翻譯成引擎參數。
2. **訴願法 77 條款篩選**：只判定「可由日期直接算出」的 77-2（逾期不受理）。
   當事人適格（77-3）另由 `check_party_standing()` 以純規則引擎提出**意見**
   （三態：適格／不適格／需人工認定），但**不作成決定**——不寫 `clause`、
   不驅動 `procedure_conclusion`、不解除任何封鎖。其餘各款仍列為人工項。
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
from backend.engine.overdue_ref import HOLIDAY_TABLE_YEARS, _is_rest_day, holiday_table_covers
from backend.engine.overdue_ref import check_timeliness as ref_check
from backend.engine.party_ref import StandingInput, check_standing
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
            "rule_assisted": ["77-3"],
            "rule_assisted_reason": (
                "當事人適格（§77 第 3 款）已由純規則引擎逐步比對並提出意見，"
                "見 `screen.party_standing`（三態：適格／不適格／需人工認定）。"
                "**引擎意見不作成不受理決定**——它不寫進 `clause`、不驅動 "
                "`procedure_conclusion`，§77-3 仍由承辦人認定。"
            ),
            "not_auto_screened": ["77-1", "77-4", "77-5", "77-6", "77-7", "77-8"],
            "not_auto_screened_reason": (
                "其餘各款（非行政處分、標的消滅等）屬法律判斷，本系統不自動判定，請承辦人審認。"
                "§77 第 3 款（當事人不適格）已改由規則引擎提出意見（見 `screen.party_standing`），"
                "但同樣不由系統作成決定。"
            ),
        }
    return {
        "screened": ["77-2"],
        "hits": [],
        "clause": None,
        "basis": "期間未逾越（或無法判定），77 條第 2 款不成立；是否有其他不受理事由需人工審認。",
        "requires_substantive_review": True,
        "rule_assisted": ["77-3"],
        "rule_assisted_reason": (
            "當事人適格（§77 第 3 款）已由純規則引擎逐步比對並提出意見，"
            "見 `screen.party_standing`（三態：適格／不適格／需人工認定）。"
            "**引擎意見不作成不受理決定**——它不寫進 `clause`、不驅動 "
            "`procedure_conclusion`，§77-3 仍由承辦人認定。"
        ),
        "not_auto_screened": ["77-1", "77-4", "77-5", "77-6", "77-7", "77-8"],
        "not_auto_screened_reason": (
            "其餘各款屬法律判斷，本系統不自動判定，請承辦人審認。"
            "§77 第 3 款（當事人不適格）已改由規則引擎提出意見（見 `screen.party_standing`），"
            "但同樣不由系統作成決定。"
        ),
    }


#: 本系統的送達方式 → 第二意見引擎的用語。補充送達（行程§73）生效日＝送達動作日，
#: 與本人簽收同語意，故同映射到 personal 那一側。
_METHOD_TO_REF = {"personal": "本人", "deposit": "寄存", "public": "公示"}

#: `deadline.py` 的 `personal` 字面是「本人（**或同居人／受僱人**）簽收」，涵蓋補充送達；
#: 映到第二意見的「本人」後它會印出行政程序法 §72，而同居人代收的法源其實是 §73。
#: **生效日相同、答案不變，但引用會錯**——CONSTITUTION 的「引用必可驗」在此有缺口。
_PERSONAL_LEGAL_REF_CAVEAT = (
    "第二意見的法源以「本人簽收」計（行政程序法 §72）；"
    "**如本案為同居人／受僱人代收，法源應為 §73**（補充送達）。"
    "兩者送達生效日相同，故屆滿日與逾期結論不受影響——換法源不換答案。"
)


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
            "compared": [],
            "shared_blind_spot": [],
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
    #: 實際完成比對的欄位。**空的代表一次都沒比過**——那不是「一致」。
    #: 沒有這個清單的話，「兩邊都沒有答案」會落到 agree = not diffs = True，
    #: 讓「案件剛進來、承辦人還沒填提起日」這個最常見的畫面顯示綠燈「兩套引擎一致」。
    compared: list[str] = []

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
        compared.append("overdue")
        if ours_overdue != ref_out.is_overdue:
            diffs.append({
                "field": "overdue",
                "ours": ours_overdue,
                "second_opinion": ref_out.is_overdue,
                "why": "兩套引擎對是否逾期的結論不同，請承辦人以卷證認定。",
            })
    if not ours_deadline and ref_out.deadline_date:
        # 對稱於 overdue 那一側：本系統沒算出屆滿日（例：公示送達拒答），
        # 第二意見卻有。原本被 `if ours_deadline and ...` 整段跳過，既不進
        # refusal_asymmetry 也不進任何清單，等於靜默消失。
        refusal_asymmetry.append({
            "field": "deadline",
            "ours": None,
            "second_opinion": ref_out.deadline_date,
            "why": (
                "本系統未算出屆滿日（拒絕自動判定），第二意見有值。"
                "**不得以第二意見的屆滿日填補本系統的留白**——兩者拒答與否的前提不同。"
            ),
        })
    elif ours_deadline and ref_out.deadline_date:
        compared.append("deadline")
    if ours_deadline and ref_out.deadline_date and ours_deadline != ref_out.deadline_date:
        transit_ours = int(intake.get("transit_days") or 0)
        # **成因可以同時成立**，所以兩個判斷各自獨立做，不用 if/elif。
        # 舊版用 elif 串起來：只要 transit_ours != 0 就一律歸「輸入不對等」，
        # 真正的國定假日漏順延會被吞掉並附上「不代表任一方算錯」——而該案本系統
        # 確實算錯。實測：同一個假日漏順延，transit=0 進 disagreement、
        # transit=1/5 進 not_comparable，成因完全相同、分類卻不同。
        transit_mismatch = ref_out.in_transit_days != transit_ours
        holiday_cause = _is_rest_day(dt.date.fromisoformat(ours_deadline))

        if transit_mismatch:
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
                    "**在途這一維無法對等比較**。"
                    + ("⚠️ 但本案屆滿日另落在第二意見認定的休息日，"
                       "**尚有第二個成因（見 `disagreement`），不得逕認無人算錯**。"
                       if holiday_cause else
                       "就已知資訊無法歸責任一方算錯。")
                ),
            })
        if holiday_cause:
            diffs.append({
                "field": "deadline",
                "ours": ours_deadline,
                "second_opinion": ref_out.deadline_date,
                "why": (
                    "本系統的屆滿日落在第二意見引擎認定的休息日而未順延"
                    "（本系統 v0 僅處理週六日）。**兩套對『本日是否為行政機關休息日』"
                    "認定不同，本系統不判斷孰是孰非**——第二意見的假日表是概估"
                    "（春節依行事曆估算、且曾把勞動節誤列為行政機關休息日），"
                    "請承辦人對照行政院人事行政總處辦公日曆表認定。"
                    + ("　⚠️ 本案同時存在在途期間輸入不對等（見 `not_comparable`），"
                       "兩個成因疊加。" if transit_mismatch else "")
                ),
            })
        elif not transit_mismatch:
            diffs.append({
                "field": "deadline",
                "ours": ours_deadline,
                "second_opinion": ref_out.deadline_date,
                "why": "屆滿日不同，成因未歸因——請承辦人對照兩套算式逐步說明。",
            })

    #: **兩套引擎在該維度上塌縮成同一套**時，相符不構成佐證。
    #: 第二意見在「屆滿日順延」這一維的全部優勢，來自一張硬編的國定假日表；
    #: 表沒涵蓋的年份，它的假日知識退化成跟本系統一樣的「只認週末」——
    #: 兩套於是用同一份錯誤知識算出同一個錯答案，而 `agree` 分不出
    #: 「兩套獨立算出同一答案」與「兩套共用同一個盲點」。
    #: 這不是「沒比到」（那是 `compared` 管的），是「比到了但比對對象不獨立」。
    shared_blind_spot: list[dict[str, Any]] = []
    _dl_indep = ours_deadline or ref_out.deadline_date
    if _dl_indep and not holiday_table_covers(dt.date.fromisoformat(_dl_indep)):
        lo, hi = HOLIDAY_TABLE_YEARS
        shared_blind_spot.append({
            "field": "deadline",
            "why": (
                f"本案屆滿日在 {_dl_indep[:4]} 年，**超出第二意見假日表的權威涵蓋期"
                f"（{lo}–{hi}）**。該年度的國定假日順延未經任何一套引擎驗證——"
                "第二意見在此維度退化為與本系統相同的「只認週末」，"
                "**兩套相符不代表算對，只代表共用同一個盲點**。"
                "請承辦人對照行政院人事行政總處辦公日曆表自行認定。"
            ),
        })

    return {
        # 一方拒答時 agree 必須是 None（未知），不能是 True。**True 代表「兩套都算過
        # 且結論相同」**，而拒答的那一方根本沒有結論可比。
        # `agree` 只在**是否逾期**這一維真的比對過時才有意義。那是承辦人要據以行動
        # 的結論，屆滿日只是中間值——兩套屆滿日相同但都還沒判逾期時回 True，
        # 前端會渲染成綠燈「兩套引擎一致」，而那個承辦人最在意的問題根本沒被回答。
        # （案件剛進來、還沒填提起日，就是這個狀態，也是最常見的畫面。）
        # 屆滿日層級的相符仍然看得到——讀 `compared`。
        # 共用盲點時也必須是 None：屆滿日若算錯，逾期結論可能跟著錯，
        # 而「兩套相符」在此情況下不是證據。
        "agree": (
            None
            if (refusal_asymmetry or shared_blind_spot or "overdue" not in compared)
            else not diffs
        ),
        "compared": compared,
        "shared_blind_spot": shared_blind_spot,
        "not_comparable": not_comparable,
        "refusal_asymmetry": refusal_asymmetry,
        "engines": {
            "primary": {
                "name": "backend/engine/deadline.py",
                "deadline": ours_deadline,
                "overdue": ours_overdue,
            },
            "second_opinion": {
                "name": (
                    "backend/engine/overdue_ref.py（外部引擎；比對語料 251 件"
                    "**皆為逾期案**，故該比對涵蓋漏抓率、**未涵蓋誤判率**——"
                    "測不到把未逾期誤判為逾期）"
                ),
                "deadline": ref_out.deadline_date,
                "overdue": ref_out.is_overdue,
                "verdict": ref_out.verdict,
                "steps": ref_out.steps,
                "legal_refs": ref_out.legal_refs,
                "missing": ref_out.missing,
                "legal_ref_caveat": (
                    _PERSONAL_LEGAL_REF_CAVEAT if method == "本人" else None
                ),
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


#: 抽取器可能回進來、但不是姓名／名稱的代稱。原處分書常以「訴願人」「受處分人」
#: 指涉相對人，模型照抄回來就會變成一個跟訴願人姓名不同的字串，
#: 進引擎後比對成「不是同一人」→ **憑一個代稱判不適格**。視同未填。
_PARTY_PLACEHOLDER_NAMES = frozenset({
    "訴願人", "受處分人", "受文者", "相對人", "當事人", "本人", "如主旨", "如上",
    "同上", "略", "無", "不詳", "未載明", "未記載", "-", "—",
})

_PARTY_LEGAL_REFS = ("訴願法 §18", "訴願法 §77 第 3 款")
_PARTY_ENGINE = (
    "backend/engine/party_ref.py（外部規則引擎，28 件真實 §77-3 決定書調校；純規則、零 LLM）"
)
#: 引擎驗證成績裡**必須一起講**的限制，而且要講給承辦人看、不是只寫在報告裡。
#:
#: 來源 `party/validate.py:70-71` 的 200 件負樣本是
#: `StandingInput(appellant_name=c["appellant"], respondent_name=c["appellant"])`——
#: **同一個字串餵兩次**，只證明了 `_same_party(x, x) is True`。那個「100% 判對」
#: 幾乎是恆真的，它沒有量到任何一件「兩造姓名不同、但訴願人仍然適格」的案子。
#:
#: 這跟逾期引擎的 167/167 是同一個形狀的洞：251 件全是逾期案，量得到「該判逾期的
#: 沒漏」，量不到「把未逾期誤判成逾期」。**兩套引擎測不到的是同一個方向——
#: 我們有多常錯誤地擋住一個人**，而那正是唯一會傷到民眾的方向。
_PARTY_VALIDATION_CAVEAT = (
    "⚠️ **本引擎的誤擋率未經量測。** 它在 28 件真實 §77-3 案上「該判不適格的抓到 86%」，"
    "但用來證明「不會誤判成不適格」的 200 件負樣本，是把同一個姓名同時當作訴願人與"
    "原處分相對人餵進去比對——那個 100% 幾乎是恆真的，**沒有量到任何一件"
    "「兩造姓名不同、但訴願人仍然適格」的案子**（法人代表人、繼受人、共同訴願、"
    "同一造在兩份文書上寫法不同，都屬這一類）。"
    "誤判不適格會把人擋在訴願門外且無從救濟，故本引擎的意見不得逕行作成不受理決定。"
)
#: `不適格` 是唯一會傷到民眾的那個方向，所以它另外再講一次、講在最前面——
#: 承辦人不該需要讀到 note 的第三句才知道這個數字沒被量過。
_PARTY_DISQUALIFIED_WARNING = (
    "**引擎判「不適格」——這是會讓本件不受理的方向，請務必自行核對卷證。**"
)


def check_party_standing(
    intake: dict[str, Any], intake_origin: dict[str, str] | None = None
) -> dict[str, Any]:
    """當事人適格（訴願法 §18／§77 第 3 款）：純規則，三態如實回報。

    三態是**三件事，不是布林**：「判定適格」／「判定不適格」／「無法判定」。
    `verdict` 一律是這三個中文字串之一，`is_qualified` 對應 True／False／None，
    而 `None` 的意思是「沒有答案」，不是「否」。

    本節點在引擎之外自己加了兩道閘——引擎沒有，但我們的資料條件需要：

    1. **拿不到原處分相對人就不呼叫引擎。** 引擎的反射利益／法律上利害關係／檢舉人
       三個分支只看 `appellant_capacity`，`respondent_name` 為空也照樣下結論，
       等於在**從未比對過姓名**的情況下判不適格。
    2. **輸入未經承辦人確認時，決斷的結論降級為「需人工認定」**，引擎意見改放
       `engine_opinion` 供對照。沿用判斷卡 7 的既有慣例
       （`settings.DEADLINE_INPUT_FIELDS`：模型抽的欄位不得驅動結論），
       而這裡的風險不對稱更陡——誤判「不適格」是把人擋在訴願門外，
       誤判「適格」只是案件照樣進實體審查。

    **本函式的任何 verdict 都不驅動自動不受理決定**：它不寫 `art77.clause`、
    不進 `derive_procedure_conclusion()`、也不解除任何結論封鎖（US-5）。

    回傳的 key 集合**不因走哪條分支而變**（早退路徑少給 key 會讓前端讀到
    undefined，這在 `cross_check_deadline` 上踩過一次）。
    """
    origins = intake_origin or {}
    appellant = str(intake.get("person") or "").strip()
    raw = str(intake.get("respondent_name") or "").strip()
    respondent = "" if raw in _PARTY_PLACEHOLDER_NAMES else raw
    origin = "absent" if not respondent else (origins.get("respondent_name") or "llm")

    base: dict[str, Any] = {
        "verdict": "需人工認定",
        "is_qualified": None,
        "standing_as": "",
        "confidence": "low",
        "legal_refs": list(_PARTY_LEGAL_REFS),
        "steps": [],
        "missing": [],
        "inputs": {
            "appellant_name": appellant,
            "respondent_name": respondent,
            # 訴願人自述身分（代表人／代理人／員工／承租人…）是引擎判斷力的另一半，
            # 但本系統的抽取欄位沒有這一欄，永遠餵空字串——引擎因此只做姓名比對。
            "appellant_capacity": "",
        },
        "respondent_name_origin": origin,
        "engine_opinion": None,
        "engine": _PARTY_ENGINE,
        "note": "",
    }

    if not appellant or not respondent:
        lacking = [
            label
            for label, value in (
                ("person（訴願人）", appellant),
                ("respondent_name（原處分相對人）", respondent),
            )
            if not value
        ]
        steps = []
        if raw and not respondent:
            steps.append(f"原處分相對人欄位填的是「{raw}」，那是代稱不是姓名或名稱，視同未填。")
        steps.append(f"缺少 {'、'.join(lacking)}，無從比對訴願人與原處分相對人是否同一人。")
        steps.append(
            "原處分相對人結構上不在訴願書裡、只記載於原處分書，"
            "請承辦人於收文表單填入後，本項才判得出來。"
        )
        return {
            **base,
            "steps": steps,
            "missing": lacking,
            "note": (
                "§77-3 當事人適格未判定。**「需人工認定」是正當結果，不是失敗**——"
                "資料不足時本系統不猜，也不以任一方推定。"
            ),
        }

    out = check_standing(
        StandingInput(
            appellant_name=appellant,
            respondent_name=respondent,
            appellant_capacity="",
        )
    ).to_dict()
    lead = f"比對訴願人「{appellant}」與原處分相對人「{respondent}」。"
    # 引擎自己就會把 appellant_capacity 列進 missing 的那幾條分支不要再加一次，
    # 同一件事講兩遍會讓承辦人以為是兩個不同的缺漏。
    engine_missing = list(out["missing"])
    if not any("appellant_capacity" in m for m in engine_missing):
        engine_missing.append(
            "appellant_capacity（訴願人自述身分）：本系統未擷取此欄，"
            "引擎僅依姓名比對，未及代理人／代表人／法律上利害關係人各類型。"
        )

    if origin != "human":
        return {
            **base,
            "steps": [
                lead,
                f"但原處分相對人「{respondent}」由模型抽取、尚未經承辦人於收文表單確認"
                f"（intake_origin.respondent_name = {origin!r}）。",
                "§77-3 的結論會把人擋在訴願門外，不得建立在未確認的姓名上——"
                "**本項降級為需人工認定**，引擎意見另列於 engine_opinion 供對照。",
            ],
            "missing": ["respondent_name 尚未經承辦人確認"] + engine_missing,
            "engine_opinion": {
                "verdict": out["verdict"],
                "is_qualified": out["is_qualified"],
                "standing_as": out["standing_as"],
                "confidence": out["confidence"],
                "steps": [lead, *out["steps"]],
                "legal_refs": out["legal_refs"],
                "engine": _PARTY_ENGINE,
                "caveat": (
                    (_PARTY_DISQUALIFIED_WARNING if out["is_qualified"] is False else "")
                    + "此為**未確認輸入**下的引擎意見，不是本系統的認定，不得逕採。"
                    + _PARTY_VALIDATION_CAVEAT
                ),
            },
            "note": (
                "§77-3 未判定：引擎算得出結論，但它依據的原處分相對人還是模型抽的。"
                "請承辦人在收文表單確認該欄後重跑，本項才會給出三態中的決斷結果。"
            ),
        }

    return {
        **base,
        "verdict": out["verdict"],
        "is_qualified": out["is_qualified"],
        "standing_as": out["standing_as"],
        "confidence": out["confidence"],
        "legal_refs": out["legal_refs"],
        "steps": [lead, *out["steps"]],
        "missing": engine_missing,
        "note": (
            (_PARTY_DISQUALIFIED_WARNING if out["is_qualified"] is False else "")
            + "原處分相對人已由承辦人確認，規則引擎依訴願法 §18／§77 第 3 款逐步比對。"
            "**這是引擎意見，不是系統的不受理決定**——§77-3 不進 `art77.clause`、"
            "不驅動 `procedure_conclusion`，仍由承辦人認定。" + _PARTY_VALIDATION_CAVEAT
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
    party_standing = check_party_standing(intake, state.intake_origin)
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
        # 當事人適格（§77-3）：純規則三態，**只是意見**，不作成不受理決定（US-2／US-5）
        "party_standing": party_standing,
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
                        (
                            f"當事人適格（§77-3，規則引擎意見）：{party_standing['verdict']}"
                            f"——{party_standing['steps'][-1] if party_standing['steps'] else ''}"
                            "（意見供對照，不作成不受理決定）"
                        ),
                        "" if party_standing["verdict"] == "適格" else "y",
                    ],
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
