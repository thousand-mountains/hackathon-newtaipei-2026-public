# rebuild-staff-workbench-frontend

> **交接對象**：Pink（前端 Vue3）
> **開立者**：`-76` session，2026-09-12。派工來源：Ci 拍板「前後端會分離，前端 Vue Pink 會處理」。
> **後端狀態**：已於 2026-09-12 12:13 實跑驗證通過（live bedrock 檔位，詳見 §Backend Contract）。
> **本文件的事實分級**：標「實測」的是本 session 實跑量到的；標「-72 實查」的是 `-72` session 查證的；
> 標「未驗證」的就是還沒有人證實過，不要當成已知事實去實作。

---

## Background

現行前端是「五步動線 ＋ 七張幕僚卡看板」的單檔內嵌 HTML（`prototype/static/app.js` 1637 行，
經 `prototype/build.py` 組成 `prototype/dist/index.html`）。它有三個問題：

1. **動線太長**：五步逐頁前進，承辦人要看到草稿得先過四關；幕僚看板佔掉大量畫面卻不影響決策。
2. **前後端沒分離**：fixture 資料內嵌在建置產物裡，前端在後端失敗時會靜默退回內嵌 fixture
   演完全程，徽章還寫死「離線 fixture（未接後端）」——**後端明明活著、只是跑失敗了**（`-72` 實查）。
   這直接違反 CONSTITUTION 分層誠實。
3. **沒打中命題方需求**：2026-09-12 上午法制局長官到場列的六項需求，現況只有一項完全命中
   （見 §Requirement Coverage）。

新設計（`design/petition-ai-demo.html`，commit `aaa50b5`「單頁版」；
另有較新的 artifact `https://claude.ai/public/artifacts/f1bdfcca-24d3-42e9-82ec-dbd57b7f8089`）
把動線收成兩頁、移除幕僚看板。但**設計稿是純前端 mock**：`setTimeout(…,1400)` 假裝生成、
句子寫死、零 API 呼叫。本 Story 要做的是把它實作成接真後端的 Vue3 前端。

---

## User Stories

### US-1: 兩頁動線取代五步動線 [P1]

As a 訴願案件承辦人,
I want 上傳卷證後直接進到一個工作台，左邊看分析、右邊看草稿,
So that 我不必為了看到草稿而逐頁前進四次。

**Acceptance Scenarios:**

- WHEN 我在上傳頁（`p-up`）選好卷證檔並按下產生分析, THEN 畫面進入工作台（`p-work`），
  左欄顯示分析、右欄顯示決定書草稿。
- WHEN 我在工作台, THEN 畫面上**不存在**七張幕僚卡的看板區塊（該區塊整個移除，不是隱藏）。
- WHEN 後端還在跑（`POST /runs` 回 202 後、輪詢還是 409）, THEN 畫面顯示進行中狀態並附
  逐節點進度（可接 SSE `events_url`），不顯示任何草稿內容。

**Independent Test:**
起後端服務，於瀏覽器實際走「選檔 → 產生 → 工作台」，確認中途沒有第三個頁面、
且 DOM 內查無幕僚卡容器。

---

### US-2: 左欄三分頁，順序固定為 案情與爭點 → 相關法條 → 相似案例 [P1]

As a 承辦人,
I want 左欄按我的審理順序分成三個分頁,
So that 我先確認案情與爭點，再看法條，最後參考相似案。

**Acceptance Scenarios:**

- WHEN 工作台載入, THEN 左欄分頁**由左至右依序**為 ①案情與爭點 ②相關法條 ③相似案例，
  且預設選中 ①。（**注意：這個順序是 Ci 2026-09-12 確認的，跟設計稿現有順序不同——
  以本文件為準，不要照設計稿的順序做。**）
- WHEN 我切到①案情與爭點, THEN 區塊內含：文件辨識產出的摘要（事實經過、訴願主張）、
  程序審查、案件爭點、相關法條（法規調整）。
- WHEN 我切到②相關法條, THEN 每一條顯示條號、燈號、以及它是否對得回法規快照
  （`laws[].lamp` / `gate_status`）。
- WHEN 我切到③相似案例, THEN 每一筆顯示來源與其 `provenance`（`official` 賽方資料集 /
  `public_crawl` 市府公開爬蟲），**兩種來源必須在畫面上分得出來**。

**Independent Test:**
對 `synthetic-ordinary-01` 跑一次 live run，逐一比對三個分頁的內容與 payload 的
`issues[]` / `laws[]` / `cases[]` 三個陣列一致。

---

### US-3: 爭點彙整要做成三方對照，不是單向摘要 [P1]

As a 承辦人,
I want 爭點以「訴願人主張 ／ 機關答辯 ／ 證據是否支持」三欄並列呈現,
So that 我一眼看得出雙方歧異落在哪、以及卷證有沒有支撐。

**Acceptance Scenarios:**

- WHEN 我看①案情與爭點的爭點區, THEN 每個爭點以三方對照呈現（主張／答辯／證據），
  不是單向的案情摘要。
- WHEN 某一方的內容在卷證裡找不到, THEN 該欄明確顯示「卷證未見」，
  **不得留白讓人誤讀成沒有爭執，也不得由模型補寫**（CONSTITUTION 不編造）。
- WHEN 爭點屬事實認定, THEN 顯示紅燈與「需人工認定」標籤（後端 `issues[].lamp` 固定為 `r`）。

**Independent Test:**
餵一個機關答辯書缺漏的案子，確認「機關答辯」欄顯示「卷證未見」而非空白或生成內容。

> **這條是命題方需求缺口，不是現況**：後端目前的 `issues[]` 只有
> `{id, t, q, src, lamp, tag, severity, origin}`——**沒有三方對照的欄位**。
> 本 Story 需要後端配合擴充，屬跨端相依，見 §Open Questions Q1。

---

### US-4: 撰稿 AI 可對全文或單句下四種指令 [P2]

As a 承辦人,
I want 選定全文或某一句，叫 AI 精簡／加強論述／補強對訴願主張之回應／改為公文正式語氣，也能直接對它提問,
So that 我修稿不必自己重打。

**Acceptance Scenarios:**

- WHEN 我選「全文」並按四個動作之一, THEN 右欄草稿依該動作更新，且更新後的句子
  **重新跑一次後端守門**（不得由前端自行判定新句子可信）。
- WHEN 我選某一句並按動作, THEN 只有該句改變，其餘句子不動。
- WHEN 我用對話式向 Agent 提問, THEN 回覆顯示在撰稿 AI 區，且**不自動寫進草稿**（要我確認）。

**Independent Test:**
對單句執行「精簡」，確認 diff 只有那一句、且該句的燈號與 `why` 由後端重新回傳而非前端保留舊值。

---

### US-5: 右欄草稿的閱覽／編輯雙模式與逐句依據 [P1]

As a 承辦人,
I want 草稿能切閱覽與編輯，點任一句就看到它的依據,
So that 我能逐句確認這句話憑什麼這樣寫。

**Acceptance Scenarios:**

- WHEN 我點草稿任一句, THEN 顯示該句的 `basis`、`refs`（`L*` 法條 / `I*` 爭點 / `R*` 判解函釋）、
  `origin` 與燈號。
- WHEN 某句的 `placeholder` 為 true, THEN 該句明確標示為佔位、不得看起來像已完成的內容。
- WHEN 結論段被封鎖（`screen.requires_human_conclusion` 為 true）, THEN 右欄**沒有結論段**，
  並顯示交接卡（`handoff.criterion.text` ＋ `handoff.questions`）說明為什麼停在這裡。

**Independent Test:**
跑 `synthetic-blocked-01`，確認右欄無結論段、且交接卡顯示的是 `handoff.criterion.text`
（本案真正的操作判準），而不是把 `handoff.signals` 整串當成封鎖原因列出。

---

### US-6: 後端失敗時前端必須說出真正原因，不得退回 fixture [P1]

As a 評審或承辦人,
I want 系統跑失敗時直接告訴我它失敗了、失敗在哪一個節點,
So that 我不會把一份離線假資料誤當成即時分析結果。

**Acceptance Scenarios:**

- WHEN `POST /runs` 或輪詢回 5xx（模型 throttle／權限／逾時）, THEN 畫面停在錯誤狀態，
  顯示後端回的 `node` 與 `error` 原文，**不顯示任何草稿內容**。
- WHEN 後端回 502, THEN 前端**不得**載入任何內嵌 fixture 演完流程。
- WHEN 前端處於離線示範模式（若保留該模式）, THEN 徽章文案必須與「接後端但失敗」明確不同。

**Independent Test（`-72` 的原始驗收條件 4）：**
把 `.env` 的 `BEDROCK_MODEL_ID_DRAFT` 改成不存在的 id 跑一次，前端要顯示真正的失敗原因。
驗完改回。（**`.env` 是 `-72` 的地盤，要驗先跟它說。**）

> **現況根因（`-72` 實查，本 session 逐行覆核確認）**：`prototype/static/app.js` 的
> `postRun()` 在 `app.js:201`，與 `fetch('api/health')`（`app.js:186`）**同在 `boot()`
> 的同一個 `try`（`app.js:182`）裡，共用 `app.js:205` 那個 catch**。
> 任一失敗都掉進同一條退路：`MODE='offline'` ＋ 載入內嵌 `data/case-demo.json` 演完全程。
>
> **一處我要把它講得比原始通報更精確**（影響 Pink 怎麼修）：錯誤字串其實**有**被帶出來——
> `app.js:207` 會把 `String(e.message)` 附在詳述文字裡。所以它不是完全靜默。
> **真正說謊的是寫死的徽章標題**：`app.js:206` 固定顯示「離線 fixture（未接後端）」、
> 詳述固定開頭「本頁未連上後端 API」——而後端明明連上了、只是 run 跑失敗。
>
> 所以修法不是「補上錯誤訊息」（那已經有了），而是
> **把「連不上後端」與「連上了但 run 失敗」當成兩種不同狀態，各自有各自的文案與畫面**，
> 且後者**不得載入任何內嵌 fixture**。

---

### US-7: 產生分析只能由使用者按鈕觸發 [P1]

As a 專案成員,
I want 開頁或重整不會自動觸發一次模型執行,
So that 我們不會每次刷新就燒一次 Opus、等約一分鐘。

**Acceptance Scenarios:**

- WHEN 我開啟或重整前端頁面, THEN **不發出任何 `POST /runs`**。
- WHEN 我按下「產生分析」, THEN 才發出 `POST /runs`。

**Independent Test:**
開 DevTools Network，重整頁面三次，確認 `POST /runs` 次數為 0。

> **現況（`-72` 實查）**：首頁 `boot()` 就自動 `POST /runs`。實測端到端約 **51 秒**
> （單次 run）到 **117 秒**（submit 重跑），每次都是真的 Bedrock 呼叫。

---

### US-8: 送出守門不得因為動線改版而消失 [P1]

As a 法制局,
I want 「結論涉及法律判斷」的案子在產出檔案前被擋下來,
So that 不會有一份系統不該生成結論的決定書被下載出去當成成品。

**Acceptance Scenarios:**

- WHEN 案件的 `submit_allowed` 為 false, THEN 「下載 PDF／Word」**被擋住**，
  並顯示 `blockers[]` 每一條的 `reason` 與 `detail`。
- WHEN 我嘗試繞過 UI 直接打 `POST /api/cases/{id}/submit`, THEN 後端回 409
  （**這一條後端已實測成立，見 §Backend Contract**）。
- WHEN `submit_allowed` 為 true, THEN 才允許下載，且畫面明說本動作
  `external_effect: "none"`（沒有寄信、沒有排議程、沒有送進任何外部系統）。

**Independent Test:**
在新 UI 上對 `synthetic-blocked-01` 走到底，確認下載被擋、且擋的理由來自後端 `blockers[]`。

> **設計稿拿掉了送出閘門，只留「下載檔案」。** `-72` 的預設打法是
> **把閘門接在「下載檔案」之前**——動線照新設計，守門不拆。本 Story 照這個做。
> 這是 CONSTITUTION 分層誠實的第三層，也是本專案最大的差異化，**不得為了動線簡潔而刪除**。

---

### US-9: 承辦人必須走得到「確認抽取欄位」這個動作 [P1]

As a 承辦人,
I want 在畫面上確認（或修改）系統從卷證抽出來的欄位,
So that 程序判斷不是建立在沒人看過的模型抽取結果上。

**Acceptance Scenarios:**

- WHEN 工作台載入且 `screen.procedural_inputs_confirmed` 為 false, THEN 畫面明確標示
  「這些欄位尚未經你確認」，並列出 `screen.unconfirmed_procedural_fields`。
- WHEN 我確認欄位, THEN 前端以 `POST /runs` 的 body 帶 `confirmed_intake`
  （只能搭配 `from_node` 為 `n1` 或 `n2`），後端才會把 `intake_origin` 記成 `human`。
- WHEN 欄位都確認過, THEN `screen.procedural_inputs_confirmed` 變 true，
  且 `conclusion_requires_human` 這條 blocker 消失（若無其他封鎖原因）。

**Independent Test:**
對 `synthetic-ordinary-01` 先不帶 `confirmed_intake` 跑一次（應被擋），
再帶齊欄位跑一次，確認 blocker 從 2 條降為 1 條、且少掉的那條是 `conclusion_requires_human`。

> **這是本 session 實測發現的缺口，優先序等同 US-8。**
> `synthetic-ordinary-01` 本來的設計用途是「不觸發結論封鎖，用來對照 `synthetic-blocked-01`」，
> 但我不帶 `confirmed_intake` 實跑，它**也被封鎖了**（blocker: `conclusion_requires_human`，
> 因為 `procedural_inputs_confirmed: false`）。這是後端設計上正確的行為（判斷卡 7），
> 但**新設計把「勾選我已核對全部欄位」的閘門拿掉了**，於是：
> - Demo 時如果走不到確認動作，兩個案子都會被擋，「我們也放得過」那半邊的對照組就消失了；
> - 更嚴重的是，`confirmed_intake` 若永遠沒被設過，系統就永遠停在「交人工」，等於這個產品
>   在 demo 裡看起來只會拒絕、不會產出。
>
> **所以新設計必須明確回答：`confirmed_intake` 在哪個畫面、由哪個動作設成 true。**

---

## Edge Cases

- **上傳案（`upload-` 前綴）在 fixture 檔位跑 runs**：後端回 400 並附原因（上傳案沒有可重播的
  fixture，只能在 `RUN_MODE=bedrock` 跑）。前端要照實顯示，不要說成「系統忙碌」。
- **SSE 事件流中斷**：後端事件只活在 process 記憶體裡、不持久化也不回放。
  前端要能退回輪詢 `GET /api/runs/{id}`，**不得因為事件流斷了就判定結果不見了**。
- **process 重啟後打 events**：回 404，訊息會指回 `GET /api/runs/{run_id}`。照它說的做。
- **相似案通道查不到 vs 查不了**：後端把三種情形分開講（無資料集／查無相似案／檢索失敗）。
  **前端必須把三種顯示成三種**，吞成任何一種都是說謊。
- **`origin_violations`**：前端全域搜尋零命中（`-72` 實查），bedrock 檔位後端也沒擋。
  **新前端不要假設這個欄位存在或已被處理。** 本次 live 實測值為 `[]`（空陣列）。
- **上游檢索全空**：模型的所有引用都會被 client 清掉並標 `unsupported`。前端要說出原因
  出在上游檢索，而不是只顯示「沒有引用」。
- **同一份判解被切成多個 chunk**：實測同一份判決會以不同分數重複出現（見 §Findings）。
  相似案／判解清單要**按來源去重**，否則「命中 5 筆」其實只有 2 份文件。

---

## Functional Requirements

- **FR-001**：前端與後端完全分離，前端不內嵌任何案件 fixture 資料。
- **FR-002**：`POST /runs` 只能由使用者明確動作觸發（US-7）。
- **FR-003**：run 的錯誤處理必須與 health 檢查分離，各自有獨立的 catch 與獨立的畫面狀態（US-6）。
- **FR-004**：`POST /runs` 在 live 檔位回 **202**（不是 200），前端必須輪詢 `result_url`
  或接 `events_url`，並正確處理 409（還在跑）／200（完成）／502（失敗）。
- **FR-005**：左欄三分頁順序固定 ①案情與爭點 ②相關法條 ③相似案例。
- **FR-006**：爭點以三方對照（主張／答辯／證據）呈現；缺漏顯示「卷證未見」（US-3）。
- **FR-007**：下載檔案前必須通過 `submit_allowed` 守門；被擋時顯示 `blockers[]` 全部理由（US-8）。
- **FR-008**：提供設定 `confirmed_intake` 的使用者動作，並顯示未確認欄位清單（US-9）。
- **FR-009**：逐句可點，顯示 `basis` / `refs` / `origin` / 燈號（US-5）。
- **FR-010**：相似案必須標示 `provenance`（`official` / `public_crawl`）並可區分。
- **FR-011**：草稿被修改後，燈號與 `why` 必須由後端重新產生，前端不得沿用舊值或自行判定。
- **FR-012**：所有清單按來源去重後再顯示計數。

---

## Success Criteria

- **SC-001**：重整頁面三次，`POST /runs` 次數為 0。
- **SC-002**：`BEDROCK_MODEL_ID_DRAFT` 設為不存在的 id 時，畫面顯示後端回的真正錯誤原因，
  且畫面上不出現任何草稿句子。
- **SC-003**：`synthetic-blocked-01` 在新 UI 上下載被擋，擋的理由逐條來自後端 `blockers[]`。
- **SC-004**：`synthetic-ordinary-01` 帶齊 `confirmed_intake` 後，blocker 由 2 條降為 1 條。
- **SC-005**：三分頁內容與 payload 的 `issues[]` / `laws[]` / `cases[]` 一致（逐筆比對）。
- **SC-006**：`/api/health` 顯示的 `run_mode` / `retriever` / `provenance.execution_note`
  在畫面上如實呈現，離線與即時兩種檔位的文案不同。

---

## Backend Contract

**以下全部由 `-76` session 於 2026-09-12 12:13 在 port 8091 實跑取得，不是讀文件抄的。**

```
GET    /api/health                      檔位、模型、檢索來源、provenance
GET    /api/cases                       案例清單（synthetic / uploaded 分開列）
POST   /api/cases                       上傳卷證建案（multipart，只收 .pdf/.txt，單檔 20MB）
POST   /api/cases/{case_id}/runs        live → 202 {run_id, result_url, events_url}
                                        fixture → 200 ＋ 完整 payload
GET    /api/runs/{run_id}               409 還在跑 / 200 結果 / 502 節點失敗 / 404 查無
GET    /api/runs/{run_id}/events        SSE 逐節點事件（加值層，可斷，斷了退回輪詢）
POST   /api/cases/{case_id}/submit      200 或 409（後端重跑六節點，不信前端）
POST   /api/deadline                    期間試算（回 verdict 燈號，前端只負責畫）
```

**實測數據（live bedrock 檔位，`RETRIEVER=kb`）：**

| 項目 | 實測值 |
|---|---|
| `GET /api/health` | 200，四項 check 全 ok，`live_settings: 齊全` |
| `run_mode` / `kb_backend` | `bedrock` / `kb` |
| `provenance.execution_note` | 「bedrock（即時推論）模式，抽取與草稿由 Amazon Bedrock 基礎模型即時產生」 |
| `POST /runs` → 完成 | **51 秒**（202 → 輪詢 200） |
| `POST /submit` | **117 秒**（重跑六節點；1.1 秒 Bedrock 限速造成） |
| 節點耗時 | n1 8608ms／n2 0／n3 0／**n5 39544ms**／n6 3ms ← n5 佔 77% |
| `model_ids` | extract `us.anthropic.claude-sonnet-4-6`／draft `us.anthropic.claude-opus-4-6-v1` |
| `degraded` | `[]`（零降級） |
| `origin_violations` | `[]` |
| 法條檢索 | 6 筆，全部 `lamp=g`、`gate_status=cited_and_gated` |
| 相似案檢索 | 5 筆（`public_crawl` 3 ＋ `official` 2） |
| 引用查核 | `ok:14, amended:0, out_of_scope:0, missing:0` |
| 燈號 | `g:16, y:1, r:2` |
| 送出守門 | `synthetic-blocked-01` → **409**，`recompute_note` 明說「未採信前端送來的任何判斷」 |

**payload 頂層欄位**（`build_payload()`，`backend/orchestrator/graph.py:651-698`）：

```
case_id, run_id, state, provenance, files, intake, intake_conf, intake_origin,
intake_confirmed, facts_excerpt, classification, screen, retrieval,
laws[], cases[], issues[], doc[], citations[], retrieval_divergence,
citation_counts, lamp_stats, blockers[], issue_refs[], handoff,
submit_allowed, agents[], token_note, run_meta, tiers{}, history, origin_violations
```

左欄三分頁直接吃 `issues[]` / `laws[]` / `cases[]`；右欄草稿吃 `doc[]`（區塊 → `ss[]` 逐句）；
三層誠實分層吃 `tiers{可驗算, 有出處, 請人工判斷}`。

**安全邊界（不要試圖繞過）**：`POST /runs` 的 body 是 `extra="forbid"`，
**永遠不會接受 `base_state` 欄位**。續跑只能帶 `base_run_id`，上游狀態由後端自己
`load_run()` 讀回來。理由寫在 `backend/api/app.py` 的 `RunIn` docstring：
若允許前端夾帶 state，呼叫端就能偽造「已人工確認、無 blocker」把 C 型封鎖整個關掉。

---

## Requirement Coverage（命題方 2026-09-12 上午六項需求）

| 命題方要的 | 現況 | 本 Story 涵蓋 |
|---|---|---|
| 程序審查（期間是否逾期） | ✅ 已有，且是他們流程第一關 | US-2 ① |
| 相關法條 | ⚠️ 有，但他們還要「其他各縣市的訴願決定」，KB 沒有 | US-2 ②（缺口見 Q2） |
| 相似案例 | ⚠️ 有，但他們還要「跟我們之前做的決定是否一致」 | US-2 ③（缺口見 Q2） |
| **爭點彙整（三方對照）** | ❌ 目前是單向摘要 | **US-3**（需後端配合，見 Q1） |
| 法律用語校對 | ❌ 未有 | 未涵蓋（見 Q3） |
| **行政法院見解（北高行）** ← 長官權重最高 | ❌ 實測引用掛零 | 未涵蓋（後端問題，見 §Findings） |

完整脈絡：team-brain `projects/hack/2026-09-12 法制局長官諮詢紀要.md`。

---

## Findings（本 session 實測，與前端相依但屬後端範圍）

### F-1：判解／函釋引用實測掛零，根因在檢索深度不足（非模型問題）

`-72` 觀察到「判解通道已接、19 筆判解在庫（含北高行 3 筆），但實跑 `citation_counts` 全是法條、
沒有判解」。本 session 追到根因，**並用實跑證實**：

1. N5（Opus）**確實呼叫了判解檢索工具 4 次**，查詢句品質很好
   （例：「寄存送達 行政程序法第74條 送達生效日 不論實際領取」），
   但**四次全部回 `hit_ids: []`**。所以不是模型不會用工具、也不是 prompt 問題。
2. 根因在 `backend/retrieval/kb.py:107`：明確指定 prefix 的分支用
   `want = max(1, min(top_k * 3, 50))`，N5 傳 `top_k=5` → **實際只抓 15 筆**。
   而判解＋函釋共 29 份，佔 KB 2477 筆的 **1.2%**，在 top-15 裡會被 2448 筆決定書完全洗掉。
   對照組：相似案通道早就因為同樣問題改成 `QUOTA_FETCH_DEPTH = 50`
   （kb.py:50-54 的註解就是同一天實測寫下的：「numberOfResults=15 撈到 official 0 筆、=50 撈到 2 筆」）——
   **判解通道被漏掉了。**
3. **實跑驗證**（直接呼叫 retriever，未改 `backend/`）：

   | 查詢 | depth 15 | depth 50 | depth 100 |
   |---|---|---|---|
   | 寄存送達 送達生效日（本案的題目） | **0** | **0** | 1（`法務部93年函-寄存送達`，score 0.762）|
   | 行政罰法第7條第1項 故意過失 | — | **2**（最高行 109上780、108判531，score 0.773）| 5 |
   | 政府資訊公開法第18條第2項 | — | **5**（最高行 106判557、108上930，score 0.748–0.784）| 5 |
   | 訴願無實益 權利保護必要 | — | **4**（最高行 94判1497、高高行 109訴186、釋字546）| 5（＋北高行 98訴2374）|

**兩個結論，要分開講：**
- **depth 15 → 50 是真的該修**，一個常數，實質法題目立刻有 2–5 筆判解命中，
  分數 0.72–0.78 遠高於門檻 0.25，且**含最高行、北高行、高高行**——正是長官權重最高的那項。
- **但我第一個測試案例的結果是誤導性的悲觀值**：`synthetic-ordinary-01` 是
  程序逾期／寄存送達案，判解庫對這個題目幾乎沒有覆蓋（只有一筆函釋，要 depth 100）。
  這是**語料覆蓋缺口，不是程式 bug**。用這一個案例評估判解功能會嚴重低估它。

**我先前有一個假設是錯的，記在這裡以免別人重踩**：我一度以為根因是
`n5_draft.py:44` 的 `REF_PREFIXES = ["行政函釋/", "司法院釋字及行政判解/"]` 少了
`kb/official/` 前綴（實際 KB 路徑是 `kb/official/行政函釋/...`）。
**這個假設是錯的**——`kb.py:58-66` 的 `_relative_path()` 會把 `kb/official/` 剝掉，
所以 `startswith("行政函釋/")` 是對得上的。查證後才發現真正的原因是抓取深度。

**此項未動任何程式**（`-72` 要求先只出診斷、等 Ci 點頭）。屬後端範圍，不在本 Story。

### F-2：`run_all.py` 會讓 `prototype/dist/index.html` 的 mtime 前進

`backend/tests/run_all.py:179-191` 的紅線掃描會實跑一次 `build.py` 覆寫 dist、
比對位元組後再 `write_bytes(before)` 還原——**內容還原了，mtime 沒有**。
所以「dist 的 mtime 比預期新」不能當成有人手改的證據，要看 `git diff`。
（2026-09-12 這件事造成一次跨 session 假警報。）

### F-3：`prospec init` 會整份覆蓋 `AGENTS.md`

本 Story 的 scaffolding 是用 `prospec init` 建的。實測（在 scratchpad 沙箱先驗過）：
- repo 根的 `CONSTITUTION.md` **不會**被覆蓋（prospec 寫到 `prospec/CONSTITUTION.md`）；
- 但 `AGENTS.md` **會被整份覆蓋**成範本（原內容全失）。
本次已先備份、init 後還原，md5 驗證一致（`88a3249b69d5f77fac72a7b9ae6ae31b`）。
**下次在既有專案跑 `prospec init` 前先備份 `AGENTS.md`。**

---

## Related Modules

_No module index generated yet（`prospec knowledge generate` 未跑）。手動對應：_

| 模組 | 路徑 | 與本 Story 的關係 |
|---|---|---|
| 前端（將汰換） | `prototype/static/app.js`、`index.tmpl.html`、`build.py` | 被新 Vue3 前端取代 |
| 設計稿 | `design/petition-ai-demo.html` | 視覺與結構來源（純 mock，零 API）|
| API 層 | `backend/api/app.py` | 前端唯一資料來源 |
| payload 組裝 | `backend/orchestrator/graph.py:592-698` | 所有畫面欄位的定義處 |
| 守門節點 | `backend/nodes/n6_gate.py`、`backend/gate/` | `blockers` / `lamp_stats` / `citations` 來源 |
| 判解檢索 | `backend/retrieval/kb.py`、`backend/nodes/n5_draft.py:44` | F-1 的位置 |

---

## Open Questions

- **Q1（阻擋 US-3，需 Ci 或 `-72` 拍板）**：三方對照（訴願人主張／機關答辯／證據是否支持）
  目前後端 `issues[]` **沒有這些欄位**。要前端自己從 `facts_excerpt` 拆，還是後端擴充
  `issues[]`？**建議後端擴充**——前端自己拆等於前端在做事實認定，違反「燈號與判斷不由前端產出」。
  `NEEDS CLARIFICATION`
- **Q2（命題方需求，範圍問題）**：「其他各縣市的訴願決定」與「跟我們之前的決定是否一致」
  都需要 KB 沒有的資料。30 小時內要不要做？若不做，畫面上要不要明說「本系統目前只涵蓋新北」？
  **建議明說**（分層誠實）。`NEEDS CLARIFICATION`
- **Q3**：法律用語校對完全未有。屬新功能，不在本 Story。要另開 Story 嗎？`NEEDS CLARIFICATION`
- **Q4**：n5 佔端到端 77%（39.5 秒）。壓它會動到主筆品質。**`-72` 已明確要求先不要自己優化**，
  需 Ci 確認是否拿品質換時間。`NEEDS CLARIFICATION`
- **Q5**：新前端是獨立 Vue3 專案（另一個 port／build）還是繼續由 FastAPI serve
  （`backend/api/app.py` 的 `GET /` ＋ `/static`）？影響 CORS 設定與部署。
  現況後端的 CORS 只放行 `localhost`／`127.0.0.1` 任意 port。`NEEDS CLARIFICATION`

---

## Constitution Check

對照 `CONSTITUTION.md` 八原則中最相關的三條：

| 原則 | 判定 | 說明 |
|---|---|---|
| 分層誠實 | **PASS（且為本 Story 主要目的）** | US-6 修掉「後端失敗靜默退回 fixture」；US-8 保住送出守門；Edge Cases 要求三種檢索失敗情形分開顯示 |
| 引用必可驗 | **PASS** | US-5 要求逐句可點看 `basis`／`refs`；FR-011 禁止前端自行判定燈號 |
| 規則引擎零 LLM | **PASS** | 前端不碰規則判斷；期間燈號由 `POST /api/deadline` 的 `verdict` 回傳，前端只負責畫 |
| 不編造測資 | **WARN** | US-3 的「卷證未見」若實作成留白或由模型補寫就會違反。已寫進 Acceptance Scenario，需在 code review 確認 |

---

## Next Steps

1. **Ci／`-72` 先回 Q1、Q5**——這兩題會改變 Pink 的實作方式，其餘可邊做邊定。
2. `prospec change plan` 生成實作計畫（**建議由 Pink 自己跑**，讓計畫長在她的理解上）。
3. F-1 的判解檢索深度屬後端、等 Ci 點頭後由後端 session 修，不併入本 Story。
