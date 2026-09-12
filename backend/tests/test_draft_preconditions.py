"""生成草稿的前置條件與「手動挑的法規」誠實回報（US-C1／US-C2）。

契約：`docs/handoff/2026-09-12-frontend-contract-v2.md` §3.5.1、§3.5.2
規格：`.prospec/changes/draft-and-export/proposal.md` US-C1（C1.1–C1.4）、US-C2

## 這支跟 `test_chat.py` 的分工（**刻意不重複**）

Epic A 已經在 `test_chat.py` 用 `_FakePipeline` 釘住工具層的擋門與
`overrides.n4_query` 的 join。**這支補的是那批假不出來的三件事：**

1. **真的 `load_case_manifest` 讀真的 `manifest.json`**——Epic A 是直接把
   `case_manifest` dict 注進建構子，等於跳過整個讀檔路徑。檔案讀不到會回 `{}`，
   而 `{}` 會讓草稿被擋下：**這條路徑壞掉的樣子是「功能永遠不可用」**，值得實跑一次。
2. **真的六節點流水線**（fixture 檔位）跑 `from_node="n4"`，不是假 adapter。
   假貨長得跟真貨不一樣的話，測試綠了也不代表接得上。
3. **契約 §3.5.2 末段：挑的法規這一輪走到哪**（三態；2026-09-13 Ci 由兩態改判）。
   這是我這一輪新加的（`chat.classify_picks`，2026-09-13 由兩態改三態），Epic A 沒有做。

## 這支最在意的那一條

**「挑了五條、一條都沒引」不得靜默發生，但也不得報成「完全沒用到」。**
契約把手動挑的法規當**查詢詞**餵回 N4，而查表只認「法名第N條」——純法規名對
通道 A 必然零命中、對通道 B 卻確實生效。舊的兩態把後者報成「檢索未命中」，
**那是一句假話，只是假在誠實的方向，所以一直沒人抓到**。

反過來也是紅線：**不得為了讓警語消失，就把挑的法規直接塞進 `laws[]`**。
那會回到「檢索佐證的是自己」，正是 2026-09-05 改成 N4 獨立檢索要擋掉的事。
`test_unused_law_is_not_smuggled_into_the_retrieved_list` 釘的就是這條。

## manifest fixture 不是規格

底下的 `_MANIFEST` 是**測試資料**，形狀照契約 §4.0 抄。
**寫入方是 Epic B（case-dossier-crud），規格以契約為準，不以這份 fixture 為準。**
兩邊對不上要回報契約擁有者，不是誰去遷就誰（CONSTITUTION §9）。
"""
from __future__ import annotations

import json
import pathlib
import tempfile
from typing import Any

import backend.llm.chat as chat_mod
from backend.config.settings import load_snapshot
from backend.dossier import runlink
from backend.llm.chat import (
    PICK_MATCHED,
    PICK_NOTES,
    PICK_QUERY_ONLY,
    PICK_UNUSED,
    ChatTools,
    RefBook,
    classify_picks,
)
from backend.retrieval.lawtable import LawTableRetriever
from backend.orchestrator.chat_bridge import load_case_manifest, pipeline_adapter
from backend.orchestrator.graph import run_case
from backend.tests.harness import TestFailure

ORDINARY = "synthetic-ordinary-01"

#: 契約 §4.0 的鍵。**少一個就是 fixture 漂了**，由 test_manifest_fixture_* 釘住。
MANIFEST_KEYS = ("case_id", "name", "latest_run_id", "files", "laws", "references", "artifacts")

#: 測試資料，不是規格（見本檔抬頭）。
#: `laws[].t` 是 KB 文件標題＝檔名去副檔名（`retrieval/kb.py:558`），所以是法規名。
_MANIFEST: dict[str, Any] = {
    "case_id": ORDINARY,
    "name": "（合成）露天燃燒案",
    "latest_run_id": None,  # 由測試填入真的 run id
    "files": [{"id": "f1", "name": "synthetic-原處分裁處書.pdf", "ext": "pdf",
               "note": "", "readable": True}],
    "laws": [
        {"id": "kb/public/相關法規_全量/訴願法.txt", "t": "訴願法",
         "src": "kb/public/相關法規_全量/訴願法.txt", "note": "",
         "verified": False, "relevance": "unknown", "body_cached": ""},
        {"id": "kb/public/相關法規_全量/水污染防治法.txt", "t": "水污染防治法",
         "src": "kb/public/相關法規_全量/水污染防治法.txt", "note": "",
         "verified": False, "relevance": "unknown", "body_cached": ""},
    ],
    "references": [
        {"id": "kb/public/新北訴願決定書_環保局全量/1151090848_駁回.txt",
         "t": "1151090848_駁回", "src": "kb/public/新北訴願決定書_環保局全量/1151090848_駁回.txt",
         "note": "", "score": 0.71, "provenance": "public_crawl",
         "doc_kind": "decision", "full_cached": ""},
    ],
    "artifacts": [],
}


def _fail(msg: str) -> None:
    raise TestFailure(msg)


def _write_manifest(root: pathlib.Path, manifest: dict[str, Any],
                    case_id: str = ORDINARY) -> pathlib.Path:
    d = root / case_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "manifest.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def _tools(events: list, *, payload=None, run_pipeline=None, run_id=None,
           case_manifest=None) -> ChatTools:
    """真的節流會讓測試睡好幾秒，換成計數器（沿用 test_chat.py 的做法）。"""
    chat_mod._throttle = lambda: None
    return ChatTools(
        payload if payload is not None else {}, RefBook(), None, None,
        lambda name, data: events.append((name, data)),
        run_pipeline=run_pipeline, run_id=run_id, case_manifest=case_manifest,
    )


def _result_of(events: list, name: str = "generate_decision_draft") -> dict[str, Any]:
    hits = [d for n, d in events if n == "tool_result" and d.get("tool") == name]
    if not hits:
        hits = [d for n, d in events if n == "tool_result"]
    if not hits:
        _fail(f"沒有收到 {name} 的 tool_result 事件")
    return hits[-1]


# ── manifest fixture 與真的讀檔路徑 ────────────────────────────────
def test_manifest_fixture_has_every_key_the_contract_lists() -> None:
    """fixture 漂掉了要當場紅，不要等到 Epic B 接上才發現兩邊形狀不一樣。"""
    missing = [k for k in MANIFEST_KEYS if k not in _MANIFEST]
    if missing:
        _fail(f"fixture 少了契約 §4.0 的鍵：{missing}")


def test_load_case_manifest_reads_a_real_file() -> None:
    """Epic A 是把 dict 直接注進建構子，整段讀檔路徑沒有被跑過。"""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        _write_manifest(root, _MANIFEST)
        got = load_case_manifest(ORDINARY, cases_dir=root)
    if got.get("case_id") != ORDINARY:
        _fail(f"讀回來的 manifest 不對：{got!r}")
    if len(got.get("laws") or []) != 2:
        _fail("laws 沒有讀進來")


def test_missing_or_broken_manifest_reads_as_empty_not_as_a_crash() -> None:
    """讀不到＝空清單＝草稿被擋下。**這個方向是刻意的**：寧可擋下來要人先查法規，
    也不要生一份通篇引用被清空的草稿。壞掉的 JSON 也當空——半份清單比沒有清單更難查。"""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        if load_case_manifest(ORDINARY, cases_dir=root) != {}:
            _fail("manifest 不存在時應回 {}")
        d = root / ORDINARY
        d.mkdir(parents=True)
        (d / "manifest.json").write_text("{壞掉的 json", encoding="utf-8")
        if load_case_manifest(ORDINARY, cases_dir=root) != {}:
            _fail("壞掉的 manifest 應回 {}，不得半讀半信")


def test_draft_is_blocked_through_the_real_manifest_path() -> None:
    """C1.3 走真的讀檔路徑：manifest 不存在 → 前置 3 擋下 → **不進 pipeline**。

    Epic A 用假 dict 驗過同一條判斷，這裡驗的是「讀檔回空」真的會接到那條判斷上。
    """
    with tempfile.TemporaryDirectory() as td:
        manifest = load_case_manifest(ORDINARY, cases_dir=pathlib.Path(td))
    ran: list = []
    events: list = []
    t = _tools(events, payload={"screen": {"x": 1}}, run_id="run-old",
               case_manifest=manifest,
               run_pipeline=lambda **kw: ran.append(kw) or {})
    out = t.generate_decision_draft()
    if ran:
        _fail("前置沒過卻跑了流水線——使用者會白等 72 秒才看到紅燈")
    r = _result_of(events)
    if r.get("status") != "failed" or "還沒有查過法規與相似案例" not in (r.get("note") or ""):
        _fail(f"應回 failed 並說明缺什麼，實得 {r!r}")
    if "不要生一份沒有依據的草稿" not in out:
        _fail("給模型的回覆沒有擋住它自己補一份草稿")


def test_draft_is_blocked_when_the_case_was_never_extracted() -> None:
    """C1.2：**不得自己先偷跑 n1**——那會讓使用者以為草稿是憑空生出來的。"""
    ran: list = []
    events: list = []
    t = _tools(events, payload={}, run_id=None, case_manifest=_MANIFEST,
               run_pipeline=lambda **kw: ran.append(kw) or {})
    out = t.generate_decision_draft()
    if ran:
        _fail("沒解析過卷證卻跑了流水線")
    if _result_of(events).get("status") != "failed":
        _fail("應回 failed")
    if "不要代為執行" not in out:
        _fail("給模型的回覆沒有擋住它代為執行解析")


# ── §3.5.2 末段：挑的法規走到哪，三態（2026-09-13 Ci 改判）──────────
#
# 舊版只有 hit／miss，而那個 miss 是**一句假話**：純法規名對法條查表必然零命中
# （查表只認「法名第N條」），但那個詞確實進了相似案檢索。說它「未命中、未進入草稿」
# 把「有被用到」講成「沒被用到」——假在誠實的方向，所以一直沒人抓到。
def test_channel_a_really_cannot_hit_a_bare_statute_name() -> None:
    """**這條是整個三態的根因，用真的 `LawTableRetriever` 驗，不是 mock。**

    這件事若不成立，三態就沒有存在的必要；而它只在真的查表器上成立
    ——mock 一個「查什麼都回 0」的假貨，驗到的是自己寫的假設不是系統的行為。
    """
    r = LawTableRetriever(load_snapshot())
    for bare in ("廢棄物清理法", "訴願法", "空氣污染防制法"):
        if r.search(bare):
            _fail(f"前提不成立：查表對純法規名 {bare!r} 竟然有命中，三態要重新設計")
    for full in ("廢棄物清理法第2條", "訴願法第14條"):
        if not r.search(full):
            _fail(f"前提不成立：查表對 {full!r} 沒有命中，比對鍵要重新看")


def test_pick_that_reached_the_lawtable_is_matched() -> None:
    got = classify_picks([{"id": "a", "t": "訴願法"}],
                         [{"id": "L1", "t": "訴願法第14條", "law": "訴願法"}],
                         "訴願法")
    if got[0]["state"] != PICK_MATCHED:
        _fail(f"進了法條查表卻沒判成 matched：{got}")


def test_pick_that_only_went_to_the_similar_case_channel_is_not_called_a_miss() -> None:
    """**這條是本次改判的核心。**

    詞有送出（`query_terms` 收得到）、但法條查表沒有它 → `query_only`，**不是 miss**。
    報 miss 等於告訴使用者「你挑的東西沒被用到」，而那是假的。
    """
    got = classify_picks([{"id": "a", "t": "水污染防治法"}],
                         [{"id": "L1", "t": "訴願法第14條", "law": "訴願法"}],
                         "水污染防治法；訴願法")
    if got[0]["state"] != PICK_QUERY_ONLY:
        _fail(f"有送進通道 B 卻被判成 {got[0]['state']}：{got}")
    note = got[0]["note"]
    if "未命中" in note:
        _fail(f"舊文案殘留：{note!r}")
    for must in ("相似案檢索", "第N條"):
        if must not in note:
            _fail(f"文案沒講清楚它被用在哪／下一步怎麼做（缺 {must!r}）：{note!r}")


def test_pick_that_was_never_sent_anywhere_is_unused() -> None:
    """兩邊都沒有才是真的沒用到。沒有 `query_terms` 依據時也不得硬說「用於相似案檢索」
    ——那會變成一句沒有根據的安慰。"""
    got = classify_picks([{"id": "a", "t": "水污染防治法"}],
                         [{"id": "L1", "t": "訴願法第14條", "law": "訴願法"}],
                         "訴願法")
    if got[0]["state"] != PICK_UNUSED:
        _fail(f"沒送出也沒命中，應為 unused，實得 {got[0]['state']}")
    if classify_picks([{"id": "a", "t": "水污染防治法"}], [], None)[0]["state"] != PICK_UNUSED:
        _fail("沒有 query_terms 依據時不得宣稱進了通道 B")


def test_every_pick_is_accounted_for() -> None:
    """三態要回**每一筆**，不是只回沒中的。只回沒中的話，畫面無從分辨
    「這條進了查表」與「這條我根本沒算」。"""
    picked = [{"id": "a", "t": "訴願法"}, {"id": "b", "t": "水污染防治法"},
              {"id": "c", "t": "冷門法規"}]
    got = classify_picks(picked, [{"id": "L1", "law": "訴願法"}], "訴願法；水污染防治法")
    if [g["id"] for g in got] != ["a", "b", "c"]:
        _fail(f"沒有逐筆回報：{got}")
    if [g["state"] for g in got] != [PICK_MATCHED, PICK_QUERY_ONLY, PICK_UNUSED]:
        _fail(f"三態判錯：{[(g['id'], g['state']) for g in got]}")


def test_classify_falls_back_to_title_when_law_field_is_absent() -> None:
    """KB 來源的法規沒有 `law` 欄位，退回比 `t`。"""
    got = classify_picks([{"id": "a", "t": "廢棄物清理法"}], [{"id": "L1", "t": "廢棄物清理法"}])
    if got[0]["state"] != PICK_MATCHED:
        _fail("`law` 缺席時沒有退回比 `t`")


def test_classify_normalises_spaces_and_txt_suffix() -> None:
    """全形空白在 KB 檔名裡真的會出現；不一起抹掉會把同一部法規判成兩部。"""
    got = classify_picks([{"id": "a", "t": "廢棄物　清理法.txt"}],
                         [{"id": "L1", "law": "廢 棄物清理法"}])
    if got[0]["state"] != PICK_MATCHED:
        _fail("空白／副檔名的差異被當成不同法規")


def test_classify_does_not_do_substring_matching() -> None:
    """「包含」比對會讓「訴願法施行細則」被「訴願法」吃掉，於是**任何**挑選看起來
    都命中了——那會把一個誠實問題變成一個永遠成立的問題。查詢詞那側同理。"""
    got = classify_picks([{"id": "a", "t": "訴願法施行細則"}],
                         [{"id": "L1", "t": "訴願法第14條", "law": "訴願法"}], "訴願法")
    if got[0]["state"] == PICK_MATCHED:
        _fail("「訴願法施行細則」被「訴願法」模糊比對吃掉了")
    if got[0]["state"] == PICK_QUERY_ONLY:
        _fail("查詢詞那側也被子字串比對吃掉了")
    got2 = classify_picks([{"id": "a", "t": "訴願法"}],
                          [{"id": "L1", "law": "訴願法施行細則"}], "訴願法施行細則")
    if got2[0]["state"] == PICK_MATCHED:
        _fail("反向的包含關係也被吃掉了")


def test_classify_ignores_entries_without_a_name() -> None:
    """名字是空的是 manifest 那筆資料壞了，不是檢索的事。
    混在一起報，承辦人會去查一個根本不存在的檢索問題。"""
    if classify_picks([{"id": "a", "t": ""}, {"id": "b"}], []):
        _fail("沒有名字的項目不該被列進檢索狀態")


def test_classify_is_empty_when_nothing_was_picked() -> None:
    if classify_picks([], [{"id": "L1", "law": "訴願法"}], "訴願法"):
        _fail("沒挑東西就不該有任何狀態")


def test_no_stale_miss_wording_anywhere() -> None:
    """舊文案不得殘留（team-lead 驗收條件）。掃的是**實際會顯示給使用者的字串**
    （`PICK_NOTES` 與 runlink 的三個常數），不是 grep 原始碼——
    註解裡解釋「為什麼舊文案是錯的」不該被判成殘留。"""
    shown = list(PICK_NOTES.values()) + [runlink.NOTE_HIT, runlink.NOTE_QUERY_ONLY,
                                         runlink.NOTE_MISS]
    for s in shown:
        if "未命中" in s:
            _fail(f"舊文案殘留在會顯示給使用者的字串裡：{s!r}")


# ── §3.5.2 走真的六節點流水線（fixture 檔位）────────────────────────
def _real_pipeline_tools(events: list, manifest: dict[str, Any]):
    """真的 adapter + 真的 run_case（fixture 檔位，零雲端呼叫）。"""
    base = run_case(ORDINARY, mode="fixture", persist=True)
    return _tools(events, payload={"screen": {"x": 1}}, run_id=base.run_id,
                  case_manifest=manifest,
                  run_pipeline=pipeline_adapter(ORDINARY, mode="fixture", persist=True))


def test_picked_law_states_are_reported_through_the_real_pipeline() -> None:
    """契約 §3.5.2 末段，走真流水線。

    `水污染防治法` 跟這個案子（空污、露天燃燒）無關，法條查表查不到——
    但它**確實**被送進了相似案檢索，所以是 `query_only` 不是 miss。
    """
    events: list = []
    t = _real_pipeline_tools(events, _MANIFEST)
    out = t.generate_decision_draft()
    r = _result_of(events)
    if r.get("status") != "ok":
        _fail(f"前置都成立卻沒生出草稿：{r!r}")
    by_name = {p.get("t"): p for p in (r.get("picked_laws") or [])}
    if "水污染防治法" not in by_name:
        _fail(f"挑的法規沒有逐筆回報：picked_laws={r.get('picked_laws')!r}")
    if by_name["水污染防治法"]["state"] != PICK_QUERY_ONLY:
        _fail(f"它有被送進相似案檢索，卻判成 {by_name['水污染防治法']['state']}")
    if by_name["訴願法"]["state"] != PICK_MATCHED:
        _fail(f"訴願法在查表結果裡，卻判成 {by_name['訴願法']['state']}")
    if "水污染防治法" not in out:
        _fail("給模型的回覆沒有提到它，模型會說得像全部都引用了")
    if "不要說它們已被引用" not in out or "不要說它們完全沒被用到" not in out:
        _fail("給模型的回覆沒有同時擋住兩個方向的謊")
    if "未命中" in out:
        _fail(f"舊文案殘留在給模型的回覆裡：{out!r}")


def test_unused_law_is_not_smuggled_into_the_retrieved_list() -> None:
    """**誠實紅線。** 不得為了讓「未命中」不出現，就把挑的法規塞進 `laws[]`——
    那會回到「檢索佐證的是自己」，正是 2026-09-05 改成 N4 獨立檢索要擋掉的事。
    """
    events: list = []
    t = _real_pipeline_tools(events, _MANIFEST)
    t.generate_decision_draft()
    retrieved = [str(l.get("law") or l.get("t") or "") for l in (t.case_payload.get("laws") or [])]
    if any("水污染防治法" in name for name in retrieved):
        _fail(f"未命中的法規被塞進 laws[] 了：{retrieved}")


def test_all_picked_laws_hit_means_no_warning() -> None:
    """全部命中時不得冒出一條空的警示——狼來了會讓真的未命中被忽略。"""
    events: list = []
    manifest = dict(_MANIFEST)
    manifest["laws"] = [{"id": "x", "t": "訴願法"}, {"id": "y", "t": "行政程序法"}]
    t = _real_pipeline_tools(events, manifest)
    out = t.generate_decision_draft()
    r = _result_of(events)
    bad = [p for p in (r.get("picked_laws") or []) if p["state"] != PICK_MATCHED]
    if bad:
        _fail(f"兩條都該進法條查表，卻判成 {[(b['t'], b['state']) for b in bad]}")
    if "沒有進入法條查表" in out:
        _fail("兩條都命中，卻還是對模型講了那段警語——狼來了")


def test_picked_laws_are_fed_back_as_a_single_joined_query_string() -> None:
    """C2 的 join：`n4_query` 是**單一字串不是陣列**。傳成 list 不會當場炸，
    只會讓查詢句變成一個 repr——錯得很安靜。"""
    seen: list = []
    events: list = []

    def spy(**kw):
        seen.append(kw)
        return {"run_id": "run-new", "state": "VERIFIED", "cite_count": 3,
                "artifact_id": None, "has_draft": True, "sections": {"laws": []}}

    t = _tools(events, payload={"screen": {"x": 1}}, run_id="run-old",
               case_manifest=_MANIFEST, run_pipeline=spy)
    t.generate_decision_draft()
    q = (seen[0].get("overrides") or {}).get("n4_query")
    if not isinstance(q, str):
        _fail(f"n4_query 必須是單一字串，實得 {type(q).__name__}：{q!r}")
    if q != "訴願法；水污染防治法":
        _fail(f"join 的結果不對：{q!r}")
    if seen[0].get("from_node") != "n4" or seen[0].get("base_run_id") != "run-old":
        _fail(f"應接在已解析的 run 上從 n4 續跑，實得 {seen[0]!r}")
