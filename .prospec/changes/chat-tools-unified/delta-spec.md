# Delta Spec: chat-tools-unified

> REQ ID 格式：`REQ-{MODULE}-{NUMBER}`

## ADDED

### REQ-GRAPH-010: `run_case()` 支援停在中途的 `to_node`

**Description:**
`run_case()` 加 keyword-only 參數 `to_node`（預設 `"n6"`），迴圈跑
`NODE_ORDER[start_idx : end_idx + 1]`。`STATE_AFTER` 已有完整 node→state 映射，
停在 `n3` 自然落在 `SCREENED`。

**Acceptance Criteria:**
1. `run_case(case_id, to_node="n3")` 終態 `final_state == "SCREENED"`，且 `state.draft` 為空。
2. `run_case(case_id, to_node="n5")` 拋 `ValueError`（紅線：會留下沒過 N6 守門的草稿）。
3. `to_node` 不在 `NODE_ORDER` 內拋 `ValueError`。
4. `to_node` 早於 `from_node` 拋 `ValueError`。
5. `run_meta.node_timings` 只含實際跑過的節點；`run_meta.model_ids` 不替沒跑的節點填值。
6. `run_meta.to_node` 如實記錄。

**Priority:** High

---

### REQ-CHAT-020: SSE 真串流

**Description:**
`gen()` 改成生產者／消費者：`emit` 推 `queue.Queue`，generator 邊跑邊 yield。
`_run_turn` 維持同步函式，仍是 SSE 與 `?stream=0` 的唯一產生路徑。

**Acceptance Criteria:**
1. `curl -N` 實測：t=0 emit 的事件在 1 秒內抵達 client（現況 2.02 秒）。
2. `?stream=0` 的 `done` 欄位集合與 SSE 的 `done` 完全相同。
3. 失敗時先前已 emit 的事件照樣送出，最後一個是 `error`，沒有 `done`。
4. `done` 與 `error` 互斥且為最後一個事件。

**Priority:** High

---

### REQ-CHAT-021: 事件三處新增（`call_id`／`tool_result.status`／`ack.session_id`）

**Description:**
所有事件帶 `turn_id` 與 `seq`；工具類事件帶 `call_id`；`tool_result` 帶
`status ∈ {ok, empty, failed}`；`ack` 在 seq 0 就帶 `session_id`。

**Acceptance Criteria:**
1. 同一回合呼叫同一支工具兩次，兩組 `tool_call`／`tool_result` 的 `call_id` 不同且各自可配對。
2. 檢索回空 → `status == "empty"`；檢索拋例外 → `status == "failed"` 且 `note` 含失敗原因。
3. `ack` 是 seq 0 且帶 `session_id`；該值與 `done.session_id` 相同。
4. 每一個事件都有 `turn_id`，同回合內相同。

**Priority:** High

---

### REQ-CHAT-022: 兩支 pipeline 工具（注入 callable）

**Description:**
`extract_case_document` 與 `generate_decision_draft` 由 `ChatTools` 經注入的
`run_pipeline` callable 觸發，`backend/llm/chat.py` 不 import orchestrator 或六節點。
`run_pipeline` 回傳 plain dict，不回傳 `CaseState`。

**Acceptance Criteria:**
1. `backend/llm/chat.py` 的 AST import 檢查仍綠（既有測試未被改寬）。
2. `extract_case_document` 跑完 `tool_result.state == "SCREENED"`、`run_id` 非空、該 run 沒有草稿。
3. `generate_decision_draft` 續跑後 `state == "VERIFIED"`，且不重跑 n1–n3。
4. `tool_step` 的 `elapsed_ms` 與 `run_meta.node_timings[node]` 數值相同。
5. `run_pipeline is None` 時回 `status:"failed"` ＋「此檔位不可用」，不靜默失敗。
6. 其餘五支工具不發 `tool_step`。

**Priority:** High

---

### REQ-CHAT-023: pipeline 工具跑完重置 RefBook 卷內編號

**Description:**
`RefBook.reset_case_refs()` 移除 `origin == "record"` 的卷內編號（`L*`／`C*`），
保留本回合聊天檢索配的 `cN`。pipeline 工具回傳之後、模型組答案之前呼叫，
並同步更新 `case_payload`。

**Acceptance Criteria:**
1. 同一回合先 `read_case("laws")` 再跑 pipeline，再次 `read_case("laws")` 時
   `refbook.get("L1")` 是**新 run** 的法條，不是舊 run 的。
2. `reset_case_refs()` 不影響 `cN` 的白名單與計數器。
3. pipeline 跑完 `case_payload` 指向新 run 的分區。

**Priority:** High

---

## MODIFIED

### REQ-CHAT-001（既有）: `run_id` 從必填改成選填

**Description:**
契約 §2.1 ③ 修訂。沒有 `run_id` 時 `case_payload` 為空、`read_case` 回「卷內是空的」，
agent 只能先呼叫 `extract_case_document`。

**Acceptance Criteria:**
1. body 不帶 `run_id` 時不回 400，串流照開。
2. 此時 `read_case` 任一分區回「卷內的『X』是空的」，不報錯。

**Priority:** High

---

## REMOVED

_No removals in this change._
