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
| `generate_decision_draft` 走到底（含真 manifest） | 需要案件資源層先寫 `manifest.json`。**流水線那一段已用真的 `run_case` 驗過**（`test_the_bridge_reproduces_the_two_chat_tools_end_to_end`，fixture 檔位：SCREENED → 續跑 → VERIFIED、不重跑 n1–n3），沒驗到的只有「manifest 有內容時前置條件 3 會放行」那一步 |

## 5. 補記：adapter 從 `api/chat.py` 搬到 `backend/orchestrator/chat_bridge.py`

原本 adapter 寫在 `backend/api/chat.py` 裡，而那個檔頂層 import fastapi——
於是 `CaseState` → dict 這段轉換**一行都跑不到**（測試路徑零外部依賴）。
它正是 Epic C 要接的東西，搬到橋那一側之後由 `test_e2e.py` 用真的 `run_case`
（fixture 檔位）跑過：解析卷證 → `SCREENED` 無草稿 → 續跑 → `VERIFIED`
且 `node_timings` 只有 n4/n5/n6。
