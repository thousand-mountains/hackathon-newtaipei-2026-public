# Delta Spec: chat-endpoint-deploy-impact

> REQ ID 格式：`REQ-{MODULE}-{NUMBER}`
> 本 change 只動**部署面**。聊天端點本身的需求在 `chat-ask-agent` 的 delta-spec（`REQ-CHAT-*`）。
> ⚠️ 本文件引用的 `.prospec/changes/chat-ask-agent/*` 與 `docs/spec/2026-09-12-chat-honesty-lamps.md` **尚未進 git 且正在被編輯**，因此指向它們的引用**一律用內容錨點（搜某個字串）而非行號**。指向已 commit 檔案的行號則保留。

## ADDED

### REQ-DEPLOY-010: 部署驗收必須能證明聊天在雲上真的在串流

**Description:**

`infra/cdk/verify.sh` 新增第 5 段檢查，插在第 4 段（C 型守門 409，`:155-165`）之後、「資料隔離」區塊（`:167`）之前。

這一段存在的理由是**本機驗不出來的東西只有雲上驗得出來**：ALB 緩衝、`X-Accel-Buffering` header 掉了、SSE generator 沒 flush——這些壞法在本機（沒有 ALB）全都看不到，而且在雲上**全都會回 HTTP 200**。既有四段沒有任何一段碰得到長連線。

**Acceptance Criteria:**

1. 請求必須帶 `run_id`，且該 run 必須是一次**已完成**的 run（契約：`docs/spec/2026-09-12-chat-honesty-lamps.md`，搜 `"run_id"` 的 jsonc 區塊；`case_id`／`run_id` 不同案回 400）。作法是重用第 3 段跑完的 `rid`（`verify.sh:116` 取得，`:167` 仍在作用域）：
   ```bash
   curl -N -s --max-time 60 -X POST "$base/api/cases/synthetic-ordinary-01/chat" \
        -H 'content-type: application/json' \
        -d "{\"run_id\":\"$rid\",\"message\":\"這個案子的受理期間怎麼算？\"}" | head -c 2000
   ```
   ⚠️ **只送 `message` 不送 `run_id` 的版本，在系統完全正常時也會判失敗**——那是恆假型驗收，不滿足本需求。
2. 前置守衛：`[[ -z "$rid" ]]` 時以 `bad` 記錄「第 3 段沒拿到 run_id，這段無法驗」，**把「第 3 段沒跑成」與「聊天壞了」分開**，不讓人誤判。
3. **判準是輸出中至少出現一行 `^data:`**。HTTP 200 但沒有 `data:` 行 → 判失敗（`bad`）。只比對狀態碼不算滿足本需求。
4. 使用既有輔助函式 `ok()`（`verify.sh:31`）與 `bad()`（`:30`，自帶 `fail=1`），不另發明 exit 機制。
5. **兩個方向都驗過才算完成**：
   - 不恆真——把 `run_id` 換成不存在的值，第 5 段必須判失敗
   - 不恆假——在已知正常的部署上跑一次，第 5 段必須通過
   只做前者不滿足本需求：反向驗證證明不了「這條檢查不是永遠失敗」。
6. fixture 模式的斷言為 **503**（已凍結：`honesty-lamps.md` 搜「為什麼是 503 而不是 501」，tech-lead 拍板，端點已實作、不可用的是執行檔位）。**不是 501。** `chat-ask-agent/proposal.md`（搜「fixture 模式回 503 而不是 501」）說法一致，兩份**沒有衝突**。
7. `bash -n infra/cdk/verify.sh` 零輸出。
8. 不新增測資：只用既有合成案例 `synthetic-ordinary-01`，並重用第 3 段真的跑出來的 run。

**Priority:** High

---

### REQ-DEPLOY-011: 「部署零改動」必須是可執行的驗收，而且基準不能會漂

**Description:**

甲案沿用 `BEDROCK_MODEL_ID_DRAFT`，因此 task role 的 `InvokeNamedModelsOnly`（`infra/cdk/lib/appeal-backend-stack.ts:101`，資源清單 `invokeResources` 在 `:94`，由 `invokeArnsFor()` `:46` 算出）已涵蓋聊天所需權限，CDK 不需改動；`boto3~=1.35.0` 與 `strands-agents>=1.15.0` 已在 `backend/requirements.txt:18-19`，不需加套件。

本需求要求把這個結論變成一條**會失敗**的檢查——若甲案實際上動了 CDK，它必須被抓出來。

**Acceptance Criteria:**

1. 濾掉註解行之後**零輸出**：
   ```bash
   git diff -U0 e27d2a7 -- infra/cdk/lib/ infra/cdk/bin/ \
     | grep '^[+-]' | grep -vE '^(\+\+\+|---)' | grep -vE '^[+-][[:space:]]*//'
   ```
   有輸出即代表前提破了，要回頭查哪裡走偏。**濾註解是刻意的**：驗的是「有沒有動到資源或權限」，不是「有沒有人改過註解」（`169dd41` 之後就有一次純註解修正）。
2. ⚠️ **基準必須釘在 commit（`e27d2a7`），不得用 `origin/main`。** `origin/main` 會跟著甲案一起前進，用它當基準這條**兩端都綠**——併之前沒東西可比、併之後基準跟著動——是一條恆真檢查，不滿足本需求。
3. `git diff --stat e27d2a7 -- backend/requirements.txt` 只包含 `:19` 那句註解的修正（見 REQ-DEPLOY-012），**不得有任何新增套件行**。
4. `/opt/homebrew/bin/python3 backend/tests/run_all.py` 全綠：374/374（另 2 項因語料不在 repo 被 harness 照實標示略過）。
   ⚠️ 必須用 `/opt/homebrew/bin/python3`；macOS 的 `/usr/bin/python3` 是 3.9，腳本自己會擋。

**Priority:** High

---

### REQ-DEPLOY-012: 換模型 id 時要改哪裡，寫死在文件而非臨場找

**Description:**

現在不做。但「臨場被迫換第三顆模型」時，漏改 IAM 的後果是**線上 `AccessDenied` 而本機完全正常**——本機走 Workshop Studio 臨時憑證，權限比 task role 寬，這個差異在本機一次都不會暴露。

**Acceptance Criteria:**

1. 改法三處寫在 `plan.md` 步驟 6，逐條可照打：`AppealBackendStackProps`（`appeal-backend-stack.ts:11` 起）加欄位、`invokeResources`（`:94`）加一行、`bin/app.ts:43-44` 附近加 `required('BEDROCK_MODEL_ID_CHAT')`。
2. 附**不需實際部署就能驗**的指令：`cdk synth` 後解析 template，確認 `bedrock:InvokeModel` statement 的 `Resource` 多了第三顆模型的 ARN。
3. 附 `cdk synth` 的前置條件（需 `cdk.context.json` 與 `CDK_DEFAULT_ACCOUNT`，否則停在 `StackAccountRegionNotSpecified`）——這個坑已實際踩過。
4. `backend/requirements.txt:19` 的註解修正為目錄級說法：實際被強制的是 `DEPENDENCY_EXEMPT_DIRS = ("api", "llm")`（`backend/tests/run_all.py:262`，套用在 `:271`／`:278`），即整個 `backend/llm/`。加 `backend/llm/chat.py` 後原註解會誤導下一個人以為自己違規。
   （註：`run_all.py:423` 附近那條是 `_llm_import_violations`，只掃 N2/N3/N4/N6 節點，跟本條無關。）

**Priority:** Medium

---

### REQ-DEPLOY-013: 交件前收窄 ALB 白名單是一個有清單、有回滾、有前置判斷的動作

**Description:**

賽方 2026-09-12 現場要求「對外開放連線請 allow 指定四組 IP」。功能已併入 main（`e27d2a7`）且**預設關閉**——不設 `ALB_ALLOWED_CIDRS` 即 `0.0.0.0/0`。Ci 拍板：現在不收窄，2026-09-13 交件前才收窄。

這是一個**有時限、只留在對話裡明天一定會漏**的動作，所以必須落在部署手冊裡。

**Acceptance Criteria:**

1. `backend/DEPLOY.md` §3.6 說明機制與查法；§6 是交件前檢查清單。
2. 清單含且順序不可跳：
   - **步驟 0**：重取 Workshop Studio 臨時憑證。`deploy.sh:43` 的 `aws sts get-caller-identity` 會第一個擋你，而錯誤訊息跟 ALB 毫無關係——交件前夜最不該卡在這裡。
   - **步驟 0b**：確認 `.env` 裡沒有 `ALB_ALLOWED_CIDRS`。`deploy.sh:32-35` 的 `set -a; . "$env_file"` 在命令列變數**之後**執行，會靜默覆蓋命令列的值，**並讓步驟 4 的回滾完全失效**（實查 2026-09-12：`.env` 目前沒有此變數，回滾可用）。
   - **步驟 1**：`cd infra/cdk && ALB_ALLOWED_CIDRS=<四組CIDR> ./deploy.sh deploy`
   - **步驟 2**：驗證，見下一條
   - **步驟 3**：重跑完整 `verify.sh`（含第 5 段）——收窄動的是 SG，最容易壞的就是自己也連不進去
   - **步驟 4（回滾）**：不帶 `ALB_ALLOWED_CIDRS` 重跑一次 deploy
3. **驗證要兩個都查**：`AlbIngress` CfnOutput（`describe-stacks`）**以及** SG 實況（`aws ec2 describe-security-groups`）。`AlbIngress` 的值從 props 推導（`appeal-backend-stack.ts:232`），**不讀 SG 實況**——有人在 console 手改 SG、或踩到步驟 0b 的覆蓋，它會照樣印出看起來正常的值。
4. **四組 CIDR 的實際值不進 repo**，標「交件前向賽方確認並填入」。寫死會被抄到過期的值。
5. 清單含風險警語：那四組是**會場出口 IP**，收窄後會場外連不進來，包含交件後才自己點開網址的評審。**先確認評審在哪裡看，問不到就不收窄。**
6. 目前狀態驗證：`AlbIngress` 現在必須是 `0.0.0.0/0`，代表沒有誤啟用。

**Priority:** High

### REQ-DEPLOY-014: 聊天呼叫也要受 1 RPS 節流保護，而且不得宣稱已全域解決

**Description:**

`_throttle()` 只管 `_invoke_structured` **每一次送出的請求**；Strands agent loop 在**一次**呼叫內因工具往返而多打的模型請求**不經過它**（`backend/llm/client.py:76-80` docstring 明寫）。聊天正是 agent loop，一輪可能連打 3–5 次。賽方規範 ≤1 RPS（`client.py:62`）。

**已定案：補 throttle。** 取捨理由——評審面前拿到 429 比慢 3–5 秒難看得多，而且 429 發生在 demo 中途無法當場除錯。

**Acceptance Criteria:**

1. `backend/llm/chat.py` 中**每一個**註冊為 tool 的函式，函式體第一句呼叫 `_throttle()`。一個都不能漏。
2. 驗法：`grep -n '@tool\|def \|_throttle()' backend/llm/chat.py`，逐一核對每個 `@tool` 底下的 `def`。**數量型門檻不算數**（「有幾個 `_throttle()`」可以湊），要的是**覆蓋每一個工具進入點**。
3. 落地後實際量一次「一輪聊天多等多久」，把估計值（約 3–5 秒）換成量測值。**在量到之前，文件一律標「估計值，未量測」。**
4. **文件與規格不得寫成「已解決全域 1 RPS」。** 必須同時寫出兩項限制：
   - 這只保證**單一路徑**不超速。甲案下六節點與聊天共用同一個進程的節流狀態，進程內有效。
   - **乙案（AgentCore）一上線即失效**：聊天跑在另一個容器，兩邊各節各的，**甲乙並存時全域仍可能超標**。真要嚴格全域限速，得把兩邊併進同一個節流器。
5. **賽制是否把聊天呼叫一起算，標「待查」。** 補 throttle 是保守選擇，不是因為查到了答案——不得在轉述時說成「已確認合規」。

**Priority:** High

---

## MODIFIED

### 既有部署驗收：段數由 4 段增為 5 段，兩處字樣要同步改

原 `verify.sh` 四段（首頁靜態資源 `:33-67`／`/api/health` 四項 `:69-106`／真打 Bedrock 端到端 `:108-153`／C 型守門 409 `:155-165`）全部保留、判準不變，僅**新增**第 5 段。

同步要改的兩處（不改就變成錯的）：
- `infra/cdk/verify.sh:2` 檔頭：「Success Criteria 四條」→ 五條
- `backend/DEPLOY.md:325` §3.4 標題：「`./verify.sh`，四條全綠才算完成」→ 五條

**不得**因為加了第 5 段而放寬任何既有一段的判準。既有兩條紅線（`/api/health` 四項任一不對即是離線重播不得交件；C 型 submit 沒回 409 即 P0）維持不變。

---

## REMOVED

_No removals in this change._

---

## 本 change 不解、但已記錄的既有問題

1. **聊天的 1 RPS 保護只到單一路徑為止**。已定案補 throttle（REQ-DEPLOY-014），但**乙案 AgentCore 一上線即失效**，甲乙並存時全域仍可能超標。**賽制是否把聊天呼叫一起算，仍待查。**
2. ~~503 vs 501 兩份來源不一致~~ **已撤回：兩份一致**，初稿的衝突宣稱源於行號漂移（引到了一列不相干的表格）。不需拍板、不需改動。
3. ~~環境收回時間兩說並存~~ **已對齊三處**（`backend/DEPLOY.md` §3.5、`appeal-backend-stack.ts` 的 VPC 註解、`deploy-backend-to-aws/proposal.md` 硬前提表）為「開到黑客松結束」。**來源等級照實記為「Ci 2026-09-12 口頭確認，非賽方書面」，不得升級成「賽方確認」。** 口頭效力弱於書面，Ci 會再確認一次。
