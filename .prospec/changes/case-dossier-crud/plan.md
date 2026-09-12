# Implementation Plan: case-dossier-crud

## Overview

右欄「案件卷宗」四個群組目前完全是前端 mock。這個變更把它接上真實後端：
**一案一份 `manifest.json` 當書籤清單**，加上 16 支一次性端點（案件 4 支、四組成員各 3 支、母庫查 4 支）。

三個關鍵設計決策：

1. **書籤 ≠ 資料庫**。`manifest.json` 只存「這個案子挑了哪些東西」＋顯示欄位快取，
   **搜尋完全不經過它**（搜尋打 Bedrock KB）。所以「不加 DB」跟「能不能搜尋」無關（契約 §4.0）。
   限制（拍板接受）：多副本會分裂、容器重啟即失——兩條都要寫進 docstring，不讓人誤當資料庫。
2. **原子寫從一開始就做**。`runstore.py:36` 的 `p.write_text` 是既有的債，
   本變更**不複製它**：新的寫入路徑一律 tmp + `os.replace`，並有測試證明中途失敗不留半份檔。
   （不順手改 `runstore.py`——那是已驗綠路徑，決賽期間不動。）
3. **母庫查是一條新通道，不是改既有檢索**。`KBRetriever._retrieve` 服務的是 N4 相似案／N5 判解，
   有配額、重排、前綴白名單與 exclude_case，那套語意不適用「使用者自己搜法規」。
   所以在 `kb.py` **純增量**加一支 `search_corpus()`，不動任何既有方法（要與主工作樹合併）。

## Affected Modules

| Module | Impact | Changes |
|--------|--------|---------|
| `backend/dossier/store.py`（新） | High | manifest 讀寫、原子寫、四組清單的 add/remove、lazy 初始化 |
| `backend/dossier/corpus.py`（新） | High | 母庫查：KB `doc_kind` filter ＋ 去重 ＋ S3 `get_object` 全文 ＋ key 白名單 |
| `backend/api/dossier.py`（新） | High | 16 支端點（APIRouter） |
| `backend/api/app.py` | Medium | `include_router`；`GET /api/cases` 補 `{id,name,created_at}`；`POST /api/cases` 建案時寫 manifest；run 成功時更新 `latest_run_id` |
| `backend/retrieval/kb.py` | **Low（純增量）** | 只加模組層函式 `search_corpus()`＋`Hit` 不變。**不重構既有路徑**，減少與主工作樹的合併衝突 |
| `backend/config/settings.py` | Low | `CASES_DIR = OUTPUT_DIR / "cases"` |
| `backend/tests/test_dossier.py`（新） | High | 新測試模組 |
| `backend/tests/run_all.py` | Low | 註冊新測試模組 |

**不碰**：`backend/api/chat.py`、`backend/llm/chat.py`、`backend/nodes/*`、`frontend/*`、`infra/*`、
`backend/retrieval/lawtable.py`（法規查表通道維持現狀，見 §4.2 的「兩條通道」）。

## Implementation Steps

1. **持久化層 `backend/dossier/store.py`**
   - `CASES_DIR = OUTPUT_DIR / "cases"`；路徑一律走 `case_id` 白名單 regex
     （沿用 `runstore._path` 的做法：不合法就 `ValueError`，**不清洗後照樣讀**）。
   - `_atomic_write_json(path, obj, dump=json.dump)`：`tempfile.mkstemp(dir=path.parent)`
     → 寫 → `flush` + `fsync` → `os.replace` → 失敗時 `unlink` 掉 tmp 再往上丟。
     `dump` 參數是**測試接縫**，讓測試可以注入「寫到一半就爆」的 dump。
   - `default_manifest(case_id)`：從既有案件推導（上傳案讀 `case.json` 的 `label`／`uploaded_at`／
     `documents`；合成案讀 `backend/data/synthetic/{id}.json`）。**不編造任何欄位**，推不出來的留 `null`。
   - `load(case_id)` / `save(manifest)` / `ensure(case_id)`（缺檔就用 default 建一份並落地）。
   - `add_items(case_id, group, items)` / `remove_item(case_id, group, item_id)`：read-modify-write，
     依 `id` 去重（重複加入不會變兩筆）。
   - `rename(case_id, name)`、`delete_case(case_id)`、`set_latest_run(case_id, run_id)`、
     `record_draft_artifact(case_id, run_id, title)`。

2. **母庫查 `backend/retrieval/kb.py` 的純增量函式 `search_corpus()`**
   - 簽名：`search_corpus(client, kb_id, query, doc_kinds, *, limit, want, min_score) -> list[dict]`
   - 內部：`retrieve_raw(client, kb_id, query, want, doc_kind_filter(doc_kinds))`
     → 逐筆 `_relative_path(uri)` 取 `(kind, rel)` → **依 `kb/{kind}/{rel}` 組回完整 S3 key 當 `id`**
     → **依 key 去重**（同一份文件只留最高分那筆）→ 截到 `limit`。
   - 為什麼不重用 `_retrieve`：那支綁了前綴白名單、exclude_case、配額與重排，
     是 N4／N5 的語意；而且它是**主工作樹正在改的區域**，動它會製造合併衝突。
   - **不加 fallback**：`_retrieve` 有「篩了全空就退回不篩」的防呆，那在 N4 是對的
     （寧可回相關性低的也不要靜默 0 筆）；但母庫查**不能**退回不篩——
     退了就會把裁判書當法規端給使用者（實測不篩 20 筆裡法規 0 筆）。查無就回空。

3. **母庫全文 `backend/dossier/corpus.py`**
   - `_safe_key(doc_id)`：必須 `^kb/(official|public)/` 開頭、不得含 `..`、必須 `.txt` 結尾、
     不得是 `.metadata.json`。不合就 `ValueError`（→ 400）。**這條是安全邊界**：
     `doc_id` 從 HTTP path 進來，直接餵 `get_object` 等於讓呼叫端讀任意物件。
   - `fetch_text(key)`：`s3.get_object` 讀全文（**不從 KB chunk 拼**，契約 §4.2／CONSTITUTION §1）。
   - `fetch_sidecar(key)`：讀 `{key}.metadata.json` 取 `doc_kind`／`outcome`／`category`／`provenance`。
     側檔缺席就全部 `null`——**不從內文猜案型**（契約 §4.3）。
   - `S3_KB_BUCKET` 沒設時 raise 並指名缺哪個環境變數（→ 503），不假裝服務正常。

4. **端點 `backend/api/dossier.py`（APIRouter）**
   - 案件：`GET /api/cases/{id}`（彙整）、`PATCH`、`DELETE`
   - 卷證：`GET|POST /api/cases/{id}/files`、`DELETE …/files/{fileId}`
   - 法規：`GET|POST /api/cases/{id}/laws`、`DELETE …/laws/{lawId:path}`
   - 案例：`GET|POST /api/cases/{id}/references`、`DELETE …/references/{refId:path}`
   - 產出：`GET /api/cases/{id}/artifacts`、`GET|DELETE …/{artifactId}`
   - 母庫：`GET /api/laws?q=`、`GET /api/laws/{lawId:path}`、`GET /api/decisions?q=`、`GET /api/decisions/{decisionId:path}`
   - `{lawId:path}` 用 path converter：id 是 S3 key，含 `/`。
   - **加入法規／案例時把全文快取進 manifest**（`body_cached`／`full_cached`，契約 §4.0），
     demo 當下不依賴 KB／S3 還活著。
   - `GET /api/decisions/{id}`：側檔 `doc_kind == "court_ruling"` 一律 **400 並說明理由**
     （本期不納入相似案例，Ci 拍板）——不靜默回一份法院裁判。

5. **`app.py` 接線**
   - `include_router(dossier_router)`。
   - `GET /api/cases` 的 `cases` 改成 `[{id,name,created_at,kind}]`；
     `synthetic`／`uploaded` 維持 id 字串陣列（既有呼叫端相容）。
   - `POST /api/cases` 在 `save_upload` 之後 `store.ensure(case_id)`。
   - `_run_in_background` 與同步 run 成功後 `store.set_latest_run(case_id, run_id)`
     ＋ `record_draft_artifact`（草稿真的存在時才記）。

6. **測試 `backend/tests/test_dossier.py`**（stdlib only，沿用 `harness`）
   - 原子寫：注入「寫一半就爆」的 dump → 舊檔完好、目錄無殘留 tmp。
   - 跨 process：`subprocess` 另起 python 讀 manifest，證明不是靠進程內狀態。
   - 四組 add/remove 冪等、去重。
   - `search_corpus` 用假 client：驗 filter 有送出、驗去重、驗**沒有** fallback。
   - key 白名單：`../`、`.metadata.json`、非 `kb/` 開頭一律被擋。
   - 端點形狀對齊契約欄位名（用 `fastapi.testclient` 若可用；否則直接呼叫函式）。
   - 註冊進 `run_all.py`。

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| `kb.py` 與主工作樹衝突 | High | 只加**模組層新函式**，零行既有程式碼被改；回報時明列改了什麼 |
| `S3_KB_BUCKET` 未設（現行 `.env` 就沒有） | High | 缺就 503 並指名變數；**回報列為待決**，部署環境要補 |
| `id` 用 `rel` 還是完整 S3 key（契約 §4.2 自相矛盾） | Medium | 採**完整 key**（契約的兩個 POST body 實例都是完整 key，且可直接 `get_object`）；回報標記請前端確認 |
| 全文快取讓 manifest 變大（法規 51 KB／份） | Medium | 只在「加入本案」時快取，不快取搜尋結果；單案量級仍是數百 KB |
| AWS 為 Workshop 短期金鑰，會過期 | Medium | 實測驗收留時間戳；過期就在回報寫「過期」，**不用 fixture 冒充實測** |
| 刪卷證檔要不要刪磁碟檔 | Low | 刪（否則 `load_upload_case` 會把它路由回來），並同步更新 `case.json`；docstring 寫明 |
