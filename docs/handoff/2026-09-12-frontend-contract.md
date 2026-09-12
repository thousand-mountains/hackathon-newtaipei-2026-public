# 前端技術交接：新設計稿接後端契約（2026-09-12）

> 讀者：Pink（寫 Vue）。**看完這份就能動手，不必讀後端程式碼。**
> 唯一契約真實來源：`docs/spec/2026-09-12-chat-honesty-lamps.md`（聊天）＋本檔第 1、2 節（既有六節點）。
> 行號依本檔撰寫當下的工作樹（HEAD `5c92e3a` ＋ 未追蹤的 spec/plan）。`backend/api/app.py` 仍在被其他 session 改動，**定位請用函式名**。
> 本檔只讀不改；沒有動 `frontend/` 任何檔。

## 1. 既有 endpoint 的請求／回應形狀

`backend/api/app.py` 目前共 **9 支路由**（`grep -nE '@app\.(get|post)' backend/api/app.py` 實數）。與前端相關的八支：

| 方法 | 路徑 | 定義 | 成功 | 回什麼 |
|---|---|---|---|---|
| GET | `/api/health` | `app.py:271` | 200／**503** | `{ok, checks[], run_mode, fixture_only, kb_backend, model_ids, cases_available[], frontend_served, provenance}`。503 ＝檢查沒過，**不是離線**（`frontend/src/api/client.js:73-82` 已這樣處理） |
| GET | `/api/cases` | `app.py:299` | 200 | `{cases[], synthetic[], uploaded[], note}` |
| POST | `/api/cases`（multipart `files`） | `app.py:311` | **201** | `{case_id, files[], provenance, next}` |
| POST | `/api/cases/{id}/runs` | `app.py:383` | 200 **或 202** | 見 §1.1 |
| GET | `/api/runs/{id}` | `app.py:431` | 200／**409**／502 | 見 §1.2 |
| GET | `/api/runs/{id}/events` | `app.py:456` | 200 SSE／404 | 見 §2 |
| POST | `/api/cases/{id}/submit` | `app.py:494` | 200／**409** | 見 §1.3 |
| POST | `/api/deadline` | `app.py:619` | 200／400 | `Result.as_dict()` ＋ `verdict{lamp,text,why,basis,origin,days}`（`app.py:586-618`）。body 欄位見 §3 的 `redirect.body_schema` |

### 1.1 `POST /api/cases/{id}/runs` — live 檔位回 **202 不是 200**

- fixture 檔位：**200** ＋ 完整 CASE payload（同步跑完）。
- bedrock 檔位：**202** ＋ `{run_id, status:"running", result_url, events_url}`（`app.py:420-428`）。要輪詢或接 SSE。
- 現成寫法在 `frontend/src/api/client.js:102-111`（`{done:true,payload}` vs `{done:false,...}`），新前端照抄即可。

### 1.2 `GET /api/runs/{id}` — **409 是「還在跑」，不是錯誤**

`app.py:441-443`。後端刻意用 409 而非「200＋狀態欄位」，就是不讓前端把「還沒跑完」誤讀成一份空結果。
- **409** → `{run_id, status:"running"}`，繼續等。
- **502** → `{run_id, status:"failed", node, error}`，這才是失敗，顯示 `error` 原文。
- **200** → 完整 payload。
輪詢實作參考 `frontend/src/api/run.js:88-103`（2 秒一次，10 分鐘上限）。

### 1.3 `POST /api/cases/{id}/submit` — **409 是正常回應，帶 blockers**

`app.py:544-546`。後端會**重跑六節點**再判斷，完全不信前端送的 `submit_allowed`（`app.py:502-510`）。
- 200 → 收據，含 `external_effect:"none"`（`app.py:557-562`）。UI 上的「已送出」**必須**同時說出「只寫進本機檔案、沒有寄信、沒有排議程」。
- **409** → `{...common, blockers[], accepted:false}`。
- ⚠️ **`blockers` 保證非空**（commit `5c92e3a`，`app.py:522-543`）：流程停在 N1（`NEEDS_INPUT`）時守門節點沒跑到、原本 `blockers` 會是空的，現在後端會補一條
  `{level:"P0", code:"run_incomplete", node:"n1", why:…, how_to_clear:…}`。**前端要顯示 `how_to_clear`**，不要只畫一個「被擋」。

## 2. 六節點 SSE（`GET /api/runs/{id}/events`）

wire 格式：`event: <名稱>\ndata: <一行 JSON>\n\n`（`backend/api/events.py:71`）。

| 事件 | payload | 來源 |
|---|---|---|
| `node_start` | `{run_id, node, agents[]}` | `backend/orchestrator/graph.py:304` |
| `node_done` | `{run_id, node, elapsed_ms, degraded, agents[], narrative{…}}` | `graph.py:325-333` |
| `run_done` / `run_failed` | `run_failed` 帶 `{node, error}` | `events.py:50-57`（終止事件，發完關流 `events.py:74`） |
| `timeout` | `{}` | 閒置 300 秒（`events.py:63,79`） |

`node` ∈ `n1…n6`，中文標籤沿用 `frontend/src/api/run.js:20-27`（讀卷證抽取／案件分類／程序審查與期間計算／檢索/撰寫草稿/逐句查核）。

**紅線：事件流斷掉一律退回輪詢，不得當成執行失敗。** 事件只活在該 process 記憶體、沒有回放（`app.py:466-473`）；結果永遠在 runstore，用 §1.2 拿。現有實作 `run.js:32-63` 把 SSE 所有錯誤靜靜吞掉，這是對的行為，保留。

### ⚠️ 已知限制：US-7 逐節點進度目前**沒有生效**

現象（team lead 實測）：SSE 連得上、`GET /events` 回 200，但畫面從未填入節點；同源環境亦然。
**根因（我的程式碼推導，未實測驗證）**：`run.js:43` 用 `es.onmessage`，而 `EventSource` 的 `onmessage` 只接收**沒有 `event:` 欄位**的訊息；後端每一筆都帶具名事件（`events.py:71`）。要收到必須改用 `es.addEventListener('node_start'|'node_done'|…)`。
**Pink 請照這樣做**：把輪詢當保證路徑先做完並確認可用，SSE 逐節點動畫當加值層另外掛；改完要實測「畫面真的逐格亮」才算通過（靜態機檢看不見這件事）。

### 設計稿工具卡 ↔ 節點事件對照

`design/訴願智慧輔助平台.html:590-622` 的 `TOOLS[]` 共 8 支（該檔文案自稱「7 支」，`:1513`，屬設計稿內部不一致）。對到六節點：

| 設計稿工具卡（`api`） | 對應節點事件 | 說明 |
|---|---|---|
| `extract_case_document` | `n1` ＋ `n2` ＋ `n3` | 一張卡對三個節點：抽取／分類／程序審查。動畫要能吃三段 |
| `search_similar_decisions` | `n4` | **兩張卡由同一個 `n4` 事件同時點亮**（見下方拍板） |
| `search_regulations` | `n4` | 同上 |
| `generate_decision_draft` | `n5` | |
| （逐句查核／燈號） | `n6` | ⚠️ 設計稿**漏了這張卡，要補**（見下方拍板）。燈號來源就是它 |
| `build_relation_graph` | 無 | 後端沒有這個節點 → 砍（§5） |
| `refine_text` | 無節點 | 屬聊天工具（§3），不是六節點 |
| `export_pdf` / `export_docx` | 無 | 後端無此端點（9 支路由裡沒有）→ 砍（§5） |

#### 拍板（2026-09-12）：不加後端事件，一個節點事件可以點亮多張卡

1. **`n4` 完成時同時點亮「查相似案例」與「搜尋法規」兩張卡。**
   那個節點內部本來就是兩個通道（法規查表、案例檢索），完成時 payload 同時帶兩邊結果。
   兩張卡一起從「執行中」轉「完成」——**這是實話，不要假裝它們分別完成。**
2. **`n6` 要補一張卡。** 守門是唯一會擋下生成的環節，也是本專案的差異化；
   漏掉它，評審看到的流程會像一般的 RAG 問答。
3. **不加後端事件。** 改 orchestrator 的事件面是動主線，30 小時內不值得；
   而「卡片級細度」是視覺需求，用一個事件驅動兩張卡就能達到。

### 2.1 ⚠️ Pink 現有分支的進度條跟後端六節點對不上

`origin/feat/frontend-ai-layout` 的 `frontend/src/components/Chat.vue:26`：

```
title="辦案進度：萃取 → 案例 → 法規 → 關聯圖 → 草稿 → 產出"
```

後端實際六節點是：**萃取／分類／程序審查／檢索（法規與案例同一節點）／草稿／守門**。

| Pink 現有格子 | 後端節點 | 處置 |
|---|---|---|
| 萃取 | `n1` | 保留 |
| （無） | `n2` 分類 | **新增** |
| （無） | `n3` 程序審查與期間計算 | **新增**（期間引擎攤開算式是主秀） |
| 案例 | `n4` | 保留，但**與下一格同時點亮** |
| 法規 | `n4` | 保留，同一個 `n4` 事件；**不要假裝兩者分別完成** |
| 關聯圖 | 無 | **砍**。後端沒有這個節點，payload 也沒有圖結構。若要保留，只能降級成畫既有引註關係（句子→法條）並明標不做相似性推論 |
| 草稿 | `n5` | 保留 |
| 產出 | — | 與 `n6` 對調語意：改成守門 |
| （無） | `n6` 守門 | **一定要補**。守門是唯一會擋下生成的環節，也是本專案的差異化；漏掉它，評審看到的流程會像一般的 RAG 問答 |

### 2.2 ⚠️ 燈號顯示要從零建，但**不要從零想**

用 `lamp`／`tier`／`verify`／三個 tier 的中文字樣掃過 `origin/feat/frontend-ai-layout` 的
整個 `frontend/src`：**六個關鍵字全部零命中**。分層誠實的顯示在她的分支上完全不存在。

但 `main` 上有完整的既有實作可以直接取回參考：

| 檔案（在 `main` 上） | 有什麼 |
|---|---|
| `frontend/src/api/adapt.js` | payload → 燈號的轉接 |
| `frontend/src/components/DecisionSheet.vue` | 決定書逐句燈號渲染 |
| `frontend/src/components/ProcPanel.vue` | 程序審查卡（含三態 `agree` 與 `shared_blind_spot`） |
| `frontend/src/components/LeftColumn.vue` | 收文確認／爭點區 |
| `frontend/src/store/workbench.js` | 燈號狀態與 `compared`／`blockers` 處理 |

**這是「取回參考」不是「搬過來調樣式」**——資料形狀有變（見 §4），但渲染邏輯與用字可以省下大半時間。

## 3. 新聊天端點 `POST /api/cases/{case_id}/chat`

> 契約已凍結，以下逐欄照抄 `docs/spec/2026-09-12-chat-honesty-lamps.md` §2–§4。**後端尚未實作**（`backend/api/` 目前只有 `app.py`／`events.py`），Pink 用 spec §6 的 mock 先開工。

### 3.1 Request

```jsonc
{
  "run_id": "run-…",        // 必填，必須是一次已完成的 run，且與 path 的 case_id 同案
  "message": "…",           // 必填，非空
  "session_id": "sess-…",   // 選填；省略＝開新對話
  "context": {              // 選填，選取句
    "scope": "sentence",    // "sentence" | "all"（省略＝all）
    "sent_id": "s12",       // scope=sentence 必填，對應 doc[].ss[].id
    "text": "…"             // 該句原文
  }
}
```
**不要把選取句拼進 `message` 字串**，用 `context`（spec §2.1）。

### 3.2 錯誤（**全部 JSON，不是 SSE**；spec §2.3）

| 狀態 | 何時 | body |
|---|---|---|
| 400 | `message` 空／`run_id` 屬於別案 | `detail` |
| 404 | `run_id` 不存在 | `detail` |
| 409 | 該 run 還在跑 | `{run_id, status:"running"}` |
| 502 | 該 run 失敗 | `{run_id, status:"failed", node, error}` |
| **503** | 非 live 檔位／缺 live 設定 | `{detail, run_mode, missing[], why}` |

503 的閘門在**開串流之前**：拿到 `text/event-stream` 就保證至少有一個 `done` 或 `error`。

### 3.3 SSE 事件（四種＋`error`，共五種；spec §4）

共通欄位：`seq`（本回合 0 起單調遞增，可偵測漏收）、`turn_id`。

- **`tool_call`** — `{seq, turn_id, tool, args{}, label}`。`tool` 值域固定五個（spec §4.0）：
  `search_regulations`（查法條）／`search_similar_decisions`（查相似訴願決定）／`retrieve_refs`（查判解與函釋）／`read_case`（讀卷內）／`refine_text`（潤稿）。`label` 後端會帶，直接顯示。
- **`tool_result`** — `{seq, turn_id, tool, hits[], note}`。
  `hits[]` 每筆：`{id:"c1", t, src, score, verified, note, provenance}`。
  `hits: []` **不是錯誤**，是查無。`read_case`／`refine_text` 恆為 `[]`。
  `verified` **原樣帶出**——`search_regulations` 也可能是 `false`，不得畫成「字號已驗」。
  `provenance` ∈ `official|public_crawl|unknown`，**可能是 `null`**（法條 hit 沒這個鍵），要有 fallback 文案，不要顯示 `undefined`。
- **`token`** — `{seq, turn_id, text}`，直接 append。**收到 `done` 前不得顯示任何燈號。**
- **`done`** — 一回合結束，**唯一帶燈號的事件**。全部 top-level key（spec §4.4）：
  `seq`、`turn_id`、`session_id`（memory off 時 `null`）、`answer`、`lamp`、`tier`、`origin`、`why`、`refs[]`、`dropped_refs[]`、`redirect`、`refine_used`、`memory`、`session_truncated`、`model_id`、`elapsed_ms`。
  `refs[]` 每筆：`{id, t, src, verified, note, provenance}`（比 `hits[]` 少 `score`）。
- **`error`** — `{seq, turn_id, stage, error}`，發完關流。**沒有 `done`。**

序保證（spec §4.7）：`done` 與 `error` 互斥且必為最後一個；`tool_call`／`tool_result` 成對、同一 tool 可多次；`token` 可為零筆；**沒有回放**，斷線即斷線。

### 3.4 `?stream=0` 一次性 JSON（spec §2.2）

`POST …/chat?stream=0` → `200 application/json`，body ＝ §3.3 那個 `done` 物件**再加一個 `events[]`**（本來會逐筆發的 `tool_call`／`tool_result`／`token`）。全部跑完才回，約 5–15 秒。
觸發條件：**連續兩次串流拿不到 `done`** 就切這條。**不得**在本地用 CSS 演一段沒發生的串流。

## 4. 誠實燈號怎麼渲染

`lamp` 只會是 `y` 或 `r`，**`g` 永不出現**（spec §3.1），不必為 `g` 寫分支。既有映射 `frontend/src/api/adapt.js:21`（`g→ok, y→warn, r→bad`）可直接沿用。

| `lamp` | `tier` | `origin` | 何時 | 畫面 |
|---|---|---|---|---|
| `y` | 有出處 | `retrieval` | 有 refs 且回答引到 | 「有出處 · 依據 N 筆卷內／檢索來源」＋可展開 `refs[]`（每筆顯示 `t`、`provenance` 標籤、`note`） |
| `r` | 請人工判斷 | `human_required` | 問題是數字類（期限／金額） | 顯示 `redirect.cta` 按鈕；**不顯示答案裡任何天數** |
| `r` | 請人工判斷 | `llm` | 用過 `refine_text`，或無 refs／引了沒命中的 | 「請人工判斷 · 模型生成，系統未替其背書」 |

每則 AI 回覆底部固定要有：**一顆燈 ＋ `tier` 文字 ＋ `why` 那一句**。另外：
- `refs[]` 裡 `verified:false` 必須看得出來，不得畫成「字號已驗」。
- `dropped_refs[]` 非空 → 明示「模型引用了檢索結果之外的來源，已移除」，**不要靜默吞掉**。
- 收到 `error` → 顯示 `error` 原文，**不得**退回任何內建範例對話。
- 503 → 顯示「目前為離線重播檔位，聊天需要即時模型」並列出 `missing[]`，**不得**演一段離線對話。

`error.stage` 四個值（spec §4.6）：

| `stage` | 畫面該說 |
|---|---|
| `tool` | 「查詢來源失敗」，可重問 |
| `model` | 「模型沒有回應」，可重問 |
| **`transport`** | 「連線中斷」，可重問。**2026-09-12 新增的契約變更**（原本只有三個值），開流之後的傳輸／proxy 失敗歸這裡，**不要說成模型錯誤** |
| `internal` | 照實顯示 `error` 原文，這是 bug |

> **這是「一開始就寫成四個值」，不是「改已經寫好的三個」。** 實查
> `origin/feat/frontend-ai-layout`：目前沒有任何 stage 處理（那是 mock UI），
> 所以現在分出來比事後補容易。
>
> 為什麼要獨立出 `transport`：開流之後的網路斷線原本只能歸進 `internal`，
> 於是**畫面會把使用者的網路問題說成我們的系統故障**——那既不準確，也會讓
> 「可重問」這個正確建議變成「這是 bug」的錯誤暗示。

## 5. 設計稿要砍掉的功能

| 砍什麼 | 位置 | 理由 |
|---|---|---|
| 斜線指令 `/` 選單 | `:512-523`、`:590-622` | 聊天契約沒有「前端指定工具」這件事——工具由 agent 自己決定並發 `tool_call`（spec §4.1）。留著會讓承辦人以為按了就一定會跑那支 |
| 多案件資料夾／拖曳 | `:490`、`:724-757`、3 處 `draggable`/`dragover` | 後端沒有資料夾概念：`GET /api/cases` 只回 `synthetic[]`／`uploaded[]` 兩個平鋪清單（`app.py:299-308`），沒有任何移動／改名／刪除端點 |
| 關聯圖（`build_relation_graph`） | `:603-606`、`:1110-1132`、`:1215-1290` | 後端 9 支路由沒有這支，六節點也不產關聯資料。整段是純前端模擬 |
| `export_pdf` / `export_docx` | `:617-622`、`:1183-1213` | 同上，後端無此端點 |
| 「分類信心 0.96」 | `:1013` | 寫死的假數字；後端 payload 沒有分類信心欄位 |
| 「相似度 94%」等 `sim` | `:851`、`:1067`、`:1078` | 寫死；真值來自 `tool_result.hits[].score`。**必須標「向量相似度，非法律相似度」**——`verified:false` 代表未對資料集實檔驗證，跟「內容相關」是兩件事 |
| 「引註 14 處」「全文 14 處引註」 | `:1151`、`:1152`、`:1379` | 寫死；真值要從 payload 數 |
| 「來源控管」那段保證文案 | `:1379` | 宣稱「皆對應右側卷宗、可逐一點按回溯」——目前系統擔保不了這句 |

**這兩條不能省：**
1. **`relevance` 一律 `unknown`。** 燈綠只代表字號能對回法規快照，**不代表系統確認它與本案相關**（`frontend/src/api/adapt.js:13-21` 的 `RELEVANCE_UNKNOWN` 已有現成文案，沿用）。
2. **相似度要標「向量相似度非法律相似度」。** KB 命中沒有對資料集實檔驗證。

## 6. Pink 專屬待辦

### ⚠️ 三件目前無主，不要因為這份檔進了 git 就當成已結案

| # | 事 | 卡在誰 |
|---|---|---|
| 1 | **`done.redirect` 那顆鈕會不會動，沒有人在驗。** 後端驗收只驗回傳值，驗不到前端行為 | **Pink 補一條前端驗收** |
| 2 | 聊天檢索取幾筆（`top_k`）——目前會**靜默吃函式預設 5**，那是「沒人決定」不是「已決定」 | Claire 拍板 |
| 3 | 賽制是否把聊天呼叫算進每秒一次的流量 | Ci 向賽方確認 |


1. **補一條前端 AC：`done.redirect` 那顆 CTA 會動。** 動作**已定案為「捲到左欄程序審查卡」**（tech lead 2026-09-12），**不是**呼叫期間試算——那個面板不存在（`frontend/src/` 沒有任何程式打 `POST /api/deadline`；`ProcPanel.vue` 只渲染 payload 的 `screen.deadline`）。後端 AC7 只驗 `done.redirect.endpoint == "/api/deadline"` 的值，**驗不到這顆鈕**。沒有人補就是沒人驗。建議 AC：點 CTA 後程序審查卡進入視窗且被 highlight。
2. **US-7 逐節點進度**：先確認輪詢路徑（§1.2）完全可用，再修 SSE 具名事件監聽（§2 已知限制），改完要**實際點下去看畫面逐格亮**才算過。
3. **`?stream=0` 降級分支**（§3.4）要跟串流分支同時做，臨場才寫來不及。
4. **`provenance: null` 的 fallback 文案**、**`verified:false` 的視覺**、**`dropped_refs` 的提示** 三處各寫一次，不要漏。
5. **上傳 UI 的格式文案要重寫，而且要寫得保守。**
   後端 2026-09-12 起不限制格式（`backend/intake/uploads.py:18-22`）。
   - `frontend/src/components/UploadPage.vue:97` 目前寫「PDF／TXT，單檔 20MB」——**那是使用者真正會讀到的一行**，已過時。
   - 設計稿 `design/petition-ai-demo.html:279` 寫「PDF／DOCX／掃描影像」，方向對但**不能照抄**：
     **目前不做 OCR，掃描影像會被標成 `unreadable`。**
   - 文案要寫成「掃描影像會被標示為無法辨讀」，**不要寫成支援**。
     寧可保守，也不要讓使用者以為丟掃描件會有結果。
   - 實際可讀：`.txt/.md/.csv/.json`、`.docx`（標準庫解）、有文字層的 `.pdf`；
     其餘收下但回 `kind="unreadable"` ＋ notes 說明原因與該怎麼辦。
   （`app.py:311`／`:22` 兩處過時說明已於 `c7737bc` 修正。）
6. **submit 409 要顯示 `blockers[].how_to_clear`**（§1.3）。
