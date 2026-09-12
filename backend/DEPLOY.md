# 部署：AWS ECS Fargate + ALB（us-west-2 奧勒岡）

> **2026-09-12 更新：已實際部署到大會 AWS 帳號並通過驗收。**
>
> 在此之前這份文件寫的是「計畫」，而且有兩處錯的：region 寫東京（賽方只允許
> `us-east-1`／`us-west-2`），以及抬頭寫「真實 Bedrock 至今一次都沒打過」。
> 現在雲上實跑的是 `RUN_MODE=bedrock` 檔位，`/api/health` 四項
> （`run_mode=bedrock`／`fixture_only=false`／`kb_backend=kb`／`similar_case_backend=kb`）
> 全對，C 型 `submit` 在雲上回 **409**。逐條驗收見 §3。
>
> **部署方式是 CDK**（`infra/cdk/`），不是手打 CLI。舊版 §3 的 CLI 步驟已被取代。

> **本文不含任何真實憑證、帳號 id、ARN 或端點**，只寫變數名稱與取得方式。
> 憑證一律走 AWS Secrets Manager／SSM Parameter Store，不進 git、不進映像檔、不進環境變數明文。

賽制限制：僅限使用 AWS 提供之基礎模型服務。本專案只碰 AWS，不使用任何其他雲的憑證或服務。

---

## 0.0 本機一個指令起整個平台（前端 + API 同一個 process）

```bash
python3 prototype/build.py     # 前端建置產物（單檔全內嵌）；static/ 或 data/ 改過才需要重跑

uv run --with fastapi --with "uvicorn[standard]" --with pydantic -- \
    python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8080
```

| 位址 | 是什麼 |
|---|---|
| <http://127.0.0.1:8080/> | 五步動線 UI，開起來就是 **live 模式**（頁首徽章寫「live 後端」） |
| <http://127.0.0.1:8080/?case=synthetic-blocked-01> | 換案例（也可以用頁面上的下拉選單） |
| <http://127.0.0.1:8080/api/health> | 真的去載 laws-snapshot 與每個合成案例，載不動回 503 |
| <http://127.0.0.1:8080/api/docs> | OpenAPI |

**這是 fixture 檔位唯一的官方啟動指令**。以前 `uv run … backend/api/app.py`（port 8788）
與 `prototype/app.py`（port 8787）是兩支各跑各的，整合後統一成上面這一行。

**bedrock 檔位**多兩個套件與一份 `.env`（`.env` 不進 git，內容見 §1.2）：

```bash
set -a; . ./.env; set +a          # RUN_MODE=bedrock 與 AWS 設定都在裡面
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart \
       --with strands-agents --with boto3 -- \
    python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8080
```

`GET /api/health` 的 `live_settings` 檢查會告訴你缺哪個變數或哪個套件（`boto3`／`strands`）。
**這條指令至今沒有在真的能打 Bedrock 的帳號上跑過**（見上方 2026-09-07 更新）。

前端在打不到 `/api/health` 時（例如直接 `file://` 開 `prototype/dist/index.html`）
會自動退回**離線 fixture 模式**，頁首徽章會改成「離線 fixture（未接後端）」——
斷網 demo 備援走這條，而且畫面上看得出來它不是後端的執行結果。

---

## 0. 前置：誰要先做什麼

大會帳號（us-west-2）的狀態，2026-09-12 實查：

| 事項 | 狀態 |
|---|---|
| AWS 帳號與角色 | **已就緒**：大會帳號，`AWS_PROFILE=hack-ntpc`。憑證是 Workshop Studio 臨時憑證，過期就回 workshop dashboard 重取 |
| Amazon Bedrock 服務開通 | **已開通**（舊版此欄寫「未開通、回 `Operation not allowed`」，那是 9/07 在別的帳號量到的） |
| Amazon Bedrock model access | **已完成**：雲上實跑 `model_ids.provider=bedrock` |
| Managed Knowledge Base | **已建**，id 在 `.env` 的 `BEDROCK_KB_ID`。**正本 bucket 尚未拍板**（帳號內有多個 bucket 與 KB），見 §3.5 |
| ECR repository | **CDK bootstrap 自動建**（`cdk-hackntpc-container-assets-…`），不用手動建 |
| VPC／子網路／安全群組 | **用預設 VPC**，SG 由 CDK 建（見 §3.2） |

沒有 Bedrock 時，**服務仍可用 `RUN_MODE=fixture` 部署並 demo**——這是刻意設計的降級路徑。

### 0.1 本機工具需求（踩過的坑寫在這）

| 工具 | 版本／取得 |
|---|---|
| Docker | 要能 build `linux/amd64`（Apple Silicon 上靠 `--platform`／CDK 的 `LINUX_AMD64`） |
| Node | v20 以上（實際用 v24）。CDK CLI 走 `npx`，不必全域安裝 |
| Python | **`/opt/homebrew/bin/python3.14`**。系統 `python3` 是 3.9，跑 `backend/tests/run_all.py` 會掛 |
| AWS CLI | v2 |

**坑（2026-09-12，在 mini 上實際撞到）：`brew install awscli` 裝完 `aws` 指令直接 ImportError。**

```
ImportError: dlopen(..._awscrt.abi3.so): Library not loaded:
  /opt/homebrew/opt/aws-c-s3/lib/libaws-c-s3.1.0.dylib
```

awscli 2.36.44 的 `_awscrt` 找 `libaws-c-s3.1.0.dylib`，但 homebrew 的 `aws-c-s3` 1.1.0
只裝了 `libaws-c-s3.1.1.0.dylib`／`.1.1.dylib`／`.dylib`——**版號對不上，是 formula 的問題，
`brew reinstall aws-c-s3` 修不掉**。補一個 symlink 就能跑：

```bash
ln -sf libaws-c-s3.1.1.0.dylib \
  /opt/homebrew/Cellar/aws-c-s3/1.1.0/lib/libaws-c-s3.1.0.dylib
aws --version   # 應該就回 aws-cli/2.36.44 了
```

> 不想動 homebrew 的檔案的話，`uv tool install awscli` 會裝 v1 到 `~/.local/bin/aws`，
> 本專案用到的子命令（`ecr`／`ecs`／`elbv2`／`ec2`／`iam`／`logs`／`sts`／`cloudformation`）v1 都有。
> 這條路沒動到系統套件，但 v1 已停止新功能更新。

**另一個坑：bash 在這台機器的 locale 下，`$var` 緊接全形標點會把標點當成變數名的一部分**
（`echo "HTTP $code，$size"` → `code，: unbound variable`）。`deploy.sh`／`verify.sh` 裡
所有靠著中文的變數都寫成 `${code}`，改這兩支腳本時請沿用。

---

## 1. 環境變數

執行期只認這些變數。**值不寫在這裡**，由 ECS task definition 注入。

### 1.1 一定要設的（無憑證需求）

| 變數 | 值域 | 預設 | 說明 |
|---|---|---|---|
| `RUN_MODE` | `fixture` / `local` / `bedrock` | `fixture` | **`fixture`／`bedrock` 可用；`local` 未實作，N1／N5 會 raise、API 回 HTTP 501 並說明缺什麼**——這是刻意的，不讓服務假裝正常。`bedrock` 另需 §1.2 的變數與憑證齊全，缺項一樣回 501／502 帶原因，**不會偷偷退回 fixture**（spec D5）。 |
| `PORT` | 整數 | `8080` | 容器監聽埠，需與 ECS target group 一致。 |

### 1.2 `RUN_MODE=bedrock` 才需要的

變數名稱以 `.env.example` 與 `backend/config/settings.py` 為準，本表與它們對齊。
**值一律不寫在這裡**（帳號 ID、KB id、model id、bucket 名都不進文件）。

| 變數 | 說明 | 怎麼供給 |
|---|---|---|
| `MODEL_PROVIDER` | `bedrock`（預設）／`openai`。**賽制僅限 AWS 服務提供之基礎模型**，`openai` 只供開發期調 prompt，其輸出不得當驗收證據 | task definition 環境變數（非機密） |
| `AWS_REGION` | 固定 `us-west-2`（賽方只允許 `us-east-1`／`us-west-2`） | 同上 |
| `AWS_PROFILE` | **只在本機開發用**的 profile 名。ECS 上**不要設**——那裡走 task role | 本機 `.env`／`~/.aws`，不進 git |
| `BEDROCK_MODEL_ID_EXTRACT` | N1 抽取用的基礎模型 id 或 inference profile id | task definition 環境變數（非機密） |
| `BEDROCK_MODEL_ID_DRAFT` | N5 主筆用 | 同上 |
| `RETRIEVER` | `lawtable_only`（預設，相似案通道回空）／`kb`（走 Managed Knowledge Base） | 同上 |
| `BEDROCK_KB_ID` | Managed Knowledge Base id（N4 檢索通道 B）。`RETRIEVER=kb` 時必填 | 同上 |
| `KB_MIN_SCORE` | 檢索命中分數下限，預設 `0.25`；低於此值的命中丟棄 | 同上 |
| `S3_KB_BUCKET` | 資料集所在的 S3 bucket 名稱，**入庫腳本用**（執行期服務不讀 S3） | 同上；bucket **必須非公開**（CONSTITUTION §6）。**2026-09-07 更名**：舊表寫作 `KB_DATA_BUCKET`，程式實際讀的是 `S3_KB_BUCKET` |

**憑證怎麼給**：不設 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` 這類變數。
ECS task 用 **task role**（`taskRoleArn`）取得臨時憑證，boto3 會自動從 container credentials
端點拿到。長期金鑰不進任何檔案、不進映像檔、不進 git。

Task role 需要的最小權限（等真的要接 Bedrock 時再開）：
- `bedrock:InvokeModel`、`bedrock:InvokeModelWithResponseStream`（限定到指定 model ARN）
- `bedrock:Retrieve`（限定到指定 Knowledge Base ARN）
- `s3:GetObject`（限定到資料集 bucket 的指定 prefix，唯讀）
- `s3:PutObject`（**只給跑 ingest 腳本的角色**，限定到資料集 bucket 的指定 prefix。
  執行期的 task role **不需要**寫入權限——服務不上傳任何東西）

### 1.3 續跑與輪詢（2026-09-07）

`POST /api/cases/{id}/runs` 的回應**依檔位不同**，前端 `postRun()` 已把差異吸收在一處：

| 檔位 | 回應 | 後續 |
|---|---|---|
| `fixture` | **200** ＋ 完整 payload | 沒有後續，同步跑完 |
| `bedrock` | **202** ＋ `{run_id, result_url}` | 前端每 2 秒輪詢 `GET /api/runs/{id}`：**409** = 還在跑（繼續等）、**200** = 結果、**502** = 節點失敗帶原因、**404** = 沒這個 run |

- 結果檔寫在 **`backend/output/runs/`**（gitignored）。`GET /api/runs/{id}` 讀的就是它。
- **process 重啟後，進行中的 run 狀態不保留**：事件匯流排在記憶體裡，重啟後那個 run_id
  會變成 404 或停在 409。demo 期間不要重開服務；真的重開就重新 `POST /runs`。
- **沒有 SSE 端點**。逐節點事件流（`GET /runs/{id}/events`）是加值層，本輪未做。
- `POST /runs` 可帶 `base_run_id` + `from_node`，從指定節點往下重跑到 N6（N6 永遠最後跑）。
  `from_node` 沒帶 `base_run_id` 回 **400**。

---

## 2. 建置與推送映像檔

> **2026-09-12 起這一節只用於「本機先驗一次」。** 實際推送由 CDK 代勞
> （`infra/cdk` 的 `fromAsset` 會自己 build、tag、push 到 CDK 的 ECR repository），
> 下面那幾條手打的 `ecr get-login-password`／`docker push` 不需要再跑，
> （那幾條的 region 原本寫東京，已一併改成 `us-west-2`。）

建置 context 是**專案根目錄**（Dockerfile 在 `backend/` 底下但要抓整個 backend 樹）：

```bash
# 從專案根目錄執行
docker build -f backend/Dockerfile -t hack-appeal-backend:phase0 .

# 本機先驗一次再推
docker run --rm -p 8080:8080 -e RUN_MODE=fixture hack-appeal-backend:phase0
curl -s http://127.0.0.1:8080/api/health
curl -s -X POST http://127.0.0.1:8080/api/cases/synthetic-ordinary-01/runs | head -c 400
```

推 ECR（`<account>`／`<repo>` 自行替換，本文不寫真實值）：

```bash
aws ecr get-login-password --region us-west-2 \
  | docker login --username AWS --password-stdin <account>.dkr.ecr.us-west-2.amazonaws.com

docker tag hack-appeal-backend:phase0 <account>.dkr.ecr.us-west-2.amazonaws.com/<repo>:phase0
docker push <account>.dkr.ecr.us-west-2.amazonaws.com/<repo>:phase0
```

映像檔是 `linux/amd64`。**在 Apple Silicon 上建置要加 `--platform linux/amd64`**，
否則 Fargate 會起不來（這是最常見的第一次部署失敗原因）：

```bash
docker build --platform linux/amd64 -f backend/Dockerfile -t hack-appeal-backend:phase0 .
```

**已在本機實測過（2026-09-05）**：上面這行 `--platform linux/amd64` 建置成功；
`docker run -p 18080:8080 -e RUN_MODE=fixture` 起容器後 `/api/health` 回 200、
兩個合成案例都跑得完（對抗案例照樣 `submit_allowed=false`）、內建 HEALTHCHECK 轉為
`healthy`、容器內以 `appuser` 執行且 `/app/prototype` 不存在（映像檔確實沒帶進去）。
**尚未實測的是 ECR 推送與 ECS 部署本身**——那需要 AWS 帳號，見 §0。

---

## 3. 部署（CDK）

程式在 `infra/cdk/`，用 `deploy.sh` 包起來——它會先讀 `.env`（模型 id 與 KB id 都在裡面，
**不進 git、不寫死在 CDK 程式裡**），再把 region 鎖在 `us-west-2`。

```bash
cd infra/cdk
npm install
./deploy.sh bootstrap                      # 一次性；qualifier=hackntpc
./deploy.sh diff                           # 看會動到什麼
./deploy.sh deploy --require-approval never
./verify.sh                                # 驗收四條，全綠才算部署完成
```

部署前 `deploy.sh` 會自動跑 `check_context.sh`，**不一致就中止、不會推上去**。
也可以單獨跑：`./deploy.sh check`。

> **⚠️ 在 git worktree 部署，必須自己 build 前端。**
> `frontend/dist` 被 `frontend/.gitignore` 排除，而 **git worktree 不共用未追蹤檔**——
> 所以 rebase 完你的 worktree 裡**不會有 dist**，`COPY frontend/dist/` 會失敗。
> （2026-09-12 實際撞到，`check_context.sh` 在非人造情境第一次攔下來。）
>
> **不要從別人的工作樹複製 dist 過來**，要在自己這邊從 committed 原始碼重建：
>
> ```bash
> cd frontend
> npx --yes pnpm@9 install --frozen-lockfile
> npx --yes pnpm@9 run build      # 產物：dist/ 恰 3 檔、232K
> ```
>
> 這比「複製後檢查有沒有夾帶」可靠一個等級：`frontend/public/` 在 git 裡**沒有任何追蹤檔**，
> 所以從 git build 出來的產物**在構造上就不可能夾帶**本機遊魂檔——
> **消除可能性優於檢查可能性**。
>
> 附帶收穫：兩台不同路徑各自建置（worktree 從 git 重建／另一台的本機 build）
> 產出**同一個 JS 雜湊 `index-BbclwKsz.js`**，這個前端是可重現的。

`deploy.sh` 找 `.env` 的順序是：`HACK_ENV_FILE` → repo 根目錄 → 隔壁的
`hackathon-newtaipei-2026/.env`。找不到就**直接停**，不會靜默退化成離線重播。

### 3.1 建了哪些資源

| 資源 | 命名／說明 |
|---|---|
| ECS cluster | `hackntpc-appeal-cluster` |
| ECS service（Fargate） | `hackntpc-appeal-service`，`desiredCount: 1`、1 vCPU／2 GiB |
| ALB | `hackntpc-appeal-alb`，internet-facing，HTTP 80 |
| target group | HTTP 8080，health check 打 `/api/health`（不是 `/`） |
| task role | `hackntpc-appeal-task-role` |
| log group | `/ecs/hack-appeal-backend`，保留 7 天 |
| ECR repository | CDK bootstrap 建的 `cdk-hackntpc-container-assets-…` |

映像檔內容：`backend/`、`prototype/dist/`、以及 **`data/manifest.json`**。
CDK 的 `exclude` 另外排掉整個 `infra/`：Dockerfile 從來沒 COPY 它，不排的話
**改一行 CDK 程式就會讓映像檔的 asset hash 變掉**，於是「只改 ALB 設定」也要
重建、重推、換 task definition。

> **⚠️ 改 Dockerfile 的 `COPY` 時，一定要連 `appeal-backend-stack.ts` 的 `exclude` 一起看。**
> `exclude` 同時決定「什麼進得了建置 context」與「asset hash 怎麼算」，
> 而前者出錯**完全不會報錯**：`docker build` 成功、容器啟動、`/api/health` 回 200，
> 東西只是默默不見（2026-09-12 一天內踩到兩次）。
>
> 這件事已經不靠人記得了——`infra/cdk/check_context.sh` 會把 Dockerfile 每一條
> `COPY` 的來源拿去跟 staged context 比對，缺了或變成空目錄就中止部署。
> `deploy.sh deploy` 會自動跑它；負向測試做過（故意把 `prototype/dist` 排掉 → 確實擋下）。
manifest 是雲上語料具名揭露的來源（`/api/health` 的 `provenance.retrieval_note`），
少了它雲上只會說「讀不到入庫清單，故不報各批筆數」。CDK 的 `exclude` 因此寫成
`data/*` ＋ `!data/manifest.json`——**只放這一份進建置 context**，賽方資料集若被放進
`data/` 也不會被帶進去（CONSTITUTION §5）。

這個帳號是共用的（已有多個 bucket 與 KB），所以所有資源都掛 `hackntpc-appeal` 前綴，
CDK bootstrap 也用專屬 qualifier `hackntpc`，一眼認得出哪些是這個專案的。

### 3.2 三個不能省的設定

1. **映像檔一定是 `linux/amd64`。**
   CDK 那邊釘在 `ecr_assets.Platform.LINUX_AMD64`。在 Apple Silicon 上少了這行，
   Fargate 會 exec format error 起不來——第一次部署最常見的失敗原因。

2. **不放 NAT gateway，task 走公有子網 ＋ public IP。**
   自建 VPC 就得配 NAT 才拉得到 ECR 映像檔，多花錢也多花時間。對外仍然只有 ALB
   進得來：`SG-alb` 只開 80 給 `0.0.0.0/0`，`SG-task` 的 8080 **只允許來源 `SG-alb`**，
   沒有任何 CIDR 規則、沒開 22。（賽方規範：不得建立對外完全開放的 SG。）

3. **ALB idle timeout 放寬到 900 秒。**
   預設 60 秒**不夠**。`POST /api/cases/{id}/submit` 刻意同步重跑六節點再判斷
   （`backend/api/app.py:441`，不信前端送來的 `submit_allowed`），bedrock 檔位端到端
   51–78 秒（實測 65.7／75 秒），超過 60 秒就被 ALB 切成 **504**。
   「202 ＋ 輪詢讓每個呼叫都是秒級」只對 `/runs` 成立，**對 `/submit` 不成立**——
   而本機驗收沒有 ALB，所以這條在本機驗不出來（2026-09-12 實際踩到）。

   **為什麼是 900 而不是剛好夠用的 300**：前端的輪詢逾時是
   `POLL_TIMEOUT_MS = 10*60*1000`（600 秒，`prototype/static/app.js:38`）。
   ALB 若短於 600 秒，慢一點的請求會**先**被 ALB 切成 504——而 504 是一個
   我們沒有寫、也解釋不了的錯誤頁，前端那條「後端連得上、只是這次跑失敗，
   不會退回離線 fixture」的誠實訊息根本輪不到出場。
   900 秒讓順序變成**客戶端先放棄 → 走我們自己的錯誤路徑 → 說得出真正原因**，
   ALB 只在客戶端已經放棄之後才收尾。這是誠實性問題，不只是「避免 504」。
   （ALB 上限 4000 秒。代價是卡住的請求佔連線更久，`desiredCount=1` 的 demo 量級吃得下。）

   要讓 `/submit` 也變秒級就得改成 202 ＋ 輪詢，那是後端的事，部署層不該替它決定。

### 3.3 憑證：容器裡一把金鑰都沒有

task definition 的 `environment` **沒有** `AWS_ACCESS_KEY_ID`／`AWS_SECRET_ACCESS_KEY`／
`AWS_SESSION_TOKEN`／`AWS_PROFILE`。`retrieval/kb.py` 與 `llm/client.py` 只傳 region、
走 boto3 預設憑證鏈，所以憑證是 task role 給的臨時憑證。

task role 的權限只有兩條，都限定到具名資源：

- `bedrock:InvokeModel`／`InvokeModelWithResponseStream` → 兩個 inference profile
  **以及它們背後的 foundation model**（`arn:aws:bedrock:*::foundation-model/…`）。
  跨區 inference profile（`us.` 前綴）少給後面這種 ARN 會 AccessDenied，
  這是配 policy 最常漏的一條。
- `bedrock:Retrieve` → 只有 `.env` 指定的那一個 knowledge base。

### 3.4 驗收（`./verify.sh`，四條全綠才算完成）

```bash
curl -s -o /dev/null -w "%{http_code} %{size_download}\n" http://<endpoint>/
curl -s http://<endpoint>/api/health | python3 -m json.tool
curl -s -X POST http://<endpoint>/api/cases/synthetic-ordinary-01/runs   # 202 → 輪詢 /api/runs/{id}
curl -s -X POST http://<endpoint>/api/cases/synthetic-blocked-01/submit \
     -H 'content-type: application/json' -d '{}' -w "\n%{http_code}\n"
```

**US-1 的判準跟前端形狀無關，這是刻意的。** 它原本寫「200 且 >100KB」，那是為
單檔全內嵌的舊 dist 訂的；切到 Vite 之後 `index.html` 只有 933 bytes（內容在 `/assets/*`），
這條就誤報了。**但正確的修法不是把門檻調低**——舊的單檔版 184KB 一樣 >100KB，
調低之後「誤部署成舊版映像檔」會照樣通過，那就變成一條因為錯誤理由而通過的檢查。

現行判準是：`GET /` 回 200 **且** `index.html` 至少引用一個站內資源 **且**
每個被引用的資源都取得到。第二項順便把「映像檔沒換到」變成可偵測的——
舊單檔版引用數為 0，會直接紅。負向測試做過：用一份 180,114 bytes、零站內引用的
假首頁（**它會通過舊判準**）→ 新判準正確報「沒有引用任何站內資源，通常代表映像檔沒換到」；
另用一份引用不存在 asset 的首頁 → 報「回 404，頁面會載入失敗」。

`verify.sh` 另外會看 `/api/health` 的 `frontend_dist`：它逐一檢查 `index.html` 引用的
每個 `/assets/…` 是否存在，所以抓得到「build context 少了一半前端、但 `GET /` 照樣回 200」
這種情況——比只看狀態碼有訊息量。後端若沒有這一項會標「略過」，不算失敗。

`verify.sh` 最後還有一段**資料隔離**檢查，對**線上**實打幾條「不該公開」的路徑。
為什麼要打線上而不是只檢查建置輸入——`backend/api/app.py:565-566` 把整個 dist 目錄
掛在 `/static` 底下（`app.mount("/static", StaticFiles(directory=FRONTEND_DIST))`），
所以 **dist 裡任何一個檔案都能被直接 `GET` 到，不需要任何憑證**，
而 `index.html` 有沒有引用它完全不影響。實測：`/static/index.html` → 200／184,068 bytes。

> **這條決定了誤放檔案的後果等級。** 一份誤放進 `frontend/public/`（vite 會原樣複製進 `dist/`）
> 的賽方資料集衍生檔，不只是「跟著映像檔上線」——它是**公開網址上直接抓得到的一個 URL**。
> 賽方資料集「僅供競賽之用」，這就是資料隔離違規。
>
> 三道檢查各擋一種，缺一不可：
> | 檢查 | 擋什麼 | 在哪 |
> |---|---|---|
> | `check_context.sh` | Dockerfile 要的東西**少了** | 建置前 |
> | 後端紅線掃描 | dist 裡**多了**沒被引用的檔 | 建置後 |
> | `verify.sh` 資料隔離段 | 線上**真的抓得到**不該抓到的 | 部署後 |
>
> 現行部署實測：`data/manifest.json` 雖在映像檔內，但不在掛載範圍，猜測路徑全部 404。
> 且 manifest 只含 `path`／`sha256`／`source_pdf`／`outcome`／`provenance`／`cjk_ratio`，
> 無文件內容、無當事人姓名（實查 2,477 筆的欄位）。

#### 為什麼那段檢查不寫死路徑清單

**掛載點會變，而且已經變過一次：**

| 版本 | 掛載 | 對外範圍 |
|---|---|---|
| 舊（`84cb4ed`） | `app.mount("/static", StaticFiles(dist))` | **整個 dist** |
| 新（切 Vite 後） | `app.mount("/assets", StaticFiles(dist/"assets"))` | 只有 `assets/`，且 `/static` **不存在** |

所以「對 `/static/…` 打出 404」在新版**不代表資料被保護，只代表那個掛載點沒了**。
一份寫死的路徑清單會在換版後全部變綠，而真正的暴露面 `/assets/` 一條都沒測到——
**一條因為錯誤理由而通過的檢查，比沒有檢查更糟**，它給人「已經驗過」的錯覺。

`verify.sh` 因此改成自我校準：

1. 抓線上的 `index.html`，用 `probe_refs.py` 反推**當下真正的掛載前綴**（`/assets/app-x.js` → `/assets`）。
2. **先證明探針打得到一個活的資源**（引用清單裡任一個回 200），之後的 404 才具有意義。
3. 對推出來的每個前綴，探測 `kb-graph.json`／`manifest.json`／`index-state.json`／`laws-snapshot.json`。
4. 單檔全內嵌版沒有任何站內引用 → 如實回報「**沒有掛載面可測，這不是通過**」，不假裝綠燈。

負向測試（用本機 fixture 模擬換版後的掛載）：

```
✓ 探針自我校準：/assets/index-abc123.css 回 200（底下的 404 才有意義）
✗ 資料隔離：/assets/kb-graph.json 回 200 — 公開網址上抓得到，這是資料隔離違規
```
移掉該檔後 → `✓ 實際掛載前綴 [/assets] 底下抓不到 …`。**前綴是推出來的，不是寫死的。**

兩條紅線：

- `/api/health` 四項任一不對 → 跑的是**離線重播**，不得交件。
- C 型 `submit` 沒回 **409** → P0，守門在雲上破了。
  （409 的 body 要看得到 `recompute_note`「未採信前端送來的任何判斷」
  與 P0 `conclusion_requires_human`。）

部署網址從 CloudFormation output 取，不寫進這份文件：

```bash
aws cloudformation describe-stacks --stack-name hackntpc-appeal-backend \
  --query "Stacks[0].Outputs[?OutputKey=='ServiceUrl'].OutputValue" --output text
```

### 3.5 已知限制

- **環境收回時間：以 Ci 2026-09-12 的口頭確認為準——環境會一直開到黑客松結束。**

  **來源等級照實記：Ci 口頭確認（2026-09-12），非賽方書面。** 這一點很重要，不要在轉述時
  升級成「賽方確認」。與 `.prospec/changes/deploy-backend-to-aws/proposal.md` 的「硬前提」
  表格同一個說法（該表也標了同樣的來源等級）。

  先前兩說**皆不再作為規劃基準**，留在這裡只為了讓看過舊版的人知道它們去哪了：

  | 已作廢的說法 | 原始來源 | 為什麼不用了 |
  |---|---|---|
  | 環境開放至 2026-09-13 13:00，屆時 AWS 開發環境關閉 | 主辦方投影片（決賽開幕說明會），一手書面 | 被 Ci 的口頭確認取代 |
  | 帳號 2026-09-15 08:00 收回 | 無一手來源，回推自 Workshop Studio 事件頁的 Duration 72 小時 | 本來就是推論 |

  ⚠️ 口頭確認的效力弱於書面。若賽後仍需長期可存取，需另備帳號——超出本 change 範圍，需 Ci 拍板。
- 前端換版後要重建映像檔：`./deploy.sh deploy` 會自己重建並推送（`fromAsset` 的 hash 變了）。
- 換 KB 正本只要改 `.env` 的 `BEDROCK_KB_ID` 再 `./deploy.sh deploy`，**不需重建映像檔**。

### 3.6 ALB 來源 IP 白名單（預設全開，交件前才收窄）

`ALB_ALLOWED_CIDRS` 控制誰連得到 ALB 的 80 port。**不設就是 `0.0.0.0/0`，任何人都點得開**——
目前（2026-09-12）就是這個狀態，Ci 已拍板在交件前不收窄。

```bash
# 現在：不帶這個變數，維持全開
./deploy.sh deploy

# 交件前收窄（逗號分隔，空白會自動去掉）
ALB_ALLOWED_CIDRS=<四組CIDR> ./deploy.sh deploy
```

怎麼確認目前是哪一種——看 stack 的 `AlbIngress` output：

```bash
AWS_PROFILE=hack-ntpc AWS_REGION=us-west-2 aws cloudformation describe-stacks \
  --stack-name hackntpc-appeal-backend \
  --query "Stacks[0].Outputs[?OutputKey=='AlbIngress'].OutputValue" --output text
```

全開時印 `0.0.0.0/0`；收窄後印那幾組 CIDR，以逗號相連。**部署完一定要看這行。**

但它**不是 ground truth**：`AlbIngress` 的值是從 CDK props 推導的（`infra/cdk/lib/appeal-backend-stack.ts:232`），
不是去讀 Security Group 實況。有人在 console 手改 SG、或踩到下面那個 `.env` 陷阱，
它會照樣印出看起來正常的值。要確認實況再查一次 SG：

```bash
AWS_PROFILE=hack-ntpc AWS_REGION=us-west-2 aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=*ServiceLBSecurityGroup*" \
  --query 'SecurityGroups[].IpPermissions[?ToPort==`80`][].IpRanges[].CidrIp' --output text
```

> `[?ToPort==\`80\`]` 後面那個 **`[]` 不能省**。少了它，投影會多包一層、
> 查詢**靜默回空**——而空輸出看起來就像「沒有任何人連得進來」，是最容易誤判的方向。
> （這條實測過：少 `[]` 時對一份含 80 埠規則的樣本回 `[]`。）
>
> 空輸出有兩種可能，要分得出來：真的沒有 80 埠規則，或 `--filters` 沒配到任何 SG。
> 先拿掉 `--query` 跑一次看有沒有撈到 SG，再判斷。

⚠️ **`.env` 會覆蓋你在命令列給的值。** `deploy.sh:32-35` 的 `set -a; . "$env_file"; set +a`
在命令列變數**之後**執行，所以 `.env` 裡若有 `ALB_ALLOWED_CIDRS`，
`ALB_ALLOWED_CIDRS=... ./deploy.sh deploy` 會被**靜默覆蓋**——更糟的是下面 §6 的**回滾**
（不帶該變數重跑）在那種情況下根本不會回滾，而 `AlbIngress` 會照著 `.env` 印，看起來一切正常。
（實查 2026-09-12：`.env` 目前沒有這個變數，回滾可用。動手前再確認一次。）

---

## 4. 備援路徑（CONSTITUTION §8：備援事先寫好，不臨場發明）

| 失敗情形 | 備援 | 最遲決定時刻 |
|---|---|---|
| ECR 推不上去 / ECS 起不來 | **本機一個指令起整個平台 demo**（見 §0.0，前端＋API 同一個 process），已實測可跑 | 部署開始後 45 分鐘 |
| Bedrock model access 未核准 | `RUN_MODE=fixture` 整條線重播，UI 明示「離線重播」 | 賽前即已成立，不需臨場決定 |
| Fargate 起不來且看不出原因 | 先查 CloudWatch log group；最常見是映像檔架構不符（見 §2） | 15 分鐘查不出就切本機 demo |

---

## 4.5 容器映像檔帶前端（2026-09-12 已修，原本的落差已解除）

原本 `backend/Dockerfile` 只 `COPY backend/`，容器的 `GET /` 回 **503**，
評審打開部署網址只看得到 API 文件。`16f46cd` 起加了
`COPY prototype/dist/ /app/prototype/dist/`，建置 context 仍是專案根目錄。

雲上實測 `GET /` 回 **200**、184,068 bytes（前端單檔全內嵌，`84cb4ed` 那版），`frontend_served: true`。

> `/api/health` 的 `frontend_dist` 檢查仍標 `blocking:false`——API-only 部署是合法狀態，
> 不該讓健康檢查因此變紅。但 US-1 的驗收會另外看 `GET /` 的狀態碼與大小。

---

## 5. 部署時的紅線檢查

推上去之前跑一次，全綠才推：

```bash
python3 backend/tests/run_all.py
```

它包含三道與部署直接相關的靜態掃描：
- **secret／禁用雲端字樣（`backend/` + `prototype/`）**：不得出現 AWS 金鑰樣式字串，
  也不得出現其他雲的 CLI／SDK／憑證變數。整合後 `prototype/` 也納入掃描範圍。
- **`prototype/dist` 可由 `build.py` 完全重現**：dist 是建置產物，手改它會讓
  「跑起來的東西」與原始碼各說各話。這條取代了 Phase 0 的「`prototype/` 未被變更」
  （那條的前提是後端工作不准碰前端，前後端整合後已不適用）。
- **測試路徑零外部依賴**：核心流程不得偷偷長出第三方相依（`backend/api/` 為具名例外）。

另外用眼睛確認兩件事：
- task definition 的 `environment` 裡沒有任何看起來像密碼或金鑰的值。
- S3 bucket（若已建）的 Block Public Access 是全開的。
---

## 6. 交件前檢查清單（2026-09-13，有時限、會被忘記的動作）

### 6.1 ALB IP 白名單收窄

| 欄位 | 內容 |
|---|---|
| 觸發時機 | **2026-09-13 交件前**（在此之前一律維持全開，Ci 2026-09-12 拍板） |
| 那四組 CIDR | **交件前向賽方確認並填入**——來源是賽方 2026-09-12 現場投影片。此處刻意不寫死，避免抄到過期的值 |
| 前置 0 | **回 Workshop Studio dashboard 重取 AWS 憑證**（臨時憑證，`~/.aws/credentials` 沒有 expiry 欄位查不出剩多久）。沒做這步，`deploy.sh:43` 的 `aws sts get-caller-identity` 會第一個擋下你，而錯誤訊息跟 ALB 毫無關係 |
| 前置 0b | **確認 `.env` 裡沒有 `ALB_ALLOWED_CIDRS`**（`grep -c '^[[:space:]]*ALB_ALLOWED_CIDRS' .env` 要是 0）。有的話它會覆蓋命令列，**而且下面的回滾會失效**。理由見 §3.6 |
| 指令 | `cd infra/cdk && ALB_ALLOWED_CIDRS=<四組CIDR> ./deploy.sh deploy` |
| 驗證 | 兩個都查：①§3.6 的 `describe-stacks`，`AlbIngress` 要從 `0.0.0.0/0` 變成那四組，逐條核對沒有少打；②§3.6 的 `describe-security-groups` 查 SG 實況——`AlbIngress` 是推導值，會說謊 |
| 收窄後 | **重跑完整 `./verify.sh`**。收窄動的是 SG，最容易壞的就是「自己也連不進去了」 |
| 回滾 | **不帶** `ALB_ALLOWED_CIDRS` 重跑一次 `./deploy.sh deploy`，`AlbIngress` 會回到 `0.0.0.0/0`（前提是前置 0b 成立） |

> ⚠️ **收窄前必須先確認評審在哪裡看。** 那四組是**會場出口 IP**，收窄之後**從會場以外連進來的人會被擋掉**，
> 包含交件後才自己點開網址的評審。這題不能用推論決定——問到答案再收窄，問不到就不要收。
