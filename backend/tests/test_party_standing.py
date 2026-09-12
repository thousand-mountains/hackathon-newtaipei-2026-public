"""當事人適格（訴願法 §18／§77 第 3 款）整合測試——`n3_procedure.check_party_standing`。

引擎本體是隊友調校過的外部規則引擎（`backend/engine/party_ref.py`，28 件真實
§77-3 決定書）。**這支測的不是引擎的準確率，是我們包在它外面的那層契約**——
準確率該由來源的 `party/validate.py` 負責，而它的負樣本是同一個字串餵兩次，
本來就量不到偽不適格率（見 `party_ref.py` 檔頭）。

所以這裡每一條釘的都是「會把人擋在訴願門外」的方向：
- 拿不到原處分相對人時，必須回「需人工認定」而不是猜一個答案
- 代稱（「訴願人」「受處分人」）不是姓名，不得拿去比對出一個不適格
- 未經承辦人確認的抽取值不得產生決斷結論（判斷卡 7 的既有慣例）
- 三態是三件事，不是布林
- §77-3 的意見**不驅動任何自動不受理**，也不解除結論封鎖
"""
from __future__ import annotations

from backend.config.settings import SUBSTANTIVE_TYPES, load_snapshot
from backend.nodes import n3_procedure
from backend.nodes.n3_procedure import (
    check_party_standing,
    derive_procedure_conclusion,
    screen_art77,
)
from backend.orchestrator.state import CaseState, NodeCtx
from backend.tests.harness import assert_eq, assert_in, assert_true

_VERDICTS = ("適格", "不適格", "需人工認定")


def _confirmed(person: str, respondent: str | None) -> dict:
    return check_party_standing(
        {"person": person, "respondent_name": respondent}, {"respondent_name": "human"}
    )


def _n3(intake: dict, origin: dict | None = None, case_type: str = "違反建築法事件"):
    """跑一次完整 N3（不是只叫 check_party_standing），回 `state.screen`。"""
    state = CaseState(case_id="synthetic-party-01")
    state.classification = {"class": {"case_type": case_type}}
    state.intake = intake
    state.intake_origin = dict(origin or {})
    n3_procedure.run(state, NodeCtx(run_mode="fixture", snapshot=load_snapshot()), digest="")
    return state


# ── 拿不到原處分相對人：正當地不判，而不是猜 ────────────────────────────────

def test_missing_respondent_name_returns_needs_human_not_a_guess():
    """AC：`respondent_name` 缺席時必須回「需人工認定」，不得猜一個答案。

    「需人工認定」是正當結果不是失敗（proposal.md:54）。反方向的錯——
    在沒有相對人姓名的情況下判「不適格」——等於憑空把人擋在訴願門外。
    """
    r = check_party_standing({"person": "陳○珠"}, {})

    assert_eq(r["verdict"], "需人工認定")
    assert_eq(r["is_qualified"], None, "沒有答案要用 None 表示，不得折算成 False")
    assert_true(r["verdict"] not in ("適格", "不適格"), "資料不足卻給了決斷結論")
    assert_eq(r["respondent_name_origin"], "absent")
    assert_eq(r["engine_opinion"], None, "引擎根本沒跑，不得端出意見")
    assert_true(
        any("respondent_name" in m for m in r["missing"]),
        f"缺什麼要講出來，missing={r['missing']}",
    )
    assert_in("原處分書", " ".join(r["steps"]), "要告訴承辦人這一欄該去哪裡拿")


def test_the_engine_is_never_called_without_a_respondent_name():
    """引擎的反射利益／利害關係／檢舉人三個分支只看 `appellant_capacity`，
    `respondent_name` 為空也照樣下結論——**那是在從未比對姓名的情況下判不適格**。
    本節點的守門是「不呼叫」，不是「呼叫完再修結果」，所以這裡直接把引擎換成地雷。
    """
    orig = n3_procedure.check_standing

    def landmine(*a, **k):
        raise AssertionError("缺原處分相對人時不得呼叫規則引擎")

    n3_procedure.check_standing = landmine
    try:
        for intake in ({"person": "陳○珠"}, {"person": "", "respondent_name": "王大明"}):
            r = check_party_standing(intake, {"respondent_name": "human"})
            assert_eq(r["verdict"], "需人工認定", f"{intake} 應為需人工認定")
    finally:
        n3_procedure.check_standing = orig


def test_a_placeholder_word_is_not_a_name():
    """原處分書常以「訴願人」「受處分人」代稱相對人。模型照抄回來，那個字串跟
    訴願人姓名一定不同 → 引擎會判「不適格」。**憑一個代稱把人擋在門外**是 P0 方向。

    沒有這道濾網的實作（把 raw 值直接餵引擎）在這條會回「不適格」而轉紅。
    """
    for placeholder in ("訴願人", "受處分人", "受文者", "同上", "無", "不詳"):
        r = _confirmed("陳○珠", placeholder)
        assert_eq(r["verdict"], "需人工認定", f"「{placeholder}」被當成姓名拿去比對了")
        assert_eq(r["is_qualified"], None)
        assert_eq(r["respondent_name_origin"], "absent", "代稱等同未填")
        assert_eq(r["inputs"]["respondent_name"], "", "代稱不得留在 inputs 裡假裝是名字")
    assert_in("代稱", " ".join(_confirmed("陳○珠", "訴願人")["steps"]))


# ── 未經承辦人確認的抽取值不得產生決斷結論（判斷卡 7 的既有慣例）──────────

def test_unconfirmed_respondent_name_is_downgraded_but_the_opinion_is_kept():
    """同一組輸入，只差 `intake_origin.respondent_name`：

    - human → 引擎的決斷結論（不適格）如實回報
    - llm   → **降級為需人工認定**，引擎意見改放 `engine_opinion`

    這是本節點跟「把引擎輸出原樣回傳」的實作唯一分歧的地方，也是本檔最有價值的一條。
    """
    intake = {"person": "陳○珠", "respondent_name": "盧江溪"}

    unconfirmed = check_party_standing(intake, {"respondent_name": "llm"})
    assert_eq(unconfirmed["verdict"], "需人工認定", "未確認的姓名不得產生決斷結論")
    assert_eq(unconfirmed["is_qualified"], None)
    assert_eq(unconfirmed["respondent_name_origin"], "llm")
    assert_eq(
        unconfirmed["engine_opinion"]["verdict"], "不適格",
        "引擎確實算得出結論——降級不是丟掉，是改標成意見",
    )
    assert_in("不得逕採", unconfirmed["engine_opinion"]["caveat"])

    confirmed = check_party_standing(intake, {"respondent_name": "human"})
    assert_eq(confirmed["verdict"], "不適格", "承辦人確認後才給決斷結論")
    assert_eq(confirmed["is_qualified"], False)
    assert_eq(confirmed["engine_opinion"], None, "已經是本系統的認定，不需要再另列意見")


def test_an_absent_origin_map_defaults_to_unconfirmed_not_confirmed():
    """`intake_origin` 沒登記這一欄時，預設必須是「未確認」。
    預設成 human 會讓一個從沒被人看過的值直接產出不適格。
    """
    r = check_party_standing({"person": "陳○珠", "respondent_name": "盧江溪"}, {})
    assert_eq(r["respondent_name_origin"], "llm")
    assert_eq(r["verdict"], "需人工認定")


# ── 三態是三件事 ────────────────────────────────────────────────────────────

def test_confirmed_inputs_yield_all_three_states():
    """三種輸入打出三種結果，缺一不可——只回兩態的實作在這裡轉紅。

    第三態用遮罩姓名（去掉遮罩字後只剩一個字）：引擎明說此時資訊不足，
    這是真實語料裡會發生的情形，不是為了湊測試造的。
    """
    same = _confirmed("王大明", "王大明")
    assert_eq(same["verdict"], "適格")
    assert_eq(same["is_qualified"], True)
    assert_eq(same["standing_as"], "相對人")

    # 真實案：新北 1101030146（§77-3 不受理），訴願人與受處分公司是兩家不同公司
    different = _confirmed("立○營造股份有限公司", "唯謙建設股份有限公司")
    assert_eq(different["verdict"], "不適格")
    assert_eq(different["is_qualified"], False)
    assert_in("並非同一人", " ".join(different["steps"]))

    indeterminate = _confirmed("陳○", "林○")
    assert_eq(indeterminate["verdict"], "需人工認定", "遮罩後資訊不足要老實說不知道")
    assert_eq(indeterminate["is_qualified"], None)


def test_verdict_and_is_qualified_never_disagree():
    """全稱性：掃過所有分支，`verdict` 與 `is_qualified` 的對應恆成立。
    一個 boolean 表達不了三態——這條釘的就是不准退化成布林。
    """
    matrix = [
        ({"person": "王大明", "respondent_name": "王大明"}, {"respondent_name": "human"}),
        ({"person": "陳○珠", "respondent_name": "盧江溪"}, {"respondent_name": "human"}),
        ({"person": "陳○", "respondent_name": "林○"}, {"respondent_name": "human"}),
        ({"person": "陳○珠", "respondent_name": "盧江溪"}, {"respondent_name": "llm"}),
        ({"person": "陳○珠", "respondent_name": "訴願人"}, {"respondent_name": "human"}),
        ({"person": "陳○珠"}, {}),
        ({}, {}),
    ]
    for intake, origin in matrix:
        r = check_party_standing(intake, origin)
        assert_true(r["verdict"] in _VERDICTS, f"未知 verdict {r['verdict']!r}")
        expected = {"適格": True, "不適格": False, "需人工認定": None}[r["verdict"]]
        assert_eq(r["is_qualified"], expected, f"{intake}：verdict 與 is_qualified 不一致")
        assert_true(
            {"訴願法 §18", "訴願法 §77 第 3 款"} <= set(r["legal_refs"]),
            f"{intake}：每一種結果都要附本規則的法條依據，legal_refs={r['legal_refs']}",
        )
        assert_true(r["steps"], f"{intake}：沒有逐步理由的結論不可驗算")


def test_contract_shape_is_identical_across_every_branch():
    """四條分支（缺席／代稱／未確認／已確認）回傳的 key 集合必須一樣。
    早退路徑少給 key 會讓前端讀到 undefined——這在 `cross_check_deadline` 上踩過一次。
    """
    branches = {
        "absent": check_party_standing({"person": "陳○珠"}, {}),
        "placeholder": _confirmed("陳○珠", "訴願人"),
        "unconfirmed": check_party_standing(
            {"person": "陳○珠", "respondent_name": "盧江溪"}, {"respondent_name": "llm"}
        ),
        "confirmed": _confirmed("王大明", "王大明"),
    }
    ref = set(branches["confirmed"])
    for name, payload in branches.items():
        assert_eq(set(payload), ref, f"{name} 分支的 key 集合與其他分支不同")
        assert_true(payload["note"], f"{name} 分支沒有給承辦人看的說明")


def test_a_disqualified_verdict_always_states_that_the_false_block_rate_is_unmeasured():
    """引擎判「不適格」時，承辦人**必須在同一段文字裡**看到「誤擋率未經量測」。

    這不是免責聲明，是可查證的事實：`party/validate.py:70-71` 的 200 件負樣本把
    同一個姓名同時當訴願人與相對人餵進去，沒有量到任何一件「兩造姓名不同、
    但訴願人仍適格」的案子。誤判不適格＝把人擋在訴願門外，受害者沒有救濟管道。

    對照組也一起釘：判「適格」時**不得**掛這句不適格警語，否則它會變成到處都有的
    背景雜訊，承辦人就不會再讀它。
    """
    disqualified = _confirmed("立○營造股份有限公司", "唯謙建設股份有限公司")
    assert_eq(disqualified["verdict"], "不適格", "前提")
    assert_in("誤擋率未經量測", disqualified["note"])
    assert_in("同一個姓名同時當作訴願人與原處分相對人", disqualified["note"])
    assert_true(
        disqualified["note"].startswith("**引擎判「不適格」"),
        "會讓本件不受理的那個方向，警語要在最前面，不能埋在第三句",
    )

    unconfirmed = check_party_standing(
        {"person": "陳○珠", "respondent_name": "盧江溪"}, {"respondent_name": "llm"}
    )
    assert_eq(unconfirmed["engine_opinion"]["verdict"], "不適格", "前提")
    assert_in("誤擋率未經量測", unconfirmed["engine_opinion"]["caveat"])
    assert_in("不適格", unconfirmed["engine_opinion"]["caveat"])

    qualified = _confirmed("王大明", "王大明")
    assert_eq(qualified["verdict"], "適格", "前提")
    assert_true(
        "會讓本件不受理的方向" not in qualified["note"],
        "適格不該掛不適格的警語——警語到處都有就等於沒有",
    )
    assert_in("誤擋率未經量測", qualified["note"], "但引擎本身的限制在哪一種結果都要講")


# ── §77-3 是意見，不是決定（US-4／US-5）──────────────────────────────────────

def test_art77_moves_77_3_out_of_not_auto_screened_and_says_where_it_went():
    for deadline_result in ({"overdue": True}, {"overdue": False}, {"overdue": None}):
        art77 = screen_art77(deadline_result)
        assert_true(
            "77-3" not in art77["not_auto_screened"],
            f"{deadline_result}：77-3 已由規則引擎提出意見，不該還掛在「不自動篩選」",
        )
        assert_eq(art77["rule_assisted"], ["77-3"])
        assert_in("party_standing", art77["rule_assisted_reason"])
        assert_in("不作成不受理決定", art77["rule_assisted_reason"])
        # 資訊不得憑空消失：只讀 reason 的前端也要看得到 77-3 去了哪裡
        assert_in("第 3 款", art77["not_auto_screened_reason"])
        assert_in("party_standing", art77["not_auto_screened_reason"])
        assert_true("77-3" not in (art77["hits"] or []), "適格與否不得寫進 77 條命中")
        assert_true(art77["clause"] != "77-3", "§77-3 不得成為系統認定的不受理款項")


def test_a_disqualified_party_never_produces_an_automatic_dismissal():
    """引擎判「不適格」（＝§77-3 不受理事由）且案件未逾期時，
    程序審查結論仍必須留白——**不受理決定不得由本引擎作成**（US-5）。

    把 §77-3 接進 `art77.clause` 的實作在這條會轉紅。
    """
    state = _n3(
        {
            "service_method": "personal", "d2": "2025-03-14", "d3": "2025-04-07",
            "person": "立○營造股份有限公司", "respondent_name": "唯謙建設股份有限公司",
        },
        {"respondent_name": "human"},
    )
    screen = state.screen
    assert_eq(screen["party_standing"]["verdict"], "不適格", "前提：引擎判不適格")
    assert_eq(screen["deadline"]["overdue"], False, "前提：本案未逾期，77-2 不成立")
    assert_eq(screen["art77"]["clause"], None, "§77-3 不得寫進 clause")
    assert_eq(
        derive_procedure_conclusion(screen["art77"])["value"], None,
        "不適格不得被折算成系統自己作成的『A：不受理』",
    )
    assert_eq(screen["procedure_conclusion"]["value"], None)
    assert_eq(screen["procedure_conclusion"]["by"], "unavailable")


def test_party_standing_never_unblocks_the_human_conclusion():
    """三種 verdict 跑同一個案子，`requires_human_conclusion` 必須完全相同。

    最危險的方向是「適格」被拿來當作「程序沒問題、可以自動寫結論」的理由。
    把 party_standing 接進 `requires_human_conclusion` 的實作在這條會轉紅。
    """
    base = {"service_method": "personal", "d2": "2025-03-14", "d3": "2025-04-07"}
    variants = {
        "適格": ({"person": "王大明", "respondent_name": "王大明"}, {"respondent_name": "human"}),
        "不適格": ({"person": "陳○珠", "respondent_name": "盧江溪"}, {"respondent_name": "human"}),
        "需人工認定": ({"person": "陳○珠"}, {}),
    }
    seen = {}
    for label, (extra, origin) in variants.items():
        state = _n3({**base, **extra}, origin)
        assert_eq(state.screen["party_standing"]["verdict"], label, f"前提：{label}")
        seen[label] = (
            state.screen["requires_human_conclusion"],
            tuple(state.screen["human_conclusion_signals"]),
        )
    assert_eq(len(set(seen.values())), 1, f"當事人適格改變了結論封鎖：{seen}")
    assert_true(
        all(v[0] for v in seen.values()),
        f"本案屬需事實認定型（{SUBSTANTIVE_TYPES[:1]}…），三種 verdict 都應維持封鎖：{seen}",
    )


# ── 真的接進 N3 了嗎 ────────────────────────────────────────────────────────

def test_n3_run_surfaces_party_standing_on_the_payload_path():
    """欄位加了卻沒有消費者＝形式具備實質不具備。

    `graph.py` 是把 `state.screen` 整包塞進 payload 的（`"screen": state.screen`），
    所以「進得了 `state.screen`」就等於「前端讀得到」。同時確認 `NodeResult.data`
    也帶著它——SSE 的 node_done 走的是那一份。
    """
    state = _n3(
        {
            "service_method": "personal", "d2": "2025-03-14", "d3": "2025-04-07",
            "person": "王大明", "respondent_name": "王大明",
        },
        {"respondent_name": "human"},
    )
    party = state.screen["party_standing"]
    assert_eq(party["verdict"], "適格")
    assert_in("party_ref.py", party["engine"], "要講出這個結論是哪一套引擎算的")
    assert_eq(
        party["inputs"]["respondent_name"], "王大明",
        "拿去比對的實際輸入要留在 payload 裡，承辦人才驗得了這一步",
    )
    assert_eq(party["inputs"]["appellant_capacity"], "", "本系統未擷取此欄，不得假裝有值")


def test_n3_narrative_mentions_party_standing_for_every_verdict():
    """承辦人畫面上的程序卡片要講出 §77-3 的結果，而且**不得講成系統的決定**。"""
    for extra, origin, expect in (
        ({"person": "王大明", "respondent_name": "王大明"}, {"respondent_name": "human"}, "適格"),
        ({"person": "陳○珠"}, {}, "需人工認定"),
    ):
        state = CaseState(case_id="synthetic-party-02")
        state.classification = {"class": {"case_type": "違反建築法事件"}}
        state.intake = {
            "service_method": "personal", "d2": "2025-03-14", "d3": "2025-04-07", **extra
        }
        state.intake_origin = dict(origin)
        result = n3_procedure.run(
            state, NodeCtx(run_mode="fixture", snapshot=load_snapshot()), digest=""
        )
        line = next(
            (log[0] for log in result.narrative["proc"]["logs"] if "當事人適格" in log[0]), None
        )
        assert_true(line is not None, f"{expect}：程序卡片沒有提到當事人適格")
        assert_in(expect, line)
        assert_in("不作成不受理決定", line)
