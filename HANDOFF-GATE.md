# HANDOFF-GATE：守門加固（2026-09-05）

> 承接 `HANDOFF.md` §五點五「第二輪 fresh-context 覆核推翻『3 個 P0 全部已修』」。
> 覆核的結論是：**修法的形狀錯了**——「拿具體反例補具體片語／規則」會反覆製造同一類洞。
> 這一輪改的是守門的**形狀**，不是再補幾個案例。
>
> branch `mission/hack-gate-hardening-20260905`（獨立 worktree）。**未 push、未開 PR、未動 remote。**
> commit：`f5d3e62`（數字解析）→ `c2db8d8`（主文封鎖／三層／穩定鍵）→ `5cb619a`（對抗 harness）。

---

## 一、每一項修法的「結構性規則」

看這一節就能判斷這輪是不是又在補片語。每條規則都是一句可反駁的判準，不是清單。

| # | 破口 | **結構性規則**（一句話） | 位置 |
|---|---|---|---|
| P0-1 | 國字條號誤讀 | **中文數字有兩套互斥書寫體系——單位式（有十百千，位值由單位決定）與數字串式（無單位，逐字對位）——判別鍵是「有沒有單位字」，各用該體系的嚴格文法解析；混用、違反文法、含未知字元一律回 `None`，絕不猜。** | `backend/retrieval/lawtable.py:111-271`（`cn_to_int` :233） |
| P0-1b | 解析失敗的去處 | **「讀不懂這個號碼」與「這個號碼不存在」是兩件不同的事，不能混。** 新增第五態 `unparseable`：黃燈、不阻擋、但整句降到「請人工判斷」層。既不猜一個整數（誤讀→綠燈），也不靜默丟掉（漏抓→「本句未附引用」→綠燈）。 | `backend/gate/citations.py:36-60`、`check_law` :198 |
| P0-2 | 主文封鎖繞過 | **主文是一種語法，不是一組片語：它一定是「（訴願／處分標的）×（處置動詞）」的共現，或「處置助詞＋處置動詞」。** 偵測前先正規化（刪所有空白含全形、統一標點、丟括號），所以排版不是繞法。 | `backend/gate/lamps.py:156-252`（`detect_conclusion_like` :245） |
| P0-2b | 槽位不是判準 | **`slot` 是模型自己標的欄位；拿它當判準等於讓被管制的一方決定自己受不受管制。** 覆核在所有槽位、所有 origin（引擎算式句與佔位句除外）生效。 | `backend/nodes/n6_gate.py:131-163` |
| P0-2c | 不變式 | **不變式判的是「存在未被攔下的主文句」，不是「conclusion 槽位有沒有東西」。** 已被 N6 標紅並列入 blockers 的不炸（流程要跑完讓人看到攔了哪句），沒攔下的才炸。 | `backend/orchestrator/state.py:130-167` |
| P0-3 | 函釋漏抓 | **函釋的辨識鍵是「發文字號結構」＝（機關名｜完整發文日期）＋「X字第N號」，不是結尾那個「函」字。** 後綴（函／函釋／令／書函／公告／釋示）改為可選；與判解字號的區辨也是結構性的——判解是「年度＋字別」（沒有月日），函釋是「年月日」或緊鄰機關名，兩者互斥。 | `backend/gate/citations.py:81-130`（`find_directives` :115） |
| 4 | 判解／釋字國字 | **號碼字元類要涵蓋半形／全形／國字三種寫法，且用同一套 `cn_to_int` 解析；解析不出走 `unparseable` 黃燈，不靜默消失。** | `backend/gate/citations.py:67-79`、`check_precedent` :251、`check_interpretation` :311 |
| 5 | 無引用句誤歸「有出處」 | **「有出處」的定義是「這句話指得出它的出處」。** 模型寫的句子一個引用都沒抽到就指不出出處，依 CONSTITUTION §1 歸「請人工判斷」。`engine/rule/static`（算式即出處）與 `record`（卷證原文即出處）的出處是結構性的，不受此限。 | `backend/gate/lamps.py:64-93`（`tier_for_sentence` :71） |
| 6 | `resolved_id` × `laws[].id` | **跨模組比對一律用結構化的穩定鍵，不用顯示字串。** 新增 `Citation.ref_key = "法規名\|條號"`；對不到時黃燈＋具名 tag「⚠ 未能對回守門結果」＋進 blockers，不再靜默走 else 用 N4 自己的 `verified` 填燈號（那等於檢索替守門發燈）。 | `backend/gate/citations.py:156-169`、`backend/nodes/n6_gate.py:177-210` |

### 為什麼 P0-1 的舊修法比原本更糟（記錄，避免重蹈）

原本：抽不到國字條號 → 系統回「本句未附引用」→ 黃燈（**安全**的失敗）。
上一輪修完：「九九九」被讀成 9 → 命中真實存在的建築法第 9 條 → **綠燈**（系統對捏造條號主動背書）。
**把安全失敗換成不安全失敗，比沒修還糟。** 所以這輪的核心不是「多支援幾種寫法」，
而是「無法無歧義解析時必須回 `None`」——寧可交人工，不可猜。

---

## 二、驗收條件逐條狀態

### AC1 對抗 harness 涵蓋覆核者全部案例 — ✅

新檔 `backend/tests/test_gate_hardening.py`（25 條，全綠）。逐項對位：

| 覆核案例 | 測試 | 預期結果 |
|---|---|---|
| 七三／九九九／一〇五／一二三／一Ｏ五／廿五／九佰九十九 | `test_cn_numeral_review_regressions` :164 | 73／999／105／123／105／25／999 |
| 同上，端到端四態 | `test_cn_article_numbers_end_to_end_states` :221 | 七三→`ok` 綠、一〇五→`ok` 綠、一Ｏ五→`ok` 綠、廿五→`ok` 綠、九九九／一二三／九佰九十九→`missing` **紅** |
| 15 種主文寫法（含「原 處 分 撤 銷 。」空白排版） | `test_all_15_real_conclusion_forms_are_detected` :299 | 15/15 命中，`missed == []` |
| 15 種塞進 reasoning 槽位 | `test_all_15_conclusion_forms_blocked_in_reasoning_slot` :365 | 每一種 `submit_allowed=false` |
| 15 種塞進其他槽位（facts／calculation／other／summary） | `test_conclusion_block_covers_every_slot_not_just_conclusion` :349 | 每個槽位皆 `submit_allowed=false`、句子紅燈、tier=請人工判斷 |
| `…號書函`、括號內無「函」字、公告 | `test_directive_without_han_suffix_is_detected` :412 | 各抽到 1 筆 directive、`out_of_scope` 黃燈、且**不**誤生幽靈判解 |
| 釋字第七四七號 | `test_precedent_and_interpretation_accept_cn_numerals` :433 | `no=747`、`out_of_scope` 黃燈 |
| 一一二年度判字第一二三號 | 同上 | `year=112, no=123`、黃燈 |
| 解析不出的判解號 | `test_unparseable_precedent_is_visible_not_silent` :452 | `unparseable` 黃燈，**不消失** |
| 無引用句 | `test_sentence_without_citation_is_not_labeled_sourced` :476 | 黃燈但 tier=請人工判斷 |
| 檢索鍵對不到 | `test_retrieval_lamp_uses_stable_key_and_reports_mismatch` :491 | 黃燈＋`retrieval_law_not_matched_by_gate` 進 blockers |

國字數字另有 **property test**（不是列例子）：
- `test_cn_numeral_roundtrip_1_to_1200` :173 — 1..1200 單位式與數字串式**兩種寫法**都 round-trip。
- `test_cn_numeral_never_disagrees_with_independent_reader` :181 — 4000 個隨機 CJK 數字串，
  與**測試自備的獨立實作**（遞迴切分，結構刻意不同於 `cn_to_int` 的線性掃描）差分比對，
  任一分歧即失敗。這條在開發過程中**當場抓到兩處真歧義**（零佔位符、裸「十」），已修進實作。
- `test_cn_numeral_rejects_garbage_and_ambiguity` :198 — 亂碼／混用體系／違反文法 → `None`。
- 離線加跑（未進 repo，因為要跑 20 秒）：20 萬個隨機串零分歧、1..9999 全域 round-trip 零失敗。

### AC2 「建築法第九九九條」端到端紅燈阻擋 — ✅

`test_ac2_fabricated_cn_article_blocks_submission_end_to_end` :238。
基底刻意用**本來會通過**的 `synthetic-ordinary-01`，才能證明是這一句把它翻掉。實跑輸出：

```
基底 synthetic-ordinary-01            submit_allowed = True
注入「另參建築法第九九九條之規定。」後  submit_allowed = False
cites   = [('建築法第999條', 'missing', 'r')]
blockers = [('citation_missing', 's7')]
```

並斷言 `citations[]` 中**不得**出現 `建築法第9條`（覆核者實測到的那個誤讀綠燈）。

### AC3 主文寫進 reasoning 被攔 — ✅

`test_ac3_conclusion_bypass_blocked_end_to_end` :383（基底為 `requires_human_conclusion=true` 的
`synthetic-blocked-01`）。實跑輸出：

```
'綜上所述，本件訴願為無理由，應予駁回。'  submit_allowed=False  lamp=r  tier=請人工判斷
'原 處 分 撤 銷 。'                    submit_allowed=False  lamp=r  tier=請人工判斷
```

### AC4 全套測試與回歸 — ⚠️ 部分（94/95，唯一未過項為**加固前就存在**的既有失敗）

```
期間引擎搬遷與測試向量   7/7
六節點單元測試          38/38
端到端整合測試          22/22
守門加固對抗測試        25/25   ← 新增
紅線靜態掃描：
  ok    secret／禁用雲端字樣
  FAIL  prototype/ 未被變更          ← 見下
  ok    核心與測試路徑零外部依賴（backend/api/ 為具名例外）
失敗：94/95 通過
```

- **原 70 條全數通過**（7+38+22+3 檢查 = 70，其中 `prototype/ 未被變更` 那一項失敗）。
- **`prototype/ 未被變更` 是加固前就已經紅的**：在本 branch 的起點 `d4c43d8`（尚未動任何一行）
  跑 `run_all.py` 就是 **69/70、同樣 11 項**。原因是 `run_all.py:81` 的 `BASE_COMMIT`
  仍指向 `ce558a8`，而 main 之後已經合入 prototype/ 的變更（build.py、dist/index.html、
  static/app.js…）。**我沒有動這個基準值**——那支檢查是紅線量尺，改它就是「調整量尺讓自己過關」，
  正是這個專案明文要防的事。`prototype/` 由整合 agent 擁有，這個基準值要不要更新請他們或 Ci 拍板。
- `synthetic-ordinary-01` 仍 `submit_allowed=true`、燈號 `{r:0, y:0, g:13}`、
  三層 `{可驗算:6, 有出處:7, 請人工判斷:3}` — 與加固前 baseline **逐項一致**
  （`test_official_cases_behaviour_unchanged` :533 釘住）。
- `synthetic-blocked-01` 仍 `false`、燈號 `{r:2, y:0, g:10}`、三層 `{6,4,3}` — 一致。
- Deterministic：同案例跑 5 次、去掉時間欄位後 hash 一致
  （`test_pipeline_is_still_deterministic` :547，兩案例各 5 次皆 1 個 hash）。
  加固前後 payload hash 本身有變（多了 `ref_key`／`gate_ref_key` 兩個新欄位），
  但**可見行為**（燈號、三層、blockers、引用狀態、檢索 tag）逐行相同，我用 baseline diff 比過。

### AC5 每項修法都寫出結構性規則 — ✅ 見上面第一節表格（8 條，每條一句判準）

### AC6 無 secret／無 GCP／無新依賴／檔案範圍 — ✅

- `run_all.py` 的兩項紅線掃描（secret＋禁用雲端字樣、核心與測試路徑零外部依賴）皆 ok。
- 新測試只用 stdlib（`json`／`pathlib`／`random`／`tempfile`／`hashlib`）。
- `git diff --stat d4c43d8..HEAD`：

```
backend/gate/citations.py     | 153 ++++--
backend/gate/lamps.py         | 154 ++++--
backend/nodes/n6_gate.py      |  65 ++--
backend/orchestrator/state.py |  27 +-
backend/retrieval/lawtable.py | 234 +++++++---
backend/tests/run_all.py      |  (註冊新測試檔)
backend/tests/test_gate_hardening.py（新檔）
```

全部在我擁有的清單內。**未動** `prototype/`、`backend/api/`、`backend/orchestrator/graph.py`、
`backend/nodes/n4_retrieval.py`、`backend/tests/test_e2e.py`、`backend/tests/test_nodes.py`、
`backend/nodes/n5_draft.py`（結論槽位處理維持原樣，這輪不需要改它——結構性封鎖在 N5 已經是
「conclusion 不進 slots 陣列」，破口在 N6 的偵測與不變式，已修）。`laws[].id` 格式未動。

---

## 三、給 Ci 的判斷卡（我刻意沒做的兩個設計決定）

### 判斷卡 1：C 型結論封鎖的判準幾乎沒有鑑別力

**問題**：覆核實測把 `synthetic-blocked-01` 的事實爭點訊號全部拿掉，**仍然封鎖**。
翻 `requires_human_conclusion`（`backend/gate/lamps.py:116-153`）的邏輯就知道為什麼——
真正在作用的是 fail-safe 分支：「案型不在 `SUBSTANTIVE_TYPES` **且**程序上沒有可直接算出的
不受理事由 → 封鎖」。也就是說目前 demo 案例被封鎖的實質理由是「未逾期」，
不是「偵測到事實認定爭點」。評審問「換個案型會怎樣」會露餡。

**選項**：
- **A（維持現狀）**：fail-safe 就是要寬，寧可多封鎖。demo 時**主動說**「這一版的封鎖判準保守，
  只要程序上算不出結論就一律交人工」，把它講成設計選擇而不是被抓到。
- **B（拆兩層顯示）**：判準不動，但 `human_conclusion_signals` 明確區分
  「因偵測到事實爭點而封鎖」與「因無法判斷而保守封鎖」，畫面上分開顯示。
  改動小（只動訊號文字與前端呈現），能直接回答「換個案型會怎樣」。
- **C（提高鑑別力）**：讓事實爭點成為獨立的封鎖理由，並準備一個**不封鎖**的對照案例，
  demo 時兩個案例並列。最有說服力，但要多做一份合成測資與一輪驗證，吃 30h 預算。

**我的建議：B（現在做）＋ C（若時間允許）。** A 的風險是評審自己發現，那比我們自己說出來糟。
**不做 A 以外的事就等於選 A**，請明確拍板。
**我沒有動 `SUBSTANTIVE_TYPES` 與 `requires_substantive_review`** —— 這是設計決定，不是 bug。

### 判斷卡 2：N4 的檢索是循環佐證（查詢句從答案反推）

**問題**：`graph.py:86→128` 的 `_collect_cited_law_strings()` 從 fixture 草稿的 `basis` 欄位
蒐集法條字串，當成 N4 的查詢句（`n4_retrieval.py:53`）。也就是說「查什麼」是照著
「答案要引用什麼」倒著填的。目前「引用一定查得到」不是獨立檢索在佐證，是同義反覆。
規格要求的 `build_query()` 實務上是死碼。**這會直接影響 demo 可信度**：
評審只要問「檢索是怎麼決定要查哪幾條的」，現在的答案是「照草稿要引用的填」。

**選項**：
- **A（誠實標註，不改程式）**：在 `retrieval_meta` 增一欄
  `query_source: "draft_basis_replay"` 並在畫面與 narrative 明說「fixture 檔位下檢索查詢
  取自草稿引用，非獨立檢索」。零風險、半小時，但等於承認這條通道 demo 不算數。
- **B（改用 `build_query(state)`）**：查詢句改由案情（案由＋爭點＋程序條文）決定，
  接受「草稿引用的法條可能查不到」——那反而會讓守門的黃燈／紅燈**真的動起來**，
  更能展示引用四態。風險：`synthetic-ordinary-01` 目前的全綠畫面會變成有黃燈，
  demo 敘事要跟著改，而且 `graph.py`／`n4_retrieval.py` 由整合 agent 擁有。
- **C（兩條都跑）**：`build_query` 出的候選與草稿引用**取聯集**，畫面上分別標
  「檢索命中」與「草稿引用」，對不上的地方正好是守門要講的故事。最有說服力也最花時間。

**我的建議：至少做 A（今天），B/C 看整合進度。** A 的成本是零，而且**不做 A 就等於在
demo 裡默示這條通道是獨立檢索**——那是誠實性問題，不只是品質問題。
**我沒有動 N4 的查詢來源**（檔案歸整合 agent，且這是設計決定）。

### 判斷卡 3（本輪新增的介面變更，需要整合 agent 知道）

1. **引用多了第五態 `unparseable`**（`lamp="y"`、`mark="？ 無法解析"`、不阻擋）。
   前端若有硬寫死的四態 switch，會掉進 default。`architecture.md §8.1` 寫的是四態，
   要不要同步改文件請拍板（我沒改 `docs/architecture.md`）。
2. **`citations[]` 多了 `ref_key` 欄位**（`"法規名|條號"`，非法條類為 `null`）；
   **`retrieval.laws[]` 多了 `gate_ref_key`**。純新增，不影響既有欄位。
3. **N6 依賴 `retrieval.laws[]` 必須帶 `law` 與 `article` 兩個結構化欄位**
   （`n4_retrieval.py:81-82` 目前有）。這是新的跨節點契約：若整合 agent 改動 N4 的
   `laws[]` 形狀而拿掉這兩欄，N6 會把每一筆都判成「未能對回守門結果」並阻擋送出
   （這是刻意的 fail-safe，不是 bug，但要事先知道）。
4. **`blockers[]` 多了兩種 reason**：`retrieval_law_not_matched_by_gate`（severity P1）。
   `submit_allowed` 是 `not blockers`，所以這一類也會擋送出。若整合時發現它在正常案例
   誤觸，優先查 N4 的 `laws[]` 欄位，不要直接把它降級成 warning。

---

## 四、沒做完 / 未驗證的（誠實條款）

| 項目 | 狀態 |
|---|---|
| `prototype/ 未被變更` 紅線檢查 | ❌ 紅，但**加固前就是紅的**（`BASE_COMMIT` 過期）。我沒改基準值，理由見 AC4。 |
| `docs/architecture.md §8.1` 的「四態」敘述 | ⬜ 未更新（實作已是五態）。文件不在我的檔案清單內，列為待辦。 |
| 前端對 `unparseable` 態的呈現 | ⬜ 未驗證。`prototype/` 不歸我，也沒跑起來看。 |
| `backend/api/` 對新欄位的序列化 | ⬜ 未驗證（沒碰 api，也沒起服務打端點）。新欄位都是純量／字串，理論上無風險，但**這是推論不是驗證**。 |
| C 型判準鑑別力、N4 循環佐證 | ⬜ 刻意未動，見判斷卡 1、2。 |
| 主文偵測的誤攔率 | ⚠️ 只用兩個合成案例的 6 句理由段與 15 句主文量過，**沒有真實決定書語料**可量。真實誤攔率未知。已把「刻意過度攔截」寫成測試（`test_conclusion_detection_is_deliberately_over_inclusive` :318），讓未來的人知道這是選擇不是 bug。 |
| 函釋 regex 對真實公文字號的覆蓋率 | ⚠️ 只驗了 5 種構造，沒有真實函釋語料。 |

---

## 五、一句話總結

這輪改的是三個**判準的形狀**：數字解析從「盡量猜」改成「讀不懂就交人工」；
主文偵測從「片語清單」改成「標的×動詞的語法共現」；跨模組比對從「顯示字串巧合」
改成「結構化穩定鍵，對不到就報錯」。三個都附了會失敗的對抗測試與 property test，
而不是只釘住覆核者測過的那幾個字串。
