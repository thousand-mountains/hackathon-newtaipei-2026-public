# frontend-api-integration

把 Pink 的 Vue 前端從純 mock 接上真後端。

- **前端現況**：`frontend/`，Vite + Vue 3 + Tailwind v4，兩頁動線已完成，**零個 API 呼叫**——資料全來自 `src/data/*`（424 行寫死 mock）。`vite.config.js` 已預留 `/api` proxy。
- **後端現況**：live 全線可用，測試 270/270，已部署到 AWS 並驗收通過。
- **做完之後**：`prototype/` 整個目錄可以移除（舊五步動線前端），部署改指 `frontend/dist`。

---

## Background

**這不是接線，是補一整層。** 前端目前沒有任何 `fetch`／`EventSource`，所有畫面資料來自 `src/data/lawdb.js`／`caseDb.js`／`factDb.js`／`procedure.js`／`docBuilders.js`。要接後端等於新增一個 API 層，並把 `store/workbench.js` 的 `state` 從 mock 來源改成 payload 映射。

**舊前端（`prototype/static/app.js`）已經有一份跑通且今天剛驗過的資料層**，裡面解過的問題不要再解一次：202＋輪詢、SSE 逐節點事件、事件流斷線退回輪詢、確認閘門、失敗時不得偽裝成離線。**移植它的邏輯，不要從零想。**

### 後端契約（實查 `/openapi.json`）

```
GET    /api/health                     檔位、模型、檢索來源、provenance
GET    /api/cases                      案例清單
POST   /api/cases                      上傳卷證（multipart）
POST   /api/cases/{case_id}/runs       → 202 {run_id, result_url, events_url}
GET    /api/runs/{run_id}              → 409 還在跑 / 200 結果 / 502 節點失敗
GET    /api/runs/{run_id}/events       SSE 逐節點事件
POST   /api/cases/{case_id}/submit     → 200 或 409（後端重跑六節點，不信前端）
POST   /api/deadline                   期間試算
```

**live 檔位的 `POST /runs` 回 202 不是 200**，端到端 51–78 秒。`POST /submit` 是**同步**的，同樣要 60–75 秒。

---

## User Stories

### US-1: 畫面資料來自後端，不是內建 mock [P1]

**作為**承辦人，**我想要**看到的法條、相似案例、爭點、草稿都是這次執行的結果。

驗收：
- `src/data/*` 的寫死資料不再供給正式流程（可保留為離線 fallback，但**必須明示**，見 US-5）
- `state.SENTS`／`lawCards`／`caseCards`／`factCards` 全部由 `/api/runs/{id}` 的 payload 映射而來
- 關掉後端再開頁面，畫面**不得**看起來像正常運作

### US-2: 呼叫模型只能由使用者動作觸發 [P1]

**作為**開發者，**我想要**每一次 Bedrock 呼叫都對應一個明確的按鈕。

驗收：
- 進頁面不自動發 run。舊前端踩過：每開一次／每重整一次燒一次 Opus 並等約 60 秒
- 「確認上傳，生成草稿」是唯一的觸發點
- 執行中按鈕 disabled 並顯示進度，不得看起來像卡死

### US-3: 後端活著但這次跑失敗時，畫面不得偽裝成離線 [P1]

**作為**這個產品的設計者，**我想要**系統據實說出失敗原因。

驗收：
- **「連不上後端」與「連上了但這次跑失敗」必須是兩種狀態、兩種文案**
- run 失敗時**不得**載入 `src/data/*` 的 mock 演完全程
- 真正的錯誤訊息顯示在畫面上，**不是塞進 tooltip**（demo 現場沒人會去 hover）

> 舊前端的原始 bug：`postRun` 與 health 探測共用同一個 catch，任何執行失敗都退回內嵌 fixture，徽章還寫死「離線 fixture（未接後端）」。修法見 `prototype/static/app.js` 的 `boot()` 與 `loadDemo()`。

### US-4: 送出守門不得因為動線改版而消失 [P1]

**作為**評審，**我想要**看到系統會拒絕它不該生成的東西。

驗收：
- 「下載檔案」之前要有一道由**後端**決定的閘：`POST /api/cases/{id}/submit`
- 後端回 **409** 時不得下載，且要顯示 blockers（含 C 型 `conclusion_requires_human`）
- **前端不得自行判定可否送出**——`submit_allowed` 只認後端

> 後端明說：「本回應的 `submit_allowed` 由後端重跑六節點得出，未採信前端送來的任何判斷。」前端要尊重這件事。

### US-5: 三個誠實維度要帶到畫面上 [P1]

**作為**評審，**我想要**知道我看到的是即時推論還是離線重播、資料是合成還是真實、檢索範圍有多大。

驗收：`/api/health` 的 `provenance` 有三個獨立欄位，畫面要有對應呈現：
- `execution_note`——bedrock 即時推論／fixture 離線重播
- `data_note`——合成測資／承辦人上傳
- `retrieval_note`——檢索來源與**「入庫清單不等於已索引」**的差異

**不得**把三者合併成一句話含糊帶過，那正是今天修掉的 bug。

### US-6: 承辦人必須走得到「確認抽取欄位」 [P1]

**作為**後端，**我需要**知道欄位是否經過人工確認，才能決定要不要解除結論封鎖。

驗收：
- `POST /runs` 的 body 要能帶 `confirmed_intake`
- 未確認時後端維持 `conclusion_requires_human`，**這是正確行為不是 bug**
- **新設計拿掉了舊版「勾選我已核對全部欄位」的閘門**，Ci 已拍板改為**內嵌在「案情與爭點」分頁**
- 若永遠沒被設過，系統在 demo 裡看起來只會拒絕、不會產出——**對照組會消失**

### US-7: 逐節點進度（SSE） [P2]

**作為**使用者，**我想要**在等 60 秒時知道現在跑到哪裡。

驗收：
- 接 `GET /api/runs/{id}/events`，逐節點顯示進度
- **事件流斷線一律退回輪詢**——事件沒了不代表執行沒了，把它當失敗就是前端自己編一個後端沒說的結論
- SSE 不是取結果的路徑，收到 `run_done` 後仍要 `GET result_url` 取 payload

### US-8: 引用卡片要分開呈現「字號可驗」與「與本案相關」 [P1]

**作為**承辦人，**我想要**分辨「這個字號存在」與「這個判解跟我的案子有關」。

驗收：
- 引用的綠燈只代表**字號在快照內可驗**，不得讓人讀成「內容相關」
- 標 `out_of_scope`（庫外未驗證）的要明確區分，不是紅燈也不是綠燈

> 實測發現：`doc_kind` filter 保證回判解，但**不保證相關**——同一個查詢與一個完全不相干的問題回傳 6 筆中有 3 筆重疊。而 N6 只驗字號存在、不驗相關性。**「可驗但不相干」會在畫面上看起來跟「可驗且相干」一模一樣。**

---

## Edge Cases

| 情境 | 預期行為 |
|---|---|
| 後端完全連不上 | 明示「未連上後端」，可保留 mock 但**必須標示** |
| run 回 502（節點失敗） | 顯示後端給的原因，不退回 mock |
| SSE 斷線但 run 還在跑 | 退回輪詢，不當作失敗 |
| 輪詢逾時 | 舊前端用 10 分鐘。**ALB idle timeout 目前 900 秒 > 客戶端 600 秒**，順序正確：客戶端先放棄，走我們自己的錯誤訊息 |
| `/submit` 要 60–75 秒 | 要有進度指示，不能看起來像當掉 |
| 使用者連點兩次 | 互斥閘，不得同時發兩個 run |

---

## Success Criteria

```bash
# 本機：起後端（換成你自己的 port），前端 pnpm dev
# 1) 不自動發 run
#    開頁面 → 後端 log 不得出現 POST /runs
# 2) 按鈕觸發 → 畫面資料來自後端
#    payload 的 run_id 要能在畫面上對得出來
# 3) 故意讓後端失敗（把 .env 的 BEDROCK_MODEL_ID_DRAFT 改成不存在的 id）
#    → 畫面顯示真正原因，且不得演完全程
# 4) C 型案件 synthetic-blocked-01
#    → 下載前被擋，顯示 P0 conclusion_requires_human
```

**第 3、4 條是紅線。** 第 3 條沒過＝系統會說謊；第 4 條沒過＝差異化消失。

---

## Related Modules

| 模組 | 關係 |
|---|---|
| `frontend/src/store/workbench.js` | 主要改動點：`state` 改由 payload 映射 |
| `frontend/src/data/*` | 退為離線 fallback 或移除 |
| `frontend/vite.config.js` | 已設 `/api` proxy（預設 `localhost:8091`），確認與實際 port 一致 |
| `prototype/static/app.js` | **參考實作**：202＋輪詢、SSE、確認閘門、誠實錯誤處理都在裡面 |
| `prototype/` | 本 change 完成後整個目錄可移除 |
| `backend/Dockerfile` | 目前 `COPY prototype/dist/`，改前端後要改成 `frontend/dist` |
| `.prospec/changes/deploy-backend-to-aws/` | 部署 change，由另一 session 負責；本 change 完成後由它換版重新部署 |

---

## Open Questions

1. **`prototype/` 何時移除？**建議本 change 驗收通過、且新前端部署上去驗過四條之後再刪，不要先刪。
2. **離線 mock 要不要保留？**保留的話是斷網 demo 的備援，但**必須明示**；不保留就沒有備援。建議保留並明示。
3. **`POST /submit` 同步 60–75 秒**是否要改成 202＋輪詢。目前靠 ALB idle timeout 900 秒撐著，那是止血不是解法。**這是後端的事，不在本 change**。

---

## Constitution Check

| 原則 | 對應 |
|---|---|
| 分層誠實 | US-3、US-5、US-8 |
| 引用必可驗 | US-8：可驗 ≠ 相關，畫面要分得開 |
| 規則引擎零 LLM | 期間計算結果由後端給，前端不重算 |
| 不編造測資 | mock 保留須明示；不得在 run 失敗時拿 mock 頂替 |
| 30h 紀律 | 移植舊前端已解過的邏輯，不重想 |

---

## Next Steps

| # | 步驟 |
|---|---|
| 1 | 新增 `src/api/` 層：`health`／`cases`／`runs`（202＋輪詢＋SSE）／`submit` |
| 2 | `store/workbench.js` 的 `state` 改由 payload 映射；`src/data/*` 退為明示的 fallback |
| 3 | 接確認閘門（US-6）與送出守門（US-4） |
| 4 | 三個誠實維度上畫面（US-5）、引用卡片分層（US-8） |
| 5 | 跑 Success Criteria 四條，**第 3、4 條要貼實測結果** |
| 6 | 交給部署 session 換版並重驗部署四條 |
| 7 | 確認無誤後移除 `prototype/` |
