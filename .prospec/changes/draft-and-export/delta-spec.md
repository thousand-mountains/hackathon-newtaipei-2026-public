# Delta Spec: draft-and-export

> REQ ID 格式：`REQ-{MODULE}-{NUMBER}`
> 本輪只落地 `REQ-EXPORT-*`；`REQ-DRAFT-*`（US-C1／C2）等 Epic A 的
> `generate_decision_draft` 工具合併後再開。

**權威來源是 `proposal.md`（commit `ec95430`）的 US-C3 四條 AC。** 下表是對照，
`REQ-EXPORT-*` 只是把它們拆細到可逐條機檢的粒度，**不取代也不改寫** AC 本身
（CONSTITUTION §9：不得為了讓驗收變綠而改契約／規格）。

| proposal AC | 對應 REQ | 機檢在哪 |
|---|---|---|
| **C3.1** docx 在 Word 開得起來、段落可編輯（python-docx 組，非 HTML 轉檔） | REQ-EXPORT-001 | `backend/tests/test_export.py` `test_docx_is_real_ooxml` / `test_docx_is_editable` |
| **C3.2** pdf 中文不是豆腐字（**沒字型時不報錯，要特別驗**） | REQ-EXPORT-002 | `test_pdf_embeds_cjk_font` / `test_pdf_text_layer_is_chinese_not_tofu` / `test_pdf_without_font_is_hard_failure` |
| **C3.3** 引註跟著出去，不得只匯出白文 | REQ-EXPORT-003 | `test_docx_carries_citations`、`test_pdf_text_layer_is_chinese_not_tofu` 後半 |
| **C3.4** 加字型前後各量一次 build context，與 4.8 MB 基準比 | （非程式碼，記在 commit 與交接） | commit `435873c` 訊息 |

`REQ-EXPORT-004`（輸入防護：format 白名單、路徑穿越、404／409）**proposal 沒有列**，
是我加的。理由：`artifactId` 從 URL 進來會被拼成檔案路徑，不擋等於開放任意檔案讀取；
以及「run 存在但還沒生成草稿」若回一份只有抬頭的空白檔，會被讀成「本案無話可說」。
這是加防線不是改契約，但**請契約擁有者知悉**。

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

### REQ-DRAFT-001: 挑了但檢索沒命中的法規要照實回報

**Description:**
契約 §3.5.2 末段。手動挑的法規是當**查詢詞**餵回 N4，所以「挑了」不等於「會進草稿」。
查不到就是查不到——**這是誠實不是 bug，但畫面要說得出來**，否則承辦人挑了五條、
一條都沒引，只會覺得系統吃掉了他的東西。

**Acceptance Criteria:**
1. 跑完 pipeline 後比對 manifest `laws[]` 與這一輪 N4 實際查到的 `laws[]`，
   差集進 `tool_result.unmatched_laws`（`{id, t, note:"檢索未命中，未進入草稿"}`）。
2. 比對鍵是**法規名的精確相等**（去空白、去 `.txt`）。**不得做模糊／包含比對**——
   「訴願法施行細則」被「訴願法」吃掉的話，任何挑選看起來都命中了。
3. **不得為了讓未命中消失，把挑的法規直接塞進 `laws[]`**（那是「檢索佐證自己」，
   2026-09-05 改成 N4 獨立檢索就是要擋這個）。
4. 全部命中時不得冒出空警示——狼來了會讓真的未命中被忽略。
5. 名字為空的項目不報：那是 manifest 資料壞了，不是檢索沒命中。

**Priority:** High

**未決（需契約擁有者拍板）**：契約 §3.5.2 說「右欄該項標『檢索未命中，未進入草稿』」，
但**沒有定義這個旗標放在哪個欄位**。本輪放在 `tool_result.unmatched_laws`（附加鍵，
不改既有形狀）。若應改放 `GET /api/cases/{id}/laws` 的 `note`，那是 Epic B 的欄位，
需要契約先寫明——**我不自行決定**（CONSTITUTION §9）。

---

## MODIFIED

### REQ-API-APP: `backend/api/app.py` 掛載新 router

**Description:** 只新增一行 `app.include_router(export.router)`，不動任何既有 route。
其他 Epic 正在同一個檔上作業，這裡刻意把改動面縮到一行。

**Priority:** Low

---

## REMOVED

_No removals in this change._
