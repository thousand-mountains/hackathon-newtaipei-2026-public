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
    assert_in("第二意見較可能正確", cc["disagreement"][0]["why"])


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
