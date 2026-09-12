# Implementation Plan: relation-graph

## Overview

一個純函式 `build_relation_graph(payload) -> dict` 把 run payload 攤成五欄圖，
外加一層 adapter 把它接進 chat 工具。**全部零 LLM**，所有邊都由字串比對推出，
推不出來就不畫、並把「推不出來」本身寫進 `unlinked` / `flagged`。

三個關鍵設計決策：

1. **`cite` 邊自己重算，不靠 `ss[].refs`。** runstore 存的是 `CaseState.as_dict()`，
   `_attach_law_refs` 的就地變更不在裡面（實查 31739 句、0 句有 `L*`）。
   用同一把尺（`citations[].raw` ↔ `laws[].t` 字串相等）自己 join，
   **不用 `resolved_id` ↔ `gate_ref_key`**（格式不同，實測 0/4 命中，而且是靜默的）。
2. **取值兩種形狀都吃。** 扁平（`build_payload` 輸出）優先，巢狀（runstore）回退。
3. **`trigger` 只比對 `facts_excerpt[].text`**，比 N3 的 haystack 窄。窄掉的那一半
   （只命中 `intake.note` 的爭點）不是丟掉，是進 `unlinked.issues` 寫明原因。

## Affected Modules

| Module | Impact | Changes |
|--------|--------|---------|
| `backend/graph/relation.py` | High | 新檔。純函式，stdlib only，不 import `backend.llm` / runstore / orchestrator |
| `backend/orchestrator/chat_bridge.py` | Medium | 新增 `relation_graph_adapter(case_id)`，比照 `pipeline_adapter` |
| `backend/llm/chat.py` | Medium | `TOOL_LABELS` 加一支；`ChatTools.__init__` 收 `build_graph` callable；新增工具進入點與 Strands 包裝 |
| `backend/api/chat.py` | Low | 注入 adapter |
| `backend/tests/test_relation_graph.py` | High | 新檔 |
| `backend/tests/run_all.py` | Low | 掛新測試模組（import + 執行清單兩處）；零 LLM 掃描擴及新模組 |
| `backend/tests/test_chat.py` | Low | 節流涵蓋清單補上新工具 |
| `docs/handoff/2026-09-12-frontend-contract-v2.md` | Low | §3.0 的 ⏳ 轉成已實作（只改狀態欄，不動形狀） |

## Implementation Steps

1. **`backend/graph/relation.py`：節點**
   - 取值 helper：扁平優先、巢狀回退。
   - `doc` 節點由 `facts_excerpt[].quote_ref` 的 `#` 前半去重；`t` 從 `files[].n` 對得到就用它、對不到就用檔名本身。
   - `fact` 節點：`t` 截斷成短摘要、`d` 截斷到上限（US-E4 個資）。
   - `issue` / `law` / `out` 節點沿用 payload 既有 id。
   - `out` 只收「有連線」的句子，`stats` 同時回總數（US-E3.1）。

2. **邊**
   - `quote`：`doc` → `fact`。
   - `trigger`：關鍵詞出現在該筆 `facts_excerpt[].text`（字串包含）。`detail` 帶命中的詞，供 AC3 反查。
   - `address`：`ss[].refs` 含 `I*`，並聯集 `gate.issue_refs[]`（同一件事的另一份紀錄）。
   - `cite`：`citations[].sentence_id` → 該句；`citations[].raw` ↔ `laws[].t` ∪ `cases[].t`。
     帶 `state` 與 `lamp`。

3. **斷掉的地方**
   - `flagged`：`raw` 查無 → 一筆記錄（`sentence_id` / `raw` / `state` / `lamp` / `basis`）。
   - `unlinked.laws`：沒有任何 `cite` 邊指到的法規／案例。
   - `unlinked.issues`：沒有任何 `trigger` 邊指到的爭點，`note` 寫明是不是只由 `intake.note` 觸發。

4. **退化與 `stats`**
   - 沒有 `doc[]` → `status:"empty"` + `note`，其餘欄位仍在（`nodes`/`edges` 為空陣列，前端不必特判）。
   - `stats` 全部用 `len()` 算，不手寫數字。

5. **接線**
   - `chat_bridge.relation_graph_adapter(case_id)` → `load_run` → `build_payload` → `build_relation_graph`，
     並比對 `case_id`（拿 A 案的 run 去問 B 案要擋）。
   - `ChatTools.build_relation_graph()`：進入點先 `_throttle()`，`_result(..., graph=graph)`。

6. **測試與掃描**
   - fixture：從既有 run 抄兩份進 `backend/tests/fixtures/`（`output/runs/` 是 gitignored，測試不能靠它）。
   - 每條關鍵斷言做變異測試。
   - `run_all.py` 兩處都掛。

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| join key 用錯 → 靜默空圖 | High | AC4 把邊數釘成「`raw` 能在 `laws[].t` 找到的筆數」，不是 `> 0`；變異測試換成 `resolved_id` ↔ `gate_ref_key` 必須紅 |
| 四種邊湊不出同一份 run | Medium | 已知（proposal Notes）。分兩份 fixture 各驗，AC2 留紅附掃描證據，**不改 N3、不加合成案** |
| `fact` 節點外流當事人姓名 | High | 截斷 + 測試釘住截斷長度；變異測試把上限改大必須紅 |
| 新工具進 `TOOL_LABELS` 打破既有節流測試 | Low | 同一個 commit 補上涵蓋清單 |
| fixture 抄進 repo 可能夾帶個資 | Medium | 只抄 `synthetic-` 開頭的合成案（CONSTITUTION §3），不抄任何上傳案 |
