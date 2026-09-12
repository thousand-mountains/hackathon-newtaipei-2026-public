# Tasks: chat-tools-unified

依相依順序。每個任務跑完即 commit。

- [ ] **T1 `run_case()` 加 `to_node`**（REQ-GRAPH-010）
  - `backend/orchestrator/graph.py`：參數、四條驗證、迴圈上界、`rerun_nodes`、`run_meta.to_node`
  - 測試：`backend/tests/test_build_graph.py` — 停 n3 落 SCREENED 且無草稿、`n5` 被拒、越界被拒
- [ ] **T2 `RefBook.reset_case_refs()`**（REQ-CHAT-023 之 2）
  - `backend/llm/chat.py`：只清 `origin == "record"`，`_n` 不動
  - 測試：`cN` 白名單與計數器不受影響
- [ ] **T3 事件三欄位 ＋ `turn_id` 上移**（REQ-CHAT-021）
  - `backend/llm/chat.py`：`_call_n`／`_current_call_id`／`_result(status=)`
  - `backend/api/chat.py`：`turn_id` 在 `chat()` 生成、`record()` 帶上、`ack` 事件
  - 測試：`call_id` 配對、三態 status、`ack` 是 seq 0 且帶 `session_id`
- [ ] **T4 `gen()` 改真串流**（REQ-CHAT-020）
  - `backend/api/chat.py`：Queue ＋ daemon thread ＋ sentinel
  - 測試：單元（假 `_run_turn` 慢 emit，驗事件先於結束抵達）＋ `curl -N` 實測時間
- [ ] **T5 `_load_case_payload` 回 `run_info`；`run_id` 選填**（REQ-CHAT-001）
  - `backend/api/chat.py`
  - 測試：SCREENED 的 run → `has_draft == False`；無 `run_id` → 不 400
- [ ] **T6 兩支 pipeline 工具 ＋ `on_event` 轉發**（REQ-CHAT-022）
  - `backend/llm/chat.py`：`PIPELINE_NODE_LABELS`、兩支工具、`_step()`、`as_strands_tools` 擴充
  - `backend/api/chat.py`：`_pipeline_adapter`、manifest reader、注入
  - 測試：假 `run_pipeline` 驗 `tool_step` 形狀與 `elapsed_ms` 一致、`run_pipeline is None` 回 failed
- [ ] **T7 RefBook 重置接進 pipeline 工具**（REQ-CHAT-023 之 1、3）
  - 測試：同回合 `read_case` → pipeline → `read_case`，`L1` 換成新 run 的
- [ ] **T8 全量驗收**
  - `pytest` 全綠（含既有 AST 層級測試未被改寬）
  - `curl -N` 記錄抵達時間；`?stream=0` 與 SSE 的 `done` 欄位集合比對
