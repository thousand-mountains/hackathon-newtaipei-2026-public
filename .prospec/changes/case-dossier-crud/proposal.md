# case-dossier-crud

案件與卷宗的持久化與 CRUD：`manifest.json` 原子寫、案件清單與彙整開案、files／laws／references／artifacts 四組本案清單的增刪讀、母庫查詢的 filter／去重、手動挑選的法規餵回檢索。

---

## Background

**建案語意（2026-09-12 Ci 拍板）：** 維持「上傳卷證即建案」。`POST /api/cases` 是 multipart（`files[]`），沿用既有端點 `backend/api/app.py:324` 的 `save_upload`，建案時順手寫一份 `manifest.json`。**沒有「先建空案」這個狀態**——前端「新增案件」按鈕＝開上傳對話框，選完檔才真的建案（`docs/handoff/2026-09-12-frontend-contract-v2.md:132-135`）。

**持久化：** 一案一份 `backend/output/cases/{case_id}/manifest.json`，**不加資料庫**（`docs/handoff/2026-09-12-frontend-contract-v2.md:621-657`）。決策理由：要存的是單案幾 KB 的書籤，引 RDS/DynamoDB 要動 VPC、IAM、CDK、migration，是 30 小時內風險最高、收益最低的一件事。三條已知限制：不原子（`runstore.py:36` 現在用 `p.write_text`，需改 tmp + `os.replace`）、多副本會分裂（chat session 本來就是進程內字典）、容器重啟就沒了（2026-09-12 Ci 拍板接受，`runs` 現在也是同樣情況）。

**母庫查詢：**

- 法規與案例都要用 KB server-side metadata filter（`doc_kind`），**filter 是必要條件不是優化**——同一查詢不篩抓 20 筆，法規只佔 1 筆（`docs/handoff/2026-09-12-frontend-contract-v2.md:668-682`）。
- 需開 `dedupe_by_source`（`backend/retrieval/kb.py:534`，預設 `False`），同一部法規會回多個 chunk。
- 法院裁判書（`court_ruling`）本期不納入相似案例（2026-09-12 Ci 拍板）——法院判的、訴願決定是訴願會決定的，混進去等於把法院裁判說成訴願前例（`docs/handoff/2026-09-12-frontend-contract-v2.md:700-706`）。
- 全文一律走 S3 `get_object` 讀完整 `.txt`，**不從 KB chunk 拼**——那會拼出一份殘缺卻看起來完整的法規（`docs/handoff/2026-09-12-frontend-contract-v2.md:686-687`）。

**手動加入的法規要餵回檢索（Ci 拍板 (b)）：** 前端擋門文案原寫「草稿只認右側卷宗裡的東西當來源」，但 N5 取的是 N4 自己檢索的結果，不是 manifest 的本案清單——使用者手動挑的法規 N5 一條都看不到。拍板走 `overrides.n4_query`：把手動加入的法規 `t` join 成單一字串，同時送進 `cited_laws` 與 `extra_case_terms` 兩條通道（`docs/handoff/2026-09-12-frontend-contract-v2.md:513-540`）。查不到的不進草稿，右欄該項要標「檢索未命中，未進入草稁」。

---

## User Stories

### US-B1: 案件清單與開案 [P0]

**作為**陳專員，**我要**左欄看到我手上的案子、點一下就把整個卷宗載出來，**才能**接續昨天沒辦完的案。

- **B1.1** `GET /api/cases` 回 `{id, name, created_at}`，**不是**只有 id 字串。
- **B1.2** `GET /api/cases/{id}` **一次**回 `{case, files, laws, references, artifacts}`，開案只打這一支。
- **B1.3** `PATCH` 改名、`DELETE` 刪案都生效且持久（重啟 process 後仍在；**容器重啟即失是已知且接受的**）。
- **B1.4** `manifest.json` 的寫入是 tmp + `os.replace`，**不是**直接 `write_text`。

**驗**：建案→改名→重啟服務→再讀；B1.4 用 AST 或讀程式碼確認

### US-B2: 上傳卷證 [P0]

**作為**陳專員，**我要**把訴願書、原處分書、答辯書丟進去就開始辦，**才能**不用先填一堆表單。

- **B2.1** 「新增案件」＝上傳卷證建案（multipart），**沒有「先建空案」這個狀態**。
- **B2.2** 不限副檔名。掃描影像／讀不到的檔回 `readable:false` 並在 `note` 說原因，**不得靜默跳過**。
- **B2.3** 上傳一份掃描 PDF、Then UI 標「無法辨讀」，**不得**出現「已上傳，可直接改」這類暗示支援 OCR 的文案。

**驗**：用 `scripts/make_scanned_pdf.py` 造一份掃描 PDF 實跑

### US-B3: 搜尋母庫並加入卷宗 [P1]

**作為**陳專員，**我要**自己搜法規和過去的決定書加進卷宗，**才能**補上 AI 沒想到的依據。

- **B3.1** `GET /api/laws?q=廢棄物` 回的每一筆 `doc_kind == "statute"`。**不加 filter 的話法規只佔 20 筆裡的 1 筆**，所以這條實質在驗 filter 有沒有帶。
- **B3.2** 同一部法規的多個 chunk **收斂成一筆**（`dedupe_by_source` 要開，預設是 False）。
- **B3.3** `GET /api/decisions?q=` 的結果**不含** `doc_kind == "court_ruling"`（法院裁判本期不納入相似案例）。
- **B3.4** `GET /api/laws/{lawId}` 回的 `body` 來自 S3 `get_object` 的完整 `.txt`，**不是** KB chunk 拼起來的。
- **B3.5** 加入後 `GET /cases/{id}/laws` 讀得到，且**不再打 KB**（全文已快取進 manifest）。

**驗**：實打 KB retrieve 比對 `doc_kind`；B3.4 比對字元數與 S3 物件大小

### US-B4: 我挑的法規要影響草稿 [P1]

**作為**陳專員，**我要**我手動挑進卷宗的法規真的被拿去用，**才不會**做了一件事而系統毫無反應。

- **B4.1** Given 卷宗有兩條手動加入的法規、When 生成草稿、Then `overrides.n4_query` 含那兩條的 `t`（join 成**單一字串**，不是陣列）。
- **B4.2** 該查詢詞同時進**兩條通道**（`cited_laws` 與 `extra_case_terms`）。只進前者的話它其實只影響法條清單。
- **B4.3** Given 挑了一條 N4 查不到的法規、Then 它**不會**出現在草稿的引用裡，且右欄該項標「檢索未命中，未進入草稿」。**這條驗的是誠實不是功能**——不得為了讓它出現而直接塞進 `laws[]`。
- **B4.4** 右欄「相關案例」群組標明「參考資料，不影響草稿生成」，**且不得沿用「草稿只認右側卷宗」那句文案**。

**驗**：挑一條冷門法規實跑；讀 `run_meta.overrides` 確認字串

---

## 不做什麼

- 不加 RDS／DynamoDB——`manifest.json` 是書籤清單，不是搜尋索引，搜尋完全不經過它（§4.0）。
- 不做 OCR：掃描影像／讀不到的檔一律回 `readable:false`，不得暗示支援辨識。
- 相似案例（`references`）本期不餵回 N4 查詢——只有法規走 `overrides.n4_query`，決定書沒有對等的查詢詞形式，硬塞會稀釋查詢句（§3.5.2）。
- 不動資料夾／拖曳（純前端 localStorage，§0.2），`PATCH /api/cases/{id}` 不需要 `folder_id`。
- 母庫沒有 `U`（不編輯法規／判決本文）——卷宗成員只有 C/R/D。

---

## 契約對應

| 章節 | 內容 |
|---|---|
| §1.1 | 系統與案件端點、建案語意（上傳卷證即建案） |
| §1.2 | 卷證檔案 C/R/D |
| §1.3 | 相關法規母庫查＋本案清單 C/R/D |
| §1.4 | 相似案例／參考決定母庫查＋本案清單 C/R/D |
| §4.0 | `manifest.json` 持久化設計與三條限制 |
| §4.1 | 卷證檔案 payload、`readable` 判斷 |
| §4.2 | 相關法規 payload、filter／去重／S3 全文 |
| §4.3 | 相似案例 payload、`court_ruling` 排除、`provenance` |
| §3.5.2 | 手動加入法規餵回 N4（`overrides.n4_query`） |
