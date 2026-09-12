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
| ① | §2.4 | `done` 的燈號欄位**後端照送、契約不複製定義**（指回 spec §4.4）；**前端本期不做燈號 UI**，只讀 `redirect` 與 `dropped_refs` 兩項 | 初版把整組欄位漏掉等於退掉 AC5–AC12；但前端從來沒畫過這塊，30 小時內從零做不划算 |
| ② | §2.3 | 加 `call_id`、`tool_result.status`；`session_id` 提前到 `ack`；`tool_step` **保留但改成真實來源** | 成對事件缺配對鍵；工具失敗會被畫成「查無」；斷線續不回；原本的 step 是前端 `sleep()` 假的 |
| ③ | §0.1 §1.6 §3 | **維持「前端只打 chat 一支」**（Ci 拍板）。`extract`/`draft` 在 chat 內部觸發 pipeline，`tool_step` 轉發真實節點事件 | 介面已是「工具卡＋對話框」，統一入口對前端最省。後端要補 `to_node`、RefBook 重置、`gen()` 真串流 |
| ④ | §2.1 §3 | chat 工具統一用後端長名，值域固定 **七支** | 原本四套命名互不相容，契約自己前後打架 |
| ⑤ | §3.2 §3.3 §4 | `hits[]` 對齊後端實際欄位；`id` = S3 相對路徑；全文從 S3 讀 | 原本的欄位多半是設計稿假資料；KB 回的是 chunk 不是全文 |
| ⑥ | §1.3 §1.4 §4.2 §4.3 | 母庫查**兩支都保留**，改用 `doc_kind` server-side filter | 668 部法規全文已在 KB 且實測命中；不篩就撈不到 |
| ⑦ | §4 | 新增 §4.0：持久化＝一案一份 `manifest.json`，**不加資料庫** | 四份清單原本無處可存 |
| ⑧ | §1.5 §4.4 | 匯出**維持 `pdf`/`docx`**（Ci 拍板，2026-09-12 23:50 覆蓋前一版的降級） | 承辦人拿到的必須是能直接送簽／續編的格式，`.md` 交不出去 |
| ⑨ | §3.7 | **關聯圖改成後端新功能**（原本列在「建議砍」） | 邊在 payload 裡已經是現成的（`citations[].sentence_id` → `resolved_id`），零 LLM 可組 |

已拍板（2026-09-12 Ci）：

- **建案**維持「上傳卷證即建案」。
- **相似案例**先只收 `doc_kind=decision`，裁判書本期不露出。
- **持久化**：`manifest` 與 `runs` 都接受「容器重啟即失」，不搬 S3。
- **AC7** 在新語料下要重跑（語料從 2477 長到 16970，多了整批裁判書）。
- **前端只打 chat 一支。** 介面已改成「工具卡＋對話框」的形式，所有工具都走 chat。
- **分層誠實燈號本期前端不渲染。** 後端照送（欄位全留，已驗綠、拿掉沒有好處），
  前端不做燈號 UI。**見 §2.4 的兩項例外，那兩項不是裝飾。**
- **關聯圖後端要做**，計畫見 `plans/2026-09-12-relation-graph.md`。
- **生成草稿的三個前置條件**（卷證／已解析／法規＋案例）**兩邊都要擋**（§3.5.1）。
- **手動挑的法規當查詢詞餵回 N4**（`overrides.n4_query`，§3.5.2）；案例本期仍只是參考資料。

## 0. UI 心智模型（先懂這個，契約才看得懂）

不是「按一顆按鈕跑完六節點」，而是**一個對話室 + 一疊卷宗**：

- **左欄**：案件清單，可分資料夾／拖曳整理。**資料夾純前端**（見 §0.2），案件本身走後端。
- **中欄**：對話串。承辦人打字或點建議 chip → agent 呼叫**工具**（tool）→ 工具逐步執行、吐出結果卡。
- **右欄**：案件卷宗，四個群組：`evidence`（卷證檔案）／`cases`（相關案例）／`laws`（相關法規）／`out`（答辯書與產出）。工具跑完會把結果**歸檔**到這裡；使用者也能自己搜尋加入或移除。

一次辦案的典型順序（`store/app.js` 的 `chips` 決定引導）：
上傳卷證 → `解析卷證` → `查案例`＋`查法規` → `生成草稿` → `優化`／`匯出`。

**這個順序不是建議，是前置依賴**（§3.5.1）：
- `解析卷證` 需要**卷證檔案**
- `生成草稿` 需要**已解析的卷證** ＋ **相關法規** ＋ **相關案例**，三個缺一不可
- 缺前置時**兩邊都要擋**：前端 disable 按鈕，後端工具回 `status:"failed"`。
  後端不擋的話會生出一份通篇引用都被清空的草稿——**那個失敗看起來很像成功**。

**關鍵差異**：工具由「使用者的訊息」觸發（打字命中關鍵字、或點 chip、或 `/` 選單），agent 端執行。所以主體是一支**聊天端點**，其餘是卷宗與案件的 CRUD。

### 0.1 「前端 API」與「agent 工具」是兩層（③ 修訂：維持單一入口）

```
前端  ──POST /cases/{id}/chat（SSE）──▶  後端 agent  ──呼叫工具──▶  解析卷證 / 查法規 / 查案例 /
     （對話動作只認識這一支）              （自己決定用哪個）        撰稿 / 潤稿 / 讀卷 / 關聯圖
```

- **前端契約**：使用者的每次對話動作只打 `POST /cases/{id}/chat`。**七支工具全部走這裡**，前端不直接呼叫 `/runs`、`/extract`、`/draft`。
- **agent 工具**：工具是**後端 agent 內部**的呼叫，對前端不可見。前端只透過串流事件看到「agent 用了哪個工具、跑到哪一步、結果是什麼」（`tool_call` / `tool_step` / `tool_result`，§2.3）。
- **同一份能力、兩個入口**：agent 的「查法規」工具，跟使用者手動搜尋加入用的 `GET /api/laws`（§1.3）**指向同一個母庫**——差別只在**誰觸發**。後端各做一次，兩個入口共用，不要做兩套。

#### 長工作（解析卷證／生成草稿）在 chat 內部怎麼跑

這兩支背後是六節點 pipeline。它們**不另外開端點**，由 chat 的工具層在同一回合內觸發，
並把 pipeline 真實的節點事件轉發成 `tool_step`：

```
extract_case_document  → run_case(to_node="n3")                    約 10–11 秒
generate_decision_draft → run_case(from_node="n4", base_run_id=…)   約 24–72 秒
                          ↓ on_event 回呼
                    轉發成 tool_step（node / elapsed_ms / degraded 都是真值）
```

**後端要補三小塊才成立**（缺任一項就不要照這節做）：

1. **`run_case()` 加 `to_node` 參數。** 迴圈是 `for node in NODE_ORDER[start_idx:]`（`graph.py:303`），
   加 `end_idx` 即可；`STATE_AFTER`（`graph.py:56-63`）已有 node → state 的完整映射，
   停在 n3 自然落在 `SCREENED`，狀態機本來就模型化了部分完成。
   **紅線：`to_node` 不得為 `n5`**——那會留下一份沒過 N6 守門的草稿。n1–n4 安全
   （沒有草稿就沒有「草稿沒重驗引用」的問題，`graph.py:200-202` 那條不變量的目的仍然成立）。
   另外 `backend/api/chat.py:104` 的 `_load_case_payload` 只看 BUS 狀態不看 `final_state`，
   要一併補上「`SCREENED` 的 run 沒有草稿」的判斷。
2. **pipeline 工具跑完後重置 RefBook 的卷內編號。** N4 每次從 `L1`/`C1` 重編
   （`n4_retrieval.py:214-217,258-262`），而 `add_case_refs` 對既有 id 直接 `continue`
   （`llm/chat.py:188-189`）→ 不重置的話，模型引新草稿的 `[L1]`，`refs[]` 卻帶出舊 run 的法條，
   **誠實層被靜默打穿**。重置時機在「工具回傳之後、模型組答案之前」，順序上安全。
   同時要更新 `case_payload`，否則 `read_case` 看不到新 run。
3. **`gen()` 改真串流。** `backend/api/chat.py:243-266` 現在把事件 append 進 `pending`，
   `_run_turn` 跑完才一次 yield（實跑：t=0 emit 的事件 t=2.02s 才抵達）。
   不改的話 `tool_step` 與 `token` 都不會逐步出現，使用者會看到 10–72 秒全黑再一次跳出。
   改的時候要顧到 `?stream=0` 與 SSE 共用同一條 `_run_turn` 的既有契約。

> **層級禁令怎麼不踩**：`docs/spec/2026-09-12-chat-honesty-lamps.md` §4.0 禁止
> `backend/llm/chat.py` import `backend.orchestrator.*`（乙案 AgentCore 容器裡沒有 runstore）。
> 做法是**注入 callable**，比照現有的 `retriever`／`snapshot`／`emit`——
> `ChatTools.__init__` 收一個 `run_pipeline`，由 HTTP 層（**允許** import orchestrator）綁 `run_case`。
> 乙案下這兩支工具會缺席，要回「此檔位不可用」，不要靜默失敗。

> **歸檔**：agent 自動查完 → 結果透過 `tool_result` 回來，並自動寫進本案清單
> （`/cases/{id}/laws`、`/references`）。使用者手動 → 走 §1.3／§1.4 的 REST 端點自己挑進來。

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
| 23 | GET | `/api/cases/{id}/artifacts/{artifactId}/export?format=pdf\|docx` | 匯出下載（`pdf`／`doc` chip） | 一次性（回檔案） |

> **⑧ 匯出維持 `pdf`／`docx`**（2026-09-12 23:50 Ci 拍板）。理由：承辦人拿到的必須是能直接
> 送簽或續行編修的格式，`.md` 交不出去。
>
> **實作上有兩個真的會踩到的點，先寫在這裡：**
> 1. **中文字型要進容器。** 沒有 CJK 字型的話 PDF 會整片豆腐字（□□□），而且**不會報錯**。
>    只塞一個字型檔（例如思源黑體 Regular 單一 weight，約 8–16MB），不要整包字型家族。
>    2026-09-12 才剛把 build context 從 905MB 壓到 4.8MB（commit `279169b`），
>    **加完要重量一次 build context**，別讓它又漲回去。
> 2. **`.docx` 要能續編，不是把 PDF 改副檔名。** 用 `python-docx` 直接組段落，
>    不要走「HTML 轉檔」那條——轉出來的 docx 樣式結構很難再編輯。
>
> 建議 `docx` 先做（做起來比 PDF 單純、而且「可續編」才是承辦人真正要的），
> `pdf` 若時間不夠可暫時用「瀏覽器列印」頂著，但**契約不退**。
> 前端 `exportDownload()`（`App.vue:169-171`）現在只是 toast，要改成真下載。

### 1.6 辦案對話（唯一串流）

| # | 方法 | 路徑 | UI 動作 | 類型 |
|---|---|---|---|---|
| 24 | **POST** | **`/api/cases/{id}/chat`** | **中欄送出訊息／點 chip／`/` 工具 `send`＋`runTool`** | **SSE 串流** |

**輪詢：0 支。** 沒有「發起→拿號碼牌→反覆問好了沒」；慢工作（解析卷證、生成草稿）
走 #24 串流的 `tool_step` 事件逐步吐（§0.1、§2.3）。
**串流：1 支**（#24 chat）。
**一次性：其餘全部（含母庫查與各層 CRUD）。**

> **`POST /api/cases/{id}/runs` 與 `GET /api/runs/{run_id}/events` 仍然存在**（既有端點，後端內部用、
> 也留給除錯與 `verify.sh`），但**不在前端契約裡**——前端不打它們。長工作的進度走 chat 的 `tool_step`。

> **實測耗時**（真 bedrock，n=3，全為 `synthetic-*`）：解析卷證（n1–n3）約 **10–11 秒**；
> 生成草稿（n4–n6）約 **24／44／72 秒**（n5 跨度 3.3 倍，**上界用 72 秒抓**）；純問答回合 5–15 秒。
> ALB `idle_timeout` 已設 900 秒，不會被切斷。**忙碌鎖要撐得住 72 秒，而且要有進度**——
> 這正是 `tool_step` 存在的理由，不要只轉圈。

## 2. 核心：`POST /api/cases/{id}/chat`（唯一串流端點）

前端每一次「送出訊息、點建議 chip、按 `/` 工具」最終都呼叫這支（`store/app.js:runTool` / `send`）。
**七支工具全部走這裡**，包含解析卷證與生成草稿（它們在 chat 內部觸發 pipeline，見 §0.1）。後端的 agent 讀訊息、自己決定要不要呼叫工具、把過程與結果串流回來。

### 2.1 Request

```jsonc
{
  "run_id": "run-…",            // **選填**（③ 修訂）。有的話 read_case 讀它；
                                //   沒有的話 agent 只能先跑 extract_case_document
  "message": "這個爭點有沒有類似的前例？", // 必填，非空字串
  "session_id": "sess-…",       // 選填；省略＝開新對話，後端在 ack 就回一個新的
  "tool_hint": "extract_case_document",
                                // 選填；點 chip／`/` 選單時帶。值域＝§3.0 的七支工具名
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

> **④ `tool_hint` 的值域改了**，原本是 `extract|cases|laws|graph|draft|refine|export`（短代號）。
> 現在一律用後端的長名，值域＝§3.0 那張表的**七支**。`export_pdf`／`export_docx` 不在內
> ——匯出是下載（§1.5 #23），不是 agent 工具。
>
> **`run_id` 從必填改成選填**（③ 修訂）。既有契約是「payload 由呼叫端提供」
> （`docs/spec/2026-09-12-chat-honesty-lamps.md` §4.0），HTTP 層做 `load_run(run_id)` → `build_payload()`
> → 切分區餵給 agent。這條**不變**；改的是「還沒有 run 的時候也要能開口」——
> 新案子上傳完卷證，第一句話一定是「解析卷證」，那時候還沒有任何 run。
> - **有 `run_id`** → 照舊載入 payload，七支工具全可用。
> - **沒有 `run_id`** → `case_payload` 為空，`read_case` 回「卷內是空的」，
>   agent 只能呼叫 `extract_case_document`（它自己會產生第一個 run）。
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

### 2.3 SSE 事件（七種；②③ 修訂）

wire 格式：`event: <名稱>\ndata: <一行 JSON>\n\n`。共通欄位：`seq`（本回合 0 起遞增）、`turn_id`。

| 事件 | payload | 前端拿去畫什麼 |
|---|---|---|
| `ack` | `{seq,turn_id,text,`**`session_id`**`}` | agent 的一句口白。對應 `store` 的 `ACKS` |
| `tool_call` | `{seq,turn_id,`**`call_id`**`,tool,label,args}` | 開一個**工具區塊**卡（`Chat.vue` 的 `kind:'tool'`）。`tool` ∈ §3.0 七種 |
| `tool_step` | `{seq,turn_id,`**`call_id`**`,step,label,status,elapsed_ms,degraded}` | 工具卡裡逐行打勾的步驟（`toolBlock` 的 `steps[]`）。可多筆 |
| `tool_result` | `{seq,turn_id,`**`call_id`**`,tool,`**`status`**`,note,hits[],…}` | 工具卡的結果（§3 各工具形狀）＋歸檔到右欄卷宗 |
| `token` | `{seq,turn_id,text}` | agent 的文字回覆，逐字 append |
| `done` | 見 §2.4 | 一回合結束，唯一終止事件 |
| `error` | `{seq,turn_id,stage,error}` | 失敗，發完關流，**沒有 `done`**。`stage`∈`tool\|model\|transport\|internal` |

序保證：`done`／`error` 互斥且為最後一個；`tool_call`／`tool_result` 成對（用 `call_id` 配對）；
`tool_step` 夾在兩者之間、可零筆；`token` 可零筆；**沒有回放，斷線即斷**。斷線處理見 §5。

#### ② 三處新增

**`call_id`（新增，必要）**　同一回合內同一支工具可能被呼叫**兩次以上**（agent 讀完爭點常會再查一次法規），
`tool` + `seq` 配不起來。`call_id` 是本回合內遞增的配對鍵，`tool_call`／`tool_step`／`tool_result` 帶同一個值。

**`tool_result.status`（新增，必要）**　值域 `ok | empty | failed`。

| status | 意思 | 前端該說什麼 |
|---|---|---|
| `ok` | 正常完成 | 顯示結果 |
| `empty` | **查無**，`hits[]` 為空 | 「這個條件下沒有找到」 |
| `failed` | **工具本身失敗**（KB 打不到、快照載不動、pipeline 炸了） | 「查詢來源失敗，可重試」 |

> 為什麼一定要分：檢索失敗在 `ChatTools._search` 內部就被攔下轉成文字回給模型（`backend/llm/chat.py:420-427`），
> **不會冒到 `error` 事件**。沒有 `status`，前端看到的 `empty` 與 `failed` 長得一模一樣，會把「查詢失敗」
> 畫成「資料庫裡沒有這筆資料」。後端那段的註解自己就是為了防這件事寫的。

**`ack.session_id`（提前）**　原本只在 `done` 送。但 §5 要求前端處理「`token` 收到一半斷了」——
斷在中途就永遠拿不到 `session_id`，下一輪只能開新 session，前面講過的話全丟。

#### ③ `tool_step`：保留，但來源換成真的

初版契約有這個事件，前端 mock 也有 `steps[]`——**但那些 step 是 `await sleep(900)` 寫死的**
（`frontend/src/store/app.js:222-232`），步驟名也是手打的。現在改成**轉發 pipeline 的真實節點事件**。

```jsonc
// event: tool_step（只有 extract_case_document / generate_decision_draft 會發）
{ "seq": 2, "turn_id": "turn-…", "call_id": "tc-1",
  "step": "n1",                    // 節點代號，來自 run_case 的 on_event
  "label": "讀卷抽取",              // 後端補的中文，前端不必自己維護對照表
  "status": "done",                // running | done | failed
  "elapsed_ms": 11021,             // status=done 才有；真值
  "degraded": false }              // true → 該行標黃（節點降級跑完）
```

- **`label` 由後端帶**（`n1` 讀卷抽取／`n2` 案件分類／`n3` 程序審查／`n4` 檢索法條與相似案／
  `n5` 草稿撰寫／`n6` 引用守門）。前端照顯示即可。
- **其餘五支工具不發 `tool_step`**（單次檢索呼叫，內部沒有階段可報，硬生就是編）。
  那五支的工具卡用單純的 spinner。
- `elapsed_ms`、`degraded` 都是 `run_case()` 實際回報的值，**一行都沒有編**。

> **前提：`gen()` 必須先改成真串流**（§0.1 第 3 點）。不改的話這些事件會在整回合跑完之後
> 一次湧出，使用者看到的是 10–72 秒全黑再一次跳出——那比沒有 `tool_step` 更糟，
> 因為畫面會假裝剛才有過程。

### 2.4 `done`（① 修訂：本期前端只讀三個欄位）

**前端本期只需要這三個：**

```jsonc
// event: done
{ "session_id": "sess-…",      // 下一輪帶回來
  "answer": "依卷內資料，…",     // 完整回答，可用來校正 token 串接
  "elapsed_ms": 4213 }
```

`done` 實際上還會帶分層誠實燈號的一整組欄位（`lamp`／`tier`／`origin`／`why`／`refs[]`／
`dropped_refs[]`／`redirect`／`refine_used`／`memory`／`session_truncated`／`model_id`）。
**完整定義在 `docs/spec/2026-09-12-chat-honesty-lamps.md` §4.4，這裡不複製**——
兩份文件各寫一次，遲早會有一份走歪。

**本期前端不做燈號 UI**（2026-09-12 Ci 拍板）。後端照送、欄位不動：它們已實作、
已由 AC5–AC12 實跑驗過，為了少送幾個 JSON 欄位去改 `classify_answer` 與 `done` 的組裝，
風險遠大於收益。之後要補燈號隨時能接。

**但下面兩項要做。它們不是燈號 UI 的一部分。**

#### 2.4.1 兩項例外：這不是「畫得好不好看」，是「會不會講假話」

**一、`redirect` 非 null → 不得顯示 agent 講的任何天數。**

使用者問期限／天數／金額時，機械規則會強制紅燈並帶上 `redirect`（規則對**問題**做字面比對，
寧可誤判也不漏判）。**前端完全不處理的話，LLM 算的天數會直接出現在畫面上**——
期間算錯在訴願案有實質後果，這是 `CONSTITUTION` §4 的紅線。

```jsonc
"redirect": {
  "endpoint": "/api/deadline",
  "reason": "期間計算由規則引擎負責，同輸入必同輸出、可逐步覆核；聊天不計算期限。",
  "cta": "查看程序審查的算式"
}
```

最小做法：`redirect` 非 null 時，**不顯示 `answer`**，改顯示 `reason` 加一顆 CTA，
按下去捲到程序審查卡（那裡有規則引擎算好的期限與算式）。

> ⚠️ **「那顆鈕真的捲得到」沒有後端驗收涵蓋。** AC7 只驗 `redirect.endpoint` 的值。
> **這條要前端補一條驗收**（例：點 CTA 後程序審查卡進入視窗且被 highlight）。沒人補就是沒人驗。

**二、`dropped_refs` 非空 → 該則回答要標「引用有問題」。**

代表模型引了一個**不存在於白名單**的字號。不顯示就等於讓編造的引用直接送到承辦人眼前，
踩 `CONSTITUTION` §2（引用必可驗）。最小做法：該則下方一行灰字
「此則含無法對應的引用，請勿直接採用」。

> 這兩項合計大約十行 Vue。**不用做三層燈、不用做 `refs[]` 展開清單、不用碰 `lamp`／`tier`／`origin`。**
> 只讀 `redirect` 與 `dropped_refs` 兩個欄位。

## 3. 七支 chat 工具（`tool_result` 的形狀；④⑤⑨ 修訂）

> **初版寫「七種工具」但名字與形狀都對不上後端。** 現在：值域固定成下表七支、一律用後端長名、
> `tool_result` 的共通欄位統一。**其中五支後端已經實作，兩支（解析卷證／生成草稿）是包既有 pipeline，
> 一支（關聯圖）是新功能。**

### 3.0 工具值域（`tool` 欄位；前端照這張表寫死 UI 文案）

| `tool` | `label`（後端會帶） | 後端狀態 | 發 `tool_step` | 歸檔到 |
|---|---|---|---|---|
| `extract_case_document` | 解析卷證檔案 | **包 pipeline**（`run_case(to_node="n3")`） | ✅ n1–n3 | 不歸檔，更新 `latest_run_id` |
| `search_regulations` | 查法條 | 已實作 | — | `laws` 群組 |
| `search_similar_decisions` | 查相似訴願決定 | 已實作 | — | `cases` 群組 |
| `retrieve_refs` | 查判解與函釋 | 已實作 | — | 不歸檔 |
| `generate_decision_draft` | 生成草稿 | **包 pipeline**（`run_case(from_node="n4")`） | ✅ n4–n6 | `out` 群組 |
| `refine_text` | 潤稿 | 已實作 | — | 不歸檔 |
| `build_relation_graph` | 產生案件關聯圖 | **新功能**（§3.7） | — | `out` 群組 |
| `read_case` | 讀卷內 | 已實作 | — | 不歸檔 |

> 八列，因為 `read_case` 是 agent 內部的追問用工具，使用者不會主動點它——
> **`tool_hint` 的值域是前七支**，`read_case` 由 agent 自己決定要不要用。
>
> `export_pdf`／`export_docx` **不在這張表**：匯出是下載（§1.5 #23），不是 agent 工具。

### 3.1 `tool_result` 的共通形狀

```jsonc
// event: tool_result
{
  "seq": 9, "turn_id": "turn-…", "call_id": "tc-1",
  "tool": "search_similar_decisions",
  "status": "ok",                        // ok | empty | failed（§2.3）
  "note": "",                            // status 非 ok 時這裡有話
  "hits": [ … ],                         // 檢索類工具才有，見 §3.2
  "run_id": null,                        // pipeline 類工具才有，見 §3.3／§3.5
  "graph": null                          // 關聯圖工具才有，見 §3.7
}
```

三類工具各自只填自己那一塊，其餘為 `null`／`[]`。

### 3.2 檢索類（`search_regulations` / `search_similar_decisions` / `retrieve_refs`）

```jsonc
"hits": [
  { "id": "c1",                        // RefBook 配的本回合編號，前端就顯示這個
    "t": "新北市政府 112 年訴字第 123 號",
    "src": "歷史訴願決定書/113年/…",
    "score": 0.79,
    "verified": false,
    "note": "KB 命中，未對資料集實檔驗證",
    "provenance": "official",          // official | public_crawl | unknown | **null**
    "origin": "retrieval" }
]
```

- `hits` 可以是空陣列。**空陣列不是錯誤**，用 `status` 區分 `empty` 還是 `failed`。
- **`provenance` 可能是 `null`**——它只存在於 KB hit 的 metadata，法條查表的 Hit 沒有這個鍵
  （`backend/retrieval/base.py:30-40`）。**前端要處理 `null`。**
- **`score` 顯示為相似度時必標「向量相似度，非法律相似度」**（誠實紅線，§6）。
- **`search_regulations` 的 `verified` 不是恆 true。** `LawTableRetriever` 造四種 Hit，只有
  「條號存在於快照」那種是 `true`；條號無法無歧義解析、法規不在快照涵蓋範圍、條號查無，
  **三種都是 `false`**。後端原樣帶出，前端**不得**把 `verified:false` 畫成「字號已驗」。
- 模型在回答裡用 `[c1]` 引用。引了白名單以外的編號 → `done.dropped_refs[]`，該回合強制紅燈。

> **初版 §3.2／§3.3 的 `no`／`type`／`verdict`／`law`／`full`／`body` 拿不到**——那些是設計稿假資料，
> chat 的 hit 沒有這些鍵。要案號、案型、結果、全文，走 §4.2／§4.3 的母庫端點（那裡有側檔 metadata
> 與 S3 全文）。

### 3.3 `extract_case_document`（解析卷證檔案）

觸發 `run_case(to_node="n3")`，跑 n1–n3（**約 10–11 秒**），逐節點發 `tool_step`。

```jsonc
"run_id": "run-…",        // 新產生的 run，前端要記下來當之後的 run_id
"status": "ok",
"state": "SCREENED"       // 停在 n3，**這個 run 沒有草稿**
```

跑完之後前端打 **#4 彙整版**取內容：案由（`intake`）、事實摘錄（`facts_excerpt`）、
爭點（`issues`）、程序審查（`screen`）。

> **`procedure_checks` 的 `lamp` 值域是 `g`／`y`／`r`**（`backend/gate/lamps.py`），
> 不是設計稿那組 `ok`／`warn`／`alert`——那是 CSS class 名。前端自己映射
> （`api/adapt.js:21` 已經有 `{g:'ok', y:'warn', r:'bad'}`），不要叫後端改。
>
> **「分類信心 0.96」砍掉**（§6）：設計稿寫死的假數字，後端沒有可信的信心分數
> （`conf` 沒鑑別力是已知問題 S-17，未修）。

### 3.4 `refine_text`（潤稿）

`hits: []`，改寫後的文字走 `token`。**本回合強制紅燈**（`classify_answer` 規則 2，
即使同回合也檢索到 refs）。

> 初版 §3.5 設計的 `diff[]`（`{op:"del"|"add"|"keep"}`）與 `notes[]` **後端沒有**。
> 要做前後對照卡的話 diff 在前端算（拿原文跟新文字比）。**前端工作，不是後端契約。**

### 3.5 `generate_decision_draft`（生成草稿）

觸發 `run_case(from_node="n4", base_run_id=<上一次的 run>)`，跑 n4–n6（**約 24–72 秒**），
逐節點發 `tool_step`。N6 守門一定會跑，所以產出的草稿**一定經過引用四態檢查**。

```jsonc
"run_id": "run-…",
"status": "ok",
"state": "VERIFIED",
"artifact_id": "art-…",
"cite_count": 14          // **真值，從 payload 數**，不要沿用設計稿寫死的「14 處」
```

全文結構走 `GET /api/cases/{id}/artifacts/{artifactId}`（§4.4 的 `sections[]`）。

#### 3.5.1 前置條件（三個都要成立才生）

| # | 條件 | 現在誰在擋 | 不成立時 |
|---|---|---|---|
| 1 | **有卷證檔案** | 前端（`store/app.js` `IMPL.extract`） | 沒有卷證就沒有案子，`extract_case_document` 本身就跑不動 |
| 2 | **已解析卷證**（有一次 `state >= SCREENED` 的 run） | 後端要補 | 回 `status:"failed"` + `note:"還沒有解析過卷證"`。**不要自己先跑 n1**——那會讓使用者以為草稿是憑空生出來的 |
| 3 | **有相關法規與相關案例** | 前端（`store/app.js:308-312`）；**後端目前沒有擋** | 見下方 ⚠️ |

**⚠️ 第 3 條後端現在不擋，而且失敗的樣子很像成功。**

`n5_draft.py:138-142`：上游檢索全空時，草稿**照樣生得出來**，但白名單是空集合，
模型引的每一個編號都會被 client 清掉並標 `unsupported`，接著 N6 判紅。
也就是說會產出一份**通篇沒有依據的草稿**——後端把原因寫進 log 了，
但**畫面上看不出原因出在上游**。

所以第 3 條要兩邊都擋：前端 disable 按鈕（已經有了），後端在工具層回
`status:"failed"` + `note:"還沒有查過法規與相似案例"`，**不要生一份全紅的草稿**。

#### 3.5.2 手動加進右欄的法規，要當成查詢詞餵回檢索（Ci 拍板 (b)）

**問題**：前端擋門文案寫「草稿**只認右側卷宗裡的東西**當來源」（`store/app.js:310`），
但 N5 取的是 `state.retrieval`——**N4 自己檢索的結果**（`n5_draft.py:121-122`），
**不是** manifest 的本案清單。使用者手動挑的法規，N5 一條都看不到。
承辦人挑了五條、按生成草稿、一條都沒引，會以為系統壞了。

**拍板：走 `overrides.n4_query`**（2026-09-12 Ci）。

```python
# generate_decision_draft 工具內部
terms = "；".join(l["t"] for l in manifest["laws"])   # 例：「廢棄物清理法第2條；訴願法第14條」
run_case(case_id, from_node="n4", base_run_id=…,
         overrides={"n4_query": terms} if terms else {})
```

`graph.py:489-496` 收到之後會送進**兩條通道**：`cited_laws=[q]`（通道 A 法規查表）
與 `extra_case_terms=[q]`（通道 B 相似案）。**`n4_query` 是單一字串不是陣列**，
多條法規要自己 join。`RunIn` 是 `extra="forbid"`，`overrides` 白名單只有 `n4_query`，不要加別的鍵。

**為什麼這樣不違反 2026-09-05 的拍板。** 那次改成 N4 獨立檢索，理由寫在 `graph.py:488`：
舊版把「草稿即將引用的法條」餵給 N4，讓「引用一定查得到」變成必然——**檢索佐證的是自己**。
而 `n4_query` 是**查詢詞不是答案**：N4 拿它去查，**查得到才會進 `laws[]`**，查不到就是查不到。
`graph.py:489` 的註解本來就把這個口子定義成「承辦人明講的查詢詞——那是人指定的，
不是從草稿引用倒著填回去的」。**使用者手動挑的法規，正好就是「人指定的」。**

**這帶來一個要顯示的狀態**：使用者挑了一條法規，但 N4 查不到 → 它不會進草稿。
這不是 bug，是誠實——但**畫面要說得出來**，否則使用者一樣覺得系統吃掉了他的東西。
右欄該項標「檢索未命中，未進入草稿」。這一條與 §3.7 的 `flagged`／`unlinked` 是同一件事的兩面。

**相似案例（`references`）本期不餵回。** 法規有乾淨的文字形式（法名＋條號），
lawtable 解析得了；決定書沒有對等的查詢詞形式，硬塞會稀釋查詢句。
**所以右欄手動加入的案例本期仍然只是參考資料，UI 要標明**，不要沿用「只認右側卷宗」那句文案。
要不要改由之後另外拍板。

### 3.6 `read_case`（讀卷內）

`section` 值域：`intake` | `facts_excerpt` | `screen` | `laws` | `cases`。
`hits: []`，內容走 `token`。給錯的 section 回一句說明。
**沒有 `run_id` 時回「卷內是空的」**，不報錯。

### 3.7 `build_relation_graph`（案件關聯圖）— 新功能 ⑨

> **計畫見 `plans/2026-09-12-relation-graph.md`**（含驗收條件）。這一節只定義回傳形狀。
>
> 原本列在「建議砍」，理由是「後端不產關聯資料」。**重查之後那是錯的**：
> 邊在 run payload 裡已經是現成的——`citations[]` 帶 `sentence_id` + `resolved_id` + `state`，
> `facts_excerpt[]` 帶 `quote_ref`，`fact_issues[]` 帶 `matched_keywords`。
> **全部零 LLM 可組。**

```jsonc
"graph": {
  "run_id": "run-…",
  "generated": "2026-09-12T23:40:00+08:00",
  "cols": ["卷證", "事實", "爭點", "法規依據", "結論"],
  "nodes": [
    { "id":"D1", "k":"doc",   "c":0, "t":"原處分裁處書.pdf", "s":"共 6 頁",
      "src":"卷證檔案", "origin":"record" },
    { "id":"F1", "k":"fact",  "c":1, "t":"露天燃燒稻稈經稽查查獲",
      "d":"訴願人於本市土城區農地露天燃燒…", "src":"原處分裁處書.pdf#p2", "origin":"record" },
    { "id":"I1", "k":"issue", "c":2, "t":"爭點1：廢棄物性質之認定",
      "d":"系爭堆置物是否屬廢棄物？", "severity":"high",
      "src":"AI 不得代為認定，請承辦人核對卷證", "origin":"rule" },
    { "id":"L3", "k":"law",   "c":3, "t":"廢棄物清理法 §2 I", "verify":"ok", "origin":"retrieval" },
    { "id":"S7", "k":"out",   "c":4, "t":"原處分撤銷，由原處分機關另為適法之處分",
      "d":"（主文段第 1 句）", "origin":"llm" }
  ],
  "edges": [
    { "from":"D1", "to":"F1", "rel":"quote",   "basis":"facts_excerpt[].quote_ref" },
    { "from":"F1", "to":"I1", "rel":"trigger", "basis":"fact_issues[].matched_keywords",
      "detail":["露天燃燒","稽查"] },
    { "from":"I1", "to":"S7", "rel":"address", "basis":"doc[].ss[].refs 含 I1" },
    { "from":"S7", "to":"L3", "rel":"cite",    "basis":"citations[].sentence_id→resolved_id",
      "state":"ok", "lamp":"g" }
  ],
  "unlinked": { "laws":["L5"], "issues":[], "note":"L5 沒有被任何句子引用" },
  "stats": { "nodes":24, "edges":31, "edges_flagged":2 }
}
```

**四種邊，每一種都有程式依據**（`basis` 欄位就是寫給人核對的）：

| `rel` | 從 → 到 | 依據 | 檔案 |
|---|---|---|---|
| `quote` | 卷證 → 事實 | `facts_excerpt[].quote_ref`（`檔名#頁`） | N1 產出 |
| `trigger` | 事實 → 爭點 | `fact_issues[].matched_keywords` 出現在該段事實文本（字串包含，與 N3 同一把尺） | `n3_procedure.py:634-645` |
| `address` | 爭點 → 結論句 | `doc[].ss[].refs` 含該 `I*`（N6 掛的） | N6 |
| `cite` | 結論句 → 法規／案例 | `citations[].sentence_id` → `resolved_id`（**字串相等比對**，`graph.py:605-611`） | N6 |

**紅線：沒有「矛盾」這種邊。** 設計稿的三種線型裡有「爭執／矛盾」，**後端沒有任何偵測矛盾的機制**，
畫了就是編。第三種線型改用**真的有的東西**：`cite` 邊帶 `state`（`ok`／`amended`／`out_of_scope`／
`missing`／`unparseable`）與 `lamp`，非 `ok` 的畫成虛線或警示色——那才是承辦人真正想看到的
「這條引用有疑慮」。

**`unlinked` 也要畫。** 檢索到但沒有任何句子引用的法規，是**真實且有意義的資訊**
（「查到了但沒用上」）。不要靜默丟掉。

**前置條件**：要有一次 `state == "VERIFIED"` 的 run（需要 `doc[]` 與 `citations[]`）。
只有 `SCREENED` 的話回 `status:"empty"` + `note:"要先生成草稿才畫得出完整關聯"`，
或退化成只畫前三欄（卷證／事實／爭點）——**哪一種由 `plans/` 定案**。

---

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
- `GET /api/cases/{id}/artifacts/{artifactId}/export?format=pdf|docx` → 回檔案（`Content-Disposition: attachment`）
  - `docx`：`python-docx` 直接組段落（要能續編）。`pdf`：需 CJK 字型進容器，否則整片豆腐字且不報錯。
  - **引註要跟著出去**：`sections[].blocks[].cites` 轉成註腳或行內標註，不要只匯出白文。

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
  - ⚠️ **解析卷證 10–11 秒、生成草稿最久 72 秒**（§1.6 實測）。忙碌鎖要能撐這麼久，且要有進度（`tool_step` 事件，§2.3），不要只轉圈。
- **檔位誠實**：`/health` 回 `run_mode`；離線重播檔位時，chat 會回 503，UI 要說「聊天需要即時模型」，**不得**演一段假對話。

---

## 6. 設計稿要砍／要改的（沿用舊契約 §5 的結論，對應到新 UI）

| 砍／改 | 位置 | 理由 |
|---|---|---|
| ~~關聯圖 `build_relation_graph`~~ **改成保留，後端要做** | `RelationGraph.vue`、`graph.js GNODES/GEDGES` | **⑨ 這列反轉。** 原因寫在 §3.7：邊在 payload 裡已經是現成的，零 LLM 可組。`GNODES`/`GEDGES` 的假資料換成 `tool_result.graph`，元件保留。**注意：跟 `plans/kb-graph.md` 不是同一個東西**——那份是全庫 2477 份的三層知識圖、獨立展示頁、不接工作台 |
| `export_pdf`/`export_docx` 當成 chat 工具 | `data.js TOOLS[pdf,doc]`、`ToolOut out.type==='export'` | 匯出是**下載**（§1.5 #23），不是 agent 工具。chip 點下去打 export 端點拿檔案，不發 chat。**格式維持 `pdf`／`docx`** |
| **`steps[]` 的假耗時** | `store/app.js:222-232` 的 `await sleep(900)` | **②③ 新增此列。** 工具卡的逐行打勾**保留**，但 step 名稱與耗時改吃 `tool_step` 事件的真值（§2.3）。其餘五支工具不發 step，用單純 spinner |
| **分層誠實燈號 UI** | 全新，前端目前沒有 | **本期不做**（Ci 拍板）。後端照送欄位。**但 §2.4.1 的兩項要做**——那兩項是「系統會不會講假話」，不是燈號好不好看 |
| `/` 斜線指令選單 | `Composer.vue` slash 選單 | 工具由 agent 決定並發 `tool_call`；留著會讓人以為「點了就一定跑那支」。可保留為**輸入輔助**但別暗示保證。`tool_hint` 會加速命中，但後端仍可忽略或覆寫 |
| 多案件資料夾／拖曳 | `CaseTree.vue`、`store folders/moveCase` | **維持純前端**（§0.2，localStorage），不落地後端。此列為「保留但不接後端」，非砍 |
| 「分類信心 0.96」 | `ToolOut.vue extract meta` | 寫死假數字，砍。後端沒有可信的信心分數（`conf` 沒鑑別力是已知問題 S-17，未修） |
| 「相似度 94%」`sim` 寫死 | `data.js CASE_POOL.sim`、`ToolOut cases` | 真值來自 `hits[].score`，且**標「向量相似度非法律相似度」** |
| 「引註 14 處」寫死 | `store draft note`、`ToolOut draft` | 真值從 artifact 的 `cite_count` 數（§4.4） |
| **「草稿只認右側卷宗裡的東西當來源」** | `store/app.js:310` | **文案與後端不符**（§3.5.2）。法規改走 `overrides.n4_query` 餵回檢索後，正確說法是「你挑的法規會成為檢索的查詢詞，查得到才會進草稿」。**案例本期仍只是參考資料**，要標明 |
| 「已上傳，可直接改」等上傳文案 | 上傳 sheet 文案 | 目前**不做 OCR**，掃描影像回 `readable:false`＋標「無法辨讀」，不要寫成支援 |

**四條不能省**（誠實紅線）：
1. 法規綠燈只代表**字號對得回法規快照**，`relevance` 一律 `unknown`——不代表與本案相關。
2. 相似度是**向量相似度**，KB 命中未對資料集實檔驗證，畫面要標明。
3. `redirect` 非 null 時**不得顯示 agent 講的任何天數**；`dropped_refs` 非空要標「引用有問題」（§2.4.1）。
4. 關聯圖**沒有「矛盾」這種邊**（§3.7）。第三種線型用 `cite` 邊的 `state`，那是真的。

---

## 7. 一句話總結給後端

這版前端需要：

1. **一支串流端點** `POST /cases/{id}/chat`（SSE）——agent 在裡面呼叫**七支工具**：
   `extract_case_document`／`search_regulations`／`search_similar_decisions`／`retrieve_refs`／
   `generate_decision_draft`／`refine_text`／`build_relation_graph`（外加 agent 自用的 `read_case`）。
   事件 `ack`／`tool_call`／`tool_step`／`tool_result`／`token`／`done`／`error`。
   **前端不打 `/runs`、不打任何第二條串流。**
2. **RESTful 一次性端點**，母庫唯讀、本案子資源 C/R/D：
   - 系統／案件：`GET /health`、`GET|POST /cases`、`GET|PATCH|DELETE /cases/{id}`
   - 卷證：`GET|POST /cases/{id}/files`、`DELETE …/files/{fileId}`
   - 法規：`GET /laws?q=`、`GET /laws/{lawId}`（KB filter `doc_kind=statute` + S3 全文）
     ＋ `GET|POST /cases/{id}/laws`、`DELETE …/laws/{lawId}`
   - 案例：`GET /decisions?q=`、`GET /decisions/{decisionId}`（filter `doc_kind=decision`）
     ＋ `GET|POST /cases/{id}/references`、`DELETE …/references/{refId}`
   - 產出：`GET /cases/{id}/artifacts`、`GET|DELETE …/{artifactId}`、`GET …/{artifactId}/export?format=pdf|docx`
3. **持久化**：一案一份 `manifest.json`（§4.0），**不加資料庫**。

**沒有輪詢**；三類卷宗成員只有 C/R/D 沒有 U（不改母庫）；資料夾維持前端本地（§0.2）。

### 後端的工作分四塊

**已經存在、只需接線**：chat 的五支檢索／讀卷／潤稿工具、pipeline 的 `from_node`＋`base_run_id` 續跑、
KB 檢索與 `doc_kind` filter、`load_run`／`build_payload`。

| 塊 | 內容 | 規模 |
|---|---|---|
| **A. chat 串流層** | `gen()` 改真串流（emit 推 queue、邊跑邊 yield，顧到 `?stream=0` 共用 `_run_turn`）；事件加 `call_id`／`tool_result.status`／`ack.session_id` | 集中在 `backend/api/chat.py` 一個檔 |
| **B. pipeline 包成工具** | `run_case()` 加 `to_node`（**不得為 n5**）；注入 `run_pipeline` callable 進 `ChatTools`（不踩 §4.0 層級禁令）；`on_event` 轉發成 `tool_step`（補中文 `label`）；工具跑完重置 RefBook 卷內編號並更新 `case_payload`；`_load_case_payload` 補「`SCREENED` 沒有草稿」判斷 | 三個檔，改動都不大但**彼此有序**：先 A 再 B |
| **C. 持久化 + 13 支 CRUD** | `manifest.json` + `GET /cases/{id}` 彙整版 + files／laws／references／artifacts 的 GET/POST/DELETE | **不碰對話框，可與 A/B 並行開工** |
| **D. 關聯圖** | `build_relation_graph` 工具（§3.7）。零 LLM，從 run payload 組節點與四種邊 | 計畫見 `plans/2026-09-12-relation-graph.md` |

外加兩件：母庫查四支（#10／#11／#15／#16）；**#23 匯出 `pdf`／`docx`**——
這件不小，要處理 CJK 字型與容器體積，建議獨立成一個工作包，`docx` 先做。

**順序建議**：C 先發（無依賴）→ A（B 的前提）→ B → D。
