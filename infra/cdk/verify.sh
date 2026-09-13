#!/usr/bin/env bash
# 部署驗收：proposal `deploy-backend-to-aws` 的 Success Criteria 四條＋聊天 SSE 一條。
#
#   ./verify.sh                 # 自動從 CloudFormation 取部署網址
#   ./verify.sh http://host     # 指定網址
#
# 兩條紅線：
#   - /api/health 四項任一不對 → 是離線重播，不得交件
#   - C 型 submit 沒回 409     → P0 事故，守門在雲上破了
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export AWS_PROFILE="${AWS_PROFILE:-hack-ntpc}"
export AWS_REGION=us-west-2
export AWS_PAGER=""

base="${1:-}"
if [[ -z "$base" ]]; then
  base="$(aws cloudformation describe-stacks \
    --stack-name hackntpc-appeal-backend \
    --query "Stacks[0].Outputs[?OutputKey=='ServiceUrl'].OutputValue" \
    --output text)"
fi
echo "驗收目標：$base"
echo

fail=0
note() { printf '%s\n' "$1"; }
bad() { printf '✗ %s\n' "$1"; fail=1; }
ok() { printf '✓ %s\n' "$1"; }

# ── 1) 首頁是 UI，不是 API 文件、不是 503、也不是舊版映像檔 ──────────────
#
# ⚠️ 這條原本寫「200 且 >100KB」，那是為**單檔全內嵌**的舊 dist 訂的。
#    切到 Vite 之後 index.html 只有 933 bytes（內容在 /assets/*），這條就誤報了。
#
#    而正確的修法**不是把門檻調低**：舊的單檔版 184KB 一樣 >100KB，
#    調低之後「誤部署成舊版映像檔」會照樣通過——那就變成一條因為錯誤理由而通過的檢查。
#
#    改成跟前端形狀無關的判準：
#      (a) GET / 回 200
#      (b) index.html 至少引用一個**站內**資源
#      (c) 每個被引用的資源都真的取得到
#    (b) 順便把「映像檔沒換到」變成可偵測——舊單檔版引用數為 0，會直接紅。
read -r code size < <(curl -s -o /dev/null -w '%{http_code} %{size_download}' "$base/")
home_html="$(curl -s "$base/")"
home_refs="$(printf '%s' "${home_html}" | python3 "${here}/probe_refs.py" refs 2>/dev/null)"
home_ref_count="$(printf '%s' "${home_refs}" | grep -c . || true)"

if [[ "$code" != "200" ]]; then
  bad "US-1 首頁：HTTP ${code} — 期望 200"
elif [[ "${home_ref_count}" -lt 1 ]]; then
  bad "US-1 首頁：HTTP 200／${size} bytes，但**沒有引用任何站內資源**。"
  bad "  這通常代表映像檔裡是舊的單檔全內嵌前端（沒換到版），而不是目前的 Vite 產物。"
else
  missing=0
  while IFS= read -r r; do
    [[ -z "$r" ]] && continue
    rc="$(curl -s -o /dev/null -w '%{http_code}' "${base}${r}")"
    if [[ "$rc" != "200" ]]; then
      bad "US-1 首頁引用的 ${r} 回 ${rc} — 頁面會載入失敗"
      missing=1
    fi
  done <<< "${home_refs}"
  [[ "$missing" == 0 ]] && ok "US-1 首頁：HTTP 200／${size} bytes，引用 ${home_ref_count} 個站內資源且全部取得到"
fi

# ── 2) 檔位四項 ───────────────────────────────────────────────────────
health="$(curl -s "$base/api/health")"
echo "$health" | python3 -m json.tool 2>/dev/null || note "$health"
check_field() {
  local key="$1" want="$2"
  local got
  got="$(printf '%s' "$health" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('<不是 JSON>'); raise SystemExit
v=d.get('$key')
print(json.dumps(v) if not isinstance(v,str) else v)
" 2>/dev/null)"
  if [[ "$got" == "$want" ]]; then ok "US-1 $key = $got"; else bad "US-1 $key = ${got}（期望 ${want}）"; fi
}
check_field run_mode bedrock
check_field fixture_only false
check_field kb_backend kb
# 正常值是 `bedrock_kb`（`backend/retrieval/kb.py` 的 `describe_similar_case_backend` docstring 列了三種）。
# 原本寫 `kb` 是把 RETRIEVER 的設定值當成了健康檢查的回報值，兩者不是同一個詞——這條因此一直誤報紅。
check_field similar_case_backend bedrock_kb

# frontend_dist：映像檔裡的前端是不是「完整的」。
# 比 GET / 回 200 有訊息量——它會逐一檢查 index.html 引用的每個 /assets/… 是否存在，
# 所以抓得到「build context 少了一半前端」這種 GET / 照樣回 200 的情況。
# 這一項在舊版後端可能不存在（回 <缺此欄位>），那是資訊不是失敗。
fd="$(printf '%s' "${health}" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('<不是 JSON>'); raise SystemExit
hit=[c for c in (d.get('checks') or []) if c.get('name')=='frontend_dist']
if not hit: print('<缺此欄位>'); raise SystemExit
c=hit[0]
print(('ok' if c.get('ok') else 'NG') + ' | ' + str(c.get('detail'))[:160])
" 2>/dev/null)"
case "${fd}" in
  ok*)          ok "US-1 frontend_dist：${fd}" ;;
  '<缺此欄位>') note "· US-1 frontend_dist：後端沒有這一項（舊版），略過" ;;
  *)            bad "US-1 frontend_dist：${fd} — 映像檔裡的前端不完整" ;;
esac

# ── 3) 真的打得到 Bedrock ─────────────────────────────────────────────
# bedrock 檔位的形狀：POST /runs → 202 ＋ run_id，之後輪詢 GET /api/runs/{id}
#   還在跑 → 409（刻意不是 200，見 app.py:364 的註解）
#   失敗   → 502 ＋ 失敗節點
#   完成   → 200 ＋ 完整 payload
run_body="$(curl -s -w '\n%{http_code}' -X POST "$base/api/cases/synthetic-ordinary-01/runs")"
run_code="$(printf '%s' "$run_body" | tail -1)"
run_json="$(printf '%s' "$run_body" | sed '$d')"
rid="$(printf '%s' "$run_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null)"

if [[ "$run_code" == "202" && -n "$rid" ]]; then
  ok "US-2 POST /runs：HTTP 202，run_id=$rid"
  provider=""
  for _ in $(seq 1 48); do
    sleep 5
    poll="$(curl -s -o /tmp/verify_poll.json -w '%{http_code}' "$base/api/runs/$rid")"
    case "$poll" in
      409) printf '  還在跑…\n' ;;
      200)
        provider="$(python3 -c "
import json
d=json.load(open('/tmp/verify_poll.json'))
print(((d.get('run_meta') or {}).get('model_ids') or {}).get('provider',''))
" 2>/dev/null)"
        printf '  完成：provider=%s\n' "$provider"
        break ;;
      502)
        bad "US-2 執行失敗（502）"; head -c 500 /tmp/verify_poll.json; echo; break ;;
      *) printf '  非預期狀態：%s\n' "$poll"; break ;;
    esac
  done
  if [[ "$provider" == "bedrock" ]]; then
    ok "US-2 run_meta.model_ids.provider = bedrock"
    python3 -c "
import json
d=json.load(open('/tmp/verify_poll.json'))
m=(d.get('run_meta') or {}).get('model_ids') or {}
print('  model_ids:', json.dumps(m, ensure_ascii=False))
" 2>/dev/null
  elif [[ -n "$provider" ]]; then
    bad "US-2 provider = ${provider}（期望 bedrock）"
  fi
else
  bad "US-2 POST /runs：HTTP $run_code"
  printf '%s\n' "$run_json" | head -c 400
fi

# ── 4) 守門（最關鍵）──────────────────────────────────────────────────
submit_code="$(curl -s -o /tmp/verify_submit.json -w '%{http_code}' \
  -X POST "$base/api/cases/synthetic-blocked-01/submit" \
  -H 'content-type: application/json' -d '{}')"
if [[ "$submit_code" == "409" ]]; then
  ok "US-3 C 型 submit：HTTP 409（守門成立）"
  python3 -m json.tool < /tmp/verify_submit.json 2>/dev/null | head -30
else
  bad "US-3 C 型 submit：HTTP $submit_code — 期望 409，這是 P0"
  # 補 echo：head -c 不會收尾換行，少了它下一段的判定結果會被黏在這坨 JSON 後面，
  # 用 grep 看輸出時會整條漏掉（2026-09-12 實際踩到）。
  head -c 600 /tmp/verify_submit.json; echo
fi

# ── 5) 聊天端點真的在串流 ─────────────────────────────────────────────
# 為什麼要有這一段：ALB 緩衝、X-Accel-Buffering 掉了、SSE generator 沒 flush——
# 這些壞法在本機（沒有 ALB）全都看不到，而且在雲上**全都會回 HTTP 200**。
# 既有四段沒有任何一段碰得到長連線。
#
# ⚠️ run_id 是必填、且必須是一次**已完成**的 run（spec 2026-09-12-chat-honesty-lamps
#    §2.1）。只送 message 的版本在系統完全正常時也會拿不到 data: 行——那是恆假檢查，
#    比假通過更毒，因為它會訓練人忽略整份 verify.sh。所以這裡重用第 3 段跑完的 $rid。
if [[ -z "${rid:-}" ]]; then
  bad "聊天 SSE：沒有可用的 run_id（第 3 段沒跑成）—— 這段無法驗，不是聊天壞了"
else
  # ⚠️ 不能截斷輸出（原本是 `| head -c 4000`）：2026-09-13 經 CloudFront 實測，這一輪完整串流
  #    13,771 bytes、17 秒，`done` 在 4000 bytes 之後——截斷就把「正常收尾」判成「串流被截斷」。
  #    --max-time 也放寬：聊天一輪 10–72 秒，90 秒的上限會把慢一點的正常回合切掉。
  chat_out="$(curl -N -s --max-time 240 -X POST \
    "$base/api/cases/synthetic-ordinary-01/chat" \
    -H 'content-type: application/json' \
    -d "{\"run_id\":\"$rid\",\"message\":\"有沒有類似的訴願決定可以參考？\"}")"
  # 判準是**真的收到 data: 行**，不是 HTTP 200。只比狀態碼抓不到上面那些壞法。
  if printf '%s' "$chat_out" | grep -q '^data:'; then
    if printf '%s' "$chat_out" | grep -q '^event: done'; then
      ok "US-5 聊天 SSE：收到 data: 事件並以 done 收尾"
    elif printf '%s' "$chat_out" | grep -q '^event: error'; then
      # error 是合法的事件形狀（spec §4.6），但它代表這一輪失敗了，不能算綠。
      bad "US-5 聊天 SSE：串流通了，但這一輪以 error 收尾"
      printf '%s\n' "$chat_out" | grep '^data:' | tail -1 | head -c 600; echo
    else
      bad "US-5 聊天 SSE：有 data: 但沒有 done 也沒有 error —— 串流被截斷"
    fi
  else
    bad "US-5 聊天 SSE：一個 data: 事件都沒收到（HTTP 通不代表串流通）"
    printf '%s' "$chat_out" | head -c 600; echo
  fi
fi

echo
echo "── 資料隔離（對線上實打）──"
# 前提：dist 會被掛成靜態目錄對外服務，誤放進去的檔案就是一個公開 URL。
#
# ⚠️ 掛載點會變，而且已經變過一次：
#   舊版（84cb4ed）   app.mount("/static", StaticFiles(dist))        → 整個 dist 對外
#   新版（切 Vite 後）app.mount("/assets", StaticFiles(dist/assets)) → 只有 assets/，
#                     而且 /static **整個不存在**。
#
# 所以「對 /static/... 打出 404」在新版不代表資料被保護，只代表那個掛載點沒了。
# 寫死路徑清單的檢查會在換版後全綠，而真正的暴露面一條都沒測到——
# 一條因為錯誤理由而通過的檢查，比沒有檢查更糟，它給人已經驗過的錯覺。
#
# 對策：從線上的 index.html 反推當下真正的掛載前綴，
# 並且**先證明探針打得到活的資源**，之後的 404 才具有意義（自我校準）。
idx_html="$(curl -s "${base}/")"
refs="$(printf '%s' "${idx_html}" | python3 "${here}/probe_refs.py" refs 2>/dev/null)"
prefixes="$(printf '%s' "${idx_html}" | python3 "${here}/probe_refs.py" prefixes 2>/dev/null)"

probe_live=0
live_example=""
while IFS= read -r r; do
  [[ -z "$r" ]] && continue
  c="$(curl -s -o /dev/null -w '%{http_code}' "${base}${r}")"
  if [[ "$c" == "200" ]]; then probe_live=1; live_example="$r"; break; fi
done <<< "${refs}"

forbidden="kb-graph.json manifest.json index-state.json laws-snapshot.json"

if [[ "$probe_live" == 1 ]]; then
  ok "資料隔離 · 探針自我校準：${live_example} 回 200（底下的 404 才有意義）"
  leak=0
  while IFS= read -r pre; do
    [[ -z "$pre" ]] && continue
    for f in ${forbidden}; do
      c="$(curl -s -o /dev/null -w '%{http_code}' "${base}${pre}/${f}")"
      if [[ "$c" == "200" ]]; then
        bad "資料隔離：${pre}/${f} 回 200 — 公開網址上抓得到，這是資料隔離違規"
        leak=1
      fi
    done
  done <<< "${prefixes}"
  [[ "$leak" == 0 ]] && ok "資料隔離：實際掛載前綴 [$(printf '%s' "${prefixes}" | tr '\n' ' ')] 底下抓不到 ${forbidden}"
else
  # 單檔全內嵌的前端不引用任何站內資源，本來就沒有靜態掛載面。
  # 這種情況**不算通過**，如實說「無可測面」，不要讓它假裝成綠燈。
  note "· 資料隔離：index.html 沒有引用任何站內靜態資源（單檔全內嵌版），沒有掛載面可測。"
  note "  這**不是通過**，只是這一版沒有這個暴露面。換成 Vite 產物後這段會自動開始真的測。"
fi

# 舊掛載點：保留但標明。換版後這些恆 404，本身不構成有效檢查。
legacy_hit=0
for pth in /static/kb-graph.json /static/manifest.json /data/manifest.json /api/data/manifest.json /kb-graph.json /manifest.json; do
  c="$(curl -s -o /dev/null -w '%{http_code}' "${base}${pth}")"
  if [[ "$c" == "200" ]]; then
    bad "資料隔離：${pth} 回 200 — 公開網址上抓得到"
    legacy_hit=1
  fi
done
if [[ "$legacy_hit" == 0 ]]; then
  note "· 資料隔離 · 舊版遺留路徑（/static、/data、根目錄）皆非 200。"
  note "  註：新版已無 /static 掛載，這幾條恆 404，**本身不是有效檢查**，留著是為了看得出涵蓋範圍的變遷。"
fi

echo
if [[ "$fail" == 0 ]]; then echo "全部通過。"; else echo "有項目未通過 — 不得交件。"; fi
exit "$fail"
