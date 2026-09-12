"""聊天誠實燈號（機械規則，零 LLM）單元測試。

契約：`docs/spec/2026-09-12-chat-honesty-lamps.md` §3（燈號規則）、§3.3（引用白名單）。

這支釘的是**規則本身**，不是覆蓋率。每一條都對應一個「換一個看似合理的實作就會紅」
的分歧點：

- `tier` 用 `tier_for_lamp()` 而不是 `tier_of()`——兩者在 `origin="llm"` 上答案相反，
  用錯那支會把聊天的紅燈標成「有出處」（spec §3.0）。這是全檔最重要的一條。
- `g` 在任何輸入下都不出現（spec §3.1）——聊天沒有 N6，KB 命中也沒對資料集實檔驗證。
- 捏造的引用編號強制紅燈（spec §3.3）——不是靜默丟掉。
- 數字類問題寧可誤判也不漏判（spec §3.2）——所以連「多抓」的形狀也要釘住，
  否則下一個人會「修掉」它。
- `backend/llm/chat.py` 的 import 圖不得碰 orchestrator 與六節點（spec §4.0）。
  AC15 用 `grep` 驗，但 `grep` 分不出註解與 import 也擋不住 alias；這裡用 AST 驗。
"""
from __future__ import annotations

import ast
import pathlib

from backend.config import settings
from backend.config.origin_registry import TIER_HUMAN, TIER_SOURCED, tier_of
import backend.llm.chat as chat_mod
from backend.llm.chat import (
    CASE_SECTIONS,
    NUMERIC_Q,
    TOOL_LABELS,
    RefBook,
    cited_ids,
    classify_answer,
    is_numeric_question,
)
from backend.retrieval.base import Hit

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _hit(hid: str = "kb-1", title: str = "新北市政府 112 年訴字第 1 號",
         verified: bool = False, provenance: str | None = "official") -> Hit:
    payload = {"provenance": provenance} if provenance is not None else {}
    return Hit(
        id=hid,
        title=title,
        score=0.79,
        source="歷史訴願決定書/x.txt",
        verified=verified,
        note="KB 命中，未對資料集實檔驗證",
        payload=payload,
    )


# ── 規則 1：數字類問題 ──────────────────────────────────────────────

def test_numeric_question_goes_red_and_carries_the_deadline_redirect():
    v = classify_answer("還剩幾天可以提訴願？", "大約還有 20 天。", RefBook())
    assert v.lamp == "r"
    assert v.origin == "human_required"
    assert v.redirect is not None
    assert v.redirect["endpoint"] == "/api/deadline"


def test_numeric_rule_wins_even_when_the_answer_cites_a_real_hit():
    """規則依序判：數字類在最前面，命中了就回，不會被規則 3 的 `y` 蓋過。

    這條防的是「把四條規則寫成互相獨立的 if、讓後面的覆蓋前面的」那種實作。
    """
    rb = RefBook()
    rb.add(_hit())
    v = classify_answer("提訴願的期限怎麼算？", "依 [c1] 所示，期間為 30 日。", rb)
    assert v.lamp == "r"
    assert v.origin == "human_required"
    assert v.refs == []


def test_every_listed_keyword_actually_triggers_the_numeric_rule():
    """清單是規則的一部分，不是註解。少接上一個詞，那個詞就是沒作用的裝飾。"""
    for kw in NUMERIC_Q:
        assert is_numeric_question(f"請問{kw}的事"), kw


def test_digit_plus_quantifier_triggers_even_without_a_listed_keyword():
    # 「30 日內」不含 NUMERIC_Q 任一關鍵詞，靠的是「數字＋量詞」那條
    assert is_numeric_question("30 日內要做什麼？")
    assert is_numeric_question("三十日內要做什麼？")  # 中文數字也要算


def test_over_triggering_on_a_case_number_is_intended_not_a_bug():
    """「112 年訴字第 123 號」含數字與「年」→ 判成數字類。

    **這是刻意接受的誤判**（spec §3.2「寧可誤判成紅燈，不可漏判」）。釘住它是為了讓
    下一個人知道這不是漏洞——要「修」它之前得先讀規格，因為反方向的代價是
    LLM 自己算了一個期限並被承辦人採用（CONSTITUTION §4 的紅線）。
    """
    assert is_numeric_question("本案 112 年訴字第 123 號的爭點是什麼？")


def test_a_plain_question_is_not_numeric():
    """光是「寧可誤判」不能變成「全部都判」——否則規則 3 永遠走不到。"""
    assert not is_numeric_question("有沒有類似的訴願決定可以參考？")
    assert not is_numeric_question("本案的爭點是什麼？")


# ── 規則 2：refine_text ────────────────────────────────────────────

def test_refine_text_forces_red_even_with_retrieval_hits_in_the_same_turn():
    rb = RefBook()
    rb.add(_hit())
    v = classify_answer("把這句改順一點", "本件訴願為無理由。[c1]", rb, refine_used=True)
    assert v.lamp == "r"
    assert v.origin == "llm"
    assert v.refine_used is True


# ── 規則 3／4：有沒有真的引到 ───────────────────────────────────────

def test_cited_hit_is_sourced_and_lists_the_ref():
    rb = RefBook()
    rb.add(_hit())
    v = classify_answer("有沒有類似的決定？", "檢索到一件：[c1] 同樣涉及裁處。", rb)
    assert v.lamp == "y"
    assert v.origin == "retrieval"
    assert [r["id"] for r in v.refs] == ["c1"]


def test_hits_that_the_answer_never_cites_do_not_count_as_sourced():
    """有 refs 但回答一個都沒引 → 落規則 4。

    這條防的是「只要工具有命中就給 y」那種實作——那等於系統替一句沒有真的用到
    來源的話背書。
    """
    rb = RefBook()
    rb.add(_hit())
    v = classify_answer("有沒有類似的決定？", "查到一些相關資料。", rb)
    assert v.lamp == "r"
    assert v.origin == "llm"
    assert v.refs == []


def test_no_hits_at_all_is_red_not_an_error():
    v = classify_answer("本案有沒有引用釋字第 999 號？", "卷內查無釋字第 999 號的引用。", RefBook())
    assert v.lamp == "r"
    assert v.refs == []


# ── spec §3.3：捏造的編號 ──────────────────────────────────────────

def test_a_fabricated_ref_id_forces_red_and_is_reported_not_swallowed():
    rb = RefBook()
    rb.add(_hit())
    v = classify_answer("有沒有類似的決定？", "參見 [c1] 與 [c9]。", rb)
    assert v.lamp == "r", "引了白名單外的編號就不能是 y"
    assert v.dropped_refs == ["c9"]


def test_a_fabricated_id_without_brackets_is_still_caught():
    """`dropped_refs` 用寬鬆樣式是刻意的：漏認一個捏造編號會讓假引用矇混過關。

    嚴格樣式（規則 3）漏認只會多紅一則，是安全方向；這裡漏認是不安全方向，
    所以兩邊刻意不對稱。換成「兩邊都用嚴格樣式」這條會紅。
    """
    v = classify_answer("有沒有類似的決定？", "參見 c9 的見解。", RefBook())
    assert v.dropped_refs == ["c9"]
    assert v.lamp == "r"


# ── spec §3.0：tier 必須用 tier_for_lamp，不是 tier_of ──────────────

def test_red_from_llm_is_human_required_not_sourced():
    """**全檔最重要的一條。**

    `origin_registry.tier_of("llm")` 回「有出處」（因為六節點路徑上模型句子必定經過
    N6 驗引用），但**聊天沒有 N6**，那個前提不成立。實作若改用 `tier_of()`，
    這條會紅。
    """
    v = classify_answer("本案的爭點是什麼？", "爭點在於裁量是否逾越。", RefBook())
    assert v.origin == "llm"
    assert v.tier == TIER_HUMAN
    # 釘住「這兩支函式在這一格真的不同」——哪天 registry 改了，這條會先紅
    assert tier_of("llm") != TIER_HUMAN, "前提消失了：tier_of 不再與 tier_for_lamp 分歧，spec §3.0 要重寫"


def test_sourced_answer_uses_the_sourced_tier():
    rb = RefBook()
    rb.add(_hit())
    v = classify_answer("有沒有類似的決定？", "[c1] 可參考。", rb)
    assert v.tier == TIER_SOURCED


# ── spec §3.1：g 永不出現 ──────────────────────────────────────────

def test_green_never_appears_for_any_input():
    rb = RefBook()
    rb.add(_hit(verified=True))  # 連 verified 的命中也不得升級成 g
    cases = [
        ("有沒有類似的決定？", "[c1] 可參考。", rb, False),
        ("還剩幾天？", "20 天。", rb, False),
        ("改順一點", "改好了。[c1]", rb, True),
        ("爭點是什麼？", "爭點在此。", RefBook(), False),
        ("爭點是什麼？", "參見 [c9]。", rb, False),
        ("", "", RefBook(), False),
    ]
    for q, a, book, refine in cases:
        v = classify_answer(q, a, book, refine_used=refine)
        assert v.lamp in ("y", "r"), (q, a, v.lamp)
        assert v.lamp != "g", (q, a)


# ── RefBook ───────────────────────────────────────────────────────

def test_refbook_numbers_are_monotonic_across_tool_calls():
    """spec §3.3：`KBRetriever.search` 每次呼叫都從 `kb-1` 重編，同回合呼叫兩次會撞號。

    RefBook 的編號必須跨工具連續，否則 `refs[]` 會指錯來源。這是實際會發生的碰撞。
    """
    rb = RefBook()
    first = rb.add_all([_hit("kb-1", "甲"), _hit("kb-2", "乙")])
    second = rb.add_all([_hit("kb-1", "丙")])  # 第二次呼叫，KB 又從 kb-1 開始
    assert [h["id"] for h in first] == ["c1", "c2"]
    assert [h["id"] for h in second] == ["c3"], "撞號了：第二個工具的 kb-1 蓋掉了第一個"
    assert rb.get("c1")["t"] == "甲"
    assert rb.get("c3")["t"] == "丙"


def test_provenance_is_none_for_a_hit_that_has_no_such_field():
    """法條查表的 Hit 的 payload 沒有 provenance（spec §4.2）。

    必須是 `None` 而不是缺這個鍵——前端要處理 `null`，拿不到鍵會變成 `undefined`。
    """
    rb = RefBook()
    entry = rb.add(_hit(provenance=None))
    assert "provenance" in entry
    assert entry["provenance"] is None


def test_verified_is_carried_through_not_forced():
    """`search_regulations` 的 `verified` 可 true 可 false，原樣帶出（spec §4.0）。

    不得自作主張填 true——猜一個條號去查表，猜錯就變成「誤讀→綠燈」。
    """
    rb = RefBook()
    assert rb.add(_hit(verified=True))["verified"] is True
    assert rb.add(_hit(verified=False))["verified"] is False


def test_cited_ids_reads_multiple_ids_in_one_bracket():
    assert cited_ids("參見 [c1, c2] 與 [c3]。") == ["c1", "c2", "c3"]
    assert cited_ids("參見【c1】與（c2）。") == ["c1", "c2"]


# ── spec §4.0：import 圖的契約（AC15 的 AST 版，騙不過去）────────────

def test_chat_module_never_imports_orchestrator_or_nodes():
    """AC15 的強化版。

    AC15 用 `grep 'backend\\.orchestrator|backend\\.nodes'`，它有兩個盲點：
    註解裡提到那兩個套件就會誤報紅；而 `import backend.orchestrator.graph as g`
    以外的寫法（或先 import 一個會轉手拉進去的模組）它也擋不住。

    這條只看 AST 的 import 節點，看的是**真的 import 了什麼**。
    """
    tree = ast.parse((ROOT / "backend" / "llm" / "chat.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    bad = [m for m in imported
           if m.startswith("backend.orchestrator") or m.startswith("backend.nodes")]
    assert not bad, f"spec §4.0：聊天層不得 import orchestrator 與六節點，實際 import 了 {bad}"


def test_chat_module_does_not_import_fastapi():
    """純 agent 模組，之後要能整份搬上託管服務（乙案 AgentCore）。"""
    tree = ast.parse((ROOT / "backend" / "llm" / "chat.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [m for m in imported if m.split(".")[0] in ("fastapi", "starlette")]


def test_ref_prefixes_has_exactly_one_source_and_nobody_copies_the_literal():
    """引用白名單只能有一個事實來源，誰都不准複製字面值。

    S2b 原本的規則是「賦值只剩 `backend/retrieval/kb.py` 一處，N5 改成 import」。
    **來源換了、規則沒換**：白名單現在由 `settings.ref_prefixes()` 供給
    （`.env` 的 `REF_PREFIXES` 帶值，預設只有 `settings.DEFAULT_REF_PREFIXES` 一份）。

    為什麼搬到 settings 而不是留在檢索層：目錄名跟著 corpus 走——第三方 corpus 的
    函釋在 `行政函釋_全量/`（4,520 筆），寫死 `行政函釋/` 的話 N5 只看得到自己整理的
    那 10 份，**而且不報錯**。原本的分層理由（聊天層不得 import `backend.nodes.*`）
    仍然成立，`settings` 比聊天層與檢索層都底層，誰都可以依賴它。

    **這裡刻意用 AST 讀原始碼，不 import `backend/nodes/n5_draft.py`**：把六節點
    import 進這支純函式測試，等於讓聊天層的測試背著 orchestrator 跑，正是 spec §4.0
    要避免的事。AST 也看得到縮排過的賦值與 `REF_PREFIXES, X = ...` 這種行首 grep
    看不到的寫法。
    """
    def _assignments(rel: str) -> list[str]:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for t in targets:
                for name in ast.walk(t):
                    if isinstance(name, ast.Name) and name.id == "REF_PREFIXES":
                        found.append(rel)
        return found

    def _calls_settings(rel: str) -> bool:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        return any(isinstance(n, ast.Attribute) and n.attr == "ref_prefixes"
                   for n in ast.walk(tree))

    for rel in ("backend/retrieval/kb.py", "backend/nodes/n5_draft.py", "backend/llm/chat.py"):
        assert _assignments(rel) == [], f"{rel} 自己賦值了一份 REF_PREFIXES，字面值會漂"
    assert len(_assignments("backend/config/settings.py")) == 0, \
        "settings 也不該有 REF_PREFIXES 這個名字的賦值——預設值叫 DEFAULT_REF_PREFIXES"
    assert "DEFAULT_REF_PREFIXES" in (ROOT / "backend/config/settings.py").read_text(encoding="utf-8"), \
        "settings 缺 DEFAULT_REF_PREFIXES，白名單沒有事實來源"

    for rel in ("backend/nodes/n5_draft.py", "backend/llm/chat.py"):
        assert _calls_settings(rel), f"{rel} 沒有走 settings.ref_prefixes()，可能又複製了一份"


# ── S4：工具層（離線，不打 Bedrock）─────────────────────────────────

class _FakeRetriever:
    """假檢索器。`calls` 記下每次的 (query, filters)，讓測試看得到 prefix 有沒有傳對。"""

    def __init__(self, hits: list[Hit] | None = None, boom: Exception | None = None) -> None:
        self._hits = hits or []
        self._boom = boom
        self.calls: list[tuple[str, dict | None]] = []

    def search(self, query: str, filters: dict | None = None, top_k: int = 5) -> list[Hit]:
        self.calls.append((query, filters))
        if self._boom is not None:
            raise self._boom
        return list(self._hits)


def _tools(monkey_throttle: list, retriever=None, snapshot=None, payload=None, events=None,
           run_pipeline=None, run_id=None, case_manifest=None, refbook=None):
    """建一組 ChatTools，並把 `_throttle` 換成計數器（真的節流會讓測試睡好幾秒）。"""
    chat_mod._throttle = lambda: monkey_throttle.append(1)
    emit = (lambda name, data: events.append((name, data))) if events is not None else None
    return chat_mod.ChatTools(
        payload if payload is not None else {"intake": {"案由": "x"}},
        refbook if refbook is not None else RefBook(), retriever, snapshot, emit,
        run_pipeline=run_pipeline, run_id=run_id, case_manifest=case_manifest,
    )


class _FakePipeline:
    """假的 `run_pipeline` adapter：發節點事件、回一份新 run 的分區。

    **回 plain dict 不回 CaseState**——那正是真 adapter 的契約
    （`backend/api/chat.py:_pipeline_adapter`）。假貨長得跟真貨不一樣的話，
    這些測試綠了也不代表接得上。
    """

    def __init__(self, nodes: list[str], sections: dict | None = None,
                 state: str = "SCREENED", boom: Exception | None = None,
                 timings: dict | None = None) -> None:
        self._nodes = nodes
        self._sections = sections or {}
        self._state = state
        self._boom = boom
        self.timings = timings or {n: (i + 1) * 1000 for i, n in enumerate(nodes)}
        self.calls: list[dict] = []

    def __call__(self, *, to_node=None, from_node="n1", base_run_id=None,
                 overrides=None, on_event=None):
        self.calls.append({"to_node": to_node, "from_node": from_node,
                           "base_run_id": base_run_id, "overrides": overrides})
        if self._boom is not None:
            raise self._boom
        for n in self._nodes:
            if on_event:
                on_event("node_start", {"node": n})
                on_event("node_done", {"node": n, "elapsed_ms": self.timings[n],
                                       "degraded": n == "n4"})
        return {"run_id": "run-new-1", "state": self._state,
                "node_timings": dict(self.timings), "cite_count": 7,
                "artifact_id": None, "has_draft": self._state == "VERIFIED",
                "sections": self._sections}


_MANIFEST_OK = {"laws": [{"id": "L1", "t": "廢棄物清理法第 2 條"}],
                "references": [{"id": "C1", "t": "新北市政府 112 年訴字第 1 號"}]}


def test_every_tool_entry_point_throttles_exactly_once():
    """spec §8.3：**每個**工具進入點各呼叫一次 `_throttle()`。

    門檻刻意寫成「覆蓋每一個工具」而不是「總共呼叫幾次」——數量門檻可以用重複呼叫
    同一個工具湊出來，覆蓋率不行。

    為什麼要逐工具補：既有 `_throttle()` 只管 `_invoke_structured` 每一次送出的請求，
    **Strands agent loop 一次呼叫內的工具往返不經過它**
    （`backend/llm/client.py` 的 docstring 明寫）。工具回傳之後模型就會再被呼叫一次。
    """
    calls: list = []
    retriever = _FakeRetriever()
    checked = set()

    for name, invoke in (
        ("extract_case_document", lambda t: t.extract_case_document()),
        ("generate_decision_draft", lambda t: t.generate_decision_draft()),
        ("search_regulations", lambda t: t.search_regulations("訴願法第 14 條")),
        ("search_similar_decisions", lambda t: t.search_similar_decisions("裁處")),
        ("retrieve_refs", lambda t: t.retrieve_refs("信賴保護")),
        ("read_case", lambda t: t.read_case("intake")),
        ("refine_text", lambda t: t.refine_text("本件訴願為無理由。")),
    ):
        calls.clear()
        t = _tools(calls, retriever, snapshot={"laws": {}})
        try:
            invoke(t)
        except Exception:  # noqa: BLE001 — refine_text 沒裝 strands 會 LLMError，節流在那之前
            pass
        assert len(calls) == 1, f"{name} 的進入點節流了 {len(calls)} 次，應該正好 1 次"
        checked.add(name)

    assert checked == set(TOOL_LABELS), f"有工具沒被這條測試涵蓋：{set(TOOL_LABELS) - checked}"


def test_refine_text_throttles_before_it_can_fail():
    """節流要在進入點，不是在成功路徑的尾巴——失敗的呼叫一樣佔用了 RPS 額度。"""
    calls: list = []
    t = _tools(calls)
    try:
        t.refine_text("x")
    except Exception:  # noqa: BLE001
        pass
    assert calls == [1]
    assert t.refine_used is True, "旗標要在進入點就立起來，失敗也算用過"


def test_retrieve_refs_passes_the_shared_prefix_filter():
    """`retrieve_refs` 只准查函釋與判解兩個前綴，而且用的是 settings 那一份。"""
    calls: list = []
    r = _FakeRetriever()
    t = _tools(calls, r)
    t.retrieve_refs("信賴保護")
    assert r.calls[0][1] == {"prefix": settings.ref_prefixes()}


def test_similar_decisions_does_not_pin_a_prefix():
    """相似案通道要讓 kb.py 自己走兩批配額，不該被聊天層寫死 prefix。"""
    calls: list = []
    r = _FakeRetriever()
    t = _tools(calls, r)
    t.search_similar_decisions("裁處")
    assert r.calls[0][1] is None


def test_a_failed_search_is_reported_as_failure_not_as_no_results():
    """檢索失敗**不得**靜默回空。

    回「查無結果」會讓模型把一次連線失敗講成「資料庫裡沒有這個東西」——
    那是一句沒有依據卻聽起來確定的話，正是分層誠實要擋的。
    """
    calls: list = []
    events: list = []
    t = _tools(calls, _FakeRetriever(boom=RuntimeError("KB 連不上")), events=events)
    out = t.retrieve_refs("信賴保護")
    assert "查無" not in out, "訊息裡出現『查無』，模型很容易照抄成『資料庫裡沒有』"
    assert "失敗" in out
    note = [d["note"] for n, d in events if n == "tool_result"][0]
    assert "失敗" in note


def test_no_retriever_says_so_instead_of_pretending_to_have_searched():
    calls: list = []
    t = _tools(calls, retriever=None)
    out = t.search_similar_decisions("裁處")
    assert "查不了" in out


def test_refbook_numbers_stay_continuous_across_different_tools():
    """跨工具連續編號：第一個工具配到 c1，第二個工具接 c2，不重來。"""
    calls: list = []
    r = _FakeRetriever([_hit("kb-1", "甲")])
    t = _tools(calls, r)
    t.retrieve_refs("a")
    t.search_similar_decisions("b")
    assert sorted(t.refbook.whitelist()) == ["c1", "c2"]


def test_tool_events_carry_the_contract_label():
    """spec §4.0：後端帶 `label`，前端不必自己維護對照表；兩邊必須一致。"""
    calls: list = []
    events: list = []
    t = _tools(calls, _FakeRetriever(), events=events)
    t.retrieve_refs("x")
    kinds = [n for n, _ in events]
    assert kinds == ["tool_call", "tool_result"], "tool_call 與 tool_result 要成對且有序"
    assert events[0][1]["label"] == TOOL_LABELS["retrieve_refs"]


def test_read_case_rejects_a_section_that_does_not_exist():
    """值域固定，模型不能亂要一個分區；而且要說得出可讀的有哪些。"""
    calls: list = []
    t = _tools(calls)
    out = t.read_case("secret_notes")
    assert "沒有" in out
    for s in CASE_SECTIONS:
        assert s in out


def test_read_case_only_reads_the_dict_it_was_given():
    """spec §4.0：payload 由呼叫端提供。這裡餵一個只有自己知道的值，讀得出來就證明
    它讀的是參數，不是自己回頭去 load_run。"""
    calls: list = []
    t = _tools(calls, payload={"facts_excerpt": "只有這個測試知道的字串"})
    assert "只有這個測試知道的字串" in t.read_case("facts_excerpt")


def test_read_case_produces_no_refs():
    """`read_case` 不產生引用（spec §4.0 的表）：hits 恆為 []，內容放 note。"""
    calls: list = []
    events: list = []
    t = _tools(calls, events=events)
    t.read_case("intake")
    assert [d["hits"] for n, d in events if n == "tool_result"] == [[]]
    assert len(t.refbook) == 0


# ── S5：HTTP 層的結構契約（AST；這層要 fastapi 才 import 得動）───────

def _api_chat_src() -> str:
    return (ROOT / "backend" / "api" / "chat.py").read_text(encoding="utf-8")


def _imports_of(src: str) -> list[str]:
    out: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def test_http_layer_does_the_data_fetching():
    """spec §4.0 的另一半：`load_run` → `build_payload` 這條路**只**出現在 HTTP 層。

    AC15 驗的是 agent 層不碰 orchestrator；這條驗的是那些 import 真的有地方可去。
    只驗一半的話，「兩層都不取資料」也會綠。
    """
    mods = _imports_of(_api_chat_src())
    assert "backend.orchestrator.graph" in mods
    assert "backend.orchestrator.runstore" in mods


def test_http_layer_never_touches_the_model_client_internals():
    """AC3 的 AST／逐行版，收窄到 `backend/api/chat.py`。

    AC3 原文掃整個 `backend/api/`，但 `backend/api/app.py` 的健康檢查本來就會提到
    `strands-agents`（它要回報這個套件缺不缺），所以那條在**乾淨工作樹上就是紅的**
    ——是假失敗，不是本 change 造成的。這裡只掃聊天端點自己，並跳過註解行。
    """
    mods = _imports_of(_api_chat_src())
    assert not [m for m in mods if m.split(".")[0] == "strands"]
    for i, line in enumerate(_api_chat_src().splitlines(), 1):
        code = line.split("#", 1)[0]
        for bad in ("_load_model", "_invoke_structured"):
            assert bad not in code, f"backend/api/chat.py:{i} 直接碰了 {bad}"


def test_the_503_gate_runs_before_the_stream_is_opened():
    """spec §2.3 紅線：閘門在**開串流之前**。

    半開一條串流再道歉，前端會先渲染出一個空白對話泡——那就是在演一段沒發生的對話。
    這條比對 `_live_gate()` 與 `StreamingResponse(` 在 `chat()` 函式體裡的行號。
    """
    tree = ast.parse(_api_chat_src())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "chat")

    def _lines(name: str) -> list[int]:
        return [n.lineno for n in ast.walk(fn)
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == name]

    gate, stream, turn = _lines("_live_gate"), _lines("StreamingResponse"), _lines("_run_turn")
    assert gate, "chat() 裡找不到 _live_gate() 呼叫"
    assert stream and turn, "chat() 裡找不到 StreamingResponse 或 _run_turn"
    # **比 StreamingResponse 更早的那條線是 `_run_turn`**：`?stream=0` 不開串流，
    # 但它一樣會呼叫模型。只比對 StreamingResponse 的版本擋不住「把閘門搬到
    # StreamingResponse 前一行、卻排在 stream=0 分支之後」——變異測試實際漏掉過一次。
    assert max(gate) < min(turn), "503 閘門必須在任何一條路徑呼叫模型之前"
    assert max(gate) < min(stream), "503 閘門必須在開串流之前"


def test_both_streaming_and_oneshot_share_one_turn_function():
    """spec §2.2：`?stream=0` 跟 SSE 走同一條產生路徑，兩邊才不會長歪。

    各寫一份的話，降級模式會慢慢變成一個沒人驗的殭屍路徑——而它正是賽場斷流時
    唯一還能用的那條。
    """
    tree = ast.parse(_api_chat_src())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "chat")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_run_turn"]
    assert len(calls) == 2, f"chat() 應該在兩條路徑各呼叫一次 _run_turn，實際 {len(calls)} 次"


def test_message_emptiness_is_not_left_to_pydantic():
    """spec §2.3 說 `message` 空是 **400**；pydantic 的 `min_length` 會回 422。

    契約凍結、前端照 400 寫分支，所以空值檢查必須手動做。實測過：加了 `min_length=1`
    的版本回的是 422。
    """
    tree = ast.parse(_api_chat_src())
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "ChatIn")
    # 用 AST 只看**真正的** Field() 呼叫，不用字串比對——不然這個檔自己解釋
    # 「為什麼不用 min_length」的 docstring 就會觸發這條檢查（同一個坑本檔已踩過三次：
    # grep／文字比對分不出註解與程式碼，寫得越清楚越容易誤報）。
    bad = [kw.arg for n in ast.walk(cls) if isinstance(n, ast.Call)
           for kw in n.keywords if kw.arg == "min_length"]
    assert not bad, "ChatIn 用 pydantic 的 min_length 擋空字串會回 422，不是契約寫的 400"
    assert any(isinstance(n, ast.FunctionDef) and n.name == "_validate"
               for n in ast.walk(tree)), "找不到手動的空值檢查 _validate()"


# ── 2026-09-12 真 Bedrock 實測抓到的兩個缺陷 ────────────────────────

def test_a_bogus_id_alongside_a_real_one_still_forces_red():
    """**這條釘的是那個僥倖情境**（tech lead 指名）。

    原版只偵測小寫 `c\\d+`，模型走 `read_case` 讀卷內之後照著引大寫 `[C1]`…`[C5]`，
    七個引用一個都沒進 `dropped_refs`。那次沒出事只是因為規則 4 已經判紅。

    但同一輪若**既引了真的 `c1`（檢索來的）又引了一個不在任何白名單裡的號**，
    規則 3 會給 `y`——綠燈配上一個未經檢查的引用，正是這條規則存在的理由。
    """
    rb = RefBook()
    rb.add(_hit())                       # 發出 c1
    v = classify_answer("有沒有類似的？", "參見 [c1] 與 [C7]。", rb)
    assert "C7" in v.dropped_refs, f"未偵測到白名單外的引用：{v.dropped_refs}"
    assert v.lamp == "r", "有未經檢查的引用卻給了 y——這正是僥倖那一格"


def test_uppercase_and_law_prefixes_are_detected():
    """卷內可引用的 id 有三種前綴，三種都要認（實測：`c1` 檢索、`C1` 相似案、`L1` 法條）。"""
    for bogus in ("C9", "L9", "c9"):
        v = classify_answer("問", f"參見 [{bogus}]。", RefBook())
        assert v.dropped_refs == [bogus], f"{bogus} 沒被偵測到"
        assert v.lamp == "r"


def test_case_ids_read_from_the_file_are_whitelisted_not_fabricated():
    """裁定：卷內資料是**最強的出處**，不是最弱的。

    `read_case` 確實把那些 id 回傳給模型了，所以引用它們不是捏造。禁止模型引用卷證，
    會讓它指不出本案最可驗的來源——那是把規則的手段誤當成目的。
    """
    calls: list = []
    t = _tools(calls, payload={"laws": [{"id": "L1", "t": "訴願法第 14 條"},
                                        {"id": "L2", "t": "行政程序法第 74 條"}]})
    t.read_case("laws")
    v = classify_answer("本案引了哪些法條？", "依 [L1] 與 [L2]。", t.refbook)
    assert v.dropped_refs == [], "卷內 id 被誤判成捏造"
    assert v.lamp == "y", "卷內引用應該算有出處"
    assert [r["id"] for r in v.refs] == ["L1", "L2"]


def test_case_refs_are_marked_as_record_not_retrieval():
    """卷內引用**不該看起來像 KB 命中**。每筆 ref 自己帶 origin，前端才分得出來。"""
    calls: list = []
    t = _tools(calls, payload={"cases": [{"id": "C1", "t": "某決定"}]})
    t.read_case("cases")
    assert t.refbook.get("C1")["origin"] == "record"
    rb2 = RefBook(); rb2.add(_hit())
    assert rb2.get("c1")["origin"] == "retrieval"


def test_read_case_still_emits_no_hits_even_though_it_registers_ids():
    """spec §4.0／§4.2 凍結：`read_case` 的 `hits` 恆為 `[]`。

    白名單是後端內部狀態，**不走事件**——所以註冊卷內 id 不改動 Pink 那邊的契約。
    """
    calls: list = []
    events: list = []
    t = _tools(calls, payload={"laws": [{"id": "L1", "t": "x"}]}, events=events)
    t.read_case("laws")
    assert [d["hits"] for n, d in events if n == "tool_result"] == [[]]
    assert "L1" in t.refbook.whitelist(), "id 沒進白名單"


def test_case_ids_keep_their_own_numbers():
    """不重新編號：`L1`、`C3` 已經印在承辦人畫面的法條卡與相似案卡上，
    改號會讓聊天講的號跟畫面上的對不起來。"""
    calls: list = []
    t = _tools(calls, payload={"laws": [{"id": "L6", "t": "x"}]})
    t.read_case("laws")
    assert "L6" in t.refbook.whitelist()
    assert t.refbook.get("L6")["t"] == "x"


def test_unknown_exceptions_are_internal_not_tool():
    """spec §4.6：認不出來的例外歸 `internal`，**不歸 `tool`**。

    原版預設 `tool`，2026-09-12 實測踩到：不存在的 model id 讓 botocore 拋
    `ValidationException`，落到預設值變成 `tool` → 前端顯示「查詢來源失敗」，
    但壞的是模型。猜一個具體的 stage 比誠實說「內部錯誤」更糟。

    這裡用 AST 讀 `backend/api/chat.py`（那個檔要 fastapi 才 import 得動）。
    """
    src = (ROOT / "backend" / "api" / "chat.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_stage_of")
    body = fn.body
    last = body[-1]
    assert isinstance(last, ast.Return) and getattr(last.value, "value", None) == "internal", \
        "_stage_of 的兜底值必須是 internal——未知失敗不得被說成查詢來源失敗"
    # botocore 的例外要判成 model（檢索例外在工具層就被攔掉，到不了這裡）
    assert "botocore" in src, "沒有把 botocore 的例外判成 model"


# ── 契約 v2 §2.3 ②：call_id / tool_result.status / ack.session_id ──────

def test_the_same_tool_called_twice_gets_two_different_pairable_call_ids():
    """契約 v2 §2.3 ②：`tool` + `seq` 配不起來，所以要有 `call_id`。

    同一回合內同一支工具被呼叫兩次是常態（讀完爭點常會再查一次法規）。
    沒有配對鍵，前端只能猜哪個 result 對應哪個 call——**猜錯就是把 A 的結果
    畫進 B 的工具卡**。
    """
    calls: list = []
    events: list = []
    t = _tools(calls, _FakeRetriever([_hit("kb-1", "甲")]), events=events)
    t.search_similar_decisions("第一次")
    t.search_similar_decisions("第二次")
    pairs = [(n, d["call_id"]) for n, d in events]
    assert pairs == [("tool_call", "tc-1"), ("tool_result", "tc-1"),
                     ("tool_call", "tc-2"), ("tool_result", "tc-2")], pairs


def test_tool_result_status_tells_empty_apart_from_failed():
    """契約 v2 §2.3 ②：`empty` 與 `failed` 長得一模一樣的話，前端會把
    「查詢來源壞了」畫成「資料庫裡沒有這筆資料」。這是兩句不同的話。
    """
    calls: list = []

    ev_ok: list = []
    _tools(calls, _FakeRetriever([_hit("kb-1", "甲")]), events=ev_ok).retrieve_refs("x")
    assert [d["status"] for n, d in ev_ok if n == "tool_result"] == ["ok"]

    ev_empty: list = []
    _tools(calls, _FakeRetriever([]), events=ev_empty).retrieve_refs("x")
    assert [d["status"] for n, d in ev_empty if n == "tool_result"] == ["empty"]

    ev_failed: list = []
    _tools(calls, _FakeRetriever(boom=RuntimeError("KB 連不上")),
           events=ev_failed).retrieve_refs("x")
    result = [d for n, d in ev_failed if n == "tool_result"][0]
    assert result["status"] == "failed"
    assert "失敗" in result["note"], "failed 一定要說得出原因，否則跟 empty 沒兩樣"

    ev_none: list = []
    _tools(calls, retriever=None, events=ev_none).search_similar_decisions("x")
    assert [d["status"] for n, d in ev_none if n == "tool_result"] == ["failed"], \
        "沒有可用來源是失敗不是查無——查無的前提是真的查過了"


def test_read_case_status_separates_a_bad_section_from_an_empty_one():
    """要一個不存在的分區是呼叫壞了；分區存在但沒東西才是 empty。"""
    calls: list = []
    ev_bad: list = []
    _tools(calls, events=ev_bad).read_case("secret_notes")
    assert [d["status"] for n, d in ev_bad if n == "tool_result"] == ["failed"]

    ev_empty: list = []
    _tools(calls, events=ev_empty, payload={"intake": {}}).read_case("intake")
    assert [d["status"] for n, d in ev_empty if n == "tool_result"] == ["empty"]


def test_tool_result_always_carries_run_id_and_graph_keys():
    """契約 v2 §3.1：三類工具各填自己那塊、其餘為 null。

    **省略鍵不等於 null**：前端照契約寫死解構，拿到 undefined 跟拿到 null 的
    分支不一樣。檢索類工具也要帶 `run_id: null`。
    """
    calls: list = []
    events: list = []
    _tools(calls, _FakeRetriever([_hit()]), events=events).retrieve_refs("x")
    d = [d for n, d in events if n == "tool_result"][0]
    for key in ("call_id", "tool", "status", "note", "hits", "run_id", "graph"):
        assert key in d, f"tool_result 少了契約共通欄位 {key}"
    assert d["run_id"] is None and d["graph"] is None


# ── 契約 v2 §0.1：兩支 pipeline 工具 ────────────────────────────────

def test_extract_forwards_real_node_events_as_tool_steps_with_labels():
    """`tool_step` 的來源必須是流水線真的發出來的事件。

    前端 mock 那版是 `await sleep(900)` 寫死的六行字。這條釘的是「每一行都對應到
    一個跑過的節點」，而且 `elapsed_ms` 是**節點回報的值**，不是另外估的。
    """
    calls: list = []
    events: list = []
    fake = _FakePipeline(["n1", "n2", "n3"], sections={"screen": {"x": 1}})
    t = _tools(calls, events=events, run_pipeline=fake)
    t.extract_case_document()

    steps = [d for n, d in events if n == "tool_step"]
    assert [(s["step"], s["status"]) for s in steps] == [
        ("n1", "running"), ("n1", "done"),
        ("n2", "running"), ("n2", "done"),
        ("n3", "running"), ("n3", "done"),
    ], steps
    assert [s["label"] for s in steps if s["status"] == "done"] == [
        "讀卷抽取", "案件分類", "程序審查"], "label 由後端帶，前端不維護對照表"
    # elapsed_ms 與 node_timings 數值相同——不是另外估的
    done_ms = {s["step"]: s["elapsed_ms"] for s in steps if s["status"] == "done"}
    assert done_ms == {k: v for k, v in fake.timings.items() if k in done_ms}
    # degraded 照實帶（假管線把 n4 標降級，這裡沒跑 n4，所以全 False）
    assert all(s["degraded"] is False for s in steps if s["status"] == "done")
    # 所有 tool_step 都掛在同一個 call_id 上，才貼得回那張工具卡
    assert {s["call_id"] for s in steps} == {"tc-1"}


def test_extract_runs_only_to_n3_and_reports_that_this_run_has_no_draft():
    calls: list = []
    events: list = []
    fake = _FakePipeline(["n1", "n2", "n3"], sections={"screen": {"x": 1}})
    out = _tools(calls, events=events, run_pipeline=fake).extract_case_document()
    assert fake.calls[0]["to_node"] == "n3", "解析卷證只跑到程序審查"
    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == "ok"
    assert result["run_id"] == "run-new-1"
    assert result["state"] == "SCREENED"
    assert "沒有草稿" in out, "回給模型的話要講明這個 run 沒有草稿，否則它會去描述一份不存在的草稿"


def test_only_the_two_pipeline_tools_emit_tool_steps():
    """契約 v2 §2.3 ③：其餘五支沒有內部階段可報，發了就是編。"""
    calls: list = []
    for invoke in (
        lambda t: t.retrieve_refs("x"),
        lambda t: t.search_similar_decisions("x"),
        lambda t: t.read_case("intake"),
    ):
        events: list = []
        invoke(_tools(calls, _FakeRetriever([_hit()]), events=events))
        assert not [n for n, _ in events if n == "tool_step"], "這支工具不該發 tool_step"


def test_pipeline_tools_say_the_tier_is_unavailable_instead_of_failing_silently():
    """乙案容器裡沒有六節點流水線。**明說不可用**，不要靜默失敗成一段空回答。"""
    calls: list = []
    for name, invoke in (("extract_case_document", lambda t: t.extract_case_document()),
                         ("generate_decision_draft", lambda t: t.generate_decision_draft())):
        events: list = []
        out = invoke(_tools(calls, events=events, run_pipeline=None))
        result = [d for n, d in events if n == "tool_result"][0]
        assert result["status"] == "failed", name
        assert "不可用" in out or "跑不了" in out, out


def test_a_pipeline_blowup_is_reported_as_failed_not_as_an_invented_answer():
    calls: list = []
    events: list = []
    fake = _FakePipeline(["n1"], boom=RuntimeError("N1 炸了"))
    out = _tools(calls, events=events, run_pipeline=fake).extract_case_document()
    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == "failed"
    assert "N1 炸了" in result["note"]
    assert "不要編造" in out


# ── 契約 v2 §3.5.1：生成草稿的前置條件（後端也要擋）──────────────────

def test_draft_is_refused_when_the_case_has_not_been_extracted():
    """前置 2。**不要自己先跑 n1**——那會讓使用者以為草稿是憑空生出來的。"""
    calls: list = []
    events: list = []
    fake = _FakePipeline(["n4", "n5", "n6"], state="VERIFIED")
    t = _tools(calls, events=events, run_pipeline=fake, run_id=None,
               case_manifest=_MANIFEST_OK)
    out = t.generate_decision_draft()
    assert not fake.calls, "前置沒過就不該真的去跑流水線"
    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == "failed"
    assert "還沒有解析過卷證" in result["note"]
    assert "先解析卷證" in out


def test_draft_is_refused_when_no_laws_or_references_were_picked():
    """前置 3。上游全空時 N5 照樣生得出草稿，但每一個引用都會被清掉並判紅——
    產出一份通篇沒有依據的草稿，而**那個失敗看起來很像成功**。所以擋在生成之前。
    """
    calls: list = []
    for manifest in ({}, {"laws": [{"id": "L1", "t": "x"}], "references": []},
                     {"laws": [], "references": [{"id": "C1", "t": "y"}]}):
        events: list = []
        fake = _FakePipeline(["n4", "n5", "n6"], state="VERIFIED")
        t = _tools(calls, events=events, run_pipeline=fake, run_id="run-old",
                   payload={"screen": {"x": 1}}, case_manifest=manifest)
        t.generate_decision_draft()
        assert not fake.calls, f"manifest={manifest} 前置沒過卻跑了流水線"
        result = [d for n, d in events if n == "tool_result"][0]
        assert result["status"] == "failed"
        assert "還沒有查過法規與相似案例" in result["note"]


def test_draft_resumes_from_n4_and_feeds_the_picked_laws_back_as_a_query_term():
    """契約 v2 §3.5.2：手動挑的法規當**查詢詞**餵回 N4（`overrides.n4_query`）。

    `n4_query` 是單一字串不是陣列，多條法規要自己 join——傳成 list 的話
    `RunIn` 那邊是 `extra="forbid"`，而這裡直接進 `run_case`，錯了不會當場炸，
    只會讓查詢句變成一個 repr。
    """
    calls: list = []
    events: list = []
    fake = _FakePipeline(["n4", "n5", "n6"], state="VERIFIED",
                         sections={"screen": {"x": 1}, "laws": []})
    manifest = {"laws": [{"id": "L1", "t": "廢棄物清理法第 2 條"},
                         {"id": "L2", "t": "訴願法第 14 條"}],
                "references": [{"id": "C1", "t": "112 年訴字第 1 號"}]}
    t = _tools(calls, events=events, run_pipeline=fake, run_id="run-old",
               payload={"screen": {"x": 1}}, case_manifest=manifest)
    out = t.generate_decision_draft()
    call = fake.calls[0]
    assert call["from_node"] == "n4" and call["to_node"] is None
    assert call["base_run_id"] == "run-old", "要接在已解析的那個 run 上，不是重跑 n1"
    assert call["overrides"] == {"n4_query": "廢棄物清理法第 2 條；訴願法第 14 條"}
    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == "ok"
    assert result["state"] == "VERIFIED"
    assert result["cite_count"] == 7, "引用數是真值，不是設計稿寫死的那個 14"
    assert "已生成草稿" in out


# ── 契約 v2 §0.1 第 2 點：RefBook 的卷內編號要重置 ───────────────────

def test_pipeline_tools_reset_the_case_ref_numbering_so_L1_means_the_new_run():
    """**誠實層被靜默打穿的那條路徑。**

    N4 每次都從 `L1` 重編，而 `add_case_refs` 對既有 id 直接 `continue`。
    同一回合先 `read_case("laws")` 再跑一次流水線，不重置的話新 run 的 `L1`
    登記不進去——模型引新草稿的 `[L1]`，`refs[]` 卻帶出舊 run 的法條。
    **號對得上、內容是別人的**，而且沒有任何一個燈會亮。
    """
    calls: list = []
    old_laws = [{"id": "L1", "t": "舊 run 的法條"}]
    new_laws = [{"id": "L1", "t": "新 run 的法條"}]
    fake = _FakePipeline(["n1", "n2", "n3"],
                         sections={"screen": {"x": 1}, "laws": new_laws})
    t = _tools(calls, payload={"laws": old_laws}, run_pipeline=fake)

    t.read_case("laws")
    assert t.refbook.get("L1")["t"] == "舊 run 的法條"

    t.extract_case_document()
    t.read_case("laws")
    assert t.refbook.get("L1")["t"] == "新 run 的法條", \
        "跑完流水線之後 L1 還指著舊 run——誠實層被靜默打穿了"


def test_resetting_case_refs_leaves_this_turn_s_retrieval_numbers_alone():
    """只清 `origin == "record"`。`cN` 是本回合聊天檢索配的號，模型可能已經引用過，
    一起清掉會讓那些引用變成 `dropped_refs` → 無辜紅燈。
    """
    rb = RefBook()
    rb.add(_hit("kb-1", "甲"))
    rb.add_case_refs([{"id": "L1", "t": "法條"}, {"id": "C3", "t": "案例"}])
    assert sorted(rb.whitelist()) == ["C3", "L1", "c1"]

    stale = rb.reset_case_refs()
    assert sorted(stale) == ["C3", "L1"]
    assert sorted(rb.whitelist()) == ["c1"]
    assert rb.get("c1")["t"] == "甲"
    # 計數器不倒退：下一筆檢索要接 c2，不是重來一次 c1
    assert rb.add(_hit("kb-2", "乙"))["id"] == "c2"


# ── 契約 v2 §0.1 第 3 點：gen() 必須是真串流 ────────────────────────
#
# `backend/api/chat.py` import fastapi，而測試路徑零外部依賴（run_all.py 有靜態掃描），
# 所以這裡只驗得了**結構**。行為證據靠實跑：抵達時間記在
# `.prospec/changes/chat-tools-unified/verification.md`（改動前六個事件全在 2.993s
# 一起到，改動後 0.000／0.000／0.999／2.002／3.007／3.008）。

def _gen_fn() -> ast.FunctionDef:
    tree = ast.parse(_api_chat_src())
    chat_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "chat")
    return next(n for n in ast.walk(chat_fn)
                if isinstance(n, ast.FunctionDef) and n.name == "gen")


def test_gen_does_not_buffer_events_into_a_list_before_yielding():
    """**這條釘的是那個 2.02 秒。**

    原版 `emit` 是 `pending.append(...)`，跑完才整批 yield。只要 `emit` 的函式體裡
    出現 `.append(`，就是又回到攢一批再送——那會讓 10–72 秒的工作變成全黑一段時間
    再一次跳出，畫面等於假裝剛才有過程。

    `emit` 必須把事件推進一個有阻塞語義的佇列（`put`），由 generator 那端取。
    """
    gen = _gen_fn()
    emit = next(n for n in ast.walk(gen)
                if isinstance(n, ast.FunctionDef) and n.name == "emit")
    attrs = [n.func.attr for n in ast.walk(emit)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert "append" not in attrs, "gen() 的 emit 又在攢清單了——串流會退回整批送"
    assert "put" in attrs, "gen() 的 emit 沒有推進佇列，generator 那端取不到東西"


def test_gen_runs_the_turn_on_a_worker_thread_so_the_generator_can_yield_meanwhile():
    """同步 generator 自己跑 `_run_turn` 就沒有機會 yield——必須有人代跑。"""
    src = ast.dump(_gen_fn())
    assert "Thread" in src, "gen() 沒有把回合丟到工作執行緒，yield 不出中間事件"
    assert "join" in src, "沒有 join：worker 還在寫 box 的時候就讀，done/error 可能讀到空"


def test_every_event_carries_turn_id_not_just_done():
    """契約 v2 §2.3：`turn_id` 是**所有事件**的共通欄位。

    原本只有 `done` 有，前端沒辦法把稍早的 `tool_call` 歸到同一回合；`error` 那條
    還明著填 `None`。所以 `turn_id` 要在事件框架（`record()`）這一層注入。
    """
    tree = ast.parse(_api_chat_src())
    rec = next(n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "record")
    keys = [k.value for n in ast.walk(rec) if isinstance(n, ast.Dict)
            for k in n.keys if isinstance(k, ast.Constant)]
    assert "turn_id" in keys and "seq" in keys, \
        "record() 沒有把 turn_id 注進每一個事件"


def test_ack_is_emitted_at_the_top_of_the_turn_with_the_session_id():
    """契約 v2 §2.3 ② 第三項：`session_id` 提前到 `ack`。

    斷在 `token` 中途就永遠拿不到 `done`，下一輪只能開新 session，前面講過的話全丟。
    """
    tree = ast.parse(_api_chat_src())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_run_turn")
    acks = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
            and getattr(n.func, "id", "") == "emit"
            and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "ack"]
    assert acks, "_run_turn 沒有發 ack"
    keys = [k.value for n in ast.walk(acks[0]) if isinstance(n, ast.Dict)
            for k in n.keys if isinstance(k, ast.Constant)]
    assert "session_id" in keys, "ack 沒有帶 session_id，斷線就接不回來"


def test_run_id_is_optional_and_an_empty_one_is_not_a_400():
    """契約 v2 §2.1 ③：新案子上傳完卷證、還沒有任何 run 的時候也要能開口。"""
    tree = ast.parse(_api_chat_src())
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "ChatIn")
    ann = next(n for n in cls.body
               if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "run_id")
    assert "None" in ast.unparse(ann.annotation), "run_id 仍是必填"
    assert ann.value is not None and ast.unparse(ann.value) == "None", "run_id 沒有預設值"
    validate = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_validate")
    assert "run_id" not in ast.unparse(validate), \
        "_validate 還在擋空 run_id——契約已改成選填"


def test_the_pipeline_callable_injected_into_the_chat_layer_is_an_adapter_not_run_case():
    """層級禁令的真正守法：注入的必須是**回傳 plain dict 的 adapter**。

    直接把 `run_case` 注進去的話，`backend/llm/chat.py` 雖然沒有 import 語句
    （AST 那條測試會綠），卻會拿到一個 `CaseState` 物件並讀它的屬性——
    **層級形式上守住、實質被穿**。這條檢查 `_pipeline_adapter` 真的存在，
    而且 `build_chat_agent` 收到的是它而不是 `run_case`。
    """
    tree = ast.parse(_api_chat_src())
    assert any(isinstance(n, ast.FunctionDef) and n.name == "_pipeline_adapter"
               for n in ast.walk(tree)), "找不到 _pipeline_adapter"
    build = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "build_chat_agent"]
    assert build, "找不到 build_chat_agent 的呼叫"
    injected = [kw for kw in build[0].keywords if kw.arg == "run_pipeline"]
    assert injected, "build_chat_agent 沒有收到 run_pipeline"
    expr = ast.unparse(injected[0].value)
    assert expr.startswith("_pipeline_adapter("), f"注入的不是 adapter，而是 {expr}"


def test_load_case_payload_reports_whether_that_run_has_a_draft():
    """契約 v2 §0.1 第 1 點：BUS 只分「跑完／還在跑／失敗」。

    `to_node="n3"` 之後「跑完」有兩種意思，分不出來的後果是
    `generate_decision_draft` 把一個沒解析過的案子當成解析過的。
    """
    tree = ast.parse(_api_chat_src())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_load_case_payload")
    src = ast.unparse(fn)
    assert "final_state" in src, "_load_case_payload 沒有看 final_state"
    assert "has_draft" in src, "_load_case_payload 沒有回報這個 run 有沒有草稿"
