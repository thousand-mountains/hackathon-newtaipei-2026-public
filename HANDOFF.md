# HANDOFF — 訴願案件審理 AI 輔助 prototype（2026-09-05 定稿）

> **這份是唯一真相。** `HANDOFF-PHASE0.md`／`HANDOFF-GATE.md`／`HANDOFF-INTEGRATION.md`
> 是三條分支各自的過程紀錄，頂部都已標「已被本檔取代」，保留供追溯——
> 裡面的測試計數、行號、「還沒做」清單都已過時，**引用它們之前先看這裡**。
>
> branch `mission/hack-integration-20260905`。**沒有 push、沒有開 PR、沒有動任何 remote。**
>
> **2026-09-07 追加**：`mission/hack-bedrock-agents-20260906` 分支（自上者切出）把六節點接上
> Bedrock 的程式路徑做完了，紀錄在**本檔 §5**。§0–§4 是 2026-09-05 的狀態，**§1「沒做」那張表
> 有幾列已被 §5 更新**，該處已就地標註；兩處說法不同時以 §5 為準。

---

## 0. 一分鐘現況

一個指令起整個平台（前端 + API 同一個 process）：

```bash
python3 prototype/build.py     # static/ 或 data/ 改過才需要重跑
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart -- \
    python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8080
```

| 位址 | 是什麼 |
|---|---|
| <http://127.0.0.1:8080/> | 五步動線 UI，開起來就是 **live 模式**（頁首徽章寫著） |
| <http://127.0.0.1:8080/?case=synthetic-blocked-01> | 換案例（頁面上也有下拉選單） |
| <http://127.0.0.1:8080/api/docs> | OpenAPI |
| `file://…/prototype/dist/index.html` | 斷網備援：**離線 fixture 模式**，徽章會改，不會假裝是後端結果 |

```
$ python3 backend/tests/run_all.py     → 全綠：162/162 通過（exit 0）
$ node prototype/tests/parity.mjs      → ✓ JS 引擎 16/16 向量全過（與 Python 零分歧）
$ uv run --with pytest -- python -m pytest prototype/tests -q   → 4 passed
$ python3 prototype/build.py           → dist/index.html 139 KB
```

162 的組成：期間引擎 8／六節點單元 38／端到端 32／CASE payload 契約 24／守門加固 57／紅線靜態掃描 3。

---

## 1. 這個系統實際上做了什麼（與沒做什麼）

**真的在跑的**

| 東西 | 實作 |
|---|---|
| 期間計算 | 純函式規則引擎，同輸入必同輸出，16 條向量鎖住，Python／JS 兩份實作零分歧 |
| 引用查核 | 逐句抽引用 → 對 `laws-snapshot.json` 定四態（在庫／已修正／庫外未驗證／查無） |
| 法規檢索 | **獨立**檢索：查詢句只由案情（N1 卷證、N2 案型、N3 程序結果）組成 |
| 結論封鎖 | C 型案件不生成結論段，改出交接卡；封鎖判準不看句子文字，改草稿文字繞不過 |
| 送出守門 | `POST /api/cases/{id}/submit` **重跑一次六節點**再判斷，不通過回 **409** |
| 分層誠實 | 每個句子帶 origin；燈號／why／引用狀態的 origin 恆為 `rule`，機器檢查 |

**沒做（不要以為做完了）**

| 項目 | 為什麼 |
|---|---|
| 真實 Bedrock 呼叫（N1／N5 live 分支） | 無 AWS 憑證與 model access。非 fixture 模式一律 raise／501，沒有假裝能跑 ——**§5 更新：程式路徑已做完，但真 Bedrock 仍一次都沒打過（帳號未開通）** |
| 真實相似案檢索、kNN 分類 | 無賽方資料集。誠實回空 + 標「庫外，未驗證」 ——**§5 更新：Managed KB 檢索器已實作，未接過真 KB** |
| PDF 視覺抽取 | 所以**實體法條號抽不到**（見判斷卡 B），上傳的檔案完全不讀 ——**§5 更新：上傳與卷證文字路由已做，視覺讀取未實測** |
| SSE 事件流（§6.1 2b） | `POST /runs` 同步跑完就回。接上模型（25–45 秒）後必須改 ——**§5 更新：改成 202＋輪詢，SSE 端點仍未做** |
| §6.1 另外 4 支端點 | 已做 6 支：`/`、health、cases、runs、submit、deadline。intake 補正、citecheck、confirm、redraft 未做 ——**§5 更新：另加 `POST /api/cases`（上傳建案）與 `GET /api/runs/{id}`（輪詢）；那 4 支仍未做** |
| 逐句「已確認」寫回後端 | 步驟 3 的紅燈確認只存在瀏覽器記憶體，重整就沒了 |
| 冪等 `run_id` | 每次 `POST /runs` 都真的重跑。接 Bedrock 後**每次重整都會燒模型費用** |
| 容器映像檔帶前端 | `Dockerfile` 只 `COPY backend/`，容器裡 `GET /` 回 503（見 `backend/DEPLOY.md` §4.5） |
| 洗防法修法日期矛盾、示範案號 | **待 Ci 裁決，刻意原樣保留** |

---

## 2. 本輪（第五輪）做的六件事

### 2.1 判斷卡 7：收文頁人工確認後，程序結果才算數 — Ci 拍板

**破口**（覆核實測）：只要改 `intake.d2` 或 `d3` 讓案件變逾期 → `art77.clause` 變 `77-2`
→ `requires_substantive_review` 變 False → `requires_human_conclusion` **True 翻 False**
→ 捏造的主文拿綠燈、列進「有出處」、`submit_allowed=True`。
而 live 檔位那兩欄的 origin 是 `llm`。

**修法**：`DEADLINE_INPUT_FIELDS`（`d2`／`d3`／`service_method`／`transit_days`／`interested_party`）
**全部**要 `intake_origin == "human"`，才可以拿程序結果解除結論封鎖；否則 fail-safe 維持封鎖，
交接卡加一條「抽取日期未經承辦人確認，結論段維持交人工」。

> 為什麼是五欄不是只有 `d2`／`d3`：`compute()` 的每個參數都會改變期滿日。
> 只確認日期、讓送達方式維持模型抽取，等於在承辦人沒看過的欄位上宣稱「已確認」——
> 同一個洞換個位置。所以收文頁**新增了送達方式／在途期間／利害關係人三個欄位**，
> 承辦人看得到才算數。

各層的動線：

| 層 | 做法 |
|---|---|
| 後端 | `run_case(..., confirmed_intake=)`；白名單制，不在 `CONFIRMABLE_INTAKE_FIELDS` 的鍵直接 ValueError |
| API | `POST /runs` 與 `/submit` 接選填 body `{"confirmed_intake": {...}}` |
| payload | `intake_confirmed[]` 列出誰被確認過；`intake_origin[f]` 翻成 `human`；`screen.procedural_inputs_confirmed` |
| 前端 | 按「啟動幕僚團分析」＝ 承辦人確認 → 帶著表單值重跑一次後端；畫面常駐「已由承辦人確認（N 欄）」 |
| CLI | `--confirm-intake`（**預設不加＝沒人確認**） |

**新的誠實基準**：`synthetic-ordinary-01` 未確認時 `submit_allowed=False`。
那不是回歸，是修掉破口的必然結果。所有相關測試改成「未確認→封鎖、確認→允許」兩個狀態都測，
並在測試註解寫明原因。

對抗測試 `test_changing_extracted_dates_cannot_unlock_the_conclusion_block`：
兩組改過的日期都必須維持封鎖，確認後才解除。

**`origin_registry` 的誠實註解原本指錯人**：它說封鎖開關的上游是 `case_type`。
實測不是——改 `case_type` 關不掉封鎖（fail-safe 會接住），關得掉的是日期。
已改成指向 `screen.deadline.*`／`screen.art77.*`／`screen.requires_human_conclusion`
（三者標 `llm_derived`，不再整批標 `rule`）。

### 2.2 離線備援文案改誠實 — Ci 拍板

`prototype/data/case-demo.json` 六處點名的宣稱全部改掉：

| 原文 | 改成 |
|---|---|
| 解析 4 份卷證，OCR 信心值 0.97 | 示範案件內建 4 份卷證中繼資料（本 demo 不讀取上傳檔內容，未執行 OCR） |
| 向量檢索 11 部法規 · 命中 6 筆 | 對離線法規快照做**條號查表** · 命中 6 筆（查表，非向量檢索） |
| 比對 101 件歷史決定書 · 相似度 ≥0.72 者 5 件 | 相似歷史決定書：**庫外，未驗證**（本機無資料集，未執行任何相似度計算） |
| 8 款不受理事由逐款比對，全數不該當 | 只自動判定可由日期算出的第 2 款；其餘 7 款屬法律判斷，未自動比對 |
| 可與卷證原文逐字比對 | 標記為卷證直錄（非模型撰寫）；**本 demo 未執行逐字比對**，請承辦人核對 |
| 已比對修正前後條文全文 | 快照只記錄修正對照（舊條次→新條次）；條文全文不在快照內，實質有無變動請人工查證 |

**沒動案號與洗防法日期**（那兩件仍待 Ci）。已重跑 `build.py`。

### 2.3 送出端點：讓「不得送出」變成真話

`POST /api/cases/{case_id}/submit`：

- **後端自己重跑六節點再判斷，完全不採信前端**（`recomputed_by: "backend"`）
- 不通過 → **409** ＋ 完整 `blockers`
- 通過 → 200 ＋ 本機收據，寫 `backend/output/submissions.jsonl`（gitignored），
  收據明寫 `external_effect: "none"`

前端「送出審議」改打這支；409 時停在燈號頁顯示「後端已拒絕送出（409）」。
完成頁的「已陳送訴願審議委員會，並排入最近一次審議會議程」**已移除**，
改成「已記錄為送出（後端回 200）」＋ 收據內容（run_id／時間／外部效果 none／寫入路徑）。

**headless 實測（刻意繞過前端）**：把 blocked 案例的送出鈕用 JS 改成 enabled 再點，
後端照樣回 409、畫面顯示拒絕、按鈕重新變灰。這證明前端的 disabled 只是提示，
真正的守門在後端。

### 2.4 宣稱清理

| 位置 | 原本 | 現在 |
|---|---|---|
| `lamps.py` 第 1 層 | 法條把處理結果「窮舉」了，是**封閉集合** | 明說**不是**封閉集合（法條列的是結果，決定書寫的是中文，不一對一），與檔案下方「不可能窮舉」一致 |
| `lamps.py` 第 3 層 | **沒有寫法能繞過** | 「不能靠改寫句子的文字繞過」＋列出兩個仍然繞得過的情形 |
| `lamps.py` WHY_* | 已阻擋送出 | 後端送出端點會以 409 拒絕本案送出 |
| `index.tmpl.html` | 逐句溯源覆蓋率 **100%** | 每一句都掛得出燈號與理由；燈號本身不保證內容正確 |
| `app.js` 上傳清單 | **✓ 已解析** | 已上傳（本 demo 不讀取上傳檔內容，案情來自後端合成案例） |
| 步驟列 / 一鍵確認 modal | 陳核並排入委員會議程／紀錄會陳送審議委員會 | 本 demo 沒有外部整合，不會真的陳送任何單位 |
| 燈號審核頁 | （無） | 新增邊界說明：**非 C 型案件之結論段由承辦人撰寫，系統不阻擋其內容** |

**誠實性 meta-test 改成措辭家族掃描**（`test_no_overclaim_in_code_or_ui`）：
掃 `backend/**/*.py`（`backend/tests/` 為具名例外）＋ `app.js` ＋ `index.tmpl.html`，
用 7 組 regex 家族（繞過／阻擋送出／鎖定／100%／已排入議程／已解析／已驗證結論）
配否定語境白名單與就地否定判斷。原本那版只比對兩句一字不差的字串，換個講法就掃不到。

### 2.5 P2 修補

- **函釋前導虛詞**：改成「剝掉虛詞後**仍需是合法機關名**才剝」（用 `_AGENCY` 文法自己驗）。
  「本件參照內政部…」「此有內政部…」現在都正確剝成 `內政部`，
  而「新北市政府警察局新店分局」不會被剝成「警察局」（取最短合法後綴會犯這個錯）。
- **去重改用結構化鍵**：`Citation.dedup_key`（法規名｜條號 / 年度字號 / 機關字別號數），
  不再用 `raw`。前導虛詞剝不乾淨時同一筆函釋不會再被算兩次。
- **determinism 白名單改完整路徑**：原本 `set(path.split("/")) & ALLOWED` 會讓任何
  巢狀在 `run_meta` 下、或名字剛好撞到的新欄位一併豁免。現在是 5 條完整路徑 + 1 個前綴例外
  （`/run_meta/node_timings/`）。

### 2.6 HANDOFF 統一

本檔。三份舊檔頂部已標「已被 HANDOFF.md 取代」。

**修正舊檔的失效數字**：`70/70` → 現況 162/162；`test_vector_count_is_15` → `_is_16`；
契約測試 21 條 → 24 條；HANDOFF-INTEGRATION 的 AC5「91/91」→ 162/162。

**裁定 HANDOFF-GATE:349 與整合實作的衝突**：
那條寫「**不得**把 `submit_allowed` 在 UI 上做成『可送出』按鈕或等義文案（判斷卡 9）」。
它成立的前提是「`submit_allowed` 沒有執行點」——**那個前提已經不成立**（§2.3 做出來了）。
**裁定：UI 可以有送出按鈕與等義文案，但必須滿足三個條件**，三個都已實作：
1. 按下去真的打後端端點，由後端重算後決定（不是前端自己判斷）；
2. 被拒絕時畫面明說是**後端**以 409 拒絕，不是前端擋的；
3. 成功時的文案不得暗示任何外部效果（現在寫「已記錄為送出」＋ `external_effect: none`）。

---

## 2.7 第六輪：窄範圍 fresh-context 覆核的收尾（2026-09-05）

**覆核判定：可 merge、可 demo。** 以下是它點名的洞與處置，全部已收。

### (1) `confirmed_intake` 的 null 會被脅迫成值 — 已修

型別脅迫寫在 `is not None` 之前，於是 `{"transit_days": null}` 被 `int(v or 0)` 變成 0、
`{"interested_party": null}` 被 `bool(v)` 變成 False，**而且照樣把 origin 翻成 human**
（實測期滿日 2024-07-18 → 2024-07-15）。等於「送一個空值就能宣稱有人確認過」，
正好是判斷卡 7 要防的那件事。

改成：**null 一律不採用**——值不動、origin 不翻、不列進 `intake_confirmed`。
`transit_days` 給非整數會 raise 帶欄位名的 ValueError，不再靜靜吞成 0。

新增測試：全 null → `intake_confirmed=[]`、仍封鎖、期滿日不變；
單欄 null → 該欄 origin 維持 `llm`、值不變、閘門仍關著。

### (2) `note` 也能改變封鎖判斷 — 已修

`intake.note` 餵進 `detect_fact_issues()`。實測：五個期間欄位全部確認後把 note 清空，
`requires_human_conclusion` True→False、`submit_allowed` False→True。

新增 `BLOCK_DECISION_INPUT_FIELDS = DEADLINE_INPUT_FIELDS + ("note",)`，
閘門判準改吃它。兩份清單分開命名，不讓人以為期間欄位就是全部。
前端 `collectIntake()` 本來就送 note，確認 checkbox 的文案也已列出「補充說明」。

### (3) 離線 fixture 殘留五處與新文案自相矛盾 — 已修

| 位置 | 原本 | 現在 |
|---|---|---|
| `agents[proc].out` | 訴願法第 77 條各款**逐款檢核通過** | 只自動判定可由日期算出的第 2 款；其餘各款屬法律判斷，未自動比對 |
| `agents[proc].logs` | 77(1) ✓ 77(3) ✓ 77(4) ✓ 77(8) ✓ | 77(2) 由規則引擎驗算（唯一自動判定的一款）；其餘七款請承辦人審認 |
| `doc[s18].src` | 規則驗算：77(1)–77(8) **全數通過** | 僅第 2 款由引擎判定；其餘 7 款未自動比對 |
| `agents[case].out` | 比對出 **5 件**高相似案例，主論理架構採 113 年第 16 號 | 相似歷史決定書：0 筆可驗證（庫外，未驗證），未執行任何相似度計算 |
| `agents[case].logs` / `doc[s14].src` / `cases[].tag` | 最高相似 **0.91** | 相似度為**示意，非計算值**（`cases[].sim` 分數保留為示範資料，tag 逐筆標明） |

**案號與洗防法日期照舊未動**（仍待 Ci）。已重跑 `build.py`；
`file://` headless 掃過全五步的 DOM，16 個舊字串**一個都不剩**，零 JS error。

### (4) 收文頁補「我已核對」checkbox — 已做

live 模式下未勾選則「啟動幕僚團分析」為 disabled，提示寫
「請先勾選『我已核對上列全部欄位』——未確認的欄位系統不會採信」。
按鈕本身不能代表「有人看過」，那正是覆核打穿的假設。離線模式隱藏該 checkbox（沒有後端可確認）。

### (5) 函釋去重鍵仍受機關名雜字影響 — 已修

「經內政部…」的「經」不在虛詞清單裡，於是同一函釋算成兩筆。
修法**不是**再往清單加一個字，而是把機關名整個排除在去重鍵之外：
`("directive", 正規化字別, 正規化號數)`。字別本身就編碼發文機關，
號數同時做全形／國字正規化，所以「經內政部台內營字第1120801234號函釋」、
「內政部台內營字第1120801234號函」、「內政部台內營字第１１２０８０１２３４號函」
三種寫法收斂成一筆。
**代價寫在 docstring 裡**：兩個不同機關若用了相同字別與號數會被併成一筆——
字別是機關專屬編碼，實務上不會撞，但這是取捨不是定理。另補一條反向測試確認
不同號數不會被併。

### (6) 誠實性 meta-test 納入離線 fixture — 已做

`_overclaim_targets()` 多掃 `prototype/data/case-demo.json`，
**只掃會顯示給人看的欄位**（`agents[].out`／`logs`、`laws/cases/issues[].tag`／`src`、
`doc[].why`／`src`），不掃案情敘述本身（那是合成案件內容，不是系統對自己能力的宣稱）。
新增四組措辭家族：逐款通過／比對出 N 件／相似度數值／信心值數值，
並把「未執行、未自動、未計算、示意、非計算值、庫外、未驗證、不讀取」加進否定語境白名單。
突變測試：把覆核點名的五處原文塞回去，五處全部被指名抓到。

### (7) 送出頁「0 ms」 — 已修

六節點在 fixture 檔位都是次毫秒，逐項四捨五入後合計可能是 0，畫面顯示「0 ms」像壞掉。
改成合計 > 0 時顯示實際數字，等於 0 時顯示 `<1 ms`。

### (8) 本檔更新 — 就是這一節

計數對現況（162/162），並記錄覆核的「可 merge」判定與上述八項的處置狀態。

---

## 3. 驗收證據

全部可重跑，腳本在 `docs/evidence/2026-09-05-integration/`（自帶 PASS/FAIL 或 exit code）。

| 情境 | 腳本 | 結果 |
|---|---|---|
| ordinary 五步 + 確認 + 送出 200 | `verify_submit.py` | **PASS**：確認 12 欄、燈號 0/0/13、完成頁「已記錄為送出（後端回 200）」＋收據 |
| ordinary **未確認**直接打 API | `verify_submit.py` | **409**，`accepted:false`，blockers 含 `conclusion_requires_human` |
| blocked 五步 + 強制點送出 | `verify_submit.py` | **409**，畫面「後端已拒絕送出（409）」，按鈕重新變灰 |
| ordinary / blocked 五步渲染 | `verify_ui.py` | exit 0，零 JS error |
| `file://` 離線 fixture | `verify_offline.py` | exit 0，徽章 offline，v0 行為不變 |
| 改日期即時重算 | `verify_recalc.py` | **PASS**：紅燈 2→3、判定句轉紅、閘門仍鎖 |
| 未勾「我已核對」→ 啟動鈕 disabled | `verify_submit.py` | **PASS**：`go1_disabled_before_confirm=true`，勾選後才啟用 |
| API 送全 null 的 confirmed_intake | `verify_submit.py` | **409**，`intake_confirmed=[]`——空值不算確認 |
| `file://` 舊文案已清除 | 一次性 headless 掃描 | 16 個舊字串**零殘留**，零 JS error |

> 唯一一則 console 訊息是 Chromium 對**刻意的** 409 記的 `Failed to load resource`。
> 那不是 JS error（程式接住並顯示了訊息），腳本把它分開記在
> `expected_http_409_console_lines`，不混進 `js_errors`——不是靜靜過濾掉。

紅線：無 secret、無 GCP 字樣、Python 依賴仍只有 fastapi/uvicorn/pydantic、
前端外連只有 Google Fonts（與 v0 相同）、啟動過的 process 全關。

---

## 4. 判斷卡（全部未決事項，合併去重）

### ⚠ A. `fact_issue_signals.json` 仍是骨架版

正式版要由 Jacky 從 114年/19、113年/20 兩份真實 C 型決定書反推。
現在的訊號詞取自公開法條用語，**不是從真實案件反推的**。

**附帶發現**：`substantive` 單獨就足以觸發封鎖，所以事實爭點偵測目前
對「要不要封鎖」幾乎沒有影響力，只影響交接卡多幾行提醒。
換上真實清單之前，**不要在簡報裡把它講成封鎖機制的主要判準**。

### ⚠ B. 實體法條號抽不到，獨立檢索只查得到程序面

案型只給得出法規「名稱」（建築法），條號寫在原處分書上，而 Phase 0 沒有 PDF 視覺抽取。
所以 `laws[]` 清一色是程序面法條，`retrieval_divergence.cited_not_retrieved` 會一直很長。
Demo 被問「你們的檢索到底檢索到什麼」，答案是「程序面查得到、實體面要等 PDF 抽取」。

### ⚠ C. 洗防法修法日期矛盾 + 示範案號

`case-demo.json` 的洗防法日期與案號**刻意未動**，等 Ci 裁決。

### ⚠ D. `/api/deadline` 的 `verdict` 超出 §6.1

§6.1 #5 寫「`Result.as_dict()`，不改」。我保留了原本每一個欄位，只加一個 `verdict` 兄弟鍵
（燈號＋說法，origin=rule）。加它的理由：前端即時重算時不能自己判斷燈號。
要不要回寫進 architecture 請拍板。

### ⚠ E. 改日期重算只涵蓋期間，不含 77 條款

改日期後 `art77.clause` 不會跟著變（那要重跑 N3）。UI 有寫「只有期間這一段重算過」，
但這是誠實的**缺口**不是設計。要補得靠 §6.1 #8 的 redraft（重跑 N3–N6）。

### ⚠ F. 非 C 型案件的結論內容，系統擋不了

系統無法用文字判準區分「合法的結論」與「捏造的結論」——兩者長得一樣。
覆核量到非 C 型下 26/26 捏造主文全綠，根因是這件事，不是少了幾條規則。
現在燈號審核頁有一行邊界說明，但**這是揭露，不是修好**。

### ⚠ G. `record`（卷證直錄）通道無條件發綠燈

綠燈代表「來源是卷證、非模型撰寫」，**不代表已與來源文件逐字比對**——本階段沒有這個控制。
`why` 已改成不作此宣稱（§2.2、§2.4），但綠燈本身仍可能被讀成「已驗證」。

### ⚠ H. 判斷卡 7 修完之後，仍然剩下的路

確認機制擋住的是「**沒有人看過**就解除封鎖」。它擋不住「承辦人看了、但沒發現 N1 抽錯」——
那時 origin 是 `human`，系統會採信。這是設計上的正解（人有最終判斷權），
但 demo 被問「所以模型抽錯日期還是會過？」時要答得出來：
**會，只要承辦人確認了**——這正是為什麼那些欄位現在都攤在收文頁上、
而且要明示勾選「我已核對上列全部欄位」才算數。

### ⚠ I. `git mv HANDOFF.md HANDOFF-PHASE0.md`

指揮官說「舊兩份可保留」，但實際上有三份舊檔。我把原本的 `HANDOFF.md`（Phase 0 那輪）
改名成 `HANDOFF-PHASE0.md` 並標為已取代，讓根目錄的 `HANDOFF.md` 空出來給這份統一版。
如果你希望保留原檔名，`git mv` 回去即可。

---

## 5. 2026-09-07 bedrock-live-nodes 分支

> branch `mission/hack-bedrock-agents-20260906`（自 `mission/hack-integration-20260905` 切出）。
> **沒有 push、沒有開 PR、沒有動任何 remote。** 22 個 commit。
> 規格：`docs/spec/2026-09-07-bedrock-live-nodes-design.md`（D1–D8）；
> 計畫：`plans/2026-09-07-bedrock-live-nodes.md`。
>
> **一句話**：`RUN_MODE=bedrock` 的程式路徑（N1／N5 呼叫模型、N4 查 Managed KB、上傳建案、
> 續跑、202＋輪詢）全部寫完並有測試；**但真的 Bedrock 一次都沒打過**——帳號的 Bedrock
> 尚未開通。所有「bedrock 檔位會怎樣」的敘述都是**程式行為，不是實測結果**。

### 5.1 做了什麼（依 task，附 commit）

| Task | 做了什麼 | commit |
|---|---|---|
| 1 | `backend/config/settings.py` 加 live 模式環境變數讀取器與缺漏檢查（`model_provider()`／`aws_*`／`bedrock_model_id()`／`retriever_kind()`／`kb_min_score()`／`RUNS_DIR`）；`run_all.py` 具名豁免 `backend/llm/` 與 `retrieval/kb.py`，新增 **LLM import graph 檢查**（N2/N3/N4/N6 不得 import strands 或 `backend.llm`，ast 遞迴） | `bff8aef` |
| 2 | `backend/llm/`：`client.py`（Strands `Agent` + `BedrockModel`，structured output、重試、值域驗證、`cite_ids` 白名單）、`schemas.py`（Pydantic，`DraftSentence` **刻意沒有** lamp／why／verified 欄位）、`prompts/n1_extract.md`／`n5_draft.md`。`requirements.txt` 補 `boto3`＋`strands-agents` | `78f5297`、`a4d4791`、`3e3740b` |
| — | **D8 重構**：所有 import 上移模組頂層，豁免檔第三方套件以 `try/except ImportError` 守衛；`run_all.py` 新增 `scan_top_level_imports`（ast 強制） | `1842b7e`（plan+spec）、`24bab5c` |
| 3 | 兩個合成案例補 `documents[]` 卷證全文（依既有 `case_digest`／`intake`／`quotes` 反推撰寫，全標「（合成測資）」，無真實案號人名）；N1 bedrock 分支呼叫 `client.extract_intake` | `67b2199` |
| 3b | `backend/intake/`＋`POST /api/cases` 上傳建案（`upload-` 前綴，存 `backend/output/uploads/`，gitignored）；卷證文字三層路由（txt／pdftotext／PDF 視覺讀）；N2／N3 改吃 N1 輸出；上傳撞名與空 `pdf_visual` 兩個誠實性守衛 | `364b6b7`（WIP）、`7ab4f6a` |
| 4 | N5 三向分流：`fixture` 行為逐字不變、`bedrock` 呼叫 `client.draft_sentences`（`retrieve_refs` 工具限函釋／判解前綴）、`local` 照舊 raise。結論段在程式端**二次剔除**（不信模型自律）；引用 ref 一律由 N5 重新編號並保留 `src_id` | `009203f`、`4aa0a4c` |
| 5 | `backend/retrieval/kb.py`：Managed KB 檢索器（boto3 `Retrieve` ＋ 後過濾、前綴過濾、`KB_MIN_SCORE`）；N4 通道 B 用**注入的** retriever 產 `cases[]`；**決定結果照檔名讀，不由模型推測** | `07a031b`、`bc26a73`（測試） |
| — | plan 補「KB recall 不足時的兩級備援」（先 rerank，再退自管 KB），見 architecture §13 #18 | `b3726ae` |
| 6 | 編排層 run 持久化（`backend/output/runs/`）＋ `base_state` + `from_node` 續跑（N6 永遠最後跑）；`on_event` 逐節點事件且**例外不回拋**；`save_run` 失敗必發 `run_failed`；`n4_query` override 走白名單 | `048dfb3`、`7febe38` |
| 7a | API：bedrock 模式 `POST /runs` 回 **202**、新增 `GET /api/runs/{id}`（409 進行中／200 結果／502 失敗帶原因／404）；`RunIn` 支援 `base_run_id`／`from_node`／`overrides`（**只收 `base_run_id` 經 runstore 讀回，body 不得直接帶 state**）；`/api/health` 加 `live_settings` 檢查（含 boto3／strands 是否裝得起來） | `5902593` |
| 8a | 前端：拖曳區真上傳建案；`postRun()` 統一入口（fixture 200 同步／bedrock 202 輪詢）；確認後**從 n2 續跑不重抽**；上傳與啟動分析互斥；輪詢十分鐘軟上限 | `be2ecbc`、`cb819e4` |
| 10 | `scripts/live_acceptance.py`：AC4–AC15 驗收腳本，讀 `run_meta.run_mode` 決定該 AC 是真判還是標 **⏸ 未驗**；證據包 `docs/evidence/2026-09-07-bedrock-live/` | `0500cd3`、`c4f9a38` |
| 11 | 本節與 `docs/architecture.md` §4.1／§7.1／§13、`docs/spec/prototype-spec.md` §4.6、`backend/DEPLOY.md`、`backlog.md` Phase S 同步；`run_all.py` secret 掃描納入 12 碼帳號 ID | 本 commit |
| — | plan-guardian 退回的六項修正（移除文件內實際帳號／KB id、命名對齊、驗收表自足、`openai` 不當證據、回放只報告、KB 命中標示） | `eec11d8` |

測試：分支起點 **162/162** → 現在 **210/210**（`python3 backend/tests/run_all.py`，exit 0）。
五道紅線靜態掃描全綠：secret／禁用雲端字樣、`prototype/dist` 可重現、核心路徑零外部依賴
（三道是既有的，具名例外擴充到 `backend/llm/`、`backend/retrieval/kb.py`），
**N2/N3/N4/N6 無 LLM 依賴**、**所有 import 在模組頂層**（這兩道是本分支新增）。
secret 掃描本輪再加一條「12 碼數字＝疑似 AWS 帳號 ID」，跑過確認不誤中合成案號（10 碼）。

### 5.2 驗收證據

`docs/evidence/2026-09-07-bedrock-live/`（`README.md` 說明產生條件、`acceptance.md` 是腳本輸出、
`ac11.md` 是手動驗證摘錄）。

**結果：6 ✅ ／ 5 ⏸ ／ 0 ❌**（exit 0）。⏸ 不是通過，是**沒驗**。

| 驗到了 | 沒驗到（⏸，待 Bedrock 開通） |
|---|---|
| health 200、`live_settings` 四項全過 | AC4 N1 抽取 12 欄 `origin=llm`／conf／model_id |
| 六節點端到端跑得完 | AC5 N5 每句 `cite_ids` 不越界 |
| AC6 C 型封鎖（無非佔位結論、`submit_allowed=false`） | AC7 KB recall（`cases` ≥ 3、同案型 ≥ 3） |
| AC8 從 n5 續跑（`node_timings` 只有 n5/n6） | AC15 上傳 txt → 抽取與 fixture 一致 |
| AC8b `from_node` 無 base → 400 | AC11 真 model id 情境（現有證據的失敗原因是 `NoCredentialsError`） |
| AC9 確認後從 n2 續跑（沒有 n1、`intake_origin.d2 == "human"`） | AC10 SSE 六對事件（端點沒做） |

> **這批證據是 fixture 模式產生的，不是 live。** 產出當下本機沒有 AWS 憑證、沒裝
> strands-agents／boto3。任何「已接 Bedrock 驗過」的說法都不成立——demo 講稿請用
> 「程式路徑已完成、真實呼叫待帳號開通」。

### 5.3 沒做什麼（不要以為做完了）

| 項目 | 說明 |
|---|---|
| **真的打過 Bedrock** | **一次都沒有。** 所有 bedrock 檔位的行為都只有單元測試與 mock，沒有真實模型或真實 KB 的回應 |
| 加值層 Task 7b：SSE 節點事件流 | `GET /runs/{id}/events` **端點不存在**。`backend/api/events.py` 的 `stream()` 寫好了但沒有人叫它。前端走輪詢 |
| 加值層 Task 8b：每卡「從這裡重新產生」 | 後端續跑 API 已具備，**前端按鈕沒做**。目前只有「確認後從 n2 續跑」這一條路徑 |
| 加值層 Task 9：資料 manifest／ingest／逾期回放 | `scripts/build_manifest.py`、`scripts/ingest_kb.py`、`scripts/replay_overdue_public.py` **都不存在**。KB 目前只能在 console 手動建。CLAUDE.md 與 architecture 提到的「以 `ingest_kb.py` 重建」是**計畫，不是現況** |
| 加值層 Task 16：掃描件視覺讀取實測 | 沒跑過。連帶：**`pdf_text` 0.60 門檻沒有用任何真實 PDF 校準過**，那是拍腦袋的值 |
| ask 追問 agent | spec §2 明確不做（沒有 story 或 AC 要求對話） |
| AgentCore Runtime 部署 | 維持 Stretch（D3）。唯一價值是承載 ask，沒有 ask 就沒有理由 |
| 冪等 `run_id` | 仍未做。每次 `POST /runs` 都真的重跑，接上模型後**每次重整都會燒模型費用** |
| 爬蟲補洗錢防制法案件 | 沒做。現有爬蟲只有廢清法、空污法——**主 demo 案型反而沒有真實案源** |
| 法規快照擴充到 18 部 | 沒做，仍是 11 部 |

### 5.4 憑證狀態與下一步

**現況：Bedrock 未開通。** 用開發期帳號（開發用 AWS，profile 名／帳號 ID 只在 `~/.aws`／`.env`）
呼叫 Bedrock 回 `Operation not allowed`；帳號本身是 **PAID／ACTIVE**，所以不是欠費或停用，
研判是這個帳號的 Bedrock 服務沒開。**要開 AWS Support Case 才能解**（本輪沒有 case 編號可填——
還沒開）。

接下來照順序：

1. **開 AWS Support Case** 請求開通 Bedrock（`us-west-2`）。這是唯一的阻塞點。
   （2026-09-12：賽制指定主要區域為 us-east-1／us-west-2，原東京 `ap-northeast-1` 方案作廢。）
2. 開通後在 console 申請 **model access**（抽取與主筆各一個 model id 或 inference profile）。
3. 建 **Managed Knowledge Base**：私有 S3 bucket（Block Public Access 四項全開）→ 資料分
   `kb/official/`、`kb/public/` 前綴上傳 → console 建 KB 指到該 prefix → 取得 KB id 填 `.env`
   的 `BEDROCK_KB_ID`，`RETRIEVER=kb`。（`scripts/ingest_kb.py` 還沒寫，這步是手動的。）
4. **重跑 live 驗收**，把 §5.2 的 5 個 ⏸ 換成真結果：

   ```bash
   set -a; . ./.env; set +a          # 內含憑證與 model id，不進 git
   uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart \
          --with strands-agents --with boto3 -- \
     python -m uvicorn backend.api.app:app --port 8123 &
   sleep 3
   python3 scripts/live_acceptance.py --base http://127.0.0.1:8123 --timeout 600 \
     > docs/evidence/2026-09-07-bedrock-live/acceptance.md; echo "exit $?"
   kill %1
   ```

   `--timeout` 是**等一次執行跑完**的上限（秒）。bedrock 檔位六節點要跑多久沒有人保證，
   跑不完就把它調大再跑——不要把「腳本等不及」寫成系統失敗。
5. AC7（KB recall）若不足，**先加 rerank 再談換架構**，兩級備援見 `docs/architecture.md` §13 #18。
   注意 `RERANK=llm` 會讓 N4 import `backend.llm`，與 AC3 衝突，需要拍板才能開。
6. 完整 live 驗收約 **5 次模型呼叫**的成本；`wait_run` 的上限在真跑起來後要盯。

### 5.5 啟動指令

**fixture 檔位**（零 AWS 依賴，斷網可跑；demo 備援走這條）：

```bash
python3 prototype/build.py     # static/ 或 data/ 改過才需要重跑
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart -- \
    python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8080
```

**bedrock 檔位**（多兩個套件與一份 `.env`；變數清單見 `backend/DEPLOY.md` §1.2）：

```bash
set -a; . ./.env; set +a          # RUN_MODE=bedrock 與 AWS 設定都在裡面
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart \
       --with strands-agents --with boto3 -- \
    python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8080
```

兩者都開 <http://127.0.0.1:8080/>。`GET /api/health` 的 `live_settings` 會列出缺哪個變數或套件。
**bedrock 這條至今沒有在真的能打 Bedrock 的帳號上跑過。**

### 5.6 延後處理清單（review 過程明確延後的項目）

分支 review 逐 commit 做過，下列是**當時判定為 minor 或跨 task、決定延後**的項目，
一條都沒有偷偷消失。「賽前清單」標記的是接上真資源後**一定要回頭看**的。

| # | 項目 | 落點 |
|---|---|---|
| 1 | `kb_min_score()` 非數字時 raise `ValueError` 沒有測試 | `backend/config/settings.py` |
| 2 | `bedrock_model_id()` 傳無效 kind、`RUNS_DIR`、`kb_bucket()` 都沒有測試 | 同上 |
| 3 | import graph 掃描看不到相對 import（`from . import x`）；本專案目前全用絕對 import，所以現在擋得住 | `backend/tests/run_all.py` |
| 4 | `client.py` 的 `result.structured_output`／`metrics.accumulated_usage` **屬性名未經真呼叫驗證**——這是最可能在第一次 live 呼叫就炸的地方 | **賽前清單** |
| 5 | `_safe_doc_name()` 把中文檔名壓成底線，多份中文 PDF 會撞名（已加序號守衛，但命名本身仍不可讀） | `backend/llm/client.py` |
| 6 | 「無文字也無附件」的佔位字串沒有測試 | 同上 |
| 7 | `documents[]` 直接索引 `d['n']`／`d['text']`，壞資料出 `KeyError`（上傳路徑已加守衛，合成案例路徑沒有） | `backend/nodes/n1_intake.py` |
| 8 | `POST /api/cases` 沒有總量／檔數上限；單檔 4.5MB vs 20MB 的落差沒有在上傳時提前告知 | backlog HACK-S-13 |
| 9 | **`pdf_text` 0.60 門檻沒有用真實 PDF 校準** | **賽前清單**、backlog HACK-S-9 |
| 10 | `draft.refs` 的語意是「**查到的**」不是「引用到的」——前端對接文件要寫清楚，別讓人讀成「這些都被引用了」 | 對接注意事項 |
| 11 | 同一份文件跨兩次工具呼叫會拿到兩個 R 號（不去重），等真檢索器定案再處理 | `backend/nodes/n5_draft.py` |
| 12 | **N6 目前不處理 `cases[]` 的燈號，`cases[].lamp` 恆為 `None`**——這是設計歸屬未定，不是既有行為壞掉 | 判斷卡候選 |
| 13 | `exclude_case` 在 fixture 檔位沒有值可供 | `backend/nodes/n4_retrieve.py` |
| 14 | 檢索命中的 `text` 截斷到 600 字**沒有截斷標示**，讀的人看不出被切過 | 同上 |
| 15 | `kb/(official|public)/` 佈局首次接真 KB 時要盯 `case_query_text` 與命中數 | **賽前清單** |
| 16 | `persist` 預設 `true`，`run_all.py` 每次跑會寫上百個 json 到 `backend/output/runs/`（gitignored，**無清理**） | backlog HACK-S-12 |
| 17 | bedrock 檔位的 `from_node`／`overrides` 驗證延後到背景執行才發生（可抽 `validate_run_args` 提前） | `backend/api/app.py` |
| 18 | `RunIn` 未知欄位靜默忽略（應 `extra="forbid"`） | backlog HACK-S-14 |
| 19 | 事件匯流排 `BUS` 永不淘汰舊 run（單 process、記憶體） | `backend/api/events.py` |
| 20 | fixture 檔位的拖曳區要不要預先標示「不接受上傳」——**文案待決** | 判斷卡候選 |
| 21 | AC5 的 `cite_ids` 白名單邏輯在 fixture 也可驗，但依裁定標 ⏸；AC10 的 pending 文案與 legend 不完全對應 | `scripts/live_acceptance.py` |
| 22 | `app.py` 的 `__main__` 在缺 uvicorn 時改吐 `SystemExit` 訊息（行為微變，D8 重構時已裁定可接受） | `backend/api/app.py` |
| 23 | 12 碼帳號 ID 掃描的 grep 修正：`-E "\b[0-9]{12}\b"` 的 `\b` 在 sha256 這類 64 碼 hex 字串裡會誤中內嵌的 12 碼子字串（`\b` 只看 `\w`／`\W` 邊界，hex 字元全屬 `\w`，遇到字串收尾或非 hex 分隔處仍可能誤判）。人工覆核／驗收前改用：`git grep -nP "(?<![0-9A-Za-z_])[0-9]{12}(?![0-9A-Za-z_])" -- backend docs plans scripts HANDOFF*.md CLAUDE.md`（來源 Task 9 報告） | 驗收檢查清單 |

### 5.7 加值層進度（2026-09-07 續）

§5.3 那張「沒做什麼」表是 2026-09-05 寫的快照，下列四個 task 之後陸續補上——**§5.3 的
7b／8b／9／16 那四列已經過時，以本節為準**：

| Task | 現況 | commit |
|---|---|---|
| 7b：SSE 節點事件流 | 完成。`GET /api/runs/{id}/events` 端點接上，逐節點推播 | `1b0db05` |
| 8b：每卡「從這裡重新產生」 | 完成。前端 SSE 訂閱＋每卡重新產生列（`from_node` + `n4_query` 兩通道） | `bc0d025`（實作）、`9d5eafb`（驗證證據） |
| 9：資料 manifest／ingest／逾期回放 | 完成。`build_manifest.py` 產出 **manifest 2,477 筆**；`replay_overdue_public.py` 產出回放集 **217 件**，對規則引擎重放**一致率 99.5%（216/217）**。`ingest_kb.py` 寫好但**未實跑**（無 AWS 憑證）。**附註：回放集 217 件全為 `expected_overdue=True` 正例，沒有反例（非逾期案）混入，一致率只驗到「引擎判正例判得準不準」，不能當成日期計算邏輯（含邊界、非逾期分支）的完整驗證** | `0d9d10d` |
| 16：掃描件視覺讀取實測 | **部分完成**（本 commit）。無 Bedrock 憑證，Step 2（模型真的讀不讀得懂圖片）驗不了，標 ⏸ 未驗；Step 1 造樣本、fixture 服務上傳與路由三件事已驗過，見 `docs/evidence/2026-09-07-bedrock-live/ac16.md` | 本 commit |

### 5.8 收尾修正（2026-09-07 續）

| 項目 | 現況 |
|---|---|
| `n6_gate` C 型案下 unsupported 句的 `why` 被封鎖文案蓋掉 | **已修**。`elif origin == "llm" and not usable_cites` 加 `and not unsupported`——`unsupported` 的 `why` 已寫成「模型引了 X、已清除」，那是更具體的同一件事，用「本案已封鎖」蓋掉會把可查證性的破口藏起來。燈號 `r` 與 tier「請人工判斷」在主路徑已定，此分支不接手也不放寬。新增 `test_unsupported_why_survives_c_type_blocking`（先紅後綠） |
| spec §5.5 表格被插入段落切開 | **不需修，已關閉**。掃過 `docs/**` 與根目錄 md 的所有表格，無「表格列與非表格行相鄰」的畸形；§5.5 現行順序連貫 |
| `backlog.md` Phase S 狀態過時 | **已修**。S-5／S-6 標完成，S-7／S-9 標**部分完成**（程式寫完但沒真跑過，缺 Bedrock 憑證），S-8 標完成但註明回放集全為正例；新增 S-15（回放集補反例）、S-16（`validate_run_args` 提前驗，取代背景 raise → 502） |

測試：**223/223**（`python3 backend/tests/run_all.py`，exit 0）；`node prototype/tests/parity.mjs` 16/16；`pytest prototype/tests` 4 passed。

---

## 6. 檔案地圖

| 路徑 | 是什麼 |
|---|---|
| `CONSTITUTION.md` | 八原則（紅線） |
| `docs/architecture.md` | 架構與資料契約（§6.1 API、§6.2 CASE payload） |
| `plans/` | 各工作包的 plan（含驗收條件） |
| `backend/DEPLOY.md` | 啟動指令、ECS 部署、備援路徑 |
| `prototype/README.md` | 前端兩種模式的差異表、檔案地圖、測試指令 |
| `docs/evidence/2026-09-05-integration/` | 可重跑的驗證腳本 + DOM 節錄 + 截圖 |
| `docs/spec/2026-09-07-bedrock-live-nodes-design.md` | 接 Bedrock 的設計規格（D1–D8 決策，含推翻的既有拍板） |
| `plans/2026-09-07-bedrock-live-nodes.md` | 對應的可執行計畫（必要層／加值層兩段） |
| `docs/evidence/2026-09-07-bedrock-live/` | live 驗收證據（**fixture 模式產生**，6 ✅／5 ⏸／0 ❌；含重跑指令） |
| `scripts/live_acceptance.py` | AC4–AC15 驗收腳本，Bedrock 開通後對 bedrock 服務重跑 |
| `HANDOFF-PHASE0.md` / `HANDOFF-GATE.md` / `HANDOFF-INTEGRATION.md` | 三條分支的過程紀錄（已被本檔取代） |
