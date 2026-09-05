# v1 決定書撰擬工作台（2026-08-26）

## 目標
把 prototype 從 v0 的三分頁工具（期間計算器／引用檢查器／草稿工作台）換成一條完整的承辦動線：
**收文與補充 → 幕僚團分析 → 草稿編輯 → 燈號審核 → 送出審議**。
評審看到的不再是三個零件，而是一個承辦人真的能從頭走到尾、且每一句都交代得出來源的系統。

版面與流程沿用設計稿（政府網站語彙：深藍 #123B63 ＋ 印信紅 #9E2A2B ＋ 紙色底、稿紙格線、騎縫章）。

## 步驟
1. 設計稿 HTML 拆進既有 build pipeline：`static/index.tmpl.html`（版面＋CSS）、`static/app.js`（流程）、
   `data/case-demo.json`（示範案件：卷證、7 名幕僚、22 段文件模型、法規／案例／爭點卡）
2. `build.py` 改注入 `case-demo.json` ＋ `laws-snapshot.json`，並在組裝後 assert 沒有殘留注入點
3. **綠燈句接真引擎**：`applyDeadlineEngine()` 呼叫 `static/engine.js` 實算訴願期間，
   句子文字、燈號、算式卡全由引擎輸出產生，不是寫死字串；逾期時燈號轉紅並點出與駁回主文的矛盾
4. **法規卡接快照**：`verifyLaw()` 以 `laws-snapshot.json` 對照條號，標「在庫 ✓／庫外」
5. **幕僚敘述接實際結果**：看板與 log 內的 `{TOKEN}` 由 `fillTokens()` 回填真實句數／燈號數／字數／日期
6. 資料來源標記：頁首常駐「示範案件・去識別化資料」徽章（CONSTITUTION §3、§6）
7. 圖示字型保底：Material Symbols 載不到時整批隱藏並以純文字符號替代，不外洩 ligature 字面
8. `data/test-vectors.json` 加入示範案件向量，把決定書「理由五」的數字鎖進測試集

## 驗收條件（可執行）
- [x] `node tests/parity.mjs` → 16/16 向量全過（Python/JS 零分歧）
- [x] `python3 -m pytest tests/test_deadline.py -q` → 4 passed
- [x] `python3 build.py` → dist/index.html 產出且無殘留注入點
- [x] headless 全程走完 1→5 步、無 JS 錯誤（僅離線環境的 Google Fonts 連線失敗）
- [x] 綠燈期間句可展開 5 步算式，每步附法條依據
- [x] 收文頁把提起日改成 114/9/30 → 該句轉紅、文字改為「已逾…（期間至 114/8/12 屆滿）」、
      程序審查官敘述同步改為「第 77 條第 2 款不受理事由待人工裁量」
- [x] 幕僚看板數字與燈號審核頁一致（紅 4／黃 7／綠 10／共 21 句）
- [x] 離線開啟 file:// → `html.icons-off`、畫面無 `account_balance` 這類字面、提示鈕仍可聚焦且 tooltip 正常
- [x] 紅燈未全部確認前「送出審議」維持 disabled；一鍵確認需勾選切結才可按

## 備援方案
`:has()` 或 `document.fonts` 在現場瀏覽器出問題 → 拿掉算式卡展開高度與圖示偵測兩段（純視覺降級），
流程與引擎不受影響。引擎接線若在現場出錯 → `case-demo.json` 把該句 `engine` 欄位拿掉即回到靜態文字。

## 最遲放棄時刻
D1 12:00（決賽首日中午）；到點沒綠就退回 v0 三分頁版本（git history 保留）。

## 預估時數
4h（實作 2.5h、瀏覽器驗收 1h、文件 0.5h）

## 待辦：留給人的問題
1. **修法日期有衝突，需法制確認。** 設計稿全篇寫「洗錢防制法 113/7/31 修正公布，原第 15 條之 2 移列為第 22 條」
   （見 `case-demo.json` 的 s9、s11、L1 與法規檢索官 log）；但 `laws-snapshot.json` 的 `amendments`
   記為 `2023-06-14`（民國 112/6/14），且註明出處是 114年/15 決定書原文。兩者只能有一個對。
   決定書寫錯修法日期在法制局面前是硬傷，**送 demo 前請 qa-legal 或人工比對資料集內的法規 PDF 定案**，
   兩邊（`case-demo.json` 與 `laws-snapshot.json`）一起改。
2. 示範案件的案號／文號／歷史決定書編號若係自賽方資料集取得，依 CONSTITUTION §6 與 CLAUDE.md
   「資料不進 git」，需確認是否改為合成編號。目前當作已去識別化處理並加了頁首徽章。
