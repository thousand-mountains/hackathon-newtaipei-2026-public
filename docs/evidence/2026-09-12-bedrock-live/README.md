# 2026-09-12 真 Bedrock live 驗收

**這是本專案第一次真的呼叫 AWS 基礎模型。** 先前所有 bedrock 檔位的紀錄
（`docs/evidence/2026-09-07-bedrock-live/`）都是程式行為，不是實測結果——當時帳號
Bedrock 未開通（`Operation not allowed`）。

## 執行條件

| 項目 | 值 |
|---|---|
| 帳號 | 賽方帳號（`WSParticipantRole`，臨時 session token，**會過期**） |
| region | us-west-2（賽制限定 us-east-1／us-west-2） |
| `RUN_MODE` | `bedrock` |
| `MODEL_PROVIDER` | `bedrock`（openai provider 已停用） |
| `RETRIEVER` | `lawtable_only` ← **AC7 失敗的原因，見下** |
| KB | Managed KB，資料源 S3 bucket，2,477 筆全索引、0 失敗 |

實際值（profile 名、帳號 ID、KB id、bucket 名、model id）一律只在 `.env`／`~/.aws`，
不進本文（CLAUDE.md 規矩段、CONSTITUTION §7）。

## 指令

```bash
set -a; . ./.env; set +a
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart \
       --with strands-agents --with boto3 -- \
    python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8123
python3 scripts/live_acceptance.py --base http://127.0.0.1:8123 --timeout 600 \
    > docs/evidence/2026-09-12-bedrock-live/acceptance.md
```

腳本 exit code **1**（有 ❌ 項）。結果見 `acceptance.md`。

## 兩個 ❌ 的性質

### AC7（KB recall）：不是 recall 不足，是檢索器沒開

`RETRIEVER=lawtable_only`，相似案通道是 `UnavailableRetriever`，所以 `cases=0` 是
**預期行為**，不是 KB 檢索品質的量測結果。

`live_acceptance.py` 的 ⏸ 判準只看 `run_mode` 與 `model_ids.provider`，沒看 `RETRIEVER`；
依它自己在 docstring 宣告的原則（「沒有可驗的對象，判 ❌ 是說謊」），AC7 在
`RETRIEVER != kb` 時應該標 ⏸ 而不是 ❌。**這是腳本的缺口，不是系統的缺陷。**

之所以沒開成 `kb`：`backend/retrieval/kb.py:37` 的 `_relative_path` 只認 `s3://` 開頭，
而 Managed KB 回的 `_source_uri` 是 `https://{bucket}.s3.{region}.amazonaws.com/...`，
比不中 `^kb/(official|public)/` → `kind="unknown"` → 再被前綴過濾刷掉，**命中會靜默歸零**
（回空 list，不報錯）。翻 `RETRIEVER=kb` 之前必須先修這裡。

### AC8b（`from_node` 無 base → 400）：fixture 與 bedrock 檔位的行為差異

驗證邏輯在 `run_case()` 裡，而 bedrock 檔位是先回 202、再於背景執行，所以：

| 檔位 | 行為 |
|---|---|
| fixture | 同步跑，直接 `400`（AC 期待的） |
| bedrock | `202 running` → 背景拋錯 → `GET /api/runs/{id}` 回 `502 failed`，`error` 為 `ValueError: from_node 不是 n1 時必須提供 base_state（base_run_id）` |

已實測重現（2026-09-12）。驗證沒有失效，只是晚了一步。

`backend/api/app.py:320` 的註解自己講過同一個道理：「案例不存在該回 404，不該先發一張
202 再讓前端輪詢半天換到 502」。`from_node` 的檢查同樣便宜，照該原則應該提到 202 之前。
**本文不代為認定要改程式還是改 AC**，列為待處理。

## 通過的項目

AC4（N1 抽取 12 欄全 `origin=llm`、conf 皆 0–1、model_id 非空）與 AC5（N5 主筆每句
`cite_ids` 無越界引用）**首次以真 AWS 基礎模型通過**，取代 2026-09-07 那份的 ⏸。
AC10（SSE 六對 node_start/node_done + run_done）與 AC15（上傳 txt 抽取一致）同樣
由 ⏸ 轉 ✅。

`acceptance.md` 末尾那句「⏸ 的項目要等 Bedrock 開通後重跑」是腳本的靜態頁尾，
本次已無 ⏸ 項目，該句不適用。
