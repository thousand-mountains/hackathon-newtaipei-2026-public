"""案件關聯圖（`backend/graph/relation.py`）單元測試。

對應 `plans/2026-09-12-relation-graph.md` §7 的 AC1–AC8。

**每一條都釘一個「換個看似合理的實作就會紅」的點**，不是覆蓋率：

- AC4 把 `cite` 邊數釘成「`raw` 能在 `laws[].t` 找到的筆數」，不是 `> 0`——
  寫 `> 0` 的話用錯 join key 也可能因為別的原因湊出幾條。
  `test_the_tempting_join_key_really_is_empty` 把那個坑本身也釘住：
  換成 `resolved_id ↔ gate_ref_key` 在同一份資料上是 **0 命中**，
  所以「有邊」這件事本身就證明了尺是對的。
- AC5 驗的是「不靜默丟棄」，是紅線不是功能。
- AC8 的截斷測試刻意用**比上限長**的真實文字，改大上限就會紅。

fixture 是三份合成 run（`backend/tests/fixtures/runs/`，全部 `synthetic-` 開頭，
CONSTITUTION §3）。**不吃 `backend/output/runs/`** ——那個目錄 gitignored，
靠它的測試在乾淨 clone 上會變成「沒有資料所以略過」，而略過看起來很像通過。
"""
from __future__ import annotations

import ast
import copy
import json
import pathlib

from backend.graph import relation
from backend.graph.relation import build_relation_graph
from backend.tests.harness import assert_eq, assert_in, assert_true

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "runs"

#: 程序型合成案：3/4 引用對得上 `laws[]`，第 4 筆（訴願法第15條）是
#: 「草稿引了檢索沒找到的法條」——AC5 的主角。
ORDINARY = FIXTURES / "run-synthetic-ordinary-01-951e19ac17ad.json"
#: 建築法時效型合成案：爭點關鍵詞真的落在卷證摘錄裡（`trigger` 邊的主角），
#: 但三筆引用的實體法條號 `laws[]` 一個都沒有（`_retrieval_divergence` 記載的已知限制）。
BLOCKED = FIXTURES / "run-synthetic-blocked-01-c9dabf566b96.json"
#: 只跑到 n3 的 run：沒有草稿，AC7 退化行為的主角。
SCREENED = FIXTURES / "run-synthetic-ordinary-01-2094bca891d7.json"


def _payload(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _graph(path: pathlib.Path) -> dict:
    return build_relation_graph(_payload(path))


def _rels(graph: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in graph["edges"]:
        out[e["rel"]] = out.get(e["rel"], 0) + 1
    return out


def _nodes_of(graph: dict, kind: str) -> list[dict]:
    return [n for n in graph["nodes"] if n["k"] == kind]


# ── AC1：零 LLM（AST，不是 grep）──────────────────────────────────


def _imports_of(path: pathlib.Path, module_name: str) -> set[str]:
    """一個檔案的絕對 import 名集合。相對 import 依所在 package 解析。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg = module_name.rsplit(".", 1)[0] if "." in module_name else ""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = pkg.split(".") if pkg else []
                base = ".".join(parts[: len(parts) - (node.level - 1)] or parts)
                found.add(f"{base}.{node.module}" if node.module else base)
            elif node.module:
                found.add(node.module)
                found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def _module_path(mod: str) -> pathlib.Path | None:
    cand = ROOT.joinpath(*mod.split(".")).with_suffix(".py")
    if cand.exists():
        return cand
    cand = ROOT.joinpath(*mod.split(".")) / "__init__.py"
    return cand if cand.exists() else None


def test_relation_graph_has_no_llm_dependency_by_ast() -> None:
    """AC1：`backend/graph/relation.py` 不得直接或間接 import `backend.llm`。

    **用 AST 不用 grep**：grep 分不出註解、字串與真正的 import（這個檔的
    docstring 裡就寫著 `backend.llm` 四個字），也擋不住 `import X as Y`。
    遞迴走 `backend.*` 的 import 邊，因為「自己沒 import、但 import 的東西有」
    一樣是依賴。
    """
    start = ROOT / "backend" / "graph" / "relation.py"
    assert_true(start.exists(), "relation.py 不見了，這條檢查會無聲地永遠綠")
    seen: set[str] = set()
    stack = [("backend.graph.relation", start)]
    while stack:
        mod, path = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        assert_true(
            mod != "backend.llm" and not mod.startswith("backend.llm."),
            f"關聯圖間接 import 了 {mod}（規則引擎零 LLM 依賴，CONSTITUTION §4）",
        )
        if path is None or not path.exists():
            continue
        for imp in _imports_of(path, mod):
            if imp == "backend" or imp.startswith("backend."):
                stack.append((imp, _module_path(imp)))
            assert_true(
                imp.split(".")[0] not in ("strands", "strands_agents", "boto3"),
                f"{mod} import 了 {imp}（關聯圖不打網路、不碰模型）",
            )


def test_relation_graph_does_not_touch_runstore_or_orchestrator() -> None:
    """層級：純函式不碰 runstore、不碰 orchestrator（plan §8）。

    這條分開寫是因為它**不是** CONSTITUTION §4 的紅線，是這份 plan 自己的
    層級約束——混在上一條裡的話，日後有人放寬其中一條會連帶放寬另一條。
    """
    imports = _imports_of(ROOT / "backend" / "graph" / "relation.py", "backend.graph.relation")
    for banned in ("backend.orchestrator", "backend.api", "backend.nodes"):
        assert_true(
            not any(i == banned or i.startswith(banned + ".") for i in imports),
            f"relation.py import 了 {banned}（它應該只吃一個 dict）",
        )


# ── AC2：邊的種類（兩份 fixture 合起來才有四種，見 plan 與 proposal）──


def test_ordinary_run_produces_quote_address_and_cite_edges() -> None:
    """AC2（其一）：程序型合成案產出 `quote` / `address` / `cite` 三種邊。

    **這份沒有 `trigger`**：它的爭點關鍵詞「未實際收受」只在 `intake.note` 裡，
    不在任何一段 `facts_excerpt[].text`。那不是漏畫，是沒有事實可歸屬——
    見 `test_issue_triggered_only_by_intake_note_is_reported_not_invented`。
    """
    rels = _rels(_graph(ORDINARY))
    for rel in ("quote", "address", "cite"):
        assert_true(rels.get(rel, 0) >= 1, f"程序型合成案少了 {rel} 邊：{rels}")


def test_blocked_run_produces_the_trigger_edge() -> None:
    """AC2（其二）：建築法時效型合成案產出 `trigger` 邊。

    四種邊在**同一份 run** 上湊不齊，掃過 9421 份不重複的 VERIFIED run 一份都沒有
    （proposal 的 Notes 有掃描表）。這是語料的事實，不改 N3 的判準、不加合成案來湊。
    """
    rels = _rels(_graph(BLOCKED))
    assert_true(rels.get("trigger", 0) >= 1, f"建築法型合成案少了 trigger 邊：{rels}")
    assert_true(rels.get("quote", 0) >= 1, f"少了 quote 邊：{rels}")
    assert_true(rels.get("address", 0) >= 1, f"少了 address 邊：{rels}")


def test_no_edge_kind_outside_the_four() -> None:
    """**沒有「矛盾」這種邊。** 後端沒有偵測矛盾的機制，畫了就是編（plan §6）。"""
    for path in (ORDINARY, BLOCKED):
        for e in _graph(path)["edges"]:
            assert_in(e["rel"], ("quote", "trigger", "address", "cite"),
                      f"{path.name} 出現了第五種邊 {e['rel']}")


def test_every_edge_carries_a_basis() -> None:
    """每條邊都要指得出它從哪個欄位推出來（CONSTITUTION §2）。"""
    for path in (ORDINARY, BLOCKED):
        for e in _graph(path)["edges"]:
            assert_true(bool(e.get("basis")), f"{path.name} 的 {e} 沒有 basis")


# ── AC3：trigger 的關鍵詞真的在那段文字裡 ─────────────────────────


def test_trigger_keywords_really_occur_in_the_fact_text() -> None:
    """AC3：每條 `trigger` 邊的 `detail` 關鍵詞，要真的出現在來源事實的文字裡。

    比對的是**未截斷的原文**（`facts_excerpt[].text`），不是節點上截斷過的 `d`——
    拿截斷後的字串去驗，一個落在截斷點後面的關鍵詞會讓這條測試假紅。
    """
    payload = _payload(BLOCKED)
    graph = build_relation_graph(payload)
    raw_text = {f"F{n}": str(f.get("text") or "")
                for n, f in enumerate(payload["facts_excerpt"], start=1)}
    triggers = [e for e in graph["edges"] if e["rel"] == "trigger"]
    assert_true(triggers, "沒有 trigger 邊，這條檢查等於沒跑")
    for e in triggers:
        assert_true(bool(e.get("detail")), f"{e} 沒有記下命中的關鍵詞")
        for kw in e["detail"]:
            assert_in(kw, raw_text[e["from"]], f"關鍵詞 {kw} 不在 {e['from']} 的原文裡")


def test_issue_triggered_only_by_intake_note_is_reported_not_invented() -> None:
    """只由 `intake.note` 觸發的爭點要進 `unlinked.issues`，**不是憑空連一條線**。

    N3 的 haystack 是 `digest + intake.note`，`digest` 才是 `facts_excerpt` 串接
    （`backend/orchestrator/graph.py:128-137`）。程序型合成案的「未實際收受」
    正好只落在 note 裡。
    """
    payload = _payload(ORDINARY)
    graph = build_relation_graph(payload)
    note = str((payload.get("intake") or {}).get("note") or "")
    facts = " ".join(str(f.get("text") or "") for f in payload["facts_excerpt"])
    keywords = [k for i in payload["screen"]["fact_issues"] for k in i["matched_keywords"]]
    assert_true(any(k in note for k in keywords), "fixture 變了：關鍵詞已經不在 intake.note 裡")
    assert_true(not any(k in facts for k in keywords), "fixture 變了：關鍵詞已經進到卷證摘錄裡")
    assert_eq(graph["unlinked"]["issues"], ["I1"], "接不到事實的爭點沒有被報出來")
    assert_in("intake.note", graph["unlinked"]["note"], "沒有說明為什麼接不到")


# ── AC4：cite 的 join 沒走歪 ───────────────────────────────────────


def _expected_cite_hits(payload: dict) -> int:
    """對的尺：`citations[].raw` 在 `laws[].t` ∪ `cases[].t` 裡找得到的筆數。"""
    laws = (payload.get("retrieval") or {}).get("laws") or []
    cases = (payload.get("retrieval") or {}).get("cases") or []
    titles = {x.get("t") for x in list(laws) + list(cases)}
    return sum(1 for c in (payload.get("gate") or {}).get("citations") or []
               if c.get("raw") in titles)


def test_cite_edge_count_equals_the_raw_to_title_join() -> None:
    """AC4：`cite` 邊數 **等於** `raw` 能在 `laws[].t` 找到的筆數。

    刻意不寫 `> 0`：用錯 join key 也可能因為別的原因湊出幾條。
    """
    for path in (ORDINARY, BLOCKED):
        payload = _payload(path)
        got = _rels(build_relation_graph(payload)).get("cite", 0)
        assert_eq(got, _expected_cite_hits(payload), f"{path.name} 的 cite 邊數對不上")


def test_the_tempting_join_key_really_is_empty() -> None:
    """把 §5.1 那個坑本身釘住：`resolved_id ↔ gate_ref_key` 在同一份資料上是 0 命中。

    這條看起來像在測 fixture，實際上是在**保護上一條的意義**：如果哪天兩種
    join key 都會命中，AC4 就不再能證明尺是對的，那時要有人被這條測試叫醒。
    """
    payload = _payload(ORDINARY)
    keys = {x.get("gate_ref_key") for x in (payload["retrieval"].get("laws") or [])}
    wrong = sum(1 for c in payload["gate"]["citations"] if c.get("resolved_id") in keys)
    assert_eq(wrong, 0, "resolved_id ↔ gate_ref_key 竟然命中了，AC4 的意義要重新確認")
    assert_true(_expected_cite_hits(payload) > 0, "對的尺竟然 0 命中，fixture 壞了")


def test_cite_edges_carry_state_and_lamp_for_the_third_line_style() -> None:
    """設計稿的第三種線型改用 `state` / `lamp`——那是真的，矛盾不是（plan §6）。"""
    edges = [e for e in _graph(ORDINARY)["edges"] if e["rel"] == "cite"]
    assert_true(edges, "沒有 cite 邊，這條檢查等於沒跑")
    for e in edges:
        assert_in(e.get("state"),
                  ("ok", "amended", "out_of_scope", "missing", "unparseable"),
                  f"{e} 的 state 不在契約的值域裡")
        assert_true(e.get("lamp") is not None, f"{e} 沒有 lamp")


# ── AC5：沒命中的 citation 不得靜默丟棄（紅線）────────────────────


def test_citation_not_found_in_laws_goes_to_flagged() -> None:
    """AC5：草稿引了檢索沒找到的法條 → 進 `flagged`。

    程序型合成案引了「訴願法第15條」，而 `laws[]` 裡沒有。
    **「AI 引了一條我們沒檢索到的法條」正是承辦人最需要知道的事。**
    """
    graph = _graph(ORDINARY)
    raws = [f["raw"] for f in graph["flagged"]]
    assert_in("訴願法第15條", raws, "查無的引用被靜默丟掉了")
    for f in graph["flagged"]:
        assert_in("查無", f["basis"], f"{f} 沒寫明為什麼被標記")


def test_every_citation_is_either_an_edge_or_flagged() -> None:
    """守恆：`citations[]` 的每一筆不是變成邊，就是進 `flagged`。**沒有第三條路。**

    這條比「flagged 非空」強：它擋的是「一部分命中、剩下的悄悄消失」。
    """
    for path in (ORDINARY, BLOCKED):
        payload = _payload(path)
        graph = build_relation_graph(payload)
        total = len(payload["gate"]["citations"])
        assert_eq(_rels(graph).get("cite", 0) + len(graph["flagged"]), total,
                  f"{path.name} 有 citation 既沒變成邊也沒進 flagged")


def test_blocked_run_flags_all_three_substantive_citations() -> None:
    """建築法型合成案的三筆引用 `laws[]` 一個都沒有——三筆全部要進 `flagged`。"""
    graph = _graph(BLOCKED)
    assert_eq(len(graph["flagged"]), 3, "查無的引用沒有全部被標記")
    assert_eq(_rels(graph).get("cite", 0), 0, "不該有 cite 邊卻有了")


def test_retrieved_but_never_cited_laws_go_to_unlinked() -> None:
    """「查到了但沒用上」是真實且有意義的資訊，不要靜默丟掉（契約 §3.7）。

    **`_nodes_of(graph, "law")` 含相似案**（兩種東西同一個 `k`），所以這裡要
    照 `origin` 濾掉——不濾的話這條在有相似案的 run 上會紅，而它紅的原因
    會是「測試自己把相似案當法規」，不是實作壞了。
    """
    graph = _graph(ORDINARY)
    cited = {e["to"] for e in graph["edges"] if e["rel"] == "cite"}
    all_laws = {n["id"] for n in _nodes_of(graph, "law") if n["origin"] == "retrieval"}
    assert_eq(sorted(graph["unlinked"]["laws"]), sorted(all_laws - cited),
              "unlinked.laws 跟實際沒被引用的法規對不上")
    assert_true(graph["unlinked"]["laws"], "這份 fixture 本來就有沒被引用的法規，結果是空的")


# ── 相似案不是法規（2026-09-13 雲上實打抓到的誤導）────────────────
#
# 三份 fixture **一份都沒有 `cases[]`**，這正是這個 bug 活下來的原因：
# 沒有任何測資走過相似案那條路。所以這裡把相似案加進去——加的是合成資料
# （CONSTITUTION §3：`synthetic-` 前綴、不暗示為真實案件），**不是把雲上那份
# 含資料集內容的 run 搬進 git**。


#: 合成相似案。`verified: False` 照 `backend/retrieval/kb.py:392` 的實況寫死：
#: KB 命中一律 `False`，意思是「還沒對回資料集實檔」，不是「查證過是假的」。
SYNTHETIC_CASES = [
    {"id": "C1", "t": "synthetic-相似案-甲-駁回", "verified": False,
     "provenance": "synthetic", "sim": 0.81},
    {"id": "C2", "t": "synthetic-相似案-乙-撤銷", "verified": False,
     "provenance": "synthetic", "sim": 0.74},
]


def _payload_with_cases(path: pathlib.Path = ORDINARY) -> dict:
    """把合成相似案塞進一份既有 fixture 的 `retrieval.cases`。"""
    payload = _payload(path)
    payload.setdefault("retrieval", {})["cases"] = copy.deepcopy(SYNTHETIC_CASES)
    payload.pop("cases", None)          # 扁平鍵優先，這裡要走巢狀那條
    return payload


def test_unlinked_never_calls_a_similar_case_a_law() -> None:
    """**這條釘住那句假話。**

    2026-09-13 QA 在雲上實打拿到 `unlinked.laws = [C1…C5, L1, L2, L3, L6]` 與
    `note = "9 條檢索到的法規沒有被任何結論句引用"`——其中 5 條是相似訴願決定。
    承辦人會照那句話去找五條不存在的法規。

    相似案沒被引用**仍然要報**（不靜默丟棄），只是要報在 `cases` 那一鍵。
    """
    graph = build_relation_graph(_payload_with_cases())
    assert_true(graph["unlinked"]["laws"], "法規那一鍵空了，這條等於沒驗")
    for lid in graph["unlinked"]["laws"]:
        assert_true(not lid.startswith("C"), f"{lid} 是相似案，卻被放進 unlinked.laws")
    assert_eq(sorted(graph["unlinked"]["cases"]), ["C1", "C2"],
              "沒被引用的相似案沒有被報出來——那是靜默丟棄")
    assert_true("相似" in graph["unlinked"]["note"],
                f"note 沒講出相似案這一種：{graph['unlinked']['note']}")


def test_unlinked_note_counts_each_kind_with_its_own_number() -> None:
    """`note` 的每個數字都要對得上它所稱呼的那一種的長度。

    **數字用程式算、量詞也要對**：法規論「條」、訴願決定論「件」。
    合併計數時「9 條法規」這種話就是這樣長出來的。
    """
    graph = build_relation_graph(_payload_with_cases())
    note = graph["unlinked"]["note"]
    n_laws, n_cases = len(graph["unlinked"]["laws"]), len(graph["unlinked"]["cases"])
    assert_in(f"{n_laws} 條檢索到的法規", note, f"法規的數字對不上：{note}")
    assert_in(f"{n_cases} 件檢索到的相似訴願決定", note, f"相似案的數字對不上：{note}")
    assert_true(str(n_laws + n_cases) + " 條" not in note,
                f"note 把兩種東西加總成一個數字了：{note}")


def test_similar_case_is_not_labelled_suspect() -> None:
    """相似案的 `verified=False` 是「還沒查」，不是「查了是假的」。

    `backend/retrieval/kb.py:392`：KB 命中**一律** `verified=False`，對回資料集
    實檔是 N6 的事。照法規那把尺讀，畫面上每一件相似訴願決定都會被標成
    `suspect`——那是這支程式自己講出來的假話。

    同一份圖裡，法規的 `verified=False` **仍然**要是 `suspect`（否則這條
    就變成「把驗證狀態全部調寬」，那是另一個方向的假話）。
    """
    payload = _payload_with_cases()
    payload["retrieval"]["laws"] = copy.deepcopy(payload["retrieval"]["laws"])
    payload["retrieval"]["laws"][0]["verified"] = False
    graph = build_relation_graph(payload)
    verify = {n["id"]: (n["origin"], n["verify"]) for n in _nodes_of(graph, "law")}
    assert_eq(verify["C1"], ("similar_case", "unverifiable"), "相似案被講成查證過是假的")
    assert_eq(verify["C2"], ("similar_case", "unverifiable"), "相似案被講成查證過是假的")
    suspect = [i for i, (o, v) in verify.items() if o == "retrieval" and v == "suspect"]
    assert_eq(len(suspect), 1, "法規那邊的 suspect 被一起放寬了")


def test_cite_basis_names_the_table_it_actually_matched() -> None:
    """`basis` 是寫給人核對的：對到 `cases[].t` 就不能寫 `laws[].t`。"""
    payload = _payload_with_cases()
    citations = payload["gate"]["citations"]
    assert_true(citations, "fixture 沒有 citations，這條等於沒驗")
    hijacked = copy.deepcopy(citations[0])
    hijacked["raw"] = "synthetic-相似案-甲-駁回"
    payload["gate"]["citations"] = citations + [hijacked]
    graph = build_relation_graph(payload)
    to_case = [e for e in graph["edges"] if e["rel"] == "cite" and e["to"] == "C1"]
    assert_eq(len(to_case), 1, "對到相似案的引用不見了")
    assert_in("cases[].t", to_case[0]["basis"], f"basis 講錯是跟哪張表對上的：{to_case[0]}")
    for e in graph["edges"]:
        if e["rel"] == "cite" and e["to"] != "C1":
            assert_in("laws[].t", e["basis"], f"法規的 basis 被一起改掉了：{e}")


# ── AC6：stats 是算出來的 ─────────────────────────────────────────


def test_stats_are_computed_not_written_by_hand() -> None:
    """AC6：`stats` 的每個數字都等於對應陣列的長度 / payload 的實際句數。"""
    for path in (ORDINARY, BLOCKED, SCREENED):
        payload = _payload(path)
        graph = build_relation_graph(payload)
        stats = graph["stats"]
        assert_eq(stats["nodes"], len(graph["nodes"]), f"{path.name} stats.nodes")
        assert_eq(stats["edges"], len(graph["edges"]), f"{path.name} stats.edges")
        assert_eq(stats["edges_flagged"], len(graph["flagged"]), f"{path.name} stats.edges_flagged")
        total = sum(len(b.get("ss") or []) for b in (payload.get("gate") or {}).get("doc") or [])
        assert_eq(stats["sentences_total"], total, f"{path.name} stats.sentences_total")


def test_sparse_address_edges_are_reported_not_padded() -> None:
    """稀疏是資料的事實。`sentences_in_graph` 要明顯小於 `sentences_total`，**不補線**。

    實測程序型合成案 13 句只有 4 句進圖。這條釘的是「兩個數字真的不一樣」——
    若哪天有人為了讓圖飽滿而把所有句子都畫進去，這條會紅。
    """
    stats = _graph(ORDINARY)["stats"]
    assert_true(stats["sentences_in_graph"] < stats["sentences_total"],
                f"每一句都進圖了，是不是補線了？{stats}")
    assert_true(stats["sentences_in_graph"] > 0, "一句都沒進圖，join 可能走歪了")


def test_out_nodes_are_exactly_the_linked_sentences() -> None:
    """`out` 節點只收有連線的句子（顯示決定）；孤立句仍在 payload 裡，只是不進圖。"""
    graph = _graph(ORDINARY)
    out_ids = {n["id"] for n in _nodes_of(graph, "out")}
    linked = {e["from"] for e in graph["edges"] if e["rel"] == "cite"}
    linked |= {e["to"] for e in graph["edges"] if e["rel"] == "address"}
    assert_eq(sorted(out_ids), sorted(linked), "進圖的結論句跟有連線的句子對不上")
    assert_eq(len(out_ids), graph["stats"]["sentences_in_graph"], "sentences_in_graph 對不上")


# ── AC7：退化行為 ─────────────────────────────────────────────────


def test_screened_only_run_returns_empty_not_an_exception() -> None:
    """AC7：只跑到程序審查的 run → `status:"empty"` + `note`。

    **不得拋例外、不得回半張圖假裝完整。** 回傳的鍵要齊，前端不必特判。
    """
    payload = _payload(SCREENED)
    assert_eq(payload["state"], "SCREENED", "fixture 變了，這已經不是只跑到 n3 的 run")
    graph = build_relation_graph(payload)
    assert_eq(graph["status"], "empty", "沒有草稿卻回了 ok")
    assert_true("草稿" in graph["note"], f"note 沒說要先生成草稿：{graph['note']}")
    assert_eq(graph["nodes"], [], "沒有草稿卻畫了節點")
    assert_eq(graph["edges"], [], "沒有草稿卻畫了邊")
    for key in ("run_id", "generated", "cols", "flagged", "unlinked", "stats"):
        assert_in(key, graph, f"退化回傳少了 {key}，前端要特判")


def test_empty_payload_does_not_raise() -> None:
    """整個 payload 是空 dict 也要回 `empty`，不是 KeyError。"""
    graph = build_relation_graph({})
    assert_eq(graph["status"], "empty", "空 payload 沒有回 empty")
    assert_eq(graph["run_id"], None, "空 payload 竟然編了一個 run_id")


def test_ok_run_reports_status_ok() -> None:
    """有草稿就是 `ok`——`status` 一律存在，前端不必用「nodes 空不空」猜。"""
    assert_eq(_graph(ORDINARY)["status"], "ok", "有草稿卻不是 ok")


# ── AC8：產物不含個資 ─────────────────────────────────────────────


def test_fact_node_text_is_clipped() -> None:
    """AC8：`fact` 節點的文字要截斷。

    fixture 的事實原文有 70 字以上，上限是 `FACT_TITLE_MAX`（40）/
    `FACT_DETAIL_MAX`（120）。**把上限改大這條就會紅**——這是它不是恆真斷言的證明。
    """
    payload = _payload(ORDINARY)
    longest = max(len(str(f.get("text") or "")) for f in payload["facts_excerpt"])
    assert_true(longest > relation.FACT_TITLE_MAX,
                "fixture 的原文比上限還短，這條測試等於沒跑")
    for n in _nodes_of(build_relation_graph(payload), "fact"):
        assert_true(len(n["t"]) <= relation.FACT_TITLE_MAX + 1,
                    f"fact 節點的 t 沒截斷：{len(n['t'])} 字")
        assert_true(len(n["d"]) <= relation.FACT_DETAIL_MAX + 1,
                    f"fact 節點的 d 沒截斷：{len(n['d'])} 字")
        assert_true(n["t"].endswith(relation.ELLIPSIS), "超過上限卻沒有截斷記號")


def test_id_number_plate_and_street_address_are_masked() -> None:
    """身分證字號／車牌／門牌要抹掉。

    **這不是完整的去識別化**（`_REDACTIONS` 的註解寫明了），主要防線是截斷；
    這條釘的是「這三種樣式確實有被處理」。
    """
    # 身分證字號剛好也是 10 碼英數，會撞到 run_all 的 KB id 掃描。逐行標記放行，
    # 理由見 `run_all.KB_ID_SCAN_OPT_OUT`。這是合成值，不是任何人的號碼。
    fake_id = "A123456789"  # NOT-A-KB-ID：合成身分證字號測資
    masked = relation._redact(f"訴願人 {fake_id} 住新北市土城區中華路2段45號3樓，車號 ABC-1234")
    for leaked in (fake_id, "45號", "ABC-1234"):
        assert_true(leaked not in masked, f"{leaked} 沒被抹掉：{masked}")


def test_redaction_does_not_eat_verifiable_citation_numbers() -> None:
    """**引用必可驗（CONSTITUTION §2）比抹得乾淨重要。**

    裸的 `\\d+號` 會把「釋字第469號」「北環稽字第1130012345號」一起抹掉，
    而那正是這張圖存在的理由。門牌那條刻意要求前面有路／街／巷／弄／段。
    """
    for keep in ("司法院釋字第469號解釋", "北環稽字第1130012345號函", "訴願法第14條"):
        assert_eq(relation._redact(keep), keep, f"{keep} 被抹掉了")


def test_redaction_runs_before_clipping() -> None:
    """先抹再截：先截的話被截掉的那半就沒抹到，而它會原封不動留在更長的欄位裡。"""
    fake_id = "A123456789"  # NOT-A-KB-ID：合成身分證字號測資
    text = "前略" * 30 + f"身分證 {fake_id}"
    assert_true(fake_id not in relation._clip(text, 1000), "長欄位漏抹了")


# ── 輸入形狀：扁平與巢狀都要吃得下 ───────────────────────────────


def test_flat_and_nested_payload_shapes_give_the_same_graph() -> None:
    """`build_payload()` 的輸出是扁平的，runstore 存的是巢狀的，兩種都要讀得到。

    這條擋的是一個會**靜默**的失敗：只讀巢狀的話，chat 工具那條路（吃扁平 payload）
    會拿到一張空圖，而空圖看起來就像「這個案子本來就沒什麼關聯」。
    """
    nested = _payload(ORDINARY)
    flat = {
        "run_id": nested["run_id"],
        "facts_excerpt": nested["facts_excerpt"],
        "screen": nested["screen"],
        "files": nested["files"],
        "laws": nested["retrieval"]["laws"],
        "cases": nested["retrieval"]["cases"],
        "doc": nested["gate"]["doc"],
        "citations": nested["gate"]["citations"],
        "issue_refs": nested["gate"]["issue_refs"],
    }
    a, b = build_relation_graph(nested), build_relation_graph(flat)
    for key in ("nodes", "edges", "flagged", "unlinked", "stats", "status"):
        assert_eq(b[key], a[key], f"兩種形狀的 {key} 不一致")


def test_issues_view_shape_would_lose_the_trigger_edges() -> None:
    """為什麼刻意不吃扁平 payload 的頂層 `issues[]`：`_issues_view()` 丟掉了
    `matched_keywords`（`backend/orchestrator/graph.py:540`），改吃它的話
    `trigger` 邊會整批無聲消失。這條把那個理由釘成測試。
    """
    payload = copy.deepcopy(_payload(BLOCKED))
    assert_true(_rels(build_relation_graph(payload)).get("trigger", 0) >= 1,
                "fixture 本來就該有 trigger 邊")
    for issue in payload["screen"]["fact_issues"]:
        issue.pop("matched_keywords", None)  # 模擬 _issues_view() 的形狀
    assert_eq(_rels(build_relation_graph(payload)).get("trigger", 0), 0,
              "拿掉 matched_keywords 之後還畫得出 trigger 邊？那條邊不是從關鍵詞來的")


def test_pure_function_does_not_mutate_the_payload() -> None:
    """純函式：不得就地改輸入。`_attach_law_refs` 就是就地改，改在別人看不到的地方。"""
    payload = _payload(ORDINARY)
    before = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    build_relation_graph(payload)
    assert_eq(json.dumps(payload, ensure_ascii=False, sort_keys=True), before,
              "build_relation_graph 改了輸入 payload")


def test_every_edge_endpoint_is_a_real_node() -> None:
    """邊不得指向不存在的節點——前端拿到懸空的 id 會畫出半條線或整個炸掉。"""
    for path in (ORDINARY, BLOCKED):
        graph = _graph(path)
        ids = {n["id"] for n in graph["nodes"]}
        for e in graph["edges"]:
            assert_in(e["from"], ids, f"{path.name} 的邊 {e} 起點不存在")
            assert_in(e["to"], ids, f"{path.name} 的邊 {e} 終點不存在")


def test_columns_match_the_contract() -> None:
    """欄位固定五欄，`c` 就是欄序（契約 §3.7 的 `cols`）。"""
    graph = _graph(ORDINARY)
    assert_eq(graph["cols"], ["卷證", "事實", "爭點", "法規依據", "結論"], "欄名跟契約對不上")
    expected = {"doc": 0, "fact": 1, "issue": 2, "law": 3, "out": 4}
    for n in graph["nodes"]:
        assert_eq(n["c"], expected[n["k"]], f"{n['id']} 的欄序不對")


def test_fixtures_are_synthetic_only() -> None:
    """fixture 一律是合成案（CONSTITUTION §3）。真實卷證不進 git。"""
    files = sorted(FIXTURES.glob("*.json"))
    assert_true(files, "fixture 不見了，上面每一條都會變成假通過")
    for p in files:
        assert_true(p.name.startswith("run-synthetic-"), f"{p.name} 不是合成案")
