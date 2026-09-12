# 訴願智慧輔助平台（前端）

以 **Vue 3 + Vite + TailwindCSS v4** 重寫 `design/訴願智慧輔助平台.html` 設計稿。
Agent 對話式辦案工作台：左側案件樹、中間對話串（工具逐步執行）、右側案件卷宗。
目前是**純前端 mock**（示範資料內建於 `src/data/`），尚未接後端。

## 開發

```bash
pnpm install
pnpm dev      # http://localhost:5175
pnpm build
pnpm preview
```

Node >= 20。`vite.config.js` 已設 `/api` 代理到後端（預設 `localhost:8091`），供之後接 API 用。

## 結構

```
src/
  main.js               進入點（含 v-focus 指令）
  App.vue               版面殼 + Sheet（modal）狀態機 + toast
  styles/main.css       Tailwind 匯入 + 設計稿樣式（含深色模式 CSS 變數）
  data/
    data.js             CASE_NO / EVIDENCE_POOL / LAW_POOL / CASE_POOL / TOOLS / GROUPS / ACKS
    graph.js            關聯圖節點與邊（GNODES/GEDGES）、決定書草稿 HTML
  store/app.js          單例 reactive 狀態＋辦案流程（runTool / IMPL / send / chips / stages …）
  components/
    TopBar.vue          品牌、日夜主題切換（動畫 SVG）、示範徽章、側欄開關
    CaseTree.vue        左側案件樹（資料夾、拖曳、右鍵選單）
    FoldersRail.vue     右側卷宗（四群組、搜尋加入、新增閃光）
    Chat.vue            對話串（空狀態、使用者/AI 訊息、工具區塊、工具清單卡）
    Composer.vue        輸入區（斜線選單、建議 chips、附檔、自動高度）
    ToolOut.vue         工具結果（萃取／案例／法規／關聯圖／修潤／匯出／草稿）
    RelationGraph.vue   關聯圖 SVG（節點/邊、點選高亮、詳情）
    Sheet.vue           通用 modal 外框
    ContextMenu.vue     右鍵選單
    icons.js            內嵌 SVG 圖示
```

## 辦案流程（示範）

上傳卷證 → `萃取答辯書` → `查找相似案例` / `搜尋相關法規` → `生成關聯圖` → `生成決定書草稿`
→ `優化文案` / `生成 PDF` / `生成 DOC`。工具會依卷宗狀態自動檢核前提（例如未備法規與案例時，
草稿生成會拒絕執行），並將產物歸檔至右側卷宗。

輸入框按 `/` 可叫出工具選單，或直接用中文描述需求（會自動對應工具）；輸入「你會什麼」列出全部 API。
支援淺色／深色主題（右上角切換，初始跟隨系統）。
