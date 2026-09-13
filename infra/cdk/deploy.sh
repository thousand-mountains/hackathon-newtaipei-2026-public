#!/usr/bin/env bash
# 部署後端到大會 AWS 帳號（us-west-2）。
#
#   ./deploy.sh bootstrap   # 只需跑一次：建 CDK 的資產 bucket 與 role
#   ./deploy.sh diff        # 看會動到什麼
#   ./deploy.sh deploy      # 實際部署
#   ./deploy.sh destroy     # 收攤
#
# 從哪個目錄呼叫都可以（腳本會自己 cd 到 infra/cdk，`cdk.json` 在那裡）。
# `deploy` 與 `check` 會**自動建前端**（frontend/dist 不進 git，見 build_frontend）。
# 已經在別處建好可以 HACK_SKIP_FRONTEND_BUILD=1 跳過。
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

# **從哪裡呼叫都要能跑。** `cdk.json`（裡面有 `--app`）在 `infra/cdk/`，而 `npx cdk`
# 是從**當下工作目錄**找它的——所以從 repo 根目錄跑會得到
# `--app is required either in command-line, in cdk.json or in ~/.cdk.json`，
# 那句話完全看不出根因是「你站錯目錄」。這跟本檔修過的其他幾處是同一個家族：
# 失敗訊息指不到真正的原因，人會往錯的方向查。
#
# **刻意放在讀完 .env 之後**：`HACK_ENV_FILE` 可能是相對路徑，那要相對於
# 使用者原本的工作目錄解析，先 cd 會把它解到別的地方去。
cd "$here"

# 一律鎖在大會帳號與 us-west-2（賽方只允許 us-east-1／us-west-2）
export AWS_PROFILE="${AWS_PROFILE:-hack-ntpc}"
export AWS_REGION=us-west-2
export AWS_DEFAULT_REGION=us-west-2
export AWS_PAGER=""

account="$(aws sts get-caller-identity --query Account --output text)"
export CDK_DEFAULT_ACCOUNT="$account"
export CDK_DEFAULT_REGION=us-west-2
echo "帳號：${account}／region：us-west-2／profile：${AWS_PROFILE}"

# ── 前端建置 ──────────────────────────────────────────────────────
# `backend/Dockerfile` 有 `COPY frontend/dist/`，但 `frontend/.gitignore` 排掉 dist，
# 而 **git worktree 不共用未追蹤檔**——所以新 clone／新 worktree 第一次部署一定撞。
# `check_context.sh` 確實會攔下來（具名說「frontend/dist 不在建置 context 裡」，
# 那個守衛是對的、別動它），但攔下來之後人還要自己去翻 DEPLOY.md 才知道要跑什麼。
# 這裡把那一步接進來。
#
# **用 pnpm 不用 npm**：`frontend/` 只有 `pnpm-lock.yaml`，**沒有 package-lock.json**，
# 所以 `npm ci` 會直接報錯（它要求 lockfile 存在）。走 `npx --yes pnpm@9` 則只需要
# node／npx——而那本來就是跑 `npx cdk` 的前提，不新增任何一項機器需求。
# 版本鎖 9：lockfile 是 pnpm 9 產的，換 major 會重算依賴樹。
frontend_dir="$repo_root/frontend"
dist_dir="$frontend_dir/dist"

build_frontend() {
  if [[ "${HACK_SKIP_FRONTEND_BUILD:-}" == "1" ]]; then
    # 明確的退出口：CI 先建好 dist 當 artifact 丟進來的情形。
    # **仍然要檢查 dist 在不在**——跳過建置不等於可以沒有產物。
    if [[ ! -f "$dist_dir/index.html" ]]; then
      echo "HACK_SKIP_FRONTEND_BUILD=1 但 $dist_dir/index.html 不存在。" >&2
      echo "要嘛先把 dist 準備好，要嘛拿掉這個環境變數讓腳本自己建。" >&2
      exit 1
    fi
    echo "略過前端建置（HACK_SKIP_FRONTEND_BUILD=1），沿用既有的 $dist_dir"
    return
  fi

  if ! command -v npx >/dev/null 2>&1; then
    {
      echo "找不到 npx（node）。部署需要它：cdk 本身就是 npx 跑的，前端也用"
      echo "npx --yes pnpm@9 建置。請先裝 Node.js（建議 20 以上）再重跑。"
      echo
      echo "若你已經在別處建好 frontend/dist，可以：HACK_SKIP_FRONTEND_BUILD=1 $0 $*"
    } >&2
    exit 1
  fi

  # **一定要設 VITE_API_MODE=real。** `frontend/src/api/config.js:12` 的預設值是
  # `mock`，而 `frontend/` 底下只有 `.env.example` 沒有 `.env`——所以「什麼都不設」
  # 建出來的是**整包在前端自己演的 mock**：案件清單、解析卷證、搜尋結果全是
  # `src/api/mock.js` 生的，一次後端都不打。
  #
  # 這個失敗形狀特別壞：畫面完全正常、有進度條、有回話、`/api/health` 也綠
  # （那支是 curl 打的，跟瀏覽器走的是兩條路），**只有「後端 log 一直沒有流量」
  # 這一個症狀**，而沒人會盯著那個。2026-09-13 實際部署後才發現，雲上跑了
  # 好幾個小時的是一份從來沒接上後端的前端。
  #
  # **刻意不用 `frontend/.env.production`**：root `.gitignore:3` 的 `.env.*` 會把它
  # 擋在 git 外，於是新 clone／新 worktree 又沒有它——這正是本檔前面兩段註解
  # 已經吃過兩次虧的同一個坑（未追蹤檔不跨 worktree）。寫在這裡才跟著 repo 走。
  #
  # 留一個覆寫口給「想在本機用 mock 建一版給設計看」的情形，但預設是 real。
  export VITE_API_MODE="${VITE_API_MODE:-real}"
  echo "建置前端：${frontend_dir}（VITE_API_MODE=${VITE_API_MODE}）"
  # **每次都重建**，不用「dist 已存在就跳過」。理由是這裡的失敗形狀特別壞：
  # 跳過的話，改了前端再部署會**默默推上舊的 dist**，畫面看起來正常、只是不是你改的那版，
  # 而且 `check_context.sh` 一樣全綠（它只看檔案在不在，不看新不新）。
  # Vite 這個專案建置只要幾秒，省不到什麼；`install` 才慢，那個有 lockfile 可以快取。
  (
    cd "$frontend_dir"
    npx --yes pnpm@9 install --frozen-lockfile
    npx --yes pnpm@9 run build
  )
  [[ -f "$dist_dir/index.html" ]] || {
    echo "前端建置跑完了，但 $dist_dir/index.html 不存在——建置沒有真的產出東西。" >&2
    exit 1
  }
  echo "前端建置完成：$(find "$dist_dir" -type f | wc -l | tr -d ' ') 個檔"
}

# ── CDK 相依 ──────────────────────────────────────────────────────
# `infra/cdk/node_modules/` 進 .gitignore，所以新 clone／新 worktree 一樣沒有。
# 少了它 `npx cdk` 會去抓一個**裸的** aws-cdk，然後 `cdk.json` 的
# `npx ts-node --prefer-ts-exts bin/app.ts` 會拿到一個沒有 typescript 的 ts-node，
# 炸在 `TypeError: Cannot read properties of undefined (reading 'fileExists')`
# ——那個錯誤訊息完全看不出根因是「相依沒裝」。
#
# **這裡是 `npm ci` 而前端是 pnpm，不是筆誤**：`infra/cdk/` 有 package-lock.json、
# `frontend/` 只有 pnpm-lock.yaml。兩邊各用各的 lockfile 對應的工具，
# 弄反的話兩邊都會報「找不到 lockfile」。
ensure_cdk_deps() {
  [[ -x "$here/node_modules/.bin/cdk" ]] && return
  if ! command -v npm >/dev/null 2>&1; then
    echo "找不到 npm（node）。部署需要它安裝 infra/cdk 的相依。請先裝 Node.js。" >&2
    exit 1
  fi
  echo "安裝 CDK 相依：$here"
  ( cd "$here" && npm ci )
}

# ── 同一時間只准一個 cdk 動作 ──────────────────────────────────────
#
# **失敗形狀是「印成功、事情沒發生」**（2026-09-13 實際踩到）：第一個 `cdk deploy`
# 還在跑時又發一個，第二個只印 `Other CLIs (PID=…) are currently reading from cdk.out`
# 就結束、**回 exit 0**，於是回報成「正在部署」。
#
# 兩道擋：
#   1. **本機鎖**（`mkdir` 是原子動作，macOS 不用裝 flock）：擋同一棵工作樹搶 `cdk.out`。
#      不是給每次執行獨立的 `--output` 目錄——`check_context.sh` 寫死讀 `$here/cdk.out`，
#      而且 cdk.out 分開也擋不住下一條。
#   2. **stack 狀態**（deploy／destroy）：兩棵工作樹各有 cdk.out，本機鎖管不到，
#      但它們更新的是**同一個 stack**，CloudFormation 只准一個更新。在我們這邊先擋，
#      錯誤訊息才講得出「誰在跑」。
#
# 鎖用 trap 清；持有者被 kill -9 留下的殘鎖，看 PID 已經不在就接手。
lock_dir="$here/.deploy.lock"

release_lock() { rm -rf "$lock_dir"; }

acquire_lock() {
  if mkdir "$lock_dir" 2>/dev/null; then
    printf 'pid=%s\nstarted=%s\ncmd=%s\n' "$$" "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" > "$lock_dir/owner"
    trap release_lock EXIT
    trap 'exit 130' INT TERM
    return
  fi
  local owner_pid
  owner_pid="$(sed -n 's/^pid=//p' "$lock_dir/owner" 2>/dev/null || true)"
  # **只有「PID 寫了、而且那個 process 已經不在」才算殘鎖。** PID 讀不到不等於殘鎖：
  # 對方可能剛 mkdir 完、還沒寫 owner——把那種當殘鎖清掉，等於搶走一把正在用的鎖。
  if [[ -z "$owner_pid" ]] || kill -0 "$owner_pid" 2>/dev/null || [[ -n "${_lock_retried:-}" ]]; then
    {
      echo "✗ 停：這棵工作樹已經有一個 cdk 動作在跑，同時跑第二個會搶 cdk.out，而且可能回成功卻什麼都沒做。"
      sed 's/^/    /' "$lock_dir/owner" 2>/dev/null || echo "    （持有者資訊還沒寫入）"
      echo "  等它結束再跑。確定它已經不在了才手動清：rm -rf \"$lock_dir\""
    } >&2
    exit 1
  fi
  echo "⚠️  發現殘留的鎖（持有者 PID ${owner_pid} 已不在），接手。" >&2
  rm -rf "$lock_dir"
  _lock_retried=1
  acquire_lock "$@"
}

ensure_stack_idle() {
  local status
  status="$(aws cloudformation describe-stacks --stack-name hackntpc-appeal-backend \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || true)"
  case "$status" in
    *_IN_PROGRESS)
      {
        echo "✗ 停：stack 目前是 ${status}——有別的部署（可能在另一棵工作樹或另一台機器）正在跑。"
        echo "  CloudFormation 只准一個更新；現在發出去會有一邊失敗。等它結束再跑："
        echo "  aws cloudformation describe-stacks --stack-name hackntpc-appeal-backend --query 'Stacks[0].StackStatus'"
      } >&2
      exit 1
      ;;
  esac
}

cmd="${1:-deploy}"
shift || true

case "$cmd" in
  deploy|destroy|check|synth|diff|ls) acquire_lock "$cmd" "$@" ;;
esac
case "$cmd" in
  deploy|destroy) ensure_stack_idle ;;
esac

case "$cmd" in
  bootstrap)
    ensure_cdk_deps
    exec npx cdk bootstrap "aws://$account/us-west-2" \
      --qualifier hackntpc \
      --toolkit-stack-name HackNtpcCDKToolkit "$@"
    ;;
  deploy)
    # 白名單守衛放最前面：它只打一次 describe-stacks，擋下來的話不必白建前端與映像檔。
    "$here/check_ingress.sh"
    # **順序有意義**：前端要在 `cdk synth` 之前建好。synth 會把建置 context staging
    # 到 cdk.out，那一刻 dist 不在，之後再建也來不及——check_context.sh 會照實報錯，
    # 但那時已經白跑一次 synth。
    build_frontend "$@"
    ensure_cdk_deps
    # 部署前先確認建置 context 與 Dockerfile 對得起來。
    # 這個不一致不會讓 docker build 失敗，只會讓映像檔默默少東西（見 check_context.sh），
    # 所以必須在這裡擋，不能靠人記得。
    npx cdk synth >/dev/null
    "$here/check_context.sh"
    echo
    # 不用 exec：exec 取代這個 shell，trap 就不會跑、鎖清不掉。
    npx cdk deploy --toolkit-stack-name HackNtpcCDKToolkit "$@"
    ;;
  destroy)
    ensure_cdk_deps
    npx cdk destroy --toolkit-stack-name HackNtpcCDKToolkit "$@"
    ;;
  check)
    # `check` 就是「把 deploy 的前置檢查跑一遍」，所以也要建前端——否則它會報
    # 「dist 不在 context 裡」，而那是 check 自己沒建造成的，不是真的設定錯誤。
    build_frontend "$@"
    ensure_cdk_deps
    npx cdk synth >/dev/null
    "$here/check_context.sh"
    ;;
  synth|diff|ls)
    # 這幾個子命令不吃 --toolkit-stack-name
    ensure_cdk_deps
    npx cdk "$cmd" "$@"
    ;;
  *)
    echo "未知指令：${cmd}（可用：bootstrap／synth／diff／check／deploy／destroy）" >&2
    exit 1
    ;;
esac
