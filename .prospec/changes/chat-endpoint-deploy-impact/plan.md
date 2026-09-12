# Implementation Plan: chat-endpoint-deploy-impact

## Overview

甲案聊天端點的 plan／proposal／delta-spec 已明文拍板「部署零改動」。本 change **不推翻那個結論，而是把它變成可執行的驗證**——「零改動」是一個斷言，斷言要能被打勾或打叉。

策略：不新增任何 AWS 資源、不改 CDK。交付三樣東西：

1. **一條 `verify.sh` 的新檢查**（第 5 段），讓「聊天在雲上真的會串流」從推論變成實測。
2. **一張條件式 IAM 改法清單**（US-2），供「臨場被迫換模型」時照著打，不必現場翻程式碼。
3. **一張交件前 ALB 收窄清單**（已落在 `backend/DEPLOY.md` §6），因為那是有時限、只留在對話裡明天一定會漏的動作。

關鍵設計決策：

- **驗收要驗行為不驗形狀。** 第 5 段判準是「輸出裡真的出現 `data:` 行」，不是「HTTP 回 200」。ALB 緩衝、`X-Accel-Buffering` 掉了、SSE generator 沒 flush——這些壞法全都會回 200，只看狀態碼一條都抓不到。
- **檢查本身也要被驗一次。** 指到不存在的路徑時第 5 段必須判失敗。沒跑過這步，就不知道自己寫的是不是一條恆真檢查。
- **IAM 那條寫成條件式而非現在就做。** 現在沿用 `BEDROCK_MODEL_ID_DRAFT`，CDK 一行都不用改；把「換模型要改哪三處」寫下來的價值在於它是**臨場才會發生**的事，而臨場沒時間找。
- **四組會場 CIDR 刻意不寫死在任何檔案裡。** 寫下去就會被抄到過期的值，而這個值錯了的後果是評審連不進來。

## Affected Modules

| Module | Impact | Changes |
|--------|--------|---------|
| `infra/cdk/verify.sh` | **High** | 新增第 5 段「聊天 SSE」，插在第 4 段（守門 409，`:155-165`）之後、「資料隔離」區塊（`:167`）之前。用既有 `ok()`／`bad()`，不另發明 exit 機制 |
| `backend/DEPLOY.md` | **High** | ✅ 已完成於 `e27d2a7`：§3.6（`ALB_ALLOWED_CIDRS` 機制與 `AlbIngress` 查法）、§6（交件前檢查清單） |
| `backend/requirements.txt` | Low | 只改 `:19` 那句過時註解——寫「只有這個檔（`llm/client.py`）可以 import strands」，但實際被強制的是**整個 `backend/llm/` 目錄**（`backend/tests/run_all.py:423`）。加 `chat.py` 後這句會變成錯的 |
| `infra/cdk/lib/appeal-backend-stack.ts` | **None（這正是要證明的）** | 沿用 `BEDROCK_MODEL_ID_DRAFT` 的前提下零改動。條件式改法見步驟 4 |
| `infra/cdk/bin/app.ts` | **None** | 同上 |
| `backend/api/app.py`／`backend/llm/chat.py`／`backend/api/chat.py` | 不在本 change | 由 `chat-ask-agent` 負責 |

## Implementation Steps

1. **等 `chat-ask-agent` 把端點做出來**（本 change 的唯一硬相依）
   - 端點路徑確定為 `POST /api/cases/{case_id}/chat`、request body 欄位名確定
   - fixture 模式的狀態碼拍板（503 還是 501）——**在拍板前不要對它下斷言**

2. **在 `infra/cdk/verify.sh` 加第 5 段**
   - 插入點：`:166` 附近（第 4 段結束、資料隔離區塊開始之前）
   - 主體：
     ```bash
     chat_out="$(curl -N -s --max-time 60 -X POST \
       "$base/api/cases/synthetic-ordinary-01/chat" \
       -H 'content-type: application/json' \
       -d '{"message":"這個案子的受理期間怎麼算？"}' | head -c 2000)"
     if printf '%s' "$chat_out" | grep -q '^data:'; then
       ok "聊天端點回得出 SSE 事件"
     else
       bad "聊天端點沒有串流出任何 data: 事件（HTTP 通不代表串流通）"
     fi
     ```
   - 用 `synthetic-ordinary-01`（既有合成案例），不新增測資
   - 用既有 `ok`／`bad`；`bad` 自己會設 `fail=1`，整份腳本的 exit code 靠它

3. **反向驗這條檢查本身**
   - 把 URL 改成 `/api/cases/does-not-exist/chat` 跑一次，第 5 段必須判失敗
   - 沒做這步，就分不出「檢查通過」與「檢查根本不會失敗」

4. **確認「部署零改動」成立**（這是交付項，不是順手）
   - `git diff --stat origin/main -- infra/cdk/lib/ infra/cdk/bin/ backend/requirements.txt`
   - 期望：`infra/cdk/lib/`、`infra/cdk/bin/` 零輸出；`requirements.txt` 只有那句註解
   - **若這裡有輸出，代表甲案實際上動了 CDK**，「部署零改動」的前提就破了，要回頭看是哪裡走偏

5. **（條件式，現在不做）若改用第三顆模型 id**
   - `appeal-backend-stack.ts:11-20` 的 `AppealBackendStackProps` 加 `readonly modelIdChat: string;`
   - 同檔 `:81-84` 的 `invokeResources` 加 `...invokeArnsFor(props.modelIdChat, region, account)`
   - `infra/cdk/bin/app.ts:43-44` 附近加 `modelIdChat: required('BEDROCK_MODEL_ID_CHAT')`
   - 三處一處都不能漏。驗法是 `cdk synth` 後查 policy 放行的 ARN 有沒有多一顆（指令見 proposal US-2）
   - ⚠️ `cdk synth` 需要 `cdk.context.json`（VPC lookup 快取，已 gitignore）與 `CDK_DEFAULT_ACCOUNT`，否則停在 `StackAccountRegionNotSpecified`

6. **交件前（2026-09-13）：收窄 ALB 白名單**
   - 照 `backend/DEPLOY.md` §6 執行：`ALB_ALLOWED_CIDRS=<四組CIDR> ./deploy.sh deploy`
   - 驗：`AlbIngress` output 從 `0.0.0.0/0` 變成那四組，逐條核對沒有少打
   - 收窄後**重跑完整 `verify.sh`**（含第 5 段）——收窄動的是 SG，最容易壞的就是「自己也連不進去了」
   - 回滾：不帶 `ALB_ALLOWED_CIDRS` 重跑一次 deploy

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| 收窄後評審連不進來（那四組是**會場出口 IP**，會場外一律被擋，包含交件後才自己點開網址的評審） | **High** | **先確認評審在會場內還是會場外看，問到答案再收窄**。問不到就不收——維持 `0.0.0.0/0` 的損失遠小於評審點不開 |
| 第 5 段寫成恆真檢查（只看 HTTP 200） | **High** | 判準是真的出現 `data:` 行；並用不存在的路徑反向驗這條檢查會失敗（步驟 3） |
| 聊天改用第三顆模型 id 卻沒改 IAM | **High** | 線上 `AccessDenied`，**本機完全驗不出來**（本機走 Workshop Studio 憑證，權限比 task role 寬）。用步驟 5 的 `cdk synth` 查 ARN，靜態就驗得出來 |
| 誤啟用白名單（不該收窄時收窄了） | Medium | 每次部署完都查 `AlbIngress` output——那是唯一一眼分辨得出來的地方 |
| SSE 被 ALB 緩衝，事件不即時 | Medium | 沿用既有端點的 `X-Accel-Buffering: no`；ALB idle timeout 900 秒已足。第 5 段的 `curl -N` 會抓到 |
| 聊天與六節點 SSE 併發撐爆 threadpool（uvicorn 預設 40 條，兩者共用） | Low（demo 量級） | 容量假設而非改動。plan 自己承認「不是通用方案」（`.prospec/changes/chat-ask-agent/proposal.md:149`）。不在本 change 解 |
| 聊天呼叫計不計入賽制 ≤1 RPS **未查證** | Low | 標「待查」不主張。六節點與聊天共用同一個節流器，高並發下吞吐被壓低是可預期的 |
| fixture 模式狀態碼未拍板（503 vs 501） | Low | 拍板前第 5 段**不對 fixture 狀態碼下斷言**，只驗 bedrock 檔位 |
