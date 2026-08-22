# 訴願承辦工作台 — Backlog

> 專案：appeal-ai-prototype ｜ 建立：2026-08-22
> 團隊配置：[.claude/team-roster.yaml](.claude/team-roster.yaml) ｜ Constitution：[CONSTITUTION.md](CONSTITUTION.md)
> 規格 SOT：[docs/spec/prototype-spec.md](docs/spec/prototype-spec.md) ｜ Tech 決策：[docs/tech-stack-decision.md](docs/tech-stack-decision.md)
> 流程：plan（plans/）→ 實作 → qa-legal 驗 → 勾稿。story 是使用者價值句，技術細節進 plans/。

## Phase 0 — 骨架就緒（開發者可觀察）

| ID | Story | 負責 | 優先 | 狀態 |
|---|---|---|---|---|
| HACK-P0-1 | 我可以本地起 FastAPI＋Vue dev server，curl /health 綠、lint 全綠 | backend＋frontend | P0 | 未開始 |
| HACK-P0-2 | 資料集在私有 S3，一條指令重建 KB（或本地向量備援），查詢回得來 | backend | P0 | 未開始 |
| HACK-P0-3 | 我可以看到 5 份 synthetic- 合成測資（1 份髒），每份附反推來源說明 | qa-legal | P0 | 未開始 |

## Phase 1 — 期間引擎（demo 主秀，最先全綠）

| ID | Story | 負責 | 優先 | 狀態 |
|---|---|---|---|---|
| HACK-P1-1 | 我輸入送達方式與日期，看到期滿日與**逐步算式**，每步附法條 | backend | P0 | 未開始 |
| HACK-P1-2 | 資料集裡的 77(2) 歷史案（含寄存＋週六順延）全部復現正確 | backend＋qa | P0 | 未開始 |
| HACK-P1-3 | 遇到「知悉時／回復原狀／80條」情境，系統顯示 caveat 要我人工確認，不硬算 | backend | P1 | 未開始 |

## Phase 2 — 主流程端到端（洗錢 77(2) 單案型）

| ID | Story | 負責 | 優先 | 狀態 |
|---|---|---|---|---|
| HACK-P2-1 | 我丟一份合成訴願書，看到抽取結果讓我確認/修正；抽不準時自動轉手動表單 | backend＋frontend | P0 | 未開始 |
| HACK-P2-2 | 我看到前 3 相似歷史案與逐案異同，畫面上沒有任何「應比照」字樣 | backend | P1 | 未開始 |
| HACK-P2-3 | 我拿到三段論草稿，每段能點開看來源；引用徽章標 ✓/⚠/✗，假判例被攔 | backend＋frontend | P0 | 未開始 |
| HACK-P2-4 | 丟入已知先合後破案，結論段變紅區＋判斷交接卡，**不出結論** | backend＋qa | P0 | 未開始 |

> Story note (HACK-P2-4)：qa-legal 對抗餵食 114年/19、113年/20；生成結論＝P0。

## Phase 3 — Demo 就緒

| ID | Story | 負責 | 優先 | 狀態 |
|---|---|---|---|---|
| HACK-P3-1 | 評審在投影幕上一眼分得出綠（可驗算）金（有出處）紅（人工判斷）三級 | frontend | P0 | 未開始 |
| HACK-P3-2 | 我一鍵切換三個 demo 情境；斷網時 fixture 照播 | frontend | P0 | 未開始 |
| HACK-P3-3 | 簡報含四功能對照表與合成測資聲明 slide | 全隊 | P0 | 未開始 |

## Phase S — Stretch（Phase 0–3 全綠才碰）

| ID | Story | 負責 | 優先 | 狀態 |
|---|---|---|---|---|
| HACK-S-1 | 第二案型（廢清 79I 煙蒂級）走完主流程 | backend | P2 | 未開始 |
| HACK-S-2 | C 型訊號涵蓋保留語氣型 | backend | P2 | 未開始 |
