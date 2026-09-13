"""端到端整合測試：兩個合成案例跑完六節點的行為驗收。

對應 plan 的驗收條件 2 與 3：
- 正常案例：exit 0、六節點結果齊全、每個欄位有 origin、三層分明
- 對抗案例：N6 必須攔下（結論封鎖 + 引用查無），不能誤放行
"""
from __future__ import annotations

import ast
import json
import pathlib
import tempfile

from backend import cli
from backend.config.origin_registry import check_payload
from backend.config.settings import (
    BLOCK_DECISION_INPUT_FIELDS,
    CONFIRMABLE_INTAKE_FIELDS,
    SYNTHETIC_DIR,
)
import backend.llm.chat as chat_mod
from backend.llm.chat import RefBook
from backend.retrieval.base import Hit
from backend.dossier import store
from backend.orchestrator import chat_bridge
from backend.config.settings import load_snapshot
from backend.nodes import n1_extract, n4_retrieval
from backend.orchestrator.graph import build_payload, list_synthetic_cases, load_case, run_case
from backend.orchestrator.runstore import load_run
from backend.tests.harness import assert_eq, assert_in, assert_true

ROOT = pathlib.Path(__file__).resolve().parents[2]
ORDINARY = "synthetic-ordinary-01"
BLOCKED = "synthetic-blocked-01"


def _payload(case_id: str) -> dict:
    return build_payload(run_case(case_id, mode="fixture"))


# ── 測資本身的紅線 ────────────────────────────────────────────────
def test_all_case_files_are_synthetic_prefixed():
    """CONSTITUTION §3：測資檔名一律 synthetic- 前綴。"""
    files = sorted(p.name for p in SYNTHETIC_DIR.glob("*.json"))
    assert_true(files, "至少要有合成案例")
    for f in files:
        assert_true(f.startswith("synthetic-"), f"{f} 不是 synthetic- 前綴")


def test_every_case_declares_provenance():
    for case_id in list_synthetic_cases():
        fx = load_case(case_id)
        prov = fx.get("provenance") or {}
        assert_true(prov.get("declaration"), f"{case_id} 缺合成測資聲明")
        assert_in("合成", prov["declaration"], f"{case_id} 的聲明必須明說是合成測資")


def test_non_synthetic_case_id_is_refused():
    """真實競賽資料不由本流程讀取——連 id 都不接受。"""
    try:
        load_case("real-case-001")
    except ValueError as e:
        assert_in("synthetic", str(e))
        return
    raise AssertionError("非 synthetic- 前綴的 case id 必須被拒絕")


# ── 正常案例 ──────────────────────────────────────────────────────
def test_ordinary_runs_all_six_nodes():
    p = _payload(ORDINARY)
    assert_eq(p["state"], "VERIFIED", "正常案例必須跑到 VERIFIED")
    timings = p["run_meta"]["node_timings"]
    assert_eq(sorted(timings.keys()), ["n1", "n2", "n3", "n4", "n5", "n6"], "六個節點都要跑過")
    assert_eq(
        p["history"],
        [
            "CREATED -> EXTRACTING",
            "EXTRACTING -> EXTRACTED",
            "EXTRACTED -> CLASSIFIED",
            "CLASSIFIED -> SCREENED",
            "SCREENED -> RETRIEVED",
            "RETRIEVED -> DRAFTED",
            "DRAFTED -> VERIFIED",
        ],
        "狀態轉換順序必須 deterministic",
    )


def test_ordinary_has_all_three_tiers():
    p = _payload(ORDINARY)
    for tier in ("可驗算", "有出處", "請人工判斷"):
        assert_true(p["tiers"][tier], f"三層誠實的「{tier}」層不得為空")


def test_ordinary_every_sentence_has_origin():
    p = _payload(ORDINARY)
    n = 0
    for block in p["doc"]:
        for s in block["ss"]:
            n += 1
            assert_true(s.get("origin"), f"句子 {s['id']} 缺 origin")
            assert_true(s.get("l"), f"句子 {s['id']} 缺燈號")
            assert_true(s.get("why"), f"句子 {s['id']} 缺 why")
            assert_eq(s["l_origin"], "rule", "燈號永遠由規則產出")
    assert_true(n >= 10, f"句子數過少（{n}），檢查組稿是否失敗")
    assert_eq(p["origin_violations"], [], "分層誠實檢查必須零違規")


def test_origin_violation_checker_actually_catches_violations():
    """對抗審查指出：`origin_violations` 恆為空，上面那條斷言其實恆真、不是防線。

    這條測試反過來驗**檢查器本身**——餵它一份違規 payload，它必須抓到。
    檢查器抓不到違規時，「零違規」這個結果就沒有意義。
    """
    clean = {"doc": [{"ss": [{"id": "s1", "origin": "llm", "l_origin": "rule", "why_origin": "rule"}]}]}
    assert_eq(check_payload(clean), [], "合規的 payload 不該被誤報")

    for bad, expect in [
        ({"doc": [{"ss": [{"id": "s1", "origin": "llm", "l_origin": "llm"}]}]}, "燈號標為 llm"),
        ({"doc": [{"ss": [{"id": "s1", "origin": "llm", "why_origin": "llm"}]}]}, "why 標為 llm"),
        ({"doc": [{"ss": [{"id": "s1"}]}]}, "缺 origin"),
        ({"doc": [{"ss": [{"id": "s1", "origin": "made_up"}]}]}, "origin 不在值域"),
        ({"citations": [{"raw": "x", "state_origin": "llm"}]}, "引用狀態標為 llm"),
    ]:
        got = check_payload(bad)
        assert_true(got, f"檢查器沒抓到違規：{expect}")


# 判斷卡 7（2026-09-05 Ci 拍板）之後，「正常案例可送出」多了一個前提：
# 承辦人要在收文頁確認過期間輸入欄位。**未確認是誠實的預設值**——
# 覆核實測證明，只要改一個模型抽出來的日期，整個結論封鎖就會被關掉。
# 所以同一個案例現在有兩個基準，兩個都要測。
def _write_tmp_case(case_id: str, fixture: dict) -> pathlib.Path:
    """把一份改過的 fixture 寫進暫存目錄，回傳可餵給 `run_case(data_dir=...)` 的路徑。"""
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / f"{case_id}.json").write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    return tmp


def _confirmed_payload(case_id: str) -> dict:
    """模擬承辦人在收文頁看過並採用 N1 抽出來的欄位。"""
    probe = run_case(case_id, mode="fixture")
    confirmed = {k: v for k, v in probe.intake.items() if k in CONFIRMABLE_INTAKE_FIELDS}
    return build_payload(run_case(case_id, mode="fixture", confirmed_intake=confirmed))


def test_ordinary_is_blocked_until_intake_is_confirmed():
    """未確認 → 封鎖。這是新的誠實基準，不是回歸。"""
    p = _payload(ORDINARY)
    assert_eq(p["screen"]["procedural_inputs_confirmed"], False, "預設就是沒有人確認過")
    assert_eq(p["screen"]["requires_human_conclusion"], True, "未確認時不得用程序結果解除封鎖")
    assert_eq(p["submit_allowed"], False, "未確認的案子不得標記為可送出")
    assert_true(
        any(b["reason"] == "conclusion_requires_human" for b in p["blockers"]),
        "要說得出是因為結論段交人工才擋",
    )


def test_ordinary_passes_the_gate_once_intake_is_confirmed():
    p = _confirmed_payload(ORDINARY)
    assert_eq(p["screen"]["procedural_inputs_confirmed"], True)
    assert_eq(p["blockers"], [], "確認後的正常案例不該有阻擋項")
    assert_eq(p["submit_allowed"], True)
    assert_eq(p["citation_counts"]["missing"], 0, "正常案例不得有查無此號的引用")


def test_confirming_intake_records_who_vouched_for_each_field():
    """確認不是靜靜改一個布林值：每個被確認的欄位都要在 payload 上看得出來。"""
    p = _confirmed_payload(ORDINARY)
    assert_true(p["intake_confirmed"], "intake_confirmed 是空的，看不出誰確認了什麼")
    for f in ("d2", "d3", "service_method"):
        assert_in(f, p["intake_confirmed"], f"{f} 應列在已確認欄位裡")
        assert_eq(p["intake_origin"][f], "human", f"{f} 的 origin 應翻成 human")


def test_null_values_in_confirmed_intake_do_not_count_as_confirmation():
    """送 null 不算確認——舊版會把 null 脅迫成 0／False 並照樣翻成 human。

    覆核實測：`{"transit_days": null}` 被 `int(v or 0)` 變成 0、
    `{"interested_party": null}` 被 `bool(v)` 變成 False，兩者都標成「承辦人確認過」，
    期滿日還因此從 2024-07-18 變成 2024-07-15。等於「送一個空值就能宣稱有人看過」。
    """
    all_null = {k: None for k in CONFIRMABLE_INTAKE_FIELDS}
    p = build_payload(run_case(ORDINARY, mode="fixture", confirmed_intake=all_null))
    assert_eq(p["intake_confirmed"], [], "全 null 不得列出任何已確認欄位")
    assert_eq(p["screen"]["procedural_inputs_confirmed"], False, "全 null 不得算已確認")
    assert_eq(p["screen"]["requires_human_conclusion"], True, "全 null 必須維持封鎖")
    assert_eq(p["submit_allowed"], False, "全 null 不得標成可送出")
    # 值也不准被動到
    base = _payload(ORDINARY)
    assert_eq(
        p["screen"]["deadline"]["deadline"], base["screen"]["deadline"]["deadline"],
        "送 null 竟然改變了期滿日——型別脅迫又跑到 None 檢查前面了",
    )


def test_a_single_null_field_leaves_that_field_untouched():
    """單欄 null：那一欄的值不變、origin 不翻，其餘欄位照常確認。"""
    probe = run_case(ORDINARY, mode="fixture")
    full = {k: v for k, v in probe.intake.items() if k in CONFIRMABLE_INTAKE_FIELDS}
    partial = dict(full, transit_days=None)
    p = build_payload(run_case(ORDINARY, mode="fixture", confirmed_intake=partial))
    assert_eq(p["intake_origin"]["transit_days"], "llm", "null 的欄位 origin 不得翻成 human")
    assert_eq(p["intake"]["transit_days"], probe.intake["transit_days"], "null 的欄位值不得被改動")
    assert_true("transit_days" not in p["intake_confirmed"], "null 的欄位不得列進 intake_confirmed")
    assert_in("d2", p["intake_confirmed"], "其餘欄位仍要正常確認")
    # 少一個期間輸入欄位 → 閘門仍關著
    assert_eq(p["screen"]["requires_human_conclusion"], True, "缺一欄未確認就不得解除封鎖")


def test_note_is_part_of_the_block_decision_and_must_be_confirmed():
    """`note` 餵進事實爭點偵測，所以它也是封鎖判斷的輸入（BLOCK_DECISION_INPUT_FIELDS）。

    覆核實測：五個期間欄位全確認之後把 note 清空，`requires_human_conclusion`
    由 True 翻成 False、`submit_allowed` 由 False 翻成 True——
    等於一個沒被納入確認範圍的欄位可以關掉封鎖。
    """
    assert_in("note", BLOCK_DECISION_INPUT_FIELDS, "note 必須列入封鎖判斷的輸入欄位")
    probe = run_case(ORDINARY, mode="fixture")
    full = {k: v for k, v in probe.intake.items() if k in CONFIRMABLE_INTAKE_FIELDS}
    without_note = {k: v for k, v in full.items() if k != "note"}
    p = build_payload(run_case(ORDINARY, mode="fixture", confirmed_intake=without_note))
    assert_in("note", p["screen"]["unconfirmed_procedural_fields"], "未確認的 note 要被列出來")
    assert_eq(p["screen"]["requires_human_conclusion"], True, "note 未確認時不得解除封鎖")
    assert_eq(p["submit_allowed"], False, "note 未確認時不得標成可送出")


def test_changing_extracted_dates_cannot_unlock_the_conclusion_block():
    """對抗測試（判斷卡 7 的核心）：改抽取日期不得解除結論封鎖。

    覆核打穿的那條路徑：改 `d2`／`d3` → 案件變逾期 → `art77.clause=77-2` →
    `requires_substantive_review` 變 False → 封鎖關掉 → 捏造主文拿綠燈、
    列進「有出處」、`submit_allowed=True`。這條把它釘死。
    """
    blocked_case = load_case(BLOCKED)
    # 原本未逾期（會因「須進實體審查」而封鎖）；把日期改成明顯逾期
    for d2, d3 in (("2020-01-01", "2025-04-07"), ("2025-03-14", "2030-01-01")):
        fixture = json.loads(json.dumps(blocked_case))
        fixture["extraction"]["intake"]["d2"] = d2
        fixture["extraction"]["intake"]["d3"] = d3
        tmp = _write_tmp_case("synthetic-datehack-01", fixture)
        p = build_payload(run_case("synthetic-datehack-01", mode="fixture", data_dir=tmp))
        assert_eq(
            p["screen"]["art77"]["clause"], "77-2",
            f"前提檢查：d2={d2} d3={d3} 應該被算成逾期，否則這條測試沒測到東西",
        )
        assert_eq(
            p["screen"]["requires_human_conclusion"], True,
            f"改抽取日期（d2={d2} d3={d3}）就解除了結論封鎖——判斷卡 7 的破口又開了",
        )
        assert_eq(p["submit_allowed"], False, "未確認狀態下不得標記為可送出")

    # 同一份資料，經過承辦人確認之後才可以解除
    fixture = json.loads(json.dumps(blocked_case))
    fixture["extraction"]["intake"]["d2"] = "2020-01-01"
    fixture["extraction"]["intake"]["d3"] = "2025-04-07"
    tmp = _write_tmp_case("synthetic-datehack-02", fixture)
    confirmed = {k: v for k, v in fixture["extraction"]["intake"].items()
                 if k in CONFIRMABLE_INTAKE_FIELDS}
    q = build_payload(run_case("synthetic-datehack-02", mode="fixture", data_dir=tmp,
                               confirmed_intake=confirmed))
    assert_eq(q["screen"]["requires_human_conclusion"], False,
              "承辦人確認過之後，程序上算出的不受理事由才可以解除封鎖")


def test_ordinary_deadline_matches_known_vector():
    """本案的期間結果對齊 test-vectors.json 的 hist-113-03 向量（寄存 113/6/13 → 113/7/15 逾期）。"""
    p = _payload(ORDINARY)
    dl = p["screen"]["deadline"]
    assert_eq(dl["deadline"], "2024-07-15")
    assert_eq(dl["overdue"], True)
    assert_eq(p["screen"]["art77"]["clause"], "77-2")
    # 判斷卡 7：期間算出來是一回事，能不能用它解除結論封鎖是另一回事。
    # 未確認 → 仍封鎖；確認後 → 才不封鎖。
    assert_eq(p["screen"]["requires_human_conclusion"], True, "未確認時逾期案仍維持封鎖")
    assert_eq(
        _confirmed_payload(ORDINARY)["screen"]["requires_human_conclusion"], False,
        "承辦人確認後，程序逾期案不封鎖結論段",
    )


# ── N4 獨立檢索：查詢句不得由草稿倒推（2026-09-05 Ci 拍板）─────────
#
# 這三條取代了舊的 `test_ordinary_cite_ids_all_resolve`。那條當初斷言的是
# 「草稿標的 L* 都解析得到 N4 的檢索結果」，但**在舊架構下它是恆真的**——
# N4 的查詢句就是拿草稿的引用去組的，草稿引用什麼就一定查得到什麼。
# 它看起來在驗「引用可驗」，實際上只驗到「我們把答案抄進了題目」。
# 改成獨立檢索之後那條更沒有鑑別力（cite_ids 已不帶入，迴圈根本不執行），所以刪掉，
# 換成下面三條**會因為有人把倒推路徑接回去而變紅**的斷言。

def test_retrieval_query_is_not_derived_from_the_draft():
    """N4 查詢句只能由**案情**組成，不得含任何草稿內容。

    `case` 是卷證原文本身（2026-09-12 加進查詢來源）——它比 N1 更上游，
    是最正當的案情來源；加它的原因見 `n4_retrieval.build_query_sources`
    （法條查表原本只讀 N1 的摘要，條號有沒有被抄進摘要決定了 laws 是 1 筆還是 0 筆）。
    白名單放寬到 `case` **不影響這條測試要守的東西**：要防的是「拿草稿倒推查詢」，
    所以下面另外明確斷言 n5／draft 一個都不准出現，而不是只靠白名單間接擋。
    """
    for case_id in (ORDINARY, BLOCKED):
        meta = _payload(case_id)["retrieval"]["retrieval_meta"]
        sources = meta.get("query_sources")
        assert_true(sources, f"{case_id}：retrieval_meta 沒有 query_sources，無法稽核查詢句來源")
        for src in sources:
            origin_node = str(src["from"]).split(".")[0]
            assert_in(
                origin_node,
                ("case", "n1", "n2", "n3"),
                f"{case_id}：查詢句來源 {src['from']!r} 不是案情來源——草稿倒推的路徑被接回來了",
            )
            assert_true(
                "n5" not in str(src["from"]) and "draft" not in str(src["from"]),
                f"{case_id}：查詢句來源 {src['from']!r} 來自草稿，那是照著答案查",
            )


def test_adversarial_citation_never_enters_the_query():
    """對抗案例植入的假法條只存在於草稿。它一旦出現在查詢句裡，就是倒推復活了。

    這條是上面那條的**具體反例版**：`建築法第999條` 在卷證、案型、程序結果裡都不存在，
    唯一的來源是 N5 的草稿。查詢句裡出現它 = 查什麼是照著答案填的。
    """
    meta = _payload(BLOCKED)["retrieval"]["retrieval_meta"]
    assert_true(
        "建築法第999條" not in meta["query_text"],
        "查詢句含有只存在於草稿的假法條，代表 N4 又在拿草稿當查詢來源",
    )
    assert_true(
        "第999條" not in json.dumps(meta.get("query_sources"), ensure_ascii=False),
        "query_sources 裡出現草稿才有的條號",
    )


def test_gate_catches_the_fake_citation_even_though_retrieval_never_found_it():
    """守門不靠檢索結果——這是獨立檢索之後最重要的一條安全性質。

    改成獨立檢索後，`建築法第999條` 不會出現在 `laws[]`（案情裡沒有任何訊號指向它）。
    如果引用查核是靠「比對檢索結果」做的，這一改就會讓假法條**靜靜通過**。
    這條釘住：查核走的是「從句子本文抽引用 → 對快照查條號」，跟檢索到什麼無關。
    """
    p = _payload(BLOCKED)
    law_titles = {l["t"] for l in p["laws"]}
    assert_true(
        "建築法第999條" not in law_titles,
        "前提已變：獨立檢索竟然命中了草稿才有的假法條，這條測試的設計需要重看",
    )
    missing = [c for c in p["citations"] if c["state"] == "missing"]
    assert_true(missing, "假法條沒有被判成 missing")
    assert_in("建築法第999條", {c["raw"] for c in missing}, "被判 missing 的不是那個假法條")
    assert_eq(p["submit_allowed"], False, "假法條沒有擋下送出")


def test_fixture_draft_cite_ids_are_not_carried_into_doc():
    """fixture 草稿手寫的 cite_ids 不得進 doc[]（narrative.CARRY_DRAFT_CITE_IDS_DEFAULT）。

    那些 id 是在 N4 跑之前手填的，改成獨立檢索後只會靠序號巧合對上不相干的法條，
    或對不上而製造假 blocker。兩種都是假訊號。

    **條文引述句（`quoted_statute`）不在此列**（2026-09-13）：那幾句的 cite_ids 是
    `narrative.article_quotes()` 自己從 N4 的 `laws[]` 對出來的，指向的就是它引述的
    那一條，不是 fixture 手寫的。這條擋的是「模型／fixture 說它引了什麼」，
    不是「編排層查表查到了什麼」。
    """
    for case_id in (ORDINARY, BLOCKED):
        p = _payload(case_id)
        for block in p["doc"]:
            for s in block.get("ss", []):
                if s.get("quoted_statute"):
                    continue
                assert_eq(
                    s.get("cite_ids"),
                    [],
                    f"{case_id}：句子 {s['id']} 帶進了 fixture 的 cite_ids {s.get('cite_ids')}",
                )


def test_retrieval_divergence_is_reported_not_hidden():
    """獨立檢索之後，「草稿引用的」與「檢索找到的」本來就會有落差——落差要外顯。

    這是這次改動最容易被誤讀的地方：對抗案例的 `laws[]` 裡看不到 `建築法第73條`，
    不是檢索壞了，是**案情裡沒有任何訊號指向那個條號**（條號只寫在原處分書上，
    Phase 0 沒有 PDF 視覺抽取）。這種落差如果只是靜靜地讓左欄少幾張卡片，
    看的人會以為系統認可了草稿的引用。所以 payload 要把兩邊的差集講出來。
    """
    p = _payload(BLOCKED)
    div = p["retrieval_divergence"]
    assert_in("建築法第999條", div["cited_not_retrieved"], "草稿引用但檢索未命中的清單漏了假法條")
    assert_in("建築法第73條", div["cited_not_retrieved"], "草稿引用但檢索未命中的清單漏了實體法條")
    assert_true(div["retrieved_not_cited"], "檢索到但草稿沒引用的清單是空的——獨立檢索應該會有這種項目")
    assert_true(bool(div.get("note")), "落差清單沒有附說明，讀的人會誤以為是檢索壞了")


# ── 對抗案例：守門必須攔下 ────────────────────────────────────────
def test_blocked_case_blocks_submission():
    """SC-03 的核心：這條沒過視同 P0。"""
    p = _payload(BLOCKED)
    assert_eq(p["submit_allowed"], False, "對抗案例必須阻擋送出")
    assert_true(p["blockers"], "必須列出阻擋原因，不能只 disable 按鈕")
    reasons = {b["reason"] for b in p["blockers"]}
    assert_in("citation_missing", reasons, "查無此號的引用必須進 blockers")


def test_blocked_case_conclusion_is_placeholder_not_generated():
    p = _payload(BLOCKED)
    assert_eq(p["screen"]["requires_human_conclusion"], True)
    conclusions = [s for b in p["doc"] for s in b["ss"] if s["slot"] == "conclusion"]
    assert_eq(len(conclusions), 1, "封鎖時結論段應為單一佔位句")
    c = conclusions[0]
    assert_eq(c["origin"], "human_required", "結論段不得由模型生成")
    assert_eq(c["placeholder"], True)
    assert_eq(c["l"], "r")


def test_blocked_case_fixture_conclusion_text_never_leaks():
    """fixture 裡確實寫了一句『原處分撤銷。』，它必須永遠不出現在輸出裡。"""
    fx = load_case(BLOCKED)
    leaked = fx["draft_fixture"]["conclusion"][0]["t"]
    p = _payload(BLOCKED)
    dumped = json.dumps(p["doc"], ensure_ascii=False)
    assert_true(leaked not in dumped, f"被封鎖的結論句 {leaked!r} 洩漏進 doc[]")


def test_conclusion_block_cannot_be_bypassed_via_reasoning_slot():
    """P0（對抗審查抓到）：把主文寫進理由段可以整個穿過 C 型結論封鎖。

    原本的封鎖只把 `conclusion` 這個 key 從 slots 刪掉，只檢查 `slot == "conclusion"`。
    模型（接上 Bedrock 後）在理由段末尾寫「綜上，原處分應予撤銷」就實質完成了結論，
    而 `submit_allowed` 照樣是 true。fixture 檔位下不會爆，一接 live 就是真破口。

    這條測試直接複現那個攻擊：把主文句塞進 reasoning，斷言必須被擋下來。
    """
    fx = load_case(BLOCKED)
    fx["draft_fixture"]["reasoning"].append(
        {
            "t": "綜上，原處分認事用法均有違誤，應予撤銷，由原處分機關另為適法之處分。",
            "cite_ids": [],
            "basis": None,
            "source_kind": "law",
            "adversarial": True,
            "adversarial_note": "刻意把主文寫進理由段，測試結論封鎖能否被繞過。",
        }
    )
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / "synthetic-bypass-01.json").write_text(json.dumps(fx, ensure_ascii=False), encoding="utf-8")
    p = build_payload(run_case("synthetic-bypass-01", mode="fixture", data_dir=tmp))

    assert_eq(p["submit_allowed"], False, "把主文寫進理由段必須照樣被阻擋")
    reasons = {b["reason"] for b in p["blockers"]}
    assert_in("conclusion_like_text_outside_conclusion_slot", reasons)
    leaked = [s for b in p["doc"] for s in b["ss"] if "應予撤銷" in (s["t"] or "")]
    assert_eq(len(leaked), 1)
    assert_eq(leaked[0]["l"], "r", "洩漏的主文句必須打紅燈")
    assert_eq(leaked[0]["tier"], "請人工判斷")


def test_adversarial_flag_is_carried_into_doc():
    """對抗測資裡刻意注入的假引用，在輸出 doc[] 裡必須帶著標記。

    否則任何人只截 doc[]（或把它貼進簡報）就會看到一句沒有註記的假法條。
    """
    p = _payload(BLOCKED)
    adv = [s for b in p["doc"] for s in b["ss"] if s.get("adversarial")]
    assert_true(adv, "對抗注入的句子必須在 doc[] 裡帶 adversarial 旗標")
    assert_true(any("999" in (s["t"] or "") for s in adv), "帶旗標的應該是那句假法條")
    for s in adv:
        assert_true(s.get("adversarial_note"), "帶旗標的句子必須說明為什麼是刻意注入")


def test_blocked_case_has_handoff_with_three_questions():
    """US-8 AC-8.2：至少 3 個具體問題 + 偵測訊號清單。"""
    p = _payload(BLOCKED)
    h = p["handoff"]
    assert_true(len(h["questions"]) >= 3, f"交接卡問題數不足（{len(h['questions'])}）")
    assert_true(h["signals"], "交接卡必須列出偵測訊號，讓人知道為什麼被封鎖")


def test_blocked_case_flags_the_injected_bad_citation():
    p = _payload(BLOCKED)
    bad = [c for c in p["citations"] if c["state"] == "missing"]
    assert_true(bad, "刻意注入的錯誤引用必須被抓到")
    assert_in("999", bad[0]["raw"])
    assert_eq(bad[0]["lamp"], "r")
    assert_eq(bad[0]["state_origin"], "rule", "引用狀態永遠由規則產出")


def test_blocked_case_high_severity_issue_detected():
    p = _payload(BLOCKED)
    high = [i for i in p["screen"]["fact_issues"] if i["severity"] == "high"]
    assert_true(high, "對抗案例必須偵測到高風險事實認定爭點")
    assert_true(high[0]["matched_keywords"], "必須說明命中哪些關鍵詞")
    assert_eq(high[0]["src"], "AI 不得代為認定，請承辦人核對卷證")


# ── 誠實留白的驗收 ────────────────────────────────────────────────
def test_similar_cases_always_empty_and_labeled():
    """CONSTITUTION §2：不編造任何相似案內容或字號。"""
    for case_id in list_synthetic_cases():
        p = _payload(case_id)
        assert_eq(p["retrieval"]["cases"], [], f"{case_id} 的相似案通道必須回空")
        meta = p["retrieval"]["retrieval_meta"]["similar_case_channel"]
        assert_eq(meta["label"], "庫外，未驗證")


def test_no_fabricated_numbers_in_run_meta():
    """沒有呼叫模型就不給 model id、不給 token 數、不給 recall 數字。"""
    for case_id in list_synthetic_cases():
        state = run_case(case_id, mode="fixture")
        p = build_payload(state)
        assert_eq(p["run_meta"]["model_ids"], None, "fixture 檔位不得謊報 model id")
        assert_eq(state.draft["usage"], None, "沒呼叫模型就沒有 token 用量")
        assert_eq(p["retrieval"]["retrieval_meta"]["recall_at5_last_eval"], None, "沒量測就不給 recall 數字")
        assert_eq(
            p["classification"]["class"]["expected_outcome_prior"], None, "沒有資料集分布就不給機率"
        )


def test_degradation_is_always_visible():
    """降級可以，隱瞞不行：每個降級都要有 reason，且進 run_meta.degraded。"""
    for case_id in list_synthetic_cases():
        p = _payload(case_id)
        for d in p["run_meta"]["degraded"]:
            assert_true(d["reason"], f"{case_id} 的 {d['node']} 降級沒說明原因")
        degraded_nodes = {d["node"] for d in p["run_meta"]["degraded"]}
        assert_in("n4", degraded_nodes, "相似案通道不可用必須外顯為降級")
        assert_in("n5", degraded_nodes, "fixture 重播草稿必須外顯為降級")


def test_cli_exit_codes():
    assert_eq(cli.main(["--case", ORDINARY, "--quiet"]), 0, "正常案例 CLI 必須 exit 0")
    assert_eq(cli.main(["--case", BLOCKED, "--quiet"]), 0, "對抗案例流程本身跑完，也是 exit 0（攔下是正確行為）")
    assert_eq(cli.main(["--case", "synthetic-does-not-exist"]), 1, "找不到案例要 exit 1")


# ── `to_node`：停在中途的部分執行（契約 v2 §0.1）──────────────────────

def test_to_node_n3_stops_at_screened_and_leaves_no_draft():
    """聊天的「解析卷證」工具只跑 n1–n3。

    釘住三件事，換一個看似合理的實作就會紅：
    - 終態是 `SCREENED`（不是 `VERIFIED`，也不是失敗）——`STATE_AFTER` 的映射本來就在，
      這條驗的是迴圈真的有上界。
    - **沒有草稿**。停在 n3 卻生得出草稿，代表上界沒生效。
    - `node_timings` 只含跑過的節點：多一個 key 就是虛報一次沒發生的執行。
    """
    state = run_case(ORDINARY, mode="fixture", to_node="n3")
    assert_eq(state.run_meta["final_state"], "SCREENED", "停在 n3 的終態")
    assert_true(not state.draft, f"停在 n3 不該有草稿，實得 {state.draft!r}")
    assert_eq(sorted(state.run_meta["node_timings"]), ["n1", "n2", "n3"], "只跑 n1–n3")
    assert_eq(state.run_meta["to_node"], "n3", "run_meta 要記下上界")
    # 沒跑 N5 就不該報草稿的 model id（fixture 檔位本來就全 None，這裡驗的是不虛報）
    assert_true(state.screen, "n3 的程序審查結果要在")


def test_to_node_n5_is_refused_because_it_would_leave_an_unguarded_draft():
    """紅線：停在 N5 ＝ 有草稿但沒過 N6 引用守門。

    `graph.py` 的既有不變量是「續跑的終態一定經過守門」。`to_node` 開了一個能繞過它的
    口子，所以這個值必須被拒絕——**不是**靜默改跑到 n6，那會讓呼叫端以為自己要到了 n5。
    """
    try:
        run_case(ORDINARY, mode="fixture", to_node="n5")
    except ValueError as e:
        assert_true("n5" in str(e), f"錯誤訊息要指出是 n5：{e}")
        return
    raise AssertionError("to_node='n5' 必須拋 ValueError")


def test_to_node_must_be_a_real_node_and_must_not_precede_from_node():
    """兩種壞輸入都要當場炸，不要跑出一個空的 run。"""
    for bad in ("n7", "N3", ""):
        try:
            run_case(ORDINARY, mode="fixture", to_node=bad)
        except ValueError:
            continue
        raise AssertionError(f"to_node={bad!r} 必須拋 ValueError")
    probe = run_case(ORDINARY, mode="fixture", to_node="n3")
    try:
        run_case(ORDINARY, mode="fixture", base_state=probe, from_node="n4", to_node="n3")
    except ValueError as e:
        assert_true("早於" in str(e), f"要說清楚是順序問題：{e}")
        return
    raise AssertionError("to_node 早於 from_node 必須拋 ValueError")


def test_default_to_node_still_runs_all_six_nodes():
    """不傳 `to_node` 的既有行為一個字都不變（這是所有既有呼叫端的保護網）。"""
    state = run_case(ORDINARY, mode="fixture")
    assert_eq(sorted(state.run_meta["node_timings"]), ["n1", "n2", "n3", "n4", "n5", "n6"],
              "預設仍跑滿六節點")
    assert_eq(state.run_meta["to_node"], "n6", "預設上界是 n6")


def test_resume_from_n4_after_a_screened_run_reaches_verified_without_rerunning_n1_n3():
    """聊天的「生成草稿」工具：接著 SCREENED 的 run 續跑 n4–n6。

    這條同時驗「部分執行的 run 可以當 base_state 續跑」——若 `to_node` 讓 run 少了
    某個續跑必需的上游欄位，這裡會炸。
    """
    base = run_case(ORDINARY, mode="fixture", to_node="n3")
    state = run_case(ORDINARY, mode="fixture", base_state=base, from_node="n4")
    assert_eq(state.run_meta["final_state"], "VERIFIED", "續跑要跑到守門")
    assert_eq(sorted(state.run_meta["node_timings"]), ["n4", "n5", "n6"], "不得重跑 n1–n3")
    assert_eq(state.run_meta["base_run_id"], base.run_id, "要指得回上一次")


def test_node_done_events_carry_the_same_elapsed_ms_as_run_meta_node_timings():
    """契約 v2 §2.3 ③：`tool_step.elapsed_ms` 是**真值，不是另外估的**。

    聊天的 pipeline 工具只是把 `node_done` 原樣轉成 `tool_step`，所以「沒有另外估」
    這件事真正要釘在**事件來源**這一層：`node_done.elapsed_ms` 必須就是
    `run_meta.node_timings[node]` 那個數字。

    兩邊各自算一次也會「看起來對」（同一次執行的時間本來就接近），差別在
    `time.perf_counter()` 呼叫的位置不同會差幾毫秒——那種不一致沒有症狀，
    只會讓畫面上的秒數跟紀錄裡的秒數對不起來，沒有人查得出為什麼。
    """
    seen: dict[str, int] = {}

    def on_event(kind: str, data: dict) -> None:
        if kind == "node_done":
            seen[data["node"]] = data["elapsed_ms"]

    state = run_case(ORDINARY, mode="fixture", on_event=on_event)
    assert_eq(seen, state.run_meta["node_timings"],
              "node_done 的 elapsed_ms 與 run_meta.node_timings 必須是同一個值")


def test_node_done_events_report_degradation_from_the_node_not_from_a_guess():
    """`degraded` 同理：轉發的那一層不判斷、只轉發，所以真值在這裡釘。"""
    reported: dict[str, bool] = {}

    def on_event(kind: str, data: dict) -> None:
        if kind == "node_done":
            reported[data["node"]] = bool(data["degraded"])

    state = run_case(ORDINARY, mode="fixture", on_event=on_event)
    from_meta = {d["node"] for d in state.run_meta["degraded"]}
    assert_eq({n for n, v in reported.items() if v}, from_meta,
              "事件說降級的節點，要跟 run_meta.degraded 記的是同一批")


# ── 聊天層與六節點的橋（`backend/orchestrator/chat_bridge.py`）────────
#
# 這一段跑的是**真的 adapter 配真的 run_case**（fixture 檔位）。
# adapter 原本寫在 `backend/api/chat.py` 裡，那個檔頂層 import fastapi，
# 於是這段 `CaseState` → dict 的轉換一行都跑不到——而它正是 Epic C 要接的東西。

def test_the_four_chips_get_a_draft_without_touching_a_single_rest_endpoint():
    """**整條 demo 動線**：解析卷證 → 查法規 → 查相似案 → 生成草稿，全部走 chip。

    2026-09-13 這條是斷的，而且斷在兩個地方：
    1. `tool_hint` 被丟掉，chip 跑去 `read_case`（`ece113d` 修）。
    2. **查到的東西不進卷宗**（契約 §3.0 的「歸檔到」那一欄從來沒被實作），
       所以 `generate_decision_draft` 的前置條件 3 讀 manifest 讀到空的，
       正確地拒絕生成。承辦人看到的是「生成草稿永遠都是空的」。

    這條測試釘住的是**接起來會動**——兩段各自的單元測試都綠，而動線是斷的。
    """
    with tempfile.TemporaryDirectory() as tmp:
        cases_dir = pathlib.Path(tmp) / "cases"
        store.ensure(ORDINARY, cases_dir=cases_dir)

        def counts():
            m = store.load(ORDINARY, cases_dir)
            return len(m["laws"]), len(m["references"]), len(m["artifacts"])

        assert_eq(counts(), (0, 0, 0), "起點應該是空卷宗")

        last = _press_every_chip(ORDINARY, cases_dir)

        laws, refs, artifacts = counts()
        assert_true(laws > 0, "查法條查到了，卻沒有一筆進卷宗")
        assert_true(refs > 0, "查相似案查到了，卻沒有一筆進卷宗")
        assert_true(artifacts > 0, "草稿沒有登記進卷宗")
        assert_true((last.get("cite_count") or 0) > 0,
                    f"草稿生出來了但一處引用都沒有：{last}")
        assert_true(last.get("artifact_id"), f"沒有 artifact_id：{last}")


def _press_every_chip(case_id: str, cases_dir: pathlib.Path) -> dict:
    """依序按四個 chip，回傳最後一張工具卡。**中間不碰任何 REST 端點。**"""
    snapshot = load_snapshot()
    events: list = []
    chat_mod._throttle = lambda: None
    run_id = None
    sections: dict = {k: None for k in chat_bridge.PAYLOAD_SECTIONS}
    law_query = case_query = ""

    for hint in ("extract_case_document", "search_regulations",
                 "search_similar_decisions", "generate_decision_draft"):
        tools = chat_mod.ChatTools(
            sections, RefBook(), _StubSimilarCases(), snapshot,
            lambda n, d: events.append((n, d)),
            run_pipeline=chat_bridge.pipeline_adapter(
                case_id, cases_dir, mode="fixture", persist=True),
            run_id=run_id,
            case_manifest=chat_bridge.load_case_manifest(case_id, cases_dir),
            archive=chat_bridge.archive_adapter(cases_dir=cases_dir, case_id=case_id),
            law_query=law_query, case_query=case_query,
        )
        tools.run_tool_hint(hint)
        run_id = tools.run_id or run_id
        if run_id:
            state = load_run(run_id)
            names = list((snapshot.get("laws") or {}).keys())
            law_query = n4_retrieval.build_query(state, names)
            case_query = n4_retrieval.build_case_query(state)
            sections = {k: build_payload(state).get(k)
                        for k in chat_bridge.PAYLOAD_SECTIONS}
    return [d for n, d in events if n == "tool_result"][-1]


class _StubSimilarCases:
    """相似案 KB 在雲上，本機沒有憑證。**回一筆形狀正確的**——包含
    `payload["kb_kind"]`，因為歸檔要靠它把 S3 key 拼回來。"""

    name = "similar_cases"

    def search(self, query, top_k=5, filters=None, **kw):  # noqa: ANN001
        return [Hit(id="kb-1", title="1151090848_駁回", score=0.71,
                    source="新北訴願決定書_環保局全量/1151090848_駁回.txt",
                    payload={"kb_kind": "public", "outcome": "駁回",
                             "provenance": "public_crawl", "category": None,
                             "text": "（命中的 chunk，不是全文）"})]


def test_archiving_the_same_hits_twice_does_not_grow_the_dossier():
    """連按兩次「查法條」，卷宗不得出現重複的同一條，也不得多出一批。

    這是 `id` 穩定性的端到端版：`store.add_items` 依 `id` 去重，而 `id` 是
    法名＋條號推導的。用 RefBook 的回合序號（`c1`／`c2`）當 id 的話，
    第二次的 `c1` 跟第一次的 `c1` 是不同的法條，去重就失效。
    """
    with tempfile.TemporaryDirectory() as tmp:
        cases_dir = pathlib.Path(tmp) / "cases"
        store.ensure(ORDINARY, cases_dir=cases_dir)
        archive = chat_bridge.archive_adapter(ORDINARY, cases_dir)
        chat_mod._throttle = lambda: None

        def press_law_chip():
            chat_mod.ChatTools({}, RefBook(), None, load_snapshot(), lambda n, d: None,
                               archive=archive, law_query="訴願法第77條"
                               ).run_tool_hint("search_regulations")

        press_law_chip()
        first = [x["id"] for x in store.load(ORDINARY, cases_dir)["laws"]]
        press_law_chip()
        second = [x["id"] for x in store.load(ORDINARY, cases_dir)["laws"]]

        assert_eq(first, ["lawtable:訴願法-77"], "id 不是法名＋條號推導的")
        assert_eq(second, first, "按第二次卷宗長出了重複的東西")


def test_a_full_text_fetch_failure_still_files_the_document_and_says_why():
    """抓全文失敗**不得讓歸檔失敗**。

    那會把「這份決定書加進卷宗了、只是沒快取全文」變成「加不進去」，
    兩者差很多。抓不到就留空並在 `note` 說明。
    """
    item = {"id": "kb/public/決定書/1.txt", "t": "1_駁回", "src": "決定書/1.txt",
            "note": "", "score": None, "provenance": "public_crawl",
            "doc_kind": "decision", "verdict": "駁回", "category": None,
            "full_cached": "", "channel": "corpus"}

    def boom(_key):
        raise RuntimeError("S3 掛了")

    with tempfile.TemporaryDirectory() as tmp:
        cases_dir = pathlib.Path(tmp) / "cases"
        store.ensure(ORDINARY, cases_dir=cases_dir)

        chat_bridge.archive_adapter(ORDINARY, cases_dir, fetch_text=boom)(
            "references", [dict(item)])
        filed = store.load(ORDINARY, cases_dir)["references"]
        assert_eq(len(filed), 1, "抓全文失敗把整筆歸檔帶走了")
        assert_eq(filed[0]["full_cached"], "", "抓失敗卻有全文？")
        assert_in(chat_bridge.NOTE_FULLTEXT_FAILED, filed[0]["note"])

    # 沒有注入抓取器時（測試／還沒設好 S3 的檔位）也要說得出來
    with tempfile.TemporaryDirectory() as tmp:
        cases_dir = pathlib.Path(tmp) / "cases"
        store.ensure(ORDINARY, cases_dir=cases_dir)
        chat_bridge.archive_adapter(ORDINARY, cases_dir)("references", [dict(item)])
        filed = store.load(ORDINARY, cases_dir)["references"]
        assert_in(chat_bridge.NOTE_NO_FULLTEXT_FETCHER, filed[0]["note"])


def test_the_lawtable_id_never_goes_looking_for_an_s3_object():
    """`lawtable:…` 的 id 在 S3 上沒有對應物件，不得拿去抓全文。

    抓了只會白拿一個 404，而第四批剛把那條路徑翻成「找不到這份文件」——
    於是右欄會出現一批寫著「全文未快取（讀母庫失敗）」的法條，
    看起來像 S3 壞了，其實是我們問錯了地方。
    """
    asked: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        cases_dir = pathlib.Path(tmp) / "cases"
        store.ensure(ORDINARY, cases_dir=cases_dir)
        archive = chat_bridge.archive_adapter(
            ORDINARY, cases_dir, fetch_text=lambda k: asked.append(k) or "x")
        archive("laws", [{"id": "lawtable:訴願法-77", "t": "訴願法第77條",
                          "note": "", "body_cached": "", "channel": "lawtable"}])
    assert_eq(asked, [], f"拿 lawtable 的 id 去 S3 問了：{asked}")


# ── 查表通道的法條要帶著條文（2026-09-13）───────────────────────────
#
# 查表通道只驗條號存在性，`laws-snapshot.json` 一個字的條文都沒有，
# 所以 `lawtable:` 那批 `body_cached` 一直是空的、點開只有一句
# 「條號存在性驗證，非法條全文」。條文在母庫（`kb/public/相關法規_全量/`）。

_LAW_FULLTEXT = """行政程序法

第 71 條
行政機關之送達，應注意其期間。

第 72 條
送達，於應受送達人之住居所、事務所或營業所為之。

第 73 條
於應受送達處所不獲會晤應受送達人時。
"""


def _law_item() -> dict:
    """`chat._law_items()` 產出的形狀（含歸檔層用完就丟的 `_lawtable`）。"""
    return {"id": "lawtable:行政程序法-72", "t": "行政程序法第72條", "src": "",
            "note": "條號存在性驗證，非法條全文（快照只索引條號）",
            "verified": True, "relevance": "unknown", "body_cached": "",
            "channel": "lawtable",
            chat_mod.ARCHIVE_LAWTABLE_META: {"law": "行政程序法", "article": "72"}}


def _file_law(fetch_text=None, find_statute_key=None, item=None, article_text=None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        cases_dir = pathlib.Path(tmp) / "cases"
        store.ensure(ORDINARY, cases_dir=cases_dir)
        chat_bridge.archive_adapter(
            ORDINARY, cases_dir, fetch_text=fetch_text, find_statute_key=find_statute_key,
            article_text=article_text,
        )("laws", [item or _law_item()])
        return store.load(ORDINARY, cases_dir)["laws"][0]


def test_the_article_index_wins_over_the_corpus_because_the_corpus_drops_the_last_clause():
    """條文有索引就用索引，**不要用母庫全量那批**。

    2026-09-13 逐條比對：`kb/public/相關法規_全量/` 每條都被截掉最後一項或一款。
    訴願法 §77 從母庫切出來只有七款，缺的是第八款「對於非行政處分或其他依法不屬
    訴願救濟範圍內之事項提起訴願者」——**不受理最常用的款次之一**。
    條號對得上、內容缺一截，而缺掉的那一款看起來完全正常。

    這條釘住兩件事：索引贏過母庫，而且 note 要說得出條文是哪一批來的。
    母庫那條路徑本身不受影響（見下面兩支測試），它服務的是索引涵蓋不到的另外 657 部。
    """
    truncated = "第 77 條\n柱書。\n　　一、甲。\n　　七、庚。\n\n第 78 條\n乙。\n"
    complete = "柱書。一、甲。七、庚。八、對於非行政處分或其他依法不屬訴願救濟範圍內之事項提起訴願者。"
    item = _law_item()
    item["t"] = "訴願法第77條"
    item["id"] = "lawtable:訴願法-77"
    # `law`／`article` 由歸檔層從這個 meta 鍵讀，不是頂層欄位。
    item[chat_mod.ARCHIVE_LAWTABLE_META] = {"law": "訴願法", "article": "77"}

    filed = _file_law(fetch_text=lambda _k: truncated,
                      article_text=lambda law, art: complete if (law, art) == ("訴願法", "77") else None,
                      item=item)
    assert_in("八、對於非行政處分", filed["body_cached"], "母庫那份缺第八款，索引那份才完整")
    assert_true("柱書。\n" not in filed["body_cached"], "拿到的是母庫那份被截斷的條文")
    assert_in("條文原文索引", filed["note"], "沒說條文是哪一批來的")

    # 索引沒有這一條 → 照舊落回母庫，而且 note 要標明它可能缺末項／款
    filed = _file_law(fetch_text=lambda _k: truncated,
                      article_text=lambda _law, _art: None, item=item)
    assert_in("柱書", filed["body_cached"], "索引沒有時應該落回母庫")
    assert_in("可能缺最後一項／款", filed["note"],
              "用了母庫全量那批卻沒有警告——承辦人會照著它寫決定書")


def test_a_lawtable_entry_carries_the_article_text_from_the_corpus():
    """點開查表通道的法條要看得到**那一條的條文**，不是只看到一句驗證說明。"""
    asked: list[str] = []

    def fetch(key):
        asked.append(key)
        return _LAW_FULLTEXT

    filed = _file_law(fetch_text=fetch)
    assert_eq(asked, ["kb/public/相關法規_全量/行政程序法.txt"],
              f"抓的不是那部法的母庫全文：{asked}")
    assert_in("送達，於應受送達人之住居所", filed["body_cached"])
    assert_true("第 73 條" not in filed["body_cached"], "切到下一條去了")
    # **來源與範圍要講出來**，而且**原本那句驗證說明要留著**：
    # 「條號存在性驗證」講的是驗證狀態，「這裡是條文」是另一件事。
    assert_in("條號存在性驗證", filed["note"], "原本的驗證說明被條文說明蓋掉了")
    assert_in("kb/public/相關法規_全量/行政程序法.txt", filed["note"], "沒說條文出自哪一份檔")
    assert_in("第72條", filed["note"], "沒說是那份檔的第幾條")
    assert_in("未改寫", filed["note"], "沒說它是原文照錄")


def test_the_two_reasons_for_having_no_article_text_are_told_apart():
    """**硬條件**：「母庫沒有這部法」與「有這部法但切不出這一條」要分得出來。

    下一步完全不同——前者要換個來源查，後者要去看那部法的條次寫法。
    合成一句「沒有條文」的話，承辦人不知道往哪邊查。
    """
    def missing(_key):
        raise FileNotFoundError("母庫沒有這份文件")

    filed = _file_law(fetch_text=missing)
    assert_eq(filed["body_cached"], "", "抓不到卻有條文？")
    assert_in("母庫沒有這部法的全文", filed["note"])
    assert_in("kb/public/相關法規_全量/行政程序法.txt", filed["note"],
              "沒說查過哪一個 key——檔名規則猜錯時這句話是唯一的線索")

    # 有全文、但那部法裡沒有第 72 條
    filed = _file_law(fetch_text=lambda _k: "第 1 條\n甲。\n\n第 2 條\n乙。\n")
    assert_eq(filed["body_cached"], "", "切不出來卻有條文？")
    assert_in("母庫有這部法的全文", filed["note"])
    assert_in("切不出第72條", filed["note"])
    assert_true("母庫沒有這部法" not in filed["note"], "兩種原因混在一起了")


def test_the_corpus_key_guess_falls_back_to_asking_the_corpus():
    """檔名規則沒對著實際 bucket 驗過（本機無憑證）。猜錯時要有後備，
    而且後備只在猜錯之後才動——正常路徑不多花一次檢索。"""
    calls: list[str] = []

    def fetch(key):
        calls.append(key)
        if key != "kb/public/相關法規_全量/行政程序法_112年修正.txt":
            raise FileNotFoundError("沒有這份")
        return _LAW_FULLTEXT

    filed = _file_law(fetch_text=fetch,
                      find_statute_key=lambda _n: "kb/public/相關法規_全量/行政程序法_112年修正.txt")
    assert_in("送達，於應受送達人之住居所", filed["body_cached"])
    assert_in("行政程序法_112年修正.txt", filed["note"], "note 要指向真正抓到的那一份")

    # 直接組的 key 抓得到時，不得再問後備一次
    asked_backup: list[str] = []
    _file_law(fetch_text=lambda _k: _LAW_FULLTEXT,
              find_statute_key=lambda n: asked_backup.append(n))
    assert_eq(asked_backup, [], "直接抓到了還去問後備，白花一次檢索")


def test_fetching_the_article_text_never_breaks_filing():
    """抓條文失敗**不得讓歸檔失敗**（沿用 references 那條的判準）。"""
    def boom(_key):
        raise RuntimeError("S3 掛了")

    filed = _file_law(fetch_text=boom)
    assert_eq(filed["id"], "lawtable:行政程序法-72", "抓條文失敗把整筆歸檔帶走了")

    # 沒注入抓取器（測試／還沒設好 S3 的檔位）也要說得出來
    filed = _file_law()
    assert_in(chat_bridge.NOTE_NO_FULLTEXT_FETCHER, filed["note"])


def test_the_scratch_field_never_reaches_the_manifest():
    """`_lawtable` 是歸檔層用完就丟的，**不得寫進 manifest**——
    契約 §4.0 沒有這個鍵，留著就是多一個沒有人定義過的欄位。"""
    filed = _file_law(fetch_text=lambda _k: _LAW_FULLTEXT)
    leaked = [k for k in filed if k.startswith("_")]
    assert_eq(leaked, [], f"歸檔層的暫用欄位漏進卷宗：{leaked}")


def test_the_scratch_key_is_the_same_string_on_both_sides():
    """`chat.py` 寫入、`chat_bridge.py` 讀出，兩邊各寫一份字串常數。

    不一致的下場是**沒有症狀**：條文永遠是空的，而 note 會說「這個檔位沒有母庫
    全文來源」——看起來像環境沒設好。所以釘住它們相等。
    （不直接 import 對方的常數，理由見 `chat_bridge._SCRATCH_PREFIX` 上面那段。）
    """
    assert_eq(chat_bridge.LAWTABLE_META_KEY, chat_mod.ARCHIVE_LAWTABLE_META)
    assert_true(chat_bridge.LAWTABLE_META_KEY.startswith(chat_bridge._SCRATCH_PREFIX),
                "暫用欄位沒有底線前綴，就不會被 pop 掉")


def test_the_chip_query_is_the_same_string_n4_actually_searched_with():
    """工具 chip 的查詢句必須是 **N4 真的送出去的那一串**，不是另外組的。

    這是這輪最容易悄悄壞掉的地方：聊天層與 N4 各組一份查詢句，兩邊都「從案情組出來」、
    看起來都合理，但**畫面上的相似案會跟 N4 卡片裡的是兩批東西，兩邊都說自己是
    本案的相似案**。這個專案已經因為同一件事兩份實作踩過兩次（`sections[]`、`cite_count`）。

    所以 `build_case_query()` 是 2026-09-13 從 N4 的函式體裡**抽出來**的，
    不是新寫的——這條測試釘的就是「它還是同一串」。
    """
    state = run_case(ORDINARY, mode="fixture", persist=False)
    # N4 通道 B 實際用的查詢句：跑完之後記在 retrieval 的 log 裡拿不到原字串，
    # 所以直接比對函式——重點是**只有一個實作**，不是比對兩份結果。
    from_n4 = n4_retrieval.build_case_query(state)
    assert_true(bool(from_n4.strip()), "合成案跑完卻組不出通道 B 的查詢句，測試前提不成立")

    # 通道 A 與通道 B 不得是同一串（混用就是用錯查詢句）
    law_names = list((load_snapshot().get("laws") or {}).keys())
    assert_true(n4_retrieval.build_query(state, law_names) != from_n4,
                "兩條通道的查詢句變成一樣了——其中一條在用錯的查詢句查東西")

    # 出處常數要跟函式實際讀的欄位對得上（它會被拿去跟承辦人說「這串字從哪來」）
    for field in ("facts_excerpt", "note", "case_type"):
        assert_true(any(field in src for src in n4_retrieval.CASE_QUERY_SOURCES),
                    f"CASE_QUERY_SOURCES 沒有提到 {field}，但 build_case_query 讀了它")


def test_a_rerun_from_n4_does_not_launder_needs_input_into_verified():
    """缺必填欄位的案子，續跑草稿之後終態不得變成 VERIFIED（2026-09-13 雲上抓到）。

    實際發生的：同一件缺欄位的案子，「解析卷證」（n1→n3）得到 `NEEDS_INPUT`，
    按下「生成草稿」（`from_node="n4"` 續跑）之後變成 `VERIFIED`、還帶了
    `cite_count=4` 與一個 artifact id。欄位一個都沒補，狀態卻說「驗完了」。

    根因是舊判準只看「這一輪的 n1 有沒有降級」，而續跑時 n1 根本不跑。

    **這條測試一定要跑真的兩段式**，不能只呼叫內部函式：這個 bug 的形狀正是
    「兩段分開看都對、接起來錯」——第一段的終態對，第二段自己也沒做錯什麼，
    錯在第二段不知道第一段發生過什麼。

    缺的欄位選 `d2`（送達日）而不是案號：它會改變期間計算，
    「缺送達日卻說驗完了」比缺案號嚴重得多，而案號已經不是必填了。
    """
    with tempfile.TemporaryDirectory() as tmp:
        fixture = load_case(ORDINARY)
        fixture["extraction"]["conf"]["d2"] = 0.10  # 低信心，但值還在 → N3 照樣算得出期間
        data_dir = pathlib.Path(tmp) / "synthetic"
        data_dir.mkdir()
        (data_dir / f"{ORDINARY}.json").write_text(
            json.dumps(fixture, ensure_ascii=False), encoding="utf-8")

        first = run_case(ORDINARY, mode="fixture", data_dir=data_dir,
                         to_node="n3", persist=True)
        assert_eq(first.state, "NEEDS_INPUT", "第一段：必填欄位信心不足就該停在 NEEDS_INPUT")

        second = run_case(ORDINARY, mode="fixture", data_dir=data_dir,
                          base_state=first, from_node="n4", persist=False)

    assert_eq(second.state, "NEEDS_INPUT",
              "續跑把 NEEDS_INPUT 洗成了 VERIFIED——欄位一個都沒補，狀態卻說驗完了")
    assert_true(any(d.get("node") == "n1" for d in (second.run_meta or {}).get("degraded") or []),
                "續跑把 n1 的降級紀錄弄丟了，前端會看不到還缺哪幾欄")


def test_a_rerun_on_a_complete_case_still_reaches_verified():
    """對照組：欄位齊全的案子續跑照樣要到 `VERIFIED`。

    沒有這一條的話，「永遠回 NEEDS_INPUT」也能讓上面那條測試變綠——
    那是把 demo 整個鎖死的過度修正，而且看起來很像修好了。
    """
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = pathlib.Path(tmp) / "synthetic"
        data_dir.mkdir()
        (data_dir / f"{ORDINARY}.json").write_text(
            json.dumps(load_case(ORDINARY), ensure_ascii=False), encoding="utf-8")

        first = run_case(ORDINARY, mode="fixture", data_dir=data_dir,
                         to_node="n3", persist=True)
        assert_eq(first.state, "SCREENED", "欄位齊全時第一段該停在 SCREENED")

        second = run_case(ORDINARY, mode="fixture", data_dir=data_dir,
                          base_state=first, from_node="n4", persist=False)
    assert_eq(second.state, "VERIFIED", "欄位齊全的案子續跑卻到不了 VERIFIED")


def test_a_missing_case_number_no_longer_stops_the_whole_case():
    """收文案號抽不到不得擋住整件案子（2026-09-13 Ci 拍板把 `no` 移出必填）。

    `backend/llm/prompts/n1_extract.md` 自己寫著訴願書內文通常沒有這個號，
    而且明文禁止拿原處分字號充數——所以**任何只有訴願書＋裁處書的上傳案都必然
    抽不到它**。留在 `REQUIRED_FIELDS` 裡的後果是每一件真實卷證都停在 NEEDS_INPUT，
    擋住的不是錯的東西。它也不進任何規則運算
    （`settings.DEADLINE_INPUT_FIELDS` 沒有它，§77-2／§77-3 也不看它）。

    **但「不必填」不等於「不抽」，更不等於「抽不到就算了」。**
    這條測試釘的是三件事，第三件最容易在重構時掉：

    1. `no` 不在 `REQUIRED_FIELDS`。
    2. 只缺 `no` 的案子跑得完，N1 不降級。
    3. **`intake` 裡 `no` 這個鍵還在，值是 `None`。**
       前端靠「鍵在不在」決定畫不畫收文表格那一列——鍵整個消失的話，
       承辦人看到的是**少一列**，從「擋住你」變成「靜默丟棄」，比擋住更糟。
    """
    assert_true("no" not in n1_extract.REQUIRED_FIELDS,
                "`no` 又被放回必填了；它不進任何規則運算，而訴願書本來就沒有這個號")

    with tempfile.TemporaryDirectory() as tmp:
        fixture = load_case(ORDINARY)
        fixture["extraction"]["intake"]["no"] = None
        fixture["extraction"]["conf"]["no"] = 0.0
        data_dir = pathlib.Path(tmp) / "synthetic"
        data_dir.mkdir()
        (data_dir / f"{ORDINARY}.json").write_text(
            json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        state = run_case(ORDINARY, mode="fixture", data_dir=data_dir, persist=False)
        payload = build_payload(state)

    n1_degraded = [d for d in (state.run_meta or {}).get("degraded") or []
                   if d.get("node") == "n1"]
    assert_eq(n1_degraded, [], "只缺收文案號不該讓 N1 降級")
    assert_true(state.state != "NEEDS_INPUT",
                f"只缺收文案號卻停在 {state.state}——每一件真實上傳案都會卡在這裡")
    assert_eq(state.low_conf_fields, [], "`no` 已非必填，不該再進低信心清單")

    assert_in("no", payload["intake"],
              "`no` 這個鍵從 intake 消失了——前端會少畫一列，承辦人完全沒有訊號")
    assert_eq(payload["intake"]["no"], None, "抽不到就是 None，不得填一個看起來像案號的東西")


def test_the_bridge_hands_the_chat_layer_a_plain_dict_with_no_orchestrator_types():
    """橋的存在理由：聊天層從頭到尾只碰得到 dict。

    直接注入 `run_case` 的話，聊天層沒有 import 語句（AST 檢查會綠），
    卻會拿到一個 `CaseState` 並讀它的屬性——**層級形式上守住、實質被穿**。
    """
    with tempfile.TemporaryDirectory() as tmp:
        run_pipeline = chat_bridge.pipeline_adapter(ORDINARY, pathlib.Path(tmp), mode="fixture")
        out = run_pipeline(to_node="n3")
        assert_eq(sorted(out), ["artifact_id", "cite_count", "degraded", "has_draft",
                                "node_timings", "run_id", "sections", "state"],
                  "橋的回傳形狀")
        for v in out.values():
            assert_true(v is None or isinstance(v, (str, int, bool, dict, list)),
                        f"橋回了一個非 plain 型別：{type(v)}")
        assert_eq(sorted(out["sections"]), sorted(chat_bridge.PAYLOAD_SECTIONS), "分區值域")


def test_the_bridge_reproduces_the_two_chat_tools_end_to_end():
    """解析卷證 → 生成草稿，兩支工具背後真正會發生的事。

    釘的是**驗收條件本身**：解析跑完 `SCREENED` 且該 run 沒有草稿；
    接著續跑之後 `VERIFIED`，而且沒有重跑 n1–n3。
    """
    with tempfile.TemporaryDirectory() as tmp:
        run_pipeline = chat_bridge.pipeline_adapter(ORDINARY, pathlib.Path(tmp), mode="fixture")

        extracted = run_pipeline(to_node="n3")
        assert_eq(extracted["state"], "SCREENED", "解析卷證停在程序審查")
        assert_true(not extracted["has_draft"], "這個 run 不該有草稿")
        assert_eq(sorted(extracted["node_timings"]), ["n1", "n2", "n3"], "只跑 n1–n3")

        drafted = run_pipeline(from_node="n4", base_run_id=extracted["run_id"],
                               overrides={"n4_query": "廢棄物清理法第 2 條"})
        assert_eq(drafted["state"], "VERIFIED", "生成草稿要跑到守門")
        assert_true(drafted["has_draft"], "續跑之後要有草稿")
        assert_eq(sorted(drafted["node_timings"]), ["n4", "n5", "n6"], "不得重跑 n1–n3")
        assert_true(drafted["run_id"] != extracted["run_id"], "續跑是一次新的執行")
        assert_true(drafted["cite_count"] > 0, "引用數是從 payload 數的真值")


def test_the_bridge_refuses_to_stop_at_n5_just_like_run_case_does():
    """紅線要在橋這一層也穿得過去，不是只有 `run_case` 自己擋。"""
    run_pipeline = chat_bridge.pipeline_adapter(ORDINARY, pathlib.Path(tempfile.gettempdir()),
                                                mode="fixture")
    try:
        run_pipeline(to_node="n5")
    except ValueError as e:
        assert_true("n5" in str(e), str(e))
        return
    raise AssertionError("橋讓 to_node='n5' 過去了")


def test_a_missing_or_broken_manifest_reads_as_an_empty_case_file_not_an_error():
    """讀不到卷宗清單＝清單是空的，而 `generate_decision_draft` 會據此擋下前置條件 3。

    壞掉的 JSON 也當成空：半份清單比沒有清單更難查。
    """
    def _write(root: pathlib.Path, case_id: str, text: str) -> None:
        (root / case_id).mkdir(parents=True, exist_ok=True)
        (root / case_id / "manifest.json").write_text(text, encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        assert_eq(chat_bridge.load_case_manifest(ORDINARY, root), {}, "沒有這個案子")

        _write(root, ORDINARY, "{ 壞掉的")
        assert_eq(chat_bridge.load_case_manifest(ORDINARY, root), {}, "壞掉的 JSON")

        _write(root, ORDINARY, "[1,2]")
        assert_eq(chat_bridge.load_case_manifest(ORDINARY, root), {}, "頂層不是物件")

        # 不合白名單的 case_id 也是空，**不是**例外：它會從 HTTP path 進來，
        # `store.manifest_path` 擋在拼路徑之前（`../../etc/x` 之類）。
        assert_eq(chat_bridge.load_case_manifest("../../etc/passwd", root), {}, "非法 case_id")

        _write(root, ORDINARY, '{"laws":[{"id":"L1","t":"x"}]}')
        m = chat_bridge.load_case_manifest(ORDINARY, root)
        assert_eq(m["laws"][0]["id"], "L1", "正常讀")
        # 走 `store.load` 的附帶好處：`_normalise` 保證四個群組一定是 list，
        # 缺鍵補空。讀端因此不必再自己防「laws 是 dict」那種形狀。
        assert_eq(m["references"], [], "缺的群組補成空 list 而不是 KeyError")
        assert_eq(m["artifacts"], [], "同上")

        _write(root, ORDINARY, '{"laws": {"L1": {"t": "x"}}}')
        assert_eq(chat_bridge.load_case_manifest(ORDINARY, root)["laws"], [],
                  "群組不是 list 時被正規化成空 list，不會讓下游拿 str 去 .get()")


def test_the_draft_tool_lets_precondition_three_through_when_the_manifest_really_has_content():
    """前置條件 3 的**放行**路徑：真的 adapter ＋ 真的 `manifest.json` ＋ 真的 `ChatTools`。

    擋下來的三種情形先前已經有測試，但「有內容時會放行」一直沒驗到——
    **擋得住不等於放得行**。一個把 `laws`／`references` 判斷寫反的實作，
    在只驗「擋下來」的測試組合下會全綠。

    這條唯一沒有用真貨的是模型（沒有 live 檔位，見 verification.md §4）。
    流水線、manifest 讀取、前置條件判斷、`n4_query` 組裝、RefBook 重置全是真的。
    """
    saved = chat_mod._throttle
    chat_mod._throttle = lambda: None          # 真的節流會讓這條睡好幾秒
    try:
        tmpdir = tempfile.TemporaryDirectory()
        root = pathlib.Path(tmpdir.name)
        (root / ORDINARY).mkdir()
        (root / ORDINARY / "manifest.json").write_text(
            json.dumps({"case_id": ORDINARY,
                        "laws": [{"id": "L1", "t": "廢棄物清理法第 2 條"},
                                 {"id": "L2", "t": "訴願法第 14 條"}],
                        "references": [{"id": "C1", "t": "112 年訴字第 1 號"}]},
                       ensure_ascii=False), encoding="utf-8")
        manifest = chat_bridge.load_case_manifest(ORDINARY, root)

        events: list = []
        tools = chat_mod.ChatTools(
            {}, chat_mod.RefBook(), emit=lambda n, d: events.append((n, d)),
            run_pipeline=chat_bridge.pipeline_adapter(ORDINARY, root, mode="fixture"),
            case_manifest=manifest,
        )

        # 先解析卷證：這一步才會讓 run_id 與 screen 進來（前置條件 2）
        tools.extract_case_document()
        assert_true(tools.run_id, "解析卷證要留下 run_id")
        assert_true(tools.case_payload.get("screen"), "解析卷證要留下程序審查結果")

        out = tools.generate_decision_draft()
        results = [d for n, d in events if n == "tool_result"]
        draft = results[-1]
        assert_eq(draft["status"], "ok", f"前置條件 3 應該放行，實得 {draft}")
        assert_eq(draft["state"], "VERIFIED", "生成草稿要跑到守門")
        assert_true(draft["cite_count"] > 0, "引用數是從 payload 數的真值")
        assert_in("已生成草稿", out)

        # 兩支工具各發一組 n1–n3 / n4–n6 的 tool_step，中文 label 由後端帶
        steps = [(d["step"], d["label"]) for n, d in events
                 if n == "tool_step" and d["status"] == "done"]
        assert_eq(steps, [("n1", "讀卷抽取"), ("n2", "案件分類"), ("n3", "程序審查"),
                          ("n4", "檢索法條與相似案"), ("n5", "草稿撰寫"), ("n6", "引用守門")],
                  "兩支工具合起來各報自己那三個節點")
    finally:
        chat_mod._throttle = saved
        tmpdir.cleanup()


# ── A/B 交界：兩條路徑都要把 run 登記進卷宗 ──────────────────────────
#
# 2026-09-12 整合時發現的洞：登記（`latest_run_id` ＋ artifact）原本只接在
# `POST /cases/{id}/runs` 上，而契約 §0.1 說**前端只打 chat、永遠不會打 `/runs`**。
# 唯一會登記的路徑正好是前端不會走的那一條——右欄「答辯書與產出」永遠是空的，
# 下一輪 chat 也沒有 `latest_run_id` 可帶。兩邊單獨看都正確，併起來才浮出來。

def test_chat_pipeline_records_latest_run_id_into_the_manifest():
    """A1.1：經 chat 解析卷證之後，卷宗要記得這次的 run。

    不記的話下一輪 chat 沒有 `run_id` 可帶 → `read_case` 看到空卷內 →
    承辦人剛解析完的卷證，下一句話就查不到了。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        run_pipeline = chat_bridge.pipeline_adapter(ORDINARY, root, mode="fixture")
        out = run_pipeline(to_node="n3")
        m = store.load(ORDINARY, root)
        assert_eq(m["latest_run_id"], out["run_id"], "latest_run_id 要指到這次的 run")
        # 停在 n3 沒有草稿 → 不該長出一筆點開是空的 artifact
        assert_eq(m["artifacts"], [], "沒有句子就不登記 artifact")


def test_chat_pipeline_records_the_draft_artifact_and_returns_its_real_id():
    """經 chat 生成草稿之後 `artifacts[]` 要多一筆，且回傳的 `artifact_id` 與它一致。

    回 `None` 或回一個編出來的 `art-…`，前端都會拿去打一支查不到的端點。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        run_pipeline = chat_bridge.pipeline_adapter(ORDINARY, root, mode="fixture")
        base = run_pipeline(to_node="n3")
        drafted = run_pipeline(from_node="n4", base_run_id=base["run_id"])

        m = store.load(ORDINARY, root)
        assert_eq(len(m["artifacts"]), 1, f"草稿沒有被登記：{m['artifacts']}")
        art = m["artifacts"][0]
        assert_eq(drafted["artifact_id"], art["id"], "回傳的 id 要就是登記的那一筆")
        assert_eq(art["run_id"], drafted["run_id"], "artifact 要指得回產出它的 run")
        assert_eq(art["kind"], "draft", "")
        assert_eq(m["latest_run_id"], drafted["run_id"], "latest_run_id 要跟著前進")

        # 同一個 run 重跑登記不得長出第二筆（id 由 run_id 決定，不是隨機）
        again = store.record_run(ORDINARY, build_payload(load_run(drafted["run_id"])),
                                 cases_dir=root)
        assert_eq(again, art["id"], "冪等呼叫要回同一個 id，不是 None")
        assert_eq(len(store.load(ORDINARY, root)["artifacts"]), 1, "重複登記長出第二筆")


def test_both_paths_record_the_same_manifest_shape():
    """`POST /runs` 與 chat 兩條路徑登記出來的形狀必須相同。

    **這條是為了防分岔**：兩邊各寫一份登記邏輯的話，差異不會有症狀——
    右欄照樣長出東西，只是欄位少一個或 id 算法不一樣，而那要等到匯出或
    點開產出才會炸。所以這裡直接比對兩份 manifest 的結構。

    `backend/api/app.py` import fastapi（測試路徑零外部依賴），所以這裡不打 HTTP 端點，
    而是**呼叫它實際呼叫的那一支**（`store.record_run(case_id, build_payload(state))`
    ——app.py:432／:463 兩處都是這一行）。要防的是「兩邊各寫一份」，
    只要兩邊真的走同一支函式，這條就成立。
    """
    with tempfile.TemporaryDirectory() as tmp_http, tempfile.TemporaryDirectory() as tmp_chat:
        http_root, chat_root = pathlib.Path(tmp_http), pathlib.Path(tmp_chat)

        # 路徑一：`POST /runs` 走的那一行
        state = run_case(ORDINARY, mode="fixture")
        store.record_run(ORDINARY, build_payload(state), cases_dir=http_root)

        # 路徑二：chat 的 pipeline 工具
        chat_pipeline = chat_bridge.pipeline_adapter(ORDINARY, chat_root, mode="fixture")
        chat_pipeline(to_node="n6")

        a, b = store.load(ORDINARY, http_root), store.load(ORDINARY, chat_root)
        assert_eq(sorted(a), sorted(b), "manifest 頂層鍵不同")
        assert_eq(sorted(a["artifacts"][0]), sorted(b["artifacts"][0]), "artifact 欄位不同")
        assert_eq(a["artifacts"][0]["kind"], b["artifacts"][0]["kind"], "")
        assert_eq(a["artifacts"][0]["name"], b["artifacts"][0]["name"], "草稿標題不同")
        # run_id 兩次執行本來就不同；比的是「id 由 run_id 算出來」這條規則一致
        for m in (a, b):
            assert_eq(m["artifacts"][0]["id"],
                      store.artifact_id_for(m["latest_run_id"]),
                      "artifact id 不是由 run_id 算出來的")


def test_the_chat_side_registration_is_load_bearing_not_decorative():
    """變異測試的固定樁：**登記那一行如果被拿掉，上面兩條必須紅**。

    這裡不改程式碼（測試不該改被測物），而是釘住「`pipeline_adapter` 真的呼叫了
    `store.record_run`」這個事實——把那行刪掉，這條會紅，上面兩條也會紅。
    三條一起紅，才知道是同一個原因。
    """
    tree = ast.parse(
        (ROOT / "backend" / "orchestrator" / "chat_bridge.py").read_text(encoding="utf-8"))
    attrs = [n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert_in("record_run", attrs)   # 沒有它，chat 這條路就不會登記
    # 反向：不得自己寫檔（那就是把登記邏輯複製一份，遲早跟 store.py 分岔）。
    # **用 AST 不用字串比對**：這個檔的註解本來就在講 `manifest.json` 與寫入，
    # 字串比對會被自己的說明觸發——寫得越清楚越容易誤報（本檔已踩過一次）。
    for banned in ("write_text", "write", "dump", "dumps", "replace"):
        assert_true(banned not in attrs,
                    f"chat_bridge 自己呼叫了 {banned}()：寫入要集中在 dossier/store.py")
