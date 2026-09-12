"""KB 知識圖抽取管線（`scripts/build_graph.py`）單元測試。

這支釘的是**抽取正確性**，不是覆蓋率：每一條都對應一個在 2,477 份真實語料上
實際踩到過的坑，換一個看似合理的實作就會紅。

- 換行中斷：pdftotext 會把 `空氣污染防制法` 斷在中間，少了 `compress()` 就抽不到。
- 動詞前綴：`依訴願法`、`以訴願人違反空氣污染防制法` 不得變成法規節點。
- 「同法」：要回上文找本尊，**找不到就丟棄並計數**，不准猜一個。
- `verify` 三態：ok／unverifiable／suspect 是三件不同的事，不是布林。
"""
from __future__ import annotations

import ast
import collections
import importlib.util
import json
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]

# `scripts/build_graph.py` 是 repo 內的檔案、不是第三方套件，但它不在 `backend`
# 套件底下，`sys.path` + `import build_graph` 會被 run_all 的「核心/測試路徑零外部
# 依賴」掃描當成外部模組擋下來（那條掃描的 allowed_local 只認 `backend`）。
# 用 importlib 依路徑載入，讓「這是本 repo 的檔案」寫在程式碼裡，而不是靠豁免清單。
_SPEC = importlib.util.spec_from_file_location("build_graph", ROOT / "scripts" / "build_graph.py")
assert _SPEC and _SPEC.loader
build_graph = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_graph)

LAWS = {
    "訴願法": ["14", "77", "79", "81"],
    "空氣污染防制法": ["20", "24", "63"],
    "廢棄物清理法": ["11", "50", "71"],
    "民法": ["120", "121"],
    "環境教育法": ["8", "23"],
}
WHITELIST = frozenset(LAWS)


def _articles(text: str, known: frozenset[str] | None = None) -> list[tuple[str, str]]:
    pairs, _ = build_graph.extract_citations(build_graph.compress(text), WHITELIST, known)
    return pairs


def _unresolved(text: str) -> int:
    _, count = build_graph.extract_citations(build_graph.compress(text), WHITELIST)
    return count


# ── (a) 換行中斷的法條引用 ────────────────────────────────────────────


def test_citation_broken_by_pdftotext_linebreak_is_extracted() -> None:
    """`空氣污染防\\n制法第24條` 要抽得到——這是 pdftotext -layout 的常態。"""
    text = "原處分機關認訴願人違反空氣污染防\n制法第24條第1項規定，爰處罰鍰。"
    assert _articles(text) == [("空氣污染防制法", "24")]


def test_citation_broken_by_spaces_inside_article_number_is_extracted() -> None:
    """`訴願法第 79  條` 中間的空白也要吃掉（-layout 會補對齊空白）。"""
    assert _articles("依訴願法第 79  條規定，訴願無理由者應予駁回。") == [("訴願法", "79")]


def test_compress_removes_every_kind_of_whitespace() -> None:
    assert build_graph.compress("空氣污染防\n制\t法 第\r\n24 條") == "空氣污染防制法第24條"


# ── (b) 動詞前綴要被切掉 ──────────────────────────────────────────────


def test_single_char_verb_prefix_is_stripped() -> None:
    assert _articles("依訴願法第79條") == [("訴願法", "79")]


def test_long_verb_phrase_prefix_is_stripped() -> None:
    """`以訴願人違反空氣污染防制法` 這種整句黏連也要切乾淨。"""
    assert _articles("原處分機關以訴願人違反空氣污染防制法第20條規定") == [
        ("空氣污染防制法", "20")
    ]


def test_verb_prefix_is_stripped_for_laws_outside_the_whitelist() -> None:
    """白名單以外的法規也要切乾淨——不然只是把 11 部法硬編死而已。

    `政府資訊公開法` 不在 `LAWS` 裡，靠的是 `INNER_NOISE`（`按`、`違`、`反`）切點。
    """
    assert _articles("按政府資訊公開法第18條第1項第3款") == [("政府資訊公開法", "18")]
    assert _articles("訴願人違反政府資訊公開法第18條") == [("政府資訊公開法", "18")]


def test_law_name_longer_than_twelve_chars_is_not_truncated() -> None:
    """原本的 `{2,12}` 窗口會把 14 字的法規名切頭，變成 `棄物清理專業技術人員管理辦法`。"""
    assert _articles("依廢棄物清理專業技術人員管理辦法第8條") == [
        ("廢棄物清理專業技術人員管理辦法", "8")
    ]


def test_characters_that_really_belong_to_law_names_are_not_cut() -> None:
    """切點字表不能誤傷真法名：`違`章建築、`認`定標準、規`則`、事`業`廢棄物。"""
    assert _articles("依違章建築處理辦法第5條") == [("違章建築處理辦法", "5")]
    assert _articles("按有害事業廢棄物認定標準第3條") == [("有害事業廢棄物認定標準", "3")]
    assert _articles("依道路交通安全規則第112條") == [("道路交通安全規則", "112")]


def test_resolve_law_name_strips_the_prefix_for_every_observed_verb_form() -> None:
    """`resolve_law_name` 單一函式的行為（整條管線的版本在
    `test_build_graph_produces_three_layers_and_real_counts`）。

    每個前綴都是在 2,477 份語料裡實際出現過的寫法。**用白名單外的法**（政府資訊
    公開法不在 `LAWS` 裡）——否則白名單最長後綴那條規則會先命中，切點字表整個
    停用測試照樣綠，等於沒測到真正在做事的那段。
    """
    for window in (
        "按政府資訊公開法",
        "依政府資訊公開法",
        "已違反政府資訊公開法",
        "以訴願人違反政府資訊公開法",
        "次按政府資訊公開法",
        "核認訴願人違反政府資訊公開法",
        "揆諸前揭政府資訊公開法",
    ):
        assert build_graph.resolve_law_name(window, WHITELIST) == "政府資訊公開法"
    # 白名單內的法走另一條路徑（最長後綴），也要一起釘住
    assert build_graph.resolve_law_name("以訴願人違反空氣污染防制法", WHITELIST) == "空氣污染防制法"
    # 這幾個前綴裡沒有任何「切點字」，只能靠詞頭剝除處理（語料實際寫法，各出現數十次）
    for window, want in (
        ("及環境講習執行辦法", "環境講習執行辦法"),
        ("前開環境講習執行辦法", "環境講習執行辦法"),
        ("與政府資訊公開法", "政府資訊公開法"),
    ):
        assert build_graph.resolve_law_name(window, WHITELIST) == want


# ── (c) 「同法」要解得出上文，解不出就丟棄 ────────────────────────────


def test_same_law_resolves_to_nearest_preceding_law() -> None:
    text = "按廢棄物清理法第11條規定……，又依同法第50條規定，得處罰鍰。"
    assert _articles(text) == [("廢棄物清理法", "11"), ("廢棄物清理法", "50")]


def test_ben_fa_inside_quoted_statute_resolves_to_the_quoted_law() -> None:
    """決定書引述法條原文時的「本法」指的是被引述的那部法。"""
    text = "按空氣污染防制法第24條規定：「公私場所違反本法第20條規定者……」"
    assert _articles(text) == [("空氣污染防制法", "24"), ("空氣污染防制法", "20")]


def test_same_law_without_antecedent_is_dropped_and_counted() -> None:
    """上文沒有法名就**丟棄**，不是猜一個最常見的法——這是 CONSTITUTION §2。"""
    text = "依同法第50條規定，應處罰鍰。"
    assert _articles(text) == []
    assert _unresolved(text) == 1


def test_prose_word_ending_in_fa_is_not_used_as_antecedent() -> None:
    """`違法`、`非法` 也結尾是「法」，被當成「同法」的先行詞會把條號掛到假法規上。"""
    text = "訴願人之行為顯屬違法，依同法第79條規定應予駁回。"
    assert _articles(text, known=WHITELIST) == []
    assert _unresolved(text) == 1


def test_same_law_matches_the_same_kind_of_terminator() -> None:
    """`同辦法` 要回去找辦法，不能抓到前面那部「法」。"""
    text = "按廢棄物清理法第11條，及一般廢棄物回收清除處理辦法第3條，依同辦法第9條。"
    assert ("一般廢棄物回收清除處理辦法", "9") in _articles(text)
    assert ("廢棄物清理法", "9") not in _articles(text)


# ── (d) verify 三態 ───────────────────────────────────────────────────


def test_verify_ok_when_article_exists_in_snapshot() -> None:
    assert build_graph.verify_article("訴願法", "79", LAWS) == "ok"


def test_verify_unverifiable_when_law_is_not_in_snapshot() -> None:
    """該法不在 snapshot 裡＝無從查證，**不是**可疑——不能把查不到講成查錯。"""
    assert build_graph.verify_article("政府資訊公開法", "18", LAWS) == "unverifiable"


def test_verify_suspect_when_law_is_known_but_article_is_not() -> None:
    assert build_graph.verify_article("訴願法", "999", LAWS) == "suspect"


def test_verify_distinguishes_all_three_states() -> None:
    states = {
        build_graph.verify_article("民法", "120", LAWS),
        build_graph.verify_article("民法", "40627", LAWS),
        build_graph.verify_article("海關緝私條例", "37", LAWS),
    }
    assert states == {"ok", "suspect", "unverifiable"}


# ── alias 收斂 ────────────────────────────────────────────────────────


def test_alias_merges_prose_glued_name_into_the_common_suffix() -> None:
    per_doc = [
        [("政府資訊公開法", "18")],
        [("政府資訊公開法", "18")],
        [("合政府資訊公開法", "18")],
    ]
    assert build_graph.build_alias(per_doc, WHITELIST) == {"合政府資訊公開法": "政府資訊公開法"}


def test_alias_does_not_swallow_a_genuine_long_regulation_name() -> None:
    """長法名比短後綴常見得多時不得被併——`一般廢棄物回收清除處理辦法` 要活著。"""
    per_doc = [[("一般廢棄物回收清除處理辦法", "3")] for _ in range(10)]
    per_doc.append([("清除處理辦法", "3")])
    alias = build_graph.build_alias(per_doc, WHITELIST, frozenset())
    assert "一般廢棄物回收清除處理辦法" not in alias


def test_alias_does_not_merge_into_a_mid_word_fragment() -> None:
    """`所得稅法` 不得被併進只出現一次的斷字 `得稅法`（切在法名中間，方向相反）。"""
    per_doc = [[("所得稅法", "4")] for _ in range(5)]
    per_doc.append([("得稅法", "4")])
    alias = build_graph.build_alias(per_doc, WHITELIST, frozenset({"所得稅法"}))
    assert alias.get("所得稅法") is None


def test_alias_is_transitive() -> None:
    per_doc = [[("檔案法", "18")], [("檔案法", "18")], [("有檔案法", "18")], [("及有檔案法", "18")]]
    alias = build_graph.build_alias(per_doc, WHITELIST, frozenset({"檔案法"}))
    assert alias["及有檔案法"] == "檔案法"


# ── 判解／釋字 ────────────────────────────────────────────────────────


def test_judgment_and_interpretation_citations_are_extracted() -> None:
    text = "參照最高行政法院102年度判字第147號判決及司法院釋字第469號解釋意旨"
    assert build_graph.extract_authorities(build_graph.compress(text)) == [
        ("司法判解", "102年度判字第147號"),
        ("司法院解釋", "釋字第469號"),
    ]


def test_administrative_document_number_is_not_mistaken_for_a_judgment() -> None:
    """行政文號不是判解，不得長出節點。兩筆都是語料裡真的出現過的寫法。

    `[一-鿿]{0,4}字第\\d+號` 這種寬鬆寫法會把它們全吃進來；限定法院案件類別字才擋得住。
    """
    text = "原處分機關核發110年板建字第233號建造執照，並依法務部101年法律字第10000038610號函辦理"
    assert build_graph.extract_authorities(build_graph.compress(text)) == []


def test_real_court_case_number_is_still_extracted_alongside() -> None:
    """擋行政文號不能連真的判字號一起擋掉。"""
    text = "原處分機關核發110年板建字第233號建造執照（最高行政法院106年度判字第151號參照）"
    assert build_graph.extract_authorities(build_graph.compress(text)) == [
        ("司法判解", "106年度判字第151號")
    ]


def test_authority_index_flattens_snapshot_precedents() -> None:
    snapshot = {
        "precedents": [{"court": "最高行政法院", "year": 102, "type": "判", "no": 147}],
        "interpretations": [469],
    }
    assert build_graph.authority_index(snapshot) == {"102年度判字第147號", "釋字第469號"}


# ── 官方檔名標籤 ──────────────────────────────────────────────────────


def test_official_filename_labels_are_parsed() -> None:
    assert build_graph.parse_official_name("01.110年-社會救助事件-77(1)-逾期不補正-不受理") == {
        "year": "110",
        "topic": "社會救助事件",
        "clause": "77(1)",
        "reason": "逾期不補正",
    }


def test_official_filename_without_outcome_segment_still_parses() -> None:
    """`21.114年-違反建築法事件-77(8)&79I-部分不受理&部分駁回`（語料裡真的有一筆）。"""
    parsed = build_graph.parse_official_name("21.114年-違反建築法事件-77(8)&79I-部分不受理&部分駁回")
    assert parsed["clause"] == "77(8)&79I"
    assert parsed["topic"] == "違反建築法事件"


def test_crawled_case_meta_comes_from_manifest_not_filename() -> None:
    entry = {
        "path": "kb/public/新北訴願決定書_全量/1091111113_駁回.txt",
        "provenance": "public_crawl",
        "case_no": "1091111113",
        "outcome": "駁回",
        "year": "109",
        "category": "廢棄物清理法",
    }
    meta = build_graph.case_meta(entry, "1091111113_駁回")
    assert meta == {
        "prov": "public_crawl",
        "outcome": "駁回",
        "id": "1091111113",
        "label": "1091111113",
        "year": "109",
        "cat": "廢棄物清理法",
    }


def test_judicial_and_interpretation_documents_get_their_own_provenance() -> None:
    jud = {"path": "kb/official/司法院釋字及行政判解/x.txt", "provenance": "official", "outcome": None}
    fun = {"path": "kb/official/行政函釋/y.txt", "provenance": "official", "outcome": None}
    assert build_graph.case_meta(jud, "x")["prov"] == "judicial"
    assert build_graph.case_meta(fun, "y")["prov"] == "interpretation"


# ── 端到端：建圖與裁剪 ────────────────────────────────────────────────


def _tmp_corpus(tmp: pathlib.Path) -> tuple[list[dict], pathlib.Path]:
    kb = tmp / "kb"
    (kb / "public" / "決定書").mkdir(parents=True)
    (kb / "public" / "決定書" / "1091111113_駁回.txt").write_text(
        "按廢棄物清理法第11條規定，又依同法第50條，另依訴願法第79條，"
        "及按政府資訊公開法第18條規定。參照最高行政法院102年度判字第147號判決。",
        encoding="utf-8",
    )
    entries = [
        {
            "path": "kb/public/決定書/1091111113_駁回.txt",
            "provenance": "public_crawl",
            "case_no": "1091111113",
            "outcome": "駁回",
            "year": "109",
            "category": "廢棄物清理法",
        }
    ]
    return entries, kb


def test_build_graph_produces_three_layers_and_real_counts() -> None:
    with tempfile.TemporaryDirectory() as raw:
        entries, kb = _tmp_corpus(pathlib.Path(raw))
        graph = build_graph.build_graph(entries, kb, LAWS, {"102年度判字第147號"})

    stats = graph["stats"]
    assert stats["docs_scanned"] == 1
    assert stats["docs_with_citation"] == 1
    assert stats["cases"] == 1
    assert stats["unresolved_pronoun"] == 0
    assert stats["articles"] == 4  # 只算法條，判解不混進來
    assert stats["judgments"] == 1
    assert stats["laws"] == 3  # 不含「司法判解」這個分類節點（訴願法／廢清法／政資法）
    assert stats["classification_nodes"] == 1

    ids = {n["id"] for n in graph["nodes"]}
    assert "law:廢棄物清理法" in ids
    assert "art:廢棄物清理法§50" in ids  # 同法 解出來的
    assert "art:政府資訊公開法§18" in ids
    assert "jud:102年度判字第147號" in ids
    assert "case:1091111113" in ids
    assert not any(n["label"].startswith(("按", "依", "及按")) for n in graph["nodes"])

    verify = {n["id"]: n["verify"] for n in graph["nodes"] if n["layer"] == 2}
    assert verify["art:廢棄物清理法§50"] == "ok"
    assert verify["art:政府資訊公開法§18"] == "unverifiable"
    assert verify["jud:102年度判字第147號"] == "ok"

    belongs = {(x["s"], x["t"]) for x in graph["links"] if x["k"] == "belongs"}
    assert ("art:訴願法§79", "law:訴願法") in belongs
    cites = {(x["s"], x["t"]) for x in graph["links"] if x["k"] == "cites"}
    assert ("case:1091111113", "art:訴願法§79") in cites


def _tmp_corpus_with_repeats(tmp: pathlib.Path) -> tuple[list[dict], pathlib.Path]:
    """同一條法條被引用多次的語料——`w` 的三種可能算法在這裡才會分歧。

    廢棄物清理法：§11 引 3 次、§50 引 1 次。
      法規 w = 各條引用次數總和   → 4   ← 規格要的
      法規 w = 相異法條數         → 2
      法規 w = 各條節點 w 總和    → 2   （節點 w 是「被幾份文件引用」）
    """
    kb = tmp / "kb"
    (kb / "public" / "決定書").mkdir(parents=True)
    (kb / "public" / "決定書" / "1090000001_駁回.txt").write_text(
        "按廢棄物清理法第11條規定，原處分機關認訴願人違反廢棄物清理法第11條，"
        "核與廢棄物清理法第11條規定相符，又依同法第50條，另依訴願法第79條。",
        encoding="utf-8",
    )
    entries = [{
        "path": "kb/public/決定書/1090000001_駁回.txt",
        "provenance": "public_crawl", "case_no": "1090000001",
        "outcome": "駁回", "year": "109", "category": "廢棄物清理法",
    }]
    return entries, kb


def test_law_node_weight_is_the_sum_of_its_articles_citations() -> None:
    """法規 w = 旗下法條**被引用總次數**，不是相異法條數、也不是法條節點 w 的總和。

    真實資料上這三種算法有 90 個法規不同（訴願法 4,020 vs 3,380），所以 fixture
    一定要有重複引用，否則換一種實作照樣綠。
    """
    with tempfile.TemporaryDirectory() as raw:
        entries, kb = _tmp_corpus_with_repeats(pathlib.Path(raw))
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    weights = {n["label"]: n["w"] for n in graph["nodes"] if n["layer"] == 1}
    assert weights["廢棄物清理法"] == 4  # §11×3 + §50×1
    assert weights["訴願法"] == 1
    arts = {n["id"]: n["w"] for n in graph["nodes"] if n["layer"] == 2}
    assert arts["art:廢棄物清理法§11"] == 1  # 法條節點 w 是「被幾份文件引用」
    cites = {x["t"]: x["w"] for x in graph["links"] if x["k"] == "cites"}
    assert cites["art:廢棄物清理法§11"] == 3  # 邊 w 才是「這份文件引了幾次」


def test_truncate_links_keeps_the_heaviest_cites_per_case_not_globally() -> None:
    """規格是「每案保留引用次數最高的前 8 條」。

    只有一個 case 的 fixture 在數學上分不出 per-case 與全域取前 8——**兩個 case、
    而且權重區間完全不重疊**才分得出來：全域取前 8 會把 case:2 整個吃掉。
    """
    graph = {
        "stats": {"links": 21},
        "links": [{"s": "art:A§1", "t": "law:A", "k": "belongs"}]
        + [{"s": "case:1", "t": f"art:A§{i}", "k": "cites", "w": 100 + i} for i in range(10)]
        + [{"s": "case:2", "t": f"art:B§{i}", "k": "cites", "w": i} for i in range(10)],
    }
    build_graph.truncate_links(graph)
    kept = [x for x in graph["links"] if x["k"] == "cites"]
    by_case = collections.Counter(x["s"] for x in kept)
    assert by_case["case:1"] == build_graph.TRUNCATE_KEEP
    assert by_case["case:2"] == build_graph.TRUNCATE_KEEP  # 全域取前 8 的話這裡會是 0
    assert min(x["w"] for x in kept if x["s"] == "case:1") == 100 + 10 - build_graph.TRUNCATE_KEEP
    assert min(x["w"] for x in kept if x["s"] == "case:2") == 10 - build_graph.TRUNCATE_KEEP
    assert graph["stats"]["links_truncated"] is True
    assert graph["stats"]["links"] == 2 * build_graph.TRUNCATE_KEEP + 1


def test_manifest_path_is_not_joined_twice_under_the_kb_root() -> None:
    kb = pathlib.Path("data/local/kb")
    assert build_graph.resolve_path(kb, "kb/public/a.txt") == pathlib.Path("data/local/kb/public/a.txt")
    assert build_graph.resolve_path(kb, "public/a.txt") == pathlib.Path("data/local/kb/public/a.txt")


FORBIDDEN_MODULES = ("boto3", "botocore", "bedrock", "anthropic", "strands", "openai", "backend.llm")
FORBIDDEN_CALLS = ("invoke_model", "converse", "invoke_model_with_response_stream")


def _imported_modules(tree: ast.AST) -> set[str]:
    """整棵 AST 裡所有 import 的模組名，**不分縮排層級**。

    先前這條測試只比對 `startswith("import ")` 的頂格字串，所以把
    `def f(): import boto3` 藏在函式裡完全擋不住（2026-09-12 稽核實測繞過成功）。
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def test_pipeline_imports_no_llm() -> None:
    """CONSTITUTION §4：抽取層零 LLM。

    `run_all.py` 的三道 import 掃描起點都在 `backend/`（`_scan_files()` 預設
    `roots=(BACKEND,)`、`scan_llm_import_graph` 從 `backend/nodes/` 起走），
    **涵蓋不到 `scripts/`**——所以這支腳本的 §4 紅線只有這條測試在守。
    """
    source = (ROOT / "scripts" / "build_graph.py").read_text(encoding="utf-8")
    modules = _imported_modules(ast.parse(source))
    for banned in FORBIDDEN_MODULES:
        offenders = [m for m in modules if m.lower() == banned or m.lower().startswith(banned + ".")]
        assert not offenders, f"scripts/build_graph.py import 了 {offenders}（CONSTITUTION §4 零 LLM）"


def test_pipeline_makes_no_model_invocation_calls() -> None:
    """連呼叫端也擋：`client.invoke_model(...)` 這種不是 import，AST 要另外看。"""
    source = (ROOT / "scripts" / "build_graph.py").read_text(encoding="utf-8")
    called = {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called & set(FORBIDDEN_CALLS), f"呼叫了模型 API：{called & set(FORBIDDEN_CALLS)}"


# ── (e) 準則／細則／通則／要點：2026-09-12 稽核找到的最大漏抽 ────────────


def test_zhunze_and_xize_are_recognised_as_law_terminators() -> None:
    """`裁罰準則`／`施行細則` 先前完全抽不到——而裁罰準則是語料裡被引第 4 多的法規。"""
    assert _articles("依違反廢棄物清理法罰鍰額度裁罰準則第3條附表二") == [
        ("違反廢棄物清理法罰鍰額度裁罰準則", "3")
    ]
    assert _articles("按環境教育法施行細則第6條規定") == [("環境教育法施行細則", "6")]
    assert _articles("依中央法規標準法通則第2條") != []
    assert _articles("按簽證作業要點第4條") == [("簽證作業要點", "4")]


def test_weifan_inside_a_regulation_name_is_not_cut_as_a_verb() -> None:
    """`違反`／`行為` 是這些準則**名稱的一部分**，不能當動詞切掉。

    切掉的話節點會變成 `廢棄物清理法罰鍰額度裁罰準則`、`管制執行準則`，
    報告就會寫出不存在的法規名。
    """
    assert _articles("原處分機關爰依違反廢棄物清理法罰鍰額度裁罰準則第3條") == [
        ("違反廢棄物清理法罰鍰額度裁罰準則", "3")
    ]
    assert _articles("及移動污染源違反空氣污染防制法裁罰準則第2條") == [
        ("移動污染源違反空氣污染防制法裁罰準則", "2")
    ]
    assert _articles("及空氣污染行為管制執行準則第6條") == [("空氣污染行為管制執行準則", "6")]


def test_verb_prefix_is_still_cut_when_the_target_is_a_statute() -> None:
    """放寬只限下位法規結尾；`違反○○法第N條` 的動詞照切。"""
    assert _articles("訴願人違反廢棄物清理法第11條") == [("廢棄物清理法", "11")]


# ── (f) 引號範圍：「本法」指被引述法規的母法，不是最近提到的那部 ──────────


def test_ben_fa_quoted_inside_a_subordinate_regulation_means_its_parent_statute() -> None:
    """`環境講習執行辦法第2條規定：「…經依本法第23條…」` 的「本法」是環境教育法。

    舊實作用「最近的同尾型 mention」解，會解成空氣污染防制法**而且 verify=ok**，
    錯得完全看不出來。全語料有 1,116 次這種「引述下位法規、內文寫本法」。
    """
    text = (
        "按空氣污染防制法第24條規定，原處分機關據以裁處。"
        "環境教育法施行細則第6條規定：「本法第23條所稱環境保護法律…」"
    )
    pairs = _articles(text)
    assert ("環境教育法", "23") in pairs
    # 舊實作會抓「最近的同尾型 mention」＝空氣污染防制法，而且 verify=ok，錯得看不出來
    assert ("空氣污染防制法", "23") not in pairs


def test_ben_fa_quoted_inside_a_statute_still_means_that_statute() -> None:
    """引述的是「法」本身時，本法就是它自己——這條不能被上面那條改壞。"""
    text = "按空氣污染防制法第24條規定：「公私場所違反本法第20條規定者…」"
    assert _articles(text) == [("空氣污染防制法", "24"), ("空氣污染防制法", "20")]


def test_ben_banfa_inside_its_own_quote_means_the_regulation_itself() -> None:
    """同一段引文裡的「本辦法」指那部辦法自己，不是它的母法、也不是最近提到的別部辦法。

    引文中間刻意再提一部辦法：靠「最近 mention」會答錯，靠引號的引導語才會答對。
    """
    text = (
        "環境講習執行辦法第2條規定：「依一般廢棄物回收清除處理辦法之規定辦理，"
        "本辦法第5條所稱之受處分人…」"
    )
    pairs = _articles(text)
    assert ("環境講習執行辦法", "5") in pairs
    assert ("一般廢棄物回收清除處理辦法", "5") not in pairs


def test_ben_fa_is_dropped_when_the_parent_cannot_be_read_off_the_title() -> None:
    """`移動污染源空氣污染物排放標準` 的名稱裡沒有母法 → 丟棄，不猜（CONSTITUTION §2）。"""
    text = "按廢棄物清理法第11條，移動污染源空氣污染物排放標準第3條規定：「本法第4條所稱…」"
    pairs = _articles(text)
    assert ("廢棄物清理法", "4") not in pairs  # 舊實作會掛到這裡
    assert not any(art == "4" for _, art in pairs)
    assert _unresolved(text) == 1


def test_parent_statute_only_accepts_known_law_names() -> None:
    """母法必須是**已知法名**，不能拿標題裡任何以「法」結尾的片段充數。"""
    known = frozenset({"廢棄物清理法", "環境教育法"})
    assert build_graph.parent_statute("環境教育法施行細則", known) == "環境教育法"
    # `事業廢棄物貯存清除處理方法及設施標準` 裡的「…處理方法」不是法
    assert build_graph.parent_statute("事業廢棄物貯存清除處理方法及設施標準", known) is None


# ── (g) 明示簡稱與文件內縮寫 ─────────────────────────────────────────


def test_explicit_abbreviation_definition_is_honoured() -> None:
    """`行政程序法（下稱本法）` 優先於「最近 mention」。"""
    # 定義之後再提一部別的法：靠「最近 mention」會答成廢棄物清理法，靠定義才答對。
    text = (
        "次按行政程序法（下稱本法）第72條規定，另按廢棄物清理法第11條規定，"
        "又本法第74條規定"
    )
    pairs = _articles(text)
    assert ("行政程序法", "74") in pairs
    assert ("廢棄物清理法", "74") not in pairs


def test_document_local_abbreviation_is_expanded_to_the_full_name() -> None:
    """後文只寫 `裁罰準則第2條` 時要還原成全名，否則同一部準則會裂成兩個節點。"""
    text = "依違反廢棄物清理法罰鍰額度裁罰準則第3條，又依裁罰準則第2條規定"
    pairs = _articles(text)
    assert pairs.count(("違反廢棄物清理法罰鍰額度裁罰準則", "2")) == 1
    assert not any(law == "裁罰準則" for law, _ in pairs)


def test_abbreviation_expansion_requires_a_suffix_not_a_substring() -> None:
    """只認「結尾」，否則 `廢棄物清理法` 會被吸進那部裁罰準則裡。"""
    text = "依違反廢棄物清理法罰鍰額度裁罰準則第3條，又依廢棄物清理法第11條"
    assert ("廢棄物清理法", "11") in _articles(text)


# ── (h) stats 三類分開計數 ───────────────────────────────────────────


def test_stats_counts_articles_judgments_and_interpretations_separately() -> None:
    """判解字號先前被算進 `articles`，報告直接引用就會寫錯（稽核 2026-09-12）。"""
    with tempfile.TemporaryDirectory() as raw:
        entries, kb = _tmp_corpus(pathlib.Path(raw))
        graph = build_graph.build_graph(entries, kb, LAWS, {"102年度判字第147號"})
    st = graph["stats"]
    assert st["articles"] + st["judgments"] + st["interpretations"] == len(
        [n for n in graph["nodes"] if n["layer"] == 2]
    )
    assert st["judgments"] == 1 and st["interpretations"] == 0
    assert st["article_verified"] + st["authority_verified"] == len(
        [n for n in graph["nodes"] if n["layer"] == 2 and n["verify"] == "ok"]
    )
    assert st["laws"] + st["classification_nodes"] == len(
        [n for n in graph["nodes"] if n["layer"] == 1]
    )
    assert st["links_cites"] + st["links_belongs"] == st["links"] == len(graph["links"])
    assert st["cites_to_articles"] + st["cites_to_authorities"] == st["links_cites"]


def _tmp_corpus_all_recall_classes(tmp: pathlib.Path) -> tuple[list[dict], pathlib.Path]:
    """四類「第N條」全部非零的語料——召回率的分子分母才守得住。

    2026-09-12 稽核：舊 fixture 的 other／enumeration／dropped 全是 0，
    所以「分母偷偷排除 other」能讓 rate 從 0.7995 跳到 0.8552 而測試全綠。
    """
    kb = tmp / "kb"
    (kb / "public" / "決定書").mkdir(parents=True)
    (kb / "public" / "決定書" / "1090000003_駁回.txt").write_text(
        "按廢棄物清理法第11條規定，"          # direct
        "又依同法第50條，"                   # pronoun（解得出）
        "及廢棄物清理法第12條、第14條規定，"    # 第12條=direct、第14條=列舉延續
        "環境部函示：「依本法第99條所定…」，"   # 引號內無引導語 → pronoun 且丟棄
        "至於違反第8條部分不予論究。",          # other（引文外的交互指涉）
        encoding="utf-8",
    )
    entries = [{
        "path": "kb/public/決定書/1090000003_駁回.txt",
        "provenance": "public_crawl", "case_no": "1090000003",
        "outcome": "駁回", "year": "109",
    }]
    return entries, kb


def test_stats_reports_a_real_recall_number() -> None:
    """召回率要是實測值，而且四類都非零——否則分子分母怎麼動都測不出來。"""
    with tempfile.TemporaryDirectory() as raw:
        entries, kb = _tmp_corpus_all_recall_classes(pathlib.Path(raw))
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    r = graph["stats"]["recall"]
    b = r["breakdown"]
    # 四類都必須非零，這條測試才有鑑別力
    assert b["direct"] == 2 and b["pronoun"] == 2 and b["enumeration_continuation"] == 1
    assert b["other"] == 1
    assert sum(b.values()) == r["article_refs_in_corpus"] == 6
    assert r["dropped_unresolved_pronoun"] == 1  # 「本法第99條」引號內無引導語
    assert r["article_refs_captured"] == 3  # direct 2 + pronoun 2 - 丟棄 1
    assert r["rate"] == 0.5


def test_recall_denominator_includes_every_class() -> None:
    """分母不得偷偷排除任何一類——排除 `other` 會讓 rate 從 0.5 變成 0.6。"""
    with tempfile.TemporaryDirectory() as raw:
        entries, kb = _tmp_corpus_all_recall_classes(pathlib.Path(raw))
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    r = graph["stats"]["recall"]
    for cls in ("direct", "pronoun", "enumeration_continuation", "other"):
        assert r["breakdown"][cls] > 0, f"{cls} 是 0，這條測試就失去鑑別力"
        assert r["article_refs_in_corpus"] > r["article_refs_in_corpus"] - r["breakdown"][cls]
    assert r["rate"] == round(r["article_refs_captured"] / r["article_refs_in_corpus"], 4)


def test_recall_numerator_excludes_deliberately_dropped_references() -> None:
    """分子不得把「主動放棄」的算成抓到——那會讓誠實成本變成免費的。"""
    with tempfile.TemporaryDirectory() as raw:
        entries, kb = _tmp_corpus_all_recall_classes(pathlib.Path(raw))
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    r = graph["stats"]["recall"]
    b = r["breakdown"]
    assert r["dropped_unresolved_pronoun"] > 0
    assert r["article_refs_captured"] == b["direct"] + b["pronoun"] - r["dropped_unresolved_pronoun"]
    assert r["article_refs_captured"] < b["direct"] + b["pronoun"]


def test_source_field_does_not_claim_to_have_scanned_the_whole_kb_directory() -> None:
    """`source` 先前寫 `data/local/kb/**/*.txt`，但實際只掃 manifest 列的那批。"""
    with tempfile.TemporaryDirectory() as raw:
        entries, kb = _tmp_corpus(pathlib.Path(raw))
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    assert "manifest" in graph["source"]
    assert "**/*.txt" not in graph["source"]


# ── (i) 稽核點名的缺測試 ──────────────────────────────────────────────


def test_document_with_no_citation_at_all_is_still_a_node() -> None:
    """真實資料有 4 份（全是官方判解／函釋），degree 0 但必須存在，
    而且不能被算進 `docs_with_citation`。"""
    with tempfile.TemporaryDirectory() as raw:
        kb = pathlib.Path(raw) / "kb"
        (kb / "official" / "行政函釋").mkdir(parents=True)
        (kb / "official" / "行政函釋" / "某部會函釋-寄存送達.txt").write_text(
            "本件係關於寄存送達之疑義，並無援引任何條文。", encoding="utf-8"
        )
        entries = [{
            "path": "kb/official/行政函釋/某部會函釋-寄存送達.txt",
            "provenance": "official", "outcome": None,
        }]
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    cases = [n for n in graph["nodes"] if n["layer"] == 3]
    assert len(cases) == 1 and cases[0]["w"] == 0
    assert cases[0]["prov"] == "interpretation"
    assert graph["stats"]["docs_scanned"] == 1
    assert graph["stats"]["docs_with_citation"] == 0
    assert graph["stats"]["links"] == 0
    # 分母 0 時 rate 要是 None，不是 0.0——「沒得抽」≠「全漏抽」
    assert graph["stats"]["recall"]["article_refs_in_corpus"] == 0
    assert graph["stats"]["recall"]["rate"] is None


def test_enumeration_continuation_is_a_known_gap_not_silently_wrong() -> None:
    """**已知限制、本輪刻意不做**（2026-09-12 拍板）：`A法第5條、第14條` 的第二個
    條號抓不到——那需要跨逗號延續先行詞的狀態機，風險比收益高。

    這條測試存在的意義是**把缺口釘住**：抓不到就是抓不到（少一筆），
    但**絕不可以把第二個條號掛到錯的法上**。哪天有人實作了延續，這條會紅，
    提醒他來更新這份宣告與 `stats.recall`。
    """
    pairs = _articles("依廢棄物清理法第11條、第14條規定")
    assert ("廢棄物清理法", "11") in pairs
    assert ("廢棄物清理法", "14") not in pairs  # 未支援；實作了請更新本測試與 recall
    assert not any(art == "14" for _, art in pairs)  # 但也不准掛到別部法上


def test_stats_articles_field_contains_only_real_articles() -> None:
    """`articles` 的類別純度：只能數 `art:` 節點，判解與釋字各有自己的欄位。"""
    with tempfile.TemporaryDirectory() as raw:
        entries, kb = _tmp_corpus(pathlib.Path(raw))
        graph = build_graph.build_graph(entries, kb, LAWS, {"102年度判字第147號"})
    l2 = [n for n in graph["nodes"] if n["layer"] == 2]
    assert graph["stats"]["articles"] == len([n for n in l2 if n["id"].startswith("art:")])
    assert graph["stats"]["judgments"] == len([n for n in l2 if n["id"].startswith("jud:")])
    assert graph["stats"]["interpretations"] == len([n for n in l2 if n["id"].startswith("int:")])
    assert all(n.get("article") and n.get("law") for n in l2)


def test_recall_breakdown_accounts_for_every_article_reference() -> None:
    """四類加總必須等於語料裡「第N條」的總數——對不上就代表還有一類沒被講出來。"""
    with tempfile.TemporaryDirectory() as raw:
        entries, kb = _tmp_corpus(pathlib.Path(raw))
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    r = graph["stats"]["recall"]
    assert sum(r["breakdown"].values()) == r["article_refs_in_corpus"]
    # 抓到的 = 直接引用 + (代名詞 - 主動放棄)
    assert r["article_refs_captured"] == (
        r["breakdown"].get("direct", 0)
        + r["breakdown"].get("pronoun", 0)
        - r["dropped_unresolved_pronoun"]
    )


def test_ambiguous_abbreviation_counter_exists_even_when_zero() -> None:
    """歧義簡稱（某法規名是另一個更長法規名的結尾）要**可觀測**。

    零命中的檢查在還沒出事時永遠是綠的，換一批語料就靜默失效；有欄位、有數字，
    下次資料一變它自己會說話（2026-09-12 拍板）。
    """
    with tempfile.TemporaryDirectory() as raw:
        entries, kb = _tmp_corpus(pathlib.Path(raw))
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    st = graph["stats"]
    assert st["ambiguous_abbrev_laws"] == 0  # 這份 fixture 沒有歧義簡稱
    assert st["ambiguous_abbrev_unresolved"] == 0
    assert "ambiguous_abbrev_unresolved" in st  # 欄位本身必須存在


def test_ambiguous_abbreviation_counter_actually_counts() -> None:
    """換一份**有**歧義簡稱的語料，計數器要動——否則上一條只是恆真。"""
    with tempfile.TemporaryDirectory() as raw:
        kb = pathlib.Path(raw) / "kb"
        (kb / "public" / "決定書").mkdir(parents=True)
        # 甲案定義了全名；乙案只寫縮寫、無從還原 → 乙案那筆就是歧義殘留
        (kb / "public" / "決定書" / "1090000001_駁回.txt").write_text(
            "按違反廢棄物清理法罰鍰額度裁罰準則第3條規定。", encoding="utf-8")
        (kb / "public" / "決定書" / "1090000002_駁回.txt").write_text(
            "按廢棄物清理法第11條，又依裁罰準則第3條規定。", encoding="utf-8")
        entries = [
            {"path": f"kb/public/決定書/109000000{i}_駁回.txt", "provenance": "public_crawl",
             "case_no": f"109000000{i}", "outcome": "駁回", "year": "109"} for i in (1, 2)
        ]
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    assert graph["stats"]["ambiguous_abbrev_laws"] >= 1
    assert graph["stats"]["ambiguous_abbrev_unresolved"] >= 1


# ── (j) 2026-09-12 第二輪稽核：修正引入的四個新問題 ──────────────────


def test_abbreviation_fallback_never_restores_toward_a_prose_glued_name() -> None:
    """A1：後綴回退只往「真名字」方向走。

    mention 裡本來就充滿散文黏連名，回退若只要求「某個 mention 以這個名字結尾」，
    `政府資訊公開法` 會被「還原」成 `有政府資訊公開法`（稽核實測 33 次）。
    """
    # 抽取層：後文的 `政府資訊公開法第20條` 不得被前文的黏連名「還原」成 `合政府資訊公開法`。
    # 要在抽取層測——到了 build_alias 那一層，比例規則會把黏連名併掉，兩種實作都會變綠。
    text = "查其內容合政府資訊公開法第18條所定情形，又按政府資訊公開法第20條規定"
    assert ("政府資訊公開法", "20") in _articles(text)

    # 整條管線（含 build_alias）：黏連變體必須併回去，不能反過來把全名吸成黏連名
    with tempfile.TemporaryDirectory() as raw:
        kb = pathlib.Path(raw) / "kb"
        (kb / "public" / "決定書").mkdir(parents=True)
        for i, body in enumerate((
            "按政府資訊公開法第18條規定辦理。",
            "按政府資訊公開法第18條規定，其情形不符。",
            "查其內容合政府資訊公開法第18條所定情形，且有政府資訊公開法第20條規定。",
        ), 1):
            (kb / "public" / "決定書" / f"10900000{i}_駁回.txt").write_text(body, encoding="utf-8")
        entries = [{"path": f"kb/public/決定書/10900000{i}_駁回.txt", "provenance": "public_crawl",
                    "case_no": f"10900000{i}", "outcome": "駁回", "year": "109"} for i in (1, 2, 3)]
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    labels = {n["label"] for n in graph["nodes"] if n["layer"] == 1}
    assert labels == {"政府資訊公開法"}, f"朝垃圾方向還原了：{labels}"


def test_abbreviation_fallback_still_restores_a_real_abbreviation() -> None:
    """A1 的閘不能把真縮寫一起擋掉：多出來的前綴裡有法名就是真名字。"""
    text = "按違反廢棄物清理法罰鍰額度裁罰準則第3條，又依裁罰準則第2條"
    pairs = _articles(text)
    assert ("違反廢棄物清理法罰鍰額度裁罰準則", "2") in pairs
    assert not any(law == "裁罰準則" for law, _ in pairs)


def test_prefix_names_a_statute_is_the_discriminator() -> None:
    lex = frozenset({"廢棄物清理法", "政府資訊公開法"})
    assert build_graph.prefix_names_a_statute("違反廢棄物清理法罰鍰額度裁罰準則", "裁罰準則", lex)
    assert not build_graph.prefix_names_a_statute("有政府資訊公開法", "政府資訊公開法", lex)


def test_prose_variants_of_a_restored_abbreviation_do_not_become_fake_nodes() -> None:
    """A2：簡稱被還原後，它的黏連變體會失去合併目標、變成獨立假法規節點。

    `資公法` → `政府資訊公開法` 之後，`有資公法`／`不論資公法` 必須跟著併過去。
    """
    with tempfile.TemporaryDirectory() as raw:
        kb = pathlib.Path(raw) / "kb"
        (kb / "public" / "決定書").mkdir(parents=True)
        # 語料實際寫法：先定義簡稱，後文才單獨使用它（含黏連變體）
        (kb / "public" / "決定書" / "1090000004_駁回.txt").write_text(
            "按政府資訊公開法（下稱資公法），資公法第18條規定，"
            "惟有資公法第20條所定情形，又不論資公法第21條規定。",
            encoding="utf-8")
        entries = [{"path": "kb/public/決定書/1090000004_駁回.txt", "provenance": "public_crawl",
                    "case_no": "1090000004", "outcome": "駁回", "year": "109"}]
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    labels = {n["label"] for n in graph["nodes"] if n["layer"] == 1}
    assert labels == {"政府資訊公開法"}, f"冒出假法規節點：{labels}"


def test_pronoun_inside_a_quote_with_no_identifiable_introducer_is_dropped() -> None:
    """A3：引號內、引導語辨識不出 → 丟棄並計數，**不退回最近 mention**。

    舊行為會拿引號外最近的法名去填，那是系統性猜錯（稽核量出根因池 382 筆）。
    """
    text = "按空氣污染防制法第24條規定。環境部113年函示：「依本法第99條所定情形…」"
    pairs = _articles(text)
    assert ("空氣污染防制法", "99") not in pairs  # 舊行為會掛到這裡
    assert not any(art == "99" for _, art in pairs)
    assert _unresolved(text) == 1


def test_weifan_is_only_kept_when_a_statute_follows_it() -> None:
    """A4：`違反` 豁免要有條件，否則 `訴願人違反○○辦法` 會留下假法規名。"""
    assert build_graph.names_a_statute_after_weifan(
        "違反廢棄物清理法罰鍰額度裁罰準則", frozenset({"廢棄物清理法"}))
    assert not build_graph.names_a_statute_after_weifan(
        "訴願人違反管理辦法", frozenset({"廢棄物清理法"}))
    # 違反後面是法名但字串到此為止 → 是動詞，不是名稱的一部分
    assert not build_graph.names_a_statute_after_weifan(
        "訴願人違反廢棄物清理法", frozenset({"廢棄物清理法"}))


def test_verb_weifan_before_a_generic_tail_does_not_create_a_law() -> None:
    """`違反` 後面沒有法名時必須照切，否則會長出 `訴願人違反管理辦法` 這種假法規——
    而且它還會被當成後文「同辦法」的先行詞（稽核實測被用了 3 次）。"""
    text = "按廢棄物清理法第11條，依訴願人違反管理辦法第5條規定"
    laws = [law for law, _ in _articles(text)]
    assert not any("違反" in law for law in laws), laws
    assert "訴願人違反管理辦法" not in laws


def test_real_penalty_guidelines_keep_their_full_name() -> None:
    """A4 的條件化不能把真的準則名字切掉——這三個是語料裡被引最多的下位法規。"""
    for text, want in (
        ("依違反廢棄物清理法罰鍰額度裁罰準則第3條", "違反廢棄物清理法罰鍰額度裁罰準則"),
        ("及移動污染源違反空氣污染防制法裁罰準則第7條", "移動污染源違反空氣污染防制法裁罰準則"),
        ("按空氣污染行為管制執行準則第6條", "空氣污染行為管制執行準則"),
    ):
        assert _articles(text)[0][0] == want


def test_pronoun_resolution_paths_are_reported_and_sum_to_unresolved() -> None:
    """代名詞怎麼解的要可稽核：各 `*_dropped` 加總必須等於 `unresolved_pronoun`。

    這個欄位存在的理由：「引述下位法規卻寫本法」有多少筆，先前是靠各自寫腳本數，
    兩邊定義不同就得到不同答案（我 1,060、稽核 1,034）。做進 stats 就只有一個數字。
    """
    with tempfile.TemporaryDirectory() as raw:
        entries, kb = _tmp_corpus_all_recall_classes(pathlib.Path(raw))
        graph = build_graph.build_graph(entries, kb, LAWS, set())
    pr = graph["stats"]["pronoun_resolution"]
    assert sum(v for k, v in pr.items() if k.endswith("_dropped")) == graph["stats"]["unresolved_pronoun"]
    assert pr["quote_no_introducer_dropped"] == 1
