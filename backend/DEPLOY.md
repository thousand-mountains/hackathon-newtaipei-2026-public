# 部署：AWS ECS Fargate（ap-northeast-1 東京）

> Phase 0 的部署目標是「**把 fixture 檔位的服務推上去，證明整條管線在雲上跑得起來**」。
> 真實 Bedrock 呼叫不在這個階段——沒有憑證與 model access 之前，推上去也只是 fixture 重播。
>
> **本文不含任何真實憑證、帳號 id、ARN 或端點**，只寫變數名稱與取得方式。
> 憑證一律走 AWS Secrets Manager／SSM Parameter Store，不進 git、不進映像檔、不進環境變數明文。

賽制限制：僅限使用 AWS 提供之基礎模型服務。本專案只碰 AWS，不使用任何其他雲的憑證或服務。

---

## 0. 前置：誰要先做什麼

| 事項 | 誰 | 狀態 |
|---|---|---|
| AWS 帳號與 IAM 使用者／角色 | Ci | **待辦**，本機目前無 `~/.aws/` |
| Amazon Bedrock model access 申請（東京 region） | Ci | **待辦**，申請到核准有時間差，建議最早送出 |
| ECR repository | Ci | 待辦 |
| VPC／子網路／安全群組 | Ci | 待辦，可用預設 VPC 起步 |

沒有前兩項時，**服務仍可用 `RUN_MODE=fixture` 部署並 demo**——這是刻意設計的降級路徑。

---

## 1. 環境變數

執行期只認這些變數。**值不寫在這裡**，由 ECS task definition 注入。

### 1.1 Phase 0 就要設的（無憑證需求）

| 變數 | 值域 | 預設 | 說明 |
|---|---|---|---|
| `RUN_MODE` | `fixture` / `local` / `bedrock` | `fixture` | 目前**只有 `fixture` 可用**。設成其他值時，N1／N5 會回 HTTP 501 並說明缺什麼——這是刻意的，不讓服務假裝正常。 |
| `PORT` | 整數 | `8080` | 容器監聽埠，需與 ECS target group 一致。 |

### 1.2 接上 Bedrock 後才需要的（**目前全部不要設**）

| 變數 | 說明 | 怎麼供給 |
|---|---|---|
| `AWS_REGION` | 固定 `ap-northeast-1` | task definition 環境變數即可（非機密） |
| `BEDROCK_MODEL_ID_EXTRACT` | N1 抽取用的基礎模型 id | task definition 環境變數（非機密） |
| `BEDROCK_MODEL_ID_DRAFT` | N5 主筆用的基礎模型 id | 同上 |
| `BEDROCK_KB_ID` | Knowledge Base id（N4 檢索通道 B） | 同上 |
| `KB_DATA_BUCKET` | 資料集所在的 S3 bucket 名稱 | 同上；bucket **必須非公開**（CONSTITUTION §6） |

**憑證怎麼給**：不設 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` 這類變數。
ECS task 用 **task role**（`taskRoleArn`）取得臨時憑證，boto3 會自動從 container credentials
端點拿到。長期金鑰不進任何檔案、不進映像檔、不進 git。

Task role 需要的最小權限（等真的要接 Bedrock 時再開）：
- `bedrock:InvokeModel`、`bedrock:InvokeModelWithResponseStream`（限定到指定 model ARN）
- `bedrock:Retrieve`（限定到指定 Knowledge Base ARN）
- `s3:GetObject`（限定到資料集 bucket 的指定 prefix，唯讀）

---

## 2. 建置與推送映像檔

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
aws ecr get-login-password --region ap-northeast-1 \
  | docker login --username AWS --password-stdin <account>.dkr.ecr.ap-northeast-1.amazonaws.com

docker tag hack-appeal-backend:phase0 <account>.dkr.ecr.ap-northeast-1.amazonaws.com/<repo>:phase0
docker push <account>.dkr.ecr.ap-northeast-1.amazonaws.com/<repo>:phase0
```

映像檔是 `linux/amd64`。**在 Apple Silicon 上建置要加 `--platform linux/amd64`**，
否則 Fargate 會起不來（這是最常見的第一次部署失敗原因）：

```bash
docker build --platform linux/amd64 -f backend/Dockerfile -t hack-appeal-backend:phase0 .
```

---

## 3. ECS 部署步驟

1. **建 ECR repository**（一次性）
   `aws ecr create-repository --repository-name <repo> --region ap-northeast-1`

2. **建 ECS cluster**（Fargate）
   `aws ecs create-cluster --cluster-name <cluster> --region ap-northeast-1`

3. **建 CloudWatch log group**（一次性）
   `aws logs create-log-group --log-group-name /ecs/hack-appeal-backend --region ap-northeast-1`

4. **註冊 task definition**，關鍵欄位：
   - `requiresCompatibilities: ["FARGATE"]`、`networkMode: "awsvpc"`
   - `cpu: "512"`、`memory: "1024"`（fixture 檔位很輕；接上模型後再往上調）
   - `executionRoleArn`：拉映像檔與寫 log 用（`AmazonECSTaskExecutionRolePolicy`）
   - `taskRoleArn`：應用程式自己呼叫 AWS 服務用。**Phase 0 什麼權限都不需要**，
     因為服務不呼叫任何 AWS API；接 Bedrock 時才加 §1.2 的權限。
   - `portMappings: [{containerPort: 8080}]`
   - `environment: [{name: "RUN_MODE", value: "fixture"}, {name: "PORT", value: "8080"}]`
   - `logConfiguration`：`awslogs` driver 指到步驟 3 的 log group
   - `healthCheck`：可沿用映像檔內建的 HEALTHCHECK，或在 task definition 覆寫

5. **建 service**
   - `launchType: FARGATE`、`desiredCount: 1`（demo 用 1 就夠，30 小時內不做 auto scaling）
   - `assignPublicIp: ENABLED`（用預設 VPC 公有子網路起步，最省事）
   - 安全群組：只開 8080 給 ALB，或 demo 期間限來源 IP。**不要對 0.0.0.0/0 全開**。

6. **（選用）ALB**
   - target group：protocol HTTP、port 8080、health check path `/api/health`
   - 沒有 ALB 也能 demo：直接打 task 的公有 IP:8080。ALB 是穩定性加分，不是必要路徑。

7. **驗收（部署完一定要跑這三條）**
   ```bash
   curl -s http://<endpoint>/api/health                      # 期望 run_mode=fixture、ok=true
   curl -s http://<endpoint>/api/cases                       # 期望列出兩個 synthetic 案例
   curl -s -X POST http://<endpoint>/api/cases/synthetic-blocked-01/runs \
     | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['submit_allowed'], len(d['blockers']))"
   # 期望輸出：False 1  ← 守門在雲上也要攔得住，這條沒過就是部署有問題
   ```

---

## 4. 備援路徑（CONSTITUTION §8：備援事先寫好，不臨場發明）

| 失敗情形 | 備援 | 最遲決定時刻 |
|---|---|---|
| ECR 推不上去 / ECS 起不來 | **本機 `uvicorn` 直接 demo**（`uv run --with fastapi --with uvicorn backend/api/app.py`），已實測可跑 | 部署開始後 45 分鐘 |
| Bedrock model access 未核准 | `RUN_MODE=fixture` 整條線重播，UI 明示「離線重播」 | 賽前即已成立，不需臨場決定 |
| Fargate 起不來且看不出原因 | 先查 CloudWatch log group；最常見是映像檔架構不符（見 §2） | 15 分鐘查不出就切本機 demo |

---

## 5. 部署時的紅線檢查

推上去之前跑一次，全綠才推：

```bash
python3 backend/tests/run_all.py
```

它包含三道與部署直接相關的靜態掃描：
- **secret／禁用雲端字樣**：`backend/` 內不得出現 AWS 金鑰樣式字串，也不得出現其他雲的 CLI／SDK／憑證變數。
- **`prototype/` 未被變更**：v0 demo 必須保持可用。
- **測試路徑零外部依賴**：核心流程不得偷偷長出第三方相依。

另外用眼睛確認兩件事：
- task definition 的 `environment` 裡沒有任何看起來像密碼或金鑰的值。
- S3 bucket（若已建）的 Block Public Access 是全開的。
