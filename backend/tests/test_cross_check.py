"""期間第二意見交叉比對（`n3_procedure.cross_check_deadline`）單元測試。

這支補的是一個**整個功能零覆蓋**的洞：`cross_check_deadline` 與
`derive_procedure_conclusion` 共 149 行分支邏輯進 main 時，270 條既有測試
沒有任何一條碰到它們——全綠而毫無意義。

每條測試都釘一個**會出錯的方向**，不是 happy path：
- agree 的語意：只有「兩套都算過且結論相同」才是 True
- 一方拒答時 agree 必須是 null，不得折算成 True（2026-09-12 實測抓到的缺陷）
- 輸入不對等要進 not_comparable，不得污染 disagreement
- 國定假日順延差異要進 disagreement 並指出第二意見較可能正確
- 程序審查結論永遠不得出現 B／C／D
"""
from __future__ import annotations

import datetime as dt

from backend.engine.deadline import compute
from backend.engine.overdue_ref import (
    _FIXED_MMDD,
    HOLIDAY_TABLE_YEARS,
    HOLIDAYS,
    _is_rest_day,
    holiday_table_covers,
)
from backend.nodes.n3_procedure import (
    cross_check_deadline,
    derive_procedure_conclusion,
    screen_art77,
)
from backend.tests.harness import assert_eq, assert_in, assert_true


def _intake(d2: str, d3: str | None, method: str, transit: int = 0) -> dict:
    return {"d2": d2, "d3": d3, "service_method": method, "transit_days": transit}


def _ours(d2: str, d3: str | None, method: str, transit: int = 0) -> dict:
    return compute(
        service_method=method,
        service_date=dt.date.fromisoformat(d2),
        filing_date=dt.date.fromisoformat(d3) if d3 else None,
        transit_days=transit,
    ).as_dict()


def _run(d2: str, d3: str | None, method: str, transit: int = 0) -> dict:
    return cross_check_deadline(_intake(d2, d3, method, transit), _ours(d2, d3, method, transit))


# ── agree 的語意 ────────────────────────────────────────────────────────────

def test_two_engines_that_both_computed_and_matched_report_agree_true():
    cc = _run("2025-03-03", "2025-03-20", "personal")
    assert_eq(cc["agree"], True, "兩套都算出同一個屆滿日與同一個逾期結論")
    assert_eq(cc["disagreement"], [], "沒有分歧")


def test_public_service_refusal_must_not_be_reported_as_agreement():
    """**回歸測試（2026-09-12）**：公示送達時本系統拒答、第二意見卻說逾期，
    舊版把 `agree` 算成 True——等於讓一個叫「一致」的欄位在只有一方有答案時
    宣稱一致，且背書的正好是本系統依原則拒絕給出的那個答案。
    """
    ours = _ours("2025-03-03", "2025-05-10", "public")
    assert_eq(ours.get("overdue"), None, "前提：本系統對公示送達拒絕判定逾期")

    cc = cross_check_deadline(_intake("2025-03-03", "2025-05-10", "public"), ours)
    assert_eq(cc["engines"]["second_opinion"]["overdue"], True, "前提：第二意見給了確定答案")
    assert_eq(cc["agree"], None, "一方拒答時 agree 必須是 null，不得是 True")
    assert_true(cc["refusal_asymmetry"], "拒答不對稱要被明確列出，不得靜默吞掉")
    assert_eq(cc["refusal_asymmetry"][0]["ours"], None, "留白的是本系統這一側")
    assert_in("不得", cc["refusal_asymmetry"][0]["why"], "必須明講不得以第二意見補上留白")


def test_agree_is_never_true_when_either_side_has_no_verdict():
    """全稱性檢查：掃過所有送達方式，只要任一側 overdue 為 None，agree 就不得為 True。"""
    for method in ("personal", "deposit", "public"):
        cc = _run("2025-03-03", "2025-05-10", method)
        ours = cc["engines"]["primary"]["overdue"]
        theirs = cc["engines"]["second_opinion"]["overdue"]
        if ours is None or theirs is None:
            assert_true(
                cc["agree"] is not True,
                f"{method}：一方無結論（ours={ours} 2nd={theirs}）卻回報 agree=True",
            )


# ── 分歧 vs 輸入不對等 ──────────────────────────────────────────────────────

def test_in_transit_mismatch_is_not_comparable_not_a_disagreement():
    """在途期間兩邊餵不到同一個值，差異必須進 not_comparable。
    污染 disagreement 會讓真分歧被雜訊淹沒（251 件實測 25 件屬此類、僅 6 件真分歧）。
    """
    cc = _run("2025-03-03", "2025-04-10", "personal", transit=5)
    assert_eq(cc["disagreement"], [], "輸入不對等不是判斷分歧")
    assert_true(cc["not_comparable"], "但必須明列出來，不得靜默丟掉")
    assert_eq(cc["not_comparable"][0]["field"], "deadline")
    assert_eq(cc["agree"], True, "not_comparable 不計入是否一致")


def test_national_holiday_rollover_is_a_real_disagreement_favouring_second_opinion():
    """屆滿日落在國慶日：本系統 v0 只順延週六日，第二意見含國定假日表。
    這是真分歧，且必須明講第二意見較可能正確。
    """
    cc = _run("2025-09-10", "2025-10-15", "personal")
    assert_eq(cc["engines"]["primary"]["deadline"], "2025-10-10", "本系統未順延國慶")
    assert_eq(cc["engines"]["second_opinion"]["deadline"], "2025-10-13", "第二意見順延至次一上班日")
    assert_eq(cc["agree"], False, "這是真分歧")
    assert_eq(cc["disagreement"][0]["field"], "deadline")
    assert_in("認定不同", cc["disagreement"][0]["why"])
    assert_true(
        "較可能正確" not in cc["disagreement"][0]["why"],
        "不得單方面背書第二意見——它的假日表是概估，本身可能錯",
    )


def test_missing_delivery_date_runs_neither_engine_and_says_so():
    cc = cross_check_deadline({"d2": None, "service_method": "personal"}, {})
    assert_eq(cc["agree"], None, "沒跑就不能宣稱一致")
    assert_eq(cc["engines"], {}, "沒跑就不得端出引擎輸出")
    assert_in("不猜", cc["note"])


def test_second_opinion_never_overwrites_our_deadline():
    """憲法性質：`screen.deadline` 永遠是本系統引擎的輸出。"""
    ours = _ours("2025-09-10", "2025-10-15", "personal")
    before = dict(ours)
    cross_check_deadline(_intake("2025-09-10", "2025-10-15", "personal"), ours)
    assert_eq(ours, before, "cross_check 不得就地竄改本系統的計算結果")


# ── 程序審查結論 ────────────────────────────────────────────────────────────

def test_procedure_conclusion_is_A_only_when_rule_engine_found_overdue():
    art77 = screen_art77(_ours("2025-03-03", "2025-05-20", "personal"))
    assert_eq(art77.get("clause"), "77-2", "前提：逾期命中 §77 第 2 款")
    c = derive_procedure_conclusion(art77)
    assert_eq(c["value"], "A")
    assert_eq(c["by"], "rule")


def test_procedure_conclusion_never_emits_a_substantive_verdict():
    """B（駁回）／C（撤銷）／D 屬實體判斷，系統一律不得代為認定。"""
    for art77 in ({}, {"clause": None}, {"clause": "77-1"}, {"clause": "77-8"}):
        c = derive_procedure_conclusion(art77)
        assert_true(
            c["value"] in (None, "A"),
            f"art77={art77} 產出了實體結論 {c['value']!r}",
        )
        if c["value"] is None:
            assert_eq(c["by"], "unavailable")
            assert_in("不代為認定", c["why"])


def test_early_return_keeps_the_same_contract_shape():
    """送達方式不明時的早退路徑，三個分流欄位一律回滿（即使是空的）。

    251 件實測有 67 件送達方式未知——這是常見情境不是邊界。少給 key 會讓
    前端讀到 undefined，而契約的形狀不該因為走哪條分支而變。
    """
    early = cross_check_deadline({"service_method": "unknown"}, {})
    normal = cross_check_deadline(
        {"service_method": "deposit", "d2": "2025-03-03", "d3": "2025-05-01", "transit_days": 0},
        {"deadline": "2025-04-02", "overdue": True},
    )
    assert set(early) == set(normal), f"早退路徑缺少 {set(normal) - set(early)}"
    for field in ("disagreement", "not_comparable", "refusal_asymmetry"):
        assert early[field] == [], f"{field} 應為空 list 而非 None／缺漏"
    assert early["agree"] is None, "第二意見未執行時不得宣稱一致"


# ── 稽核發現的回歸（2026-09-12 對抗式審查，三條全部實測復現）────────────────

def test_neither_side_answered_is_not_agreement():
    """**最常見的畫面**：案件剛進來、承辦人還沒填提起日，兩套都沒有 overdue 結論。
    舊版 `agree = not diffs` 給 True，前端 ProcPanel 渲染成綠色「兩套引擎一致」——
    而實際上一次比對都沒做過。
    """
    for method in ("personal", "public"):
        cc = _run("2025-03-03", None, method)
        assert_eq(
            cc["engines"]["primary"]["overdue"], None, f"{method}：前提是本系統無結論"
        )
        assert_true(
            cc["agree"] is not True,
            f"{method}：兩邊都沒有 overdue 結論卻回報 agree=True",
        )


def test_agree_true_requires_having_actually_compared_something():
    """`compared` 是空的就不可能是 True——沒比過不叫一致。"""
    cc = _run("2025-03-03", None, "public")
    assert_eq(cc["compared"], [], "前提：一次都沒比過")
    assert_eq(cc["agree"], None)

    # 屆滿日比對過、但逾期未比對 → 仍是 None，且 compared 要誠實列出比過的那一維
    partial = _run("2025-03-03", None, "personal")
    assert_eq(partial["compared"], ["deadline"], "屆滿日確實比過了，要列出來")
    assert_eq(partial["agree"], None, "但逾期沒比過，不得宣稱兩套一致")

    ok = _run("2025-03-03", "2025-03-20", "personal")
    assert_in("overdue", ok["compared"])
    assert_eq(ok["agree"], True, "真的比過且無分歧才是 True")


def test_holiday_cause_is_not_swallowed_by_a_transit_mismatch():
    """同一個國定假日漏順延，不該因為 transit 不同就換一個分類。

    舊版 `if transit_mismatch: ... elif holiday: ...` 讓 transit>0 的案子一律歸
    not_comparable 並附「不代表任一方算錯」——而那些案子本系統確實算錯。
    2025-02-28 是週五且為和平紀念日，依行政程序法 §48 II 應順延。
    """
    seen = []
    for d2, transit in (("2025-01-01", 0), ("2025-01-28", 1), ("2025-01-24", 5)):
        cc = _run(d2, "2025-06-01", "personal", transit)
        assert_true(
            cc["disagreement"],
            f"transit={transit}：假日漏順延必須進 disagreement，不得只歸 not_comparable",
        )
        seen.append(cc)

    both = seen[1]
    assert_true(both["not_comparable"], "在途不對等仍要記錄，兩個成因並存")
    assert_in("兩個成因疊加", both["disagreement"][0]["why"])
    assert_in("不得逕認無人算錯", both["not_comparable"][0]["why"])


def test_labour_day_is_not_an_agency_rest_day():
    """勞動節是勞動基準法給勞工的假，行政機關照常上班，非 §48 II 的休息日。

    誤列會讓系統在自己其實算對時，反過來叫承辦人採信錯的一方。
    2025-05-01 是星期四。
    """
    assert_true("05-01" not in _FIXED_MMDD, "勞動節不得列入行政機關休息日表")
    assert_true(
        not _is_rest_day(dt.date(2025, 5, 1)),
        "2025-05-01 是星期四且行政機關上班，不是休息日",
    )
    cc = _run("2025-04-01", "2025-07-01", "personal")
    assert_eq(cc["engines"]["primary"]["deadline"], "2025-05-01")
    assert_eq(cc["agree"], True, "本系統算對，兩套應一致")


def test_deadline_refusal_asymmetry_is_recorded_not_silently_dropped():
    """本系統沒算出屆滿日、第二意見有值時，原本整段被跳過、靜默消失。"""
    cc = _run("2025-03-03", "2025-05-10", "public")
    assert_eq(cc["engines"]["primary"]["deadline"], None, "前提：本系統拒答屆滿日")
    fields = [r["field"] for r in cc["refusal_asymmetry"]]
    assert_in("deadline", fields)
    assert_eq(cc["agree"], None)


# ── 共用盲點：比到了，但兩個比對對象不獨立（2026-09-12 稽核 #6）──────────────

def test_agree_is_none_when_both_engines_share_the_holiday_blind_spot():
    """**這一種是前幾條修不掉的**：兩維都確實比對過（`compared` 滿的），
    但第二意見在「屆滿日順延」的全部優勢來自一張硬編假日表，表沒涵蓋的年份
    它退化成跟本系統一樣的「只認週末」——兩套用同一份錯誤知識算出同一個
    錯答案，`agree` 分不出「獨立算出同一答案」與「共用同一個盲點」。

    不必等到 2028：`_LUNAR_NEW_YEAR` 只到 2026，2027 春節今天就已經缺漏，
    而 2026-12 送達的案子屆滿日就落在 2027-01。
    """
    for target in ("2027-02-08", "2028-10-10"):
        t = dt.date.fromisoformat(target)
        d2 = (t - dt.timedelta(days=30)).isoformat()
        d3 = (t + dt.timedelta(days=5)).isoformat()
        cc = _run(d2, d3, "personal")
        assert_eq(cc["engines"]["primary"]["deadline"], target, "前提：屆滿日在涵蓋期外")
        assert_eq(
            cc["engines"]["second_opinion"]["deadline"],
            target,
            "前提：兩套算出同一個（未順延的）屆滿日",
        )
        assert_in("overdue", cc["compared"])
        assert_true(cc["shared_blind_spot"], f"{target}：共用盲點必須被標出")
        assert_eq(cc["agree"], None, f"{target}：共用盲點時不得回報一致")
        assert_in("共用同一個盲點", cc["shared_blind_spot"][0]["why"])


def test_in_coverage_years_still_report_real_agreement():
    """守住反向：涵蓋期內不得因為這條守衛而全都變成 None。"""
    cc = _run("2025-02-18", "2025-03-25", "personal")
    assert_eq(cc["shared_blind_spot"], [], "涵蓋期內不該觸發共用盲點")
    assert_eq(cc["agree"], True)


def test_holiday_table_coverage_is_the_intersection_of_both_sources():
    """涵蓋期要取兩個來源的交集：固定節日展到 2027，但春節字典只到 2026，
    所以 2027 的春節整段缺漏——涵蓋期若寫成 2027 就是自己騙自己。
    """
    lo, hi = HOLIDAY_TABLE_YEARS
    assert_eq((lo, hi), (2021, 2026))
    assert_true(
        not holiday_table_covers(dt.date(2027, 2, 6)),
        "2027 春節不在表內，不得宣稱涵蓋 2027",
    )
    assert_true(holiday_table_covers(dt.date(2025, 1, 1)))
    assert_true(
        dt.date(2027, 2, 6) not in HOLIDAYS,
        "前提：2027 春節確實不在假日集合裡",
    )


def test_second_opinion_name_states_what_the_251_sample_cannot_measure():
    """251 件全是逾期案，只量得到漏抓率、量不到誤判率。
    無條件的「已驗證」是「可驗但不相干」——數字是真的，它支撐的結論不是它量到的。
    """
    cc = _run("2025-03-03", "2025-03-20", "personal")
    name = cc["engines"]["second_opinion"]["name"]
    assert_in("未涵蓋誤判率", name)
    assert_true("已驗證引擎" not in name, "不得無條件宣稱已驗證")


def test_personal_service_carries_the_supplementary_delivery_legal_ref_caveat():
    """personal 涵蓋同居人／受僱人代收（§73），但第二意見一律印 §72。
    答案不變、引用會錯，必須讓承辦人知道換法源不換答案。
    """
    cc = _run("2025-03-03", "2025-03-20", "personal")
    caveat = cc["engines"]["second_opinion"]["legal_ref_caveat"]
    assert_in("§73", caveat)
    assert_in("換法源不換答案", caveat)
    dep = _run("2025-03-03", "2025-03-20", "deposit")
    assert_eq(dep["engines"]["second_opinion"]["legal_ref_caveat"], None, "寄存不適用")
