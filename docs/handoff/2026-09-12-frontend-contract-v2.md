# 前端 API 契約 v2：對話式辦案工作台（2026-09-12）

> 對應 UI：`feat/frontend-ai-layout` 分支的 `frontend/`（設計稿 `design/訴願智慧輔助平台.html`）。
> **這版 UI 跟舊版（六節點 pipeline）完全不同**，舊契約 `2026-09-12-frontend-contract.md` 對這版**不適用**。
> 目前前端是純 mock（`frontend/src/store/app.js` 全部在前端跑，零 API 呼叫）。本文件定義它接後端時該長什麼樣。
> 行號依撰寫當下的 `feat/frontend-ai-layout`。

## 修訂紀錄

**2026-09-12 22:50 修訂（Ci，依可行性審查與 AWS 實查）。** 初版與後端現況有八處對不上，已就地修正。
審查報告：`https://ci-reports-917.pages.dev/reports/2026-09-12-frontend-contract-v2-review/`；
修訂對照與每支 API 實作指引：`.../2026-09-12-contract-v2-patch-preview/`。

改了哪八處：

| # | 章節 | 改什麼 | 為什麼 |
|---|---|---|---|
| ① | §2.3 | `done` 恢復分層誠實燈號（`lamp`/`tier`/`origin`/`why`/`refs[]`/`dropped_refs[]`/`redirect`） | 初版漏掉，等於退掉 `CONSTITUTION` §1/§2 的實作與 AC5–AC12 |
| ② | §2.3 | 加 `call_id`、`tool_result.status`；`session_id` 提前到 `ack`；**砍 `tool_step`** | 成對事件缺配對鍵；工具失敗會被畫成「查無」；斷線續不回 |
| ③ | §0.1 §1.6 §3 | `extract`/`draft` **離開 chat**，改走 `POST /runs` + `GET /runs/{id}/events`。**串流 1 支 → 2 支** | chat 的 SSE 現況不是逐筆送（實跑證明），且 `run_case()` 沒有 `to_node`、卷內編號會靜默撞號 |
| ④ | §2.1 §3 | chat 工具收斂成**既有五支**，統一用後端長名 | 原本四套命名互不相容，契約自己前後打架 |
| ⑤ | §3.2 §3.3 §4 | `hits[]` 對齊後端實際欄位；`id` = S3 相對路徑；全文從 S3 讀 | 原本的欄位多半是設計稿假資料；KB 回的是 chunk 不是全文 |
| ⑥ | §1.3 §1.4 §4.2 §4.3 | 母庫查**兩支都保留**，改用 `doc_kind` server-side filter | 668 部法規全文已在 KB 且實測命中；不篩就撈不到 |
| ⑦ | §4 | 新增 §4.0：持久化＝一案一份 `manifest.json`，**不加資料庫** | 四份清單原本無處可存 |
| ⑧ | §1.5 §6 | 匯出降級成 `html`/`md` | PDF/DOCX 要把中文字型與排版引擎塞進容器，投報率最低 |

已拍板（2026-09-12 Ci）：建案維持「上傳卷證即建案」；「相似案例」**先只收 `doc_kind=decision`**，裁判書本期不露出；
`manifest` 與 `runs` **都接受「容器重啟即失」**，不搬 S3；AC7 在新語料下**要重跑**。

## 0. UI 心智模型（先懂這個，契約才看得懂）

不是「按一顆按鈕跑完六節點」，而是**一個對話室 + 一疊卷宗**：

- **左欄**：案件清單，可分資料夾／拖曳整理。**資料夾純前端**（見 §0.2），案件本身走後端。
- **中欄**：對話串。承辦人打字或點建議 chip → agent 呼叫**工具**（tool）→ 工具逐步執行、吐出結果卡。
- **右欄**：案件卷宗，四個群組：`evidence`（卷證檔案）／`cases`（相關案例）／`laws`（相關法規）／`out`（答辯書與產出）。工具跑完會把結果**歸檔**到這裡；使用者也能自己搜尋加入或移除。

一次辦案的典型順序（`store/app.js` 的 `chips` 決定引導）：
上傳卷證 → `萃取` → `查案例`＋`查法規` → `生成草稿` → `優化`／`匯出`。

**關鍵差異**：工具由「使用者的訊息」觸發（打字命中關鍵字、或點 chip、或 `/` 選單），agent 端執行。所以主體是一支**聊天端點**，其餘是卷宗與案件的 CRUD。

### 0.1 三條路，別混淆（③ 修訂）

```
① 對話（問答／查法規／查案例／讀卷／潤稿）
   前端 ──POST /cases/{id}/chat（SSE）──▶ agent ──▶ 五支工具 ──▶ KB / 法規快照 / 卷內 payload

② 慢工作（解析卷證／生成草稿）
   前端 ──POST /cases/{id}/runs──▶ 202 + events_url
        └─訂閱 GET /runs/{run_id}/events（SSE）──▶ node_start / node_done 逐節點進度

③ 其餘（案件、卷宗、母庫查、產出）
   前端 ──一次性 REST──▶ manifest.json / KB / S3
```

- **`extract`／`draft` 不是 chat 工具。** 「解析卷證檔案」「生成草稿」兩個 chip **不發 chat**，直接打 ②。
  - 解析卷證：`POST /cases/{id}/runs`
  - 生成草稿：`POST /cases/{id}/runs`，body 帶 `{"from_node":"n4","base_run_id":"<上一次的 run_id>"}`（續跑，`UPSTREAM_FIELDS` 會把 n1–n3 的欄位深拷貝過來）
  - 兩者都訂閱 `GET /runs/{run_id}/events` 拿逐節點進度。
- **為什麼不放進 chat**（三個實跑查證的理由，別再改回去）：
  1. `run_case()` **沒有 `to_node` 參數**，`backend/orchestrator/graph.py:200-202` 的 docstring 明寫「不能停在中間：N6 永遠最後重跑」——這是刻意的不變量（續跑產生的終態一定經過守門）。所以 `extract` 做不到「只跑 N1–N3」。
  2. **chat 的 SSE 現況不是逐筆送的**：`backend/api/chat.py:243-266` 的 `gen()` 把事件 append 進 `pending`，`_run_turn` 跑完才一次 yield（實跑：t=0 emit 的事件 t=2.02s 才抵達）。搬進 chat 拿不到「逐步顯示」這個唯一好處。而 `GET /runs/{id}/events` 的 `BUS.stream`（`backend/api/events.py:63-74`）**現在就是真串流**。
  3. 卷內編號會**靜默撞號**：N4 每次執行都從 `L1`/`C1` 重編（`backend/nodes/n4_retrieval.py:214-217,258-262`），而 `add_case_refs` 對既有 id 直接 `continue`（`backend/llm/chat.py:188-189`）→ 模型引新草稿的 `[L1]`，白名單查得到判成黃燈，但 `refs[]` 帶出**舊 run 的法條**。誠實層被打穿。
- **agent 工具**：仍是**後端 agent 內部**的呼叫，對前端不可見。前端只透過 `tool_call`／`tool_result` 看到「agent 用了哪個工具、結果是什麼」（§2.3）。
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
| 3 | POST | `/api/cases` | 左欄「新增案件」`CaseTree.vue newCaseTop` | **multipart** |
| 4 | GET | `/api/cases/{id}` | 開案載入單一案件 | 一次性 |
| 5 | PATCH | `/api/cases/{id}` | 重新命名 `renameCase`（`{name}`） | 一次性 |
| 6 | DELETE | `/api/cases/{id}` | 刪除案件 `deleteCase` | 一次性 |

> **建案語意（2026-09-12 Ci 拍板）：維持「上傳卷證即建案」。** `POST /api/cases` 是 multipart（`files[]`），
> 沿用既有端點（`backend/api/app.py:324` 的 `save_upload`），建案時順手寫一份 `manifest.json`（§4.0）。
> **沒有「先建空案」這個狀態**——前端的「新增案件」按鈕＝開上傳對話框，選完檔才真的建案。
> 理由：沒有卷證的案子在這個 UI 上什麼都不能做，空案只會是個死狀態。

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
| 23 | GET | `/api/cases/{id}/artifacts/{artifactId}/export?format=html\|md` | 匯出下載（`pdf`／`doc` chip） | 一次性（回檔案） |

> **⑧ 匯出降級成 `html`／`md`（本期不做 PDF／DOCX）。** 產 PDF／DOCX 要把中文字型與排版引擎塞進容器，
> 而 2026-09-12 才剛把 build context 從 905MB 壓到 4.8MB（commit `279169b`）。demo 時「下載一個檔案」
> 幾乎看不見，投報率最低。PDF 交給瀏覽器列印。前端 `exportDownload()`（`App.vue:169-171`）現在只是 toast。

### 1.6 兩條串流（③ 修訂：原本寫「唯一串流」）

| # | 方法 | 路徑 | UI 動作 | 類型 |
|---|---|---|---|---|
| 24 | **POST** | **`/api/cases/{id}/chat`** | 中欄送出訊息／點對話類 chip `send`＋`runTool` | **SSE 串流 ①** |
| 25 | **POST** | **`/api/cases/{id}/runs`** | 「解析卷證檔案」／「生成草稿」chip | 一次性（回 `202` + `events_url`） |
| 26 | **GET** | **`/api/runs/{run_id}/events`** | 承 #25，訂閱逐節點進度 | **SSE 串流 ②** |

**輪詢：0 支。** 沒有「發起→拿號碼牌→反覆問好了沒」——#25 回的 `events_url` 是一條 SSE，不是輪詢端點。
**串流：2 支**（#24 chat、#26 runs events）。
**一次性：其餘全部（含母庫查與各層 CRUD）。**

#### #25／#26 怎麼用

```jsonc
// 解析卷證
POST /api/cases/{id}/runs            // body 可空
→ 202 { "run_id": "run-…", "status": "running", "events_url": "/api/runs/run-…/events" }

// 生成草稿（續跑，沿用上一次的 n1–n3 結果）
POST /api/cases/{id}/runs
{ "from_node": "n4", "base_run_id": "<上一次的 run_id>" }
→ 202 { "run_id": "run-…", "events_url": "…" }
```

`GET /api/runs/{run_id}/events` 的事件（既有，`backend/api/events.py`）：

| 事件 | data | 前端畫什麼 |
|---|---|---|
| `node_start` | `{run_id, node, agents}` | 工具卡新增一行步驟，狀態「執行中」 |
| `node_done` | `{run_id, node, elapsed_ms, degraded, narrative}` | 該行打勾 + 顯示耗時；`degraded:true` 標黃 |
| `run_done` | `{run_id, final_state}` | 收尾，接著打 #4 彙整版重載右欄 |
| `run_failed` | `{run_id, node, error}` | 該行標紅，顯示 `error` |

> **`node` 只有 `"n1"`…`"n6"`，沒有中文步驟名**（對比 chat 的 `tool_call` 是有 `label` 的）。
> **前端要自備 `n1..n6` → 中文的對照表**，或請後端在轉發時補。這是小工，但要指定給人做，
> 別假設事件裡已經有。建議文案：n1 讀卷抽取／n2 案件分類／n3 程序審查／n4 檢索法條與相似案／n5 草稿撰寫／n6 引用守門。
>
> **實測耗時**（真 bedrock，n=3，全為 `synthetic-*`）：n1–n3 約 **10–11 秒**；n4–n6 約 **24／44／72 秒**
> （n5 跨度 3.3 倍，**上界用 72 秒抓**）。ALB `idle_timeout` 已設 900 秒，不會被切斷。
>
> **續跑的三道護欄**（`backend/orchestrator/graph.py:205-217, 233-238`）：`from_node != "n1"` 而沒帶
> `base_run_id` → 400；`base_state.run_mode` 與當前 mode 不同 → 拒絕（禁止 fixture/bedrock 混血）；
> `confirmed_intake` 只能配 `from_node ∈ {n1, n2}`。**N6 永遠最後重跑**，所以續跑產生的終態一定經過守門。

> **母庫 vs 卷宗清單的分離是重點**：`GET /api/laws`（查全國法規母庫）與 `GET /api/cases/{id}/laws`（本案已挑的法規）是兩件事；案例同理（`/api/decisions` vs `/cases/{id}/references`）。母庫唯讀、卷宗清單可增刪。
> **資料夾純前端**（§0.2）：不在上表，後端不做，`PATCH /cases/{id}` 不含 `folder_id`。
> **U（更新）刻意不存在**：三類卷宗成員都沒有 PATCH——使用者不改母庫內容。草稿的「重新產生／優化」是 agent 透過 #24 生新 artifact，不是使用者手改。
> **agent 工具不在上表**：工具是後端 agent 內部呼叫（§0.1），前端只打 #24 chat；工具與 §1.3／§1.4 的 REST 端點共用同一份資料源。

---

## 2. 核心：`POST /api/cases/{id}/chat`（串流 ①）

前端的**對話類**動作（送出訊息、點對話類 chip）呼叫這支（`store/app.js:runTool` / `send`）。
**「解析卷證檔案」「生成草稿」兩個 chip 不走這裡**，見 §0.1／§1.6。後端的 agent 讀訊息、自己決定要不要呼叫工具、把過程與結果串流回來。

### 2.1 Request

```jsonc
{
  "run_id": "run-…",            // 必填。必須是一次**已完成**的 run（read_case 的 payload 來源）
  "message": "這個爭點有沒有類似的前例？", // 必填，非空字串
  "session_id": "sess-…",       // 選填；省略＝開新對話，後端在 ack 就回一個新的
  "tool_hint": "search_similar_decisions",
                                // 選填；點對話類 chip 時帶。值域＝§3 的五支工具名
                                //   純打字時省略，由後端自行判斷要不要呼叫工具
  "context": {                   // 選填；承辦人在草稿上選了哪一句
    "scope": "sentence",         // "sentence" | "all"，省略視同 "all"
    "sent_id": "s12",
    "text": "本件訴願人所訴各節均無理由。"
  },
  "args": {                      // 選填；某些工具的參數
    "instruction": "語氣更嚴謹"   // refine_text 用
  }
}
```

> **④ `tool_hint` 的值域改了**，原本是 `extract|cases|laws|graph|draft|refine|export`。
> `extract`／`draft` 已離開 chat（§0.1），`graph`／`export` 本來就不是 chat 工具（§6）。
> 現在只有 §3 的五支。
>
> **`run_id` 是必填，這是既有契約**（`docs/spec/2026-09-12-chat-honesty-lamps.md` §4.0「payload 由呼叫端提供」）。
> HTTP 層做 `load_run(run_id)` → `build_payload()` → 切分區，餵給 agent；`read_case(section)` 讀的就是這份 dict。
> 前端在一個 case 裡沿用「最後一次成功的 run_id」即可（#4 彙整版會回 `latest_run_id`）。
>
> **`args.sources` 已移除。** 原本是 `["ev1","law3"]` 靠字串前綴分辨四種資源，而右欄四群組是四個不同 namespace，
> 前綴約定沒寫在任何地方。草稿的來源控管改由 §4.0 的 manifest 決定（agent 只看本案已挑進來的卷宗）。

前端命中工具的邏輯在 `store/app.js:matchTool`（先比 `/命令` 前綴，再比關鍵字）。**這是前端的體貼，不是契約**——後端仍應自己判斷；`tool_hint` 只是加速，後端可忽略或覆寫。

### 2.2 錯誤（全部 JSON，開串流前先擋）

| 狀態 | 何時 | body |
|---|---|---|
| 400 | `message` 空 | `{detail}` |
| 404 | `case_id` 不存在 | `{detail}` |
| 503 | 非 live 檔位／缺 live 設定 | `{detail, run_mode, missing[]}` |

拿到 `text/event-stream` 就保證至少收到一個 `done` 或 `error`。

### 2.3 SSE 事件（六種；①② 修訂）

wire 格式：`event: <名稱>\ndata: <一行 JSON>\n\n`。共通欄位：`seq`（本回合 0 起遞增）、`turn_id`。

| 事件 | payload | 前端拿去畫什麼 |
|---|---|---|
| `ack` | `{seq,turn_id,text,`**`session_id`**`}` | agent 的一句口白。對應 `store` 的 `ACKS`。**`session_id` 提前到這裡**（見下） |
| `tool_call` | `{seq,turn_id,`**`call_id`**`,tool,label,args}` | 開一個**工具區塊**卡（`Chat.vue` 的 `kind:'tool'`）。`tool` ∈ §3 五種 |
| `tool_result` | `{seq,turn_id,`**`call_id`**`,tool,hits[],note,`**`status`**`}` | 工具卡的結果（§3 的 `hits[]` 形狀）＋歸檔到右欄卷宗 |
| `token` | `{seq,turn_id,text}` | agent 的文字回覆，逐字 append |
| `done` | **見 §2.4（沿用既有 spec §4.4，欄位很多）** | 一回合結束，唯一終止事件，**唯一帶燈號的事件** |
| `error` | `{seq,turn_id,stage,error}` | 失敗，發完關流，**沒有 `done`**。`stage`∈`tool\|model\|transport\|internal` |

序保證：`done`／`error` 互斥且為最後一個；`tool_call`／`tool_result` 成對（用 `call_id` 配對）；`token` 可零筆；**沒有回放，斷線即斷**。斷線的處理見 §5。

#### ② 三處新增／一處刪除

**`call_id`（新增，必要）**　同一回合內同一支工具可能被呼叫**兩次以上**（agent 讀完爭點常會再查一次法規），
`tool` + `seq` 配不起來。`call_id` 是本回合內遞增的配對鍵，`tool_call` 與 `tool_result` 帶同一個值。

**`tool_result.status`（新增，必要）**　值域 `ok | empty | failed`。

| status | 意思 | 前端該說什麼 |
|---|---|---|
| `ok` | 查到東西，`hits[]` 非空 | 正常顯示 |
| `empty` | **查無**，`hits[]` 為空 | 「這個條件下沒有找到」 |
| `failed` | **查詢本身失敗**（KB 打不到、快照載不動），`hits[]` 為空 | 「查詢來源失敗，可重試」 |

> 為什麼一定要分：檢索失敗在 `ChatTools._search` 內部就被攔下轉成文字回給模型（`backend/llm/chat.py:420-427`），
> **不會冒到 `error` 事件**。沒有 `status`，前端看到的 `empty` 與 `failed` 長得一模一樣，會把「查詢失敗」
> 畫成「資料庫裡沒有這筆資料」。後端那段的註解自己就是為了防這件事寫的。

**`ack.session_id`（提前）**　原本只在 `done` 送。但 §5 要求前端處理「`token` 收到一半斷了」——
斷在中途就永遠拿不到 `session_id`，下一輪只能開新 session，前面講過的話全丟。

**`tool_step`（刪除）**　原本要畫成「工具卡裡逐行打勾的步驟」。刪掉的理由：

1. chat 的五支工具都是**單次** retriever 呼叫，內部沒有階段可報。硬生就是編步驟名與耗時，違反 `CONSTITUTION` §1。
2. 就算生了也不會逐步出現——`backend/api/chat.py:243-266` 的 `gen()` 是整回合跑完才一次沖出（實跑：t=0 emit 的事件 t=2.02s 才到）。

**逐行打勾移到 #26 的 `node_start`／`node_done`**（§1.6），那條是真串流、步驟與耗時都是真的。
chat 的工具卡用單純的 spinner。

> **後端另有一項修正（不是契約變更，但前端會感覺到）**：`gen()` 要改成真串流（emit 推 queue、邊跑邊 yield），
> 否則 §2.3 承諾的 `token` 逐字 append 也做不到。改的時候要顧到 `?stream=0` 與 SSE 共用同一條 `_run_turn`
> 的既有契約（`docs/spec/2026-09-12-chat-honesty-lamps.md` §2.2）。

### 2.4 `done` 的完整形狀（① 修訂：唯一帶燈號的事件）

> **沿用 `docs/spec/2026-09-12-chat-honesty-lamps.md` §4.4，那份是契約凍結狀態、且已由 AC5–AC12 實跑驗過。**
> 初版契約把 `done` 寫成 `{seq,turn_id,session_id,elapsed_ms,model_id}` 五個欄位，**那是漏寫，不是簡化**。

```jsonc
// event: done
{
  "seq": 17, "turn_id": "turn-…",
  "session_id": "sess-…",        // 也在 ack 出現過，這裡是最終值
  "answer": "依卷內資料，…[c1]…",  // 完整回答，前端可用它校正 token 串接
  "lamp": "y",                   // "y" | "r"，**永遠不會是 "g"**
  "tier": "有出處",               // "有出處" | "請人工判斷"
  "origin": "retrieval",         // "retrieval" | "llm" | "human_required"
  "why": "本則回答引用了 1 筆檢索命中；命中為 KB 向量相似度結果，未對資料集實檔驗證，請覆核後採用。",
  "refs": [
    { "id":"c1", "t":"新北市政府 112 年訴字第 123 號", "src":"歷史訴願決定書/…",
      "verified": false, "note":"KB 命中，未對資料集實檔驗證", "provenance":"official" }
  ],
  "dropped_refs": [],            // 模型引了但不在白名單的編號 → 該回合強制紅燈
  "redirect": null,              // 見下；只在「數字類問題」時非 null
  "refine_used": false,
  "memory": "on",                // "on" | "off"
  "session_truncated": false,
  "model_id": "…", "elapsed_ms": 4213
}
```

**前端必須做的三件事：**

1. **收到 `done` 之前不得顯示任何燈號。** 在那之前系統還不知道這則回答算哪一層。
2. **`lamp` 只有 `y`／`r`，不必為 `g` 寫分支。** 聊天沒有 N6 守門，`g`（可驗算／字號全部已驗）在任何輸入下都不會出現，後端有測試釘住。
3. **`redirect` 非 null 時，在該則回答下方放一顆 CTA，按下去捲到左欄的程序審查卡。**
   **不要把 agent 講的任何天數顯示出來。**

```jsonc
"redirect": {
  "endpoint": "/api/deadline",
  "reason": "期間計算由規則引擎負責，同輸入必同輸出、可逐步覆核；聊天不計算期限（CONSTITUTION §4）。",
  "cta": "查看程序審查的算式"
}
```

> `redirect` 在使用者問期限／天數／金額時**一定**會出現（機械規則，對問題做字面比對，寧可誤判成紅燈也不漏判）。
> `endpoint` 是**出處標示**，不是要前端去打那支 API——這一版的動作是**捲動**，不是呼叫。
>
> ⚠️ **「那顆鈕真的會捲得到」沒有後端驗收涵蓋。** AC7 只驗 `done.redirect.endpoint` 的值，驗不到前端行為。
> **這條要前端補一條驗收**（例：點 CTA 後程序審查卡進入視窗且被 highlight）。沒人補就是沒人驗。

---

## 3. 五支 chat 工具的 `hits[]` 形狀（`tool_result`；④⑤ 修訂）

> **初版寫「七種工具」並給每支一個 bespoke 的 `result` 物件。兩件事都改了：**
> 1. `extract`／`draft` **不是 chat 工具**，改走 `POST /runs`（§0.1、§1.6）。`graph`／`export` 本來就不是（§6）。
>    剩下五支——**而這五支後端全部已經實作，一支都不用新增**。
> 2. `tool_result` 的形狀是**統一的** `{call_id, tool, hits[], note, status}`，不是每支一個形狀。
>    後端 `RefBook` 對所有檢索工具產出同一種 entry。

### 3.0 工具值域（`tool` 欄位；一張表，前端照這個寫死 UI 文案）

| `tool` | `label`（後端會帶） | 產生引用 | `verified` | 後端實作 | 歸檔到 |
|---|---|---|---|---|---|
| `search_regulations` | 查法條 | 是（**無條文原文**） | **原樣帶出，可 true 可 false** | `backend/retrieval/lawtable.py` 查表 | `laws` 群組 |
| `search_similar_decisions` | 查相似訴願決定 | 是 | 恆 `false` | `KBRetriever.search()` | `cases` 群組 |
| `retrieve_refs` | 查判解與函釋 | 是 | 恆 `false` | `KBRetriever.search(filters={"prefix": REF_PREFIXES})` | 不歸檔 |
| `read_case` | 讀卷內 | 否（`hits: []`） | — | 唯讀呼叫端餵入的 payload | 不歸檔 |
| `refine_text` | 潤稿 | 否（`hits: []`） | — | 單獨一次模型呼叫；**本回合強制紅燈** | 不歸檔 |

> **`search_regulations` 的 `verified` 不是恆 true。** `LawTableRetriever` 會造四種 Hit，只有「條號存在於快照」
> 那一種是 `verified=True`；條號寫法無法無歧義解析、法規不在快照涵蓋範圍、條號在快照裡查無，**三種都是 false**。
> 後端**原樣帶出**；前端**不得**把 `verified:false` 畫成「字號已驗」。

### 3.1 `tool_result` 的統一形狀

```jsonc
// event: tool_result
{
  "seq": 1, "turn_id": "turn-…",
  "call_id": "tc-1",                     // 與 tool_call 配對
  "tool": "search_similar_decisions",
  "status": "ok",                        // ok | empty | failed（§2.3）
  "note": "",                            // status 非 ok 時這裡有話
  "hits": [
    {
      "id": "c1",                        // RefBook 配的本回合編號，前端就顯示這個
      "t": "新北市政府 112 年訴字第 123 號",   // 標題
      "src": "歷史訴願決定書/113年/…",       // 來源相對路徑
      "score": 0.79,
      "verified": false,
      "note": "KB 命中，未對資料集實檔驗證",
      "provenance": "official",          // official | public_crawl | unknown | **null**
      "origin": "retrieval"
    }
  ]
}
```

- `hits` 可以是空陣列。**空陣列不是錯誤**，用 `status` 區分是 `empty` 還是 `failed`。
- **`provenance` 是選填，可能是 `null`。** 它只存在於 KB hit 的 metadata；法條查表的 Hit 沒有這個鍵
  （`backend/retrieval/base.py:30-40` 的 `as_dict()` 沒有它）。**前端要處理 `null`，不要假設一定有值。**
- **`score` 顯示為相似度時必須標「向量相似度，非法律相似度」**（誠實紅線，§6）。
- 引用編號用 `[c1]` 這種標號出現在回答文字裡。模型引了白名單以外的編號 → 進 `done.dropped_refs[]`，
  **該回合強制紅燈**。

> **初版 §3.2／§3.3 的欄位多半拿不到。** `no`／`type`／`verdict`／`law`／`full`／`body` 是設計稿的假資料，
> 後端的 hit 沒有這些鍵。要顯示案號、案型、結果、全文，走 §4.3 的母庫端點（那裡有側檔 metadata 與 S3 全文），
> 不要期待 chat 的 `hits[]` 帶。

### 3.2 `read_case` 的 `section` 值域

`intake` | `facts_excerpt` | `screen` | `laws` | `cases`。給錯的 section 會回一句說明 + `hits: []`。

### 3.3 `refine_text` 一定紅燈

只要本回合用過 `refine_text`，`done.lamp` **強制 `r`**、`tier` = 「請人工判斷」、`origin` = `llm`，
即使同回合也檢索到 refs。這是機械規則（`classify_answer` 規則 2），不是模型自評。

> 初版 §3.5 設計的 `diff[]`（`{op:"del"|"add"|"keep"}`）與 `notes[]` **後端沒有**，`refine_text` 回的是改寫後的文字。
> 前端要做前後對照卡的話，diff 要在前端算（拿原文與 `token` 串出來的新文字比）。**這是前端工作，不是後端契約。**

### 3.4 解析卷證與生成草稿的產物形狀

這兩支走 `POST /runs`（§1.6），產物不在 `tool_result` 裡，而是跑完之後打 **#4 彙整版**或 **#21 單一產出**取得。

- **解析卷證** → 更新 case 的 `latest_run_id`；案由／訴願主張／爭點／程序審查燈在 run payload 裡，
  打 `GET /api/cases/{id}` 拿（欄位沿用既有 payload：`intake`／`facts_excerpt`／`issues[]`／`screen`）。
- **生成草稿** → 產生一個 artifact，打 `GET /api/cases/{id}/artifacts/{artifactId}` 拿 `sections[]`（§4.4）。

> **`procedure_checks` 的 `lamp` 值域**：既有 payload 用 `g`／`y`／`r`（`backend/gate/lamps.py`），
> 前端 `adapt.js:21` 已有 `{g:'ok', y:'warn', r:'bad'}` 的映射。初版契約寫的 `ok|warn|alert` 是設計稿的
> CSS class 名，**不是後端值域**——前端自己映射，不要叫後端改。
>
> **「分類信心 0.96」砍掉**（§6）：那是設計稿寫死的假數字，後端沒有可信的信心分數
> （`conf` 沒有鑑別力是已知問題 S-17，未修）。


## 4. 各資源 CRUD 的 payload

右欄「四群組」是**四個獨立資源的清單合起來畫**，不是一個泛用 endpoint。**開案首載打彙整版 `GET /api/cases/{id}`**
（回 `{case, files, laws, references, artifacts}`）省往返，之後各資源增刪打各自的端點。

### 4.0 持久化：一案一份 `manifest.json`（⑦ 新增；**不加資料庫**）

```
backend/output/cases/{case_id}/manifest.json
```

```jsonc
{
  "case_id": "upload-…", "name": "吉○實業 廢清法案", "created_at": "2026-09-12T21:40:00+08:00",
  "latest_run_id": "run-…",                    // 最後一次成功的 run，chat 的 run_id 用它
  "files":      [ {id,name,ext,note,readable} ],
  "laws":       [ {id,t,src,note,verified,relevance,body_cached} ],
  "references": [ {id,t,src,note,score,provenance,doc_kind,full_cached} ],
  "artifacts":  [ {id,name,kind,note,created_at,run_id} ]
}
```

**設計決定與理由：**

- **單檔不拆四檔**：開案首載本來就要一次全拿，單檔一次讀完最省。增刪＝read-modify-write。
- **加入時就把全文快取進去**（`body_cached`／`full_cached`），不要留到讀取時再打 KB／S3
  ——demo 當下不依賴外部服務還活著。
- **不引 RDS／DynamoDB**：要動 VPC、IAM、CDK、migration 與本地開發環境，是 30 小時內風險最高、
  收益最低的一件事；而要存的東西是單案幾 KB 的書籤。這份檔跟 `runstore`（`backend/output/runs/{run_id}.json`）
  同一個目錄樹、同一套 gitignore、同一個部署單元。

**三條限制（寫在這裡，不要讓人以為它是資料庫）：**

1. **不原子**。`runstore.py:36` 用 `p.write_text`，這個債已存在且被記錄過。寫 tmp + `os.replace` 三行可修，建議順手做。
2. **多副本會分裂**。但 chat session 本來就是進程內字典（`backend/api/chat.py:55`）、ECS 現在單台，不是新債。
3. **容器重啟就沒了**（2026-09-12 Ci 拍板接受）。`runs` 現在也是寫容器本地磁碟，同一個問題已經存在。
   **`manifest` 與 `runs` 一起接受，不搬 S3**——現在改儲存層等於在已驗綠的路徑上動刀。

> **母庫 vs 書籤，這是最容易搞混的一層：**
> **母庫＝Bedrock KB + S3**，它就是「資料庫」，而且已經有了，**搜尋打這裡**。
> **`manifest.json`＝書籤清單**，只存 id 與顯示欄位快取，**不是搜尋索引，搜尋完全不經過它**。
> 所以「不加 DB」跟「能不能搜尋」是兩件無關的事。

### 4.1 卷證檔案

- `GET /api/cases/{id}/files` → `{ files:[ {id,name,ext,note,readable} ] }`
  - `readable`：掃描影像／不支援格式回 `false`，UI 標「無法辨讀」（見 §6）。判斷沿用
    `backend/intake/documents.py` 的 `route_documents`（五種 `kind`，讀不到的產 `unreadable` 並在 `notes` 說原因，**不靜默跳過**）。
- `POST /api/cases/{id}/files`（multipart `files`）→ `201 { files:[…新上傳…] }`
  - **不限副檔名**（2026-09-12 Ci 拍板）。收下不等於讀得到。
- `DELETE /api/cases/{id}/files/{fileId}` → `204`

### 4.2 相關法規（母庫唯讀 ＋ 本案清單；⑥ 修訂）

> **⑥ 初版判「法規母庫做不出來」，那是錯的，已更正。**
> **668 部法規全文已在 KB**（`kb/public/相關法規_全量/*.txt`，含 668 個側檔 metadata），
> data source 的 `inclusionPrefixes` 是 `kb/` 全包，ingest 已 COMPLETE。
> 2026-09-12 實測：查「廢棄物之定義 本法所稱廢棄物」→ `相關法規_全量/廢棄物清理法.txt` 得分 **0.866**。
> `backend/retrieval/lawtable.py:15` 的 `ARTICLE_TEXT_AVAILABLE = False` 說的是 **snapshot 沒有條文**，不是資料不存在。

- **母庫查**　`GET /api/laws?q=關鍵字` → `{ results:[ {id,t,src,score,doc_kind} ] }`；查無 `{results:[]}`（不是錯誤）。
  - 實作：KB `retrieve(q)` + **server-side metadata filter**（實測可用）：
    ```jsonc
    {"vectorSearchConfiguration":{
       "numberOfResults":10,
       "filter":{"equals":{"key":"doc_kind","value":"statute"}}}}
    ```
  - **filter 是必要條件不是優化**：同一查詢不篩抓 20 筆，法規只佔 **1 筆**，前面全被裁判書與決定書佔滿。
  - **要開去重**（`dedupe_by_source`，`backend/retrieval/kb.py:534`，**預設 False**）：同一部法規會回多個 chunk。
  - `id` = **S3 相對路徑**（`_relative_path(uri)` 的 `rel`，`kb.py:529`，後端已經算出來了），穩定且可回查。
- **看全文**　`GET /api/laws/{lawId}` → `{ id,t,src,body,verified:false,relevance:"unknown" }`
  - `lawId` 就是 S3 相對路徑（URL-encode）。後端 `s3.get_object` 讀該 `.txt`。**不要從 KB 片段拼**
    ——那會拼出一份殘缺卻看起來完整的法規，正是 `CONSTITUTION` §1 要防的。
  - `verified:false` 不得畫成「字號已驗」；`relevance` 一律 `unknown`。
- **本案清單**　`GET /api/cases/{id}/laws` → `{ laws:[ {id,t,src,note,verified,relevance,body_cached} ] }`
- **加入**　`POST /api/cases/{id}/laws` body `{ "law_ids":["kb/public/相關法規_全量/廢棄物清理法.txt"] }` → `201 { laws:[…] }`
- **移出**　`DELETE /api/cases/{id}/laws/{lawId}` → `204`

> **法規有兩條通道，保證等級不同，不可以合成一個 `verified`：**
> **查表**（`retrieval/lawtable.py` + `laws-snapshot.json`）＝**引用可驗、二元、綠燈依據**，維持現狀不動；
> **KB 法規全文**＝**給人看與搜尋用，恆 `verified:false`，不影響 `lamp`**。畫面上也要分得出來。
> 現在對使用者宣告的驗證範圍是「`laws-snapshot.json` 的涵蓋範圍」（`backend/config/settings.py:500` 的 `dataset_scope`），
> 兩者混成一個旗標，燈號語意當場就穿。

### 4.3 相似案例／參考決定（母庫唯讀 ＋ 本案清單；⑥ 修訂）

- **母庫查**　`GET /api/decisions?q=關鍵字` → `{ results:[ {id,t,src,score,verdict,category,provenance,doc_kind} ] }`
  - 實作同 §4.2，`filter` 換成 `{"equals":{"key":"doc_kind","value":"decision"}}`。
  - **`court_ruling`（法院裁判書）本期不納入**（2026-09-12 Ci 拍板）。KB 裡有一整批
    `新北裁判書_環保局全量`，實測很會搶席次，但那是法院判的、訴願決定是訴願會決定的，
    混進「相似案例」等於把法院裁判說成訴願前例。
  - `verdict` 來自側檔 `outcome`（或決定書檔名），`category` 來自側檔 `category`。
    **案型沒有退路**：公開爬蟲那批檔名只有「案號_結果」，側檔缺席就誠實留 `null`，**不從內文猜**。
  - `score` 顯示為相似度，**必標「向量相似度，非法律相似度」**。
  - `provenance` ∈ `official`（賽方資料集）｜`public_crawl`（市府公開爬蟲）｜`null`，要能在畫面分辨；
    **`null` 要處理**（法條查表的 Hit 沒有這個鍵）。
- **看全文**　`GET /api/decisions/{decisionId}` → `{ id,t,src,verdict,category,full }`
  - 同 §4.2：S3 `get_object` 讀全文，**不要拼 chunk**。
- **本案清單**　`GET /api/cases/{id}/references` → `{ references:[ {id,t,src,note,score,provenance,doc_kind,full_cached} ] }`
- **加入**　`POST /api/cases/{id}/references` body `{ "decision_ids":["kb/public/新北訴願決定書_環保局全量/1151090848_駁回.txt"] }` → `201 { references:[…] }`
- **移出**　`DELETE /api/cases/{id}/references/{refId}` → `204`

### 4.4 產出

- `GET /api/cases/{id}/artifacts` → `{ artifacts:[ {id,name,kind:"draft",note,created_at,run_id} ] }`
- `GET /api/cases/{id}/artifacts/{artifactId}` → 草稿結構：
  ```jsonc
  { "artifact_id":"art-…", "title":"訴願決定書草稿 v1", "run_id":"run-…",
    "sections":[ { "h":"主文", "blocks":[
        { "text":"原處分撤銷，由原處分機關於2個月內另為適法之處分。",
          "cites":[ {"id":"L8","label":"訴願法§81 I"}, {"id":"C4","label":"案例1147030254"} ] } ]} ],
    "cite_count": 14 }          // **真值，從 payload 數**，不要沿用設計稿寫死的「14 處」
  ```
  - 後端實作：`load_run(run_id)` → `build_payload()` → 取 `doc[]` 轉成 `sections[]`。
  - 「來源控管」文案只有在**每個 cite 真的可回溯**時才顯示，否則拿掉。
- `DELETE /api/cases/{id}/artifacts/{artifactId}` → `204`（從 manifest 移除，**不刪 run**）
- `GET /api/cases/{id}/artifacts/{artifactId}/export?format=html|md` → 回檔案（`Content-Disposition: attachment`）

> **同一份文件被切多 chunk**：`GET /api/laws`、`GET /api/decisions` 與本案清單 **後端先去重再回**
> （`dedupe_by_source`，預設 False，**母庫查要記得開**），否則「命中 5 筆」其實只有 2 份文件。

---

## 5. 前端已有、要沿用的行為

- **串流斷線**：`token` 收到一半斷了 → 顯示「連線中斷，可重問」，**不要**當成模型錯誤（`error.stage:transport`）。
  連兩次拿不到 `done` 退化為非串流：**`?stream=0` 已經實作，是既有契約的一部分**
  （`backend/api/chat.py:222-238`；`docs/spec/2026-09-12-chat-honesty-lamps.md` §2.2），
  body＝`done` 物件攤平 + `events[]`（本來會逐筆發的 `tool_call`／`tool_result`）。**前端要寫這條分支。**
  它存在的理由是：賽場網路讓 SSE 斷流時切過來，**而不是**在本地用 CSS 演一段沒發生的串流。
- **忙碌鎖**：一個工具跑的時候 `state.busy=true`，chips 換成「工具執行中…」，送出鍵 disabled（`Composer.vue canSend`）。後端不需配合，這是前端節流。
  - ⚠️ **解析卷證 10–11 秒、生成草稿最久 72 秒**（§1.6 實測）。忙碌鎖要能撐這麼久，且要有進度（#26 的 `node_done`），不要只轉圈。
- **檔位誠實**：`/health` 回 `run_mode`；離線重播檔位時，chat 會回 503，UI 要說「聊天需要即時模型」，**不得**演一段假對話。

---

## 6. 設計稿要砍／要改的（沿用舊契約 §5 的結論，對應到新 UI）

| 砍／改 | 位置 | 理由 |
|---|---|---|
| **關聯圖** `build_relation_graph` | `data.js TOOLS[graph]`、`RelationGraph.vue`、`graph.js GNODES/GEDGES` | 後端無此節點、不產關聯資料，整段是前端模擬。已另有獨立展示頁 `plans/kb-graph.md`（Ci 拍板「不接工作台、不動主線流程」）。**demo 想保留就明講「示意圖，非後端產物」**，否則砍 |
| `export_pdf`/`export_docx` 當成 chat 工具 | `data.js TOOLS[pdf,doc]`、`ToolOut out.type==='export'` | 匯出是**下載**（§1.5 #23），不是 agent 工具。**且本期只做 `html`／`md`**，PDF 交給瀏覽器列印 |
| **`extract`／`draft` 當成 chat 工具** | `data.js TOOLS[extract,draft]`、`store IMPL.extract/draft` | **③ 新增此列。** 兩者改打 `POST /runs` + 訂閱 `GET /runs/{id}/events`（§1.6），不發 chat |
| **`tool_step` 逐行打勾** | `store toolBlock` 的 `steps[]`、`Chat.vue` 工具卡 | **② 新增此列。** chat 的五支工具沒有內部階段，硬生就是編。逐行打勾改用 #26 的 `node_start`／`node_done`；chat 工具卡用單純 spinner |
| `/` 斜線指令選單 | `Composer.vue` slash 選單 | 工具由 agent 決定並發 `tool_call`；留著會讓人以為「點了就一定跑那支」。可保留為**輸入輔助**但別暗示保證。**但「解析卷證」「生成草稿」兩項是確定會跑的**（它們不經過 agent 判斷） |
| 多案件資料夾／拖曳 | `CaseTree.vue`、`store folders/moveCase` | **維持純前端**（§0.2，localStorage），不落地後端。此列為「保留但不接後端」，非砍 |
| 「分類信心 0.96」 | `ToolOut.vue extract meta` | 寫死假數字，砍。後端沒有可信的信心分數（`conf` 沒鑑別力是已知問題 S-17，未修） |
| 「相似度 94%」`sim` 寫死 | `data.js CASE_POOL.sim`、`ToolOut cases` | 真值來自 `hits[].score`，且**標「向量相似度非法律相似度」** |
| 「引註 14 處」寫死 | `store draft note`、`ToolOut draft` | 真值從 artifact 的 `cite_count` 數（§4.4） |
| 「已上傳，可直接改」等上傳文案 | 上傳 sheet 文案 | 目前**不做 OCR**，掃描影像回 `readable:false`＋標「無法辨讀」，不要寫成支援 |

**三條不能省**（誠實紅線）：
1. 法規綠燈只代表**字號對得回法規快照**，`relevance` 一律 `unknown`——不代表與本案相關。
2. 相似度是**向量相似度**，KB 命中未對資料集實檔驗證，畫面要標明。
3. **收到 `done` 之前不得顯示任何燈號**；`redirect` 非 null 時**不得顯示 agent 講的任何天數**（§2.4）。

---

## 7. 一句話總結給後端

這版前端需要：

1. **兩條串流**：
   - `POST /cases/{id}/chat`（SSE）——agent 呼叫 `search_regulations`／`search_similar_decisions`／`retrieve_refs`／`read_case`／`refine_text`
     **五支既有工具**，事件 `ack`/`tool_call`/`tool_result`/`token`/`done`/`error`（**無 `tool_step`**）。
     `done` 必帶分層誠實燈號（§2.4）。
   - `POST /cases/{id}/runs` → `202` + `events_url`，訂閱 `GET /runs/{run_id}/events`（SSE）——解析卷證與生成草稿的逐節點進度。
2. **RESTful 一次性端點**，母庫唯讀、本案子資源 C/R/D：
   - 系統／案件：`GET /health`、`GET|POST /cases`、`GET|PATCH|DELETE /cases/{id}`
   - 卷證：`GET|POST /cases/{id}/files`、`DELETE …/files/{fileId}`
   - 法規：`GET /laws?q=`、`GET /laws/{lawId}`（母庫唯讀，KB filter `doc_kind=statute` + S3 全文）＋ `GET|POST /cases/{id}/laws`、`DELETE …/laws/{lawId}`
   - 案例：`GET /decisions?q=`、`GET /decisions/{decisionId}`（母庫唯讀，filter `doc_kind=decision`）＋ `GET|POST /cases/{id}/references`、`DELETE …/references/{refId}`
   - 產出：`GET /cases/{id}/artifacts`、`GET|DELETE …/{artifactId}`、`GET …/{artifactId}/export?format=html|md`
3. **持久化**：一案一份 `manifest.json`（§4.0），**不加資料庫**。

**沒有輪詢**；三類卷宗成員只有 C/R/D 沒有 U（不改母庫）；關聯圖與資料夾維持前端本地或砍（§6）。

### 後端真正的新工只有兩塊

其餘都是接線與改字——chat 的五支工具、pipeline 的續跑（`from_node`＋`base_run_id`）、
`GET /runs/{id}/events` 的真串流、KB 檢索，**全部已經存在**。

1. **`gen()` 改真串流**（`backend/api/chat.py:243-266`）：emit 推 queue、邊跑邊 yield。
   要顧到 `?stream=0` 與 SSE 共用同一條 `_run_turn` 的既有契約。
2. **`manifest.json` 持久化 + 13 支 CRUD**（#2, #4–#9, #12–#14, #17–#19, #20–#22）。**這塊不碰對話框，可立刻開工。**

外加三件小的：chat 事件加 `call_id`／`status`／`ack.session_id`；母庫查四支（#10/#11/#15/#16）；#23 匯出改 html／md。
