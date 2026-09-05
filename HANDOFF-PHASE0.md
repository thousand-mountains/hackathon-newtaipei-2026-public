> ⚠️ **已被根目錄 `HANDOFF.md` 取代（2026-09-05）。**
> 這份保留供追溯，**不是現況**：裡面的測試計數、行號與「還沒做」的清單都已經過時。
> 唯一真相在 `HANDOFF.md`。
>
> 本檔記錄的是 Phase 0 純後端那一輪（當時 70/70）。

# HANDOFF：Phase 0 backend pipeline（2026-09-05 過夜無人值守）

給 Ci 早上看的誠實交接。**這份的原則是：做了什麼講清楚、沒做什麼標明白、拿不準的直接列出來讓你判斷。**

- branch：`mission/hack-manual-phase0-backend-pipeline-20260905`
- base commit：`ce558a8`
- commits：9 筆（見文末）
- **沒有 push、沒有開 PR、沒有動任何 remote**
- `prototype/` 一個位元組都沒動（`git status --porcelain -- prototype/` 為空，且有自動化測試守著）

---

## 一、驗收條件逐條狀態

### SC-01 `backend/tests/run_all.py` 全綠，期間引擎搬遷後對照測試向量集零分歧 — ✅ 通過

指令與結果：

```
$ python3 backend/tests/run_all.py
...
全綠：70/70 通過
$ echo $?
0
```

分成四段：

| 段落 | 通過數 | 內容 |
|---|---|---|
| 期間引擎搬遷與測試向量 | 7/7 | 含 sha256 逐位元組比對、15 條向量零分歧 |
| 六節點單元測試 | 38/38 | 每個節點至少一條紅線行為 |
| 端到端整合測試 | 22/22 | 兩個合成案例的完整行為驗收 |
| 紅線靜態掃描 | 3/3 | secret／禁用雲端字樣、`prototype/` 未變更、核心與測試路徑零外部依賴 |

搬遷保真的證據不是「我看過了」，是機器比對——`backend/tests/test_deadline.py:31`
直接算兩個檔的 sha256 並斷言相同（實測皆為 `715102c4bd7ac8f354db6b96d6bb6d9e0429d3ba`）。
測試向量數量也鎖成 15（`test_vector_count_is_15`），將來有人偷偷刪向量會被抓到。

> ⚠ 一個要提的落差：`docs/architecture.md` §8.3 說「五步動線分支多 1 條示範案件向量、
> 16/16」，但 main 分支的 `prototype/data/test-vectors.json` 只有 **15 條**。我搬的是
> main 這份 15 條的。**第 16 條在另一個分支，我沒有去找、也沒有自己補一條**——補一條
> 我造的向量進去等於污染鎖定基準。要不要把那條合併進來由你決定。

### SC-02 `python3 -m backend.cli --case synthetic-ordinary-01` exit 0，六節點結果齊全、每欄有 origin、三層分明 — ✅ 通過

```
$ python3 -m backend.cli --case synthetic-ordinary-01
案例：synthetic-ordinary-01　狀態：VERIFIED　模式：fixture
【程序】期滿日 2024-07-15　逾期 True　77 條款 77-2
        結論段封鎖：False
          · 爭點 I1［medium］爭點1：送達生效日之爭執（命中：未實際收受）
【檢索】法規 4 筆／相似案 0 筆（庫外，未驗證）
【守門】引用 在庫 5／已修正 0／庫外未驗證 0／查無 0
        燈號 綠 13／黃 0／紅 0
        送出：允許（blockers 0 項）
【三層誠實】
  可驗算（6 項）／有出處（7 項）／請人工判斷（3 項）
【降級】2 個節點降級
    ⚠ n4：相似歷史案通道不可用（無資料集），僅法規查表通道有結果
    ⚠ n5：fixture 檔位：草稿為模板重播，非模型即時生成
【分層檢查】通過：每個句子都有 origin，燈號與 why 均非模型產出。
$ echo $?
0
```

- 六節點都跑過：`run_meta.node_timings` 有 n1–n6 六個鍵（`test_ordinary_runs_all_six_nodes` 斷言）
- 狀態轉換 deterministic：`CREATED→EXTRACTING→EXTRACTED→CLASSIFIED→SCREENED→RETRIEVED→DRAFTED→VERIFIED`（逐項斷言）
- 每個句子都有 `origin`／`l`／`why`，且 `l_origin` 恆為 `rule`（`test_ordinary_every_sentence_has_origin`）
- 三層都非空：可驗算 6／有出處 7／請人工判斷 3
- 期間結果對齊既有向量 `hist-113-03`（寄存 113/6/13 → 期滿 113/7/15、逾期）

### SC-03 對抗案例 N6 正確攔下／降級，不誤放行 — ✅ 通過（這條我特別加測）

```
$ python3 -m backend.cli --case synthetic-blocked-01
【程序】期滿日 2025-04-14　逾期 False　77 條款 無命中
        結論段封鎖：True
          · 訊號：程序合法且須進入實體審查（案型：違反建築法事件）
          · 訊號：存在高風險事實認定爭點 I1：爭點1：裁處權時效之起算與行為終了時點認定
【守門】引用 在庫 2／已修正 0／庫外未驗證 0／查無 1
        燈號 綠 10／黃 0／紅 2
        送出：阻擋（blockers 1 項）
          ✗ [citation_missing] s5：建築法第999條：快照中建築法無第 999 條（最大條號 105）
【交接卡】…（3 個具體問題）
$ echo $?
0
```

兩道防線同時觸發，不是只擋一種：

1. **C 型結論封鎖**：`requires_human_conclusion=true` → N5 把 `conclusion` 從 slots
   陣列**直接刪掉**（`backend/nodes/n5_draft.py:25` 的 `resolve_slots()`），結論段只剩 `origin=human_required`
   的佔位句 + 交接卡 3 題。
2. **引用查無此號**：注入的「建築法第999條」被判 `missing` → 紅燈 → 進 `blockers` →
   `submit_allowed=false`。

**最硬的一條證據**：`backend/tests/test_e2e.py:139` 的
`test_blocked_case_fixture_conclusion_text_never_leaks`——fixture 裡確實寫了一句
「原處分撤銷。」，測試會把整個 `doc[]` 序列化成字串，斷言這句**完全找不到**。
不是「檢查旗標有沒有設對」，是「檢查那段文字有沒有真的洩漏出去」。

另外有一條防線是為了防未來的人改壞：`backend/orchestrator/state.py:130`
的 `assert_verified_invariant()`——就算有人把 N5 改壞讓結論句溜進來，N6 也會先記
`blockers` 再拋 AssertionError（`test_n6_flags_generated_conclusion_when_blocked` 驗過）。

### SC-04 `n4_retrieval.py` 相似案通道誠實回報庫外未驗證，不編造內容 — ✅ 通過

- 相似案通道由 `backend/retrieval/base.py:UnavailableRetriever` 實作，`search()` **恆回空 list**。
- `retrieval_meta.similar_case_channel` = `{available: false, hits: 0, label: "庫外，未驗證", reason: "..."}`，
  reason 明說「回空不是查無相似案，而是本系統目前無法檢索」。
- N4 內有一行 `assert cases == []`（`backend/nodes/n4_retrieval.py:88`），未來有人接上檢索卻忘了改這裡會直接炸。
- 這條被算成**降級**並外顯在 `run_meta.degraded`，不是靜靜地當作正常。
- 順帶：法規查表通道也**不補寫條文原文**（`laws[].q` 恆為 `null` + `q_note` 說明快照只索引條號）。

### SC-05 `prototype/` 無變更；無真實資料；無 secret；測試路徑無外部依賴 — ✅ 通過

| 項目 | 驗法 | 結果 |
|---|---|---|
| `prototype/` 未變更 | `git status --porcelain -- prototype/`、`git diff ce558a8..HEAD -- prototype/` | 皆為空；`run_all.py` 每次跑都重驗 |
| 無真實競賽資料 | 只讀 `synthetic-` 前綴（`graph.py:load_case` 硬擋，非 synthetic 的 case id 直接 ValueError） | 這台機器上本來就沒有該檔，我也沒去找 |
| 無 secret 字樣 | `run_all.py` 掃 AKIA／ASIA／`aws_secret_access_key=`／PRIVATE KEY | 零命中 |
| 無新增外部依賴在測試路徑 | `run_all.py` 用 `sys.stdlib_module_names` 逐檔掃 import | 零命中（`backend/api/` 例外，見下） |
| 無 `.env`／AWS 憑證 | 沒有建立、沒有讀取；`~/.aws/` 不存在 | 確認 |
| 無任何雲端 API 實際呼叫 | 全樹無 `boto3`／`bedrock-runtime` import | 確認 |

`backend/api/` 是唯一用第三方套件（fastapi/uvicorn/pydantic）的地方，這是任務明確要求的
範圍升級。**依賴掃描刻意把 `backend/api/` 排除在外**，其餘全部維持 stdlib——
也就是說 `run_all.py` 與 `python3 -m backend.cli` 在一台只有 python3 的機器上照樣跑得起來。

### SC-06 `backend/` 有 Dockerfile／requirements.txt／DEPLOY.md，完全不含 GCP 字樣 — ✅ 通過（且超出要求，實際 build 過）

- `grep -rniE "gcp|gcloud|google" backend/`（排除掃描器自身）→ **零命中**。
- 靜態掃描把 `gcloud`／`google-cloud`／`googleapis.com`／`GOOGLE_APPLICATION_CREDENTIALS`／`GCP`
  列為禁用字樣，任何檔案出現即 exit 1。

**額外做的（任務只要求「部署就緒」，我實際 build 了）**：

```
$ docker build --platform linux/amd64 -f backend/Dockerfile -t hack-appeal-backend:phase0 .
→ 成功
$ docker run -d -p 18080:8080 -e RUN_MODE=fixture hack-appeal-backend:phase0
$ curl http://127.0.0.1:18080/api/health          → ok=True, mode=fixture
$ curl -X POST .../synthetic-blocked-01/runs      → submit_allowed=False, blockers=1
$ curl -X POST .../synthetic-ordinary-01/runs     → state=VERIFIED, submit_allowed=True
$ docker exec ... whoami                          → appuser（非 root）
$ docker exec ... ls /app/prototype               → No such file（映像檔沒帶 prototype）
$ docker inspect --format='{{.State.Health.Status}}' → healthy
```

容器已刪除（`docker rm -f hack-api-test`）。**映像檔 `hack-appeal-backend:phase0` 留在你本機**，
不需要的話 `docker rmi hack-appeal-backend:phase0`。

**尚未實測**：ECR 推送與 ECS 部署本身——那需要 AWS 帳號，我沒有也不會去建。

### SC-07 `backend/api/app.py` 是真的能跑起來的 FastAPI 服務，實際啟動並打過 `/api/health` — ✅ 通過

用任務指定的方式啟動：`uv run --with fastapi --with uvicorn backend/api/app.py`（port 8788）。
實際打過的每一支與結果：

| 請求 | 結果 |
|---|---|
| `GET /api/health` | 200，`run_mode=fixture`、`model_ids=null`（誠實回報未呼叫模型） |
| `GET /api/cases` | 200，列出兩個 synthetic 案例 |
| `POST /api/cases/synthetic-ordinary-01/runs` | 200，`submit_allowed=true`，燈號 g13/y0/r0，三層 6/7/3 |
| `POST /api/cases/synthetic-blocked-01/runs` | 200，`submit_allowed=false`，`blockers=[citation_missing]`，交接卡 3 題 |
| `POST /api/cases/synthetic-nope/runs` | 404 |
| `POST /api/cases/real-case-001/runs` | **400**，訊息說明非 synthetic 前綴被拒 |
| `POST /api/deadline` 正常輸入 | 200，`2024-07-15 / overdue=true / 6 steps` |
| `POST /api/deadline` `{"method":"bogus"}` | 400 |
| `RUN_MODE=local` 重啟後 `POST .../runs` | **501**，訊息說明缺 Bedrock 憑證 |

**啟動的 process 都已關閉**（`pkill -f "backend/api/app.py"`，`pgrep` 確認無殘留）。

---

## 二、我自己拿不準的判斷（請你裁決）

### ⚠ 1. 對抗測資裡放了一個不存在的法條，這算不算踩紅線？

`backend/data/synthetic/synthetic-blocked-01.json` 的草稿裡有一句
「至擅自變更使用之處罰要件，另參**建築法第999條**之規定」——這個條號**不存在**
（建築法最大條號 105）。

- **我為什麼這樣做**：`plans/2026-09-05-phase0-backend-pipeline.md:46-48` 與你的任務說明
  都明確要求「一個故意有問題的案例（例如引用查無此號）用來驗證 N6 真的會攔下來」。
  沒有一個假引用，就沒辦法證明守門是真的。
- **我做了什麼防護**：該檔的 `provenance.adversarial_injections` 明文列出這是刻意注入、
  為什麼注入、期望結果是什麼；該句本身也帶 `adversarial: true` 與 `adversarial_note`。
- **我拿不準的**：這份 JSON 如果被單獨拿去看（例如貼進簡報、或未來被誰當成範例），
  那句話讀起來像是系統對法律的認知。**demo 時如果要展示這個案例，請務必口頭說明
  「這個條號是我們故意放的假引用，用來示範守門」**。若你認為風險太高，最小改法是把
  該句改成明顯不可能被誤認的形式（例如「建築法第999條（測試用假條號，不存在）」），
  代價是 demo 時比較不像真實誤植。**這個取捨我沒有替你決定。**
- **後續補強（獨立審查指出後已修）**：原本 `adversarial` 旗標只存在於合成案例檔，**沒有帶進輸出的 `doc[]`**——也就是說輸出 JSON 裡那句假法條完全沒有標記。現在旗標與說明會一路帶到句子物件（`test_adversarial_flag_is_carried_into_doc` 釘住）。風險小了一截，但 demo 口頭聲明這件事還是要做。

### ⚠ 2. 判解白名單外一律標「庫外，未驗證」而不擋 — 與 v0 前端行為不同

`prototype/static/app.js:83` 的 v0 邏輯是**二態**：判解不在 17 筆白名單內就標 `bad`（紅、擋）。
我照 `docs/architecture.md` §8.1 實作成**四態**：白名單外但字號格式成立 → `out_of_scope`（黃、**不擋**）。

- 理由是架構文件寫的：白名單只有 17 筆，真實決定書引用的判解幾乎必然超出，二態會讓系統
  用自己的正確輸出把送出鈕鎖死。
- **但這代表 backend 與 v0 前端現在行為不一致**。哪一邊要改由你決定；我沒有動 `prototype/`。

### ⚠ 3. 釋字沒有做上限檢查

`釋字第999號` 目前被判 `out_of_scope`（黃、不擋），不是 `missing`。
- 我原本想加「釋字號數上限」的格式檢查，但**那需要我斷言「最後一號釋字是第幾號」這個
  法律事實，我不確定，所以沒做**。判解那邊我只做了「年度不得超過當前民國年」這種
  純日期可算的檢查，那不需要法律知識。
- 要不要補釋字上限，等有人查證後再加。

### ⚠ 4. `fact_issue_signals` 用 JSON 不是 YAML

`docs/architecture.md` §4.3 指定 `backend/config/fact_issue_signals.yaml`。
我用了 `fact_issue_signals.json`，因為 YAML 需要 PyYAML，會違反「測試路徑零外部依賴」。
欄位名完全一致，要換回 YAML 只需改讀取器。**清單內容是我寫的骨架版**——
架構文件說正式版應由 Jacky 從 114年/19、113年/20 兩份真實 C 型決定書反推。
我沒有那兩份決定書，所以訊號詞取自公開法條用語（時效、裁處權、行為終了…），
**不是從真實案件反推的**。這份要換掉。

### ⚠ 5. 檔名與架構文件不同

任務指定 `n3_procedure.py` / `n4_retrieval.py`，架構文件 §10 寫的是
`n3_screen.py` / `n4_retrieve.py`。我照任務指定的檔名。職責完全相同，
但要不要統一命名（以及要改哪一邊）你決定。

---

## 三、我自己在過程中發現並修掉的兩個真 bug

（獨立審查另外打出 6 個，見第四點五節。）

### Bug 1：庫外法規引用被整個漏掉（漏抓）

引用抽取原本只認快照內的 11 部法規名（沿用 v0 前端的 regex 寫法），
結果是：**快照範圍外的法規引用會被整個漏掉**，連「庫外，未驗證」都標不出來。
`architecture.md` §8.1 明確要求這一態存在（Claire 量測決定書引用的 479 個法條有 17%
對不回資料集：政府資訊公開法、行政訴訟法、檔案法…）。

也就是說原本的寫法會讓那 17% 靜默消失——**漏抓比誤攔更危險**。
修法：`backend/retrieval/lawtable.py:107` 加泛用法規名比對（中文字 + 法／條例／準則／
辦法／細則／規則／通則結尾）+ 前導虛詞剝除，並保證兩輪掃描的結果依出現位置排序
（L1、L2… 的編號要 deterministic）。實測：

```
依訴願法第14條                        → ('訴願法第14條', 'ok')
依民事訴訟法第100條                    → ('民事訴訟法第100條', 'out_of_scope')
按政府資訊公開法第18條及行政程序法第74條 → out_of_scope + ok（兩筆都抓到）
另參建築法第999條                      → ('建築法第999條', 'missing')
爰依行政訴訟法第98條                    → ('行政訴訟法第98條', 'out_of_scope')
```

這個 bug 是被 `test_citation_state_out_of_scope_for_unknown_law` 抓出來的——
**測試先失敗，我才發現**。如果我沒寫那條測試，這個洞會一路帶到賽場。

### Bug 2：全形數字造成**正確的引用被誤攔**（誤抓，比 Bug 1 更陰險）

寫完 HANDOFF 後我又手動構造了 18 組對抗輸入去打引用檢查器，抓到這個：

```
'訴願法第１４條'   →  ('訴願法第14條', 'missing')   ← 修正前：合法引用被判查無此號
'訴願法第１４條'   →  ('訴願法第14條', 'ok')        ← 修正後
```

全形數字是法規 PDF 轉文字的常見形式。修正前這種引用會被打紅燈、進 `blockers`、
**鎖死送出鈕**——系統把承辦人寫對的東西攔下來，而且理由寫著「快照中訴願法無第
１４條」，看起來還很像在認真把關。

`architecture.md` §8.1 花了整整一段講「不能誤攔」（判解設計成四態就是為了這個），
但數字正規化這層原本沒做到。修法在 `backend/retrieval/lawtable.py:128`。

同一輪對抗輸入還確認了幾件事是**本來就對的**（沒改）：空白與換行容忍、
邊界條號（建築法 105 通過／106 與 0 查無）、判解字號的前導零與省略「度」字、
`之N` 條號、amended 態（洗防法 15之2 → 22）。

另外找到一個**已知限制，我沒修**：「本法第14條」「同法第74條」這類相對指稱抓不到
（需要上下文追蹤）。我判斷不修是因為它的失敗模式是安全的——抓不到 → 該句沒有引用
→ 燈號黃（交人工），**不會**被誤標成綠燈。我補了一條測試把這個行為釘住
（`test_relative_law_reference_is_not_silently_passed`），避免將來有人「順手」
把無引用句改成預設綠燈。

**這兩個 bug 都是「寫測試／構造對抗輸入」抓到的，不是讀程式碼看出來的。**
如果只靠讀程式碼自我檢查，兩個都會漏掉。

---

## 四、明確沒做的（不要以為做完了）

| 項目 | 為什麼沒做 |
|---|---|
| **真實 Bedrock 呼叫（N1／N5 live 分支）** | 沒有 AWS 憑證與 model access。介面寫好、guard 好，非 fixture 模式一律 raise/501。**沒有假裝能跑。** |
| **真實相似案檢索、真實 kNN 分類** | 沒有賽方資料集。誠實回空 + 標庫外未驗證。 |
| **SSE 事件流（`/api/runs/{id}/events`）** | `POST /runs` 目前是同步跑完就回。fixture 檔位毫秒級，沒有阻塞問題；接上模型後必須改成 202 + SSE。 |
| **另外 6 支 API 端點** | architecture §6.1 列 10 支，我做了 4 支（health／cases／runs／deadline）。intake 補正、citecheck、confirm、redraft、submit 沒做。 |
| **前端** | 不在範圍。 |
| **`scripts/consistency_check.py`／`contract_check.py`** | 架構文件指派給 qa-legal（Jacky）。`origin_registry.check_payload()` 做了 contract_check 的核心邏輯並掛進 CLI 與 API，但不是那支獨立腳本。 |
| **洗防法修法日期矛盾的定案** | architecture §8.2 說「這支腳本一寫出來就會 fail，這是設計意圖」——要翻原始 PDF 定案。我不能翻 PDF，也不該猜哪個日期對，**所以沒動**。 |
| **ECR 推送／ECS 實際部署** | 需要 AWS 帳號。 |
| **`backlog.md`／`.claude/agents/` 同步** | plan 明確列為「留給 Ci 判斷」，不放進自動化範圍。 |

---

## 四點五、獨立審查結果：打出 3 個 P0，**全部已修**

依「產出的人不驗自己」的規矩，我派了一個 fresh-context 的 opus agent 做對抗式審查
（它沒看過我的產製過程，只看最終結果）。它一度沒回應、我催過一次，最後回報了
**三個實際打穿守門的 P0**。我逐條複現驗證——**全部屬實，不是誤報**：

| # | 破口 | 我複現的實際輸出 | 狀態 |
|---|---|---|---|
| P0-1 | **國字條號完全漏抓** | 「建築法第九百九十九條」→ 抽到 0 個引用 → 系統回「本句未附引用」→ 綠燈放行 | ✅ 已修 |
| P0-2 | **結論封鎖可繞過**：主文寫進 reasoning 槽位 | 「綜上…應予撤銷，由原處分機關另為適法之處分」放在理由段 → `submit_allowed=True` | ✅ 已修 |
| P0-3 | **函釋零檢查通道** | 「台內營字第1120801234號函釋」→ 抽到 0 個 → 放行 | ✅ 已修 |
| P1-1 | 判解字別漏「抗」 | 「112年度抗字第123號」→ 抽到 0 個 | ✅ 已修 |
| P1-2 | 「同法第999條」只拿黃燈，且吐出「又同法」這種假法規名 | 假條號寫成「同法」就繞過紅燈 | ✅ 已修 |
| P1-3 | **案型辨識不出來反而解除結論封鎖**（fail-open） | `case_type="其他事件"` → `requires_human_conclusion` True→False | ✅ 已修（改 fail-safe） |

**這三個 P0 為什麼重要**：它們的共同形態都是「**漏抓被講成沒有**」——
系統抽不到引用，然後輸出「本句未附引用，屬涵攝或評價語句」並放行。
那不只是漏一筆，是**用一句肯定的話掩蓋一次失敗**，正好是這個系統最不該犯的錯。
P0-2 更直接：fixture 檔位下永遠不會爆（模板不會亂寫），**一接上 Bedrock 就是真破口**。

修法與新增的 11 條回歸測試見 commit `58d97a6`。修完後 70/70 全綠，
兩個正式案例行為不變（ordinary `submit_allowed=true`、blocked `false`），
FastAPI 重新起來打過三支端點也一致。

### 審查同時指出、我也修了的三項「誠實性」問題

1. **對抗測資的 `adversarial` 旗標沒帶進 `doc[]`** — 輸出 JSON 裡那句假法條完全沒有標記，
   任何人只截 `doc[]` 或貼進簡報就會看到一句沒註記的假條號。已把旗標與說明一路帶到句子物件。
   （這也讓上面第二節 ⚠1 的風險小了一截，但**demo 時仍要口頭聲明**。）
2. **掃描器被改成排除 `api/` 卻仍用「測試路徑零外部依賴」的名字報綠** — 這正是本系統要防的
   那種「調整量尺讓自己過關」。已改名為「核心與測試路徑零外部依賴（`backend/api/` 為具名例外：Web 介面層）」，
   例外具名，不藏在實作裡。
3. **`origin_registry` 的 `ORIGIN`／`LLM_FORBIDDEN_PATHS` 是死碼，docstring 卻宣稱「會擋下來」** —
   已改成誠實對照表，逐項標明哪個真的在跑、哪個只是文件（等 qa-legal 的 `contract_check.py`）。
   另外 `classification.class.case_type` 原本標 `rule`（可驗算層），但它的輸入來自 N1 的模型輸出，
   而它又餵給結論封鎖開關——已改標 `llm_derived` 並在註解寫明這條依賴鏈。

### ⚠ 審查中我**沒有**處理的部分（留給你）

- 審查者自陳有三項**未查完**：architecture.md §3.1／§4.2／§4.3／§6.2／§8.1 的逐條規格對位、
  期間向量的獨立重驗、獨立於我們自己那支 scanner 的 secret grep。
- 審查者提的一個問題我**無法替你判斷**：`laws-snapshot.json` 的 17 筆判解白名單標註來源為
  賽方資料集且已進 git。純法條條號索引我認為沒問題，但判解白名單算不算 §6「僅供競賽之用」
  的衍生物，**請你確認**。這份檔是 v0 就有的、我只是搬遷，沒有新增內容。
- 審查者認為 FastAPI/Docker 超出 plan 的 out-of-scope 應該補一段 plan 範圍變更紀錄。
  這部分是你的任務明確要求的範圍升級（plan 寫的是沙箱限制，不適用），我沒有改 plan——
  要不要補一段追認由你決定。
- `backend/api/app.py` 目前**零測試覆蓋**（只有手動打過端點）。

**結論**：現在這份程式碼**有**通過一輪獨立對抗審查，而且審查抓到的東西不是雞毛蒜皮。
但同一位審查者也自陳有未查完的項目，所以它是「查過一輪、不是查完」。


---

## 五點五、第二輪 fresh-context 對抗覆核：推翻「3 個 P0 全部已修」

> 這節是主對話（指揮官）事後補的，**不是**上面建置 agent 寫的。派了另一個全新、沒看過
> 建置過程的 opus agent 專門對抗式覆核上面第四點五節的「已修」宣稱，不能只信文件。

**結論先講：P0-3（函釋）是真的修好了；P0-1（國字條號）和 P0-2（結論封鎖繞過）都只是
補了原審查者測過的那個具體案例，通用繞法照樣穿過。P0-1 的修法甚至把「漏抓→黃燈」這個
安全的失敗模式，換成了「誤讀→綠燈」這個不安全的。**

### ❌ P0-1 國字條號：只修了「單位式」，「數字串式」全被誤讀成綠燈

`cn_to_int`（`backend/retrieval/lawtable.py:145`）：

```
七十三 → 73  ✓          七三   → 3   ✗（誤讀）
九百九十九 → 999 ✓       九九九  → 9   ✗（誤讀）
一百零五 → 105 ✓         一〇五  → 5   ✗（誤讀，「〇」是法律文書標準寫法）
```

實測「建築法第九九九條」端到端跑完六節點：`submit_allowed=True`，`cites=[('建築法第9條','ok')]`，
because「九九九」被誤讀成 `9`、剛好在庫、綠燈放行。**比原本更糟**：原本是抽不到 → 黃燈
（安全失敗），現在是抽到錯的 → 命中存在的低條號 → 綠燈（系統對捏造條號主動背書）。

### ❌ P0-2 結論封鎖繞過：15 種真實主文寫法漏 8 種

`detect_conclusion_like`（`gate/lamps.py:136`）是 11 條硬編碼片語的子字串比對，
無空白正規化。漏的 8 種包含**最標準的寫法**：

```
漏  本件訴願為無理由，應予駁回。
漏  訴願人之訴願為無理由，爰予駁回。
漏  本件訴願逾期，不予受理。
漏  原處分應予維持。／原處分核有違誤，爰予撤銷。
漏  原 處 分 撤 銷 。（決定書主文常見排版，字間有空白）
```

實測放行：「綜上所述，本件訴願為無理由，應予駁回。」放進 reasoning 槽位 → `submit_allowed=True`。
**HANDOFF 第四點五節那句「一接上 Bedrock 就是真破口」仍然成立，這個破口沒被關掉。**

### ✅ P0-3 函釋：是真的做了，通道有邏輯在跑，只是漏抓 2 種字號格式（`…號書函`、括號內無「函」字者）

### 附帶：agent 另外做的 architecture 規格逐條對位，抓到幾個會影響 demo 可信度的結構性問題

- **N4 檢索查詢句其實是從 N5 草稿 fixture 反推的**（`graph.py:86→128`），不是被案情決定；
  規格要求的 `build_query()` 實務上是死碼。也就是說目前「引用一定查得到」是因為
  「查什麼」是照著「答案要引用什麼」倒著填的，**不是獨立檢索在佐證什麼**。
- `resolved_id` 與 `laws[].id` 命名空間交集是空集合，靠字串巧合過關，顯示字串一變會
  靜默失效不報錯。
- **C 型結論封鎖的判準幾乎沒有鑑別力**：實測把 blocked 案例的事實爭點訊號全拿掉，
  **仍然封鎖**——真正的判準其實是「未逾期」，不是「偵測到事實認定爭點」。demo 被問
  「換個案型會怎樣」會露餡，這是簡報風險。
- `backend/api/app.py` 無 CORS middleware，前端串接第一天就會被瀏覽器擋下。

### 我（主對話）自己補做的驗證

親自用 `uv run --with fastapi --with uvicorn[standard] --with pydantic` 啟動
`backend.api.app`，實打 `/api/health`、`/api/cases`、`POST /api/cases/{id}/runs`
（兩個案例）：全部 200，`synthetic-blocked-01` 正確回 `submit_allowed=false`。
SC-07 現在有獨立於本文件宣稱的第三方驗證。

### 給 Ci 的建議

**不要 merge。** 這不是修得不認真，是「拿具體反例補具體片語／規則」這個修法本身
會反覆製造同一類洞——下一輪要改守門的**形狀**（無法可靠解析就回 `None`／降黃，
不要猜一個值；主文偵測改結構判準，不要再加片語），不是再補幾個案例。另外請針對
第二節 ⚠2 的白名單資料歸屬問題、以及這裡新發現的 N4 循環佐證與 C 型判準鑑別力問題
給出方向，這兩個影響的是 demo 敢不敢在評審面前被追問細節，不只是程式碼品質。

---

## 五、建議你早上先做的三件事

1. **決定上面第二節的五個判斷**（尤其 ⚠1 對抗測資的假引用要不要改寫法）。
2. **送出 AWS 帳號與 Bedrock model access 申請**——這是唯一有「行政等待時間」的路徑，
   plan 的「待 Ci 醒來後決定的事」也建議睡前先送出申請。其餘東西都可以之後補。
3. 跑一次 `python3 backend/tests/run_all.py` 自己確認（70/70、exit 0）。
   **另外建議**：獨立審查已經打出 3 個 P0（都修了），這說明這類破口確實會出現。merge 前值得再派一輪審查，重點放在審查者自陳未查完的三項（architecture 規格逐條對位、期間向量獨立重驗、獨立於我們自己 scanner 的 secret grep）。

---

## 附：commit 清單

```
58d97a6 fix(gate): 修掉獨立審查打出來的 3 個 P0 + 3 個 P1 破口
337d744 docs: HANDOFF 補記 Bug 2 與誠實聲明
403252b fix(gate): 全形數字造成合法引用被誤攔
84379f6 docs: HANDOFF.md
91d48a7 docs(deploy): 補記 Docker 映像檔本機實測結果
7dea039 feat(api): 可運作的 FastAPI 服務 + ECS 部署就緒
56d1e1a test(backend): 55 項統整測試全綠
aa22add feat(backend): 六節點 pipeline + deterministic 編排 + 兩個合成案例可端到端跑完
9b7d9e6 test(engine): 原封搬遷期間計算引擎到 backend/ 並鎖定零分歧
```

## 附：檔案清單（41 個新檔，全在 `backend/` 底下）

```
backend/
├── DEPLOY.md                      ECS 部署步驟與環境變數（只寫名稱不寫值）
├── Dockerfile                     ECS Fargate 用，非 root，內建 healthcheck
├── requirements.txt               只有 Web 層需要；核心與測試路徑零外部依賴
├── cli.py                         python3 -m backend.cli --case <synthetic-id>
├── api/app.py                     FastAPI 四支端點
├── engine/deadline.py             原封搬遷（sha256 相同）
├── config/
│   ├── settings.py                RUN_MODE、provenance、幕僚卡片、需事實認定型案型
│   ├── origin_registry.py         JSON path → origin，三層誠實的機器強制
│   └── fact_issue_signals.json    C 型訊號清單（骨架版，待 qa-legal 換掉）
├── retrieval/
│   ├── base.py                    凍結的 Retriever 介面 + UnavailableRetriever
│   └── lawtable.py                法條查表 + 引用抽取（含庫外法規）
├── gate/
│   ├── citations.py               引用四態
│   └── lamps.py                   燈號規則 + C 型封鎖開關
├── nodes/n1..n6                   六節點
├── orchestrator/
│   ├── state.py                   CaseState/NodeResult/NodeCtx + 不變式
│   ├── graph.py                   deterministic 狀態機 + 三層輸出視圖
│   └── narrative.py               決定書骨架模板 + 幕僚卡片文案
├── data/
│   ├── laws-snapshot.json         搬遷自 prototype
│   ├── test-vectors.json          搬遷自 prototype（15 條）
│   └── synthetic/
│       ├── synthetic-ordinary-01.json   正常案例
│       └── synthetic-blocked-01.json    對抗案例（含刻意注入的假引用）
└── tests/
    ├── harness.py                 stdlib-only 測試 harness
    ├── test_deadline.py           搬遷保真 + 向量零分歧
    ├── test_nodes.py              六節點單元測試
    ├── test_e2e.py                端到端整合測試
    └── run_all.py                 統整入口 + 三道紅線靜態掃描
```
