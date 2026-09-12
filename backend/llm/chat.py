"""聊天追問 agent：純函式層（燈號判定、引用編號簿）。

契約唯一真實來源：`docs/spec/2026-09-12-chat-honesty-lamps.md`。
計畫：`plans/2026-09-12-chat-ask-agent.md`。

**這一層不 import fastapi、不呼叫模型、不碰 orchestrator。**
`classify_answer()` 是機械規則：輸入四樣東西（問題原文、回答原文、本回合的引用白名單、
本回合有沒有用過 `refine_text`），輸出一顆燈。**沒有任何一步問模型「你這句可不可信」**
——那正是 CONSTITUTION §1 要防的事。

## 為什麼 `backend/orchestrator/` 與 `backend/nodes/` 不准被這個檔 import

（本段刻意用路徑形式稱呼那兩個套件。AC15 用 `grep` 找點號形式的模組名，而 `grep`
分不出註解與真正的 import——用點號寫說明會讓這個檔被自己的驗收判紅。**這是改格式
不是改措辭**：講的是同兩個套件，資訊一個字沒少。真正的檢查在
`backend/tests/test_chat.py` 的 AST 版，它只看 import 節點，騙不過去。）

spec §4.0「payload 由呼叫端提供」：`read_case` 只讀呼叫端餵進來的 dict，不得自己
`build_payload()` 或 `load_run()`。`backend/orchestrator/graph.py` 一被 import 就把六個
節點整包拉進 import 圖，純函式測試從此要背著 orchestrator 跑。

**沒有任何既有紅線掃描抓得到這條**：第 4 條（`scan_llm_import_graph`）只從 N2/N3/N4/N6
起走；第 3 條（`scan_core_path_dependencies`）對 `backend/llm/` 具名豁免。所以它是
規格層紅線，由 AC15 的機械檢查把關（`test -f` ＋ `grep -rn 'backend\\.orchestrator|backend\\.nodes'`）。

同理引用白名單從 `backend/config/settings.py` 取（`settings.ref_prefixes()`），
**不從 `backend/nodes/n5_draft.py`**——後者自己 import `backend.llm.client`，
聊天層再 import 它就是層級倒置。也不從 `backend/retrieval/kb.py` 取：那裡已經沒有
模組層常數了（目錄名跟著 corpus 走，寫死就會漏，見 settings.ref_prefixes 的說明）。
`settings` 比聊天層與檢索層都底層，誰都可以依賴它。

## strands 的頂層 import 用 try/except 守衛

比照 `backend/llm/client.py` 的既有做法（spec D8：import 一律在模組頂層）。
沒裝 strands 時 `import backend.llm.chat` 仍要成功（AC2），缺席只在呼叫點 raise。
"""
from __future__ import annotations

import contextvars
import dataclasses
import json
import re
import sys
import threading
from typing import Any

from backend.config import settings
from backend.config.origin_registry import TIER_HUMAN
from backend.gate.lamps import tier_for_lamp
from backend.llm.client import _STRANDS_MISSING, LLMError, _load_model, _prompt, _throttle
from backend.retrieval.lawtable import LawTableRetriever

try:
    from strands import Agent, tool
    from strands.models.bedrock import BedrockModel
except ImportError:  # 未安裝 strands：純函式層與測試路徑不需要它，缺時於呼叫點 raise LLMError
    Agent = tool = BedrockModel = None


# ── 燈號判定用的常數 ────────────────────────────────────────────────

#: 數字類問題的關鍵詞（spec §3.2）。**寧可誤判成紅燈，不可漏判**——
#: 誤判的代價是一則答案多標一次「請人工判斷」加一個試算連結；
#: 漏判的代價是 LLM 自己算了一個期限並被承辦人採用，那是 CONSTITUTION §4 的紅線。
#:
#: 這份清單**未經真實提問語料校準**（spec §8.4）。校準方式：demo 前用十個口語提問
#: 實測，漏判就加詞——**只加不減**。
NUMERIC_Q = (
    "期限", "期間", "幾天", "天數", "多久", "到期", "起算",
    "逾期", "過期", "屆滿", "幾號", "日期",
    "金額", "罰鍰", "罰款", "多少錢", "幾元",
)

#: 量詞規則：問題同時含（阿拉伯或中文）數字**且**含這些量詞之一，也算數字類。
_QUANTIFIERS = ("天", "日", "月", "年", "元")
_CJK_DIGITS = "零一二三四五六七八九十百千萬兩"
_HAS_DIGIT = re.compile(r"[0-9０-９" + _CJK_DIGITS + r"]")

#: 回答裡的引用標號。**帶括號的形式**才算「模型引用了它」（規則 3）。
#: 容忍半形／全形方括號、黑括號、圓括號，以及一組括號內用逗號分隔多筆。
#:
#: ⚠️ **三種前綴都要認，而且大小寫都要**（2026-09-12 真 Bedrock 實測踩到）：
#:   `c1` RefBook 配給檢索命中的號
#:   `C1` 卷內相似案的號（`retrieval.cases[].id`，N4 編的）
#:   `L1` 卷內法條的號（`retrieval.laws[].id`，N4 編的）
#: 原版只認小寫 `c\d+`，模型走 `read_case` 讀卷內之後照著引 `[C1]`…`[C5]`，
#: **七個引用一個都沒被偵測到**，`dropped_refs` 是空的。那次剛好因為規則 4
#: 已經判紅所以沒出事，但同一輪若又引了真的 `c1`，規則 3 會給 `y`——
#: 綠燈配上未經檢查的引用，正是這條規則存在的理由。
_CITE_PREFIX = r"[CcLl]"
_CITE_BRACKETED = re.compile(
    r"[\[\［【(（]\s*((?:" + _CITE_PREFIX + r"\d+\s*[,，、]?\s*)+)[\]\］】)）]")
_CITE_ID = re.compile(_CITE_PREFIX + r"\d+")

#: 偵測「引了白名單以外的編號」用的**寬鬆**樣式，連沒加括號的 `c9` 也算。
#: 兩種樣式刻意不對稱，方向都朝安全：
#:   - 規則 3（判 `y`）用嚴格樣式——漏認一個引用只會多紅一則，是安全方向。
#:   - `dropped_refs`（強制紅）用寬鬆樣式——漏認一個捏造編號會讓假引用矇混過關，
#:     那是不安全方向，所以寧可多抓。
_CITE_LOOSE = re.compile(_CITE_PREFIX + r"\d+")

REDIRECT_DEADLINE = {
    "endpoint": "/api/deadline",
    "method": "POST",
    # 既有契約，欄位名不改（`backend/api/app.py` 的 deadline 端點）
    "body_schema": {
        "method": "personal|deposit|public",
        "service": "YYYY-MM-DD",
        "filing": "YYYY-MM-DD|null",
        "transit": 0,
        "interested": False,
    },
    "reason": "期間計算由規則引擎負責，同輸入必同輸出、可逐步覆核；"
              "聊天不計算期限（CONSTITUTION §4）。",
    "cta": "查看程序審查的算式",
}

WHY_NUMERIC = "期間計算由規則引擎負責，聊天不計算期限。"
WHY_REFINE = "本則為模型改寫的文字，系統不替其內容背書，請覆核後採用。"
#: `ranked_by` → **那個分數是什麼**。契約 §3.2 把「一律說成向量相似度」列為不實陳述：
#: 開了重排之後 `score` 是 cross-encoder 判的語意相關性（實測同一批命中
#: rerank 0.950 vs embedding 0.732，差 22 個百分點），而法條查表的 1.0／0.0
#: 根本不是相似度，是條號在不在快照裡。
#:
#: 這張表**跟前端 `api/ranker.js` 的 `RANKER_CAPTION` 講同一件事，但不是同一句**：
#: 那邊是標在分數旁邊的短標籤（「重排模型判定」），這裡是燈號說明句的一個片語。
#: 兩邊都由 `hits[].ranked_by` 決定，所以不會各自漂到不同的事實上。
WHY_RANKED_BY = {
    "rerank": "重排模型判定的語意相關性",
    "embedding": "向量相似度",
    None: "法條查表的二元命中（條號在不在快照裡，不是相似度）",
}
#: 出現順序固定，否則同一組命中會因為 dict 走訪順序而講出不同的句子。
_RANKED_BY_ORDER = ("rerank", "embedding", None)

WHY_SOURCED = ("本則回答引用了 {n} 筆檢索命中（{kinds}）；"
               "未對資料集實檔驗證，請覆核後採用。")


def _why_sourced(refs: list[dict[str, Any]]) -> str:
    """依這幾筆命中**實際的** `ranked_by` 組出燈號說明。

    2026-09-13 之前這句話寫死「命中為 KB 向量相似度結果」，不看命中是什麼。
    雲上實測兩頭都錯（QA 存檔 `t02`／`t04`）：

    - `t02` 是法條查表，`ranked_by:null`、分數是 1.0／0.0 的二元命中——不是相似度。
    - `t04` 是重排過的 KB 命中，`ranked_by:"rerank"`，分數由 cross-encoder 判。

    兩者的 `why` 都講「向量相似度」。`hits[].ranked_by` 今晚修好了，這一句漏掉。
    """
    present = {r.get("ranked_by") for r in refs}
    kinds = [WHY_RANKED_BY[k] for k in _RANKED_BY_ORDER if k in present]
    # 認不得的值不猜一個說法：寧可少講一種，也不要把某一批分數歸到錯的說法裡。
    return WHY_SOURCED.format(n=len(refs), kinds="、".join(kinds) if kinds else "來源未標明")
WHY_UNSOURCED = "本則回答沒有引用任何檢索命中，系統無法確認其依據，請人工判斷。"
#: ⚠️ **這句話原本自己在說一件它不知道的事。** 原文是「模型引用了檢索結果之外的
#: 來源（…），已移除」，但寬鬆樣式（`_loose_ids`）抓的是「這幾個編號出現在回答裡」，
#: **分不出模型是在引用它、還是在說它不存在**。
#:
#: 雲上實測（QA `t18`）：承辦人在問題裡自己打了 `[c7]`、`[c8]` 要模型補上，模型
#: 正確拒絕——「工具回傳的編號只有 L1 到 L6，並沒有 `[c7]` 或 `[c8]`」——
#: 而系統把這則**正確的拒絕**判成「引用了檢索結果之外的來源」。
#:
#: 偵測維持原樣（寬鬆、寧可誤報），理由見 `_loose_ids`；但**說明句不再替模型的
#: 意圖作證**。紅燈該亮還是亮，只是不編一個「它引用了」的事實。
WHY_DROPPED = ("本則出現了檢索結果以外的編號（{dropped}）。系統無法分辨那是引用、"
               "還是在說明那些編號不存在，一律不替本則內容背書，請人工判斷。")


# ── 引用編號簿 ──────────────────────────────────────────────────────

class RefBook:
    """本回合的引用編號簿：把每一筆工具命中配一個 `c1`、`c2`… 的單調遞增編號。

    **為什麼不直接用 KB 給的編號**（spec §3.3）：`KBRetriever.search` 每次呼叫都從
    `kb-1` 重新編號，同一回合呼叫兩次就會撞號，`refs[]` 會指錯來源。這是實際會發生的
    碰撞，不是防禦性設計。

    編號**跨工具連續**：第一個工具配到 c1、c2，第二個工具接著配 c3。
    """

    def __init__(self) -> None:
        self._n = 0
        self._by_id: dict[str, dict[str, Any]] = {}

    def add(self, hit: Any, score_kind: str | None = "embedding") -> dict[str, Any]:
        """收一筆 `Hit`（或已經 `as_dict()` 過的 dict），配號並回傳 wire 格式的 hit。

        回傳的形狀就是 spec §4.2 `tool_result.hits[]` 的一筆：
        `{id, t, src, score, verified, note, provenance, ranked_by}`。
        `provenance` 只有 KB hit 的 `payload` 裡有，法條查表的 hit 沒有 → `None`
        （spec §4.2 要求前端處理 `null`，所以這裡明確填 `None` 而不是省略這個鍵）。

        ## `ranked_by`：`score` 那個數字**是哪一種**

        2026-09-13 補。開了重排之後 `KBRetriever` 會把 `score` 換成**重排分數**並在
        `payload["ranked_by"]` 標 `"rerank"`（`backend/retrieval/kb.py:546`），
        但這裡原本沒把它帶出去——於是前端拿到一個重排分數，畫面上寫「向量相似度 87%」。
        **那個數字不是向量相似度**，而那句文案就在 demo 主畫面上（CONSTITUTION §1）。

        `score_kind` 是**呼叫端告訴這裡「這個檢索器的分數預設是哪一種」**：

        - `"embedding"`（預設）：KB 檢索。`payload` 標了 `"rerank"` 就用它，
          沒標就是 embedding——**沒重排時檢索器刻意不標**這個鍵
          （`test_live_plumbing.py:2737` 釘住），所以「缺鍵」在這裡等於 embedding，
          與 `backend/nodes/n4_retrieval.py:345` 同一條規則。
        - `None`：法條查表。它的 `score` 是 **1.0／0.0 的二元命中**，不是相似度，
          報任何一種「排序方式」都是把二元結果講成程度。前端據此不顯示百分比。

        **為什麼由呼叫端給而不是在這裡嗅 payload**：嗅探（例如「有 `law` 鍵就是查表」）
        會在某一天欄位改名時靜默倒向另一邊，而症狀是畫面上的文案錯了、沒有燈會亮。
        呼叫端本來就握著 retriever，而 retriever 的 `name` 是**宣告出來的類別屬性**。
        """
        self._n += 1
        cid = f"c{self._n}"
        raw = hit.as_dict() if hasattr(hit, "as_dict") else dict(hit)
        payload = raw.get("payload") or {}
        entry = {
            "id": cid,
            "t": raw.get("t", ""),
            "src": raw.get("src", ""),
            "score": raw.get("score"),
            "verified": bool(raw.get("verified", False)),
            "note": raw.get("note", ""),
            "provenance": payload.get("provenance"),
            # 每一筆 ref 自己帶 origin，前端才分得出「KB 命中」與「卷內既有」。
            # `done.origin` 維持 spec §7 凍結的三個值域，不在那裡新增第四個。
            "origin": "retrieval",
            # 缺鍵不等於 null：省略會讓前端拿到 undefined 而不是值（契約 §2.3 同一條紀律）。
            "ranked_by": (payload.get("ranked_by") or score_kind) if score_kind else None,
        }
        self._by_id[cid] = entry
        return entry

    def add_all(self, hits: list[Any],
                score_kind: str | None = "embedding") -> list[dict[str, Any]]:
        return [self.add(h, score_kind) for h in hits]

    def add_case_refs(self, items: list[dict[str, Any]]) -> list[str]:
        """把卷內分區（`laws`／`cases`）自帶的 id 註冊進白名單。

        **為什麼卷內 id 要進白名單，而不是禁止模型引用它們**（tech lead 2026-09-12 裁定）：
        卷內資料是**最強的出處**，不是最弱的。白名單要擋的是「回答引用了工具沒回傳過的
        東西」，而 `read_case` 確實把那些 id 回傳給模型了。禁止引用卷證，會讓模型指不出
        本案最可驗的來源——那是把規則的手段誤當成目的。

        **不重新編號**：這些 id（`L1`、`C3`）已經印在承辦人畫面上的法條卡與相似案卡上，
        改號會讓聊天講的號跟畫面上的對不起來。RefBook 自己配的 `c1` 系列是另一個命名空間，
        兩者不會撞（前綴不同）。

        `origin` 標 `record`（卷證直錄）而不是 `retrieval`——**卷內引用不該看起來像 KB 命中**。
        `backend/config/origin_registry.py` 的 `ORIGIN_TO_TIER["record"]` 本來就是「有出處」。
        """
        added: list[str] = []
        for item in items or []:
            cid = str(item.get("id") or "").strip()
            if not cid or cid in self._by_id:
                continue
            self._by_id[cid] = {
                "id": cid,
                "t": item.get("t", ""),
                "src": item.get("src", ""),
                "score": None,
                "verified": bool(item.get("verified", False)),
                "note": item.get("note", "") or "卷內既有資料，非本次聊天檢索所得。",
                "provenance": None,
                "origin": "record",
                # 卷內既有資料沒有檢索分數（`score` 就是 None），
                # 也就沒有「誰排的」可言。明確填 None，不省略這個鍵。
                "ranked_by": None,
            }
            added.append(cid)
        return added

    def reset_case_refs(self) -> list[str]:
        """把卷內編號（`L1`、`C3`…）從白名單清掉，回傳被清掉的 id。

        **為什麼需要這個**（契約 v2 §0.1 第 2 點）：N4 每次執行都從 `L1`／`C1` 重編
        （`backend/nodes/n4_retrieval.py`），而 `add_case_refs` 對既有 id 直接 `continue`。
        同一回合裡先 `read_case("laws")`（登記了舊 run 的 `L1`）再跑一次 pipeline，
        新 run 的 `L1` 就登記不進去——模型引新草稿的 `[L1]`，`refs[]` 卻帶出舊 run 的法條。
        **那是誠實層被靜默打穿**：號對得上、內容是別人的。

        **只清 `origin == "record"`，不清 `cN`。** `cN` 是本回合聊天檢索配的號，
        模型可能已經在前文引用過；一起清掉會讓那些引用變成 `dropped_refs` → 無辜紅燈。
        `self._n` 也不動——兩者是不同的命名空間，`cN` 的計數器不該因為卷內換了一批而倒退。
        """
        stale = [cid for cid, e in self._by_id.items() if e.get("origin") == "record"]
        for cid in stale:
            del self._by_id[cid]
        return stale

    def whitelist(self) -> set[str]:
        """本回合配出去過的所有編號。回答裡出現、但不在這裡面的就是捏造的。"""
        return set(self._by_id)

    def get(self, cid: str) -> dict[str, Any] | None:
        return self._by_id.get(cid)

    def __len__(self) -> int:
        return self._n


# ── 燈號判定 ────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Verdict:
    """`classify_answer()` 的輸出，對應 spec §4.4 `done` 事件的燈號欄位。"""

    lamp: str
    tier: str
    origin: str
    why: str
    refs: list[dict[str, Any]]
    dropped_refs: list[str]
    redirect: dict[str, Any] | None
    refine_used: bool

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def is_numeric_question(question: str) -> bool:
    """問題是不是數字類（期限／天數／金額）。**只看使用者的問題，不看回答，也不問模型。**

    兩條規則任一命中即算：
    1. 命中 `NUMERIC_Q` 任一關鍵詞。
    2. 同時含數字（阿拉伯或中文）與量詞（天／日／月／年／元）。

    已知會誤判的形狀（**刻意接受**，spec §3.2「寧可誤判成紅燈，不可漏判」）：
    「本案 112 年訴字第 123 號的爭點是什麼？」含數字與「年」，會被判成數字類而亮紅燈
    加一個試算連結。代價是多一次人工覆核；反方向的代價是模型自己算了一個期限。
    """
    q = question or ""
    if any(kw in q for kw in NUMERIC_Q):
        return True
    return bool(_HAS_DIGIT.search(q)) and any(u in q for u in _QUANTIFIERS)


def cited_ids(answer: str) -> list[str]:
    """回答裡**帶括號**引用到的編號，依出現順序、去重。供規則 3 判定用。"""
    seen: list[str] = []
    for group in _CITE_BRACKETED.findall(answer or ""):
        for cid in _CITE_ID.findall(group):
            if cid not in seen:
                seen.append(cid)
    return seen


def _loose_ids(answer: str) -> list[str]:
    """回答裡出現的所有 `cN` 樣式（不論有沒有括號），依出現順序、去重。

    只用來抓 `dropped_refs`——寬鬆的方向是「多抓」，多抓只會多紅一則。

    **已知的誤報，2026-09-13 評估後刻意不收窄**（QA `t18`）：承辦人自己在問題裡打了
    `[c7]`，模型正確地回「工具回傳的編號只有 L1 到 L6，並沒有 [c7]」，這裡照樣抓到。

    想過的收窄法與為什麼不採用：
    - **問題裡出現過的編號就不算**——擋不住真正危險的那種。承辦人打了 `[c7]`、
      模型接著說「依 [c7] 該案駁回」，那是**替一個不存在的來源編造內容**，
      而這個規則會放它過去。誤報的代價是一則正確的回答被標成不可信；
      漏報的代價是一段編造的內容被標成有出處。**兩者不對稱。**
    - **判斷編號出現在肯定句還是否定句**——那是語意判斷，沒有可靠的樣式可寫，
      而寫不可靠的樣式等於把紅線交給運氣。

    所以偵測維持寬鬆，改的是**說明句不再替模型的意圖作證**（見 `WHY_DROPPED`）。
    """
    seen: list[str] = []
    for cid in _CITE_LOOSE.findall(answer or ""):
        if cid not in seen:
            seen.append(cid)
    return seen


def classify_answer(
    question: str,
    answer: str,
    refbook: RefBook | None = None,
    refine_used: bool = False,
    whitelist: set[str] | None = None,
) -> Verdict:
    """依 spec §3 的四條規則判一顆燈。四條**依序**判，第一個命中即回。

    | # | 條件 | lamp | origin |
    |---|---|---|---|
    | 1 | 問題是數字類 | r | human_required（帶 redirect） |
    | 2 | 本回合用過 refine_text | r | llm |
    | 3 | refs 非空 **且** 回答含至少一個 ref 標號 | y | retrieval |
    | 4 | 其餘 | r | llm |

    外加 spec §3.3：回答引了白名單以外的編號 → 進 `dropped_refs`，**強制紅燈**
    （比照 `backend/llm/client.py` 對 N5 的既有做法）。這條插在規則 2 之後、
    規則 3 之前——規則 1、2 本來就紅，它要擋的是「有捏造引用卻被規則 3 判成 y」。

    `tier` **一律**由 `backend/gate/lamps.py` 的 `tier_for_lamp()` 算，
    **不得**用 `backend/config/origin_registry.py` 的 `tier_of()`：後者
    `ORIGIN_TO_TIER["llm"] = TIER_SOURCED`，那個前提是「模型句子必定經過 N6 驗引用」，
    而**聊天沒有 N6**（spec §3.0）。用錯那支會把聊天的紅燈標成「有出處」。

    `lamp` 的值域只有 `y` 與 `r`，**`g` 在任何輸入下都不出現**（spec §3.1）：
    聊天不執行規則引擎，KB 命中也沒有對資料集實檔驗證。
    """
    allowed = whitelist if whitelist is not None else (
        refbook.whitelist() if refbook is not None else set()
    )
    bracketed = cited_ids(answer)
    dropped = [c for c in _loose_ids(answer) if c not in allowed]
    used = [c for c in bracketed if c in allowed]

    def _verdict(lamp: str, origin: str, why: str,
                 refs: list[dict[str, Any]] | None = None,
                 redirect: dict[str, Any] | None = None) -> Verdict:
        return Verdict(
            lamp=lamp,
            tier=tier_for_lamp(lamp, origin),
            origin=origin,
            why=why,
            refs=refs or [],
            dropped_refs=list(dropped),
            redirect=redirect,
            refine_used=refine_used,
        )

    # 規則 1：數字類問題 —— 期間與金額由規則引擎算，聊天不代算。
    if is_numeric_question(question):
        return _verdict("r", "human_required", WHY_NUMERIC, redirect=dict(REDIRECT_DEADLINE))

    # 規則 2：用過 refine_text —— 即使同回合也檢索到 refs，改寫文字仍然無出處。
    if refine_used:
        return _verdict("r", "llm", WHY_REFINE)

    # spec §3.3：引了白名單以外的編號 —— 強制紅，不得落到規則 3。
    if dropped:
        return _verdict("r", "llm", WHY_DROPPED.format(dropped="、".join(dropped)))

    # 規則 3：有檢索命中且回答真的引到了。
    if used:
        refs = [refbook.get(c) for c in used] if refbook is not None else []
        refs = [r for r in refs if r is not None]
        if refs:
            return _verdict("y", "retrieval", _why_sourced(refs), refs=refs)

    # 規則 4：其餘（refs 空、或有 refs 但回答一個都沒引）。
    return _verdict("r", "llm", WHY_UNSOURCED)



# ── S4：agent 層（五個工具 ＋ Strands 單 agent）────────────────────

#: `tool` 欄位的值域與給 UI 的中文標籤（spec §4.0）。後端帶 `label`，
#: 前端不必自己維護對照表；兩邊必須一致。
TOOL_LABELS = {
    "extract_case_document": "解析卷證檔案",
    "search_regulations": "查法條",
    "search_similar_decisions": "查相似訴願決定",
    "retrieve_refs": "查判解與函釋",
    "generate_decision_draft": "生成草稿",
    "refine_text": "潤稿",
    "read_case": "讀卷內",
    "build_relation_graph": "畫案件關聯圖",
}
# 第八支 `build_relation_graph` 2026-09-13 實作完成才進這張表
# （計畫 `plans/2026-09-12-relation-graph.md`，契約 v2 §3.0／§3.7）。
# **在那之前它刻意不在這裡**：先把名字放進值域會讓前端以為它在，而
# `test_every_tool_entry_point_throttles_exactly_once` 也會要求它有進入點——
# 列一個不存在的工具，兩邊都在說謊。

#: pipeline 工具轉發 `tool_step` 時的節點中文標籤（契約 v2 §2.3 ③）。
#: **後端帶 label，前端不維護對照表**——兩邊各存一份遲早會有一份走歪。
PIPELINE_NODE_LABELS = {
    "n1": "讀卷抽取",
    "n2": "案件分類",
    "n3": "程序審查",
    "n4": "檢索法條與相似案",
    "n5": "草稿撰寫",
    "n6": "引用守門",
}

#: 查詢句節錄的長度。相似案那條吃的是整段卷證原文，可以很長。
_QUERY_CLIP = 60


def _clip(text: str, limit: int = _QUERY_CLIP) -> str:
    one_line = " ".join(str(text).split())
    return one_line if len(one_line) <= limit else one_line[:limit] + "…（節錄）"


#: `extract_case_document` 回傳字串的結尾。**不要叫模型接著去讀卷內。**
#:
#: 原文是「要看內容請用 `read_case` 讀 intake／facts_excerpt／screen」，QA 雲上實測
#: （`r05`）的後果是模型照做，一次按鈕跑出四張卡：
#:
#:     tc-1 extract_case_document   （n1 11150ms／n2 0ms／n3 0ms）
#:     tc-2 read_case {"section":"intake"}
#:     tc-3 read_case {"section":"facts_excerpt"}
#:     tc-4 read_case {"section":"screen"}
#:
#: 整輪 28 秒而 n1 只佔 11 秒，其餘是三輪模型往返加三次節流。**不是模型不聽話，
#: 是這句話叫它去做的。** 而契約 §3.3 說那四塊內容由前端打彙整版取，agent 不必再讀。
#:
#: 順帶修掉兩件事：原文把 `read_case` 這個函式名與三個分區鍵名端到模型面前
#: （紅線 5 管的是模型的輸出，管不到工具回傳值——假話與術語的來源常常是我們自己）。
_EXTRACT_NO_REREAD = (
    "收文欄位、事實摘錄與程序審查的結果都已經隨這次解析產出，**不必再讀一次卷內**。"
    "請用一兩句話說明解析完成、目前走到哪一步就好。"
    "承辦人接著問某一項的細節時，那時再去讀對應的部分。"
)


def degraded_summary(out: dict[str, Any]) -> str:
    """流水線回報的降級原因 → 一句給承辦人的話。沒有降級就回 `""`。

    **零加工**：每一句都來自 `run_meta.degraded[].reason`，這一層只在前面補上節點的
    中文名（`PIPELINE_NODE_LABELS`，契約 §2.3 ③ 已在用的同一張表）。沒有 `reason`
    的那筆直接跳過——**不得替它組一句「可能是某某有問題」**，那就是編造（CONSTITUTION §1）。

    為什麼需要這支（2026-09-13 雲上實測）：兩個合成案跑「解析卷證」都停在
    `NEEDS_INPUT`，`run_meta.degraded` 明寫「缺漏：案號，需人工表單補齊後才能續跑」，
    但 `tool_result.note` 是空字串、回給模型的字串只說「終態 NEEDS_INPUT」，
    於是畫面上只剩「還沒有生成草稿」。**工具卡會標黃、燈號會紅，但黃燈沒說黃在哪**，
    承辦人只會覺得它就是沒生，不會知道下一步是去補案號。
    """
    parts: list[str] = []
    for entry in out.get("degraded") or []:
        if not isinstance(entry, dict):
            continue
        reason = str(entry.get("reason") or "").strip()
        if not reason:
            continue
        node = str(entry.get("node") or "")
        label = PIPELINE_NODE_LABELS.get(node, node)
        parts.append(f"{label}：{reason}" if label else reason)
    return "；".join(parts)


#: 乙案（AgentCore Runtime）容器裡沒有六節點流水線，`run_pipeline` 不會被注入。
#: **明說「這個檔位沒有」，不要靜默失敗**（契約 v2 §0.1 末段）。
_NO_PIPELINE = ("這個檔位沒有卷證分析流水線，解析卷證與生成草稿在這裡跑不了。"
                "請告訴使用者這項功能目前不可用，不要改用推測代替。")

#: 同理：關聯圖要讀 run 檔才畫得出來，沒有注入 adapter 的檔位畫不了。
#: **明說沒有，不要回一張空圖**——空圖看起來就像「這個案子本來就沒什麼關聯」。
_NO_GRAPH = ("這個檔位沒有辦法讀取執行紀錄，案件關聯圖在這裡畫不出來。"
             "請告訴使用者這項功能目前不可用，不要自己描述一張圖。")

#: 還沒有任何一次 run 就要畫圖：不是失敗，是還沒到那一步。
_NO_RUN_FOR_GRAPH = ("這個案子還沒有執行紀錄，畫不出關聯圖。"
                     "請先用 extract_case_document 解析卷證、"
                     "再用 generate_decision_draft 生成草稿。")

#: `read_case` 讀得到的分區。值域固定，讓模型不能亂要一個不存在的欄位。
#: `read_case` 可讀的分區。**`laws`／`cases` 與 `retrieved_*` 是兩份不同的東西**
#: （2026-09-13 拆開；在那之前只有一份，而那一份是錯的那一份）。
#:
#: 實際發生的：承辦人用右欄的 `+` 把兩條法規、兩件案例加進卷宗，畫面清楚寫著
#: 「相關法規 2」「相關案例 2」，然後問「可以生草稿了嗎」，得到
#: 「法規依據：卷內還沒有／相似案例：卷內也還沒有」。
#:
#: 根因是「卷內」這個詞被用在兩個不同的東西上：
#: - 畫面右欄的「案件卷宗」＝ `manifest.json`（**承辦人挑的**，走 REST 加入）
#: - `read_case` 讀的「卷內」＝ 上一次 run 的 payload（**N4 這一輪檢索到的**）
#: 那位承辦人的 run 只跑到 n3，payload 的 `laws` 當然是空的——而 manifest 有東西。
#: 從他的角度，「卷內的 laws 是空的」**是一句假話**。
#:
#: 兩份都要讀得到，而且**名字要說得出自己是哪一份**：
#: - `laws`／`cases`：卷宗清單。`generate_decision_draft` 的前置條件看的就是這份。
#: - `retrieved_laws`／`retrieved_cases`：這一輪檢索到的。右欄的三態標記
#:   （`matched`／`query_only`／`unused`，`classify_picks`）靠它比對，不能拿掉。
CASE_SECTIONS = ("intake", "facts_excerpt", "screen", "laws", "cases",
                 "retrieved_laws", "retrieved_cases")

#: 卷宗清單那兩個分區 → `manifest.json` 的鍵。**`cases` 對到 `references`**：
#: 契約 §4.0 的鍵名是 `references`，而畫面與工具講的是「相似案例」。
_MANIFEST_SECTIONS = {"laws": "laws", "cases": "references"}
#: 這一輪檢索那兩個分區 → run payload 的鍵。
_RETRIEVED_SECTIONS = {"retrieved_laws": "laws", "retrieved_cases": "cases"}

#: 由「查法條」chip 歸檔進卷宗的法規，`id` 的前綴。**不用 `hits[].id`**
#: （`c1`／`c2`）：那是 RefBook 每回合重配的序號，存進 manifest 下一輪就對不上，
#: 同一條會被當成新的一筆重複加入。這個前綴由「法名＋條號」推導，同一條永遠一樣。
#:
#: 不用 `#` 當分隔（原提案是 `lawtable:訴願法#77`）：`#` 在 URL 是 fragment 分隔符，
#: 移除路徑 `DELETE …/laws/{lawId}` 要靠呼叫端記得 percent-encode 才活得下來。
#: 用連字號沒有這個依賴。
ARCHIVE_LAWTABLE_PREFIX = "lawtable:"

#: 卷宗 `laws[].channel` 的兩個值。契約 §4.2 要求「KB 全文通道與查表通道畫面上要分得出來」，
#: 這是把那句話落實到資料上——兩種東西的保證等級不同：
#: - `lawtable`：條號**存在性**驗證（`laws-snapshot.json` 只有條號清單，沒有條文原文）
#: - `corpus`：母庫全文檔（S3 上有 body）
ARCHIVE_CHANNEL_LAWTABLE = "lawtable"
ARCHIVE_CHANNEL_CORPUS = "corpus"

#: 第五種 `retrieval_status`（2026-09-13 新增，契約 §3.5.2）。
#:
#: **前四種沒有一種在寫的那一刻是真的**：`unknown` 說「還沒查過」，而這批**正是查出來的**；
#: `matched` 的文案是「已進入法條查表，草稿可引用本法條」，但草稿還沒跑。
#: 硬套任何一個都是說反話，所以新增一個。
#:
#: 它是**短命的**：下一次 `record_run()` 會用 `classify_law_retrieval()` 重算成三態之一。
#: 但那個空窗正是承辦人按完 chip 盯著右欄看的時候，所以它得說得出實話。
PICK_FOUND_BY_TOOL = "found_by_tool"

#: 承辦人挑的法規在這一輪檢索裡的三種下場（契約 v2 §3.5.2 末段，2026-09-13 Ci 改判）。
#:
#: **原本只有 hit／miss 兩態，而那個 miss 是一句假話。** 實測（2026-09-13）：
#:   LawTableRetriever.search("廢棄物清理法")     → 0 命中
#:   LawTableRetriever.search("廢棄物清理法第2條") → 1 命中
#: manifest 的 `t` 是純法規名（法規檔是整部法一檔，KB 標題＝檔名去副檔名），
#: 所以手動挑的法規對**通道 A（法條查表）幾乎必然零貢獻**——查表只認「法名第N條」。
#: 但同一個詞**確實進了通道 B**（相似案語意檢索）：實測 `retrieval_meta.case_query_extra_terms`
#: 收得到它、`case_query_text` 尾端也接上了。
#:
#: 所以舊文案「檢索未命中，未進入草稿」是錯的，不只是吵：使用者挑的東西**有**被用到。
#: 它假在誠實的方向（把有說成沒有），所以一直沒人抓到。
#: 旁證：`n4_retrieval.py:150` 早就把「案型對應法規名（無條號）」標成
#: `SUBSTANTIVE_ARTICLE_UNKNOWN`——系統自己知道無條號的法規名查表是 no-op。
PICK_MATCHED = "matched"
PICK_QUERY_ONLY = "query_only"
PICK_UNUSED = "unused"

#: 文案原則：**讓使用者知道下一步能做什麼。**「未命中」講完就沒了；
#: 「要進法條查表得指定到條」是他真的按得下去的動作。
PICK_NOTES = {
    PICK_MATCHED: "已進入法條查表，草稿可引用本法條",
    PICK_QUERY_ONLY: (
        "已用於相似案檢索；未進法條查表——查表只認「法名第N條」，"
        "純法規名查不到。要讓它成為草稿可引用的依據，請指定到條（例：訴願法第14條）"
    ),
    PICK_UNUSED: "本次生成未送進任何檢索通道",
    PICK_FOUND_BY_TOOL: (
        "由「查法條」在法條查表找到（條號存在性驗證，非法條全文）；"
        "還沒跑過草稿，生成之後才知道草稿有沒有引用它"
    ),
}

#: 比對法規名時要抹掉的空白。全形空白（U+3000）在法規名裡真的會出現
#: （KB 檔名是人整理的），不一起抹掉就會把同一部法規判成兩部。
_SPACE_CHARS = " \t　 "

#: `overrides.n4_query` 的分隔符（`generate_decision_draft` 用它 join）。
PICK_QUERY_SEP = "；"


def _statute_key(text: Any) -> str:
    """法規名的比對鍵。**只做去空白與去副檔名，不做任何模糊比對。**

    模糊比對（包含、前綴、編輯距離）在這裡特別危險：它讓「使用者挑的法規有沒有
    影響草稿」這個誠實問題，變成一個**看起來總是成立**的問題。
    """
    s = str(text or "")
    for ch in _SPACE_CHARS:
        s = s.replace(ch, "")
    return s[:-4] if s.lower().endswith(".txt") else s


def _sent_keys(query_terms: Any) -> set[str]:
    """這一輪**真的被送進通道 B** 的查詢詞（`retrieval_meta.case_query_extra_terms`，
    或 `generate_decision_draft` join 出來的那個字串）。

    用「照分隔符切開後精確相等」，不是子字串比對——子字串會讓
    「訴願法」被「訴願法施行細則」判成有送出，那又回到「看起來總是成立」。
    """
    if query_terms is None:
        return set()
    raw = [query_terms] if isinstance(query_terms, str) else list(query_terms)
    out: set[str] = set()
    for chunk in raw:
        for part in str(chunk or "").split(PICK_QUERY_SEP):
            key = _statute_key(part)
            if key:
                out.add(key)
    return out


def classify_picks(picked: list[dict[str, Any]],
                   retrieved: list[dict[str, Any]],
                   query_terms: Any = None) -> list[dict[str, Any]]:
    """承辦人手動挑的法規，這一輪各自走到哪（契約 v2 §3.5.2 末段）。

    回傳**每一筆**都帶 `state`／`note`，不是只回沒中的那幾筆——三態的重點是
    把一句假話換成三句真話，不是把紅燈調綠。

    | state | 意思 | 判準（都有可查證的依據，不是推論） |
    |---|---|---|
    | `matched` | 進了通道 A，草稿可引用 | 法規名出現在 N4 回的 `laws[].law`／`t` |
    | `query_only` | 只進了通道 B（相似案語意檢索） | 出現在送出的查詢詞裡，但不在通道 A 結果 |
    | `unused` | 兩邊都沒有 | 兩個依據都對不上 |

    ⚠️ **`matched` 不等於「因為你挑了它才查到」。** 通道 A 是 N4 依案情獨立檢索的
    （2026-09-05 拍板），`訴願法` 這種常見法規本來就會被查到——挑不挑都一樣。
    這裡只能誠實回答「它在不在結果裡」，回答不了「它是不是因你而在」，
    **所以文案寫「已進入法條查表」而不是「你的挑選生效了」。**

    ⚠️ **不得為了讓警語消失而把挑的法規直接塞進 `laws[]`**——那就回到
    「檢索佐證的是自己」，正是 2026-09-05 改成 N4 獨立檢索要擋掉的事。

    比對鍵是**法規名**，兩邊同一粒度（2026-09-12 實測）：挑的那側（manifest `laws[].t`）
    ＝ KB 標題＝檔名去副檔名（`retrieval/kb.py:558`）；查到那側＝N4 `laws[].law`，
    缺席時退回 `t`。Epic B 若把 `t` 改成別的東西，**要改的是比對鍵不是拿掉功能**。
    """
    hit_keys: set[str] = set()
    for r in retrieved or []:
        if not isinstance(r, dict):
            continue
        for value in (r.get("law"), r.get("t")):
            key = _statute_key(value)
            if key:
                hit_keys.add(key)
    sent_keys = _sent_keys(query_terms)

    out: list[dict[str, Any]] = []
    for p in picked or []:
        if not isinstance(p, dict):
            continue
        key = _statute_key(p.get("t"))
        # 名字是空的**完全不列**：那是 manifest 那筆資料壞了，不是檢索的事。
        # 混著報會讓承辦人去查一個根本不存在的檢索問題。
        if not key:
            continue
        if key in hit_keys:
            state = PICK_MATCHED
        elif key in sent_keys:
            state = PICK_QUERY_ONLY
        else:
            state = PICK_UNUSED
        out.append({"id": p.get("id"), "t": p.get("t"),
                    "state": state, "note": PICK_NOTES[state]})
    return out


def picks_needing_note(picked: list[dict[str, Any]],
                       retrieved: list[dict[str, Any]],
                       query_terms: Any = None) -> list[dict[str, Any]]:
    """`classify_picks` 裡**沒有進法條查表**的那幾筆（`query_only` ＋ `unused`）。

    給「要不要對使用者多講一句」用。`matched` 不需要講——沒事也講一句，
    狼來了會讓真的有事那次被忽略。
    """
    return [p for p in classify_picks(picked, retrieved, query_terms)
            if p["state"] != PICK_MATCHED]


#: **這一支工具**的 `call_id`。不是 instance 欄位——同一個 model turn 的多支工具
#: 併發執行，共享欄位會被後進來的那支蓋掉（實測見 `ChatTools._current_call_id`）。
#: ContextVar 讓每個執行緒／task 各有一份，配對就不會跨支錯亂。
_CURRENT_CALL_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "chat_current_call_id", default=None)


_NO_RETRIEVER = "目前沒有可用的檢索來源，這個工具查不了。請直接說明查不到，不要改用推測作答。"

#: 分數**不是相似度**的檢索器。`LawTableRetriever.name`（`backend/retrieval/lawtable.py:21`）
#: 是宣告出來的類別屬性，不是從 payload 嗅出來的。
_BINARY_SCORE_BACKENDS = frozenset({"lawtable"})


def _score_kind(retriever: Any) -> str | None:
    """這個檢索器的 `score` 預設是哪一種數字（`RefBook.add` 的 `score_kind`）。

    法條查表回 `None`：它的 1.0／0.0 是**條號在不在快照裡**，報成任何一種排序方式
    都是把二元結果講成程度。其餘（KB）預設 embedding，實際重排過的那幾筆
    由 `payload["ranked_by"]` 覆蓋。
    """
    return None if getattr(retriever, "name", "") in _BINARY_SCORE_BACKENDS else "embedding"


class ChatTools:
    """一回合的工具集合與狀態。

    把狀態收在一個物件裡而不是散在閉包，是為了讓 HTTP 層拿得到
    `refine_used`（規則 2 要用）與 `refbook`（規則 3 要用），而不必回頭解析事件。

    **每個工具進入點都先 `_throttle()`**（spec §8.3）。既有的 `_throttle()` 只管
    `_invoke_structured` 每一次送出的請求，**Strands agent loop 在一次呼叫內因工具
    往返而多打的模型請求不經過它**（`backend/llm/client.py` 的 docstring 明寫）。
    聊天正是 agent loop，一輪可能連打 3–5 次。工具回傳之後模型就會再被呼叫一次，
    所以在工具進入點節流，等於替那一次後續的模型呼叫排隊。

    ⚠️ **誠實限制，不要讀成「1 RPS 已解決」**：這只保證**單一進程內**不超速。
    乙案（AgentCore Runtime）上線後聊天跑在另一個容器，與六節點是兩份獨立的節流狀態，
    **甲乙並存時全域仍可能超標**。賽制是否把聊天呼叫一起算進 1 RPS：**待查**。

    `read_case` 不打網路也照樣節流，看似浪費，但理由同上——它的代價是每次讀卷多等
    約一個節流間隔，換到的是「工具往返不會把 RPS 衝上去」。
    """

    def __init__(self, case_payload: dict[str, Any], refbook: RefBook,
                 retriever: Any = None, snapshot: dict[str, Any] | None = None,
                 emit: Any = None, *, run_pipeline: Any = None,
                 run_id: str | None = None,
                 case_manifest: dict[str, Any] | None = None,
                 build_graph: Any = None,
                 archive: Any = None,
                 law_query: str = "",
                 law_query_sources: list[str] | None = None,
                 case_query: str = "",
                 case_query_sources: list[str] | None = None) -> None:
        self.case_payload = case_payload or {}
        self.refbook = refbook
        self.retriever = retriever
        self.snapshot = snapshot or {}
        self._emit = emit
        self.refine_used = False
        self.tool_calls: list[dict[str, Any]] = []
        # 六節點流水線由呼叫端注入（契約 v2 §0.1 末段）。**注入的是回傳 plain dict 的
        # adapter，不是 `run_case` 本身**：直接注入 run_case 的話這一層雖然沒有 import
        # 語句，卻會拿到一個 CaseState 物件並讀它的屬性——AST 測試綠、層級實質被穿。
        #
        # adapter 契約（由 `backend/api/chat.py` 實作，那一層允許 import orchestrator）：
        #   run_pipeline(*, to_node=None, from_node="n1", base_run_id=None,
        #                overrides=None, on_event=None) -> dict
        #   回傳 {"run_id", "state", "node_timings", "cite_count",
        #         "artifact_id", "has_draft", "sections"}
        self.run_pipeline = run_pipeline
        #: 關聯圖 adapter，由呼叫端注入（`backend/orchestrator/chat_bridge.py`
        #: 的 `relation_graph_adapter`）。理由與 `run_pipeline` 完全相同：
        #: 這一層只碰得到 dict，runstore 與 `build_payload` 都在另一側。
        #:   build_graph(*, run_id) -> dict（契約 v2 §3.7 的形狀）
        self.build_graph = build_graph
        #: 把檢索結果寫進本案卷宗的 callable，由呼叫端注入（見 `_archive_hits`）：
        #:   archive(group: "laws"|"references", items: list[dict]) -> Any
        #: `None` ＝ 這個檔位沒有卷宗可寫（乙案容器），檢索照跑但不歸檔。
        self.archive = archive
        #: 本案最後一次成功的 run。extract 跑完會換成新的，generate 拿它當 base。
        self.run_id = run_id
        #: 本案卷宗清單（`manifest.json`，契約 v2 §4.0）。由呼叫端唯讀帶進來。
        self.case_manifest = case_manifest or {}
        #: 工具 chip 要用的兩串查詢句，由呼叫端注入（`backend/api/chat.py` 呼叫
        #: `n4_retrieval` 的 `build_query()` 與 `build_case_query()`——**跟 N4 的兩條
        #: 通道用的是同兩支**，不另寫一份）。chip 沒帶查詢詞，而模型**不准自己編一個**
        #: （紅線 1），所以缺的那一格必須由案情確定性地補上，補不出來就照實說。
        #:
        #: **兩串刻意不同**，理由與 N4 相同：`law_query` 給法條查表（吃條號與法規名），
        #: `case_query` 給相似案語意檢索（吃卷證原文與案型，不吃改寫句——改寫句會漏
        #: 撤銷案）。混用的話畫面上的相似案會跟 N4 卡片裡的是兩批東西。
        self.law_query = (law_query or "").strip()
        self.case_query = (case_query or "").strip()
        #: 兩串各自的出處（payload 欄位路徑）。給模型用：承辦人要看得出那幾個字
        #: 從哪裡來的。**兩串的出處不一樣**，共用一份就會講錯——相似案那串根本
        #: 沒讀過 `screen.art77`，卻說它是來源，那就是在陳述一件不成立的事。
        self.law_query_sources = list(law_query_sources or [])
        self.case_query_sources = list(case_query_sources or [])
        self._call_n = 0
        #: `_call_n` 的鎖。同一個 model turn 的多支工具是**併發**跑的（見 `_call`），
        #: `+= 1` 不是原子操作，兩支同時進來會拿到同一個 `tc-N`。
        self._call_lock = threading.Lock()

    # ── 事件 ────────────────────────────────────────────────────────

    @property
    def _current_call_id(self) -> str | None:
        """**這一支工具**的 `call_id`。值在 `_CURRENT_CALL_ID` 這個 ContextVar 裡。

        2026-09-13 之前它是一個 instance 欄位，`_call()` 寫、`_result()` 讀。
        雲上實測（QA `t17-callid2.sse`）抓到配錯：

            seq 1  tool_call   tc-1  search_similar_decisions
            seq 2  tool_call   tc-2  retrieve_refs
            seq 3  tool_result tc-2  retrieve_refs
            seq 4  tool_result tc-2  search_similar_decisions   ← 應該是 tc-1

        **兩個 `tool_call` 都發完了，才發第一個 `tool_result`** ——所以同一個 model turn
        的多支工具是併發跑的，不是一支跑完再跑下一支（跨輪連續呼叫的 `t16` 配對正確，
        因為那是真的一支一支來）。tc-2 的 `_call()` 在 tc-1 的 `_result()` 之前把共享
        欄位蓋掉，於是 tc-1 那張卡永遠等不到它的 result。

        畫面後果（`frontend/src/store/app.js:539` 依 `call_id` 開卡）：
        **tc-1 那張卡永遠轉圈**，而 tc-2 那張卡的標題與內容不是同一件事。

        用 `ContextVar` 不用 `threading.local`：執行緒與 asyncio task 兩種併發模型
        都收得住，而這一層不該假設上游用哪一種。
        """
        return _CURRENT_CALL_ID.get()

    def _call(self, name: str, args: dict[str, Any]) -> str:
        """開一次工具呼叫，配一個本回合內遞增的 `call_id`（契約 v2 §2.3 ②）。

        **為什麼 `tool` + `seq` 配不起來**：同一回合內同一支工具可能被呼叫兩次以上
        （讀完爭點常會再查一次法規），前端拿不到配對鍵就只能猜哪個 result 對應哪個 call。

        配號要上鎖、存號要存進 ContextVar——理由見 `_current_call_id`。
        """
        with self._call_lock:
            self._call_n += 1
            call_id = f"tc-{self._call_n}"
        _CURRENT_CALL_ID.set(call_id)
        self.tool_calls.append({"tool": name, "args": args, "call_id": call_id})
        if self._emit:
            self._emit("tool_call", {"call_id": call_id, "tool": name,
                                     "args": args, "label": TOOL_LABELS[name]})
        return call_id

    def _result(self, name: str, hits: list[dict[str, Any]], note: str = "",
                status: str = "ok", **extra: Any) -> None:
        """收一次工具呼叫。`status` 的三態是契約 v2 §2.3 ② 的第二項。

        **`empty` 與 `failed` 一定要分得開**：檢索失敗在 `_search` 內部就被攔下轉成文字
        回給模型，不會冒到 `error` 事件。沒有 `status`，前端看到的兩者長得一模一樣，
        會把「查詢來源壞了」畫成「資料庫裡沒有這筆資料」。

        `run_id`／`graph` 一律帶（沒有就是 `None`）：契約 §3.1 說三類工具各填自己那塊、
        其餘為 null，前端照著寫死解構。省略鍵會讓它拿到 undefined 而不是 null。
        """
        if not self._emit:
            return
        data: dict[str, Any] = {
            "call_id": self._current_call_id,
            "tool": name,
            "status": status,
            "note": note,
            "hits": hits,
            "run_id": None,
            "graph": None,
        }
        data.update(extra)
        self._emit("tool_result", data)

    def _step(self, node: str, status: str, elapsed_ms: int | None = None,
              degraded: bool = False, note: str = "") -> None:
        """轉發一則流水線節點事件成 `tool_step`（契約 v2 §2.3 ③）。

        `elapsed_ms` 與 `degraded` 都是 `run_case()` 實際回報的值——**一行都沒有估**。
        前端 mock 那版的 step 是 `await sleep(900)` 寫死的，換成這條的整個意義就在
        「畫面上那幾行真的對應到跑過的節點」。
        """
        if not self._emit or not node:
            return
        data = {
            "call_id": self._current_call_id,
            "step": node,
            "label": PIPELINE_NODE_LABELS.get(node, node),
            "status": status,
            "elapsed_ms": elapsed_ms,
            "degraded": degraded,
        }
        if note:
            data["note"] = note
        self._emit("tool_step", data)

    # ── 檢索共用 ────────────────────────────────────────────────────

    def _search(self, name: str, query: str, filters: dict[str, Any] | None,
                which: str) -> str:
        """跑一次檢索、配號、發事件，回給模型一段純文字。

        `which` 只影響查無時的說明文字——三個檢索工具查無的意思不一樣，
        混用同一句會讓模型誤以為「整個系統沒東西」。
        """
        _throttle()
        self._call(name, {"query": query})
        retriever = self._retriever_for(name)
        if retriever is None:
            # 沒有可用來源是**失敗**不是查無：查無的前提是真的查過了。
            self._result(name, [], _NO_RETRIEVER, status="failed")
            return _NO_RETRIEVER
        try:
            hits = retriever.search(query, filters=filters, top_k=5)
        except Exception as e:  # noqa: BLE001 — 檢索失敗要說出來，不得靜默回空當「查無」
            note = f"檢索失敗（{type(e).__name__}）：{e}"
            self._result(name, [], note, status="failed")
            # 不回「查無結果」——那會讓模型把一次失敗講成「資料庫裡沒有」。
            # 訊息本身也不寫「查無」二字：模型很容易照抄回覆裡出現過的詞。
            return f"{note}。請告訴使用者這次查詢失敗了，不要說成資料庫裡沒有這筆資料。"
        entries = self.refbook.add_all(hits, _score_kind(retriever))
        # 歸檔在**發事件之前**：前端收到 `tool_result` 就會去重載右欄那一組
        # （`refreshGroupIds`），寫在事件之後會讓它讀到還沒寫進去的舊清單。
        self._archive_hits(name, hits)
        self._result(name, entries, "" if entries else f"{which}查無結果。",
                     status="ok" if entries else "empty")
        if not entries:
            return f"{which}查無結果。請直接說查無，不要引用任何編號。"
        lines = []
        for h, entry in zip(hits, entries):
            text = (getattr(h, "payload", None) or {}).get("text", "")
            lines.append(f"[{entry['id']}] {entry['t']}（{entry['src']}）\n{text}".rstrip())
        return "\n\n".join(lines)

    # ── 歸檔（契約 §3.0「歸檔到」那一欄）────────────────────────────

    def _archive_hits(self, name: str, hits: list[Any]) -> None:
        """把檢索到的東西寫進本案卷宗。**失敗不得讓工具失敗。**

        ## 為什麼要有這一段

        契約 §3.0 寫著 `search_regulations` → `laws` 群組、`search_similar_decisions`
        → `cases` 群組，但 2026-09-13 之前**從來沒有人實作它**：後端唯二寫卷宗的地方
        都在 REST 端點裡。後果是整條 demo 動線斷掉——chip 查到了東西，東西沒落地，
        `generate_decision_draft` 的前置條件 3 讀 manifest 讀到空的，正確地拒絕生成。
        承辦人看到的是「生成草稿永遠都是空的」。

        ## 為什麼是注入的 callable

        寫檔與讀 S3 都是 I/O，這一層不碰（spec §4.0，理由同 `run_pipeline`）。
        乙案（AgentCore Runtime）容器裡沒有卷宗目錄，`archive` 就是 `None`，
        檢索照跑、只是不歸檔——那是真的沒有地方可寫，不是靜默失敗。

        ## 為什麼吞掉例外

        歸檔是**副作用**，不是這次查詢的結果。寫檔失敗不該讓一次成功的檢索
        變成「查詢失敗」——那會把使用者的注意力導到錯的地方。失敗印進伺服器 log。
        """
        if self.archive is None:
            return
        group = {"search_regulations": "laws",
                 "search_similar_decisions": "references"}.get(name)
        if group is None:
            return
        try:
            items = (self._law_items(hits) if group == "laws"
                     else self._reference_items(hits))
            if items:
                self.archive(group, items)
        except Exception as e:  # noqa: BLE001 — 見 docstring：歸檔失敗不等於檢索失敗
            print(f"[warn] 歸檔 {group} 失敗（檢索結果仍然有效）：{type(e).__name__}: {e}",
                  file=sys.stderr)

    @staticmethod
    def _law_items(hits: list[Any]) -> list[dict[str, Any]]:
        """法條查表的 hit → 卷宗 `laws[]` 的一筆。

        **只收 `verified` 的那些**（條號存在於快照）。查表還會回兩種非命中：
        條號寫法無法無歧義解析、以及法規不在快照涵蓋範圍內——兩種的 `score` 都是 0、
        `note` 都在說「本系統驗不了」。把它們當成「相關法規」歸檔進去，等於把一句
        「我不知道」畫成一筆依據。

        `body_cached` 一律空字串：**快照裡根本沒有條文原文**
        （`laws-snapshot.json` 只有條號清單與 `max`）。假裝有全文比沒有更糟，
        所以 `note` 直接說明它是什麼等級的東西。
        """
        items: list[dict[str, Any]] = []
        for h in hits or []:
            if not getattr(h, "verified", False):
                continue
            payload = getattr(h, "payload", None) or {}
            law, article = payload.get("law"), payload.get("article")
            if not law or article is None:
                continue
            items.append({
                "id": f"{ARCHIVE_LAWTABLE_PREFIX}{law}-{article}",
                "t": getattr(h, "title", "") or f"{law}第{article}條",
                "src": getattr(h, "source", ""),
                "note": "條號存在性驗證，非法條全文（快照只索引條號）",
                "verified": True,
                "relevance": "unknown",
                "body_cached": "",
                "channel": ARCHIVE_CHANNEL_LAWTABLE,
                "retrieval_status": PICK_FOUND_BY_TOOL,
                "retrieval_note": PICK_NOTES[PICK_FOUND_BY_TOOL],
            })
        return items

    @staticmethod
    def _reference_items(hits: list[Any]) -> list[dict[str, Any]]:
        """母庫相似決定的 hit → 卷宗 `references[]` 的一筆。

        **`id` 就是 S3 key**，跟 REST 那條路存的一模一樣：同一份文件從兩條路加進來
        要落在同一筆（`store.add_items` 依 `id` 去重），移除端點也就不必認兩種 id。

        key 要 `kb/<kind>/<source>` 才完整，而 `kind` 原本在 `Hit` 上留不下來——
        2026-09-13 在 `backend/retrieval/kb.py` 把它補進 `payload["kb_kind"]`。
        **拿不到就不歸檔這一筆**：猜一個前綴（先試 official 再試 public）在猜錯時
        會讀到另一份文件，而那個錯看起來完全正常。

        `full_cached` 留空，由 `archive` 那一側決定要不要去 S3 抓
        （`payload["text"]` 是**這一次命中的 chunk**，不是全文，拿它充數就是假資料）。
        """
        items: list[dict[str, Any]] = []
        for h in hits or []:
            payload = getattr(h, "payload", None) or {}
            kind, source = payload.get("kb_kind"), getattr(h, "source", "")
            if not kind or not source or kind == "unknown":
                continue
            items.append({
                "id": f"kb/{kind}/{source}",
                "t": getattr(h, "title", ""),
                "src": source,
                "note": "",
                # 分數是**某一次查詢**的相似度，不是這份決定書的屬性。加進卷宗之後
                # 它就沒有對應的查詢了，填一個舊分數會讓人以為那是「跟本案的相似度」。
                # 與 REST 那條路同一個判斷（`backend/api/dossier.py` 的 add_case_references）。
                "score": None,
                "provenance": payload.get("provenance"),
                "doc_kind": "decision",
                "verdict": payload.get("outcome"),
                "category": payload.get("category"),
                "full_cached": "",
                "channel": ARCHIVE_CHANNEL_CORPUS,
            })
        return items

    def _retriever_for(self, name: str) -> Any:
        if name == "search_regulations":
            if not self.snapshot:
                return None
            return LawTableRetriever(self.snapshot)
        return self.retriever

    # ── 五個工具的實作（與 @tool 包裝分離，方便離線測試）──────────────

    def search_regulations(self, query: str) -> str:
        return self._search("search_regulations", query, None, "法規快照")

    def search_similar_decisions(self, query: str) -> str:
        return self._search("search_similar_decisions", query, None, "相似訴願決定")

    def retrieve_refs(self, query: str) -> str:
        return self._search("retrieve_refs", query,
                            {"prefix": settings.ref_prefixes()}, "函釋與判解")

    #: 每個分區是什麼的一句話。**跟著資料一起給模型**：光給一個鍵名，
    #: 模型會照自己的理解命名它，於是「卷宗清單」與「這一輪檢索到的」又混在一起。
    _SECTION_WHAT = {
        "intake": "收文欄位（卷證抽取出來的）",
        "facts_excerpt": "事實段原文摘錄",
        "screen": "程序審查結果",
        # **不要寫「畫面右欄」**（2026-09-13 修）：後端不知道承辦人的畫面長什麼樣，
        # 而模型會照抄（紅線 6）。講「案件卷宗」就夠了——那是這個東西的名字，
        # 不是它在螢幕上的位置，而分辨這兩份靠的本來就是名字不是位置。
        "laws": "**案件卷宗**裡的相關法規——承辦人自己挑進本案的那份，"
                "也是生成草稿的前置條件看的那份",
        "cases": "**案件卷宗**裡的相關案例——承辦人自己挑進本案的那份，"
                 "也是生成草稿的前置條件看的那份",
        "retrieved_laws": "**這一輪檢索**查到的法條（N4 查表的結果），"
                          "不是承辦人挑的那份",
        "retrieved_cases": "**這一輪檢索**查到的相似決定，不是承辦人挑的那份",
    }

    def _section_value(self, section: str) -> Any:
        """分區 → 值。兩個來源：卷宗清單（manifest）與這一輪的 run payload。

        **`laws`／`cases` 讀的是 manifest 不是 payload**（2026-09-13 改，見
        `CASE_SECTIONS` 的說明）。承辦人在右欄看到「相關法規 2」的時候，
        任何一個回答都不能說「卷內沒有法規」。
        """
        if section in _MANIFEST_SECTIONS:
            return self.case_manifest.get(_MANIFEST_SECTIONS[section])
        if section in _RETRIEVED_SECTIONS:
            return self.case_payload.get(_RETRIEVED_SECTIONS[section])
        return self.case_payload.get(section)

    def _empty_section_note(self, section: str) -> str:
        """某個分區空的時候，順便講清楚**另外那一份有沒有東西**。

        不講的話最糟的形狀會回來：卷宗是空的、但這一輪檢索到 5 條，模型回一句
        「卷內沒有法規」，承辦人看著右欄的檢索結果一頭霧水。兩份各自的空滿要分開說。
        """
        what = self._SECTION_WHAT.get(section, section)
        note = f"「{section}」是空的（{what}）。"
        pair = {"laws": "retrieved_laws", "cases": "retrieved_cases",
                "retrieved_laws": "laws", "retrieved_cases": "cases"}.get(section)
        if not pair:
            return note
        other = self._section_value(pair)
        n = len(other) if isinstance(other, list) else 0
        if n:
            note += (f"但另外那一份不是空的：「{pair}」有 {n} 筆"
                     f"（{self._SECTION_WHAT.get(pair, pair)}）。"
                     f"**不要說成「卷內什麼都沒有」**，兩份是不同的東西。")
        elif section in _MANIFEST_SECTIONS:
            note += "請承辦人先把法規／案例加進本案卷宗，或先查一次再加。"
        return note

    def read_case(self, section: str) -> str:
        """讀一個分區。**不碰 runstore、不呼叫 build_payload**（spec §4.0）。

        兩個來源：卷宗清單（`case_manifest`，承辦人挑的）與這一輪的 run payload
        （N4 檢索到的）。哪個分區走哪個來源見 `CASE_SECTIONS` 的說明——
        那段也寫了為什麼 2026-09-13 要把它們拆開。
        """
        _throttle()
        self._call("read_case", {"section": section})
        if section not in CASE_SECTIONS:
            note = f"沒有「{section}」這個分區。可讀的是：{'、'.join(CASE_SECTIONS)}。"
            # 要一個不存在的分區是呼叫本身壞了，不是「這裡沒有資料」。
            self._result("read_case", [], note, status="failed")
            return note
        value = self._section_value(section)
        if value in (None, "", [], {}):
            note = self._empty_section_note(section)
            self._result("read_case", [], note, status="empty")
            return note
        # 這一輪檢索到的 laws/cases 自帶 id（N4 編的 `L1`、`C3`），模型讀了就會引用它們。
        # 註冊進白名單，否則那些引用會被當成捏造的（或更糟：在原版的小寫樣式下
        # 根本偵測不到，靜默放行）。
        #
        # **卷宗清單那兩個分區不註冊**：它們的 `id` 是母庫 S3 key
        # （`kb/public/…txt`），不是畫面上印得出來的編號。註冊進去模型就會寫
        # `[kb/public/…]`，那既不是承辦人看得懂的東西，也不在任何一張卡片上。
        registered: list[str] = []
        if isinstance(value, list) and section in _RETRIEVED_SECTIONS:
            registered = self.refbook.add_case_refs(
                [v for v in value if isinstance(v, dict) and v.get("id")])
        note = f"已讀取「{section}」（{self._SECTION_WHAT.get(section, section)}）。"
        if registered:
            note += f"可引用的卷內編號：{'、'.join(registered)}。"
        elif section in _MANIFEST_SECTIONS:
            note += ("這份是承辦人挑的卷宗清單，**沒有可引用的編號**——"
                     "要引用請讀 retrieved_laws／retrieved_cases。")
        # hits 仍為 []：spec §4.0／§4.2 明訂 read_case 不產生引用事件，前端照這個寫。
        # 白名單是後端內部狀態，不走事件。
        self._result("read_case", [], note)
        return json.dumps({"section": section, "是什麼": self._SECTION_WHAT.get(section, section),
                           "內容": value}, ensure_ascii=False, indent=1)

    def refine_text(self, text: str, instruction: str = "改寫得更通順") -> str:
        """單獨一次模型呼叫改寫文字。**本回合強制紅燈**（規則 2）。

        改寫出來的句子沒有任何出處可言，系統不替它背書——所以旗標一旦立起來就不放下，
        即使同回合也檢索到了 refs。
        """
        _throttle()
        self.refine_used = True
        self._call("refine_text", {"instruction": instruction})
        if Agent is None:
            self._result("refine_text", [], "模型不可用", status="failed")
            raise LLMError(_STRANDS_MISSING)
        agent = Agent(model=_load_model(model_kind="draft"),
                      system_prompt=_prompt("chat_refine"),
                      tools=[], callback_handler=None)
        out = str(agent(f"指示：{instruction}\n\n原文：\n{text}"))
        self._result("refine_text", [], "已改寫；改寫文字無出處，本則回答標為請人工判斷。")
        return out

    # ── 兩支 pipeline 工具（契約 v2 §3.3／§3.5）──────────────────────

    def _forward_node_event(self, kind: str, data: dict[str, Any]) -> None:
        """`run_case()` 的 `on_event` → `tool_step`。**只轉發，不加工。**"""
        node = str(data.get("node") or "")
        if kind == "node_start":
            self._step(node, "running")
        elif kind == "node_done":
            self._step(node, "done",
                       elapsed_ms=data.get("elapsed_ms"),
                       degraded=bool(data.get("degraded")))
        elif kind == "run_failed":
            self._step(node, "failed", note=str(data.get("error") or ""))

    def _adopt_run(self, out: dict[str, Any]) -> None:
        """把流水線跑出來的新 run 接管成「本回合之後的卷內」。

        **順序是契約的一部分**（契約 v2 §0.1 第 2 點）：先把舊 run 的卷內編號從白名單
        清掉，再換上新 run 的分區。反過來的話 `read_case` 會拿新內容去撞舊編號，
        模型引新草稿的 `[L1]`、`refs[]` 卻帶出舊 run 的法條——號對得上、內容是別人的。
        呼叫時機在「工具回傳之後、模型組答案之前」，所以模型看到的一定是新的那批。
        """
        self.refbook.reset_case_refs()
        self.case_payload = dict(out.get("sections") or {})
        self.run_id = out.get("run_id") or self.run_id

    def _pipeline_missing(self, name: str) -> str:
        self._result(name, [], _NO_PIPELINE, status="failed")
        return _NO_PIPELINE

    def extract_case_document(self) -> str:
        """跑 n1–n3，把卷證解析成收文欄位、事實摘錄與程序審查。"""
        _throttle()
        self._call("extract_case_document", {})
        if self.run_pipeline is None:
            return self._pipeline_missing("extract_case_document")
        try:
            out = self.run_pipeline(to_node="n3", on_event=self._forward_node_event)
        except Exception as e:  # noqa: BLE001 — 解析失敗照實說，不得回一段編出來的卷內
            note = f"解析卷證失敗（{type(e).__name__}）：{e}"
            self._result("extract_case_document", [], note, status="failed")
            return f"{note}。請告訴使用者這次解析失敗了，不要編造卷內內容。"
        self._adopt_run(out)
        # 降級原因照實往下傳（2026-09-13 補）。沒有降級時 `note` 仍是 `""`、
        # 回給模型的字串一個字都不變——正常案件不該多出一段警告。
        stuck = degraded_summary(out)
        self._result("extract_case_document", [], stuck, status="ok",
                     run_id=out.get("run_id"), state=out.get("state"))
        if stuck:
            return (f"卷證解析跑完了，但**有節點降級，這個案子還不能往下走**"
                    f"（run {out.get('run_id')}，終態 {out.get('state')}）。"
                    f"降級原因：{stuck}。"
                    f"這個 run 只跑到程序審查，沒有草稿。"
                    f"請照實把上面的原因告訴使用者，講清楚卡在哪一步、要補哪幾個欄位，"
                    f"並說明補齊後才能續跑。**不要說解析已完成，也不要說現在可以生成草稿。**"
                    f"{_EXTRACT_NO_REREAD}")
        return (f"已完成卷證解析（run {out.get('run_id')}，終態 {out.get('state')}）。"
                f"這個 run **只跑到程序審查，沒有草稿**。{_EXTRACT_NO_REREAD}")

    def generate_decision_draft(self) -> str:
        """跑 n4–n6，產出一份經過引用守門的決定書草稿。

        三個前置條件（契約 v2 §3.5.1）擋在這裡。**後端也要擋**，不是只靠前端 disable：
        上游檢索全空時 N5 照樣生得出草稿，但每一個引用都會被清掉並判紅——
        產出一份通篇沒有依據的草稿，而**那個失敗看起來很像成功**。
        """
        _throttle()
        self._call("generate_decision_draft", {})
        if self.run_pipeline is None:
            return self._pipeline_missing("generate_decision_draft")

        # 前置 2：要有一次跑過程序審查的 run。**不要自己先跑 n1** ——
        # 那會讓使用者以為草稿是憑空生出來的。
        if not self.run_id or not self.case_payload.get("screen"):
            note = "還沒有解析過卷證。"
            self._result("generate_decision_draft", [], note, status="failed")
            return f"{note}請告訴使用者要先解析卷證，不要代為執行。"

        # 前置 3：要有相關法規與相關案例。來源是本案卷宗清單（manifest），
        # 不是 N4 自己查到的東西——這條擋的正是「使用者什麼都沒挑就按生成」。
        laws = list(self.case_manifest.get("laws") or [])
        refs = list(self.case_manifest.get("references") or [])
        if not laws or not refs:
            note = "還沒有查過法規與相似案例。"
            self._result("generate_decision_draft", [], note, status="failed")
            return f"{note}請告訴使用者要先查法規與相似案例，不要生一份沒有依據的草稿。"

        # 形狀不對就**乾淨地失敗**，不要讓它在下面炸掉。
        #
        # 為什麼需要這一條（2026-09-12 實測）：`laws` 若是 dict 而不是 list of dict，
        # `list()` 會拿到 key 的字串，接著 `l.get("t")` 拋 `AttributeError`。
        # 那個例外在兩個地方都在 `try` 之外——組 `n4_query` 的那行，以及
        # `_adopt_run` 之後的 `classify_picks()`。後果不是一張寫著失敗的工具卡，
        # 而是**整輪以 `error`／`stage:"internal"` 收掉，先前發出的 `tool_call`
        # 永遠等不到配對的 `tool_result`，前端那張卡一直轉**。
        #
        # **這不是替寫端的假想錯誤寫特判**（那會是「如果是 dict 就轉成 list」）。
        # 這是讀端自己的責任：同一份壞輸入，要壞成一張說得出原因的 `failed` 卡。
        # 正常路徑其實到不了這裡——`load_case_manifest` 走 `store.load()`，
        # `_normalise` 已經保證四個群組是 list。但 `case_manifest` 是**注入**的，
        # 誰餵進來都算數，工具不該假設呼叫端一定守規矩。
        #
        # 與「靜默回空」的差別：檔案不存在、JSON 壞掉、頂層不是物件，那些在
        # `load_case_manifest` 就變成 `{}`，語意是「這個案子的清單是空的」——
        # 使用者去挑幾條法規就解決了。這裡攔的是「清單有東西但不是清單的形狀」，
        # 那是資料壞了，不是使用者少做一步，**兩者不同級，所以訊息也不同**。
        bad = [x for x in laws + refs if not isinstance(x, dict)]
        if bad:
            note = "卷宗清單的格式不對：法規或案例不是一筆一筆的資料。"
            self._result("generate_decision_draft", [], note, status="failed")
            return (f"{note}請告訴使用者這個案子的卷宗資料有問題，"
                    f"不要猜它原本想放什麼，也不要生一份草稿代替。")

        # 手動挑的法規當**查詢詞**餵回 N4（契約 v2 §3.5.2，Ci 拍板 (b)）。
        # 這不違反 2026-09-05「N4 獨立檢索」的拍板：餵的是查詢詞不是答案，
        # N4 查得到才會進 laws[]，查不到就是查不到。
        terms = PICK_QUERY_SEP.join(str(l.get("t") or "").strip() for l in laws if l.get("t"))
        overrides = {"n4_query": terms} if terms else None
        try:
            out = self.run_pipeline(from_node="n4", base_run_id=self.run_id,
                                    overrides=overrides,
                                    on_event=self._forward_node_event)
        except Exception as e:  # noqa: BLE001
            note = f"生成草稿失敗（{type(e).__name__}）：{e}"
            self._result("generate_decision_draft", [], note, status="failed")
            return f"{note}。請告訴使用者這次生成失敗了，不要自己寫一份草稿代替。"
        self._adopt_run(out)
        # 契約 §3.5.2 末段（2026-09-13 Ci 改判成三態）。挑的法規幾乎必然只進通道 B
        # 比對在 `_adopt_run` 之後做：那時 `case_payload["laws"]` 已經換成這一輪
        # N4 真的查到的東西。
        # ——查表只認「法名第N條」，而 manifest 的 `t` 是純法規名。舊文案報「未命中」
        # 是**一句假話**：那個詞確實被用到了，只是用在相似案檢索。
        # `terms` 就是這一輪真的送出去的查詢詞，拿它當通道 B 的依據，不是推論。
        picks = classify_picks(laws, self.case_payload.get("laws") or [], terms)
        needs_note = [p for p in picks if p["state"] != PICK_MATCHED]
        # 同 `extract_case_document`：降級原因是流水線自己寫的，一個字都不加工。
        # 這條路徑 `from_node="n4"`，所以帶上來的可能含 N1 那筆（沒重跑的節點會被
        # `run_case()` 從 base_state 續帶）——那正是要講的：欄位還缺著就生了草稿。
        stuck = degraded_summary(out)
        self._result("generate_decision_draft", [], stuck, status="ok",
                     run_id=out.get("run_id"), state=out.get("state"),
                     artifact_id=out.get("artifact_id"),
                     cite_count=out.get("cite_count"),
                     picked_laws=picks)
        note = ""
        if needs_note:
            names = "、".join(str(u.get("t") or u.get("id")) for u in needs_note)
            note = (f"另外：你挑的法規中，{names} 沒有進入法條查表，所以草稿不會引用它們"
                    f"（它們仍被用於相似案檢索）。原因是法條查表只認「法名第N條」，"
                    f"純法規名查不到。請照實告訴使用者這件事與下一步："
                    f"要讓某條法規成為草稿的引用依據，要把它指定到條，例如「訴願法第14條」。"
                    f"不要說它們已被引用，也不要說它們完全沒被用到。")
        stuck_note = ""
        if stuck:
            stuck_note = (f"另外：這次執行有節點降級——{stuck}。"
                          f"請照實把這件事告訴使用者，"
                          f"**不要說這份草稿已經可以直接用、也不要說案件已審查完成**。")
        return (f"已生成草稿（run {out.get('run_id')}，終態 {out.get('state')}，"
                f"引用 {out.get('cite_count')} 處）。草稿全文不要在這裡整份複述——"
                f"它會以完整文件的形式交給承辦人。{stuck_note}{note}")

    # ── 工具 chip（`tool_hint`）──────────────────────────────────────

    def run_tool_hint(self, hint: str) -> str | None:
        """前端工具 chip 指定的那一支，**這一回合就把它跑掉**。

        回傳要塞進 user message 的一段話（已經跑完、結果如下、不要再跑一次）；
        `hint` 不在白名單就回 `None`，由模型照原本的方式自己判斷。

        ## 為什麼不能只是「提示」模型

        2026-09-13 實測：Ci 按了 chip「/查找相似案例」，畫面上跑的是 `read_case`，
        然後回「卷內還沒有相似案例的資料」。**按鈕寫查找，它去讀已經有的東西。**

        而模型的選擇其實有道理：`search_similar_decisions(query)` 要一個查詢字串，
        chip 沒帶，它**不願意編一個**——紅線 1 就是這樣要求的。它退而求其次去讀卷內，
        然後老實說是空的。**問題不在模型，在沒有人給它查詢詞。**

        所以解法是兩半，缺一不可：
        1. chip 是**明確指令**不是暗示 → 後端直接執行，不留給模型決定。
        2. 缺的參數由**案情確定性導出**（`self.case_query`，來源是 N4 的
           `build_query()`，同一支不另寫），導不出來就照實說，**不編一個詞去查**。

        ## 自由打字的問句不走這裡

        那條路徑不帶 `tool_hint`，維持原樣由模型自己判斷（契約 §2.1）。
        把它也變成強制的話，「幫我看一下這件案子」會被硬塞成某一支工具。
        """
        runner = {
            "extract_case_document": self.extract_case_document,
            "generate_decision_draft": self.generate_decision_draft,
            "build_relation_graph": self.build_relation_graph,
            "search_similar_decisions":
                lambda: self._search_from_case("search_similar_decisions", "相似訴願決定",
                                               self.case_query, self.case_query_sources),
            "search_regulations":
                lambda: self._search_from_case("search_regulations", "法規快照",
                                               self.law_query, self.law_query_sources),
        }.get(hint)
        if runner is None:
            return None
        out = runner()
        return (f"【已代為執行】承辦人按的是工具「{TOOL_LABELS.get(hint, hint)}」，"
                f"系統已經替他跑完了，結果如下。**不要再呼叫一次同一支工具**，"
                f"直接根據這個結果回答：\n{out}")

    def _search_from_case(self, name: str, which: str, query: str,
                          sources: list[str]) -> str:
        """用**本案案情**組出來的查詢句跑檢索。查詢句是空的就照實說，不編一個。

        空的原因一定是上游還沒有東西可組（沒解析過卷證、或 N1–N3 的結果裡沒有
        案型／不受理事由／法規名）。那時候唯一誠實的做法是停下來講清楚——
        隨手丟一個「行政處分」之類的通用詞進去，會查回一堆跟本案無關的決定書，
        而且看起來很像查到了（CONSTITUTION §1）。
        """
        if not query:
            _throttle()
            self._call(name, {"query": None})
            note = ("這件案子還導不出查詢詞：查詢詞由卷證解析的結果組出來"
                    "（案型、程序不受理事由、期間依據的法條），目前這些都還是空的。")
            self._result(name, [], note, status="empty")
            return (f"{note}請告訴承辦人**要先解析卷證**，跑完之後這個按鈕才查得到東西；"
                    f"或者他可以直接打字把爭點、處分依據告訴你，你再用那些字去查。"
                    f"**不要自己想一個查詢詞去查**，也不要說查無相似案例"
                    f"——這次根本還沒查。")
        out = self._search(name, query, None, which)
        # 查詢句可能很長（相似案那條吃整段卷證原文），回給模型的話裡只放節錄——
        # 整串貼進對話等於把卷證再複述一遍，而模型只需要知道「這是系統組的、不是它想的」。
        froms = "、".join(str(x) for x in sources if x)
        return (f"（查詢詞「{_clip(query)}」是系統從本案案情組出來的"
                f"{'，來源：' + froms if froms else ''}，不是承辦人打的，也不是你想的。"
                f"要講查詢詞就講來源，不要把它整串唸出來。）\n{out}")

    def build_relation_graph(self) -> str:
        """畫本案的關聯圖：卷證 → 事實 → 爭點 → 法規依據 → 結論（契約 v2 §3.7）。

        **零模型呼叫**：圖整個是 payload 欄位的字串比對推出來的
        （`backend/graph/relation.py`）。這一層只負責把它拿到、放進 `tool_result`
        的 `graph` 欄位，**不描述圖的內容**——圖是給眼睛看的，複述一遍只會
        多一個可能說錯的地方。

        回給模型的是**數字與斷點**，不是節點清單：模型要講的是
        「有幾條引用查無」這種承辦人需要被提醒的事。
        """
        _throttle()
        self._call("build_relation_graph", {})
        if self.build_graph is None:
            self._result("build_relation_graph", [], _NO_GRAPH, status="failed")
            return _NO_GRAPH
        if not self.run_id:
            self._result("build_relation_graph", [], _NO_RUN_FOR_GRAPH, status="empty")
            return _NO_RUN_FOR_GRAPH
        try:
            graph = self.build_graph(run_id=self.run_id)
        except Exception as e:  # noqa: BLE001 — 畫不出來照實說，不要描述一張沒有的圖
            note = f"畫關聯圖失敗（{type(e).__name__}）：{e}"
            self._result("build_relation_graph", [], note, status="failed")
            return f"{note}。請告訴使用者這次畫不出來，不要自己描述一張圖。"

        stats = graph.get("stats") or {}
        if graph.get("status") == "empty":
            # `empty` 不是 `failed`：資料還沒到那一步，不是東西壞了（契約 v2 §2.3 ②）。
            note = str(graph.get("note") or "")
            self._result("build_relation_graph", [], note, status="empty", graph=graph)
            return f"{note}（這不是失敗，是還沒生成草稿。）"
        self._result("build_relation_graph", [], "", status="ok", graph=graph)

        flagged = graph.get("flagged") or []
        unlinked = graph.get("unlinked") or {}
        parts = [f"已畫出關聯圖：{stats.get('nodes')} 個節點、{stats.get('edges')} 條關聯，"
                 f"草稿 {stats.get('sentences_total')} 句裡有 "
                 f"{stats.get('sentences_in_graph')} 句連得上爭點或法規。"]
        if flagged:
            raws = "、".join(str(f.get("raw")) for f in flagged)
            parts.append(f"**有 {len(flagged)} 處引用在檢索結果裡查無：{raws}。**"
                         f"這一項請務必告訴使用者——草稿引了我們沒檢索到的法條。")
        if unlinked.get("laws"):
            parts.append(f"另有 {len(unlinked['laws'])} 條檢索到的法規沒有被任何句子引用。")
        if unlinked.get("issues"):
            parts.append(str(unlinked.get("note") or ""))
        # **不要寫「回給畫面了」這種話**（2026-09-13 修）：後端不知道承辦人的畫面
        # 長什麼樣，而模型會照抄。這跟紅線 6 是同一件事，只是搬到了工具回傳值上——
        # 假話的來源不是模型，是我們餵給它的字串。
        parts.append("這張圖已經以結構化資料回傳，**不要在對話裡逐一複述節點**。")
        return "".join(parts)

    # ── 給 Strands 的 @tool 包裝 ────────────────────────────────────

    def as_strands_tools(self) -> list[Any]:
        """包成 Strands 的 `@tool`。docstring 就是模型看到的工具說明，寫給模型看。"""
        if tool is None:
            raise LLMError(_STRANDS_MISSING)
        outer = self

        @tool
        def search_regulations(query: str) -> str:
            """查某個法條在不在法規快照裡。**不會回條文原文**，只回存不存在。

            Args:
                query: 法規名加條號，例如「訴願法第 14 條」。
            """
            return outer.search_regulations(query)

        @tool
        def search_similar_decisions(query: str) -> str:
            """查性質相近的歷史訴願決定。

            Args:
                query: 爭點或處分依據的關鍵字，繁體中文。
            """
            return outer.search_similar_decisions(query)

        @tool
        def retrieve_refs(query: str) -> str:
            """查行政函釋與司法院釋字／行政判解的原文段落。

            Args:
                query: 法律原則或爭點的關鍵字，繁體中文。
            """
            return outer.retrieve_refs(query)

        @tool
        def extract_case_document() -> str:
            """解析本案卷證檔案：抽收文欄位、摘錄事實、做程序審查。

            **只有卷證還沒解析過、或承辦人明確要求重新解析時才用。**
            這一支要跑十秒以上，不要為了確認一件小事就呼叫它。
            """
            return outer.extract_case_document()

        @tool
        def generate_decision_draft() -> str:
            """依卷內資料與已挑選的法規、相似案例，生成決定書草稿。

            **只有承辦人明確要求生成草稿時才用。** 需要先解析過卷證、
            並且本案卷宗裡已經有法規與相似案例；缺任一項會回失敗，
            這時請照實告訴承辦人缺什麼，不要自己寫一份草稿代替。
            """
            return outer.generate_decision_draft()

        @tool
        def build_relation_graph() -> str:
            """畫本案的關聯圖：卷證 → 事實 → 爭點 → 法規依據 → 結論。

            **只有承辦人想看「這個結論是從哪裡推出來的」時才用。**
            需要先生成草稿；只解析過卷證的話會回「還畫不出來」，
            這時請照實說要先生成草稿，不要自己描述一張圖。
            """
            return outer.build_relation_graph()

        @tool
        def read_case(section: str) -> str:
            """讀本案卷內資料。

            Args:
                section: intake（收文欄位）｜facts_excerpt（事實摘錄）｜
                    screen（程序審查）｜laws（卷宗清單裡的法規）｜cases（卷宗清單裡的案例）｜
                    retrieved_laws（這一輪檢索到的法條）｜retrieved_cases（這一輪檢索到的相似決定）。
            """
            return outer.read_case(section)

        @tool
        def refine_text(text: str, instruction: str = "改寫得更通順") -> str:
            """把一段文字改寫得更通順。**只有承辦人明確要求改寫時才用。**

            Args:
                text: 要改寫的原文。
                instruction: 改寫方向。
            """
            return outer.refine_text(text, instruction)

        return [extract_case_document, search_regulations, search_similar_decisions,
                retrieve_refs, generate_decision_draft, read_case, refine_text,
                build_relation_graph]


def build_chat_agent(case_payload: dict[str, Any], refbook: RefBook,
                     retriever: Any = None, snapshot: dict[str, Any] | None = None,
                     emit: Any = None, *, run_pipeline: Any = None,
                     run_id: str | None = None,
                     case_manifest: dict[str, Any] | None = None,
                     build_graph: Any = None,
                     archive: Any = None,
                     law_query: str = "",
                     law_query_sources: list[str] | None = None,
                     case_query: str = "",
                     case_query_sources: list[str] | None = None,
                     ) -> tuple[Any, ChatTools]:
    """建一個聊天 agent。回傳 `(agent, tools)`——`tools` 帶著本回合的狀態。

    `case_payload` 是一個**普通 dict**，由 HTTP 層（`backend/api/chat.py`）先
    `load_run` → `build_payload` → 切分區之後餵進來（spec §4.0）。這一層完全不知道
    runstore 與 orchestrator 的存在，乙案（AgentCore Runtime）容器裡也沒有它們。

    `model_kind` **一律用關鍵字傳**：既有兩處用位置參數，日後在中間插一個參數會同時
    靜默錯位，而錯位的症狀是「用了另一顆模型」——那在 IAM 只放行兩顆模型 ARN 的
    task role 上會變成 AccessDenied，且本機權限較寬，完全驗不出來。
    """
    if Agent is None:
        raise LLMError(_STRANDS_MISSING)
    tools = ChatTools(case_payload, refbook, retriever, snapshot, emit,
                      run_pipeline=run_pipeline, run_id=run_id,
                      case_manifest=case_manifest, build_graph=build_graph,
                      archive=archive,
                      law_query=law_query, law_query_sources=law_query_sources,
                      case_query=case_query, case_query_sources=case_query_sources)
    agent = Agent(
        model=_load_model(model_kind="draft"),
        system_prompt=_prompt("chat_ask"),
        tools=tools.as_strands_tools(),
        callback_handler=None,
    )
    return agent, tools


__all__ = [
    "NUMERIC_Q",
    "REDIRECT_DEADLINE",
    "RefBook",
    "Verdict",
    "CASE_SECTIONS",
    "ChatTools",
    "TOOL_LABELS",
    "PIPELINE_NODE_LABELS",
    "build_chat_agent",
    "cited_ids",
    "ARCHIVE_CHANNEL_CORPUS",
    "ARCHIVE_CHANNEL_LAWTABLE",
    "ARCHIVE_LAWTABLE_PREFIX",
    "PICK_FOUND_BY_TOOL",
    "degraded_summary",
    "classify_answer",
    "is_numeric_question",
    "TIER_HUMAN",
]
