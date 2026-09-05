# HANDOFF-GATE：守門加固（2026-09-05）

> 承接 `HANDOFF.md` §五點五「第二輪 fresh-context 覆核推翻『3 個 P0 全部已修』」。
>
> **這份文件經過三輪對抗覆核，前兩輪都把我的『已修』宣稱打穿了。** 誠實記錄如下，
> 因為「修了幾輪、每輪被打穿什麼」本身就是判斷這份程式可不可信的資料：
>
> | 輪次 | 覆核結果 |
> |---|---|
> | 我的第 1 版 | 覆核用 30 句真實主文打穿 **29 句**；`cn_to_int('一百五')` 回 105（人讀 150），捏造條號拿到綠燈 |
> | 我的第 2 版 | 覆核構造 90 句打穿 **55 句**（三條繞法：record origin／假出處當盾牌／通則引述殼） |
> | 我的第 3 版 | 覆核判 **No-Go**：`out_of_scope` 仍可當盾牌（宣稱修了、只修了 tier 沒修阻擋）、第 1 層漏 §83/§93/§84/§81 自為決定（25/28 穿過且拿綠燈）、「本會同意」繞過轉述豁免、宣稱「已移除字元視窗」不實 |
> | 我的第 4 版 | 覆核判 **Go-with-caveats**：case 層封鎖確實擋得住所有寫法，但**它的開關上游是 LLM 抽取欄位**（改一個日期就能關掉整條防線），且非 C 型案件內容守門歸零（26/26 捏造主文全綠）；另抓到兩處我自己寫的、系統擔保不了的宣稱 |
> | 我的第 5 版（現況） | 移除不實宣稱並用測試擋住復發；空草稿不得送出；非 C 型留主文註記。**兩個結構問題依任務指示不得修（屬 C 型判準設計），寫成判斷卡 7／8** |
>
> 兩個教訓，都是覆核講的、不是我自己想到的：
>
> 1. **只要最後一道防線是在比對字串，就一定有盲點。** 我前三版都在把片語表做得更大，
>    每一版都宣稱「這次窮舉了」，每一版都被推翻。第 4 版把防線換位置——
>    C 型案件一律不得送出，這條判準不看句子寫了什麼，**改草稿文字繞不過它**。
> 2. **但它也不是絕對的，而我一度把它寫成絕對的。** 這條防線的開關
>    （`requires_human_conclusion`）吃的是 N1／N2 的抽取結果，`origin_registry` 自己
>    就標明 `case_type` 是 `llm_derived`。第四輪覆核只改了一個 N1 抽的日期欄位，
>    就讓一件真正的 C 型案降級成非 C 型、整條封鎖不觸發、26/26 捏造主文拿到綠燈。
>    **「沒有任何寫法能繞過」這句話是我寫的，它不成立。** 已從程式註解移除，
>    並加了一條測試擋住這類宣稱復發。
>
> branch `mission/hack-gate-hardening-20260905`（獨立 worktree）。**未 push、未開 PR、未動 remote。**
> commit：`f5d3e62`（數字解析）→ `c2db8d8`（主文封鎖／三層／穩定鍵）→ `5cb619a`（對抗 harness）
> → `c53831d`（第二輪修法）→ `53bed46`（第三輪修法）。

---

## 一、每一項修法的「結構性規則」

看這一節就能判斷這輪是不是又在補片語。每條規則都是一句可反駁的判準，不是清單。

| # | 破口 | **結構性規則**（一句話） | 位置 |
|---|---|---|---|
| P0-1 | 國字條號誤讀 | **中文數字有兩套互斥書寫體系——單位式（有十百千，位值由單位決定）與數字串式（無單位，逐字對位）——判別鍵是「有沒有單位字」，各用該體系的嚴格文法解析；混用、違反文法、含未知字元一律回 `None`，絕不猜。** | `backend/retrieval/lawtable.py:111-300`（`cn_to_int` :260、`_parse_unit_style` :176） |
| P0-1b | 解析失敗的去處 | **「讀不懂這個號碼」與「這個號碼不存在」是兩件不同的事，不能混。** 新增第五態 `unparseable`：黃燈、不阻擋、但整句降到「請人工判斷」層。既不猜一個整數（誤讀→綠燈），也不靜默丟掉（漏抓→「本句未附引用」→綠燈）。 | `backend/gate/citations.py:36-60`、`check_law` :198 |
| P0-2 | 主文封鎖繞過 | **送出封鎖不掛在文字判斷上。** `requires_human_conclusion=true`（規則算出的案件性質）⇒ 一律加 case 層 blocker `conclusion_requires_human`、`submit_allowed=false`。理由：C 型案件的定義就是「結論需要人來下」，那份草稿本來就不是可逕行送出的決定書。**這條判準完全不看句子寫了什麼**，所以任何寫法／origin／假出處都繞不過。片語層（法定處理結果 × 評價述語）退為**提示層**：把看起來像主文的句子標紅給人看，漏抓的後果是少標一個紅。 | `backend/nodes/n6_gate.py`（case 層封鎖）、`backend/gate/lamps.py`（片語層）、`backend/orchestrator/state.py:130`（不變式） |
| P0-2d | 免責框架自己變成繞法 | **第 1 層（法定處理結果）任何框架都不豁免。** 「按…者，…」通則引述殼曾被用來包裝完整主文並配真實條號，同時繞過片語層與兜底層。轉述豁免保留但只作用於第 2 層，且以**述語**位置判定：「訴願人請求撤銷原處分」（動詞＝轉述，豁免）vs「訴願人**之**請求為無理由」（名詞被評價＝機關的結論，不豁免）。 | `backend/gate/lamps.py:280-297`（`_is_attributed`） |
| P0-2e | 假出處當盾牌 | **「有出處」必須是「有可查證的出處」**：只有 `ok`／`amended` 算數。`out_of_scope`（庫外法規、**任何**函釋字號、白名單外判解）是系統明說「我驗不了」的東西——`check_directive` 對任何函釋一律回 `out_of_scope`，所以「編一個函釋字號」是零成本的。第三輪覆核用它示範了一句捏造主文如何拿到「有出處、字號已驗」。 | `backend/nodes/n6_gate.py`（`usable_cites`）、`backend/gate/lamps.py`（`tier_for_sentence`） |
| — | 誠實性：卷證直錄的宣稱 | **`WHY_RECORD` 不再宣稱「未經改寫或生成」。** `facts_excerpt` 由 N1（live 檔位是 LLM）透傳，全流程沒有任何一處把它與來源文件逐字比對。那是一句系統擔保不了的**事實宣稱**，對法制局評審講錯的代價不是技術債。改成講出限制的版本。 | `backend/gate/lamps.py`（`WHY_RECORD`） |
| P0-2b | 槽位不是判準 | **`slot` 是模型自己標的欄位；拿它當判準等於讓被管制的一方決定自己受不受管制。** 覆核在所有槽位、所有 origin（引擎算式句與佔位句除外）生效。 | `backend/nodes/n6_gate.py:132-199` |
| P0-2c | 不變式 | **不變式也不看字串**：C 型 ⇒ 必須存在 case 層封鎖且 `submit_allowed=false`；`conclusion` 槽位不得有模型生成句。前幾版的不變式重跑同一個偵測器，等於用同一把尺量兩次——三輪覆核都點過這件事。 | `backend/orchestrator/state.py:130` |
| P0-3 | 函釋漏抓 | **函釋的辨識鍵是「發文字號結構」＝（機關名｜完整發文日期）＋「X字第N號」，不是結尾那個「函」字。** 後綴（函／函釋／令／書函／公告／釋示）改為可選；與判解字號的區辨也是結構性的——判解是「年度＋字別」（沒有月日），函釋是「年月日」或緊鄰機關名，兩者互斥。 | `backend/gate/citations.py:81-142`（`find_directives` :127） |
| 4 | 判解／釋字國字 | **號碼字元類要涵蓋半形／全形／國字三種寫法，且用同一套 `cn_to_int` 解析；解析不出走 `unparseable` 黃燈，不靜默消失。** | `backend/gate/citations.py:67-79`、`check_precedent` :251、`check_interpretation` :311 |
| 5 | 無引用句誤歸「有出處」 | **「有出處」的定義是「這句話指得出它的出處」。** 模型寫的句子一個引用都沒抽到就指不出出處，依 CONSTITUTION §1 歸「請人工判斷」。`engine/rule/static`（算式即出處）與 `record`（卷證原文即出處）的出處是結構性的，不受此限。 | `backend/gate/lamps.py:64-95`（`tier_for_sentence` :74） |
| 6 | `resolved_id` × `laws[].id` | **跨模組比對一律用結構化的穩定鍵，不用顯示字串。** 新增 `Citation.ref_key = "法規名\|條號"`；對不到時黃燈＋具名 tag「⚠ 未能對回守門結果」＋進 blockers，不再靜默走 else 用 N4 自己的 `verified` 填燈號（那等於檢索替守門發燈）。 | `backend/gate/citations.py:156-169`、`backend/nodes/n6_gate.py:213-246` |

### 為什麼 P0-1 的舊修法比原本更糟（記錄，避免重蹈）

原本：抽不到國字條號 → 系統回「本句未附引用」→ 黃燈（**安全**的失敗）。
上一輪修完：「九九九」被讀成 9 → 命中真實存在的建築法第 9 條 → **綠燈**（系統對捏造條號主動背書）。
**把安全失敗換成不安全失敗，比沒修還糟。** 所以這輪的核心不是「多支援幾種寫法」，
而是「無法無歧義解析時必須回 `None`」——寧可交人工，不可猜。

---

## 二、驗收條件逐條狀態

### AC1 對抗 harness 涵蓋覆核者全部案例 — ✅

新檔 `backend/tests/test_gate_hardening.py`（**52 條，全綠**），分五區，每區對應一輪覆核。
最新的整體量測（第 4 版）：

- **756 組**「主文寫法 × 假出處 × origin」交叉組合 → **0 穿過**
- **63 句**主文寫法（四輪覆核累積）→ 片語層 **0 漏標**
- **20 句**全新真實理由段 → 片語層 **0 誤攔**（兜底層改為註記後不再硬擋）

挑幾條說明對位（行號會隨編輯漂移，以測試名稱為準）：

| 覆核案例 | 測試 | 預期結果 |
|---|---|---|
| 七三／九九九／一〇五／一二三／一Ｏ五／廿五／九佰九十九 | `test_cn_numeral_review_regressions` :164 | 73／999／105／123／105／25／999 |
| 上述寫法端到端四態 | `test_cn_article_numbers_end_to_end_states` :221 | 真條號綠、假條號**紅** |
| 「一百五」類省略式（第二輪） | `test_unit_omitted_numerals_are_ambiguous_and_refused` :634 | 一律 `None`（歧義不猜） |
| 「一百零五十」類跳級失效（第三輪） | `test_zero_placeholder_scope_is_enforced` :808 | 一律 `None`；合法跳級寫法不得誤判 |
| 15 種標準主文寫法（含空白排版） | `test_all_15_real_conclusion_forms_are_detected` :299 | 15/15 命中 |
| 第一輪打穿的 15 種繞法 | `test_review_bypass_forms_all_blocked` :584 | 每種 `submit_allowed=false` |
| 同上，**配真實引用**讓兜底層失效 | `test_review_bypass_forms_caught_by_pattern_layer_even_when_armed_with_citations` :595 | 片語層必須自己擋住 |
| 第二輪打穿的 16 種法定處理結果 | `test_statutory_disposition_forms_are_all_detected` :760 | 16/16 命中 |
| **交叉組合：主文 × 假出處 × origin（168 組）** | `test_conclusion_survives_every_shield_and_origin_combination` :766 | **0 穿過** |
| 假出處當盾牌 | `test_unverifiable_citation_is_not_a_valid_shield_for_the_backstop` :785 | 兜底層照樣攔 |
| 只引庫外法規 ≠ 有出處 | `test_out_of_scope_only_citation_is_not_sourced_tier` :794 | tier=請人工判斷 |
| 全槽位覆蓋 | `test_conclusion_block_covers_every_slot_not_just_conclusion` :349 | 5 個槽位全攔 |
| `…號書函`／括號／公告／國字號數／點式日期／`-1` 尾綴 | `test_directive_without_han_suffix_is_detected` :412、`test_directive_with_cn_number_and_dotted_date_is_detected` :659 | 各抽到 1 筆、黃燈、不誤生幽靈判解 |
| 釋字第七四七號、一一二年度判字第一二三號 | `test_precedent_and_interpretation_accept_cn_numerals` :433 | 正確解析且 kind 正確 |
| 國字年度判解不得被誤判成函釋 | `test_cn_year_precedent_is_not_misclassified_as_directive` :679 | kind=precedent |
| 無引用句不得歸「有出處」 | `test_sentence_without_citation_is_not_labeled_sourced` :504 | tier=請人工判斷 |
| 檢索鍵對不到要報錯 | `test_retrieval_lamp_uses_stable_key_and_reports_mismatch` :519 | 黃燈＋進 blockers |

國字數字用 **property test**，不是列例子：
- `test_cn_numeral_roundtrip_1_to_1200` :173 — 1..1200 兩種寫法都 round-trip。
- `test_cn_numeral_never_disagrees_with_independent_reader` :181 — 4000 個隨機 CJK 串與
  **測試自備的獨立實作**（遞迴切分，結構刻意不同於 `cn_to_int` 的線性掃描）差分比對。
  這條在三輪中**抓到 4 處真歧義**。⚠️ 第二輪覆核指出過一個真問題：獨立實作曾與實作
  **共犯同一個錯誤**（省略式），差分測試因此失效——已修正，兩實作重新獨立。
- 離線加跑（未進 repo，各約 20 秒）：30 萬隨機串零分歧、1..9999 全域 round-trip 零失敗。

### AC2 「建築法第九九九條」端到端紅燈阻擋 — ✅

`test_ac2_fabricated_cn_article_blocks_submission_end_to_end` :238。基底刻意用本來會通過的
`synthetic-ordinary-01`，證明是這一句把它翻掉：

```
基底 synthetic-ordinary-01            submit_allowed = True
注入「另參建築法第九九九條之規定。」後  submit_allowed = False
cites   = [('建築法第999條', 'missing', 'r')]
blockers = [('citation_missing', 's7')]
```

並斷言 `citations[]` 中不得出現 `建築法第9條`（覆核實測到的誤讀綠燈）。
第二輪的同類案例「建築法第一百五條」另有 `test_unit_omitted_article_does_not_get_green_light` :646。

### AC3 主文寫進 reasoning 被攔 — ✅

`test_ac3_conclusion_bypass_blocked_end_to_end` :383：

```
'綜上所述，本件訴願為無理由，應予駁回。'  submit_allowed=False  lamp=r  tier=請人工判斷
'原 處 分 撤 銷 。'                    submit_allowed=False  lamp=r  tier=請人工判斷
```

三輪覆核的全部繞法（15 種 + 16 種 + 168 組交叉）現況 0 穿過，見 AC1 表。

### AC4 全套測試與回歸 — ⚠️ 部分（121/122，唯一未過項為**加固前就存在**的既有失敗）

```
期間引擎搬遷與測試向量   7/7
六節點單元測試          38/38
端到端整合測試          22/22
守門加固對抗測試        52/52   ← 新增
紅線靜態掃描：
  ok    secret／禁用雲端字樣
  FAIL  prototype/ 未被變更          ← 見下
  ok    核心與測試路徑零外部依賴（backend/api/ 為具名例外）
失敗：121/122 通過
```

- **原 70 條全數通過**。
- **`prototype/ 未被變更` 是加固前就已經紅的**：在 branch 起點 `d4c43d8`（尚未動任何一行）
  跑 `run_all.py` 就是 **69/70、同樣 11 項**。原因是 `run_all.py:81` 的 `BASE_COMMIT`
  仍指向 `ce558a8`，而 main 之後已合入 prototype/ 變更。**我沒有動這個基準值**——
  那是紅線量尺，改它就是「調整量尺讓自己過關」。第二輪覆核獨立確認了這點
  （`git diff d4c43d8..HEAD -- prototype/` 為空、`BASE_COMMIT` 未變、掃描規則未鬆綁）。
  ⚠️ 但「跑全套不會綠」這件事 demo 前一定會被看到，請整合 agent 或 Ci 決定怎麼處理（見判斷卡 5）。
- `synthetic-ordinary-01` 仍 `submit_allowed=true`、燈號 `{r:0, y:0, g:13}`、
  三層 `{可驗算:6, 有出處:7, 請人工判斷:3}`；`synthetic-blocked-01` 仍 `false`、
  `{r:2, y:0, g:10}`、`{6,4,3}`、blockers 僅 `citation_missing`——**與加固前 baseline 逐行一致**
  （用 probe 腳本 diff 過，只有新增欄位造成的 payload hash 變化）。
  `test_official_cases_behaviour_unchanged` :824 釘住。
- Deterministic：同案例跑 5 次去掉時間欄位後 hash 一致
  （`test_pipeline_is_still_deterministic` :838）。

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

## 三、給 Ci 的判斷卡

前兩張是我刻意沒做的設計決定；第 3–6 張是三輪覆核逼出來、**必須有人拍板**的產品問題。

### 判斷卡 1：C 型結論封鎖的判準幾乎沒有鑑別力

**問題**：覆核實測把 `synthetic-blocked-01` 的事實爭點訊號全部拿掉，**仍然封鎖**。
真正在作用的是 `requires_human_conclusion`（`backend/gate/lamps.py:116-153`）的 fail-safe 分支：
「案型不在 `SUBSTANTIVE_TYPES` 且程序上沒有可直接算出的不受理事由 → 封鎖」。
也就是說 demo 案例被封鎖的實質理由是「未逾期」，不是「偵測到事實認定爭點」。
評審問「換個案型會怎樣」會露餡。

**選項**：A 維持現狀但 demo 主動說明；B 判準不動，`human_conclusion_signals` 區分
「因事實爭點封鎖」與「因無法判斷保守封鎖」，畫面分開顯示（改動小）；
C 讓事實爭點成為獨立封鎖理由並準備不封鎖的對照案例（最有說服力，吃 30h 預算）。
**建議 B（現在做）＋ C（時間允許）。不做就等於選 A，請明確拍板。**
我沒有動 `SUBSTANTIVE_TYPES` 與 `requires_substantive_review`。

### 判斷卡 2：N4 的檢索是循環佐證（查詢句從答案反推）

**問題**：`graph.py:86→128` 從 fixture 草稿的 `basis` 蒐集法條字串當 N4 查詢句
（`n4_retrieval.py:53`）。「查什麼」照著「答案要引用什麼」倒著填，規格要求的 `build_query()`
實務上是死碼。評審問「檢索怎麼決定要查哪幾條」，現在的答案是「照草稿要引用的填」。

**選項**：A 誠實標註（`retrieval_meta` 加 `query_source`，畫面明說，零風險半小時）；
B 改用 `build_query(state)`，接受草稿引用可能查不到——那反而讓引用四態真的動起來；
C 兩條都跑取聯集，對不上的地方正好是守門要講的故事。
**建議至少做 A（今天）。不做 A 等於在 demo 裡默示這是獨立檢索——那是誠實性問題。**
檔案歸整合 agent，我沒有動。

### 判斷卡 3：C 型案件一律不得送出——**我已經照 (b) 做了，請你追認或否決**

原本這張卡問的是「C 型案件到底允不允許沒有引用的句子」。第 4 版**已經做了決定**，
因為任務指示明寫「預設 fail-safe：拿不準就封鎖（needs_human），絕不放行」：

**現況：`requires_human_conclusion=true` ⇒ 一律 `submit_allowed=false`**，
以一條 case 層 blocker（`conclusion_requires_human`）表示，不逐句累加。

理由：C 型的定義就是「系統拒絕下結論」，一份沒有結論段的決定書本來就不是成品。
而且這條判準不看文字，是唯一擋得住所有主文繞法的機制——四輪覆核打穿的每一種寫法
（含 25/30 捏造成卷證事實、一個編出來的函釋字號當盾牌）在它面前都無效。

**但這是產品定位決定，你可以否決**：
- 若 C 型的預期輸出是「一份可送出的理由段草稿」，這條要退場或改成只警示——
  那樣就回到片語層當防線，而片語層在四輪裡被打穿了三輪。
- 若是「一張交接卡 + 需人工接手的草稿」（我的判斷），現況正確，但**畫面與文案要改**：
  現在會列一條 P0 blocker，UI 應該呈現成「本案需人工接手」而不是「錯誤」。前端不歸我。

附帶：句子層的「無出處」不再逐句進 blockers（第三輪覆核量到會擋掉 22 句真實理由段中的
20 句，真訊號被雜訊淹掉），改為降層到「請人工判斷」+ `why` 說明。

### 判斷卡 4：純引述法定處理結果的句子會被誤攔（刻意的取捨）

「按訴願法第79條規定，訴願無理由者，應以決定駁回之」是純引述法條，現在會被攔。
原因：覆核用**同樣的殼**包裝完整主文並配真實條號
（「按訴願法第81條規定，訴願有理由者，原處分撤銷，並命…另為適法之處分」），
兩者結構上無法區分，只能二選一。**我選攔**（誤攔＝多按一次確認；漏放＝系統替一份
沒人看過的法律結論背書）。若你認為引述句被攔會嚴重干擾承辦人，這條可以翻——
但翻了就等於把那條繞法重新打開，請明確知道代價。
釘在 `test_pure_statutory_quotation_of_a_disposition_is_deliberately_over_blocked` :708。

### 判斷卡 5：`prototype/ 未被變更` 紅線檢查目前恆紅

加固前就紅（`BASE_COMMIT` 過期，main 已合入 prototype 變更）。我沒動量尺。
但 demo 前跑 `run_all.py` 一定會看到一個紅的紅線檢查。
**選項**：更新 `BASE_COMMIT` 到 merge 後基準（弱化這條紅線的意義）／明確標記它已失效並
在報表上分開顯示／保持現狀但交接時講清楚。`prototype/` 歸整合 agent，請他們或你決定。
**不要靜靜留著一個永遠紅的檢查。**

### 判斷卡 6（本輪新增的介面變更，整合 agent 必須知道）

1. **引用多了第五態 `unparseable`**（`lamp="y"`、`mark="？ 無法解析"`、不阻擋）。
   前端若有硬寫死的四態 switch 會掉進 default。`docs/architecture.md §8.1` 仍寫四態，
   要不要同步改文件請拍板（我沒改該文件）。
2. **`citations[]` 多了 `ref_key`**（`"法規名|條號"`，非法條類為 `null`）；
   **`retrieval.laws[]` 多了 `gate_ref_key`**。純新增。
3. **N6 依賴 `retrieval.laws[]` 帶 `law` 與 `article` 兩個結構化欄位**（`n4_retrieval.py:81-82`
   目前有）。若整合 agent 改動 N4 的 `laws[]` 形狀拿掉這兩欄，N6 會把每筆判成
   「未能對回守門結果」並阻擋送出——這是刻意的 fail-safe，但要事先知道。
4. **`blockers[]` 多了兩種 reason**：`conclusion_requires_human`（P0，`sentence_id` 為
   `null`，**case 層**，只在 C 型案件出現，見判斷卡 3）、`retrieval_law_not_matched_by_gate`（P1）。
   兩者都會擋送出。`conclusion_requires_human` 在 UI 上應呈現為「本案需人工接手」，
   不是紅色錯誤；若 `retrieval_law_not_matched_by_gate` 在正常案例誤觸，先查 N4 的
   `laws[]` 欄位，**不要直接把它降級成 warning**。
5. **三層歸屬變嚴**：只引到庫外法規／讀不懂號碼的句子不再是「有出處」，改列「請人工判斷」。
   前端若依 tier 分欄顯示，該欄的句數會變多。

### 判斷卡 7（第四輪覆核抓到，**最高優先**）：C 型封鎖的開關可被上游抽取錯誤關掉

**問題**：`requires_human_conclusion`（`backend/gate/lamps.py`）的 fail-safe 只在
`art77.clause` 為空時生效。一旦案件被判逾期（`clause="77-2"`），
`requires_substantive_review` 變 `False`、`substantive` 隨之 `False`；同時
`fact_issue_signals.json` 的 `applies_to.art77_clause` 濾網會把 `penalty_limitation`
（severity=high）整條濾掉，`high` 也空了 → **封鎖關閉**。

而 `d2`／`d3`／`service_method` 全部是 `intake_origin="llm"`。覆核實測：
**只改 N1 抽的一個日期欄位 `intake.d3`**，就把一件卷證明寫裁處權時效爭議的 C 型案
降級成非 C 型 → `needs_human=False`、交接卡全空、26/26 捏造主文與捏造卷證
拿到綠燈＋「有出處」＋`submit_allowed=True`＋0 blockers。

方向是反的：**模型把日期讀錯，不該讓系統更寬鬆，應該更保守。**

**為什麼我沒修**：任務明確指示「不要改 C 型封鎖的判準設計（`SUBSTANTIVE_TYPES`、
`requires_substantive_review`），那是 Ci 要拍板的設計決定」。且 `n3_procedure.py` 與
`fact_issue_signals.json` 不在我擁有的檔案清單內。

**選項**：
- **A（覆核建議的最小修法）**：`requires_human_conclusion` 對 substantive 案型 fail-closed
  ——即使逾期也不解除封鎖。代價：「逾期 + 實體案型」的案子從此都要人寫結論。
  **這是法律面的取捨，需要 qa-legal／法制局側確認可否接受。**
- **B**：要求 `intake_origin['d3'] == 'human'`（人工確認過的日期）才承認 `clause`，
  否則走 fail-safe。代價：demo 時逾期案要先按一次「確認日期」。
- **C**：不改，但 demo 口徑必須說「C 型判定來自模型抽取的日期與案由，尚未經人工確認」。

**我的建議：B**（它把「未經確認的模型輸出」與「可解除封鎖的權力」切開，
而不需要改法律判準）。**不做選擇就等於選 C，那麼口徑必須嚴格執行。**

### 判斷卡 8：`record` 通道無條件發綠燈，與它自己的 `why` 打架

`origin="record"` 的句子直接 `lamp="g"`＋tier「有出處」，完全不看內容也不看引用。
但同一句的 `why` 現在寫的是「本階段尚無法與來源文件逐字比對」——**燈號說綠、理由說驗不了**。
覆核實測：一句完整法律結論只要被標成卷證直錄，在非 C 型案件下就是綠燈＋有出處。

**為什麼我沒修**：改成黃燈會動到 `synthetic-ordinary-01` 的燈號分布（13 綠 → 11 綠 2 黃），
違反本任務 AC4 明列的「燈號分布不變」。

**選項**：A 改黃燈＋「待覆核原文」（一行改動，與 `WHY_RECORD` 對齊，但動到 demo 畫面）；
B 維持綠燈但在 UI 標註「來源為卷證，未逐字驗證」；C 補 source-span 驗證（正解，Phase 0 無卷證可做）。
**建議 A 或 B，B 較不動 demo。不要維持現狀的「綠燈 + 驗不了」自相矛盾。**

### 判斷卡 9：`submit_allowed` 目前沒有執行點

`backend/api/` 沒有送出端點（只有 health／cases／runs／deadline），沒有任何程式讀
`submit_allowed` 去擋任何動作。它是一個**訊號欄位**。
（`n6_gate` 舊 docstring 宣稱「送出端點回 409」，已改掉並加測試擋住復發。）

**選項**：A 補一個最小送出端點讀 `submit_allowed` 回 409（`backend/api/` 不歸我）；
B 不補，但 demo 與簡報一律說「標記為不得逕行送出」，**不得說「系統會擋下送出」**。
**兩者擇一，不能既不補又照舊講「會擋」。**

---

## 四、沒做完 / 未驗證的（誠實條款）

| 項目 | 狀態 |
|---|---|
| **`facts_excerpt`（`origin="record"`）零逐字驗證** | ⚠️ **已知控制缺口，但不再影響送出**。第 4 版把送出封鎖搬到 case 層之後，C 型案件不論 origin 都擋得住（覆核的 25/30 捏造卷證結論現在全部無法送出）。**剩下的缺口是非 C 型案件**：一句被模型捏造成「卷證事實」的內容，系統沒有任何機制驗證它真的出現在來源文件（`facts_excerpt` 由 N1 透傳，全 repo 無 source-span 比對）。<br>**已做的誠實性修正**：`WHY_RECORD` 不再宣稱「未經改寫或生成」。<br>**正確的解法是不同的控制**：source span 驗證，Phase 0 沒有真實卷證可驗。列為後續工作，且 **demo 不得宣稱卷證直錄已驗**。 |
| 主文偵測的真實誤攔率 | ⚠️ 片語層在四輪覆核累積的 **20 句全新真實理由段上誤攔 0**，另有 1 句刻意過攔（判斷卡 4 的純引述）。但**沒有真實決定書語料**，真實誤攔率未知。片語層現在只影響燈號，不影響送出。 |
| 函釋 regex 對真實公文字號的覆蓋率 | ⚠️ 驗了 10 種構造（含國字號數、點式日期、`-1` 尾綴、書函／公告／釋示、無後綴），沒有真實函釋語料。 |
| `docs/architecture.md §8.1` 的「四態」敘述 | ⬜ 未更新（實作已是五態）。文件不在我的檔案清單內。 |
| 前端對 `unparseable`／新 blocker reason 的呈現 | ⬜ 未驗證。`prototype/` 不歸我。 |
| `backend/api/` 對新欄位的序列化 | ⬜ 未驗證（沒碰 api、沒起服務打端點）。新欄位都是純量／字串，理論上無風險，但**這是推論不是驗證**。 |
| `assert_verified_invariant` 與偵測器同源 | ✅ **已修**。第 4 版起不變式不再重跑 `detect_conclusion_like`，改成只看案件性質（C 型 ⇒ 必須有 case 層封鎖且 `submit_allowed=false`）。三輪覆核都點過「用同一把尺量兩次」，這裡拆開了。 |
| 片語層清單本質上不可能窮舉 | ⚠️ **明講**：四輪覆核每一輪都找得到新寫法（最近一輪是 §83 情況決定、§93 停止執行、§84 損害賠償）。程式註解已寫明這份清單不是也不可能是窮舉。**這就是為什麼送出封鎖不掛在它上面。** demo 不得宣稱「主文偵測擋得住」，只能說「片語層是提示、case 層是阻擋」。 |
| C 型判準鑑別力、N4 循環佐證 | ⬜ 刻意未動，見判斷卡 1、2。 |

## 五、Demo 口徑（第四輪覆核列為硬性條件，請照抄）

覆核給的是 **Go-with-caveats**：兩個正式案例跑出來的畫面是誠實的、可以上台，
所有破口都需要改測資才會觸發，不會在照稿 demo 時自己冒出來。但下列三條是硬性的：

1. **不得說**「任何寫法都繞不過」「系統會擋下送出」。
   **要說**「C 型案件會被標記為不得逕行送出；該標記目前是訊號，尚未接到送出流程；
   C 型的判定來自模型抽取的日期與案由，尚未經人工確認」。
2. **不得**把 `synthetic-ordinary-01` 的「13 句全綠、允許送出」當成守門成功的證據——
   那份案子是非 C 型，本版不對它做結論內容偵測（只留註記）。要放這張畫面就要一起說明。
3. **不得**把 `submit_allowed` 在 UI 上做成「可送出」按鈕或等義文案（見判斷卡 9）。

若團隊要把「結構性守門」當成主打賣點，覆核的判斷是 **No-Go**，直到判斷卡 7／8／9 處理完。

---

## 六、一句話總結

這輪最重要的改動不是任何一條規則，而是**把防線換了位置**：從「判斷這句話是不是主文」
（字串比對，四輪被打穿三輪）改成「這個案件需不需要人下結論」（規則判定，改草稿文字繞不過）。

但第二重要的，是把**我自己寫下的兩句擔保不了的話**拿掉：程式註解曾宣稱
「送出端點回 409」（沒有那個端點）與「沒有任何寫法能繞過」（開關上游是模型輸出）。
那兩句比任何一個漏抓都危險——漏抓是能力不足，宣稱不實是對法制局講錯話。
現在有一條測試在擋這類宣稱復發。

留給接手的人：**如果有人要放寬 case 層封鎖（判斷卡 3），或不處理判斷卡 7，
請知道你把防線交還給誰——交還給那個在四輪裡被打穿三輪的片語層。**
