# Tasks: case-dossier-crud

**Input**: `proposal.md`（4 US／16 AC）、`plan.md`、`delta-spec.md`
**測試入口**：`python3 backend/tests/run_all.py`（**不是 pytest**）。新測試**必須掛進 `run_all.py`**。
**約束**：不引入任何新第三方套件。

## Format: `[ID] [P?] Description → 對應 AC`

---

## Phase 1：持久化（B1.3／B1.4）— ✅ 已完成

- [x] T1 `settings.CASES_DIR` → 基礎
- [x] T2 `store._atomic_write_json`（tmp + fsync + `os.replace`，`dump` 為測試接縫）→ **B1.4**
- [x] T3 `store` 的 load/save/ensure/add_items/remove_item/rename/delete_case/set_latest_run → **B1.3**
- [x] T4 測試：中途失敗不留半份、跨 process 讀回、去重冪等 → **B1.4／B1.3**

## Phase 2：母庫查（B3.1–B3.4）— ✅ 已完成

- [x] T5 `kb.search_corpus()`（純增量，filter ＋去重 ＋**無 fallback**）→ **B3.1／B3.2**
- [x] T6 `corpus.py`：`safe_key`／`fetch_text`／`fetch_sidecar`／四支查詢 → **B3.3／B3.4**
- [x] T7 測試：假 client 驗 filter 有帶、驗去重、驗無 fallback、驗 key 白名單

## Phase 3：端點（B1.1／B1.2／B2.1／B3.5）— ✅ 已完成

- [x] T8 `api/dossier.py` 19 支端點 → **B1.1／B1.2／B3.5**
- [x] T9 `app.py`：掛 router、`GET /api/cases` 補欄位、建案寫 manifest → **B1.1／B2.1**
- [x] T10 測試：AST 驗端點清單逐字相同、母庫只有 GET、成員沒有 U
- [x] T11 掛進 `run_all.py`

## Phase 4：本輪新增（rebase 讀到完整 AC 後補的缺口）

- [x] T12 **`readable` 修正**：`pdf_visual`（掃描影像、無文字層）要回 `false`，
      `note` 帶 `route_documents` 給的原因 → **B2.2／B2.3** ~15 lines
- [x] T13 **B4.3 檢索未命中標記**：草稿 run 之後把 manifest 的 `laws[]` 分成
      「有進檢索結果」與「檢索未命中，未進入草稿」，寫 `retrieval_status`＋`retrieval_note`
      → **B4.3** ~70 lines
- [x] T14 **B4.3 反向測試**：斷言「未命中的法規**沒有**被塞進 `payload["laws"]`」
      ——這條驗的是誠實不是功能 → **B4.3** ~40 lines
- [x] T15 **跨 Epic 契約測試**：`chat_bridge.load_case_manifest` 讀的路徑與欄位名
      必須等同 `store` 寫的 → **B4.1／B4.2 的前提** ~35 lines
- [x] T16 **`artifact_id` 接線**：`chat_bridge.pipeline_adapter` 回真的 artifact id；
      `GET .../artifacts/{id}` 同時接受 `run-…`（契約 §4.4 過渡規則）~45 lines
- [x] T17 **B3.5 實證**：`GET /cases/{id}/laws` 不打 KB（注入會爆的 client 證明沒被呼叫）~25 lines

## Phase 5：實跑驗收（每條都要貼數字，湊不出來就留紅）

- [x] T18 **B3.1**：不帶 filter 抓 20 筆時法規佔幾筆 vs 帶了之後 10 筆全為 statute —— 兩個數字都要
- [x] T19 **B3.2**：未去重 N 筆 → 去重後 M 筆
- [x] T20 **B3.3**：`GET /api/decisions` 的 `doc_kind` 集合
- [x] T21 **B3.4**：`body` 的 utf-8 位元組數 == S3 `ContentLength`
- [x] T22 **B2.2／B2.3**：用 `scripts/make_scanned_pdf.py` 造掃描 PDF 實跑，看 `readable` 與 `note`
- [x] T23 **B1.3**：建案→改名→**重啟 process**→再讀
- [x] T24 **B4.1／B4.2**：讀 `run_meta.overrides` 確認 `n4_query` 是**單一字串**且兩條通道都收到
- [x] T25 **B4.3**：挑一條冷門法規實跑，確認它沒進草稿引用、且右欄標了「檢索未命中」

---

## Summary

| Item | Count |
|------|-------|
| Total tasks | 25 |
| 已完成（rebase 前） | 11 |
| 本輪要做 | 14 |

## Commit 切分

| Commit | 內容 | Task |
|---|---|---|
| （已提交）| prospec 文件／持久化／母庫查／端點 | T1–T11 |
| 6 | `fix(dossier)`：掃描 PDF 的 readable 與原因 | T12、T22 |
| 7 | `feat(dossier)`：B4.3 檢索未命中標記 ＋ 反向測試 | T13、T14、T25 |
| 8 | `feat(dossier)`：artifact_id 接線 ＋ 跨 Epic 契約測試 | T15、T16、T17 |
| 9 | `docs(prospec)`：plan／tasks 對齊完整 AC | 本檔 |
