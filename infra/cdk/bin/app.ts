#!/usr/bin/env node
/**
 * 訴願案件審理 AI 輔助 — 大會 AWS 帳號部署入口（CDK v2）
 *
 * 設計前提（皆為賽方硬規範或既有事實，改前先讀 .prospec/changes/deploy-backend-to-aws/proposal.md）：
 *   - region 僅限 us-east-1／us-west-2；本專案全部在 us-west-2
 *   - 服務白名單不含 apprunner，因此走 ECS Fargate + ALB
 *   - 容器不得帶長期金鑰：Bedrock 憑證一律走 task role
 *   - 模型 id／KB id 一律從環境變數讀（來源是未進 git 的 .env），
 *     **不得寫死在這份檔案裡**——這份會進 git。
 *
 * 用法：
 *   set -a && . /path/to/.env && set +a
 *   npx cdk deploy
 */
import * as cdk from 'aws-cdk-lib';
import { AppealBackendStack } from '../lib/appeal-backend-stack';

/** 缺一個就直接停——比讓服務靜默退化成離線重播好。 */
function required(name: string): string {
  const v = process.env[name];
  if (!v || v.trim() === '') {
    throw new Error(
      `缺少環境變數 ${name}。請先 source 專案 .env：\n` +
        `  set -a && . <repo>/.env && set +a`,
    );
  }
  return v.trim();
}

const app = new cdk.App();

new AppealBackendStack(app, 'HackNtpcAppealBackend', {
  stackName: 'hackntpc-appeal-backend',
  description:
    '新北黑客松 2026 訴願審理 AI 輔助 — 後端 ECS Fargate + ALB（千山鳥飛絕）',
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.AWS_REGION ?? 'us-west-2',
  },
  // Bootstrap 用專屬 qualifier，讓 CDK 自建的資源在這個共用帳號裡一眼認得出來
  synthesizer: new cdk.DefaultStackSynthesizer({ qualifier: 'hackntpc' }),
  modelIdExtract: required('BEDROCK_MODEL_ID_EXTRACT'),
  modelIdDraft: required('BEDROCK_MODEL_ID_DRAFT'),
  knowledgeBaseId: required('BEDROCK_KB_ID'),
  s3KbBucket: required('S3_KB_BUCKET'),
  kbMinScore: required('KB_MIN_SCORE'),
  // 這四項與 KB_MIN_SCORE 是一組的（見 stack 的 rerankModelId 說明）：
  // 少帶任何一項，服務都會**安靜地**退化——不報錯、畫面照樣演完，
  // 只是相似案變空或混進語意無關的命中。所以一律必填，缺了就停在這裡。
  rerankModelId: required('BEDROCK_RERANK_MODEL_ID'),
  rerankMinScore: required('RERANK_MIN_SCORE'),
  similarCaseQuota: required('SIMILAR_CASE_QUOTA'),
  refPrefixes: required('REF_PREFIXES'),
  // 唯一的選填：留空＝不篩，是有意義的設定值（沒有側檔的 KB 一篩就全空）。
  refDocKinds: process.env.REF_DOC_KINDS ?? '',
  // 逗號分隔的 CIDR。不設＝維持 0.0.0.0/0（任何人都點得開）。
  // 例：ALB_ALLOWED_CIDRS=1.2.3.4/32,5.6.7.8/32 ./deploy.sh deploy
  albAllowedCidrs: (process.env.ALB_ALLOWED_CIDRS ?? '')
    .split(',')
    .map((c) => c.trim())
    .filter((c) => c.length > 0),
});

cdk.Tags.of(app).add('Project', 'hackntpc-appeal-2026');
cdk.Tags.of(app).add('Team', 'thousand-mountains');

app.synth();
