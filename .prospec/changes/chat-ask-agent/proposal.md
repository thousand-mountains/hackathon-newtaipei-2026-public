# chat-ask-agent

在一份**已經跑完六節點**的案子上開一個聊天端點，讓承辦人自由追問，回答只准來自卷內與 KB 檢索，每一則都帶機械判定的誠實燈號。

- **後端現況**：六節點 live 全線可用、已部署到 AWS、`verify.sh` 四條通過。**沒有任何多輪 tool-use 迴圈**——`backend/llm/client.py` 只有兩個一次性結構化呼叫（`extract_intake:203`、`draft_sentences:264`）。
- **前端現況**：`frontend/src/components/ChatPanel.vue` **已經存在**（80 行），有自己的互動模型（選取句 `state.selId`／全文切換 `state.chatAll`／四個改寫 chip），目前以 `frontend/src/store/workbench.js:26` 的 `REWRITE_ENABLED = false` 停用，因為舊版改寫是前端正則字串替換不是模型產出。本 change 就是給它一個真的後端。
- **做完之後**：設計稿的工具清單裡唯一需要新端點的那個補上；其餘由既有 `/runs` SSE 事件或前端既有能力供給，後端不動。（設計稿的「8 個工具」出自 2026-09-12 的新設計接入分析報告，該報告在 scratchpad 不在 repo，**此數字未經 repo 驗證**。）
- **不做**：AgentCore Runtime 部署（維持 Stretch，理由見 Background）。

---

## Background

### 這是一條新的「模型直接對使用者說話」的路徑，不是既有功能的介面開放

六節點的每一句話都經過 N6 守門才出場：燈號由 `backend/gate/lamps.py` 的規則算，模型只寫句子（`backend/nodes/n6_gate.py:143-170`）。**聊天沒有 N6。** 模型的話直接進承辦人眼睛，所以誠實標記必須在聊天層重新建一套，而且同樣是**機械規則、零 LLM 自評**——讓模型自己說「我這句可不可信」，正是 CONSTITUTION §1 要防的形狀。

規則正式版在 `docs/spec/2026-09-12-chat-honesty-lamps.md`，本文件的驗收條件引用它。

### 為什麼不做「逐工具單獨觸發」端點

`RunIn.from_node`／`base_run_id` 只決定**續跑起點**，執行永遠跑到 N6（spec `2026-09-07-bedrock-live-nodes-design.md:54` D6）。要單獨觸發某個工具等於繞過守門，那是新設計不是開放既有介面。

### 推翻的既有拍板

| 既有 | 位置 | 本 change 的處理 |
|---|---|---|
| 「ask 追問 agent」列為不做 | `docs/spec/2026-09-07-bedrock-live-nodes-design.md:39` | **推翻**。理由：新設計稿把自由問答放進主畫面，且 backlog 已有對應 story `HACK-S-3`（`backlog.md:61`）。註：該 story 原寫「Strands 單 agent + **六**工具」且優先序 P2；本 change 收斂為**五個**工具（第六個是什麼，backlog 沒寫，也查不到對應需求），優先序提為 P1。`backlog.md:61` **已同步更新**為五工具、P1（2026-09-12） |
| AgentCore 維持 Stretch | 同上 `:51`（D3） | **不推翻**。聊天 agent 寫成不依賴 FastAPI 的純模組；要託管時**同一份模組**交給乙案，做法／開工條件／驗收一律見 `plans/2026-09-12-agentcore-runtime-chat.md`（走 CDK L1 併入現有 stack，**不是** `agentcore launch` 獨立管線）。甲案本身的部署改動為零 |

### 後端契約現況（實查程式碼，不是憑印象）

| 事實 | 出處 |
|---|---|
| SSE 端點是同步 `def` + starlette threadpool，headers 帶 `Cache-Control: no-store`、`X-Accel-Buffering: no` | `backend/api/app.py:435-465` |
| 事件匯流排在進程記憶體，不持久化、無回放 | `backend/api/events.py` |
| Strands `Agent` + `BedrockModel` 只准出現在 `backend/llm/` | `backend/llm/client.py:1-9`、spec `2026-09-07` D1 |
| 模型載入器 `_load_model(model_kind)`，`model_kind ∈ {extract, draft}` | `backend/llm/client.py:125-156` |
| 既有 `@tool` 寫法（含「查無結果時明令不得引用」） | `backend/llm/client.py:277-291` |
| KB 檢索：相似案走配額查詢，判解／函釋走 `filters={"prefix": REF_PREFIXES}` | `backend/retrieval/kb.py:109-123`、`backend/nodes/n5_draft.py:44,90` |
| 法條查表只回**條號存在性**，沒有條文原文 | `backend/retrieval/lawtable.py:15`（`ARTICLE_TEXT_AVAILABLE = False`） |
| 燈號值域是 `g`／`y`／`r`，前端映射 `g→ok, y→warn, r→bad` | `backend/gate/lamps.py:5-9`、`frontend/src/api/adapt.js:21` |
| `origin` 值域與三層誠實對照 | `backend/config/origin_registry.py:35-45` |
| 測試路徑零第三方依賴，豁免只有 `backend/api/`、`backend/llm/`、`backend/retrieval/kb.py` | `backend/tests/run_all.py:262-263` |
| `run_all.py` 必須能在只有 `python3` 的機器上跑完，所以**沒有任何測試 import 得動 fastapi** | `backend/tests/harness.py:1-7`；實查 `backend/tests/` 零 fastapi import |
| `ORIGIN_TO_TIER["llm"] = TIER_SOURCED`（「有出處」），前提是模型句子**必定**經過 N6 | `backend/config/origin_registry.py:42` |
| `LawTableRetriever.search()` 造的四種 Hit 裡**只有一種** `verified=True` | `backend/retrieval/lawtable.py:51-96` |
| `_throttle()` **擋不住** agent loop 的內部工具往返 | `backend/llm/client.py:76-79`（docstring 自己寫的） |

**倒數第三條決定了架構**：所有誠實判定邏輯必須是 `backend/llm/chat.py` 裡的**純函式**，HTTP 層只負責串流。放進 `backend/api/chat.py` 就永遠測不到——**不是因為靜態掃描會擋**（第一方 import 會放行），是因為 import 那個模組就會 ImportError。

---

## User Stories

### US-1: 承辦人可以對已跑完的案子自由追問卷內法規與案例 [P1]

**作為**承辦人，**我想要**在草稿旁邊直接問「這個爭點有沒有類似案例」，**而不用**自己再開一次檢索。

驗收（跑什麼 → 看到什麼）：

```bash
# RUN_MODE=bedrock，先跑一個案子拿 run_id
RID=$(curl -s -X POST $BASE/api/cases/synthetic-ordinary-01/runs | python3 -c 'import json,sys;print(json.load(sys.stdin)["run_id"])')
# 等到 GET /api/runs/$RID 回 200 後：
curl -N -X POST "$BASE/api/cases/synthetic-ordinary-01/chat" \
  -H 'content-type: application/json' \
  -d "{\"run_id\":\"$RID\",\"message\":\"有沒有類似的訴願決定可以參考？\"}"
```

- 看到至少一組 `event: tool_call` / `event: tool_result`，`tool` ∈ `{search_regulations, search_similar_decisions, retrieve_refs, read_case, refine_text}`
- 看到一串 `event: token`，最後一個 `event: done`
- 選取句由 body 的 `context` 欄位送（spec §2.1），前端不自己拼進 `message` 字串
- `done.refs[]` 每一筆的 `id` 都在前面某個 `tool_result.hits[].id` 裡出現過（**引用必可驗，CONSTITUTION §2**）

### US-2: 每一則回答底部都有一顆燈，而且那顆燈不是模型自己說的 [P1]

**作為**評審，**我想要**知道這句回答是「有出處」還是「請人工判斷」，**並且**知道這個判定不是模型自評。

驗收：

```bash
/opt/homebrew/bin/python3 backend/tests/run_all.py     # 內含 test_chat.py
```

- `classify_answer()` 的單元測試涵蓋四條規則（數字題 / 有 ref 且答案引到 / 有 ref 但答案沒引到 / 無 ref），每條斷言 `(lamp, tier, origin)` 三元組
- **`lamp == "g"` 在任何輸入下都不得出現**（測試明確斷言；理由見 spec §3）
- `classify_answer()` **不呼叫任何模型**，用測試釘而不是用 grep 釘。`backend/tests/test_chat.py` 要有一條測試把 `_load_model` 換成「一被呼叫就 raise」的 stub，然後跑四條燈號規則——**仍須全綠**。
  **patch 目標必須同時蓋兩種 import 形式，並且在一個都沒攔到時讓測試紅**：

  ```python
  import backend.llm.client as _client
  import backend.llm.chat as _chat

  def _boom(*a, **k):
      raise AssertionError("判定路徑不得呼叫模型")

  # ① 先擋空轉：chat 側必須真的握著那個函式的某一種綁定。
  #    ⚠️ 不要斷言 `targets` 非空——`_client._load_model` 一直存在（client.py:125），
  #    那句是永真的，chat 完全沒用到它時照樣綠。要問的是「chat 拿到了嗎」。
  assert hasattr(_chat, "_load_model") or getattr(_chat, "client", None) is _client, \
      "chat 沒有用到 _load_model 的任何綁定形式——這條測試在空轉，不是通過"

  # ② 兩種 import 形式各對應一個綁定，只 patch 一邊會攔不到另一邊：
  #      from backend.llm.client import _load_model           → 綁在 chat 命名空間
  #      from backend.llm import client; client._load_model() → 綁在 client 命名空間
  targets = [(m, "_load_model") for m in (_client, _chat) if hasattr(m, "_load_model")]
  saved = [(m, n, getattr(m, n)) for m, n in targets]
  try:
      for m, n in targets:
          setattr(m, n, _boom)
      ...  # 跑四條燈號規則，仍須全綠
  finally:
      # ③ 一定要還原：in-place 換掉 client._load_model 會污染同批其他測試。
      #    症狀是「單獨跑這支綠、跑全套時別的測試莫名紅或莫名綠」，很難查。
      for m, n, orig in saved:
          setattr(m, n, orig)
  ```
  > ⚠️ 原本寫成「grep `Agent(\|_invoke\|_load_model` 的命中都不在判定函式的行號範圍內」，那**無法機械判定**（要人去比對行號區間），而且 `backend/llm/chat.py` 本來就會有那些字串（agent 層在同一個檔）。換成 stub 測試之後，判定函式若哪天偷偷碰了模型，測試會紅。
  > ⚠️ **只 patch 一個名字會留下假通過路徑**：`from backend.llm.client import _load_model` 會把名字綁進 `chat` 命名空間，此時替換 `backend.llm.client._load_model` **攔不到任何東西**，測試照樣全綠；反之只 patch `chat._load_model` 也擋不住 `client._load_model()` 的呼叫形式。所以兩個都 patch。**擋空轉要斷言 chat 側的綁定，不要斷言 `targets` 非空**——`_client._load_model` 一直存在（`backend/llm/client.py:125`），`assert targets` 是**永真斷言**，chat 完全沒用到它時照樣綠，抓不到空轉。
- 線上實測：US-1 的 curl 輸出裡 `done.lamp` ∈ `{"y","r"}`、`done.tier` ∈ `{"有出處","請人工判斷"}`

### US-3: 承辦人可以請系統潤飾一句草稿，而且潤飾出來的永遠標紅 [P1]

**作為**承辦人，**我想要**把一句拗口的草稿改順，**同時**清楚知道改寫後的版本沒有任何人背書。

> Ci 2026-09-12 拍板：`refine_text` 進 tools，**接受它永遠紅燈**。

驗收：

```bash
curl -N -X POST "$BASE/api/cases/synthetic-ordinary-01/chat" \
  -H 'content-type: application/json' \
  -d "{\"run_id\":\"$RID\",\"message\":\"把這句改順一點\",\"context\":{\"scope\":\"sentence\",\"sent_id\":\"s12\",\"text\":\"本件訴願人所訴各節均無理由。\"}}"
```

- 看到 `event: tool_call` 且 `tool == "refine_text"`
- `done.lamp == "r"`、`done.origin == "llm"`、`done.tier == "請人工判斷"`——**即使該回合也檢索到了 refs**
- 改寫結果**不寫回**任何 `doc[]` 句子：`test -f backend/api/chat.py && test -f backend/llm/chat.py && grep -rn 'save_run' backend/api/chat.py backend/llm/chat.py` **無輸出**（聊天路徑沒有任何寫入 runstore 的呼叫）。**兩個 `test -f` 都要有**——少任何一個，那個檔還沒建立時 grep 就 exit 2、stdout 空，這條在還沒做之前就是綠的。
- 契約紅線改驗這條（可機械判定）：`backend/tests/test_chat.py` 的 `test_chat_module_never_imports_orchestrator_or_nodes`（AST 版，已註冊進 `run_all.py`）通過。**用 AST 不用 grep**：grep 分不出 docstring 與真 import（實作端已踩過一次），也擋不住 alias import（**必須先驗檔案存在**：少了 `test -f`，`backend/llm/chat.py` 還沒建立時 grep 印警告到 stderr、stdout 空、exit 2，這條就會在**還沒做**的時候是綠的——跟原版 AC3 同一個坑。） **無輸出**
  > ⚠️ 原本寫成「`grep 'save_run\|build_payload' backend/api/chat.py` 只出現在唯讀取用」，有兩個毛病：(a) 依 spec §4.0 的 payload 契約，`backend/api/chat.py` **必然**含 `build_payload`（它就是負責 `load_run` → `build_payload` → 切分區的那一層），所以那條會把預期行為判成違規；(b)「只出現在唯讀取用」**無法機械判定**。真正的紅線在 `backend/llm/chat.py` 的 import 清單，不在 api 層。

### US-4: fixture 模式照實拒絕，不演一段假對話 [P1]

**作為**這個產品的設計者，**我想要**系統在沒接模型時說「我現在不能聊」，**而不是**用離線資料湊一段對話。

驗收：

```bash
RUN_MODE=fixture <啟動指令>
curl -s -o /tmp/c.json -w '%{http_code}\n' -X POST http://127.0.0.1:8080/api/cases/synthetic-ordinary-01/chat \
  -H 'content-type: application/json' -d '{"run_id":"x","message":"hi"}'
```

- HTTP **503**，且回應是 JSON 不是 SSE（`Content-Type: application/json`）
- body 含 `run_mode`、`missing[]`（來自 `settings.missing_live_settings()`）與一句照實說明
- **不得**有任何 `event:` 字串出現在回應裡——半開一條串流再道歉，前端會先渲染出一個空白對話泡

### US-5: 部署零改動 [P1]

**作為**負責部署的人，**我想要**這個功能不需要新的 AWS 資源、不改 CDK、不改 IAM。

驗收：

```bash
# $BASE_SHA = 本工作包開工前的 commit，S1 時記下來寫進 plans/
git diff --stat $BASE_SHA...HEAD -- infra/ backend/requirements.txt backend/Dockerfile
```

- 輸出**只有** `infra/cdk/verify.sh` 一列（前提：聊天沿用 `BEDROCK_MODEL_ID_DRAFT`，Ci 已拍板）
- ⚠️ **不要寫成 `git diff --stat main`**：在 `main` 分支上它等於「工作樹 vs HEAD」，commit 之後恆為空，不論改了多少 infra——那是一條因為錯誤理由而通過的檢查
- `infra/cdk/verify.sh` 新增第 5 段後執行，該段通過
- `.env.example` 若新增變數，一律**有預設值**且缺了不影響既有四條驗收

---

## Edge Cases

| 情境 | 預期行為 |
|---|---|
| `run_id` 不存在或還在跑 | 404 / 409，沿用 `_translate` 與 `BUS.status` 的既有語義，**不開串流** |
| `run_id` 屬於別的 `case_id` | 400，訊息指明不一致。不得默默用 run 的內容回答另一個案子 |
| 同一回合多次工具呼叫，KB 每次都從 `kb-1` 重新編號 | 聊天層自己重新編號成 `c1, c2, …` 單調遞增，並在 `tool_result` 回新編號。**這是實際會發生的碰撞**（`backend/retrieval/kb.py:123` 每次 `search` 都重編） |
| 模型回答引用了工具沒回傳的編號 | 該編號從 `refs[]` 剔除，該回合強制 `lamp="r"`，`done.dropped_refs[]` 列出被剔除的（比照 `backend/llm/client.py:318-321` 的既有做法） |
| 問「還剩幾天可以訴願」 | 不由 agent 計算。`done.lamp="r"` + `done.redirect` 指向 `/api/deadline`（spec §3 規則 1） |
| 模型呼叫失敗／逾時 | 發 `event: error` 後關流。**不得**用 `done` 包一句道歉——那會被前端當成一則正常回答並標燈 |
| 串流中客戶端斷線 | 後端結束該回合，不重試、不留半截 session 歷史 |
| session 歷史過長 | 只保留最近 N 回合（spec §4）；超過不報錯，但 `done.session_truncated=true` |
| 賽場網路慢，SSE 中間沉默 > 60 秒 | ALB idle timeout 900 秒（`infra/cdk/lib/appeal-backend-stack.ts:222`），足夠；但每個開著的 SSE 佔一個 threadpool thread（預設 40），demo 量級可接受，**不是通用方案** |

---

## Success Criteria

```bash
# 1) 單元（離線，一台只有 python3 的機器就能跑）
/opt/homebrew/bin/python3 backend/tests/run_all.py          # exit 0，且 section 清單裡有「聊天誠實燈號」

# 2) fixture 拒絕
RUN_MODE=fixture <啟動> && curl -s -w '%{http_code}' -X POST .../chat -d '{...}'   # 503 + JSON

# 3) live 端到端
RUN_MODE=bedrock <啟動> && curl -N -X POST .../chat -d '{"run_id":"...","message":"..."}'
#    → tool_call/tool_result/token/done 齊備；done.refs[] ⊆ tool_result 命中

# 3b) 失敗不得偽裝成回答
#    把 BEDROCK_MODEL_ID_DRAFT 改成不存在的 id 再打
#    → 收到 event: error，且該回合沒有 event: done

# 3c) 降級模式
curl -s -w '%{content_type}' -X POST '.../chat?stream=0' -d '{...}'   # application/json + done 物件

# 4) 引用不可造假（紅線）
#    問一個卷內查無的東西（例：「本案有沒有引用釋字第 999 號？」）
#    → 回答必須說查無，且 done.refs[] 為空、done.lamp="r"

# 5) 部署
git diff --stat $BASE_SHA...HEAD -- infra/ backend/requirements.txt backend/Dockerfile   # 只有 verify.sh
./infra/cdk/verify.sh   # 原四條 + 新的聊天一條全綠
```

**第 4、5 條是紅線。** 第 4 條沒過＝系統會生出查無此號的引用（CONSTITUTION §2，P0）；第 5 條沒過＝這個 change 的前提（零部署風險）不成立，要退回重議。

---

## Related Modules

| 模組 | 關係 |
|---|---|
| `backend/llm/chat.py` | **新建**。純 agent 模組：建 agent、五個工具、ref 編號、誠實判定。**不 import fastapi、不 import `backend.orchestrator.*` 或 `backend.nodes.*`**——`read_case` 的 payload 由呼叫端餵進來（spec §4.0） |
| `backend/api/chat.py` | **新建**。`POST /api/cases/{case_id}/chat`，503 閘門、SSE 串流、`?stream=0`。**唯一碰 runstore／orchestrator 的檔** |
| `backend/api/app.py` | 掛 router 一行 |
| `backend/llm/client.py` | 沿用 `_load_model`、`_throttle`、`LLMError`、`model_ids`；不改行為 |
| `backend/retrieval/kb.py` | 唯讀取用；另收 `REF_PREFIXES`（從 `backend/nodes/n5_draft.py:44` 上移） |
| `backend/retrieval/lawtable.py` | 唯讀取用，不改 |
| `backend/nodes/n5_draft.py` | 只改一行：`REF_PREFIXES` 改成從 `backend/retrieval/kb.py` import |
| `backend/config/origin_registry.py` | `origin` 值域來源，不改 |
| `backend/gate/lamps.py` | 燈號語義來源，不改（聊天不經 N6） |
| `backend/tests/test_chat.py` | **新建**，登記進 `run_all.py` 的 sections |
| `infra/cdk/verify.sh` | 新增第 5 段 |
| `docs/spec/2026-09-12-chat-honesty-lamps.md` | **新建**，Pink 的前端契約 |
| `plans/2026-09-12-chat-ask-agent.md` | **新建**，plan-guardian 驗收用 |

---

## Open Questions

1. **`refine_text` 的實作形狀**：它是一個會再呼叫一次模型的 tool（多一次 Bedrock 呼叫、**多約 3–5 秒，這是估計值，未量測**）。備案是不做成 tool、改成前端獨立按鈕打同一支端點帶 `mode:"refine"`。**本 change 照 Ci 拍板做成 tool**，但若 live 實測延遲不可接受，降級方式寫在 plan 的備援方案。
2. **fixture 模式回 503 而不是 501**：tech-lead 已拍板 503（理由見 spec §2.3），契約凍結。Ci 若要改回 501，**開工前說**。
3. **session 記憶跨 process**：ECS 若擴到多台，in-process dict 會讓同一個對話打到不同容器就失憶。目前單台，**不處理**；列此以免日後被讀成 bug。
4. **1 RPS 節流已定做法，但保護範圍有限**：每個工具進入點各補一次 `_throttle()`（`_throttle()` 本身擋不住 agent loop 的內部往返，見 `backend/llm/client.py:76-79`）。**只保證單一進程不超速**——乙案上線後聊天在另一個容器，甲乙並存時全域仍可能超標。賽制是否把聊天呼叫一起算，**待查**，Ci 確認。
5. **`redirect` 的 CTA 已拍板**：改成「捲到左欄程序審查卡」（那裡已有規則引擎算好的期限與算式）。互動試算面板不存在、本 change 不含。**剩下的未決是誰驗那顆鈕會動**——AC7 只驗 `done.redirect.endpoint`，驗不到前端行為，需 Pink 補一條前端 AC。
6. ~~`backend/tests/test_build_graph.py` 尚未進 git~~ **已解決**：team lead 已於 commit `f5c1813` 連同 `scripts/build_graph.py` 推上 main（乾淨 checkout 復現過 ImportError，補上後 374/374）。**本地工作樹若還沒 `git pull`，那個檔會顯示為未追蹤**，不要誤判成沒做。
7. **`error` 事件新增 `stage: "transport"`**（spec §4.6）：這是給 Pink 的契約變更。原本三個值在「開流之後傳輸失敗」只能硬塞 `internal`，畫面會把網路斷線說成系統內部錯誤。

---

## Constitution Check

| 原則 | 對應 |
|---|---|
| §1 分層誠實 | US-2（機械判定燈號）、US-4（fixture 照實拒絕）、Edge Case 的 `error` 事件不得偽裝成回答 |
| §2 引用必可驗 | US-1 驗收第三條、Success Criteria 第 4 條、ref 編號碰撞的處理 |
| §3 不編造測資 | 驗收一律用既有 `synthetic-*` 案，不新造 |
| §4 規則引擎零 LLM | 數字類問題一律導向 `/api/deadline`，聊天 agent 不得自己算期間（spec §3 規則 1） |
| §5 Plan 先行 | `plans/2026-09-12-chat-ask-agent.md` |
| §7 雲與 secret 隔離 | 零新增環境變數值進 repo；沿用既有 `BEDROCK_MODEL_ID_DRAFT` |
| §8 30 小時紀律 | 備援方案與最遲放棄時刻寫在 plan，不臨場發明 |

---

## Next Steps

| # | 步驟 |
|---|---|
| 1 | 凍結 spec（`docs/spec/2026-09-12-chat-honesty-lamps.md`）→ Pink 據此寫 mock SSE 開工 |
| 2 | `backend/llm/chat.py`：五個工具 + ref 編號 + `classify_answer()` 純函式 |
| 3 | `backend/tests/test_chat.py` + 登記進 `run_all.py` |
| 4 | `backend/api/chat.py` + `app.py` 掛 router + 503 閘門 |
| 5 | live 端到端跑 Success Criteria 第 3、4 條 |
| 6 | `verify.sh` 加第 5 段，重部署後跑全部五段 |
