# 案件關聯圖（`build_relation_graph`）

2026-09-12 Ci 拍板：**後端新功能**，回傳給前端工作台。契約形狀見
`docs/handoff/2026-09-12-frontend-contract-v2.md` §3.7。

> **跟 `plans/kb-graph.md` 不是同一個東西。** 那份是全庫 2477 份文件的「法規→法條→個案」
> 三層知識圖、離線批次、獨立展示頁、**不接工作台**。這份是**單一案件**的辦案脈絡圖，
> 走 chat 工具回傳給工作台。兩者資料來源、產物、消費端都不同，不要合併也不要互相引用實作。

## 0. 一句話

把一次 run 的 payload 組成「卷證 → 事實 → 爭點 → 法規依據 → 結論」五欄關聯圖，
**每一條邊都指得出它是從 payload 哪個欄位推出來的**，零 LLM。

## 1. 目標與非目標

**目標**：承辦人看一眼就知道「這個結論是從哪條卷證、經過哪個爭點、引哪條法規推出來的」，
以及**哪裡斷掉了**（查到但沒用上的法規、引用了但檢索沒找到的法條）。

**非目標**：
- 不做全庫知識圖（那是 `kb-graph.md`）。
- 不做自動排版美化（欄位固定五欄，列位置由前端算）。
- 不偵測矛盾（§5）。

## 2. 紅線（CONSTITUTION）

- **零 LLM**：全部從 payload 欄位推導，不呼叫 Bedrock、不 import `backend.llm`。
- **不編造**：每條邊帶 `basis` 欄位寫明依據。推不出來就不畫，**不補一條看起來合理的線**。
- **不靜默丟棄**：檢索到但沒被引用的法規、引用了但檢索沒找到的法條，
  都要出現在 `unlinked` / `flagged`，不是當作不存在。
- **層級**：實作不得 import `backend.llm.*`（本功能沒有模型呼叫，這條是白拿的）。

## 3. 資料來源（2026-09-12 實查 `backend/output/runs/*.json` 確認）

| payload 欄位 | 實際欄位 | 用途 |
|---|---|---|
| `facts_excerpt[]` | `{text, page, quote_ref}`；`quote_ref` 例 `"synthetic-原處分裁處書.pdf#p2"` | 卷證節點、事實節點、`quote` 邊 |
| `screen.fact_issues[]` | `{id, t, q, severity, signal_id, matched_keywords, src, origin}` | 爭點節點、`trigger` 邊 |
| `retrieval.laws[]` | `{id, law, article, t, verified, lamp, gate_ref_key, gate_status, note, …}` | 法規節點 |
| `retrieval.cases[]` | 同形（相似案） | 案例節點 |
| `gate.doc[].ss[]` | `{id, t, refs, cite_ids, citations, l, origin, tier, …}` | 結論句節點、`address` 邊 |
| `gate.citations[]` | `{sentence_id, resolved_id, raw, state, lamp, kind, blocking, note, …}` | `cite` 邊 |

## 4. 節點推導

| `k` | `c`（欄） | 來源 | `id` | 備註 |
|---|---|---|---|---|
| `doc` | 0 | `facts_excerpt[].quote_ref` 的 `#` 前半，去重 | `D{n}` | 同一份卷證多段摘錄只出一個節點 |
| `fact` | 1 | `facts_excerpt[]` 每筆一個 | `F{n}` | `d` 放 `text`，`src` 放 `quote_ref` |
| `issue` | 2 | `screen.fact_issues[]` | 沿用 `issue.id`（`I1`…） | `origin:"rule"`，`severity` 帶出 |
| `law` | 3 | `retrieval.laws[]` + `retrieval.cases[]` | 沿用 `id`（`L1`…／`C1`…） | `verify` 由 `gate_status`／`verified` 決定 |
| `out` | 4 | `gate.doc[].ss[]` **只取被引用或引用他人的句子** | 沿用 `ss[].id`（`s3`…） | 全部 13 句都畫會糊掉；只畫有連線的 |

> `out` 節點的取捨是**顯示決定不是資料決定**：孤立句子仍然在 payload 裡，只是不進圖。
> 這一條要寫在回傳的 `stats` 裡（`sentences_total` vs `sentences_in_graph`），不要讓人以為草稿只有那幾句。

## 5. 邊推導（四種，每種都有程式依據）

| `rel` | 從 → 到 | 依據 | 驗證過的命中率 |
|---|---|---|---|
| `quote` | `doc` → `fact` | `facts_excerpt[].quote_ref` 的 `#` 前半 | 2/2 |
| `trigger` | `fact` → `issue` | `fact_issues[].matched_keywords` 的任一詞出現在該筆 `facts_excerpt[].text`（**字串包含**，與 N3 同一把尺，`n3_procedure.py:619-634`） | 見 §7 AC3 |
| `address` | `issue` → `out` | `ss[].refs` 含該 `I*`（N6 掛的） | **1/13 句**——稀疏是資料的事實，不要補 |
| `cite` | `out` → `law` | **`citations[].raw` ↔ `laws[].t` 字串相等** | 3/4 |

### 5.1 ⚠️ join key 有坑，用錯會靜默得到空圖

實查同一份 run：

```
citations[].resolved_id = "L-訴願法-14"      格式 L-{法名}-{條號}
laws[].gate_ref_key     = "訴願法|77"        格式 {法名}|{條號}

join resolved_id ↔ gate_ref_key  →  0/4 命中   ← 看起來該用這個，實際全空
join raw         ↔ laws[].t      →  3/4 命中   ← 正確
```

**用 `citations[].raw` 比對 `laws[].t`**，這也是 `_attach_law_refs` 既有的做法
（`backend/orchestrator/graph.py:605`：`by_raw = {law.get("t"): law.get("id") ...}`）。
那段的註解已經寫明「字串相等比對，不做模糊比對、不靠 `cite_ids` 的序號巧合」。

**直接複用 `_attach_law_refs` 的結果**（它已經把 `L*` 插進 `ss[].refs`）比自己重算安全——
重算一次就多一個會走歪的地方。

### 5.2 沒命中的那一筆不是 bug，是訊號

`訴願法第15條` 被草稿引用，但 `laws[]` 裡沒有——**草稿引了檢索沒找到的法條**。
payload 已經有 `retrieval_divergence` 這個欄位在記這件事（`graph.py:715`）。

這種 citation 要進 `flagged`，在圖上畫成**指向一個虛節點**或**帶警示的懸空邊**，
`basis` 標 `"citations[].raw 在 laws[] 查無"`。**不要靜默丟掉**——「AI 引了一條我們沒檢索到的法條」
正是承辦人最需要知道的事。

## 6. 沒有的東西（畫了就是編）

- **「矛盾／爭執」邊**：後端**沒有任何偵測矛盾的機制**。設計稿的第三種線型改用
  `cite` 邊的 `state`（`ok`／`amended`／`out_of_scope`／`missing`／`unparseable`）與 `lamp`，
  非 `ok` 畫虛線或警示色——那是真的，而且更有用。
- **`issue` → `law` 直連**：N4 的查詢句是從 issues 組的（`build_query_sources`），
  但**沒有 per-issue → per-law 的記錄**。要連只能猜。不做。
- **邊的權重／強度**：沒有任何欄位可以支撐。不做。

## 7. 驗收條件

> 每條都要**實際跑過**並貼出輸出。驗不了的留紅並寫原因——留紅不算失敗，湊綠才算。

**AC1　零 LLM（AST，不是 grep）**
```bash
python3 - <<'PY'
import ast,sys
t=ast.parse(open('backend/graph/relation.py').read())
bad=[n for n in ast.walk(t) if isinstance(n,(ast.Import,ast.ImportFrom))
     and any(('backend.llm' in (a.name if isinstance(n,ast.Import) else (n.module or '')))
             for a in n.names)]
print('FAIL' if bad else 'PASS', len(bad))
PY
```
用 AST 不用 grep，因為 grep 分不出註解、字串與真正的 import，也擋不住 `import X as Y`。

**AC2　四種邊各至少產出一條**（拿一份 `state == "VERIFIED"` 的 run 實跑）
```bash
python3 -m backend.graph.relation --run <run_id> | \
  python3 -c "import json,sys,collections;d=json.load(sys.stdin);\
print(collections.Counter(e['rel'] for e in d['edges']))"
```
四種 `rel` 都要出現。**`address` 只有一條也算過**——稀疏是資料的事實。

**AC3　`trigger` 邊的關鍵詞真的在那段文字裡**
```bash
python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
facts={n['id']:n.get('d','') for n in d['nodes'] if n['k']=='fact'}
bad=[e for e in d['edges'] if e['rel']=='trigger'
     and not any(k in facts.get(e['from'],'') for k in e.get('detail',[]))]
print('FAIL' if bad else 'PASS', bad[:2])
" <輸出檔>
```

**AC4　`cite` 邊的 join 沒走歪**（防 §5.1 那個坑）
邊數必須等於 `citations[]` 裡 `raw` 能在 `laws[].t` 找到的筆數。
**這條刻意不寫成「> 0」**——寫 `> 0` 的話用錯 join key 也可能因為別的原因湊出幾條。

**AC5　沒命中的 citation 有進 `flagged`**
拿那份 `訴願法第15條` 的 run 跑，`flagged` 必須非空且含該筆。
**這條驗的是「不靜默丟棄」**，是紅線不是功能。

**AC6　`stats` 的數字是算出來的**
`stats.nodes` / `stats.edges` 與陣列長度相等；`sentences_total` 與 payload 的 `ss[]` 總數相等。
加總用程式算不用眼睛核。

**AC7　退化行為**（只有 `SCREENED` 的 run）
拿一份 `to_node="n3"` 的 run 跑，必須回 `status:"empty"` + `note` 說明要先生成草稿，
**不得拋例外、不得回半張圖假裝完整**。

**AC8　產物不含個資**
節點的 `t`／`d` 不得含地址、車牌、身分證字號。`facts_excerpt[].text` 是卷證原文，
**會含當事人姓名**——`fact` 節點的 `d` 要截斷或遮蔽，這條要有測試。

## 8. 實作位置

- `backend/graph/relation.py`——純函式，輸入 payload dict，輸出 graph dict。**不碰 runstore、不碰 orchestrator**。
- chat 工具層注入使用（比照 `run_pipeline` 的注入方式，見契約 §0.1），
  **不得讓 `backend/llm/chat.py` 直接 import 它以外的東西**。
- 測試 `backend/tests/test_relation_graph.py`，fixture 用既有的 `backend/output/runs/*.json`
  （挑一份 VERIFIED、一份 SCREENED）。

## 9. ~~最遲放棄時刻~~ — **已作廢（2026-09-13 Ci 拍板：關聯圖是必要項）**

> 本節原本寫「這是加分項不是主線；若到 2026-09-13 上午仍未通過 AC2，改回契約 §6
> 原本的處置（前端標『示意圖，非後端產物』或整段砍掉）」。
>
> **2026-09-13 Ci 改判：關聯圖是必要交付項，沒有放棄時刻。**
>
> 但**放棄時刻作廢不等於紅線放寬**。原本那句「不要為了讓圖有東西而放寬 §6 的紅線」
> 現在更要緊——以前做不出來還可以砍，現在不能砍，**而「不能砍」正是最會逼人湊數字的處境**。
> §5 的四種邊、§6 的「沒有矛盾邊」、§2 的不編造，一條都不因為它變成必要項而鬆動。
> 邊畫不出來就是畫不出來，圖稀疏就讓它稀疏（§5 已實測 `address` 邊 13 句只有 1 句）。

## 10. 未決

- `out` 節點只畫「有連線的句子」是我的建議（§4 備註），**要不要改成畫全部由實作者實跑看糊不糊決定**，
  決定之後把理由寫回這份。
- `cases[]`（相似案）要不要跟 `laws[]` 放同一欄（`c:3`）。目前契約寫在同一欄，
  若視覺上擠就分欄，**分了要同步改契約 §3.7 的 `cols`**。
