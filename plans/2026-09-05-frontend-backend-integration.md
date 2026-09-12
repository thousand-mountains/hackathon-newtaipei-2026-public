# 前後端接起來：一個指令起一個平台（2026-09-05）

## 目標

現在有兩個各自成熟、但沒接上的東西：

- **五步動線前端**（`prototype/`）：原生 JS，資料由 `build.py` 把 `data/case-demo.json`
  字串替換內嵌，**零 fetch、零 API 呼叫**。
- **v2 六節點 backend**（`backend/`）：FastAPI 四支端點，fixture 檔位可端到端跑完。

要做的是：**一個指令起來、瀏覽器打開、前端真的打後端 API 把案子跑過五步**，
而且畫面上要誠實標示現在看到的是 live 後端還是離線 fixture，不能讓人搞不清楚。

## 步驟

1. **後端 payload 對齊 `docs/architecture.md` §6.2**
   （補頂層 `files[]`／`laws[]`／`cases[]`／`issues[]`／`intake.auto_fields`／`token_note`／`run_id`；
   每個新欄位在 `config/origin_registry.py` 有 `origin` 註冊）
2. **一個 process serve 整個平台**：`backend/api/app.py` 除 API 外也 serve
   `prototype/dist/index.html`，沿用 `prototype/app.py` 的 `FileResponse` + `StaticFiles` 慣例；
   補 CORS；`/api/health` 改成真的檢查（laws-snapshot 可載入、synthetic 案例可載入）
3. **前端雙模式**：載入時打 `/api/health`
   - 成功 → **live 模式**：`/api/cases` 取清單、`POST /api/cases/{id}/runs` 取 payload，
     五步全部用 payload 渲染；**燈號、引用四態、why 一律以後端為準，前端不重算**
     （CONSTITUTION §1：燈號不是模型／前端產出）
   - 失敗（例如 `file://` 直開）→ **離線 fixture 模式**：沿用內嵌的 `case-demo.json`
   - 頁首常駐徽章寫明是哪一種
4. **契約測試** `backend/tests/test_contract.py`（stdlib only）：對兩個 synthetic 案例
   逐欄斷言 §6.2 要求的欄位存在、型別正確、每欄有 origin 註冊

## 驗收條件（可執行）

- [ ] `uv run --with fastapi --with "uvicorn[standard]" --with pydantic -- python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8080`
      起得來；`GET /` 回五步 UI HTML；`GET /api/health` 200 且 checks 反映真實載入結果
- [ ] headless Chromium 開 `http://127.0.0.1:8080/`：徽章顯示 live、案例清單來自 `/api/cases`、
      `synthetic-ordinary-01` 五步全由 live payload 渲染、無 JS error、
      幕僚看板數字與燈號審核頁一致
- [ ] `synthetic-blocked-01`：送出審議 disabled、blockers 顯示、結論段為 human_required 佔位、
      對抗假引用（建築法第999條）的 adversarial 標記在畫面上看得到
- [ ] `file://` 開 `prototype/dist/index.html`：徽章顯示離線 fixture、原內容照常、無 JS error
- [ ] `python3 backend/tests/run_all.py` 全綠（含新契約測試）
- [ ] `node prototype/tests/parity.mjs` 16/16；`python3 prototype/build.py` 成功且 assert 無殘留注入點；
      prototype pytest 向量測試照過
- [ ] 無 secret、Python 依賴仍只有 fastapi/uvicorn/pydantic、前端無新外部依賴

## 回滾方式

前端改動集中在 `prototype/static/app.js` 與 `index.tmpl.html`，
`git revert` 對應 commit 即回到純離線單檔版；`dist/index.html` 由 `build.py` 重新產生。
後端改動是**加欄位不改既有欄位**，舊消費者（CLI、既有測試）不受影響。

## 備援方案

live 模式接不起來（例如 payload 對不上前端形狀且時間不夠）→
退回「後端 serve 靜態頁 + 徽章顯示離線 fixture」，
至少做到「一個指令起一個站」，前端維持 v0 行為。**不做半調子的假 live**。

## 最遲放棄時刻

本工作包開工 + 6h。

## 已知的範圍變更（要 Ci 追認）

`backend/tests/run_all.py` 的紅線檢查 `prototype/ 未被變更` 是 Phase 0（純後端）的約束，
本工作包的任務就是改前端，這條必然失敗——事實上在本 worktree **開工前就已經是紅的**
（main 分支的五步前端相對該基準 commit 已有變更，69/70）。
處置：把該檢查換成仍然有意義的守門（`dist/index.html` 可由 `build.py` 完全重現，
不得有手改的 dist；並把 `prototype/` 納入 secret／禁用雲端字樣掃描），不是靜靜刪掉。
