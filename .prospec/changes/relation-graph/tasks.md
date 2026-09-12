# Tasks: relation-graph

一個可驗收單位一個 commit。

## T1 — 純函式核心 `backend/graph/relation.py`
- [ ] T1.1 取值 helper（扁平優先／巢狀回退）+ 個資截斷常數
- [ ] T1.2 五種節點
- [ ] T1.3 四種邊（`quote` / `trigger` / `address` / `cite`）
- [ ] T1.4 `flagged` / `unlinked` / `stats` / 退化 `status:"empty"`
- **驗**：`python3 -m backend.graph.relation --run <id>` 對兩份 fixture 各跑一次

## T2 — 測試 `backend/tests/test_relation_graph.py`
- [ ] T2.1 fixture 抄進 `backend/tests/fixtures/`（僅 `synthetic-`）
- [ ] T2.2 AC1 AST 零 LLM
- [ ] T2.3 AC3 `trigger` 關鍵詞真的在文字裡
- [ ] T2.4 AC4 `cite` 邊數 == `raw` 在 `laws[].t` 命中筆數
- [ ] T2.5 AC5 查無的 citation 進 `flagged`
- [ ] T2.6 AC6 `stats` 與陣列長度一致
- [ ] T2.7 AC7 `SCREENED` 退化
- [ ] T2.8 AC8 個資截斷
- [ ] T2.9 掛進 `run_all.py`（import + 執行清單兩處）
- **驗**：`/tmp/hv312/bin/python backend/tests/run_all.py` 全綠且總數增加

## T3 — 接成 chat 工具
- [ ] T3.1 `chat_bridge.relation_graph_adapter`
- [ ] T3.2 `ChatTools.build_relation_graph` + `TOOL_LABELS` + Strands 包裝
- [ ] T3.3 `backend/api/chat.py` 注入
- [ ] T3.4 `test_chat.py` 節流涵蓋清單
- **驗**：`run_all.py` 全綠；AST 測試確認 `backend/llm/chat.py` 沒有新增禁止的 import

## T4 — 變異測試與契約同步
- [ ] T4.1 每條關鍵斷言各做一次變異，證明會紅
- [ ] T4.2 契約 §3.0 的 ⏳ 轉成已實作
- **驗**：變異後的失敗訊息逐條貼出
