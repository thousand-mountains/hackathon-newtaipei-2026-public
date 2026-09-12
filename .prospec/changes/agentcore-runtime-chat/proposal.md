# agentcore-runtime-chat

把聊天 agent 從 FastAPI 行程內搬到 Amazon Bedrock AgentCore Runtime 託管，**同一份程式碼、換一個託管層**。

- **狀態**：**Stretch／放著**。甲案（FastAPI 內建 Strands agent）是已拍板主線，本 change **不排進必做**。
- **前置**：甲案 `backend/llm/chat.py` 必須先存在，且是純 agent 模組（不 import FastAPI）。
- **開關**：`CHAT_BACKEND=inproc|agentcore`，**預設 `inproc`**。不設定就等於本 change 不存在。
- **設計文件**：`docs/spec/2026-09-12-agentcore-runtime-design.md`（架構圖、IAM、容器合約、風險全在那）。
- **計畫**：`plans/2026-09-12-agentcore-runtime-chat.md`（superpowers 格式，含最遲放棄時刻）。

---

## Background

**為什麼現在才可以談**：`docs/architecture.md:948-949`（§9.2 原文，9/3 寫）的結論是 AgentCore 只有東京／新加坡有，
us-west-2 不在支援區——而賽方只允許 `us-east-1`／`us-west-2`（`infra/cdk/deploy.sh:38-40` 已鎖 us-west-2）。
**這條結論已被官方區域表推翻**：us-west-2 的 Runtime microVMs、Memory、Gateway、Identity、
Observability、Policy、Evaluations 全部支援，只有 payments 是 No（用不到）。
來源：<https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html>（2026-09-12 讀取）。

**為什麼還是 Stretch**：區域只是「做不做得到」。當初把它排到 Stretch 的**三條理由**都還在（下表前三列；第四列不是理由，是它的前提依賴）——

| 既有立場 | 出處 | 現況 |
|---|---|---|
| D3「AgentCore 維持 Stretch，本規格不碰」 | `docs/spec/2026-09-07-bedrock-live-nodes-design.md:51` | **不推翻**，本 change 維持 Stretch |
| 「AgentCore 是加分不是地基；30h 內 agent 間編排＝除錯地獄」 | `docs/spec/prototype-spec.md:86-89` | 本案是**單一 agent 換託管層**，不是 agent 呼叫 agent；且預設關閉 |
| 「ARM64 ＋ 自訂 HTTP contract 是額外複雜度，建議只在有餘裕時做」 | `docs/architecture.md:929-933` | **仍然成立**，是本案主要成本 |
| HACK-S-4「跑在 AgentCore Runtime 上（沒有 ask 就沒有理由做）」P3 | `backlog.md:62` | 前提（ask 聊天）由甲案滿足後，本 change 才有意義 |

**低後悔路徑**：Strands 是 AgentCore 的原生框架，Runtime 的合約就是把 Strands agent 包進
`@app.entrypoint`。所以 `chat.py` 寫成純 agent 模組之後，甲案由 FastAPI 呼叫它、乙案由 Runtime 呼叫它，
前端永遠只打 `POST /api/cases/{case_id}/chat`。評審問「有沒有用 AgentCore」兩種答案都站得住：
**地基是 Bedrock + Strands，AgentCore 是可插拔的託管層**。

---

## User Stories

### US-1: 切換開關後前端行為不變 [P2·Stretch]

**作為**承辦人（以及評審），**我想要**聊天面板的行為跟後端跑在哪裡無關，
**這樣**託管層的實驗不會變成產品風險。

驗收：
- `CHAT_BACKEND=inproc` 與 `CHAT_BACKEND=agentcore` 兩種設定下，
  `POST /api/cases/{case_id}/chat` 回傳**同樣的 SSE 事件型別序列**：`tool_call`／`tool_result`／`token`／`done`／`error`。
- `done` 事件的**欄位集合**在兩種模式下都與 `docs/spec/2026-09-12-chat-honesty-lamps.md` §4.4 **逐欄相同**（撰寫時 2026-09-12 是 16 個 key，**以該 spec 當下內容為準**；不是只比 `lamp`／`tier`／`origin`／`refs[]`——
  只比這四個會讓 `session_id`／`why`／`redirect` 在 agentcore 模式靜默消失而驗收照樣綠）。
- **前端程式碼零改動**（呼叫路徑、事件名稱、欄位名稱都沒變）。
- 驗法：同一個問題在兩種模式各打一次，`curl -N` 收到的事件型別序列逐項比對。

### US-2: 回滾 10 分鐘內完成 [P2·Stretch]

**作為**部署的人，**我想要**在評審日發現乙案不穩時能立刻退回甲案，
**這樣**託管層實驗不會吃掉唯一一次評審機會。

驗收：
- 回滾動作＝改 task definition 的 `CHAT_BACKEND` 回 `inproc` + `infra/cdk/deploy.sh deploy`，
  **不需重建映像檔、不需動前端、不需刪任何 AWS 資源**。
- 實測一次並計時，**總時間 ≤ 10 分鐘**（改設定重部署既有實測約 4 分鐘，
  見 `.prospec/changes/deploy-backend-to-aws/proposal.md:162`）。
- 回滾後 `infra/cdk/verify.sh` 全綠。甲案完成後 `verify.sh` 是**五段有標號的檢查**（首頁是 UI／檔位四項／Bedrock 連通／守門 409／chat SSE）**外加一段未標號的資料隔離檢查**；「檔位四項」是其中**一段**不是四段。

### US-3: 看得出來現在到底跑在哪一層 [P2·Stretch]

**作為**簡報的人，**我想要**`/api/health` 明講 `chat_backend` 的實際值，
**這樣**不會發生「以為在展示 AgentCore、其實是行程內執行」。

驗收：
- `GET /api/health` 回傳含 `chat_backend`，值是 `inproc` 或 `agentcore` 的**實際生效值**，不是設定檔期望值。
- `CHAT_BACKEND=agentcore` 但 `AGENTCORE_RUNTIME_ARN` 缺 → **啟動即失敗**，不得靜默退回 `inproc`。
- CONSTITUTION §1 分層誠實。

### US-4: Runtime 的權限不大於 ECS task 現在的權限 [P2·Stretch]

**作為**這個專案的技術負責人，**我想要**新增的 Runtime 執行角色跟現有 `taskRole` 一樣是白名單，
**這樣**加一個託管層不會順手把權限面放大。

驗收：
- Runtime 執行角色只含：指定那一顆模型的 `bedrock:InvokeModel`／`InvokeModelWithResponseStream`、
  指定那一個 KB 的 `bedrock:Retrieve`、自己 log group 的 logs 權限、新 ECR repo 的拉取權限。
- **本 change 新增的 statement 中**，除 `ecr:GetAuthorizationToken` 外不含 `Resource: "*"`，且不含 `bedrock:*`、`s3:*`。
  （`ecr:GetAuthorizationToken` 是 AWS 明文不支援資源層級限定的 action，寫成 repo ARN 則 Runtime 拉不到映像。）
- ⚠️ 人工核時要先列出**既存**的 `Resource: "*"` 當基線：現有 stack 用
  `ApplicationLoadBalancedFargateService`，CDK 自動建的 execution role 本來就帶
  `AmazonECSTaskExecutionRolePolicy`（內含該 action on `*`），所以無限定的 grep 在動手前就有命中。
- 驗法：`npx cdk synth` 印出 policy 人工逐條核（不依賴自動產生的 policy，
  因為 `aws/aws-cdk#35852`（<https://github.com/aws/aws-cdk/issues/35852>） 回報 L1 的 execution role policy 模板權限不足）。

---

## Edge Cases

| 情境 | 預期行為 |
|---|---|
| `CHAT_BACKEND` 沒設 | 走 `inproc`（fail-safe）。乙案等於不存在 |
| `CHAT_BACKEND=agentcore`、`AGENTCORE_RUNTIME_ARN` 缺 | **啟動即報錯**。不靜默退回——比照「忘記給 `RUN_MODE` 會靜默退化成離線重播」的教訓 |
| Runtime **開流前**打不通（不健康、權限錯、冷啟動逾時） | 回 **503**，形狀比照 `chat-honesty-lamps.md` §2.3 的 503。**不回 502**——§2.3 已把 502 定義成「該 run 是失敗的」，形狀不同的 502 會被前端誤讀成案子跑失敗、去顯示一個不存在的失敗節點 |
| Runtime **開流後**才斷（throttle、microVM 回收、session 逾時、ALB 900 秒切斷） | header 已送出、**回不了狀態碼**，所以發一個 `error` 事件後關流，`stage` 用 `transport`（值域見 `chat-honesty-lamps.md` §4.6）。不發就違反 §2.3 的紅線「拿到 `text/event-stream` 就一定至少有一個 `done` 或 `error`」 |
| 兩者皆然 | **不自動退回 inproc**。延續 D5「live 失敗不自動退回 fixture」（`docs/spec/2026-09-07-bedrock-live-nodes-design.md:53`） |
| 冷啟動導致第一次回應明顯慢 | 上台前手動打一次熱機；同一 `session_id` 內重複呼叫會重用熱機環境 |
| session 超過 8 小時或閒置 15 分鐘 | Runtime 回收 session。proxy 端要能重建 session，不可假設 `session_id` 永久有效 |
| ~~TypeScript CDK 沒有 `aws-bedrockagentcore` 模組~~ | **已排除**：2026-09-12 實跑確認存在且含 L2。若 L2 用起來卡住，退回 L1 `CfnRuntime`；再不行才放棄乙案 |
| 賽方規則對 AgentCore 有疑義 | 關掉開關即可，甲案在任何解讀下都合規 |

---

## Success Criteria

```bash
# 0) 本 change 完成後，未設 CHAT_BACKEND 時應為 inproc
#    ⚠️ 本 change 尚未實作時，/api/health 沒有 chat_backend 這個 key（backend/api/app.py 的 health），
#       這條會印 None——那是「還沒做」，不是失敗。做完才有意義。
curl -s http://<endpoint>/api/health | python3 -c "import json,sys; print(json.load(sys.stdin).get('chat_backend'))"
#    期望 inproc

# 1) 切到 agentcore 後，事件型別的出現順序不變（US-1）
#
#    ⚠️ 不要用 `sort -u`——那驗的是集合，順序錯亂照樣綠，而 lamps §4.7 對順序是有保證的
#       （done/error 互斥且必為最後一個；tool_call 與 tool_result 成對）。
#    ⚠️ 也不要原樣比對整串：`token` 的筆數每次不同（§4.7 明說可以是零筆），必然 diff。
#       用 `uniq` 把連續重複的同型別事件塌成一筆，順序保留、筆數差異被吸收。
curl -N -X POST http://<endpoint>/api/cases/synthetic-ordinary-01/chat \
     -H 'content-type: application/json' -d '{"run_id":"...","message":"本案的法條依據是哪幾條？"}' \
     | grep -o '^event: .*' | uniq > /tmp/events-<檔位>.txt
diff /tmp/events-inproc.txt /tmp/events-agentcore.txt && echo "事件順序一致"
#    期望 diff 空輸出。最後一筆必須是 done 或 error，且兩邊相同。
#    若兩次模型挑了不同的工具（tool_call 序列不同），那是模型的非決定性不是缺陷——
#    重跑一次，或改成逐條核對 lamps §4.7 的順序保證。

# 2) health 說實話（US-3）
curl -s http://<endpoint>/api/health | python3 -m json.tool | grep chat_backend
#    期望 agentcore

# 3) 回滾（US-2）：改回 inproc → deploy → verify，全程計時 ≤ 10 分鐘
time ( infra/cdk/deploy.sh deploy && infra/cdk/verify.sh )

# 4) done 的欄位集合逐欄相同（US-1 第二條；只比四個欄位不算過）
#
#    ⚠️ 這條**不能寫成 shell 迴圈**。切換檔位要改 task definition 再 deploy（約 4 分鐘），
#       塞不進迴圈裡。先前的寫法 `for m in inproc agentcore` 只是用 $m 命名輸出檔，
#       兩次 curl 打的是同一個部署、同一個檔位 —— diff 必然為空，是一條必綠的假檢查。
#       這正是本文件一再強調「只比四個欄位會靜默通過」的那種錯，寫在驗收裡格外諷刺。
#
#    正確流程是兩段手動的，中間隔一次部署：

dump_done() {   # $1 = 期望的檔位名，用來命名輸出檔並先驗檔位真的對
  got="$(curl -s http://<endpoint>/api/health \
        | python3 -c "import json,sys; print(json.load(sys.stdin).get('chat_backend'))")"
  [ "$got" = "$1" ] || { echo "檔位是 $got 不是 $1，停"; return 1; }
  curl -s http://<endpoint>/api/health > "/tmp/health-$1.json"   # 檔位證據一起存檔
  curl -N -X POST http://<endpoint>/api/cases/synthetic-ordinary-01/chat \
       -H 'content-type: application/json' -d '{"run_id":"...","message":"本案的法條依據是哪幾條？"}' \
  | tee "/tmp/sse-$1.txt" \
  | awk '/^event: done$/{f=1;next} f&&/^data: /{sub(/^data: /,"");print;exit}' \
  | python3 -c "import json,sys; print('\n'.join(sorted(json.load(sys.stdin))))" > "/tmp/done-$1.keys"
}

# 第一段：目前是 inproc（或先把 CHAT_BACKEND 設成 inproc 部署一次）
dump_done inproc

# ── 中間這三步是手動的，不可省 ──
#   1. 改 infra/cdk/lib/appeal-backend-stack.ts 的 CHAT_BACKEND 為 agentcore
#   2. infra/cdk/deploy.sh deploy          （約 4 分鐘）
#   3. 等 ECS service 穩定（desiredCount=1 的新 task 進 RUNNING 且 target healthy）

# 第二段
dump_done agentcore

diff /tmp/done-inproc.keys /tmp/done-agentcore.keys && echo "key 集合一致"
#    期望：diff 空輸出，且 key 數與 chat-honesty-lamps §4.4 當下的 done 相同
#         （撰寫時 2026-09-12 是 16 個；那份 spec 仍在演進，以它當下內容為準）
#    /tmp/health-*.json 是「兩次真的跑在不同檔位」的證據，不要只留 diff 結果

# 5) 權限白名單（US-4）
#
#    ⚠️ 不要用 `cdk synth | grep 'bedrock-agentcore'`——**它抓不到 Runtime 執行角色**：
#       該角色的 statement 內容是 bedrock:InvokeModel／ecr:*／logs:*，沒有 bedrock-agentcore 這個字串；
#       CFN 資源型別是 AWS::BedrockAgentCore::Runtime，大小寫也不匹配。
#       那條只會印出 taskRole 的新 action，而 US-4 要核的正是 Runtime 角色。
#
#    改成從 template 結構定位：把每個 IAM 角色連同它的 policy 全部印出來，人工核新增的那幾個。
cd infra/cdk && npx cdk synth --json > /tmp/tpl.json
python3 - <<'EOF'
import json
res = json.load(open('/tmp/tpl.json'))['Resources']
rt = [k for k,v in res.items() if v['Type'] == 'AWS::BedrockAgentCore::Runtime']
print('Runtime 資源:', rt or '（尚未實作，此檢查要等資源建好才有意義）')
for k in rt:
    print('  它引用的執行角色:', res[k]['Properties'].get('RoleArn') or res[k]['Properties'])
for k, v in res.items():
    if v['Type'] in ('AWS::IAM::Role', 'AWS::IAM::Policy'):
        doc = v['Properties'].get('PolicyDocument') or v['Properties'].get('Policies')
        print('---', k, v['Type'])
        print(json.dumps(doc, ensure_ascii=False, indent=1))
EOF
#    人工核**新增的那幾個角色**：除 ecr:GetAuthorizationToken 外無 Resource: "*"，
#    且無 bedrock:*、s3:*。既存命中的基線見 US-4 的說明（ECS execution role 本來就有）。
```

**任一條不過 → 開關留在 `inproc`，不交付乙案。** 乙案不成立不影響任何既有交付項。

---

## Related Modules

| 模組 | 關係 |
|---|---|
| `backend/llm/chat.py`（甲案新檔） | **前提**。必須是純 agent 模組（不 import FastAPI），**且 `classify_answer()`／`RefBook`／`NUMERIC_Q` 也在這裡**——Runtime 要靠它在自己那一端算燈號 |
| `backend/api/chat.py`（甲案新檔） | 加 `CHAT_BACKEND` 分派。`agentcore` 分支**只做 bytes 轉送，不重組、不補算燈號**；唯一例外是 `?stream=0`（見下）。**payload 分區要在這裡先讀好餵進去**，Runtime 不碰 runstore |
| `?stream=0` 降級路徑 | `chat-honesty-lamps.md` §2.2 的凍結契約。`agentcore` 模式下**由 proxy 聚合**成單一 JSON——這是「不重組」原則的具名例外。不做的話，開一個 Stretch 開關就讓甲案一條已驗綠的 AC 失效 |
| `backend/requirements.txt` | 可能要放寬 `boto3~=1.35.0` 的版本釘（見 OQ7）。**這個檔在甲案 AC9 的觀察範圍內**，改動要跟甲案對齊 |
| `backend/agentcore_entry.py` | **新檔**：`@app.entrypoint` 包 `build_chat_agent()`，**並在 Runtime 內呼叫 `classify_answer()` 把 `done` 組好再吐**（欄位集合以 `chat-honesty-lamps.md` §4.4 當下內容為準，撰寫時是 16 個 key；見設計文件 §4.4「燈號在哪裡算」）。少了這步，agentcore 模式會掉燈號 |
| `backend/Dockerfile.agentcore` | **新檔**：ARM64、`0.0.0.0:8080`、`/invocations` + `/ping` |
| `backend/Dockerfile` | **不動**（ECS 那條線維持 X86_64） |
| `backend/config/settings.py` | 加 `chat_backend`、`agentcore_runtime_arn` |
| `infra/cdk/lib/appeal-backend-stack.ts` | 加 Runtime／ECR／執行角色；`taskRole` 加一條 statement（`:86-102` 旁）；`environment` 加兩個 key（`:178-188`） |
| `infra/cdk/verify.sh` | **沿用甲案已排入的第 5 段 chat SSE 檢查**，不另外加一條；改成在 `CHAT_BACKEND=agentcore` 檔位下再跑一次 |
| `docs/architecture.md:935-946`（§9.2 標題與新加註） | 區域結論已就地加註更新，`:948-949` 的過時原文保留供追溯 |

---

## Open Questions

1. ~~`bedrock-agentcore:InvokeAgentRuntime` action 名稱與 ARN 形狀~~ **已結案（2026-09-12 實跑）**：
   aws-cdk-lib 的 `aws-bedrockagentcore/lib/runtime/perms.js`（`npm ci` 後在 `infra/cdk/node_modules/` 底下，不在 git 裡） 的 `RUNTIME_INVOKE_PERMS` 就是這個字串。
   而且不必自己寫——L2 `Runtime` 的 `grantInvokeRuntime()` 會連 ARN 一起產生（設計文件 §6.1）。
2. ~~TypeScript 端有沒有 `aws-bedrockagentcore`~~ **已結案（2026-09-12 實跑）**：有，而且含 **L2 `Runtime` class**
   與 grant helper，不只 L1。`cd infra/cdk && npm ci && ls node_modules/aws-cdk-lib | grep -i agentcore` → `aws-bedrockagentcore`。
3. **從零到能被 HTTP 呼叫的實際時數**——**查不到**官方或社群數字，社群教學暗示小時級但那是推論。
   計畫的 5.3 小時是估值不是查證值。
4. **`boto3~=1.35.0` 有沒有 `bedrock-agentcore` client**——`backend/requirements.txt` 釘的是
   2024-08 的版本，早於 AgentCore 存在。沒有的話 `boto3.client('bedrock-agentcore')` 會拋
   `UnknownServiceError`，proxy 一行都跑不起來。驗法：`pip download 'boto3~=1.35.0'` 後試建 client。
5. **賽方規則原文**對「僅限 AWS 服務提供之基礎模型」的措辭（**技術前提裡唯一需要 Ci 去對外部文件查證的一條**；下面第 8 條是另一回事，那是要不要做的 Go／No-Go）——AgentCore 官方定位是編排層不是模型，
   查無 AWS 文件把兩者掛鉤，但**這不是賽方文件**，需 Ci 對照原文。
6. ~~與甲案 S7 的路線衝突~~ **已解，無須拍板**。甲案兩份原本把 stretch 做法寫成
   `agentcore launch`（路線 B），已於 2026-09-12 改為指向本 change。現在只有路線 A 一條。
7. **`InvokeAgentRuntime` 的 SSE 行為**——「設 `Content-Type: text/event-stream` 即以 SSE 回傳」
   讀自官方 CLI reference 與 runtime-invoke-agent 文件（`docs/research/2026-09-12-agentcore.md` §4），
   **但本團隊未實測**。這條是整個串流設計的地基，實作前直接試打一次確認。
8. **要不要做（Go／No-Go）**——本 change 預設就是不做；Ci 決定啟用才開工。這不是技術待查項，是排程決定。

---

## Constitution Check

| 原則 | 本 change 的對應 |
|---|---|
| §1 分層誠實 | US-3：`/api/health` 報實際生效的 `chat_backend`；Runtime 失敗不自動退回（開流前 503、開流後 `error` 事件，見 Edge Cases） |
| §2 引用必可驗 | 燈號規則依 `docs/spec/2026-09-12-chat-honesty-lamps.md`，**不因託管層改變** |
| §4 規則引擎零 LLM | 聊天 agent 不得計算期限，數字類問題導向規則引擎；換託管層不影響 |
| §5 plan 先行 | `plans/2026-09-12-agentcore-runtime-chat.md` |
| §6 資料隔離 | ARM64 映像的建置 context exclude 要獨立確認；賽方資料集不得進 context |
| §7 secret | Runtime 走執行角色臨時憑證，無長期金鑰；ARN 只進 task definition 不進程式 |
| §8 30h 紀律 | **預設關閉、可插拔、有最遲放棄時刻**。放棄的代價是零 |

---

## Next Steps

**全部 ⏸ 暫不開工。** Ci 拍板且甲案完成、且時間有剩才動。

| # | 步驟 | 狀態 |
|---|---|---|
| 0 | 甲案 `backend/llm/chat.py` 完成且是純 agent 模組 | ⏸ 前提 |
| 1 | 核 Open Questions **4、7**（boto3 版本、**SSE 行為＝串流設計的地基**）。**OQ1／OQ2 已於 2026-09-12 實跑結案。** OQ5 需 Ci 對照賽方文件 | ⏸ |
| 2 | `Dockerfile.agentcore` + `agentcore_entry.py`，本機 ARM64 跑通 `/ping` | ⏸ |
| 3 | CDK 加 Runtime／ECR／執行角色，`cdk synth` 人工核 policy | ⏸ |
| 4 | `backend/api/chat.py` 加 `agentcore` 分派與 SSE 轉送 | ⏸ |
| 5 | 部署、跑 Success Criteria **六條**（`# 0)`～`# 5)`）、計時回滾一次 | ⏸ |
