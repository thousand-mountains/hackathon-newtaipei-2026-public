# 開發期試跑證據：六節點對真模型（非 AWS）

日期 2026-09-08。分支 `mission/hack-bedrock-agents-20260906`。

> ## ⚠ 這不是賽制驗收證據
>
> 本次執行的 provider 是 **`openai`**（`MODEL_PROVIDER=openai`，開發期調 prompt 用），
> **不是 AWS 服務提供之基礎模型**。賽制僅限 AWS 基礎模型，所以：
>
> - 本目錄的任何結果**不得**寫進 demo、不得當成 AC4／AC5／AC7／AC15 的通過證據
> - `docs/evidence/2026-09-07-bedrock-live/acceptance.md` 那 5 個 ⏸ **仍然是 ⏸**
> - 這裡證明的是「**程式路徑跑得通、守門擋得住真模型**」，不是「系統驗收過了」
>
> payload 自己就講得出這件事：`run_meta.model_ids.provider == "openai"`，
> `run_meta.model_ids_note` 前置了「呼叫的不是 AWS 基礎模型」的警告。

## 怎麼產生的

```bash
set -a; . ./.env; set +a          # RUN_MODE=bedrock MODEL_PROVIDER=openai RETRIEVER=lawtable_only
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart \
       --with "strands-agents[openai]" -- \
  python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8123
curl -X POST http://127.0.0.1:8123/api/cases/synthetic-ordinary-01/runs -H 'content-type: application/json' -d '{}'
curl http://127.0.0.1:8123/api/runs/<run_id>          # 202 → 輪詢
```

`RETRIEVER=lawtable_only`：KB 還沒建（Bedrock 未授權），所以相似案通道不可用，
`cases[]` 為空。**AC7（KB recall）本次完全沒有驗到。**

## 跑出來的東西

| | `run-ordinary.json` | `run-blocked.json` |
|---|---|---|
| 案例 | `synthetic-ordinary-01` | `synthetic-blocked-01`（C 型） |
| state | VERIFIED | VERIFIED |
| node_timings (ms) | n1 16761 / n2 0 / n3 1 / n4 2 / n5 11770 / n6 2 | n1 15950 / n2 0 / n3 1 / n4 0 / n5 6882 / n6 1 |
| 端到端 | ≈ 28.5 秒 | ≈ 22.8 秒 |
| lamp_stats | r 3 / y 0 / g 12 | r 3 / y 0 / g 11 |
| submit_allowed | **false** | **false** |
| blockers | conclusion_like_text_outside_conclusion_slot、cite_id_unsupported、conclusion_requires_human | 同上三項 |
| origin_violations | `[]` | `[]` |

`docs/architecture.md` §5 的「端到端 ≤ 90 秒」在這個檔位下成立（28.5s），
但**那是 OpenAI 的數字，換 Bedrock 必須重量**（architecture:1317 已經寫了這句）。

## 守門擋住了真模型（本次最有價值的部分）

到今天以前，這些防線全部只有 mock 與單元測試踩過。真模型第一次踩下去的結果：

| 防線 | 真模型做了什麼 | 系統怎麼反應 |
|---|---|---|
| 結論段封鎖 | C 型案的 `conclusion` 槽位**只有佔位句**（`origin=human_required`），模型沒有把主文寫進去 | 成立 |
| 主文型語句偵測 | 模型改把結論塞進 `reasoning` 槽（`s6`：「訴願期間未逾越，且不適用訴願法第77條第2款不受理事由」） | 判紅 ＋ `conclusion_like_text_outside_conclusion_slot`（P0） |
| 引用白名單 | 模型引用了 N4 從來沒給過的來源（`s2`：`synthetic-原處分裁處書-建築法.pdf#p1`） | 清除該引用 ＋ 判紅 ＋ `cite_id_unsupported`（P0）→ **這正是 2026-09-08 早上修的那條路徑，真模型第一次踩到** |
| 燈號來源 | — | 每句 `l_origin=rule`，`origin_violations=[]` |
| 送出守門 | — | `submit_allowed=false`，兩案皆是 |

## 兩個發現

### 1. 修掉的 bug：文字欄收得下布林值 → N2 崩潰

**第一次呼叫就炸**，N1 成功、N2 掛掉：

```
{"status":"failed","node":"n2","error":"AttributeError: 'bool' object has no attribute 'strip'"}
```

根因兩層：

- `schemas.FieldValue.value` 是 `str | int | bool | None`。那個聯集是為
  `transit_days`（int）與 `interested_party`（bool）開的，卻**套用在全部十二欄**。
  模型在 `type`（案由）回了一個 `true`，schema 收得下。
- `client.extract_intake` 只驗 `service_method`（白名單）與 `transit_days`（int），
  剩下九個文字欄**零型別檢查**。
- 於是壞值一路流到 `n2_classify.py` 的 `(intake.get("type") or "").strip()` 才炸——
  `or ""` 是 **falsy 守衛不是型別守衛**（`True or ""` → `True`）。同一個函式裡
  `note`／`org` 本來就包了 `str()`，只有 `type` 漏掉。

修法（commit 見下）：schema 的文字欄收窄成 `str | None`（另開 `LooseFieldValue`
給那兩個有容錯轉換的欄位）；`client` 加一道文字欄型別檢查，**bool 直接 `LLMError`**
（不 `str()` 成 `"True"` 混過去——那個字面值會進 N2 案型分類的 haystack），
**int 則 `str()` 接受**（案號寫成數字是無損轉換，不必炸）；`n2_classify` 補上 `str()`。

### 2. 沒修的發現：真模型把最要命的那一欄抽錯，而且 conf 給 1.0

`run-ordinary.json` 的 `intake.d2`（送達日）＝ **`2024-07-20`**，`intake_conf.d2` ＝ **1.0**。

卷證原文寫得很清楚：

```
送達：前開裁處書因未獲會晤應受送達人，於民國 113 年 6 月 13 日寄存於轄區派出所……
```

**正確答案是 6/13，模型給 7/20**（那是提起日 `d3` 的值），`transit_days` 也從 0 變成 2。

後果不是小數字誤差，是**結論翻面**：

| | 送達日 | 起算 | 期滿 | 提起 | 判定 |
|---|---|---|---|---|---|
| 卷證（正確） | 6/13 | 6/14 | 7/13 | 7/20 | **逾期** |
| 模型抽的 | 7/20 | 7/21 | 8/21 | 7/20 | 未逾期 |

這正是 `backend/config/settings.py:151`（判斷卡 7）寫的那條攻擊路徑：
**分類關不掉結論封鎖，日期可以。**

**既有防線有守住**：`intake_origin.d2 == "llm"`（未經承辦人確認），所以結論段維持封鎖、
`submit_allowed=false`。系統沒有讓錯誤的日期變成一份可送出的決定書。

**但 demo 風險是真的**：期間計算卡上那六句 `origin=engine` 的句子全是綠燈、標「可驗算」，
而它們算的是**錯的輸入**。引擎誠實（同輸入必同輸出），錯的是上游。承辦人若不核對
收文頁的日期就往下走，畫面看起來完全可信。

延伸：這次 12 欄裡有 8 欄 conf ＝ 1.0，包含抽錯的 `d2`。prompt 寫著
「抓不到就給低值，不要猜」，**這個模型的 conf 沒有鑑別力**，不能當可信度指標用。

→ 已記入 `backlog.md`（HACK-S-17／S-18）。

## 順手發現、還沒修的謊報

| 位置 | 問題 |
|---|---|
| `backend/config/settings.py` `PROVENANCE.note` | 寫死「本系統目前執行於 fixture（離線重播）模式」，`RUN_MODE=bedrock` 下照樣輸出——payload 的 `provenance.note` 在說謊 |
| `backend/api/app.py:218` | `/api/health` 寫死 `model_ids: None` ＋「fixture 檔位未呼叫任何基礎模型。」，bedrock 檔位下也一樣 |
| `backend/nodes/n2_classify.py` `agreement.note` | 寫死「離線重播檔位：kNN 通道不可用」；kNN 不可用是真的，「離線重播檔位」不是 |
| `backend/orchestrator/graph.py:325-327` | 節點拋例外時只 `emit("run_failed")` 然後 `raise`，`save_run(state)` 在 353 行、只有成功路徑走得到——**失敗的 run 不會落地**，事後查不到崩在哪一節點的完整 state（本次那個 AttributeError 就是這樣沒留下 run json） |

## 追加：HACK-S-21（沒有主文卻可送出）已修

上傳案那次執行（`run-upload-confirmed-overdue.json`）暴露的問題：模型把不受理的
**理由**寫完了（reasoning 5 句，其中一句已寫出「訴願法第77條第2款，應為不受理之
決定」），卻**沒寫主文**，`doc[]` 的 conclusion 槽位一句都沒有，而 `blockers` 是空的、
`submit_allowed` 是 `true`。

N6 原本只有 `empty_draft`（整份全空）這道檢查，擋不住「有內容但沒有結論段」。
已加 `conclusion_missing`（P0 blocker）。C 型案不在此列（本來就沒有主文，由
`conclusion_requires_human` 擋）；整份全空也不在此列（由 `empty_draft` 報）。

驗證方式是拿**當時那次真模型輸出**離線重跑 N6（零模型呼叫）：

```
真模型當時寫出的結論句數: 0
submit_allowed: False
  [P0] conclusion_missing
```

> ⚠ `run-upload-confirmed-overdue.json` 是**修正前**的 payload，裡面 `submit_allowed`
> 仍是 `true`——那份留著當問題的證據，不要當成現行行為。
