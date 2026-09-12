# 前端 API 契約 v2：對話式辦案工作台（2026-09-12）

> 對應 UI：`feat/frontend-ai-layout` 分支的 `frontend/`（設計稿 `design/訴願智慧輔助平台.html`）。
> **這版 UI 跟舊版（六節點 pipeline）完全不同**，舊契約 `2026-09-12-frontend-contract.md` 對這版**不適用**。
> 目前前端是純 mock（`frontend/src/store/app.js` 全部在前端跑，零 API 呼叫）。本文件定義它接後端時該長什麼樣。
> 行號依撰寫當下的 `feat/frontend-ai-layout`。

## 0. UI 心智模型（先懂這個，契約才看得懂）

不是「按一顆按鈕跑完六節點」，而是**一個對話室 + 一疊卷宗**：

- **左欄**：案件清單，可分資料夾／拖曳整理。**資料夾純前端**（見 §0.2），案件本身走後端。
- **中欄**：對話串。承辦人打字或點建議 chip → agent 呼叫**工具**（tool）→ 工具逐步執行、吐出結果卡。
- **右欄**：案件卷宗，四個群組：`evidence`（卷證檔案）／`cases`（相關案例）／`laws`（相關法規）／`out`（答辯書與產出）。工具跑完會把結果**歸檔**到這裡；使用者也能自己搜尋加入或移除。

一次辦案的典型順序（`store/app.js` 的 `chips` 決定引導）：
上傳卷證 → `萃取` → `查案例`＋`查法規` → `生成草稿` → `優化`／`匯出`。

**關鍵差異**：工具由「使用者的訊息」觸發（打字命中關鍵字、或點 chip、或 `/` 選單），agent 端執行。所以主體是一支**聊天端點**，其餘是卷宗與案件的 CRUD。

### 0.1 「前端 API」與「agent 工具」是兩層，別混淆

```
前端  ──POST /cases/{id}/chat──▶  後端 agent  ──呼叫工具──▶  查法規 / 查案例 / 萃取 / 撰稿 / 潤稿 …
     （前端只認識這一支聊天端點）    （agent 自己決定用哪個）      （後端內部的 tool，前端不直接打）
```

- **前端契約**：使用者的每次對話動作只打 `POST /cases/{id}/chat`。前端**不會**直接呼叫 `/extract`、`/draft` 之類。
- **agent 工具**：工具確實是「API 等級」的能力，但它是**後端 agent 內部**的呼叫（去打檢索、KB、Bedrock），對前端**不可見**。前端只透過串流事件看到「agent 用了哪個工具、結果是什麼」（`tool_call` / `tool_result`，§2.3）。
- **同一份能力、兩個入口**：agent 的「查法規」工具，跟使用者手動搜尋加入用的 `GET /api/laws`（§1.3）**指向同一個母庫**——差別只在**誰觸發**（agent 自動查並歸檔 vs 使用者手動搜尋挑選）。**後端把「查法規／查案例／讀卷／撰稿」各做一次，兩個入口共用**，不要做兩套。
  - agent 自動查完 → 結果透過 chat 的 `tool_result` 回來，並自動寫進本案清單（`/cases/{id}/laws`、`/references`）。
  - 使用者手動 → 走 §1.3／§1.4 的 REST 端點自己挑進來。

### 0.2 資料夾＝純前端（後端不碰）

左欄的資料夾（新增／改名／刪除／拖曳案件進出）**全部前端狀態**，存 localStorage，**不打後端**。`store/app.js` 的 `folders`／`moveCase`／`deleteFolder` 維持前端實作即可。
- 後端只需要案件層級 CRUD（§1.1）；`PATCH /api/cases/{id}` **不需要** `folder_id` 欄位。
- 決策理由：資料夾是純整理用的輔助功能，30 小時內落地後端 CP 值低；跨裝置同步不是本期需求。

---

## 1. API 總覽（RESTful，依前端實際動作反推）

資源設計原則：
- **母庫（法規庫、歷史決定書庫）對使用者唯讀**——系統裡不能改一條法律或一份判決。母庫只有 `GET`（查詢）。
- **卷宗成員 = 「本案挑進來的清單」**，是 case 底下的子資源（`/cases/{id}/laws`、`/references`、`/files`），有 **C/R/D**（加入、讀、移出），**沒有 U**（不編輯母庫內容）。
- **產出（草稿、匯出檔）** 是 case 底下的 artifacts，由 agent 生成。

### 1.1 系統與案件

| # | 方法 | 路徑 | 觸發它的 UI 動作（程式位置） | 類型 |
|---|---|---|---|---|
| 1 | GET | `/api/health` | App 開機檢查後端／檔位 | 一次性 |
| 2 | GET | `/api/cases` | 左欄載入案件清單 | 一次性 |
| 3 | POST | `/api/cases` | 左欄「新增案件」`CaseTree.vue newCaseTop` | 一次性 |
| 4 | GET | `/api/cases/{id}` | 開案載入單一案件 | 一次性 |
| 5 | PATCH | `/api/cases/{id}` | 重新命名 `renameCase`（`{name}`） | 一次性 |
| 6 | DELETE | `/api/cases/{id}` | 刪除案件 `deleteCase` | 一次性 |

### 1.2 卷證檔案（`/cases/{id}/files`）— 本案專屬，C/R/D

| # | 方法 | 路徑 | UI 動作 | 類型 |
|---|---|---|---|---|
| 7 | GET | `/api/cases/{id}/files` | 右欄「卷證檔案」群組載入 | 一次性 |
| 8 | POST | `/api/cases/{id}/files` | composer「＋」／卷證群組上傳 `uploadToFolder` | multipart |
| 9 | DELETE | `/api/cases/{id}/files/{fileId}` | 卷證移除 `removeDoc('evidence')` | 一次性 |

### 1.3 相關法規 — 母庫查（唯讀）＋ 本案清單（C/R/D）

| # | 方法 | 路徑 | UI 動作 | 類型 |
|---|---|---|---|---|
| 10 | GET | `/api/laws?q=…` | 「搜尋並加入法規」對話框查母庫 `searchPool('laws')` | 一次性 |
| 11 | GET | `/api/laws/{lawId}` | 點法規看條文全文 | 一次性 |
| 12 | GET | `/api/cases/{id}/laws` | 右欄「相關法規」群組載入 | 一次性 |
| 13 | POST | `/api/cases/{id}/laws` | 搜尋加入 `addSearched('laws')`（`{law_ids:[…]}`） | 一次性 |
| 14 | DELETE | `/api/cases/{id}/laws/{lawId}` | 移出 `removeDoc('laws')` | 一次性 |

### 1.4 相似案例／參考決定 — 母庫查（唯讀）＋ 本案清單（C/R/D）

| # | 方法 | 路徑 | UI 動作 | 類型 |
|---|---|---|---|---|
| 15 | GET | `/api/decisions?q=…` | 「搜尋並加入案例」對話框查母庫 `searchPool('cases')` | 一次性 |
| 16 | GET | `/api/decisions/{decisionId}` | 點案例看決定書全文 | 一次性 |
| 17 | GET | `/api/cases/{id}/references` | 右欄「相關案例」群組載入 | 一次性 |
| 18 | POST | `/api/cases/{id}/references` | 搜尋加入 `addSearched('cases')`（`{decision_ids:[…]}`） | 一次性 |
| 19 | DELETE | `/api/cases/{id}/references/{refId}` | 移出 `removeDoc('cases')` | 一次性 |

### 1.5 產出（`/cases/{id}/artifacts`）— 草稿與匯出檔

| # | 方法 | 路徑 | UI 動作 | 類型 |
|---|---|---|---|---|
| 20 | GET | `/api/cases/{id}/artifacts` | 右欄「答辯書與產出」群組載入 | 一次性 |
| 21 | GET | `/api/cases/{id}/artifacts/{artifactId}` | 點產出看內容（草稿全文） | 一次性 |
| 22 | DELETE | `/api/cases/{id}/artifacts/{artifactId}` | 移出 `removeDoc('out')` | 一次性 |
| 23 | GET | `/api/cases/{id}/artifacts/{artifactId}/export?format=pdf\|docx` | 匯出下載（`pdf`／`doc` chip） | 一次性（回檔案） |

### 1.6 辦案對話（唯一串流）

| # | 方法 | 路徑 | UI 動作 | 類型 |
|---|---|---|---|---|
| 24 | **POST** | **`/api/cases/{id}/chat`** | **中欄送出訊息／點 chip／`/` 工具 `send`＋`runTool`** | **SSE 串流** |

**輪詢：0 支。** 沒有「發起→拿號碼牌→反覆問好了沒」；慢工作（萃取、草稿）走 #24 串流的 `tool_step` 事件逐步吐。
**串流：1 支**（#24 chat）。
**一次性：其餘全部（含母庫查與各層 CRUD）。**

> **母庫 vs 卷宗清單的分離是重點**：`GET /api/laws`（查全國法規母庫）與 `GET /api/cases/{id}/laws`（本案已挑的法規）是兩件事；案例同理（`/api/decisions` vs `/cases/{id}/references`）。母庫唯讀、卷宗清單可增刪。
> **資料夾純前端**（§0.2）：不在上表，後端不做，`PATCH /cases/{id}` 不含 `folder_id`。
> **U（更新）刻意不存在**：三類卷宗成員都沒有 PATCH——使用者不改母庫內容。草稿的「重新產生／優化」是 agent 透過 #24 生新 artifact，不是使用者手改。
> **agent 工具不在上表**：工具是後端 agent 內部呼叫（§0.1），前端只打 #24 chat；工具與 §1.3／§1.4 的 REST 端點共用同一份資料源。

---

## 2. 核心：`POST /api/cases/{id}/chat`（唯一串流端點）

前端每一次「送出訊息、點建議 chip、按 `/` 工具」最終都呼叫這支（`store/app.js:runTool` / `send`）。後端的 agent 讀訊息、自己決定要不要呼叫工具、把過程與結果串流回來。

### 2.1 Request

```jsonc
{
  "message": "幫我解析卷證檔案", // 必填（點 chip 時前端會帶對應的命令字串，如 "/解析卷證檔案"）
  "session_id": "sess-…",       // 選填；省略＝沿用該 case 的對話 session
  "tool_hint": "extract",       // 選填；點 chip／/ 選單時帶（extract|cases|laws|graph|draft|refine|export）
                                //   純打字時省略，由後端自行判斷要不要呼叫工具
  "args": {                      // 選填；某些工具的參數
    "instruction": "語氣更嚴謹",  // refine 用
    "sources": ["ev1","law3"]     // draft 用：限定引用來源（右欄卷宗 id）
  }
}
```

前端命中工具的邏輯在 `store/app.js:matchTool`（先比 `/命令` 前綴，再比關鍵字）。**這是前端的體貼，不是契約**——後端仍應自己判斷；`tool_hint` 只是加速，後端可忽略或覆寫。

### 2.2 錯誤（全部 JSON，開串流前先擋）

| 狀態 | 何時 | body |
|---|---|---|
| 400 | `message` 空 | `{detail}` |
| 404 | `case_id` 不存在 | `{detail}` |
| 503 | 非 live 檔位／缺 live 設定 | `{detail, run_mode, missing[]}` |

拿到 `text/event-stream` 就保證至少收到一個 `done` 或 `error`。

### 2.3 SSE 事件（六種）

wire 格式：`event: <名稱>\ndata: <一行 JSON>\n\n`。共通欄位：`seq`（本回合 0 起遞增）、`turn_id`。

| 事件 | payload | 前端拿去畫什麼 |
|---|---|---|
| `ack` | `{seq,turn_id,text}` | agent 的一句口白（「收到，開始讀卷…」）。對應 `store` 的 `ACKS` |
| `tool_call` | `{seq,turn_id,tool,label,args}` | 開一個**工具區塊**卡（`Chat.vue` 的 `kind:'tool'`）。`tool` ∈ §3 七種 |
| `tool_step` | `{seq,turn_id,tool,step,elapsed_ms}` | 工具卡裡逐行打勾的步驟（`toolBlock` 的 `steps[]`）。可多筆 |
| `tool_result` | `{seq,turn_id,tool,result{…}}` | 工具卡的結果內容（§3 各工具的 `result` 形狀）＋歸檔到右欄卷宗 |
| `token` | `{seq,turn_id,text}` | agent 收尾的文字回覆，逐字 append（草稿建議、下一步提示） |
| `done` | `{seq,turn_id,session_id,elapsed_ms,model_id}` | 一回合結束，唯一終止事件 |
| `error` | `{seq,turn_id,stage,error}` | 失敗，發完關流，**沒有 `done`**。`stage`∈`tool\|model\|transport\|internal` |

序保證：`done`／`error` 互斥且為最後一個；`tool_call`／`tool_result` 成對；`token` 可零筆；**沒有回放，斷線即斷**。斷線的處理見 §5。

---

## 3. 七種工具的 `result` 形狀（`tool_result.result`）

> 這一節描述的是 **agent 內部工具的產物形狀**——即 chat 串流 `tool_result.result` 的 JSON。前端不直接呼叫這些工具（§0.1），只用這裡的形狀把 `tool_result` 畫成工具卡並歸檔。

前端設計稿列 8 張工具卡（`data.js TOOLS`），但 `export_pdf`／`export_docx` 其實是**下載**（走 §1.5 的 export 端點 #23，不是 chat 工具），關聯圖是否保留見 §6。以下是走 chat 的工具：

### 3.1 `extract`（解析卷證檔案）→ 歸檔：不歸檔，直接顯示在對話卡
對應 `ToolOut.vue out.type==='extract'`。
```jsonc
{
  "case_no": "1143062584",
  "meta": { "type": "違反廢棄物清理法事件", "petitioner": "吉○實業有限公司",
            "agency": "新北市政府環境保護局", "disposition_no": "新北環稽字第1140876543號",
            "disposition_date": "114/05/20", "penalty": "罰鍰新臺幣6萬元…", "laws": ["廢清法§36 I","§52"] },
  "facts": "訴願人於本市林口區…",
  "claims": ["系爭堆置物為可回收再利用之產源物料…", "…"],   // 訴願主張，逐條
  "issues": ["爭點一 系爭堆置物之法律性質…", "…"],           // 本案爭點，逐條
  "procedure_checks": [                                       // 程序審查，逐項一顆燈
    { "label": "訴願期間", "lamp": "ok",    "detail": "原處分114/05/23送達…未逾期" },
    { "label": "陳述意見程序", "lamp": "warn",  "detail": "卷內未見…建議先函請補提" },
    { "label": "廢棄物性質之證據", "lamp": "alert", "detail": "卷附採證照片僅呈現外觀…" }
  ]
}
```
- `lamp` ∈ `ok|warn|alert`（前端 CSS `.bdg.ok/.warn/.alert`）。**不要自己補「分類信心 0.96」那種假數字**（設計稿寫死的，砍）。

### 3.2 `cases`（查相似案例）→ 歸檔：`cases` 群組
對應 `ToolOut.vue out.type==='cases'` 與右欄卡。
```jsonc
{
  "hits": [
    { "id":"c1", "no":"1143062011", "score":0.94, "provenance":"official|public_crawl",
      "type":"廢棄物清理法", "verdict":"訴願駁回", "law":"廢清法§36 I、§52",
      "title":"事業廢棄物露天堆置未設防雨設施，訴願駁回",
      "full":"<p>…決定書全文或要旨…</p>" }
  ]
}
```
- `score` 前端顯示為「相似度 94%」，**必須標「向量相似度，非法律相似度」**（誠實）。
- `provenance` 要在畫面上分得出 `official`（賽方資料集）vs `public_crawl`（市府公開爬蟲）。
- 同一份決定書被切多 chunk 時**後端先去重**再回。

### 3.3 `laws`（搜尋相關法規）→ 歸檔：`laws` 群組
```jsonc
{
  "hits": [
    { "id":"l1", "no":"廢棄物清理法 §2 I", "title":"廢棄物之定義",
      "body":"本法所稱廢棄物…", "note":"本案爭點一之判準…",
      "verified": true,          // 字號能對回法規快照
      "relevance": "unknown" }   // 一律 unknown：綠燈只代表字號存在，不代表與本案相關
  ]
}
```
- `verified:false` 不得畫成「字號已驗」；`relevance` 一律 `unknown`（沿用 `adapt.js` 現成文案精神）。

### 3.4 `draft`（生成草稿）→ 歸檔：`out` 群組（「訴願決定書草稿 v1」）
```jsonc
{
  "artifact_id": "art-…",
  "title": "訴願決定書草稿 v1",
  "sections": [                    // 逐段逐句，句子帶引註
    { "h":"主文", "blocks":[
        { "text":"原處分撤銷，由原處分機關於2個月內另為適法之處分。",
          "cites":[ {"id":"l8","label":"訴願法§81 I"}, {"id":"c4","label":"案例1147030254"} ] }
    ]},
    { "h":"事實", "blocks":[ … ] },
    { "h":"理由", "blocks":[ … ] }
  ],
  "cite_count": 14,                // 真值，不要寫死
  "sources_used": ["ev1","law1","c2"]  // 實際引用到的卷宗 id
}
```
- **引註數要從 payload 數**，不要沿用設計稿寫死的「14 處」。
- 「來源控管」保證文案只有在**每個 cite 真的可回溯**時才顯示，否則拿掉。

### 3.5 `refine`（優化文案）→ 不歸檔，顯示 diff 卡
對應 `ToolOut.vue out.type==='refine'`。
```jsonc
{
  "target": "理由欄 第五點",
  "diff": [ {"op":"del","text":"卷內看不到…"}, {"op":"add","text":"遍查全卷，並無…"}, {"op":"keep","text":"…"} ],
  "notes": ["將口語改為公文用語…","補充§114補正時限…","新增敘述皆對應卷宗內來源"]
}
```

### 3.6 `read_case`（讀卷內，追問用）
承辦人針對某句／某卷追問時，agent 讀卷回答。`result` 通常是 `token` 為主，可無 `tool_result`；若有引用，帶 `hits[]`（同 3.2/3.3 形狀）。

### 3.7 `retrieve_refs`（查判解與函釋，追問用）
同 `laws` 的 hits 形狀，但來源是判解／函釋庫。

> 上面 3.6/3.7 是「追問」場景會用到的工具，主流程（extract→cases→laws→draft）用不到；列出來是因為 chat 端點的 `tool` 值域要涵蓋它們。

---

## 4. 各資源 CRUD 的 payload

右欄「四群組」是**四個獨立資源的清單合起來畫**，不是一個泛用 endpoint。前端可四支各打一次、或後端另提供一支彙整 `GET /api/cases/{id}`（回 `{case, files, laws, references, artifacts}`）省往返——**建議提供彙整版**當開案首載，之後各資源增刪打各自的端點。

### 4.1 卷證檔案

- `GET /api/cases/{id}/files` → `{ files:[ {id,name,ext,note,readable} ] }`
  - `readable`：掃描影像／不支援格式回 `false`，UI 標「無法辨讀」（見 §6）。
- `POST /api/cases/{id}/files`（multipart `files`）→ `201 { files:[…新上傳…] }`
- `DELETE /api/cases/{id}/files/{fileId}` → `204`

### 4.2 相關法規（母庫唯讀 ＋ 本案清單）

- **母庫查**　`GET /api/laws?q=關鍵字` → `{ results:[ {id,no,title,note} ] }`；查無 `{results:[]}`（不是錯誤）。
- **看全文**　`GET /api/laws/{lawId}` → `{ id,no,title,body,verified,relevance:"unknown" }`
  - `verified:false` 不得畫成「字號已驗」；`relevance` 一律 `unknown`。
- **本案清單**　`GET /api/cases/{id}/laws` → `{ laws:[ {id,no,title,note,verified,relevance} ] }`
- **加入**　`POST /api/cases/{id}/laws` body `{ "law_ids":["l3","l7"] }` → `201 { laws:[…] }`
- **移出**　`DELETE /api/cases/{id}/laws/{lawId}` → `204`

### 4.3 相似案例／參考決定（母庫唯讀 ＋ 本案清單）

- **母庫查**　`GET /api/decisions?q=關鍵字` → `{ results:[ {id,no,title,type,verdict,law,score,provenance} ] }`
  - `score` 顯示為相似度，**必標「向量相似度，非法律相似度」**；`provenance` ∈ `official|public_crawl`，要能在畫面分辨。
- **看全文**　`GET /api/decisions/{decisionId}` → `{ id,no,title,verdict,law,full }`
- **本案清單**　`GET /api/cases/{id}/references` → `{ references:[ {id,no,title,note,score,provenance,full} ] }`
- **加入**　`POST /api/cases/{id}/references` body `{ "decision_ids":["d1","d5"] }` → `201 { references:[…] }`
- **移出**　`DELETE /api/cases/{id}/references/{refId}` → `204`

### 4.4 產出

- `GET /api/cases/{id}/artifacts` → `{ artifacts:[ {id,name,kind:"draft"|"pdf"|"docx",note,created_at} ] }`
- `GET /api/cases/{id}/artifacts/{artifactId}` → 草稿的 `sections` 結構（§3.4）或匯出檔的 metadata
- `DELETE /api/cases/{id}/artifacts/{artifactId}` → `204`
- `GET /api/cases/{id}/artifacts/{artifactId}/export?format=pdf|docx` → 回檔案（`Content-Disposition: attachment`）

> 同一份決定書被切多 chunk：`GET /api/decisions` 與 `references` **後端先去重再回**，否則「命中 5 筆」其實只有 2 份文件。

---

## 5. 前端已有、要沿用的行為

- **串流斷線**：`token` 收到一半斷了 → 顯示「連線中斷，可重問」，**不要**當成模型錯誤（`error.stage:transport`）。連兩次拿不到 `done` 可退化為非串流（後端若提供 `?stream=0`，body＝把所有事件收進一個物件；沒有就直接顯示重試）。
- **忙碌鎖**：一個工具跑的時候 `state.busy=true`，chips 換成「工具執行中…」，送出鍵 disabled（`Composer.vue canSend`）。後端不需配合，這是前端節流。
- **檔位誠實**：`/health` 回 `run_mode`；離線重播檔位時，chat 會回 503，UI 要說「聊天需要即時模型」，**不得**演一段假對話。

---

## 6. 設計稿要砍／要改的（沿用舊契約 §5 的結論，對應到新 UI）

| 砍／改 | 位置 | 理由 |
|---|---|---|
| **關聯圖** `build_relation_graph` | `data.js TOOLS[graph]`、`RelationGraph.vue`、`graph.js GNODES/GEDGES` | 後端無此節點、不產關聯資料，整段是前端模擬。**demo 想保留就明講「示意圖，非後端產物」**，否則砍 |
| `export_pdf`/`export_docx` 當成 chat 工具 | `data.js TOOLS[pdf,doc]`、`ToolOut out.type==='export'` | 匯出是**下載**（§1.5 #23），不是 agent 工具。chip 點下去應打 export 端點拿檔案，不是發 chat |
| `/` 斜線指令選單 | `Composer.vue` slash 選單 | 工具由 agent 決定並發 `tool_call`；留著會讓人以為「點了就一定跑那支」。可保留為**輸入輔助**但別暗示保證 |
| 多案件資料夾／拖曳 | `CaseTree.vue`、`store folders/moveCase` | **維持純前端**（§0.2，localStorage），不落地後端。此列為「保留但不接後端」，非砍 |
| 「分類信心 0.96」 | `ToolOut.vue extract meta` | 寫死假數字，砍 |
| 「相似度 94%」`sim` 寫死 | `data.js CASE_POOL.sim`、`ToolOut cases` | 真值來自 `tool_result.hits[].score`，且**標「向量相似度非法律相似度」** |
| 「引註 14 處」寫死 | `store draft note`、`ToolOut draft` | 真值從 `draft.cite_count` 數 |
| 「已上傳，可直接改」等上傳文案 | 上傳 sheet 文案 | 目前**不做 OCR**，掃描影像回 `readable:false`＋標「無法辨讀」，不要寫成支援 |

**兩條不能省**（誠實紅線）：
1. 法規綠燈只代表**字號對得回法規快照**，`relevance` 一律 `unknown`——不代表與本案相關。
2. 相似度是**向量相似度**，KB 命中未對資料集實檔驗證，畫面要標明。

---

## 7. 一句話總結給後端

這版前端只需要：

1. **1 支串流聊天端點** `POST /cases/{id}/chat`——agent 在裡面呼叫 extract／cases／laws／draft／refine／read_case／retrieve_refs 七種工具，用 `ack`/`tool_call`/`tool_step`/`tool_result`/`token`/`done`/`error` 事件回傳。
2. **RESTful 一次性端點**，母庫唯讀、本案子資源 C/R/D：
   - 系統／案件：`GET /health`、`GET|POST /cases`、`GET|PATCH|DELETE /cases/{id}`
   - 卷證：`GET|POST /cases/{id}/files`、`DELETE …/files/{fileId}`
   - 法規：`GET /laws`、`GET /laws/{lawId}`（母庫唯讀）＋ `GET|POST /cases/{id}/laws`、`DELETE …/laws/{lawId}`（本案清單）
   - 案例：`GET /decisions`、`GET /decisions/{decisionId}`（母庫唯讀）＋ `GET|POST /cases/{id}/references`、`DELETE …/references/{refId}`（本案清單）
   - 產出：`GET /cases/{id}/artifacts`、`GET|DELETE …/{artifactId}`、`GET …/{artifactId}/export`

**沒有輪詢**（慢工作走串流的 `tool_step`）；三類卷宗成員只有 C/R/D 沒有 U（不改母庫）；關聯圖與資料夾建議前端本地化或砍（§6）。
