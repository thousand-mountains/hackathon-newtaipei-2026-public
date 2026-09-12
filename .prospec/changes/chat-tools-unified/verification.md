# 驗收證據：chat-tools-unified

> 產製日 2026-09-12。**測試入口是 `python3 backend/tests/run_all.py`，不是 `pytest`**
> ——這個 repo 的測試路徑刻意零外部依賴（`backend/requirements.txt` 開頭明寫），
> 機器上也沒有 pytest。`run_all.py` 就是這裡的「全綠」。

## 1. 全量測試

```
$ /opt/homebrew/bin/python3 backend/tests/run_all.py
已執行的全綠：507/507 通過　⚠️ 另有 2 項略過、未執行
```

基準：起點 474/474。

略過的 2 項是既有狀態，與本 change 無關：
`test_party_standing` 的兩條需要 `backend/data/local/party-facts-77-3.jsonl`（含未遮罩個資、不在 repo）。

**層級禁令測試是綠的，而且沒有被改寬**：

```
  ok    backend.tests.test_chat.test_chat_module_never_imports_orchestrator_or_nodes
  ok    backend.tests.test_chat.test_chat_module_does_not_import_fastapi
  ok    N2/N3/N4/N6 無 LLM 依賴（ast 遞迴，含 strands）
```

`git diff c9435b3 -- backend/tests/test_chat.py` 全檔只有 **2 行刪除**，兩行都在
`_tools()` 這個測試輔助函式的簽章上（加注入參數），AST 那兩條一個字都沒動。

## 2. SSE 逐筆抵達（實測，含對照組）

量的是**串流管線本身**：`_run_turn` 換成一個在 t=0 發兩則、然後每秒發一則的假回合，
`_live_gate` 停掉。真回合走的是同一條 `emit → queue → yield`，差別只在誰呼叫 `emit`。
探針：`stream_probe.py`／`legacy_probe.py`（scratchpad，不進 repo）。

**對照組＝改動前的 `gen()`**（`git show c9435b3:backend/api/chat.py`），同一支探針、同一個假回合：

```
=== 改動前 gen()（port 8098）===
   2.993s  event: ack
   2.993s  event: tool_call
   2.993s  event: tool_step
   2.993s  event: tool_step
   2.994s  event: tool_step
   2.994s  event: done
```

**改動後**：

```
=== 改動後 gen()（port 8099）===
   0.000s  event: ack
   0.000s  event: tool_call
   0.999s  event: tool_step
   2.002s  event: tool_step
   3.007s  event: tool_step
   3.008s  event: done
```

t=0 emit 的 `ack` 與 `tool_call` **在 0.000 秒抵達**（驗收要求 1 秒內）；
三則 `tool_step` 分別在 0.999／2.002／3.007 抵達，對應各自 emit 的時點。
改動前六則全部擠在 2.993s——整批送。

### 中途失敗

```
   0.000s  event: ack
   0.000s  event: tool_call
   0.988s  event: tool_step
   1.993s  event: tool_step
   2.998s  event: tool_step
   2.998s  event: error
   2.999s  data: {"seq": 5, "turn_id": "turn-61928af7d169", "stage": "transport",
                  "error": "ConnectionError: KB 連線中斷"}
```

先前的事件照樣送出；最後一個是 `error`、**沒有 `done`**；`stage` 是 `transport`
（提案 US-A4.3）。`error` 現在也帶 `turn_id`（原版明著填 `None`）。

## 3. `?stream=0` 沒有退化

```
一次性頂層鍵：      ['answer', 'elapsed_ms', 'seq', 'session_id', 'turn_id']
SSE done 鍵：       ['answer', 'elapsed_ms', 'seq', 'session_id', 'turn_id']
→ done 欄位集合相同：True
events 的事件序：   ['ack', 'tool_call', 'tool_step', 'tool_step', 'tool_step']
SSE 事件序：        ['ack', 'tool_call', 'tool_step', 'tool_step', 'tool_step', 'done']
→ 事件序相同（不含 done）：True
```

（欄位集合來自假回合，所以少了燈號那組。兩條路徑共用同一個 `_run_turn`，
這件事由既有的 `test_both_streaming_and_oneshot_share_one_turn_function` 釘住。）

## 4. 未驗（留紅，寫明原因）

| 項目 | 為什麼驗不了 |
|---|---|
| 真 Bedrock 上的端到端聊天（含真模型決定呼叫哪支工具） | 這台機器沒有 fastapi／strands／boto3 的執行環境，也沒有跑過 live 檔位。**沒有拿 fixture 的結果冒充 live** |
| 解析卷證實際耗時 10–11 秒、生成草稿 24–72 秒 | 同上，需要真模型 |
| ~~`generate_decision_draft` 走到底（含真 manifest）~~ | **已補驗，不再留紅**（見 §8） |

## 5. 補記：adapter 從 `api/chat.py` 搬到 `backend/orchestrator/chat_bridge.py`

原本 adapter 寫在 `backend/api/chat.py` 裡，而那個檔頂層 import fastapi——
於是 `CaseState` → dict 這段轉換**一行都跑不到**（測試路徑零外部依賴）。
它正是 Epic C 要接的東西，搬到橋那一側之後由 `test_e2e.py` 用真的 `run_case`
（fixture 檔位）跑過：解析卷證 → `SCREENED` 無草稿 → 續跑 → `VERIFIED`
且 `node_timings` 只有 n4/n5/n6。

## 6. 提案 15 條 AC 逐條

| AC | 狀態 | 證據／原因 |
|---|---|---|
| A1.1 發 `tool_call{extract_case_document}`、`state=="SCREENED"` | ✅ | `test_extract_runs_only_to_n3_and_reports_that_this_run_has_no_draft`、`test_the_bridge_reproduces_the_two_chat_tools_end_to_end` |
| A1.1 的 `latest_run_id` 有值 | ❌ **留紅** | `latest_run_id` 是 `manifest.json` 的欄位，寫入屬於案件資源層。本 change **只讀不寫** manifest——聊天層也去寫會跟資源層的 read-modify-write 互相蓋掉（契約 §4.0 明寫增刪是 RMW）。誰寫要拍板。功能上前端可先用 `tool_result.run_id`（契約 §3.3 本來就要前端記下來） |
| A1.2 `from_node="n4"`＋`base_run_id` 續跑到 `VERIFIED`、不重跑 n1–n3 | ✅ | `test_the_bridge_reproduces_the_two_chat_tools_end_to_end`（真 `run_case`，`node_timings` 只有 n4/n5/n6） |
| A1.3 七支工具值域與 §3.0 逐字相符 | ⚠️ **6/7** | `build_relation_graph` 未實作，也刻意不列進 `TOOL_LABELS`（獨立新功能，`plans/2026-09-12-relation-graph.md`）。列一個不存在的工具會讓前端以為它在 |
| A1.4 503 在開串流之前 | ✅ | 既有 `test_the_503_gate_runs_before_the_stream_is_opened` 仍綠（該條同時比對 `_run_turn`，擋得住「搬到 `StreamingResponse` 前一行但排在 stream=0 分支之後」） |
| A1.5 `to_node="n5"` 被拒絕 | ✅ | `test_to_node_n5_is_refused_because_it_would_leave_an_unguarded_draft`、`test_the_bridge_refuses_to_stop_at_n5_just_like_run_case_does` |
| A2.1 t=0 emit 的事件 1 秒內抵達 | ✅ | 實測 **0.000s**（§2 的對照組：改動前 2.993s） |
| A2.2 `tool_step` 筆數 | ⚠️ **提案與契約打架** | 提案寫「×6（n1–n6）」，契約 §3.0 寫解析卷證是 **n1–n3**。實作照契約（解析 3 組、生成草稿 3 組）。理由見下 |
| A2.2 `elapsed_ms` 與 `node_timings` 數值相同 | ✅ | `test_node_done_events_carry_the_same_elapsed_ms_as_run_meta_node_timings`（釘在**事件來源**，不是只釘轉發層） |
| A2.3 其餘五支不發 `tool_step` | ✅ | `test_only_the_two_pipeline_tools_emit_tool_steps` |
| A2.4 `?stream=0` 與 SSE 同一條 `_run_turn`、`done` 欄位集合相同 | ✅ | 既有 AST 測試 ＋ §3 的實測比對 |
| A3.1 檢索回空 → `status=="empty"` | ✅ | `test_tool_result_status_tells_empty_apart_from_failed` |
| A3.2 KB 打不通 → `status=="failed"` 且 `note` 含原因 | ⚠️ **綠但打折** | 用會拋例外的 retriever 走同一條 `except` 分支驗過。**真的把 KB endpoint 改壞沒驗**（沒有 live 環境） |
| A3.3 同一支工具兩次，`call_id` 不同且可配對 | ✅ | `test_the_same_tool_called_twice_gets_two_different_pairable_call_ids` |
| A4.1 `ack`（seq 0）帶 `session_id` | ✅ | §2 實測 `{"seq": 0, ..., "session_id": ...}` |
| A4.2 收到 `ack` 後斷線，用該 `session_id` 續問記得前一輪 | ✅ | 見 §7 實測 |
| A4.3 開流後傳輸失敗 → `error.stage=="transport"` | ✅ | §2 實測 `{"stage": "transport", "error": "ConnectionError: ..."}` |

### A2.2 為什麼照契約不照提案

解析卷證若真的跑 n1–n6，**就會產出一份草稿**——那跟契約 §3.0 的表
（`extract_case_document` → `run_case(to_node="n3")`、`tool_step` ✅ n1–n3）、
跟 `to_node` 這個參數存在的理由、跟驗收條件「解析卷證跑完該 run 沒有草稿」
三者同時矛盾。提案那句是筆誤，不是另一種設計。**沒有自己選一邊**：
契約是唯一事實來源（`CONSTITUTION` §9），提案要改由 Ci 決定。

## 7. A4.2 實測：斷線之後接得回來

探針 `disconnect_probe.py`（scratchpad）。**跑的是真的 `gen()`**（含 worker thread
與 generator 被放棄那條路），只有 `_run_turn` 換成假的——但那個假的做了真的會做的事：
寫 `_SESSIONS`。回合要跑 3 秒，客戶端 1 秒就切斷。

```
1. 送出、收到 ack 之後 1 秒斷線
   event: ack
   data: {"seq": 0, "turn_id": "turn-f1ed234dc37a", "text": "收到", "session_id": "sess-A4"}
2. 立刻查 session（回合還沒跑完）：{"turns":0}
3. 等回合跑完再查：                {"turns":2}     ← worker 沒有因為客戶端走了就停
4. 用同一個 session_id 再問一輪：   {"turns":4}     ← 第一輪沒被丟掉
```

客戶端在 `ack` 就拿到 `session_id`（A4.1），斷線之後那一輪照樣被記下來，
續問接得回去。**這正是把 `session_id` 從 `done` 提前到 `ack` 的理由**：
斷在中途就永遠拿不到 `done`。

## 8. 補驗：前置條件 3 的**放行**路徑（2026-09-12 追加）

原本這條留紅，理由是「要等案件資源層寫 `manifest.json`」。重看之後那是錯的——
production 要等 Epic B，但**驗收只要一份手寫的 manifest fixture**（team lead 同日確認：
測試 fixture 是正當的，production 行為維持嚴格）。所以補上了：

`backend/tests/test_e2e.py::test_the_draft_tool_lets_precondition_three_through_when_the_manifest_really_has_content`

用**真的** `pipeline_adapter`（fixture 檔位）＋ **真的** `manifest.json`（暫存目錄）
＋ **真的** `ChatTools`，跑 解析卷證 → 生成草稿：
`status == "ok"`、`state == "VERIFIED"`、`cite_count > 0`，
六筆 `tool_step` 的中文 label 依序是 讀卷抽取／案件分類／程序審查／檢索法條與相似案／
草稿撰寫／引用守門。唯一沒有用真貨的是模型。

**為什麼這條非補不可**：先前只驗了「擋下來」的三種情形。**擋得住不等於放得行**——
一個把 `laws`／`references` 判斷寫反的實作，在只驗擋下來的組合下會全綠。

變異測試（把 `if not laws or not refs` 改成 `if laws and refs`）：

```
FAIL  test_the_draft_tool_lets_precondition_three_through_when_the_manifest_really_has_content
FAIL  test_draft_is_refused_when_no_laws_or_references_were_picked
FAIL  test_draft_resumes_from_n4_and_feeds_the_picked_laws_back_as_a_query_term
失敗：505/508 通過
```

三條都紅，這條測試真的咬得住。（順帶：初版裡我寫了一句
`assert_true(draft["run_id"] != tools.run_id or True, "")`——那是恆真斷言，已移除。）

## 9. 剩下的紅：寫成有解鎖條件的骨架

`backend/tests/test_live_plumbing.py` 末段兩條，**現在一定略過、不計入通過數**：

- `test_live_chat_extract_then_draft_walks_the_whole_contract`
- `test_live_chat_reports_a_broken_retrieval_source_as_failed_not_empty`

解鎖條件寫在檔裡（起 live 伺服器的指令、`RUN_MODE=bedrock`、
`CHAT_LIVE_BASE`／`CHAT_LIVE_CASE` 兩個環境變數）。兩段式略過：

```
$ python3 backend/tests/run_all.py
  略過 …：需要 live 聊天端點：設 CHAT_LIVE_BASE… 解鎖條件見本段檔頭註解。

$ CHAT_LIVE_BASE=… CHAT_LIVE_CASE=… python3 backend/tests/run_all.py
  略過 …：檔位不是 live：run_mode=fixture、missing=[]。聊天端點會回 503，這條驗不到真模型。
```

環境到位時會變成「骨架未實作」——**不會假綠**。這三條 live 才驗得到的東西，
docstring 裡逐條寫明了：模型看不看得懂工具說明、真實耗時是否落在契約的 10–11／24–72 秒
（契約那兩個數字是 n=3 量的，要重量）、真實負載下 `tool_step` 是否逐筆抵達。
