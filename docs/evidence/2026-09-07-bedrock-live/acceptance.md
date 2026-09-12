✅ 通過　❌ 失敗　⏸ 未驗（需 Bedrock）

| AC | 結果 | 證據 |
|---|---|---|
| health 200 且 live_settings ok | ✅ | HTTP 200、run_mode=fixture、checks=[('laws_snapshot', True), ('synthetic_cases', True), ('frontend_dist', True), ('live_settings', True)] |
| synthetic-ordinary-01 跑完六節點 | ✅ | HTTP 200、run_mode=fixture |
| AC10 SSE 6 對 start/done + run_done（加值層） | ⏸ | fixture 檔位的 POST /runs 同步回 200，沒有 ticket 也就沒有 events_url，無 SSE 可驗（端點 GET /api/runs/{id}/events 本身已實作，見 Task 7b commit 1b0db05；要驗 SSE 需對 RUN_MODE=bedrock 的服務跑，那時 /runs 回 202 帶 events_url） |
| AC4 N1 live：12 欄 origin=llm、conf 0–1、model_id 非空 | ⏸ | 未驗（服務為 fixture 模式，需 Bedrock 開通） |
| AC5 N5 live：每句 cite_ids ⊆ N4 ∪ 工具命中 | ⏸ | 未驗（服務為 fixture 模式，需 Bedrock 開通） |
| AC7 KB recall：cases ≥ 3 且同案型 ≥ 3 | ⏸ | 未驗（服務為 fixture 模式，需 Bedrock 開通） |
| AC8 續跑 n5：N1–N4 相同、只跑 n5/n6 | ✅ | retrieval 相同=True、node_timings=['n5', 'n6']、base_run_id 正確=True |
| AC9 確認後從 n2 續跑不重抽 | ✅ | node_timings=['n2', 'n3', 'n4', 'n5', 'n6']、intake_origin.d2=human |
| AC8b from_node 無 base → 400 | ✅ | HTTP 400："from_node 不是 n1 時必須提供 base_state（base_run_id）" |
| AC6 C 型封鎖成立（requires_human_conclusion／無 llm 結論／submit_allowed=false） | ✅ | requires_human_conclusion=True、非佔位 llm 結論=無、submit_allowed=False、blockers=['citation_missing', 'conclusion_requires_human'] |
| AC15 上傳 txt → N1 抽取與 fixture 一致、N2 案型一致 | ⏸ | 上傳建案 201（case_id 前綴 upload-）；POST runs → HTTP 400："上傳案件沒有可重播的 fixture，只能在 RUN_MODE=bedrock 執行；fixture 模式請選 synthetic- 案例。"。機制正確、live 未驗（需 Bedrock 開通） |

服務模式：`fixture`。⏸ 的項目要等 Bedrock 開通後，對 `RUN_MODE=bedrock`（且 `MODEL_PROVIDER=bedrock`）的服務重跑同一支腳本。
AC11（模型 id 設成不存在值 → 502 且 payload 無 fixture 內容）需另起一個服務實例驗證，證據見 `ac11.md`。
