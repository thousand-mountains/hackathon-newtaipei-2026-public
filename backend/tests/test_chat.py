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
from backend.retrieval.kb import REF_PREFIXES
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


def test_ref_prefixes_is_assigned_in_exactly_one_place_and_imported_elsewhere():
    """S2b 的結構檢查：賦值只剩 `backend/retrieval/kb.py` 一處，N5 改成 import。

    複製字面值會漂——改一邊漏一邊，`retrieve_refs` 就會撈到不准引用的來源。

    **這裡刻意用 AST 讀原始碼，不 import `backend/nodes/n5_draft.py`**：把六節點
    import 進這支純函式測試，等於讓聊天層的測試背著 orchestrator 跑，正是 spec §4.0
    要避免的事（而且函式內 import 會違反紅線第 5 條，模組頂層 import 又會把相依寫死）。
    執行期的 `is` 同一物件由 AC14 在 run_all 之外單獨驗。

    這條也比 AC13 的 `grep -c '^REF_PREFIXES'` 強：AST 看得到縮排過的賦值與
    `REF_PREFIXES, X = ...` 這種寫法，行首 grep 看不到。
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

    assert len(_assignments("backend/retrieval/kb.py")) == 1, "kb.py 應該是唯一的賦值處"
    assert _assignments("backend/nodes/n5_draft.py") == [], "n5_draft 仍在自己賦值，字面值會漂"

    n5 = ast.parse((ROOT / "backend" / "nodes" / "n5_draft.py").read_text(encoding="utf-8"))
    imported_from_kb = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "backend.retrieval.kb"
        and any(a.name == "REF_PREFIXES" for a in n.names)
        for n in ast.walk(n5)
    )
    assert imported_from_kb, "n5_draft 沒有從 backend/retrieval/kb.py 取 REF_PREFIXES"


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


def _tools(monkey_throttle: list, retriever=None, snapshot=None, payload=None, events=None):
    """建一組 ChatTools，並把 `_throttle` 換成計數器（真的節流會讓測試睡好幾秒）。"""
    chat_mod._throttle = lambda: monkey_throttle.append(1)
    emit = (lambda name, data: events.append((name, data))) if events is not None else None
    return chat_mod.ChatTools(
        payload if payload is not None else {"intake": {"案由": "x"}},
        RefBook(), retriever, snapshot, emit,
    )


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
    """`retrieve_refs` 只准查函釋與判解兩個前綴，而且用的是 kb.py 那一份。"""
    calls: list = []
    r = _FakeRetriever()
    t = _tools(calls, r)
    t.retrieve_refs("信賴保護")
    assert r.calls[0][1] == {"prefix": list(REF_PREFIXES)}


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
