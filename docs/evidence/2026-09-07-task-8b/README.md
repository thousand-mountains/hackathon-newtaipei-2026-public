# Task 8b 驗證證據：SSE 逐節點進度、每卡重新產生列

日期 2026-09-07。程式碼：`prototype/static/app.js`（`postRun()` 的 SSE 分支、`wireRegenBar()`）、
`prototype/static/index.tmpl.html`（`#regenbar`）。

手動驗證一律用 **port 8123**，驗完關服務；不清 `backend/output/`。

```bash
# fixture 檔位（/runs 同步回 200，跑不到 SSE 路徑）
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart \
  -- python -m uvicorn backend.api.app:app --port 8123

# bedrock 假設定（/runs 回 202 → 走 SSE；n1 沒有憑證會 run_failed）
RUN_MODE=bedrock AWS_REGION=x BEDROCK_MODEL_ID_EXTRACT=x BEDROCK_MODEL_ID_DRAFT=x \
  uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart \
  --with strands-agents --with boto3 -- python -m uvicorn backend.api.app:app --port 8123
```

| 腳本 | 檔位 | 驗什麼 | 結果 |
|---|---|---|---|
| `verify_regen.py` | fixture | 三顆按鈕的 `from_node`／`base_run_id`／`overrides.n4_query`；n1 重抽後欄位重填、承辦人確認清空、資料來源那一行換 run_id；步驟 5 時間軸容忍缺格；離線模式整列真的看不見 | exit 0（`regen.json`） |
| `verify_sse.py` | bedrock 假設定 | EventSource 真的開起來、收到 `node_start` 與 `run_failed`、`postRun` 往外拋且訊息講出是哪個節點 | exit 0（`sse-run-failed.json`、`sse-run-failed.png`） |
| `verify_sse_happy.py` | 替身 | 沒有 AWS 憑證跑不出成功的 202 執行，所以把 `fetch`／`EventSource` 換成替身直接測 `postRun`：`node_start`→`node_done`（含 `elapsed_ms`）→`run_done`→`close()`→**回頭 GET `result_url`** 才拿到 payload，`L.runId` 只在那時更新 | exit 0（`sse-happy.json`） |
| `prove_hidden.py` | fixture | 證明 `.regenbar[hidden]{display:none}` 是必要的：拿掉那條規則，`hidden` 的那一列 computed display 變回 `flex`（作者樣式壓過 UA 的 `[hidden]`） | `display_without_rule: "flex"` |
| `docs/evidence/2026-09-05-integration/verify_ui.py`（複製到 /tmp 改 port，原檔未改） | fixture | 五步動線回歸，兩個案例 | 皆 exit 0、`js_errors: []` |

`python3 backend/tests/run_all.py`：221/221 全綠（含 `prototype/dist` 可由 `build.py` 完全重現）。

## 截圖

| 檔案 | 內容 |
|---|---|
| `regenbar.png` | live 模式的重新產生列（三顆按鈕＋查詢詞輸入） |
| `regen-n4-done.png` | 帶查詢詞重新檢索完成，提示寫出 run_id、接續的 run 與查詢詞 |
| `regen-n1-confirm-cleared.png` | 從 n1 重抽後：欄位換成新抽取結果、勾選框自動取消、旁邊的說明同步變回「尚未由承辦人確認」 |
| `regen-step5-timeline.png` | 只重跑 n5、n6 之後的步驟 5：「本次重跑 2 節點合計（n5、n6；其餘沿用上一次執行）」 |
| `sse-run-failed.png` | bedrock 假設定下 `run_failed` 的訊息落在徽章 tooltip |

## 已知落差（不在本任務修）

- `run_done`／`timeout` 之後真正接上 bedrock 的順跑路徑沒有實機證據（沒有 AWS 憑證），
  只有 `verify_sse_happy.py` 的替身測試涵蓋前端這一段。
- bedrock 檔位「後端連得上但執行失敗」時，`boot()` 的離線退回訊息寫的是
  「本頁未連上後端 API（…節點失敗…）」——原因描述不準（後端其實連得上）。
  那句話是 `HANDOFF-INTEGRATION.md` 已記錄的口徑，要改要一起改，故留給 tech-lead 裁定。
