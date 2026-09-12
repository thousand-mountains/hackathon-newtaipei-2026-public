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
3. **契約 §3.5.2 末段的「檢索未命中，未進入草稿」**——挑了但 N4 沒查到的法規。
   這是我這一輪新加的（`chat.unmatched_picks`），Epic A 沒有做。

## 這支最在意的那一條

**「挑了五條、一條都沒引」不得靜默發生。** 契約把手動挑的法規當**查詢詞**餵回 N4，
所以「挑了」不等於「會進草稿」——這是誠實不是 bug，但畫面要說得出來。

反過來也是紅線：**不得為了讓「未命中」不出現，就把挑的法規直接塞進 `laws[]`**。
那會回到「檢索佐證的是自己」，正是 2026-09-05 改成 N4 獨立檢索要擋掉的事。
`test_unmatched_law_is_not_smuggled_into_the_retrieved_list` 釘的就是這條。

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
from backend.llm.chat import UNMATCHED_LAW_NOTE, ChatTools, RefBook, unmatched_picks
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


# ── §3.5.2 末段：挑了但沒查到（純函式）──────────────────────────────
def test_unmatched_picks_matches_on_statute_name() -> None:
    picked = [{"id": "a", "t": "訴願法"}, {"id": "b", "t": "水污染防治法"}]
    retrieved = [{"id": "L1", "t": "訴願法第14條", "law": "訴願法"}]
    got = unmatched_picks(picked, retrieved)
    if [u["id"] for u in got] != ["b"]:
        _fail(f"應只報「水污染防治法」未命中，實得 {got}")
    if got[0]["note"] != UNMATCHED_LAW_NOTE:
        _fail(f"note 字樣不是契約寫的那句：{got[0]['note']!r}")


def test_unmatched_picks_falls_back_to_title_when_law_field_is_absent() -> None:
    """KB 來源的法規沒有 `law` 欄位，退回比 `t`。"""
    picked = [{"id": "a", "t": "廢棄物清理法"}]
    if unmatched_picks(picked, [{"id": "L1", "t": "廢棄物清理法"}]):
        _fail("`law` 缺席時沒有退回比 `t`")


def test_unmatched_picks_normalises_spaces_and_txt_suffix() -> None:
    """全形空白在 KB 檔名裡真的會出現；不一起抹掉會把同一部法規判成兩部。"""
    picked = [{"id": "a", "t": "廢棄物　清理法.txt"}]
    if unmatched_picks(picked, [{"id": "L1", "law": "廢 棄物清理法"}]):
        _fail("空白／副檔名的差異被當成不同法規")


def test_unmatched_picks_does_not_do_substring_matching() -> None:
    """**這條是全檔最重要的一條。**

    「包含」比對會讓「訴願法施行細則」被「訴願法」吃掉，於是**任何**挑選看起來
    都命中了——那會把一個誠實問題變成一個永遠成立的問題。
    寧可報未命中讓人自己看，也不要用相似度湊出一個「有命中」。
    """
    got = unmatched_picks([{"id": "a", "t": "訴願法施行細則"}],
                          [{"id": "L1", "t": "訴願法第14條", "law": "訴願法"}])
    if not got:
        _fail("「訴願法施行細則」被「訴願法」模糊比對吃掉了")
    got2 = unmatched_picks([{"id": "a", "t": "訴願法"}],
                           [{"id": "L1", "t": "訴願法施行細則第2條", "law": "訴願法施行細則"}])
    if not got2:
        _fail("反向的包含關係也被吃掉了")


def test_unmatched_picks_ignores_entries_without_a_name() -> None:
    """名字是空的是 manifest 那筆資料壞了，不是檢索沒命中。
    混在一起報，承辦人會去查一個根本不存在的檢索問題。"""
    if unmatched_picks([{"id": "a", "t": ""}, {"id": "b"}], []):
        _fail("沒有名字的項目不該被報成「檢索未命中」")


def test_unmatched_picks_is_empty_when_nothing_was_picked() -> None:
    if unmatched_picks([], [{"id": "L1", "law": "訴願法"}]):
        _fail("沒挑東西就不該有未命中")


# ── §3.5.2 走真的六節點流水線（fixture 檔位）────────────────────────
def _real_pipeline_tools(events: list, manifest: dict[str, Any]):
    """真的 adapter + 真的 run_case（fixture 檔位，零雲端呼叫）。"""
    base = run_case(ORDINARY, mode="fixture", persist=True)
    return _tools(events, payload={"screen": {"x": 1}}, run_id=base.run_id,
                  case_manifest=manifest,
                  run_pipeline=pipeline_adapter(ORDINARY, mode="fixture", persist=True))


def test_picked_law_that_retrieval_never_hit_is_reported_not_swallowed() -> None:
    """契約 §3.5.2 末段，走真流水線。

    `水污染防治法` 跟這個案子（空污、露天燃燒）無關，N4 查不到——
    承辦人挑了它卻一條都沒引，畫面要說得出原因。
    """
    events: list = []
    t = _real_pipeline_tools(events, _MANIFEST)
    out = t.generate_decision_draft()
    r = _result_of(events)
    if r.get("status") != "ok":
        _fail(f"前置都成立卻沒生出草稿：{r!r}")
    names = [u.get("t") for u in (r.get("unmatched_laws") or [])]
    if "水污染防治法" not in names:
        _fail(f"挑了卻沒命中的法規沒有被回報：unmatched_laws={r.get('unmatched_laws')!r}")
    if "訴願法" in names:
        _fail("N4 明明查到了訴願法，卻被報成未命中")
    if "水污染防治法" not in out:
        _fail("給模型的回覆沒有提到未命中，模型會說得像全部都引用了")
    if "不要說它們已被引用" not in out:
        _fail("給模型的回覆沒有擋住它把未命中說成已引用")


def test_unmatched_law_is_not_smuggled_into_the_retrieved_list() -> None:
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
    if r.get("unmatched_laws"):
        _fail(f"兩條都該命中，卻報了未命中：{r['unmatched_laws']}")
    if "檢索沒有命中" in out:
        _fail("沒有未命中卻對模型講了未命中")


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
