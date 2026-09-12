# chat-tools-unified

把七支 agent 工具收斂進單一 `POST /cases/{id}/chat` 串流端點的骨架：真串流、`call_id` 配對、`tool_result.status` 三態、pipeline 工具的 `to_node`／事件轉發、卷內引用重編、斷線續接。

---

## Background

**現況（實查程式碼）：**

| 事實 | 出處 |
|---|---|
| `gen()` 現在把事件 append 進 `pending`，`_run_turn` 跑完才一次 yield；實跑 t=0 emit 的事件要 2.02 秒後才隨整批抵達 | `docs/handoff/2026-09-12-frontend-contract-v2.md:100`（引 `backend/api/chat.py:243-266`） |
| `run_case()` 目前沒有 `to_node`，迴圈是 `for node in NODE_ORDER[start_idx:]`，跑到底 | `docs/handoff/2026-09-12-frontend-contract-v2.md:82`（引 `graph.py:303`） |
| `STATE_AFTER` 已有 node → state 的完整映射，停在 n3 自然落在 `SCREENED` | `docs/handoff/2026-09-12-frontend-contract-v2.md:83`（引 `graph.py:56-63`） |
| `_load_case_payload` 只看 BUS 狀態不看 `final_state`，需一併補「`SCREENED` 的 run 沒有草稿」的判斷 | `docs/handoff/2026-09-12-frontend-contract-v2.md:87`（引 `backend/api/chat.py:104`） |
| N4 每次從 `L1`／`C1` 重編，`add_case_refs` 對既有 id 直接 `continue` | `docs/handoff/2026-09-12-frontend-contract-v2.md:90-91`（引 `n4_retrieval.py:214-217,258-262`、`llm/chat.py:188-189`） |
| `?stream=0` 與 SSE 共用同一條 `_run_turn` 的既有契約 | `docs/handoff/2026-09-12-frontend-contract-v2.md:97` |
| `backend.llm.chat` 不得 import `backend.orchestrator.*`（乙案容器裡沒有 runstore），改用注入 callable | `docs/handoff/2026-09-12-frontend-contract-v2.md:103-106` |

**契約要求：**

- 前端的每次對話動作只打這一支端點，七支工具全部走這裡，前端不直接呼叫 `/runs`、`/extract`、`/draft`（§0.1）。
- 工具值域固定七支，`tool` 欄位照 §3.0 表格逐字對應，`label` 由後端帶出。
- SSE 事件七種（`ack`／`tool_call`／`tool_step`／`tool_result`／`token`／`done`／`error`），新增 `call_id`（配對鍵）、`tool_result.status`（`ok|empty|failed`）、`ack.session_id`（提前送出）（§2.3）。
- 解析卷證與生成草稿兩支工具背後是六節點 pipeline，不另開端點，由 chat 工具層在同一回合內觸發並把節點事件轉發成 `tool_step`（§0.1）。

---

## User Stories

### US-A1: 一個對話框辦完一件案 [P0]

**作為**陳專員，**我要**在同一個對話框裡完成解析卷證、查法規、查案例、生成草稿、潤稿，**才能**不用在幾個分頁之間切換，也不用記得哪個功能在哪裡。

- **A1.1** Given 一個剛上傳卷證的案子、When 送出「幫我解析卷證」、Then 後端發出 `tool_call{tool:"extract_case_document"}`，且該回合結束時 case 的 `latest_run_id` 有值、`state == "SCREENED"`。
- **A1.2** Given 已解析的案子、When 送出「生成草稿」、Then 後端以 `from_node="n4"` ＋ 上一次的 `base_run_id` 續跑，終態 `VERIFIED`。**不得重跑 n1–n3。**
- **A1.3** 七支工具的 `tool` 值域與契約 §3.0 那張表**逐字相符**，`label` 由後端帶出，前端零對照表。
- **A1.4** Given `RUN_MODE != bedrock`、When 送出任何訊息、Then **開串流之前**回 503 JSON，**不得**半開一條串流再道歉。
- **A1.5** `to_node` 傳 `"n5"` 時必須被拒絕（會留下沒過 N6 守門的草稿）。

**驗**：實跑 chat 端點並讀 SSE；`pytest` 釘住 A1.3 的值域與 A1.5 的拒絕

### US-A2: 串流是真的逐筆送 [P0]

**作為**陳專員，**我要**在系統跑那 10 到 72 秒的時候看得到它跑到哪一步，**才能**知道它是在工作而不是當掉了。

- **A2.1** Given 一個會呼叫工具的回合、When 在 t=0 emit 一個事件、Then client 在 **1 秒內**收到它。**目前實測是 2.02 秒後才隨整批一起到——這條現在是紅的。**
- **A2.2** Given 解析卷證、Then 收到 `tool_step` ×3（n1–n3，各一組 running/done）；生成草稿則是 n4–n6 ×3。`elapsed_ms` 與 `run_meta.node_timings` **數值相同**（不是另外估的）。
  > **2026-09-13 更正**：原本誤寫「×6（n1–n6）」。契約 §3.0 說解析卷證是 `to_node="n3"`，只跑 n1–n3。執行者照契約做是對的（`CONSTITUTION` §9）。
- **A2.3** 其餘五支工具**不發** `tool_step`（沒有內部階段可報，發了就是編）。
- **A2.4** `?stream=0` 與 SSE 走**同一條** `_run_turn`，兩邊的 `done` 欄位集合相同。

**驗**：用 `curl -N` 記每個 event 的抵達時間；比對 `node_timings`

### US-A3: 查無與失敗要分得開 [P0]

**作為**陳專員，**我要**系統講清楚「資料庫裡沒有」跟「這次查詢壞了」，**才不會**把一次連線失敗當成「本案沒有前例」。

- **A3.1** Given 檢索回空結果、Then `tool_result.status == "empty"`。
- **A3.2** Given 把 KB endpoint 改成打不通、When 呼叫查案例、Then `status == "failed"` 且 `note` 含失敗原因，**不得**是 `"empty"`。
- **A3.3** 同一回合呼叫同一支工具兩次、Then 兩組 `tool_call`／`tool_result` 的 `call_id` **不同且可配對**。

**驗**：改 env 讓 KB 打不通實跑一次；`pytest` 釘住 A3.3

### US-A4: 斷線接得回來 [P1]

**作為**陳專員，**我要**賽場網路抖一下之後還能接著問，**才不會**前面講過的話全部重來。

- **A4.1** `ack`（seq 0）就帶 `session_id`，不是只在 `done` 才給。
- **A4.2** Given 收到 `ack` 後立刻斷線、When 用該 `session_id` 再問、Then 後端記得前一輪。
- **A4.3** 開流之後的傳輸失敗回 `error.stage == "transport"`，**不是** `"model"` 也不是 `"internal"`。

**驗**：實際中斷連線再續問

---

## 不做什麼

- 不做 AgentCore Runtime 部署（維持 Stretch）；乙案的做法／開工條件／驗收見 `plans/2026-09-12-agentcore-runtime-chat.md`，本 change 的部署改動為零。
- 不把 `read_case` 開放成使用者可見工具——`tool_hint` 值域只有前七支，`read_case` 由 agent 自己決定要不要用（§3.0）。
- 不決定「`/` 斜線指令選單」的最終互動行為（契約標「還沒定，不擋開工」，待 Pink 回覆）。
- 不動資料夾功能（純前端 localStorage，§0.2）。
- 不做完整的分層誠實燈號判定邏輯（`classify_answer` 等）——那是 `honesty-guardrails` 的範圍，本 change 只負責把 `done` 的既有欄位如實透傳。

---

## 契約對應

| 章節 | 內容 |
|---|---|
| §0.1 | 前端 API 與 agent 工具兩層架構、長工作在 chat 內部怎麼跑（`to_node`、RefBook 重置、真串流三件事） |
| §2.3 | SSE 七種事件、`call_id`／`tool_result.status`／`ack.session_id` 三處新增、`tool_step` 來源改真 |
| §3.0 | 七支工具值域表 |
| §5 | 斷線續接的前端已有行為（A4 對應） |
