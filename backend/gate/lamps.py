"""燈號規則與 C 型結論封鎖（architecture §4.3、§8.1）——真邏輯，零 LLM。

燈號三色對應 CONSTITUTION §1 的三層誠實：

| lamp | 三層         | 什麼句子會拿到                                       |
|------|--------------|------------------------------------------------------|
| `g`  | 可驗算／有出處 | 期間引擎算出的句子、卷證直錄的事實段、引用全部在庫的句子 |
| `y`  | 有出處（需覆核）| 引用有條次異動或庫外未驗證、無引用之涵攝句             |
| `r`  | 請人工判斷    | 引用查無此號（阻擋送出）、結論段佔位（拒絕生成）        |

紅線：燈號永遠由本模組（規則）產出，模型只寫句子不寫「這句可不可信」。
"""
from __future__ import annotations

import re
from typing import Any

from backend.config.origin_registry import TIER_HUMAN, TIER_SOURCED, TIER_VERIFIABLE
from backend.gate.citations import (
    STATE_AMENDED,
    STATE_MISSING,
    STATE_OK,
    STATE_OUT_OF_SCOPE,
    STATE_UNPARSEABLE,
    Citation,
)

WHY_ENGINE = "期間由日期規則直接驗算，攤開算式可逐步覆核，無詮釋空間。"
# ⚠️ 這句話原本寫「卷證原文直錄，未經改寫或生成」——那是一個**系統無法擔保的事實宣稱**：
# facts_excerpt 由 N1（live 檔位是 LLM）透傳，全流程沒有任何一處把它跟來源文件逐字比對。
# 對法制局評審講一句擔保不了的話，代價不是技術債。改成誠實版本。
WHY_RECORD = "本句標示為卷證直錄；本階段尚無法與來源文件逐字比對，請覆核原文後採用。"
WHY_ALL_OK = "所引法條／判解全部可對回資料集，字號已驗。"
WHY_AMENDED = "引用之條次已異動，新舊條號並陳，請確認適用版本。"
WHY_OUT_OF_SCOPE = "引用超出資料集涵蓋範圍，本系統無法驗證，請人工查證後再採用。"
WHY_MISSING = "引用在資料集內查無此號，可能為誤植，已阻擋送出。"
WHY_NO_CITE = (
    "本句未附任何可查證的引用，系統無從指出它的出處，因此不列入「有出處」層，"
    "改列「請人工判斷」，請承辦人確認其法律依據。"
)
WHY_UNPARSEABLE = (
    "本句的引用號碼寫法無法無歧義解析，系統不猜也不宣稱它存在或不存在，請人工確認號碼。"
)
WHY_UNRESOLVED_REF = "本句標註的引用編號在檢索結果中解析不到，已阻擋送出。"
WHY_PLACEHOLDER = "結論涉及法律判斷，系統不生成，僅提供交接問題清單。"
WHY_CONCLUSION_LEAK = "結論段已封鎖，但本句出現主文型語句。實質結論不得因為換個槽位就繞過封鎖，已阻擋送出。"
WHY_UNSOURCED_WHILE_BLOCKED = (
    "本案結論段已封鎖（需人工判斷），而本句未附任何可查證的引用——系統無法確認它不是實質結論，已交人工。"
)


def lamp_for_states(states: list[str]) -> str:
    """一句話的燈號 = 它所有引用狀態裡最嚴重的那一個。"""
    if STATE_MISSING in states:
        return "r"
    if STATE_AMENDED in states or STATE_OUT_OF_SCOPE in states:
        return "y"
    if states and all(s == STATE_OK for s in states):
        return "g"
    return "y"


def tier_for_lamp(lamp: str, origin: str) -> str:
    if lamp == "r":
        return TIER_HUMAN
    if origin in ("engine", "rule", "static"):
        return TIER_VERIFIABLE
    return TIER_SOURCED


# 哪些 origin 的「出處」是結構性的、不靠引用字號：
#   engine/rule/static = 算式本身；record = 卷證原文直錄。
# 其餘（llm）的出處**只能**來自引用——沒有引用就沒有出處。
_STRUCTURAL_SOURCE_ORIGINS = ("engine", "rule", "static", "record")


def tier_for_sentence(lamp: str, origin: str, states: list[str], has_citations: bool) -> str:
    """句子該進三層誠實的哪一層（CONSTITUTION §1）。

    **結構性規則**：「有出處」層的定義是「這句話指得出它的出處」。
    模型寫的句子若一個引用都沒抽到，它指不出任何出處，就不能算「有出處」——
    舊版把這種句子丟進「有出處」層，等於系統替一句沒有依據的話背書。
    依 CONSTITUTION §1，無法歸入可驗算或有出處的，就是「請人工判斷」。

    同理，引用存在但號碼無法解析（`unparseable`）也不算有出處：系統讀不懂那個出處。
    """
    if lamp == "r":
        return TIER_HUMAN
    if origin in ("engine", "rule", "static"):
        return TIER_VERIFIABLE
    if origin in _STRUCTURAL_SOURCE_ORIGINS:
        return TIER_SOURCED
    if not has_citations:
        return TIER_HUMAN
    # 「有出處」＝出處**指得出來而且對得回資料集**。只引到庫外／讀不懂的號碼時，
    # 系統其實沒有驗到任何東西，卻在畫面上蓋「有出處、字號已驗」——那是替它沒看過的
    # 東西背書（覆核實測：捏造的函釋字號足以讓整句變成「有出處」）。
    # CONSTITUTION §2 把「庫外未驗證」定位成必須明標的**保留**，不是出處。
    if not any(s in (STATE_OK, STATE_AMENDED) for s in states):
        return TIER_HUMAN
    return TIER_SOURCED


def why_for(lamp: str, origin: str, states: list[str], unresolved: bool = False) -> str:
    if origin == "human_required":
        return WHY_PLACEHOLDER
    if origin == "engine":
        return WHY_ENGINE
    if origin == "record":
        return WHY_RECORD
    if unresolved:
        return WHY_UNRESOLVED_REF
    if not states:
        return WHY_NO_CITE
    if STATE_MISSING in states:
        return WHY_MISSING
    if STATE_UNPARSEABLE in states:
        return WHY_UNPARSEABLE
    if STATE_AMENDED in states:
        return WHY_AMENDED
    if STATE_OUT_OF_SCOPE in states:
        return WHY_OUT_OF_SCOPE
    return WHY_ALL_OK


def requires_human_conclusion(
    art77: dict[str, Any],
    case_type: str,
    fact_issues: list[dict[str, Any]],
    substantive_types: tuple[str, ...],
) -> tuple[bool, list[str]]:
    """C 型結論封鎖的結構性開關（architecture §4.3）。

    只吃 N2 與 N3 的輸出（兩者都在 N5 之前跑），沒有循環依賴。
    回傳 (是否封鎖, 觸發訊號清單)。
    """
    signals: list[str] = []
    substantive = bool(art77.get("requires_substantive_review")) and case_type in substantive_types
    if substantive:
        signals.append(f"程序合法且須進入實體審查（案型：{case_type}）")
    high = [i for i in fact_issues if i.get("severity") == "high"]
    for i in high:
        signals.append(f"存在高風險事實認定爭點 {i['id']}：{i['t']}")

    # Fail-safe：**案型辨識不出來、而且程序上也沒有可直接算出的不受理事由時，一律封鎖結論。**
    #
    # 這是對抗審查打出來的洞：原本只要把 case_type 換成系統不認得的值，
    # requires_human_conclusion 就從 True 變 False——「分類失敗」反而變成「解除封鎖」。
    # 方向完全相反：分類不出來代表系統**更**不了解這個案子，應該更保守。
    #
    # 為什麼要加「程序上沒有不受理事由」這個條件：逾期不受理（77-2）是期間引擎直接算出來的，
    # 屬可驗算層，那種案子不需要靠案型判斷就能寫結論（synthetic-ordinary-01 就是）。
    unknown_type = case_type not in substantive_types
    procedurally_resolved = bool(art77.get("clause"))
    if unknown_type and not procedurally_resolved:
        signals.append(
            f"案型「{case_type or '（空白）'}」不在已知需事實認定型清單內，"
            f"且程序上未命中可直接算出的不受理事由——系統無法判斷是否需實體審查，"
            f"保守封鎖結論段交人工。"
        )
        return True, signals

    return (substantive or bool(high)), signals


# ════════════════════════════════════════════════════════════════════
# 主文型語句偵測（P0-2 的結構性修法，2026-09-05 對抗覆核後第二版）
# ════════════════════════════════════════════════════════════════════
#
# C 型封鎖只把 `conclusion` 槽位刪掉，擋不住「把主文寫進理由段」。第一版改成
# 「標的×處置動詞的固定字元視窗共現」，覆核用 30 句真實主文打穿 29 句——因為那仍然是
# 一張「6 個動詞 × 6 個標的」的片語共現表：不含那 6 個動詞的主文（「本件訴願為無理由。」
# 「命被告機關重為處分。」「本府決定如主文。」）、把插入語寫長超過視窗的、拆成兩句寫的，
# 全部放行。
#
# 第二版改成**三層判準**，其中第三層才是真正的骨幹：
#
# 1. **法定處理結果動詞（強）**——訴願法 §77／§79–82 把可能的處理結果**窮舉**了：
#    不受理、駁回、撤銷、變更、維持、確認、命應為之處分、發回另為處分。
#    這一層的清單不是從測試案例長出來的，是從法條長出來的，因此是封閉集合。
#    句段內出現其中之一即命中，**不設字元視窗**（視窗本身就是繞法）。
# 2. **評價性結論述語（弱）**——「有／無理由」「有／無據」「可採」「違誤」等，
#    必須與案件標的**緊鄰**（≤4 字）才命中，避免「原處分認定之違規事實…尚無違誤」
#    這種講事實認定的句子被誤攔。
# 3. **兜底：C 型封鎖下，模型寫的句子若一個可查證的引用都沒有，一律交人工**
#    （在 `n6_gate.py` 實作）。這一層不看字串，所以**沒有寫法能繞過**：
#    覆核打穿的 9 種主文寫法沒有一句帶引用。片語層漏掉的，這層接住。
#
# 免責框架（negative evidence，也是結構性的）：
#   - **通則引述**：「按…第X條規定，…者，…」——講的是法條通則不是本案，
#     用「者，／時，」條件句 + 沒有「本件／系爭」來辨識。
#   - **轉述當事人**：「訴願人請求撤銷原處分之理由，…」——主張的主體不是機關。
#   免責只豁免第 1、2 層，**不豁免第 3 層兜底**。
#
# 正規化：刪所有空白（含全形）、統一標點、丟括號，所以「原 處 分 撤 銷 。」不是繞法。
_WS_RE = re.compile(r"[\s　 ]+")
_PUNCT_MAP = {
    "。": "。", "．": "。", ".": "。", "｡": "。", "！": "。", "!": "。",
    "，": "，", ",": "，", "、": "，", "；": "，", ";": "，", "：": "，", ":": "，",
}
_DROP_CHARS = "「」『』（）()〔〕【】《》〈〉“”\"'…—－-─·　"


def normalize_for_structure(text: str) -> str:
    """主文偵測前的正規化：刪空白、丟裝飾標點、統一句讀。

    這一步本身就是守門的一部分：排版空白、全形標點、括號都不該成為繞過封鎖的手段。
    """
    if not text:
        return ""
    t = _WS_RE.sub("", text)
    return "".join("" if c in _DROP_CHARS else _PUNCT_MAP.get(c, c) for c in t)


def _segments(text: str) -> list[str]:
    """以句號切句段。共現判在**同一句段內**，不用字元視窗——視窗可以靠加長插入語繞過。"""
    return [seg for seg in normalize_for_structure(text).split("。") if seg]


# ── 第 1 層：法定處理結果（來源＝訴願法，逐條標註出處）────────────────
# 這一層的清單**必須對得回法條**，不是從測試案例長出來的。每條後面標的條號就是它的來源；
# 新增任何一條都要能指出它在訴願法哪一條——指不出來的，代表它不屬於這一層。
# 第二輪覆核打穿 15/35，就是因為第一版漏了 §81 的「確認」、§82 的「作成…處分」、
# §61 的移送管轄、§60/§77 的撤回終結——那些不是罕見寫法，是法定處理結果本身。
DISPOSITION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("法定處理結果：不受理（訴願法 §77）",
     re.compile(r"不(?:予)?受理|不應受理|應不受理|(?:訴願|程序|受理)要件[^。]{0,6}?(?:不備|欠缺|不合)")),
    ("法定處理結果：駁回（訴願法 §79）", re.compile(r"駁回")),
    ("法定處理結果：撤銷／廢止（訴願法 §81）", re.compile(r"撤銷|廢止|註銷")),
    ("法定處理結果：變更／自為決定／酌減（訴願法 §81）",
     re.compile(r"變更原(?:處分|核定|決定)|予以變更|應予變更|自為決定|逕行更正|酌減|減為")),
    ("法定處理結果：確認違法／無效／不存在（訴願法 §81、行程法 §113）",
     re.compile(r"確認[^。]{0,30}(?:違法|無效|不存在|不成立)|宣示[^。]{0,20}違法|標的不存在")),
    ("法定處理結果：命應為之處分（訴願法 §82）",
     re.compile(r"另(?:為|行|就)[^。]{0,30}?(?:處分|處理|辦理)|重(?:為|新)[^。]{0,30}?(?:處分|決定|審查|審酌)"
                r"|作成[^。]{0,30}?(?:處分|決定)|另行[^。]{0,20}?辦理"
                r"|依本決定(?:書)?[^。]{0,20}?(?:意旨|所載)[^。]{0,20}?(?:辦理|處理|為之)")),
    ("法定處理結果：情況決定（訴願法 §83、§84）",
     re.compile(r"情況決定|損害賠償|賠償金額|協議賠償")),
    ("法定處理結果：停止執行（訴願法 §93）", re.compile(r"停止(?:原處分之)?執行|執行[^。]{0,8}應予停止")),
    ("法定處理結果：移送管轄（訴願法 §61）", re.compile(r"移(?:送|由|轉)[^。]{0,20}?(?:管轄|機關|審理)")),
    ("法定處理結果：程序終結／撤回（訴願法 §60）",
     re.compile(r"(?:訴願|程序|本件)[^。]{0,30}?終結|撤回訴願|訴願[^。]{0,8}?撤回|不再續行")),
    ("法定處理結果：否准／不予准許", re.compile(r"否准|不能准許|不予准許|礙難准許|不應准許")),
    ("指涉主文（決定如主文）", re.compile(r"如主文|決定如主文|主文所示")),
    ("發回（訴願法 §81）", re.compile(r"發回")),
)

# ⚠️ **這份清單不是、也不可能是窮舉。** 三輪覆核每一輪都找得到新寫法（第三輪找到
# §83 情況決定、§93 停止執行、§84 損害賠償、§81 自為決定／酌減）。
# 這正是為什麼 C 型案件的送出封鎖**不掛在這一層**（見 `n6_gate.py` 的 case 層封鎖）：
# 這一層漏抓的後果是「少標一個紅」，不是「放行一份系統沒看過的法律結論」。
# 新增規則時仍請標註條號——標不出來的，代表它可能不屬於「法定處理結果」這個類別。

# ── 第 2 層：評價性結論述語，必須與案件標的緊鄰（≤4 字）──────────────
# 「緊鄰」是為了避開「原處分認定之違規事實…尚無違誤」這種在講事實認定的句子。
_TARGET = (
    r"(?:本件|本案|系爭處分|系爭核定|原處分|原核定|原決定|原行政處分|原裁處|復查決定"
    r"|訴願人之訴願|訴願人之主張|訴願人之請求|訴願人所執|訴願|再訴願|復查|申請|請求|異議|主張)"
)
# 注意：**不含裸的「理由」**——「原處分之理由」「爭執之理由」是名詞，不是評價述語，
# 放進來會把「又訴願人請求撤銷原處分之理由，均係…之爭執」這種高頻理由段句子誤攔。
_VERDICT = (
    r"(?:有理由|無理由|有據|無據|可採|採據|違誤|不合法|於法有違|於法無據"
    r"|應予維持|予以維持|維持|准許|不足採|不足以動搖|不足取|洵屬無據)"
)
_NEG = r"(?:並無|尚無|核無|洵無|難謂|難認|不能|不足|非無|均無|核有|洵有|應|為|係|屬|認|尚屬|洵屬|實有|自屬|均不|皆不)"
VERDICT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "案件標的＋評價性結論述語（本件訴願為無理由／原處分並無違誤）",
        re.compile(rf"{_TARGET}[，]?{_NEG}?[^，。]{{0,4}}(?P<v>{_VERDICT})"),
    ),
    (
        "處置助詞＋處置動詞（應予駁回／爰予撤銷／應予維持）",
        re.compile(r"(?:應予|爰予|茲予|均予|准予|著予|洵予|應遭|應為)(?:駁回|撤銷|維持|變更|不受理|廢止|註銷|否准)"),
    ),
)

# ── 免責框架（negative evidence）──────────────────────────────────────
# 免責只豁免**第 2 層**（評價述語）；第 1 層的法定處理結果**任何框架都不豁免**。
#
# 為什麼第 1 層不給豁免（第二輪覆核打穿的就是這個）：
# 「按訴願法第81條規定，訴願有理由者，原處分撤銷，並命原處分機關另為適法之處分。」
# 這句話的殼是通則引述，內容卻是完整主文，而且帶著真實條號（同時繞過第 3 層兜底）。
# 純引述法定處理結果的句子（「按訴願法第79條規定，訴願無理由者，應以決定駁回之」）
# 因此會被誤攔——**這是刻意選的方向**：誤攔的代價是承辦人多按一次確認，
# 漏放的代價是系統替一份沒人看過的法律結論背書。
#
# 轉述當事人（逐命中級）只作用在第 2 層：只有**緊貼在**「主張／請求／稱」後面（≤6 字）
# 的評價述語才算轉述——「訴願人請求撤銷原處分之理由」的「撤銷」是當事人請求的內容。
_ATTRIBUTION_VERB = re.compile(r"(?:主張|請求|稱|略以|陳稱|指摘|爭執|答辯|認為)")
_ATTRIBUTOR = re.compile(r"(?:訴願人|申請人|原處分機關|被告機關|代理人)")
_ATTRIBUTION_GAP = 6
# 機關把當事人的請求採納為自己的決定：這時轉述框架失效。
_AGENCY_ADOPTS = re.compile(r"(?:本會|本府|本機關|本部|訴願會)[^。]{0,8}"
                           r"(?:同意|照准|准許|採納|從其所請|敬表同意|依其請求|照辦|准如所請)")


def _is_attributed(segment: str, hit_start: int) -> bool:
    """這個述語是不是「緊貼在當事人主張後面」＝轉述，不是機關自己的判斷。

    「之」是關鍵的結構分水嶺：「訴願人**請求**撤銷原處分」是當事人在請求（動詞，轉述）；
    「訴願人**之請求**為無理由」是機關在評價那個請求（名詞，機關自己的結論）。
    差一個「之」，主語就換人了，所以前面帶「之」的主張／請求不算轉述框架。
    """
    if not _ATTRIBUTOR.search(segment[:hit_start]):
        return False
    # 「訴願人請求撤銷原處分，**本會同意**」——前半是轉述，後半是機關把它變成自己的決定。
    # 只看述語前面會整句豁免掉，所以句段後半只要出現機關的採納語，就不算轉述。
    if _AGENCY_ADOPTS.search(segment):
        return False
    start = max(0, hit_start - _ATTRIBUTION_GAP)
    window = segment[start:hit_start]
    for m in _ATTRIBUTION_VERB.finditer(window):
        abs_pos = start + m.start()
        if abs_pos > 0 and segment[abs_pos - 1] == "之":
            continue  # 「之請求／之主張」是名詞，被評價的對象，不是轉述
        return True
    return False


def detect_conclusion_like(text: str) -> list[str]:
    """回傳命中的**結構規則名稱**（空 list = 不像主文）。

    回的是規則名不是片語，因為這裡判的是語法結構；blocker 訊息要能說出
    「因為出現了哪一種主文結構」，而不是「因為出現了某個字串」。
    """
    hits: list[str] = []
    for segment in _segments(text):
        for name, pattern in DISPOSITION_RULES:
            if name in hits:
                continue
            for m in pattern.finditer(segment):
                # 唯一豁免：處置動詞**緊貼**在當事人「主張／請求」後面＝轉述其請求內容。
                # 通則引述框架（按…者，…）**不再**豁免第 1 層——那個殼是覆核實測的繞法。
                if _is_attributed(segment, m.start()):
                    continue
                hits.append(name)
                break
        for name, pattern in VERDICT_RULES:
            if name in hits:
                continue
            for m in pattern.finditer(segment):
                # 判「述語」前面有沒有轉述框架，不是判「標的」前面——
                # 「訴願人請求撤銷原處分，經核於法有據」的評價落在「於法有據」，
                # 前面那個「請求撤銷」修飾的是標的，不能把整條評價一起豁免掉。
                anchor = m.start("v") if "v" in (m.groupdict() or {}) and m.start("v") >= 0 else m.start()
                if _is_attributed(segment, anchor):
                    continue
                hits.append(name)
                break
    return hits


def lamp_stats(doc: list[dict[str, Any]]) -> dict[str, int]:
    stats = {"r": 0, "y": 0, "g": 0}
    for block in doc:
        for s in block.get("ss", []):
            lamp = s.get("l")
            if lamp in stats:
                stats[lamp] += 1
    return stats


def attach_issue_refs(
    doc: list[dict[str, Any]], fact_issues: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """N6 用 N3 的 fact_issues 關鍵詞比對到句子上，事後補掛 I* ref。

    為什麼由 N6 補而不是 N5 產：讓模型認定「這句涉及哪個事實爭點」等於讓它做法律涵攝，
    那屬「請人工判斷」級（architecture §3.1 N6 說明）。比對不到就不掛，不視為錯誤。
    """
    refs: list[dict[str, Any]] = []
    for block in doc:
        for s in block.get("ss", []):
            text = s.get("t") or ""
            for issue in fact_issues:
                for kw in issue.get("matched_keywords", []) or issue.get("any_of", []):
                    if kw and kw in text:
                        s.setdefault("refs", [])
                        if issue["id"] not in s["refs"]:
                            s["refs"].append(issue["id"])
                        refs.append(
                            {"sentence_id": s.get("id"), "issue_id": issue["id"], "matched_by": f"keyword:{kw}"}
                        )
                        break
    return refs


def citation_states_for(cites: list[Citation]) -> list[str]:
    return [c.state for c in cites]
