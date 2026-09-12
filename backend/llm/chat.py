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

同理 `REF_PREFIXES` 從 `backend/retrieval/kb.py` import，**不從 `backend/nodes/n5_draft.py`**
——後者自己 import `backend.llm.client`，聊天層再 import 它就是層級倒置。

## strands 的頂層 import 用 try/except 守衛

比照 `backend/llm/client.py` 的既有做法（spec D8：import 一律在模組頂層）。
沒裝 strands 時 `import backend.llm.chat` 仍要成功（AC2），缺席只在呼叫點 raise。
"""
from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

from backend.config.origin_registry import TIER_HUMAN
from backend.gate.lamps import tier_for_lamp
from backend.llm.client import _STRANDS_MISSING, LLMError, _load_model, _prompt, _throttle
from backend.retrieval.kb import REF_PREFIXES
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
_CITE_BRACKETED = re.compile(r"[\[\［【(（]\s*((?:c\d+\s*[,，、]?\s*)+)[\]\］】)）]")
_CITE_ID = re.compile(r"c\d+")

#: 偵測「引了白名單以外的編號」用的**寬鬆**樣式，連沒加括號的 `c9` 也算。
#: 兩種樣式刻意不對稱，方向都朝安全：
#:   - 規則 3（判 `y`）用嚴格樣式——漏認一個引用只會多紅一則，是安全方向。
#:   - `dropped_refs`（強制紅）用寬鬆樣式——漏認一個捏造編號會讓假引用矇混過關，
#:     那是不安全方向，所以寧可多抓。
_CITE_LOOSE = re.compile(r"c\d+")

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
WHY_SOURCED = ("本則回答引用了 {n} 筆檢索命中；命中為 KB 向量相似度結果，"
               "未對資料集實檔驗證，請覆核後採用。")
WHY_UNSOURCED = "本則回答沒有引用任何檢索命中，系統無法確認其依據，請人工判斷。"
WHY_DROPPED = ("模型引用了檢索結果之外的來源（{dropped}），已移除；"
               "該則回答視為無出處。")


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

    def add(self, hit: Any) -> dict[str, Any]:
        """收一筆 `Hit`（或已經 `as_dict()` 過的 dict），配號並回傳 wire 格式的 hit。

        回傳的形狀就是 spec §4.2 `tool_result.hits[]` 的一筆：
        `{id, t, src, score, verified, note, provenance}`。
        `provenance` 只有 KB hit 的 `payload` 裡有，法條查表的 hit 沒有 → `None`
        （spec §4.2 要求前端處理 `null`，所以這裡明確填 `None` 而不是省略這個鍵）。
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
        }
        self._by_id[cid] = entry
        return entry

    def add_all(self, hits: list[Any]) -> list[dict[str, Any]]:
        return [self.add(h) for h in hits]

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
            return _verdict("y", "retrieval", WHY_SOURCED.format(n=len(refs)), refs=refs)

    # 規則 4：其餘（refs 空、或有 refs 但回答一個都沒引）。
    return _verdict("r", "llm", WHY_UNSOURCED)



# ── S4：agent 層（五個工具 ＋ Strands 單 agent）────────────────────

#: `tool` 欄位的值域與給 UI 的中文標籤（spec §4.0）。後端帶 `label`，
#: 前端不必自己維護對照表；兩邊必須一致。
TOOL_LABELS = {
    "search_regulations": "查法條",
    "search_similar_decisions": "查相似訴願決定",
    "retrieve_refs": "查判解與函釋",
    "read_case": "讀卷內",
    "refine_text": "潤稿",
}

#: `read_case` 讀得到的分區。值域固定，讓模型不能亂要一個不存在的欄位。
CASE_SECTIONS = ("intake", "facts_excerpt", "screen", "laws", "cases")

_NO_RETRIEVER = "目前沒有可用的檢索來源，這個工具查不了。請直接說明查不到，不要改用推測作答。"


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
                 emit: Any = None) -> None:
        self.case_payload = case_payload or {}
        self.refbook = refbook
        self.retriever = retriever
        self.snapshot = snapshot or {}
        self._emit = emit
        self.refine_used = False
        self.tool_calls: list[dict[str, Any]] = []

    # ── 事件 ────────────────────────────────────────────────────────

    def _call(self, name: str, args: dict[str, Any]) -> None:
        self.tool_calls.append({"tool": name, "args": args})
        if self._emit:
            self._emit("tool_call", {"tool": name, "args": args, "label": TOOL_LABELS[name]})

    def _result(self, name: str, hits: list[dict[str, Any]], note: str = "") -> None:
        if self._emit:
            self._emit("tool_result", {"tool": name, "hits": hits, "note": note})

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
            self._result(name, [], _NO_RETRIEVER)
            return _NO_RETRIEVER
        try:
            hits = retriever.search(query, filters=filters, top_k=5)
        except Exception as e:  # noqa: BLE001 — 檢索失敗要說出來，不得靜默回空當「查無」
            note = f"檢索失敗（{type(e).__name__}）：{e}"
            self._result(name, [], note)
            # 不回「查無結果」——那會讓模型把一次失敗講成「資料庫裡沒有」。
            # 訊息本身也不寫「查無」二字：模型很容易照抄回覆裡出現過的詞。
            return f"{note}。請告訴使用者這次查詢失敗了，不要說成資料庫裡沒有這筆資料。"
        entries = self.refbook.add_all(hits)
        self._result(name, entries, "" if entries else f"{which}查無結果。")
        if not entries:
            return f"{which}查無結果。請直接說查無，不要引用任何編號。"
        lines = []
        for h, entry in zip(hits, entries):
            text = (getattr(h, "payload", None) or {}).get("text", "")
            lines.append(f"[{entry['id']}] {entry['t']}（{entry['src']}）\n{text}".rstrip())
        return "\n\n".join(lines)

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
                            {"prefix": list(REF_PREFIXES)}, "函釋與判解")

    def read_case(self, section: str) -> str:
        """讀呼叫端餵進來的 payload 分區。**不碰 runstore、不呼叫 build_payload。**"""
        _throttle()
        self._call("read_case", {"section": section})
        if section not in CASE_SECTIONS:
            note = f"沒有「{section}」這個分區。可讀的是：{'、'.join(CASE_SECTIONS)}。"
            self._result("read_case", [], note)
            return note
        value = self.case_payload.get(section)
        if value in (None, "", [], {}):
            note = f"卷內的「{section}」是空的。"
            self._result("read_case", [], note)
            return note
        note = f"已讀取卷內「{section}」。"
        self._result("read_case", [], note)
        return json.dumps(value, ensure_ascii=False, indent=1)

    def refine_text(self, text: str, instruction: str = "改寫得更通順") -> str:
        """單獨一次模型呼叫改寫文字。**本回合強制紅燈**（規則 2）。

        改寫出來的句子沒有任何出處可言，系統不替它背書——所以旗標一旦立起來就不放下，
        即使同回合也檢索到了 refs。
        """
        _throttle()
        self.refine_used = True
        self._call("refine_text", {"instruction": instruction})
        if Agent is None:
            self._result("refine_text", [], "模型不可用")
            raise LLMError(_STRANDS_MISSING)
        agent = Agent(model=_load_model(model_kind="draft"),
                      system_prompt=_prompt("chat_refine"),
                      tools=[], callback_handler=None)
        out = str(agent(f"指示：{instruction}\n\n原文：\n{text}"))
        self._result("refine_text", [], "已改寫；改寫文字無出處，本則回答標為請人工判斷。")
        return out

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
        def read_case(section: str) -> str:
            """讀本案卷內資料。

            Args:
                section: intake（收文欄位）｜facts_excerpt（事實摘錄）｜
                    screen（程序審查）｜laws（六節點抓到的法條）｜cases（相似案）。
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

        return [search_regulations, search_similar_decisions,
                retrieve_refs, read_case, refine_text]


def build_chat_agent(case_payload: dict[str, Any], refbook: RefBook,
                     retriever: Any = None, snapshot: dict[str, Any] | None = None,
                     emit: Any = None) -> tuple[Any, ChatTools]:
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
    tools = ChatTools(case_payload, refbook, retriever, snapshot, emit)
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
    "build_chat_agent",
    "cited_ids",
    "classify_answer",
    "is_numeric_question",
    "TIER_HUMAN",
]
