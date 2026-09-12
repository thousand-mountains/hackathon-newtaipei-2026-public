"""六節點單元測試（stdlib only）。

每個節點至少驗一條「紅線行為」，不只是 happy path：
- N1：非 fixture 模式必須 raise；低信心必須降級
- N2：分類失敗要誠實回未能分類；不得輸出 expected_outcome_prior 數字
- N3：期間計算薄殼正確；77-2 只在逾期時命中；fact_issues applies_to 濾網有效
- N4：相似案通道必須回空；條文原文必須留白
- N5：未實作模式必須 raise、bedrock 走真模型；封鎖時 conclusion 不進 slots
- N6：四態判定、missing 阻擋、C 型事後覆核
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from backend.config.settings import SUBSTANTIVE_TYPES, load_fact_issue_signals, load_snapshot
from backend.gate.citations import (
    STATE_AMENDED,
    STATE_MISSING,
    STATE_OK,
    STATE_OUT_OF_SCOPE,
    CitationChecker,
)
from backend.engine.deadline import compute
from backend.gate.lamps import detect_conclusion_like, lamp_for_states, requires_human_conclusion
from backend.llm import client as llm_client
from backend.nodes import n1_extract, n2_classify, n3_procedure, n4_retrieval, n5_draft, n6_gate
from backend.orchestrator.state import CaseState, NodeCtx
from backend.retrieval.base import UnavailableRetriever
from backend.retrieval.lawtable import LawTableRetriever, cn_to_int
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
    """未實作的模式必須 raise；已實作的 bedrock 分支也不得退回 fixture 資料。

    `bedrock` 自 2026-09-07 起是真分支（走 backend.llm.client），所以不再列在
    NotImplementedError 的清單裡。但它守的還是同一條紅線：卷證全文不在時
    要炸掉（ValueError），不准偷偷重播 `extraction` 區塊當成模型抽取結果。
    bedrock 成功路徑與 LLMError 傳遞由 test_live_plumbing 覆蓋。
    """
    state = CaseState(case_id="synthetic-unit-01")
    for mode in ("local",):
        try:
            n1_extract.run(state, _ctx(mode), case_fixture=_minimal_fixture())
        except NotImplementedError as e:
            assert_in("Bedrock", str(e), "raise 訊息要說明需要 Bedrock 憑證")
        else:
            raise AssertionError(f"RUN_MODE={mode} 時 N1 必須 raise，不得靜默回 fixture 資料")

    try:
        n1_extract.run(state, _ctx("bedrock"), case_fixture=_minimal_fixture())  # 無 documents
    except ValueError as e:
        assert_in("documents", str(e), "raise 訊息要說明缺的是卷證全文")
    else:
        raise AssertionError("bedrock 模式缺 documents 時必須 raise，不得靜默回 fixture 資料")


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


def _n3(intake: dict) -> Any:
    """跑**真的 N3 節點**（不是只呼叫 `compute()`）。

    炸點在呼叫端還是引擎端，決定了有沒有改對地方：2026-09-13 的
    `AttributeError` 是 `run()` 把 `None` 餵進 `compute()` 造成的，
    只測 `compute()` 會得出「引擎壞了」這個錯誤結論。
    """
    state = CaseState(case_id="synthetic-unit-01")
    state.classification = {"class": {"case_type": "違反建築法事件"}}
    state.intake = dict(intake)
    return n3_procedure.run(state, _ctx(), digest="")


def test_n3_refuses_instead_of_crashing_when_the_service_date_is_missing():
    """缺送達日時 N3 必須降級拒答，**不得拋例外**（2026-09-13 修）。

    原本的行為：`compute()` 的 `service_date` 型別標的是 `dt.date`，
    `personal`／`deposit` 兩條路都會走到 `_roc(eff)` → `None.year` → `AttributeError`
    → 整條 run 以 `run_failed` 收掉，聊天視窗只說「解析卷證失敗」。
    `public` 不會，它在 `compute()` 第一步就先拒答回傳——所以
    `graph.py` 那句「實測缺 d2 時 N3 的行為也正確」**只有三分之一成立**。

    **為什麼是主線不是邊角**：`backend/llm/prompts/n1_extract.md` 明寫
    「卷證沒有這種記載就 null」，而訴願書本來就常常沒寫送達日（那在原處分書上）。

    六格逐一測（三種送達方式 × 送達日有／無），**含會過的那幾格**——
    只測會壞的那兩格的話，「把三條路都改成拒答」這種過度修正不會被抓到。
    """
    for method in ("personal", "deposit", "public"):
        for d2 in ("2025-03-14", None):
            intake = {"service_method": method, "d3": "2025-04-07"}
            if d2:
                intake["d2"] = d2
            r = _n3(intake)  # 拋例外就是這條測試紅的方式
            cell = f"{method}/d2={'有' if d2 else '無'}"
            if method == "public" or not d2:
                assert_eq(r.data["deadline"]["deadline"], None, f"{cell} 不該算得出期滿日")
                assert_eq(r.degraded, True, f"{cell} 必須降級")
                assert_true(bool(r.degrade_reason), f"{cell} 降級卻沒給理由")
                assert_true(bool(r.data["deadline"]["caveats"]),
                            f"{cell} 沒有給承辦人看的說明")
            else:
                assert_true(r.data["deadline"]["deadline"] is not None,
                            f"{cell} 資料齊全卻算不出期滿日")
                assert_eq(r.degraded, False, f"{cell} 資料齊全不該降級")

    # 缺送達日的理由要說得出缺的是**哪一欄**，而且用中文標籤不是鍵名
    r = _n3({"service_method": "personal", "d3": "2025-04-07"})
    assert_in("送達日", r.degrade_reason or "", "降級理由沒說缺的是哪一欄")
    assert_true("d2" not in (r.degrade_reason or ""),
                f"降級理由漏出開發者鍵名：{r.degrade_reason!r}")


def test_n3_does_not_change_a_single_date_it_could_already_compute():
    """這一輪只處理「該拒答時拒答而不是崩潰」，**既有算出來的日期一個都不准變**。

    拿資料齊全的兩條路去比對直接呼叫引擎的結果，逐鍵相同。
    """
    for method in ("personal", "deposit"):
        r = _n3({"service_method": method, "d2": "2024-06-13", "d3": "2024-07-20"})
        direct = compute(method, dt.date(2024, 6, 13), dt.date(2024, 7, 20)).as_dict()
        assert_eq(r.data["deadline"], direct, f"{method}：N3 的輸出跟引擎不一致了")


def test_n3_says_so_when_a_date_field_cannot_be_read_at_all():
    """日期欄位存在但不是 ISO（`114/5/1`、民國字樣）同樣不得炸。

    `_date()` 走 `dt.date.fromisoformat`，非 ISO 會 raise `ValueError`。
    N1 的 prompt 只是**要求** ISO，不保證——bedrock 抽取回民國格式是真的會發生的。

    兩欄的後果不同級，訊息也不同：
    - `d2` 讀不出來 → 算不了，降級拒答。
    - `d3` 讀不出來 → 期滿日照算（`filing_date=None` 是合法輸入），但**逾期與否
      變成未判定**。那件事會安靜地發生，所以要寫進 caveats。
    """
    bad_d2 = _n3({"service_method": "personal", "d2": "114/5/1", "d3": "2025-04-07"})
    assert_eq(bad_d2.data["deadline"]["deadline"], None, "讀不出送達日卻算出了期滿日")
    assert_eq(bad_d2.degraded, True)
    assert_in("送達日", bad_d2.degrade_reason or "")
    assert_in("114/5/1", " ".join(bad_d2.data["deadline"]["caveats"]),
              "沒把讀不出來的原值寫出來，承辦人不知道要改哪裡")

    bad_d3 = _n3({"service_method": "personal", "d2": "2025-03-14", "d3": "民國114年4月7日"})
    assert_true(bad_d3.data["deadline"]["deadline"] is not None,
                "收文日讀不出來不該影響期滿日的計算")
    assert_eq(bad_d3.data["deadline"]["overdue"], None, "沒有收文日就不得判逾期")
    assert_in("收文日", " ".join(bad_d3.data["deadline"]["caveats"]),
              "逾期未判定這件事安靜地發生了，沒有寫進 caveats")


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


def test_unknown_case_type_fails_safe_to_blocked():
    """P1（對抗審查抓到）：案型辨識不出來時，原本會**解除**結論封鎖。

    把 case_type 換成系統不認得的值，requires_human_conclusion 就從 True 變 False——
    等於「分類失敗 = 放行」。方向完全相反：分類不出來代表系統更不了解這個案子。
    """
    unknown_procedural = {"requires_substantive_review": True, "clause": None}
    ok, signals = requires_human_conclusion(unknown_procedural, "未能分類", [], SUBSTANTIVE_TYPES)
    assert_eq(ok, True, "案型不明且程序未定案時必須保守封鎖結論")
    assert_true(signals, "必須說明為什麼被封鎖")

    ok_empty, _ = requires_human_conclusion(unknown_procedural, "", [], SUBSTANTIVE_TYPES)
    assert_eq(ok_empty, True, "案型空白同樣要封鎖")

    # 但逾期不受理是期間引擎直接算出來的（可驗算層），不該被這條 fail-safe 誤攔——
    # **前提是那個期間結果建立在承辦人確認過的欄位上**（判斷卡 7，2026-09-05 Ci 拍板）。
    overdue = {"requires_substantive_review": False, "clause": "77-2"}
    ok_confirmed, _ = requires_human_conclusion(
        overdue, "未能分類", [], SUBSTANTIVE_TYPES, procedural_inputs_confirmed=True
    )
    assert_eq(ok_confirmed, False, "承辦人確認過期間輸入後，程序上算出的不受理事由可解除封鎖")

    # 未確認時同一組輸入必須維持封鎖：覆核實測只要改一個模型抽的日期就能關掉封鎖，
    # 所以「程序上已算出不受理事由」這句話，在沒有人確認過那些日期之前不算數。
    ok_unconfirmed, sig_unconfirmed = requires_human_conclusion(
        overdue, "未能分類", [], SUBSTANTIVE_TYPES,
        procedural_inputs_confirmed=False, unconfirmed_fields=("d2", "d3"),
    )
    assert_eq(ok_unconfirmed, True, "期間輸入未經確認時，不得用程序結果解除封鎖")
    assert_true(
        any("未經承辦人確認" in s for s in sig_unconfirmed),
        "必須說明是因為欄位未確認才維持封鎖",
    )


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
    """未實作的模式必須 raise；已實作的 bedrock 分支則要走真模型、且說清楚自己是真模型。

    `bedrock` 自 2026-09-07 起是真分支（走 backend.llm.client.draft_sentences），
    所以不再列在 NotImplementedError 的清單裡。它守的還是同一條紅線：不准靜默
    回一份 fixture 模板句子，所以這裡順手釘住 `generation_mode`——bedrock 分支
    若哪天偷偷退回模板重播，這個斷言會先炸。工具接線、槽位封鎖與 LLMError 傳遞
    由 test_live_plumbing 覆蓋。
    """
    state = CaseState(case_id="synthetic-unit-01")
    state.screen = {"requires_human_conclusion": False, "deadline": {"steps": []}}
    for mode in ("local",):
        try:
            n5_draft.run(state, _ctx(mode), case_fixture=_minimal_fixture())
        except NotImplementedError as e:
            assert_in("Bedrock", str(e), "raise 訊息要說明需要 Bedrock 憑證")
        else:
            raise AssertionError(f"RUN_MODE={mode} 時 N5 必須 raise，不得靜默回 fixture 資料")

    def fake_draft(context, slots, retrieve_fn=None, **kw):
        return {"slots": {s: [] for s in slots}, "tool_calls": [], "usage": None, "model_id": "m"}

    orig = llm_client.draft_sentences
    llm_client.draft_sentences = fake_draft
    try:
        n5_draft.run(state, _ctx("bedrock"), case_fixture=_minimal_fixture())
    finally:
        llm_client.draft_sentences = orig
    assert_eq(state.draft["generation_mode"], "bedrock_live", "bedrock 分支不得退回模板重播")


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


def test_anaphoric_law_reference_resolves_to_antecedent():
    """「同法／本法第X條」必須綁回前文的法規，不能變成一部叫「又同法」的未知法規。

    這是編造條號最自然的後門：把假條號寫成「同法第999條」，若不做回指解析就只拿
    黃燈（庫外未驗證）而不擋。對抗審查實際打到這個洞。
    """
    ck = CitationChecker(SNAPSHOT)

    r = ck.check_text("按建築法第73條規定；又同法第999條、本法第888條亦有明文。")
    states = {c.raw: c.state for c in r}
    assert_eq(states.get("建築法第73條"), STATE_OK)
    assert_eq(states.get("建築法第999條"), STATE_MISSING, "「同法第999條」必須解析回建築法並判查無此號")
    assert_eq(states.get("建築法第888條"), STATE_MISSING, "「本法第888條」同樣要解析回建築法")
    assert_true(
        all("同法" not in c.raw and "本法" not in c.raw for c in r),
        "回指詞不得洩漏成法規名（先前會產生「又同法」這種假法規名）",
    )

    ok = ck.check_text("按建築法第73條規定；又同法第99條亦有明文。")
    assert_eq([c.state for c in ok], [STATE_OK, STATE_OK], "回指到真實存在的條號要判在庫，不得誤攔")


def test_anaphora_without_antecedent_is_visible_not_silent():
    """找不到前行詞時不猜是哪部法，但也不能靜靜放過——要留在畫面上是黃的。"""
    ck = CitationChecker(SNAPSHOT)
    r = ck.check_text("又同法第999條亦有明文。")
    assert_eq(len(r), 1, "沒有前行詞的回指仍要被抽出來，不得整個消失")
    assert_eq(r[0].state, STATE_OUT_OF_SCOPE, "無法解析時標庫外未驗證（黃），不冒充已驗證")
    assert_eq(lamp_for_states([]), "y", "沒有引用的句子必須是黃燈交人工，絕不可預設綠燈")


def test_chinese_numeral_articles_are_detected():
    """P0（對抗審查抓到）：國字條號原本完全漏抓。

    訴願決定書大量使用國字條號。漏抓的後果不只是少一筆引用——系統會回
    「本句未附引用」然後放行，也就是把「沒抓到」講成「沒有引用」。
    """
    ck = CitationChecker(SNAPSHOT)
    bad = ck.check_text("另參建築法第九百九十九條之規定。")
    assert_eq(len(bad), 1, "國字條號必須被抽出來")
    assert_eq(bad[0].state, STATE_MISSING, "國字寫的假條號一樣要判查無此號並阻擋")
    assert_eq(bad[0].raw, "建築法第999條", "顯示字串要正規化成阿拉伯數字")

    good = ck.check_text("按建築法第七十三條規定。")
    assert_eq(good[0].state, STATE_OK, "國字寫的真條號不得被誤攔")


def test_cn_numeral_parser():
    for s, want in [("七十三", 73), ("九百九十九", 999), ("十四", 14), ("二十", 20),
                    ("一百零五", 105), ("一百", 100), ("五", 5), ("一百零一", 101)]:
        assert_eq(cn_to_int(s), want, f"國字數字 {s} 轉換錯誤")
    assert_eq(cn_to_int("不是數字"), None, "非數字要回 None，不得亂猜")


def test_directive_citations_are_visible_not_silent():
    """P0（對抗審查抓到）：CONSTITUTION §2 明列函釋要可驗，但原本零檢查通道。

    快照沒有函釋白名單，所以正確行為是「庫外，未驗證」（黃燈、不擋、但看得見），
    **不是**整個抽不到然後綠燈放行。
    """
    ck = CitationChecker(SNAPSHOT)
    r = ck.check_text("參內政部112年5月1日台內營字第1120801234號函釋。")
    directives = [c for c in r if c.kind == "directive"]
    assert_eq(len(directives), 1, "函釋必須被抽出來")
    assert_eq(directives[0].state, STATE_OUT_OF_SCOPE)
    assert_eq(directives[0].lamp, "y")
    assert_eq(directives[0].blocking, False, "無法驗證不等於偽造，不阻擋但要標明")
    assert_true(
        all(c.kind != "precedent" for c in r),
        "函釋裡的「112年5月1日」不得被判解 regex 誤讀成幽靈判解字號",
    )


def test_precedent_type_coverage_includes_kang():
    """P1（對抗審查抓到）：字別白名單漏「抗」，那種引用會完全隱形。"""
    ck = CitationChecker(SNAPSHOT)
    r = ck.check_text("參最高行政法院112年度抗字第123號裁定。")
    assert_eq(len(r), 1, "抗字號必須被抽出來")
    assert_eq(r[0].state, STATE_OUT_OF_SCOPE)


def test_law_name_split_by_whitespace_still_detected():
    """PDF 抽取常把法規名用空白或換行拆開，不能因此漏抓。"""
    ck = CitationChecker(SNAPSHOT)
    for text in ("依 建 築 法 第 999 條", "另參建築\n法第999條"):
        r = ck.check_text(text)
        assert_true(r, f"{text!r} 應該要抽到引用")
        assert_eq(r[0].state, STATE_MISSING, f"{text!r} 的假條號要照樣被攔")


def test_conclusion_like_text_detection():
    assert_true(
        detect_conclusion_like("綜上，原處分認事用法均有違誤，應予撤銷，由原處分機關另為適法之處分。"),
        "主文型語句必須被偵測到",
    )
    assert_eq(
        detect_conclusion_like("查原處分認定之違規事實，有現場勘查紀錄附卷可稽。"),
        [],
        "一般理由段句子不得被誤判為主文",
    )


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
