# 交接：模型與知識庫（給 Claire，2026-09-12）

> 範圍：聊天端點 `POST /api/cases/{case_id}/chat` 要新增時，**模型與知識庫這一側**的現況與待確認事項。
> 契約凍結於 `docs/spec/2026-09-12-chat-honesty-lamps.md`；後端步驟見 `plans/2026-09-12-chat-ask-agent.md`。
> **本檔不寫任何 model id 實際值、AWS 帳號 id、bucket 名、KB id**，只寫環境變數名稱與讀取位置。
> 行號依當下工作樹（`backend/api/app.py` 有其他 session 並行改動，定位時請用函式名）。

---

## 1. 模型現況

### 1.1 兩個環境變數，兩個用途

| 用途 | 環境變數 | 誰在用（唯一呼叫點） | 從哪讀 |
|---|---|---|---|
| 抽取 | `BEDROCK_MODEL_ID_EXTRACT` | N1 收文抽取 `backend/nodes/n1_extract.py:86` → `llm_client.extract_intake()` | `backend/config/settings.py:42-46` `bedrock_model_id("extract")` |
| 主筆 | `BEDROCK_MODEL_ID_DRAFT` | N5 草稿主筆 `backend/nodes/n5_draft.py:113` → `llm_client.draft_sentences()` | 同上，`bedrock_model_id("draft")` |

驗法：`grep -rn "extract_intake\|draft_sentences" backend`（排除 `backend/tests/`）在非測試碼只有這兩個呼叫點，
加上 `backend/llm/client.py` 自身的定義（`:203` extract、`:264` draft）。**沒有第三個節點打模型。**

呼叫鏈：`extract_intake`/`draft_sentences` → `_invoke_structured(..., model_kind=)`（`backend/llm/client.py:158-186`）
→ `_load_model(model_kind)`（`:125-155`）→ `BedrockModel(model_id=..., region_name=..., temperature=0.0, max_tokens=8000)`（`:155`）。

⚠️ **`_invoke_structured` 的 `model_kind` 預設值是 `"extract"`**（`backend/llm/client.py:159`）。
新增呼叫點忘了傳 `model_kind`，會**靜默**打抽取那顆模型，不會報錯（兩顆都在 IAM 放行清單內）。

**硬性要求（不是建議）：聊天層一律用關鍵字傳 `model_kind="draft"`。**

現有兩處是**位置參數**傳的，本身沒錯但很脆：
`client.py:216` → `_invoke_structured(system, user, "ExtractionResult", None, "extract", ...)`
`client.py:308` → `_invoke_structured(system, user, "DraftResult", tools, "draft")`
日後有人在中間插一個參數，**兩處會同時靜默錯位而不報錯**。用關鍵字傳就免疫。

（釐清：這是對**新程式碼**的要求，不是現有 bug——現有兩處都有傳。）

其他相關變數：`MODEL_PROVIDER`（`settings.py:37-39`，預設 `bedrock`；`openai` 只供開發期調 prompt，
其輸出**不得**當驗收證據——`.env.example:3`、`backend/DEPLOY.md:126`）、`AWS_REGION`（`settings.py:58-59`）。
`model_ids()`（`client.py:98-113`）刻意回報「真的被呼叫的那顆」，provider 是 openai 時不報 `BEDROCK_MODEL_ID_*`。

### 1.2 IAM 只放行這兩顆，換第三顆模型必須改 CDK

`infra/cdk/lib/appeal-backend-stack.ts:95-106`：`invokeResources` 只由
`invokeArnsFor(props.modelIdExtract)` ＋ `invokeArnsFor(props.modelIdDraft)` 組成，
policy sid 就叫 `InvokeNamedModelsOnly`。**任何第三顆模型 → `AccessDenied`。**

要加第三顆模型，這四處都要改（少一處就壞）：

| 檔案:行號 | 要改什麼 |
|---|---|
| `infra/cdk/bin/app.ts:43-44` | `required('BEDROCK_MODEL_ID_...')` 加一行；`required()` 缺值會擋住 deploy |
| `infra/cdk/lib/appeal-backend-stack.ts:11-19` | `AppealBackendStackProps` 加一個 `readonly modelIdXxx: string` |
| `infra/cdk/lib/appeal-backend-stack.ts:95-98` | `invokeResources` 加一組 `...invokeArnsFor(props.modelIdXxx, region, account)` |
| `infra/cdk/lib/appeal-backend-stack.ts:197-207` | 容器 `environment` 加一列，否則容器讀不到 |

另注意 `invokeArnsFor()`（`:46-60`）：跨區 inference profile（`us.` / `eu.` / `apac.` / `global.` 前綴）
**要兩種 ARN**（profile 本身 ＋ 不綁 region 的 foundation model），少給第二種就 `AccessDenied`。

---

## 2. 聊天要用哪顆模型（已定案：沿用主筆那顆）

**定案**：聊天沿用 `BEDROCK_MODEL_ID_DRAFT`，`_load_model("draft")`（`plans/2026-09-12-chat-ask-agent.md:22`），
回應欄位 `model_id` 沿用 `run_meta.model_ids.draft`（spec `:265`）。**IAM 零改動**，第 1.2 節那四處都不用碰。
`refine_text` 工具是「單獨一次模型呼叫」（spec `:161`），走的也是同一顆。

### 2.1 請 Claire 確認的三個參數

| 參數 | 目前預設值 | 位置 | 說明 |
|---|---|---|---|
| 聊天 system prompt（法律語氣） | **檔案尚未存在** | 要新建 `backend/llm/prompts/chat_ask.md`（`plans:22`） | `backend/llm/prompts/` 目前只有 `n1_extract.md`、`n5_draft.md` 兩檔（實查 `ls`）。載入器是 `client.py:116-117` `_prompt(name)`，讀 `prompts/<name>.md` |
| 語氣參考範本 | 12 行硬性規則 | `backend/llm/prompts/n5_draft.md:1-12` | 現行主筆語氣：「新北市政府訴願審議委員會的決定書主筆」「只寫句子，不判斷句子可不可信」「不補充輸入沒有的事實」「繁體中文，法規與案號用原文」 |
| 檢索 `top_k` | **`5`（函式簽名預設）** | `backend/retrieval/kb.py:109` `search(..., top_k: int = 5)` | 六節點路徑目前都顯式傳 5：`n5_draft.py:90`（`retrieve_refs`）、`n4_retrieval.py:246`（相似案）。**spec §4.0（`:151-161`）沒有規定聊天工具用多少**——這是 Claire 要拍的第一個數字 |
| 分數門檻 | `0.25` | `settings.py:31` `DEFAULT_KB_MIN_SCORE`；讀取器 `settings.py:71-72` `kb_min_score()`（env `KB_MIN_SCORE`）；套用點 `kb.py:95` | 部署時由 CDK 硬帶（`bin/app.ts:46` `required('KB_MIN_SCORE')` → `appeal-backend-stack.ts:205`），**不是靠 settings.py 的預設** |

> ⚠️ 降門檻是**明文禁止**的降級手段：`plans:68`「**不用降低 `KB_MIN_SCORE` 來湊命中**」。
> 檢索 recall 不夠時的既定降級是「砍 `search_similar_decisions`，只留 `retrieve_refs` 與 `read_case`」。

`temperature=0.0`、`max_tokens=8000` 寫死在 `client.py:155`，目前不是可調參數。

---

## 3. 知識庫不換：聊天兩個檢索工具會用到的既有函式

同一個 `BEDROCK_KB_ID`（`settings.py:67-68`）、同一個 `KBRetriever`。IAM 上 `bedrock:Retrieve`
只放行**一個** KB ARN（`appeal-backend-stack.ts:108-116`，sid `RetrieveFromOneKnowledgeBaseOnly`），換 KB 要改 CDK。

| 聊天工具（spec §4.0） | 呼叫什麼 | 關鍵位置 |
|---|---|---|
| `search_similar_decisions` | `KBRetriever.search(query, top_k=?)`，**不傳 prefix** → 走配額查詢 | `kb.py:109-123` → `_quota_search()` `:125-146` |
| `retrieve_refs` | `KBRetriever.search(query, filters={"prefix": REF_PREFIXES}, top_k=?)` → 單次查詢＋來源去重 | `kb.py:109-123` → `_retrieve(..., dedupe_by_source=True)` `:148-210` |
| `search_regulations` | 法條查表，**不打 KB** | `backend/retrieval/lawtable.py:44-97`；`ARTICLE_TEXT_AVAILABLE = False`（`lawtable.py:15`，沒有條文原文） |

會影響檢索結果的既有常數（都在 `backend/retrieval/kb.py`，**改一個就影響六節點主線**）：

| 常數 | 值 | 行 | 是什麼 |
|---|---|---|---|
| `DEFAULT_PREFIXES` | 兩批訴願決定書前綴 | `:37` | 相似案通道收哪些前綴的唯一事實來源 |
| `SIMILAR_CASE_QUOTA` | official 2 ＋ public_crawl 3 | `:46` | 兩批分開查、各自取席次再合併；不足由另一批補位（`:137-144`） |
| `RETRIEVE_INTERVAL_S` | `1.1` | `:49` | 配額查詢兩批之間 `time.sleep()`（`:129-130`）。**與 `client.py` 的節流器是兩份獨立狀態** |
| `QUOTA_FETCH_DEPTH` | `50` | `:54` | 相似案每批抓多深。2026-09-12 實測：15 撈到 official 0 筆、50 撈到 2 筆 |
| `REF_FETCH_DEPTH` | `50` | `:65` | 判解／函釋通道抓多深。實測效益是「同一題 3 筆→6 筆」，**不是「從 0 變有」**；語料本身沒覆蓋的（例：寄存送達）調深也變不出來（`:58-64`） |
| `REF_PREFIXES` | `["行政函釋/", "司法院釋字及行政判解/"]` | **目前在 `backend/nodes/n5_draft.py:44`** | 依 plan S2b（`plans:20`）要搬到 `kb.py`，兩邊 import 同一份物件（AC13/AC14，`plans:50-51`）。搬完後要在 `kb.py` 找它 |

命中一律 `verified=False`（`kb.py:85`、`:200`）；`outcome` 照檔名／主文正則抓，不由模型推測（`kb.py:66`、`:202-204`）。
`search_regulations` 的 `verified` **不是恆 true**，後端要原樣帶出（spec `:163-165`）。

---

## 4. 待 Claire／Ci 確認：節流（1 RPS）

**問題**：賽方規範要求 Bedrock 壓在 1 RPS 以下（`settings.py:32`、`client.py:62` 都引 team-brain〈2026-09-12 決賽環境規範〉）。
既有節流器 `_throttle()`（`client.py:70-95`）的 docstring **白紙黑字寫著**（`client.py:76-80`）：

> 這個閘只管本模組每一次 `_invoke_structured` 送出的請求。Strands 的 agent loop
> 在**一次**呼叫內可能因工具往返而多次打模型，那些內部往返不經過這裡。

聊天正是 agent loop，一輪可能連打 3–5 次模型（spec `:403`）。

**已定案做法**：在**每個工具進入點各呼叫一次 `_throttle()`**（spec `:401`、`plans:24`、`plans:70`）。
理由：評審面前吃 429 比慢三秒難看得多，且 429 發生在 demo 中途無法當場除錯。

**三條誠實限制（不得讀成「已解決」）**：

1. **只保證單一路徑／單一進程內不超速。** `backend/retrieval/kb.py` 的 `RETRIEVE_INTERVAL_S`（`:49`）
   是另一個獨立的閘，兩者相加仍可能超過 1 RPS（`client.py:79-80`、spec `:403`）。
2. **之後若上 AgentCore（乙案）就失效。** 聊天會跑在另一個容器，與六節點的節流器是兩份獨立狀態，
   甲乙並存時全域仍可能超標。真要嚴格全域限速得把兩邊併進同一個節流器，本 change 不做（spec `:402`）。
3. **延遲 3–5 秒是估計值、未量測。** spec `:404` 與 `plans:70` 都明標「估計未量測」。
   附帶一提：光是 `search_similar_decisions` 一次呼叫，`_quota_search` 內部就已經 `sleep(1.1)` 一次（`kb.py:129-130`），
   這段**不在**上面那個估計的推導裡，實際延遲只會更長——**這也是估計，同樣未量測**。

**待 Ci 拍板**：賽制是否把聊天呼叫一起算進 1 RPS（spec `:405`、`plans:119` 都標「待查」）。
確認「不算」→ 可拿掉逐工具節流，一輪省下數秒。

另注意兩個會讓節流靜默失效的旋鈕：`MODEL_PROVIDER != bedrock` 時 `_throttle()` 直接 return（`client.py:84-85`）；
`client.MIN_INTERVAL_S = 0` 關閉節流，測試就是這樣用的（`client.py:63-65`）。**別讓這兩個狀態進交付路徑。**
