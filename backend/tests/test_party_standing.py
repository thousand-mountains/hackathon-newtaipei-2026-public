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

import json
import pathlib

from backend.config.settings import SUBSTANTIVE_TYPES, load_snapshot
from backend.engine.party_ref import StandingInput, check_standing
from backend.nodes import n3_procedure
from backend.nodes.n3_procedure import (
    check_party_standing,
    derive_procedure_conclusion,
    screen_art77,
)
from backend.orchestrator.state import CaseState, NodeCtx
from backend.tests.harness import TestSkipped, assert_eq, assert_in, assert_true

#: 單元測試的 fixture 姓名一律合成（王大明／李小華／大明營造…）。
#: 真實案件事實由 28 件語料的測試負責，兩者刻意分開：
#: fixture 驗的是規則行為，語料驗的是對真實案件的判準。
_VERDICTS = ("適格", "不適格", "需人工認定")

#: 28 件真實 §77-3 不受理案（新北市政府公開訴願決定書），正確答案全部是「不適格」。
#: 來源：`origin/hack-petition-procedure-review-ref` 的 `party/party_facts.jsonl`。
#:
#: ⚠️ **這份語料不進 git**（`.gitignore` 排除 `backend/data/local/`）。訴願決定書遮
#: 訴願人、**不遮原處分相對人**，抽取器原樣帶出，28 件中有 11 件的相對人欄位是
#: 未遮罩的自然人實名。
#:
#: 為什麼不改成遮罩後入庫：實測過了——遮罩後 `_norm_name` 會把「甯○○」正規化成
#: 一個字，`_same_party` 判資訊不足，**不適格命中率從 25/28 掉到 16/28**。
#: 那份語料就不再量得到它該量的東西，留著只會給人一個假的評估數字。
#:
#: 要跑這些測試，先補語料（不會進 git）：
#:   mkdir -p backend/data/local && git show \
#:     origin/hack-petition-procedure-review-ref:reference/petition-procedure-review/party/party_facts.jsonl \
#:     > backend/data/local/party-facts-77-3.jsonl
#: 沒有它時這些測試會**明確標記為「略過」**——不計入通過數。一條沒跑的測試被算成
#: 綠燈，就是我們一直在抓的那個形狀。
_REAL_77_3 = pathlib.Path("backend/data/local/party-facts-77-3.jsonl")


def _real_77_3_rows() -> list[dict]:
    if not _REAL_77_3.exists():
        raise TestSkipped(
            f"語料不在 repo（含未遮罩個資）：{_REAL_77_3}。補齊指令見本檔檔頭。"
        )
    return [
        json.loads(line)
        for line in _REAL_77_3.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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
    r = check_party_standing({"person": "王大明"}, {})

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
        for intake in ({"person": "王大明"}, {"person": "", "respondent_name": "王大明"}):
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
        r = _confirmed("王大明", placeholder)
        assert_eq(r["verdict"], "需人工認定", f"「{placeholder}」被當成姓名拿去比對了")
        assert_eq(r["is_qualified"], None)
        assert_eq(r["respondent_name_origin"], "absent", "代稱等同未填")
        assert_eq(r["inputs"]["respondent_name"], "", "代稱不得留在 inputs 裡假裝是名字")
    assert_in("代稱", " ".join(_confirmed("王大明", "訴願人")["steps"]))


# ── 未經承辦人確認的抽取值不得產生決斷結論（判斷卡 7 的既有慣例）──────────

def test_unconfirmed_respondent_name_is_downgraded_but_the_opinion_is_kept():
    """同一組輸入，只差 `intake_origin.respondent_name`：

    - human → 引擎的決斷結論（不適格）如實回報
    - llm   → **降級為需人工認定**，引擎意見改放 `engine_opinion`

    這是本節點跟「把引擎輸出原樣回傳」的實作唯一分歧的地方，也是本檔最有價值的一條。
    """
    intake = {"person": "王大明", "respondent_name": "李小華"}

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
    r = check_party_standing({"person": "王大明", "respondent_name": "李小華"}, {})
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
    different = _confirmed("大明營造股份有限公司", "志成建設股份有限公司")
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
        ({"person": "王大明", "respondent_name": "李小華"}, {"respondent_name": "human"}),
        ({"person": "陳○", "respondent_name": "林○"}, {"respondent_name": "human"}),
        ({"person": "王大明", "respondent_name": "李小華"}, {"respondent_name": "llm"}),
        ({"person": "王大明", "respondent_name": "訴願人"}, {"respondent_name": "human"}),
        ({"person": "王大明"}, {}),
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
        "absent": check_party_standing({"person": "王大明"}, {}),
        "placeholder": _confirmed("王大明", "訴願人"),
        "unconfirmed": check_party_standing(
            {"person": "王大明", "respondent_name": "李小華"}, {"respondent_name": "llm"}
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
    disqualified = _confirmed("大明營造股份有限公司", "志成建設股份有限公司")
    assert_eq(disqualified["verdict"], "不適格", "前提")
    assert_in("誤擋率未經量測", disqualified["note"])
    assert_in("同一個姓名同時當作訴願人與原處分相對人", disqualified["note"])
    assert_true(
        disqualified["note"].startswith("**引擎判「不適格」"),
        "會讓本件不受理的那個方向，警語要在最前面，不能埋在第三句",
    )

    unconfirmed = check_party_standing(
        {"person": "王大明", "respondent_name": "李小華"}, {"respondent_name": "llm"}
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
            "person": "大明營造股份有限公司", "respondent_name": "志成建設股份有限公司",
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
        "不適格": ({"person": "王大明", "respondent_name": "李小華"}, {"respondent_name": "human"}),
        "需人工認定": ({"person": "王大明"}, {}),
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
        ({"person": "王大明"}, {}, "需人工認定"),
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


# ── 偽不適格：唯一會傷到民眾的方向 ──────────────────────────────────────────
#
# 引擎的驗證成績有一個講不出口的洞：`party/validate.py:70-71` 的 200 件負樣本是
# 把同一個字串同時當訴願人與相對人餵進去，只證明了 `_same_party(x, x) is True`。
# **「兩造姓名不同、但訴願人仍適格」的誤判率從來沒有被量過。**
#
# 那個真正的負樣本要從 8,486 件決定書的 `data/cases.jsonl` 挑，而那份語料**不在
# git 裡**（在 S3，需 Jacky 授權）——所以本檔量不到那個率，也不假裝量得到。
#
# 量得到的是**機制**：下面五種**結構**都實際出現在真實語料
# `backend/data/local/party-facts-77-3.jsonl` 的相對人欄位，它們是同一造的兩種寫法，
# 而引擎會判「並非同一人」。這比一個湊出來的百分比有用——它指出 pipeline 裡真的會
# 發生的事。
#
# ⚠️ **姓名是合成的，結構是真的。** 不用真實姓名有兩個理由：
#   1. 那些欄位帶未遮罩的自然人實名，不進 git（見檔頭）。
#   2. 遮罩過的姓名測不到這條機制——`_norm_name` 會把「甯○○」正規化成一個字，
#      低於子字串門檻，這五條會全部假性通過。
# 用合成姓名測字串比對規則**不算編造測資**：被驗的是比對邏輯本身，不是案件事實；
# 案件事實那半由上面 28 件真實語料的測試負責，語料不在就明確標「略過」。

_SAME_PARTY_TWO_SPELLINGS = [
    ("王大明", "王大明即大明工程行", "自然人即獨資商號：訴願書寫本名、原處分書寫『本名即商號』"),
    ("大明工程行", "王大明即大明工程行", "同上，反向（訴願書寫商號）"),
    ("曾大有", "應為曾大有", "抽取夾帶前綴詞——這個結構原封不動出現在真實語料裡"),
    ("新北市私立大明高級工業家事職業學校", "財團法人新北市私立大明高級工業家事職業學校", "省略『財團法人』法人格前綴"),
    ("陳大川", "陳大川等 20 人（詳如附表）", "共同訴願：附表寫法"),
]


def test_the_same_party_spelled_two_ways_is_never_disqualified():
    """五種真實寫法差異，一件都不准判成「不適格」。

    沒有這道守門的實作（把引擎的「並非同一人」直接當答案）五條全紅。
    """
    for appellant, respondent, why in _SAME_PARTY_TWO_SPELLINGS:
        r = _confirmed(appellant, respondent)
        assert_true(
            r["verdict"] != "不適格",
            f"偽不適格：「{appellant}」vs「{respondent}」被判不適格（{why}）",
        )
        assert_eq(r["is_qualified"], None, f"{why}：兩造是否同一造未定，不得給決斷結論")
        assert_in("同一造", " ".join(r["steps"]), f"{why}：要講出為什麼不下不適格")


def test_the_false_block_guard_costs_nothing_on_the_28_real_disqualified_cases():
    """守門的代價要用真實資料量，不是用直覺講。

    `backend/data/party-facts-77-3.jsonl` 是 28 件真實 §77-3 不受理案（新北市政府
    公開訴願決定書，來源 `origin/hack-petition-procedure-review-ref` 的
    `party/party_facts.jsonl`），**全部的正確答案都是「不適格」**。

    來源 README 宣稱的成績是「不適格 86%、需人工 14%、誤判為適格 0%」。這裡同時
    釘住三件事：(1) 我們重現得出那個數字，(2) 加了偽不適格守門之後**一件都沒有
    因此被降級**，(3) 誤判為適格永遠是 0 件——那是另一個方向的錯，同樣不准發生。
    """
    rows = _real_77_3_rows()
    assert_eq(len(rows), 28, "測試向量檔被改動過")

    tally: dict[str, int] = {}
    wrongly_qualified = []
    for row in rows:
        r = _confirmed(row["appellant"], row["respondent"])
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
        if r["verdict"] == "適格":
            wrongly_qualified.append((row["case_no"], row["appellant"], row["respondent"]))

    assert_eq(wrongly_qualified, [], "把真實的不適格案判成適格，方向錯得最離譜")
    assert_eq(
        tally.get("不適格"), 25,
        f"不適格命中率變了（本 pipeline 實測 25/28，實得 {tally}）",
    )
    assert_eq(tally.get("需人工認定"), 3, f"需人工件數變了：{tally}")
    # 守門的代價：實測**恰好 1 件**（26 → 25），而且那一件本來就該被降級。
    caught = [
        row["case_no"]
        for row in rows
        if n3_procedure._looks_like_the_same_party_spelled_differently(
            row["appellant"], row["respondent"]
        )
    ]
    assert_eq(
        caught, ["1131061414"],
        "偽不適格守門攔到的真實案件清單變了——它的代價要有人看過才准變",
    )
    # 1131061414 的相對人欄位是「應為曾○榕」，而訴願人是「曾○榕」。那個「應為」
    # 是抽取夾帶的前綴詞，不是名字的一部分——真正的相對人是誰，從這個字串看不出來。
    # 原引擎判它不適格是**因為前綴讓兩個字串不同**，不是因為認定了兩造不同人。
    # 降級成需人工是對的，這 1 件不是損失。
    row = next(r for r in rows if r["case_no"] == "1131061414")
    assert_eq(row["respondent"], "應為曾○榕", "這一件的相對人欄位內容變了，理由要重寫")
    assert_in("曾○榕", row["appellant"])


def test_we_are_more_willing_to_disqualify_than_the_validated_engine_and_we_say_so():
    """**本 pipeline 25/28 比來源設定的 24/28 更敢下不適格，而且是往危險的方向。**

    差別在 `appellant_capacity`：來源的 `party/validate.py` 餵得到這一欄，我們沒有
    這個抽取欄位。逐件比對 28 件真實案，兩件因此分歧，兩件都是我們判不適格、
    原引擎判需人工——
      1101031047 capacity=代表人、相對人為公司（可能是代表公司提起 → 其實適格）
      1131070709 capacity=共有人（§18 的法律上利害關係人 → 其實可能適格）

    這兩類正是「訴願人其實適格卻被擋掉」的路徑。這條測試釘的是：**payload 必須
    把這件事講給承辦人聽**，不能只寫在報告裡讓它隨交件消失。
    """
    rows = _real_77_3_rows()
    stricter = []
    for row in rows:
        source = check_standing(
            StandingInput(
                appellant_name=row["appellant"],
                respondent_name=row["respondent"],
                appellant_capacity=row["appellant_capacity"],
                filed_in_own_name=row["filed_in_own_name"],
                respondent_is_corp=row["respondent_is_corp"],
            )
        ).verdict
        ours = _confirmed(row["appellant"], row["respondent"])["verdict"]
        if ours == "不適格" and source == "需人工認定":
            stricter.append(row["case_no"])
    assert_eq(
        sorted(stricter), ["1101031047", "1131070709"],
        "本 pipeline 比原引擎更早下不適格的案件清單變了——這是誤擋方向，變動要有人看過",
    )

    # 而且每一個不適格結論都要自己講出這個盲點
    disqualified = _confirmed("大明營造股份有限公司", "志成建設股份有限公司")
    assert_eq(disqualified["verdict"], "不適格", "前提")
    joined = " ".join(disqualified["steps"])
    assert_in("只比對了姓名", joined)
    assert_in("代表公司提起", joined)
    assert_in("共有人", joined)
    assert_true(
        any("以何身分提起" in m for m in disqualified["missing"]),
        f"缺的那一欄要列進 missing：{disqualified['missing']}",
    )


def test_the_containment_predicate_ignores_single_character_names():
    """守門的 2 字門檻是直接測的，不是靠上層行為間接推的。

    上層測不到它：遮罩後只剩一個字時 `_same_party` 本來就回 None（需人工認定），
    守門根本輪不到執行。但這個 predicate 是模組層函式，門檻放寬成 1 字之後
    「陳」會被當成「陳○政」的子字串——對未來的呼叫端是真的洞，所以直接釘住。
    """
    same = n3_procedure._looks_like_the_same_party_spelled_differently
    assert_true(same("王大明", "王大明即大明工程行"), "兩字以上的子字串要視為疑似同一造")
    assert_true(not same("陳", "陳○政"), "單字姓氏不是子字串證據，會把兩個陳姓當同一人")
    assert_true(not same("王大明", "王大明"), "完全相同交給引擎判適格，不歸這道守門")
    assert_true(not same("", "王大明"), "空字串不算子字串")
    assert_true(not same("王大明", "李小華"), "毫無關係的兩造不得被判成疑似同一造")
