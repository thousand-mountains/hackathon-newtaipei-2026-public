"""Task 8b：SSE 順跑路徑（node_start → node_done → run_done → 仍走 result_url 取 payload）。

沒有 AWS 憑證就跑不出成功的 202 執行，所以這一支把 fetch 與 EventSource 換成替身，
直接對 postRun() 本身下手——驗的是前端這段程式碼的行為，不是後端。
"""
import json, sys
from playwright.sync_api import sync_playwright

r = {"errors": []}
STUB = """
async ()=>{
  const prog=[]; const calls=[];
  const realFetch=window.fetch, realES=window.EventSource;
  window.fetch=(u,o)=>{
    const url=String(u); calls.push(((o&&o.method)||'GET')+' '+url);
    if(/\\/runs$/.test(url))
      return Promise.resolve(new Response(JSON.stringify(
        {run_id:'run-stub-1',status:'running',result_url:'/api/runs/run-stub-1',
         events_url:'/api/runs/run-stub-1/events'}),{status:202}));
    if(/api\\/runs\\/run-stub-1$/.test(url))
      return Promise.resolve(new Response(JSON.stringify(
        {run_id:'run-stub-1',run_meta:{from_node:'n5',node_timings:{n5:11,n6:22}}}),{status:200}));
    return realFetch(u,o);
  };
  window.EventSource=function(u){
    calls.push('SSE '+u);
    const ls={};
    const es={addEventListener:(n,f)=>{(ls[n]=ls[n]||[]).push(f)},close:()=>{calls.push('SSE closed')},onerror:null};
    setTimeout(()=>{
      (ls.node_start||[]).forEach(f=>f({data:JSON.stringify({node:'n5'})}));
      (ls.node_done||[]).forEach(f=>f({data:JSON.stringify({node:'n5',elapsed_ms:1234})}));
      (ls.run_done||[]).forEach(f=>f({data:'{}'}));
    },10);
    return es;
  };
  L.chosen='synthetic-ordinary-01';
  let out;
  try{ out={ok:true,p:await postRun({from_node:'n5'},m=>prog.push(m))}; }
  catch(e){ out={ok:false,err:String(e&&e.message||e)}; }
  window.fetch=realFetch; window.EventSource=realES;
  return {out:out,prog:prog,calls:calls,runId:L.runId,pending:L.pendingRunId};
}
"""
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page()
    pg.on("pageerror", lambda e: r["errors"].append(f"pageerror: {e}"))
    pg.goto("http://127.0.0.1:8123/?case=synthetic-ordinary-01", wait_until="networkidle")
    pg.wait_for_function("document.querySelector('#sourcenote').textContent!==''", timeout=30000)
    r["result"] = pg.evaluate(STUB)
    b.close()

res = r["result"]
if not res["out"]["ok"]: r["errors"].append("postRun 應該成功：" + str(res["out"].get("err")))
elif res["out"]["p"]["run_id"] != "run-stub-1": r["errors"].append("payload 不是 result_url 拿回來的那一份")
if not any("SSE " in c for c in res["calls"]): r["errors"].append("沒有接 events_url")
if "SSE closed" not in res["calls"]: r["errors"].append("run_done 之後沒有 close() 事件流")
if not any(c == "GET api/runs/run-stub-1" for c in res["calls"]):
    r["errors"].append("run_done 之後沒有回頭 GET result_url")
if not any("n5" in m or "決定書主筆" in m for m in res["prog"]):
    r["errors"].append("進度訊息沒有講出節點")
if not any("1234 ms" in m for m in res["prog"]): r["errors"].append("node_done 沒有把 elapsed_ms 說出來")
if res["runId"] != "run-stub-1": r["errors"].append("L.runId 沒有更新成拿到 payload 的那一次")
print(json.dumps(r, ensure_ascii=False, indent=1))
sys.exit(1 if r["errors"] else 0)
