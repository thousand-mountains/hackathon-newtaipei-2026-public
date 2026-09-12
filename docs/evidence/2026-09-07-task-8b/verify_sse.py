"""Task 8b：SSE 路徑驗證（bedrock 假設定：202 → EventSource → node_start → run_failed）。"""
import json, sys
from playwright.sync_api import sync_playwright

r = {"requests": [], "errors": []}
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 1000})
    pg.on("pageerror", lambda e: r["errors"].append(f"pageerror: {e}"))
    pg.on("request", lambda q: r["requests"].append(q.url) if "/runs" in q.url else None)
    # 直接在頁面裡開一條 EventSource，記下真的收到哪些事件（EventSource 不進 Playwright 的 response 事件）
    pg.add_init_script("""
      window.__sse=[];
      const OrigES=window.EventSource;
      window.EventSource=function(u){
        const es=new OrigES(u); window.__sse.push(['open',u]);
        ['node_start','node_done','run_done','run_failed','timeout'].forEach(n=>
          es.addEventListener(n,e=>window.__sse.push([n,e.data])));
        return es;
      };
      window.EventSource.prototype=OrigES.prototype;
    """)
    pg.goto("http://127.0.0.1:8123/?case=synthetic-ordinary-01", wait_until="domcontentloaded")
    # boot() 的 postRun 會拿到 202、接 SSE；n1 打不到 bedrock 會 run_failed
    pg.wait_for_function("document.querySelector('#modebadge').dataset.m==='offline'", timeout=120000)
    r["sse"] = pg.evaluate("()=>window.__sse")
    r["badge_text"] = pg.inner_text("#modebadge")
    r["badge_tip"] = pg.eval_on_selector("#modebadge", "e=>e.dataset.tip")
    r["regenbar_hidden"] = pg.eval_on_selector("#regenbar", "e=>e.hidden")
    pg.screenshot(path="/tmp/t8b/sse-run-failed.png", clip={"x":0,"y":0,"width":1440,"height":320})
    b.close()

evs = [e[0] for e in r.get("sse", [])]
if "open" not in evs: r["errors"].append("沒有開 EventSource")
if "node_start" not in evs: r["errors"].append("沒有收到 node_start")
if "run_failed" not in evs: r["errors"].append("沒有收到 run_failed")
if "n1" not in (r.get("badge_tip") or "") and "卷證書記官" not in (r.get("badge_tip") or ""):
    r["errors"].append("失敗訊息沒有講出是哪個節點")
print(json.dumps(r, ensure_ascii=False, indent=1))
sys.exit(1 if r["errors"] else 0)
