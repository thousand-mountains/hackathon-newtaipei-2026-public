# Runbook：起後端 + 打真 Bedrock + 調 prompt

> 對象：在本機把 `RUN_MODE=bedrock` 跑起來，驗證現在的 prompt，然後改 prompt 再驗一次。
> 前置事實（2026-09-12）：真 Bedrock 已經打通，證據在 `docs/evidence/2026-09-12-bedrock-live/`。
> 賽方帳號是 `WSParticipantRole` 的**臨時 session token，會過期**——每次開工先做 Step 1。

---

## Step 1　確認 AWS 憑證還活著

```bash
cd <repo root>
set -a; . ./.env; set +a          # 這行之後才有 AWS_PROFILE / AWS_REGION
aws sts get-caller-identity --profile "$AWS_PROFILE"
```

- 回得出 Account／Arn → 憑證有效，往下走。
- `ExpiredToken` / `InvalidClientTokenId` → session token 過期了，**重新取一次賽方的臨時憑證**寫回 `~/.aws/credentials`（或重跑賽方給的登入流程）。程式端不用改任何東西。

順手確認模型叫得動（不確定 model id 有沒有被收回時才需要）：

```bash
aws bedrock list-foundation-models --region "$AWS_REGION" --profile "$AWS_PROFILE" \
  --query 'length(modelSummaries)'
```

> 已知：`opus-4-5` / `opus-4-1` 可用；`opus-5`、`fable-5`、`opus-4-8`、`opus-4-7` 一律 `AccessDenied`（2026-09-12 實測）。model id 只在 `.env`，不要寫進程式或文件。

---

## Step 2　起服務（bedrock 檔位）

```bash
python3 prototype/build.py        # 只有動過 prototype/static 或 data 才需要

set -a; . ./.env; set +a
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart \
       --with strands-agents --with boto3 -- \
    python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8123
```

| 位址 | 用途 |
|---|---|
| <http://127.0.0.1:8123/> | 五步動線 UI（頁首徽章要寫「live 後端」） |
| <http://127.0.0.1:8123/api/health> | 真的載快照＋案例＋檢查 live 設定，缺東西回 503 |
| <http://127.0.0.1:8123/api/docs> | OpenAPI |

**先驗健康檢查再做任何事**：

```bash
curl -s http://127.0.0.1:8123/api/health | python3 -m json.tool
```

要看到 `run_mode: "bedrock"`、`live_settings` 那項 `ok: true`。缺變數或缺套件它會直接講是哪一個（`missing_live_settings()` 逐項報名字）。

> 服務**不會偷偷退回 fixture**：設定不齊就 501/502 帶原因（spec D5）。看到 `run_mode: fixture` 就是 `.env` 沒載進來。

---

## Step 3　驗證目前的 prompt（最快的一圈）

調 prompt 不要每次都打 API。**CLI 在 bedrock 模式是同步跑完六節點**，最省事：

```bash
set -a; . ./.env; set +a
python3 -m backend.cli --list                          # 看有哪些合成案例
python3 -m backend.cli --case synthetic-ordinary-01    # 一般案：看 N1 抽取 + N5 主筆
python3 -m backend.cli --case synthetic-blocked-01     # 對抗案：結論段必須被封鎖
```

摘要會印出：期滿日／逾期／77 條款、結論段封鎖、檢索筆數、引用四態計數、燈號、送出與否、三層誠實分層。完整 JSON 寫在 `backend/output/`（gitignored）。

**看 prompt 好不好的四個地方**（從 JSON 撈）：

```bash
J=backend/output/<剛剛那支檔>.json
python3 - "$J" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("model:", d["run_meta"]["model_ids"])            # provider 必須是 bedrock
print("intake:", json.dumps(d["intake"], ensure_ascii=False))
print("conf:", json.dumps(d.get("intake_conf"), ensure_ascii=False))
print("blockers:", d["blockers"])
print("citations:", d["citation_counts"], d["lamp_stats"])
PY
```

| 看什麼 | 判準 | 壞掉代表哪個 prompt 要改 |
|---|---|---|
| `intake` 12 欄的值與 `conf` | 日期是不是抽對（`d2` 送達日、`d3` 提起日最常抽錯）、`service_method` 在白名單內 | `backend/llm/prompts/n1_extract.md` |
| `facts_excerpt` | 是不是逐字照抄，不是摘要 | 同上 |
| `citation_counts.out_of_scope` / 句子的 `unsupported` | 有越界引用＝主筆在編字號 | `backend/llm/prompts/n5_draft.md` |
| `blockers` / `submit_allowed` | 對抗案必須 `False` | 不是 prompt 問題，是守門（`gate/lamps.py`），先別動 prompt |

> ⚠ `d2`/`d3` 抽錯會讓「逾期」翻成「未逾期」，而期間那六句在畫面上是**最像已驗證過**的東西（綠燈、origin=engine）。每次調完 N1 的 prompt，日期一定要逐案核對——這是 2026-09-08 實測打穿過的破口。

---

## Step 4　改 prompt

兩支檔，各自對應一個節點：

| 檔案 | 節點 | 角色 |
|---|---|---|
| `backend/llm/prompts/n1_extract.md` | N1 | 卷證書記官：只抽欄位，不做法律判斷 |
| `backend/llm/prompts/n5_draft.md` | N5 | 決定書主筆：只寫句子，不判斷可信度 |

**不用重啟服務**：`llm/client._prompt()` 每次呼叫都重讀檔案，沒有快取。改完存檔，下一次 `POST /runs` 或下一次 CLI 就吃到新版。

改的時候守住三條紅線（CONSTITUTION）：

1. **不要把判斷搬進 prompt**。期間計算、燈號、引用驗證全是零 LLM 的規則引擎，prompt 裡多寫一句「請判斷是否逾期」就是把可驗算的東西變成猜的。
2. **不要放寬引用**。N5 的 `cite_ids` 白名單是程式強制的（越界會被清空並標 `unsupported`），prompt 只是先講清楚；不要寫成「找不到就引最接近的」。
3. **值不進 prompt**。model id、KB id、帳號、bucket 一律只在 `.env`。

改完最快的回歸：

```bash
python3 -m backend.cli --case synthetic-ordinary-01 && \
python3 -m backend.cli --case synthetic-blocked-01
```

只想重跑 N5、不想重抽 N1（省一次模型呼叫、也保證上游不變），走 API 的續跑：

```bash
# 1) 先跑一次完整的，拿 run_id
curl -s -X POST http://127.0.0.1:8123/api/cases/synthetic-ordinary-01/runs
# bedrock 檔位回 202 {run_id, result_url, events_url}

# 2) 輪詢（409=還在跑、200=結果、502=節點失敗帶原因）
curl -s http://127.0.0.1:8123/api/runs/<run_id>

# 3) 改完 n5_draft.md，只重跑 n5/n6
curl -s -X POST http://127.0.0.1:8123/api/cases/synthetic-ordinary-01/runs \
  -H 'content-type: application/json' \
  -d '{"base_run_id":"<run_id>","from_node":"n5"}'
```

> `from_node` 不帶 `base_run_id` 會失敗（fixture 檔位回 400；bedrock 檔位先回 202、背景拋錯，輪詢時變 502）。
> 續跑不能跨模式：fixture 的 run 接不到 bedrock 的 run，會直接拒絕。

逐節點看事件流（含每個幕僚卡片實際用的 prompt 欄位）：

```bash
curl -N http://127.0.0.1:8123/api/runs/<run_id>/events
```

---

## Step 5　回歸測試（改完一定要跑）

```bash
python3 backend/tests/run_all.py
```

零外部依賴、不需要 AWS、不需要 uv。它同時跑三道靜態掃描：secret／禁用雲端字樣、`prototype/dist` 可重現、測試路徑零第三方相依。**全綠才算改完。**

---

## Step 6　產生 live 驗收證據（要留痕時才做）

```bash
# 服務照 Step 2 起在 8123
python3 scripts/live_acceptance.py --base http://127.0.0.1:8123 --timeout 600 \
  > docs/evidence/$(date +%F)-bedrock-live/acceptance.md
```

- ✅／❌／⏸ 三態。⏸ = 這個模式下沒有可驗的對象（不是失敗）。
- 腳本只有在 `run_mode=bedrock` **且** `model_ids.provider=bedrock` 時才真驗 AC4／AC5／AC7／AC15。
- **兩個已知的 ❌ 不是系統缺陷**（見 `docs/evidence/2026-09-12-bedrock-live/README.md`）：
  - **AC7**：`RETRIEVER=lawtable_only` 時相似案通道本來就回空，腳本沒看 `RETRIEVER` 就判 ❌，是腳本的缺口。
  - **AC8b**：bedrock 檔位先回 202 再背景拋錯，驗證沒失效只是晚一步。

---

## Step 7　要驗 KB（相似案通道 B）時

`.env` 把 `RETRIEVER` 翻成 `kb`（`BEDROCK_KB_ID` 要有值），重啟服務。`kb.py` 的 `https://{bucket}.s3...` URI 解析已於 2026-09-12 修好，命中不再靜默歸零。

驗 recall：

```bash
python3 scripts/live_acceptance.py --base http://127.0.0.1:8123 --timeout 600 \
  > docs/evidence/$(date +%F)-bedrock-live/acceptance-kb.md
```

AC7 要的是 `cases ≥ 3` **且**「同案型 ≥ 3」。2026-09-12 那次是 `cases=5、同案型=0`——檢索得到東西但案型全不對，那是**檢索品質**問題（chunk／metadata／query 組法），不是 prompt 問題，別去改 `n5_draft.md`。

重建 KB（換帳號或資料更新時）：`python3 scripts/ingest_kb.py`。

---

## 疑難排解

| 症狀 | 原因 | 處置 |
|---|---|---|
| `/api/health` 回 `run_mode: fixture` | `.env` 沒載進 shell | 重跑 `set -a; . ./.env; set +a`，**在同一個 shell** 起 uvicorn |
| 503 + `live_settings` not ok | 缺環境變數或缺 `boto3`／`strands` | 照訊息補；套件用 `uv run --with` 帶進去 |
| 501 | `RUN_MODE=local`（未實作）或設定不齊 | 改回 `bedrock`，補齊 §1.2 變數 |
| 502 `模型呼叫失敗` | 憑證過期／model id 沒權限／throttling | Step 1 重驗憑證；`AccessDenied` 就換回 opus-4-5／4-1 |
| `strands 未安裝` | 忘了 `--with strands-agents` | 補上 |
| `抽取結果 service_method=... 不在 (...)` | 模型不照 schema | 這是 N1 prompt 要收緊的訊號，不是 bug |
| run_id 查不到（404/一直 409） | 服務重啟過，事件匯流排在記憶體裡 | 重新 `POST /runs`；demo 期間不要重開服務 |

## 相關檔案

- `backend/DEPLOY.md` — 完整啟動／部署指令與環境變數表
- `.env.example` — 變數名稱與用途（沒有值）
- `CONSTITUTION.md` — 八原則，改 prompt 前先讀 §1 分層誠實、§2 引用必可驗、§4 規則引擎零 LLM
- `docs/evidence/2026-09-12-bedrock-live/` — 目前最新的真模型實測結果
