# 設計規格：聊天追問的分層誠實燈號與 SSE 契約（2026-09-12）

> 狀態：**契約凍結**。後端（Ci）與前端（Pink）照此各自開工，不再協商。
> **所有 `檔案:行號` 依 HEAD `7724acf`。** `backend/api/app.py` 的工作樹已被另一個 session 改動（約 +21 行），開工前請用**函式名**定位，不要只信行號。
> 計畫見 `plans/2026-09-12-chat-ask-agent.md`；
> 使用者故事與 Edge Cases 見 `.prospec/changes/chat-ask-agent/proposal.md`。
> 本規格**不修改** `CONSTITUTION.md`，只引用它。

## 0. 一句話

聊天的每一則回答都要帶一顆燈，那顆燈由**機械規則**算出來，模型不參與判定自己可不可信。

## 1. 為什麼需要這份規格

現有三層誠實（可驗算／有出處／請人工判斷）是**逐句掛在批次 `doc[]` 上**的，由 N6 守門節點依 `backend/gate/lamps.py` 的規則產出（`backend/nodes/n6_gate.py:143-170`），前端在 `frontend/src/api/adapt.js:21` 把 `g/y/r` 映射成 `ok/warn/bad`。

**聊天沒有 N6。** 模型的話直接進承辦人眼睛。任何回答不標就違反 CONSTITUTION §1（分層誠實）與 §2（引用必可驗）。搜遍 `docs/spec/`、`backlog.md`、`CONSTITUTION.md` 沒有既有規則可套——**這是全新規則**，所以先寫規格再實作。

適用範圍：只管 `POST /api/cases/{case_id}/chat`。六節點的燈號規則一行不動。

## 2. 端點契約

### 2.1 請求

```
POST /api/cases/{case_id}/chat
Content-Type: application/json
```

```jsonc
{
  "run_id": "run-20260912-abc123",   // 必填。必須是一次**已完成**的 run
  "message": "有沒有類似的訴願決定可以參考？",  // 必填，非空字串
  "session_id": "sess-…",            // 選填。省略＝開新對話，後端回一個新的
  "context": {                       // 選填。承辦人在草稿上選了哪一句
    "scope": "sentence",             // "sentence" | "all"，省略視同 "all"
    "sent_id": "s12",                // scope="sentence" 時必填，對應 doc[].ss[].id
    "text": "本件訴願人所訴各節均無理由。"   // 該句原文，後端不回頭查 payload 以免不一致
  }
}
```

`case_id` 取路徑參數，`run_id` 取 body。**兩者必須屬於同一個案子**，否則 400。

> **為什麼要有 `context`**：既有 `frontend/src/components/ChatPanel.vue` 的互動模型就是以**選取句**為單位（`state.selId`／`state.chatAll`／四個改寫 chip，見 `frontend/src/store/workbench.js:66,71`），目前以 `REWRITE_ENABLED = false` 停用（`workbench.js:26`）。把選取句塞進 `message` 字串會讓後端得靠字面猜哪一段是原文。**前端不要自己拼字串，用這個欄位。**
>
> 後端對 `context` 只做一件事：把它放進 user message 的結構化區塊供模型參考。**它不改變燈號規則**——§3 規則 2 只看有沒有用過 `refine_text`，不看 `context`。

### 2.2 成功回應

```
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-store
X-Accel-Buffering: no
```

之後是 §4 的事件序。SSE 格式與既有 `GET /api/runs/{id}/events` 完全相同（`event:` 一行、`data:` 一行 JSON、空行分隔，見 `backend/api/events.py:70-71`）。

**非串流模式（降級用，契約的一部分）**：加 query string `?stream=0`，回

```
HTTP/1.1 200 OK
Content-Type: application/json
```

body 就是 §4.4 那個 `done` 物件**再加一個 `events[]`**，內容是本來會逐筆發出的 `tool_call`／`tool_result`／`token`。沒有中間狀態，等到全部跑完才回（約 5–15 秒）。

這條存在的理由：賽場網路讓 SSE 斷流時，前端偵測到連續兩次收不到 `done` 就切過來，**而不是**在本地用 CSS 演一段沒發生的串流。Pink 要為這條寫一個分支。

### 2.3 錯誤回應（**全部是 JSON，不是 SSE**）

| 狀態 | 何時 | body 重點欄位 |
|---|---|---|
| 400 | `message` 空、或 `run_id` 屬於別的 `case_id` | `detail` |
| 404 | `run_id` 不存在 | `detail` |
| 409 | 該 run 還在跑 | `{"run_id", "status": "running"}`，沿用 `GET /api/runs/{id}` 的語義 |
| 502 | 該 run 是失敗的 | `{"run_id", "status": "failed", "node", "error"}` |
| **503** | `RUN_MODE != "bedrock"`，或 `settings.missing_live_settings()` 非空 | `{"detail", "run_mode", "missing": [...], "why"}` |

**503 的行為是紅線**：閘門在開串流**之前**。半開一條串流再道歉，前端會先渲染出一個空白對話泡，那就是在演一段沒發生的對話。前端據此可以安全地假設「拿到 `text/event-stream` 就一定至少有一個 `done` 或 `error`」。

> **為什麼是 503 而不是 501（tech-lead 拍板，已凍結）**：端點本身已實作，不可用的是執行檔位。503 表達「設好環境變數就會有」，501 表達「這台伺服器永遠不做這件事」。與 `_translate` 把 `NotImplementedError` 對到 501（`backend/api/app.py:322`，依 `7724acf`）不同，是刻意的。前端照 503 寫分支即可，不必兩種都處理。Ci 若要改回 501，**開工前說**；開工後不改。

## 3. 燈號規則（機械判定，零 LLM 自評）

判定發生在模型回答**之後**，由 `backend/llm/chat.py` 的純函式 `classify_answer()` 執行。輸入只有四樣：使用者的問題原文、模型的回答原文、本回合工具命中的 ref 編號集合、本回合是否用過 `refine_text`。**沒有任何一步問模型「你這句可不可信」。**

四條規則**依序**判，第一個命中即回：

| # | 條件 | `lamp` | `tier` | `origin` | 附帶 |
|---|---|---|---|---|---|
| 1 | **問題**是數字類（期限／天數／金額） | `r` | 請人工判斷 | `human_required` | `redirect` 指向 `/api/deadline`；回答不得含天數 |
| 2 | 本回合用過 `refine_text` | `r` | 請人工判斷 | `llm` | 即使同回合也檢索到 refs |
| 3 | `refs[]` 非空 **且** 回答文字含至少一個 ref 標號 | `y` | 有出處 | `retrieval` | `refs[]` 列出被引到的那幾筆 |
| 4 | 其餘（含 refs 空、或有 refs 但回答一個都沒引） | `r` | 請人工判斷 | `llm` | — |

### 3.0 `tier` 怎麼算——**不要用 `origin_registry.tier_of()`**

上表的 `tier` 欄位是**規則的一部分**，不是從 `origin` 查表查出來的。既有 repo 有兩支長得很像的函式，在 `origin="llm"` 這格**答案相反**：

| 函式 | `tier_of("llm")` 這類單看 origin 的 | 結果 |
|---|---|---|
| `backend/config/origin_registry.py:154` `tier_of(origin)` | 查 `ORIGIN_TO_TIER`，其中 `"llm": TIER_SOURCED` | **「有出處」** ← 對聊天是錯的 |
| `backend/gate/lamps.py:72` `tier_for_lamp(lamp, origin)` | `lamp == "r"` 先回 `TIER_HUMAN` | **「請人工判斷」** ← 對 |

`ORIGIN_TO_TIER["llm"] = TIER_SOURCED` 之所以成立，是因為在六節點路徑上模型句子**必定**經過 N6 驗引用才出場（`origin_registry.py:42` 的註解就是這樣寫的）。**聊天沒有 N6**，這個前提不成立。

所以：**`tier` 一律用 `backend/gate/lamps.py:72` 的 `tier_for_lamp(lamp, origin)` 算**，`lamp` 先由上表四條規則決定。`classify_answer()` 的測試要釘住這件事——直接斷言規則 4 的輸出是「請人工判斷」，實作換成 `tier_of()` 會紅。

### 3.1 `lamp == "g"` 永不出現在聊天

`g`（可驗算／字號全部已驗）在既有語義裡代表兩件事之一：規則引擎算出來的，或引用**全部對回資料集**（`backend/gate/lamps.py:61-69`）。聊天兩者都不成立：

- 聊天不執行規則引擎（規則 1 把數字題導走）。
- KB 命中**沒有**對資料集實檔驗證——`Hit.verified` 預設 `False`，相似案卡現行文案就是「KB 命中，未對資料集實檔驗證」（`backend/retrieval/base.py:16-26`）。

所以聊天的「有出處」對應 `y`（有出處、需覆核），前端既有映射把它畫成 warn 色。**後端必須在測試裡斷言 `g` 在任何輸入下都不出現**，前端不必為 `g` 寫分支。

### 3.2 數字類問題怎麼偵測

對**使用者的問題**做字面比對（不是對回答，也不是問模型）。命中任一關鍵詞即算數字類：

```
期限 期間 幾天 天數 多久 到期 起算 逾期 過期 屆滿 幾號 日期
金額 罰鍰 罰款 多少錢 幾元
```

外加一條：問題同時含阿拉伯數字或中文數字，且含「天／日／月／年／元」量詞。

**寧可誤判成紅燈，不可漏判。** 誤判的代價是一則答案被多標了一次「請人工判斷」加一個試算連結；漏判的代價是 LLM 自己算了一個期限並被承辦人採用——那是 CONSTITUTION §4 的紅線。

偵測到之後，system prompt 也會擋（模型被明令不得計算期間），但**規則層不依賴 prompt 有效**：即使模型講了天數，`lamp` 仍是 `r` 且帶 `redirect`。

### 3.3 ref 編號與白名單

工具每次回傳命中時，聊天層用 `RefBook` 配一個**本回合單調遞增**的編號 `c1`、`c2`、…，並把它放進 `tool_result.hits[].id`。

**為什麼不直接用 KB 給的編號**：`KBRetriever.search` 每次呼叫都從 `kb-1` 重新編號（`backend/retrieval/kb.py:123`），同一回合呼叫兩次就會撞號，`refs[]` 會指錯來源。這是實際會發生的碰撞，不是防禦性設計。

模型在回答裡用 `[c1]` 這種標號引用。判定時：

- 回答裡出現、且在白名單內的 → 進 `refs[]`。
- 回答裡出現、但**不在**白名單的 → 進 `dropped_refs[]`，該回合**強制 `lamp="r"`**（比照 `backend/llm/client.py:318-321` 對 N5 的既有做法）。
- 白名單內但回答沒引到的 → 不進 `refs[]`（規則 3 因此不成立，落到規則 4）。

## 4. SSE 事件 schema

五種事件。`seq` 是本回合的事件序號，從 0 起單調遞增，前端可用它偵測漏收。`turn_id` 在一回合內固定。

### 4.0 工具清單（`tool` 欄位的值域）

固定五個，前端可以照這張表寫死 UI 文案；`label` 後端也會帶，兩者一致。

| `tool` | `label` | 產不產生引用 | `verified` | 後端實作 |
|---|---|---|---|---|
| `search_regulations` | 查法條 | 是（但**沒有條文原文**） | **原樣帶出 `Hit.verified`，可 true 可 false** | `backend/retrieval/lawtable.py` 查表 |
| `search_similar_decisions` | 查相似訴願決定 | 是 | 恆 `false` | `KBRetriever.search()` 配額查詢 |
| `retrieve_refs` | 查判解與函釋 | 是 | 恆 `false` | `KBRetriever.search(filters={"prefix": REF_PREFIXES})` |
| `read_case` | 讀卷內 | 否（`hits: []`） | — | **唯讀呼叫端餵進來的 payload 分區**，見下方「payload 由呼叫端提供」 |
| `refine_text` | 潤稿 | 否（`hits: []`） | — | 單獨一次模型呼叫；**本回合強制紅燈** |

> **`search_regulations` 的 `verified` 不是恆 true。** `LawTableRetriever.search()` 會造四種 Hit，只有「條號存在於快照」那一種是 `verified=True`；條號寫法無法無歧義解析（`backend/retrieval/lawtable.py:51-64`）、法規不在快照涵蓋範圍內（`:65-78`）、條號在快照裡查無（`:80-96` 的 `verified=in_lib`）**三種都是 `False`**。
>
> 後端**原樣帶出**，不得自作主張填 true；前端**不得**把 `verified:false` 的法條 hit 畫成「字號已驗」。`lawtable.py:52` 的註解就是為了防這個形狀：猜一個條號去查表，猜錯就變成「誤讀→綠燈」。

#### 契約：payload 由呼叫端提供（`backend/llm/chat.py` 不碰 orchestrator）

`read_case` **不得**自己去 `build_payload()` 或 `load_run()` 取資料。`build_payload()` 在 `backend/orchestrator/graph.py:592`，而 `graph.py:45` 就是 `from backend.nodes import n1_extract, n2_classify, n3_procedure, n4_retrieval, n5_draft, n6_gate`——聊天層只要 import 它，就把 orchestrator 與六個節點整包拉進 import 圖，正是本節下一段禁止的那件事**繞道發生**。

**沒有任何機檢抓得到這個。** 紅線第 4 條（`scan_llm_import_graph`）只從 N2／N3／N4／N6 起走；第 3 條（`scan_core_path_dependencies`）對 `backend/llm/` 具名豁免；`AC2` 因為有 `try/except ImportError` 守衛也不會紅。所以它是規格層的紅線，不是掃描層的。

所以：

- **HTTP 層（`backend/api/chat.py`）負責取資料**：`load_run(run_id)` → `build_payload(state)` → 切出 agent 需要的分區。
- **agent 層（`backend/llm/chat.py`）只收參數**：`build_chat_agent(case_payload, refbook, retriever, snapshot)` 的 `case_payload` 是一個**普通 dict**，`read_case(section)` 從它取值。
- `backend/llm/chat.py` 的 import 清單裡**不得出現** `backend.orchestrator.*` 與 `backend.nodes.*`。

這同時是乙案（AgentCore Runtime）的前提：Runtime 容器裡沒有 runstore，payload 只能由呼叫端隨 request 帶進去。**「payload 由呼叫端提供」對甲乙兩案都是契約，不是甲案的實作細節。**

> 已知殘留風險（**不處理**，記在這裡以免日後被當成新發現）：`save_run()` 用 `p.write_text`（`backend/orchestrator/runstore.py:36`）不是原子寫，理論上存在「run 剛完成、寫到一半被讀到截斷 JSON」的窗口。409 閘門擋掉絕大部分——聊天只接受已完成的 run。

`REF_PREFIXES` 目前定義在 `backend/nodes/n5_draft.py:44`。**`backend/llm/chat.py` 不得 import `backend.nodes.*`**（`n5_draft.py` 自己 import `backend.llm.client`，會造成層級倒置，也會把 orchestrator 整包拉進純函式測試的 import 圖）。實作時把這個常數上移到 `backend/retrieval/kb.py`，兩邊都從那裡 import；**不要複製字面值**。

### 4.1 `tool_call`

模型決定呼叫某個工具時發。

```jsonc
// event: tool_call
{
  "seq": 0,
  "turn_id": "turn-…",
  "tool": "search_similar_decisions",   // 五個之一，見 §4.0
  "args": { "query": "廢棄物清理法 裁處 訴願" },  // 原樣帶出，供 UI 顯示「正在查…」
  "label": "查相似訴願決定"              // 給 UI 直接顯示的中文，前端不必自己維護對照表
}
```

### 4.2 `tool_result`

工具回來時發。**`hits[]` 就是引用白名單的來源**。

```jsonc
// event: tool_result
{
  "seq": 1,
  "turn_id": "turn-…",
  "tool": "search_similar_decisions",
  "hits": [
    {
      "id": "c1",                    // RefBook 配的，前端就顯示這個
      "t": "新北市政府 112 年訴字第 123 號",   // 標題
      "src": "歷史訴願決定書/…",      // 來源路徑
      "score": 0.79,
      "verified": false,             // KB 命中未對資料集實檔驗證
      "note": "KB 命中，未對資料集實檔驗證",
      "provenance": "official"       // official | public_crawl | unknown
    }
  ],
  "note": ""                          // 工具層的說明；查無時這裡有話，hits 為 []
}
```

- `hits` 可以是空陣列。空陣列**不是錯誤**，是「查無」。
- `search_regulations` 的 `hits[]` 形狀相同，`verified` 依 §4.0 的說明**原樣帶出**（可 true 可 false），`note` 會註明**沒有條文原文**（`backend/retrieval/lawtable.py:15`，`ARTICLE_TEXT_AVAILABLE = False`）。
- `refine_text` 與 `read_case` 不產生引用：`hits` 恆為 `[]`，內容放 `note`。
- **`provenance` 是選填。** `Hit.as_dict()` 沒有這個鍵（`backend/retrieval/base.py:30-40`）；它只存在於 KB hit 的 `payload["provenance"]`（`backend/retrieval/kb.py:204` 附近），由聊天層攤平出來。法條查表的 Hit 的 `payload` 是 `{law, article, in_snapshot_law}`，**沒有 provenance** → 該欄位為 `null`。前端要處理 `null`，不要假設一定有值。

### 4.3 `token`

模型逐段輸出。

```jsonc
// event: token
{ "seq": 2, "turn_id": "turn-…", "text": "依卷內資料，" }
```

前端把 `text` 直接 append。**在收到 `done` 之前不得顯示任何燈號**——這段時間系統還不知道這則回答算哪一層。

### 4.4 `done`

一回合結束。**這是唯一帶燈號的事件。**

```jsonc
// event: done
{
  "seq": 17,
  "turn_id": "turn-…",
  "session_id": "sess-…",        // 下次追問帶回來；memory="off" 時為 null
  "answer": "依卷內資料，…[c1]…",  // 完整回答（與所有 token 串起來相同，前端可用它校正）
  "lamp": "y",                   // "y" | "r"，永遠不會是 "g"
  "tier": "有出處",               // "有出處" | "請人工判斷"
  "origin": "retrieval",         // "retrieval" | "llm" | "human_required"
  "why": "本則回答引用了 1 筆檢索命中；命中為 KB 向量相似度結果，未對資料集實檔驗證，請覆核後採用。",
  "refs": [
    { "id": "c1", "t": "新北市政府 112 年訴字第 123 號", "src": "歷史訴願決定書/…",
      "verified": false, "note": "KB 命中，未對資料集實檔驗證", "provenance": "official" }
  ],
  "dropped_refs": [],            // 模型引了但不在白名單的編號，字串陣列
  "redirect": null,              // 見 §4.5
  "refine_used": false,
  "memory": "on",                // "on" | "off"（降級成單輪時是 "off"）
  "session_truncated": false,    // 歷史被截斷過
  "model_id": "…",               // 沿用 run_meta.model_ids.draft 的同一顆
  "elapsed_ms": 4213
}
```

### 4.5 `redirect`（只在規則 1 命中時非 null）

```jsonc
"redirect": {
  "endpoint": "/api/deadline",
  "method": "POST",
  "body_schema": {                    // 既有契約，欄位名不改（backend/api/app.py:173-180）
    "method": "personal|deposit|public",   // 送達方式
    "service": "YYYY-MM-DD",               // 送達日
    "filing": "YYYY-MM-DD|null",           // 提起日
    "transit": 0,                          // 在途期間日數
    "interested": false                    // 是否利害關係人
  },
  "reason": "期間計算由規則引擎負責，同輸入必同輸出、可逐步覆核；聊天不計算期限（CONSTITUTION §4）。",
  "cta": "查看程序審查的算式"
}
```

前端收到就在該則回答下方放一顆按鈕，按下去**捲到左欄的程序審查卡**（見下方拍板）。**不要把 agent 講的任何天數顯示出來。**

> `endpoint` 與 `body_schema` 是**出處標示**，不是要前端去打那支 API：它們說明「這個數字該由誰算」。這一版的動作是捲動，不是呼叫。`AC7` 釘的也只是 `endpoint` 的值。

> ⚠️ **期間試算面板目前不存在。** 實查 `frontend/src/`：沒有任何程式呼叫 `POST /api/deadline`；`ProcPanel.vue` 只是渲染 run payload 裡的 `screen.deadline`，不是互動試算。所以這顆 CTA 若沒有對應面板就是一顆死鈕。
>
> **已拍板（tech lead，2026-09-12）：CTA 改成「捲到左欄的程序審查卡」**，那裡已經有規則引擎算好的期限與算式，零新工、現在就會動。做互動試算另開工作包，本 change 不含。
>
> ⚠️ **「那顆鈕真的會捲得到」沒有後端驗收涵蓋。** AC7 只驗 `done.redirect.endpoint` 的值，它驗不到前端行為。**這條屬於前端驗收，需要 Pink 補一條**（例：點 CTA 後程序審查卡進入視窗且被 highlight）。沒有人補就是沒人驗——不要因為 AC7 綠了就以為這顆鈕通了。

### 4.6 `error`

任何一步失敗時發，發完關流。

```jsonc
// event: error
{ "seq": 9, "turn_id": "turn-…", "stage": "model", "error": "LLMError: 模型呼叫失敗（重試 3 次）：…" }
```

`stage` ∈ `{"tool", "model", "transport", "internal"}`，四個值：

| `stage` | 什麼壞了 | 前端該說什麼 |
|---|---|---|
| `tool` | 某個檢索工具失敗（KB 打不到、快照載不動） | 「查詢來源失敗」，可重問 |
| `model` | 模型呼叫失敗（重試耗盡、throttle、schema 不符） | 「模型沒有回應」，可重問 |
| `transport` | 開流之後的傳輸／託管層失敗（ALB 斷線、乙案的 proxy 跳失敗） | 「連線中斷」，可重問；**不要說成模型錯誤** |
| `internal` | 後端自己的邏輯錯誤 | 照實顯示 `error` 原文，這是 bug |

> **`transport` 是 2026-09-12 新增的契約變更，Pink 要處理。** 原本只有三個值，開流之後的傳輸失敗只能硬塞 `internal`，畫面上就會把「網路斷了」說成「系統內部錯誤」。乙案（AgentCore Runtime）多一跳 proxy 之後這類失敗會變多，所以現在就把它分出來。

> `error` 不在最初的四事件清單裡，是本規格新增的。理由：失敗必須有一個**與 `done` 不同**的形狀。用 `done` 包一句道歉，前端會把它當一則正常回答並標燈——那正是 CONSTITUTION §1 要防的。比照既有 `run_failed` 與 `run_done` 分開的做法（`backend/api/events.py:50-58`）。

### 4.7 事件序保證

- `done` 與 `error` **互斥**，一回合只會有其中一個，且一定是最後一個事件。
- `tool_call` 與 `tool_result` 成對出現，同一 `tool` 可以出現多次。
- `token` 可以是零筆（模型直接用工具結果作結時）。
- 事件只活在這個 process 的記憶體裡，**沒有回放**。斷線就是斷線，重問即可。

## 5. 前端呈現要求（Pink 的驗收面）

1. 每則 AI 回覆底部一顆燈 ＋ 一句話：
   - `y`：「有出處 · 依據 N 筆卷內／檢索來源」＋ 可展開的 `refs[]` 清單（每筆顯示 `t`、`provenance` 標籤、`note`）。
   - `r`：「請人工判斷 · 模型生成，系統未替其背書」。
2. **`refs[]` 裡 `verified: false` 的必須看得出來**。不得把「KB 命中」畫成「字號已驗」——`verified` 只代表能對回資料集，跟「內容相關」是兩件事（既有 US-8 的同一個坑）。**法條 hit 也可能是 `false`**（見 §4.0），不要因為「法條是查表的」就一律畫成已驗。
   - `provenance` 可能是 `null`（法條 hit 沒有這個欄位），要有 fallback 文案，不要顯示 `undefined`。
3. **收到 `done` 之前不顯示燈號。** 串流中顯示「思考中／查詢中」即可。
4. `dropped_refs` 非空時，明示「模型引用了檢索結果之外的來源，已移除」。不要靜默吞掉。
5. `redirect` 非 null → 顯示 CTA 按鈕，**不顯示**答案裡的任何天數。
6. 收到 `error` → 顯示錯誤原文，**不得**退回任何內建範例對話。
7. 503 → 顯示「目前為離線重播檔位，聊天需要即時模型」，並列出 `missing[]`。**不得**演一段離線對話。
8. 選取句用 `context` 欄位送（§2.1），**不要**自己把原文拼進 `message` 字串。
9. 連續兩次串流拿不到 `done` → 改打 `?stream=0`（§2.2），**不得**在本地用 CSS 演一段串流動畫。

## 6. 給前端的 mock（可直接照抄）

一輪完整的 wire 格式。Pink 可把這段存成 `.txt` 用本地 SSE mock 回放，不必等後端。

```
event: tool_call
data: {"seq":0,"turn_id":"turn-1","tool":"search_similar_decisions","args":{"query":"廢棄物清理法 裁處"},"label":"查相似訴願決定"}

event: tool_result
data: {"seq":1,"turn_id":"turn-1","tool":"search_similar_decisions","hits":[{"id":"c1","t":"新北市政府 112 年訴字第 123 號","src":"歷史訴願決定書/112-123.txt","score":0.79,"verified":false,"note":"KB 命中，未對資料集實檔驗證","provenance":"official"}],"note":""}

event: token
data: {"seq":2,"turn_id":"turn-1","text":"卷內檢索到 1 件性質相近的決定："}

event: token
data: {"seq":3,"turn_id":"turn-1","text":"[c1] 同樣涉及廢棄物清理法的裁處處分。"}

event: done
data: {"seq":4,"turn_id":"turn-1","session_id":"sess-1","answer":"卷內檢索到 1 件性質相近的決定：[c1] 同樣涉及廢棄物清理法的裁處處分。","lamp":"y","tier":"有出處","origin":"retrieval","why":"本則回答引用了 1 筆檢索命中；命中為 KB 向量相似度結果，未對資料集實檔驗證，請覆核後採用。","refs":[{"id":"c1","t":"新北市政府 112 年訴字第 123 號","src":"歷史訴願決定書/112-123.txt","verified":false,"note":"KB 命中，未對資料集實檔驗證","provenance":"official"}],"dropped_refs":[],"redirect":null,"refine_used":false,"memory":"on","session_truncated":false,"model_id":"<draft 模型>","elapsed_ms":4213}
```

數字題那一輪的 `done`（規則 1）：

```
event: done
data: {"seq":1,"turn_id":"turn-2","session_id":"sess-1","answer":"訴願期間的起算與屆滿日由規則引擎計算，我不代算。左欄「程序審查」已有本案的算式與法條依據。","lamp":"r","tier":"請人工判斷","origin":"human_required","why":"期間計算由規則引擎負責，聊天不計算期限。","refs":[],"dropped_refs":[],"redirect":{"endpoint":"/api/deadline","method":"POST","body_schema":{"method":"personal|deposit|public","service":"YYYY-MM-DD","filing":"YYYY-MM-DD|null","transit":0,"interested":false},"reason":"期間計算由規則引擎負責，同輸入必同輸出、可逐步覆核；聊天不計算期限（CONSTITUTION §4）。","cta":"查看程序審查的算式"},"refine_used":false,"memory":"on","session_truncated":false,"model_id":"<draft 模型>","elapsed_ms":1180}
```

失敗那一輪（§4.6）。**注意：沒有 `done`。**

```
event: tool_call
data: {"seq":0,"turn_id":"turn-3","tool":"retrieve_refs","args":{"query":"信賴保護原則"},"label":"查判解與函釋"}

event: error
data: {"seq":1,"turn_id":"turn-3","stage":"model","error":"LLMError: 模型呼叫失敗（重試 3 次）：ThrottlingException: Too many requests"}
```

> mock 裡的案號 `112 年訴字第 123 號` 是**示意用的假字號**，不是資料集內的真實案件。前端 mock 專用，不得出現在任何 demo 畫面或簡報（CONSTITUTION §3）。

## 7. 與既有契約的關係

| 既有 | 本規格 |
|---|---|
| `doc[].ss[].l` 的 `g/y/r` | **同一組值域**，聊天只用 `y`／`r`。前端 `lampClass`（`frontend/src/api/adapt.js:21`）不必改 |
| `origin` 值域 | 只用既有三個：`retrieval`／`llm`／`human_required`（`backend/config/origin_registry.py:35-45`）。**不新增值** |
| 三層標籤字串 | 用既有常數 `TIER_SOURCED`＝「有出處」、`TIER_HUMAN`＝「請人工判斷」 |
| `tier` 的算法 | 用 `backend/gate/lamps.py:72` `tier_for_lamp()`，**不得**用 `origin_registry.py:154` `tier_of()`——兩者在 `origin="llm"` 上刻意不同，理由見 §3.0 |
| `dropped_cite_ids`（N5 路徑，`backend/llm/client.py:321`） | 聊天用的是**另一個名字** `dropped_refs`（§4.4）。兩條串流共用 parser 時記得這是兩個欄位，不是同一個 |
| `check_payload()` | **不涵蓋聊天**。它只掃 `doc[]` 與 `citations[]`（`origin_registry.py:159-183`），聊天回應不是 payload。聊天的等價強制在 `classify_answer()` 的單元測試 |
| `GET /api/runs/{id}/events` | 事件名不重疊（那邊是 `node_start`／`node_done`／`run_done`／`run_failed`／`timeout`）。前端兩條串流可以共用同一個 SSE parser |
| spec `2026-09-07` §2「不做 ask 追問 agent」（`:39`） | **被本規格推翻**，理由見 proposal |
| spec `2026-09-07` D3「AgentCore 維持 Stretch」（`:51`） | **不推翻**。本規格的實作跑在既有 FastAPI 容器內 |

## 8. 未定與待查

1. **503 vs 501**（§2.3）：tech-lead 已拍板 503，契約凍結。Ci 若要改，開工前說。
2. **session 記憶跨容器**：ECS 擴到多台會失憶。目前單台，不處理，列此以免被讀成 bug。
3. **1 RPS 節流：已定做法，但保護範圍有限**（誠實條款，**不要讀成已解決**）：
   實作**一律在每個工具進入點各呼叫一次 `_throttle()`**（tech lead 依 Ci 傾向定；理由是評審面前吃 429 比慢三秒難看得多，而且 429 發生在 demo 中途無法當場除錯）。Ci 若確認賽制不把聊天呼叫算進 1 RPS，再拿掉。
   **保護範圍的誠實說明**：這只保證**單一進程內**不超速。乙案（AgentCore Runtime）上線後聊天跑在另一個容器，與六節點的節流器是兩份獨立狀態，**甲乙並存時全域仍可能超過 1 RPS**。真要嚴格全域限速得把兩邊併進同一個節流器，本 change 不做。
   底層限制（這是為什麼要逐工具補，而不是靠既有的閘）：`backend/llm/client.py:76-79` 的 docstring 白紙黑字寫著，`_throttle()` 只管 `_invoke_structured` 每一次送出的請求，「**Strands 的 agent loop 在一次呼叫內可能因工具往返而多次打模型，那些內部往返不經過這裡**」。聊天 agent 正是 agent loop，一輪可能連打 3–5 次模型。`backend/retrieval/kb.py` 的 `RETRIEVE_INTERVAL_S` 是另一個獨立的閘，兩者相加仍可能超標。
   - 代價：一輪聊天多等約 3–5 秒（**估計，未量測**）。
   - 賽制是否把聊天呼叫一起算進 1 RPS：**待查**，Ci 確認。
4. **數字類關鍵詞清單**（§3.2）是第一版，未經真實提問語料校準。**校準方式**：demo 前用十個口語提問實測，漏判就加詞——只加不減。
5. **`refine_text` 的改寫品質**不在本規格的驗收範圍。它永遠紅燈，系統不替它背書。
6. **`redirect` 的 CTA 指到哪**（§4.5）：**已拍板**捲到左欄程序審查卡。剩下的未決是「誰驗那顆鈕會動」——屬前端驗收，待 Pink 補一條 AC。

以上六條，**沒有一條會改動 wire 契約**（端點、五個事件、欄位名、燈號值域、狀態碼都已定），所以 Pink 可以照 §6 的 mock 立刻開工。
