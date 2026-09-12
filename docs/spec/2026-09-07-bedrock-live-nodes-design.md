# 設計規格：六節點接 Bedrock、KB 檢索、可續跑執行（2026-09-07）

> 狀態：草稿，待 Claire 審閱。審過後進 `plans/` 寫可執行計畫。
> 分支：`mission/hack-bedrock-agents-20260906`（自 `mission/hack-integration-20260905` 切出）
> 參考：`/Users/claireliang/Desktop/aws_hackthon/AppealAssist/`（Strands + AgentCore POC，降為參考，不再演進）

## 0. 一句話

讓現有六節點流水線在 `RUN_MODE=bedrock` 下真的呼叫模型與 Knowledge Base，並讓畫面上每張幕僚卡可以「從該節點往下重新產生」；fixture 離線重播一行不動。

## 1. 背景

- `backend/` 六節點（N1 抽取、N2 分類、N3 程序審查、N4 檢索、N5 主筆、N6 守門）已可在 fixture 模式端到端執行，前端五步全由後端 payload 渲染（HANDOFF.md）。
- N1／N5 在非 fixture 模式下 `raise NotImplementedError`，API 回 501。`llm/client.py` 尚不存在，`boto3` 未進 `requirements.txt`。
- `backend/retrieval/` 只有 `lawtable.py`（法條查表），相似案通道是 `UnavailableRetriever`。
- 參考專案 AppealAssist 已用 Strands SDK 驗證：LLM 只做抽取與爭點對照、相似案與決定結果由程式供給時，43 個相似案案號零幻覺、零越界結論。其 `model/load.py`、`retrieve` 工具、PetitionFields schema 可直接對照移植。
- Knowledge Base 是 Bedrock 自身的服務（Managed Knowledge Base，2026-06 GA），以 `bedrock-agent-runtime.retrieve` 加 `managedSearchConfiguration` 查詢，**不需要 AgentCore**。

## 2. 範圍

### 做

| # | 工作包 | 一句話 |
|---|---|---|
| A | `backend/llm/` | N1、N5 的模型呼叫層；Strands `Agent` + `BedrockModel`，`MODEL_PROVIDER` 可切 |
| B | `backend/retrieval/kb.py` + `scripts/ingest_kb.py` | N4 通道 B 接 Managed KB；冪等入庫腳本，換帳號重跑即搬遷 |
| B2 | `backend/intake/` + `POST /api/cases` | 上傳訴願書／原處分書（PDF、txt）建案；卷證文字路由 pdftotext／視覺讀；N2、N3 改吃 N1 輸出 |
| C | 可續跑執行 | `CaseState` 持久化、`POST /runs` 收 `base_run_id` + `from_node`，從指定節點往下重跑到 N6 |
| D | SSE 節點事件 | bedrock 模式 `POST /runs` 回 202，`GET /runs/{id}/events` 逐節點推進；fixture 模式維持同步 |
| E | 資料 | 兩批來源分前綴上 S3、manifest 進 git、爬蟲 251 件逾期案變成期間引擎回放測試集 |
| F | 文件同步 | `docs/architecture.md` §4.1／§8／§13、`CLAUDE.md` 帳號那條、`backend/DEPLOY.md` |

工作包分**必要層**（A、B、B2、C、D 的 202＋輪詢、F）與**加值層**（D 的 SSE 與每卡按鈕、E）；plan 對應 Task 順序。

### 不做（列 backlog Phase S）

- 獨立 OCR 服務（Textract 不支援中文；Bedrock Data Automation 要建 project 與非同步 job，30 小時內是坑；Tesseract 多一個依賴且手寫中文效果差）。掃描件走 §5.8 的視覺讀取，抽不出就手動表單（architecture §3.5）。

- ask 追問 agent。`backlog.md`、`docs/spec/`、`docs/architecture.md` 沒有任何 story 或驗收條件要求對話。
- AgentCore Runtime 部署。唯一價值是承載 ask 那種有記憶的 agent；沒有 ask 就沒有理由。
- Strands multi-agent／Graph。流水線是確定性的，節點間無需協商（architecture §4.1 結論不變）。
- 爬蟲補洗錢防制法案件。現有爬蟲只有廢清法、空污法，補爬不在 30 小時預算內。
- 逐字串流。只做節點級事件。

## 3. 決策與推翻的既有拍板

| 決策 | 內容 | 影響的既有文件 |
|---|---|---|
| D1 | Strands **只在 N1／N5 內部**使用，編排層仍是自寫 state machine | architecture §13 #6 原「不用 Strands」→ 改為「限 N1/N5 內部；import 於模組頂層，strands 以 try/except ImportError 守衛」 |
| D2 | KB 用 **Managed Knowledge Base**，不自管 vector store、不控制 chunking | architecture §8 原「chunking=NONE、自附 metadata」→ 改；§13 新增待拍板「Managed KB recall 實測須 ≥ 60%」 |
| D3 | AgentCore 維持 **Stretch**，本規格不碰 | architecture §13 #5 立場不變；spec §4.6「僅抽取與草稿兩節點用 AgentCore 包」須改為「用 Strands 包」 |
| D4 | 開發期使用開發用 AWS 帳號（profile 名與帳號 ID 只在 `.env`／`~/.aws`，不進文件），賽方帳號到手後以 ingest 腳本重建 | `CLAUDE.md`「不借用其他專案的任何 secret」→ 改為「開發期可用開發用 AWS；GCP 仍不碰；賽方帳號到手即切換」。**Claire 拍板** |
| D5 | live 呼叫失敗**不自動退回 fixture**，節點拋錯、API 回 502 帶原因 | 延續 `NodeCtx.require_fixture` 精神，CONSTITUTION §1 |
| D6 | 重跑只能「從某節點往下全部重跑」，N6 永遠最後重跑；不提供單節點重跑 | 新增契約 |
| D8 | 所有 import 放模組頂層；豁免檔第三方套件以 `try/except ImportError` 守衛，缺套件於呼叫點 raise；`run_all.py` 以 ast 強制 | Claire 2026-09-07 拍板；影響 Task 2、3 已寫程式（需重構）與後續全部 |
| D7 | 上傳案 `upload-` 前綴存 `backend/output/uploads/`（gitignored），只能在 bedrock 模式跑；卷證文字三層路由（txt／pdftotext／PDF 視覺讀），不裝 OCR 套件 | `graph.load_case` 原本只放行 `synthetic-`；上傳目錄不進 git 故不違 CONSTITUTION §6 |

## 4. 呼叫形狀

```
前端 ──POST /api/cases/{id}/runs {from_node?, base_run_id?, confirmed_intake?, overrides?}──▶ FastAPI
                                                                                            │
      fixture：同步跑完，200 + 完整 payload（現況，不動）                                       │
      bedrock：202 + {run_id}；前端 GET /runs/{run_id}/events（SSE）                          ▼
                                                                       orchestrator.run_case(...)
                                                                       ├ 載入 base_run 的 CaseState（若有）
                                                                       ├ 從 from_node 起依序 dispatch 到 n6
                                                                       │   N1 ──▶ llm.client.extract_intake()   ── Bedrock Converse
                                                                       │   N2/N3 純程式
                                                                       │   N4 ──▶ retrieval.kb.search()          ── Bedrock KB Retrieve
                                                                       │   N5 ──▶ llm.client.draft_sentences()      ── Converse（工具：retrieve 限函釋/判解）
                                                                       │   N6 純程式
                                                                       └ 每節點 start/done 各發一個 SSE 事件；終態存檔
```

單一 API、單一 process、兩次模型呼叫。「七位幕僚」仍是敘事層（`settings.AGENTS_NARRATIVE`），工程上只有 N1、N5 呼叫模型。

## 5. 元件設計

### 5.1 `backend/llm/`

```
backend/llm/
  __init__.py
  client.py        唯一允許 import strands / boto3 的檔
  schemas.py       Pydantic：ExtractionResult、DraftResult
  prompts/
    n1_extract.md
    n5_draft.md
```

`client.py` 對外三個函式。**import 一律在模組頂層**（D8，Claire 2026-09-07 拍板，全 backend/ 適用含測試）；strands 與 pydantic schema 以 `try/except ImportError` 守衛為 `None`，缺套件時於呼叫點 raise `LLMError`。`run_all.py` 新增 `scan_top_level_imports()` 以 ast 禁止函式內 import：

```python
def load_model():
    """MODEL_PROVIDER=bedrock（預設）| openai。Bedrock 用 strands BedrockModel，
    model_id 與 region_name 一律由環境變數顯式帶入（避開 strands 預設 us-west-2）。"""

def extract_intake(document_text: str, *, pdf_documents: list[tuple[str, bytes]] | None = None, retries=3, backoff_s=1.5) -> dict:
    """N1 用。無工具的 Agent，structured_output_model=ExtractionResult；回 {intake, conf, quotes, facts_excerpt, usage, model_id}。失敗 raise LLMError，不吐假資料。"""

def draft_sentences(context: dict, slots: list[str], retrieve_fn=None, *, retries=3, backoff_s=1.5) -> dict:
    """N5 用。可掛 retrieve_refs 工具；回 {slots: {slot: [{t, cite_ids, basis, source_kind, unsupported?}]}, tool_calls, usage, model_id}。"""

# 測試接縫：_invoke_structured(system, user, schema_name, tools=None, model_kind, attachments=None) -> (dict, usage)
```

`MODEL_PROVIDER=openai` **僅供開發期調 prompt**（AppealAssist 在 Bedrock 未通時的做法），不得進交付路徑（architecture §1 第 5 條「不引入非 AWS 模型到交付路徑」）；demo 前必須切回 Bedrock 重跑 AC4–AC7。

`schemas.py`：

- `ExtractionResult`：對齊 architecture §3.1 N1 輸出（`no, type, person, org, d1, d2, d3, agent, note, service_method, transit_days, interested_party` 各帶 `value`、`conf`、`quote`；另有 `facts_excerpt: list[{text, page, quote_ref}]`）。參考 AppealAssist `PetitionFields`。
- `DraftResult`：`reasoning: list[DraftSentence]`、`conclusion: list[DraftSentence]`，`DraftSentence = {t, cite_ids, basis, source_kind}`（與合成案例 `draft_fixture` 同形，`build_doc_skeleton` 不改）。**不含** `lamp`、`verified`——那是 N6 的欄位，模型不得產出。

模型 id 環境變數：`BEDROCK_MODEL_ID_EXTRACT`、`BEDROCK_MODEL_ID_DRAFT`（預設值皆由 `.env.example` 說明，程式內無寫死）。`run_meta.model_ids` 在 bedrock 模式填實際值，fixture 模式維持 `None`。

### 5.2 N1 抽取 live 分支

`n1_extract.run()` 開頭由 `ctx.require_fixture()` 改為三向分流：

| `ctx.run_mode` | 行為 |
|---|---|
| `fixture` | 現有重播程式碼，不動 |
| `bedrock` | 讀 `prompts/n1_extract.md`，把 §5.7 路由後的卷證（文字，或 `pdf_visual` 的 PDF 附件）餵 `client.extract_intake()`；每欄 `origin="llm"`、`conf` 用模型值；`conf < CONF_THRESHOLD` 的欄位觸發現有 `degraded → NEEDS_INPUT` 路徑 |
| 其他 | 維持 raise `NotImplementedError` |

narrative 的降級文案由「離線重播」改為實際情況，不得再出現「fixture」字樣。

### 5.3 N5 主筆 live 分支

- `client.draft_sentences(context, slots, retrieve_fn)`，工具 `retrieve_refs`。
- prompt 內塞入 N4 結果（法條、相似案含決定結果）。**相似案與法條不給模型自己查**。
- 唯一工具 `retrieve_refs(query)`：呼叫 `retrieval.kb.search(query, filters={"prefix": ["行政函釋/", "司法院釋字及行政判解/"]})`，用途是替爭點對照補函釋或判解原文。`max_tool_calls=6`。
- `requires_human_conclusion=True` 時：prompt 明講不寫結論，且程式端再刪除 `slot=conclusion` 的句子（雙保險，現有 `dropped_conclusion` 邏輯沿用）。
- 每句 `cite_ids` 必須出現在「N4 結果 id ∪ 工具回傳 id」集合內，否則白名單外的 id 一律清除，該句標 `unsupported` 並帶 `dropped_cite_ids`。
- **N6 端要有對應的消費者（2026-09-07 本輪新增，取代原本「N6 邏輯不變」）**：`unsupported` 與 `dropped_cite_ids` 由 `narrative._sentence()` 帶進 `doc[]`（`build_doc_skeleton(carry_draft_cite_ids=True)`，bedrock 分支才開；fixture 維持 False 保 AC1），N6 對這種句子判 `l="r"`、`why` 用 `lamps.WHY_UNSUPPORTED_CITATION`，並匯總一條 `{"reason": "cite_id_unsupported", "sentence_ids": [...]}` 的 blocker（P0）→ `submit_allowed=False`。N6 仍不 import `backend.llm`，只讀 doc 上的旗標。
- 原本這一節同時寫「交 N6 判紅」與「N6 邏輯不變」，兩句互相矛盾（覆核 I-3），以上為裁定後的定稿。

### 5.4 N4 通道 B：`backend/retrieval/kb.py`

實作現有 `Retriever` protocol：

```python
class KBRetriever:
    name = "bedrock_kb"
    def search(self, query, filters=None, top_k=5) -> list[Hit]: ...
    def meta(self) -> dict: {"backend": "bedrock_kb", "available": True, "kb_snapshot_date": ...}
```

- boto3 `bedrock-agent-runtime.retrieve`，`retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": min(top_k*3, 50)}}`。多抓三倍再後過濾（Managed KB filter 不支援路徑比對，AppealAssist 實測）。**2026-09-12 修正**：原本寫 `managedSearchConfiguration`，S3 Vectors 後端只吃 `vectorSearchConfiguration`，前者會丟 `ValidationException`。
- 後過濾：`score < KB_MIN_SCORE` 丟；`_file_type == "PDF"` 丟；URI 前綴不在 `filters["prefix"]` 丟；`filters["exclude_case"]` 命中丟（demo 案的來源決定書）。
- 前綴預設值 `retrieval.kb.DEFAULT_PREFIXES = ["歷史訴願決定書/", "新北訴願決定書_全量/"]`，是相似案通道收哪些前綴的**唯一事實來源**——N4 不傳 `filters["prefix"]`，不在節點層再寫一份。**2026-09-12 放寬**：原本只收 `["歷史訴願決定書/"]`（寫 spec 時爬蟲批的角色還沒定）。實測 KB 裡 public 批 2347 筆、official 批 101 筆，`numberOfResults=15` 時前 15 名全是 public，官方那批被完全擠掉，後過濾把相似案清成 0 筆。兩批都是新北市政府訴願決定書，性質相同，來源差異由 `payload.provenance`（`official` / `public_crawl`）標示，UI 看得到。刻意仍**不收** `行政函釋/` 與 `司法院釋字及行政判解/`——那兩類是通道 A 與 N5 `REF_PREFIXES` 的材料，不是相似案。
- 相似案的**席次配額**（2026-09-12 加）：`SIMILAR_CASE_QUOTA = {"歷史訴願決定書/": 2, "新北訴願決定書_全量/": 3}`，合計仍是 `top_k=5`。做法是**兩批分開查詢、各自取配額席次、合併後按分數排序**（`kb-1` 永遠是分數最高的那筆），不是「先撈一大包再硬塞席次」——後者會在官方那批其實不相關時把爛結果塞進前五名。某一批不足由另一批補滿（不留空位），但一律仍受 `KB_MIN_SCORE` 約束：寧可少一筆也不塞。配額查詢的 `numberOfResults` 用 `QUOTA_FETCH_DEPTH=50`（實測：=15 時 official 0 筆、=50 時 2 筆，抓不夠深席次會永遠空著）。兩次 retrieve 之間 sleep `RETRIEVE_INTERVAL_S=1.1` 秒（賽方規範：Bedrock 壓在 1 RPS 以下）。**這個配額成立的前提是兩批分數落在同一條線（實測 0.77–0.80）**；若 official 的最佳命中掉到與 public 差一截，保席次等於犧牲相關性，屆時要回頭重議。
- 明確指定 `filters["prefix"]`（N5 的 `retrieve_refs`）→ 單次查詢、抓三倍，行為不變；不指定 prefix ＝ 相似案通道 ＝ 走配額。
- `retrieval_meta.similar_case_channel.hits_by_provenance` 報兩批各自的實際命中數（例：`{"official": 2, "public_crawl": 3}`），數的是真的回來的命中，不是配額設定值。
- `Hit.source` = 去掉 bucket 與 `kb/{official|public}/` 之後的相對路徑，`Hit.verified` 由 N6 對 manifest 決定，`Hit.payload` 帶 `{"score", "text", "provenance": "official" | "public_crawl"}`。
- 查詢句：N1 抽出的事實段**原文**（`facts_excerpt[].text` 加 `intake.note`），不用改寫句（AppealAssist ask 模式教訓：改寫句漏撤銷案）。現有 `n4_retrieval.build_query()` 調整。
- 環境變數：`BEDROCK_KB_ID`、`AWS_REGION`、`KB_MIN_SCORE`（預設 0.25）。`RETRIEVER=kb` 才啟用，否則維持 `UnavailableRetriever`。
- 通道 A（法條）維持 `lawtable`，不進 KB。

### 5.5 可續跑執行

**持久化**：每次 `run_case` 終態 `CaseState` 以 JSON 存 `backend/output/runs/{run_id}.json`（gitignored）。Phase 1 用檔案，不上 DB。

**API**：`POST /api/cases/{case_id}/runs` body 擴充：

```jsonc
{
  "base_run_id": "run-...",        // 選填；要接續的執行
  "from_node": "n5",               // 選填；n1..n6；缺省 n1；給了就必須給 base_run_id
  "confirmed_intake": {...},       // 現有
  "overrides": { "n4_query": "…" } // 選填；白名單：目前只有 n4_query
}
```

**編排**：`run_case(..., base_state=None, from_node="n1", overrides=None)`：

- 有 `base_state`：複製其 `n1..n(from-1)` 的結果與 `field_origins`，`state.run_id` 給新值，`run_meta.base_run_id` 記來源。
- 從 `from_node` 起依 `NODE_ORDER` dispatch 到 n6。**不允許停在中間**。
- `from_node="n2"` + `confirmed_intake`：即「承辦人確認欄位後繼續」，N1 不重跑、模型不重抽（解掉 live 模式下第二次抽取欄位飄移的問題）。

**畫面動作對映**：

| 動作 | from_node | 重跑 | 呼叫模型 |
|---|---|---|---|
| 重新抽取 | n1 | 全部 | N1、N5 |
| 確認欄位後繼續 | n2 + confirmed_intake | N2–N6 | N5 |
| 重新檢索（可帶 n4_query） | n4 | N4–N6 | N5 |
| 重新產生草稿 | n5 | N5、N6 | N5 |

`overrides.n4_query` **同時進兩條通道**（覆核 I-5）：以 `cited_laws` 進通道 A（法規查表的 `query_text`），並以 `extra_case_terms` 附加在通道 B（相似案）`case_query` 的尾端——附加而不取代，案情組出來的查詢句仍是主體。畫面上那個輸入框緊鄰相似案卡，只進通道 A 會讓功能名稱與實際行為不符。

`run_meta.run_id_note` 改為如實描述：「支援 base_run_id 續跑；同 body 重送仍產生新 run」。

### 5.6 SSE 節點事件

- 僅 `RUN_MODE=bedrock`。`POST /runs` 回 `202 {run_id}`，執行放 `BackgroundTasks`。
- `GET /api/runs/{run_id}/events`：`text/event-stream`，事件序：

```
event: node_start   data: {"node":"n1","agents":["clerk"]}
event: node_done    data: {"node":"n1","elapsed_ms":7120,"degraded":false}
...
event: run_done     data: {"run_id":"...","final_state":"VERIFIED"}   // 或 run_failed {"node","error"}
```

- `GET /api/runs/{run_id}`：回完整 payload（`build_payload(state)`），前端收到 `node_done` 後可立即取部分 payload 渲染該節點的卡。
- fixture 模式：`POST /runs` 維持 200 + 完整 payload，前端偵測狀態碼分流。斷網備援路徑不變。

### 5.7 上傳案件與卷證文字路由

**入口**：`POST /api/cases`（multipart，`.pdf`／`.txt`，單檔 20 MB）→ `backend/output/uploads/upload-<sha12>/{原檔, case.json}` → 回 `{case_id, files, provenance{kind:"uploaded"}}`。接著照常 `POST /api/cases/upload-xxx/runs`。fixture 模式對上傳案回 400（沒有可重播的 fixture，不假裝）。

**卷證文字路由**（`backend/intake/documents.py`，不裝 OCR 套件）：

| 輸入 | 路由 | 進 N1 的形式 |
|---|---|---|
| `.txt` | `txt` | 文字 |
| `.pdf`，`pdftotext -layout` 後中文比例 ≥ 0.60 | `pdf_text` | 文字 |
| `.pdf`，中文比例 < 0.60（掃描件、CID 字型） | `pdf_visual` | 整份 PDF 以 Converse `document` 區塊餵模型視覺讀（單檔 4.5 MB 上限，超過則送殘缺文字並註記） |
| 以上仍抽不出必填欄位 | — | N1 `degraded` → `NEEDS_INPUT` → 承辦人手動表單（architecture §3.5，既有） |

走了哪一層、中文比例多少，寫進 N1 `generation.input_route` 與 clerk 敘述（分層誠實）。

**下游改吃 N1 輸出**：N2 分類與 N3 事實爭點偵測原本吃合成案例的 `case_digest`；上傳案沒有它，改由 `facts_excerpt[].text` 與 `intake.note` 組成 `digest_from_state()`。合成案例仍用 `case_digest`，fixture 行為不變。

**Provenance**：payload 的 `provenance` 改讀案例自帶的區塊（合成案例本來就有 `kind: synthetic`），上傳案為 `kind: uploaded` 並帶橫幅「卷證來自使用者上傳，未進 git」。

### 5.8 設定與 secret

- 新增環境變數（全部在 `backend/DEPLOY.md` 說明、`.env.example` 給名稱不給值）：`MODEL_PROVIDER`、`BEDROCK_MODEL_ID_EXTRACT`、`BEDROCK_MODEL_ID_DRAFT`、`BEDROCK_KB_ID`、`AWS_REGION`、`AWS_PROFILE`（本機）、`RETRIEVER`、`KB_MIN_SCORE`、`S3_KB_BUCKET`。
- `settings.py` 維持紅線：不出現任何帳號 ID、KB id、model id 的實際值。
- `requirements.txt` 新增 `boto3~=1.35`、`strands-agents>=1.15`、`pydantic` 已有。測試路徑（`run_all.py`）仍零第三方依賴。
- CI import graph 檢查（architecture §1 第 4 條的 grep）加入 `strands` 關鍵字：N2、N3、N4、N6 不得直接或間接 import `backend.llm` 或 `strands`。**注意**：N4 import `retrieval.kb`（boto3）是允許的——它是檢索不是 LLM；grep 白名單要把 `retrieval/kb.py` 的 boto3 排除在「LLM 依賴」之外，改用 ast 檢查 `backend.llm` 模組路徑而不是 grep `boto3`。

## 6. 資料與 KB

### 6.1 兩批來源

| | 賽方資料集（official） | 爬蟲資料（public_crawl） |
|---|---|---|
| 來源 | 法制局，「僅供競賽之用」 | web.law.ntpc.gov.tw、law.moj.gov.tw 公開資料 |
| 內容 | 決定書 101、法規 11、函釋 10、釋字判解 19 | 決定書 2,347（廢清法 1,177、空污法 1,170，民 110–114）、法規 18 部 1,153 條、合成訴願書 1,607 |
| 進 git | **不進**（CONSTITUTION §6、`.gitignore`） | 程式碼進獨立 repo；資料檔（74 MB）不進任何 git |
| 進 KB | 決定書、函釋、釋字判解；**法規不進**（走查表） | 決定書進，標 `provenance=public_crawl` |
| UI | 無標記 | 相似案卡加「公開資料庫」標記 |

### 6.2 S3 佈局與 manifest

```
s3://{S3_KB_BUCKET}/
  kb/official/歷史訴願決定書/{年}/{原檔名}.txt
  kb/official/行政函釋/…txt
  kb/official/司法院釋字及行政判解/…txt
  kb/public/新北訴願決定書_全量/{case_no}.txt
  raw/official/…pdf                      ← 原始 PDF，KB data source 不掃這裡
```

- KB data source 只指到 `kb/`。bucket block public access 全開。
- `data/manifest.json` 進 git：每筆 `{path, provenance, sha256, source_pdf, case_no?, outcome?, year?}`，**不含內容**。
- `scripts/ingest_kb.py`：讀 manifest → 對本機檔算 sha256 比對 → 缺的上傳 → 呼叫 `start_ingestion_job` → 等完成。冪等：重跑不重傳已存在且 hash 相同的檔。
- 決定書 txt 檔名保留案號與結果字樣（`_駁回`／`_撤銷`／`_不受理`），N6 對實檔與 UI 顯示結果都靠它。

### 6.3 KB 整理規則

1. `pdftotext -layout` 轉 txt；每檔檢查 CJK 字元比例 ≥ 60%，不足者列入 `ingest_report.md` 人工處理（Managed KB 直接吃 PDF 全亂碼，AppealAssist 實測）。
2. 法規 11 部**不入 KB**，已在 `laws-snapshot.json`。
3. 檔名去掉「 的副本」等雜訊，正規化為 `{序}.{年}-{案型}-{條款}-{結果}.txt`。
4. 爬蟲決定書由 `cases.jsonl.full_text` 產生單檔，metadata 側檔帶 `outcome`、`year`、`category`（若 Managed KB 支援 metadata filter 則後過濾可少抓）。

### 6.4 搬遷程序

1. 開發用帳號：建 bucket、建 Managed KB、跑 `ingest_kb.py`、實測兩個合成案 recall。
2. 賽方帳號到手：換 `AWS_PROFILE`、`S3_KB_BUCKET`，建 KB，重跑 `ingest_kb.py`，把新 `BEDROCK_KB_ID` 填進部署環境。程式零改動。
3. 兩邊 `kb_snapshot_date` 與 manifest hash 一致即視為搬遷完成。

### 6.5 爬蟲資料的其他用途（本規格內只做第一項）

1. **期間引擎回放**：從 `cases.jsonl` 篩 `outcome` 含不受理且 `legal_basis` 含 77(2) 的 251 件，抽 `disposition_date`／`decision_date`／送達與收文日期欄位，產生 `backend/data/replay/overdue-public.jsonl`（去識別、只留日期與案號），`qa-legal` 用 `engine/deadline.py` 回放，目標一致率 ≥ 99%。
2. Phase S：法規快照擴充（18 部含修正日）、N2 kNN 鄰居擴充、合成訴願書當 N1 評測輸入（`synthetic-` 前綴）。

## 7. 錯誤處理與降級

| 情境 | 行為 | 使用者看到 |
|---|---|---|
| Bedrock 呼叫失敗（重試耗盡） | 節點 raise `LLMError` → `run_failed` 事件 → `GET /runs/{id}` 回 502 帶 `{node, error}` | 該節點卡紅字「模型呼叫失敗：{原因}」，其餘卡維持上次結果 |
| KB 呼叫失敗（KB 不可用） | `similar.search` 的任何例外由 N4 接住 → `cases=[]`、`similar_available=False`、`degraded=True`、`degrade_reason="相似案檢索失敗（KB 不可用）：{型別}: {訊息}"`，通道 A 照常 | 相似案卡紅色降級 log，文案與「查無相似案」「無資料集」明確分開；法條卡正常（architecture §3.2 降級可見） |
| 模型輸出不符 schema | strands structured_output 重試，仍失敗視同呼叫失敗 | 同第一列 |
| N1 conf 不足 | 現有 `NEEDS_INPUT` 路徑 | 收文頁要求補欄位 |
| `from_node` 給了但 `base_run_id` 找不到 | 400 | — |
| 上傳案在 fixture 模式 | `POST /runs` 回 400「只能在 RUN_MODE=bedrock 執行」 | hint 顯示原因，不用舊資料 |
| PDF 無文字層且 > 4.5 MB | 送殘缺文字，N1 必填欄位抽不出 → `NEEDS_INPUT` | 收文頁要求手動補欄位，敘述註明原因 |
| `RUN_MODE=bedrock` 但缺 `BEDROCK_*` 變數 | 啟動時 `/api/health` 回 503 列缺哪些 | 健康檢查頁 |

任何情境都**不自動切回 fixture**。

## 8. 測試與驗收條件（可執行）

| # | 條件 | 驗證方式 |
|---|---|---|
| AC1 | fixture 模式行為零變化 | `python3 backend/tests/run_all.py` 全綠；HANDOFF.md 的 Playwright 腳本 exit 0 |
| AC2 | 零依賴測試路徑不變 | `run_all.py` 靜態掃描通過（第三方 import 只在豁免檔，且以頂層 try/except 守衛）；`scan_top_level_imports` 綠 |
| AC3 | N2/N3/N4/N6 無 LLM 依賴 | `run_all.py` 的 `scan_llm_import_graph`（ast 遞迴）綠 |
| AC4 | N1 live 抽取 | `RUN_MODE=bedrock` 對 `synthetic-ordinary-01` 的卷證 txt 跑 N1，12 個 intake 欄位全部有 `origin=llm` 與 0–1 的 `conf`；`run_meta.model_ids.extract` 非空 |
| AC5 | N5 live 組稿且引用可驗 | 同案跑到 N6，`doc[]` 每句 `cite_ids` ⊆ N4 結果 ∪ 工具回傳；白名單外的引用被清除且該句在 N6 判紅並進 `cite_id_unsupported` blocker（§5.3） |
| AC6 | C 型封鎖在 live 下仍成立 | `synthetic-blocked-01` live 跑完 `requires_human_conclusion=true` 且 `doc[]` 無 `slot=conclusion, origin=llm` |
| AC7 | KB recall | 現有兩個合成案（`synthetic-ordinary-01` 空污、`synthetic-blocked-01` 建築法）各跑一次，top-5 至少 3 件同案型（`case_type` 相同）；相似案卡在 manifest 對檔前一律標「KB 命中，未對資料集實檔驗證」；記錄於 `docs/evidence/…/kb-recall.md` |
| AC8 | 續跑正確 | `from_node=n5, base_run_id=R`：N1–N4 結果與 R 位元相同、N5/N6 重算、`run_meta.base_run_id==R`；`from_node=n5` 不帶 base 回 400 |
| AC9 | 確認欄位不重抽 | `from_node=n2` + `confirmed_intake`：`node_timings` 無 `n1`，模型呼叫次數為 1（N5） |
| AC10 | SSE | `curl -N /api/runs/{id}/events` 收到 6 對 `node_start/node_done` 與 1 個 `run_done`，順序符合 `NODE_ORDER` |
| AC11 | 失敗不假裝 | 把 `BEDROCK_MODEL_ID_EXTRACT` 設成不存在的 id，`GET /runs/{id}` 回 502，body 含 `node:"n1"` 與原始錯誤字串；無任何 fixture 內容出現在 payload |
| AC12 | 入庫冪等 | `ingest_kb.py` 連跑兩次，第二次上傳數 0，ingestion job 仍成功 |
| AC13 | 期間回放 | `overdue-public.jsonl` 回放一致率 ≥ 99%，輸出報告進 `docs/evidence/` |
| AC15 | 上傳案端到端 | 把 `synthetic-ordinary-01` 的 `documents[]` 文字以 txt 上傳建案，bedrock 跑完 `intake.no/d2/d3/service_method` 與該案 fixture 一致、N2 案型一致 |
| AC16（加值） | 掃描件視覺讀取 | 同一份文字排成圖片存 PDF（無文字層）上傳，N1 走 `pdf_visual`，clerk 敘述含「視覺讀取」，`intake.type` 與 `d3` 抽得出；抽不出如實記錄 |
| AC14 | secret 掃描 | `git grep -nE "\b[0-9]{12}\b|AKIA[0-9A-Z]{16}" -- backend docs plans scripts` 無結果（12 碼數字視為疑似 AWS 帳號 ID；`run_all.py` 的 secret 掃描同步納入此 regex） |

AC4–AC11、AC15、AC16 標 `@live`，需要 Bedrock 開通；其餘在 fixture 下可跑。必要層必過：AC1–AC9、AC11、AC14、AC15；加值層：AC10、AC12、AC13、AC16。

## 9. 文件同步（工作包 F）

- `docs/architecture.md`：§4.1 加註 D1；§8 改 Managed KB（D2）並刪 chunking=NONE；§13 #5 維持、#6 改、新增 Managed KB recall 待拍板與 D4。
- `docs/spec/prototype-spec.md` §4.6：「用 AgentCore 包」→「用 Strands 包，AgentCore 為 Stretch」。
- `CLAUDE.md`：帳號規矩改為 D4 文字；「不碰 GCP <other-gcp-project>」保留。
- `backend/DEPLOY.md` §1.2：環境變數表補齊；IAM 最小權限加 `s3:GetObject/PutObject` 限 bucket。
- `backlog.md` Phase S：ask 追問 agent、AgentCore Runtime、爬蟲法規擴充、洗錢案補爬。
- `HANDOFF.md`：本分支交接段。

## 10. 風險與待拍板

| # | 風險 | 處置 |
|---|---|---|
| R1 | 開發用帳號 Bedrock 仍 `Operation not allowed`（帳號驗證中，PAID/ACTIVE 已確認） | Support Case 已建議送出；程式開發不受阻（fixture + monkeypatch）；live AC 標「未驗」，**不得以 `MODEL_PROVIDER=openai` 的輸出充當 AC 證據**（賽制僅限 AWS 基礎模型）；賽方是否提供帳號在 `#hack-general` 確認 |
| R2 | Managed KB 不可控 chunking 導致 recall 不足 | AC7 實測；不足則 §13 待拍板改自管 KB + S3 Vectors（architecture §8 原案），介面不變 |
| R3 | Sonnet 5 需跨區 inference profile；改 us-west-2 後前綴由 `apac.` 變 `us.` | 開通後 `list-inference-profiles` 確認；model id 走環境變數，只改 region 不改 model id 會 ValidationException |
| R4 | live 模式下 N1 兩次抽取結果不同 | D6 + AC9：確認後從 n2 續跑，不重抽 |
| R6 | 手寫掃描件的視覺讀取品質未實測（手上沒有真實訴願書） | AC16 用自造掃描件量測；賽場遇到抽不出就走手動表單，這是設計不是失敗 |
| R7 | 賽方「僅供競賽之用」資料集上傳到非競賽用帳號的 S3 是 CONSTITUTION §6 的邊界 | bucket 私有、只放 `kb/` 前綴 txt、賽方帳號到手後重建並**刪除**開發用帳號的 bucket 與 KB；Claire 2026-09-06 拍板「先用開發用帳號」 |
| R5 | 開發期借開發用帳號與 CLAUDE.md 衝突 | D4 由 Claire 拍板並改文件；權限分類器目前擋開發用 profile 的 aws 指令，需在 `.claude/settings.local.json` 加允許規則，否則 live 測試由人手動跑 |

## 11. 前置條件

- Bedrock model access 通過（帳號驗證通過或賽方帳號）。
- AWS CLI ≥ 2.30（本機已從 2.12 升級）。
- 賽方資料集實體檔（`C_法制局-資料集.zip`，問 Ci），本機解壓至 `prototype/data/dataset/`（gitignored）。
- 爬蟲資料 `cases.jsonl` 本機路徑或 S3 位置。
