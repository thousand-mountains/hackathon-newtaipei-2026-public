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
  /**
   * KB 語料所在的 S3 bucket。母庫查的**全文**走 `s3:GetObject` 直接讀原始 `.txt`
   * ——KB retrieve 回的是 chunk，拼片段會拼出一份殘缺卻看起來完整的文件。
   * 缺這一項，`GET /api/laws/{id}` 與 `GET /api/decisions/{id}` 在雲上會 503。
   */
  readonly s3KbBucket: string;
  /** 檢索分數下限（字串原樣傳給後端，由後端解析） */
  readonly kbMinScore: string;
  /**
   * 重排（cross-encoder）模型 arn。
   *
   * **這一項漏掉的後果是安靜的，所以 `bin/app.ts` 把它列為必填。**
   * 沒有重排時檢索只有 embedding 的向量距離把關，而實測那個分數擋不掉語意無關的
   * 命中（真實命中 median 0.21、閒聊句 0.73——雜訊高過訊號）。更糟的是
   * `KB_MIN_SCORE` 放寬到 0.15 的正當性**建立在「後面有重排接手」上**，
   * 兩者一起漏就是「門檻寬 ＋ 沒有人擋」。
   */
  readonly rerankModelId: string;
  /** 重排後的相關性門檻（字串原樣傳給後端） */
  readonly rerankMinScore: string;
  /**
   * 相似案通道的 `前綴/:席次`，以及 N5 可引用來源的前綴白名單。
   *
   * **這兩項跟著 corpus 綁定，換 KB 一定要一起換**：舊 corpus 的目錄叫
   * `新北訴願決定書_全量/`，第三方那份叫 `新北訴願決定書_環保局全量/`——
   * 只差三個字，但比不中時整條通道**靜默回 0 筆、不報錯**。
   * 既然 `BEDROCK_KB_ID` 是必填，這兩項就沒有理由是選填。
   */
  readonly similarCaseQuota: string;
  readonly refPrefixes: string;
  /** 引用通道伺服器端先篩的 doc_kind。**留空＝不篩**，所以這一項可以不給。 */
  readonly refDocKinds?: string;
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

    // 重排模型要單獨開，**它不在 InvokeNamedModelsOnly 裡**（2026-09-12 部署前攔下）。
    // `Rerank` 是 bedrock-agent-runtime 的另一支 API，動作名也不同（`bedrock:Rerank`）。
    // 漏了它的失敗方式很難查：`/api/health` 照樣報 `rerank.enabled=true`（那只看環境變數），
    // 但每次重排都 AccessDenied → 例外往上拋到 N4 → **相似案通道整條變 unavailable**。
    // 本機驗不到這一條：本機走開發者自己的憑證，不是 task role。
    // **`bedrock:Rerank` 不支援資源層級限縮，必須給 `*`**（AWS 官方 rerank-prereq 明載；
    // 2026-09-12 線上實測過一輪才確定）。把它 scoped 到 foundation-model ARN 的話，
    // statement 對這個 action 完全不匹配，執行時的錯誤是
    // 「no identity-based policy allows the bedrock:Rerank **action**」
    // ——它抱怨的是 action 不是 resource，這就是分辨「權限沒開」與「資源寫錯」的線索。
    //
    // 能限縮的是 `bedrock:InvokeModel`，所以拆成兩條：**該窄的仍然窄**。
    // `Rerank` 這個動作本身只能重排呼叫端自己送進去的文件，不讀任何資料來源，
    // 開 `*` 的實際暴露面是「可以用帳號內任何重排模型重排自己的文字」。
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'RerankActionCannotBeResourceScoped',
        actions: ['bedrock:Rerank'],
        resources: ['*'],
      }),
    );

    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InvokeRerankModelOnly',
        actions: ['bedrock:InvokeModel'],
        resources: invokeArnsFor(props.rerankModelId, region, account),
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

    // 母庫查的全文通道：只准讀 KB 語料那個 bucket 的 `kb/` 前綴，且只有 GetObject。
    // **不給 ListBucket**——端點是拿明確的 key 去讀，不需要列目錄；給了就等於
    // 把整個語料的檔名清單開放給任何能打到這支端點的人。
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ReadCorpusObjectsOnly',
        actions: ['s3:GetObject'],
        resources: [`arn:aws:s3:::${props.s3KbBucket}/kb/*`],
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
            S3_KB_BUCKET: props.s3KbBucket,
            KB_MIN_SCORE: props.kbMinScore,
            // 檢索品質的四個旋鈕。**它們與 KB_MIN_SCORE 是一組的，不能只帶一半**
            // （2026-09-12 部署前實際攔下來）：只帶 KB_MIN_SCORE=0.15 而不帶重排，
            // 等於把門檻放寬之後拿掉唯一接手的那一關；不帶前綴則是目錄名對不上
            // 第三方 corpus，相似案與引用兩條通道一起靜默回 0 筆。
            BEDROCK_RERANK_MODEL_ID: props.rerankModelId,
            RERANK_MIN_SCORE: props.rerankMinScore,
            SIMILAR_CASE_QUOTA: props.similarCaseQuota,
            REF_PREFIXES: props.refPrefixes,
            // 留空＝不篩，所以這裡用空字串而不是省略：省略會讓「刻意不篩」與
            // 「忘了設」在 task definition 上長得一樣。
            REF_DOC_KINDS: props.refDocKinds ?? '',
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
