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

### Phase S 追加（2026-09-07 bedrock-live-nodes 分支未做的事）

> 來源：`docs/spec/2026-09-07-bedrock-live-nodes-design.md` §2「不做」＋ plan 加值層四個 task（7b／8b／9／16）＋ review 過程延後的項目。
> 這些**全部沒做**，列在這裡是為了不讓它們消失，不是排程承諾。

| ID | Story | 負責 | 優先 | 狀態 |
|---|---|---|---|---|
| HACK-S-3 | 我可以對一份已跑完的案子追問細節，系統用同一份卷證回答（ask 追問 agent：Strands 單 agent + 六工具，SSE 串流） | backend | P2 | 未開始 |
| HACK-S-4 | 系統跑在 Bedrock AgentCore Runtime 上（承載 ask 那種有記憶的 agent；沒有 ask 就沒有理由做） | backend | P3 | 未開始 |
| HACK-S-5 | 我按下開始分析後，看得到六個節點逐一亮起（SSE 節點事件流 `GET /runs/{id}/events`；plan Task 7b） | backend＋frontend | P2 | 未開始 |
| HACK-S-6 | 我可以在任一張幕僚卡上按「從這裡重新產生」，只重跑該節點以下（前端每卡重新產生列；plan Task 8b，後端續跑 API 已具備） | frontend | P2 | 未開始 |
| HACK-S-7 | 我一條指令就能把資料集重新入庫到新帳號的 KB（`scripts/build_manifest.py`＋`ingest_kb.py` 冪等入庫；plan Task 9） | backend | P1 | 未開始 |
| HACK-S-8 | 爬蟲抓到的 251 件逾期案變成期間引擎的回放測試集（plan Task 9 逾期回放） | backend＋qa | P2 | 未開始 |
| HACK-S-9 | 掃描件（無文字層 PDF）的視覺讀取實測，並用真實 PDF 校準 `pdf_text` 0.60 門檻（plan Task 16；目前門檻是拍腦袋的值） | backend＋qa | P1 | 未開始 |
| HACK-S-10 | 法規快照擴充到 18 部（爬蟲，含修正日期），不再只有 11 部 | backend | P2 | 未開始 |
| HACK-S-11 | 洗錢防制法案件補爬（現有爬蟲只有廢清法、空污法，主 demo 案型反而沒有真實案源） | backend | P2 | 未開始 |
| HACK-S-12 | `backend/output/runs/` 有清理機制（現在每跑一次 `run_all.py` 就寫上百個 json，gitignored 但無上限） | backend | P3 | 未開始 |
| HACK-S-13 | `POST /api/cases` 有上傳總量與檔數上限（現在沒有；單檔 4.5MB vs 20MB 的落差也該在上傳時就講） | backend | P2 | 未開始 |
| HACK-S-14 | `RunIn` 改 `extra="forbid"`，未知欄位回 400 而不是靜默忽略 | backend | P3 | 未開始 |
