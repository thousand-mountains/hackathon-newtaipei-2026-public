# Delta Spec: case-dossier-crud

> REQ ID 格式：`REQ-{MODULE}-{NUMBER}`。每條對應 `proposal.md` 的 AC 編號。

## ADDED

### REQ-DOSSIER-001：一案一份 manifest 的持久化層（US-B1）

`backend/output/cases/{case_id}/manifest.json`，四份清單同一物件，不加資料庫。

1. 頂層含 `case_id`／`name`／`created_at`／`latest_run_id`／四組清單。（B1.2）
2. 寫入走 tmp + `os.replace`；**有測試**證明中途失敗舊檔完好、不留 `.tmp`。（**B1.4**）
3. `case_id` 走白名單 regex，不合法直接拒絕，不做路徑清洗。
4. 改名／刪案在重啟 process 後仍生效。（**B1.3**）
5. docstring 明載三條限制：不原子已修、多副本會分裂、容器重啟即失。

**Priority:** P0

---

### REQ-DOSSIER-002：案件清單與彙整開案（US-B1）

1. `GET /api/cases` 每筆含 `{id, name, created_at}`。（**B1.1**）
2. `GET /api/cases/{id}` 一次回 `{case, files, laws, references, artifacts}`。（**B1.2**）

**Priority:** P0

---

### REQ-DOSSIER-003：上傳卷證建案與可讀性誠實（US-B2）

1. `POST /api/cases` 維持 multipart「上傳卷證即建案」，**沒有「先建空案」**。（**B2.1**）
2. 不限副檔名；讀不到的回 `readable:false` 並在 `note` 說原因，**不得靜默跳過**。（**B2.2**）
3. **掃描影像 PDF（`route_documents` 判 `pdf_visual`，無文字層）`readable` 必須是 `false`**
   ——契約 §4.1 與 §6 各講了一次。`note` 要帶得出原因（中文比例低於門檻、改以視覺讀取），
   讓畫面說得出「為什麼讀不到」，而不是只說讀不到，也不得暗示支援 OCR。（**B2.3**）

**Priority:** P0

---

### REQ-CORPUS-001：母庫查法規（US-B3）

1. 回的每一筆 `doc_kind == "statute"`；要有「不帶 filter 抓 20 筆時法規佔幾筆」的實測數字。（**B3.1**）
2. 同一部法規的多個 chunk 收斂成一筆；要有「未去重 N → 去重後 M」的實測數字。（**B3.2**）
3. `body` 的 utf-8 位元組數 == 該 S3 物件 `ContentLength`。（**B3.4**）
4. 恆 `verified:false`、`relevance:"unknown"`；`retrieval/lawtable.py` 一行未改。
5. filter 篩不到東西時**回空，不退回不篩**（與 N4 的防呆刻意相反）。

**Priority:** P1

---

### REQ-CORPUS-002：母庫查訴願決定（US-B3）

1. 結果**不含** `court_ruling`。（**B3.3**）
2. `verdict`／`category` 來自側檔，缺席留 `null`，**不從內文猜**。
3. 對 `court_ruling` 的 id 直取回 400 並說明本期不納入。

**Priority:** P1

---

### REQ-DOSSIER-004：加入即快取全文（US-B3）

1. `POST /cases/{id}/laws`／`references` 寫 `body_cached`／`full_cached`。
2. `GET /cases/{id}/laws` **只讀 manifest，不打 KB**。（**B3.5**）

**Priority:** P1

---

### REQ-DOSSIER-005：手動挑的法規影響草稿（US-B4）

1. `manifest.json` 的路徑與欄位名必須是 `chat_bridge.load_case_manifest` 讀得到的形狀
   ——B4.1／B4.2 的 `terms` 由 `llm/chat.py` 組，前提是讀得到這份檔。（**B4.1／B4.2 的前提**）
2. 一次草稿 run 之後，manifest 的 `laws[]` 每筆要標「有進檢索結果」或
   「檢索未命中，未進入草稿」。（**B4.3**）
3. **紅線**：未命中的法規**不得**被塞進 `payload["laws"]` 以求好看。
   這條要有**反向測試**——斷言「塞進去」這件事沒有發生，不是只測「有標記」。（**B4.3**）

**Priority:** P1

---

## MODIFIED

### REQ-API-CASES-LIST：`GET /api/cases` 回應形狀
**Before:** `cases` 是 case_id 字串陣列。
**After:** `cases` 是 `[{id,name,created_at,kind}]`；`synthetic`／`uploaded` 維持字串陣列（相容）。
**理由:** B1.1。現無呼叫端讀 `cases` 這個鍵。

### REQ-BRIDGE-ARTIFACT-ID：`pipeline_adapter` 的 `artifact_id`
**Before:** 寫死 `None`，註解寫「產出 id 由案件資源層配，這一層還不知道」。
**After:** 由案件資源層登記後回真值；`GET .../artifacts/{id}` 同時接受 `run-…`（契約 §4.4 過渡規則）。
**理由:** 那個註解指的就是本 Epic。

## REMOVED

_No removals in this change._
