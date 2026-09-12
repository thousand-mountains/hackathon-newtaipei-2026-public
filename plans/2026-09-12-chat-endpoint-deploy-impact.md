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
   ⚠️ 請求**必須帶 `run_id`**（契約見 `docs/spec/2026-09-12-chat-honesty-lamps.md`，搜 `"run_id"` 的 jsonc 區塊：必填且須為已完成的 run）。漏了它，這條檢查在系統完全正常時也會失敗。
2. **條件式 IAM 改法清單**——沿用 `BEDROCK_MODEL_ID_DRAFT` 就零改動；換第三顆模型 id 要改 `appeal-backend-stack.ts` 兩處＋`bin/app.ts` 一處，附不必真部署就能驗的 `cdk synth` 查 ARN 指令。
3. **交件前 ALB 收窄清單**——已落在 `backend/DEPLOY.md` §3.6／§6。

## 驗收（可執行，完整版見 delta-spec）

```bash
# 期望零輸出。基準釘 commit（不用 origin/main，它會漂）；濾註解行（驗資源與權限，不驗註解）
git diff -U0 e27d2a7 -- infra/cdk/lib/ infra/cdk/bin/ \
  | grep '^[+-]' | grep -vE '^(\+\+\+|---)' | grep -vE '^[+-][[:space:]]*//'
grep -c 'run_id' infra/cdk/verify.sh                       # 期望 ≥1
bash -n infra/cdk/verify.sh                                # 期望零輸出
/opt/homebrew/bin/python3 backend/tests/run_all.py         # 期望 374/374
```

第 5 段落地後要各跑一次：**不存在的 `run_id` 必須判失敗**（證明不恆真），**已知正常的部署必須通過**（證明不恆假）。

## 待 Ci 拍板（不要自己決定）

1. ~~**聊天要不要併進 1 RPS 節流器？**~~ **已定案：補 throttle**（每個工具進入點各呼叫一次）。理由是評審面前拿到 429 比慢 3–5 秒難看得多。⚠️ **只保證單一路徑**：甲案進程內有效，**乙案 AgentCore 一上線就破**（另一個容器，各節各的）。**賽制是否把聊天呼叫一起算，仍待查**——補 throttle 是保守選擇，不是查到了答案。
2. ~~**503 vs 501**~~ **已撤回**：兩份來源其實一致（都寫「tech-lead 已拍板 503、契約凍結」）。初稿的衝突宣稱源於行號漂移。不需拍板。
3. **要不要真的收窄 ALB？** 那四組是會場出口 IP，收窄後會場外連不進來，包含交件後才自己點開網址的評審。**先確認評審在哪裡看。**
4. ~~**環境收回時間兩說並存**~~ **已對齊三處**為「開到黑客松結束」。**來源等級照實記為「Ci 2026-09-12 口頭確認，非賽方書面」，不得升級成「賽方確認」。** Ci 會再確認一次。
