# v0 一夜版 prototype（2026-08-22）

## 目標
今晚討論用的可操作 prototype：期間計算器＋引用檢查器（真功能）、草稿工作台（fixture 畫面），
單一自足 HTML 可上報告台、本地 FastAPI 可跑 API 版。

## 步驟
1. data/laws-snapshot.json（11 部法規真實條號白名單＋修法對照＋19 判解白名單）
2. engine/deadline.py（純 Python）＋ data/test-vectors.json ＋ pytest
3. static/engine.js（鏡像實作，吃同一份 test-vectors）＋ node 自測
4. static/index.html 模板 ＋ build.py（inline engine/snapshot/fixtures → dist/index.html）
5. app.py（FastAPI：serve dist ＋ /api/deadline）
6. 驗收全綠 → 發布報告台 ＋ commit

## 驗收條件（可執行）
- [ ] pytest 全綠（≥15 案例，含 114年/03、113年/03 兩份歷史案復現）
- [ ] node 跑 engine.js 對同一份 test-vectors 全綠（Python/JS 零分歧）
- [ ] 引用檢查：貼 114年/15 理由段原文 → 無紅字；貼「最高行政法院110年度判字第9999號」→ 被攔
- [ ] C 情境切換 → 結論段紅區＋交接卡，頁首常駐「合成測資」badge
- [ ] dist/index.html 單檔、無外部資源、file:// 直開可用
- [ ] headless 截圖三分頁皆可讀

## 備援方案
JS/Python 引擎分歧修不完 → 砍 JS 版，發布頁改「僅引用檢查互動＋期間引擎截圖」，API 版仍完整。

## 最遲放棄時刻
今晚 22:00（相對：開工＋4h）

## 預估時數
4h

---
## 驗收紀錄（2026-08-22 完工）

| 條件 | 結果 | 證據 |
|---|---|---|
| pytest 全綠 | ✅ 4 passed（15 向量＋3 測試） | `uv run --with pytest pytest tests/ -q` |
| JS/Python 零分歧 | ✅ 15/15 | `node tests/parity.mjs` |
| 真實段落全綠 | ✅ 「找到 5 個引用：5 在庫、0 需注意、0 攔下」 | headless dump-dom `#t2-real` |
| 假字號被攔 | ✅ 「3 個引用：1 需注意（15之2→22）、2 攔下（110判9999、噪音法55）」 | `#t2-fake` |
| C 情境紅區＋交接卡 | ✅ 截圖 tab3.png；synthetic badge 常駐 | headless 截圖 |
| 單檔無外部資源 | ✅ 49KB、0 個 http src/href、file:// 直開自測 15/15 | grep + dump-dom |
| API 版可跑 | ✅ POST /api/deadline 回 2024-07-15/overdue（與 JS 同結果） | uvicorn smoke test |
| 歷史案復現 | ✅ 113年/03（寄存 6/13→週六順延→113/7/15）、114年/03 | test-vectors hist-* |

實際工時：~1.5h（AI 執行）。備援方案未動用。
