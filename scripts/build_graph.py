#!/usr/bin/env python3
"""把 KB 全文的法條引用抽成三層知識圖（plans/kb-graph.md 的產物介面）。

    python3 scripts/build_graph.py --kb data/local/kb --manifest data/manifest.json \
        --laws backend/data/laws-snapshot.json --out data/local/kb-graph.json

紅線（CONSTITUTION §2/§4/§6）：
- **零 LLM**：全部 regex + 查表，不 import backend.llm、不呼叫 Bedrock。
- **不編造**：`stats` 每個欄位都是實際計數；「同法」上文解不出來就丟棄並計入
  `unresolved_same_law`，不猜一個法名補上。
- **產物不含個資**：只輸出法名、條號、案號、年度、結果、來源標籤，不輸出任何全文片段。
  產物本身不進 git（`.gitignore`），可由本腳本重建。

## 法名怎麼從句子裡切出來

全文是 `pdftotext -layout` 的產物，法條引用中間會硬換行（`空氣污染防\n制法第44條`），
所以比對前先把全部空白壓掉。壓掉之後最單純的
`([一-鿿]{2,12}(?:法|條例|規則|辦法|標準))第(\\d+)條` 有兩個方向相反的毛病：

1. **吃進前綴動詞**：`依訴願法`、`按空氣污染防制法`、`以訴願人違反空氣污染防制法`。
2. **把長法名切斷**：`{2,12}` 放不下 `廢棄物清理專業技術人員管理辦法`（14 字），
   只抽到 `棄物清理專業技術人員管理辦法`。

治法（`resolve_law_name`，三步，全部是查表，沒有統計猜測）：

1. 窗口放寬到 30 字（治毛病 2；現行法規名最長的 `行政院及各級行政機關訴願審議
   委員會審議規則` 是 21 字）。
2. **laws-snapshot 的法名當白名單**：窗口若以其中一部法收尾，直接取最長的那個後綴。
   這條吃掉語料裡九成以上的引用。
3. 白名單沒中（其餘上百部法規）就靠 `INNER_NOISE` —— 一組**法規名內部不可能出現**
   的字（依、按、違、反、揆、諸…）。從窗口裡最後一個這種字之後切開，再剝掉開頭的
   `HEAD_PHRASES`／`HEAD_NOISE`（前開、上揭、及、與…）。
4. 掃完全部語料後再做一次 **alias 收斂**（`build_alias`）：某個抽出來的法名 N，若它的
   某個**更短後綴** S 也是抽出來的法名、而且 S 的文件數是 N 的兩倍以上，就把 N 併到 S
   —— `合政府資訊公開法`→`政府資訊公開法`。倍數門檻是為了不把真正的長法名併掉
   （`一般廢棄物回收清除處理辦法` 遠比任何短後綴常見，不會被併）。

   一開始試過非監督斷詞的「左鄰字自由度」，在這份語料上**不成立**：`制法` 左邊同時
   有 `防`（空氣污染防制法）與 `管`（噪音管制法），自由度訊號被同尾法名互相污染，
   實測 400 份抽出 `防制法`、`開法`、`理法` 這種碎片，所以改用上面這組查表規則。

殘留（誠實標示，不假裝解決）：判解那批是流暢論述文，仍有少數 df=1 的碎片
（`用所得稅法`、`有教師法`）；`空污法`、`資公法`、`廢清法` 是決定書自己定義的簡稱，
不是抽壞；`廢棄清理法`、`廢棄物理法` 是 pdftotext 掉字，原文就長那樣。
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

# 2026-09-12 稽核：原本只有五種結尾詞，漏掉 `準則`／`細則`——而
# `違反廢棄物清理法罰鍰額度裁罰準則` 被引 2,696 次、`環境教育法施行細則` 221 次，
# 是承辦人最常用的裁罰工具，整批不在圖裡。召回率因此只有 65.3%。
TERMINATORS = ("法", "條例", "規則", "辦法", "標準", "準則", "細則", "通則", "要點")
TERMINATOR_RE = "(?:法|條例|規則|辦法|標準|準則|細則|通則|要點)"
# 下位法規的結尾詞。它們的「本法」指的是**母法**，不是自己（見 `quote_scopes`）。
SUBORDINATE_TAILS = ("條例", "規則", "辦法", "標準", "準則", "細則", "通則", "要點")
# 窗口放寬到 30 字，長法名才不會被切頭。
CITE_RE = re.compile(rf"([一-鿿]{{2,30}}{TERMINATOR_RE})第(\d+(?:之\d+)?)條")
# 「同法／本法／該辦法第N條」：法名要靠上文解析，解不出來就丟棄，不猜。
SAME_LAW_RE = re.compile(rf"(?:同|本|該)({TERMINATOR_RE})第(\d+(?:之\d+)?)條")
# 語料中每一次「像法名的字串」，不限於後面接「第N條」的——上文回溯要靠這批。
MENTION_RE = re.compile(rf"[一-鿿]{{2,30}}{TERMINATOR_RE}")
# 判字號限定法院的案件類別字（判／裁／訴／上／簡…）。放寬成 `[一-鿿]{0,4}` 會把
# 「109年新北環稽字第00-000-000060號」這種行政文號也吃成判解，憑空長出假節點。
JUDGMENT_RE = re.compile(r"(\d{2,3})年度?([判裁訴上簡抗聲再更全停交]{1,2})字第(\d+)號")
INTERPRETATION_RE = re.compile(r"釋字第(\d+)號")
# 召回率量測用：語料裡所有「第N條」的出現次數當分母。
ARTICLE_REF_RE = re.compile(r"第\d+(?:之\d+)?條")
# 召回率拆解用：每一個「第N條」前面長什麼樣，決定它屬於哪一類（四類加總 = 總數）。
REF_DIRECT_RE = re.compile(rf"[一-鿿]{{2,30}}{TERMINATOR_RE}$")
REF_PRONOUN_RE = re.compile(rf"(?:同|本|該){TERMINATOR_RE}$")
REF_ENUM_RE = re.compile(r"[、及或至與和暨][」』）]*$")

# 法規名**內部**不可能出現的字（動詞、連接詞、指示詞）。窗口裡最後一個這種字之後
# 才是法名本體。刻意排除會出現在真法名裡的字，每個都踩過：
#   則（規則）、業（事業廢棄物／業務監督管理辦法）、採（政府採購法）、師（教師法）、
#   非（非訟事件法）、條（條例）、關／機（行政機關訴願審議委員會審議規則）、
#   人（公職人員選舉罷免法）、得／所（所得稅法）、有（有限合夥法）、
#   原（原住民族基本法）、經（經濟部…辦法）、及（…方法及設施標準）、
#   開（政府資訊公開法）、準（標準）、合（合作社法）、工（工廠管理輔導法）、
#   認／核（有害事業廢棄物認定標準、核定…辦法）、違（違章建築處理辦法）。
# 「違反」靠留在表內的「反」切掉就夠了，不必連「違」一起犧牲。
INNER_NOISE = set(
    "依按查爰遂惟揆諸復又再另反已亦即揭該其者而故茲若但且如因犯此並次末首以為係暨未觀逕款項號第"
    "參酌屬涉符究何段姓據時是否援引適蓋慮倘僅卻含嗣訂稱謂落莫背值甚擬列附俱徒難逾遭致遞"
)
# 法名不可能以這些字開頭（`INNER_NOISE` 之外再加一批只在詞頭當雜訊的字）。
HEAD_NOISE = INNER_NOISE | set("及與或之上前至於在向對非業則採師用指述考應論際供負仍須遵守達認核條")
HEAD_PHRASES = re.compile(
    r"^(?:前開|上開|前揭|上揭|首揭|前述|上述|該管|所稱|所定|系爭|準用|適用|援用|參酌|參照|規定|類推)"
)

# 「同法」這類代名詞與光禿禿的「管理辦法」都不是法名，要回上文找本尊。
GENERIC_TAILS = {
    "管理辦法", "處理辦法", "執行辦法", "實施辦法", "許可辦法",
    "排放標準", "設施標準", "管制標準", "裁罰標準", "細則", "規定",
}
PRONOUN_NAMES = {p + t for p in "同本該" for t in TERMINATORS} | set(TERMINATORS) | GENERIC_TAILS
# 「主管機關執行本法」「條及同法」也是代名詞引用，只是前面還黏著散文。
# 負向前查擋掉 `環境基本法`／`原住民族基本法` —— 那個「本法」是法名的一部分。
PRONOUN_SUFFIX_RE = re.compile(rf"(?<!基)(?:同|本|該){TERMINATOR_RE}$")
# 上文回溯只認「像法規」的名字：`非法`、`違法`、`合法`、`行政法`、`立法` 這些散文詞
# 也結尾是「法」，被 MENTION_RE 掃得到，當成「同法」的先行詞會把條號掛到假法規上。
MIN_CONTEXT_DF = 2

MIN_ALIAS_DF = 2  # 併過去的短法名至少要出現在這麼多份文件裡
ALIAS_RATIO = 2  # 短後綴的文件數要是長字串的幾倍才敢併（防止把真正的長法名併掉）

VERIFY_OK = "ok"
VERIFY_UNVERIFIABLE = "unverifiable"
VERIFY_SUSPECT = "suspect"

# 判解／函釋掛在這兩個 layer-1 節點下。laws-snapshot 的 precedents（17 筆）與
# interpretations（2 則）是「本案相關」清單、不是全國判解總表，所以不在清單裡
# 只能算 unverifiable，**不能**算 suspect。
JUDICIAL_LAW = "司法判解"
INTERPRETATION_LAW = "司法院解釋"

OFFICIAL_NAME_RE = re.compile(
    r"^\d+\.(?P<year>\d{2,3})年-(?P<topic>[^-]+)-(?P<clause>[^-]+)-(?P<reason>[^-]+?)"
    r"(?:-(?P<outcome>[^-]+))?$"
)

MAX_BYTES = 4 * 1024 * 1024
TRUNCATE_KEEP = 8  # 超過上限時每案保留引用次數最高的前 N 條


def compress(text: str) -> str:
    """把全部空白壓掉——pdftotext 會在法條引用中間硬換行。"""
    return re.sub(r"\s+", "", text)


def terminator_of(name: str) -> str:
    """法名尾巴是哪一種（法／條例／規則／辦法／標準）——上文回溯要同型比對。"""
    for t in SUBORDINATE_TAILS:
        if name.endswith(t):
            return t
    return "法"


def strip_head(name: str) -> str:
    """剝掉詞頭雜訊：先剝多字詞（前開、上揭…），再逐字剝。剩 2 字就停。"""
    while True:
        stripped = HEAD_PHRASES.sub("", name)
        if stripped != name and len(stripped) >= 2:
            name = stripped
            continue
        if len(name) > 2 and name[0] in HEAD_NOISE:
            name = name[1:]
            continue
        return name


def names_a_statute_after_weifan(window: str, lexicon: frozenset[str]) -> bool:
    """`違反` 後面接著一部**已知的法**、而且字串還沒結束 → 這個「違反」是法規名的一部分。

    `違反廢棄物清理法罰鍰額度裁罰準則` ✓（違反＋廢棄物清理法＋罰鍰額度裁罰準則）
    `違反管理辦法` ✗（違反後面沒有法名）
    `違反道路交通管理條例` ✗（違反後面是條例、而且到此為止，是動詞不是名稱）
    """
    for m in re.finditer("違反", window):
        rest = window[m.end():]
        for k in range(2, min(len(rest), 24)):
            head = rest[:k]
            if head.endswith("法") and head in lexicon and len(rest) > k:
                return True
    return False


def resolve_law_name(
    window: str, whitelist: frozenset[str], lexicon: frozenset[str] | None = None
) -> str:
    """從引用窗口切出法名本體（白名單最長後綴 → 內部雜訊字切點 → 詞頭剝除）。"""
    for start in range(len(window)):
        if window[start:] in whitelist:
            return window[start:]
    # `違反廢棄物清理法罰鍰額度裁罰準則`、`空氣污染行`為`管制執行準則`——這些
    # 下位法規的名稱**本身**就含「違反」「為」。窗口以下位法規結尾時放寬切點，
    # 但 `違`／`反` 的放寬**有條件**：後面要真的接著一部已知的法（`names_a_statute_after_weifan`），
    # 否則 `訴願人違反○○辦法` 會留下 `違反管理辦法` 這種假法規名，而且會被當先行詞用。
    noise = INNER_NOISE
    if any(window.endswith(t) for t in SUBORDINATE_TAILS):
        noise = INNER_NOISE - {"為"}
        if names_a_statute_after_weifan(window, lexicon or whitelist):
            noise = noise - {"違", "反"}
    cut = 0
    for i, ch in enumerate(window):
        if ch in noise:
            cut = i + 1
    if len(window) - cut < 2:  # 切完剩不到 2 字代表切點落在法名裡，退回整個窗口
        cut = 0
    return strip_head(window[cut:])


def build_alias(
    per_doc: list[list[tuple[str, str]]],
    whitelist: frozenset[str],
    known: frozenset[str] = frozenset(),
    expansions: dict[str, str] | None = None,
) -> dict[str, str]:
    """把「還黏著散文前綴」的法名併到它那個更常見的短後綴上（含遞移收斂）。

    兩條併法，取最長的合格後綴：
    - 後綴的文件數是它的 `ALIAS_RATIO` 倍以上 —— 擋住把真正的長法名併掉
      （`一般廢棄物回收清除處理辦法` 遠比任何短後綴常見，不會被併）。
    - 這個名字本身沒進 `known`（沒被獨立引用過兩次），而後綴至少一樣常見 ——
      專治判解那批只出現一次的散文黏連（`條及臺北縣騎樓…設置標準`）。
    - 後綴是**已被還原過的簡稱**（`expansions`，例如 `資公法`→`政府資訊公開法`）——
      簡稱一旦被還原，它就不再以自己的名字出現在語料統計裡，`有資公法`、
      `不論資公法` 這些黏連變體會失去合併目標、變成獨立的假法規節點
      （2026-09-12 稽核）。這條就是把還原後的全名補回合併目標池。
    """
    expansions = expansions or {}
    tf: collections.Counter = collections.Counter()
    df: collections.Counter = collections.Counter()
    for pairs in per_doc:
        seen = {law for law, _ in pairs}
        for law, _ in pairs:
            tf[law] += 1
        for law in seen:
            df[law] += 1

    direct: dict[str, str] = {}
    for name in tf:
        if name in whitelist:
            continue
        for i in range(1, len(name) - 1):  # 由長到短，取最長的合格後綴
            suffix = name[i:]
            # 只有「散文黏連」才靠簡稱表找合併目標。`違反廢棄物清理法罰鍰額度裁罰準則`
            # 的後綴也是 `裁罰準則`，但它多出來的前綴裡有法名 → 它是真名字，
            # 不能被併到另一部準則去（會把兩部不同的準則合成一個節點）。
            if (
                suffix in expansions
                and expansions[suffix] != name
                and not prefix_names_a_statute(name, suffix, known or whitelist)
            ):
                direct[name] = expansions[suffix]
                break
            if suffix not in tf:
                continue
            strong = df[suffix] >= MIN_ALIAS_DF and df[suffix] >= ALIAS_RATIO * df[name]
            # 後綴被引用的次數不少於長字串 → 長的那個是散文黏連（`有資公法`→`資公法`）。
            # 判解那批的黏連常常只出現在同一份文件裡，df 分不開，要看 tf。
            # `tf[suffix] >= MIN_ALIAS_DF` 這道閘很重要：少了它，`所得稅法` 會被併進
            # 只出現一次的斷字 `得稅法`（切在法名中間，方向剛好相反）。
            attested = tf[suffix] >= MIN_ALIAS_DF and tf[suffix] >= tf[name]
            # 名字自己沒被獨立引用過，被剝掉的又全是詞頭雜訊字 → 併。
            orphan = name not in known and (
                df[suffix] > df[name] or all(c in HEAD_NOISE for c in name[:i])
            )
            if strong or attested or orphan:
                direct[name] = suffix
                break

    alias: dict[str, str] = {}
    for name in direct:
        target, seen = name, {name}
        while target in direct and direct[target] not in seen:
            target = direct[target]
            seen.add(target)
        alias[name] = target
    return alias


def is_pronoun(name: str) -> bool:
    """`同法`／`本法`／`同法施行細則`／光禿禿的`管理辦法` 都不是法名本體，要回上文找本尊。

    前綴判斷（`同`/`本`/`該` 開頭）是必要的：`同法施行細則第6條` 若只看後綴會被當成
    一部叫「法施行細則」的法規。我國沒有任何法規以這三個字開頭，所以這條沒有誤傷。
    """
    return (
        name in PRONOUN_NAMES
        or name.startswith(("同", "本", "該"))
        or bool(PRONOUN_SUFFIX_RE.search(name))
    )


QUOTE_OPEN, QUOTE_CLOSE = "「", "」"
# 引述某法規條文前的引導語：`環境講習執行辦法第2條規定：「…`
INTRODUCER_RE = re.compile(
    rf"([一-鿿]{{2,40}}{TERMINATOR_RE})(?:第\d+(?:之\d+)?條)?"
    rf"(?:第[\d一二三四五六七八九十]+項)?[^」「]{{0,10}}?[：:]?$"
)
# 語料自己下的定義：`空氣污染防制法（下稱本法）`、`…裁罰準則（下稱裁罰準則）`
DEFINITION_RE = re.compile(
    rf"([一-鿿]{{2,40}}{TERMINATOR_RE})[（(](?:以下簡稱|以下稱|下簡稱|下稱|簡稱)([一-鿿]{{2,20}}?)[）)]"
)


def quote_scopes(text: str) -> list[tuple[int, int]]:
    """回傳所有「…」的範圍（含巢狀，內層也會列出）。決定書大量引述法條原文，
    而「本法」這種代名詞是**被引述那部法規的內部用語**，範圍就是這對引號。"""
    out: list[tuple[int, int]] = []
    stack: list[int] = []
    for i, ch in enumerate(text):
        if ch == QUOTE_OPEN:
            stack.append(i)
        elif ch == QUOTE_CLOSE and stack:
            out.append((stack.pop(), i))
    return out


def innermost_scope(scopes: list[tuple[int, int]], pos: int) -> tuple[int, int] | None:
    best = None
    for start, end in scopes:
        if start < pos < end and (best is None or start > best[0]):
            best = (start, end)
    return best


def prefix_names_a_statute(long_name: str, short: str, lexicon: frozenset[str]) -> bool:
    """`long_name` 比 `short` 多出來的那段前綴裡，有沒有一部已知的法？

    這是「真法規名」與「散文黏連」的分水嶺：
      `違反廢棄物清理法罰鍰額度裁罰準則` ← `裁罰準則`，多出來的是
      `違反廢棄物清理法罰鍰額度`，裡面有「廢棄物清理法」→ 這是真名字。
      `有政府資訊公開法` ← `政府資訊公開法`，多出來的只有「有」→ 這是散文。
    """
    prefix = long_name[: len(long_name) - len(short)]
    return any(
        cand in prefix and (cand.endswith("法") or cand.endswith("條例"))
        for cand in lexicon
    )


def parent_statute(name: str, candidates: frozenset[str]) -> str | None:
    """從下位法規的**名稱本身**取出母法：`環境教育法施行細則` → `環境教育法`。

    這不是統計猜測——我國下位法規的命名就是「〈母法名〉施行細則」、
    「違反〈母法名〉…裁罰準則」，母法名是標題的子字串。但只認**已知的法名**
    （白名單或語料中被獨立引用過的），否則 `事業廢棄物貯存清除處理方法及設施標準`
    會被切出一個叫「事業廢棄物貯存清除處理方法」的假母法。
    """
    best = None
    for cand in candidates:
        if cand != name and cand in name and (cand.endswith("法") or cand.endswith("條例")):
            if not any(cand.endswith(t) for t in SUBORDINATE_TAILS):
                if best is None or len(cand) > len(best):
                    best = cand
    return best


def verify_article(law: str, article: str, laws: dict[str, list[str]]) -> str:
    """三態：條號對得上＝ok；該法不在 snapshot 內＝無從查證；在清單內但條號不在＝可疑。"""
    if law not in laws:
        return VERIFY_UNVERIFIABLE
    return VERIFY_OK if article in laws[law] else VERIFY_SUSPECT


def extract_citations(
    text: str,
    whitelist: frozenset[str],
    known: frozenset[str] | None = None,
    out_expansions: dict[str, str] | None = None,
    out_paths: collections.Counter | None = None,
) -> tuple[list[tuple[str, str]], int]:
    """回傳 [(法名, 條號), ...] 與代名詞解不出來而丟棄的筆數。

    代名詞（同法／本法／本準則…）依這個順序解，**解不出來就丟棄並計數，不猜**
    （CONSTITUTION §2）：

    1. **同一對引號內的明示定義**：`空氣污染防制法（下稱本法）` → 本法 = 空氣污染防制法。
    2. **引號的引導語**（`X第N條規定：「…」` 裡的 X）：
       - 尾型相符（引述辦法、寫「本辦法」）→ 就是 X 自己。
       - 寫「本法」但 X 是下位法規 → 指的是 X 的**母法**，從 X 的名稱取
         （`環境教育法施行細則` → `環境教育法`）；名稱裡沒有母法就丟棄。
    3. 都不適用 → 退回「該位置之前最近一次、同尾型的法名」。

    第 2 條是 2026-09-12 稽核挖出來的：全語料有 1,116 次「引述下位法規、內文寫本法」，
    舊的『最近同尾型 mention』對這批**系統性地解錯**——`環境講習執行辦法第2條…本法第23條`
    會解成空氣污染防制法（而且 verify=ok，看不出來），正解是環境教育法。
    """
    scopes = quote_scopes(text)
    lexicon = known if known is not None else whitelist

    def is_candidate(name: str) -> bool:
        if known is not None:
            return name in known
        return name in whitelist or len(name) >= 4

    mentions: list[tuple[int, str]] = []
    for m in MENTION_RE.finditer(text):
        name = resolve_law_name(m.group(0), whitelist, lexicon)
        if not is_pronoun(name) and is_candidate(name):
            mentions.append((m.start(), name))

    # 明示定義：(位置, 所屬引號範圍, 簡稱) -> 全名
    definitions: list[tuple[int, tuple[int, int] | None, str, str]] = []
    for m in DEFINITION_RE.finditer(text):
        full = resolve_law_name(m.group(1), whitelist, lexicon)
        if not is_pronoun(full):
            definitions.append((m.start(), innermost_scope(scopes, m.start()), m.group(2), full))

    introducers: dict[tuple[int, int], str] = {}
    for scope in scopes:
        before = text[max(0, scope[0] - 60):scope[0]]
        m = INTRODUCER_RE.search(before)
        if m:
            name = resolve_law_name(m.group(1), whitelist, lexicon)
            if not is_pronoun(name):
                introducers[scope] = name

    def defined_as(pos: int, short: str) -> str | None:
        scope = innermost_scope(scopes, pos)
        best = None
        for at, dscope, dshort, full in definitions:
            if at < pos and dshort == short and (dscope == scope or dscope is None):
                best = full
        if best:
            if out_expansions is not None and best != short:
                out_expansions[short] = best
            return best
        # 沒有明示定義時，若這個名字剛好是**同一份文件裡**某個更長法規名的結尾，
        # 它就是那部法規的縮寫（`裁罰準則` ← `違反廢棄物清理法罰鍰額度裁罰準則`）。
        # 要求「結尾」而非「包含」，所以 `廢棄物清理法` 不會被吸進裁罰準則。
        #
        # **只往「真名字」的方向還原**（2026-09-12 稽核）：mention 裡本來就充滿散文
        # 黏連名，不設閘會朝垃圾方向還原——`政府資訊公開法` 被「還原」成
        # `有政府資訊公開法` 33 次、`合政府資訊公開法` 15 次、
        # `性質相同之政府資訊公開法` 9 次。判準是多出來的前綴裡有沒有一部已知的法：
        # `違反廢棄物清理法罰鍰額度`＋`裁罰準則` 有 → 還原；`有`＋`政府資訊公開法` 沒有 → 不還原。
        longest = None
        for at, name in mentions:
            if at < pos and name != short and name.endswith(short):
                if not prefix_names_a_statute(name, short, lexicon):
                    continue
                if longest is None or len(name) > len(longest):
                    longest = name
        if longest and out_expansions is not None:
            out_expansions[short] = longest
        return longest

    def nearest_mention(pos: int, kind: str) -> str | None:
        best = None
        for at, name in mentions:
            if at >= pos:
                break
            if terminator_of(name) == kind:
                best = name
        return best

    def tally(key: str) -> None:
        if out_paths is not None:
            out_paths[key] += 1

    def resolve_pronoun(pos: int, word: str, kind: str) -> str | None:
        explicit = defined_as(pos, word)
        if explicit:
            tally("definition")
            return explicit
        scope = innermost_scope(scopes, pos)
        if scope is not None:
            quoted = introducers.get(scope)
            if quoted is None:
                tally("quote_no_introducer_dropped")
                # 在引號裡、但引導語辨識不出（`環境部…號函示：「…」` 這種沒有法規名的）。
                # **丟棄並計數，不退回最近 mention**（2026-09-12 拍板）：引號內的代名詞
                # 屬於被引述的那份文件，拿引號外最近的法名去填就是系統性猜錯。
                # 寧可 unresolved 多幾筆，也不要留錯的邊。
                return None
            if terminator_of(quoted) == kind:
                tally("quote_introducer_itself")
                return quoted
            if kind == "法" and any(quoted.endswith(t) for t in SUBORDINATE_TAILS):
                # 引述下位法規、內文寫「本法」→ 指母法。名稱裡找不到母法就丟棄，不猜。
                parent = parent_statute(quoted, lexicon)
                tally("quote_parent_resolved" if parent else "quote_parent_not_derivable_dropped")
                return parent
            tally("quote_kind_mismatch_dropped")
            return None
        near = nearest_mention(pos, kind)
        tally("nearest_mention" if near else "no_antecedent_dropped")
        return near

    out: list[tuple[str, str]] = []
    unresolved = 0
    covered: set[int] = set()

    for m in CITE_RE.finditer(text):
        covered.add(m.end())
        name = resolve_law_name(m.group(1), whitelist, lexicon)
        if not is_pronoun(name):
            # 語料自己定義的簡稱（`…裁罰準則（下稱裁罰準則）`）要還原成全名，
            # 否則同一部準則會裂成兩個節點。
            out.append((defined_as(m.start(), name) or name, m.group(2)))
            continue
        word = name if name.startswith(("同", "本", "該")) else "同" + terminator_of(name)
        if name.startswith(("同", "本", "該")):
            law = resolve_pronoun(m.start(), word, terminator_of(name))
        else:
            # 光禿禿的 `管理辦法`／`標準`：沒有代名詞前綴，只能回上文找同尾型的。
            law = nearest_mention(m.start(), terminator_of(name))
            tally("bare_tail_nearest" if law else "bare_tail_dropped")
        if law is None:
            unresolved += 1
            continue
        out.append((law, m.group(2)))

    # 「，同法第79條」前面只有 1 個字，CITE_RE 的 {2,30} 吃不到，補一輪。
    for m in SAME_LAW_RE.finditer(text):
        if m.end() in covered:
            continue
        law = resolve_pronoun(m.start(), text[m.start():m.start() + 1] + m.group(1), m.group(1))
        if law is None:
            unresolved += 1
            continue
        out.append((law, m.group(2)))

    return out, unresolved


def extract_authorities(text: str) -> list[tuple[str, str]]:
    """判解與釋字引用 → (layer-1 權威來源名, 字號 label)。"""
    out: list[tuple[str, str]] = []
    for m in JUDGMENT_RE.finditer(text):
        out.append((JUDICIAL_LAW, f"{m.group(1)}年度{m.group(2)}字第{m.group(3)}號"))
    for m in INTERPRETATION_RE.finditer(text):
        out.append((INTERPRETATION_LAW, f"釋字第{m.group(1)}號"))
    return out


def authority_index(snapshot: dict) -> set[str]:
    """laws-snapshot 的 precedents／interpretations 攤平成字號字串，供 verify 比對。"""
    index = {f"釋字第{no}號" for no in snapshot.get("interpretations", [])}
    for p in snapshot.get("precedents", []):
        index.add(f"{p['year']}年度{p['type']}字第{p['no']}號")
    return index


def parse_official_name(stem: str) -> dict[str, str]:
    """官方那批的檔名自帶標籤：`01.110年-社會救助事件-77(1)-逾期不補正-不受理`。"""
    m = OFFICIAL_NAME_RE.match(stem)
    if not m:
        return {}
    got = m.groupdict()
    return {k: got[k] for k in ("year", "topic", "clause", "reason") if got.get(k)}


def case_meta(entry: dict, stem: str) -> dict:
    """case 節點的中繼資料。公開爬蟲那批靠 manifest，官方那批靠檔名。"""
    prov = entry.get("provenance")
    path = entry["path"]
    if prov == "official":
        if "司法院釋字及行政判解" in path:
            prov = "judicial"
        elif "行政函釋" in path:
            prov = "interpretation"
    meta: dict = {"prov": prov, "outcome": entry.get("outcome")}
    if entry.get("case_no"):
        meta["id"] = entry["case_no"]
        meta["label"] = entry["case_no"]
        meta["year"] = entry.get("year")
        if entry.get("category"):
            meta["cat"] = entry["category"]
    else:
        meta["id"] = stem
        meta["label"] = stem
        meta.update(parse_official_name(stem))
    return meta


def resolve_path(kb_root: pathlib.Path, rel: str) -> pathlib.Path:
    """manifest 的 path 以 `kb/` 開頭，--kb 指的就是那個 kb 目錄，不要接兩次。"""
    base = kb_root.parent if rel.split("/", 1)[0] == kb_root.name else kb_root
    return base / rel


def build_graph(
    entries: list[dict],
    kb_root: pathlib.Path,
    laws: dict[str, list[str]],
    authorities: set[str] | None = None,
) -> dict:
    authorities = authorities or set()
    whitelist = frozenset(laws)

    law_w: dict[str, int] = collections.defaultdict(int)
    art_nodes: dict[tuple[str, str], dict] = {}
    case_nodes: list[dict] = []
    cites: list[tuple[str, str, int]] = []
    docs_with_citation = 0
    unresolved_same_law = 0

    texts = [
        compress(resolve_path(kb_root, e["path"]).read_text(encoding="utf-8", errors="ignore"))
        for e in entries
    ]

    # 第一輪：先掃出「真的被寫成 `○○法第N條` 引用過」的法名，當第二輪「同法」先行詞的
    # 白名單。**只收直接引用**——若把第一輪自己解出來的「同法」結果也收進來，
    # 第一輪的爛先行詞（`仲介非法`、`訴願書不合法`）會被漂白成第二輪的合法先行詞。
    seed: collections.Counter = collections.Counter()
    for text in texts:
        direct = {resolve_law_name(m.group(1), whitelist) for m in CITE_RE.finditer(text)}
        seed.update({n for n in direct if not is_pronoun(n)})
    known = frozenset({n for n, c in seed.items() if c >= MIN_CONTEXT_DF} | set(whitelist))

    # 第二輪：逐份抽引用。alias 收斂要看過全語料的文件數才算得出來，所以先全部抽完。
    scanned: list[tuple[dict, list[tuple[str, str]], list[tuple[str, str]]]] = []
    expansions: dict[str, str] = {}
    paths: collections.Counter = collections.Counter()
    for entry, text in zip(entries, texts):
        pairs, unresolved = extract_citations(text, whitelist, known, expansions, paths)
        unresolved_same_law += unresolved
        scanned.append((entry, pairs, extract_authorities(text)))

    alias = build_alias([pairs for _, pairs, _ in scanned], whitelist, known, expansions)

    # 第二輪：套 alias 之後才建節點，`合政府資訊公開法` 不會變成獨立的法規節點。
    # 召回率：分母是語料裡「第N條」的總出現次數，分子是實際抽出的條文引用數。
    # 這個量測是稽核 2026-09-12 指出的缺口——先前只驗過精確率（抽出來的對不對），
    # 沒有量過召回率（該抽的有沒有漏）。
    breakdown: collections.Counter = collections.Counter()
    for text in texts:
        for m in ARTICLE_REF_RE.finditer(text):
            left = text[: m.start()]
            if REF_PRONOUN_RE.search(left[-6:]):
                breakdown["pronoun"] += 1
            elif REF_DIRECT_RE.search(left[-32:]):
                breakdown["direct"] += 1
            elif REF_ENUM_RE.search(left[-4:]):
                breakdown["enumeration_continuation"] += 1
            else:
                breakdown["other"] += 1
    recall = {
        "article_refs_in_corpus": sum(breakdown.values()),
        "article_refs_captured": sum(len(pairs) for _, pairs, _ in scanned),
        "breakdown": dict(breakdown),
    }
    # 分母可能是 0（整批都沒有「第N條」，例如只有函釋的小語料）——回 None 不回 0，
    # 「沒有東西可抽」跟「一條都沒抽到」是兩件事，混起來報告會寫錯。
    recall["rate"] = (
        round(recall["article_refs_captured"] / recall["article_refs_in_corpus"], 4)
        if recall["article_refs_in_corpus"]
        else None
    )
    recall["dropped_unresolved_pronoun"] = unresolved_same_law
    recall["note"] = (
        "四類加總＝article_refs_in_corpus。抓到的＝direct＋(pronoun−主動放棄)。"
        "`enumeration_continuation` 是能力缺口（`A法第5條、第14條` 的第二個條號，"
        "需要跨逗號延續先行詞的狀態機），2026-09-12 評估風險大於收益、本輪不做。"
        "`dropped_unresolved_pronoun` 是誠實成本：看得見但解不出先行詞，選擇不猜。"
        "`other` 是引文內部的交互指涉（「違反第8條」）、表格欄位、"
        "以及「第77、91條」這類複合寫法。"
    )

    docs_with_article = 0
    for entry, pairs, auths in scanned:
        per_target: collections.Counter = collections.Counter()
        for law, article in pairs:
            per_target[(alias.get(law, law), article, "art")] += 1
        if per_target:
            docs_with_article += 1
        for law, label in auths:
            per_target[(law, label, "auth")] += 1

        meta = case_meta(entry, pathlib.Path(entry["path"]).stem)
        case_id = f"case:{meta.pop('id')}"
        if per_target:
            docs_with_citation += 1
        case_nodes.append(dict(id=case_id, layer=3, w=len(per_target), **meta))

        for (law, article, kind), count in per_target.items():
            key = (law, article)
            if key not in art_nodes:
                if kind == "art":
                    node = {
                        "id": f"art:{law}§{article}",
                        "label": f"{law} §{article}",
                        "verify": verify_article(law, article, laws),
                    }
                else:
                    prefix = "jud" if law == JUDICIAL_LAW else "int"
                    node = {
                        "id": f"{prefix}:{article}",
                        "label": article,
                        "verify": VERIFY_OK if article in authorities else VERIFY_UNVERIFIABLE,
                    }
                art_nodes[key] = {**node, "layer": 2, "law": law, "article": article, "w": 0}
            art_nodes[key]["w"] += 1
            law_w[law] += count
            cites.append((case_id, art_nodes[key]["id"], count))

    nodes: list[dict] = [
        {"id": f"law:{law}", "layer": 1, "label": law, "w": w} for law, w in law_w.items()
    ]
    nodes += list(art_nodes.values())
    nodes += case_nodes
    links: list[dict] = [
        {"s": n["id"], "t": f"law:{n['law']}", "k": "belongs"} for n in art_nodes.values()
    ]
    links += [{"s": s, "t": t, "k": "cites", "w": w} for s, t, w in cites]
    art_nodes_by_id = {n["id"]: n for n in art_nodes.values()}

    # stats 一律**分開**計數。2026-09-12 稽核：判解字號與釋字先前被混進 `articles`，
    # 於是「articles 712」其實是 536 條法條 + 176 個字號，「article_verified 233」
    # 也把判解的 19 筆算進去——報告直接引用就會寫錯。
    art_only = [n for n in art_nodes.values() if n["id"].startswith("art:")]
    jud_only = [n for n in art_nodes.values() if n["id"].startswith("jud:")]
    int_only = [n for n in art_nodes.values() if n["id"].startswith("int:")]
    art_verify = collections.Counter(n["verify"] for n in art_only)
    auth_verify = collections.Counter(n["verify"] for n in jud_only + int_only)
    cites_to_articles = sum(1 for x in links if x["k"] == "cites" and x["t"].startswith("art:"))
    n_cites = sum(1 for x in links if x["k"] == "cites")
    real_laws = [k for k in law_w if k not in (JUDICIAL_LAW, INTERPRETATION_LAW)]
    # 歧義簡稱：某個法規名是另一個（更長的）法規名的結尾，代表它其實是縮寫、
    # 而且在它出現的那份文件裡找不到可還原的全名（找得到的已經在抽取時還原了）。
    # **零命中也要留這個欄位**——零命中的檢查在沒出事前永遠是綠的，等語料一換
    # 就靜默失效。有數字它才會自己說話。
    ambiguous = [
        n for n in real_laws
        if any(other != n and other.endswith(n) for other in real_laws)
    ]
    ambiguous_cites = sum(
        x["w"] for x in links
        if x["k"] == "cites" and x["t"].startswith("art:")
        and art_nodes_by_id[x["t"]]["law"] in ambiguous
    )
    return {
        "generated": "2026-09-12",
        "source": (
            f"data/manifest.json 列出的 {len(entries)} 份 txt（regex 引用抽取，零 LLM）。"
            "**不是**磁碟上 data/local/kb/ 的全部——該目錄有 6,997 個 .txt，"
            "其中 4,520 份（kb/public/行政函釋/）不在 manifest、未被掃描。"
        ),
        "stats": {
            "docs_scanned": len(entries),
            "docs_with_citation": docs_with_citation,
            "docs_with_article_citation": docs_with_article,
            "laws": len(real_laws),
            "classification_nodes": len(law_w) - len(real_laws),
            "articles": len(art_only),
            "judgments": len(jud_only),
            "interpretations": len(int_only),
            "cases": len(case_nodes),
            "links": len(links),
            "links_cites": n_cites,
            "links_belongs": len(links) - n_cites,
            "cites_to_articles": cites_to_articles,
            "cites_to_authorities": n_cites - cites_to_articles,
            "article_verified": art_verify[VERIFY_OK],
            "article_unverifiable": art_verify[VERIFY_UNVERIFIABLE],
            "article_suspect": art_verify[VERIFY_SUSPECT],
            "authority_verified": auth_verify[VERIFY_OK],
            "authority_unverifiable": auth_verify[VERIFY_UNVERIFIABLE],
            "unresolved_pronoun": unresolved_same_law,
            "pronoun_resolution": dict(paths),
            "ambiguous_abbrev_laws": len(ambiguous),
            "ambiguous_abbrev_unresolved": ambiguous_cites,
            "recall": recall,
        },
        "nodes": nodes,
        "links": links,
    }


def truncate_links(graph: dict) -> dict:
    """超過 4 MB 就裁 layer 3 的 cites：每案只留引用次數最高的前 8 條，並誠實標示。"""
    kept = [x for x in graph["links"] if x["k"] != "cites"]
    by_case: dict[str, list[dict]] = collections.defaultdict(list)
    for link in graph["links"]:
        if link["k"] == "cites":
            by_case[link["s"]].append(link)
    for case_links in by_case.values():
        case_links.sort(key=lambda x: -x["w"])
        kept.extend(case_links[:TRUNCATE_KEEP])
    graph["links"] = kept
    graph["stats"]["links"] = len(kept)
    graph["stats"]["links_truncated"] = True
    return graph


def dump(graph: dict) -> str:
    return json.dumps(graph, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default="data/local/kb")
    ap.add_argument("--manifest", default="data/manifest.json")
    ap.add_argument("--laws", default="backend/data/laws-snapshot.json")
    # 預設落在 data/local/（不進 git、不進映像檔）。**不要**把預設改成
    # `frontend/public/`：那是 vite 的靜態資源目錄，會原封不動複製進 dist、
    # 再被 `backend/Dockerfile` 整包 COPY 上公開網址——賽方資料集「僅供競賽之用」，
    # 這就是資料隔離違規（CONSTITUTION §6）。run_all 的
    # `scan_frontend_dist_has_no_stowaways` 會擋。要給前端吃就明寫 `--out`，
    # 並且先決定那份 json 能不能公開。
    ap.add_argument("--out", default="data/local/kb-graph.json")
    a = ap.parse_args()

    entries = json.loads(pathlib.Path(a.manifest).read_text(encoding="utf-8"))["entries"]
    snapshot = json.loads(pathlib.Path(a.laws).read_text(encoding="utf-8"))
    laws = {name: body["articles"] for name, body in snapshot["laws"].items()}

    graph = build_graph(entries, pathlib.Path(a.kb), laws, authority_index(snapshot))
    payload = dump(graph)
    if len(payload.encode("utf-8")) > MAX_BYTES:
        payload = dump(truncate_links(graph))

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    print(f"{out}：{len(payload.encode('utf-8')) / 1024 / 1024:.2f} MB")
    print(json.dumps(graph["stats"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
