# 訴願決定書 AI 輔助撰擬系統（前端）

以 **Vue 3 + Vite + TailwindCSS v4** 重寫 `design/petition-ai-demo.html` 設計稿。
目前是**純前端 mock**（資料內建於 `src/data/`），尚未接後端；後端契約見
`.prospec/changes/rebuild-staff-workbench-frontend/proposal.md`。

## 開發

```bash
pnpm install
pnpm dev      # http://localhost:5175
pnpm build    # 產出 dist/
pnpm preview
```

Node >= 20。`vite.config.js` 已設定 `/api` 代理到後端（預設 `localhost:8091`），供之後接 API 用。

## 結構

```
src/
  main.js                 進入點
  App.vue                 版面殼（header + 上傳頁／工作台切換 + tooltip + toast）
  styles/main.css         Tailwind 匯入 + 設計 tokens 與元件樣式（自設計稿移植）
  store/workbench.js      單例響應式狀態與所有互動邏輯
  data/
    lawdb.js              法規資料庫（LAWDB）
    caseDb.js             相似案例（CASEDB）＋ caseText.json（全文）
    caseText.json         歷史決定書全文
    factDb.js             案情與爭點（FACTDB）
    procedure.js          程序審查 → 版式/主文/款次 對照表、委員名單
    docBuilders.js        四種版式（A/B/C/D）決定書組裝
  utils/lawRender.js      法規全文 / 重點條項渲染
  components/
    UploadPage.vue        上傳卷證頁
    Workbench.vue         工作台殼（wbar、下載選單、可拖曳分割、掛載 modal）
    LeftColumn.vue        左欄三分頁（案情與爭點／相關法條／相似案例）
    ProcPanel.vue         程序審查控制盤
    ChatPanel.vue         撰稿 AI 對話
    DecisionSheet.vue     右欄決定書稿紙（逐句可點、可編輯）
    AddLawModal.vue       加入法條
    FullTextModal.vue     法條／案例全文
    ConfirmModal.vue      確認對話框
    TheTooltip.vue        data-tip 提示
```

## 功能對照設計稿

- 兩頁動線：上傳頁 → 工作台（左分析／右草稿）。
- 左欄三分頁順序：①案情與爭點 ②相關法條 ③相似案例（預設 ①）。
- 程序審查下拉（A 不受理／B 駁回／C 撤銷、撤銷另處／D 部分駁回部分不受理）即時重生主文、事實、理由與結構。
- 逐句 ↔ 依據卡雙向連動高亮；點程序審查連到主文。
- 相關法條「看全文」列出整部法規（依條號排序、本件援用條項螢光筆標示）。
- 相似案例「看全文」顯示歷史決定書並標關鍵段落。
- 撰稿 AI：全文／單句四種指令（精簡／加強論述／補強回應／改公文語氣）＋自由提問。
- 下載 PDF（列印）／Word（.doc）。
