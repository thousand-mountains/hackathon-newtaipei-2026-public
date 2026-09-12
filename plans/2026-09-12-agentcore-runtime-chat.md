# 聊天 agent 託管到 AgentCore Runtime（2026-09-12）· **Stretch**

> **這份是放著的計畫，不是開工單。** 甲案（FastAPI 內建 Strands agent）是主線，
> 本工作包預設**不做**；`CHAT_BACKEND` 預設 `inproc`，等於乙案不存在。
> 架構設計：`docs/spec/2026-09-12-agentcore-runtime-design.md`
> prospec change：`.prospec/changes/agentcore-runtime-chat/`
> **前提（甲案）**：`plans/2026-09-12-chat-ask-agent.md` 與 `.prospec/changes/chat-ask-agent/`；
> 事件與燈號契約唯一真實來源是 `docs/spec/2026-09-12-chat-honesty-lamps.md`，本檔不另行定義。

## 目標

把已經寫好的 Strands 聊天 agent 換一個託管層跑在 Bedrock AgentCore Runtime 上，
**agent 程式碼與前端都不改**，並且隨時能用一個環境變數切回來。
做完評審能看到什麼：系統上有一個真的跑在 AgentCore Runtime 的 agent，
而且可以當場切回行程內執行、畫面一模一樣——**可插拔本身就是展示內容**。

## 步驟

1. **核待查項**（0.3 h）——五項裡兩項已於 2026-09-12 實跑結案，剩三項；任一查不到就停。
   - ~~TypeScript CDK 有沒有 `aws-bedrockagentcore`~~ **已結案**：有，且含 L2 `Runtime` 與 grant helper（2026-09-12 實跑）
   - ~~`bedrock-agentcore:InvokeAgentRuntime` 的 action 名稱~~ **已結案**：`perms.js` 的 `RUNTIME_INVOKE_PERMS`；改用 `grantInvokeRuntime()` 後不必自己寫
   - `bedrock-agentcore` Python SDK 的 import 名稱與 `@app.entrypoint` API
   - `InvokeAgentRuntime` 設 `Content-Type: text/event-stream` 是否即回 SSE（**串流設計的地基**）
   - `boto3~=1.35.0`（`backend/requirements.txt` 現在的釘法，2024-08 版本）有沒有
     `bedrock-agentcore` client——沒有就會拋 `UnknownServiceError`，proxy 一行都跑不起來
2. **本機 ARM64 容器跑通**（1.5 h）——新增 `backend/agentcore_entry.py` 與
   `backend/Dockerfile.agentcore`，`docker run --platform linux/arm64` 後 `GET /ping` 回 200、
   `POST /invocations` 收到 SSE。建置 context 的 exclude 獨立確認（CONSTITUTION §6）。
3. **CDK 加資源**（1.5 h）——Runtime、ARM64 ECR repo、Runtime 執行角色（手刻）、
   `taskRole` 第三條 statement、`environment` 兩個新 key。`cdk synth` 人工核 policy。
4. **後端分派與 SSE 轉送**（1 h）——`settings.py` 兩個設定、`backend/api/chat.py` 的
   `agentcore` 分支（**只做 bytes 轉送，不重組、不補算燈號**；唯一例外是 `?stream=0`
   要由 proxy 聚合成單一 JSON）、`/api/health` 加 `chat_backend`。
   失敗分兩種，**不重用 502**：開流前打不通回 **503**，開流後才斷發 `error` 事件、
   `stage` 用 `transport`（設計文件 §8.1）。兩者都不自動退回 `inproc`。
   燈號由 Runtime 內的 `agentcore_entry.py` 呼叫甲案的 `classify_answer()` 算好再吐；
   **payload 分區由 HTTP 層先讀好餵進去，Runtime 不碰 runstore**（設計文件 §4.4）。
5. **部署、驗收、計時回滾**（1 h）——跑驗收指令、實際回滾一次並計時。

## 驗收條件

| # | 跑什麼 | 看到什麼才算過 |
|---|---|---|
| 1 | 兩種模式各打一次 `curl -N ... \| grep -o '^event: .*' \| uniq`，兩份 `diff` | diff 空輸出＝**事件型別的出現順序相同**（`tool_call`／`tool_result`／`token`／`done`／`error`）。**不用 `sort -u`**（驗的是集合，順序錯亂照樣綠，而 lamps §4.7 對順序有保證）；也不原樣比整串（`token` 筆數每次不同必然 diff） |
| 2 | `curl -s <endpoint>/api/health \| python3 -m json.tool \| grep chat_backend` | 值是 `agentcore`（且切回後是 `inproc`） |
| 3 | `cd infra/cdk && npx cdk synth --json` 後照 proposal Success Criteria 第 5 條的 python 逐角色印出（**不要 grep `bedrock-agentcore`，抓不到 Runtime 執行角色**） | **人工逐條核本 change 新增的 statement**：除 `ecr:GetAuthorizationToken`（AWS 明文不支援資源限定）外無 `Resource: "*"`，且無 `bedrock:*`、`s3:*`。⚠️ 先列出既存命中當基線——`ApplicationLoadBalancedFargateService` 自動建的 execution role 本來就帶 `AmazonECSTaskExecutionRolePolicy`（含該 action on `*`） |
| 4 | `time ( infra/cdk/deploy.sh deploy && infra/cdk/verify.sh )` 從 `agentcore` 切回 `inproc` | 總時間 **≤ 10 分鐘**，`verify.sh` 全綠 |
| 5 | `infra/cdk/verify.sh` | **甲案完成後**為五段有標號（首頁／檔位四項／Bedrock 連通／守門 **409**／chat SSE）外加一段未標號的資料隔離檢查，全部綠。今天跑只有四段有標號。**不得因本工作包出現任何新紅項** |
| 6 | 設 `CHAT_BACKEND=agentcore` 但不給 `AGENTCORE_RUNTIME_ARN`，啟動服務 | **啟動失敗**並指出缺哪個設定（不得靜默退回 `inproc`） |
| 7 | 兩種模式各 dump 一次 `done` 事件的 **key 集合**並 diff | 兩邊 key 集合相同，且與 `docs/spec/2026-09-12-chat-honesty-lamps.md` §4.4 **逐欄相同**（撰寫時 2026-09-12 是 16 個 key；那份 spec 仍在演進，**以它當下內容為準**，不要拿這裡的數字當契約）。只比四個欄位不算過 |

> 第 7 條的實際指令見 `.prospec/changes/agentcore-runtime-chat/proposal.md` 的 Success Criteria 第 4 條。
> **那是兩段式手動流程，中間隔一次 `deploy.sh deploy`（約 4 分鐘）**，不可寫成 shell 迴圈——
> 迴圈版兩次打的是同一個檔位，diff 必然為空，是一條必綠的假檢查。每次 dump 前先驗
> `/api/health` 的 `chat_backend` 真的是預期值，並把 health 輸出一起存檔當證據。

> 第 3 條刻意寫「人工逐條核」而不是 grep 判定：grep 綠不代表 policy 對，
> 而一條因為錯誤理由而通過的檢查比沒有檢查更糟。

## 備援方案

**`CHAT_BACKEND` 留在 `inproc`。**

備援不是降級版本，它就是**已拍板的主線**。乙案不成立的代價：少一句「我們用了 AgentCore」，其餘為零。
未完成的 CDK 改動一律 `git revert`，**不留半成品在 stack 裡**；已建的 Runtime 資源留著不刪
（不在請求路徑上就不影響 demo）。

⚠️ **交件夜不要對這個 stack 下 `cdk destroy`**——它會拆掉整個 `hackntpc-appeal-backend`，
連 ALB 與評審在用的網址一起消失。要清掉乙案新增的資源，做法是把那幾個 construct 從
stack 程式碼移除後再 deploy，CloudFormation 會只刪掉消失的那幾個。`destroy` 留給賽後收攤。

## 最遲放棄時刻

**D2 08:00**（評審日當天上午）。

任一命中就放棄，不再嘗試：
- 到該時點雲上的 `/ping` 還沒回過 200；
- 步驟 1 剩下的三個待查項有任一項查不到確切答案；
- 回滾計時超過 10 分鐘；
- `verify.sh` 因本工作包出現任何新紅項。

## 預估時數

**5.3 小時**（0.3 + 1.5 + 1.5 + 1 + 1）。**原本是 5.5，step 1 因兩個待查項於 2026-09-12 實跑結案而從 0.5 降到 0.3。**

⚠️ **估值，非查證值。** `docs/research/2026-09-12-agentcore.md` §7 明確記錄「查不到官方或社群給出的精確時數」，
社群教學暗示小時級但那是推論。開工後以最遲放棄時刻為準，不以這個數字為準。
