# Implementation Plan: case-dossier-crud

> 對應 `proposal.md` 的 4 個 User Story、16 條 AC。**AC 是唯一驗收依據**，
> 本檔與 AC 衝突時以 AC 為準。

## Overview

右欄「案件卷宗」四群組目前完全是前端 mock。這個變更把它接上真實後端：
**一案一份 `manifest.json` 當書籤清單**，19 支一次性端點，加上「手動挑的法規餵回 N4」。

四個關鍵設計決策：

1. **書籤 ≠ 資料庫**。`manifest.json` 只存「這案挑了哪些東西」＋顯示欄位快取，
   **搜尋完全不經過它**（打 Bedrock KB）。「不加 DB」與「能不能搜尋」無關。
2. **原子寫從一開始就做**（B1.4）。不複製 `runstore.py:36` 的 `p.write_text` 債，
   而且**用測試而不是讀程式碼**證明中途失敗不留半份檔——AC 只要求「AST 或讀程式碼」，
   但那證明不了行為，測試才證明得了。
3. **母庫查是新通道，不是改既有檢索**。`kb.py` **純增量**加一支 `search_corpus()`，
   零行既有程式碼被改（主工作樹有別的 session 在動同一個檔）。
4. **B4 的一半已經是 Epic A 做的**（`llm/chat.py:695-700` 組 `terms`、
   `orchestrator/chat_bridge.py:43` 讀 manifest）。**我的部分是把 manifest 生出來
   讓它讀得到，加上 B4.3 的「檢索未命中」標記**——那個狀態只有案件資源層寫得了。

## Affected Modules

| Module | Impact | Changes |
|--------|--------|---------|
| `backend/dossier/store.py`（新） | High | manifest 讀寫、原子寫、四組 add/remove、lazy 初始化、B4.3 標記 |
| `backend/dossier/corpus.py`（新） | High | 母庫查：`doc_kind` filter ＋去重 ＋S3 `get_object` 全文 ＋key 白名單 |
| `backend/dossier/artifacts.py`（新） | Medium | run payload → 契約 §4.4 `sections[]`（放這裡才進得了 `run_all.py`） |
| `backend/api/dossier.py`（新） | High | 19 支端點 |
| `backend/api/app.py` | Medium | 掛 router；`GET /api/cases` 補欄位；建案寫 manifest；run 成功寫 `latest_run_id`＋artifact＋B4.3 標記 |
| `backend/retrieval/kb.py` | **Low（純增量）** | 只加模組層 `search_corpus()`。**不重構那個檔** |
| `backend/orchestrator/chat_bridge.py` | **Low（Epic A 的檔，只補一處）** | `pipeline_adapter` 的 `artifact_id` 現在寫死 `None`，註解自己寫「產出 id 由案件資源層配」——那就是本 Epic |
| `backend/config/settings.py` | Low | `CASES_DIR` |
| `backend/tests/test_dossier.py`（新）＋`run_all.py` | High | 新測試模組並**掛進 `run_all.py`**（沒掛＝沒有） |

**不碰**：`backend/api/chat.py`、`backend/llm/chat.py`、`backend/nodes/*`、`frontend/*`、
`infra/*`、`backend/retrieval/lawtable.py`（法規查表通道維持現狀）。

**測試入口是 `python3 backend/tests/run_all.py`，不是 pytest**（本專案沒裝 pytest，
`backend/tests/harness.py` 檔頭寫明 Phase 0 紅線要求測試路徑零第三方依賴）。
本變更**不引入任何新套件**。

## Implementation Steps

1. **持久化層 `store.py`**（B1.3、B1.4）
   - `_atomic_write_json`：`mkstemp` → 寫 → `fsync` → `os.replace`，失敗 `unlink` tmp 再往上丟。
     `dump` 參數是測試接縫，讓測試注入「寫一半就爆」的 dump。
   - `case_id` 白名單 regex 才拼路徑（沿用 `runstore._path`），不清洗、直接拒絕。
   - `default_manifest` 從既有案件推導，推不出的留空／`None`，**不編造**。
   - `add_items` 依 `id` 去重且不覆蓋使用者寫過的 `note`；`remove_item` 回 bool 讓上層翻 404。

2. **卷證可讀性**（B2.2、B2.3）
   - `readable` 由 `route_documents` 的 `kind` 決定。
     **`pdf_visual`（掃描影像、沒有文字層）必須是 `false`**——契約在 §4.1 與 §6 講了兩次。
     `note` 照抄 `route_documents` 的原因（例「中文比例 0.0 < 0.6，改以視覺讀取」），
     讓畫面說得出「為什麼讀不到」而不是只說讀不到。

3. **母庫查**（B3.1–B3.4）
   - `kb.py` 新增 `search_corpus()`：`doc_kind` filter ＋依完整 S3 key 去重 ＋截 limit。
   - **不加「篩了全空退回不篩」的 fallback**（`_retrieve` 有，母庫查不能有）：
     退了就是把法院裁判書當法規端出去。
   - `corpus.py`：`safe_key` 白名單、`fetch_text`（S3 全文）、`fetch_sidecar`（側檔）。
   - `search_decisions` 在 filter 之外**再過濾一次 `court_ruling`**（雙保險）。

4. **加入即快取全文**（B3.5）
   - `POST /cases/{id}/laws`／`references` 時寫 `body_cached`／`full_cached`。
   - `GET /cases/{id}/laws` **只讀 manifest**，不建 KB client、不打 KB。

5. **B4：手動挑的法規餵回 N4**
   - B4.1／B4.2 由 Epic A 的 `llm/chat.py` 完成，**我要做的是讓它讀得到 manifest**
     （`chat_bridge.load_case_manifest` 讀的路徑就是 `store` 寫的路徑），並用測試釘住
     「兩邊講的是同一個檔案位置與同一組欄位名」——跨 Epic 的隱性契約要有人看著。
   - **B4.3 是我的**：一次草稿 run 之後，把「有進 `laws[]`」與「沒進」分開標。
     判準是 N4 實際檢索結果（`payload["laws"]` 的 `t`／來源），**不是**把 manifest 的
     法規直接塞進 `laws[]`。標記寫回 manifest 的 `laws[].retrieval_status`
     ＋人類可讀的 `retrieval_note`。
   - **紅線**：不得為了讓它出現而塞進 `laws[]`。這條要有一條「反向測試」——
     斷言塞進去這件事沒有發生。

6. **`artifact_id` 接線**
   - `chat_bridge.pipeline_adapter` 現在回 `artifact_id: None`；改成由案件資源層登記後回真值。
   - `GET /cases/{id}/artifacts/{artifactId}` **同時接受 `run-…`**（契約 §4.4 的過渡規則：
     manifest 存在時 `artifacts[].id` 永遠優先，但前端在切換期仍用得動 `run-…`）。

7. **測試**：新模組 `backend/tests/test_dossier.py`，**掛進 `run_all.py` 的 sections**。

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| `kb.py` 與主工作樹衝突 | High | 只加模組層新函式，`git diff --stat` 必須是 `+N/-0`；回報明列 |
| `chat_bridge.py` 是 Epic A 的檔，可能還在改 | Medium | 只改 `artifact_id` 那一處，其餘零行；回報明講請 Epic A 覆核 |
| `S3_KB_BUCKET` 未設、task role 無 S3 權限 | **High** | 缺就 503 並指名變數；**列為待決**，infra 不是本 Epic 的範圍 |
| `pdf_visual` 標 `readable:false` 會不會反而不誠實 | Medium | `readable` 講的是「有沒有文字層」，`note` 要說明管線仍以視覺讀取。兩個欄位分開講，不合成一句 |
| B4.3 被實作成「直接塞進 laws[]」 | **High（誠實紅線）** | 寫一條反向測試釘住「沒被塞進去」，不是只測「有標記」 |
| AWS 為 Workshop 短期金鑰會過期 | Medium | 驗收留時間戳；過期就說過期，**不用 fixture 冒充實測** |
