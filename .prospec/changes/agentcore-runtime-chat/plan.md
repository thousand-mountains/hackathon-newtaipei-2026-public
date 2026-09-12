# Implementation Plan: agentcore-runtime-chat

> **Stretch／預設關閉。** 不排進必做；備援就是「留在 `inproc`」，代價為零。
> 架構細節見 `docs/spec/2026-09-12-agentcore-runtime-design.md`。

## Overview

把甲案已經寫好的 Strands 聊天 agent（`backend/llm/chat.py`）搬到 Bedrock AgentCore Runtime 託管，
**不改 agent 程式碼、不改前端呼叫路徑**。FastAPI 的 `POST /api/cases/{case_id}/chat` 依
`CHAT_BACKEND` 環境變數決定「行程內執行」或「proxy 到 Runtime 並轉送 SSE」。

四個關鍵設計決策：

1. **部署走 CDK 併入現有 stack**（2026-09-12 查證後用 L2 `Runtime`），不走 `agentcore launch` 獨立管線。
   理由是回滾——路線 A 的回滾是 `git revert` + 一次 `deploy.sh deploy`，
   路線 B 要手動追刪散落資源，而評審日只有一次機會。（設計文件 §3.2）
2. **`chat.py` 是純 agent 模組，不 import FastAPI。** 這是乙案能成立的全部前提；
   一旦它綁上 FastAPI，乙案就得改程式碼，那就不是可插拔了。（設計文件 §4.4）
3. **Runtime 執行角色手刻，不用自動產生的 policy**（呼叫端那條例外：用 L2 的 `grantInvokeRuntime()`，見設計文件 §6.1）。 `aws/aws-cdk#35852`（<https://github.com/aws/aws-cdk/issues/35852>） 回報 L1 的
   execution role policy 模板權限不足，不能信任預設。（設計文件 §3.3）
4. **Runtime 不讀 runstore，case payload 由 HTTP 層先讀好夾帶進 `/invocations`。**
   `build_payload()` 住在 `backend/orchestrator/graph.py`、吃的是從 ECS task 本機檔案
   `backend/output/runs/{run_id}.json` 讀出的 `CaseState`；Runtime 是另一個 microVM，
   沒有那個檔，映像裡也沒有 `backend/orchestrator/`。不定這個契約，`read_case` 這個 tool
   **必然壞掉**。甲案已依此調整，乙案沿用。（設計文件 §4.4）

## Affected Modules

| Module | Impact | Changes |
|--------|--------|---------|
| `backend/llm/chat.py` | **None（前提）** | 不改。必須是純 agent 模組（不 import fastapi），且 `classify_answer()`／`RefBook`／`NUMERIC_Q` 也在這裡——`agentcore_entry.py` 要靠它在 Runtime 內算燈號 |
| `backend/agentcore_entry.py` | High（新檔） | `@app.entrypoint` 包 `build_chat_agent()`，實作 `/invocations` + `/ping`，**並在 Runtime 內呼叫 `classify_answer()` 組出與 `chat-honesty-lamps.md` §4.4 逐欄相同的 `done`**（撰寫時 2026-09-12 是 16 個 key；那份 spec 仍在演進，以它當下內容為準） |
| `backend/Dockerfile.agentcore` | High（新檔） | ARM64、`0.0.0.0:8080`，只裝 agent 需要的東西 |
| `backend/Dockerfile` | None | **不動**，ECS 那條線維持 X86_64 |
| `backend/api/chat.py` | Medium | 依 `settings.chat_backend` 分派；`agentcore` 時呼叫 `InvokeAgentRuntime` 並**只做 bytes 轉送**（不重組、不補算燈號） |
| `backend/config/settings.py` | Low | 加 `chat_backend`（預設 `inproc`）、`agentcore_runtime_arn` |
| `backend/api/app.py` | Low | `/api/health` 加 `chat_backend` 實際生效值 |
| `infra/cdk/lib/appeal-backend-stack.ts` | High | 加 Runtime／ECR／Runtime 執行角色；`taskRole` 加第三條 statement；`environment` 加兩個 key |
| `infra/cdk/verify.sh` | Low | **沿用甲案第 5 段 chat SSE 檢查**，改成在 `agentcore` 檔位下再跑一次；不另外加一條 |
| 前端 | **None** | 零改動。呼叫路徑與事件格式都沒變 |

## Implementation Steps

1. **核待查事項（不得憑推論寫程式）** — 0.3 h。**五項裡兩項已於 2026-09-12 實跑結案，剩三項。**
   - ~~`ls node_modules/aws-cdk-lib | grep -i agentcore`~~ **已結案（2026-09-12 實跑）**：
     `aws-bedrockagentcore` 存在，且含 **L2 `Runtime` class** 與 grant helper，不只 L1。
     直接用 L2；卡住才退 L1 `CfnRuntime`。
   - ~~核 `bedrock-agentcore:InvokeAgentRuntime` 的 action 名稱與 ARN 形狀~~ **已結案**：
     aws-cdk-lib 的 `aws-bedrockagentcore/lib/runtime/perms.js`（`npm ci` 後在 `infra/cdk/node_modules/` 底下，不在 git 裡） 的 `RUNTIME_INVOKE_PERMS`。
     而且改用 `runtime.grantInvokeRuntime(taskRole)` 之後，這兩個值都不必自己寫。
   - 確認 `bedrock-agentcore` Python SDK 的 import 名稱與 `@app.entrypoint` 的 API。
   - 核 `InvokeAgentRuntime` 的 SSE 行為（設 `Content-Type: text/event-stream` 是否即回
     `data: {...}`）。**這條是整個串流設計的地基**，比 IAM action 名更載重。
   - 核 `boto3~=1.35.0`（`backend/requirements.txt` 現在的釘法，2024-08 版本）有沒有
     `bedrock-agentcore` client：`pip download 'boto3~=1.35.0'` 後試 `boto3.client('bedrock-agentcore')`。
     **沒有就會拋 `UnknownServiceError`**，步驟 4 的 proxy 一行都跑不起來。要放寬版本釘就得改
     `requirements.txt`，而那個檔在甲案 AC9 的觀察範圍內，改動要跟甲案對齊。

   > 註（不是待查項）：甲案 S7 原本寫 `agentcore launch`（路線 B），**已於 2026-09-12
   > 改為指向本計畫**，衝突已解，**無須拍板**。見設計文件 §3.2。

2. **本機把 ARM64 容器跑起來** — 1.5 h
   - 寫 `backend/agentcore_entry.py` 與 `backend/Dockerfile.agentcore`。
   - `docker buildx build --platform linux/arm64` → `docker run --platform linux/arm64`。
   - **先打 `GET /ping` 拿到 200 再談別的**；再打 `POST /invocations` 收到 SSE。
   - 賽方資料集不得進建置 context（CONSTITUTION §6），exclude 清單獨立確認一次。

3. **CDK 加資源** — 1.5 h
   - Runtime、ARM64 專用 ECR repo、Runtime 執行角色（重用 `invokeArnsFor()`，
     `appeal-backend-stack.ts` 的 `invokeArnsFor()`，跨區 inference profile 要雙 ARN）。
   - `taskRole` 加 `bedrock-agentcore:InvokeAgentRuntime`（資源限定自己那一個 Runtime ARN）。
   - `environment` 加 `CHAT_BACKEND` 與 `AGENTCORE_RUNTIME_ARN`。
   - `npx cdk synth` 印出 policy **人工逐條核**：除 `ecr:GetAuthorizationToken` 外無 `Resource: "*"`，
     且無 `bedrock:*`、`s3:*`。既存命中的基線見本檔驗收指令區塊的同一條說明。

4. **後端分派與 SSE 轉送** — 1 h
   - `settings.py` 加兩個設定；`chat_backend` 值不認得就走 `inproc`（fail-safe）。
   - `CHAT_BACKEND=agentcore` 但 ARN 缺 → **啟動即報錯**，不靜默退回。
   - `backend/api/chat.py` 的 `agentcore` 分支：`InvokeAgentRuntime` 設
     `Content-Type: text/event-stream`，逐塊轉送不重組，保留 `X-Accel-Buffering: no`。
   - Runtime 失敗**分兩種**（設計文件 §8.1）：**開流前**打不通回 **503**（形狀比照
     `chat-honesty-lamps.md` §2.3 的 503，**不可重用 502**——§2.3 已把 502 定義成
     「該 run 是失敗的」）；**開流後**才斷則發 `error` 事件、`stage` 用 `transport`（值域見 `chat-honesty-lamps.md` §4.6，**不自行造值**）
     （header 已送出，回不了狀態碼）。兩者都**不自動退回 `inproc`**（延續 D5）。
   - `?stream=0`（`chat-honesty-lamps.md` §2.2 的凍結契約）在 `agentcore` 模式下
     **由 proxy 聚合成單一 JSON**——這是「不重組」原則的具名例外，不做會讓甲案一條已驗綠的 AC 失效。
   - **payload 分區在這裡先讀好餵進 agent**，Runtime 不碰 runstore（設計文件 §4.4）。
   - `/api/health` 加 `chat_backend`。

5. **部署、驗收、計時回滾** — 1 h
   - `infra/cdk/deploy.sh deploy`。
   - 跑 proposal 的 Success Criteria **六條**（`# 0)`～`# 5)`，第 4 條是 `done` key diff、第 5 條是權限白名單）。
   - **實際回滾一次並計時**，必須 ≤ 10 分鐘（US-2 的驗收條件，不能只是宣稱）。
   - 在 `agentcore` 檔位下跑一次甲案的 verify.sh 第 5 段（chat SSE）。

## 驗收指令

**七段**（US-1 順序／US-3 health／US-1 第二條 key 集合／未設 CHAT_BACKEND／US-4 權限／US-2 回滾計時／既有紅線）。
對應 proposal 的 Success Criteria 六條（`# 0)`～`# 5)`）加一段既有紅線的 verify.sh。

```bash
# US-1 事件型別的出現順序一致
#   不用 sort -u（那驗集合、順序錯亂照樣綠，而 lamps §4.7 對順序有保證）；
#   也不原樣比整串（token 筆數每次不同，必然 diff）。用 uniq 塌連續重複。
curl -N -X POST http://<endpoint>/api/cases/synthetic-ordinary-01/chat \
     -H 'content-type: application/json' \
     -d '{"run_id":"<rid>","message":"本案的法條依據是哪幾條？"}' \
  | grep -o '^event: .*' | uniq > /tmp/events-<檔位>.txt
diff /tmp/events-inproc.txt /tmp/events-agentcore.txt && echo "事件順序一致"

# US-3 health 說實話
curl -s http://<endpoint>/api/health | python3 -m json.tool | grep chat_backend

# US-1 第二條：done 的 key 集合逐欄相同
#   ⚠️ 不可寫成 shell 迴圈——切檔位要改 task definition 再 deploy（約 4 分鐘）。
#      迴圈版兩次打同一個檔位，diff 必然為空，是一條必綠的假檢查。
#   完整的兩段式流程與 dump_done() 見 proposal 的 Success Criteria 第 4 條，照那份跑。
#   每次 dump 前先驗 /api/health 的 chat_backend 真的是預期值，並把 health 輸出一起存檔。
diff /tmp/done-inproc.keys /tmp/done-agentcore.keys && echo "key 集合一致"

# 未設 CHAT_BACKEND 時應為 inproc（proposal Success Criteria 第 0 條）
curl -s http://<endpoint>/api/health \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('chat_backend'))"
#   期望 inproc。本 change 未實作時這裡印 None，那是「還沒做」不是失敗。

# US-4 權限白名單（人工核，不是 grep 綠就算過）
#   ⚠️ 不要 grep 'bedrock-agentcore'——抓不到 Runtime 執行角色（它的 statement 是
#      bedrock:InvokeModel／ecr:*／logs:*，沒有那個字串；CFN 型別 AWS::BedrockAgentCore::Runtime
#      大小寫也不匹配）。完整做法見 proposal 的 Success Criteria 第 5 條，從 template 結構定位。
#   除 ecr:GetAuthorizationToken 外不得有 Resource: "*"（那個 action AWS 明文不支援資源限定）。
#   先列出既存命中當基線：ApplicationLoadBalancedFargateService 自動建的 execution role
#   本來就帶 AmazonECSTaskExecutionRolePolicy（含該 action on *），動手前就有命中。
cd infra/cdk && npx cdk synth --json > /tmp/tpl.json   # 之後照 proposal 那段 python 逐角色印出

# US-2 回滾計時
time ( infra/cdk/deploy.sh deploy && infra/cdk/verify.sh )

# 既有紅線不得因本 change 破掉
infra/cdk/verify.sh          # 甲案完成後為五段有標號（首頁／檔位四項／Bedrock 連通／守門 409／chat SSE）
                             # 外加一段未標號的資料隔離檢查。全部綠才算
```

## 備援方案

**留在 `CHAT_BACKEND=inproc`。**

這不是降級版本，是**主線本身**——甲案是已拍板的交付項，乙案從來就不在必做清單上。
放棄乙案的代價是：少一個「我們用了 AgentCore」的加分說法，其餘為零。
Runtime 資源留著不刪（不在請求路徑上就不影響 demo）。
**交件夜不要對這個 stack 下 `cdk destroy`**——它會拆掉整個 `hackntpc-appeal-backend`，
連 ALB 與評審在用的網址一起消失。要清乙案新增的資源，是把那幾個 construct 從 stack
程式碼移除後再 deploy（CloudFormation 只刪掉消失的資源）。`destroy` 留給賽後收攤。

## 最遲放棄時刻

**D2 08:00**（決賽第二天上午，評審日當天）。

判準（任一命中就放棄，不再嘗試）：
- 到該時點 `/ping` 還沒在雲上拿到 200；
- 或步驟 1 剩下的三個技術待查項有任一項查不到確切答案（含 SSE 行為與 boto3 版本那兩條）；
- 或回滾計時超過 10 分鐘；
- 或 `verify.sh` 因本 change 出現任何新的紅項。

放棄＝把 `CHAT_BACKEND` 留在 `inproc`、把未完成的 CDK 改動 `git revert`，**不留半成品在 stack 裡**。

## 預估時數

**5.3 小時**（0.3 + 1.5 + 1.5 + 1 + 1）。**原本是 5.5，step 1 因 T1／T3 於 2026-09-12 實跑結案而從 0.5 降到 0.3。**

⚠️ **這是估值不是查證值。** `docs/research/2026-09-12-agentcore.md` §7 明確記錄「查不到官方或社群給出的精確時數」，
社群教學暗示小時級，但那是推論。真的開工時以最遲放棄時刻為準，不以這個數字為準。

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| ~~TypeScript CDK 沒有 `aws-bedrockagentcore` 模組~~ | **已排除** | 2026-09-12 實跑確認存在且含 L2 `Runtime` 與 grant helper。殘餘風險降為「L2 用起來卡住」，退路是 L1 `CfnRuntime` |
| ~~`bedrock-agentcore:InvokeAgentRuntime` action 名稱猜錯~~ | **已排除** | 2026-09-12 從 `perms.js` 的 `RUNTIME_INVOKE_PERMS` 查證，名稱正確；而且改用 `grantInvokeRuntime()` 之後不必自己寫 |
| ARM64 建置是第二條管線，Apple Silicon 上要 `buildx` | Medium | 本機 `docker run --platform linux/arm64` 打通 `/ping` 再推 |
| L1 execution role policy 模板權限不足（`aws/aws-cdk#35852`） | Medium | 角色全手刻 + `cdk synth` 人工核 |
| 冷啟動讓現場第一次回應明顯慢 | Medium | 上台前手動打一次熱機；同 `session_id` 重用熱機環境 |
| 多一跳 proxy，SSE 中斷點變多 | Medium | 在 `agentcore` 檔位下跑一次甲案 verify.sh 的第 5 段（chat SSE）；開流前失敗回 503、開流後發 `error` 事件，都不假裝成功（設計文件 §8.1） |
| session 8 小時上限、閒置 15 分鐘回收 | Low | proxy 端能重建 session，不假設 `session_id` 永久有效 |
| 賽方規則對 AgentCore 有疑義 | Low | 開關關掉即可；甲案在任何解讀下都合規 |
| `read_case` 在 Runtime 裡拿不到 runstore（ImportError） | **High** | §4.4 的 payload 契約：分區由 HTTP 層先讀好夾帶進 `/invocations`；`Dockerfile.agentcore` 不需要也不該 COPY `backend/orchestrator/` |
| `boto3~=1.35.0` 可能沒有 `bedrock-agentcore` client | **High** | 步驟 1 的第 5 個待查項先查；沒有就得放寬 `requirements.txt` 的版本釘，而那個檔在甲案 AC9 的觀察範圍內 |
| 建置 context 洩漏賽方資料集 | **紅線** | ARM64 asset 的 exclude 獨立確認；CONSTITUTION §6 |
