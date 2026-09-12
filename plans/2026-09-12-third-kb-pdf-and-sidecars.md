# 第三個 KB（ntpc-petition-kb）：PDF 轉 txt ＋ 補 metadata 側檔（2026-09-12）

## Global Constraints

- KB id、bucket 名、帳號 ID **只在 `.env`／`~/.aws`**，不進程式與文件（CLAUDE.md 規矩段、CONSTITUTION §7）。
  本文件提到的 bucket 名由 `.env` 的 `S3_KB_BUCKET` 帶入，指令一律寫 `$S3_KB_BUCKET`。
- 賽方資料集「僅供競賽之用」：**不進 git**，stage 目錄落在 `data/local/`（已 gitignore）。
- 側檔只放**可公開的分類資訊**（來源、文件種類、案型、結果、年度），
  不含內文、不含當事人（CONSTITUTION §6，沿用 `build_kb_metadata.py` 既有標準）。
- **這個 bucket 與 KB 不是我們建的**（命名風格與我們的兩個都不同）。
  任何寫入／刪除／重新 ingestion 前要先確認，否則會蓋掉隊友的狀態。

## 背景：為什麼要做

勘查結果（2026-09-12，`aws s3 ls --recursive`）：

| | 數量 | 格式 |
|---|---|---|
| `kb/official/`（賽方資料集） | 141 | **100% PDF** |
| `kb/public/新北訴願決定書_環保局全量` | 8,486 | txt |
| `kb/public/行政函釋_全量` | 4,520 | txt |
| `kb/public/新北裁判書_環保局全量` | 3,166 | txt |
| `kb/public/相關法規_全量` | 668 | txt |
| **`.metadata.json` 側檔** | **0** | — |

兩個問題：

1. **賽方資料集全是 PDF，而 `kb.py` 看到 PDF 一律丟掉**
   （`metadata._file_type == "PDF"` → `continue`，理由是 Managed KB 對這批 PDF 解析全是亂碼）。
   所以在這個 KB 上，**賽方那 141 份文件等於不存在**——這正是「官方那批永遠撈不到」的根因。
2. **沒有任何側檔**，所以 `category`／`provenance`／`outcome` 全都讀不到。
   相似案卡標題只有檔名，AC7 的同案型量測也沒有 golden 可比。

## 目標

讓這個 KB 的 141 份賽方文件**真的可檢索**，且全庫的分類欄位可篩、可顯示。

## 步驟

### 階段 A：本機（不動雲端，可反覆重跑）

1. 下載 141 個 official PDF 到 `data/local/kb3/pdf/`（約 23 MB）
2. `pdftotext -layout` 轉 txt，沿用 `build_manifest.pdf_to_txt` 的 **CJK 比例 ≥ 0.6** 判準
3. 轉檔品質報告：逐檔列出 CJK 比例，低於門檻的**單獨列出**，不混進上傳批次
4. 產生側檔：141 個 official ＋ 16,840 個既有 public
5. 本機自我檢查：側檔是合法 JSON、值全為字串、10 KB 以內

### 階段 B：雲端（**需確認後才執行**）

6. 上傳 txt 與側檔到 `s3://$S3_KB_BUCKET/kb/`
7. 決定 PDF 去留（見下方「待決」）
8. `start_ingestion_job` 並等完成
9. `.env` 切到這組，重跑門檻量測（不同 KB 必須重量）

## 驗收條件

| # | 條件 | 怎麼驗 |
|---|---|---|
| AC1 | 141 個 PDF 全部轉出 txt | `find data/local/kb3/txt -name '*.txt' \| wc -l` = 141 |
| AC2 | 轉檔品質有據可查 | 每檔的 CJK 比例寫進 `docs/evidence/`，低於 0.6 的逐一列名 |
| AC3 | 側檔數 = 文件數 | 側檔數 == txt 數（本機）；上傳後 S3 上同樣成立 |
| AC4 | 側檔內容合法 | 每個檔 `json.loads` 成功、`metadataAttributes` 值全為 str、< 10 KB |
| AC5 | 不含個資 | 側檔欄位限來源／種類／案型／結果／年度，掃描不得出現內文片段 |
| AC6 | 賽方文件真的檢索得到 | ingestion 後以官方決定書的內文片段查詢，回得到該檔且 `provenance=official` |
| AC7 | 門檻重量過 | 這個 KB 的 `KB_MIN_SCORE` 有自己的量測證據，不沿用另外兩個 KB 的值 |

## 已拍板（2026-09-12，Ci）

1. **PDF 轉完後從 bucket 刪掉。** 理由：留著的話同一份文件有 PDF 與 txt 兩筆進 KB，
   PDF 那筆被 `kb.py` 後過濾丟掉卻仍佔掉 retrieve 名額
   （實測 MANAGED KB 的 `numberOfResults` 是上限不是筆數，被卡掉就少一筆）。
   本機 `data/local/kb3/pdf/` 保留 141 份備份。刪除前逐檔確認對應 txt 已在 bucket 上。
2. **`official/相關法規`（11 份）不入 KB**，與舊 pipeline 的 `OFFICIAL_DIRS` 一致
   （法條文字走規則引擎的查表通道，比檢索準確；本 bucket 另有 `public/相關法規_全量` 668 份）。
   做法：不上傳它們的 txt，PDF 一併刪除。

## 意外發現：案型不必用猜的（2026-09-12 驗證）

原本判斷「公開決定書檔名只有 `案號_結果`，案型無來源，只能靠舊 manifest 的 `case_no`
對接，覆蓋率 27.5%」。**這個判斷是錯的。**

實測（四個批次各抽 40 筆，決定書另抽 300 筆）：**每個 txt 開頭都有結構化檔頭**，
後接一行 `====` 分隔線，四批的欄位分別是：

| 批次 | 檔頭欄位 | 覆蓋率 |
|---|---|---|
| 訴願決定書_環保局全量 | 案號／標題／公布日期／**類別**／原處分機關／**主文結果**／**法條依據** | 100%（clause 99.7%） |
| 行政函釋_全量 | 標題／**分類** | 100% |
| 新北裁判書_環保局全量 | 裁判字號／裁判日期／**裁判案由**／來源 | 100% |
| 相關法規_全量 | 法規名稱／**分類**／最新修正日期 | 100% |

以舊 manifest 的 `category` 當 golden 比對 200 筆：**200/200 一致**（正規化後）。
抽出 7 種案型，舊 manifest 只有 2 種——**這順帶修好了 `kb-min-score.md` 記的
「golden 只有兩類、隨機基線 50%、同案型率沒有鑑別力」那個量測限制。**

案型跨批詞彙統一：異體字交給 `settings.normalize_case_type`（汙→污），
再各自剝掉「違反」前綴與「事件」後綴，賽方的「違反空氣汙染防制法事件」
與公開的「空氣污染防制法」收斂到同一個值，單一 `equals` filter 就能跨兩批。

**這是讀文件自己聲明的欄位，不是從內文推論**（CONSTITUTION §1）：
分隔線之後的內文一個字都不進側檔。

## 備援方案

轉檔若大量亂碼（CJK 比例普遍 < 0.6），代表這批 PDF 是掃描檔，
`pdftotext` 救不了，要走 OCR（`tesseract` 本機有裝）或 Bedrock 的視覺讀。
該情況下先只處理過得了門檻的那些，其餘列進「已知缺口」，不硬塞。
