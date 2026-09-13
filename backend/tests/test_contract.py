"""CASE payload 契約測試：逐欄對齊 `docs/architecture.md` §6.2。

**這份存在的理由**：前端與後端是兩個人／兩個 agent 在改，契約只寫在文件裡的話，
「後端少給一個欄位」這件事會在 demo 現場才被發現——那時畫面就是一塊空白，
而且沒人說得出是誰的責任。這裡把 §6.2 的每一欄變成可執行的斷言。

檢查四件事：
1. §6.2 列出的頂層欄位都在，型別對。
2. 每個頂層欄位都在 `config/origin_registry.ORIGIN` 註冊了 origin
   （新增欄位忘了註冊 → 紅）。
3. 陣列元素（`laws[]` / `cases[]` / `issues[]` / `files[]` / `doc[].ss[]`）
   的必要欄位齊全，且逐項帶得出 origin。
4. 三個紅線位置（燈號 `l`、`why`、`citations[].state`）不得是模型產出。

零外部依賴（stdlib only），跟 `run_all.py` 其餘部分一樣。
"""
from __future__ import annotations

import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config.origin_registry import ORIGIN_TO_TIER, registered_origin  # noqa: E402
from backend.orchestrator import narrative  # noqa: E402
from backend.config.settings import SUBSTANTIVE_TYPES  # noqa: E402
from backend.orchestrator.graph import build_payload, list_synthetic_cases, run_case  # noqa: E402
from backend.orchestrator.narrative import conclusion_block_criterion  # noqa: E402
from backend.tests.harness import assert_eq, assert_in, assert_true  # noqa: E402

CASES = ("synthetic-ordinary-01", "synthetic-blocked-01")

# §6.2 + §6.1 要求的頂層欄位 → 期望型別
TOP_LEVEL_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "case_id": str,
    "run_id": str,          # §6.1 2a
    "state": str,
    "provenance": dict,     # §6.2 provenance.{kind,note,banner,constitution}
    "files": list,          # §6.2 files[].{n,s,x}
    "intake": dict,         # §6.2 intake.* + auto_fields + auto_toast
    "intake_conf": dict,    # §6.2 新增欄位 1
    "intake_origin": dict,  # §6.2 新增欄位 1
    "agents": list,         # §6.2 agents[].{k,ico,name,role,out,logs}
    "laws": list,           # §6.2 laws[].{id,t,q,src} + lamp/tag
    "cases": list,          # §6.2 cases[].{id,t,sim,src,tag,d}
    "issues": list,         # §6.2 issues[].{id,t,q,src,lamp,tag}
    "doc": list,            # §6.2 doc[].{ty,text,ind} + ss[]
    "citations": list,      # §6.2 新增欄位 4
    "blockers": list,       # §6.2 新增欄位 5
    "handoff": dict,        # §6.2 新增欄位 3（+ criterion／observations，如實描述封鎖原因）
    "retrieval_divergence": dict,  # 獨立檢索 vs 草稿引用的差集
    "submit_allowed": bool,
    "run_meta": dict,       # §6.2 新增欄位 6
    "token_note": str,
    "lamp_stats": dict,
    "citation_counts": dict,
    "tiers": dict,
}

_CACHE: dict[str, dict[str, Any]] = {}


def payload(case_id: str) -> dict[str, Any]:
    if case_id not in _CACHE:
        _CACHE[case_id] = build_payload(run_case(case_id, mode="fixture"))
    return _CACHE[case_id]


def _both(fn) -> None:
    for cid in CASES:
        fn(cid, payload(cid))


# ── 1. 頂層欄位存在且型別正確 ───────────────────────────────────────
def test_top_level_fields_exist_with_right_types() -> None:
    def check(cid: str, p: dict) -> None:
        for key, typ in TOP_LEVEL_SCHEMA.items():
            assert_in(key, p, f"{cid}：payload 缺 §6.2 要求的頂層欄位 {key!r}")
            assert_true(
                isinstance(p[key], typ),
                f"{cid}：{key} 型別應為 {typ}，實得 {type(p[key])}",
            )

    _both(check)


def test_every_top_level_field_has_registered_origin() -> None:
    """分層誠實的機器強制：payload 裡的每個頂層欄位都要說得出 origin。

    新增欄位卻忘了在 `origin_registry.ORIGIN` 註冊，這條會紅——
    「這個數字哪來的」不能靠讀程式碼推。
    """

    def check(cid: str, p: dict) -> None:
        for key in p:
            origin = registered_origin(key)
            assert_true(origin is not None, f"{cid}：頂層欄位 {key!r} 沒有在 ORIGIN 註冊 origin")
            assert_in(origin, ORIGIN_TO_TIER, f"{cid}：{key} 的 origin={origin!r} 不在已知值域")

    _both(check)


# ── 2. §6.2 逐欄：intake / files / agents ───────────────────────────
def test_intake_has_auto_fields_and_toast_with_matching_count() -> None:
    """`auto_toast` 的數字必須來自 `len(auto_fields)`，不得寫死（§6.2）。"""

    def check(cid: str, p: dict) -> None:
        intake = p["intake"]
        for field in ("no", "type", "person", "org", "d1", "d2", "d3", "agent", "note", "service_method"):
            assert_in(field, intake, f"{cid}：intake 缺 §6.2 欄位 {field!r}")
        assert_in("auto_fields", intake, f"{cid}：intake 缺 auto_fields")
        assert_in("auto_toast", intake, f"{cid}：intake 缺 auto_toast")
        assert_true(isinstance(intake["auto_fields"], list), f"{cid}：auto_fields 應為 list")
        assert_in(
            str(len(intake["auto_fields"])),
            intake["auto_toast"],
            f"{cid}：auto_toast 的數字與 len(auto_fields) 對不上（{intake['auto_toast']!r}）",
        )
        # service_method 的值域直接餵 N3，錯了期間就算錯（§6.2）
        assert_in(
            intake["service_method"],
            ("personal", "deposit", "public", "unknown"),
            f"{cid}：service_method 值域不合 §6.2",
        )

    _both(check)


def test_intake_conf_and_origin_cover_every_extracted_field() -> None:
    """§6.2 新增欄位 1：分層誠實要求標明哪欄是人填的、哪欄是模型抽的。"""

    def check(cid: str, p: dict) -> None:
        for field in p["intake_origin"]:
            assert_in(
                p["intake_origin"][field],
                ("llm", "human"),
                f"{cid}：intake_origin[{field}] 只能是 llm 或 human",
            )
        # 抽取欄位（不含編排層加上的 auto_*）都要有 origin
        for field in p["intake"]:
            if field in ("auto_fields", "auto_toast"):
                continue
            assert_in(field, p["intake_origin"], f"{cid}：intake[{field}] 沒有對應的 intake_origin")

    _both(check)


def test_files_have_n_s_x() -> None:
    def check(cid: str, p: dict) -> None:
        assert_true(len(p["files"]) > 0, f"{cid}：files[] 是空的，前端步驟 0 會看到空清單")
        for f in p["files"]:
            for k in ("n", "s", "x"):
                assert_in(k, f, f"{cid}：files[] 元素缺 §6.2 欄位 {k!r}")
            assert_true(
                str(f["n"]).startswith("synthetic-"),
                f"{cid}：卷證檔名 {f['n']!r} 不是 synthetic- 前綴（CONSTITUTION §3）",
            )

    _both(check)


def test_agents_are_seven_cards_with_narrative() -> None:
    """§6.1：節點與卡片的映射是契約的一部分，六個節點餵滿七張卡。"""

    def check(cid: str, p: dict) -> None:
        assert_eq(len(p["agents"]), 7, f"{cid}：幕僚卡片應為 7 張")
        assert_eq(
            [a["k"] for a in p["agents"]],
            ["clerk", "clf", "proc", "law", "case", "draft", "qc"],
            f"{cid}：幕僚卡片 key 與順序不合 §3.2",
        )
        for a in p["agents"]:
            for k in ("k", "name", "role", "out", "logs"):
                assert_in(k, a, f"{cid}：agents[{a.get('k')}] 缺 {k!r}")
            assert_true(bool(a["out"]), f"{cid}：agents[{a['k']}].out 是空的")
            for log in a["logs"]:
                assert_eq(len(log), 2, f"{cid}：agents[{a['k']}].logs 元素應為 [text, cls]")
            # 模板 token 必須被實際數字填掉，不能把 {TOTAL} 這種東西送到前端
            assert_true("{" not in a["out"], f"{cid}：agents[{a['k']}].out 有未填的模板 token：{a['out']!r}")

    _both(check)


# ── 3. §6.2 逐欄：laws / cases / issues ─────────────────────────────
def test_laws_have_id_t_q_src_and_lamp_from_rules() -> None:
    """`laws[].q` 是條文原文、不得改寫；查不到就是 None + q_note 說明，不腦補。"""

    def check(cid: str, p: dict) -> None:
        assert_true(len(p["laws"]) > 0, f"{cid}：laws[] 是空的")
        ids = set()
        for law in p["laws"]:
            for k in ("id", "t", "q", "src", "lamp", "tag", "origin", "gate_status", "gate_note", "gate_ref_key"):
                assert_in(k, law, f"{cid}：laws[] 元素缺 §6.2 欄位 {k!r}")
            assert_true(law["id"] not in ids, f"{cid}：laws[].id 重複：{law['id']}")
            ids.add(law["id"])
            assert_true(str(law["id"]).startswith("L"), f"{cid}：laws[].id 應為 L* 命名空間，實得 {law['id']!r}")
            # 燈號只屬於「草稿有引用、守門查核過」的卡片。獨立檢索命中但草稿沒引用的
            # 那些卡片不給燈號（None）——不是漏填，是那張卡沒有可以發燈的對象。
            assert_in(
                law["gate_status"],
                ("cited_and_gated", "retrieved_not_cited", "unkeyed"),
                f"{cid}：laws[{law['id']}].gate_status 值域錯",
            )
            if law["gate_status"] == "cited_and_gated":
                assert_in(law["lamp"], ("r", "y", "g"), f"{cid}：查核過的卡片必須有燈號")
            else:
                assert_eq(
                    law["lamp"], None,
                    f"{cid}：laws[{law['id']}] 沒有對應的草稿引用卻給了燈號 {law['lamp']!r}"
                    f"——燈號不得由檢索自己發（覆核發現②）",
                )
            assert_true(bool(law.get("tag")), f"{cid}：laws[{law['id']}] 沒有狀態標籤")
            assert_true(bool(law.get("gate_note")), f"{cid}：laws[{law['id']}] 沒有說明它為什麼是這個狀態")
            assert_eq(law["origin"], "retrieval", f"{cid}：laws[{law['id']}].origin 應為 retrieval")
            if law["q"] is None:
                assert_true(
                    bool(law.get("q_note")),
                    f"{cid}：laws[{law['id']}].q 為 None 卻沒有 q_note 說明為什麼沒有原文",
                )

    _both(check)


def test_retrieved_not_cited_is_a_status_not_a_blocker() -> None:
    """獨立檢索命中、草稿沒引用 → 中性狀態，不得擋住送出。

    合併守門分支時踩到的整合效應：守門把「laws[] 對不到守門引用」當 blocker，
    在 N4 倒推查詢的舊設計下 laws[] 永遠等於草稿引用所以從不觸發；
    改成獨立檢索後，每個正常案例都會因此被擋住。這條釘住正確語意。
    """

    def check(cid: str, p: dict) -> None:
        rnc = [l for l in p["laws"] if l["gate_status"] == "retrieved_not_cited"]
        blocked_ids = {b.get("sentence_id") for b in p["blockers"]}
        for law in rnc:
            assert_true(
                law["id"] not in blocked_ids,
                f"{cid}：{law['id']}（{law['t']}）只是檢索到而草稿沒引用，不該進 blockers",
            )
        reasons = {b["reason"] for b in p["blockers"]}
        assert_true(
            "retrieval_law_not_matched_by_gate" not in reasons,
            f"{cid}：舊的「檢索對不到守門」阻擋事由又回來了",
        )

    _both(check)


def test_cases_channel_is_honest_about_being_unavailable() -> None:
    """相似案通道沒接上就回空 + 明說「不是查無相似案」，不得靜靜當作 0 筆正常結果。"""

    def check(cid: str, p: dict) -> None:
        meta = p["retrieval"]["retrieval_meta"]["similar_case_channel"]
        if not meta["available"]:
            assert_eq(p["cases"], [], f"{cid}：通道不可用卻回了相似案，這是編出來的")
            assert_true(bool(meta.get("reason")), f"{cid}：similar_case_channel 不可用卻沒說原因")
            assert_in("未驗證", meta.get("label", ""), f"{cid}：不可用的通道沒有標「庫外，未驗證」")
        else:
            for c in p["cases"]:
                for k in ("id", "t", "sim", "src"):
                    assert_in(k, c, f"{cid}：cases[] 元素缺 §6.2 欄位 {k!r}")

    _both(check)


def test_issues_carry_lamp_tag_and_human_only_disclaimer() -> None:
    """事實認定爭點的 `src` 固定寫「AI 不得代為認定」（§6.2），燈號恆為紅。"""

    def check(cid: str, p: dict) -> None:
        for issue in p["issues"]:
            for k in ("id", "t", "q", "src", "lamp", "tag", "origin"):
                assert_in(k, issue, f"{cid}：issues[] 元素缺 §6.2 欄位 {k!r}")
            assert_true(str(issue["id"]).startswith("I"), f"{cid}：issues[].id 應為 I* 命名空間")
            assert_eq(issue["lamp"], "r", f"{cid}：事實認定爭點的燈號只能是紅燈")
            assert_eq(issue["origin"], "rule", f"{cid}：issues[].origin 應為 rule（不是模型認定）")
            assert_in("AI 不得代為認定", issue["src"], f"{cid}：issues[{issue['id']}].src 缺 §6.2 指定的聲明")

    _both(check)


# ── 4. §6.2 逐欄：doc[].ss[] 與紅線位置 ─────────────────────────────
def test_doc_blocks_use_the_four_declared_types() -> None:
    def check(cid: str, p: dict) -> None:
        for bk in p["doc"]:
            assert_in(bk["ty"], ("title", "meta", "h", "p"), f"{cid}：doc[].ty 只能是 §6.2 的四型")

    _both(check)


def test_every_sentence_has_the_fields_the_frontend_renders() -> None:
    """前端逐句渲染要用到的欄位一個都不能少——缺一個畫面就是 undefined。"""
    required = ("id", "t", "l", "why", "refs", "src", "origin", "slot", "placeholder", "engine")

    def check(cid: str, p: dict) -> None:
        seen = 0
        for bk in p["doc"]:
            for s in bk.get("ss", []):
                seen += 1
                for k in required:
                    assert_in(k, s, f"{cid}：doc[].ss[{s.get('id')}] 缺前端需要的欄位 {k!r}")
                assert_in(s["l"], ("r", "y", "g"), f"{cid}：句子 {s['id']} 的燈號值域錯")
                assert_true(isinstance(s["refs"], list), f"{cid}：句子 {s['id']}.refs 應為 list")
                assert_in(s["origin"], ORIGIN_TO_TIER, f"{cid}：句子 {s['id']}.origin 不在已知值域")
                assert_true(bool(s["why"]), f"{cid}：句子 {s['id']} 沒有 why，燈號說不出理由")
        assert_true(seen > 0, f"{cid}：doc[] 裡一句都沒有")

    _both(check)


def test_sentence_refs_resolve_to_existing_cards() -> None:
    """`refs[]` 指到的 L*／C*／I* 一定要真的存在於左欄三個清單裡。

    指到不存在的 id，前端會渲染出一個點不開的空按鈕——那是「看起來有依據、
    其實沒有」，比沒有 ref 更糟。
    """

    def check(cid: str, p: dict) -> None:
        known = {x["id"] for x in p["laws"]} | {x["id"] for x in p["cases"]} | {x["id"] for x in p["issues"]}
        for bk in p["doc"]:
            for s in bk.get("ss", []):
                for ref in s["refs"]:
                    assert_in(ref, known, f"{cid}：句子 {s['id']} 的 ref {ref!r} 在左欄清單裡不存在")

    _both(check)


def test_lamp_and_why_and_citation_state_are_never_llm() -> None:
    """architecture §6.4：這四個位置永遠不准是模型產的。"""

    def check(cid: str, p: dict) -> None:
        for bk in p["doc"]:
            for s in bk.get("ss", []):
                assert_eq(s.get("l_origin"), "rule", f"{cid}：句子 {s['id']} 的燈號 origin 必須是 rule")
                assert_eq(s.get("why_origin"), "rule", f"{cid}：句子 {s['id']} 的 why origin 必須是 rule")
        for c in p["citations"]:
            assert_eq(c.get("state_origin"), "rule", f"{cid}：citations[{c.get('raw')}].state origin 必須是 rule")
        assert_eq(p["origin_violations"], [], f"{cid}：origin 分層檢查有違規")

    _both(check)


def test_lamp_stats_match_the_actual_sentences() -> None:
    """看板數字與逐句燈號必須一致——前端兩處各顯示一次，說法不同就穿幫。"""

    def check(cid: str, p: dict) -> None:
        counted = {"r": 0, "y": 0, "g": 0}
        for bk in p["doc"]:
            for s in bk.get("ss", []):
                counted[s["l"]] += 1
        assert_eq(
            {k: p["lamp_stats"].get(k, 0) for k in ("r", "y", "g")},
            counted,
            f"{cid}：lamp_stats 與逐句實際燈號對不上",
        )

    _both(check)


# ── 5. §6.2 新增欄位：citations / blockers / handoff / run_meta ─────
def test_citations_have_four_state_fields() -> None:
    def check(cid: str, p: dict) -> None:
        for c in p["citations"]:
            for k in ("raw", "state", "resolved_id", "note"):
                assert_in(k, c, f"{cid}：citations[] 元素缺 §6.2 欄位 {k!r}")
            assert_in(
                c["state"],
                ("ok", "amended", "out_of_scope", "missing"),
                f"{cid}：引用狀態 {c['state']!r} 不是四態之一",
            )
        for k in ("ok", "amended", "out_of_scope", "missing"):
            assert_in(k, p["citation_counts"], f"{cid}：citation_counts 缺 {k!r}")

    _both(check)


def test_blockers_say_why_not_just_that_it_is_blocked() -> None:
    """§6.2 新增欄位 5：送出閘門要說「為什麼擋」，不能只 disable 按鈕。"""

    def check(cid: str, p: dict) -> None:
        for b in p["blockers"]:
            for k in ("sentence_id", "reason"):
                assert_in(k, b, f"{cid}：blockers[] 元素缺 §6.2 欄位 {k!r}")
            assert_true(bool(b.get("detail") or b.get("reason")), f"{cid}：blocker 沒說原因")
        # 有 blockers 就不准放行；沒有 blockers 也不代表一定放行（可能有別的閘門）
        if p["blockers"]:
            assert_eq(p["submit_allowed"], False, f"{cid}：有 blockers 卻允許送出，閘門是壞的")

    _both(check)


def test_run_meta_has_the_stats_step5_shows() -> None:
    """§6.2 新增欄位 6：步驟 4/5 的統計要有權威來源，不由前端自己算。"""

    def check(cid: str, p: dict) -> None:
        rm = p["run_meta"]
        for k in ("elapsed_ms", "node_timings", "degraded", "model_ids", "kb_snapshot_date", "run_mode", "run_id"):
            assert_in(k, rm, f"{cid}：run_meta 缺 §6.2 欄位 {k!r}")
        assert_eq(sorted(rm["node_timings"]), ["n1", "n2", "n3", "n4", "n5", "n6"], f"{cid}：六節點時間不齊")
        assert_eq(rm["run_id"], p["run_id"], f"{cid}：run_meta.run_id 與頂層 run_id 不一致")
        # fixture 檔位沒呼叫任何模型，就不准報 model id（不腦補）
        if rm["run_mode"] == "fixture":
            assert_eq(rm["model_ids"], None, f"{cid}：fixture 檔位卻報了 model id")

    _both(check)


def test_handoff_gives_at_least_three_questions_when_conclusion_is_blocked() -> None:
    """US-8 AC-8.2：結論段封鎖時，交接卡至少 3 個具體問題 + 訊號清單。"""

    def check(cid: str, p: dict) -> None:
        blocked = p["screen"].get("requires_human_conclusion")
        ho = p["handoff"]
        assert_in("questions", ho, f"{cid}：handoff 缺 questions")
        assert_in("signals", ho, f"{cid}：handoff 缺 signals")
        if blocked:
            assert_true(len(ho["questions"]) >= 3, f"{cid}：結論封鎖但交接卡問題少於 3 個")
            assert_true(len(ho["signals"]) >= 1, f"{cid}：結論封鎖但沒有訊號清單")

    _both(check)


def test_block_criterion_matches_the_actual_gate_decision() -> None:
    """`handoff.criterion` 是把守門的判斷重講一遍人話——重講的結論必須跟守門一致。

    這是**防漂移**的鎖：`criterion` 的推導寫在 `orchestrator/narrative.py`，
    真正的封鎖邏輯寫在 `gate/lamps.py`。兩份程式分屬不同模組（也常常是不同人在改），
    一旦 lamps 改了判準而描述沒跟上，UI 就會理直氣壯地講一個錯的理由。
    """

    def check(cid: str, p: dict) -> None:
        criterion = p["handoff"]["criterion"]
        assert_eq(
            criterion["blocked"],
            bool(p["screen"]["requires_human_conclusion"]),
            f"{cid}：criterion.blocked 與 screen.requires_human_conclusion 不一致——"
            f"描述層與守門層漂移了",
        )
        assert_true(bool(criterion["text"]), f"{cid}：criterion 沒有說明文字")
        assert_in(criterion["origin"], ORIGIN_TO_TIER, f"{cid}：criterion.origin 不在值域")

    _both(check)


def test_fact_issue_is_not_presented_as_the_blocking_reason_when_it_is_not() -> None:
    """對抗案例的封鎖原因是「程序合法且須進入實體審查」，**不是**事實認定爭點。

    覆核實測：把 blocked 案例的事實爭點全部拿掉，結論段**仍然封鎖**。
    所以 UI 不得把事實爭點講成封鎖原因。這條同時釘住兩件事：
    1. 本案的 `reason_id` 是程序判準，不是爭點；
    2. 拿掉爭點後 `blocked` 仍為 True（證明爭點在本案確實不是操作條件）。
    """
    p = payload("synthetic-blocked-01")
    criterion = p["handoff"]["criterion"]
    assert_eq(
        criterion["reason_id"],
        "procedurally_valid_needs_substantive_review",
        "對抗案例的封鎖原因被講成別的東西了",
    )
    assert_eq(criterion["fact_issue_role"], "observation", "事實爭點被標成操作判準，但它不是")
    assert_in("提醒", p["handoff"]["observations_label"], "爭點清單的標題沒有標明它只是提醒")

    # 反事實：把爭點全拿掉，仍然封鎖 → 證明爭點不是本案的操作條件
    screen_without_issues = dict(p["screen"])
    screen_without_issues["fact_issues"] = []
    still = conclusion_block_criterion(screen_without_issues, p["classification"], SUBSTANTIVE_TYPES)
    assert_eq(still["blocked"], True, "拿掉事實爭點後就不封鎖了——那本測試的前提要重寫")
    assert_eq(still["reason_id"], "procedurally_valid_needs_substantive_review", "反事實下的判準應不變")


def test_the_blocked_main_text_says_which_of_the_two_procedural_outcomes_it_is() -> None:
    """主文被封鎖時，**寫哪一句取決於程序審查算出什麼**（2026-09-13 Claire 指定）。

    兩條路的文字不能對調，因為它們講的是相反的事：

        art77.clause 有值 → 引擎算出不受理事由 → 「訴願不受理。」＋所憑欄位未確認
        art77.clause 為空 → 沒有算出不受理事由 → 受理與否交人判斷

    對調了 800 個測試照樣全綠——兩邊都是 `placeholder=True`、`origin=human_required`，
    既有的契約測試只驗形狀不驗內容。這是決定書的主文，不該只靠人眼守。
    """
    def check(cid: str, p: dict) -> None:
        clause = ((p["screen"] or {}).get("art77") or {}).get("clause")
        main = [s for bk in p["doc"] for s in bk.get("ss", [])
                if s.get("slot") == "conclusion" and s.get("placeholder")]
        if not main:
            return              # 這一案沒有被封鎖，不在本測試範圍
        text = main[0]["t"]
        if clause:
            assert_true(text.startswith("訴願不受理。"),
                        f"{cid}：引擎算出 {clause}，主文卻不是不受理——{text[:40]}")
            assert_in("尚未經承辦人確認", text,
                      f"{cid}：算得出來不等於定稿，必須講明所憑欄位還沒人確認")
        else:
            assert_eq(text, narrative.SUBSTANTIVE_PENDING_TEXT,
                      f"{cid}：沒有算出不受理事由時，受理與否應交承辦人判斷")
            assert_true("應予受理" not in text,
                        f"{cid}：不得寫「應予受理」——引擎只判定 §77 第 2 款，"
                        f"宣稱其餘各款都不成立是系統查不到的事")

    _both(check)


def test_a_conclusive_placeholder_still_cannot_be_submitted() -> None:
    """主文佔位句現在**含實質結論**（「訴願不受理。」），而佔位句豁免於主文洩漏偵測。

    `n6_gate.detect_conclusion_like` 對 `placeholder=True` 的句子一律跳過——那在
    佔位句不含結論的年代是對的。2026-09-13 改成寫出結論之後，那道偵測就不再是
    這條路上的守門。**擋住送出的只剩 `conclusion_requires_human` 這一條 blocker。**

    所以這裡直接釘住最終效果，不釘中間機制：封鎖的案子，不論主文那一格寫了什麼，
    `submit_allowed` 必須是 False 且 blocker 必須在。
    """
    def check(cid: str, p: dict) -> None:
        blocked = [s for bk in p["doc"] for s in bk.get("ss", [])
                   if s.get("slot") == "conclusion" and s.get("placeholder")]
        if not blocked:
            return
        assert_eq(p["submit_allowed"], False,
                  f"{cid}：主文是佔位句卻可送出——含結論的佔位句必須擋得住")
        reasons = [b["reason"] for b in p["blockers"]]
        assert_in("conclusion_requires_human", reasons,
                  f"{cid}：缺 conclusion_requires_human，實得 {reasons}")

    _both(check)


def test_blocked_conclusion_is_a_placeholder_not_a_generated_sentence() -> None:
    """§6.2 新增欄位 2：C 型結論段要能跟一般紅燈句區分（`placeholder` + `slot`）。"""
    p = payload("synthetic-blocked-01")
    assert_eq(p["screen"]["requires_human_conclusion"], True, "對抗案例應觸發結論封鎖")
    conclusions = [s for bk in p["doc"] for s in bk.get("ss", []) if s.get("slot") == "conclusion"]
    assert_true(bool(conclusions), "結論段一句都沒有，前端會看不到佔位塊")
    for s in conclusions:
        assert_eq(s["placeholder"], True, f"結論句 {s['id']} 沒標 placeholder")
        assert_eq(s["origin"], "human_required", f"結論句 {s['id']} 的 origin 應為 human_required")


def test_adversarial_injection_is_marked_in_the_payload() -> None:
    """對抗測資的假引用必須帶標記出場——沒標記的話，任何人截 doc[] 貼進簡報
    就會看到一句沒註記的假條號（HANDOFF 第二節 ⚠1）。"""
    p = payload("synthetic-blocked-01")
    marked = [s for bk in p["doc"] for s in bk.get("ss", []) if s.get("adversarial")]
    assert_true(bool(marked), "對抗案例的 doc[] 裡找不到任何 adversarial 標記")
    for s in marked:
        assert_true(bool(s.get("adversarial_note")), f"句子 {s['id']} 標了 adversarial 卻沒說明為什麼")


# ── 6. 兩個案例的契約形狀必須一致 ───────────────────────────────────
def test_all_synthetic_cases_share_the_same_top_level_shape() -> None:
    """不管案例走哪條分支，payload 的頂層欄位集合都要一樣。

    否則前端得寫「這個欄位在某些案例會不見」的防禦碼，那就不是契約了。
    """
    shapes = {cid: set(payload(cid)) for cid in list_synthetic_cases()}
    ref_id, ref_keys = next(iter(shapes.items()))
    for cid, keys in shapes.items():
        assert_eq(keys, ref_keys, f"{cid} 與 {ref_id} 的頂層欄位集合不同（差異：{keys ^ ref_keys}）")
