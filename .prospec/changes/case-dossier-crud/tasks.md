# Tasks: case-dossier-crud

**Input**: Design documents from `.prospec/changes/case-dossier-crud/`
**Prerequisites**: plan.md, delta-spec.md

## Format: `[ID] [P?] Description (~lines)`

- **[P]**: 可並行執行（不同檔案，無相互依賴）
- **~N lines**: 預估變更行數

---

## Phase 1: 持久化層（無外部依賴，先做）

- [ ] T1 `backend/config/settings.py` 加 `CASES_DIR` ~3 lines
- [ ] T2 `backend/dossier/store.py`：原子寫 `_atomic_write_json`（tmp + fsync + `os.replace`，
      `dump` 為測試接縫）＋ `case_id` 白名單 ~60 lines
- [ ] T3 `backend/dossier/store.py`：`default_manifest`／`load`／`save`／`ensure`／
      `add_items`／`remove_item`／`rename`／`delete_case`／`set_latest_run`／`record_draft_artifact` ~150 lines
- [ ] T4 [P] `backend/tests/test_dossier.py`：原子寫中途失敗、跨 process 讀回、去重冪等 ~120 lines

## Phase 2: 母庫查（要打 AWS）

- [ ] T5 `backend/retrieval/kb.py` **純增量**加模組層 `search_corpus()`（filter ＋ 去重 ＋
      完整 S3 key 當 id ＋ **不退回不篩**） ~55 lines
- [ ] T6 `backend/dossier/corpus.py`：`_safe_key` 白名單、`fetch_text`、`fetch_sidecar`、
      `search_statutes`／`search_decisions`／`get_statute`／`get_decision` ~150 lines
- [ ] T7 [P] `backend/tests/test_dossier.py`：假 client 驗 filter／去重／無 fallback／key 白名單 ~110 lines

## Phase 3: 端點

- [ ] T8 `backend/api/dossier.py`：16 支端點（案件 3＋四組 12＋母庫 4，扣掉 app.py 已有的 `GET /api/cases`） ~330 lines
- [ ] T9 `backend/api/app.py`：`include_router`、`GET /api/cases` 補欄位、建案寫 manifest、
      run 成功寫 `latest_run_id` ~45 lines
- [ ] T10 [P] `backend/tests/test_dossier.py`：端點形狀逐欄對齊契約 §4 ~140 lines

## Phase 4: 收尾與實測

- [ ] T11 `backend/tests/run_all.py` 註冊 `test_dossier` ~3 lines
- [ ] T12 實打 AWS 驗收：filter 前後筆數、去重前後筆數、`decisions` 不含 court_ruling、
      `body` 位元組數 == `ContentLength`、建案→改名→重啟→再讀

---

## Summary

| Item | Count |
|------|-------|
| Total tasks | 12 |
| Parallelizable | 3 |

## Commit 切分（一個可驗收單位一個 commit）

| Commit | 內容 | 對應 task |
|---|---|---|
| 1 | `docs(prospec)`：proposal／plan／delta-spec／tasks | — |
| 2 | `feat(dossier)`：持久化層 ＋ 原子寫 ＋ 測試 | T1–T4 |
| 3 | `feat(retrieval)`：`search_corpus` 母庫查通道 ＋ 測試 | T5, T7（前半） |
| 4 | `feat(dossier)`：母庫全文與側檔（S3）＋ key 白名單 ＋ 測試 | T6, T7（後半） |
| 5 | `feat(api)`：16 支端點 ＋ app.py 接線 ＋ 測試 | T8–T11 |
