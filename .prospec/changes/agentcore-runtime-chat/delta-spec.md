# Delta Spec: agentcore-runtime-chat

> **Stretch／預設關閉。** 下列需求只在 `CHAT_BACKEND=agentcore` 時生效；預設 `inproc` 時等同不存在。
> **例外（兩種檔位都生效）**：<br>
> · REQ-CHAT-102 的 `/api/health` 欄位——它存在的理由就是分辨現在跑哪一層。<br>
> · REQ-CHAT-101 **AC3** 的「Runtime 不讀 runstore、case payload 由 HTTP 層先讀好」——**這是甲乙共用的契約**（甲案已依此調整，見設計文件 §4.4），不是 agentcore 專屬。照表頭字面讀會以為 inproc 不適用，那會讓人在甲案就繞過它。

## ADDED

### REQ-CHAT-101: 聊天託管層可切換且前端無感

**Description:**
`POST /api/cases/{case_id}/chat` 依環境變數 `CHAT_BACKEND`（`inproc` | `agentcore`，預設 `inproc`）
決定在 FastAPI 行程內執行 Strands agent，或 proxy 到 Bedrock AgentCore Runtime。
兩種模式共用同一份 `backend/llm/chat.py`，前端呼叫路徑與 SSE 事件格式不變。

**Acceptance Criteria:**
1. 兩種模式下 `curl -N` 收到的 SSE **事件型別出現順序**相同（`tool_call`／`tool_result`／`token`／`done`／`error`），依 `chat-honesty-lamps.md` §4.7 的事件序保證。驗法用 `uniq` 塌連續重複後 `diff`，**不用 `sort -u`**（那驗集合不驗順序）。
2. `done` 事件的**欄位集合**在兩種模式都與 `docs/spec/2026-09-12-chat-honesty-lamps.md` §4.4 **逐欄相同**；
   只比對 `lamp`／`tier`／`origin`／`refs[]` 不算通過。**16 為撰寫時（2026-09-12）的值，以 lamps §4.4 當下內容為準**——
   那份 spec 仍在演進，欄位增刪不會有任何機檢報錯，所以契約指向來源而不是這個快照。
3. **燈號在 Runtime 內算。** `agentcore` 模式下由 `backend/agentcore_entry.py` 呼叫
   `backend/llm/chat.py` 的 `classify_answer()` 把 `done` 組好再吐；FastAPI proxy 只做
   bytes 轉送、**不重組也不補算燈號**（唯一例外是 `?stream=0`，見 REQ-CHAT-103）。
   Runtime 也**不讀 runstore**——case payload 分區由 HTTP 層先讀好、隨 `/invocations` 的
   payload 夾帶進去（`build_payload()` 住在 `backend/orchestrator/graph.py`，那個目錄不在 Runtime 映像裡）。
4. 前端程式碼零改動。

**Priority:** Low（Stretch）

---

### REQ-CHAT-102: 託管層的實際生效值必須外顯

**Description:**
`GET /api/health` 回傳 `chat_backend`，值為**實際生效**的託管層，而非設定期望值。
`CHAT_BACKEND=agentcore` 但 `AGENTCORE_RUNTIME_ARN` 缺時**啟動即失敗**，不得靜默退回 `inproc`。
Runtime 呼叫失敗時不自動退回 `inproc`；回什麼見 REQ-CHAT-104（開流前 503、開流後 `error` 事件，**不重用 502**）。

**Acceptance Criteria:**
1. `/api/health` 含 `chat_backend`，值為 `inproc` 或 `agentcore`。
2. 缺 ARN 時服務啟動失敗，錯誤訊息指出缺哪個設定。
3. Runtime 失敗時不出現「看起來成功」的回應；狀態碼與事件依 REQ-CHAT-104。

**Priority:** Low（Stretch）· CONSTITUTION §1 分層誠實

---

### REQ-CHAT-103: `?stream=0` 在 agentcore 模式下由 proxy 聚合

**Description:**
`chat-honesty-lamps.md` §2.2 把 `?stream=0` 定為凍結契約（回 `application/json`，body 是 `done`
物件再加 `events[]`），它是前端硬性驗收項、也是賽場網路讓 SSE 斷流時唯一的降級路徑。
`agentcore` 模式下這條**由 FastAPI proxy 收齊 Runtime 的事件後聚合成單一 JSON**——
這是 REQ-CHAT-101 第 3 條「proxy 不重組」原則的**具名例外**，不得擴大到串流路徑。

**Acceptance Criteria:**
1. `CHAT_BACKEND=agentcore` 下帶 `?stream=0`，回 `application/json`，形狀與 `inproc` 模式逐欄相同。
2. 甲案驗證 `?stream=0` 的那條 AC 在 `agentcore` 檔位下**仍然綠**。
   不做這段的後果是：開一個 Stretch 開關就讓主線一條已驗綠的 AC 失效。

**Priority:** Low（Stretch）· 但在乙案內部是必要條件，不是可選項

---

### REQ-CHAT-104: Runtime 失敗不重用 502，且分開流前後

**Description:**
`chat-honesty-lamps.md` §2.3 已把 **502 定義成「該 run 是失敗的」**（body 為
`{run_id, status:"failed", node, error}`）。託管層失敗回一個形狀不同的 502，前端會誤讀成
案子跑失敗、去顯示一個不存在的失敗節點。而 §2.3 的紅線是「拿到 `text/event-stream`
就一定至少有一個 `done` 或 `error`」，開流後才斷的話已經回不了狀態碼。

**Acceptance Criteria:**
1. **開流前**失敗（Runtime 不健康／權限錯／冷啟動逾時）→ 回 **503**，形狀比照 §2.3 的 503，
   `why` 說明是託管層不可用並註明「切 `CHAT_BACKEND=inproc` 即恢復」。**不得回 502。**
2. **開流後**失敗（throttle／microVM 回收／session 逾時／ALB 900 秒切斷）→ 發一個 `error`
   事件後關流，`stage` 用 `transport`（值域見 `docs/spec/2026-09-12-chat-honesty-lamps.md` §4.6）。
   **不自行造值**；該值若不在 §4.6 的值域裡，停下來問，不要自己挑一個。
3. 兩種情況都**不自動退回 `inproc`**，也都不得演成「回答完成」。

**Priority:** Low（Stretch）· CONSTITUTION §1

---

### REQ-INFRA-101: 新增的 Runtime 權限不大於現有 ECS task

**Description:**
Runtime 執行角色與 `taskRole` 新增的 statement 一律手刻白名單，
不使用 CDK 自動產生的 execution role policy（L1／L2 皆然）（`aws/aws-cdk#35852`（<https://github.com/aws/aws-cdk/issues/35852>） 回報模板權限不足）。

**Acceptance Criteria:**
1. Runtime 執行角色僅含：指定模型的 `bedrock:InvokeModel`／`InvokeModelWithResponseStream`、
   指定 KB 的 `bedrock:Retrieve`、自身 log group 的 logs 權限、新 ECR repo 的拉取權限。
2. `npx cdk synth` 的輸出中，本 change 新增的 statement **除 `ecr:GetAuthorizationToken` 外無
   `Resource: "*"`**，且無 `bedrock:*`、`s3:*`。`ecr:GetAuthorizationToken` 是 AWS 明文不支援
   資源層級限定的 action，寫成 repo ARN 則 Runtime 拉不到映像，這是唯一例外。
   ⚠️ 人工核前要先列出**既存**命中當基線：現有 stack 用 `ApplicationLoadBalancedFargateService`，
   CDK 自動建的 execution role 本來就帶 `AmazonECSTaskExecutionRolePolicy`（含該 action on `*`），
   所以無限定的 grep 在動手之前就已經有命中。
3. 跨區 inference profile 沿用 `appeal-backend-stack.ts` 的 `invokeArnsFor()` 雙 ARN 規則。

**Priority:** Low（Stretch）· CONSTITUTION §7

---

### REQ-INFRA-102: 回滾在 10 分鐘內完成且不需刪資源

**Description:**
回滾動作為「改 task definition 的 `CHAT_BACKEND` 回 `inproc` + 重新部署」，
不重建映像檔、不動前端、不刪任何 AWS 資源。

**Acceptance Criteria:**
1. 實測回滾一次並計時，總時間 ≤ 10 分鐘。
2. 回滾後 `infra/cdk/verify.sh` 全綠。甲案完成後 `verify.sh` 是**五段有標號的檢查**（首頁是 UI／檔位四項／Bedrock 連通／守門 409／chat SSE）**外加一段未標號的資料隔離檢查**。

**Priority:** Low（Stretch）

---

## MODIFIED

_No modifications in this change._
既有六節點、`/runs`、守門、規則引擎與所有現有 endpoint 皆不受影響。

## REMOVED

_No removals in this change._
