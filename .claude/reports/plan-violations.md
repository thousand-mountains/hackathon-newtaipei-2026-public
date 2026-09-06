# plan-violations.md — plan-guardian 違規紀錄

| 日期 | 工作包 | 時點 | 缺什麼 | 狀態 |
|---|---|---|---|---|
| 2026-09-07 | bedrock-live-nodes（plans/2026-09-07-bedrock-live-nodes.md） | 開工前 | **§7／AC14 被掏空**：`docs/spec/2026-09-07-bedrock-live-nodes-design.md` L52、L305 已含實際 AWS 帳號 ID 與 KB id 且已進 git；plan L17 自訂「文件不得出現帳號 ID／KB id 實際值」，卻在 Task 9／11 把 AC14 grep 改成 `':!plans' ':!docs/spec' ':!HANDOFF*.md'` 排除掉含值的檔。驗收條件被縮到必過。 | 待修：spec 值改 `<ACCOUNT_ID>`／`<KB_ID>`，grep 不得排除目錄 |
| 2026-09-07 | 同上 | 開工前 | **§2 標示缺口**：Task 5 KB 命中一律 `verified: False`，manifest 對檔在加值層 Task 9；必要層相似案卡沒有「未對資料集驗證」字樣（narrative 在 retriever 可用時把「庫外，未驗證」拿掉了）。 | 待修：Task 5 加明確標示或把 build_manifest 挪進必要層 |
| 2026-09-07 | 同上 | 開工前 | **驗收條件外包給矛盾的草稿 spec**：plan L57「見 spec §8」，但 spec 與 plan 命名不一致（`source_ids`↔`cite_ids`、`facts_text`↔`facts_excerpt`、`structured()`↔`extract_intake()`、AC3 `scripts/check_llm_imports.py`↔`run_all.scan_llm_import_graph`）；AC7「三個 demo 案」但 repo 只有 2 個 synthetic 案且 Task 10 只驗 1 案；spec §5.2 說 PDF 視覺讀是 stretch，plan 把它放必要層 Task 3b（3.0h）。 | 待修：plan 驗收段自足列 AC，或先改 spec |
| 2026-09-07 | 同上 | 開工前 | **CLAUDE.md 衝突順序**：D4「開發期用開發用 AWS」與現行 CLAUDE.md「不借用其他專案的任何 secret」相抵；plan 把 CLAUDE.md 改寫排在最後 Task 11，Task 3–10 的 live 測試會在規矩仍禁止時執行。且賽方「僅供競賽之用」資料集將上傳到非競賽用公司帳號 S3（§6 邊界），需 Claire 明文確認留檔。 | 待確認：拍板紀錄 + 把 CLAUDE.md 改動提前到 Task 1 |
| 2026-09-07 | 同上 | 開工前 | **時數貼頂**：自報 23.5h／上限 24h，buffer 0.5h（非 6h）。前一版 plan 19.5h→一日內 +4h（Task 3b、16、AC15/16）尚未寫任何程式即膨脹。 | 建議先砍，見回覆 |
