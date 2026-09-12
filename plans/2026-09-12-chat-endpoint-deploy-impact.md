# 甲案聊天端點的部署影響盤點

> CONSTITUTION §5：開工前必有 plan。本檔是 `plans/` 這一側的入口，**完整內容在 prospec change**：
> `.prospec/changes/chat-endpoint-deploy-impact/`（`proposal.md` / `plan.md` / `delta-spec.md`）。
> 兩邊不要各寫一份會漂掉的版本——這裡只放「一眼看完」的摘要與指路。

- **日期**：2026-09-12
- **負責**：`-0b` session
- **狀態**：plan（實作等 `chat-ask-agent` 落地）
- **相關 commit**：`e27d2a7`（ALB 白名單併回 main，預設關閉）、`53566dd`（開 change）

## 一句話

甲案已拍板「部署零改動」。本 plan 不推翻它，是**把那句話變成一條會失敗的檢查**。

## 要交付的三樣

1. **`infra/cdk/verify.sh` 第 5 段**——驗聊天在雲上真的會串流。判準是真的收到 `data:` 行，不是 HTTP 200。
   ⚠️ 請求**必須帶 `run_id`**（`docs/spec/2026-09-12-chat-honesty-lamps.md:31-33` 契約，必填且須為已完成的 run）。漏了它，這條檢查在系統完全正常時也會失敗。
2. **條件式 IAM 改法清單**——沿用 `BEDROCK_MODEL_ID_DRAFT` 就零改動；換第三顆模型 id 要改 `appeal-backend-stack.ts` 兩處＋`bin/app.ts` 一處，附不必真部署就能驗的 `cdk synth` 查 ARN 指令。
3. **交件前 ALB 收窄清單**——已落在 `backend/DEPLOY.md` §3.6／§6。

## 驗收（可執行，完整版見 delta-spec）

```bash
git diff --stat e27d2a7 -- infra/cdk/lib/ infra/cdk/bin/   # 期望零輸出（基準釘 commit，不用 origin/main）
grep -c 'run_id' infra/cdk/verify.sh                       # 期望 ≥1
bash -n infra/cdk/verify.sh                                # 期望零輸出
/opt/homebrew/bin/python3 backend/tests/run_all.py         # 期望 374/374
```

第 5 段落地後要各跑一次：**不存在的 `run_id` 必須判失敗**（證明不恆真），**已知正常的部署必須通過**（證明不恆假）。

## 待 Ci 拍板（不要自己決定）

1. **聊天要不要併進 1 RPS 節流器？** 程式碼白紙黑字說聊天**不受保護**——`_throttle()` 只管 `_invoke_structured` 每次送出的請求，Strands agent loop 一次呼叫內的工具往返不經過它（`backend/llm/client.py:76-80`），而聊天正是 agent loop。賽方規範 ≤1 RPS（`client.py:62`）。**賽制是否把聊天呼叫一起算，待查。**
2. **503 vs 501**：`honesty-lamps.md:83` 說已凍結 503，`chat-ask-agent/proposal.md:200` 說請 Ci 確認。兩份打架，需一句話定案。
3. **要不要真的收窄 ALB？** 那四組是會場出口 IP，收窄後會場外連不進來，包含交件後才自己點開網址的評審。**先確認評審在哪裡看。**
4. **環境收回時間兩說並存**（既有矛盾，非本 change 造成）：`backend/DEPLOY.md:413-428` vs `.prospec/changes/deploy-backend-to-aws/proposal.md:32`，`appeal-backend-stack.ts:72` 註解也指回舊說法。要改三處一起改。
