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
> | 我的第 3 版（現況） | 前兩輪全部 168 組交叉組合 **0 穿過**；等待第三輪獨立覆核 |
>
> 每一輪被打穿的共同教訓：**只要判準的最後一道防線是「比對字串」，就一定有盲點。**
> 現在真正扛住的是第 3 層兜底（不看字串），片語層只是縱深防禦。
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
| P0-2 | 主文封鎖繞過 | **三層縱深，扛住的是第三層。** ①法定處理結果動詞：清單逐條標註訴願法出處（§60/§61/§77/§79/§81/§82），**指不出條號就不屬於這一層**，句段內出現即命中、無字元視窗；②評價性述語需與案件標的緊鄰 ≤4 字；③**兜底：C 型封鎖下，模型寫的、沒有任何可用引用的句子一律紅燈交人工**——這層不看字串，所以沒有寫法能繞過。偵測前正規化（刪所有空白含全形、統一標點、丟括號），排版不是繞法。 | `backend/gate/lamps.py:222-250`（法定處理結果）、`:252-266`（評價述語）、`:299`（`detect_conclusion_like`）、`backend/nodes/n6_gate.py:155-179`（兜底層） |
| P0-2d | 免責框架自己變成繞法 | **第 1 層（法定處理結果）任何框架都不豁免。** 「按…者，…」通則引述殼曾被用來包裝完整主文並配真實條號，同時繞過片語層與兜底層。轉述豁免保留但只作用於第 2 層，且以**述語**位置判定：「訴願人請求撤銷原處分」（動詞＝轉述，豁免）vs「訴願人**之**請求為無理由」（名詞被評價＝機關的結論，不豁免）。 | `backend/gate/lamps.py:280-297`（`_is_attributed`） |
| P0-2e | 假出處當盾牌 | **「有引用」必須是「有可用的引用」**：`unparseable`／`missing` 不算出處，不能用來讓兜底層失效。連帶：只引到庫外法規的句子不得標「有出處」——那等於替系統沒看過的東西背書。 | `backend/nodes/n6_gate.py:155`、`backend/gate/lamps.py:74-95` |
| P0-2b | 槽位不是判準 | **`slot` 是模型自己標的欄位；拿它當判準等於讓被管制的一方決定自己受不受管制。** 覆核在所有槽位、所有 origin（引擎算式句與佔位句除外）生效。 | `backend/nodes/n6_gate.py:132-199` |
| P0-2c | 不變式 | **不變式判的是「存在未被攔下的主文句」，不是「conclusion 槽位有沒有東西」。** 已被 N6 標紅並列入 blockers 的不炸（流程要跑完讓人看到攔了哪句），沒攔下的才炸。 | `backend/orchestrator/state.py:130-167` |
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

新檔 `backend/tests/test_gate_hardening.py`（**41 條，全綠**），分三區：第一輪覆核案例、
第二輪覆核案例、第三輪覆核案例。挑幾條說明對位（行號為當前檔案）：

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

### AC4 全套測試與回歸 — ⚠️ 部分（110/111，唯一未過項為**加固前就存在**的既有失敗）

```
期間引擎搬遷與測試向量   7/7
六節點單元測試          38/38
端到端整合測試          22/22
守門加固對抗測試        41/41   ← 新增
紅線靜態掃描：
  ok    secret／禁用雲端字樣
  FAIL  prototype/ 未被變更          ← 見下
  ok    核心與測試路徑零外部依賴（backend/api/ 為具名例外）
失敗：110/111 通過
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

### 判斷卡 3（最重要，覆核逼出來的）：C 型案件到底允不允許「沒有引用的句子」？

**這是產品決策，不是工程細節，我不能替你決定。**

現況：C 型封鎖下，模型寫的、沒有任何可用引用的句子一律紅燈 + 進 blockers + 擋送出
（`n6_gate.py:155-179`）。這一層是**唯一擋得住所有主文繞法的機制**——三輪覆核打穿的
每一種寫法都沒有引用，因為主文本來就不引法條。

代價：覆核用 16 句真實理由段量測，**9 句（56%）會被這層擋下**，包括
「上開事實有現場照片、稽查紀錄表附卷可稽」「本會於某日通知訴願人到會陳述意見」
這種純敘述句。因為 `submit_allowed = not blockers`，**任何 C 型案件只要理由段有一句
沒引用，就永遠送不出去**。

現有 demo 看不到這個問題，只因為 `synthetic-blocked-01` 的 fixture 每一句理由都剛好帶
`basis`。**fixture 的形狀正好把這個問題遮住了**——這點是覆核發現的，值得記住。

**要拍的板**：C 型案件的預期輸出是
- **(a) 一份可送出的理由段草稿**（那麼這層要退場或改成只警示不擋，安全性隨之下降），還是
- **(b) 一張交接卡 + 全紅的理由段，本來就不該送出**（那麼現況正確，但畫面與文案要改成
  「本案需人工接手」而不是列一排 P0 錯誤）？

**我的建議是 (b) + 改文案**：C 型的定義本來就是「系統拒絕下結論」，那份草稿本來就不是
成品。但這是產品定位問題，請你或法制局側定。

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
4. **`blockers[]` 多了兩種 reason**：`retrieval_law_not_matched_by_gate`（P1）、
   `unsourced_sentence_while_conclusion_blocked`（P0，只在 C 型案件觸發，見判斷卡 3）。
   兩者都會擋送出。若在正常案例誤觸，先查 N4 的 `laws[]` 欄位，**不要直接把它降級成 warning**。
5. **三層歸屬變嚴**：只引到庫外法規／讀不懂號碼的句子不再是「有出處」，改列「請人工判斷」。
   前端若依 tier 分欄顯示，該欄的句數會變多。

---

## 四、沒做完 / 未驗證的（誠實條款）

| 項目 | 狀態 |
|---|---|
| **`origin="record"` 不受兜底層管轄** | ⚠️ **已知殘餘風險**。兜底層只管 `origin=="llm"`；`facts_excerpt`（N1 由模型從卷證抽取）標的是 `record`。覆核用它繞過兜底層，那 4 個例子現在被片語層接住了，但**結構性的洞還在**：一句被模型捏造、又剛好躲開片語層的「卷證事實」不會被兜底層攔。<br>**為什麼沒用擴大兜底層解決**：那會讓 C 型案件的卷證直錄事實全部變紅燈（blocked-01 會多 2 個紅），畫面上「引用的卷證事實是紅的」對評審是更難解釋的行為，而且那是判斷卡 3 的同一個產品決策。<br>**正確的解法是不同的控制**：record 的正確不變式是「這句確實出現在來源文件」（source span 驗證），Phase 0 沒有真實卷證可驗。列為後續工作。 |
| 主文偵測的真實誤攔率 | ⚠️ 片語層在覆核的 18 句真實理由段上誤攔 **1/18**（就是判斷卡 4 那句純引述）。但**沒有真實決定書語料**，真實誤攔率未知。兜底層的 9/16 見判斷卡 3。 |
| 函釋 regex 對真實公文字號的覆蓋率 | ⚠️ 驗了 10 種構造（含國字號數、點式日期、`-1` 尾綴、書函／公告／釋示、無後綴），沒有真實函釋語料。 |
| `docs/architecture.md §8.1` 的「四態」敘述 | ⬜ 未更新（實作已是五態）。文件不在我的檔案清單內。 |
| 前端對 `unparseable`／新 blocker reason 的呈現 | ⬜ 未驗證。`prototype/` 不歸我。 |
| `backend/api/` 對新欄位的序列化 | ⬜ 未驗證（沒碰 api、沒起服務打端點）。新欄位都是純量／字串，理論上無風險，但**這是推論不是驗證**。 |
| `assert_verified_invariant` 與偵測器同源 | ⚠️ 它重用 `detect_conclusion_like`（`state.py:130`），片語層漏掉的它也漏掉，**不是獨立的第二把尺**。真正的第二道防線是兜底層（不同判準）。覆核兩輪都點出這件事，這裡明講，不要誤以為它是獨立驗證。 |
| C 型判準鑑別力、N4 循環佐證 | ⬜ 刻意未動，見判斷卡 1、2。 |

## 五、一句話總結

這輪改的是三個**判準的形狀**：數字解析從「盡量猜」改成「讀不懂就交人工」；
主文封鎖從「片語清單」改成「三層縱深，最後一層不看字串」；跨模組比對從「顯示字串巧合」
改成「結構化穩定鍵，對不到就報錯」。

但更該記住的是過程：**前兩輪我都宣稱修好了，前兩輪都被獨立覆核打穿。**
第一輪敗在「以為換個大一點的片語表就是結構性」，第二輪敗在「新加的兩個機制自己變成繞法」。
真正扛住的那一層（無可用引用即交人工）之所以有效，正因為它**不判斷句子寫了什麼**。
如果之後有人要放寬它（判斷卡 3 很可能會），請同時知道：放寬它，前面兩層就是唯一防線，
而那兩層在三輪裡被打穿過兩次。
