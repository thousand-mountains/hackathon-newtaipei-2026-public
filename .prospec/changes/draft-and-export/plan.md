# Implementation Plan: draft-and-export（本輪＝US-C3 匯出）

> 需求的權威來源是 `proposal.md`（commit `ec95430`，3 個 US／12 條 AC）。
> 本份只講**怎麼做**與**為什麼這樣做**，不重述也不改寫 AC（CONSTITUTION §9）。

## Overview

承辦人現在看得到草稿、拿不走草稿（前端 `exportDownload()` 只是 toast）。本輪把
契約 §1.5 #23 的 `GET /api/cases/{id}/artifacts/{artifactId}/export?format=pdf|docx`
做成真的檔案下載。

**策略是「三層切乾淨」**，因為這三層的失敗方式完全不同、應該分開驗：

| 層 | 檔案 | 依賴 | 失敗長什麼樣 |
|---|---|---|---|
| 取材（doc[] → `sections[]`） | `backend/orchestrator/artifact_sections.py` | 零第三方 | 引註掉光、`cite_count` 對不上 |
| 組檔（`sections[]` → bytes） | `backend/api/export_render.py` | python-docx／fpdf2 | **豆腐字、不可續編** |
| 傳輸（HTTP、檔名、格式白名單） | `backend/api/export.py` | fastapi | 路徑穿越、`Content-Disposition` 壞掉 |

取材層放 `orchestrator/` 而不是 `api/`，是為了讓它**受「核心路徑零第三方依賴」靜態掃描管轄**
（`backend/tests/run_all.py:319`）——引註怎麼算出來的這件事不該偷渡任何外部套件。
組檔層必須放 `api/`，那是 `DEPENDENCY_EXEMPT_DIRS` 的具名豁免之一。

**關鍵設計決策：**

1. **字型找不到＝硬失敗（503），不降級。** 沒有 CJK 字型時 PDF 產出的是整片 □□□ 而
   **不會丟例外**，HTTP 照樣 200——這是本工作包唯一一個「跑起來像成功」的失敗模式。
   所以字型解析寫成獨立函式，找不到就 raise，並在訊息裡具名列出找過哪些路徑。
2. **字型隨庫走，不靠系統安裝。** `backend/assets/fonts/NotoSansTC-Regular.otf`
   （SIL OFL 1.1，5.68 MB，單一 weight）。理由：apt 裝的 `fonts-noto-cjk` 是 `.ttc`
   集合檔，fpdf2／reportlab 對 `.ttc` 的支援都要指定 `fontNumber`，多一個會在雲上才炸的變數。
   搜尋順序仍保留 env `APPEAL_EXPORT_CJK_FONT` → 隨庫字型 → Linux／macOS 系統路徑，
   讓「忘了 COPY 字型」在本機也能跑，但雲上以隨庫那份為準。
3. **`.docx` 用 `python-docx` 直接組段落**，不經 HTML。樣式只用內建
   `Title`／`Heading 1`／`Normal`，讓承辦人在 Word 裡改得動。
4. **引註兩處都出**：行內 `[L3]` ＋ 文末「引註對照」。只做行內的話，`L3` 對誰
   要回系統才查得到；只做文末的話，看不出是哪一句引的。
5. **`artifactId` → `run_id` 兩條解析**：manifest（Epic B 產）優先；manifest 不在時
   允許 `artifactId` 本身就是 `run-…`。後者寫進 OpenAPI docstring，不是隱藏後門。

## Affected Modules

| Module | Impact | Changes |
|--------|--------|---------|
| `backend/orchestrator/artifact_sections.py` | High（新檔） | `build_sections(payload)` → 契約 §4.4 `{sections, cite_count, title, run_id}` |
| `backend/api/export_render.py` | High（新檔） | `render_docx()`／`render_pdf()`／`resolve_cjk_font()` |
| `backend/api/export.py` | High（新檔） | `APIRouter`：export route ＋ `artifactId`→`run_id` 解析 |
| `backend/api/app.py` | **Low（只加一行）** | `app.include_router(export.router)` |
| `backend/assets/fonts/` | Medium（新資產） | Noto Sans TC Regular OTF ＋ `LICENSE` |
| `backend/requirements.txt` | Low | `python-docx`、`fpdf2` |
| `backend/Dockerfile` | Low | 字型不需額外 COPY（`COPY backend/` 已含），只加註解說明它為何不能被 ignore |
| `backend/tests/test_export.py` | High（新檔） | AC1–AC7 的機檢 |

## Implementation Steps

1. **取材層 `artifact_sections.py`**
   - `doc[]` 的 `ty` 值域實測為 `title`／`meta`／`h`／`p`。`h` 開一個 section；
     `title`／`meta` 進 `_meta`（不當 section）；`p` 的每個 `ss[]` 變一個 block。
   - `cites`：`s["refs"]` 的 `L*`／`C*`／`R*`／`I*` 四種前綴，label 依序查
     `payload["laws"]`／`cases`／`refs`／`issues` 的 `t`；查不到就**照實回 id 當 label**
     並記進 `unresolved`，不靜默丟掉。
   - `cite_count` ＝ 所有 block 的 `cites` 總數（實數）。

2. **組檔層 `export_render.py`**
   - `resolve_cjk_font()`：env → 隨庫 → 系統路徑；全部落空 raise `CJKFontMissing`
     並帶上找過的路徑清單。
   - `missing_glyphs(font, text)`：用 fpdf2 內部的 fontTools cmap 查覆蓋率，
     回傳無字型的字元集合（給 `X-Export-Warning` 用，不阻擋）。
   - `render_docx(view)`：Title → 每個 section 一個 Heading 1 → 每個 block 一段，
     段末接行內 `[L3]`；文末「引註對照」。
   - `render_pdf(view)`：fpdf2 + `add_font(…, resolve_cjk_font())`，同樣的版面。

3. **傳輸層 `export.py` ＋ 一行 `include_router`**
   - `format` 不在 `{pdf,docx}` → 400；`case_id`／`artifact_id` 走白名單 regex → 400。
   - 解析 run → `load_run` → `build_payload` → `build_sections` → render → `Response`。
   - `RunNotFound` → 404；`CJKFontMissing` → 503。
   - `Content-Disposition` 用 RFC 5987 `filename*=UTF-8''…`（檔名含中文）。

4. **測試 `backend/tests/test_export.py`**
   - 走 fixture run，零雲端呼叫。
   - docx：zip 內有 `word/document.xml`；`python-docx` 讀回段落數 > 0；
     append 一段再 save 成功（可續編）。
   - pdf：`%PDF` magic；字型資源名含 CJK 字型；**若本機有 `pdftotext` 就抽文字比對中文原句**，
     沒有就留紅並說明（不假裝驗過）。
   - 引註：docx／pdf 的文字裡都找得到 `[L3]` 與「訴願法第14條」。
   - 字型缺席：monkeypatch 搜尋路徑成空 → 503，不得 200。

5. **量 build context ＋ 交接**
   - 加字型前後各跑一次 `docker build --dry-run` 等價量測（`tar` 掉 `.dockerignore` 排除項後計 bytes），
     兩個數字寫進 commit message 與回報。

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| **豆腐字且不報錯**（HTTP 200 假綠） | **High** | 字型缺席硬失敗 503 + 測試 monkeypatch 驗它真的 503；PDF 實際 rasterize 目視一次 |
| build context 從 4.8 MB 漲回去 | Medium | 選 5.68 MB 的 TC subset（非 16.4 MB 全量）；加字型前後各量一次並記錄 |
| 與 Epic B 的 artifacts CRUD 撞檔 | Medium | 全部新檔且命名帶 `export_`；`app.py` 只加一行 include_router，不動既有 route |
| `python-docx`／`fpdf2` 污染核心路徑 | Medium | 只在 `backend/api/`（具名豁免目錄）import；`run_all.py` 的靜態掃描會擋 |
| `.docx` 不可續編（樣式結構壞） | Medium | 只用內建樣式；測試實際 append + save |
| `artifactId` 路徑穿越 | High | 白名單 regex，比照 `runstore.RUN_ID_RE` 的既有作法 |
