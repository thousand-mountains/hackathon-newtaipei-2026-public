# chat-endpoint-deploy-impact

盤點甲案聊天端點（`POST /api/cases/{case_id}/chat`，SSE 串流）對**部署**的改動範圍，並把 2026-09-13 交件前那個有時限、會被忘記的動作釘死。

- **負責**：`-0b` session（合併 `hack-deploy-aws` 的 ALB 白名單那條線）
- **範圍**：只盤部署面。**不實作聊天端點本身**——那是 `chat-ask-agent` 那張 change 的事。
- **相依**：`chat-ask-agent`（甲案本體）、`deploy-backend-to-aws`（現行部署）
- **死線**：繳交 2026-09-13。**ALB 白名單收窄是交件前最後一個部署動作。**

> ⚠️ **本文件引用的兩份檔案目前尚未進 git**：`.prospec/changes/chat-ask-agent/*` 與 `docs/spec/2026-09-12-chat-honesty-lamps.md` 在主工作樹是未追蹤檔（`git ls-tree -r HEAD` 查不到），而且**正在被編輯**。在它們被 commit 之前，從乾淨 clone 打不開這些引用。
>
> 因此**指向這兩份的引用一律用內容錨點（搜某個字串）而非行號**——初稿用行號，已經害我做出一次錯誤判斷（誤以為 503/501 有衝突，實際是引到了一列不相干的表格）。指向 repo 內已 commit 檔案的行號則保留，那些不會動。

---

## Background

甲案的 plan／proposal／delta-spec **已經明文拍板「部署零改動」**，本 change 不是去推翻它，而是去**把那句話變成可執行的驗證**——「零改動」是一個斷言，斷言要能被打勾或打叉，不能只是寫在文件裡。

實查基礎（2026-09-12，行號皆以 `53566dd` 的工作樹實查，非推論）：

| 面向 | 現況 | 甲案的影響 |
|---|---|---|
| IAM | task role 的 `InvokeNamedModelsOnly`（`infra/cdk/lib/appeal-backend-stack.ts:101`）只放行 `invokeResources`（`:94`）算出的 ARN，來源是 `invokeArnsFor()`（`:46`）吃 `modelIdExtract`／`modelIdDraft`；兩者由 `BEDROCK_MODEL_ID_EXTRACT`／`BEDROCK_MODEL_ID_DRAFT` 注入（`infra/cdk/bin/app.ts:43-44`） | 沿用 `BEDROCK_MODEL_ID_DRAFT` → **CDK 一行都不用改**。換第三顆模型 id → 必改，見 US-2 |
| 套件 | `boto3~=1.35.0`、`strands-agents>=1.15.0` 都已在 `backend/requirements.txt:18-19` | **不用加套件**。線上的 `llm/client.py` 就是透過 strands 走 Converse 在跑 |
| SSE | 既有 `GET /api/runs/{run_id}/events`（`backend/api/app.py:435-464`）用**同步 `def`** generator 交給 starlette threadpool（說明在 `:443-445`），header 帶 `Cache-Control: no-store` 與 `X-Accel-Buffering: no`（`:459-463`） | 照抄同一個寫法 |
| ALB | idle timeout 900 秒（`appeal-backend-stack.ts:256`） | 夠長連線 SSE 用，**不用改** |
| ALB 來源 IP | `ALB_ALLOWED_CIDRS` 未設＝`0.0.0.0/0`，`AlbIngress` output（`appeal-backend-stack.ts:232`）印 `0.0.0.0/0`（`e27d2a7`） | 與聊天無關，但**交件前要收窄**，見 US-4 |
| 部署鏈形狀 | `frontend/` Vite build → `frontend/dist/` → `backend/Dockerfile` COPY → CDK `fromAsset` → `deploy.sh` → `verify.sh` | Pink 的 Vue 重寫仍是 Vite 產物、仍同容器 serve → **形狀不變** |

**所以本 change 真正要交付的只有兩件事**：一條新的 `verify.sh` 檢查（讓「聊天在雲上真的會串流」變成可驗證的），以及一張交件前收窄的檢查清單。其餘都是「確認不用動」——而「確認不用動」也要有證據。

---

## User Stories

### US-1: 部署後有人能一行指令證明聊天在雲上真的會串流 [P1]

**作為**要交件的人，**我想要**跑 `verify.sh` 就知道聊天端點在雲上活著且真的在串流，**這樣**我不必靠「本機可以跑」來推論雲上可以。

背景：`verify.sh` 目前 4 段（首頁靜態資源 `:33-67`／`/api/health` 四項 `:69-106`／真打 Bedrock 端到端 `:108-153`／C 型守門 409 `:155-165`），輔助函式是 `note()` `:29`、`bad()` `:30`（同時設 `fail=1`）、`ok()` `:31`。

⚠️ **契約重點，寫錯這條檢查就永遠失敗**：`run_id` 是**必填**，而且必須是一次**已完成**的 run（`docs/spec/2026-09-12-chat-honesty-lamps.md`（搜 `"run_id"`，請求 body 的 jsonc 區塊）；`case_id`／`run_id` 不同案回 400）。只送 `message` 的 curl **在系統完全正常時也拿不到 `data:` 行**。

驗收（可執行）：
- `infra/cdk/verify.sh` 新增**第 5 段「聊天 SSE」**，插在第 4 段之後、「資料隔離」區塊（`:167`）之前。
- 重用第 3 段已經跑完的那個 run——`rid` 在 `verify.sh:116` 取得，到 `:167` 仍在作用域：
  ```bash
  if [[ -z "$rid" ]]; then
    bad "聊天 SSE：第 3 段沒拿到 run_id，這段無法驗（不是聊天壞了）"
  else
    chat_out="$(curl -N -s --max-time 60 -X POST \
      "$base/api/cases/synthetic-ordinary-01/chat" \
      -H 'content-type: application/json' \
      -d "{\"run_id\":\"$rid\",\"message\":\"這個案子的受理期間怎麼算？\"}" | head -c 2000)"
    if printf '%s' "$chat_out" | grep -q '^data:'; then
      ok "聊天端點回得出 SSE 事件"
    else
      bad "聊天端點沒有串流出任何 data: 事件（HTTP 通不代表串流通）"
    fi
  fi
  ```
  判準：輸出中至少出現一行 `data:` 開頭的內容。**只有 200 沒有 `data:` 要判失敗**——ALB 緩衝、`X-Accel-Buffering` 掉了、generator 沒 flush，這些壞法全都會回 200。
- 用既有的 `ok`／`bad` 記錄，不要自己另發明 exit 機制（`bad` 會設 `fail=1`，整份腳本的 exit code 靠它）。
- **這段硬相依第 3 段成功**（重用它的 run）。這是刻意的：另跑一個 run 要多花 50–80 秒，而第 3 段失敗時本來就不該交件。上面的 `-n "$rid"` 守衛負責把「第 3 段沒跑成」跟「聊天壞了」分開，不要讓人誤判。

**這條檢查本身要怎麼驗**（兩個方向都要，缺一不可）：
- **驗它不是恆真**：把 `run_id` 換成一個不存在的值跑一次，必須判失敗。
- **驗它不是恆假**：在一個**已知正常**的部署上跑一次，必須通過。反向驗證證明不了這件事——一條參數寫錯的檢查永遠失敗，反向驗證照樣「通過」。

⚠️ **fixture 模式的狀態碼已凍結為 503，不是待決。** 兩份來源**一致**：`docs/spec/2026-09-12-chat-honesty-lamps.md`（搜「為什麼是 503 而不是 501」）寫「tech-lead 拍板，已凍結…Ci 若要改回 501，**開工前說**；開工後不改」；`.prospec/changes/chat-ask-agent/proposal.md`（搜「fixture 模式回 503 而不是 501」）寫「tech-lead 已拍板 503（理由見 spec §2.3），契約凍結。Ci 若要改回 501，**開工前說**」。第 5 段可以直接對 fixture 模式斷言 503。

> 本文件初稿曾宣稱這兩份打架，那是**行號漂移造成的誤判**（引用的 `:200` 實際是一列 `kb.py` 的表格）。已撤回。所有指向這兩份未追蹤檔的引用**一律改用內容錨點**（搜某個字串）而非行號。

### US-2: 換模型 id 時，IAM 要改哪裡是寫死的，不是臨場找 [P1]

**作為**臨場被迫換模型的人，**我想要**照著一張明確清單改，**這樣**我不會在剩沒幾小時的時候才發現 task role 沒放行、線上回 AccessDenied。

驗收：
- **沿用 `BEDROCK_MODEL_ID_DRAFT` 的情況**：CDK 對**部署面**零改動。基準釘在 commit，不要用 `origin/main`——`origin/main` 會跟著甲案一起前進，那樣這條兩端都綠：
  ```bash
  git diff -U0 e27d2a7 -- infra/cdk/lib/ infra/cdk/bin/ \
    | grep '^[+-]' | grep -vE '^(\+\+\+|---)' | grep -vE '^[+-][[:space:]]*//'
  # 期望：零輸出（濾掉註解行——驗的是有沒有動到資源或權限，不是有沒有人改註解）
  ```
- **改用第三顆模型 id 的情況**（條件式，現在不做），要動的是這三處，一處都不能漏：
  1. `infra/cdk/lib/appeal-backend-stack.ts:11` 起的 `AppealBackendStackProps` 加 `readonly modelIdChat: string;`
  2. 同檔 `:94` 的 `invokeResources` 加 `...invokeArnsFor(props.modelIdChat, region, account)`
  3. `infra/cdk/bin/app.ts:43-44` 附近加 `modelIdChat: required('BEDROCK_MODEL_ID_CHAT')`
- 驗法（不用真的部署也能驗）：`cdk synth` 後查 policy 裡放行的 ARN 數量有沒有增加：
  ```bash
  cdk synth --quiet -o /tmp/synth-chat
  python3 -c "import json,glob; t=json.load(open(glob.glob('/tmp/synth-chat/*.template.json')[0]))['Resources']; print([s['Resource'] for r in t.values() if r['Type']=='AWS::IAM::Policy' for s in r['Properties']['PolicyDocument']['Statement'] if 'bedrock:InvokeModel' in str(s.get('Action'))])"
  ```
  期望：看得到第三顆模型的 ARN。**沒放行就是線上 `AccessDenied`，而本機完全驗不出來**——本機走 Workshop Studio 臨時憑證，權限比 task role 寬。

> `cdk synth` 需要 `cdk.context.json`（VPC lookup 快取，已 gitignore）與 `CDK_DEFAULT_ACCOUNT`；沒有就會停在 `StackAccountRegionNotSpecified`。這條踩過，寫在這裡免得下一個人重踩。

### US-3: 加了聊天之後，紅線掃描仍然全綠 [P1]

**作為**要推 code 的人，**我想要**知道新檔會不會踩到既有的靜態紅線，**這樣**我不會在推之前才發現掃描紅了。

實查結論（不是推論）：
- 外部依賴掃描的具名例外是**目錄級**：`DEPENDENCY_EXEMPT_DIRS = ("api", "llm")`（`backend/tests/run_all.py:262`，套用在 `:271`／`:278`），所以 **`backend/llm/chat.py` import `strands` 會過**。
- `N2/N3/N4/N6 無 LLM 依賴` 那條（`_llm_import_violations`，`run_all.py:423` 附近）掃的是節點，`chat.py` 不是節點，不受影響。
- ⚠️ **文件與實作不一致**：`backend/requirements.txt:19` 的註解寫「只有這個檔可以 import 它」（指 `llm/client.py`），但實際被強制的是整個 `backend/llm/` 目錄。加 `chat.py` 之後這句註解會變成錯的。**順手把註解改成目錄級的說法**，否則下一個人會以為自己違規。

驗收：
```bash
/opt/homebrew/bin/python3 backend/tests/run_all.py
# 期望：374/374（另 2 項因語料不在 repo 略過，harness 會照實標示）
```
> ⚠️ 一定要用 `/opt/homebrew/bin/python3`。macOS 的 `/usr/bin/python3` 是 3.9，腳本自己會擋下來並說「不要以為前面幾條綠就是驗過了」。

> 曾經的前置阻擋，**已解除**：`7724acf` 註冊了 `backend.tests.test_build_graph` 卻沒 commit `backend/tests/test_build_graph.py`，乾淨 clone 跑 `run_all.py` 會 `ImportError`、七條紅線一條都跑不到。`f5c1813` 補上該檔與 `scripts/build_graph.py` 後解除，在該 commit 上實跑 374/374 通過。DEPLOY.md §5 的「全綠才推」現在做得到。

### US-4: 交件前收窄 ALB 白名單這件事不會被忘記 [P1]

**作為**交件當天的人，**我想要**有一張照著打就能執行的清單，**這樣**這個有時限的動作不會因為只留在對話裡而漏掉。

背景：Ci 2026-09-12 拍板——**現在不收窄，維持 `0.0.0.0/0`；2026-09-13 交件前才收窄**。功能已併入 main（`e27d2a7`）且預設關閉。

驗收：
- `backend/DEPLOY.md` 已有 §3.6（機制與查法）與 §6（交件前檢查清單）。✅ 完成於 `e27d2a7`，`.env` 覆蓋陷阱與 SG 實況查法補於後續 commit。
- 交件前實際執行時，逐條打勾（完整版在 DEPLOY.md §6，以那份為準）：
  0. 先重取 Workshop Studio 臨時憑證——`deploy.sh:43` 的 `aws sts get-caller-identity` 會第一個擋下你，而錯誤訊息跟 ALB 毫無關係
  0b. 先確認 `.env` 裡**沒有** `ALB_ALLOWED_CIDRS`——`deploy.sh:32-35` 的 `set -a; . "$env_file"` 在命令列變數**之後**執行，會靜默覆蓋你在命令列給的值（實查 2026-09-12：`.env` 目前沒有這個變數，回滾可用）
  1. `cd infra/cdk && ALB_ALLOWED_CIDRS=<四組CIDR> ./deploy.sh deploy`
  2. 查 `AlbIngress` output 有沒有變成那四組，**再對 SG 實況查一次**（見下）
  3. 重跑完整 `verify.sh`（含第 5 段）
- **回滾**：不帶 `ALB_ALLOWED_CIDRS` 重跑一次 `./deploy.sh deploy`，`AlbIngress` 回到 `0.0.0.0/0`。（前提是步驟 0b 成立。）

⚠️ **`AlbIngress` 是 CfnOutput，值從 props 推導（`appeal-backend-stack.ts:232`），不是讀 SG 實況。** 有人在 console 手改 SG、或踩到 `.env` 覆蓋，它會照樣印出看起來正常的值。真正的 ground truth 是 `aws ec2 describe-security-groups`。兩個都要看。

⚠️ **收窄前必須先確認評審在哪裡看。** 那四組是**會場出口 IP**，收窄後從會場以外連進來的人會被擋掉，**包含交件後才自己點開網址的評審**。這題不能用推論決定——問到答案再收窄，問不到就不要收。

### US-5: 聊天不會讓我們在評審面前吃 429 [P1]

**作為**在 demo 現場的人，**我想要**聊天呼叫也受 1 RPS 節流保護，**這樣**我不會在評審面前拿到一個當場除不了錯的 429。

背景（實查，非推論）：`_throttle()` 只管 `_invoke_structured` **每一次送出的請求**，Strands agent loop 在**一次**呼叫內因工具往返而多打的模型請求**不經過它**（`backend/llm/client.py:76-80` docstring 明寫）。聊天正是 agent loop，一輪可能連打 3–5 次。賽方規範是 ≤1 RPS（`client.py:62`）。

**已定案：補 throttle。** 取捨理由——慢 3–5 秒是可以解釋的體驗損失，429 不是。

驗收：
- `backend/llm/chat.py` 的**每個工具進入點**各呼叫一次 `_throttle()`，一個都不能漏。
- 驗法（可執行）：列出 `chat.py` 裡所有註冊為 tool 的函式，逐一確認第一行是 `_throttle()`：
  ```bash
  grep -n '@tool\|def \|_throttle()' backend/llm/chat.py
  # 每個 @tool 底下的 def，其函式體第一句要是 _throttle()
  ```
- 代價：一輪聊天多等約 3–5 秒（**估計值，未量測**）。落地後量一次實際值填回這裡。

⚠️ **誠實限制，不要讓規格寫得像已經解決：**
1. 補 throttle **只保證單一路徑不超速**。甲案下六節點與聊天共用**同一個進程**的節流狀態，所以進程內是有效的。
2. **乙案（AgentCore）一上線就破了**：聊天跑在另一個容器，兩邊各節各的，**甲乙並存時全域仍可能超標**。真要嚴格全域限速，得把兩邊併進同一個節流器——`client.py:76-80` 的第 2 點（`retrieval/kb.py` 的 `RETRIEVE_INTERVAL_S` 是另一個獨立的閘）講的是同一件事。
3. **賽制是否把聊天呼叫一起算，仍然待查。** 補 throttle 是保守選擇，不是因為查到了答案。

---

## Edge Cases

| 情境 | 預期行為 | 怎麼發現 |
|---|---|---|
| 聊天用了 `BEDROCK_MODEL_ID_DRAFT` 以外的模型，但沒改 CDK | 線上 `AccessDenied`，本機完全驗不出來（本機憑證權限比 task role 寬） | US-2 的 `cdk synth` 查 ARN；或 `verify.sh` 第 5 段在雲上失敗 |
| 第 5 段的 curl 漏送 `run_id` | 回 400／422，拿不到 `data:` 行，**系統正常也判失敗** | 契約在 `docs/spec/2026-09-12-chat-honesty-lamps.md`（搜 `"run_id"`，請求 body 的 jsonc 區塊）。這是恆假型驗收，比恆真更毒——它會訓練人忽略 `verify.sh` |
| SSE 在 ALB 後面被緩衝，事件不即時 | `X-Accel-Buffering: no` 已在既有端點處理（`app.py:459-463`）；ALB idle timeout 900 秒夠 | US-1 的 `curl -N` 必須真的看到 `data:` 行，只看狀態碼抓不到 |
| **聊天繞過 1 RPS 節流器** | ⚠️ **不是「會被壓低」，是「不受保護」**：`_throttle()` 只管 `_invoke_structured` 每次送出的請求，**Strands agent loop 在一次呼叫內的工具往返不經過它**（`backend/llm/client.py:76-80` docstring 明寫）。聊天正是 agent loop，一輪可能連打 3–5 次模型；`retrieval/kb.py` 的 `RETRIEVE_INTERVAL_S` 是另一個獨立的閘，兩者相加仍可能超標 | **已定案：補 throttle**（見 US-5）。理由是評審面前拿到 429 比慢 3–5 秒難看得多，而且 429 發生在 demo 中途無法當場除錯 |
| 聊天與六節點 SSE 同時開很多條 | 共用同一個 uvicorn threadpool（預設 40 條），demo 量級夠用；不是通用方案 | 容量假設，非本 change 改動範圍 |
| ALB 白名單誤啟用（不該收窄時收窄了） | `AlbIngress` 會顯示 CIDR 而非 `0.0.0.0/0`，**但它可能說謊**（見 US-4 警語） | `AlbIngress` ＋ `describe-security-groups` 兩個都看 |
| `.env` 裡被人加了 `ALB_ALLOWED_CIDRS` | `deploy.sh` 會靜默覆蓋命令列的值，**且回滾步驟失效** | US-4 步驟 0b。實查 2026-09-12：目前 `.env` 沒有這個變數 |
| 前端 Vue 重寫後忘記重建映像檔 | `fromAsset` 的 hash 會變，`./deploy.sh deploy` 自己會重建 | `verify.sh` 第 1 段比對首頁 bytes |

---

## Success Criteria

本 change 完成的定義（全部通過才算）：

```bash
# 1) 「部署零改動」是可驗證的。基準釘在 commit，不是 origin/main；且濾掉註解行
git diff -U0 e27d2a7 -- infra/cdk/lib/ infra/cdk/bin/ \
  | grep '^[+-]' | grep -vE '^(\+\+\+|---)' | grep -vE '^[+-][[:space:]]*//'
#    期望：零輸出。濾註解的理由——驗的是有沒有動到資源或權限，不是有沒有人改註解
git diff --stat e27d2a7 -- backend/requirements.txt
#    期望：只有 :19 那句註解的修正，不得有新增套件行

# 2) verify.sh 第 5 段存在、真的驗串流、而且帶了 run_id
grep -c '^data:\|data:' infra/cdk/verify.sh   # 期望 ≥1（判準確實檢查 SSE 事件行）
grep -c 'run_id' infra/cdk/verify.sh          # 期望 ≥1（缺 run_id 這條就是恆假）
bash -n infra/cdk/verify.sh                   # 語法檢查，期望零輸出

# 3) 紅線全綠
/opt/homebrew/bin/python3 backend/tests/run_all.py
#    期望：374/374（另 2 項因語料不在 repo 略過，harness 會照實標示）

# 4) 交件前清單存在且指令可照打
grep -n 'ALB_ALLOWED_CIDRS' backend/DEPLOY.md    # 期望在 §3.6 與 §6 都出現
```

第 5 段落地後，還要各跑一次：**指到不存在的 `run_id` 必須判失敗**（證明不恆真），**指到已知正常的部署必須通過**（證明不恆假）。

---

## Related Modules

| 模組 | 關係 | 本 change 要不要動 |
|---|---|---|
| `infra/cdk/verify.sh` | 部署驗收腳本 | **要**——加第 5 段。**連同 `:2` 檔頭那句「Success Criteria 四條」一起改成五條** |
| `backend/DEPLOY.md` §3.4 標題（`:325`）| 寫著「`./verify.sh`，四條全綠才算完成」 | **要**——第 5 段落地當下就變成錯的 |
| `backend/DEPLOY.md` §3.6／§6 | 部署手冊 | ✅ 主體完成於 `e27d2a7`；`.env` 覆蓋陷阱、憑證步驟、SG 實況查法為後續補強 |
| `backend/requirements.txt` | 套件 | 只改 `:19` 那句過時註解（單檔 → 目錄級），**不得新增套件行** |
| `infra/cdk/lib/appeal-backend-stack.ts` | IAM／ALB／ECS | **不動**（沿用 draft 模型的前提下）；US-2 寫了條件式改法 |
| `infra/cdk/bin/app.ts` | 環境變數入口 | **不動**（同上） |
| `backend/api/app.py` | 掛 chat router 一行、SSE 寫法範本（`:435-464`） | 由 `chat-ask-agent` 負責，非本 change |
| `backend/llm/chat.py`／`backend/api/chat.py` | 聊天本體 | 由 `chat-ask-agent` 負責，非本 change |
| `backend/tests/run_all.py` | 紅線掃描 | **不動**（`test_build_graph` 斷鏈已由 `f5c1813` 修掉） |

---

## Open Questions

1. ~~**503 vs 501 兩份來源不一致。**~~ **已撤回：兩份其實一致**，都寫「tech-lead 已拍板 503、契約凍結、Ci 若要改開工前說」。初稿的衝突宣稱源於引用了一個漂掉的行號，不是真的矛盾。**不需拍板，不需改動任何檔案。**
2. ~~**聊天要不要併進 1 RPS 節流器？**~~ **已定案：補 throttle**（US-5）。**但仍有一項待查：賽制是否把聊天呼叫一起算。** 補 throttle 是保守選擇，不是因為查到了答案——這一點不要在轉述時說成「已確認合規」。
3. **四組會場 IP 的實際值？** 交件前向賽方確認。刻意不寫死在任何文件裡，避免抄到過期的值。
4. **評審是在會場內還是會場外看？** 未確認。**這題沒答案就不該收窄**（US-4 的警語）。
5. **`chat-ask-agent` 與 `chat-honesty-lamps` 要不要 commit？** 不 commit 的話，本文件的五個引用在 main 上全是死鏈（本文件開頭已標注）。
6. **AgentCore（Stretch）若真的做，環境變數開關要對齊** `CHAT_BACKEND=inproc|agentcore`，預設 `inproc`。本 change 不設計這個開關，只記下名稱以免兩邊各發明一個。
7. ~~**環境收回時間兩說並存。**~~ **已對齊三處**（`backend/DEPLOY.md` §3.5、`infra/cdk/lib/appeal-backend-stack.ts` 的 VPC 註解、`deploy-backend-to-aws/proposal.md` 的硬前提表），統一為「環境開到黑客松結束」。**來源等級一律照實寫成「Ci 2026-09-12 口頭確認，非賽方書面」，不得在轉述時升級成「賽方確認」。** 口頭確認效力弱於書面，Ci 會再確認一次這個事實是否仍成立。

---

## Constitution Check

| 原則 | 本 change 的對應 |
|---|---|
| 分層誠實 | US-1 要求第 5 段必須真的收到 `data:` 行；只看 200 是「看起來在跑」，正是誠實性要擋的。**聊天繞過 1 RPS 節流器**已照實寫進 Edge Cases，不粉飾成「會被壓低」 |
| 引用必可驗 | 每個事實都附 `檔案:行號`；查不到的（賽制 RPS 計法、會場 IP、評審位置）一律標「待查」不填。**兩份尚未進 git 的引用已在文件開頭標注** |
| 規則引擎零 LLM | 聊天不碰 N2/N3/N4/N6，紅線掃描 US-3 會擋 |
| 不編造測資 | 第 5 段用既有合成案例 `synthetic-ordinary-01`，並重用第 3 段真的跑出來的 run |
| plan 先行 | 本 change 有 `plans/2026-09-12-chat-endpoint-deploy-impact.md` 與本 prospec 三件套 |
| 資料隔離 | 不新增資料路徑；`verify.sh` 既有的資料隔離區塊不動 |
| secret | 四組 CIDR 不寫死在 repo；模型 id 仍只從環境變數注入（紅線第 6 條會掃） |
| 30h 紀律 | 只加一段驗收與一張清單，不新增 AWS 資源、不改 CDK |
| （既有例外）request 路徑同步阻塞 | 第 5 段驗的是既有的同步 `def` + threadpool 寫法（`app.py:443-445` 已把這個例外講清楚）。本 change **沿用既有已知例外，不新增** |

---

## Next Steps

| # | 步驟 | 狀態 |
|---|---|---|
| 0 | 把 ALB 白名單併回 main（預設關閉）＋ DEPLOY.md §3.6／§6 | ✅ `e27d2a7` |
| 1 | 解決 `test_build_graph` 斷鏈 | ✅ `f5c1813`（補檔，非拿掉註冊）；已在該 commit 上實跑 374/374 |
| 2 | `chat-ask-agent` 實作完成後，加 `verify.sh` 第 5 段（**帶 `run_id`**），並跑「不存在 run_id 判失敗」＋「正常部署判通過」兩個方向 | ⏳ 等甲案 |
| 3 | 同步改 `verify.sh:2` 檔頭與 `DEPLOY.md:325` §3.4 標題的「四條」→「五條」 | ⏳ 併在步驟 2 一起 |
| 4 | 修 `backend/requirements.txt:19` 的過時註解（單檔 → 目錄級） | ⏳ 併在步驟 2 一起 |
| 5 | 跑 Success Criteria #1 的濾註解 diff，確認「部署零改動」成立 | ⏳ 等步驟 2 |
| 6 | 在 `backend/llm/chat.py` 每個工具進入點補 `_throttle()`（US-5，已定案），並量實際延遲填回規格 | ⏳ 等甲案 |
| 6b | ~~Ci 拍板 503/501 與 RPS~~ | ✅ 503/501 本無衝突（誤判已撤回）；RPS 已定案補 throttle |
| 6c | 環境收回時間三處對齊 | ✅ 本 commit（DEPLOY.md §3.5、`appeal-backend-stack.ts` VPC 註解；proposal 那份本來就是新的） |
| 7 | 交件前：照 DEPLOY.md §6 收窄 ALB，確認 `AlbIngress` **與 SG 實況**都變成四組，重跑 `verify.sh` 全段 | ⏳ 2026-09-13 |
