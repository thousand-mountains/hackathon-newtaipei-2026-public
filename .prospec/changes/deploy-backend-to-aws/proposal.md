# deploy-backend-to-aws

把後端部署到大會 AWS 帳號，取得一個評審可以自己點開的部署網址。

- **負責**：後端與 git `-d4` session／部署執行 `-2e` session（原 `-72` 已結束）
- **相依**：前端 `rebuild-staff-workbench-frontend` 由 Pink 負責，**本 change 不等它**——映像檔帶的是目前已建置的 `prototype/dist/`，前端換版後重建映像檔即可。
- **死線**：繳交 2026-09-13 13:00。**部署網址是繳交必要項，評審會自己點。**

---

## Background

**目前狀態（2026-09-12 13:5x 實測）**

後端 live 全線可用：六節點、Bedrock 即時推論、Managed KB 檢索、C 型結論封鎖。測試 270/270。**但只跑在 `127.0.0.1`，沒有任何對外網址。**

`backend/DEPLOY.md` 的 ECS 計畫寫好了但**一次都沒實際部署過**。本 change 開始前已完成兩件前置（commit `16f46cd`，實跑驗證）：

- 刪掉 `Dockerfile` 寫死的 `ENV RUN_MODE=fixture` —— 原本會讓「忘記給 env」靜默退化成離線重播
- 加 `COPY prototype/dist/` —— 原本容器 `GET /` 回 503，評審打開只看得到 API 文件

**本機容器已驗證可打真帳號**：`GET /` 200／180,681 bytes；`/api/health` 四項全對；`POST /runs` 202→200 且 `model_ids` 是真實 inference profile；**C 型 `submit` 回 409**。

**硬前提（皆實查）**

| 前提 | 內容 | 影響 |
|---|---|---|
| Region | 僅 `us-east-1`／`us-west-2` | 全部在 us-west-2 |
| 服務白名單 | `ecs`／`ecr`／`elasticloadbalancing`／`ec2`／`iam`／`logs` 皆可用；**`apprunner` 不在清單** | App Runner 排除 |
| 回應時間 | 端到端 51–78 秒，N5 主筆佔 77% | **已用 202＋輪詢吸收**，每個 HTTP 呼叫都很短 |
| Bedrock 限速 | 賽方規範 ≤ 1 RPS | 已在 `llm/client.py` 主動節流 |
| 帳號回收 | **規劃基準 2026-09-13 13:00**（一手書面：開幕投影片「環境開放 9/12 08:00 – 9/13 13:00，AWS 開發環境關閉」）；9/15 08:00 之說**無一手來源**，係由 Workshop Studio 事件頁 Duration 72 小時回推 | **不投資難拆的架構**；不引入 CDK／IaC |

**方案選擇**：ECS Fargate + ALB。詳細 pros/cons 見報告 `2026-09-12-hack-ntpc-deploy-options`。一句話理由——**它是唯一能讓「本機驗過的那個東西」原封不動跑到雲上的方案**，而評審日只有一次機會。

---

## User Stories

### US-1: 評審點開部署網址就看得到系統，而且是真的在跑 [P1]

**作為**評審，**我想要**點開繳交表單上的網址就直接看到系統畫面，**這樣**我不必自己架環境就能評估。

驗收：
- 打開 ALB 網址，`GET /` 回 200 且是完整 UI（不是 API 文件、不是 503）
- `GET /api/health` 同時滿足四項：`run_mode=bedrock`、`fixture_only=false`、`kb_backend=kb`、`similar_case_backend=kb`
- **四項任一不對就是離線重播，不得交件**

### US-2: 服務在雲上呼叫 Bedrock 不需要任何長期金鑰 [P1]

**作為**開發者，**我想要**容器用 task role 取得臨時憑證，**這樣**金鑰不會進映像檔、不會進 task definition、不會進 git。

驗收：
- task definition 的 `environment` **沒有** `AWS_ACCESS_KEY_ID`／`AWS_SECRET_ACCESS_KEY`／`AWS_SESSION_TOKEN`／`AWS_PROFILE`
- 容器內 `sts get-caller-identity` 回的是 task role
- 實跑一次 `POST /runs`，`run_meta.model_ids.provider` 是 `bedrock`

前提已滿足：`retrieval/kb.py` 與 `llm/client.py` 都只傳 `region_name`、走 boto3 預設憑證鏈，**程式碼不需要改**。

### US-3: 守門在雲上也擋得住 [P1]

**作為**這個產品的設計者，**我想要**確認結論封鎖不是只在本機成立，**這樣**「拒絕生成」這個差異化才是真的。

驗收：
- 對部署網址直接打 `POST /api/cases/synthetic-blocked-01/submit`（**繞過前端**）
- 期望 **HTTP 409**，回應含 `recompute_note`「未採信前端送來的任何判斷」與 P0 `conclusion_requires_human`
- **回 200 就是 P0 事故，不得交件**

### US-4: task definition 是「跑哪個檔位」的唯一事實來源 [P1]

**作為**下一個部署的人，**我想要**只看 task definition 就知道服務會跑 fixture 還是 bedrock，**這樣**不會有第二處設定偷偷覆蓋它。

驗收：
- 映像檔內 `RUN_MODE` 未設（已完成，`16f46cd`）
- task definition 的 `environment` 明確列出 `RUN_MODE`／`RETRIEVER`／`MODEL_PROVIDER`／`AWS_REGION`／`BEDROCK_MODEL_ID_EXTRACT`／`BEDROCK_MODEL_ID_DRAFT`／`BEDROCK_KB_ID`／`KB_MIN_SCORE`／`PORT`
- `.env` 不進映像檔、不進 task definition

### US-5: EC2 不對外，只有 ALB 對外 [P1]

**作為**參賽者，**我想要**符合賽方「不得建立 Security Group 對外完全開放」的規範。

驗收：
- `SG-alb`：inbound 80 來自 `0.0.0.0/0`（僅此一個 port）
- `SG-task`：inbound 8080 **僅允許來源 `SG-alb`**，不含任何 CIDR
- 兩個 SG 都不開 22 / 全 port

### US-6: 部署失敗時看得出來失敗在哪 [P2]

**作為**部署的人，**我想要**在 task 起不來時能快速定位，**這樣**不會在評審日耗掉 45 分鐘。

驗收：
- CloudWatch log group `/ecs/hack-appeal-backend` 收得到容器 stdout
- task definition 沿用映像檔內建 HEALTHCHECK
- **Apple Silicon 上建置一律 `--platform linux/amd64`**——忘記加是第一次部署最常見的失敗原因

### US-7: 網址在評審日之前保持活著 [P1]

**作為**評審，**我想要**在 9/13 評審時段點開網址還是活的。

驗收：
- `desiredCount: 1`，task 異常時 ECS 自動重啟
- **9/13 上午再驗一次 US-1 的四項與 US-3 的 409**
- 已知限制：**部署網址可能在 2026-09-13 13:00 失效**。一手書面（開幕投影片）寫「環境開放 9/12 08:00 – 9/13 13:00，AWS 開發環境關閉」；主辦口頭另稱「環境開 72 小時，但以活動時間為準，明天 13:00 後即使環境還開著也交不了」〔紀要 :98〕——**口頭的「即使還開著」暗示 13:00 後不必然立刻關，但兩邊都沒把實際關閉時刻講死**。
- ⚠️ **交付風險（非文件細節）**：繳交項目要求部署網址，而**評審很可能在 9/13 13:00 之後才點開**。若環境屆時已關，我們交出去的網址會在被看見之前就死掉。**需向賽方一手確認實際關閉時刻**，並準備備援（錄影 demo／截圖存證）。需 Ci 拍板。

---

## Edge Cases

| 情境 | 預期行為 |
|---|---|
| 忘記給 `RUN_MODE` | fallback 到 fixture（fail-safe，不偷打模型），但 `/api/health` 的 `fixture_only=true` 會讓 US-1 驗收失敗，抓得到 |
| Bedrock 臨時憑證過期 | Workshop Studio 憑證僅供**本機**；雲上走 task role 不受影響 |
| ALB 閒置逾時 | ~~預設 60 秒，我們每個呼叫都是秒級不受影響~~ **這條已被實測推翻（2026-09-12）**。「秒級」只對 `/runs` 成立，**對 `/submit` 不成立**——`/submit` 刻意同步重跑六節點（`backend/api/app.py` 的 `run_case()`），bedrock 檔位端到端實測 65.7～75 秒，第一次部署時 C 型 submit 回的是 **ALB 504 而不是 409**。本機沒有 ALB，這條在本機驗不出來。現況：ALB idle timeout 設 **900 秒**；不取剛好夠用的 300，是因為前端輪詢逾時為 600 秒（`prototype/static/app.js` `POLL_TIMEOUT_MS`），**ALB 若短於它會先切斷**，評審看到的是我們沒寫也解釋不了的 504，而前端那句「後端連得上、只是這次跑失敗」的誠實訊息根本輪不到出場——**這是誠實性問題，不只是可用性**。⚠️ 900 秒是止血不是解法，正解是 `/submit` 改 202＋輪詢 |
| Bedrock throttle | `llm/client.py` 已主動節流 1.1 秒；仍被 throttle 時節點失敗回 502 帶原因，**不會靜默退回 fixture** |
| KB 不可用 | N4 通道 B 降級、通道 A 照常，`degraded` 欄位會說明是「檢索失敗」而非「查無相似案」 |
| 映像檔架構錯 | Fargate 起不來，CloudWatch 看得到；**先本機 `docker run` 驗過再推** |

---

## Success Criteria

部署完成的定義（**全部通過才算**，任一不過不得交件）：

```bash
# 1) 首頁是 UI
curl -s -o /dev/null -w "%{http_code} %{size_download}\n" http://<endpoint>/
#    期望 200，且 bytes 與本機 dist/index.html 同量級

# 2) 四項檔位檢查
curl -s http://<endpoint>/api/health | python3 -m json.tool
#    run_mode=bedrock, fixture_only=false, kb_backend=kb, similar_case_backend=kb

# 3) 真的打得到模型
curl -s -X POST http://<endpoint>/api/cases/synthetic-ordinary-01/runs
#    202 → 輪詢 200，run_meta.model_ids.provider == "bedrock"

# 4) 守門擋得住（最關鍵）
curl -s -X POST http://<endpoint>/api/cases/synthetic-blocked-01/submit \
     -H 'content-type: application/json' -d '{}' -w "\n%{http_code}\n"
#    期望 409
```

---

## Related Modules

| 模組 | 關係 |
|---|---|
| `backend/Dockerfile` | 已修（`16f46cd`）：不寫死 RUN_MODE、帶 `prototype/dist/` |
| `backend/DEPLOY.md` | **內容過時**，需隨本 change 更新：region 寫東京、taskRole 寫「什麼權限都不需要」、抬頭仍寫「真實 Bedrock 至今一次都沒打過」 |
| `backend/config/settings.py` | `DEFAULT_RUN_MODE` 維持 fixture（fail-safe），不改 |
| `backend/retrieval/kb.py`／`backend/llm/client.py` | 只傳 region、走預設憑證鏈，**雲上不需改** |
| `prototype/dist/index.html` | 建置產物，前端換版後要重建映像檔 |

---

## Open Questions

1. **KB 正本 bucket 未定。** 帳號內有三個 KB、四個 bucket，`.env` 的 `S3_KB_BUCKET` 指的那個**目前沒有被 KB 索引**（已在 `.env` 加警語）。task definition 的 `BEDROCK_KB_ID` 要填哪個，取決於這題。**本 change 可以先用現行的 `BEDROCK_KB_ID` 部署**，正本定案後改 task definition 重新部署即可（不需重建映像檔）。
2. **賽後網址是否需存活。** 環境關閉時刻兩說並陳（一手書面 9/13 13:00／無來源的 9/15 08:00 推論），**規劃基準取較保守的 9/13 13:00**。需向賽方一手確認，並由 Ci 拍板是否另議帳號。
3. ~~**要不要引入 CDK／IaC。** 本 change 的立場是不要。~~ **已推翻：Ci 拍板採用 CDK，且已用 CDK 部署完成**（worktree `team/hackathon-deploy`，分支 `hack-deploy-aws`）。換版流程 `infra/cdk/deploy.sh deploy`（約 4 分鐘、改設定不需重建映像檔），驗收 `infra/cdk/verify.sh`。
   ⚠️ **CDK 的 build context `exclude` 同時決定「什麼能被 `COPY`」與「映像檔 hash 怎麼算」**，兩邊不對齊不會報錯（已踩過兩次）。動 Dockerfile 的 `COPY` 要同時看 `infra/cdk/lib/appeal-backend-stack.ts` 的 exclude 清單。

---

## Constitution Check

| 原則 | 本 change 的對應 |
|---|---|
| 分層誠實 | US-1 的四項檢查就是為了擋「看起來在跑、其實是重播」 |
| 引用必可驗 | 不受部署影響（引用驗證在 N6，走 laws-snapshot 本地快照） |
| 規則引擎零 LLM | 不受部署影響 |
| 不編造測資 | 部署只跑既有合成案例 |
| 資料隔離 | 賽方資料集不進映像檔（`Dockerfile` 只 COPY `backend/` 與 `prototype/dist/`） |
| secret | US-2：無長期金鑰；`.env` 不進映像檔、不進 task definition |
| 30h 紀律 | 不引入 CDK；只建必要資源 |

---

## Next Steps

| # | 步驟 | 狀態 |
|---|---|---|
| 0 | 修 Dockerfile（RUN_MODE、前端） | ✅ 完成 `16f46cd` |
| 1 | 本機 `--platform linux/amd64` 建置並實跑驗證 | ✅ 完成（四項驗收全過，含 409） |
| 2 | 建 ECR repository、推映像檔 | ✅ 完成（改以 CDK 實作） |
| 3 | 建 task role（`bedrock:InvokeModel` 限定 inference profile ARN、`bedrock:Retrieve` 限定 KB ARN） | ✅ 完成（改以 CDK 實作） |
| 4 | 建 `SG-alb`／`SG-task`、ECS cluster、task definition、service | ✅ 完成（改以 CDK 實作） |
| 5 | 建 ALB ＋ target group（health check `/api/health`） | ✅ 完成（改以 CDK 實作） |
| 6 | 跑 Success Criteria 四條 | ✅ 完成（改以 CDK 實作） |
| 7 | 更新 `backend/DEPLOY.md` 為實測後的版本 | ✅ 完成（改以 CDK 實作） |
| 8 | 9/13 上午複驗 US-1 四項與 US-3 的 409 | ⏳ |
| 8a | **複驗第一步：回 Workshop Studio dashboard 重取 AWS 憑證**——那是臨時憑證會過期，且 `~/.aws/credentials` 沒有 expiry 欄位查不出剩多久，不要假設舊憑證還活著 | ⏳ |
