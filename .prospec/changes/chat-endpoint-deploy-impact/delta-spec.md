# Delta Spec: chat-endpoint-deploy-impact

> REQ ID 格式：`REQ-{MODULE}-{NUMBER}`
> 本 change 只動**部署面**。聊天端點本身的需求在 `chat-ask-agent` 的 delta-spec（`REQ-CHAT-*`）。

## ADDED

### REQ-DEPLOY-010: 部署驗收必須能證明聊天在雲上真的在串流

**Description:**

`infra/cdk/verify.sh` 新增第 5 段檢查，插在第 4 段（C 型守門 409，`:155-165`）之後、「資料隔離」區塊（`:167`）之前。

這一段存在的理由是**本機驗不出來的東西只有雲上驗得出來**：ALB 緩衝、`X-Accel-Buffering` header 掉了、SSE generator 沒 flush——這些壞法在本機（沒有 ALB）全都看不到，而且在雲上**全都會回 HTTP 200**。既有四段沒有任何一段碰得到長連線。

**Acceptance Criteria:**

1. 指令長這樣，對既有合成案例 `synthetic-ordinary-01` 發送：
   ```bash
   curl -N -s --max-time 60 -X POST "$base/api/cases/synthetic-ordinary-01/chat" \
        -H 'content-type: application/json' -d '{"message":"這個案子的受理期間怎麼算？"}' | head -c 2000
   ```
2. **判準是輸出中至少出現一行 `^data:`**。HTTP 200 但沒有 `data:` 行 → 判失敗（`bad`）。只比對狀態碼不算滿足本需求。
3. 使用既有輔助函式 `ok()`（`verify.sh:31`）與 `bad()`（`:30`，自帶 `fail=1`），不另發明 exit 機制。
4. **反向驗證已執行**：把路徑換成 `/api/cases/does-not-exist/chat` 跑一次，第 5 段判失敗。未做此步不得宣稱本需求完成。
5. `bash -n infra/cdk/verify.sh` 零輸出。
6. 不新增測資：只用既有合成案例。

**Priority:** High

---

### REQ-DEPLOY-011: 「部署零改動」必須是可執行的驗收，不是文件裡的宣稱

**Description:**

甲案沿用 `BEDROCK_MODEL_ID_DRAFT`，因此 task role 的 `InvokeNamedModelsOnly`（`infra/cdk/lib/appeal-backend-stack.ts:86-92`，來源是 `invokeArnsFor()` `:33-47` 與 `invokeResources` `:81-84`）已涵蓋聊天所需權限，CDK 不需改動；`boto3~=1.35.0` 與 `strands-agents>=1.15.0` 已在 `backend/requirements.txt:18-19`，不需加套件。

本需求要求把這個結論變成一條會失敗的檢查——若甲案實際上動了 CDK，它必須被抓出來，而不是靠人回頭讀文件發現。

**Acceptance Criteria:**

1. `git diff --stat origin/main -- infra/cdk/lib/ infra/cdk/bin/` **零輸出**。有輸出即代表前提破了，要回頭查是哪裡走偏。
2. `git diff -- backend/requirements.txt` 只包含 `:19` 那句註解的修正（見 REQ-DEPLOY-012），**不得有任何新增套件行**。
3. `/opt/homebrew/bin/python3 backend/tests/run_all.py` 全綠：374/374（另 2 項因語料不在 repo 被 harness 照實標示略過）。
   ⚠️ 必須用 `/opt/homebrew/bin/python3`；macOS 的 `/usr/bin/python3` 是 3.9，腳本自己會擋。

**Priority:** High

---

### REQ-DEPLOY-012: 換模型 id 時要改哪裡，寫死在文件而非臨場找

**Description:**

現在不做。但「臨場被迫換第三顆模型」時，漏改 IAM 的後果是**線上 `AccessDenied` 而本機完全正常**——本機走 Workshop Studio 臨時憑證，權限比 task role 寬，這個差異在本機一次都不會暴露。

**Acceptance Criteria:**

1. 改法三處寫在 `plan.md` 步驟 5，逐條可照打：`AppealBackendStackProps`（`appeal-backend-stack.ts:11-20`）加欄位、`invokeResources`（`:81-84`）加一行、`bin/app.ts:43-44` 附近加 `required('BEDROCK_MODEL_ID_CHAT')`。
2. 附**不需實際部署就能驗**的指令：`cdk synth` 後解析 template，確認 `bedrock:InvokeModel` statement 的 `Resource` 多了第三顆模型的 ARN。
3. 附 `cdk synth` 的前置條件（需 `cdk.context.json` 與 `CDK_DEFAULT_ACCOUNT`，否則停在 `StackAccountRegionNotSpecified`）——這個坑已實際踩過。
4. `backend/requirements.txt:19` 的註解修正為目錄級說法：實際被強制的是整個 `backend/llm/`（`backend/tests/run_all.py:423` 的錯誤訊息即「只有 `backend/llm/` 可以」），不是單一 `llm/client.py`。加 `backend/llm/chat.py` 後原註解會誤導下一個人以為自己違規。

**Priority:** Medium

---

### REQ-DEPLOY-013: 交件前收窄 ALB 白名單是一個有清單、有回滾、有前置判斷的動作

**Description:**

賽方 2026-09-12 現場要求「對外開放連線請 allow 指定四組 IP」。功能已併入 main（`e27d2a7`）且**預設關閉**——不設 `ALB_ALLOWED_CIDRS` 即 `0.0.0.0/0`。Ci 拍板：現在不收窄，2026-09-13 交件前才收窄。

這是一個**有時限、只留在對話裡明天一定會漏**的動作，所以必須落在部署手冊裡。

**Acceptance Criteria:**

1. `backend/DEPLOY.md` §3.6 說明機制與 `AlbIngress` 的查法；§6 是交件前檢查清單。✅ 完成於 `e27d2a7`。
2. 清單含：觸發時機（2026-09-13 交件前）、指令（`ALB_ALLOWED_CIDRS=<四組CIDR> ./deploy.sh deploy`）、驗證（`describe-stacks` 查 `AlbIngress` 是否變成那四組）、回滾（不帶該變數重跑 deploy）。
3. **四組 CIDR 的實際值不進 repo**，標「交件前向賽方確認並填入」。寫死會被抄到過期的值。
4. 清單含風險警語：那四組是**會場出口 IP**，收窄後會場外連不進來，包含交件後才自己點開網址的評審。**先確認評審在哪裡看，問不到就不收窄。**
5. 收窄後必須重跑完整 `verify.sh`（含第 5 段）——收窄動的是 SG，最容易壞的就是自己也連不進去。
6. 目前狀態驗證：`AlbIngress` output 現在必須是 `0.0.0.0/0`，代表沒有誤啟用。

**Priority:** High

---

## MODIFIED

### REQ-DEPLOY-00X（既有部署驗收）：驗收段數由 4 段增為 5 段

原 `verify.sh` 四段（首頁靜態資源 `:33-67`／`/api/health` 四項 `:69-106`／真打 Bedrock 端到端 `:108-153`／C 型守門 409 `:155-165`）全部保留、判準不變，僅**新增**第 5 段。

**不得**因為加了第 5 段而放寬任何既有一段的判準。既有兩條紅線（`/api/health` 四項任一不對即是離線重播不得交件；C 型 submit 沒回 409 即 P0）維持不變。

---

## REMOVED

_No removals in this change._
