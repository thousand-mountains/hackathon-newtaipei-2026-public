# 整合幕僚團新 UX（設計稿 e9aec4b）到 live 後端（2026-09-11）

> 決賽前夜整合計畫。tech-lead 審定的缺口分析 + 契約凍結 + 並行施工切法。
> 讀者：backend-dev、frontend-dev、plan-guardian、Ci。
> 設計稿只存在於 `git show e9aec4b:prototype/dist/index.html`（直接匯出、未經 build.py），下文以 `design:行號` 引用；現行程式以 `檔案:行號` 引用（HEAD `693a9b8`）。

## 目標

按下「啟動幕僚團分析」後，七張幕僚卡**依後端節點事件真的逐張完成**（不是計時器演的）；點任一張完成的卡開彈窗，看到該幕僚的**產出、作業紀錄、提示詞（只有 LLM 節點有）、耗時**，並能從那個節點**重跑到守門員**；規則引擎、法規查核、離線 fixture 三條既有能力原樣保留；`run_all.py` 全綠、`dist/index.html` 可由 `build.py` 重現。

## 四個假設的查證結果

| 假設 | 裁決 | 證據 |
|---|---|---|
| A. `startAgent(a,onDone,dur)` 是計時器模擬，要改接 SSE | **成立** | `design:774-805`：進度靠 CSS `transition` + `setTimeout(finish,dur+500)`，logs 依 `dur*(j+1)/(n+1)` 排程；整份設計稿 **零** `fetch`／`EventSource`／`/api/`。附帶發現：**現行 `app.js` 的 live 模式也是假節奏**——`app.js:580-615` 用 `400+i*1400` 固定排程重播後端給的 logs；SSE 目前只餵步驟 0 的一行進度字（`app.js:120-150` → `#go1hint`），跑完才 `goto(1); runAgents()`（`app.js:510-522`）。所以要改的不只是設計稿，是「run 在步驟 0 跑完才進步驟 1」這個順序。 |
| B. 彈窗要 prompt／log／產出，後端不帶 per-node prompt 與 log | **半成立** | **log 與產出已有**：payload `agents[].{out,logs,node}`（`backend/orchestrator/narrative.py:299-307`、`graph.py:643`）。**SSE 沒帶**：`node_done` 只有 `{node, elapsed_ms, degraded}`（`graph.py:321`），跟 `docs/architecture.md:615-621` 寫的契約（含 `agents`、`narrative`）不一致——這是後端要補的缺口。**prompt 沒有**，而且**只有兩個節點有 prompt 可給**：`backend/llm/prompts/n1_extract.md`、`n5_draft.md`；n2/n3/n4/n6 是純規則／檢索（`docs/architecture.md:176-184`、CONSTITUTION §4），設計稿替七個幕僚各寫的 prompt（`design:720-744`）是虛構文案，**不得移植**。 |
| C. 「單獨重跑某幕僚」對應 `from_node` 續跑，會連帶重跑下游 | **成立** | `graph.py:199-202`「不能停在中間：N6 永遠最後重跑」；spec D6（`docs/spec/2026-09-07-bedrock-live-nodes-design.md:54`）明寫不提供單節點重跑。另外兩個限制：`overrides` 白名單只有 `n4_query`（`app.py:106`、`test_live_plumbing.py:1040-1046` 釘住 `n5_prompt` 必須 raise）→ 設計稿「調整指示後重新生成」（`design:839-851`）**接不到後端**；`base_run_id` 只能是上一次**成功**的 run（`app.js:447-448`）。 |
| D. 版式選擇器純前端即可 | **不成立** | `design:897-998` 的 `TPL.A/B/C.build()` 是**整份決定書連燈號、why、refs 一起寫死**在前端，切換＝丟掉後端 N5 產的句子與 N6 判的燈號，換成前端固定文案——違反「燈號永遠不是前端／模型產的」（`origin_registry.py:159-176`、`app.js:577`）。後端只有一種模板 `decision_v1`（`n5_draft.py:151`），槽位由 `requires_human_conclusion` 決定（`n5_draft.py:57-58`），77／79／81 的分岔在 N3 `screen.art77`（`narrative.py:243-254`）。要做「承辦人選版式→N5 依版式重組」得開 `overrides.template` 白名單＋主文查表＋prompt 槽位＋測試，估 3–4h，**今晚砍**（見 §可砍）。 |

## 幕僚 ↔ 節點（契約的一部分，不動）

`backend/config/settings.py:117-124` `NODE_TO_AGENTS`：n1→clerk、n2→clf、n3→proc、**n4→[law, case]**、n5→draft、n6→qc。七張卡由 `test_contract.py:164-183` 釘死數量與順序；payload 每張卡自帶 `node`（`narrative.py:304`），前端**不要另寫映射表**（離線 `case-demo.json` 的 agents 沒有 `node` 欄，前端保底用 `NODE_TO_AGENTS` 反查，見 F2）。

**4 vs 7 的建議**：9/6「收斂為 4 個 agent」我在本 worktree 查不到原文（`knowledge/team-brain` submodule 是空目錄，未 checkout），依 Ci 給的事實處理。今晚**不改卡片數**：改動要同時動 `settings.py`、`narrative.py`、`test_contract.py`、前端，橫跨兩個工作包、不可並行，估 2h，換來的只是簡報敘事一致。建議簡報口徑照 `docs/architecture.md:170-174`：「實作只有 2 個 LLM 呼叫（N1、N5），七張卡是敘事層」。要不要改，Ci 拍板（見 §待決）。

## 缺口表

| # | UI 元素（設計稿） | 需要的資料 | 後端現況 | 缺什麼／改哪裡 | 估時 |
|---|---|---|---|---|---|
| G1 | 卡片逐張完成、進度 `#agprog`（`design:459-460`、`774-805`） | 每個節點的 start／done 事件 + 該卡的 out/logs | SSE 有 `node_start{node,agents}`／`node_done{node,elapsed_ms,degraded}`（`graph.py:304,321`）；**`node_done` 沒帶 agents 與 narrative** | 後端 B1：`node_done` 加 `agents[]`、`narrative{k:{out,logs}}`（欄位**只增不改**）。前端 F2：`runAgents` 改由事件驅動 | B 0.5h／F 1.5h |
| G2 | 彈窗「產出」`#agout`、「作業紀錄」`#aglog`（`design:601-603`） | `agents[k].out`、`agents[k].logs[[text,cls]]` | **已有**（`narrative.py:305-306`；fixture 同形，`prototype/data/case-demo.json`） | 前端 F2：`renderAgDetail` 改讀 `AGENTS.find(k)`，捨棄設計稿的 `agState.logs` 計時器堆疊 | F 0.5h |
| G3 | 彈窗「指示」`#agprompt`（`design:604-605`） | 該節點的 system prompt 原文 | **沒有**。只有 n1/n5 有 prompt 檔（`backend/llm/prompts/*.md`，各 <1KB） | 後端 B2：payload `agents[].prompt: str\|null` + `agents[].prompt_note: str`（非 LLM 節點 `null` + 「規則／檢索節點，無提示詞」；fixture 檔位 `null` + 「離線重播未呼叫模型」）。前端 F2：textarea 改 **readonly**（見 C 的 overrides 限制） | B 0.5h／F 0.25h |
| G4 | 彈窗「耗時」`#agtime`（`design:598,822`） | 該節點 elapsed | **已有**：`run_meta.node_timings[node]`（`graph.py:344`） | 前端 F2 直接讀，不加後端欄位 | F 0.1h |
| G5 | 「重新生成」`#ag_rerun`（`design:609,839-851`） | 從該節點續跑 | **已有**：`POST /runs {base_run_id, from_node, overrides?}`（`app.py:98-123`、`app.js:452-505` 已有三顆按鈕的完整邏輯） | 前端 F2：把 `wireRegenBar` 的 handler 抽成 `regenFrom(from,{query})` 供彈窗共用；彈窗要**明講重跑範圍**（從 nX 到 n6，下游卡片會重算、草稿與燈號會換）；只有 `law`/`case` 卡有「查詢詞」輸入（對應 `overrides.n4_query`）；離線或 `!L.runId` 時按鈕 disabled 並說原因（`app.js:463-466` 既有文案） | F 0.75h |
| G6 | 作業紀錄 `console` 併入彈窗（設計稿拿掉 `design:451-463` 的第二張卡） | 全域三行啟動 log（`app.js:576-578`：案號、資料來源、run_id） | 前端表現層 | 前端 F1：移除 `index.tmpl.html:586-589` 的作業紀錄卡，改一行 `#agsource` 在看板下方（保留「資料來源：後端 run_id …／離線 fixture」這句，**這是分層誠實的外顯**，不能沒了） | F 0.25h |
| G7 | 版式選擇器 `#tplsel`/`#mainsel`/`#cmask`（`design:495-500,587-593,1170-1197`） | 承辦人選版式→N5 重組 | **無**（見假設 D） | **砍**。不搬 `TPL.*`、不搬 `cmask` | 0 |
| G8 | `rebuildSents`（`design:1003-1005`） | — | 現行 `app.js:642-650` 已有等價實作，且**原地改陣列**保住 closure | **不搬**設計稿版本（它重新指派 `SENTS=[]`，會弄斷 `app.js:640-641` 說明的 closure） | 0 |
| G9 | 設計稿拿掉的規則引擎 `computeDeadline`/`applyDeadlineEngine`、`verifyLaw`、內嵌 `case-data`/`snapshot-data` | — | 現行都在（`app.js:671,700,809`；`index.tmpl.html:747-748`） | **不動**。設計稿的 `<script>` 區塊一律不整段覆蓋，只搬 G1–G6 對應片段 | 0 |

## 契約凍結（開工前拍板，兩邊照此寫，不再協商）

### C1. SSE `node_done` 事件（`graph.py:321`）——只加欄位

```jsonc
// event: node_done
{ "run_id": "run-…", "node": "n4", "elapsed_ms": 1234, "degraded": false,
  "agents": ["law", "case"],                        // 新增：= NODE_TO_AGENTS[node]
  "narrative": {                                    // 新增：merge 後的卡片內容（含降級紅 log）
    "law":  {"out": "…", "logs": [["…",""],["…","y"]]},
    "case": {"out": "…", "logs": [["…",""]]} } }
```

`node_start` 維持 `{run_id,node,agents}`（`graph.py:304`）。`run_done`／`run_failed`／`timeout` 不變。續跑時沒重跑的節點**不發事件**（現況，`graph.py:303`），前端據此把上游卡標「沿用上一次執行（未重跑）」。

### C2. payload `agents[]`——只加兩個鍵

```jsonc
{ "k":"draft","ico":"draft","name":"決定書主筆","role":"…","node":"n5","out":"…","logs":[…],
  "prompt": "…n5_draft.md 全文…" ,   // 新增：只有 n1/n5 在 bedrock 檔位有值，其餘 null
  "prompt_note": "模型即時生成所用的系統提示（唯讀；調整提示詞不在本次範圍）" }
// 非 LLM 節點：prompt=null, prompt_note="規則／檢索節點，無提示詞（CONSTITUTION §4）"
// fixture 檔位的 n1/n5：prompt=null, prompt_note="離線重播未呼叫模型，無提示詞可示"
```

`origin_registry.check_payload` 只掃 `doc[]`／`citations[]`（`origin_registry.py:159-176`），`test_contract.py:174-181` 只驗鍵存在——加鍵安全。`prompt` 的 origin 視為 `static`（檔案原文）。

### C3. 前端只用這些欄位

`agents[].{k,name,role,node,out,logs,prompt,prompt_note}`、`run_meta.node_timings`、`run_meta.run_id`、SSE `node_start`／`node_done`／`run_done`／`run_failed`。**不新增任何其他端點**。

## 步驟（工作包）

**兩個工作包不共用任何檔案。** 後端不碰 `prototype/`；前端不碰 `backend/`。

### WP-B（backend-dev）改 `backend/orchestrator/graph.py`、`backend/tests/test_live_plumbing.py`、`docs/architecture.md`

- [ ] B1 `graph.py:321` 的 `node_done` 加 `agents`、`narrative`（從 `merge_agent_narrative` 剛併好的 `agents` dict 取該節點的卡）。
- [ ] B2 `graph.py` 組 `agents_narrative` 之後（`:331` 附近）為每張卡補 `prompt`／`prompt_note`（規則見 C2）；prompt 檔用 `backend/llm/client.py:79 _prompt()` 同一條路徑讀，**不要**讓 `orchestrator/` import `backend.llm`（`scan_llm_import_graph` 只管 N2/N3/N4/N6，但 CONSTITUTION §4 精神一樣）——改成 `pathlib` 直接讀 `backend/llm/prompts/<node>.md` 文字即可。
- [ ] B3 測試：`test_live_plumbing.py` 加兩條——(a) fixture 跑一次，`node_done` for n4 的 `agents==["law","case"]` 且 `narrative` 有兩鍵、每個 `logs` 元素長度 2；(b) fixture payload 每張卡有 `prompt` 鍵且值為 `None`、`prompt_note` 非空；bedrock 檔位不寫真打模型的測試。
- [ ] B4 `docs/architecture.md:612-628` 的 SSE 範例改成與 C1 一致（事件名是 `node_done` 不是 `node.done`，順手修）。
- [ ] B5 `uv run --no-project --python 3.12 --with-requirements backend/requirements.txt python backend/tests/run_all.py` exit 0。

驗收：`RUN_MODE=fixture` 起服務後 `curl -N localhost:8080/api/runs/<rid>/events` 看得到 `event: node_done` 且 data 內含 `"agents"` 與 `"narrative"`（fixture 檔位是同步 200，用 `run_case(..., on_event=…)` 在測試裡驗即可，見 B3）；`GET /api/runs/<rid>` 的 `agents[]` 每張有 `prompt`／`prompt_note`。

### WP-F（frontend-dev）改 `prototype/static/index.tmpl.html`、`prototype/static/app.js`，最後跑 `prototype/build.py`

- [ ] F1 模板：(a) `index.tmpl.html:586-589` 作業紀錄卡 → 一行 `<p class="hint" id="agsource">`；(b) 在 `id="mask"`（`:729`）之前加設計稿的 `agmask` 彈窗（`design:595-612`），`#agprompt` 加 `readonly`，`#agout` 上方多一行 `#agscope`（重跑範圍說明）、`#ag_rerun` 旁加只在 `law`/`case` 顯示的 `<input id="ag-query">`；(c) CSS 補 `design:147-151,161`（done 可點、hover、`.more` 箭頭、`p padding-right`）。**不搬** `tplsel`／`mainsel`／`cmask`。
- [ ] F2 `app.js`：
  - `postRun(body,onProgress,onEvent)` 多一個 `onEvent(kind,data)`，`app.js:131-135` 的 `node_start`／`node_done` handler 轉呼叫它（既有 `say()` 進度字保留）。
  - `$('#go1').onclick`（`:510-522`）live 分支改序：先 `goto(1); renderAgentBoard('pending')` → 再 `runConfirmed()`；`runConfirmed` 把 `onEvent` 接到卡片：`node_start` → 該 `agents[]` 的卡進 `run`；`node_done` → 用事件裡的 `narrative` 填 `out`、記 logs、卡進 `done`、`#agprog` +1；`run_done` 後照舊輪詢拿 payload → `applyLivePayload` → `buildDraft(); buildRefs()`；沒收到事件的卡（續跑上游）標「沿用 run <base_run_id>（未重跑）」並直接以 payload 的 out/logs 填成 done。
  - fixture-live（`POST /runs` 同步 200，無事件）與離線：保留現行 `app.js:580-615` 的排程重播，但 `#agsource` 明寫「**重播**本次執行結果，非即時節點事件」／「離線 fixture」。
  - 彈窗：`renderAgDetail(k)` 讀 `AGENTS.find(a=>a.k===k)`；`#agtime` = `run_meta.node_timings[a.node]`（沒有就「—」）；`#agprompt` = `a.prompt || a.prompt_note`；`#agscope` = `從 ${a.node} 重跑到 n6：連帶重算 ${下游卡名…}，草稿與燈號會換掉`；`#ag_rerun` → `regenFrom(a.node,{query})`，其 disabled 條件與文案沿用 `app.js:455-466`。
  - `regenFrom(from,{query})`：從 `wireRegenBar`（`:452-508`）抽出，三顆舊按鈕與彈窗共用；重跑成功後 `agentsRan=false; runAgents()` 的路徑（`:483`）改成事件驅動版。
  - 離線 `case-demo.json` 的 agents 沒 `node`：`adaptPayload`（`:66-89`）保底 `node: a.node || INV_NODE_TO_AGENTS[a.k]`（表照 `settings.py:117-124` 抄，僅此一處）。
  - **不動**：`computeDeadline`／`applyDeadlineEngine`／`applyLiveDeadline`／`verifyLaw`／`rebuildSents`／`fillTokens`／submit 流程。
- [ ] F3 `python3 prototype/build.py` 重建 dist；`node prototype/tests/parity.mjs` 綠；`grep -c "tplsel\|TPL\.A\|cmask" prototype/dist/index.html` 為 0。

驗收：
1. `RUN_MODE=fixture` 起服務，headless（沿用 `HANDOFF-INTEGRATION.md` AC2 的檢查：`pageerror` 與 `console.error` 皆空）：步驟 1 勾確認 → 啟動 → 步驟 2 七張卡全 done、`#agprog` 顯示 `7 / 7`、`#agsource` 含「重播」字樣；點 `law` 卡 → 彈窗 `#agprompt` 顯示 prompt_note（非空）、`#ag_rerun` 可按、`#ag-query` 可見；點 `proc` 卡 → `#ag-query` 隱藏。
2. `file://` 直開 dist：徽章離線、七張卡 done、彈窗可開、`#ag_rerun` disabled 且說明原因；改收文日期後期間句仍即時重算（規則引擎沒被蓋掉）。
3. `RUN_MODE=bedrock`（有憑證機器）：啟動後**卡片在 run 進行中逐張變 done**，順序 n1…n6，`law`/`case` 同一事件同時完成；彈窗 `#agtime` 有毫秒數；從 `draft` 卡重跑 → 只有 draft、qc 兩卡重新跑，其餘五張標「沿用」。
4. `run_all.py` exit 0（含 `scan_prototype_dist_reproducible`）。

### WP-I（Ci 或 tech-lead）整合

- [ ] I1 兩包各自 commit 後合併（無共同檔，預期零衝突）。
- [ ] I2 `uv run … run_all.py` 全綠；`python3 prototype/build.py` 後 `git status` 乾淨（dist 與 build 一致）。
- [ ] I3 `RUN_MODE=bedrock` 跑一次 demo 案例走完五步，截圖存 `docs/evidence/2026-09-11-staff-ux/`。
- [ ] I4 push 與否由 Ci 決定（本計畫不 push）。

## 施工順序

```
22:30  契約凍結（本檔 §契約）──┐
       WP-B (1.5h) ─────────┤ 並行；前端先對 fixture 同步 200 與離線兩條路開發，
       WP-F (3.0h) ─────────┘ B1 落地後再驗 bedrock 事件驅動
01:00  WP-I 整合 + 驗收 (0.75h)
02:00  feature freeze：只修 bug，不加功能
```

## 可砍清單（依序砍）

| 順位 | 砍什麼 | 砍掉後 demo 怎麼講 |
|---|---|---|
| 已砍 | 版式選擇器（G7） | 不提；版式由 N3 程序審查決定，結論交承辦人 |
| 已砍 | 可編輯 prompt 重生成 | 彈窗 prompt 唯讀；重生成＝續跑既有節點 |
| 1 | bedrock 事件驅動卡片（F2 第 2 點） | 退回「run 在步驟 0 跑完 → 步驟 2 重播」，`#agsource` 誠實標「重播本次執行 run_id …，非即時」；**後端 B1 可整包不做** |
| 2 | 彈窗內重跑（G5） | 保留步驟 0 既有三顆重新產生按鈕（`index.tmpl.html:500-506`），彈窗只看不改 |
| 3 | prompt 欄（G3／B2） | 彈窗不顯示指示區塊（整塊 `hidden`），不顯示假 prompt |
| 保底 | 整份計畫 | 現行 `693a9b8` 本來就能 demo（五步全通、SSE 進度字、三顆重跑鈕） |

**紅線（不可砍也不可繞）**：不得把 `design:720-744` 的虛構 prompt／logs 搬進來；fixture／重播一律在畫面上標示；燈號、why、引用狀態只來自後端 N6；`dist` 不手改。

## 備援方案

- 到 01:00 WP-F 的事件驅動還沒綠 → 執行可砍順位 1（重播版），WP-B 的 B1 留在分支但前端不依賴它。
- 到 01:30 彈窗還沒穩 → 執行順位 2、3，只留「點卡看 out/logs」。
- WP-B 任何一條讓 `run_all` 變紅且 15 分鐘內修不好 → 整包 revert，前端走順位 1。

## 最遲放棄時刻

- 事件驅動卡片：**9/12 01:00**
- 全部（含彈窗重跑、prompt 欄）：**9/12 02:00**（之後只修 bug）

## 預估時數

WP-B 1.5h、WP-F 3.0h、WP-I 0.75h；並行後 wall-clock 約 3.75h。plan-guardian 記帳：WP-F 超過 3.5h 即啟動可砍順位 1。

## 待決（Ci 拍板）

1. 幕僚卡 7 張維持不動（本計畫預設）還是照 9/6 收斂 4 個？後者今晚不做，要做請排 9/12 現場的第一個 2h。
2. 版式選擇器確定砍（本計畫預設）？若一定要，最低成本是「唯讀標籤：版式由程序審查判定」0.5h，但仍不能切換。
3. 整合後要不要 push `hack-integrate-main-bedrock`。
