import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecs_patterns from 'aws-cdk-lib/aws-ecs-patterns';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export interface AppealBackendStackProps extends cdk.StackProps {
  /** N2 抽取用模型（inference profile id 或 foundation model id） */
  readonly modelIdExtract: string;
  /** N5 主筆用模型 */
  readonly modelIdDraft: string;
  /** Bedrock Managed Knowledge Base id */
  readonly knowledgeBaseId: string;
  /** 檢索分數下限（字串原樣傳給後端，由後端解析） */
  readonly kbMinScore: string;
  /**
   * ALB 對外開放的來源 CIDR 清單。
   *
   * 留空（預設）＝ `0.0.0.0/0`，任何人都能點開部署網址。
   * 給了清單就**只有**這些來源進得來——賽方 2026-09-12 現場投影片要求
   * 「AWS 部署時，對外開放連線請 allow 以下四組 IP」時用這個。
   *
   * ⚠️ 收窄有代價：那四組是**會場出口 IP**（實查 2026-09-12：本機對外 IP
   * 就是其中之一），所以收窄後**從會場以外連進來的人會被擋掉**，
   * 包含交件後才自己點開網址的評審。收窄前要先確認評審在會場內還是會場外看，
   * 這題不能用推論決定。
   */
  readonly albAllowedCidrs?: readonly string[];
}

/** 這個帳號是共用的（已有四個 bucket、三個 KB），所有資源都掛這個前綴才認得出來。 */
const PREFIX = 'hackntpc-appeal';

/**
 * 把 `.env` 給的模型 id 轉成 IAM 要的 resource ARN 清單。
 *
 * 跨區 inference profile（`us.` / `eu.` / `apac.` / `global.` 前綴）**需要兩種 ARN**：
 *   1. profile 本身（呼叫端指定的那個）
 *   2. profile 背後會路由到的 foundation model，且不能綁單一 region
 * 少給第二種，InvokeModel 會被 AccessDenied——這是第一次配 policy 最常漏的一條。
 */
function invokeArnsFor(modelId: string, region: string, account: string): string[] {
  if (modelId.startsWith('arn:')) return [modelId];

  const crossRegionPrefixes = ['us.', 'eu.', 'apac.', 'global.'];
  const prefix = crossRegionPrefixes.find((p) => modelId.startsWith(p));

  if (prefix) {
    const baseModelId = modelId.slice(prefix.length);
    return [
      `arn:aws:bedrock:${region}:${account}:inference-profile/${modelId}`,
      `arn:aws:bedrock:*::foundation-model/${baseModelId}`,
    ];
  }
  return [`arn:aws:bedrock:${region}::foundation-model/${modelId}`];
}

export class AppealBackendStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AppealBackendStackProps) {
    super(scope, id, props);

    const region = this.region;
    const account = this.account;

    // ── VPC ───────────────────────────────────────────────────────────────
    // 用帳號的預設 VPC（四個公有子網），不自建 VPC：
    //   自建就得配 NAT gateway 才能讓 task 拉 ECR 映像檔——多花錢、多花時間，
    //   而環境本來就是短命的（收回時間見 DEPLOY.md §3.5：開到黑客松結束，
    //   Ci 2026-09-12 口頭確認，非賽方書面）。
    //   task 放公有子網 + 指派 public IP 即可拉映像檔，
    //   對外仍然只有 ALB 進得來（見下面 SG）。
    const vpc = ec2.Vpc.fromLookup(this, 'DefaultVpc', { isDefault: true });

    // ── 記錄 ──────────────────────────────────────────────────────────────
    const logGroup = new logs.LogGroup(this, 'BackendLogs', {
      logGroupName: '/ecs/hack-appeal-backend',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ── task role：容器唯一的憑證來源 ─────────────────────────────────────
    // 映像檔與 task definition 都不帶任何 AWS 金鑰（proposal US-2）。
    const taskRole = new iam.Role(this, 'TaskRole', {
      roleName: `${PREFIX}-task-role`,
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      // IAM role 的 description 只吃 ASCII/Latin-1（CloudFormation 會擋中文），
      // 所以這行刻意用英文；中文說明見上方註解。
      description: 'Appeal backend task role: Bedrock invoke + one KB retrieve only',
    });

    const invokeResources = [
      ...invokeArnsFor(props.modelIdExtract, region, account),
      ...invokeArnsFor(props.modelIdDraft, region, account),
    ];

    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InvokeNamedModelsOnly',
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: [...new Set(invokeResources)],
      }),
    );

    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'RetrieveFromOneKnowledgeBaseOnly',
        actions: ['bedrock:Retrieve'],
        resources: [
          `arn:aws:bedrock:${region}:${account}:knowledge-base/${props.knowledgeBaseId}`,
        ],
      }),
    );

    // ── 叢集 ──────────────────────────────────────────────────────────────
    const cluster = new ecs.Cluster(this, 'Cluster', {
      clusterName: `${PREFIX}-cluster`,
      vpc,
      containerInsightsV2: ecs.ContainerInsights.DISABLED,
    });

    // ── 映像檔 ────────────────────────────────────────────────────────────
    // 建置 context 是 repo 根目錄（Dockerfile 要抓 backend/ 與 prototype/dist/）。
    // platform 一定要釘 linux/amd64：在 Apple Silicon 上少了這行，
    // Fargate 會因為 exec format error 起不來——第一次部署最常見的失敗原因。
    const albAllowedCidrs = props.albAllowedCidrs ?? [];
    const hasCidrAllowlist = albAllowedCidrs.length > 0;

    const repoRoot = path.resolve(__dirname, '..', '..', '..');
    const image = ecs.ContainerImage.fromAsset(repoRoot, {
      file: 'backend/Dockerfile',
      platform: ecr_assets.Platform.LINUX_AMD64,
      exclude: [
        '.git',
        '.env',
        // cdk.out 在 repo 裡面，不排掉的話 staging 會把自己複製進自己（ENAMETOOLONG）。
        // CDK 的 exclude 是相對於 asset 根目錄比對，所以 'cdk.out' 一條不夠。
        '**/cdk.out',
        '**/node_modules',
        // 整個 infra/ 排掉，Dockerfile 從來沒 COPY 它。
        // 不排的話，改一行 CDK 程式就會讓映像檔的 asset hash 變掉，
        // 於是「只改 ALB 設定」也要重建、重推、換 task definition——純浪費。
        // 附帶好處：cdk.context.json（存帳號與 subnet id）不會被塞進建置 context。
        'infra',
        // data/ 底下只放 manifest.json 進 context（Dockerfile 會 COPY 它，
        // 雲上的語料具名揭露靠它；manifest 只記路徑、來源與 sha256，不含內容）。
        // 其餘一律排除：賽方資料集若被放進 data/，不得進建置 context（CONSTITUTION §5）。
        'data/*',
        '!data/manifest.json',
        'docs',
        'knowledge',
        'design',
        // backend/output/ 是本機跑 run 的產物（runs/*.json），**不是程式碼**。
        // 2026-09-12 實測：主工作樹這個目錄已長到 892 MB／16,260 檔，兩者都沒有時
        // `cdk synth` 的 staging 是 **905 MB**；擋掉之後 4.8 MB。之前部署沒炸，
        // 是因為部署走的是另一棵工作樹（那裡只有 7.5 MB），不是因為這裡擋住了。
        //
        // **實際在擋的是根目錄的 `.dockerignore`**（CDK 的 fromAsset 會讀它，實測
        // 只留 .dockerignore 就已經是 4.8 MB／同一個 hash）。這一條是第二層保險：
        // `.dockerignore` 被刪或改名時仍擋得住。兩邊都留著，改一邊記得看另一邊。
        'backend/output',
        'prototype/static',
        'prototype/node_modules',
      ],
    });

    // ── 服務 ＋ ALB ───────────────────────────────────────────────────────
    const service = new ecs_patterns.ApplicationLoadBalancedFargateService(
      this,
      'Service',
      {
        cluster,
        serviceName: `${PREFIX}-service`,
        loadBalancerName: `${PREFIX}-alb`,
        cpu: 1024,
        memoryLimitMiB: 2048,
        desiredCount: 1,
        minHealthyPercent: 100,
        publicLoadBalancer: true,
        // 有來源清單時不讓 pattern 自己加 0.0.0.0/0，改由下面逐條加。
        openListener: !hasCidrAllowlist,
        // task 在公有子網並指派 public IP：沒有 NAT 也拉得到 ECR 映像檔。
        assignPublicIp: true,
        taskSubnets: { subnetType: ec2.SubnetType.PUBLIC },
        runtimePlatform: {
          cpuArchitecture: ecs.CpuArchitecture.X86_64,
          operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
        },
        healthCheckGracePeriod: cdk.Duration.seconds(120),
        // 起不來就讓它失敗收場，不要卡三小時；rollback 關掉才讀得到現場 log。
        circuitBreaker: { rollback: false },
        taskImageOptions: {
          image,
          containerName: 'backend',
          containerPort: 8080,
          taskRole,
          logDriver: ecs.LogDrivers.awsLogs({
            streamPrefix: 'backend',
            logGroup,
          }),
          // task definition 是「跑哪個檔位」的唯一事實來源（proposal US-4）。
          // 這裡刻意把每一項都明寫出來，不依賴 settings.py 的預設值。
          environment: {
            RUN_MODE: 'bedrock',
            MODEL_PROVIDER: 'bedrock',
            RETRIEVER: 'kb',
            AWS_REGION: region,
            BEDROCK_MODEL_ID_EXTRACT: props.modelIdExtract,
            BEDROCK_MODEL_ID_DRAFT: props.modelIdDraft,
            BEDROCK_KB_ID: props.knowledgeBaseId,
            KB_MIN_SCORE: props.kbMinScore,
            PORT: '8080',
          },
        },
      },
    );

    // 打 /api/health 而不是 /：後者是前端單檔，回 200 不代表後端檔位正確。
    service.targetGroup.configureHealthCheck({
      path: '/api/health',
      healthyHttpCodes: '200',
      interval: cdk.Duration.seconds(30),
      timeout: cdk.Duration.seconds(10),
      healthyThresholdCount: 2,
      unhealthyThresholdCount: 3,
    });

    if (hasCidrAllowlist) {
      for (const cidr of albAllowedCidrs) {
        service.loadBalancer.connections.allowFrom(
          ec2.Peer.ipv4(cidr),
          ec2.Port.tcp(80),
          // SG 規則描述跟 IAM role description 一樣只吃 ASCII，CloudFormation 會擋中文
          `organiser allow list ${cidr}`,
        );
      }
    }

    new cdk.CfnOutput(this, 'AlbIngress', {
      value: hasCidrAllowlist ? albAllowedCidrs.join(',') : '0.0.0.0/0',
      description: 'Who can reach the ALB on port 80',
    });

    // 這是個短命的競賽環境，不必等連線排空。
    service.targetGroup.setAttribute('deregistration_delay.timeout_seconds', '15');

    // ALB 預設 idle timeout 是 60 秒，對這個後端**不夠**。
    //
    // 實測（2026-09-12，本 stack 第一次部署）：`POST /api/cases/{id}/submit` 回 ALB 504，
    // 不是 409。原因不是守門破了，是這支端點刻意**同步**重跑六節點再判斷
    // （backend/api/app.py:441 `run_case()`，不信前端送來的 submit_allowed），
    // 而 bedrock 檔位端到端是 51–78 秒 —— 超過 60 秒就被 ALB 切斷。
    //
    // 「202 ＋ 輪詢讓每個呼叫都是秒級」只對 `/runs` 成立，**對 `/submit` 不成立**；
    // 本機驗收沒有 ALB，所以這條在本機是驗不出來的。
    //
    // 這裡放寬到 900 秒（2026-09-12 Ci 拍板；觀測值 75 秒的十二倍）。
    // 為什麼不是剛好夠用的 300：現場 Bedrock 被節流重試時那 75 秒會往上跑，
    // 而評審日只有一次機會——寧可留大的餘裕。ALB 上限是 4000 秒。
    // 代價是「卡住的請求」會佔著連線更久，但 desiredCount=1 的 demo 量級吃得下。
    // 要讓 `/submit` 也變成秒級呼叫，得改成 202 ＋ 輪詢——那是後端的事，
    // 不是部署層該偷偷替它決定的。
    service.loadBalancer.setAttribute('idle_timeout.timeout_seconds', '900');

    new cdk.CfnOutput(this, 'ServiceUrl', {
      value: `http://${service.loadBalancer.loadBalancerDnsName}`,
      description: 'Public URL for judges',
    });
    new cdk.CfnOutput(this, 'LogGroupName', { value: logGroup.logGroupName });
    new cdk.CfnOutput(this, 'TaskRoleArn', { value: taskRole.roleArn });
  }
}
