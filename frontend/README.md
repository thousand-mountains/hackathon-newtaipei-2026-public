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

## 功能現況（2026-09-12 接上真後端後）

**資料來源是後端 payload，不是 `src/data/*`。** 那些 mock 只在「完全連不上後端」時
作為明示的離線備援，畫面會標示它是假資料且不提供下載。

- 兩頁動線：上傳頁 → 工作台（左分析／右草稿）。
- **進頁面只探測 `/api/health` 與 `/api/cases`，不發 `POST /runs`。** 呼叫模型只由
  「確認上傳，生成草稿」觸發（舊版每開一次／每重整一次就燒一次 Opus 並等約 60 秒）。
- 左欄三分頁順序：①案情與爭點 ②相關法條 ③相似案例（預設 ①）。
- ①內含**收文欄位確認**（`IntakePanel.vue`）：13 個可確認欄位，
  `respondent_name`（原處分相對人）為必填。按「我已核對」會帶 `confirmed_intake` 重跑；
  未確認前後端維持結論段交人工，**那是正確行為不是故障**。
- 程序審查（`ProcPanel.vue`）顯示規則引擎的期間計算與攤開算式；
  **審查結論的下拉不再重新生成草稿**——草稿只能來自後端 `doc[]`。
  面板會說出這個結論是「規則引擎判定」還是「承辦人自行認定」（讀後端的
  `screen.procedure_conclusion.by`，前端不自己推）。
- 第二意見交叉比對：`compared`／`disagreement`／`not_comparable`／
  `refusal_asymmetry`／`shared_blind_spot` **五類分開顯示、顏色不同**。
  共用盲點最醒目且絕不給綠——它長得最像沒事。
- 引用卡片分開呈現**「字號可驗」與「與本案相關」**：綠燈只代表字號能對回法規快照，
  不代表系統確認它與本案相關（守門查核字號存在，不查核相關性）。
- 下載 PDF／Word **前會先打 `POST /api/cases/{id}/submit`**，由後端重跑六節點決定，
  409 就不下載並列出 blockers。前端不自行判定 `submit_allowed`。
- **撰稿 AI 目前停用**（`REWRITE_ENABLED = false`）：舊版那四個動作是前端寫死的
  正則字串替換，不是模型產出，而後端沒有改寫端點可接。畫面上有說明。

## 除錯注意事項

- **dev 模式看到「HTTP 500」先確認後端還活著。** Vite dev proxy 會把後端的
  ECONNREFUSED 轉成 500 回應，所以後端 process 死掉時畫面顯示的是
  「執行失敗 HTTP 500」而不是「無法連上後端」。生產環境同源、沒有 proxy，
  行為是對的（真正的 fetch reject → 歸類為連不上）。
  2026-09-12 因為這個差點把自己 process 被 SIGTERM 收掉的狀況報成後端 bug。
- **不要用 `innerText` 判斷 `<select>` 的選中值**——它會把所有 `<option>` 都吐出來。
  要讀 `el.value`。同日因此誤判過一次「後端未判定」。
- 端到端驗證請在**實際起的服務**上做，不要只看 `file://` 或靜態伺服器。
