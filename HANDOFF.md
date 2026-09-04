# HANDOFF：Phase 0 backend pipeline（2026-09-05 過夜無人值守）

給 Ci 早上看的誠實交接。**這份的原則是：做了什麼講清楚、沒做什麼標明白、拿不準的直接列出來讓你判斷。**

- branch：`mission/hack-manual-phase0-backend-pipeline-20260905`
- base commit：`ce558a8`
- commits：5 筆（見文末）
- **沒有 push、沒有開 PR、沒有動任何 remote**
- `prototype/` 一個位元組都沒動（`git status --porcelain -- prototype/` 為空，且有自動化測試守著）

---

## 一、驗收條件逐條狀態

### SC-01 `backend/tests/run_all.py` 全綠，期間引擎搬遷後對照測試向量集零分歧 — ✅ 通過

指令與結果：

```
$ python3 backend/tests/run_all.py
...
全綠：55/55 通過
$ echo $?
0
```

分成四段：

| 段落 | 通過數 | 內容 |
|---|---|---|
| 期間引擎搬遷與測試向量 | 7/7 | 含 sha256 逐位元組比對、15 條向量零分歧 |
| 六節點單元測試 | 26/26 | 每個節點至少一條紅線行為 |
| 端到端整合測試 | 19/19 | 兩個合成案例的完整行為驗收 |
| 紅線靜態掃描 | 3/3 | secret／禁用雲端字樣、`prototype/` 未變更、測試路徑零外部依賴 |

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

## 三、我在過程中發現並修掉的一個真 bug

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

## 五、建議你早上先做的三件事

1. **決定上面第二節的五個判斷**（尤其 ⚠1 對抗測資的假引用要不要改寫法）。
2. **送出 AWS 帳號與 Bedrock model access 申請**——這是唯一有「行政等待時間」的路徑，
   plan 的「待 Ci 醒來後決定的事」也建議睡前先送出申請。其餘東西都可以之後補。
3. 跑一次 `python3 backend/tests/run_all.py` 自己確認（55/55、exit 0），再決定要不要 merge。

---

## 附：commit 清單

```
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
