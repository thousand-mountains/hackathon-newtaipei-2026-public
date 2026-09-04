"""六節點單元測試（stdlib only）。

每個節點至少驗一條「紅線行為」，不只是 happy path：
- N1：非 fixture 模式必須 raise；低信心必須降級
- N2：分類失敗要誠實回未能分類；不得輸出 expected_outcome_prior 數字
- N3：期間計算薄殼正確；77-2 只在逾期時命中；fact_issues applies_to 濾網有效
- N4：相似案通道必須回空；條文原文必須留白
- N5：非 fixture 模式必須 raise；封鎖時 conclusion 不進 slots
- N6：四態判定、missing 阻擋、C 型事後覆核
"""
from __future__ import annotations

import datetime as dt

from backend.config.settings import SUBSTANTIVE_TYPES, load_fact_issue_signals, load_snapshot
from backend.gate.citations import (
    STATE_AMENDED,
    STATE_MISSING,
    STATE_OK,
    STATE_OUT_OF_SCOPE,
    CitationChecker,
)
from backend.gate.lamps import lamp_for_states, requires_human_conclusion
from backend.nodes import n1_extract, n2_classify, n3_procedure, n4_retrieval, n5_draft, n6_gate
from backend.orchestrator.state import CaseState, NodeCtx
from backend.retrieval.base import UnavailableRetriever
from backend.retrieval.lawtable import LawTableRetriever
from backend.tests.harness import assert_eq, assert_in, assert_true

SNAPSHOT = load_snapshot()


def _ctx(mode: str = "fixture") -> NodeCtx:
    return NodeCtx(run_mode=mode, snapshot=SNAPSHOT)


def _minimal_fixture(**over):
    base = {
        "id": "synthetic-unit-01",
        "extraction": {
            "intake": {
                "no": "synthetic-x",
                "type": "違反建築法事件",
                "d2": "2025-03-14",
                "d3": "2025-04-07",
                "service_method": "personal",
                "note": "",
            },
            "conf": {"no": 0.95, "type": 0.95, "d2": 0.95, "service_method": 0.95},
            "facts_excerpt": [{"text": "合成卷證原文。", "page": 1, "quote_ref": "synthetic.pdf#p1"}],
        },
        "draft_fixture": {
            "reasoning": [{"t": "按建築法第73條規定。", "cite_ids": ["L1"], "basis": "建築法第73條"}],
            "conclusion": [{"t": "訴願駁回。", "cite_ids": [], "basis": "訴願法第79條"}],
        },
    }
    base.update(over)
    return base


# ── N1 抽取 ────────────────────────────────────────────────────────
def test_n1_raises_when_not_fixture_mode():
    state = CaseState(case_id="synthetic-unit-01")
    for mode in ("local", "bedrock"):
        try:
            n1_extract.run(state, _ctx(mode), case_fixture=_minimal_fixture())
        except NotImplementedError as e:
            assert_in("Bedrock", str(e), "raise 訊息要說明需要 Bedrock 憑證")
        else:
            raise AssertionError(f"RUN_MODE={mode} 時 N1 必須 raise，不得靜默回 fixture 資料")


def test_n1_marks_every_field_origin_llm():
    state = CaseState(case_id="synthetic-unit-01")
    n1_extract.run(state, _ctx(), case_fixture=_minimal_fixture())
    assert_true(state.intake_origin, "intake_origin 不得為空")
    assert_true(all(v == "llm" for v in state.intake_origin.values()), "N1 抽出的欄位一律標 origin=llm")


def test_n1_degrades_on_low_confidence():
    fx = _minimal_fixture()
    fx["extraction"]["conf"]["service_method"] = 0.62
    state = CaseState(case_id="synthetic-unit-01")
    r = n1_extract.run(state, _ctx(), case_fixture=fx)
    assert_eq(r.degraded, True, "必填欄位信心低於門檻必須降級")
    assert_in("service_method", r.data["low_conf_fields"])


# ── N2 分類 ────────────────────────────────────────────────────────
def test_n2_is_honest_about_being_v0_fallback():
    state = CaseState(case_id="synthetic-unit-01")
    state.intake = {"type": "違反建築法事件"}
    r = n2_classify.run(state, _ctx(), digest="建築法")
    assert_eq(r.data["class"]["method"], "rule_v0_fallback")
    assert_eq(r.data["knn"], [], "沒有資料集就不得回任何 kNN 結果")
    assert_eq(r.data["agreement"]["simulated"], True, "交叉比對安全網在 fixture 檔位是模擬的，必須標明")
    assert_eq(r.data["class"]["expected_outcome_prior"], None, "沒有資料集分布就不得給機率數字")


def test_n2_returns_unknown_when_no_signal():
    state = CaseState(case_id="synthetic-unit-01")
    state.intake = {"type": "某種未知事件"}
    r = n2_classify.run(state, _ctx(), digest="與任何已知法規名無關的文字")
    assert_eq(r.data["class"]["case_type"], n2_classify.UNKNOWN_TYPE)
    assert_eq(r.degraded, True, "分類失敗必須降級交人工，不得硬猜一個案型")


# ── N3 程序審查 ────────────────────────────────────────────────────
def test_n3_deadline_matches_engine_directly():
    """薄殼不得改寫引擎結果：N3 的輸出必須等於直接呼叫 engine.compute。"""
    from backend.engine.deadline import compute

    state = CaseState(case_id="synthetic-unit-01")
    state.intake = {"service_method": "deposit", "d2": "2024-06-13", "d3": "2024-07-20"}
    state.classification = {"class": {"case_type": "違反空氣污染防制法事件"}}
    r = n3_procedure.run(state, _ctx(), digest="")
    direct = compute("deposit", dt.date(2024, 6, 13), dt.date(2024, 7, 20)).as_dict()
    assert_eq(r.data["deadline"], direct, "N3 薄殼改動了引擎輸出")


def test_n3_art77_hits_only_when_overdue():
    state = CaseState(case_id="synthetic-unit-01")
    state.classification = {"class": {"case_type": "違反建築法事件"}}
    state.intake = {"service_method": "personal", "d2": "2025-03-14", "d3": "2025-04-07"}
    r = n3_procedure.run(state, _ctx(), digest="")
    assert_eq(r.data["art77"]["clause"], None, "未逾期不得命中 77-2")
    assert_eq(r.data["art77"]["requires_substantive_review"], True)

    state2 = CaseState(case_id="synthetic-unit-02")
    state2.classification = {"class": {"case_type": "違反建築法事件"}}
    state2.intake = {"service_method": "personal", "d2": "2025-03-14", "d3": "2025-12-31"}
    r2 = n3_procedure.run(state2, _ctx(), digest="")
    assert_eq(r2.data["art77"]["clause"], "77-2", "逾期必須命中 77-2")
    assert_eq(r2.data["art77"]["requires_substantive_review"], False)


def test_n3_fact_issue_applies_to_filter_works():
    """洗防法專屬訊號不得在建築法案件上觸發（applies_to 濾網）。"""
    signals = load_fact_issue_signals()
    issues = n3_procedure.detect_fact_issues(
        case_type="違反建築法事件",
        art77_clause=None,
        digest="訴願人主張帳戶遭詐騙、非本人使用",
        note="",
        signals=signals,
    )
    assert_true(
        all(i["signal_id"] != "acct_control" for i in issues),
        "acct_control 只適用洗錢防制法事件，不該在建築法案件觸發",
    )


def test_n3_fact_issue_uses_string_match_not_semantics():
    signals = load_fact_issue_signals()
    issues = n3_procedure.detect_fact_issues(
        case_type="違反建築法事件", art77_clause=None, digest="裁處權時效已消滅", note="", signals=signals
    )
    hit = next((i for i in issues if i["signal_id"] == "penalty_limitation"), None)
    assert_true(hit is not None, "時效關鍵詞必須觸發 penalty_limitation")
    assert_eq(hit["severity"], "high")
    assert_true(hit["matched_keywords"], "必須列出命中的關鍵詞，讓人知道為什麼被攔")


def test_requires_human_conclusion_switch():
    art77_substantive = {"requires_substantive_review": True}
    art77_procedural = {"requires_substantive_review": False}
    ok, sig = requires_human_conclusion(art77_substantive, "違反建築法事件", [], SUBSTANTIVE_TYPES)
    assert_eq(ok, True, "程序合法 + 需事實認定型案型 → 封鎖結論")
    assert_true(sig)
    ok2, _ = requires_human_conclusion(art77_procedural, "違反建築法事件", [], SUBSTANTIVE_TYPES)
    assert_eq(ok2, False, "程序不合（逾期）→ 結論可由模板組")
    ok3, _ = requires_human_conclusion(
        art77_procedural, "違反建築法事件", [{"id": "I1", "t": "x", "severity": "high"}], SUBSTANTIVE_TYPES
    )
    assert_eq(ok3, True, "高風險事實爭點單獨即可封鎖結論")


# ── N4 檢索 ────────────────────────────────────────────────────────
def test_n4_similar_case_channel_returns_empty_and_labels_unverified():
    """CONSTITUTION §2：沒有資料集就誠實回空，不編造案號。"""
    state = CaseState(case_id="synthetic-unit-01")
    state.classification = {"class": {"case_type": "違反建築法事件", "law_hits": ["建築法"]}}
    state.screen = {"art77": {"clause": None}}
    r = n4_retrieval.run(state, _ctx(), cited_laws=["建築法第73條"])
    assert_eq(r.data["cases"], [], "相似案通道必須回空")
    meta = r.data["retrieval_meta"]["similar_case_channel"]
    assert_eq(meta["available"], False)
    assert_eq(meta["label"], "庫外，未驗證")
    assert_true(meta["reason"], "必須說明為什麼回空")


def test_n4_does_not_fabricate_article_text():
    state = CaseState(case_id="synthetic-unit-01")
    state.classification = {"class": {"case_type": "違反建築法事件", "law_hits": ["建築法"]}}
    state.screen = {"art77": {"clause": None}}
    r = n4_retrieval.run(state, _ctx(), cited_laws=["建築法第73條"])
    for law in r.data["laws"]:
        assert_eq(law["q"], None, "快照沒有條文原文，系統不得補寫")
        assert_true(law["q_note"], "留白必須附說明")


def test_n4_dedupes_repeated_citations():
    state = CaseState(case_id="synthetic-unit-01")
    state.classification = {"class": {"case_type": "違反建築法事件", "law_hits": []}}
    state.screen = {"art77": {"clause": None}}
    r = n4_retrieval.run(state, _ctx(), cited_laws=["建築法第73條", "建築法第73條", "行政罰法第27條"])
    ids = [l["t"] for l in r.data["laws"]]
    assert_eq(len(ids), len(set(ids)), "同一條法條不得重複列")


def test_unavailable_retriever_always_empty():
    u = UnavailableRetriever("x", "沒有資料集")
    assert_eq(u.search("任何查詢", top_k=10), [])


def test_lawtable_reports_missing_article_honestly():
    lt = LawTableRetriever(SNAPSHOT)
    hits = lt.search("建築法第999條")
    assert_eq(len(hits), 1)
    assert_eq(hits[0].verified, False)
    assert_in("無第 999 條", hits[0].note)


# ── N5 主筆 ────────────────────────────────────────────────────────
def test_n5_raises_when_not_fixture_mode():
    state = CaseState(case_id="synthetic-unit-01")
    state.screen = {"requires_human_conclusion": False, "deadline": {"steps": []}}
    try:
        n5_draft.run(state, _ctx("bedrock"), case_fixture=_minimal_fixture())
    except NotImplementedError:
        return
    raise AssertionError("RUN_MODE=bedrock 時 N5 必須 raise")


def test_n5_drops_conclusion_slot_when_blocked():
    """結構性封鎖：conclusion 根本不進 slots，不是靠 prompt 請求模型別寫。"""
    assert_eq(n5_draft.resolve_slots(True), ["reasoning"])
    assert_eq(n5_draft.resolve_slots(False), ["reasoning", "conclusion"])

    state = CaseState(case_id="synthetic-unit-01")
    state.screen = {"requires_human_conclusion": True, "deadline": {"steps": [], "caveats": []}}
    state.intake = {"no": "synthetic-x"}
    r = n5_draft.run(state, _ctx(), case_fixture=_minimal_fixture())
    assert_true("conclusion" not in r.data["slots"], "封鎖時 conclusion 不得出現在 slots")
    assert_eq(r.data["conclusion_dropped"], True)
    conclusion_sentences = [
        s for b in r.data["doc_skeleton"] for s in b["ss"] if s["slot"] == "conclusion"
    ]
    assert_eq(len(conclusion_sentences), 1, "封鎖時結論段應只有一個佔位句")
    assert_eq(conclusion_sentences[0]["origin"], "human_required")
    assert_eq(conclusion_sentences[0]["placeholder"], True)


def test_n5_does_not_produce_lamps_or_why():
    state = CaseState(case_id="synthetic-unit-01")
    state.screen = {"requires_human_conclusion": False, "deadline": {"steps": [], "caveats": []}}
    state.intake = {"no": "synthetic-x"}
    r = n5_draft.run(state, _ctx(), case_fixture=_minimal_fixture())
    for b in r.data["doc_skeleton"]:
        for s in b["ss"]:
            assert_eq(s["l"], None, "模型只寫句子，不寫燈號")
            assert_eq(s["why"], None, "why 由守門產出")


# ── N6 守門與引用四態 ──────────────────────────────────────────────
def test_citation_state_ok():
    c = CitationChecker(SNAPSHOT).check_text("依訴願法第14條規定")
    assert_eq(len(c), 1)
    assert_eq(c[0].state, STATE_OK)
    assert_eq(c[0].lamp, "g")
    assert_eq(c[0].blocking, False)


def test_citation_state_missing_blocks():
    c = CitationChecker(SNAPSHOT).check_text("依建築法第999條規定")
    assert_eq(c[0].state, STATE_MISSING)
    assert_eq(c[0].lamp, "r")
    assert_eq(c[0].blocking, True, "查無此號是唯一會阻擋送出的狀態")


def test_citation_state_amended():
    """洗錢防制法 15之2 已改列第 22 條（snapshot amendments）。"""
    c = CitationChecker(SNAPSHOT).check_text("依洗錢防制法第15條之2規定")
    assert_eq(c[0].state, STATE_AMENDED)
    assert_eq(c[0].lamp, "y")
    assert_eq(c[0].blocking, False, "條次異動不阻擋送出，只標黃")
    assert_in("22", c[0].note)


def test_citation_state_out_of_scope_for_unknown_law():
    """法規完全不在快照 11 部內 → 庫外未驗證（黃），不是查無（紅）。

    這條是防誤攔：Claire 量測決定書引用的法條有 17% 對不回資料集，
    一律標查無會造成大量誤攔（architecture §8.1）。
    """
    c = CitationChecker(SNAPSHOT).check_text("依民事訴訟法第100條規定")
    # 「民事訴訟法」不在快照的 11 部法規內
    assert_true(c, "應該要抽到引用")
    assert_eq(c[0].state, STATE_OUT_OF_SCOPE)
    assert_eq(c[0].lamp, "y")
    assert_eq(c[0].blocking, False)


def test_precedent_four_states_not_two():
    ck = CitationChecker(SNAPSHOT, today=dt.date(2026, 9, 5))
    in_lib = ck.check_text("最高行政法院108年度判字第531號")
    assert_eq(in_lib[0].state, STATE_OK)

    out_lib = ck.check_text("最高行政法院110年度判字第1號")
    assert_eq(out_lib[0].state, STATE_OUT_OF_SCOPE, "白名單外但格式成立 → 庫外未驗證，不阻擋")
    assert_eq(out_lib[0].blocking, False)

    bad_format = ck.check_text("最高行政法院999年度判字第1號")
    assert_eq(bad_format[0].state, STATE_MISSING, "年度超出可能範圍 → 格式不成立，阻擋")
    assert_eq(bad_format[0].blocking, True)


def test_interpretation_states():
    ck = CitationChecker(SNAPSHOT)
    assert_eq(ck.check_text("釋字第469號")[0].state, STATE_OK)
    out = ck.check_text("釋字第999號")[0]
    assert_eq(out.state, STATE_OUT_OF_SCOPE, "非白名單釋字標庫外未驗證，不宣稱它不存在")
    assert_eq(out.blocking, False)


def test_citation_survives_fullwidth_digits():
    """全形數字不得造成誤攔。

    法規 PDF 轉出來的文字常帶全形數字。若不正規化，「訴願法第１４條」這種**正確的引用**
    會被判成查無此號並阻擋送出——誤攔比漏抓更難察覺，因為它看起來像系統在認真把關。
    這條測試是實際構造對抗輸入時抓到真 bug 後補的。
    """
    ck = CitationChecker(SNAPSHOT)
    ok = ck.check_text("訴願法第１４條")
    assert_eq(len(ok), 1)
    assert_eq(ok[0].state, STATE_OK, "全形數字的合法引用不得被誤判為查無此號")
    assert_eq(ok[0].raw, "訴願法第14條", "顯示字串應正規化成半形，方便前端比對")

    sub = ck.check_text("廢棄物清理法第３９條之１")
    assert_eq(sub[0].state, STATE_OK, "全形數字的『之N』條號也要正規化")

    bad = ck.check_text("建築法第９９９條")
    assert_eq(bad[0].state, STATE_MISSING, "正規化不得讓真的不存在的條號變成通過")

    prec = ck.check_text("最高行政法院１０８年度判字第５３１號")
    assert_eq(prec[0].state, STATE_OK, "判解字號的全形數字同樣不得誤攔")


def test_citation_tolerates_spacing_and_newlines():
    ck = CitationChecker(SNAPSHOT)
    for text in ("訴願法第 14 條", "訴願法\n第14條", "最高行政法院 108 年度 判 字 第 531 號"):
        r = ck.check_text(text)
        assert_true(r, f"{text!r} 應該要抽到引用")
        assert_eq(r[0].state, STATE_OK, f"{text!r} 不得因空白或換行被誤判")


def test_citation_boundary_article_numbers():
    """邊界條號：max 之內通過、超過一號即查無。"""
    ck = CitationChecker(SNAPSHOT)
    assert_eq(ck.check_text("建築法第105條")[0].state, STATE_OK, "最大條號本身應在庫")
    assert_eq(ck.check_text("建築法第106條")[0].state, STATE_MISSING, "超過最大條號一號即查無")
    assert_eq(ck.check_text("建築法第0條")[0].state, STATE_MISSING, "第 0 條不存在")


def test_relative_law_reference_is_not_silently_passed():
    """「本法第14條」「同法第74條」這種相對指稱抓不到，是**已知限制**。

    重點是它的失敗模式安全：抓不到 → 該句沒有引用 → 燈號黃（無引用之涵攝句，交人工），
    **不會**被誤標成綠燈「已驗證」。這條測試把這個行為釘住，避免將來有人「順手」
    把無引用句改成預設綠燈。
    """
    from backend.gate.lamps import lamp_for_states

    ck = CitationChecker(SNAPSHOT)
    assert_eq(ck.check_text("本法第14條"), [], "相對指稱目前抓不到（已知限制）")
    assert_eq(ck.check_text("同法第74條"), [], "相對指稱目前抓不到（已知限制）")
    assert_eq(lamp_for_states([]), "y", "沒有引用的句子必須是黃燈交人工，絕不可預設綠燈")


def test_lamp_severity_ordering():
    assert_eq(lamp_for_states([STATE_OK, STATE_OK]), "g")
    assert_eq(lamp_for_states([STATE_OK, STATE_AMENDED]), "y")
    assert_eq(lamp_for_states([STATE_OK, STATE_OUT_OF_SCOPE]), "y")
    assert_eq(lamp_for_states([STATE_OK, STATE_MISSING]), "r", "一句話的燈號取最嚴重的引用狀態")
    assert_eq(lamp_for_states([]), "y", "沒有引用的涵攝句預設黃燈交人工")


def test_n6_flags_generated_conclusion_when_blocked():
    """C 型事後覆核：封鎖時若真的冒出模型結論句，必須進 blockers 並標 P0。"""
    state = CaseState(case_id="synthetic-unit-01")
    state.screen = {
        "requires_human_conclusion": True,
        "fact_issues": [],
        "human_conclusion_signals": ["測試訊號"],
        "deadline": {"steps": [], "caveats": []},
    }
    state.retrieval = {"laws": [], "cases": [], "retrieval_meta": {}}
    # 手動塞一個違規的結論句（模擬未來有人改壞 N5 的情形）
    state.draft = {
        "doc_skeleton": [
            {
                "ty": "p",
                "text": "",
                "ind": 1,
                "ss": [
                    {
                        "id": "s1",
                        "t": "原處分撤銷。",
                        "origin": "llm",
                        "slot": "conclusion",
                        "cite_ids": [],
                        "basis": None,
                        "engine": None,
                        "placeholder": False,
                        "l": None,
                        "why": None,
                        "refs": [],
                        "l_origin": "rule",
                        "why_origin": "rule",
                    }
                ],
            }
        ]
    }
    try:
        n6_gate.run(state, _ctx())
    except AssertionError as e:
        assert_in("P0", str(e), "不變式必須明說是 P0")
        assert_true(
            any(b["reason"] == "conclusion_generated_while_blocked" for b in state.gate["blockers"]),
            "覆核必須先記進 blockers 再炸",
        )
        return
    raise AssertionError("封鎖時出現模型生成的結論句，N6 必須攔下")
