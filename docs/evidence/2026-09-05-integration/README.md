# 前後端整合的驗收證據（2026-09-05）

這裡的東西是**可以重跑的**，不是截圖存檔而已。宣稱「驗過了」的每一條都對應到下面某一個檔。

## 怎麼重跑

先起服務（DEPLOY.md §0.0）：

```bash
python3 prototype/build.py
uv run --with fastapi --with "uvicorn[standard]" --with pydantic -- \
    python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8080
```

再跑瀏覽器驗證（headless Chromium）：

> 2026-09-05 追加：多一支 `verify_recalc.py`，驗 live 模式改日期即時重算。

```bash
uv run --with playwright -- python docs/evidence/2026-09-05-integration/verify_recalc.py \
    synthetic-blocked-01 2025-06-30 /tmp/shots
uv run --with playwright -- python docs/evidence/2026-09-05-integration/verify_ui.py \
    synthetic-ordinary-01 /tmp/shots
uv run --with playwright -- python docs/evidence/2026-09-05-integration/verify_ui.py \
    synthetic-blocked-01 /tmp/shots
uv run --with playwright -- python docs/evidence/2026-09-05-integration/verify_offline.py \
    "$PWD/prototype/dist/index.html" /tmp/shots
```

兩支腳本都會蒐集 `pageerror` 與 `console.error`，**有任何一則就 exit 1**。
輸出是 JSON（每一步實際讀到的 DOM 內容），可以跟這裡存的對照。

## 檔案

| 檔 | 是什麼 |
|---|---|
| `verify_ui.py` | live 模式五步逐步驗證（載入案件 → 幕僚團 → 草稿 → 燈號審核 → 送出） |
| `verify_offline.py` | `file://` 直開的離線 fixture 模式驗證 |
| `verify_recalc.py` | live 模式改提起訴願日 → 打 `POST /api/deadline` 重算 → 該句轉紅（自帶 PASS/FAIL 判定） |
| `recalc-blocked-01.json` | 重算前後的燈號、判定句、程序審查官卡片與閘門狀態 |
| `synthetic-blocked-01-step2.png` | 左欄兩份清單：獨立檢索結果 vs 草稿實際引用 + 落差說明 |
| `synthetic-ordinary-01-step2.png` | 法規卡的三種狀態：綠燈（草稿有引用、守門查核過）vs 中性虛線徽章「檢索到，草稿未引用」 |
| `synthetic-blocked-01-recalc-after.png` | 改日期重算後的燈號審核頁 |
| `synthetic-ordinary-01.json` | 一般案例的完整 DOM 節錄（本次執行輸出） |
| `synthetic-blocked-01.json` | 對抗案例：blockers、交接卡、對抗測資標記、送出鎖定 |
| `offline.json` | 離線模式：徽章、v0 行為照常、零 console error |
| `*-step3.png` | 燈號審核頁截圖（三種情境） |
| `synthetic-ordinary-01-step4.png` | 送出完成頁，統計改吃後端 run_meta |

## 這些證據**沒有**涵蓋的

- Playwright 的瀏覽器來自本機 `~/Library/Caches/ms-playwright` 快取，
  版本沒有釘住；換一台機器跑可能是不同版本的 Chromium。
- 只驗了 Chromium。Safari／Firefox 未測。
- 只驗了 `RUN_MODE=fixture`。live 模型分支沒有憑證，未驗。
- 只驗了兩個合成案例。真實卷證的形狀會不會打破前端渲染，未知。
