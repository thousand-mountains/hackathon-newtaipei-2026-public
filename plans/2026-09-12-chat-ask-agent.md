# 聊天追問 agent：FastAPI 內建 Strands 單 agent（2026-09-12）

> 讀者：backend-dev（實作）、plan-guardian（驗收）、Pink（只需讀 spec）、Ci（拍板）。
> 契約唯一真實來源：`docs/spec/2026-09-12-chat-honesty-lamps.md`。
> prospec 對應：`.prospec/changes/chat-ask-agent/`（proposal.md 有完整 US 與 Edge Cases）。
> 依 HEAD `7724acf` 分析。**本計畫與 prospec plan.md 不一致時，以本檔為準**（CONSTITUTION §5 指名 `plans/`）。

## 目標

新增 `POST /api/cases/{case_id}/chat`（SSE），讓承辦人在一份已跑完六節點的案子上可以自由追問，系統只用卷內與 KB 檢索回答，**每一則回答底部都有一顆由機械規則判出來的燈**，而且那顆燈不是模型自評。demo 現場評審能看到：問一個查得到的 → 亮「有出處」並列出可點的引用；問一個查不到的 → 系統說查無、亮紅燈、不編一個字號出來。

## 步驟

每步做完 `/opt/homebrew/bin/python3 backend/tests/run_all.py` 必須 exit 0 才進下一步。
**用 homebrew 的 python3，不要用裸 `python3`**——這台機器的 `/usr/bin/python3` 是 3.9，`run_all.py:20` 的版本守衛會直接 exit 1。

- [ ] **S0 開工前置**（10 分鐘）：(a) **開工當下**跑 `git rev-parse HEAD` 記下 `$BASE_SHA`，填進 AC9。**不要在文件裡寫死一個 hash**——`origin/main` 會前進，寫死就會過期（實際發生過兩次：`f5c1813` → `50d36c9` → `5c92e3a`）；(b) ~~`git add backend/tests/test_build_graph.py` 並 commit~~ **已完成**：team lead 已於 commit `f5c1813` 連同 `scripts/build_graph.py` 一起推上 main，並在乾淨 checkout 上復現過 ImportError、補上後 374/374 通過。該 commit 在 `origin/main` 的祖先鏈上。**⚠️ 本地工作樹若還沒 `git pull`，`backend/tests/test_build_graph.py` 會顯示為未追蹤**（複驗實測本地 HEAD 是 `b4366e9`，不含 `f5c1813`）——那是本地落後，**不是 S0 那項沒做**，先 pull 再判斷；(c) `git status` 確認 `backend/api/app.py` 與 `backend/Dockerfile` 的並行改動已落地，本計畫的 `app.py` 行號依 `7724acf`，開工時請用**函式名**定位。
- [ ] **S1 凍結契約**：寫 `docs/spec/2026-09-12-chat-honesty-lamps.md`（端點／五個事件／欄位／四條燈號規則）。**這是 Pink 的 unblocker，排第一。** 寫完立刻在 `#hack-dev` 貼路徑。
- [ ] **S2 純函式層**：`backend/llm/chat.py` 的 `RefBook`、`NUMERIC_Q`、`classify_answer()`。不 import fastapi、不呼叫模型。頂層 import 用 `try/except ImportError` 守衛（比照 `backend/llm/client.py:22-26`）。`tier` 用 `backend/gate/lamps.py:72` 的 `tier_for_lamp()`，**不得**用 `origin_registry.py:154` 的 `tier_of()`（spec §3.0）。
- [ ] **S2b 常數上移**：`REF_PREFIXES` 從 `backend/nodes/n5_draft.py:44` 移到 `backend/retrieval/kb.py`，兩邊 import 同一份。**`backend/llm/chat.py` 不得 import `backend.nodes.*`**——`n5_draft.py` 自己 import `backend.llm.client`，聊天層再 import 它就是層級倒置，而且會把 `backend.orchestrator.*` 整包拉進純函式測試的 import 圖。**不要複製字面值**（會漂移）。**做完立刻跑 AC13 與 AC14**——這兩條是離線靜態檢查，不必等 live。
      驗法分工（實作端定案，AC13／AC14 不改）：**AC13 用 AST 讀原始碼、不 import `n5_draft`**（函式內 import 違反紅線、模組頂層 import 又會把 orchestrator 拉進純函式測試的 import 圖）；**執行期的「同一個物件」比對由 AC14 單獨驗**。靜態與執行期各驗一半，合起來才釘得住。
- [ ] **S3 測試**：`backend/tests/test_chat.py`（stdlib only）＋登記進 `backend/tests/run_all.py` 的 `sections`。
- [ ] **S4 agent 層**：`build_chat_agent(case_payload, refbook, retriever, snapshot)`＋五個 `@tool`＋`backend/llm/prompts/chat_ask.md`。沿用 `client.py` 的 `_load_model("draft")`、`_throttle()`、`_prompt()`、`LLMError`。
      **`read_case` 只從參數進來的 `case_payload` dict 取值**，不得自己呼叫 `build_payload()` 或 `load_run()`——`build_payload()` 在 `backend/orchestrator/graph.py:592`，而 `graph.py:45` import 了全部六個節點，聊天層一碰就把 orchestrator 整包拉進 import 圖（spec §4.0「payload 由呼叫端提供」）。
      **每個工具進入點各呼叫一次 `_throttle()`**（spec §8.3）。
      **寫完立刻跑 AC15**（import 清單的機械檢查），不要等到 S6。
- [ ] **S5 HTTP 層**：`backend/api/chat.py`。503 閘門在開串流之前；SSE headers 照抄 `app.py` 的 `run_events()` 尾端（依 `7724acf` 是 460-464，工作樹已漂移，**用函式名定位**）。
      **這一層負責取資料**：`load_run(run_id)` → `build_payload(state)` → 切出分區 → 當參數餵給 `build_chat_agent()`。orchestrator 與 runstore 的 import 只出現在這個檔。
      **同時做 `?stream=0` 的一次性 JSON 模式**（spec §2.2），它是契約也是備援。
- [ ] **S6 掛載與上線**：`app.py` 掛 router ＋ 檔頭端點表加一列；跑 AC3–AC8、AC11–AC15（**AC13／AC14 在 S2b 已先跑過一次，這裡再跑一次確認沒被後續步驟改壞**）；`infra/cdk/verify.sh` 加第 5 段（開頭要有 `rid` 非空守衛，見 AC10）；重部署後跑五段。
- [ ] **S7（stretch，不在 30h 預算內）**：把同一份 `chat.py` 上 AgentCore Runtime。**做法、開工條件、放棄時刻、驗收全部以 `plans/2026-09-12-agentcore-runtime-chat.md` 為準**，本檔不重述（`CHAT_BACKEND` 預設 `inproc`，不設定就等於它不存在）。乙案走的是 CDK L1 併入現有 stack，**不是** `agentcore launch` 獨立管線。**沒做不影響甲案任何 AC。**

## 驗收條件

每條：跑什麼 → 看到什麼。「應該可以」不算。

| AC | 跑什麼 | 看到什麼 |
|---|---|---|
| AC1 | `/opt/homebrew/bin/python3 backend/tests/run_all.py` | exit 0；輸出含 section「聊天誠實燈號（機械規則，零 LLM）」；既有 9 個 section 與 7 條紅線掃描全綠 |
| AC2 | `/opt/homebrew/bin/python3 -c "import backend.llm.chat"`（用**沒裝 strands** 的 `/opt/homebrew/bin/python3`） | 成功，無 traceback。缺 strands 只在呼叫點 raise `LLMError` |
| AC3 | `test -f backend/api/chat.py && grep -rni 'strands\|_load_model\|_invoke_structured' backend/api/` | 檔案存在（`test -f` 為真）**且** grep 無輸出。⚠️ 原本寫成 `grep ... backend/api/chat.py` 是**假通過**：檔案還沒建立時 grep 印警告到 stderr、stdout 空、exit 2，看起來就是「無輸出」。改成先驗存在，並改掃**整個 `backend/api/`** 且加 `-i`——`Agent(` 大小寫敏感、`import Agent as A` 都會從原版漏掉 |
| AC4 | `RUN_MODE=fixture` 起服務，`curl -s -o /tmp/c.json -w '%{http_code}' -X POST .../api/cases/synthetic-ordinary-01/chat -H 'content-type: application/json' -d '{"run_id":"x","message":"hi"}'` | **503**；`/tmp/c.json` 是 JSON、含 `run_mode` 與 `missing[]`；`grep -c 'event:' /tmp/c.json` 為 0 |
| AC5 | `RUN_MODE=bedrock`，先跑完一個 run 取 `$RID`，再 `curl -N -X POST .../chat -d '{"run_id":"'$RID'","message":"有沒有類似的訴願決定可以參考？"}'` | 依序收到 `event: tool_call`、`event: tool_result`、多筆 `event: token`、一筆 `event: done`；`done.lamp` ∈ `{y,r}`；`done.refs[]` 的每個 `id` 都出現在前面某個 `tool_result.hits[].id` |
| AC6 | 同上，問「本案有沒有引用釋字第 999 號？」 | 回答明說查無；`done.refs` 為 `[]`；`done.lamp == "r"`。**答案裡出現任何「釋字第 999 號」的實質內容即為 P0**（CONSTITUTION §2） |
| AC7 | 同上，問「還剩幾天可以提訴願？」 | `done.lamp == "r"`；`done.redirect.endpoint == "/api/deadline"`；答案**不含**任何天數數字。規則引擎的答案只從 `/api/deadline` 出（CONSTITUTION §4） |
| AC8 | 同上，問「把這句改順一點：本件訴願人所訴各節均無理由。」（用 `context.scope="sentence"` 帶原文） | 收到 `tool_call` 且 `tool == "refine_text"`；`done.lamp == "r"`、`done.origin == "llm"` |
| AC9 | `git diff --stat $BASE_SHA...HEAD -- infra/ backend/requirements.txt backend/Dockerfile`（`$BASE_SHA` 在 S0 記下） | **只有 `infra/cdk/verify.sh` 一列**。⚠️ 兩個陷阱：(a) 不可寫成 `git diff --stat main`——在 main 分支上 commit 之後它恆為空，是假通過；(b) **時序陷阱**：HEAD 還停在 `$BASE_SHA` 時（也就是改動都還沒 commit），三點式 `$BASE_SHA...HEAD` **恆空、必綠**。開工中請改用工作樹版 `git diff --stat -- infra/ backend/requirements.txt backend/Dockerfile`（含未提交改動），**commit 之後再用三點式重跑一次**；(c) **本條只適用甲案必要層（S0–S6）**，見下方範圍說明 |
| AC10 | `./infra/cdk/verify.sh` | 原四段＋新第 5 段全綠。**第 5 段開頭要有 `[[ -n "$rid" ]] \|\| bad "沒有可用的 run_id"`**，否則第 3 段失敗時它會拿空 run_id 去打而看起來像通過 |
| AC11 | live 模式，把 `BEDROCK_MODEL_ID_DRAFT` 改成不存在的 id 後打 chat | 收到 `event: error` 且 **`grep -c 'event: done'` 為 0**。失敗不得偽裝成一則回答（spec §4.6） |
| AC12 | `RUN_MODE=bedrock`，`curl -s -D- -o /tmp/ns.json '.../chat?stream=0' -d '{...}'`，再 `python3 -c "import json;d=json.load(open('/tmp/ns.json'));assert d['lamp'] in ('y','r');assert len(d['events'])>0;print(len(d['events']))"` 與 `grep -c 'event:' /tmp/ns.json` | HTTP 200、`Content-Type: application/json`；`events[]` **非空**（印出筆數）；`grep -c 'event:'` 為 **0**（不得夾帶 SSE 框架字串）。前兩條少一條，回一個空殼就會過（spec §2.2 的降級模式） |
| AC13 | `grep -c '^REF_PREFIXES[[:space:]]*=' backend/nodes/n5_draft.py` 與 `... backend/retrieval/kb.py` | 前者 **0**、後者 **1**（賦值只剩一處）。⚠️ **不要**用「`grep -c 'REF_PREFIXES' n5_draft.py` 為 1」當門檻——搬完之後那個檔仍有 3 處提及（docstring、import、使用），這條門檻會誤報紅 |
| AC14 | `/opt/homebrew/bin/python3 -c "from backend.nodes.n5_draft import REF_PREFIXES as a; from backend.retrieval.kb import REF_PREFIXES as b; assert a is b, (a, b); print('same object')"` | 印出 `same object`。**這是真正釘住的那條**：`is` 比對保證 N5 與聊天用的是同一個物件，不是兩份長得一樣的字面值 |
| AC15 | `/opt/homebrew/bin/python3 -c "import sys;sys.path.insert(0,'.');from backend.tests import test_chat;test_chat.test_chat_module_never_imports_orchestrator_or_nodes();print('PASS')"`（或直接跑 AC1 的 `run_all.py`，`backend/tests/run_all.py:37,500` 已註冊 `test_chat`） | 印出 `PASS`。**AST 版，不是 grep 版。** 它只看 `ast.Import`／`ast.ImportFrom` 節點，所以 (a) docstring 與註解裡提到那兩個套件**不會誤報**——實作端已經踩過一次，`backend/llm/chat.py` 的 docstring 就在解釋「為什麼不准 import」；(b) `import backend.orchestrator.graph as g` 這種 alias 形式擋得住，grep 版擋不住。留著會誤報的 gate，等於逼下一個寫 docstring 的人去改措辭避開關鍵字——**那是資訊失真換綠燈**。 這是 spec §4.0「payload 由呼叫端提供」契約的機械驗法，也是本 change 唯一一條沒有既有紅線掃描覆蓋的架構規則（紅線第 4 條只從 N2／N3／N4／N6 起走，第 3 條對 `backend/llm/` 具名豁免）。注意 `backend/api/chat.py` **含** `build_payload` 是預期行為不是違規——取資料就是它的職責 |

**AC6、AC7 是紅線。** AC6 沒過＝系統生出查無此號的引用；AC7 沒過＝規則引擎的專屬領域被 LLM 侵入。任一條紅就不得進 demo，降級照下表。

live AC（AC5–AC8、AC10–AC12）需要 Bedrock 可用；AC13／AC14／AC15 是離線靜態檢查，任何機器都跑得動。跑不了時一律標「未驗」，**不得以 `MODEL_PROVIDER=openai` 的輸出當證據**（賽制僅限 AWS 基礎模型）。

AC12 的 `?stream=0` 是備援模式，**但它是契約的一部分（spec §2.2），S5 要一起做**——降級路徑臨場才寫就來不及了。

**AC9 的適用範圍：只有甲案必要層（S0–S6）。** S7 的部署改動歸乙案自己的驗收（`plans/2026-09-12-agentcore-runtime-chat.md`）。乙案**兩邊都會動**：`infra/` 的 CDK stack（L1 資源併入現有 stack），以及 `backend/requirements.txt`——它要加 `bedrock-agentcore` SDK，而目前釘的 `boto3~=1.35.0` 沒有那個 client。所以 S7 一旦開工，AC9 的 `infra/` 與 `requirements.txt` **兩半邊都會紅，那是預期行為不是違規**。做 S7 時改跑乙案的驗收，不要回頭用 AC9 判它。

## 備援方案

| 到最遲放棄時刻仍紅 | 降級成 |
|---|---|
| Strands 多輪記憶行為不如預期（tool 歷史格式、訊息累積出錯） | **單輪無記憶**：每次呼叫只帶當前問題＋案件摘要，`done.session_id = null`、`done.memory = "off"`。前端照樣畫對話串，只是系統不記得上一題 |
| `refine_text` 的額外模型呼叫讓回應慢到不能 demo | `refine_text` 退出 tools，改由前端獨立按鈕打同一端點帶 `mode:"refine"`，後端單輪改寫。**燈號規則不變**（永遠紅） |
| KB 檢索在聊天情境 recall 太差（問三題有兩題撈不到相關） | 砍 `search_similar_decisions`，只留 `retrieve_refs` 與 `read_case`。系統仍能回答「卷內怎麼寫」，只是不推薦相似案；**不用降低 `KB_MIN_SCORE` 來湊命中** |
| SSE 在 ALB 後面表現不穩（demo 現場斷流） | 切到 `?stream=0` 的一次性 JSON 回應（spec §2.2，S5 已實作、AC12 已驗），前端偵測到連續兩次拿不到 `done` 就切過去。**不做本地假串流動畫**——那是用 CSS 演一個沒發生的過程 |
| 聊天超過賽制 1 RPS（`_throttle()` 擋不住 agent loop 內部往返） | 在每個工具進入點各呼叫一次 `_throttle()`，一輪多等約 3–5 秒（估計未量測）。**這條要先問 Ci**，見 spec §8.3 |
| live AC 完全跑不了（Bedrock 不可用） | 純函式層（S2、S3）照樣完成並 commit，AC1–AC4、AC13–AC15 綠、**AC5–AC8 與 AC10–AC12 一律標「未驗」**。**demo 不展示聊天**，口徑照實說「聊天端點已實作，本次未取得模型額度驗證」 |
| 整個工作包做不完 | **全砍**。設計稿的問答面板改成靜態說明卡，寫明「本次未實作」。六節點主線不受影響——這個 change 從第一天起就是可拆的 |

## 最遲放棄時刻

T0 = 本工作包開工時刻（wall-clock 依 9/12 現場公告的賽程，**待填**）。

**放棄時刻優先於估時。** 下面每個時點都比估時多留約 5%；到點沒綠就降級，不是「再給 20 分鐘」。

| 關卡 | 到此累計估時 | 最遲放棄時刻 |
|---|---|---|
| S0 前置 | 0.2h | **T0+0.3h** |
| S1 契約凍結 | 0.7h | **T0+0.8h**。過了還沒凍結就直接砍整個工作包——Pink 不能無限期等一份 schema |
| S2–S3 純函式＋常數上移＋測試 | 2.7h | **T0+3.0h** |
| S4–S5 agent＋HTTP＋`?stream=0` | 4.95h | **T0+5.3h** |
| S6 上線＋verify | 6.2h | **T0+6.5h**。到點 live AC 沒綠 → 照備援表逐條降級，不延長 |

S7（AgentCore）：**開工條件與放棄判準一律以 `plans/2026-09-12-agentcore-runtime-chat.md` 為準**，本檔不留第二套數字。（原本寫「距交件 > 6h 才開始」與乙案的「最遲 D2 08:00」不等價——距交件 6 小時可能已經過了 D2 08:00。）

## 預估時數

| 步驟 | 估時 |
|---|---|
| S0 前置（sha／commit 測試／對並行改動） | 0.2h |
| S1 契約凍結 | 0.5h |
| S2 純函式層 | 1.0h |
| S2b `REF_PREFIXES` 上移 | 0.25h |
| S3 測試 | 0.75h |
| S4 agent 層 | 1.25h |
| S5 HTTP 層（含 `?stream=0`） | 1.0h |
| S6 掛載／live／verify | 1.25h |
| **合計（必要層）** | **6.2h** |
| S7 AgentCore（stretch） | 見乙案 plan，5.5h 估值，**不進 30h 預算** |

## 回滾

一個 commit 內完成的四件事，回滾就是反做這幾件：

1. `backend/api/app.py` 拿掉 `include_router` 那一行。
2. 刪 `backend/api/chat.py`、`backend/llm/chat.py`、`backend/tests/test_chat.py`；`run_all.py` 拿掉那一列。
3. `REF_PREFIXES`（S2b）：**列在這裡但不必回退**——它是純搬家、行為不變，`n5_draft.py` 改成 import 之後留著不影響任何既有路徑。真要回退就把賦值搬回 `n5_draft.py:44` 並移除 `kb.py` 的那一行與 `n5_draft.py` 的 import；回退後 AC13／AC14 會紅，那是預期的。**這一項先前漏在清單外，它是唯一動到六節點主線檔的改動，不列會漏回滾。**
4. `infra/cdk/verify.sh` 拿掉第 5 段。

其餘檔案一律未改動，所以**沒有需要還原的既有行為**。重新部署即回到目前線上狀態。

## 這份計畫沒有處理的事（誠實條款）

- **多容器 session**：記憶在進程內 dict，ECS 擴到多台就會失憶。目前單台，不處理。
- **聊天的 Bedrock 成本與 RPS 佔用**未估。賽制對 RPS 的限制是否把聊天一起算進去，**待查**。
- **`refine_text` 的品質**沒有驗收條件。它永遠紅燈，所以系統沒有替它背書；但「改寫得好不好」這件事本計畫不驗，也不宣稱。
- **期間試算面板不存在**：`redirect` 的 CTA 目前沒有對應畫面（實查 `frontend/src/` 零 `POST /api/deadline` 呼叫）。預設把 CTA 改成「捲到左欄程序審查卡」，做互動試算不在本計畫內。
- **數字類關鍵詞清單未經語料校準**，第一版憑常識列。demo 前用十個口語提問實測，漏判就加詞。
