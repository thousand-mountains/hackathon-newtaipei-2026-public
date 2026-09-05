"""端到端整合測試：兩個合成案例跑完六節點的行為驗收。

對應 plan 的驗收條件 2 與 3：
- 正常案例：exit 0、六節點結果齊全、每個欄位有 origin、三層分明
- 對抗案例：N6 必須攔下（結論封鎖 + 引用查無），不能誤放行
"""
from __future__ import annotations

import json
import pathlib

from backend.config.settings import (
    BLOCK_DECISION_INPUT_FIELDS,
    CONFIRMABLE_INTAKE_FIELDS,
    SYNTHETIC_DIR,
)
from backend.orchestrator.graph import build_payload, list_synthetic_cases, load_case, run_case
from backend.tests.harness import assert_eq, assert_in, assert_true

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
    from backend.config.origin_registry import check_payload

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
    import tempfile

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
    """N4 查詢句只能由 N1／N2／N3 組成，不得含任何草稿內容。"""
    for case_id in (ORDINARY, BLOCKED):
        meta = _payload(case_id)["retrieval"]["retrieval_meta"]
        sources = meta.get("query_sources")
        assert_true(sources, f"{case_id}：retrieval_meta 沒有 query_sources，無法稽核查詢句來源")
        for src in sources:
            origin_node = str(src["from"]).split(".")[0]
            assert_in(
                origin_node,
                ("n1", "n2", "n3"),
                f"{case_id}：查詢句來源 {src['from']!r} 不是案情節點——草稿倒推的路徑被接回來了",
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
    """
    for case_id in (ORDINARY, BLOCKED):
        p = _payload(case_id)
        for block in p["doc"]:
            for s in block.get("ss", []):
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
    import json
    import pathlib
    import tempfile

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
    from backend import cli

    assert_eq(cli.main(["--case", ORDINARY, "--quiet"]), 0, "正常案例 CLI 必須 exit 0")
    assert_eq(cli.main(["--case", BLOCKED, "--quiet"]), 0, "對抗案例流程本身跑完，也是 exit 0（攔下是正確行為）")
    assert_eq(cli.main(["--case", "synthetic-does-not-exist"]), 1, "找不到案例要 exit 1")
