# Delta Spec: draft-and-export

> REQ ID 格式：`REQ-{MODULE}-{NUMBER}`
> 本輪只落地 `REQ-EXPORT-*`；`REQ-DRAFT-*`（US-C1／C2）等 Epic A 的
> `generate_decision_draft` 工具合併後再開。

## ADDED

### REQ-EXPORT-001: 草稿匯出為 `.docx`（可續編）

**Description:**
`GET /api/cases/{id}/artifacts/{artifactId}/export?format=docx` 回一份用 `python-docx`
**直接組段落**的 OOXML 檔，承辦人在 Word／LibreOffice 開得起來、改得動、能再存檔送簽。
不得走「HTML 轉檔」或「把 PDF 改副檔名」。

**Acceptance Criteria:**
1. `200`；`Content-Type` 為 `application/vnd.openxmlformats-officedocument.wordprocessingml.document`；
   `Content-Disposition: attachment` 且 `filename*=UTF-8''…`。
2. 回傳 bytes 是 zip 且含 `word/document.xml`。
3. `python-docx` 讀回後 `len(doc.paragraphs) > 0`，且 `add_paragraph()` 後 `save()` 成功。
4. 段落樣式只用內建 `Title`／`Heading 1`／`Normal`。

**Priority:** High

---

### REQ-EXPORT-002: 草稿匯出為 `.pdf`（中文不得是豆腐字）

**Description:**
`?format=pdf` 回內嵌 CJK 字型的 PDF。**沒有可用 CJK 字型時，PDF 會整片 □□□ 而不丟例外**——
本需求要求該情況必須是硬失敗，而不是一份看起來成功的爛檔案。

**Acceptance Criteria:**
1. `200`；`Content-Type: application/pdf`；bytes 以 `%PDF` 開頭。
2. PDF 內嵌字型名含所用 CJK 字型（`NotoSansTC`）。
3. `pdftotext` 抽回來的文字包含草稿正文的中文原句（本機有 poppler 時必驗）。
4. 找不到任何 CJK 字型時回 **`503`**，訊息具名列出搜尋過的路徑；**不得回 200**。

**Priority:** High

---

### REQ-EXPORT-003: 引註必須跟著匯出檔出去

**Description:**
契約 §4.4 的 `sections[].blocks[].cites` 必須在兩種格式裡都看得見。只匯出白文等於在
交付的那一刻把「每句都有出處」整個丟掉（CONSTITUTION §2）。

**Acceptance Criteria:**
1. 每個帶 `cites` 的段落，正文尾端有行內標註 `[L3]`（多筆以空格分隔）。
2. 文件最後有「引註對照」區塊，逐列 `L3　訴願法第14條`。
3. label 解析不到時**照實印 id 並標「（未解析）」**，不得靜默省略。
4. `cite_count` 由 payload 實數計算，不得沿用設計稿寫死的 `14`。

**Priority:** High

---

### REQ-EXPORT-004: 匯出端點的輸入防護

**Description:**
`artifactId` 之後會從 URL 進來，不擋就等於讓呼叫端指定任意檔案路徑
（比照 `backend/orchestrator/runstore.py` 的 `RUN_ID_RE` 既有作法）。

**Acceptance Criteria:**
1. `format` ∉ `{pdf, docx}` → `400`。
2. `case_id`／`artifactId` 不合白名單 regex → `400`；`../` 一律擋下。
3. 找不到對應的 run → `404`。
4. run 存在但尚未產出草稿（`doc[]` 為空）→ `409` 並說明要先生成草稿，不回一份空白檔。

**Priority:** High

---

## MODIFIED

### REQ-API-APP: `backend/api/app.py` 掛載新 router

**Description:** 只新增一行 `app.include_router(export.router)`，不動任何既有 route。
其他 Epic 正在同一個檔上作業，這裡刻意把改動面縮到一行。

**Priority:** Low

---

## REMOVED

_No removals in this change._
