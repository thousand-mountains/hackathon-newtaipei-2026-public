# Delta Spec: chat-ask-agent

> REQ ID 格式：`REQ-{MODULE}-{NUMBER}`。
> 契約細節（欄位、事件名、狀態碼）的唯一真實來源是 `docs/spec/2026-09-12-chat-honesty-lamps.md`；
> 本檔只列「需求層級的增刪改」，不重複抄 schema。

## ADDED

### REQ-CHAT-001: 已完成的 run 上可以自由追問

**Description:**
新增 `POST /api/cases/{case_id}/chat`，body `{run_id, message, session_id?, context?}`（`context` 承載承辦人選取的那一句，欄位形狀見 spec §2.1），以 SSE 回應；另有 `?stream=0` 的一次性 JSON 降級模式。回答只准來自五個工具的結果：`search_regulations`（法條查表）、`search_similar_decisions`（KB 相似案）、`retrieve_refs`（KB 判解函釋）、`read_case`（唯讀卷內）、`refine_text`（潤稿）。

**架構契約**：`read_case` 的 payload **由呼叫端提供**。`backend/llm/chat.py` 不得 import `backend.orchestrator.*` 或 `backend.nodes.*`——`build_payload()` 在 `backend/orchestrator/graph.py:592`，而 `graph.py:45` import 了全部六個節點。HTTP 層負責 `load_run` → `build_payload` → 切分區 → 餵參數。這對甲乙兩案都成立（Runtime 容器裡沒有 runstore）。**沒有機檢抓得到這條，要靠人看。**

**Acceptance Criteria:**
1. `RUN_MODE=bedrock` 下 `curl -N` 打該端點，依序收到 `tool_call`／`tool_result`／`token`／`done`。
2. `done.refs[]` 的每個 `id` 都在同回合某個 `tool_result.hits[].id` 出現過。
3. 問一個卷內查無的字號時，回答明說查無、`done.refs` 為 `[]`、`done.lamp == "r"`。
4. 模型呼叫失敗時收到 `event: error`，且該回合**沒有** `event: done`（失敗不得偽裝成一則回答）。
5. `?stream=0` 回 HTTP 200 的 JSON（`done` 物件加 `events[]`，`events[]` 非空、body 不含 `event:` 框架字串），供 SSE 斷流時降級。
6. `error` 事件的 `stage` 值域為 `{tool, model, transport, internal}` 四個；開流之後的傳輸／託管層失敗回 `transport`，**不得**塞 `internal`。
7. `grep -c '^REF_PREFIXES[[:space:]]*=' backend/nodes/n5_draft.py` 為 0、`... backend/retrieval/kb.py` 為 1，且 `from backend.nodes.n5_draft import REF_PREFIXES as a; from backend.retrieval.kb import REF_PREFIXES as b; assert a is b` 成立。

**Priority:** High

---

### REQ-CHAT-002: 每則回答帶機械判定的誠實燈號

**Description:**
`backend/llm/chat.py` 的純函式 `classify_answer(question, answer, refs, refine_used)` 依四條規則判出 `lamp`／`tier`／`origin`，全程不呼叫模型。`lamp` 值域限 `{y, r}`，沿用既有 `g/y/r` 的前端映射。

**Acceptance Criteria:**
1. `/opt/homebrew/bin/python3 backend/tests/run_all.py` exit 0，且輸出含 section「聊天誠實燈號（機械規則，零 LLM）」。
2. 單元測試涵蓋四條規則，每條斷言 `(lamp, tier, origin)` 三元組；`tier` 由 `backend/gate/lamps.py:72` `tier_for_lamp()` 算出，改用 `origin_registry.tier_of()` 會讓規則 4 的測試紅。
3. 有一條測試斷言 `lamp == "g"` 在所有規則分支下都不出現。
4. `test -f backend/api/chat.py && grep -rni 'strands\|_load_model\|_invoke_structured' backend/api/` 檔案存在且 grep 無輸出（agent 只在 `backend/llm/` 建）。⚠️ **不可**寫成單檔 `grep ... backend/api/chat.py`——檔案還沒建立時 grep 印警告到 stderr、stdout 空、exit 2，看起來就是「無輸出」，那是假通過。加 `-i` 與 `-r` 是為了擋 `import Agent as A` 這類漏法。
5. `backend/tests/test_chat.py` 的 `test_chat_module_never_imports_orchestrator_or_nodes`（AST 版，已註冊進 `run_all.py`）通過。**用 AST 不用 grep**：grep 分不出 docstring 與真 import（實作端已踩過一次），也擋不住 alias import（**必須先驗檔案存在**：少了 `test -f`，`backend/llm/chat.py` 還沒建立時 grep 印警告到 stderr、stdout 空、exit 2，這條就會在**還沒做**的時候是綠的——跟原版 AC3 同一個坑。） 無輸出（架構契約的機械驗法；`backend/api/chat.py` 含 `build_payload` 是**預期行為**，不是違規）。

**Priority:** High

---

### REQ-CHAT-003: 數字類問題不由聊天計算

**Description:**
偵測到期限／天數／金額類提問時，強制 `lamp="r"`、`origin="human_required"`，並回 `redirect` 指向既有 `/api/deadline`。偵測對**使用者的問題**做字面比對，不依賴模型判斷。

**Acceptance Criteria:**
1. 問「還剩幾天可以提訴願？」→ `done.redirect.endpoint == "/api/deadline"`、`done.lamp == "r"`。
2. 回答文字不含任何天數數字。
3. 即使模型違規講出天數，`lamp` 仍為 `r` 且 `redirect` 非 null（規則層不依賴 prompt 有效）。

**Priority:** High

---

### REQ-CHAT-004: 潤稿永遠標「請人工判斷」

**Description:**
`refine_text` 進 tools（Ci 2026-09-12 拍板），接受它永遠紅燈。改寫結果不寫回任何 `doc[]` 句子。每個工具進入點各呼叫一次 `_throttle()`（spec §8.3；只保證單一進程不超速，甲乙並存時全域仍可能超過 1 RPS）。

**Acceptance Criteria:**
1. 請系統改寫一句草稿 → 收到 `tool_call` 且 `tool == "refine_text"`。
2. `done.lamp == "r"`、`done.origin == "llm"`，即使同回合也檢索到 refs。
3. `test -f backend/api/chat.py && test -f backend/llm/chat.py && grep -rn 'save_run' backend/api/chat.py backend/llm/chat.py` **無輸出**（聊天路徑不寫 runstore）。⚠️ 舊版寫「無任何寫入 run 或 **payload** 的路徑」有兩個毛病：無法機械判定；而且依 REQ-CHAT-001 的架構契約，`backend/api/chat.py` **本來就要**呼叫 `build_payload`——那是取資料，不是寫入。兩個 `test -f` 少一個就會在檔案還沒建立時必綠。

**Priority:** Medium

---

### REQ-CHAT-005: fixture 檔位照實拒絕

**Description:**
`RUN_MODE != "bedrock"` 或 `settings.missing_live_settings()` 非空時，在開串流**之前**回 503 JSON，附 `run_mode` 與 `missing[]`。

**Acceptance Criteria:**
1. `RUN_MODE=fixture` 下打該端點 → HTTP 503、`Content-Type: application/json`。
2. 回應內容不含任何 `event:` 字串。
3. body 含 `run_mode` 與 `missing[]` 兩個欄位。

**Priority:** High

---

### REQ-CHAT-006: 部署零改動

**Description:**
聊天沿用 `BEDROCK_MODEL_ID_DRAFT`，不新增 AWS 資源、不改 CDK、不改 IAM、不加 Python 套件。**本需求只適用甲案必要層**；乙案（AgentCore Runtime，`.prospec/changes/agentcore-runtime-chat/`）會同時改 `infra/` 的 CDK stack 與 `backend/requirements.txt`（要加 `bedrock-agentcore` SDK，目前釘的 `boto3~=1.35.0` 沒有那個 client），那是乙案自己的驗收範圍。

**Acceptance Criteria:**
1. `git diff --stat $BASE_SHA...HEAD -- infra/ backend/requirements.txt backend/Dockerfile` 只有 `infra/cdk/verify.sh` 一列（`$BASE_SHA` 為開工前的 commit；**不可用 `main` 當左端**，在 main 分支上那是假通過）。
2. `./infra/cdk/verify.sh` 原四段 ＋ 新增的聊天段全綠。

**Priority:** High

---

## MODIFIED

### REQ-SPEC-2026-09-07-SCOPE: 「不做 ask 追問 agent」

**原內容：** `docs/spec/2026-09-07-bedrock-live-nodes-design.md:39` 把 ask 追問 agent 列為不做，理由是「沒有任何 story 或驗收條件要求對話」。

**改為：** 做。理由已不成立——新設計稿把自由問答放進主畫面，且 `backlog.md:61` 的 `HACK-S-3` 就是這條 story。本 change 提供驗收條件。

**未一併推翻：** 同一份 spec `:51` 的 D3「AgentCore 維持 Stretch」**維持不變**。聊天 agent 跑在既有 FastAPI 容器內。

---

## REMOVED

_No removals in this change._
