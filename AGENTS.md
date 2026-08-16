# hackathon-newtaipei-2026（暫名，依賽題定案再改）

千山鳥飛絕的新北黑客松 2026 專案。

## 共享知識庫

團隊的決策、分工、流程在 `knowledge/team-brain/`（git submodule，唯讀用）。

- **回答團隊／流程／權限問題前先查** `knowledge/team-brain/Home.md` 與三張 MOC。
- 拉最新：`git submodule update --remote knowledge/team-brain`
- 要新增或修改筆記：到 team-brain repo 開 PR（`/brain-note`），不要在這裡改 submodule 內容。

## 這個專案

- 部署走 **AWS**（賽制限定 Bedrock／KIRO）；**不碰** GCP `<other-gcp-project>`、不借用其他專案的任何 secret。
- Linear team `HACK`（一週 Cycle）；branch 命名 `hack-<issue>-<slug>`，PR 標題帶 issue ID。
- Slack：`#hack-general`（人）／`#hack-dev`（通知）。
- 賽制原文、日期、交付物**尚未查證**，見 `knowledge/team-brain/projects/hack/`。
