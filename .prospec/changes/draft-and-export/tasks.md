# Tasks: draft-and-export（US-C3 匯出）

**Input**: `.prospec/changes/draft-and-export/{proposal,plan,delta-spec}.md`
**Prerequisites**: plan.md、delta-spec.md 已填
**權威 AC**: `proposal.md`（commit `ec95430`）US-C3 的 C3.1–C3.4；
delta-spec 的 `REQ-EXPORT-*` 是它的機檢粒度拆解，對照表見 delta-spec 抬頭。
**US-C1／US-C2 本輪不做**——依賴 Epic A 的 `generate_decision_draft` 工具。

## Format: `[ID] [P?] Description (~lines)`

- **[P]**: 可並行執行（不同檔案，無相互依賴）
- **~N lines**: 預估變更行數

---

## Phase 1: 取材層（零第三方依賴）

- [x] T1 `backend/orchestrator/artifact_sections.py`：`build_sections(payload)` →
      `{title, run_id, sections[], cite_count, unresolved[]}`（契約 §4.4）~150 lines
      - 驗收：REQ-EXPORT-003 AC4（`cite_count` 實數）

## Phase 2: 資產

- [x] T2 [P] `backend/assets/fonts/NotoSansTC-Regular.otf`（5.68 MB）＋ `LICENSE`（SIL OFL 1.1）
      ＋ `README.md`（為什麼選這一份、為什麼不能被 .dockerignore 掉）
- [x] T3 [P] `backend/requirements.txt` 加 `python-docx`、`fpdf2`（釘 minor）

## Phase 3: 組檔層（僅 `backend/api/`，具名豁免目錄）

- [x] T4 `backend/api/export_render.py`：`resolve_cjk_font()` ~60 lines
      - 驗收：REQ-EXPORT-002 AC4（找不到就 raise，不降級）
- [x] T5 `export_render.render_docx(view)` ~80 lines — 驗收：REQ-EXPORT-001
- [x] T6 `export_render.render_pdf(view)` ~90 lines — 驗收：REQ-EXPORT-002 AC1–3
- [x] T7 兩種格式的引註輸出（行內 `[L3]` ＋ 文末「引註對照」）~40 lines
      - 驗收：REQ-EXPORT-003 AC1–3

## Phase 4: 傳輸層

- [x] T8 `backend/api/export.py`：router ＋ `artifactId`→`run_id` 解析
      （manifest 優先、`run-…` 後備）~140 lines — 驗收：REQ-EXPORT-004
- [x] T9 `backend/api/app.py` 加**一行** `include_router` ~1 line — 驗收：REQ-API-APP

## Phase 5: 測試

- [x] T10 [P] `backend/tests/test_export.py`：REQ-EXPORT-001～004 逐條機檢 ~260 lines
- [x] T11 [P] 掛進 `backend/tests/run_all.py` 的測試清單 ~3 lines
- [x] T12 實跑 `python3 backend/tests/run_all.py` 全綠（含零第三方依賴靜態掃描）

## Phase 6: 驗證與交接

- [x] T13 PDF **實際 rasterize 目視**一次（`pdftoppm`），確認不是豆腐字
      —— 這條不能只看 HTTP 200
- [x] T14 `.docx` 用 `python-docx` 讀回驗結構（本機無 Word／LibreOffice，明講是替代驗法）
- [x] T15 加字型前後各量一次 build context，與 4.8 MB 基準比較

---

## Phase 7: US-C1／US-C2（Epic A 合併後）

- [x] T16 確認 Epic A 已實作的範圍：`backend/llm/chat.py` 的 `generate_decision_draft`
      已擋前置 2／3 並做了 `overrides.n4_query` join，`test_chat.py` 有假 pipeline 的測試
- [x] T17 **補契約 §3.5.2 末段**：`unmatched_picks()` ＋ `tool_result.unmatched_laws`
      —— 驗收：REQ-DRAFT-001
- [x] T18 `backend/tests/test_draft_preconditions.py`：走**真** `load_case_manifest`
      與**真**六節點流水線（fixture 檔位），15 條 ~300 lines
- [x] T19 三條變異測試驗斷言不是恆真（永遠回空／模糊比對／塞進 laws[] 各自要紅）
- [ ] T20 **C1.4 前端三個條件的 disable 與說明文字** —— 前端範圍，不在本工作包
- [ ] T21 **未命中旗標放哪個欄位** —— 契約沒定義，待契約擁有者拍板（見 delta-spec）

---

## Summary

| Item | Count |
|------|-------|
| Total tasks | 15 |
| Parallelizable | 4 |
| 本輪不做 | US-C1／US-C2（等 Epic A 的 `generate_decision_draft`） |
