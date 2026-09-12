# relation-graph

把一次 run 的 payload 組成「卷證 → 事實 → 爭點 → 法規依據 → 結論」五欄關聯圖，
**每條邊都指得出它從 payload 哪個欄位推出來**，零 LLM。回傳形狀見契約
`docs/handoff/2026-09-12-frontend-contract-v2.md` §3.7；完整計畫見
`plans/2026-09-12-relation-graph.md`（§9「最遲放棄時刻」已由 Ci 作廢，本功能是必要項）。

---

## Background

承辦人要一眼看出「這個結論是從哪條卷證、經過哪個爭點、引哪條法規推出來的，**以及哪裡是斷的**」。
四種邊在 payload 裡都已經是現成的，零模型呼叫可組。

### 實查修正三件事（動工前掃過 9421 份不重複的 VERIFIED run）

**① `ss[].refs` 裡沒有 `L*`，不能複用 `_attach_law_refs` 的結果。**
派工說「直接複用 `_attach_law_refs`（`backend/orchestrator/graph.py:621`）比自己重算安全」。
實查：**9421 份 run、31739 句，`refs` 含 `L*` 的是 0 句**，其中 9383 句帶著
`citations[]`（`raw` 對得上 `laws[].t`）卻照樣沒有 `L*` ref。
原因是 `backend/output/runs/*.json` 存的是 `CaseState.as_dict()`
（`backend/orchestrator/runstore.py:35`），不是 `build_payload()` 的輸出，
而 `_attach_law_refs` 是 `build_payload()` 內才呼叫的就地變更。
→ `cite` 邊由本模組自己用同一把尺重算（`citations[].raw` ↔ `laws[].t` 字串相等）。

**② 輸入有兩種形狀，都要吃得下。**
`build_payload()` 的輸出是**扁平**的（頂層 `doc` / `citations` / `laws` / `cases`），
runstore 存的是**巢狀**的（`gate.doc` / `gate.citations` / `retrieval.laws`）。
`facts_excerpt` 與 `screen.fact_issues` 兩種形狀相同。
→ 取值一律「先扁平、後巢狀」，兩種都讀得到。
**不能改用 `build_payload()` 的頂層 `issues[]`**：`_issues_view()`（`graph.py:540`）
把 `matched_keywords` 丟掉了，`trigger` 邊會整批消失。

**③ `trigger` 的尺比 N3 窄，這是刻意的。**
N3 的 haystack 是 `digest + intake.note`，而 `digest` = 所有 `facts_excerpt[].text`
串接 + `intake.note`（`graph.py:128-137`）。關鍵詞可能只命中 `intake.note`——
那種爭點**沒有可歸屬的事實節點**，畫線就是編。
→ 只比對 `facts_excerpt[].text`；只由收文備註觸發的爭點進 `unlinked.issues` 並寫明原因。

---

## User Stories

### US-E1: 五欄關聯圖 [P0]

**作為**陳專員，**我要**看到結論回推到卷證的那條線，**才能**逐段覆核而不是整份重讀。

- **E1.1** `build_relation_graph(payload)` 是純函式：輸入 dict、輸出 dict，不碰 runstore、不碰 orchestrator。
- **E1.2** 節點五種（`doc`/`fact`/`issue`/`law`/`out`），欄位 `c` 固定 0–4，`id` 沿用 payload 既有編號。
- **E1.3** 邊四種（`quote`/`trigger`/`address`/`cite`），每條帶 `basis` 寫明依據欄位。
- **E1.4** 回傳形狀與契約 §3.7 一致：`run_id`/`generated`/`cols`/`nodes`/`edges`/`unlinked`/`stats`。

**驗**：`backend/tests/test_relation_graph.py`，拿真 run 實跑。

### US-E2: 斷掉的地方要看得見 [P0，紅線]

**作為**科長，**我要**知道「AI 引了一條我們沒檢索到的法條」，**才不會**把一份引用懸空的草稿送出去。

- **E2.1** `citations[].raw` 在 `laws[].t` 查無 → 進 `flagged`，**不得靜默丟棄**。
- **E2.2** 檢索到但沒被任何句子引用的法規 → 進 `unlinked.laws`。
- **E2.3** 只由 `intake.note` 觸發、沒有事實可歸屬的爭點 → 進 `unlinked.issues`。
- **E2.4** `cite` 邊帶 `state` 與 `lamp`（`ok`/`amended`/`out_of_scope`/`missing`/`unparseable`），
  給前端畫第三種線型。**沒有「矛盾」這種邊**——後端沒有偵測矛盾的機制，畫了就是編。

**驗**：拿 `synthetic-blocked-01`（三筆引用全部查無）與 `synthetic-ordinary-01`（3/4 命中）各跑一次。

### US-E3: 稀疏與退化都照實回 [P0]

**作為**承辦人，**我要**圖上沒有的東西真的就是資料裡沒有，**才能**相信圖上有的。

- **E3.1** `stats` 同時回 `sentences_total` 與 `sentences_in_graph`，差很多也照實回。
- **E3.2** 沒有草稿的 run（`SCREENED`）→ `status:"empty"` + `note`，**不拋例外、不回半張圖**。
- **E3.3** `address` 邊稀疏（實測一份 run 13 句只有 1 句掛得到爭點）是資料的事實，不補線。

**驗**：拿 `to_node="n3"` 的 run 跑一次。

### US-E4: 產物不含個資 [P0，紅線]

**作為**資料保護的那一關，**我要**圖的節點文字不外流當事人姓名，**因為**
`facts_excerpt[].text` 是卷證原文。

- **E4.1** `fact` 節點的 `d` 截斷到上限長度，`t` 是摘要不是全文。
- **E4.2** 有測試證明截斷會發生（不是恆真斷言）。

### US-E5: 接進 chat 工具 [P0]

- **E5.1** 依既有注入模式：`backend/orchestrator/chat_bridge.py` 提供 adapter，
  `ChatTools` 收一個 callable，**`backend/llm/chat.py` 不 import runstore 或 orchestrator**。
- **E5.2** `build_relation_graph` 進 `TOOL_LABELS`（契約 §3.0 的 ⏳ 轉綠），
  並補進 `test_every_tool_entry_point_throttles_exactly_once` 的涵蓋清單。
- **E5.3** 沒注入 adapter 時（乙案容器）回 `status:"failed"` + 明說這個檔位沒有，不靜默失敗。

---

## 不做什麼

- 不做全庫知識圖（那是 `plans/kb-graph.md`，資料來源／產物／消費端都不同）。
- 不做 `issue` → `law` 直連：N4 的查詢句由 issues 組成，但**沒有 per-issue → per-law 的紀錄**，要連只能猜。
- 不做邊的權重／強度：沒有任何欄位撐得起來。
- 不做矛盾偵測。
- 不做前端渲染（本 change 只到後端回傳）。

## Notes

**AC2（四種邊各至少一條，同一份 run）很可能驗不了，動工前就知道。**
掃過 9421 份不重複的 VERIFIED run，**沒有任何一份同時產得出 `trigger` 與 `cite`**：

| 合成案 | `trigger` | `cite` | 原因 |
|---|---|---|---|
| `synthetic-ordinary-01`（程序型） | ✗ | ✓ 3/4 | 爭點關鍵詞「未實際收受」只在 `intake.note`，不在任何 `facts_excerpt[].text` |
| `synthetic-blocked-01`（建築法時效型） | ✓ | ✗ 0/3 | 草稿引的實體法條號（建築法 §73、行政罰法 §27）`laws[]` 全部沒有——`_retrieval_divergence` 已記載的已知限制：缺 PDF 視覺抽取，實體條號推不出來 |
| `synthetic-adversarial-01` | 594 份各半，**從不同時發生** | | 兩個 fixture 變體 |

這不是實作走歪，是語料的事實。**不改 N3 的判準、不加合成案來湊綠**（CONSTITUTION §3、§5）。
四種邊會分別在兩份 fixture 上各自驗出來，AC2 留紅並附這份掃描證據。
