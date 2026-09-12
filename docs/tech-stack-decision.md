# Tech Stack 決策（ADR-001，2026-08-22）

**狀態：已定（sprint 模式，Ci 拍板；賽場環境 9/12 公告後若衝突再修）**

| 層 | 選擇 | 為什麼 |
|---|---|---|
| LLM | **AWS Bedrock**（Claude 系列基礎模型） | 賽制硬限制：「僅限使用 AWS 服務提供之基礎模型」（8/5 錄取信原文） |
| 檢索 | **Bedrock Knowledge Bases + S3**（資料集 141 份 PDF 入庫） | 賽方資料即知識庫；KB 免自建向量管線，30 小時預算內 |
| 後端 | **Python + FastAPI** | 團隊現役技術（既有專案 同棧），零學習成本 |
| 前端 | **Vue 3 + Vite + shadcn-vue** | 團隊現役技術（既有專案 同棧） |
| 規則引擎 | 純 Python 模組（無 LLM 依賴） | 期間計算必須 deterministic、可攤開算式——這是產品主張不是實作細節 |
| 開發工具 | Kiro（每人 2,000 credits，官方提供非強制）＋ Claude Code | 8/16 官方信 |
| 部署 | 本地 demo 優先；AWS 部署依 9/12 現場公告的競賽環境 | 「專屬黑客松競賽之開發環境」開幕才公告，不預先押注 |

## 刻意不用的

- ~~**prospec SDD**~~：**2026-09-13 推翻，改為採用。** 原本的理由是「sprint 模式、30 小時、
  用後即棄的 prototype——規格先行的成本划不來」。實際走下來相反：決賽期間有多個 session
  與 worktree 並行，**需要一個共用的 story／AC 載體**，口頭與 `plans/` 撐不住跨 session 對齊
  （2026-09-13 就踩過一次：proposal 沒 commit，三個 epic agent 讀不到規格照樣開工）。
  現況 repo 內有 11 個 `.prospec/changes/`。規則見 `CONSTITUTION.md` §5。
  單人臨時工作包仍可只寫 `plans/`（兩者並存，不是二選一）。
- **自建向量資料庫**（pgvector/OpenSearch 自管）：30 小時內是坑，Bedrock KB 頂得住 demo 量。
- **微服務**：單體 FastAPI，demo 完就丟。

## 紅線（不因趕時間放鬆）

- 不碰其他專案的 GCP `<other-gcp-project>` 與其任何 secret；AWS 憑證只放 `.env`（gitignored）
- 賽方資料集「僅供競賽之用」：**不進 git**，上 S3 時 bucket 不開公開
