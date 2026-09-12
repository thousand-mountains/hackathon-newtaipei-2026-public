# Implementation Plan: chat-endpoint-deploy-impact

## Overview

甲案聊天端點的 plan／proposal／delta-spec 已明文拍板「部署零改動」。本 change **不推翻那個結論，而是把它變成會失敗的檢查**——「零改動」是斷言，斷言要能被打叉。

策略：不新增任何 AWS 資源、不改 CDK。交付三樣東西：

1. **一條 `verify.sh` 的新檢查**（第 5 段），讓「聊天在雲上真的會串流」從推論變成實測。
2. **一張條件式 IAM 改法清單**（步驟 5），供「臨場被迫換模型」時照著打。
3. **一張交件前 ALB 收窄清單**（落在 `backend/DEPLOY.md` §6），因為那是有時限、只留在對話裡明天一定會漏的動作。

關鍵設計決策：

- **驗收要驗行為不驗形狀。** 第 5 段判準是「輸出裡真的出現 `data:` 行」，不是「HTTP 回 200」。ALB 緩衝、`X-Accel-Buffering` 掉了、SSE generator 沒 flush——這些壞法全都會回 200。
- **恆真與恆假要分開驗。** 反向驗證（指到不存在的 `run_id` 必須失敗）只證明「不恆真」；要證明「不恆假」，得在一個已知正常的部署上真的跑通一次。**一條參數寫錯的檢查永遠失敗，反向驗證照樣「通過」**——這是本 change 自己踩過的坑（初稿的 curl 漏了必填的 `run_id`）。
- **IAM 那條寫成條件式而非現在就做。** 現在沿用 `BEDROCK_MODEL_ID_DRAFT`，CDK 一行都不用改；把「換模型要改哪三處」寫下來的價值在於它**臨場才會發生**，而臨場沒時間找。
- **四組會場 CIDR 刻意不寫死在任何檔案裡。** 寫下去就會被抄到過期的值，而這個值錯了的後果是評審連不進來。

## Affected Modules

| Module | Impact | Changes |
|--------|--------|---------|
| `infra/cdk/verify.sh` | **High** | 新增第 5 段「聊天 SSE」，插在第 4 段（守門 409，`:155-165`）之後、「資料隔離」區塊（`:167`）之前。用既有 `ok()` `:31`／`bad()` `:30`，重用第 3 段的 `rid`（`:116`）。**連同 `:2` 檔頭「Success Criteria 四條」一起改成五條** |
| `backend/DEPLOY.md` §3.4 標題（`:325`） | **High** | 現寫「`./verify.sh`，四條全綠才算完成」，第 5 段落地當下就變錯 |
| `backend/DEPLOY.md` §3.6／§6 | **High** | ✅ 主體完成於 `e27d2a7`（`ALB_ALLOWED_CIDRS` 機制、`AlbIngress` 查法、交件前清單）；`.env` 覆蓋陷阱、憑證步驟、SG 實況查法為後續補強 |
| `backend/requirements.txt` | Low | 只改 `:19` 那句過時註解——寫「只有這個檔（`llm/client.py`）可以 import strands」，但實際被強制的是 `DEPENDENCY_EXEMPT_DIRS = ("api", "llm")`（`backend/tests/run_all.py:262`），**目錄級**。加 `chat.py` 後那句會誤導人。**不得新增套件行** |
| `infra/cdk/lib/appeal-backend-stack.ts` | **None（這正是要證明的）** | 沿用 `BEDROCK_MODEL_ID_DRAFT` 的前提下零改動。條件式改法見步驟 5 |
| `infra/cdk/bin/app.ts` | **None** | 同上 |
| `backend/api/app.py`／`backend/llm/chat.py`／`backend/api/chat.py` | 不在本 change | 由 `chat-ask-agent` 負責 |

## Implementation Steps

1. **等 `chat-ask-agent` 把端點做出來**（本 change 的唯一硬相依）
   - 端點路徑 `POST /api/cases/{case_id}/chat`；request body 契約已定於 `docs/spec/2026-09-12-chat-honesty-lamps.md:31-33`：`{run_id（必填，須為已完成的 run）, message（必填非空）, session_id?, context?}`，`case_id`／`run_id` 不同案回 400
   - fixture 模式狀態碼**已凍結為 503**（同檔 `:83`，tech-lead 拍板，開工後不改）

2. **在 `infra/cdk/verify.sh` 加第 5 段**
   - 插入點：`:166` 附近（第 4 段結束、資料隔離區塊開始之前）
   - 重用第 3 段跑完的 run（`rid` 在 `:116` 取得，`:167` 仍在作用域）：
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
   - ⚠️ **`run_id` 不能省。** 漏掉它，這條檢查在**系統完全正常**時也會判失敗——恆假型驗收比恆真更毒，它會訓練人忽略 `verify.sh`
   - `-n "$rid"` 守衛負責把「第 3 段沒跑成」與「聊天壞了」分開；硬相依第 3 段是刻意的（另跑一個 run 多花 50–80 秒，而第 3 段失敗本來就不該交件）
   - 用 `synthetic-ordinary-01`（既有合成案例），不新增測資

3. **兩個方向都驗這條檢查本身**
   - **不恆真**：把 `run_id` 換成不存在的值跑一次，必須判失敗
   - **不恆假**：在已知正常的部署上跑一次，必須通過
   - 只做前者，就會漏掉「參數寫錯導致永遠失敗」這一類——本 change 初稿正是這樣錯的

4. **同步改兩處「四條」的字樣**
   - `infra/cdk/verify.sh:2` 檔頭、`backend/DEPLOY.md:325` §3.4 標題
   - 不改的話，第 5 段落地當下這兩處就變成錯的

5. **確認「部署零改動」成立**（這是交付項，不是順手）
   - `git diff --stat e27d2a7 -- infra/cdk/lib/ infra/cdk/bin/` → 期望零輸出
   - ⚠️ **基準要釘在 commit，不要用 `origin/main`**：`origin/main` 會跟著甲案一起前進，用它當基準這條**兩端都綠**（併之前沒東西可比、併之後基準跟著動），是一條恆真檢查
   - `git diff --stat e27d2a7 -- backend/requirements.txt` → 期望只有 `:19` 那句註解
   - 若 `infra/cdk/lib/`／`bin/` 有輸出，代表甲案實際上動了 CDK，前提破了，要回頭看哪裡走偏

6. **（條件式，現在不做）若改用第三顆模型 id**
   - `appeal-backend-stack.ts:11` 起的 `AppealBackendStackProps` 加 `readonly modelIdChat: string;`
   - 同檔 `:94` 的 `invokeResources` 加 `...invokeArnsFor(props.modelIdChat, region, account)`（`invokeArnsFor()` 在 `:46`，policy statement `InvokeNamedModelsOnly` 在 `:101`）
   - `infra/cdk/bin/app.ts:43-44` 附近加 `modelIdChat: required('BEDROCK_MODEL_ID_CHAT')`
   - 三處一處都不能漏。驗法是 `cdk synth` 後查 policy 放行的 ARN 有沒有多一顆（指令見 proposal US-2）
   - ⚠️ `cdk synth` 需要 `cdk.context.json`（VPC lookup 快取，已 gitignore）與 `CDK_DEFAULT_ACCOUNT`，否則停在 `StackAccountRegionNotSpecified`

7. **交件前（2026-09-13）：收窄 ALB 白名單**
   - 完整清單在 `backend/DEPLOY.md` §6，以那份為準。順序不可跳：
     0. 重取 Workshop Studio 臨時憑證（`deploy.sh:43` 的 `aws sts get-caller-identity` 會第一個擋你，錯誤訊息跟 ALB 無關）
     0b. 確認 `.env` 裡沒有 `ALB_ALLOWED_CIDRS`（`deploy.sh:32-35` 的 `set -a; . "$env_file"` 在命令列變數**之後**執行，會靜默覆蓋，**且讓回滾失效**。實查 2026-09-12：目前沒有）
     1. `cd infra/cdk && ALB_ALLOWED_CIDRS=<四組CIDR> ./deploy.sh deploy`
     2. 查 `AlbIngress` output **並且** `aws ec2 describe-security-groups` 查 SG 實況
     3. 重跑完整 `verify.sh`（含第 5 段）——收窄動的是 SG，最容易壞的就是「自己也連不進去了」
   - 回滾：不帶 `ALB_ALLOWED_CIDRS` 重跑一次 deploy（前提是步驟 0b 成立）

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| **第 5 段漏送必填的 `run_id` → 恆假檢查** | **High** | 契約在 `honesty-lamps.md:31-33`。步驟 2 的寫法已帶 `run_id`；步驟 3 的「不恆假」方向專門擋這類 |
| 收窄後評審連不進來（那四組是**會場出口 IP**，會場外一律被擋，包含交件後才自己點開網址的評審） | **High** | **先確認評審在會場內還是會場外看，問到答案再收窄**。問不到就不收——維持 `0.0.0.0/0` 的損失遠小於評審點不開 |
| 聊天改用第三顆模型 id 卻沒改 IAM | **High** | 線上 `AccessDenied`，**本機完全驗不出來**（本機憑證權限比 task role 寬）。用步驟 6 的 `cdk synth` 查 ARN，靜態就驗得出來 |
| **聊天繞過 1 RPS 節流器**（`_throttle()` 只管 `_invoke_structured` 送出的請求，**Strands agent loop 的工具往返不經過它**——`backend/llm/client.py:76-80` docstring 明寫；聊天正是 agent loop，一輪可能連打 3–5 次） | **High（賽制風險）** | ⚠️ **未緩解**。賽方規範 ≤1 RPS（`client.py:62`）。選項見 `honesty-lamps.md:371-373`：在 `chat.py` 每個工具進入點各呼叫一次 `_throttle()`（一輪多等約 3–5 秒，估計未量測），或承認可能短暫超標。**待 Ci 拍板**；賽制是否把聊天呼叫一起算仍**待查** |
| 用 `origin/main` 當「零改動」基準 → 恆真檢查 | Medium | 步驟 5 已改成釘 `e27d2a7` |
| `.env` 被加了 `ALB_ALLOWED_CIDRS` → 命令列值被靜默覆蓋、**回滾失效** | Medium | 步驟 7 的 0b。實查 2026-09-12：目前 `.env` 沒有這個變數 |
| `AlbIngress` 說謊（值從 props 推導，不讀 SG 實況；`appeal-backend-stack.ts:232`） | Medium | 步驟 7 步驟 2 要求 `describe-security-groups` 一起查 |
| 誤啟用白名單（不該收窄時收窄了） | Medium | 每次部署完都查 `AlbIngress` ＋ SG 實況 |
| SSE 被 ALB 緩衝，事件不即時 | Medium | 沿用既有端點的 `X-Accel-Buffering: no`（`app.py:459-463`）；ALB idle timeout 900 秒（`appeal-backend-stack.ts:256`）已足。第 5 段的 `curl -N` 會抓到 |
| 第 5 段落地後忘了改兩處「四條」字樣 | Low | 步驟 4 明列 |
| 聊天與六節點 SSE 併發撐爆 threadpool（uvicorn 預設 40 條，兩者共用） | Low（demo 量級） | 容量假設而非改動。不在本 change 解 |
