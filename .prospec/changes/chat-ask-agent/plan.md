# Implementation Plan: chat-ask-agent

> 與 `plans/2026-09-12-chat-ask-agent.md` 同一份計畫的兩種格式：這份給 prospec 流程與開發者，
> `plans/` 那份給 plan-guardian 依 CONSTITUTION §5 驗收。**兩份不一致時以 `plans/` 那份為準**，
> 因為 §5 指名的是 `plans/`。契約細節（端點、事件名、欄位名）以
> `docs/spec/2026-09-12-chat-honesty-lamps.md` 為唯一真實來源。

## Overview

在既有 FastAPI 進程裡加一個 `POST /api/cases/{case_id}/chat`，用**已經在用的** Strands SDK 建一個單 agent，把既有檢索（法條查表、KB 相似案、KB 判解函釋）與唯讀卷內讀取包成工具，回答以 SSE 串流回前端。

三個設計決策決定了檔案怎麼切：

1. **誠實判定是純函式，不在 HTTP 層。** `run_all.py` 必須能在一台只有 `python3` 的機器上跑完（`backend/tests/harness.py:1-7`），而 `backend/api/chat.py` 頂層會 import fastapi——測試只要 `from backend.api.chat import ...` 就 **ImportError**。所以判定邏輯全部放 `backend/llm/chat.py` 的純函式。
   - ⚠️ **理由不是「靜態掃描會擋」**：`scan_core_path_dependencies`（豁免名單在 `backend/tests/run_all.py:262-263`）只掃**檔案自己那一行 import** 的第三方套件名，`from backend.api.chat import ...` 是第一方 import，**掃描會放行**。擋住的是執行期。照錯的理由做，會以為「只要不寫 `import fastapi` 就行」而把判定放進 api 層。
   - ✅ 已確認安全：`backend/llm/chat.py` 頂層 import strands **不會**被 `scan_llm_import_graph` 擋——該掃描只從 `LLM_FORBIDDEN_NODES`（n2／n3／n4／n6）起走可達圖，`backend/llm/` 不在範圍內。
2. **agent 模組不依賴 FastAPI，也不依賴 orchestrator。** 這樣它同時能被 FastAPI 呼叫（本次）與被 AgentCore Runtime 呼叫（乙案）。前端呼叫路徑兩種情況都不變。
   - **`read_case` 由呼叫端餵 payload，不自己去取。** `build_payload()` 在 `backend/orchestrator/graph.py:592`，`graph.py:45` 是 `from backend.nodes import n1_extract, …, n6_gate`——聊天層 import 它就把 orchestrator 與六個節點整包拉進 import 圖，等於繞道違反「不得 import `backend.nodes.*`」。
   - **這件事沒有機檢抓得到**：紅線第 4 條只從 N2／N3／N4／N6 起走、第 3 條對 `backend/llm/` 具名豁免、AC2 有 `try/except` 守衛也不會紅。它是規格層紅線（spec §4.0「payload 由呼叫端提供」），要靠人看。
   - 這同時是乙案的前提：Runtime 容器裡沒有 runstore。**所以「payload 由呼叫端提供」對甲乙兩案都是契約。**
3. **ref 編號由聊天層自己發。** `KBRetriever.search` 每次呼叫都從 `kb-1` 重新編號（`backend/retrieval/kb.py:123`），同一回合呼叫兩次就會撞號。聊天層維護 `c1, c2, …` 單調遞增的對照表。

## Affected Modules

| Module | Impact | Changes |
|--------|--------|---------|
| `backend/llm/chat.py` | High | **新建**。`build_chat_agent(case_payload, refbook, retriever, snapshot)`、五個 `@tool`、`RefBook`（ref 編號）、`classify_answer()`、`SESSIONS`（進程內記憶）。**import 清單不得出現 `backend.orchestrator.*` 或 `backend.nodes.*`** |
| `backend/api/chat.py` | High | **新建**。`router`、`ChatIn`、503 閘門、SSE generator、`?stream=0` 模式。**唯一碰 runstore／orchestrator 的檔**：`load_run` → `build_payload` → 切分區 → 餵給 agent |
| `backend/api/app.py` | Low | `include_router(chat.router)` 一行 + 檔頭端點表加一列 |
| `backend/tests/test_chat.py` | High | **新建**。純函式測試（stdlib、零 fastapi／strands import） |
| `backend/tests/run_all.py` | Low | `sections` 加一列、import 加一行 |
| `infra/cdk/verify.sh` | Low | 新增第 5 段：`curl -N` 打 chat 收到 `done` |
| `docs/spec/2026-09-12-chat-honesty-lamps.md` | High | **新建**。燈號規則 + SSE schema，Pink 的契約 |
| `plans/2026-09-12-chat-ask-agent.md` | High | **新建**。plan-guardian 驗收用 |
| `backend/llm/prompts/chat_ask.md` | Medium | **新建**。system prompt |
| `backend/nodes/n5_draft.py`、`backend/retrieval/kb.py` | Low | `REF_PREFIXES` 從前者搬到後者，兩邊 import 同一份。純搬家、行為不變，既有測試須仍綠 |
| `backend/llm/client.py` | None | 只取用 `_load_model`／`_throttle`／`LLMError`／`model_ids`，**不改** |
| `infra/cdk/lib/appeal-backend-stack.ts` | None | 不動。聊天沿用 `BEDROCK_MODEL_ID_DRAFT`，IAM 已涵蓋 |
| `backend/requirements.txt`、`backend/Dockerfile` | None | 不動。`strands-agents`／`boto3` 已在 |

## Implementation Steps

1. **凍結契約（先做，Pink 的 unblocker）**
   - 寫 `docs/spec/2026-09-12-chat-honesty-lamps.md`：端點、五個事件、每個事件的欄位、四條燈號規則、`refine_text` 的永遠紅燈、`g` 永不出現的理由。
   - 通知 Pink：可用 mock SSE 開工，不必等後端。
   - 驗收（對照對象與門檻都寫明，不要只說「逐字一致」）：對五份檔各跑一次
     `grep -c 'cases/{case_id}/chat'`（每份 ≥ 1）、`grep -c 'tool_call'`／`'tool_result'`／`'token'`／`'done'`／`'error'`、以及 `grep -c 'transport'`（凡提到 `stage` 值域的檔都要有）。
     **任一份為 0 就是沒同步**。這是步驟自檢，不是 AC——AC 在 `plans/` 那份。

2. **`backend/llm/chat.py` — 純函式層（先寫可測的部分）**
   - `RefBook`：`add(hits) -> list[dict]` 回配好 `c<N>` 的命中；`ids() -> set[str]`。
   - `NUMERIC_Q = re.compile(...)`：偵測期限／天數／金額類提問（規則與字面清單寫在 spec §3）。
   - `classify_answer(question, answer, refs, refine_used) -> dict`：回 `{lamp, tier, origin, why, refs, dropped_refs, redirect}`。**四條規則依序判，第一個命中即回。**
   - `tier` 用 `backend/gate/lamps.py:72` 的 `tier_for_lamp(lamp, origin)` 算，**不得**用 `backend/config/origin_registry.py:154` 的 `tier_of()`——後者的 `ORIGIN_TO_TIER["llm"] = TIER_SOURCED`，在聊天情境會把紅燈標成「有出處」（spec §3.0）。
   - **不呼叫任何模型。** 這是 US-2 驗收的第三條。
   - 驗收：`/opt/homebrew/bin/python3 -c "import backend.llm.chat"` 在**沒裝 strands** 的機器上成功（頂層 import 用既有 try/except 守衛寫法，比照 `client.py:22-26`）。

3. **`backend/tests/test_chat.py` + 登記**
   - 四條燈號規則各一個 test；`lamp == "g"` 永不出現一個 test（掃過所有規則分支的輸出）。
   - `RefBook` 跨兩次 `search` 不撞號一個 test（餵兩批都叫 `kb-1` 的假 Hit）。
   - 模型引用白名單外編號 → 被剔除且強制紅一個 test。
   - `refine_used=True` 時即使 refs 非空也回紅一個 test。
   - `run_all.py`：`from backend.tests import test_chat` + `sections` 加 `("聊天誠實燈號（機械規則，零 LLM）", [test_chat])`。
   - 驗收：`/opt/homebrew/bin/python3 backend/tests/run_all.py` exit 0，輸出含該 section 名稱。

4. **`backend/llm/chat.py` — agent 層**
   - `build_chat_agent(case_payload, refbook, retriever, snapshot)`：`Agent(model=_load_model("draft"), system_prompt=_prompt("chat_ask"), tools=[...], callback_handler=None)`。`case_payload` 是呼叫端切好的普通 dict。
   - **`read_case(section)` 只從 `case_payload` 取值**，不碰 `load_run()`／`build_payload()`（見 Overview 第 2 條）。
   - **每個工具進入點各呼叫一次 `_throttle()`**（spec §8.3；Ci 若確認賽制不算聊天再拿掉）。
   - 五個工具：`search_regulations`（lawtable，只回條號存在性，**明講沒有條文原文**，`verified` 原樣帶出不得填死 true）、`search_similar_decisions`（KB 配額查詢）、`retrieve_refs`（KB + `REF_PREFIXES`）、`read_case`（唯讀 payload 分區）、`refine_text`（單獨一次 `_load_model("draft")` 呼叫）。
   - **先把 `REF_PREFIXES` 從 `backend/nodes/n5_draft.py:44` 上移到 `backend/retrieval/kb.py`**，兩邊都從那裡 import。`backend/llm/chat.py` 不得 import `backend.nodes.*`（`n5_draft.py` 自己 import `backend.llm.client`，會層級倒置）。改完 `n5_draft.py` 的既有測試要仍綠。
   - 每個工具在回傳前 `refbook.add(hits)`，並把該次呼叫 append 進 `turn.tool_calls`（比照 `client.py:285-287`）。
   - system prompt 三條硬規則：只依工具結果回答／查無就說查無不得推測／期限天數金額一律不算，改請使用者用期間試算。
   - 驗收（兩條，與 `plans/` 的 AC3 一致）：`test -f backend/api/chat.py && grep -rni 'strands\|_load_model\|_invoke_structured' backend/api/` 檔案存在且 grep 無輸出；`backend/tests/test_chat.py` 的 `test_chat_module_never_imports_orchestrator_or_nodes`（AST 版，已註冊進 `run_all.py`）通過（**必須先驗檔案存在**：少了 `test -f`，`backend/llm/chat.py` 還沒建立時 grep 印警告到 stderr、stdout 空、exit 2，這條就會在**還沒做**的時候是綠的——跟原版 AC3 同一個坑。） 無輸出。**不可**寫成單檔 `grep ... backend/api/chat.py`——檔案還沒建立時那條必綠（exit 2、stdout 空）。

5. **`backend/api/chat.py` — HTTP 層**
   - 503 閘門排第一：`run_mode() != "bedrock"` 或 `missing_live_settings()` 非空 → `JSONResponse(..., 503)`，**在開串流之前**。
   - 接著驗 `run_id`：`BUS.status` running → 409；`load_run` 失敗 → `_translate`；run 的 case 與路徑 `case_id` 不符 → 400。
   - **取資料是這一層的職責**：`load_run(run_id)` → `build_payload(state)` → 切出 agent 要的分區 → 當參數餵給 `build_chat_agent()`。
   - 同步 `def` + `StreamingResponse`，headers 照抄 `app.py` 的 `run_events()` 尾端（依 `7724acf` 是 460-464；**工作樹已被另一個 session 改動約 +21 行，用函式名定位不要只信行號**）。
   - generator 依序 yield `tool_call`／`tool_result`／`token`／`done`，例外一律 `error` 後關流。
   - **同時做 `?stream=0` 的一次性 JSON 模式**（spec §2.2）：同一個 `done` 物件加 `events[]`。它是契約也是 SSE 斷流時的備援，臨場才寫來不及。
   - 驗收：fixture 模式 curl 回 503 且 body 不含 `event:`；`?stream=0` 回 `application/json`。

6. **掛 router、live 端到端、verify.sh**
   - `app.py` 加 `include_router`；檔頭端點表加一列（檔頭表是對外契約的一部分，不是註解）。
   - 跑 proposal 的 Success Criteria 第 3、4 條，把輸出貼進完成回報。
   - `verify.sh` 第 5 段：沿用第 3 段拿到的 `$rid`，`curl -N --max-time 90` 打 chat，檢查收到 `event: done` 且該行 JSON 有 `lamp` 欄位、值 ∈ `{y, r}`。
   - 驗收：`./infra/cdk/verify.sh` 五段全綠。

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Strands 多輪記憶行為與預期不同（訊息格式、tool 歷史） | High | **降級成單輪無記憶**：每次呼叫只帶當前問題 + 案件摘要，`done.session_id` 回 `null` 並附 `memory:"off"`。前端已經在畫對話泡，少了上下文不影響 demo 主線 |
| `refine_text` 多一次模型呼叫，回應變慢到 demo 不能用 | Medium | 降級成不進 tools：改由前端獨立按鈕打同一端點帶 `mode:"refine"`，後端直接單輪改寫。燈號規則不變（永遠紅） |
| 同回合多次 KB 查詢撞號，refs 指錯來源 | High | `RefBook` + 第 3 步的專屬測試。**這是引用必可驗的紅線**（CONSTITUTION §2），不是體驗問題 |
| 模型自己算期限並講出一個天數 | High | 兩道：system prompt 禁止 + `classify_answer` 對**問題**偵測數字類即強制紅並帶 `redirect`。規則引擎的答案只從 `/api/deadline` 出（CONSTITUTION §4） |
| SSE 佔用 threadpool thread（預設 40） | Low | demo 量級遠低於 40。**不改成 async**——改寫既有同步寫法的風險大於收益 |
| 賽場網路使 SSE 中斷 | Medium | 前端收不到 `done` 時顯示「連線中斷，請重問」，**不得**把已收到的半截 token 當成一則完整回答標燈 |
| 聊天把 Bedrock 呼叫量推高、吃到賽制 1 RPS 限制 | Medium | **已定做法：每個工具進入點各呼叫一次 `_throttle()`**（一輪多等約 3–5 秒，估計未量測）。不能只靠既有的閘——`backend/llm/client.py:76-79` 的 docstring 明說 agent loop 的內部工具往返不經過 `_throttle()`，而聊天正是 agent loop。**誠實限制**：這只保證單一進程不超速；乙案上線後聊天在另一個容器，兩邊各節各的，甲乙並存時全域仍可能超標。賽制是否把聊天算進去，待 Ci 確認（spec §8.3） |
| 這個 change 推翻了 2026-09-07 spec 的「不做 ask agent」 | Low | 已在 proposal Background 明列並附行號。AgentCore 那條**沒有**被推翻——乙案另有一份放著的計畫（`.prospec/changes/agentcore-runtime-chat/`、`plans/2026-09-12-agentcore-runtime-chat.md`），`CHAT_BACKEND` 預設 `inproc`，且該計畫明訂事件與燈號契約以本 change 的 spec 為唯一真實來源 |
