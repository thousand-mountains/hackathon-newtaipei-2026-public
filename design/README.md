# design/

設計端匯出的單檔 HTML 設計稿，**不是**跑得動的 prototype、也不是 build 輸入。

| 檔案 | 說明 |
|---|---|
| `petition-ai-demo.html` | 2026-09-12 版設計稿。4 步動線（收文／幕僚團／草稿與審核／預覽與送出）、三段式幕僚區、法條搜尋彈窗、案例全文彈窗。單檔自足，直接用瀏覽器開。 |

實際跑的 UI 在 `prototype/static/`（`build.py` 組出 `prototype/dist/index.html`，由 `backend/api/app.py` serve）。
兩邊目前未同步：設計稿是 4 步且沒有送達方式／在途期間／承辦人確認勾選／blockers／N6 交接卡／節點重跑等後端接點，要套用得先決定這些控制項怎麼安置。
