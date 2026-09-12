# Implementation Plan: chat-tools-unified

## Overview

把七支工具收斂進 `POST /api/cases/{id}/chat` 的地基改造。四塊互相有相依：
**真串流**是 `tool_step` 有意義的前提；**`to_node`** 是 `extract_case_document` 的前提；
**注入 callable** 是不踩層級禁令的唯一做法；**RefBook 重置**是 pipeline 工具跑完之後
誠實層不被靜默打穿的前提。

關鍵設計決策：

1. **`run_pipeline` 注入的是「回傳 plain dict 的 adapter」，不是 `run_case` 本身。**
   契約 §0.1 只說「注入 callable」。若直接注入 `run_case`，`backend/llm/chat.py` 雖然
   沒有 import 語句，卻會拿到一個 `CaseState` 物件並讀它的屬性——**AST 測試綠、層級實質被穿**。
   adapter 由 `backend/api/chat.py`（允許 import orchestrator）做 `CaseState → dict` 的轉換，
   聊天層只看得到 `{run_id, state, node_timings, degraded_nodes, cite_count, sections}`。

2. **`turn_id` 在 `chat()` 產生，往下傳。** 契約 §2.3 說 `turn_id` 是**所有事件**的共通欄位，
   而它現在只出現在 `done`。事件的 `record()` 在 HTTP 層、`turn_id` 原本在 `_run_turn` 裡生成
   ——順序反了。改成 `chat()` 生成後同時餵給 `record()` 與 `_run_turn()`。

3. **真串流用「工作執行緒 ＋ Queue」，不改 `_run_turn` 的形狀。**
   `_run_turn` 仍是同步函式、仍是 SSE 與 `?stream=0` 的唯一產生路徑（spec §2.2 的既有契約）。
   改的只有 `gen()`：把 `_run_turn` 丟到一條 daemon thread，`emit` 推 `queue.Queue`，
   generator 邊 `q.get()` 邊 yield。`?stream=0` 那條完全不動，因此不可能退化。

4. **RefBook 重置只清 `origin == "record"` 的卷內編號，不清 `cN`。**
   `cN` 是本回合聊天檢索配的號，模型可能已經在前文引用過；清掉會讓那些引用變成
   `dropped_refs` → 無辜紅燈。要防的是「舊 run 的 `L1` 卡住新 run 的 `L1`」，
   那些正好就是 `origin == "record"` 那批。

## Affected Modules

| Module | Impact | Changes |
|--------|--------|---------|
| `backend/orchestrator/graph.py` | Medium | `run_case()` 加 `to_node`；`to_node == "n5"` 拒絕；`rerun_nodes` 與 `run_meta` 跟著收斂 |
| `backend/api/chat.py` | High | `gen()` 改真串流；`turn_id` 上移；`_load_case_payload` 回傳 run 資訊；接 `run_pipeline` adapter 與 manifest reader |
| `backend/llm/chat.py` | High | `call_id`／`status`／`ack`；兩支 pipeline 工具；`PIPELINE_NODE_LABELS`；`RefBook.reset_case_refs()` |
| `backend/tests/test_chat.py` | Medium | 新增單元測試（不改既有 AST 層級測試） |
| `backend/tests/test_build_graph.py` | Low | `to_node` 的行為與拒絕 |

## Implementation Steps

1. **`run_case()` 加 `to_node`**
   - 參數 `to_node: str = "n6"`，放在既有 keyword-only 區塊。
   - `to_node not in NODE_ORDER` → `ValueError`；`to_node == "n5"` → `ValueError`（紅線）。
   - `end_idx < start_idx` → `ValueError`（`from_node` 在 `to_node` 之後無意義）。
   - 迴圈改 `NODE_ORDER[start_idx : end_idx + 1]`；`rerun_nodes` 同步收斂，
     `_model_ids_if_live` 因此不會替沒跑的節點虛報 model id。
   - `run_meta` 加 `"to_node"`，讓讀紀錄的人看得出這是一次「部分執行」。

2. **`RefBook.reset_case_refs()`**
   - 移除 `_by_id` 中 `origin == "record"` 的項目，回傳被移除的 id 清單。
   - `self._n` 不動（`cN` 的計數器跟卷內編號是兩個命名空間）。

3. **事件三欄位（`call_id` / `status` / `ack.session_id`）**
   - `ChatTools` 加 `_call_n`；`_call()` 配 `tc-{n}` 並記在 `self._current_call_id`；
     `_result()` 與 `_step()` 沿用同一個值。
   - `_result(..., status=...)`：檢索無來源／例外 → `failed`；`entries` 空 → `empty`；否則 `ok`。
   - `_run_turn` 開頭 `emit("ack", {"text": ..., "session_id": session_id})`，
     `session_id` 因此在 seq 0 就出去（契約 §2.3 ② 的第三項）。

4. **`gen()` 改真串流**
   - `queue.Queue` ＋ daemon thread ＋ sentinel；例外由 worker 收進 `box`，
     generator 在 sentinel 之後才決定要送 `done` 還是 `error`。
   - 既有語義保留：`error` 與 `done` 互斥且為最後一個；失敗時先前的事件照樣送出去。

5. **兩支 pipeline 工具**
   - `ChatTools.__init__` 收 `case_id`、`run_pipeline`、`run_info`、`case_manifest`。
   - `extract_case_document()` → `run_pipeline(to_node="n3")`；成功後更新 `case_payload`、
     重置 RefBook 卷內編號、`tool_result` 帶 `run_id` 與 `state`。
   - `generate_decision_draft()` → 先擋前置條件（§3.5.1 第 2、3 條），
     再 `run_pipeline(from_node="n4", base_run_id=…, overrides={"n4_query": …})`。
   - `on_event` 轉發：`node_start` → `tool_step{status:"running"}`；
     `node_done` → `tool_step{status:"done", elapsed_ms, degraded}`；
     `run_failed` → `tool_step{status:"failed"}`。
   - `run_pipeline is None`（乙案容器）→ `status:"failed"` ＋「此檔位不可用」，不靜默失敗。

6. **HTTP 層接線**
   - `backend/api/chat.py` 定義 `_pipeline_adapter(case_id)`，內部呼叫 `run_case` 並轉成 dict。
   - `_load_case_payload` 改回 `(sections, run_info)`，`run_info` 帶 `final_state` 與 `has_draft`。
   - `run_id` 缺省時不再 400，改成空 payload ＋ `run_info=None`（契約 §2.1 的 `run_id` 選填）。

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| 真串流的 worker thread 與 generator 共用 `record()` 的 `seq` 計數器 | Medium | `done`／`error` 只在 sentinel 之後才 `record()`，那時 worker 已結束，沒有並行寫入 |
| 注入 `run_case` 會讓層級禁令形式上綠、實質被穿 | High | 注入的是回傳 plain dict 的 adapter；AST 測試之外另加一條「工具不得碰 `CaseState` 屬性」的約定（靠 adapter 邊界保證） |
| `to_node` 讓 `final_state` 停在中途，既有讀 run 的程式可能假設一定 `VERIFIED` | Medium | `_load_case_payload` 回 `run_info.has_draft`；`generate_decision_draft` 據此擋前置條件 2 |
| 前置條件 3（要有法規與案例）依賴 `manifest.json`，而那份檔由 Epic B 寫 | High | 本 change 只讀不寫；manifest 不存在＝清單為空＝擋下並回 `failed`。**若 Epic B 未落地，生成草稿會永久被擋**——這是要回報的相依，不是靜默放行 |
| 提案 US-A2.2 寫「解析卷證收到 `tool_step` ×6（n1–n6）」與契約 §3.0（n1–n3）矛盾 | Low | 以契約為準：解析卷證 3 組、生成草稿 3 組。已在回報中標出提案筆誤 |
