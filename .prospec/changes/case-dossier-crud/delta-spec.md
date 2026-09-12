# Delta Spec: case-dossier-crud

> REQ ID 格式：`REQ-{MODULE}-{NUMBER}`

## ADDED

### REQ-DOSSIER-001: 一案一份 manifest 的持久化層

**Description:**
`backend/output/cases/{case_id}/manifest.json` 存一個案子的四份成員清單與案件中繼資料。
寫入一律 tmp + `os.replace`。不引入資料庫。

**Acceptance Criteria:**
1. 檔案落在 `backend/output/cases/{case_id}/manifest.json`，頂層含
   `case_id`／`name`／`created_at`／`latest_run_id`／`files`／`laws`／`references`／`artifacts`。
2. 序列化中途失敗時，磁碟上的舊檔內容不變，且目錄內不留 `.tmp` 殘骸（有測試）。
3. `case_id` 走白名單 regex，不合法直接拒絕，不做路徑清洗。
4. docstring 明載兩條限制：多副本會分裂、容器重啟即失。

**Priority:** High

---

### REQ-DOSSIER-002: 案件 CRUD

**Description:**
`GET /api/cases` 補齊顯示欄位；`GET /api/cases/{id}` 一次回彙整；`PATCH` 改名；`DELETE` 刪案。
建案語意維持「上傳卷證即建案」。

**Acceptance Criteria:**
1. `GET /api/cases` 的每個項目含 `{id, name, created_at}`。
2. `GET /api/cases/{id}` 回 `{case, files, laws, references, artifacts}`。
3. `PATCH /api/cases/{id}` body `{name}` → 改名後重啟 process 仍讀得到新名字。
4. `DELETE /api/cases/{id}` → `204`。
5. 沒有「先建空案」的端點。

**Priority:** High

---

### REQ-DOSSIER-003: 四組卷宗成員 C/R/D

**Description:**
`files`／`laws`／`references`／`artifacts` 各自 GET／POST／DELETE，路徑與欄位名逐字等同契約 §4.1–§4.4。

**Acceptance Criteria:**
1. 12 支端點齊備，四組都**沒有** PUT／PATCH。
2. `POST …/laws` body `{law_ids:[…]}`、`POST …/references` body `{decision_ids:[…]}`，回 `201`。
3. `DELETE` 一律 `204`；刪不存在的 id 回 `404`。
4. 重複加入同一個 id 不會在清單裡變成兩筆。
5. 加入時把全文快取進 `body_cached`／`full_cached`。

**Priority:** High

---

### REQ-CORPUS-001: 母庫查（法規）

**Description:**
`GET /api/laws?q=` 走 Bedrock KB 的 server-side `doc_kind=statute` filter ＋ 來源去重；
`GET /api/laws/{lawId}` 走 S3 `get_object` 讀全文。

**Acceptance Criteria:**
1. 回的每一筆 `doc_kind == "statute"`。
2. 同一部法規只回一筆。
3. filter 篩不到東西時**回空**，不退回不篩（與 N4 的防呆刻意相反，理由見 plan §2）。
4. `body` 以 UTF-8 編碼的位元組數 == 該 S3 物件的 `ContentLength`。
5. 回應恆 `verified:false`、`relevance:"unknown"`；`retrieval/lawtable.py` 一行未改。
6. `lawId` 不是 `kb/(official|public)/…​.txt` 形狀時回 400。

**Priority:** High

---

### REQ-CORPUS-002: 母庫查（訴願決定）

**Description:**
`GET /api/decisions?q=` filter `doc_kind=decision`；`GET /api/decisions/{decisionId}` 讀全文。

**Acceptance Criteria:**
1. 結果**不含** `court_ruling`。
2. `verdict` 來自側檔 `outcome`、`category` 來自側檔 `category`；側檔缺席留 `null`，不從內文猜。
3. `provenance` ∈ `official`／`public_crawl`／`null`。
4. 對 `court_ruling` 的 `GET /api/decisions/{id}` 回 400 並說明本期不納入。

**Priority:** High

---

## MODIFIED

### REQ-API-CASES-LIST: `GET /api/cases` 回應形狀

**Before:** `cases` 是 case_id 字串陣列。
**After:** `cases` 是 `[{id,name,created_at,kind}]`；`synthetic`／`uploaded` 維持字串陣列（相容）。
**理由:** 左欄要顯示案名與建立時間（契約 §1.1 #2）。現無任何呼叫端讀 `cases` 這個鍵
（`scripts/run_eval.py`、`scripts/live_acceptance.py` 只打 `POST /api/cases`）。

---

## REMOVED

_No removals in this change._
