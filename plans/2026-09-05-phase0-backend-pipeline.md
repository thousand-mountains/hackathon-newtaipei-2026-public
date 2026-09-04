# Phase 0：backend/ 六節點骨架 + 本地端到端流程（fixture mode）

日期：2026-09-05
對應：`docs/architecture.md`（會後版 v2，2026-09-03）§10 模組責任表、§11.2 Phase 0（H0-H4）
執行方式：mission-loop-v2（Development Mode，過夜無人值守，隔離 worktree branch，絕不動 main）

## 目標

把 v2 架構的六節點（N1 抽取／N2 分類／N3 程序審查／N4 檢索／N5 主筆／N6 守門）與
deterministic orchestrator 從 0 蓋起來，並且能在**本機**用一個指令把一個案例完整跑過
全部六個節點、產出符合「分層誠實」三色契約的輸出。這是 v0（8/22 一夜版，只有期間計
算器＋fixture UI）之後的第一次真正落地 v2 架構。

## 為什麼不是「全部做完」

1. **沒有 AWS 憑證與 Bedrock quota**（本機無 `~/.aws/`、無 `.env`、無 AWS CLI）——N1/N5
   真正呼叫 Bedrock 這件事今晚做不到，需要 Ci 自己申請帳號與 model access，這條路徑
   本計畫**不嘗試**，只把介面寫對、guard 好，等憑證到位再插。
2. **沒有競賽資料集實體檔**（`C_法制局-資料集.zip` 不在這台機器、也不進 git）——N4 的
   「相似案」通道今晚**不能**假造相似案，只能誠實回報「庫外／未驗證」，這是
   CONSTITUTION §2（引用必可驗）的直接要求，不是偷懶。
3. 系統沒裝 `fastapi`/`pytest`，專案也沒有 lockfile（v0 是用 `uv run --with fastapi`
   臨時裝的）。mission-loop-v2 的沙箱執行不能連網裝套件，所以本計畫刻意把
   `dependency_policy` 設為 `deny`，範圍只用 Python stdlib。FastAPI 的 Web 介面層是
   下一步（人工或下一個 mission），不在今晚範圍內。

## 範圍（in scope）

- `backend/engine/deadline.py`：從 `prototype/engine/deadline.py` **原封搬遷**（v2 §10
  自己註記的做法），測試向量比對零分歧。
- `backend/nodes/n1_extract.py`：fixture 模式抽取，`origin="llm"`，帶信心值；
  `RUN_MODE != "fixture"` 時明確 raise，不得靜默造假資料。
- `backend/nodes/n2_classify.py`：規則式分類（誠實標註「v0 fallback，等真實資料集才能
  做 kNN」），`origin="rule"`。
- `backend/nodes/n3_procedure.py`：包一層薄殼呼叫 `engine.deadline.compute`，`origin="rule"`。
- `backend/nodes/n4_retrieval.py`：法規查表（沿用/搬遷 `laws-snapshot.json`）為真實邏輯；
  相似案通道**誠實回空**並標「庫外，未驗證」，不得編造。
- `backend/nodes/n5_draft.py` + `backend/orchestrator/narrative.py`：fixture 模式模板組稿，
  三層標色（可驗算／有出處／人工判斷），`RUN_MODE != "fixture"` 時明確 raise。
- `backend/nodes/n6_gate.py`：引用四態比對（可驗／推論／未驗證／缺漏）、燈號規則、C 型
  結論封鎖——這是**真邏輯**，不是 fixture，直接落實 CONSTITUTION §1/§2。
- `backend/orchestrator/state.py` + `graph.py`：deterministic 狀態機，把 N1→N6 串起來，
  每個欄位帶 `origin` 標記（對齊 `docs/architecture.md` §6.2 資料契約）。
- `backend/cli.py`：`python3 -m backend.cli --case <synthetic-id>` 對一個 synthetic 案例
  跑完整流程，輸出三層 JSON 到 `backend/output/`。
- `backend/data/synthetic/`：手造 2-3 個 synthetic 案例（含至少一個「故意有問題」的案例，
  用來驗證 N6 守門真的會攔下來，不是擺好看）。檔名/目錄一律 `synthetic-` 前綴或
  `synthetic/` 路徑，不得暗示為真實案件。
- `backend/tests/`：搬遷後的期間引擎測試 + 六節點單元測試 + 一個端到端整合測試。

## 明確排除（out of scope，今晚不做）

- 真實 Bedrock 呼叫（N1/N5 live 分支）——待 Ci 取得 AWS 憑證與 model access 後再接。
- FastAPI Web 層／10 支 API 端點／SSE——下一步，人工用 `uv run --with fastapi` 補上即可，
  技術上不難，只是需要連網裝套件，不適合放進今晚的隔離沙箱任務。
- `prototype/` 目錄本身——保持不動，v0 demo 繼續可用，不互相干擾。
- 真實相似案檢索、真實 kNN 分類——沒有資料集，今晚只能誠實留白。
- 任何會員/協作者角色分工調整（`.claude/agents/*.md`、`backlog.md` 同步）——留給 Ci 事後
  自己判斷要不要動，不放進這次自動化範圍。

## 驗收條件（可執行）

1. `python3 backend/tests/run_all.py` 全綠，其中包含 `deadline.py` 搬遷後與
   `prototype/tests/test_deadline.py` 的既有測試向量集逐條零分歧。
2. `python3 -m backend.cli --case <至少一個 synthetic 正常案例>` exit 0，輸出的 JSON
   每個欄位都有 `origin` 標記，且三層（可驗算/有出處/人工判斷）齊全。
3. 對「故意有問題」的 synthetic 案例（例如引用查無此號、或屬於 C 型結論情境），N6 必須
   正確攔下/降級，不能誤放行——這條沒過視同 P0，不能算完成。
4. `prototype/` 目錄無任何變更；`backend/` 底下沒有任何真實競賽資料、沒有 secret/credential
   字樣、沒有新增外部依賴（全程 stdlib）。

## 回滾方式

整個工作在獨立的 `mission/...` git worktree branch 上進行，不 push、不動 `main`。若結果
不理想，直接捨棄該 branch（`cleanup`），`main` 與 `prototype/` 完全不受影響。

## 待 Ci 醒來後決定的事（不在本計畫自動化範圍）

- 是否/何時申請 AWS 帳號與 Bedrock model access（建議今晚睡前先送出申請）。
- 是否要把這次產出的 `mission/...` branch merge 進 main。
- `.claude/agents/backend-dev.md` 要不要補上 N5/N6 職責、要不要新增對應 Claire
  （檢索/N2+N4）的 agent 角色——`docs/architecture.md` §10 的落差分析已經指出這個缺口。
- `backlog.md` 要不要同步改寫成 v2 六節點語言。
