# Bedrock AgentCore 研究（2026-09-12 查證）

給黑客松團隊決策：「案件問答聊天」要不要從 FastAPI 直呼 Bedrock Converse 改用 AgentCore。

## 1. GA 狀態與元件清單

以官方 region 支援表（2026-09-12 讀取）為準，下列元件目前**全部視為可用（表上打勾＝已上線，不再分 preview/GA 兩欄）**：
Runtime microVMs、Runtime Instances、Memory、Gateway、Identity、Built-in Tools、Observability、Policy、Evaluations、payments、optimization。

時間線：AgentCore 主線於 2025-10 GA；Policy 於 2026-03-03 GA；Evaluations 於 2026-03-31 GA；harness 於 2026-06-17 GA；Runtime Instances（跑在自己 EC2）於 2026-08-06 GA；payments 於 2026-08-18 GA。也就是說**到 2026-09-12，核心元件（Runtime microVMs、Gateway、Memory、Identity、Observability）都已 GA 超過一季以上**，不是新出的 preview 功能。

來源：
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html（官方 region×feature 支援表，最權威）
- https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available
- https://awsinsider.net/blogs/awsinsider-release-radar/2026/03/amazon-bedrock-agentcore.aspx
- https://aws.amazon.com/about-aws/whats-new/2026/06/amazon-bedrock-agentcore-harness-generally-available/
- https://aws.amazon.com/about-aws/whats-new/2026/08/aws-bedrock-agentcore-runtime-instances-generally-available/

## 2. us-west-2 支援

**us-west-2（US West Oregon）在官方支援表裡，Runtime microVMs、Runtime Instances、Memory、Gateway、Identity、Built-in Tools、Observability、Policy、Evaluations、optimization 全部打勾支援。** 只有 payments 在 us-west-2 是 No（黑客松用不到 payments，不影響）。

來源：https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html

## 3. 最快部署方式

`bedrock-agentcore-starter-toolkit`（`agentcore` CLI）流程：
1. `agentcore create`（或既有專案手動接 SDK）：scaffold 出 agent 實作、model client、MCP 整合、可選 IaC（CDK 或 Terraform）。
2. `agentcore configure`：把設定寫進隱藏檔 `bedrock_agentcore.yaml` / `agentcore/agentcore.json`；不指定 `--execution-role` 時會自動建一個 Runtime Execution Role（含 ECR/CloudWatch/Bedrock 權限）。
3. `agentcore launch`：預設用 **AWS CodeBuild** 把程式碼建成 **ARM64 容器**、推進自動建立的 **ECR** repo，再部署到 AgentCore Runtime（也支援「direct code deployment」zip 模式，跳過容器建置）。

**需要的 IAM 角色兩種**：Runtime Execution Role（跑 agent 用）+ CodeBuild Execution Role（建置容器用）。**已知坑**：自動建立的 CodeBuild role 預設沒帶 ECR 權限，導致第一次 `agentcore launch` 會因 ECR 授權失敗而炸——這是社群回報的已知問題，不是你們環境設錯。

**CDK/CloudFormation 官方支援已存在**：CloudFormation 有正式資源類型 `AWS::BedrockAgentCore::Runtime`、`AWS::BedrockAgentCore::Gateway`、`AWS::BedrockAgentCore::GatewayTarget`、`AWS::BedrockAgentCore::ResourcePolicy` 等；CDK 有官方 L1 construct module `aws_cdk.aws_bedrockagentcore`（Python，v2）。**可以併進既有 CDK stack**，不必依賴 `agentcore` CLI 自己那條部署路徑。但注意 GitHub 上有 open issue（aws/aws-cdk#35852）反映「custom execution role policy for runtime lacks proper permissions」——CDK L1 construct 目前權限模板還不夠成熟，手刻 role policy 要自己補權限，不能完全信任預設。

來源：
- https://aws.github.io/bedrock-agentcore-starter-toolkit/user-guide/create/quickstart.html（頁面本身抓不到內容，只顯示 redirect，未能查證完整逐字步驟）
- https://aws.github.io/bedrock-agentcore-starter-toolkit/user-guide/runtime/permissions.html
- https://github.com/aws/bedrock-agentcore-starter-toolkit/blob/main/documentation/docs/user-guide/runtime/permissions.md
- https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/AWS_BedrockAgentCore.html
- https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_bedrockagentcore.html
- https://github.com/aws/aws-cdk/issues/35852

## 4. 串流回應（Runtime）

> ⚠️ **本節的行為描述來自官方文件的閱讀，但本團隊尚未實測。**
> 設計文件 `docs/spec/2026-09-12-agentcore-runtime-design.md` §10 的 T6 把它列為「實作前必核」——
> 它是整個串流設計的地基，值得自己打一次確認，不要只靠讀文件。

`InvokeAgentRuntime` API 接受最大 100MB 的請求 payload，回傳是**串流回應**：設定 `Content-Type: text/event-stream` 即以 SSE 格式回傳，前端可用瀏覽器原生 `EventSource` 或 Fetch API 逐塊接收（`data: {"event": ...}` 格式）。支援 session 識別碼維持多輪對話上下文。

有實測文章描述「Bedrock AgentCore + API Gateway + Lambda」串流回應的具體架構模式，值得部署前參考。

來源：
- https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore/invoke-agent-runtime.html
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-invoke-agent.html
- https://thecraftman.medium.com/stream-agent-responses-in-real-time-with-amazon-bedrock-agentcore-runtime-bed2fb88b55b
- https://dev.classmethod.jp/en/articles/amazon-bedrock-agentcore-runtime-api-gateway-lambda-streaming-response/

## 5. Gateway 的 OpenAPI→tools 與 auth 需求（關鍵：不強制 OAuth）

**Gateway 的 inbound auth 不是只有 OAuth/Cognito 一種選項**，官方文件（2026-09-12 讀取）列出三大類：
- **JWT**（可用任何相容 OIDC 的 IdP，含 Cognito，也支援 CLI 建 gateway 時用 Cognito 「EZ Auth」自動配置）
- **IAM identity**（用呼叫端的 IAM 憑證做授權，`bedrock-agentcore:InvokeGateway` 權限即可，不需要另外的 OAuth provider）
- **Offloaded**：`AUTHENTICATE_ONLY`（只驗證 SigV4 簽章、不做授權判斷）或 **`NONE`（完全不做 inbound 驗證，請求可以是未認證的）**

也就是說，**黑客松 demo 完全可以用 `authorizerType=NONE` 或 IAM-based 跳過 OAuth/Cognito 這個門檻**，不是強制要接身分系統。官方文件明確警告 `NONE` 不建議用於 production（要有自己的節流/防護），但拿來做 30 小時內的 demo/prototype 是文件裡列出的合法選項，不是繞過限制。

OpenAPI 轉 tools 的機制本身（Gateway 把既有 REST API/OpenAPI spec 轉成 MCP tools）查到存在，但這次沒查到 OpenAPI target 特有的**限制清單**（例如 spec 大小上限、不支援的 OpenAPI 語法），這條建議部署前再查一次官方 `gateway-building-adding-targets` 文件或直接試跑一次。

來源：
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html（完整讀取，最權威，含 IAM/JWT/NONE/AUTHENTICATE_ONLY 全部選項與範例）
- https://aws.amazon.com/blogs/machine-learning/introducing-amazon-bedrock-agentcore-gateway-transforming-enterprise-ai-agent-tool-development/
- https://kane.mx/posts/2026/agentcore-gateway-cognito-mcp-oauth/（社群經驗文，講 Cognito+APIGW façade 做法，屬於進階/production 路線，不是必須）

## 6. 計費模式

- **Runtime**（microVMs，這是黑客松會用到的）：**$0.0895/vCPU-小時 + $0.00945/GB-小時**，按實際 CPU 使用秒數（最低計費單位 1 秒）與峰值記憶體計費；**agent 在等模型回應、沒有背景運算時的 CPU 等待不計費**。
- **Gateway**：$0.005 / 1,000 次工具呼叫（含 list tools、health check 等都算）；Search API 另計 $0.025/1,000 次；工具索引 $0.02/100 個工具/月。
- **Memory**：短期 $0.25/1,000 個新事件；長期儲存（內建）$0.75/1,000 筆/月；檢索 $0.50/1,000 次。
- **Identity**：非 AWS 資源的 token/API key 請求 $0.010/1,000 次；透過 Runtime/Gateway 使用則免費。
- **免費額度**：**元件層級沒有專屬免費額度**，只有新帳號通用的 AWS Free Tier（最高 $200 額度）。Agent Registry 另有獨立免費額度（5,000 筆記錄/月等），但黑客松用不到這個元件。

**結論：以 30 小時黑客松demo等級的流量，Runtime + Gateway 成本應該是幾美元等級，不是決策的阻礙點。**

來源：https://aws.amazon.com/bedrock/agentcore/pricing/（官方頁面，完整讀取）

## 7. 從零到能被 HTTP 呼叫的實際時間與踩坑

**時間**：查不到一個官方或社群給出的精確「X 小時」數字，只能推：`agentcore create` → `configure` → `launch` 三個 CLI 指令是官方設計的最短路徑，社群教學（DEV.to、Medium）多半在單篇文章內完整走完一次部署，暗示是「一兩小時等級」而非「一兩天」，但**這是推論不是查證到的事實，請視為不確定**。

**踩坑清單**（社群 + 官方文件都有提到）。**編號即 `§7-N`**，其他文件就是這樣引用它的：
**§7-1** **CodeBuild 角色預設缺 ECR 權限** → 第一次 `agentcore launch` 常因 ECR 授權失敗而炸（見 §3）。
**§7-2** **容器必須是 ARM64**，`Host 0.0.0.0` `Port 8080` 是硬性合約，不符合會部署失敗。
**§7-3** **Session 生命週期上限 8 小時**（不可超過），閒置預設 15 分鐘會被回收；同一 session 內重複呼叫可以重用已熱機的執行環境、避免重複冷啟動。
**§7-4** **冷啟動延遲存在**，官方與社群都建議用「預熱 ping / 多個 endpoint / 自建 warm pool」緩解——若黑客松 demo 是現場即時展示，第一次呼叫可能明顯慢，建議上台前先手動打一次熱機。
**§7-5** **Gateway Lambda 更新後，正在跑的 Runtime container 可能還吃到舊的 tool 定義**（MCP client 有 cache 行為）——改了 Gateway 後端要記得重啟/重新部署 Runtime 才會生效，這條在 demo 前特別容易踩到。
**§7-6** **region 選錯會直接報 unrecognized region 錯誤**（GitHub issue 有記錄 `us-east-2` 這種常見手誤）——你們用 us-west-2，官方表已確認支援，這條風險低但仍要在 `agentcore configure` 明確指定 region。

來源：
- https://repost.aws/articles/ARCJIn3t7aRC2FxiRTV1SuCA/minimizing-startup-latency-with-amazon-bedrock-agentcore-runtime
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-troubleshooting.html
- https://github.com/aws/bedrock-agentcore-starter-toolkit/issues/411
- https://strandsagents.com/docs/user-guide/deploy/deploy_to_bedrock_agentcore/（Strands 官方部署指南提到 Gateway Lambda cache 坑）
- https://aws.github.io/bedrock-agentcore-starter-toolkit/user-guide/runtime/permissions.html

## 8. 賽制「僅限 AWS 服務提供之基礎模型」與 AgentCore 的關係（僅列事實，不下判斷）

- AgentCore 官方定位是「編排/執行層」——Runtime 是執行環境、Gateway 是工具轉接層、Memory/Identity/Observability 是配套服務，**它本身不是一個基礎模型（foundation model）**，模型仍是透過 Bedrock 呼叫（Claude、Nova 等 AWS Bedrock 目錄內的模型），或依官方說法「bring your own model and integrate it into the runtime」。
- 查到的 AWS AI Agent Global Hackathon 官方頁面把 AgentCore、Bedrock、Q、SageMaker、Agents SDK、Transform、Kiro 並列為「可選用的 AWS AI 服務」，沒有查到條款把「用 AgentCore」跟「基礎模型合規」綁在一起講。
- **這是你們賽制的獨立文件，不是這次查到的通用 AWS 文件**——上面兩點只能證明「AgentCore 官方定位是編排服務、模型走 Bedrock」，無法直接證明「你們新北市法制局賽制認定它合規」。這條請你們對照賽方規則原文自己判斷，我這裡沒查到會影響判斷的反面訊號（沒有查到任何說法指出 AgentCore 本身會被當成「非 AWS 提供的模型」）。

來源：
- https://dev.to/aws-builders/agent-as-a-service-comparing-claude-managed-agents-and-amazon-bedrock-agentcore-22eb
- https://aws-agent-hackathon.devpost.com/
- https://aws.amazon.com/startups/events/aws-ai-agents-hackathon?lang=en-US
