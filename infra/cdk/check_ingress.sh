#!/usr/bin/env bash
# 部署前擋下「白名單悄悄變回全開」。deploy.sh 在 deploy 前自動跑它。
#
# 為什麼需要這支：`ALB_ALLOWED_CIDRS` **不在 `.env` 裡**（刻意的，見 DEPLOY.md §3.6），
# 所以「不帶這個變數重新部署」＝把線上的會場 IP 白名單拿掉、回到任何人都連得進來。
# 部署照樣印 ✅，而且沒有任何東西會壞，**失敗看起來就是成功**。
# 2026-09-13 實際發生：白名單收窄後不到一小時，另一個 session 為了修前端重新部署，
# 沒帶變數，`AlbIngress` 從四組 IP 變回 `0.0.0.0/0`，是靠人工交叉詢問才發現的。
#
# 判準：
#   線上 AlbIngress 是具名 CIDR  ＋  這次沒帶 ALB_ALLOWED_CIDRS  → 停
#   真的要打開 → 明確帶 HACK_ALLOW_OPEN_INGRESS=1
#   stack 還不存在／線上本來就全開 → 放行（沒有東西會被悄悄拿掉）
#
# 用部署前擋、不用部署後報警：部署後才報，全開已經生效了。
#
# ⚠️ `AlbIngress` 是從 CDK props 推導的值，不是讀 CloudFront Function 實況
# （見 DEPLOY.md §3.6）。這支擋的是「部署指令帶錯」，不是「有人在 console 手改」。
set -euo pipefail

stack="${HACK_STACK_NAME:-hackntpc-appeal-backend}"

current="$(aws cloudformation describe-stacks --stack-name "$stack" \
  --query "Stacks[0].Outputs[?OutputKey=='AlbIngress'].OutputValue" \
  --output text 2>/dev/null || true)"
# 查不到 stack 時 aws 回非 0、輸出空；有 stack 但沒有這個 output 時 --output text 印 `None`
[[ "$current" == "None" ]] && current=""

wanted="${ALB_ALLOWED_CIDRS:-}"

if [[ -z "$current" || "$current" == "0.0.0.0/0" ]]; then
  exit 0
fi

if [[ -z "$wanted" ]]; then
  if [[ "${HACK_ALLOW_OPEN_INGRESS:-}" == "1" ]]; then
    echo "⚠️  HACK_ALLOW_OPEN_INGRESS=1：這次部署會把白名單（${current}）拿掉，改成任何人都連得進來。"
    exit 0
  fi
  {
    echo "✗ 停：線上白名單是 ${current}，但這次沒帶 ALB_ALLOWED_CIDRS。"
    echo "  照這樣部署，白名單會被拿掉、回到任何人都連得進來，而且部署照樣印成功。"
    echo
    echo "  維持白名單：ALB_ALLOWED_CIDRS=<四組 CIDR，逗號分隔> $0 ..."
    echo "    （四組 IP 在 team-brain「2026-09-12 決賽環境規範」的部署 IP allow list 段）"
    echo "  真的要打開：HACK_ALLOW_OPEN_INGRESS=1 ./deploy.sh deploy"
  } >&2
  exit 1
fi

# 帶了但跟線上不一樣：可能是刻意換，也可能少打一組。不擋，但要看得到。
normalize() { tr ',' '\n' | sed 's/[[:space:]]//g' | grep -v '^$' | sort | paste -sd, -; }
if [[ "$(printf '%s' "$wanted" | normalize)" != "$(printf '%s' "$current" | normalize)" ]]; then
  echo "⚠️  白名單會從 ${current} 改成 ${wanted}，確認沒有少打。"
fi
exit 0
