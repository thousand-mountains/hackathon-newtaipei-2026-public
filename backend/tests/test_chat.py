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
import json
import pathlib
import re
import threading

from backend.config import settings
from backend.config.origin_registry import TIER_HUMAN, TIER_SOURCED, tier_of
import backend.llm.chat as chat_mod
from backend.orchestrator import chat_bridge
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
    """數字類問題一律紅燈；答案下了期間結論時才附 redirect。

    **這一則的答案 2026-09-13 換過**：原本是「大約還有 20 天。」，靠「數字＋期間量詞」
    那條分支觸發。那條分支已經被拿掉（實測 0 抓到真的、5 誤抓），所以現在用一個
    **真的下了期間結論**的答案——這正是拿掉之後的殘餘風險：純數字不帶結論詞的說法
    會漏。詳見 `backend/llm/chat.py` `_PERIOD_CONCLUSION` 上面那一大段。
    """
    v = classify_answer("還剩幾天可以提訴願？", "大約還有 20 天，尚未逾期。", RefBook())
    assert v.lamp == "r"
    assert v.origin == "human_required"
    assert v.redirect is not None
    assert v.redirect["endpoint"] == "/api/deadline"


def test_the_known_gap_is_a_bare_number_with_no_conclusion_word():
    """**已知缺口，刻意留著**：純數字、不帶任何結論詞的說法不會觸發 redirect。

    這條測試是把缺口**寫下來**，不是宣稱它安全。紅燈仍然亮（只看問題），
    所以最壞情況是「模型講了天數、紅燈亮了、答案照顯示」——比「正確答案被吃掉」溫和。

    27 則雲上實打的回答裡找不到任何一則長這樣（模型下期間結論時一定會用到
    逾期／屆滿／期限內），但 **27 則不是 2700 則**。哪天實際遇到，改的是這裡，
    而且要先讀 chat.py 那段說明——不要用特例補。
    """
    v = classify_answer("還剩幾天？", "大約還有 20 天。", RefBook())
    assert v.lamp == "r", "紅燈不受影響"
    assert v.redirect is None, "如果這裡變成非 None，代表有人把數字分支加回來了"


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
           run_pipeline=None, run_id=None, case_manifest=None, refbook=None,
           build_graph=None):
    """建一組 ChatTools，並把 `_throttle` 換成計數器（真的節流會讓測試睡好幾秒）。"""
    chat_mod._throttle = lambda: monkey_throttle.append(1)
    emit = (lambda name, data: events.append((name, data))) if events is not None else None
    return chat_mod.ChatTools(
        payload if payload is not None else {"intake": {"案由": "x"}},
        refbook if refbook is not None else RefBook(), retriever, snapshot, emit,
        run_pipeline=run_pipeline, run_id=run_id, case_manifest=case_manifest,
        build_graph=build_graph,
    )


class _FakePipeline:
    """假的 `run_pipeline` adapter：發節點事件、回一份新 run 的分區。

    **回 plain dict 不回 CaseState**——那正是真 adapter 的契約
    （`backend/api/chat.py:_pipeline_adapter`）。假貨長得跟真貨不一樣的話，
    這些測試綠了也不代表接得上。
    """

    def __init__(self, nodes: list[str], sections: dict | None = None,
                 state: str = "SCREENED", boom: Exception | None = None,
                 timings: dict | None = None,
                 degraded: list[dict] | None = None) -> None:
        self._nodes = nodes
        self._sections = sections or {}
        self._state = state
        self._boom = boom
        self.timings = timings or {n: (i + 1) * 1000 for i, n in enumerate(nodes)}
        # 真 adapter 2026-09-13 起帶 `degraded`（`chat_bridge.pipeline_adapter`）。
        # 假貨少一個鍵就等於替聊天層假設「這個鍵不會有」，那正是這批測試要擋的事。
        self._degraded = degraded or []
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
                "node_timings": dict(self.timings),
                "degraded": [dict(d) for d in self._degraded], "cite_count": 7,
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
        ("build_relation_graph", lambda t: t.build_relation_graph()),
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
    # 讀的是「這一輪檢索到的」那份——帶 `L1`／`C3` 編號的是它，
    # 卷宗清單（`laws`）的 id 是母庫 S3 key，不進白名單（2026-09-13 拆開）。
    t.read_case("retrieved_laws")
    v = classify_answer("本案引了哪些法條？", "依 [L1] 與 [L2]。", t.refbook)
    assert v.dropped_refs == [], "卷內 id 被誤判成捏造"
    assert v.lamp == "y", "卷內引用應該算有出處"
    assert [r["id"] for r in v.refs] == ["L1", "L2"]


def test_read_case_does_not_call_the_dossier_empty_when_the_screen_shows_two():
    """承辦人看到「相關法規 2」時，任何一個回答都不能說「卷內沒有法規」。

    2026-09-13 實際發生：Ci 用右欄的 `+` 加了兩條法規、兩件案例（走 REST），
    畫面清楚顯示，然後問「可以生成草稿了嗎」，得到「法規依據：卷內還沒有」。

    根因是「卷內」被用在兩個不同的東西上——畫面右欄的案件卷宗是 `manifest.json`，
    而 `read_case` 讀的是**上一次 run 的 payload**。他的 run 只跑到 n3，
    payload 的 `laws` 當然是空的。**從他的角度那句話就是假的。**

    連鎖後果不只是講錯一句：模型拿那份錯的去判斷前置條件，於是**根本沒有呼叫**
    `generate_decision_draft`——而那支自己的檢查用的是 manifest，會通過。
    """
    calls: list = []
    manifest = {"laws": [{"id": "kb/public/相關法規_全量/訴願法.txt", "t": "訴願法"}],
                "references": [{"id": "kb/public/…/1143101448_駁回.txt", "t": "1143101448_駁回"}]}
    # run 只跑到 n3：payload 的 laws／cases 是空的（N4 還沒跑）
    t = _tools(calls, payload={"screen": {"x": 1}}, case_manifest=manifest)

    for section, title in (("laws", "訴願法"), ("cases", "1143101448_駁回")):
        events: list = []
        t._emit = lambda n, d: events.append((n, d))
        out = t.read_case(section)
        result = [d for n, d in events if n == "tool_result"][-1]
        assert result["status"] == "ok", \
            f"卷宗裡有東西卻回 {result['status']}：{result['note']}"
        assert "是空的" not in result["note"], f"畫面上有兩筆，它說空的：{result['note']}"
        assert title in out, f"讀出來的不是承辦人挑的那筆：{out[:120]}"


def test_the_read_case_note_is_written_for_the_operator_not_the_model():
    """`note` 只進事件、**模型看不到**，所以它是一個純粹給承辦人看的欄位。

    2026-09-13 之前寫的卻是給模型看的話——分區鍵名（`laws`）、引用編號（`L1`）、
    另外兩個分區的英文名。那三樣都是紅線 5 要擋的識別字，只是躲在一個當時沒有被
    渲染的欄位裡（前端 `applyToolResult` 沒有 `read_case` 分支）。

    **沒有被渲染不等於可以寫錯**：前端補上分支的那一天它就會上畫面。
    """
    calls: list = []
    events: list = []
    t = _tools(calls, payload={"laws": [{"id": "L1", "t": "訴願法第 14 條"},
                                        {"id": "L2", "t": "行政程序法第 74 條"}]},
               case_manifest={"laws": [{"id": "kb/…/訴願法.txt", "t": "訴願法"}],
                              "references": []},
               events=events)
    for section in ("retrieved_laws", "laws", "intake"):
        events.clear()
        t.read_case(section)
        note = [d for n, d in events if n == "tool_result"][-1]["note"]
        for leaked in ("retrieved_laws", "retrieved_cases", "L1", "L2", "「laws」",
                       "「intake」", "**"):
            assert leaked not in note, f"note 漏出給模型看的東西（{section}）：{note!r}"
        assert note, f"{section} 的 note 是空的，卡片會只剩一個打勾"

    # 模型需要的兩件事沒有因此消失：編號在它拿到的 JSON 內容裡，白名單照樣註冊
    events.clear()
    body = t.read_case("retrieved_laws")
    assert "L1" in body and "L2" in body, "模型拿不到可引用的編號了"
    assert "L1" in t.refbook.whitelist(), "白名單沒註冊"


def test_the_graph_summary_counts_similar_cases_not_just_statutes():
    """關聯圖摘要只講法規不講相似案，相似案的件數在聊天裡**靜默消失**。

    `unlinked` 被拆成 `laws`／`cases` 兩鍵之後，這行只剩一半——圖與契約裡都還在，
    只是模型不會提。量詞要分：法規論「條」、相似訴願決定論「件」
    （與 `backend/graph/relation.py:392,394` 一致）。
    """
    calls: list = []
    events: list = []
    graph = {"nodes": [], "edges": [], "sentences": [],
             "unlinked": {"laws": ["L1", "L2", "L3"], "cases": ["C1", "C2"]},
             "stats": {"nodes": 0, "edges": 0}}
    t = _tools(calls, events=events, run_id="run-1",
               build_graph=(lambda *, run_id: graph))
    out = t.build_relation_graph()
    assert "3 條檢索到的法規" in out, out
    assert "2 件檢索到的相似訴願決定" in out, f"相似案的件數消失了：{out}"
    assert "2 條檢索到的相似" not in out, "相似訴願決定的量詞用錯了（應為「件」）"


def test_the_dossier_and_this_turn_s_retrieval_are_two_different_sections():
    """兩份都要讀得到，而且名字要說得出自己是哪一份。

    只改成讀 manifest（選項 a）的話，「這一輪檢索到什麼」就讀不到了——
    而那份資訊有人在用：右欄的三態標記（`matched`／`query_only`／`unused`）
    靠它比對，Ci 剛剛才實際看到那個標記在運作。
    """
    calls: list = []
    manifest = {"laws": [{"id": "kb/…/訴願法.txt", "t": "訴願法"}], "references": []}
    retrieved = [{"id": "L1", "t": "訴願法第 14 條"}]
    t = _tools(calls, payload={"laws": retrieved}, case_manifest=manifest)

    assert "訴願法第 14 條" in t.read_case("retrieved_laws"), "這一輪檢索到的讀不到了"
    assert "L1" in t.refbook.whitelist(), "檢索到的那份仍然要能被引用"

    # 卷宗清單那份**不**進白名單：它的 id 是母庫 S3 key，不是畫面上印得出來的編號
    t.read_case("laws")
    assert "kb/…/訴願法.txt" not in t.refbook.whitelist(), \
        "S3 key 進了白名單，模型會寫出 `[kb/public/…]` 這種沒人看得懂的引用"


def test_an_empty_section_says_whether_the_other_one_has_anything():
    """某份空的時候要講清楚另外那份有沒有東西。

    不講的話最糟的形狀會回來：卷宗是空的、這一輪檢索到 5 條，模型回一句
    「卷內沒有法規」，承辦人看著右欄的檢索結果一頭霧水。
    """
    calls: list = []
    events: list = []
    t = _tools(calls, payload={"laws": [{"id": "L1", "t": "訴願法第 14 條"}]},
               case_manifest={"laws": [], "references": []}, events=events)
    note = t.read_case("laws")
    assert "這一輪檢索到的法條" in note and "1 筆" in note, \
        f"卷宗空的時候沒說「檢索到的那份有東西」：{note}"
    assert "不要說成" in note, "沒有擋掉「卷內什麼都沒有」這種講法"
    # 這條路徑的 note 現在就會上畫面，英文鍵名一個都不能有
    assert "retrieved_laws" not in note and "laws" not in note, f"漏出鍵名：{note}"

    # 反過來：兩份都空的時候不要無中生有地說另一份有東西
    events2: list = []
    t2 = _tools(calls, payload={}, case_manifest={"laws": [], "references": []},
                events=events2)
    note2 = t2.read_case("laws")
    assert "這一輪檢索到的法條" not in note2, f"兩份都空卻說另一份有東西：{note2}"
    assert "加進本案卷宗" in note2, "沒告訴承辦人下一步怎麼做"


def test_case_refs_are_marked_as_record_not_retrieval():
    """卷內引用**不該看起來像 KB 命中**。每筆 ref 自己帶 origin，前端才分得出來。"""
    calls: list = []
    t = _tools(calls, payload={"cases": [{"id": "C1", "t": "某決定"}]})
    t.read_case("retrieved_cases")
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
    t.read_case("retrieved_laws")
    assert [d["hits"] for n, d in events if n == "tool_result"] == [[]]
    assert "L1" in t.refbook.whitelist(), "id 沒進白名單"


def test_case_ids_keep_their_own_numbers():
    """不重新編號：`L1`、`C3` 已經印在承辦人畫面的法條卡與相似案卡上，
    改號會讓聊天講的號跟畫面上的對不起來。"""
    calls: list = []
    t = _tools(calls, payload={"laws": [{"id": "L6", "t": "x"}]})
    t.read_case("retrieved_laws")
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


class _RecordingRetriever:
    """記下收到什麼 query。**查詢詞有沒有真的送到檢索層**是這批測試的核心。"""

    name = "similar_cases"

    def __init__(self, hits: list | None = None) -> None:
        self.queries: list[str] = []
        self._hits = hits if hits is not None else [_hit()]

    def search(self, query, top_k=5, filters=None, **kw):  # noqa: ANN001
        self.queries.append(query)
        return self._hits


#: 查表要查得到東西，歸檔測試才測得到東西。條號清單照 `laws-snapshot.json` 的形狀。
_SNAPSHOT_WITH_LAWS = {"laws": {"訴願法": {"articles": [str(i) for i in range(1, 102)], "max": 101}}}


def _hint_tools(calls: list, events: list, *, retriever=None, law_query="訴願法第77條",
                case_query="卷證事實原文", run_pipeline=None, run_id=None,
                payload=None, case_manifest=None):
    chat_mod._throttle = lambda: calls.append(1)
    return chat_mod.ChatTools(
        payload if payload is not None else {"screen": {"x": 1}}, RefBook(),
        retriever, _SNAPSHOT_WITH_LAWS, lambda n, d: events.append((n, d)),
        run_pipeline=run_pipeline, run_id=run_id, case_manifest=case_manifest,
        build_graph=(lambda *, run_id: {"nodes": [], "edges": []}),
        law_query=law_query, law_query_sources=["n2.classification.class.case_type"],
        case_query=case_query, case_query_sources=["n1.facts_excerpt[].text"],
    )


def test_two_tools_running_at_once_do_not_swap_their_call_ids():
    """同一個 model turn 的多支工具是**併發**的，`call_id` 不得互相蓋掉。

    雲上實測（QA `t17-callid2.sse`）：

        seq 1  tool_call   tc-1  search_similar_decisions
        seq 2  tool_call   tc-2  retrieve_refs
        seq 3  tool_result tc-2  retrieve_refs
        seq 4  tool_result tc-2  search_similar_decisions   ← 應該是 tc-1

    兩個 `tool_call` 都發完了才發第一個 `tool_result`——所以它們是重疊執行的。
    共享的 `_current_call_id` 被後進來的那支蓋掉，於是**tc-1 那張卡永遠轉圈**
    （前端依 `call_id` 開卡，`frontend/src/store/app.js:539`），
    而 tc-2 那張卡的標題與內容不是同一件事。

    這條用真的執行緒重現那個交錯：兩支工具都在對方的 `_call()` 與 `_result()`
    之間卡住，單一共享欄位一定配錯。
    """
    events: list = []
    lock = threading.Lock()
    started = threading.Barrier(2)
    calls: list = []
    tools = _tools(calls, _FakeRetriever([_hit()]),
                   events=events, payload={"screen": {"x": 1}})
    tools._emit = lambda n, d: (lock.acquire(), events.append((n, d)), lock.release())[0]

    def run(section):
        # 兩支都先各自 _call() 完，才讓任何一支去 _result()——正是 t17 的形狀
        tools._call("read_case", {"section": section})
        started.wait(timeout=5)
        tools._result("read_case", [], f"讀了 {section}")

    threads = [threading.Thread(target=run, args=(s,)) for s in ("intake", "screen")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    call_ids = [d["call_id"] for n, d in events if n == "tool_call"]
    result_ids = [d["call_id"] for n, d in events if n == "tool_result"]
    assert sorted(call_ids) == ["tc-1", "tc-2"], f"配號重複了：{call_ids}"
    assert sorted(result_ids) == ["tc-1", "tc-2"], \
        f"兩個 result 掛在同一個 call_id 上，前端會有一張卡永遠轉圈：{result_ids}"


def test_the_lamp_reason_says_which_kind_of_score_the_hits_carried():
    """燈號說明不得一律講「向量相似度」（契約 §3.2 列為不實陳述）。

    雲上實測兩頭都錯（QA `t02`／`t04`）：法條查表的二元命中講向量相似度、
    重排過的命中也講向量相似度。`hits[].ranked_by` 今晚修好了，這一句漏掉。
    """
    rb = RefBook()
    rb.add(_lawtable_hit(), score_kind=None)          # ranked_by=None：查表二元命中
    v = classify_answer("問", "依 [c1]。", rb)
    assert "二元命中" in v.why, f"查表命中被講成相似度：{v.why}"
    assert "向量相似度" not in v.why, v.why

    rb2 = RefBook()
    rb2.add(_hit())                                   # ranked_by=embedding
    v2 = classify_answer("問", "依 [c1]。", rb2)
    assert "向量相似度" in v2.why, v2.why
    assert "重排" not in v2.why, v2.why

    # 混著引用時兩種都要講出來，不得只挑一種
    rb3 = RefBook()
    rb3.add(_lawtable_hit(), score_kind=None)
    rb3.add(_hit("kb-9", "某決定"))
    v3 = classify_answer("問", "依 [c1] 與 [c2]。", rb3)
    for kind in ("二元命中", "向量相似度"):
        assert kind in v3.why, f"混合命中漏講 {kind}：{v3.why}"


#: 一份**真的** Strands callback payload 的形狀，照本機安裝的
#: `strands.handlers.callback_handler.PrintingCallbackHandler.__call__` 的宣告抄：
#: `reasoningText`（推理文字）／`data`（文字內容）／`complete`（是不是最後一塊）／
#: `event`（ModelStreamChunkEvent）。
_REASONING_CHUNK = {
    "reasoningText": "使用者問的是期限，我應該先讀卷再決定要不要拒答……",
    "event": {"contentBlockDelta": {"delta": {"reasoningContent": {"text": "…"}}}},
}
_TEXT_CHUNK = {"data": "依卷內資料，", "complete": False,
               "event": {"contentBlockDelta": {"delta": {"text": "依卷內資料，"}}}}


def test_the_model_text_is_streamed_as_token_events():
    """`token` 在契約 §2.3 的事件表裡，但後端從來沒發過一筆（QA 掃 r01–r10，共 0 筆）。

    `callback_handler=None` ＋ 一次阻塞呼叫 ＝ 模型的文字沒有中途出口。
    畫面不會壞（前端 `:548` 會用 `done.answer` 補一則泡泡），差別是
    **一輪 10–72 秒完全沒有聲音**。
    """
    events: list = []
    cb = chat_mod.token_callback_handler(lambda n, d: events.append((n, d)))
    cb(**_TEXT_CHUNK)
    cb(data="第 14 條規定……", complete=True)
    assert [d["text"] for n, d in events if n == "token"] == ["依卷內資料，", "第 14 條規定……"]


def test_the_reasoning_text_never_reaches_the_operator():
    """`reasoningText` 是模型的推理過程，**一個字都不准出去**。

    承辦人看到它會以為那是系統的判斷理由，而它不是——那會變成「把內部的東西端到
    承辦人面前」的又一個實例。

    **這條餵的是一份真的含 reasoning 的 callback payload**，不是斷言程式碼裡有那個
    `if`：白名單寫錯、或哪天 Strands 把推理也塞進 `data`，只看程式碼是看不出來的。
    """
    events: list = []
    cb = chat_mod.token_callback_handler(lambda n, d: events.append((n, d)))
    cb(**_REASONING_CHUNK)
    assert events == [], f"推理文字漏出去了：{events}"

    # 同一塊同時帶 reasoning 與 data 時，只出 data
    events.clear()
    cb(reasoningText="我在想……", data="所以本案", complete=False)
    assert [d["text"] for n, d in events if n == "token"] == ["所以本案"]
    assert "我在想" not in json.dumps(events, ensure_ascii=False)


def test_an_empty_chunk_does_not_open_an_empty_bubble():
    """空的 `data` 不發事件——前端收到第一筆 token 就 push 一則泡泡（`:484`）。"""
    events: list = []
    cb = chat_mod.token_callback_handler(lambda n, d: events.append((n, d)))
    cb(data="")
    cb(complete=True)
    cb(event={"messageStop": {}})
    assert events == [], f"發了空的 token：{events}"


def test_refine_text_stays_silent():
    """`refine_text` 自己另建一個 agent，**那支不得串流**。

    它的輸出是「改寫後的文字」，走 `tool_result` 回給模型，不是這一輪的回答。
    串流出去會讓改寫稿逐字打在對話框裡，看起來像 agent 在回話。
    """
    src = pathlib.Path(chat_mod.__file__).read_text(encoding="utf-8")
    refine = src[src.index("def refine_text"):]
    refine = refine[:refine.index("\n    def ", 10)]
    assert "callback_handler=None" in refine, "refine_text 的 agent 接上了串流"
    assert "token_callback_handler" not in refine, "refine_text 的改寫稿會被逐字打出來"


def test_no_emit_means_no_callback_at_all():
    """`?stream=0` 與直呼路徑沒有 `emit`，那時不該硬塞一個 callback 進 Agent。"""
    assert chat_mod.token_callback_handler(None) is None


def test_redirect_does_not_eat_an_answer_that_has_nothing_to_suppress():
    """`redirect` 非 null 會讓前端把整則答案丟掉，所以它不能只看問題的關鍵字。

    QA `r10` 實測：承辦人問「訴願人是誰、處分日期是哪一天」（含關鍵字「日期」），
    模型正確回「收文欄位目前是空的，所以我沒辦法告訴你」——**一個數字都沒算**。
    而 `frontend/src/store/app.js:563` 拿到 `redirect` 就丟掉 `answer`、只顯示
    reason 與 CTA（照契約 §2.4.1 一做的）。承辦人看到的是「期間計算由規則引擎負責」
    加一個「查看程序審查」，**而他真正需要的那句「欄位是空的，請先補」被吃掉了。**

    紅燈維持（誤判＝多一次覆核，便宜）；`redirect` 改成看答案（誤判＝拿不到答案）。
    """
    v = classify_answer("訴願人是誰、處分日期是哪一天？",
                        "收文欄位目前是空的，所以我沒辦法告訴你訴願人是誰。", None)
    assert v.lamp == "r", "紅燈不該被放寬"
    assert v.redirect is None, "答案裡沒有要擋的東西，卻把整則吃掉了"


def test_redirect_still_fires_when_the_answer_really_does_state_a_number():
    """**這一格是上一條的對照組，比其他任何一格重要。**

    改成看答案之後最容易變成破口的就是這裡：答案真的講了天數／期間結論時，
    `redirect` 必須照樣觸發、照樣被擋。§2.4.1 一要擋的是「不得顯示 agent 講的
    任何天數」——改成看答案是**更忠實地**執行同一條紅線，不是放寬它。
    """
    for answer in ("從送達日起算 30 天，到 6 月 13 日屆滿。",
                   "這件已經逾期了。",
                   "還來得及，期間內。",
                   "提起訴願逾法定期間，應為不受理之決定。"):
        v = classify_answer("期限怎麼算？", answer, None)
        assert v.lamp == "r", answer
        assert v.redirect is not None, f"答案講了要擋的東西卻沒攔：{answer}"


#: **QA 雲上實打的原文**（`scratchpad/qa/chat/*.sse` 的 `done.answer`，逐字抄）。
#: 這幾則不是編出來的例子——2026-09-13 的判準改動就是被它們推翻與定案的。
#: 存檔在 scratchpad 裡會隨 session 消失，所以把關鍵的幾則留在測試裡。
_R10_ANSWER = ("收文欄位目前是空的，所以我沒辦法告訴你訴願人是誰、處分日期是哪一天。\n\n"
               "這些基本資料需要先補進收文欄位，案子才能往下處理。\n")
#: 五則誤抓的實際觸發片段，各自是一種完全不同的東西。
_FALSE_POSITIVES = {
    "r10 疑問詞「哪一天」": _R10_ANSWER,
    "t01 收文欄位的記載值": "| 在途期間 | 0 天 |\n| 送達方式 | 寄存送達 |",
    "t04 函釋字號裡的民國年": "另可參環保署 98 年函釋，該案認定露天燃燒屬廢棄物清理法規範。",
    "t05 判解字號裡的民國年": "行政法院 92 年度判字第 123 號判決可供參考。",
    "t09 同 r10（另一份存檔）": _R10_ANSWER,
}
#: 三則真陽性：人工標註「真的下了期間結論」的那幾則裡的關鍵句。
_TRUE_POSITIVES = {
    "r05 逐步算式＋結論": "3. 法定期間 30 日，屆滿日為 114/4/10\n4. 訴願人於 114/4/2 提起訴願 → **未逾期**",
    "t10 結論列": "- **結論**：✅ 未逾期",
    "t15 期限內": "- 實際提起日：114/4/2 → 在期限內",
}


def test_the_exact_r10_answer_is_no_longer_eaten():
    """**驗收第一格：把原始那一則餵進去。**

    2026-09-13 的教訓：上一輪我用自己編的問題與**截短的答案**驗這條修正，得出
    「修好了」——而 `r10` 的完整原文其實還是會被攔（「處分日期是哪一天」的
    「一天」＝中文數字＋期間量詞）。**手邊就有那則原文，卻沒拿它去跑。**

    這種「針對某一則真實回答」的修正，驗收第一格一律是原文，等價情境排在後面。
    """
    assert not chat_mod.answer_states_a_period_conclusion(_R10_ANSWER)
    v = classify_answer("訴願人是誰、處分日期是哪一天？", _R10_ANSWER, None)
    assert v.lamp == "r", "紅燈不受影響（問題含「日期」）"
    assert v.redirect is None, "r10 的原文還是被吃掉了——這條修正就是為它寫的"


def test_none_of_the_five_real_false_positives_fire_any_more():
    """五則誤抓逐一驗。它們分別是疑問詞、記載欄位值、兩種字號裡的民國年。

    **不要用特例把它們一一排除**：那是三件互不相關的事，堆成三條例外規則之後，
    沒有人有辦法推理那個判準下一次會怎麼判。拿掉整條數字分支才是對的收法。
    """
    for label, answer in _FALSE_POSITIVES.items():
        assert not chat_mod.answer_states_a_period_conclusion(answer), \
            f"{label} 仍然被當成期間結論：{answer[:60]!r}"


def test_all_three_real_period_conclusions_still_fire():
    """三則真陽性逐一驗——拿掉數字分支不得讓真的期間結論漏掉。

    這三則各自命中不同的詞（屆滿／未逾期／期限內），冗餘度高，
    這正是「結論詞那條分支 3/3 抓到」的原因。
    """
    for label, answer in _TRUE_POSITIVES.items():
        assert chat_mod.answer_states_a_period_conclusion(answer), \
            f"{label} 漏掉了：{answer[:60]!r}"


def test_money_goes_red_but_does_not_get_the_deadline_redirect():
    """金額只紅燈、不 redirect（2026-09-13 Ci 拍板走 (c)）。

    `redirect` 的語意是「別看模型講的，去看規則引擎算的」，而**罰鍰沒有規則引擎**。
    它指向 `/api/deadline`——對一個罰鍰問題不是誤判的程度問題，是**目的地根本
    不存在**，而前端會為此把整則答案丟掉。

    實測（本機接雲上 Bedrock）：問「訴願人是誰、處分日期是哪一天」，答案裡的
    「裁處罰鍰 6 萬元」就足以讓整則答案被吃掉——而那筆罰鍰是卷內記載的。
    """
    for answer in ("裁處罰鍰 6 萬元。", "罰鍰 6000 元。", "減輕百分之 20。"):
        v = classify_answer("罰鍰多少錢？", answer, None)
        assert v.lamp == "r", f"金額仍然要紅燈：{answer}"
        assert v.redirect is None, f"金額被導去期間計算端點了：{answer}"
        assert "期間計算由規則引擎負責" not in v.why, \
            f"非期間問題卻講期間計算，是非所問：{v.why}"

    # 但**金額句裡若同時有期間結論**，照樣攔——不得因為有「元」就整句放行
    v = classify_answer("罰鍰多少錢？", "罰鍰 6 萬元，而且這件已經逾期了。", None)
    assert v.redirect is not None, "金額句裡的期間結論被放過了"


def test_section_77_clauses_are_not_eaten_just_for_saying_not_admissible():
    """`不受理` 不再單獨觸發——§77 八款只有第 2 款關於期間。

    其餘各款（非行政處分、無代理權、訴願書不合法定程式…）都會導向不受理但跟期間無關。
    留著它，承辦人問「§77 有哪幾款」的正常回答會被整則吃掉。

    拿掉不會漏掉真的期間結論：系統自己的結論句都含「逾／期滿／屆滿」
    （`n3_procedure.py:56,76`、`settings.py:712,738`，逐句查證過）。
    """
    v = classify_answer("訴願法第 77 條有哪幾款？",
                        "共八款，包括非行政處分、無代理權等，均應為不受理之決定。", None)
    assert v.redirect is None, "講到不受理就被當成期間結論了"

    # 真的期間結論仍然攔得到——它一定帶著「逾」
    v2 = classify_answer("這個案子怎麼樣？",
                         "提起訴願逾法定期間，應為不受理之決定。", None)
    assert v2.redirect is not None, "真的期間結論漏掉了"


def test_a_period_conclusion_is_caught_even_when_the_question_never_asked():
    """問題沒問期限、但**模型自己下了期間結論**——以前完全不受攔。

    規則 1 只看問題，而 §2.4.1 一的紅線是關於答案內容的。
    「這個案子怎麼樣？」→「距離期滿還有幾天」就這樣穿過去。
    """
    v = classify_answer("這個案子怎麼樣？", "這件已經逾期，應為不受理。", None)
    assert v.lamp == "r", "模型自己講了期間結論卻沒判紅"
    assert v.redirect is not None, "紅線破口：答案裡的期間結論沒有被攔"


def test_quoting_a_date_recorded_in_the_file_is_not_a_calculation():
    """**只認期間結論詞、不認裸數字**——否則會反過來咬掉正常的回答。

    「原處分日是哪一天？」→「113 年 6 月 11 日」有數字有量詞，但那是**卷內記載的
    日期**，不是模型算出來的期間。把它也擋掉，承辦人問一個記在卷裡的日期會拿到
    一句「期間計算由規則引擎負責」——那比原本的毛病更糟。
    """
    v = classify_answer("原處分日是哪一天？", "卷內記載的原處分日是 113 年 6 月 11 日。", None)
    assert v.redirect is None, "把卷內記載的日期當成模型算出來的期間擋掉了"


def test_the_numeric_reason_describes_the_rule_not_this_turn():
    """`why` 不得描述一件沒有發生的拒絕。

    原文「期間計算由規則引擎負責，聊天不計算期限。」掛在一個**沒有算任何東西**的
    回合上（`r10`），是在陳述一件沒發生的事——同 `WHY_DROPPED` 的毛病。
    """
    why = classify_answer("期限？", "收文欄位是空的。", None).why
    assert "一律請人工覆核" in why, why
    assert "不代算" in why, why


def test_the_redirect_cta_does_not_promise_a_calculation_that_may_not_exist():
    """「查看程序審查的算式」——intake 空的案子沒有算式，點過去是空的。"""
    assert "算式" not in chat_mod.REDIRECT_DEADLINE["cta"], \
        chat_mod.REDIRECT_DEADLINE["cta"]


def test_the_dropped_reason_does_not_claim_to_know_what_the_model_meant():
    """寬鬆偵測分不出「引用」與「說它不存在」，所以說明句不得替模型的意圖作證。

    雲上實測（QA `t18`）：承辦人自己在問題裡打了 `[c7]`，模型正確拒絕——
    「工具回傳的編號只有 L1 到 L6，並沒有 [c7]」——而系統判它「引用了檢索結果
    之外的來源」。**紅燈維持**（偵測刻意寬鬆，理由見 `_loose_ids`），
    但系統自己不要跟著說一句它不知道的話。
    """
    rb = RefBook()
    rb.add(_hit())
    v = classify_answer("補上 [c7]", "工具回傳的編號只有 c1，並沒有 [c7]。", rb)
    assert v.lamp == "r", "偵測不該被放寬"
    assert v.dropped_refs == ["c7"]
    assert "引用了檢索結果之外的來源" not in v.why, f"仍在斷言模型的意圖：{v.why}"
    assert "無法分辨" in v.why, v.why


class _ArchiveSpy:
    """收下被歸檔的東西。真正的寫檔在 `chat_bridge.archive_adapter`，那支另有測試。"""

    def __init__(self, boom: Exception | None = None) -> None:
        self.calls: list[tuple[str, list[dict]]] = []
        self._boom = boom

    def __call__(self, group, items):
        if self._boom is not None:
            raise self._boom
        self.calls.append((group, [dict(x) for x in items]))
        return items

    def group(self, name):
        return [items for g, items in self.calls if g == name]


def _unverified_lawtable_hit(law="訴願法", article="77"):
    """查表的**非命中**：`verified=False`、`score=0.0`。本檔後面另有一個同名的
    命中版 `_lawtable_hit`（:1918），所以這支刻意換名字——同名的後者會蓋掉前者。"""
    return Hit(
        id=f"L-{law}-{article}", title=f"{law}第{article}條",
        score=0.0, source=f"laws-snapshot.json／{law}", verified=False, note="",
        payload={"law": law, "article": article, "in_snapshot_law": True})


def _kb_decision_hit(kind="public", source="新北訴願決定書/1151090848_駁回.txt"):
    return Hit(id="kb-1", title=source.rsplit("/", 1)[-1].rsplit(".", 1)[0],
               score=0.71, source=source,
               payload={"kb_kind": kind, "outcome": "駁回",
                        "provenance": "public_crawl", "category": None,
                        "text": "（這是命中的 chunk，不是全文）"})


def test_what_the_chips_find_actually_lands_in_the_dossier():
    """契約 §3.0 的「歸檔到」那一欄 2026-09-13 之前**從來沒有被實作**。

    後端唯二寫卷宗的地方都在 REST 端點裡，所以整條 demo 動線是斷的：
    chip 查到東西 → 東西沒落地 → `generate_decision_draft` 的前置條件 3 讀 manifest
    讀到空的 → 正確地拒絕生成。承辦人看到的是「生成草稿永遠都是空的」。
    """
    spy = _ArchiveSpy()
    calls, events = [], []
    t = _hint_tools(calls, events, retriever=_RecordingRetriever([_kb_decision_hit()]))
    t.archive = spy
    t.run_tool_hint("search_similar_decisions")
    t.run_tool_hint("search_regulations")

    assert spy.group("references"), "相似案查到了卻沒有進卷宗"
    assert spy.group("laws"), "法條查到了卻沒有進卷宗"


def test_archived_ids_are_stable_not_the_per_turn_refbook_numbers():
    """`id` 不得是 `c1`／`c2`——那是 RefBook **每回合重配**的序號。

    存進 manifest 的話下一輪就對不上，同一條法規會被當成新的一筆重複加入，
    右欄會越按越長。這條是硬條件。
    """
    spy = _ArchiveSpy()
    calls, events = [], []
    t = _hint_tools(calls, events, retriever=_RecordingRetriever([_kb_decision_hit()]))
    t.archive = spy
    t.run_tool_hint("search_regulations")
    t.run_tool_hint("search_similar_decisions")

    for group, items in spy.calls:
        for item in items:
            assert not re.fullmatch(r"c\d+", str(item["id"])), \
                f"{group} 用了 RefBook 的回合序號當 id：{item['id']}"
    # 法條：法名＋條號推導，同一條每次都一樣
    law_ids = [i["id"] for items in spy.group("laws") for i in items]
    assert law_ids == ["lawtable:訴願法-77"], law_ids
    # 相似案：就是 S3 key，跟 REST 那條路存的同一個值（同一份文件只會有一筆）
    ref_ids = [i["id"] for items in spy.group("references") for i in items]
    assert ref_ids == ["kb/public/新北訴願決定書/1151090848_駁回.txt"], ref_ids


def test_the_two_law_channels_are_told_apart_in_the_dossier():
    """條號查表與母庫全文**不是同一種東西**，卷宗裡要分得出來（契約 §4.2）。

    `laws-snapshot.json` 只有條號清單、沒有條文原文，所以查表來的那筆
    `body_cached` 必然是空的——**`note` 要說清楚它是什麼等級的東西**，
    假裝有全文比沒有更糟。
    """
    spy = _ArchiveSpy()
    calls, events = [], []
    t = _hint_tools(calls, events, retriever=_RecordingRetriever([]))
    t.archive = spy
    t.run_tool_hint("search_regulations")

    item = spy.group("laws")[0][0]
    assert item["channel"] == chat_mod.ARCHIVE_CHANNEL_LAWTABLE
    assert item["body_cached"] == "", "快照裡沒有條文原文，不得憑空生一份"
    assert "非法條全文" in item["note"], f"沒說清楚它是什麼等級的東西：{item['note']}"
    assert item["retrieval_status"] == chat_mod.PICK_FOUND_BY_TOOL, \
        "用了一個在寫的那一刻不成立的狀態值"


def test_a_hit_the_lawtable_could_not_verify_is_not_filed_as_a_legal_basis():
    """查表的非命中不得歸檔。

    它會回兩種非命中：條號寫法無法無歧義解析、法規不在快照涵蓋範圍內——
    兩種的 `score` 都是 0、`note` 都在說「本系統驗不了」。
    把它們當成「相關法規」歸檔，等於把一句「我不知道」畫成一筆依據。
    """
    assert chat_mod.ChatTools._law_items([_unverified_lawtable_hit()]) == []
    assert chat_mod.ChatTools._law_items(
        [Hit(id="L-訴願法-?", title="訴願法第？條", score=0.0, source="無法解析",
             verified=False, payload={"law": "訴願法", "article": None})]) == []


def test_a_decision_whose_s3_key_cannot_be_rebuilt_is_not_filed():
    """拿不到 `kb_kind` 就不歸檔，**不猜前綴**。

    `Hit.source` 是 `kb/<kind>/` 底下的相對路徑，`kind` 原本在 Hit 上留不下來。
    猜（先試 official 再試 public）在猜錯時會讀到另一份文件，而那個錯看起來完全正常。
    """
    no_kind = Hit(id="kb-1", title="x", score=0.5, source="某目錄/某檔.txt", payload={})
    assert chat_mod.ChatTools._reference_items([no_kind]) == []
    unknown = Hit(id="kb-1", title="x", score=0.5, source="某檔.txt",
                  payload={"kb_kind": "unknown"})
    assert chat_mod.ChatTools._reference_items([unknown]) == []


def test_the_chunk_text_is_never_passed_off_as_the_full_document():
    """`payload["text"]` 是**這一次命中的 chunk**，不是全文。

    拿它塞 `full_cached` 就是假資料：右欄會出現一份看起來完整、其實只有一段的決定書。
    全文由 `archive_adapter` 那一側決定要不要去 S3 抓。
    """
    item = chat_mod.ChatTools._reference_items([_kb_decision_hit()])[0]
    assert item["full_cached"] == "", "把 chunk 當成全文存進去了"
    assert "（這是命中的 chunk，不是全文）" not in json.dumps(item, ensure_ascii=False)


def test_archiving_failure_does_not_turn_a_good_search_into_a_failed_one():
    """歸檔是**副作用**，不是這次查詢的結果。

    寫檔失敗不該讓一次成功的檢索變成「查詢失敗」——那會把使用者的注意力導到錯的地方。
    """
    calls, events = [], []
    t = _hint_tools(calls, events, retriever=_RecordingRetriever([_kb_decision_hit()]))
    t.archive = _ArchiveSpy(boom=RuntimeError("磁碟滿了"))
    t.run_tool_hint("search_similar_decisions")

    result = [d for n, d in events if n == "tool_result"][-1]
    assert result["status"] == "ok", f"歸檔失敗把檢索結果吃掉了：{result}"
    assert result["hits"], "hits 也被吃掉了"


def test_a_tier_with_no_dossier_just_does_not_archive():
    """乙案容器裡沒有卷宗目錄，`archive` 是 `None`——檢索照跑，只是不歸檔。

    那是真的沒有地方可寫，不是靜默失敗。
    """
    calls, events = [], []
    t = _hint_tools(calls, events, retriever=_RecordingRetriever([_kb_decision_hit()]))
    assert t.archive is None
    t.run_tool_hint("search_similar_decisions")
    assert [d for n, d in events if n == "tool_result"][-1]["status"] == "ok"


def test_a_tool_chip_runs_the_tool_it_is_named_after():
    """chip 按下去要做它寫的那件事（2026-09-13 Ci 在畫面上抓到）。

    實際發生的：按「/查找相似案例」，畫面上跑的是 `read_case`，然後回
    「卷內還沒有相似案例的資料」。**按鈕寫查找，它去讀已經有的東西。**

    根因是 `tool_hint` 收下來就丟掉（整個 backend 只有欄位宣告那一行提到它），
    模型只看到 `/查找相似案例` 這串文字，自己決定做什麼。

    **模型的選擇其實有道理，不要誤判成模型笨**：`search_similar_decisions(query)`
    要一個查詢字串，chip 沒帶，它不願意編一個——紅線 1 就是這樣要求的。
    問題不在模型，在沒有人給它查詢詞。
    """
    wanted = {
        "search_similar_decisions": "search_similar_decisions",
        "search_regulations": "search_regulations",
        "build_relation_graph": "build_relation_graph",
    }
    for hint, tool in wanted.items():
        calls: list = []
        events: list = []
        t = _hint_tools(calls, events, retriever=_RecordingRetriever(), run_id="run-1")
        out = t.run_tool_hint(hint)
        called = [d["tool"] for n, d in events if n == "tool_call"]
        assert called == [tool], f"chip {hint} 跑的是 {called}，不是它寫的那支"
        assert "read_case" not in called, f"chip {hint} 又跑去讀卷內了"
        assert out and "不要再呼叫一次同一支工具" in out, out

    # 流水線那兩支要用假 adapter（會真的跑節點）
    for hint in ("extract_case_document", "generate_decision_draft"):
        calls, events = [], []
        t = _hint_tools(calls, events, run_pipeline=_FakePipeline(["n1"]), run_id="run-1",
                        case_manifest=_MANIFEST_OK)
        t.run_tool_hint(hint)
        called = [d["tool"] for n, d in events if n == "tool_call"]
        assert called == [hint], f"chip {hint} 跑的是 {called}"


def test_the_two_chips_use_the_two_different_queries_n4_uses():
    """查法條吃通道 A 的查詢句，查相似案吃通道 B 的——**兩串刻意不同**。

    N4 自己就是這樣分的（`build_query` 給法條查表、`build_case_query` 給相似案語意
    檢索，後者只吃卷證原文與案型，不吃改寫句——改寫句會漏撤銷案）。混用的話
    畫面上的相似案會跟 N4 卡片裡的是兩批東西，兩邊都說自己是「本案的相似案」。
    """
    calls, events = [], []
    kb = _RecordingRetriever()
    _hint_tools(calls, events, retriever=kb).run_tool_hint("search_similar_decisions")
    assert kb.queries == ["卷證事實原文"], f"相似案用錯查詢句：{kb.queries}"

    calls, events = [], []
    t = _hint_tools(calls, events, retriever=_RecordingRetriever())
    t.run_tool_hint("search_regulations")
    # 法條走內建的 LawTableRetriever（吃 snapshot），所以看送進事件的 args
    args = [d["args"]["query"] for n, d in events if n == "tool_call"]
    assert args == ["訴願法第77條"], f"法條用錯查詢句：{args}"


def test_every_status_the_backend_emits_is_in_the_contract_value_domain():
    """實際發出去的 `status` 不得超出契約 §2.3 的值域。

    **用 AST 掃 `_result(...)` 的呼叫**，不是 grep：grep 會被註解與 docstring
    裡的示例字串騙（這個檔的註解裡到處都是 `status:"empty"`）。
    """
    tree = ast.parse(pathlib.Path(chat_mod.__file__).read_text(encoding="utf-8"))
    seen: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_result"):
            continue
        for kw in node.keywords:
            if kw.arg != "status":
                continue
            if isinstance(kw.value, ast.Constant):
                seen.add(kw.value.value)
            elif isinstance(kw.value, ast.Name):
                seen.add(getattr(chat_mod, kw.value.id))
            elif isinstance(kw.value, ast.IfExp):  # `"ok" if entries else "empty"`
                seen.update(b.value for b in (kw.value.body, kw.value.orelse)
                            if isinstance(b, ast.Constant))
    assert seen, "一個 status 都沒掃到，這條檢查的前提壞了"
    stray = sorted(seen - set(chat_mod.TOOL_RESULT_STATUSES))
    assert not stray, f"發出了契約值域以外的 status：{stray}"
    # 四個值都要真的有人在用——列了一個沒人發的值等於契約說謊
    assert set(chat_mod.TOOL_RESULT_STATUSES) - seen == set(), \
        f"契約列了但後端從來不發：{sorted(set(chat_mod.TOOL_RESULT_STATUSES) - seen)}"


def test_a_chip_with_no_derivable_query_says_why_instead_of_inventing_one():
    """導不出查詢詞時**不得編一個去查**（CONSTITUTION §1 紅線）。

    隨手丟一個「行政處分」之類的通用詞進去，會查回一堆跟本案無關的決定書，
    而且看起來很像查到了——那比查無更糟。
    """
    calls, events = [], []
    kb = _RecordingRetriever()
    out = _hint_tools(calls, events, retriever=kb, law_query="", case_query=""
                      ).run_tool_hint("search_similar_decisions")

    assert kb.queries == [], f"沒有查詢詞卻還是去查了：{kb.queries}"
    result = [d for n, d in events if n == "tool_result"][0]
    # **`not_attempted` 不是 `empty`**（2026-09-13 加的第四個值）：檢索層一次都沒被
    # 呼叫過。講成「查無」是在報告一件沒發生的事——而兩者的 note 都非空，
    # 前端在資料上分不出來，只能比對中文字串，那種比對下次改字就悄悄失效。
    assert result["status"] == chat_mod.STATUS_NOT_ATTEMPTED, \
        f"還沒查被講成查無了：{result['status']}"
    assert "還導不出查詢詞" in result["note"], result["note"]
    assert "要先解析卷證" in out, out
    assert "不要自己想一個查詢詞" in out, out
    assert "不要說查無相似案例" in out, "會被讀成「查過了、沒有」——這次根本還沒查"


def test_a_free_typed_question_is_left_to_the_model():
    """沒有 chip 的自由問句維持原狀（契約 §2.1）。

    把它也變成強制的話，「幫我看一下這件案子」會被硬塞成某一支工具。
    """
    for hint in ("", "read_case", "不存在的工具", "refine_text"):
        calls, events = [], []
        assert _hint_tools(calls, events, retriever=_RecordingRetriever()
                           ).run_tool_hint(hint) is None, f"{hint!r} 不該被當成 chip 執行"
        assert not events, f"{hint!r} 不該發出任何事件"


#: 畫面方位詞。**只收「螢幕上的位置」，不收「文件裡的位置」**——
#: prompt 自己會寫「上面那五個工具」「見下方規則」，那是在指這份文件，不是畫面。
#: 這個分界靠語感，沒有辦法機械判定，所以清單保守：寧可漏抓，不要抓錯讓人去改措辭
#: （改措辭避開關鍵字會讓資訊失真，那比漏抓更糟）。
_SCREEN_WORDS = ("左欄", "右欄", "左邊", "右邊", "左側", "右側", "左上", "右上",
                 "按鈕", "分頁", "頁籤")

#: 反例標記。`❌` 那幾行寫的正是「不准講的話」，掃描要跳過它們，
#: 否則規則本身會把自己判違規。用一個顯眼的符號當標記，比「跳過含『不是』的行」
#: 這種靠語意的判斷可靠。
_COUNTEREXAMPLE = "❌"


def _chat_prompt() -> str:
    return chat_mod._prompt("chat_ask")


def test_the_chat_prompt_forbids_leaking_tool_and_field_identifiers():
    """承辦人面前不得出現 `read_case` 這種程式名（2026-09-13 實跑抓到）。

    實例：「如果你想先看目前抽到的內容，跟我說一聲，我可以用 `read_case` 幫你讀出來。」
    這是這個專案第三個同類——前兩個是欄位鍵名 `no`、以及
    `art77.not_auto_screened_reason` 裡的「見 `screen.party_standing`」。

    **這條測試只驗得到「約束寫進 prompt 了」**，驗不到模型會不會照做。
    見 `test_what_these_two_prompt_rules_can_and_cannot_be_verified_offline`。
    """
    prompt = _chat_prompt()
    assert "不要把程式名" in prompt, "prompt 沒有這條約束"
    # 要有正反例，光寫一句禁令模型會照自己的理解發揮
    assert _COUNTEREXAMPLE in prompt and "read_case" in prompt, "這條禁令沒有給反例"
    assert "中文名" in prompt, "沒說清楚該講什麼（只說不准講什麼，模型會不敢提）"
    assert "內部代號" in prompt, "內部流水線代號也要擋（QA t06 實測外洩「六節點分析」）"


def test_the_chat_prompt_forbids_describing_a_screen_it_cannot_see():
    """模型不得說「請在左邊的人工表單裡補」——它看不到 UI（2026-09-13 實跑抓到）。

    這句沒有捏造事實，但它在陳述一件自己不知道的事，跟編造同一個家族。

    **順便釘住 prompt 自己不犯這條**：2026-09-13 之前紅線 2 寫的是
    「期間計算由**左欄的**程序審查負責」——**prompt 自己在教模型描述畫面**，
    一邊禁止一邊示範。這種自我矛盾沒有任何 grep 會抓，因為兩句話各自都合理。
    """
    prompt = _chat_prompt()
    assert "不要描述畫面" in prompt, "prompt 沒有這條約束"
    assert "看不到承辦人的螢幕" in prompt, "沒說清楚為什麼不能講（模型需要理由才守得住）"

    strays = [(i, line, w)
              for i, line in enumerate(prompt.splitlines(), 1)
              if _COUNTEREXAMPLE not in line
              for w in _SCREEN_WORDS if w in line]
    if strays:
        raise AssertionError(
            "chat_ask.md 自己在描述畫面，一邊禁止一邊示範："
            + "、".join(f"第 {i} 行「{w}」：{line.strip()}" for i, line, w in strays))


def test_the_prompt_describes_every_tool_the_agent_actually_has():
    """prompt 說「你有五個工具」，實際註冊八支——**那三支不在任何紅線的字面涵蓋內**。

    雲上實測（QA `t06`）的外洩正是這麼來的：紅線 5 寫「上面那五個工具名」，
    而 prompt 裡 `:1`／`:7` 自己在用「六節點分析」「六節點抓到的法條」，
    模型照抄出「法規依據（六節點分析抓到的相關法條）」——
    **「六節點」是內部流水線代號，正是紅線 5 要擋的東西，而 prompt 自己在教它講。**
    """
    prompt = _chat_prompt()
    # `TOOL_LABELS` 是契約 §3.0 的值域，也是 `as_strands_tools()` 註冊的那一組
    # （`test_tool_labels_cover_every_registered_tool` 釘住兩者一致）。
    for name in chat_mod.TOOL_LABELS:
        assert name in prompt, f"{name} 有註冊但 prompt 沒提到，模型不知道它能做什麼"
    assert f"你有八個工具" in prompt, "工具數量寫錯，紅線 5 的「上面那N個」就框不住全部"
    assert len(chat_mod.TOOL_LABELS) == 8, \
        f"工具數量變成 {len(chat_mod.TOOL_LABELS)} 了，prompt 那句話要跟著改"


def test_the_prompt_does_not_teach_the_model_to_say_the_internal_node_names():
    """`n1`–`n6`／「六節點」是內部代號，prompt 自己不得用它們講話。

    同一個形狀今晚已經出現兩次：紅線 2 原本寫「左欄的程序審查」（prompt 自己在
    描述畫面），現在是「六節點分析」（prompt 自己在講內部代號）。
    **禁令與示範寫在同一份檔案裡時，示範會贏。**
    """
    strays = [(i, line) for i, line in enumerate(_chat_prompt().splitlines(), 1)
              if "六節點" in line and _COUNTEREXAMPLE not in line]
    if strays:
        raise AssertionError(
            "chat_ask.md 自己在用內部代號講話，一邊禁止一邊示範："
            + "、".join(f"第 {i} 行：{line.strip()}" for i, line in strays))


def test_the_prompt_does_not_assume_the_case_has_already_been_analysed():
    """契約 §2.1 ③：沒有 run 也要能開口。

    prompt 開頭原本寫「承辦人正在看一份**已經跑完六節點分析**的案子」——
    對新上傳的案子那是**假前提**，而模型會照著它去描述一份不存在的分析結果。
    """
    prompt = _chat_prompt()
    assert "已經跑完" not in prompt, "prompt 假設案子分析過了"
    assert "可能還沒有被分析過" in prompt, "沒有明說新案子的情況"


def test_the_tool_return_strings_do_not_describe_the_screen():
    """紅線 6 擋的是模型描述畫面，但**假話的來源常常是後端餵給它的字串**。

    雲上實測：`generate_decision_draft` 回「草稿全文請由承辦人在**產出區**檢視」、
    `build_relation_graph` 回「圖已經**回給畫面**了」。模型照講，而前端那個群組
    叫「答辯書與產出」，畫面上沒有叫「產出區」的東西。

    這跟「prompt 自己在犯紅線 2」是同一個形狀，只是搬到了工具回傳值上。
    """
    src = pathlib.Path(chat_mod.__file__).read_text(encoding="utf-8")
    # 只看會被送回模型的字串：`return f"..."` 與 `parts.append("...")`
    for lineno, line in enumerate(src.split("\n"), 1):
        stripped = line.strip()
        if not (stripped.startswith(("return f\"", "return \"", "f\"", "\"", "parts.append("))
                or ".append(f\"" in stripped or ".append(\"" in stripped):
            continue
        for word in ("產出區", "回給畫面", "左欄", "右欄", "左邊", "右邊", "按鈕"):
            assert word not in line, \
                f"backend/llm/chat.py:{lineno} 把畫面位置寫進了要回給模型的字串：{stripped[:90]}"


def test_what_these_two_prompt_rules_can_and_cannot_be_verified_offline():
    """**這條測試的內容就是它的限制說明。** 故意留在測試裡讓人讀到。

    驗得到（上面兩條測試）：
    - 兩條約束確實在 `chat_ask.md` 裡，而且各自附了反例與理由。
    - prompt **自己**沒有在示範「描述畫面」。

    驗不到（離線測試路徑沒有模型，一行模型輸出都拿不到）：
    - 模型會不會照做。
    - 寫成「輸出不得含 `read_case`」那種字串檢查**擋不住變形**——
      `read_case_document`、「讀卷工具」、「那個讀卷的函式」都繞得過去；
      方位那條更沒有 pattern 可寫（「在你畫面比較靠上的那一區」）。
    - 所以**不要**在別處補一條「模型輸出不得含 X」然後當成驗過了。
      那種檢查會給出安全感而擋不住實際發生過的那兩句。

    要真的驗，只有一條路：live 聊天實跑 + 人或模型照 rubric 判讀輸出
    （`test_live_chat_*` 那組的位置），而那需要 `CHAT_LIVE_BASE`。
    **這一條目前是紅的，原因就是上面這句。**
    """
    assert "read_case" in _chat_prompt(), "連 prompt 都沒提到工具名的話，上面的推論要重寫"


#: N1 實際寫出來的降級原因（`backend/nodes/n1_extract.py` 的 `reason`）。
#: 這裡刻意抄一份**實際字串**而不是 import 過來組：這幾支測的是「聊天層把拿到的
#: 原因原樣講出去」，用 n1 的程式去生它就變成兩邊用同一個 bug 互相佐證。
#: 中文標籤是否跟前端一致由 `test_live_plumbing` 的跨層守衛另外釘。
_N1_STUCK = ("必填欄位信心不足或缺漏（低信心：無；缺漏：案號、送達日），"
             "需人工表單補齊後才能續跑")


def test_extract_says_what_it_is_stuck_on_not_just_that_there_is_no_draft():
    """CONSTITUTION §1：管線知道自己為什麼卡住，就不准只說「還沒有草稿」。

    2026-09-13 雲上實測的漏：`run_meta.degraded` 寫著「缺漏：案號」，但
    `tool_result.note` 是空字串、回給模型的字串只提終態，於是聊天視窗講的是
    「這份案子還沒有生成草稿」——**承辦人不會知道下一步是去補案號**。
    工具卡會標黃、燈號會紅，但黃燈沒說黃在哪。
    """
    calls: list = []
    events: list = []
    fake = _FakePipeline(["n1", "n2", "n3"], sections={"screen": {"x": 1}},
                         state="NEEDS_INPUT",
                         degraded=[{"node": "n1", "reason": _N1_STUCK}])
    out = _tools(calls, events=events, run_pipeline=fake).extract_case_document()

    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == "ok", "降級不是失敗，狀態不變"
    assert _N1_STUCK in result["note"], f"note 沒帶上降級原因：{result['note']!r}"
    assert "讀卷抽取" in result["note"], "要說得出是哪一步卡住"
    # 欄位鍵名不得端到承辦人面前
    for code in ("['no']", "'no'", "service_method"):
        assert code not in result["note"], f"note 出現開發者鍵名 {code}：{result['note']!r}"

    # 回給模型的字串：講事實、講下一步、擋掉會講錯的話（照 picked_laws 那段的寫法）
    assert _N1_STUCK in out, out
    assert "不要說解析已完成" in out and "不要說現在可以生成草稿" in out, out


def test_a_run_with_nothing_degraded_says_not_one_extra_word():
    """沒有降級時 `note` 與回給模型的字串**逐字**維持原樣。

    釘這條是因為反向的錯同樣嚴重：正常案件多出一段「有節點降級」的警告，
    承辦人會去找一個不存在的問題，而那種雜訊沒有任何測試會抓。
    """
    calls: list = []
    events: list = []
    fake = _FakePipeline(["n1", "n2", "n3"], sections={"screen": {"x": 1}})
    out = _tools(calls, events=events, run_pipeline=fake).extract_case_document()

    result = [d for n, d in events if n == "tool_result"][0]
    assert result["note"] == "", f"沒有降級卻寫了 note：{result['note']!r}"
    assert out == ("已完成卷證解析（run run-new-1，終態 SCREENED）。"
                   "這個 run **只跑到程序審查，沒有草稿**。"
                   + chat_mod._EXTRACT_NO_REREAD), out


def test_extract_does_not_order_the_model_to_read_the_case_again():
    """一次「解析卷證」不該跳出四張卡（QA `r05`）。

        tc-1 extract_case_document   （n1 11150ms／n2 0ms／n3 0ms）
        tc-2 read_case {"section":"intake"}
        tc-3 read_case {"section":"facts_excerpt"}
        tc-4 read_case {"section":"screen"}

    整輪 28 秒而 n1 只佔 11 秒，其餘是三輪模型往返加三次節流。
    **不是模型不聽話**——`extract_case_document` 的回傳字串自己寫著
    「要看內容請用 read_case 讀 intake／facts_excerpt／screen」。
    而契約 §3.3 說那四塊內容由前端打彙整版取，agent 不必再讀。

    chip 的目的是「按鈕寫什麼就做什麼」，那三張卡跟這個目的相反。
    """
    for degraded in ([], [{"node": "n1", "reason": _N1_STUCK}]):
        calls: list = []
        events: list = []
        fake = _FakePipeline(["n1", "n2", "n3"], sections={"screen": {"x": 1}},
                             degraded=degraded)
        out = _tools(calls, events=events, run_pipeline=fake).extract_case_document()
        assert "不必再讀一次卷內" in out, out
        # 函式名與分區鍵名都不得端到模型面前（紅線 5 管不到工具回傳值，所以這裡自己擋）
        for leaked in ("read_case", "facts_excerpt", "intake／"):
            assert leaked not in out, f"回傳字串漏出了 {leaked}：{out}"


def test_a_degraded_entry_with_no_reason_is_not_padded_with_a_guess():
    """管線標了降級卻沒寫原因時，**不得替它編一句**（CONSTITUTION §1 零編造）。

    唯一能講的是那張黃卡本身，講不出為什麼就不講——組一句「可能是案號有問題」
    比不講更糟：它看起來像系統知道，其實是猜的。
    """
    calls: list = []
    events: list = []
    fake = _FakePipeline(["n1"], sections={"screen": {"x": 1}}, state="NEEDS_INPUT",
                         degraded=[{"node": "n1", "reason": None},
                                   {"node": "n1", "reason": "   "}])
    out = _tools(calls, events=events, run_pipeline=fake).extract_case_document()

    result = [d for n, d in events if n == "tool_result"][0]
    assert result["note"] == "", f"沒有原因就不該有 note：{result['note']!r}"
    assert "降級" not in out, out


def test_draft_also_relays_the_degradation_reason():
    """`generate_decision_draft` 同一個毛病：n5／n6 降級時也要講得出來。

    這條路徑 `from_node="n4"`，帶上來的可能含 N1 那筆（沒重跑的節點由
    `run_case()` 從 base_state 續帶）——**欄位還缺著就生了草稿**正是要講的事。
    """
    calls: list = []
    events: list = []
    fake = _FakePipeline(["n4", "n5", "n6"], state="VERIFIED",
                         degraded=[{"node": "n5",
                                    "reason": "fixture 檔位：草稿為模板重播，非模型即時生成"}])
    out = _tools(calls, events=events, run_pipeline=fake, run_id="run-old",
                 payload={"screen": {"x": 1}}, case_manifest=_MANIFEST_OK
                 ).generate_decision_draft()

    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == "ok"
    assert "草稿撰寫：fixture 檔位：草稿為模板重播" in result["note"], result["note"]
    assert "模板重播" in out, out
    assert "不要說這份草稿已經可以直接用" in out, out


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

    t.read_case("retrieved_laws")
    assert t.refbook.get("L1")["t"] == "舊 run 的法條"

    t.extract_case_document()
    t.read_case("retrieved_laws")
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

def _gen_fn() -> ast.AsyncFunctionDef:
    tree = ast.parse(_api_chat_src())
    chat_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "chat")
    return next(n for n in ast.walk(chat_fn)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "gen")


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
    assert any(a.startswith("put") or a == "call_soon_threadsafe" for a in attrs), \
        "gen() 的 emit 沒有把事件推進佇列，generator 那端取不到東西"


def test_gen_runs_the_turn_on_a_worker_thread_so_the_generator_can_yield_meanwhile():
    """generator 自己跑 `_run_turn` 就沒有機會 yield——必須有人代跑。"""
    assert "Thread" in ast.dump(_gen_fn()), \
        "gen() 沒有把回合丟到工作執行緒，yield 不出中間事件"


def test_gen_is_an_async_generator_so_it_does_not_hold_a_threadpool_token():
    """**這條釘的是「41 條 SSE 就把服務弄掉」那個懸崖**（2026-09-13 實測）。

    starlette 迭代**同步** generator 的方式是丟進 anyio 的 threadpool，而且阻塞多久
    就佔著那個 token 多久。聊天一輪 10–72 秒、池子預設 40。實測（sync 版、池子 40）：
    39 條並行時 `/api/cases` 2.1 ms，**41 條就 timeout**。接下來是 ALB 判 task 不健康
    → 換掉 task → `backend/output/` 是容器本地磁碟，上傳的卷證與 manifest 全部消失。

    而這個 token 對這支 generator 是**純浪費**：真正的工作跑在自己的
    `threading.Thread` 上，同步版只是坐在佇列上空等。改 async 之後
    這條路徑的 threadpool 用量歸零（實測：100 條並行時 `/api/cases` 1.9 ms）。

    **不要因為「同步版讀起來比較簡單」就改回去。**
    """
    assert isinstance(_gen_fn(), ast.AsyncFunctionDef), \
        "gen() 不是 async generator：每條 SSE 會佔住一個 threadpool token 長達 72 秒"


def test_health_does_not_share_the_threadpool_with_the_streaming_endpoints():
    """健康檢查不該跟它要監測的負載搶同一個資源（2026-09-13）。

    兩件事缺一不可，所以兩件都釘：
    1. `health` 是 `async def`——同步 `def` 會被丟進 anyio 預設池，跟 SSE 同一池。
    2. 它的實際工作跑在**專用**執行緒池上——只改 async 會把讀檔搬到 event loop，
       變成阻塞所有人而不是只佔一個 token，**那比不改更糟**。
    """
    tree = ast.parse((ROOT / "backend" / "api" / "app.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "health")
    assert isinstance(fn, ast.AsyncFunctionDef), \
        "health 是同步 def：它會跟 SSE 搶同一個 threadpool，41 條就打不開"
    src = ast.unparse(fn)
    assert "_HEALTH_POOL" in src, "health 沒有用專用執行緒池，SSE 還是搶得走"
    assert "run_in_executor" in src, "health 的讀檔沒有離開 event loop"
    # 紅線不變：它必須真的去讀，不得回一個快取過的常數
    body = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_health_body")
    assert "_health_checks" in ast.unparse(body), "health 沒有真的跑檢查"


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


def test_every_payload_backed_section_is_actually_sliced_by_the_bridge():
    """橋切出來的分區，與 `read_case` 從 payload 讀的分區，必須剛好對上。

    兩邊各存一份是刻意的（編排層不該依賴聊天層，方向會反），代價是可能漂。
    漂掉的症狀：`read_case` 說某個分區是空的，但畫面上明明有東西。

    **2026-09-13 起不再是「兩個 tuple 逐字相等」**：`CASE_SECTIONS` 多了
    `laws`／`cases` 兩個**讀卷宗清單**（`manifest.json`）的分區，它們不經過橋。
    所以這裡釘的是真正的那條不變量——**payload 那邊沒有多切、也沒有少切**。
    寫成相等的話，拆開這件事會讓一條沒壞的測試紅，而那會誘人去把它改成恆真的。
    """
    from_payload = ({"intake", "facts_excerpt", "screen"}
                    | set(chat_mod._RETRIEVED_SECTIONS.values()))
    assert from_payload == set(chat_bridge.PAYLOAD_SECTIONS), \
        (f"橋與聊天層的分區值域漂了：橋切 {sorted(chat_bridge.PAYLOAD_SECTIONS)}、"
         f"read_case 讀 {sorted(from_payload)}")
    # 卷宗清單那兩個分區必須真的存在於 CASE_SECTIONS，否則模型讀不到承辦人挑的東西
    for section in ("laws", "cases"):
        assert section in CASE_SECTIONS
        assert section in chat_mod._MANIFEST_SECTIONS, f"{section} 應該讀卷宗清單"


def test_the_pipeline_callable_injected_into_the_chat_layer_is_an_adapter_not_run_case():
    """層級禁令的真正守法：注入的必須是**回傳 plain dict 的 adapter**。

    直接把 `run_case` 注進去的話，`backend/llm/chat.py` 雖然沒有 import 語句
    （AST 那條測試會綠），卻會拿到一個 `CaseState` 物件並讀它的屬性——
    **層級形式上守住、實質被穿**。這條檢查 `_pipeline_adapter` 真的存在，
    而且 `build_chat_agent` 收到的是它而不是 `run_case`。
    """
    tree = ast.parse(_api_chat_src())
    imported = _imports_of(_api_chat_src())
    assert "backend.orchestrator.chat_bridge" in imported, \
        "api/chat.py 沒有從橋那一側取 adapter"
    build = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "build_chat_agent"]
    assert build, "找不到 build_chat_agent 的呼叫"
    injected = [kw for kw in build[0].keywords if kw.arg == "run_pipeline"]
    assert injected, "build_chat_agent 沒有收到 run_pipeline"
    expr = ast.unparse(injected[0].value)
    assert expr.startswith("pipeline_adapter("), f"注入的不是 adapter，而是 {expr}"
    # 反向：`run_case` 不得被直接注入，也不該再出現在這個檔裡
    assert "run_case" not in _api_chat_src(), \
        "api/chat.py 還碰得到 run_case——adapter 的意義就是讓聊天層只碰得到 dict"


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


# ── 壞掉的卷宗清單要壞得乾淨（假設清單第 6 條，2026-09-12）──────────────

def test_a_malformed_manifest_fails_the_tool_cleanly_instead_of_blowing_up_the_turn():
    """`laws` 不是 list of dict 時，要回 `tool_result{status:"failed"}`。

    **不是**讓 `AttributeError` 冒出去。那樣的後果是整輪以 `error`／`stage:"internal"`
    收掉，而且先前發出的 `tool_call` **永遠等不到配對的 `tool_result`**——
    前端那張工具卡會一直轉，畫面上看不出發生了什麼事。

    這條同時釘住「成對」：`call_id` 配得起來才是一張收得掉的卡。
    """
    for manifest in (
        {"laws": {"L1": {"t": "x"}}, "references": [{"id": "C1"}]},   # dict 不是 list
        {"laws": [{"id": "L1", "t": "x"}], "references": ["C1"]},     # 裡面是字串
        {"laws": ["廢棄物清理法第 2 條"], "references": [{"id": "C1"}]},
    ):
        calls: list = []
        events: list = []
        fake = _FakePipeline(["n4", "n5", "n6"], state="VERIFIED")
        t = _tools(calls, events=events, run_pipeline=fake, run_id="run-old",
                   payload={"screen": {"x": 1}}, case_manifest=manifest)
        out = t.generate_decision_draft()          # 不得拋例外

        kinds = [(n, d["call_id"], d.get("status")) for n, d in events]
        assert kinds == [("tool_call", "tc-1", None), ("tool_result", "tc-1", "failed")], kinds
        note = [d["note"] for n, d in events if n == "tool_result"][0]
        assert "格式不對" in note, note
        assert "格式不對" in out
        assert not fake.calls, "形狀壞掉就不該真的去跑流水線"


def test_a_malformed_manifest_is_not_reported_as_the_user_forgetting_to_pick_things():
    """壞掉的資料與「還沒挑」是**兩件事**，訊息不得混用。

    「還沒有查過法規與相似案例」是使用者少做一步，他去挑幾條就解決了；
    「格式不對」是資料壞了，叫他再挑一次沒有用。講錯會讓人一直重試同一個動作。
    """
    calls: list = []
    ev_empty: list = []
    _tools(calls, events=ev_empty, run_pipeline=_FakePipeline([]), run_id="run-old",
           payload={"screen": {"x": 1}}, case_manifest={}).generate_decision_draft()
    empty_note = [d["note"] for n, d in ev_empty if n == "tool_result"][0]

    ev_bad: list = []
    _tools(calls, events=ev_bad, run_pipeline=_FakePipeline([]), run_id="run-old",
           payload={"screen": {"x": 1}},
           case_manifest={"laws": ["x"], "references": ["y"]}).generate_decision_draft()
    bad_note = [d["note"] for n, d in ev_bad if n == "tool_result"][0]

    assert empty_note != bad_note, "兩種失敗講同一句話"
    assert "還沒有查過" in empty_note and "還沒有查過" not in bad_note


def test_an_empty_or_unreadable_manifest_still_means_the_list_is_empty_not_broken():
    """對照組：**靜默回空是對的，不要一起改掉。**

    檔案不存在、JSON 壞掉、頂層不是物件，在 `load_case_manifest` 就變成 `{}`，
    語意是「這個案子的清單是空的」——使用者去挑幾條法規就解決了。
    那跟「清單有東西但不是清單的形狀」不同級（`AttributeError` 那條）。
    這條釘住前者仍然走「還沒有查過」，沒有被新的守衛順手改成「格式不對」。
    """
    calls: list = []
    for manifest in ({}, {"laws": [], "references": []}, {"laws": None, "references": None}):
        events: list = []
        _tools(calls, events=events, run_pipeline=_FakePipeline([]), run_id="run-old",
               payload={"screen": {"x": 1}}, case_manifest=manifest).generate_decision_draft()
        note = [d["note"] for n, d in events if n == "tool_result"][0]
        assert "還沒有查過法規與相似案例" in note, f"{manifest} → {note}"


# ── 案件關聯圖工具（契約 v2 §3.7）─────────────────────────────────


def _graph_ok(**over):
    """一份最小但形狀正確的關聯圖回傳（`backend/graph/relation.py` 的輸出）。"""
    g = {"run_id": "run-x", "generated": "2026-09-13T00:00:00+08:00", "status": "ok",
         "note": "", "cols": ["卷證", "事實", "爭點", "法規依據", "結論"],
         "nodes": [{"id": "D1", "k": "doc", "c": 0, "t": "a.pdf"}],
         "edges": [{"from": "D1", "to": "F1", "rel": "quote", "basis": "x"}],
         "flagged": [], "unlinked": {"laws": [], "issues": [], "note": ""},
         "stats": {"nodes": 1, "edges": 1, "edges_flagged": 0,
                   "sentences_total": 13, "sentences_in_graph": 4}}
    g.update(over)
    return g


def test_relation_graph_tool_puts_the_graph_in_the_tool_result():
    """圖走 `tool_result.graph`（契約 v2 §3.1 三類工具各填自己那塊）。"""
    calls: list = []
    events: list = []
    graph = _graph_ok()
    t = _tools(calls, events=events, run_id="run-x", build_graph=lambda *, run_id: graph)
    answer = t.build_relation_graph()
    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == "ok", result
    assert result["graph"] is graph, "圖沒有進 tool_result.graph"
    assert result["hits"] == [], "關聯圖不產生引用事件"
    assert "13" in answer and "4" in answer, f"回給模型的話沒帶上稀疏的真值：{answer}"
    assert "複述" in answer, "沒有叫模型別在對話裡把節點念一遍"


def test_relation_graph_tool_without_an_adapter_fails_loudly():
    """乙案容器沒有 runstore：**明說沒有，不要回一張空圖**。

    空圖與「畫不出來」在畫面上長得一模一樣，而意思相反——
    一個是「這個案子沒什麼關聯」，一個是「我們沒去看」。
    """
    calls: list = []
    events: list = []
    note = _tools(calls, events=events, run_id="run-x").build_relation_graph()
    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == "failed", result
    assert result["graph"] is None, "畫不出來卻給了 graph"
    assert "不要自己描述" in note, note


def test_relation_graph_tool_without_a_run_is_not_attempted_not_failed_not_empty():
    """還沒跑過任何一次 run → `not_attempted`。

    不是 `failed`（資料還沒到那一步，不是壞了），也不是 `empty`
    （2026-09-13 拆開：`empty` 的前提是**真的畫過了**，而這裡 `build_graph`
    一次都沒被呼叫——測試用一個被呼叫就爆炸的假 adapter 釘住這件事）。
    """
    calls: list = []
    events: list = []
    boom = lambda *, run_id: (_ for _ in ()).throw(AssertionError("不該被呼叫"))
    note = _tools(calls, events=events, build_graph=boom).build_relation_graph()
    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == chat_mod.STATUS_NOT_ATTEMPTED, result
    assert "extract_case_document" in note, note


def test_a_graph_that_really_was_drawn_stays_empty_not_not_attempted():
    """**做了但結果不完整 → `empty`**，不得因為「還沒到那一步」就改標 not_attempted。

    只跑到程序審查的 run 畫得出圖（`graph` 就在事件裡），只是沒有結論句所以只有前半。
    工作確實發生了——這正是 `empty` 與 `not_attempted` 的分界。
    """
    calls: list = []
    events: list = []
    graph = _graph_ok(status="empty", note="要先生成草稿才畫得出完整關聯",
                      nodes=[], edges=[])
    _tools(calls, events=events, run_id="run-x",
           build_graph=(lambda *, run_id: graph)).build_relation_graph()
    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == "empty", f"圖真的畫了卻標成沒做：{result['status']}"
    assert result["graph"] is not None, "圖沒有帶出去"


def test_relation_graph_tool_passes_empty_status_through():
    """只解析過卷證的 run：純函式回 `empty`，工具要照原樣轉出去，**不翻成 failed**。"""
    calls: list = []
    events: list = []
    graph = _graph_ok(status="empty", note="要先生成草稿才畫得出完整關聯", nodes=[], edges=[])
    note = _tools(calls, events=events, run_id="run-x",
                  build_graph=lambda *, run_id: graph).build_relation_graph()
    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == "empty", result
    assert result["graph"] is graph, "退化時也要把圖帶出去（前端照樣渲染空狀態）"
    assert "不是失敗" in note, note


def test_relation_graph_tool_surfaces_flagged_citations_to_the_model():
    """**紅線**：草稿引了檢索沒找到的法條，模型一定要講出來。

    這條釘的不是格式是**內容**：`flagged` 非空時，回給模型的字串要帶上那些條號，
    否則模型沒有材料可講，承辦人就看不到最該看到的那一項。
    """
    calls: list = []
    events: list = []
    graph = _graph_ok(flagged=[{"sentence_id": "s5", "raw": "訴願法第15條",
                                "state": "ok", "lamp": "g", "basis": "查無"}])
    note = _tools(calls, events=events, run_id="run-x",
                  build_graph=lambda *, run_id: graph).build_relation_graph()
    assert "訴願法第15條" in note, note
    assert "查無" in note, note
    assert [d for n, d in events if n == "tool_result"][0]["status"] == "ok"


def test_relation_graph_tool_reports_adapter_failure_instead_of_describing_a_graph():
    """adapter 炸了（例：run_id 屬於別的案件）→ `failed` + 照實說，不要描述一張圖。"""
    calls: list = []
    events: list = []

    def boom(*, run_id: str) -> dict:
        raise ValueError(f"run_id {run_id} 屬於案件 case-b，不是 case-a")

    note = _tools(calls, events=events, run_id="run-x",
                  build_graph=boom).build_relation_graph()
    result = [d for n, d in events if n == "tool_result"][0]
    assert result["status"] == "failed", result
    assert "ValueError" in note and "case-b" in note, note


def test_relation_graph_tool_is_in_the_tool_label_vocabulary():
    """契約 §3.0 的值域：實作完成才進 `TOOL_LABELS`。

    這條與 `test_every_tool_entry_point_throttles_exactly_once` 是一對：
    那條保證表裡的每一支都有進入點，這條保證這一支真的在表裡
    （前端靠 `label` 顯示，缺了會拿到 undefined）。
    """
    assert TOOL_LABELS["build_relation_graph"] == "畫案件關聯圖"


# ── `ranked_by`：score 那個數字是哪一種（2026-09-13）──────────────────

def _kb_hit(ranked: bool) -> Hit:
    """KB 命中。**沒重排時檢索器刻意不標 `ranked_by`**——那是既有行為
    （`test_live_plumbing.py` 的「沒重排就不該標 ranked_by」釘住的），
    所以這裡的假 hit 也不標，否則測的是一個不存在的形狀。"""
    payload = {"provenance": "official"}
    if ranked:
        payload |= {"ranked_by": "rerank", "embedding_score": 0.42, "rerank_score": 0.87}
    return Hit(id="kb-1", title="甲", score=0.87 if ranked else 0.42,
               source="歷史訴願決定書/x.txt", payload=payload)


def _lawtable_hit() -> Hit:
    """法條查表命中。`score` 是 **1.0／0.0 的二元命中**，不是相似度。"""
    return Hit(id="L-訴願法-14", title="訴願法第 14 條", score=1.0,
               source="laws-snapshot.json／訴願法", verified=True,
               payload={"law": "訴願法", "article": "14", "in_snapshot_law": True})


def test_a_reranked_hit_says_so_instead_of_letting_the_screen_call_it_vector_similarity():
    """**這條釘的是畫面上那句文案會不會說謊。**

    開了重排之後 `KBRetriever` 把 `score` 換成重排分數（`retrieval/kb.py:546`）。
    不把 `ranked_by` 帶出去的話，前端拿到一個重排分數卻寫「向量相似度 87%」——
    **那個數字不是向量相似度**，而那句話就在 demo 主畫面上（CONSTITUTION §1）。
    """
    entry = RefBook().add(_kb_hit(ranked=True))
    assert entry["ranked_by"] == "rerank", entry
    assert entry["score"] == 0.87, "score 要是重排分數本身，不是 embedding 分數"


def test_a_plain_embedding_hit_still_carries_the_field_rather_than_omitting_it():
    """沒重排時**也要送**，值是 embedding。

    檢索器在沒重排時刻意不標這個鍵，但「缺鍵」不能原樣傳給前端——
    省略會讓前端拿到 `undefined` 而不是值，跟契約 §2.3／§4.4 同一條紀律。
    規則與 `backend/nodes/n4_retrieval.py:345` 一致（`p.get("ranked_by") or "embedding"`）。
    """
    entry = RefBook().add(_kb_hit(ranked=False))
    assert "ranked_by" in entry, "沒重排就把鍵省略了——前端會拿到 undefined"
    assert entry["ranked_by"] == "embedding", entry


def test_a_law_table_hit_reports_null_because_its_score_is_not_a_similarity():
    """法條查表的 1.0 是**條號在不在快照裡**，報成任何一種排序方式都是
    把二元結果講成程度。回 `None`，前端據此不顯示百分比。
    """
    entry = RefBook().add(_lawtable_hit(), chat_mod._score_kind(
        type("R", (), {"name": "lawtable"})()))
    assert entry["ranked_by"] is None, entry
    assert "ranked_by" in entry, "None 也要有這個鍵，不能省略"


def test_the_score_kind_comes_from_the_retriever_name_not_from_sniffing_the_payload():
    """判斷依據是 retriever **宣告出來的** `name`（`lawtable.py:21` 的類別屬性）。

    嗅 payload（「有 `law` 鍵就是查表」）會在某天欄位改名時靜默倒向另一邊，
    而症狀是畫面上的文案錯了、**沒有任何燈會亮**。
    """
    assert chat_mod._score_kind(type("R", (), {"name": "lawtable"})()) is None
    assert chat_mod._score_kind(type("R", (), {"name": "bedrock_kb"})()) == "embedding"
    # 認不出來的檢索器當成一般相似度檢索，不當成二元命中——
    # 寧可多報一個 embedding（前端顯示百分比），也不要把相似度靜默藏起來
    assert chat_mod._score_kind(type("R", (), {"name": "local_bm25"})()) == "embedding"


def test_case_refs_have_no_ranked_by_because_they_have_no_score():
    """卷內既有資料（`L1`／`C3`）沒有檢索分數，也就沒有「誰排的」可言。"""
    rb = RefBook()
    rb.add_case_refs([{"id": "L1", "t": "廢棄物清理法第 2 條"}])
    entry = rb.get("L1")
    assert entry["score"] is None and entry["ranked_by"] is None, entry


def test_ranked_by_reaches_both_tool_result_hits_and_done_refs():
    """契約要求**兩個地方**都有：`tool_result.hits[]` 與 `done.refs[]`。

    `done.refs[]` 是 `classify_answer` 從 RefBook 撈回來的，所以只驗 hits[]
    會漏掉「refs 那條路上有人重組 entry 把欄位弄丟」的情形。
    """
    calls: list = []
    events: list = []
    t = _tools(calls, _FakeRetriever([_kb_hit(ranked=True)]), events=events)
    t.retrieve_refs("信賴保護")

    hits = [d["hits"] for n, d in events if n == "tool_result"][0]
    assert hits[0]["ranked_by"] == "rerank", hits

    verdict = classify_answer("這個爭點有前例嗎？", "有的，參見 [c1]。", t.refbook)
    assert verdict.refs, "這一則應該有 refs 才驗得到"
    assert verdict.refs[0]["ranked_by"] == "rerank", verdict.refs


def test_gen_sends_a_heartbeat_while_the_turn_is_silent():
    """**這條釘的是「部署走 CloudFront 後，生成草稿在雲上斷線」**（2026-09-13）。

    CloudFront 規定 origin 兩個封包之間超過 response timeout 就切斷，而 n5 主筆是
    一次不串流的 Bedrock 呼叫，本機實測空檔 69 秒。gen() 若只 `await q.get()`，
    那 69 秒一個 byte 都不會出去。

    驗兩件事：①有 `: ping` 這個 yield；②等佇列的那一步帶逾時（`asyncio.wait(..., timeout=)`）
    ——少了逾時，ping 永遠輪不到。實跑驗證（uvicorn＋curl，心跳 1 秒、回合 4.5 秒）：
    ping 每秒抵達一次、`done` 照常收尾，見 commit 說明。
    """
    gen = _gen_fn()
    yielded = [n.value.value for n in ast.walk(gen)
               if isinstance(n, ast.Yield) and isinstance(n.value, ast.Constant)]
    assert any(isinstance(v, str) and v.startswith(":") for v in yielded), \
        "gen() 沒有送 SSE 註解心跳：CloudFront 會在 n5 的空檔切斷連線"
    waits = [n for n in ast.walk(gen)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "wait" and any(k.arg == "timeout" for k in n.keywords)]
    assert waits, "gen() 等佇列時沒有逾時，心跳送不出去"
    beat = next(float(n.value.value) for n in ast.parse(_api_chat_src()).body
                if isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == "SSE_HEARTBEAT_SECONDS" for t in n.targets))
    assert beat < 60, f"心跳間隔 {beat} 秒，要遠小於 CloudFront readTimeout（120 秒）"

