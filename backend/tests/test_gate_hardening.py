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

    def read(part: str, allowed: list[tuple[str, int]], ones_ok: bool) -> int | None:
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
                zero = False
                rest = allowed[i + 1:]
                if right.startswith("〇"):
                    # 「一百零五」的零是**跳級**佔位符：宣告「至少跳過一個位級」，
                    # 所以餘數要從再下一級開始讀（一千零二十 → 餘數從百以下讀起）。
                    # 「一百零五十」的餘數用了緊鄰的十位＝沒真的跳，是壞字串。
                    if len(right) > 1 and right[1] == "〇":
                        return None  # 「一千零零五」連兩個佔位符
                    if not rest:
                        return None
                    right = right[1:]
                    if right == "":
                        return None
                    rest = allowed[i + 2:]
                    zero = True
                # 尾數裸數字只有在「剛用完十位」或「有〇跳級」時才無歧義：
                # 「一百五」是 150 還是 105？兩種讀法都有人用 → 不猜。
                tail = read(right, rest, ones_ok=(v == 10 or zero))
                return None if tail is None else head * v + tail
        # 沒有單位了：只能是單一個位數，而且要有資格當個位
        if len(part) == 1 and part in digits and ones_ok:
            return digits[part]
        return None

    if any(u in s for u, _ in units):
        v = read(s, units, ones_ok=True)
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


def test_attribution_frame_is_exempt_at_pattern_layer_but_still_caught_by_backstop():
    """轉述當事人主張的句子在片語層免責，但兜底層照樣接住——兩層的分工要寫成測試。

    「訴願人主張原處分應予撤銷云云」是轉述，不是機關的結論。在片語層攔它會製造誤攔
    （覆核實測誤攔率 14%，而且踩在最高頻句型上），所以用結構性免責框架放行。
    但它一個可查證引用都沒有，在 C 型封鎖下仍會被第三層兜底攔下並交人工。
    **免責只豁免片語層，不豁免兜底層**——這正是兜底層存在的理由。
    """
    text = "又訴願人請求撤銷原處分之理由，均係就原處分機關認定事實之爭執。"
    assert_eq(detect_conclusion_like(text), [], "轉述框架在片語層應免責，避免高頻誤攔")
    # 對照：處置動詞離主張／請求較遠時**不**豁免——那是機關自己的處置，不是轉述。
    assert_true(
        detect_conclusion_like("訴願人主張原處分應予撤銷云云，尚非可採。"),
        "「應予撤銷」不緊貼主張，仍視為處置語句（刻意保守，寧可多攔）",
    )
    assert_true(
        detect_conclusion_like("認訴願人之主張核無可採，其請求不能准許，予以駁回。"),
        "轉述後面接機關自己的處置，不得因為前面有「主張」就整句豁免",
    )

    state = _blocked_state_with([_sentence("s1", text, "reasoning")])
    n6_gate.run(state, _ctx())
    assert_eq(state.gate["submit_allowed"], False, "無引用的模型句在 C 型封鎖下仍要交人工")
    assert_in(
        "unsourced_sentence_while_conclusion_blocked",
        {b["reason"] for b in state.gate["blockers"]},
    )


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
# 第二輪對抗覆核（2026-09-05）打穿的破口，逐條回歸
# ════════════════════════════════════════════════════════════════════
# 覆核用 30 句真實主文打穿 29 句、並找到一整類回「錯誤整數而非 None」的數字寫法。
# 這一區每一條都對應一個當時真的穿過去的輸入。

# 覆核構造的繞法：不含「駁回／不受理／撤銷／廢止／變更／維持」這六個動詞的主文、
# 插入語超過字元視窗的、拆成兩句寫的。
REVIEW_BYPASS_FORMS = (
    "本件訴願為無理由。",
    "命被告機關重為處分。",
    "本府決定如主文。",
    "原處分並無違誤，訴願人執詞爭議，難認有據。",
    "本件應發回原處分機關，於二個月內重為適法之決定。",
    "訴願人之訴願，難謂有理由，其請求應遭否准。",
    "系爭核定應予註銷。",
    "本件訴願，經審酌全部卷證資料及兩造陳述意見後，認訴願人之主張核無可採，其請求不能准許，予以駁回。",
    "本件訴願，為無理由。",
    "依法駁回。",
    "原處分應予維持，訴願駁回。",
    "訴願人之請求為無理由。",
    "本件訴願事件，經核並無理由，爰予否准。",
    "本府認原處分並無不當，應予維持。",
    "系爭處分應予變更。",
)


def test_review_bypass_forms_all_blocked():
    """覆核打穿的 15 種繞法，端到端一句都不准放行。"""
    leaked = []
    for i, text in enumerate(REVIEW_BYPASS_FORMS):
        state = _blocked_state_with([_sentence(f"s{i}", text, "reasoning")])
        n6_gate.run(state, _ctx())
        if state.gate["submit_allowed"]:
            leaked.append(text)
    assert_eq(leaked, [], f"{len(leaked)} 種繞法仍穿過封鎖：{leaked}")


def test_review_bypass_forms_caught_by_pattern_layer_even_when_armed_with_citations():
    """把繞法句配上**真實可查證的引用**（讓兜底層失效），片語層必須自己擋得住。

    這條是分層檢驗：兜底層（無引用即交人工）擋得住所有沒帶引用的主文，
    但攻擊者只要加一個合法法條就能繞過兜底層。這時只剩片語層，它必須自己成立。
    """
    missed = []
    for text in REVIEW_BYPASS_FORMS:
        armed = f"依訴願法第79條規定，{text}"
        if not detect_conclusion_like(armed):
            missed.append(armed)
    assert_eq(missed, [], f"片語層漏抓 {len(missed)} 種（兜底層此時已被引用繞過）：{missed}")


def test_unsourced_model_sentence_blocks_under_c_type():
    """兜底層本身：C 型封鎖下，模型寫的、零引用的句子一律交人工。

    這一層不看字串，所以沒有任何寫法能繞過它——覆核打穿的 15 種主文沒有一句帶引用。
    """
    state = _blocked_state_with([_sentence("s1", "本件事證明確，堪予認定。", "reasoning")])
    n6_gate.run(state, _ctx())
    s = state.gate["doc"][0]["ss"][0]
    assert_eq(s["l"], "r")
    assert_eq(s["tier"], "請人工判斷")
    assert_eq(state.gate["submit_allowed"], False)
    assert_in(
        "unsourced_sentence_while_conclusion_blocked",
        {b["reason"] for b in state.gate["blockers"]},
    )


def test_backstop_does_not_fire_when_conclusion_not_blocked():
    """兜底層只在 C 型封鎖下生效——一般案件的無引用涵攝句仍可送出（只是黃燈交人工）。"""
    state = _blocked_state_with([_sentence("s1", "本件事證明確，堪予認定。", "reasoning")])
    state.screen["requires_human_conclusion"] = False
    n6_gate.run(state, _ctx())
    assert_eq(state.gate["submit_allowed"], True, "非 C 型案件不得被兜底層鎖死")


def test_unit_omitted_numerals_are_ambiguous_and_refused():
    """覆核找到的整類誤讀：「一百五」人讀 150、舊版讀成 105 → 捏造條號拿到綠燈。

    兩種讀法都有人用 = 歧義 = 不猜。這裡要的是 `None`，不是「讀對」。
    """
    for s in ("一百五", "一百二", "二百五", "三千八", "一千五", "七千〇六百四"):
        assert_eq(cn_to_int(s), None, f"{s!r} 有兩種讀法，必須回 None 而不是猜一個")
    # 對照組：加了跳級零或以十位收尾就無歧義，必須讀得出來
    for s, want in (("一百零五", 105), ("七十三", 73), ("三百十三", 313), ("一千零二十", 1020)):
        assert_eq(cn_to_int(s), want, f"{s!r} 無歧義，不得誤判成無法解析")


def test_unit_omitted_article_does_not_get_green_light():
    """端到端：「建築法第一百五條」不得被讀成第 105 條而拿到 ✓ 在庫 綠燈。

    建築法快照最大條號 105。人讀「第一百五條」是第 150 條（不存在）。
    舊版讀成 105 → 命中 → 綠燈 → 可送出，正是這輪要根除的「誤讀→綠燈」。
    """
    p = _run_with_injected(ORDINARY, "reasoning", [{"t": "另參建築法第一百五條之規定。"}])
    cites = [c for c in p["citations"] if "建築法" in c["raw"]]
    assert_eq(len(cites), 1, f"應抽到 1 筆建築法引用，實得 {[c['raw'] for c in p['citations']]}")
    assert_eq(cites[0]["state"], STATE_UNPARSEABLE, "歧義寫法應標無法解析，不得猜成 105")
    assert_true(cites[0]["lamp"] != "g", "絕不得對歧義條號發綠燈")


def test_directive_with_cn_number_and_dotted_date_is_detected():
    """覆核找到的隱形函釋：國字號數、點式發文日期、帶尾綴的號數。

    隱形本身在單獨成句時是安全失敗（無引用→交人工），但只要同句另有一個合法法條，
    整句就會變綠燈＋「有出處」——等於系統替一個它根本沒看見的函釋背書。
    """
    ck = CitationChecker(SNAPSHOT)
    for text in (
        "內政部台內營字第一〇一〇八一〇八八一號函參照。",
        "內政部 88.5.10 台內營字第8873101號",
        "內政部台內營字第1010810881-1號函",
    ):
        directives = [c for c in ck.check_text(text) if c.kind == "directive"]
        assert_eq(len(directives), 1, f"{text!r} 的函釋整筆隱形")

    mixed = "依建築法第73條及內政部台內營字第一〇一〇八一〇八八一號函釋意旨，本件應予處罰。"
    kinds = [c.kind for c in ck.check_text(mixed)]
    assert_in("directive", kinds, "同句有合法法條時，隱形的函釋會讓整句誤標綠燈")


def test_cn_year_precedent_is_not_misclassified_as_directive():
    """覆核找到的 kind 誤判：`最高行政法院一一二年度判字第123號` 被判成函釋。

    結構性區辨：發文字別不含「年」「度」，判解字號才有「年度」。
    誤判的後果是畫面對評審顯示「函釋不在本系統的驗證範圍」這句與事實不符的說明。
    """
    ck = CitationChecker(SNAPSHOT)
    for text in ("最高行政法院一一二年度判字第123號判決參照。", "最高行政法院一一二年度判字第一二三號"):
        r = ck.check_text(text)
        assert_eq(len(r), 1, f"{text!r} 應抽到 1 筆")
        assert_eq(r[0].kind, "precedent", f"{text!r} 是判解字號，不是函釋")


def test_attribution_quotation_is_not_false_positive():
    """轉述當事人請求的句子必須放行（覆核量到的誤攔之一，最高頻句型）。"""
    for text in (
        "又訴願人請求撤銷原處分之理由，均係就原處分機關認定事實之爭執。",
        "訴願人主張其未收受原處分書，請求撤銷原處分並退還已繳納之罰鍰。",
    ):
        assert_eq(detect_conclusion_like(text), [], f"{text!r} 是轉述當事人請求，不得誤攔")


def test_agency_verdict_on_a_request_is_not_treated_as_quotation():
    """「訴願人**之**請求為無理由」是機關的評價，不是轉述——差一個「之」就換了主語。"""
    assert_true(detect_conclusion_like("訴願人之請求為無理由。"), "機關對請求下的評價就是主文的實質")
    assert_true(detect_conclusion_like("訴願人請求撤銷原處分，經核於法有據，本會爰依其請求辦理。"),
                "轉述後面接機關自己的評價，不得整句豁免")


def test_pure_statutory_quotation_of_a_disposition_is_deliberately_over_blocked():
    """刻意選的方向：第 1 層（法定處理結果）**任何框架都不豁免**，純引述也照攔。

    「按訴願法第79條規定，訴願無理由者，應以決定駁回之」是純引述法條，攔它是誤攔。
    但覆核實測「按訴願法第81條規定，訴願有理由者，原處分撤銷，並命…另為適法之處分」
    ——同樣的殼、完整的主文、還帶真實條號（同時繞過第三層兜底）。兩者在結構上無法區分，
    所以只能二選一。選攔：誤攔的代價是承辦人多按一次確認，漏放的代價是系統替一份
    沒人看過的法律結論背書。**這條測試存在的目的是讓未來的人知道這是選擇，不是 bug。**
    """
    assert_true(detect_conclusion_like("按訴願法第77條第2款規定，提起訴願逾法定期間者，應為不受理之決定。"))
    assert_true(
        detect_conclusion_like("按訴願法第81條規定，訴願有理由者，原處分撤銷，並命原處分機關於二個月內另為適法之處分。"),
        "偽裝成通則引述的實質主文必須被攔（覆核實測的繞法）",
    )


# ════════════════════════════════════════════════════════════════════
# 第三輪對抗覆核（2026-09-05）打穿的破口，逐條回歸
# ════════════════════════════════════════════════════════════════════
# 覆核構造 90 句、55 句穿過。三條繞法：(a) origin=record 不受兜底層管轄；
# (b) 隨便塞一個「系統自己標明無法驗證」的引用就讓兜底層失效；
# (c)「按…者，…」通則引述殼可以整段豁免片語層。全部在這裡釘死。

# 覆核找出的、第 1 層漏掉的法定處理結果（都對得回訴願法條號，不是罕見寫法）
STATUTORY_DISPOSITION_FORMS = (
    "確認原處分違法。",
    "確認原處分不存在。",
    "確認原處分機關民國一一二年五月一日之行政處分為違法。",
    "原處分機關應於三十日內作成准予閱覽卷宗之處分。",
    "原處分機關應於收受本決定書之次日起二個月內，就訴願人之申請案作成決定。",
    "本件訴願事件移送於管轄機關。",
    "本件移由有管轄權之機關處理。",
    "訴願人業已撤回訴願，本件應予終結。",
    "本件訴願程序終結。",
    "本件訴願事件因訴願人死亡且無人承受而終結。",
    "原處分機關嗣後應依本決定意旨另為處理。",
    "原處分機關應於三十日內就系爭申請案重新審查並作成決定。",
    "綜上所述，原處分認事用法並無違誤，訴願人之主張不足採據。",
    "綜上，訴願人所執各節均不足以動搖原處分之認定。",
    "準此，訴願人之主張洵屬無據。",
    "命新北市政府環境保護局於二個月內就訴願人之申請作成准予備查之處分。",
)

# 覆核用來讓兜底層失效的「假出處」：系統自己都標明無法驗證的東西
FAKE_SHIELDS = (
    "政府資訊公開法第9條",      # 庫外法規（out_of_scope）
    "行政訴訟法第4條",          # 庫外法規
    "建築法第一百五條",          # 無法解析的條號（unparseable）
    "內政部台內營字第9999999號函",  # 捏造的機關函釋
)


def test_statutory_disposition_forms_are_all_detected():
    """第 1 層宣稱「來源是訴願法窮舉」——覆核證明當時沒窮舉完，這 16 種全漏。"""
    missed = [t for t in STATUTORY_DISPOSITION_FORMS if not detect_conclusion_like(t)]
    assert_eq(missed, [], f"第 1 層仍漏 {len(missed)} 種法定處理結果：{missed}")


def test_conclusion_survives_every_shield_and_origin_combination():
    """交叉組合：主文寫法 × 假出處 × origin，一種都不准放行。

    覆核的三條繞法會互相加乘：用 record origin 躲開兜底層、用假引用讓兜底層失效、
    用通則引述殼豁免片語層。這條測試把它們乘起來一次打。
    """
    leaks = []
    forms = STATUTORY_DISPOSITION_FORMS + REVIEW_BYPASS_FORMS
    for text in forms:
        for shield in (None,) + FAKE_SHIELDS:
            for origin in ("llm", "record"):
                state = _blocked_state_with([_sentence("s1", text, "reasoning", origin=origin)])
                state.draft["doc_skeleton"][0]["ss"][0]["basis"] = shield
                n6_gate.run(state, _ctx())
                if state.gate["submit_allowed"]:
                    leaks.append((origin, shield, text[:24]))
    assert_eq(leaks, [], f"{len(leaks)} 種組合穿過封鎖：{leaks[:8]}")


def test_unverifiable_citation_is_not_a_valid_shield_for_the_backstop():
    """兜底層的「有引用」必須是**可用的**引用：讀不懂的、查無此號的都不算出處。"""
    for shield in ("建築法第一百五條", "建築法第9999條"):
        state = _blocked_state_with([_sentence("s1", "本件事證明確，堪予認定。", "reasoning")])
        state.draft["doc_skeleton"][0]["ss"][0]["basis"] = shield
        n6_gate.run(state, _ctx())
        assert_eq(state.gate["submit_allowed"], False, f"{shield!r} 不得當成出處讓兜底層失效")


def test_out_of_scope_only_citation_is_not_sourced_tier():
    """只引到庫外法規時不得標「有出處」——系統其實沒有驗到任何東西。

    覆核實測：一個捏造的函釋字號足以讓整句在畫面上變成「有出處、字號已驗」。
    CONSTITUTION §2 把「庫外未驗證」定位成必須明標的保留，不是出處。
    """
    state = _blocked_state_with([_sentence("s1", "依政府資訊公開法第9條規定，訴願人得申請閱覽卷宗。", "reasoning")])
    state.screen["requires_human_conclusion"] = False
    n6_gate.run(state, _ctx())
    s = state.gate["doc"][0]["ss"][0]
    assert_eq(s["l"], "y", "庫外引用是黃燈")
    assert_eq(s["tier"], "請人工判斷", "庫外未驗證不是「有出處」")


def test_zero_placeholder_scope_is_enforced():
    """覆核找到的第二類猜值：〇 宣告跳級，後面卻沒真的跳級。

    「一百零五十」人讀 150、舊版讀成 150 並命中查表；「一千零零五」連兩個佔位符。
    規則：〇 後面的餘數必須**小於下一個位級**（一千零二十 → 20 < 100 ✓）。
    """
    for s in ("一百〇五十", "一百零五十", "一千零零五", "一千零一百"):
        assert_eq(cn_to_int(s), None, f"{s!r} 的〇沒有真的跳級，屬壞字串")
    for s, want in (("一千零二十", 1020), ("一百零五", 105), ("一千零五", 1005), ("二千零五十", 2050)):
        assert_eq(cn_to_int(s), want, f"{s!r} 是合法跳級寫法，不得誤判")


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
