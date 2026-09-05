"""守門加固的對抗測試（stdlib only）。

這一份只放**對抗輸入**：每一條都對應第二輪 fresh-context 覆核實際打穿守門的破口，
或是修法後新引入的風險面。與 `test_nodes.py` 的分工：那邊驗「功能正常時的行為」，
這邊驗「有人故意繞的時候擋不擋得住」。

寫這份的紀律（不然它會退化成「補片語清單」的第二現場）：
1. 每條測試先寫出它在防**哪一種結構性繞法**，不是「哪一個字串」。
2. 國字數字用 property test（1..1200 兩種寫法 round-trip、隨機串差分比對），
   不是列幾個例子——列例子正是上一輪只修好被測過那幾個的原因。
3. 端到端案例要斷言 `submit_allowed`，不能只斷言中間欄位。
"""
from __future__ import annotations

import json
import pathlib
import random
import tempfile
from typing import Any

from backend.config.settings import load_snapshot
from backend.gate.citations import (
    STATE_MISSING,
    STATE_OK,
    STATE_OUT_OF_SCOPE,
    STATE_UNPARSEABLE,
    CitationChecker,
)
from backend.gate.lamps import detect_conclusion_like, normalize_for_structure
from backend.nodes import n6_gate
from backend.orchestrator.graph import build_payload, load_case, run_case
from backend.orchestrator.state import CaseState, NodeCtx
from backend.retrieval.lawtable import MAX_ARTICLE_VALUE, cn_to_int
from backend.tests.harness import assert_eq, assert_in, assert_true

SNAPSHOT = load_snapshot()
ORDINARY = "synthetic-ordinary-01"
BLOCKED = "synthetic-blocked-01"


def _ctx() -> NodeCtx:
    return NodeCtx(run_mode="fixture", snapshot=SNAPSHOT)


def _run_with_injected(base_case: str, slot: str, sentences: list[dict[str, Any]]) -> dict[str, Any]:
    """把對抗句注入某個合成案例的草稿槽位，端到端跑完六節點，回 payload。"""
    fx = load_case(base_case)
    fx["draft_fixture"].setdefault(slot, [])
    for s in sentences:
        fx["draft_fixture"][slot].append(
            {
                "cite_ids": [],
                "basis": None,
                "source_kind": "law",
                "adversarial": True,
                "adversarial_note": "對抗測試注入，非真實決定書內容。",
                **s,
            }
        )
    tmp = pathlib.Path(tempfile.mkdtemp())
    case_id = "synthetic-adversarial-01"
    (tmp / f"{case_id}.json").write_text(json.dumps(fx, ensure_ascii=False), encoding="utf-8")
    return build_payload(run_case(case_id, mode="fixture", data_dir=tmp))


# ════════════════════════════════════════════════════════════════════
# P0-1 國字數字：雙體系解析 + 「解析不出就 None」
# ════════════════════════════════════════════════════════════════════
# 結構性規則：中文數字有兩套互斥書寫體系（單位式／數字串式），判別鍵是有無單位字；
# 混用或違反文法一律 None。舊版用單一掃描規則硬吃，「九九九」讀成 9。

def _write_unit_style(n: int) -> str:
    """把整數寫成單位式國字（九百九十九）。測試自備的參考寫法產生器。"""
    if n <= 0 or n > 9999:
        raise ValueError(n)
    digits = "零一二三四五六七八九"
    units = ["", "十", "百", "千"]
    s = str(n)
    out = []
    zero_pending = False
    for idx, ch in enumerate(s):
        d = int(ch)
        place = len(s) - idx - 1
        if d == 0:
            zero_pending = True
            continue
        if zero_pending and out:
            out.append("〇")
        zero_pending = False
        if d == 1 and place == 1 and not out:
            out.append("十")  # 十四，不寫「一十四」
        else:
            out.append(digits[d] + units[place])
    return "".join(out)


def _write_digit_string(n: int) -> str:
    """把整數寫成數字串式國字（九九九、一〇五）。"""
    digits = "〇一二三四五六七八九"
    return "".join(digits[int(c)] for c in str(n))


def _reference_read(s: str) -> int | None:
    """**獨立實作**的國字讀法（遞迴切分，寫法刻意跟 cn_to_int 的線性掃描不同）。

    用途：差分測試。兩個結構不同的實作對同一串字給出不同答案，就代表至少一邊有歧義，
    那種字串本來就不該被任何一邊猜出一個值。
    """
    variants = {"壹": "一", "貳": "二", "貮": "二", "弍": "二", "參": "三", "叁": "三", "叄": "三",
                "肆": "四", "伍": "五", "陸": "六", "陆": "六", "柒": "七", "捌": "八", "玖": "九",
                "兩": "二", "两": "二", "拾": "十", "佰": "百", "陌": "百", "仟": "千", "阡": "千",
                "廿": "二十", "卅": "三十", "卌": "四十",
                "零": "〇", "○": "〇", "◯": "〇", "Ｏ": "〇", "O": "〇", "o": "〇", "ｏ": "〇"}
    s = "".join(variants.get(c, c) for c in s.strip())
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = [("千", 1000), ("百", 100), ("十", 10)]
    if not s or any(c not in digits and c != "〇" and c not in dict(units) for c in s):
        return None

    def read(part: str, allowed: list[tuple[str, int]]) -> int | None:
        if part == "":
            return 0
        for i, (u, v) in enumerate(allowed):
            if u in part:
                left, _, right = part.partition(u)
                if u in right or any(x in right for x, _ in allowed[: i + 1]):
                    return None
                if left == "":
                    if v != 10:
                        return None  # 「百五」無法無歧義解讀
                    head = 1
                elif len(left) == 1 and left in digits:
                    head = digits[left]
                else:
                    return None
                if right.startswith("〇"):
                    # 「一百零五」的零是**跳級**佔位符：只有還有更低的單位級可跳時才合法，
                    # 且它後面必須還有東西（「三百十〇三」「一百〇」都是壞字串）。
                    if not allowed[i + 1:]:
                        return None
                    right = right.lstrip("〇")
                    if right == "":
                        return None
                tail = read(right, allowed[i + 1:])
                return None if tail is None else head * v + tail
        # 沒有單位了：只能是單一個位數
        if len(part) == 1 and part in digits:
            return digits[part]
        return None

    if any(u in s for u, _ in units):
        v = read(s, units)
    else:
        if s.startswith("〇"):
            return None
        v = 0
        for c in s:
            v = v * 10 + (0 if c == "〇" else digits[c])
    if v is None or not (0 < v <= 9999):
        return None
    return v


def test_cn_numeral_review_regressions():
    """覆核者實測被誤讀的那七種寫法，逐一釘住。"""
    for s, want in [
        ("七三", 73), ("九九九", 999), ("一〇五", 105), ("一二三", 123),
        ("一Ｏ五", 105), ("廿五", 25), ("九佰九十九", 999),
    ]:
        assert_eq(cn_to_int(s), want, f"{s} 必須讀成 {want}（舊版讀成個位數並綠燈放行）")


def test_cn_numeral_roundtrip_1_to_1200():
    """property：1..1200 的兩種寫法都必須 round-trip。"""
    for n in range(1, 1201):
        u, d = _write_unit_style(n), _write_digit_string(n)
        assert_eq(cn_to_int(u), n, f"單位式 {u} round-trip 失敗")
        assert_eq(cn_to_int(d), n, f"數字串式 {d} round-trip 失敗")


def test_cn_numeral_never_disagrees_with_independent_reader():
    """property：隨機 CJK 數字串上，與獨立實作的讀法零分歧。

    差分測試的意義：任何一邊讀得出、另一邊讀不出（或讀出不同值）的字串，
    就是「有歧義」的證據，這種字串必須是 None，不能有人猜一個值。
    """
    rng = random.Random(20260905)  # 固定種子：測試要 deterministic
    alphabet = "一二三四五六七八九〇零十百千廿卅佰仟拾壹貳參Ｏ○"
    checked = 0
    for _ in range(4000):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 6)))
        got, ref = cn_to_int(s), _reference_read(s)
        assert_eq(got, ref, f"與獨立實作分歧：{s!r}（歧義字串必須兩邊都回 None）")
        checked += 1
    assert_true(checked == 4000)


def test_cn_numeral_rejects_garbage_and_ambiguity():
    """亂碼、混用兩套體系、違反單位文法 → None，絕不猜。"""
    for s in ["不是數字", "第條", "", "  ", "十十", "百五", "三四十", "一百二百",
              "〇五", "五十六十", "abc", "１２ａ", "千千"]:
        assert_eq(cn_to_int(s), None, f"{s!r} 無法無歧義解析，必須回 None")
    assert_eq(cn_to_int(_write_digit_string(MAX_ARTICLE_VALUE + 1)), None, "超出條號上限要回 None")


def test_unparseable_article_is_yellow_and_human_not_green():
    """解析不出的條號 → 「無法解析」黃燈交人工，既不猜也不宣稱查無此號。

    為什麼不判 missing（紅）：那等於宣稱「這個條號不存在」，那是系統沒有的知識。
    為什麼不丟掉：丟掉就變成「本句未附引用」→ 綠燈放行，正是要修掉的失敗模式。
    """
    ck = CitationChecker(SNAPSHOT)
    r = ck.check_text("另參建築法第百五條之規定。")
    assert_eq(len(r), 1, "無法解析的引用仍必須被抽出來，不得靜默消失")
    assert_eq(r[0].state, STATE_UNPARSEABLE)
    assert_eq(r[0].lamp, "y")
    assert_eq(r[0].blocking, False, "讀不懂不等於偽造，不阻擋，但要看得見")
    assert_in("不猜", r[0].note)


def test_cn_article_numbers_end_to_end_states():
    """國字條號在四態判定上與阿拉伯數字完全一致（不得因寫法不同而放水）。"""
    ck = CitationChecker(SNAPSHOT)
    for text, want in [
        ("建築法第七三條", STATE_OK),           # 73 在庫
        ("建築法第一〇五條", STATE_OK),          # 105 = 最大條號
        ("建築法第一Ｏ五條", STATE_OK),          # 全形 O 當零
        ("建築法第廿五條", STATE_OK),
        ("建築法第九九九條", STATE_MISSING),      # 假條號照樣紅燈
        ("建築法第一二三條", STATE_MISSING),
        ("建築法第九佰九十九條", STATE_MISSING),
    ]:
        r = ck.check_text(text)
        assert_eq(len(r), 1, f"{text} 應抽到 1 筆引用")
        assert_eq(r[0].state, want, f"{text} 的狀態錯誤")


def test_ac2_fabricated_cn_article_blocks_submission_end_to_end():
    """AC2：「建築法第九九九條」端到端 → 紅燈 missing、進 blockers、submit_allowed=false。

    這條直接複現覆核者實測「submit_allowed=True、cites=[('建築法第9條','ok')]」的那次放行。
    基底刻意用會通過的 ordinary 案例，才能證明是這一句把它從 true 翻成 false。
    """
    clean = build_payload(run_case(ORDINARY, mode="fixture"))
    assert_eq(clean["submit_allowed"], True, "基底案例本來是可送出的")

    p = _run_with_injected(ORDINARY, "reasoning", [{"t": "至擅自變更使用之處罰要件，另參建築法第九九九條之規定。"}])
    assert_eq(p["submit_allowed"], False, "捏造的國字條號必須阻擋送出")
    bad = [c for c in p["citations"] if "999" in c["raw"]]
    assert_eq(len(bad), 1, f"應抽到建築法第999條，實得 {[c['raw'] for c in p['citations']]}")
    assert_eq(bad[0]["state"], STATE_MISSING)
    assert_eq(bad[0]["lamp"], "r")
    assert_true(
        any(b["reason"] == "citation_missing" and "999" in b["detail"] for b in p["blockers"]),
        "必須進 blockers 並說明是哪一筆",
    )
    assert_true(
        all("建築法第9條" != c["raw"] for c in p["citations"]),
        "絕不得把「九九九」誤讀成 9 而命中真實存在的低條號",
    )


# ════════════════════════════════════════════════════════════════════
# P0-2 主文封鎖：結構判準、全槽位、空白排版
# ════════════════════════════════════════════════════════════════════
# 結構性規則：主文 = （訴願／處分標的）×（處置動詞）的語法共現，或處置助詞＋動詞。
# 偵測前刪掉所有空白並統一標點，所以排版不能當繞法。

# 15 種真實決定書主文寫法（法律文書的定型句式，非杜撰案情）
REAL_CONCLUSION_FORMS = (
    "訴願駁回。",
    "本件訴願為無理由，應予駁回。",
    "訴願人之訴願為無理由，爰予駁回。",
    "綜上所述，本件訴願為無理由，應予駁回。",
    "訴願不受理。",
    "本件訴願逾期，不予受理。",
    "本件訴願程序不合法，應不受理。",
    "原處分撤銷。",
    "原 處 分 撤 銷 。",
    "原處分應予撤銷。",
    "原處分核有違誤，爰予撤銷。",
    "原處分撤銷，由原處分機關另為適法之處分。",
    "原處分應予維持。",
    "原處分並無違誤，訴願駁回。",
    "撤銷原處分，發回原處分機關另為適法之處分。",
)

# 真實理由段句子（取自兩個合成案例），一句都不准被誤判成主文
REAL_REASONING_FORMS = (
    "查原處分認定之違規事實，有現場勘查紀錄、照片及使用執照存根附卷可稽，事實認定尚無違誤。",
    "惟按行政罰之裁處權，因三年期間之經過而消滅；期間自違反行政法上義務之行為終了時起算。",
    "至擅自變更使用之處罰要件，另參建築法第73條之規定。",
    "按訴願之提起，應自行政處分達到或公告期滿之次日起 30 日內為之。",
    "查系爭裁處書於民國 113 年 6 月 13 日寄存送達，均以寄存之日視為收受送達之日期，而發生送達效力。",
    "訴願人於陳述意見時陳稱，該處自民國 109 年間即由前承租人改作餐飲使用。",
)


def test_all_15_real_conclusion_forms_are_detected():
    """覆核者列的 15 種真實主文寫法（含字間空白排版）全部要命中。"""
    missed = [t for t in REAL_CONCLUSION_FORMS if not detect_conclusion_like(t)]
    assert_eq(missed, [], f"漏抓 {len(missed)} 種主文寫法：{missed}")


def test_real_reasoning_sentences_are_not_false_positives():
    """理由段的正常句子不得被誤判（誤攔會讓守門變成雜訊，人就開始無視它）。"""
    fired = [(t, detect_conclusion_like(t)) for t in REAL_REASONING_FORMS if detect_conclusion_like(t)]
    assert_eq(fired, [], f"誤判為主文：{fired}")


def test_whitespace_and_punctuation_normalisation_is_not_bypassable():
    """排版不是繞法：字間空白、全形空格、換行、括號都要先被正規化掉。"""
    for variant in ["原 處 分 撤 銷 。", "原　處　分　撤　銷", "原\n處\n分\n撤\n銷", "（原處分撤銷）", "原處分  撤銷"]:
        assert_true(detect_conclusion_like(variant), f"{variant!r} 必須被偵測到")
    assert_eq(normalize_for_structure("原 處 分 撤 銷 。"), "原處分撤銷。")


def test_conclusion_detection_is_deliberately_over_inclusive():
    """fail-safe 的成本要寫成測試，不要當意外。

    「訴願人主張原處分應予撤銷云云」是轉述、不是主文，但它照樣被攔。
    這是刻意的：誤攔的代價是多一次人工確認，漏放的代價是系統替一份沒人看過的
    法律結論背書。這條測試存在的目的是讓未來的人**知道這是選擇**，不是 bug。
    """
    assert_true(detect_conclusion_like("訴願人主張原處分應予撤銷云云，尚非可採。"))


def _blocked_state_with(sentences: list[dict[str, Any]]) -> CaseState:
    state = CaseState(case_id="synthetic-unit-slotcheck")
    state.screen = {
        "requires_human_conclusion": True,
        "fact_issues": [],
        "human_conclusion_signals": ["測試訊號"],
        "deadline": {"steps": [], "caveats": []},
    }
    state.retrieval = {"laws": [], "cases": [], "retrieval_meta": {}}
    state.draft = {"doc_skeleton": [{"ty": "p", "text": "", "ind": 1, "ss": sentences}]}
    return state


def _sentence(sid: str, text: str, slot: str, origin: str = "llm") -> dict[str, Any]:
    return {
        "id": sid, "t": text, "origin": origin, "slot": slot, "cite_ids": [], "basis": None,
        "engine": None, "placeholder": False, "l": None, "why": None, "refs": [],
        "l_origin": "rule", "why_origin": "rule",
    }


def test_conclusion_block_covers_every_slot_not_just_conclusion():
    """封鎖狀態下，主文出現在**任何**槽位都要被攔。

    slot 是模型自己標的欄位。拿它當判準等於讓被管制的一方決定自己受不受管制——
    把主文標成 reasoning/facts/other 就能穿過，那封鎖等於沒有。
    """
    for slot in ("reasoning", "facts", "calculation", "other", "summary"):
        state = _blocked_state_with([_sentence("s1", "綜上所述，本件訴願為無理由，應予駁回。", slot)])
        n6_gate.run(state, _ctx())
        reasons = {b["reason"] for b in state.gate["blockers"]}
        assert_in("conclusion_like_text_outside_conclusion_slot", reasons, f"slot={slot} 沒被攔")
        assert_eq(state.gate["submit_allowed"], False, f"slot={slot} 竟可送出")
        assert_eq(state.gate["doc"][0]["ss"][0]["l"], "r", f"slot={slot} 的主文句必須紅燈")
        assert_eq(state.gate["doc"][0]["ss"][0]["tier"], "請人工判斷")


def test_all_15_conclusion_forms_blocked_in_reasoning_slot():
    """15 種主文寫法逐一塞進 reasoning，每一種都要 submit_allowed=false。"""
    leaked = []
    for i, text in enumerate(REAL_CONCLUSION_FORMS):
        state = _blocked_state_with([_sentence(f"s{i}", text, "reasoning")])
        n6_gate.run(state, _ctx())
        if state.gate["submit_allowed"]:
            leaked.append(text)
    assert_eq(leaked, [], f"{len(leaked)} 種主文寫法穿過封鎖：{leaked}")


def test_conclusion_like_in_record_origin_is_also_blocked():
    """連 record（卷證直錄）槽位也查：把主文包裝成「引述原處分」是最省事的繞法。"""
    state = _blocked_state_with([_sentence("s1", "原處分撤銷。", "facts", origin="record")])
    n6_gate.run(state, _ctx())
    assert_eq(state.gate["submit_allowed"], False, "record origin 的主文句照樣要攔")


def test_ac3_conclusion_bypass_blocked_end_to_end():
    """AC3：主文寫進 reasoning 的兩種寫法端到端都要 submit_allowed=false。"""
    for text in ("綜上所述，本件訴願為無理由，應予駁回。", "原 處 分 撤 銷 。"):
        p = _run_with_injected(BLOCKED, "reasoning", [{"t": text}])
        assert_eq(p["submit_allowed"], False, f"{text!r} 未被阻擋")
        reasons = {b["reason"] for b in p["blockers"]}
        assert_in("conclusion_like_text_outside_conclusion_slot", reasons)
        leaked = [s for b in p["doc"] for s in b["ss"] if (s["t"] or "").strip() == text.strip()]
        assert_eq(len(leaked), 1, f"注入的句子應在 doc[] 出現一次：{text!r}")
        assert_eq(leaked[0]["l"], "r")
        assert_eq(leaked[0]["tier"], "請人工判斷")


def test_invariant_catches_unblocked_conclusion_in_any_slot():
    """不變式是最後一道：主文句若沒被標紅並列入 blockers，必須直接炸。"""
    state = _blocked_state_with([_sentence("s1", "本件訴願為無理由，應予駁回。", "reasoning")])
    state.gate = {"doc": state.draft["doc_skeleton"], "blockers": []}  # 模擬守門漏掉
    try:
        state.assert_verified_invariant()
    except AssertionError as e:
        assert_in("P0", str(e))
        return
    raise AssertionError("不變式沒抓到未被攔下的主文句")


# ════════════════════════════════════════════════════════════════════
# P0-3／判解／釋字：字號辨識鍵是結構，不是結尾那個字
# ════════════════════════════════════════════════════════════════════

def test_directive_without_han_suffix_is_detected():
    """`…號書函`、括號內無「函」字者：辨識鍵是發文字號結構，不是結尾的「函」。"""
    ck = CitationChecker(SNAPSHOT)
    for text in (
        "參內政部101年3月5日台內營字第1010801234號書函。",
        "（內政部101年3月5日台內營字第1010801234號）",
        "參內政部台內營字第1010801234號。",
        "參內政部101年3月5日台內營字第1010801234號公告。",
        "參內政部112年5月1日台內營字第1120801234號函釋。",
    ):
        r = ck.check_text(text)
        directives = [c for c in r if c.kind == "directive"]
        assert_eq(len(directives), 1, f"{text!r} 應抽到 1 筆函釋，實得 {[(c.kind, c.raw) for c in r]}")
        assert_eq(directives[0].state, STATE_OUT_OF_SCOPE)
        assert_eq(directives[0].lamp, "y")
        assert_true(
            all(c.kind != "precedent" for c in r),
            f"{text!r} 的發文日期不得被判解 regex 誤讀成幽靈判解字號",
        )


def test_precedent_and_interpretation_accept_cn_numerals():
    """`釋字第七四七號`、`一一二年度判字第一二三號` 原本零偵測。"""
    ck = CitationChecker(SNAPSHOT)
    r = ck.check_text("參釋字第七四七號解釋意旨。")
    assert_eq(len(r), 1, "國字釋字必須被抽出來")
    assert_eq(r[0].kind, "interpretation")
    assert_eq(r[0].payload["no"], 747, "號數要解析成 747，不是猜一個數")
    assert_eq(r[0].state, STATE_OUT_OF_SCOPE, "非白名單釋字標庫外未驗證")

    r = ck.check_text("參最高行政法院一一二年度判字第一二三號判決。")
    assert_eq(len(r), 1, "國字判解字號必須被抽出來")
    assert_eq(r[0].kind, "precedent")
    assert_eq((r[0].payload["year"], r[0].payload["no"]), (112, 123))

    # 在庫的白名單字號用國字寫也要對得上（不得因寫法不同而漏綠燈）
    r = ck.check_text("參釋字第四六九號解釋。")
    assert_eq(r[0].state, STATE_OK, "白名單內的釋字用國字寫也要判在庫")


def test_unparseable_precedent_is_visible_not_silent():
    """判解號數讀不懂時標「無法解析」黃燈，不得靜默消失。"""
    ck = CitationChecker(SNAPSHOT)
    r = ck.check_text("參最高行政法院一一二年度判字第百五號判決。")
    assert_eq(len(r), 1, "讀不懂也要抽出來")
    assert_eq(r[0].state, STATE_UNPARSEABLE)
    assert_eq(r[0].lamp, "y")


def test_cn_citations_reach_the_gate_end_to_end():
    """國字判解／釋字要一路走到 payload 的 citations[]，不是只在單元層看得到。"""
    p = _run_with_injected(
        BLOCKED, "reasoning",
        [{"t": "參釋字第七四七號解釋及最高行政法院一一二年度判字第一二三號判決意旨。"}],
    )
    kinds = {c["kind"] for c in p["citations"]}
    assert_in("interpretation", kinds)
    assert_in("precedent", kinds)


# ════════════════════════════════════════════════════════════════════
# 三層歸屬與檢索比對
# ════════════════════════════════════════════════════════════════════

def test_sentence_without_citation_is_not_labeled_sourced():
    """覆核發現④：模型寫的句子一個引用都沒抽到時，不得歸「有出處」層。

    「有出處」的定義是這句話指得出它的出處。指不出就是 CONSTITUTION §1 的
    「請人工判斷」層——把它放進「有出處」等於系統替一句沒有依據的話背書。
    """
    state = _blocked_state_with([_sentence("s1", "本件事證明確，堪予認定。", "reasoning")])
    state.screen["requires_human_conclusion"] = False
    n6_gate.run(state, _ctx())
    s = state.gate["doc"][0]["ss"][0]
    assert_eq(s["citations"], [], "這句本來就沒有引用")
    assert_eq(s["l"], "y", "沒有引用維持黃燈（不是紅燈，它不必然錯）")
    assert_eq(s["tier"], "請人工判斷", "但它不得被歸進「有出處」層")


def test_retrieval_lamp_uses_stable_key_and_reports_mismatch():
    """n6:172 的比對改用穩定鍵；對不到時要明講，不得靜默填預設燈號。"""
    state = _blocked_state_with([_sentence("s1", "依建築法第73條規定。", "reasoning")])
    state.screen["requires_human_conclusion"] = False
    state.retrieval = {
        "laws": [
            {"id": "L1", "t": "建築法第73條", "law": "建築法", "article": "73", "verified": True,
             "lamp": None, "tag": None},
            # 檢索給了一筆草稿根本沒引用的法條：舊版會靜默用 verified 填綠燈
            {"id": "L2", "t": "訴願法第14條", "law": "訴願法", "article": "14", "verified": True,
             "lamp": None, "tag": None},
        ],
        "cases": [], "retrieval_meta": {},
    }
    n6_gate.run(state, _ctx())
    laws = {l["id"]: l for l in state.retrieval["laws"]}
    assert_eq(laws["L1"]["lamp"], "g", "對得到的照守門結果發燈")
    assert_eq(laws["L1"]["gate_ref_key"], "建築法|73", "比對鍵必須是結構化欄位，不是顯示字串")
    assert_eq(laws["L2"]["lamp"], "y", "對不到的絕不預設綠燈")
    assert_in("未能對回", laws["L2"]["tag"])
    assert_true(
        any(b["reason"] == "retrieval_law_not_matched_by_gate" for b in state.gate["blockers"]),
        "對不到必須進 blockers，不能靜默走 else",
    )


def test_resolved_id_and_laws_id_namespaces_are_documented_as_disjoint():
    """釘住那個 bug 的根因：兩個 id 命名空間交集為空，所以不能拿來互相比對。"""
    ck = CitationChecker(SNAPSHOT)
    c = ck.check_text("依訴願法第14條規定")[0]
    assert_eq(c.resolved_id, "L-訴願法-14")
    assert_eq(c.ref_key, "訴願法|14")
    assert_true(
        not c.resolved_id.startswith("L1") and c.resolved_id != "L1",
        "resolved_id 與 N4 的 laws[].id（L1、L2…）不同命名空間，比對必須改用 ref_key",
    )


# ════════════════════════════════════════════════════════════════════
# 回歸：兩個正式案例的行為不得因為加固而改變
# ════════════════════════════════════════════════════════════════════

def test_official_cases_behaviour_unchanged():
    """加固不得改動兩個正式案例的燈號分布與送出判斷（AC4 的行為面）。"""
    o = build_payload(run_case(ORDINARY, mode="fixture"))
    assert_eq(o["submit_allowed"], True)
    assert_eq(o["lamp_stats"], {"r": 0, "y": 0, "g": 13}, "ordinary 的燈號分布不得改變")
    assert_eq(o["blockers"], [])
    assert_eq({k: len(v) for k, v in o["tiers"].items()}, {"可驗算": 6, "有出處": 7, "請人工判斷": 3})

    b = build_payload(run_case(BLOCKED, mode="fixture"))
    assert_eq(b["submit_allowed"], False)
    assert_eq(b["lamp_stats"], {"r": 2, "y": 0, "g": 10}, "blocked 的燈號分布不得改變")
    assert_eq({k: len(v) for k, v in b["tiers"].items()}, {"可驗算": 6, "有出處": 4, "請人工判斷": 3})


def test_pipeline_is_still_deterministic():
    """同案例跑 5 次（去掉時間欄位）雜湊必須一致——守門加固不得引入不確定性。"""
    import hashlib

    def _strip(o):
        drop = {"elapsed_ms", "node_timings", "started_at", "summary"}
        if isinstance(o, dict):
            return {k: _strip(v) for k, v in o.items() if k not in drop}
        if isinstance(o, list):
            return [_strip(v) for v in o]
        return o

    for case_id in (ORDINARY, BLOCKED):
        hashes = {
            hashlib.sha256(
                json.dumps(_strip(build_payload(run_case(case_id, mode="fixture"))),
                           ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            for _ in range(5)
        }
        assert_eq(len(hashes), 1, f"{case_id} 跑 5 次結果不一致（deterministic 壞了）")
