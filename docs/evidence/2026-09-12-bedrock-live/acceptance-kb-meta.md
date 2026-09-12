✅ 通過　❌ 失敗　⏸ 未驗（需 Bedrock）

| AC | 結果 | 證據 |
|---|---|---|
| health 200 且 live_settings ok | ✅ | HTTP 200、run_mode=bedrock、checks=[('laws_snapshot', True), ('synthetic_cases', True), ('frontend_dist', True), ('live_settings', True)] |
| synthetic-ordinary-01 跑完六節點 | ✅ | HTTP 200、run_mode=bedrock |
| AC10 SSE 6 對 start/done + run_done（加值層） | ❌ | node_start → node_done → run_done |
| AC4 N1 live：12 欄 origin=llm、conf 0–1、model_id 非空 | ✅ | 欄位數=12、全 llm=True、conf 皆 0–1=True、extract model_id 非空=True |
| AC5 N5 live：每句 cite_ids ⊆ N4 ∪ 工具命中 | ✅ | 越界引用：無 |
| AC7 KB recall：cases ≥ 3 且同案型 ≥ 3 | ❌ | cases=0、同案型=0、明細=[] |
| AC8 續跑 n5：N1–N4 相同、只跑 n5/n6 | ✅ | retrieval 相同=True、node_timings=['n5', 'n6']、base_run_id 正確=True |
| AC9 確認後從 n2 續跑不重抽 | ❌ | 續跑失敗：HTTP 502 {"run_id": "run-synthetic-ordinary-01-7532d57aeb30", "status": "failed", "node": "n3", "error": "AttributeError: 'NoneType' object has no attribute 'year'"} |
| AC8b from_node 無 base → 400 | ❌ | HTTP 202：{"run_id": "run-synthetic-ordinary-01-f738575daf82", "status": "running", "result_url": "/api/runs/run-synthetic-ordinary-01-f738575daf82", "events_url": "/api/runs/run-synthetic-ordinary-01-f738575daf82/events"} |
| AC6 C 型封鎖成立（requires_human_conclusion／無 llm 結論／submit_allowed=false） | ❌ | requires_human_conclusion=False、非佔位 llm 結論=無、submit_allowed=False、blockers=[] |
| AC15 上傳 txt → N1 抽取與 fixture 一致、N2 案型一致 | ❌ | 檢查時例外：KeyError: 'class' |

服務模式：`bedrock`、model provider：`bedrock`。⏸ 的項目要等 Bedrock 開通後，對 `RUN_MODE=bedrock`（且 `MODEL_PROVIDER=bedrock`）的服務重跑同一支腳本。
AC11（模型 id 設成不存在值 → 502 且 payload 無 fixture 內容）需另起一個服務實例驗證，證據見 `ac11.md`。
