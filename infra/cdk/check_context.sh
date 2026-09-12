#!/usr/bin/env bash
# 檢查「Dockerfile 要 COPY 的每個路徑」都真的在 CDK staged 的建置 context 裡。
#
# 為什麼需要這支：CDK 的 `exclude` 同時決定兩件事——
#   (a) 什麼檔案會被放進建置 context（因此 Dockerfile COPY 得到）
#   (b) 映像檔的 asset hash 怎麼算
# 其中 (a) 出錯時**完全不會報錯**：docker build 成功、容器啟動、health 回 200，
# 只是東西默默不見。2026-09-12 一天之內踩到兩次：
#   1. 後端加 `COPY data/manifest.json`，但 exclude 有 `data` → 語料具名揭露靜默消失
#   2. Dockerfile 從 `prototype/dist` 改成 `frontend/dist` → 若 exclude 沒跟著看，
#      映像檔就少了整個前端，`GET /` 回 503 而不是報錯
#
# 這支把那個沉默變成出聲。deploy.sh 會在 deploy 前自動跑它。
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
dockerfile="$repo_root/backend/Dockerfile"
outdir="$here/cdk.out"

[[ -f "$dockerfile" ]] || { echo "找不到 $dockerfile" >&2; exit 1; }
[[ -d "$outdir" ]] || { echo "找不到 $outdir，請先跑 cdk synth" >&2; exit 1; }

# 下面那段 python 只用到 json／sys／pathlib，**3.6 以上都跑得動**，
# 含 macOS 內建的 /usr/bin/python3（3.9）——實測過。
#
# 原本這裡寫死 `/opt/homebrew/bin/python3.14`。那在寫的人機器上永遠是對的，
# 所以**本機永遠驗不出來**；而 `deploy.sh` 是 `set -euo pipefail`，
# 直譯器不存在時 `$(...)` 失敗會**直接中止部署**，錯誤訊息還只是 command not found。
# Claire／Pink 的 homebrew python 不是 3.14、brew 升到 3.15、或任何 Linux 環境都會中。
#
# 注意**不要**把它跟 `backend/tests/run_all.py` 的需求搞混：那支需要 3.10+
# （它用 `sys.stdlib_module_names`，而且自己會擋並說明）。**這支不需要。**
# 兩支腳本對直譯器的要求不一樣，套同一個版本要求只會逼人多裝一個 python。
py="${PYTHON:-}"
if [[ -z "$py" ]]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then py="$candidate"; break; fi
  done
fi
if [[ -z "$py" ]]; then
  {
    echo "找不到 python3。這支腳本用它解析 cdk.out 的 *.assets.json（只用 stdlib 的"
    echo "json／sys／pathlib，3.6+ 都可以，macOS 內建的 /usr/bin/python3 就夠）。"
    echo "請安裝 python3，或指定：PYTHON=/path/to/python3 $0"
  } >&2
  exit 1
fi

# 從 assets.json 找出 docker image asset 被 staging 到哪個目錄
asset_dir="$(
  "$py" - "$outdir" <<'PY'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1])
for f in out.glob('*.assets.json'):
    d = json.loads(f.read_text())
    for _, v in (d.get('dockerImages') or {}).items():
        src = (v.get('source') or {}).get('directory')
        if src:
            print(out / src)
            raise SystemExit
raise SystemExit('找不到 dockerImages asset')
PY
)"

[[ -d "$asset_dir" ]] || { echo "staged context 不存在：$asset_dir" >&2; exit 1; }
echo "建置 context：$asset_dir"

# 取出 Dockerfile 每一條 COPY 的來源路徑。
# 規則：跳過 --from=（那是 multi-stage，來源不是 context）；
# 一行 COPY 的最後一個 token 是目的地，其餘都是來源。
# macOS 內建 bash 是 3.2，沒有 mapfile，所以用 while read 收集。
sources=()
while IFS= read -r line; do
  [[ -n "$line" ]] && sources+=("$line")
done < <(
  grep -iE '^[[:space:]]*COPY[[:space:]]' "$dockerfile" \
    | grep -viE '^[[:space:]]*COPY[[:space:]]+--from=' \
    | sed -E 's/^[[:space:]]*COPY[[:space:]]+//' \
    | sed -E 's/--chown=[^[:space:]]+[[:space:]]+//' \
    | awk '{ for (i = 1; i < NF; i++) print $i }'
)

if [[ ${#sources[@]} -eq 0 ]]; then
  echo "⚠️  Dockerfile 裡沒有解析到任何 COPY 來源，這本身就可疑" >&2
  exit 1
fi

fail=0
for src in "${sources[@]}"; do
  # 去掉可能的引號與結尾斜線
  clean="${src%\"}"; clean="${clean#\"}"; clean="${clean%/}"
  if [[ -e "$asset_dir/$clean" ]]; then
    if [[ -d "$asset_dir/$clean" ]]; then
      n="$(find "$asset_dir/$clean" -type f | wc -l | tr -d ' ')"
      if [[ "$n" == "0" ]]; then
        echo "✗ ${clean} 在 context 裡是空目錄（0 個檔）——exclude 很可能把內容清掉了"
        fail=1
      else
        echo "✓ ${clean}（${n} 個檔）"
      fi
    else
      echo "✓ ${clean}（$(wc -c <"$asset_dir/$clean" | tr -d ' ') bytes）"
    fi
  else
    echo "✗ ${clean} **不在建置 context 裡** —— Dockerfile 會 COPY 失敗，"
    echo "   或（若是目錄）靜默帶進一個空的。檢查 appeal-backend-stack.ts 的 exclude 清單。"
    fail=1
  fi
done

if [[ "$fail" != 0 ]]; then
  echo
  echo "建置 context 與 Dockerfile 不一致，已中止。" >&2
  exit 1
fi
echo "Dockerfile 的每個 COPY 來源都在 context 裡。"
