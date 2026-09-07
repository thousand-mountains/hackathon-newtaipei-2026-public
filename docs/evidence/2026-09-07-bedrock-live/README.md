# 證據包：六節點接 Bedrock（2026-09-07）

## 這批證據是在什麼條件下產生的

**fixture 模式，不是 live。** 產出當下本機沒有 AWS 憑證、沒裝 strands-agents／boto3，
Bedrock 帳號仍在驗證中，所以無法起 `RUN_MODE=bedrock` 的服務。依 plan 備援方案第一列，
`scripts/live_acceptance.py` 每次拿到 payload 都讀 `run_meta.run_mode`：

- **不是 `bedrock`** → 需要真模型／真 KB 的 AC 標 **⏸ 未驗**，不計入失敗，也絕不寫成通過。
- **不需要模型就能驗的 AC** → 在任何模式都照常真判 ✅／❌。

賽制僅限 AWS 服務提供之基礎模型，**非 AWS 模型（例如 `MODEL_PROVIDER=openai`）的輸出
一律不得當作 AC 證據**，這批證據沒有動用任何非 AWS 模型。

| 檔案 | 內容 |
|---|---|
| `acceptance.md` | `scripts/live_acceptance.py` 的輸出（fixture 模式，exit 0） |
| `ac11.md` | AC11（假 model id → 502、body 不夾草稿）的手動驗證摘錄，來源 Task 7a 報告 |

## 這次真的驗到了什麼

| AC | 結果 | 說明 |
|---|---|---|
| health 200、live_settings ok | ✅ | fixture 模式健康檢查四項全過 |
| 六節點跑得完 | ✅ | `synthetic-ordinary-01` 同步回 200 |
| AC6 C 型封鎖 | ✅ | `requires_human_conclusion=true`、無非佔位 llm 結論、`submit_allowed=false` |
| AC8 從 n5 續跑 | ✅ | `retrieval` 與 base 相同、`node_timings` 只有 n5/n6、`base_run_id` 正確 |
| AC8b `from_node` 無 base | ✅ | 400 |
| AC9 確認後從 n2 續跑 | ✅ | 沒有 n1、`intake_origin.d2 == "human"` |
| AC11 | 手動 | 見 `ac11.md`（來源 Task 7a，2026-09-07） |

## 待 Bedrock 開通後要重跑的

| AC | 為什麼現在驗不了 |
|---|---|
| AC4 N1 抽取 12 欄 origin=llm／conf／model_id | fixture 檔位是模板重播，沒有模型呼叫，也沒有 model_id |
| AC5 N5 每句 cite_ids 不越界 | 草稿非模型即時生成，驗不到真實生成的引用 |
| AC7 KB recall（cases ≥ 3、同案型 ≥ 3） | 相似歷史案通道在 fixture 檔位不可用，`cases` 為空 |
| AC15 上傳 txt → 抽取與 fixture 一致 | 上傳建案回 201，但 `POST runs` 回 400「上傳案件沒有可重播的 fixture，只能在 RUN_MODE=bedrock 執行」。**機制正確、live 未驗** |
| AC11（真 model id 情境） | 現有證據的失敗原因是 `NoCredentialsError`；開通後要改驗「憑證正常但 model id 不存在」 |
| AC10 SSE 六對事件 | 加值層（Task 7b）未做，ticket 沒有 `events_url` |

## 重跑指令（同一支腳本，對 bedrock 服務跑）

```bash
set -a; . ./.env; set +a          # 內含 AWS 憑證與 model id，不進 git
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart \
       --with strands-agents --with boto3 -- \
  python -m uvicorn backend.api.app:app --port 8123 &
sleep 3
python3 scripts/live_acceptance.py --base http://127.0.0.1:8123 \
  > docs/evidence/2026-09-07-bedrock-live/acceptance.md; echo "exit $?"
kill %1
```

`run_mode` 一旦是 `bedrock`，上表的 ⏸ 會自動變成真判的 ✅／❌，不必改腳本。
**期望值：全部 ✅、exit 0**；只要有一條 ❌ 就 exit 1。

本次 fixture 產出用的是同一條指令，只是少了 `--with strands-agents --with boto3`
與 `.env`（fixture 模式不需要雲端設定）。

## 紀律

- 腳本與證據檔內不得出現實際帳號 ID／KB id／model id；本包內的 model id 一律寫成 `x`。
- ⏸ 不是通過。demo 或對外說明時，⏸ 的 AC 必須講成「尚未驗證」。
