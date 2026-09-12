# hackathon-newtaipei-2026（暫名，依賽題定案再改）

千山鳥飛絕的新北黑客松 2026 專案。

## 共享知識庫

團隊的決策、分工、流程在 `knowledge/team-brain/`（git submodule，唯讀用）。

- **回答團隊／流程／權限問題前先查** `knowledge/team-brain/Home.md` 與三張 MOC。
- 拉最新：`git submodule update --remote knowledge/team-brain`
- 要新增或修改筆記：到 team-brain repo 開 PR（`/brain-note`），不要在這裡改 submodule 內容。

## 這個專案（2026-08-22 更新：prototype 開工）

**做什麼**：法制局命題「訴願案件審理 AI 輔助」的 prototype。選題決策與規格見 `docs/spec/`。

| 檔案 | 是什麼 |
|---|---|
| `CONSTITUTION.md` | **九原則**（分層誠實/引用必可驗/不編造測資/規則引擎零 LLM/plan 先行＋prospec/資料隔離/secret/30h 紀律/**契約即介面**） |
| `docs/tech-stack-decision.md` | ADR：Bedrock + KB/S3 + FastAPI + Vue3。**prospec 2026-09-13 起改為採用**（多 session 並行需要共用的 story/AC 載體），`plans/` 並存 |
| `backlog.md` | 使用者價值句 stories（overnight-loop 讀這份） |
| `plans/` | superpowers 式計畫文件，含可執行驗收條件。**並行 Epic 走 `.prospec/changes/`**，兩者並存 |
| `.claude/agents/` + `team-roster.yaml` | 5 agents：tech-lead/backend/frontend/qa-legal/plan-guardian |
| `prototype/` | 程式碼 |

**Model 分派政策**（2026-09-05 拍板，覆蓋全域 `performance.md` 的一般分層）：
- **重要決策**（tech-lead、plan-guardian 這類判斷/把關角色）→ `fable`（Fable 5.1）
- **開發**（backend-dev、frontend-dev 這類寫程式角色）→ `opus`（Opus 5）
- **其他**（機械性/例行工作）→ `sonnet`（Sonnet 5）
- `qa-legal` 待確認：其工作是驗證/對抗測試，跟全域「審查用最強模型」原則有張力，暫維持 `opus` 未降級，需 Ci 確認是否要照上述規則改 `sonnet`。

**規矩**：
- 部署走 **AWS**（賽制：僅限 AWS 服務提供之基礎模型）。**開發期可用開發用 AWS 帳號**（2026-09-06 Claire 拍板；profile 名、帳號 ID、KB id、bucket 名一律只在 `.env`／`~/.aws`，不進程式與文件；賽方帳號到手即以 `scripts/ingest_kb.py` 重建並切換，之後刪除開發用帳號上的 bucket 與 KB）；**不碰** GCP `<other-gcp-project>`。
- 賽方資料集「僅供競賽之用」：**不進 git**、S3 不公開。實體檔問 Ci（`C_法制局-資料集.zip`）。
- Linear team `HACK`；branch `hack-<issue>-<slug>`，小改可直推 main，要 demo 的走 PR。
- Slack：`#hack-general`（人）／`#hack-dev`（通知）。
- 時程：9/8 決賽名單、9/12–13 決賽（30 小時內交付，內容以 9/12 現場公告為主）。
