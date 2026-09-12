#!/usr/bin/env bash
# 部署後端到大會 AWS 帳號（us-west-2）。
#
#   ./deploy.sh bootstrap   # 只需跑一次：建 CDK 的資產 bucket 與 role
#   ./deploy.sh diff        # 看會動到什麼
#   ./deploy.sh deploy      # 實際部署
#   ./deploy.sh destroy     # 收攤
#
# 模型 id 與 KB id 從 .env 讀（.env 不進 git）。
# 找不到 .env 就停——不要讓它靜默退化成離線重播。
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"

env_file="${HACK_ENV_FILE:-}"
if [[ -z "$env_file" ]]; then
  for candidate in \
    "$repo_root/.env" \
    "$(dirname "$repo_root")/hackathon-newtaipei-2026/.env"
  do
    [[ -f "$candidate" ]] && { env_file="$candidate"; break; }
  done
fi

if [[ ! -f "${env_file:-}" ]]; then
  echo "找不到 .env。請設 HACK_ENV_FILE=<路徑> 再跑。" >&2
  exit 1
fi

echo "讀取環境設定：$env_file"
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

# 一律鎖在大會帳號與 us-west-2（賽方只允許 us-east-1／us-west-2）
export AWS_PROFILE="${AWS_PROFILE:-hack-ntpc}"
export AWS_REGION=us-west-2
export AWS_DEFAULT_REGION=us-west-2
export AWS_PAGER=""

account="$(aws sts get-caller-identity --query Account --output text)"
export CDK_DEFAULT_ACCOUNT="$account"
export CDK_DEFAULT_REGION=us-west-2
echo "帳號：${account}／region：us-west-2／profile：${AWS_PROFILE}"

cmd="${1:-deploy}"
shift || true

case "$cmd" in
  bootstrap)
    exec npx cdk bootstrap "aws://$account/us-west-2" \
      --qualifier hackntpc \
      --toolkit-stack-name HackNtpcCDKToolkit "$@"
    ;;
  deploy)
    # 部署前先確認建置 context 與 Dockerfile 對得起來。
    # 這個不一致不會讓 docker build 失敗，只會讓映像檔默默少東西（見 check_context.sh），
    # 所以必須在這裡擋，不能靠人記得。
    npx cdk synth >/dev/null
    "$here/check_context.sh"
    echo
    exec npx cdk deploy --toolkit-stack-name HackNtpcCDKToolkit "$@"
    ;;
  destroy)
    exec npx cdk destroy --toolkit-stack-name HackNtpcCDKToolkit "$@"
    ;;
  check)
    npx cdk synth >/dev/null
    exec "$here/check_context.sh"
    ;;
  synth|diff|ls)
    # 這幾個子命令不吃 --toolkit-stack-name
    exec npx cdk "$cmd" "$@"
    ;;
  *)
    echo "未知指令：${cmd}（可用：bootstrap／synth／diff／check／deploy／destroy）" >&2
    exit 1
    ;;
esac
