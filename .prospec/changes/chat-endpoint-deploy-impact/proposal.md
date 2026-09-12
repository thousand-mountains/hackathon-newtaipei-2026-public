# chat-endpoint-deploy-impact

盤點甲案聊天端點（`POST /api/cases/{case_id}/chat`，SSE 串流）對**部署**的改動範圍，並把 2026-09-13 交件前那個有時限、會被忘記的動作釘死。

- **負責**：`-0b` session（合併 `hack-deploy-aws` 的 ALB 白名單那條線）
- **範圍**：只盤部署面。**不實作聊天端點本身**——那是 `chat-ask-agent` 那張 change 的事。
- **相依**：`chat-ask-agent`（甲案本體）、`deploy-backend-to-aws`（現行部署）
- **死線**：繳交 2026-09-13。**ALB 白名單收窄是交件前最後一個部署動作。**

---

## Background

甲案的 plan／proposal／delta-spec **已經明文拍板「部署零改動」**，本 change 不是去推翻它，而是去**把那句話變成可執行的驗證**——「零改動」是一個斷言，斷言要能被打勾或打叉，不能只是寫在文件裡。

實查基礎（2026-09-12，皆附檔案:行號，非推論）：

| 面向 | 現況 | 甲案的影響 |
|---|---|---|
| IAM | task role 的 `InvokeNamedModelsOnly` 只放行 `invokeArnsFor(modelIdExtract)` ∪ `invokeArnsFor(modelIdDraft)`（`infra/cdk/lib/appeal-backend-stack.ts:33-47`、`:81-92`），來源是 `BEDROCK_MODEL_ID_EXTRACT`／`BEDROCK_MODEL_ID_DRAFT`（`infra/cdk/bin/app.ts:43-44`） | 沿用 `BEDROCK_MODEL_ID_DRAFT` → **CDK 一行都不用改**。換第三顆模型 id → 必改，見 US-2 |
| 套件 | `boto3~=1.35.0`、`strands-agents>=1.15.0` 都已在 `backend/requirements.txt:18-19` | **不用加套件**。線上的 `llm/client.py` 就是透過 strands 走 Converse 在跑 |
| SSE | 既有 `GET /api/runs/{run_id}/events` 用**同步 `def`** generator 交給 starlette threadpool，header 帶 `Cache-Control: no-store` 與 `X-Accel-Buffering: no`（`backend/api/app.py:456-485`） | 照抄同一個寫法 |
| ALB | idle timeout 900 秒（`appeal-backend-stack.ts:222`） | 夠長連線 SSE 用，**不用改** |
| ALB 來源 IP | `ALB_ALLOWED_CIDRS` 未設＝`0.0.0.0/0`，`AlbIngress` output 印 `0.0.0.0/0`（`e27d2a7`） | 與聊天無關，但**交件前要收窄**，見 US-4 |
| 部署鏈形狀 | `frontend/` Vite build → `frontend/dist/` → `backend/Dockerfile` COPY → CDK `fromAsset` → `deploy.sh` → `verify.sh` | Pink 的 Vue 重寫仍是 Vite 產物、仍同容器 serve → **形狀不變** |

**所以本 change 真正要交付的只有兩件事**：一條新的 `verify.sh` 檢查（讓「聊天在雲上真的會串流」變成可驗證的），以及一張交件前收窄的檢查清單。其餘都是「確認不用動」——而「確認不用動」也要有證據。

---

## User Stories

### US-1: 部署後有人能一行指令證明聊天在雲上真的會串流 [P1]

**作為**要交件的人，**我想要**跑 `verify.sh` 就知道聊天端點在雲上活著且真的在串流，**這樣**我不必靠「本機可以跑」來推論雲上可以。

背景：`verify.sh` 目前 4 段（首頁靜態資源 `:33-67`／`/api/health` 四項 `:69-106`／真打 Bedrock 端到端 `:108-153`／C 型守門 409 `:155-165`），輔助函式是 `note()` `:29`、`bad()` `:30`（同時設 `fail=1`）、`ok()` `:31`。

驗收（可執行）：
- `infra/cdk/verify.sh` 新增**第 5 段「聊天 SSE」**，插在第 4 段之後、「資料隔離」區塊（`:167`）之前。
- 這一段必須實際收到 SSE 事件才算過，不是只看 HTTP 狀態碼：
  ```bash
  curl -N -s --max-time 60 -X POST "$base/api/cases/synthetic-ordinary-01/chat" \
       -H 'content-type: application/json' -d '{"message":"這個案子的受理期間怎麼算？"}' \
       | head -c 2000
  ```
  判準：輸出中至少出現一行 `data:` 開頭的內容。**只有 200 沒有 `data:` 要判失敗**——那代表串流沒出來，正是 ALB／buffering 會壞掉的地方。
- 用既有的 `ok`／`bad` 記錄，不要自己另發明 exit 機制（`bad` 會設 `fail=1`，整份腳本的 exit code 靠它）。
- **反向驗證**：故意把網址指到一個不存在的路徑（例如 `/api/cases/does-not-exist/chat`），這一段必須判失敗。沒做過這步就等於沒驗過這條檢查本身。

⚠️ 待拍板（會改變這條的斷言）：fixture 模式下聊天回 **503 還是既有慣例的 501**，`docs/spec/2026-09-12-chat-honesty-lamps.md:62` 與 `.prospec/changes/chat-ask-agent/proposal.md:200` 都標「待 Ci 確認」。在拍板之前，第 5 段**不要**對 fixture 模式的狀態碼下斷言。

### US-2: 換模型 id 時，IAM 要改哪裡是寫死的，不是臨場找 [P1]

**作為**臨場被迫換模型的人，**我想要**照著一張明確清單改，**這樣**我不會在剩沒幾小時的時候才發現 task role 沒放行、線上回 AccessDenied。

驗收：
- **沿用 `BEDROCK_MODEL_ID_DRAFT` 的情況**：`git diff` 對 `infra/cdk/` 必須是**空的**。這是可執行的驗收，不是宣稱：
  ```bash
  git diff --stat -- infra/cdk/ backend/requirements.txt
  # 期望：沒有任何輸出
  ```
- **改用第三顆模型 id 的情況**（條件式，現在不做），要動的是這三處，一處都不能漏：
  1. `infra/cdk/lib/appeal-backend-stack.ts:11-20` 的 `AppealBackendStackProps` 加 `readonly modelIdChat: string;`
  2. 同檔 `:81-84` 的 `invokeResources` 加 `...invokeArnsFor(props.modelIdChat, region, account)`
  3. `infra/cdk/bin/app.ts:43-44` 附近加 `modelIdChat: required('BEDROCK_MODEL_ID_CHAT')`
- 驗法（不用真的部署也能驗）：`cdk synth` 後查 policy 裡放行的 ARN 數量有沒有增加：
  ```bash
  cdk synth --quiet -o /tmp/synth-chat
  python3 -c "import json,glob,sys; t=json.load(open(glob.glob('/tmp/synth-chat/*.template.json')[0]))['Resources']; print([s['Resource'] for r in t.values() if r['Type']=='AWS::IAM::Policy' for s in r['Properties']['PolicyDocument']['Statement'] if 'bedrock:InvokeModel' in str(s.get('Action'))])"
  ```
  期望：看得到第三顆模型的 ARN。**沒放行就是線上會 AccessDenied，這是靜態就驗得出來的。**

> `cdk synth` 需要 `cdk.context.json`（VPC lookup 快取，已 gitignore）與 `CDK_DEFAULT_ACCOUNT`；沒有就會停在 `StackAccountRegionNotSpecified`。這條踩過，寫在這裡免得下一個人重踩。

### US-3: 加了聊天之後，紅線掃描仍然全綠 [P1]

**作為**要推 code 的人，**我想要**知道新檔會不會踩到既有的靜態紅線，**這樣**我不會在推之前才發現掃描紅了。

實查結論（不是推論）：
- 外部依賴掃描的具名例外是**目錄級的 `backend/llm/`**（`backend/tests/run_all.py:423` 的訊息就是「只有 `backend/llm/` 可以」、`:514` 的標題同列），所以 **`backend/llm/chat.py` import `strands` 會過**。
- `N2/N3/N4/N6 無 LLM 依賴` 那條掃的是節點，`chat.py` 不是節點，不受影響。
- ⚠️ **文件與實作不一致**：`backend/requirements.txt:19` 的註解寫「只有這個檔可以 import 它」（指 `llm/client.py`），但實際被強制的是整個 `backend/llm/` 目錄。加 `chat.py` 之後這句註解會變成錯的。**順手把註解改成目錄級的說法**，否則下一個人會以為自己違規。

驗收：
```bash
/opt/homebrew/bin/python3 backend/tests/run_all.py
# 期望：七條紅線靜態掃描全 ok，總數全綠
```
> ⚠️ 一定要用 `/opt/homebrew/bin/python3`。macOS 的 `/usr/bin/python3` 是 3.9，腳本自己會擋下來並說「不要以為前面幾條綠就是驗過了」。

> 曾經的前置阻擋，**已解除**：`7724acf` 註冊了 `backend.tests.test_build_graph` 卻沒 commit `backend/tests/test_build_graph.py`，乾淨 clone 跑 `run_all.py` 會 `ImportError`、七條紅線一條都跑不到。`f5c1813` 補上該檔與 `scripts/build_graph.py` 後解除，在 `f5c1813` 上實跑 374/374 通過。DEPLOY.md §5 的「全綠才推」現在做得到。

### US-4: 交件前收窄 ALB 白名單這件事不會被忘記 [P1]

**作為**交件當天的人，**我想要**有一張照著打就能執行的清單，**這樣**這個有時限的動作不會因為只留在對話裡而漏掉。

背景：Ci 2026-09-12 拍板——**現在不收窄，維持 `0.0.0.0/0`；2026-09-13 交件前才收窄**。功能已併入 main（`e27d2a7`）且預設關閉。

驗收：
- `backend/DEPLOY.md` 已有 §3.6（機制與查法）與 §6（交件前檢查清單）。✅ 完成於 `e27d2a7`。
- 交件前實際執行時，逐條打勾：
  ```bash
  # 1) 收窄（四組 CIDR 交件前向賽方確認並填入，不要抄舊值）
  cd infra/cdk && ALB_ALLOWED_CIDRS=<四組CIDR> ./deploy.sh deploy

  # 2) 確認真的變了（唯一能一眼分辨有沒有誤啟用的地方）
  AWS_PROFILE=hack-ntpc AWS_REGION=us-west-2 aws cloudformation describe-stacks \
    --stack-name hackntpc-appeal-backend \
    --query "Stacks[0].Outputs[?OutputKey=='AlbIngress'].OutputValue" --output text
  # 期望：從 0.0.0.0/0 變成那四組，逐條核對沒有少打

  # 3) 收窄後重跑完整驗收（含新的第 5 段）
  ./verify.sh
  ```
- **回滾**：不帶 `ALB_ALLOWED_CIDRS` 重跑一次 `./deploy.sh deploy`，`AlbIngress` 回到 `0.0.0.0/0`。

⚠️ **收窄前必須先確認評審在哪裡看。** 那四組是**會場出口 IP**，收窄後從會場以外連進來的人會被擋掉，**包含交件後才自己點開網址的評審**。這題不能用推論決定——問到答案再收窄，問不到就不要收。

---

## Edge Cases

| 情境 | 預期行為 | 怎麼發現 |
|---|---|---|
| 聊天用了 `BEDROCK_MODEL_ID_DRAFT` 以外的模型，但沒改 CDK | 線上 `AccessDenied`，本機完全驗不出來（本機走 Workshop Studio 憑證，權限比 task role 寬） | US-2 的 `cdk synth` 查 ARN；或 `verify.sh` 第 5 段在雲上失敗 |
| SSE 在 ALB 後面被緩衝，事件不即時 | `X-Accel-Buffering: no` 已在既有端點處理；ALB idle timeout 900 秒夠 | US-1 的 `curl -N` 必須真的看到 `data:` 行，只看狀態碼抓不到 |
| 聊天與六節點 SSE 同時開很多條 | 共用同一個 uvicorn threadpool（預設 40 條），demo 量級夠用；不是通用方案 | 容量假設，非本 change 改動範圍。plan 自己也承認（`.prospec/changes/chat-ask-agent/proposal.md:149`） |
| 聊天呼叫是否計入賽制 Bedrock ≤1 RPS | **待查**（`proposal.md:202`、`docs/spec/2026-09-12-chat-honesty-lamps.md:303`）。六節點與聊天共用同一個節流器 `BEDROCK_MIN_INTERVAL_S` | 未驗證，不主張。高並發下吞吐會被節流器壓低是可預期的 |
| ALB 白名單誤啟用（不該收窄時收窄了） | `AlbIngress` output 會顯示 CIDR 而非 `0.0.0.0/0` | US-4 步驟 2；**每次部署完都要看這行** |
| 前端 Vue 重寫後忘記重建映像檔 | `fromAsset` 的 hash 會變，`./deploy.sh deploy` 自己會重建 | `verify.sh` 第 1 段比對首頁 bytes |

---

## Success Criteria

本 change 完成的定義（全部通過才算）：

```bash
# 1) 「部署零改動」是可驗證的，不是宣稱
git diff --stat origin/main -- infra/cdk/lib/ infra/cdk/bin/ backend/requirements.txt
#    期望：只有 verify.sh 以外的 infra 檔零改動（verify.sh 的第 5 段是預期內的）

# 2) verify.sh 第 5 段存在且真的驗串流
grep -c 'data:' infra/cdk/verify.sh    # 期望 ≥1（判準確實檢查 SSE 事件行）
bash -n infra/cdk/verify.sh            # 語法檢查，期望零輸出

# 3) 紅線全綠
/opt/homebrew/bin/python3 backend/tests/run_all.py
#    期望：374/374（另 2 項因語料不在 repo 略過，harness 會照實標示）

# 4) 交件前清單存在且指令可照打
grep -n 'ALB_ALLOWED_CIDRS' backend/DEPLOY.md    # 期望在 §3.6 與 §6 都出現
```

---

## Related Modules

| 模組 | 關係 | 本 change 要不要動 |
|---|---|---|
| `infra/cdk/verify.sh` | 部署驗收腳本 | **要**——加第 5 段 |
| `backend/DEPLOY.md` | 部署手冊 | **已動**（`e27d2a7` 加 §3.6、§6） |
| `infra/cdk/lib/appeal-backend-stack.ts` | IAM／ALB／ECS | **不動**（沿用 draft 模型的前提下）；US-2 寫了條件式改法 |
| `infra/cdk/bin/app.ts` | 環境變數入口 | **不動**（同上） |
| `backend/requirements.txt` | 套件 | 只改 `:19` 那句過時註解（目錄級 vs 單檔） |
| `backend/api/app.py` | 掛 chat router 一行、SSE 寫法範本（`:456-485`） | 由 `chat-ask-agent` 負責，非本 change |
| `backend/llm/chat.py`／`backend/api/chat.py` | 聊天本體 | 由 `chat-ask-agent` 負責，非本 change |
| `backend/tests/run_all.py` | 紅線掃描 | **不動**（`test_build_graph` 斷鏈已由 `f5c1813` 修掉） |

---

## Open Questions

1. **fixture 模式下聊天回 503 還是 501？** 待 Ci 確認（`docs/spec/2026-09-12-chat-honesty-lamps.md:62`）。**會改變 `verify.sh` 第 5 段的斷言**，拍板前不對 fixture 狀態碼下斷言。
2. **聊天呼叫計不計入賽制 ≤1 RPS？** 待查（`.prospec/changes/chat-ask-agent/proposal.md:202`）。不改部署設定，但影響共用節流器的實際吞吐。
3. **四組會場 IP 的實際值？** 交件前向賽方確認。刻意不寫死在任何文件裡，避免抄到過期的值。
4. **評審是在會場內還是會場外看？** 未確認。**這題沒答案就不該收窄**（US-4 的警語）。
5. **AgentCore（Stretch）若真的做，環境變數開關要對齊** `CHAT_BACKEND=inproc|agentcore`，預設 `inproc`。本 change 不設計這個開關，只記下名稱以免兩邊各發明一個。

---

## Constitution Check

| 原則 | 本 change 的對應 |
|---|---|
| 分層誠實 | US-1 要求第 5 段必須真的收到 `data:` 行；只看 200 是「看起來在跑」，正是誠實性要擋的 |
| 引用必可驗 | 本 change 的每個事實都附 `檔案:行號`；查不到的（RPS、會場 IP、評審位置）一律標「待查」不填 |
| 規則引擎零 LLM | 聊天不碰 N2/N3/N4/N6，紅線掃描 US-3 會擋 |
| 不編造測資 | 第 5 段用既有合成案例 `synthetic-ordinary-01` |
| plan 先行 | 本 change 本身就是 plan，實作前跑 `prospec change plan` |
| 資料隔離 | 不新增資料路徑；`verify.sh` 既有的資料隔離區塊不動 |
| secret | 四組 CIDR 不寫死在 repo；模型 id 仍只從環境變數注入（紅線第 6 條會掃） |
| 30h 紀律 | 只加一段驗收與一張清單，不新增 AWS 資源、不改 CDK |

---

## Next Steps

| # | 步驟 | 狀態 |
|---|---|---|
| 0 | 把 ALB 白名單併回 main（預設關閉）＋ DEPLOY.md §3.6／§6 | ✅ `e27d2a7` |
| 1 | 解決 `test_build_graph` 斷鏈——**US-3 的前置阻擋** | ✅ `f5c1813`（補檔，非拿掉註冊）；已在該 commit 上實跑 374/374 |
| 2 | `chat-ask-agent` 實作完成後，加 `verify.sh` 第 5 段並用反向案例驗這條檢查本身 | ⏳ 等甲案 |
| 3 | 修 `backend/requirements.txt:19` 的過時註解（單檔 → 目錄級） | ⏳ 併在步驟 2 一起 |
| 4 | 跑 `git diff --stat -- infra/cdk/lib/ infra/cdk/bin/` 確認「部署零改動」成立 | ⏳ 等步驟 2 |
| 5 | 交件前：照 DEPLOY.md §6 收窄 ALB，確認 `AlbIngress` 變成四組，重跑 `verify.sh` 全段 | ⏳ 2026-09-13 |
