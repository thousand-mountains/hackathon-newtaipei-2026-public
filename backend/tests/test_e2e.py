"""端到端整合測試：兩個合成案例跑完六節點的行為驗收。

對應 plan 的驗收條件 2 與 3：
- 正常案例：exit 0、六節點結果齊全、每個欄位有 origin、三層分明
- 對抗案例：N6 必須攔下（結論封鎖 + 引用查無），不能誤放行
"""
from __future__ import annotations

import json

from backend.config.settings import SYNTHETIC_DIR
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


def test_ordinary_passes_the_gate():
    p = _payload(ORDINARY)
    assert_eq(p["blockers"], [], "正常案例不該有阻擋項")
    assert_eq(p["submit_allowed"], True)
    assert_eq(p["citation_counts"]["missing"], 0, "正常案例不得有查無此號的引用")


def test_ordinary_deadline_matches_known_vector():
    """本案的期間結果對齊 test-vectors.json 的 hist-113-03 向量（寄存 113/6/13 → 113/7/15 逾期）。"""
    p = _payload(ORDINARY)
    dl = p["screen"]["deadline"]
    assert_eq(dl["deadline"], "2024-07-15")
    assert_eq(dl["overdue"], True)
    assert_eq(p["screen"]["art77"]["clause"], "77-2")
    assert_eq(p["screen"]["requires_human_conclusion"], False, "程序逾期案不封鎖結論段")


def test_ordinary_cite_ids_all_resolve():
    p = _payload(ORDINARY)
    law_ids = {l["id"] for l in p["retrieval"]["laws"]}
    for block in p["doc"]:
        for s in block["ss"]:
            for cid in s.get("cite_ids", []):
                if cid.startswith("L"):
                    assert_in(cid, law_ids, f"句子 {s['id']} 的 {cid} 解析不到檢索結果")


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
