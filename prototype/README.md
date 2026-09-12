# prototype／五步動線前端

承辦人動線：**收文與補充 → 幕僚團分析 → 草稿編輯 → 燈號審核 → 送出審議**。
原生 JS、無框架、無 npm、無外部 JS 依賴（外連只有 Google Fonts，載不到會走系統字型並整批隱藏圖示）。

## 怎麼跑

### 想看完整平台（前端真的打後端 API）— 建議走這條

```bash
python3 prototype/build.py     # static/ 或 data/ 改過才需要重跑

uv run --with fastapi --with "uvicorn[standard]" --with pydantic -- \
    python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8080
```

打開 <http://127.0.0.1:8080/>。頁首徽章會顯示 **「live 後端・<案例 id>」**。
換案例用頁面上的下拉選單，或直接 `?case=synthetic-blocked-01`。

### 只想看畫面／要斷網備援

直接用瀏覽器開 `prototype/dist/index.html`（`file://`）。
頁首徽章會顯示 **「離線 fixture（未接後端）」**——這是刻意的，
讓人一眼知道畫面上的東西**不是**後端六節點的執行結果。

## 兩種資料模式在做什麼

| | live 後端 | 離線 fixture |
|---|---|---|
| 觸發條件 | `GET /api/health` 回 200 且 `ok:true` | 打不到 API（含 `file://` 直開） |
| 資料來源 | `POST /api/cases/{id}/runs` 的完整 CASE payload | build 時內嵌的 `data/case-demo.json` |
| 燈號、引用四態、why | **後端守門節點判定，前端不重算** | 頁內 `verifyLaw()` 對照 `laws-snapshot.json` |
| 期間算式 | 後端 N3 的 `screen.deadline.steps` | 頁內 `engine.js` 實算（改日期會即時重算） |
| 送出閘門 | 紅燈全確認 **且** 後端 `submit_allowed` | 只看紅燈是否全確認 |
| 交接卡／blockers | 後端 N6 的 `handoff` / `blockers` | 不顯示（fixture 沒有這兩塊） |

為什麼 live 模式前端不自己算燈號：CONSTITUTION §1 說燈號不是模型產出，
它也不該是前端產出。前端再算一次就等於多一個會跟後端打架的真相來源——
v0 的引用檢查是二態、後端是四態，兩邊同時開著就會出現「同一句，兩個顏色」。

## 檔案

| 路徑 | 是什麼 |
|---|---|
| `static/index.tmpl.html` | 版面與 CSS 模板，含 `__CASE__` / `__SNAPSHOT__` / `/*__ENGINE__*/` / `/*__APP__*/` 四個注入點 |
| `static/app.js` | 五步流程與雙模式邏輯 |
| `static/engine.js` | 訴願期間規則引擎（JS 鏡像實作） |
| `engine/deadline.py` | 訴願期間規則引擎（Python，權威實作；已搬遷至 `backend/engine/deadline.py`，sha256 相同） |
| `data/case-demo.json` | 離線 fixture 的示範案件（手寫，v0 遺產） |
| `data/laws-snapshot.json` | 離線法規快照（離線模式的引用對照用） |
| `data/test-vectors.json` | 期間引擎測試向量，Python 與 JS 共用同一份 |
| `build.py` | 組裝 `dist/index.html`（全內嵌單檔），組完 assert 沒有殘留注入點 |
| `app.py` | v0 的獨立本機伺服器（port 8787）。**整合後不需要用它**，留著是為了 v0 可回溯 |
| `dist/index.html` | 建置產物。**不得手改**——`backend/tests/run_all.py` 會實跑 build.py 比對位元組 |

## 測試

```bash
node prototype/tests/parity.mjs                                   # JS 與 Python 引擎零分歧
uv run --with pytest -- python -m pytest prototype/tests -q       # Python 引擎向量測試
python3 prototype/build.py                                        # 重建 dist（assert 無殘留注入點）
python3 backend/tests/run_all.py                                  # 含 dist 可重現性與契約測試
```
