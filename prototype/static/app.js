/* 訴願決定書 AI 輔助撰擬系統 — 前端流程（5 步：收文→幕僚團→草稿→燈號→送出）。

   兩種資料模式，頁首徽章常駐標示，不讓人搞不清楚看到的是哪一種：

   ┌ live 後端 ──────────────────────────────────────────────────────────────┐
   │ /api/health 打得通 → 案例清單來自 /api/cases，payload 來自              │
   │ POST /api/cases/{id}/runs（六節點跑完的結果）。                          │
   │ **燈號、引用四態、why、blockers、送出許可一律以後端為準，前端不重算。**   │
   │ 理由是 CONSTITUTION §1：燈號不是模型也不是前端產出，它是守門節點的判定；  │
   │ 前端自己再算一次就等於多一個會跟後端打架的真相來源。                      │
   └──────────────────────────────────────────────────────────────────────────┘
   ┌ 離線 fixture ───────────────────────────────────────────────────────────┐
   │ 打不通（例如 file:// 直開單檔）→ 用 build.py 內嵌的 data/case-demo.json，│
   │ 期間由 engine.js 實算、法條由 laws-snapshot.json 對照（v0 行為原樣保留）。│
   └──────────────────────────────────────────────────────────────────────────┘ */

const FIXTURE_CASE = JSON.parse(document.getElementById('case-data').textContent);
const SNAP = JSON.parse(document.getElementById('snapshot-data').textContent);

const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

/* ================= 模式 ================= */
let MODE='offline';   /* 'live' | 'offline' */
let CASE=FIXTURE_CASE;
let LIVE=null;        /* live 模式下的後端 payload 原文，未經前端改寫 */

/* live 模式的狀態容器（chosen 案例、案例清單、上一次的 run_id）。
   boot() 與 bootApp() 兩邊都要碰同一份——續跑要拿 runId、上傳建案要換 chosen——
   所以放在模組層，而不是只活在 bootApp() 裡。 */
const L={};

/* 同一時間只允許一個後端動作（上傳建案／確認後重跑）。兩個一起跑會對同一個案件
   送出兩次執行，後到的那次還可能拿一個根本還沒建立的 base_run_id。 */
let BUSY=false;
/* 輪詢的軟上限。不是「後端一定失敗了」，只是前端不再等下去——
   逾時訊息會把 run_id 與 result_url 一起講出來，這次執行沒有丟。 */
const POLL_TIMEOUT_MS=10*60*1000;

function setBadge(mode,label,tip){
  const el=$('#modebadge'); if(!el)return;
  el.dataset.m=mode;
  el.innerHTML='<span class="dotm"></span>'+esc(label);
  if(tip)el.dataset.tip=tip;
}

/* 徽章上的 run_id 必須跟畫面上這一份 payload 是同一次執行。
   換過 payload（承辦人確認續跑、上傳建案）就要重寫一次，
   否則 tooltip 會拿上一次的 run_id 替這一頁的內容背書。 */
function liveBadge(payload){
  setBadge('live','live 後端・'+L.chosen,
    '本頁資料來自後端六節點的一次實際執行（run_id '+((payload&&payload.run_id)||'—')+'，'+
    'RUN_MODE='+((payload&&payload.run_meta&&payload.run_meta.run_mode)||'?')+'）。'+
    '燈號、引用狀態、送出許可全部由後端守門節點判定，前端不重算。');
}

/* 幕僚卡片圖示是前端的表現層（architecture §6.2「前端有、後端不需要新增的」），
   後端只給 k，這裡對回 Material Symbols 的 ligature 名。 */
const AGENT_ICO={clerk:'find_in_page',clf:'category',proc:'gavel',law:'menu_book',
                 case:'manage_search',draft:'edit_note',qc:'traffic'};
/* auto_fields 後端回的是欄位名（no/type/…），這裡才知道對應哪個表單徽章元素。 */
const AUTO_FIELD_DOM={type:'a_type',person:'a_p',org:'a_org',d1:'a_d1',d2:'a_d2',d3:'a_d3'};

/* 後端 payload → 前端 CASE 形狀。只做「補前端表現層需要的欄位」，
   不改任何一個燈號、why、引用狀態的值。 */
function adaptPayload(p){
  const dl=(p.screen&&p.screen.deadline)||{};
  const doc=(p.doc||[]).map(bk=>{
    if(!bk.ss)return bk;
    return Object.assign({},bk,{ss:bk.ss.map(s=>Object.assign({},s,{
      /* 算式卡吃 N3 期間引擎的 steps／caveats（後端 screen.deadline），前端不重算 */
      steps:s.engine==='deadline'?(dl.steps||null):null,
      caveats:s.engine==='deadline'?(dl.caveats||null):null,
      refs:s.refs||[]
    }))});
  });
  return {
    provenance:p.provenance||{},
    intake:p.intake||{},
    files:p.files||[],
    agents:(p.agents||[]).map(a=>Object.assign({},a,{ico:AGENT_ICO[a.k]||'article'})),
    laws:p.laws||[], cases:p.cases||[], issues:p.issues||[],
    doc:doc,
    token_note:p.token_note||''
  };
}

/* ================= 執行入口 ================= */
/* 後端的錯誤原文照搬給承辦人看；FastAPI 把訊息包在 {detail:…} 裡，
   拆出來才不會在畫面上出現一整串 JSON 標點蓋掉真正的那句話。 */
async function errText(res){
  const t=await res.text();
  try{const d=JSON.parse(t).detail; return typeof d==='string'?d:(d?JSON.stringify(d):t);}
  catch(_){return t;}
}

/* 全站唯一一個「叫後端跑六節點」的地方。兩個檔位的回應形狀不同，差異吸收在這裡：
   - fixture：200 ＋ 完整 payload（同步跑完）。
   - bedrock：202 ＋ {run_id,result_url,events_url}。先接 events_url 的 SSE 看逐節點進度
     （加值層，斷了就退回輪詢），拿結果一律走 result_url：每 2 秒 GET 一次，
     409 表示還在跑、200 是結果、其他一律當失敗。
   失敗一定往外拋：呼叫端的責任是把錯誤顯示出來，不是拿舊 payload 續跑（CONSTITUTION §1）。 */
async function postRun(body,onProgress){
  const res=await fetch('api/cases/'+encodeURIComponent(L.chosen)+'/runs',{
    method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},
    body:JSON.stringify(body||{})});
  if(res.status===200){const p=await res.json(); L.runId=p.run_id; return p;}
  if(res.status!==202)throw new Error('runs HTTP '+res.status+'：'+(await errText(res)).slice(0,200));
  /* 進行中的 run 先放 pendingRunId：**L.runId 只在跑完拿到 payload 時才更新**。
     失敗的 run 沒有可讀的終態，讓它變成下一次的 base_run_id 只會換來一個 404。
     pendingRunId 只用來把「進行中的 run」講出來（SSE 進度訊息）。 */
  const ticket=await res.json(); L.pendingRunId=ticket.run_id;

  /* 加值層：有事件流就先接 SSE，逐節點把進度說出來。bedrock 檔位一次執行要幾十秒，
     只寫「執行中…」看起來跟卡死沒兩樣，說得出「現在跑到哪一個節點」才是進度。
     **SSE 不是取結果的路徑**：收到 run_done 之後仍然往下走輪詢迴圈 GET result_url
     把 payload 拿回來（後端 §5.6 明寫事件不持久化、沒有回放）。
     事件流斷線（process 重啟、proxy 把事件緩衝掉、404）一律 resolve 退回輪詢——
     事件沒了不代表執行沒了，把它當失敗就是前端自己編出一個後端沒說的結論。 */
  if(ticket.events_url&&window.EventSource){
    const NAMES={n1:'卷證書記官 抽取中',n2:'分類調查官 判型中',n3:'程序審查官 算期間',
                 n4:'法規／案例檢索中',n5:'決定書主筆 組稿中',n6:'品管守門員 驗證中'};
    const label=n=>NAMES[n]||('節點 '+String(n||'?'));
    const say=t=>onProgress&&onProgress('run '+ticket.run_id+' 進行中：'+t);
    await new Promise((resolve,reject)=>{
      const es=new EventSource(String(ticket.events_url).replace(/^\//,''));
      /* 每條 SSE 在後端佔一個 threadpool thread，任何收尾都要先 close()，
         不然離開這個 promise 之後連線還開著。 */
      const end=fn=>{es.close();fn();};
      const parse=e=>{try{return JSON.parse(e.data)||{}}catch(_){return {}}};
      es.addEventListener('node_start',e=>say(label(parse(e).node)));
      es.addEventListener('node_done',e=>{
        const d=parse(e);
        say(label(d.node)+' 完成（'+(d.elapsed_ms==null?'—':d.elapsed_ms+' ms')+'）');
      });
      es.addEventListener('run_done',()=>end(resolve));
      es.addEventListener('run_failed',e=>{
        const d=parse(e);
        end(()=>reject(new Error(
          (d.node?label(d.node)+'（'+d.node+'）失敗：':'執行失敗：')+
          (d.error||'後端未附錯誤訊息'))));
      });
      /* 後端閒置 5 分鐘沒有新事件就發 timeout 並關流。措辭照輪詢逾時那一套：
         這是「前端不再等下去」，不是「後端失敗了」——run_id 與 result_url 都講出來。 */
      es.addEventListener('timeout',()=>end(()=>reject(new Error(
        '事件流閒置逾時（5 分鐘沒有新的節點事件）：run '+ticket.run_id+
        ' 可能仍在執行，稍後可用 GET '+ticket.result_url+' 取結果'))));
      es.onerror=()=>end(resolve);   /* 斷線／404 → 不當失敗，退回輪詢 */
    });
  }

  /* 輪詢的逾時預算從這裡起算，不含上面的 SSE 等待：有逐節點事件在進來就代表執行還活著，
     上限要管的是「沒有事件可看、只能盲等」的那一段。 */
  const started=Date.now();
  for(;;){
    await new Promise(r=>setTimeout(r,2000));
    if(Date.now()-started>POLL_TIMEOUT_MS)
      throw new Error('等待結果逾時（'+Math.round(POLL_TIMEOUT_MS/60000)+' 分鐘）：run '+ticket.run_id+
                      ' 仍在執行，稍後可用 GET '+ticket.result_url+' 取結果');
    const r=await fetch(String(ticket.result_url||'').replace(/^\//,''),{headers:{'Accept':'application/json'}});
    if(r.status===409){onProgress&&onProgress('六節點執行中…'+Math.round((Date.now()-started)/1000)+' 秒');continue;}
    if(r.status===200){const p=await r.json(); L.runId=p.run_id||ticket.run_id; return p;}
    throw new Error('執行失敗 HTTP '+r.status+'：'+(await errText(r)).slice(0,300));
  }
}

/* ================= 啟動：先探後端，再決定用哪一份資料 ================= */
async function boot(){
  let health=null, caseIds=[], payload=null, chosen=null;
  try{
    /* file:// 直開時瀏覽器根本不允許 fetch，先擋掉，
       否則 devtools 會多一則看起來像壞掉的網路錯誤。 */
    if(location.protocol==='file:')throw new Error('file:// 直開，瀏覽器不允許 fetch');
    const r=await fetch('api/health',{headers:{'Accept':'application/json'}});
    if(!r.ok)throw new Error('health HTTP '+r.status);
    health=await r.json();
    if(!health.ok)throw new Error('health.ok=false');

    const rc=await fetch('api/cases',{headers:{'Accept':'application/json'}});
    if(!rc.ok)throw new Error('cases HTTP '+rc.status);
    caseIds=(await rc.json()).cases||[];
    if(!caseIds.length)throw new Error('後端沒有可用案例');

    const want=new URLSearchParams(location.search).get('case');
    chosen=caseIds.includes(want)?want
      :(caseIds.includes('synthetic-ordinary-01')?'synthetic-ordinary-01':caseIds[0]);

    L.chosen=chosen;
    payload=await postRun({},msg=>setBadge('live','live 後端・'+chosen+'・'+msg,''));
  }catch(e){
    MODE='offline';
    setBadge('offline','離線 fixture（未接後端）',
      '本頁未連上後端 API（'+String(e&&e.message||e)+'）。畫面資料來自建置時內嵌的 data/case-demo.json，'+
      '期間由頁內 engine.js 實算、法條對照離線快照。這不是後端六節點的執行結果。');
    bootApp();
    return;
  }
  MODE='live'; LIVE=payload; CASE=adaptPayload(payload);
  liveBadge(payload);
  bootApp({caseIds:caseIds,chosen:chosen,health:health});
}

/* ================= 主程式 ================= */
function bootApp(live){
  Object.assign(L,live||{});
  const isLive=MODE==='live';

/* ================= 狀態 ================= */
const S={step:0,unlocked:0,startTime:null};

function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('on');clearTimeout(t._x);t._x=setTimeout(()=>t.classList.remove('on'),2200);}

function goto(i){
  S.step=i; S.unlocked=Math.max(S.unlocked,i);
  $$('.panel').forEach(p=>p.classList.toggle('on',+p.dataset.p===i));
  $$('.step').forEach(b=>{
    const n=+b.dataset.i;
    b.disabled=n>S.unlocked;
    b.setAttribute('aria-current',n===i?'step':'false');
    b.dataset.on=n<=S.unlocked?1:0;
    b.dataset.done=n<i?1:0;
  });
  window.scrollTo({top:0,behavior:'smooth'});
}
$('#stepbar').addEventListener('click',e=>{const b=e.target.closest('.step');if(b&&+b.dataset.i<=S.unlocked)goto(+b.dataset.i);});
$$('[data-back]').forEach(b=>b.onclick=()=>goto(+b.dataset.back));

/* ================= 1 上傳 ================= */
let DEMO_FILES=CASE.files||[];
/* 離線模式（沒有後端可送）拖進來的檔案**不會被讀取**，只是動線示意——
   原本寫「✓ 已解析」會讓人以為系統剖析了他丟進來的 PDF。
   live 模式走 uploadFiles()，檔案是真的送到 POST /api/cases。 */
const UPLOAD_NOTE='本 demo 不讀取上傳檔內容，案情來自後端合成案例';
function addFile(f,i){
  const li=document.createElement('li');
  li.style.animationDelay=(i*90)+'ms';
  li.innerHTML=`<span class="mi doc">description</span>
    <span class="meta"><b>${esc(f.n)}</b><span>${esc(f.s)}　·　${esc(f.x)}</span></span>
    <span class="ok" title="${esc(f.x)}">已上傳</span><button aria-label="移除">×</button>`;
  li.querySelector('button').onclick=()=>{li.remove();updateGo1()};
  $('#filelist').appendChild(li);
  updateGo1();
}
/* select 的選項清單是 v0 寫死的；後端案型不在清單內時補一個，
   不要靜默設不進去讓「啟動幕僚團」永遠是灰的。 */
function setSelectValue(sel,val){
  if(val==null||val==='')return;
  if(![...sel.options].some(o=>o.value===val))
    sel.appendChild(Object.assign(document.createElement('option'),{value:val,textContent:val}));
  sel.value=val;
}
/* after：欄位填完（含 updateGo1／renderConfirmNote）之後才跑的收尾。
   填欄位排在 700ms 的 setTimeout 裡，呼叫端要在那之後才說得出「已換成本次抽取的結果」，
   不然那句話會被 updateGo1() 重算的提示蓋掉。 */
function loadDemo(after){
  $('#filelist').innerHTML='';
  DEMO_FILES.forEach(addFile);
  const K=CASE.intake||{};
  setTimeout(()=>{
    $('#f_no').value=K.no||'';
    setSelectValue($('#f_type'),K.type);
    $('#f_person').value=K.person||'';
    $('#f_org').value=K.org||'';
    $('#f_d1').value=K.d1||'';
    $('#f_d2').value=K.d2||'';
    $('#f_d3').value=K.d3||'';
    setSelectValue($('#f_agent'),K.agent);
    setSelectValue($('#f_sm'),K.service_method);
    $('#f_transit').value=(K.transit_days!=null?K.transit_days:0);
    $('#f_interested').checked=!!K.interested_party;
    $('#f_note').value=K.note||'';
    updateGo1();
    (K.auto_fields||[]).forEach(id=>{
      const dom=String(id).startsWith('a_')?id:AUTO_FIELD_DOM[id];
      const el=dom&&$('#'+dom); if(el)el.textContent='自動擷取';
    });
    if((K.auto_fields||[]).includes('service_method')){const e=$('#a_sm'); if(e)e.textContent='自動擷取';}
    renderConfirmNote();
    if(K.auto_toast)toast(K.auto_toast);
    if(after)after();
  },700);
}
/* 不寫 onclick=loadDemo：那樣會把 click 事件當成 after 傳進去（後面就 after is not a function）。 */
$('#demoload').onclick=()=>loadDemo();

/* live 模式：拖進來的檔案真的 POST /api/cases 建成 upload- 案件，接著跑六節點。
   fixture 檔位的後端會在 runs 回 400（上傳案沒有可重播的 fixture，只能在 RUN_MODE=bedrock 執行），
   那就把後端那句話原樣顯示出來——不拿合成案例的畫面假裝剛剛剖析了他的卷證。
   失敗時把 chosen／runId 退回原案例，也不把跑不動的選項留在下拉選單裡：
   「沒有切換過去」跟「切換成功」是兩件事，畫面不能讓人分不出來。 */
/* 後端動作的互斥閘。上傳建案與「啟動幕僚團分析」都會打 /runs：
   兩個同時跑會對同一個案件送出兩次執行，而且後按的那次會拿一個還沒建立的 base_run_id。
   進行中就擋下來並說出來，不排隊、不靜默丟掉。 */
function beginBusy(){
  BUSY=true;
  $('#go1').disabled=true;
  drop.classList.add('busy');
}
function endBusy(){
  BUSY=false;
  drop.classList.remove('busy');
  /* updateGo1() 會把 #go1 的 disabled 與提示都重算——失敗訊息不能被它洗掉，先留著再放回去。 */
  const keep=$('#go1hint').textContent;
  updateGo1();
  if(keep)$('#go1hint').textContent=keep;
}
const BUSY_MSG='上一個動作還在進行中，請稍候';

async function uploadFiles(fileList){
  const files=[...(fileList||[])];
  if(!files.length)return;
  const hint=$('#go1hint'), prevCase=L.chosen, prevRun=L.runId;
  if(BUSY){hint.textContent=BUSY_MSG;return;}
  beginBusy();
  hint.textContent='上傳卷證中…（'+files.length+' 個檔案）';
  try{
    const fd=new FormData();
    files.forEach(f=>fd.append('files',f,f.name));
    const res=await fetch('api/cases',{method:'POST',body:fd});
    if(!res.ok)throw new Error('上傳 HTTP '+res.status+'：'+(await errText(res)).slice(0,200));
    const meta=await res.json();
    L.chosen=meta.case_id; L.runId=null;
    hint.textContent='已建案 '+meta.case_id+'，卷證書記官抽取中…';
    applyLivePayload(await postRun({},m=>{hint.textContent=m;}));
    const sel=$('#caseselect');
    if(sel&&![...sel.options].some(o=>o.value===meta.case_id))
      sel.appendChild(Object.assign(document.createElement('option'),
        {value:meta.case_id,textContent:meta.case_id+'（上傳）'}));
    if(sel)sel.value=meta.case_id;
    $('#demoload').textContent='載入案件（'+meta.case_id+'）';
    hint.textContent='';
    loadDemo();
  }catch(e){
    L.chosen=prevCase; L.runId=prevRun;
    hint.textContent='上傳或抽取失敗（'+String(e&&e.message||e)+'）——未以舊資料假裝成功，仍停留在案例 '+prevCase+'。';
  }finally{
    endBusy();
  }
}

const drop=$('#drop');
drop.onclick=()=>$('#filein').click();
drop.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();loadDemo();}};
['dragenter','dragover'].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.add('hot')}));
['dragleave','drop'].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.remove('hot')}));
drop.addEventListener('drop',e=>{
  const fs=[...(e.dataTransfer?.files||[])];
  if(!fs.length){loadDemo();return;}
  if(isLive)uploadFiles(fs);
  else fs.forEach((f,i)=>addFile({n:f.name,s:Math.round(f.size/1024)+' KB',x:UPLOAD_NOTE},i));
});
$('#filein').onchange=e=>{
  const fs=[...e.target.files];
  if(isLive)uploadFiles(fs);
  else fs.forEach((f,i)=>addFile({n:f.name,s:Math.round(f.size/1024)+' KB',x:UPLOAD_NOTE},i));
};

function updateGo1(){
  const files=$('#filelist').children.length;
  const no=$('#f_no').value.trim(), ty=$('#f_type').value;
  /* live 模式多一道：承辦人必須明示勾選「我已核對上列全部欄位」。
     按下「啟動幕僚團分析」不能自己代表「有人看過」——那正是判斷卡 7 打穿的假設。 */
  const confirmed=!isLive||$('#f_confirm').checked;
  const ok=files>0&&no&&ty&&confirmed;
  $('#go1').disabled=!ok;
  $('#go1hint').textContent=!files?'請先上傳卷證'
    :(!no||!ty)?'請填寫收文案號與案件類型'
    :(!confirmed)?'請先勾選「我已核對上列全部欄位」——未確認的欄位系統不會採信':'';
}
['#f_no','#f_type','#f_confirm'].forEach(s=>{$(s).addEventListener('input',updateGo1);$(s).addEventListener('change',updateGo1)});
if(!isLive){const cb=$('#confirmbox'); if(cb)cb.style.display='none';}
/* ================= 判斷卡 7：承辦人確認 intake ================= */
/* 「啟動幕僚團分析」＝ 承辦人已經看過這一頁的欄位（可能改過、也可能原樣採用）。
   把它們當作**人工確認**送進後端，後端才允許用程序結果解除結論封鎖。
   不確認的話，覆核實測證明：只要 N1 抽錯一個日期，整個結論封鎖就會被關掉。 */
function collectIntake(){
  const v=s=>$(s).value;
  return {
    no:v('#f_no'), type:v('#f_type'), person:v('#f_person'), org:v('#f_org'),
    d1:v('#f_d1'), d2:v('#f_d2'), d3:v('#f_d3'), agent:v('#f_agent'), note:v('#f_note'),
    service_method:v('#f_sm'),
    transit_days:parseInt(v('#f_transit'),10)||0,
    interested_party:$('#f_interested').checked
  };
}
function renderConfirmNote(){
  const el=$('#confirmnote'); if(!el)return;
  const conf=(isLive&&LIVE&&LIVE.intake_confirmed)||[];
  if(!isLive){el.textContent='離線模式：本頁不與後端往來，沒有「承辦人確認」這個狀態。';return;}
  el.innerHTML=conf.length
    ? '<b style="color:var(--green)">已由承辦人確認（'+conf.length+' 欄）</b>：'+conf.map(esc).join('、')+
      '。程序判斷（期滿日、訴願法 77 條款）建立在這些確認過的欄位上。'
    : '<b style="color:var(--amber)">尚未由承辦人確認</b>：目前欄位全部由模型自卷證抽取。'+
      '按「啟動幕僚團分析」即視為承辦人已核對本頁欄位；'+
      '在此之前，系統不會用期間結果解除結論段封鎖（結論一律交人工）。';
}

/* 帶著確認過的欄位重跑一次後端，並把整個畫面換成新 payload。
   為什麼要重跑而不是改前端狀態：確認會改變後端的封鎖判斷，
   那個判斷只有後端算得準——前端拿舊 payload 改幾個欄位就是在假裝。 */
async function runConfirmed(){
  const hint=$('#go1hint');
  if(BUSY){hint.textContent=BUSY_MSG;return false;}
  beginBusy();
  hint.textContent='送出承辦人確認並重跑六節點…';
  try{
    const body={confirmed_intake:collectIntake()};
    /* 有上一次的 run_id 就從 n2 續跑：卷證書記官（n1）已經抽過了，
       承辦人確認改的是欄位不是卷證，再抽一次只會多花一次模型呼叫、還可能抽出不一樣的東西。 */
    if(L.runId){body.base_run_id=L.runId; body.from_node='n2';}
    applyLivePayload(await postRun(body,m=>{hint.textContent=m;}));
    hint.textContent='';
    return true;
  }catch(e){
    hint.textContent='後端重跑失敗（'+String(e&&e.message||e)+'）——停在本步，未以舊資料續跑。';
    return false;
  }finally{
    endBusy();
  }
}

/* 「資料來源」那一行跟徽章一樣，講的是**畫面上這一份 payload** 是哪一次執行。
   重跑換過 payload 就要重寫，否則徽章寫著新 run_id、來源說明還留著上一次的，
   等於兩個 run_id 同時替同一頁背書。 */
function renderSourceNote(){
  const note=$('#sourcenote');
  if(!note||!isLive)return;
  note.innerHTML='資料來源：後端 <code>POST /api/cases/'+esc(L.chosen)+
    '/runs</code>（run_id <code>'+esc((LIVE&&LIVE.run_id)||'—')+'</code>）。'+
    '燈號與引用狀態由後端守門節點判定，本頁不重算。';
}

function applyLivePayload(payload){
  LIVE=payload; CASE=adaptPayload(payload);
  DOC=CASE.doc||[]; DEMO_FILES=CASE.files||[];
  LAWS=CASE.laws||[]; CASES=CASE.cases||[]; ISSUES=CASE.issues||[];
  ALLREFS=[...LAWS,...CASES,...ISSUES]; AGENTS=CASE.agents||[];
  rebuildSents();
  renderConfirmNote();
  renderProv();
  if(isLive){liveBadge(payload); renderSourceNote();}
}

/* ================= 每卡重新產生（加值層，spec §5.5） ================= */
/* 三顆按鈕＝三個「從這裡往下重跑到守門員」的入口：
   n1 連卷證都重抽、n4 只換檢索（可附加查詢詞）、n5 只重組稿。
   n1 以外都要帶 base_run_id，而且只認 L.runId——那是**上一次成功執行**的 id；
   進行中或失敗的 run 沒有可讀的終態，拿它當 base_run_id 只會換來一個 404（見 postRun）。
   跟上傳建案、承辦人確認重跑共用同一個 BUSY 閘門：三條路都會打 /runs，
   同時跑會對同一個案件送出兩次執行。 */
const REGEN_LABEL={n1:'重新抽取',n4:'重新檢索',n5:'重新產生草稿'};
function wireRegenBar(){
  const bar=$('#regenbar'); if(!bar)return;
  /* 離線 fixture 沒有後端可重跑，整列藏起來——按不動的按鈕比沒有按鈕更難解釋。 */
  bar.hidden=!isLive;
  if(!isLive)return;
  const btns=[...bar.querySelectorAll('button[data-from]')];
  btns.forEach(b=>b.onclick=async()=>{
    const hint=$('#go1hint'), from=b.dataset.from;
    if(BUSY){hint.textContent=BUSY_MSG;return;}
    const body={from_node:from};
    if(from!=='n1'){
      if(!L.runId){
        hint.textContent='沒有可接續的執行：'+REGEN_LABEL[from]+
          '要接上一次成功執行的 run_id，請先讓六節點完整跑過一次。';
        return;
      }
      body.base_run_id=L.runId;
    }
    /* 查詢詞只有 n4 送得出去（後端 overrides 白名單只有 n4_query）。
       別的節點就算輸入框有字也不帶——不然畫面在暗示一個不存在的作用。 */
    const q=(($('#regen-query')||{}).value||'').trim();
    if(from==='n4'&&q)body.overrides={n4_query:q};
    beginBusy();
    btns.forEach(x=>x.disabled=true);
    hint.textContent=REGEN_LABEL[from]+'：從 '+from+' 往下重跑到守門員…';
    try{
      applyLivePayload(await postRun(body,m=>{hint.textContent=m;}));
      const rm=(LIVE&&LIVE.run_meta)||{};
      /* 重跑會換掉期間算式、幕僚敘述與草稿。已經跑過幕僚團的話要整段重繪，
         否則第 2 步留著上一次執行的卡片、第 3 步卻是新草稿——畫面自相矛盾。
         沒跑過就不動：正常動線是按「啟動幕僚團分析」時才第一次渲染。 */
      if(agentsRan){agentsRan=false; runAgents();}
      /* 勾選框跟著後端的 intake_confirmed 走，不自己記狀態。從 n1 重抽會把欄位換成
         新抽出來的一份、後端的確認也一併清空——勾勾還打著就是畫面替承辦人背書，
         而旁邊的 renderConfirmNote() 已經寫著「尚未由承辦人確認」，兩句話會打架。 */
      const confirmed=(((LIVE&&LIVE.intake_confirmed)||[]).length>0);
      const cf=$('#f_confirm'); if(cf)cf.checked=confirmed;
      const where='已從 '+(rm.from_node||from)+' 重跑（run '+((LIVE&&LIVE.run_id)||'—')+
        (rm.base_run_id?'，接續 '+rm.base_run_id:'')+
        (body.overrides?'，查詢詞「'+q+'」':'')+'）';
      if(from==='n1'){
        /* 重抽出來的欄位要真的顯示出來：畫面留著舊值、run_meta 卻說剛剛重抽過，
           承辦人核對的就是一份不存在的抽取結果。 */
        loadDemo(()=>{hint.textContent=where+'：欄位已換成本次抽取的結果'+
          (confirmed?'。':'，承辦人確認已清空——請重新核對欄位並勾選「我已核對上列全部欄位」。')});
      }else{
        hint.textContent=where+'。燈號與引用狀態一律由後端守門節點重判。';
      }
    }catch(e){
      hint.textContent=REGEN_LABEL[from]+'失敗（'+String(e&&e.message||e)+
        '）——畫面仍是上一次的執行結果，沒有把新舊資料混拼成一份。';
    }finally{
      btns.forEach(x=>x.disabled=false);
      endBusy();   /* endBusy() 會保留上面這句 hint，不被 updateGo1() 洗掉 */
    }
  });
}

$('#go1').onclick=async ()=>{
  if($('#go1').disabled)return;
  /* 上傳還在跑的時候按下來：擋掉。按鈕在 beginBusy() 就已經 disabled，
     這一道是防拖曳中途的競態（例如鍵盤觸發），不是重複的保險。 */
  if(BUSY){$('#go1hint').textContent=BUSY_MSG;return;}
  if(isLive){
    /* 按鈕停用與還原由 runConfirmed() 內的 beginBusy／endBusy 管，這裡不再各管一份 */
    const ok=await runConfirmed();
    if(!ok)return;   /* 後端沒回來就停在第 1 步，不拿舊 payload 假裝跑過 */
  }
  S.startTime=Date.now();
  goto(1); runAgents();
};

/* ---- live 模式：案例選單與資料來源說明 ---- */
(function(){
  const note=$('#sourcenote');
  /* 兩種模式都要跑一次：live 顯示重新產生列、離線把它藏起來。
     按鈕的 handler 在點下去時才讀 L.runId，所以換過 payload 之後不需要重綁。 */
  wireRegenBar();
  if(isLive){
    $('#demoload').textContent='載入案件（'+L.chosen+'）';
    const pick=$('#casepick'), sel=$('#caseselect');
    if(pick&&sel){
      pick.style.display='inline-flex';
      (L.caseIds||[]).forEach(id=>sel.appendChild(
        Object.assign(document.createElement('option'),{value:id,textContent:id})));
      sel.value=L.chosen;
      /* 換案例＝重新向後端要一次執行結果。用整頁重載而不是就地換資料，
         避免半套狀態（已展開的草稿、已確認的紅燈）殘留造成畫面說謊。 */
      sel.onchange=()=>{location.search='?case='+encodeURIComponent(sel.value)};
    }
    renderSourceNote();
  }else if(note){
    note.textContent='資料來源：本頁內嵌的示範案件（data/case-demo.json）。未連上後端，'+
      '期間由頁內規則引擎實算、法條對照離線快照。';
  }
})();

/* ================= 2 幕僚團 ================= */
let AGENTS=CASE.agents||[];
let agentsRan=false;
function runAgents(){
  if(agentsRan)return; agentsRan=true;
  /* 先把期間結果掛上，幕僚敘述與燈號統計才有真數字可填。
     live 模式吃後端 N3 的算式；離線模式才由頁內引擎實算。 */
  isLive?applyLiveDeadline():applyDeadlineEngine();
  const wrap=$('#agents'); wrap.innerHTML=''; $('#console').innerHTML='';
  $('#agprog').textContent='0 / '+AGENTS.length;
  AGENTS.forEach(a=>{
    const d=document.createElement('div');
    d.className='agent'; d.dataset.s='idle'; d.id='ag-'+a.k;
    d.innerHTML=`<span class="pill"></span>
      <div class="hd"><span class="ico"><span class="mi" style="font-size:17px;color:var(--navy)">${esc(a.ico)}</span></span><h3>${esc(a.name)}</h3></div>
      <p class="out">等待前置作業…</p>
      <div class="st"><span class="dot">待命</span></div>
      <div class="bar-out"><i class="bar-in"></i></div>`;
    wrap.appendChild(d);
  });
  let done=0;
  const clock=()=>new Date().toLocaleTimeString('zh-TW',{hour12:false});
  const log=(txt,cls='')=>{
    const c=$('#console');
    c.insertAdjacentHTML('beforeend',`<div><span class="t">${clock()}</span> <span class="${cls}">${txt}</span></div>`);
    c.scrollTop=c.scrollHeight;
  };
  log('幕僚團啟動 · 案號 '+($('#f_no').value||'—'),'g');
  if(isLive)log('資料來源：後端六節點執行結果 run_id '+((LIVE&&LIVE.run_id)||'—')+'（前端不重算燈號）','g');
  else log('資料來源：離線 fixture（未接後端）','y');

  AGENTS.forEach((a,i)=>{
    const t0=400+i*1400;
    setTimeout(()=>{
      const el=$('#ag-'+a.k);
      el.dataset.s='run';
      el.querySelector('.out').textContent='分析中…';
      el.querySelector('.st').innerHTML='<span class="spin"></span>執行中';
      el.querySelector('.bar-in').style.width='18%';
      log(`▶ ${a.name} 開始作業`);
      (a.logs||[]).forEach((l,j)=>setTimeout(()=>{
        log('　　'+fillTokens(l[0]),l[1]);
        el.querySelector('.bar-in').style.width=(30+j*22)+'%';
      },260+j*300));
      /* 先跑到 100%，等進度條動畫真的結束才切成「完成」 */
      setTimeout(()=>{
        const bar=el.querySelector('.bar-in');
        bar.style.width='100%';
        let fired=false;
        const finish=()=>{
          if(fired)return; fired=true;
          el.dataset.s='done';
          el.querySelector('.out').textContent=fillTokens(a.out);
          el.querySelector('.st').innerHTML='✓ 完成';
          done++; $('#agprog').textContent=done+' / '+AGENTS.length;
          if(done===AGENTS.length){
            log('全部幕僚作業完成，草稿與查核清單已產出','g');
            $('#go2').disabled=false;
            toast('幕僚團分析完成');
            buildDraft(); buildRefs();
          }
        };
        bar.addEventListener('transitionend',finish,{once:true});
        setTimeout(finish,700);   /* reduced-motion 下不會有 transitionend，保底 */
      },950);
    },t0);
  });
}
$('#go2').onclick=()=>goto(2);

/* 幕僚敘述裡的數字一律回填實際結果，避免看板說「紅燈 2」而審核頁顯示 4 這種自相矛盾。
   live 模式的敘述由後端節點填好數字送來，這裡不會有 {TOKEN} 可替換，等同 no-op。 */
function fillTokens(str){
  const n=l=>SENTS.filter(s=>s.l===l).length;
  const svc=$('#f_d2').value, fil=$('#f_d3').value;
  const eng=SENTS.find(s=>s.engine==='deadline')||{};
  const days=(svc&&fil)?Math.round((Date.parse(fil+'T12:00:00Z')-Date.parse(svc+'T12:00:00Z'))/86400000):'—';
  const map={
    TOTAL:SENTS.length, RED:n('r'), AMBER:n('y'), GREEN:n('g'),
    CHARS:SENTS.reduce((a,s)=>a+s.t.replace(/\s/g,'').length,0).toLocaleString('en-US'),
    D2:svc?rocDate(svc):'—', D3:fil?rocDate(fil):'—', DAYS:days,
    PROC:eng.l==='g'?'未逾 30 日法定期間，應為實體審查'
        :'期間查核未通過，第 77 條第 2 款不受理事由待人工裁量',
  };
  return String(str).replace(/\{(\w+)\}/g,(m,k)=>k in map?map[k]:m);
}

/* ================= 文件模型：以「句」為單位 ================= */
/* lamp: g 可被計算 / y 有證據 / r 需人工審核　refs 對應左欄卡片 id */
let DOC=CASE.doc||[];
const SENTS=[];
/* live 模式改日期會由後端重算期間，整段期間計算的句子會換掉（步數可能從 6 變 5），
   所以句子清單要能重建。**原地改陣列內容**，不重新指派——所有 closure 都抓著同一個參考。 */
function rebuildSents(){
  SENTS.length=0;
  DOC.forEach(bk=>(bk.ss||[]).forEach(s=>{
    if(s.orig===undefined)s.orig=s.t;
    if(s.baseL===undefined)s.baseL=s.l;
    s.refs=s.refs||[];
    SENTS.push(s);
  }));
}
rebuildSents();
/* 期間句的 why 由後端給。重算時沿用同一句說明，不由前端另外寫一句。 */
const ENGINE_WHY=(DOC.flatMap(b=>b.ss||[]).find(s=>s.engine==='deadline')||{}).why
  ||'期間由日期規則直接驗算，攤開算式可逐步覆核，無詮釋空間。';
const byId=id=>SENTS.find(s=>s.id===id);
const sentNo=id=>{const s=byId(id);return s?SENTS.indexOf(s)+1:null};
const LAMPNAME={r:'紅燈　需人工審核',y:'黃燈　有證據',g:'綠燈　可被計算'};

/* 對抗測資標記：合成案例刻意注入的錯誤引用，畫面上一定要看得出來，
   不然這句讀起來就像系統對法律的認知（HANDOFF 第二節 ⚠1）。 */
const advMark=s=>s.adversarial
  ?`<span class="advmark" tabindex="0" data-tip="${esc(s.adversarial_note||'刻意注入的對抗測資')}">⚠ 對抗測資</span>`:'';
const advNote=s=>s.adversarial
  ?`<div class="advnote"><b>⚠ 對抗測資</b>：${esc(s.adversarial_note||'本句為刻意注入的錯誤引用，用來驗證守門會攔下。')}</div>`:'';

/* ================= 規則引擎：訴願期間實算（零 LLM 依賴） ================= */
/* 綠燈句宣稱「可被計算」，就得真的算：離線模式呼叫 engine.js 的 computeDeadline，
   該引擎與 engine/deadline.py 由 data/test-vectors.json 鎖定零分歧（tests/parity.mjs）。
   收文頁改日期 → 這一句與其燈號跟著變；逾期時燈號轉紅並點出與駁回主文的矛盾。 */
const rocDate=iso=>{const[y,m,d]=iso.split('-').map(Number);return `${y-1911}年${m}月${d}日`;};
function applyDeadlineEngine(){
  const tgt=SENTS.filter(s=>s.engine==='deadline');
  if(!tgt.length)return;
  const svc=$('#f_d2').value, fil=$('#f_d3').value;
  if(!svc||!fil){
    tgt.forEach(s=>{s.l=s.baseL='r';s.steps=null;s.caveats=null;
      s.why='合法送達日期或提起訴願日期未填，法定期間無從驗算，請承辦人補列後重跑。';
      s.src='規則驗算：輸入不足，未計算';});
    return;
  }
  const r=computeDeadline({method:CASE.intake.service_method||'personal',service:svc,filing:fil});
  const days=Math.round((Date.parse(fil+'T12:00:00Z')-Date.parse(svc+'T12:00:00Z'))/86400000);
  tgt.forEach(s=>{
    s.steps=r.steps; s.caveats=r.caveats;
    if(r.overdue){
      s.t=`五、另本件原處分之送達日期為${rocDate(svc)}，訴願人於${rocDate(fil)}提起訴願，計${days}日，已逾訴願法第14條所定30日之法定期間（期間至${rocDate(r.deadline)}屆滿）。`;
      s.l='r';
      s.why=`規則引擎判定逾期：期滿日 ${r.deadline}，提起日 ${fil}。逾期為訴願法第77條第2款之不受理事由，與本稿「訴願駁回」之主文相互矛盾；是否改列不受理、或依訴願法第80條職權處理，屬承辦人裁量，系統不代為決定。`;
    }else{
      s.t=`五、另本件原處分之送達日期為${rocDate(svc)}，訴願人於${rocDate(fil)}提起訴願，計${days}日，未逾訴願法第14條所定30日之法定期間，程序上並無不合。`;
      s.l='g';
      s.why='本句之期間由規則引擎逐步驗算產出，非模型生成，無詮釋空間；可展開算式逐步對照法條依據。';
    }
    s.baseL=s.l; s.orig=s.t;
    s.src=`規則驗算：${svc} → ${fil} ＝ ${days} 日；法定期間至 ${r.deadline} 屆滿`;
  });
}
/* live 模式：算式來自後端 N3 的期間引擎（adaptPayload 已掛上 steps／caveats）。
   **句子文字與燈號一個字都不動**——那是後端守門的判定，前端重算就是製造第二個真相。 */
function applyLiveDeadline(){
  const dl=(LIVE&&LIVE.screen&&LIVE.screen.deadline)||{};
  SENTS.filter(s=>s.engine==='deadline').forEach(s=>{
    if(!s.steps&&dl.steps)s.steps=dl.steps;
    if(!s.caveats&&dl.caveats)s.caveats=dl.caveats;
  });
}
/* ================= live 模式：改日期 → 後端重算期間 ================= */
/* PR #2 的賣點是「收文頁改日期，決定書那句與燈號跟著變」。整合後不能讓它消失，
   但也不能讓前端自己判斷逾期——燈號不准是前端產出（CONSTITUTION §1）。
   做法：打 `POST /api/deadline`，**算式、燈號、句子文字全部用後端回的**，
   前端只負責把它畫出來，以及誠實標明「只有期間這一段重算過，其餘仍是原始執行結果」。 */
let recalcSeq=0, recalcTimer=null;

function setRecalcNote(kind,msg){
  const el=$('#recalcnote'); if(!el)return;
  el.style.display=msg?'':'none';
  el.innerHTML=msg||'';
  el.style.color=kind==='error'?'var(--seal)':'var(--ink-3)';
}

function calcBlock(){
  return DOC.find(bk=>(bk.ss||[]).some(s=>s.engine==='deadline'));
}

function applyDeadlineRecompute(r,svc,fil){
  const bk=calcBlock();
  if(!bk){setRecalcNote('error','找不到期間計算段，無法套用重算結果。');return false;}
  const v=r.verdict||{};
  const steps=r.steps||[];
  bk.ss=steps.map((st,i)=>({
    id:'d'+(i+1), t:`${st.rule}：${st.value}`,
    origin:'engine', slot:'calculation', engine:'deadline',
    basis:st.basis, src:st.basis, l:'g', baseL:'g', why:ENGINE_WHY,
    refs:[], citations:[], placeholder:false, adversarial:false,
    steps:steps, caveats:r.caveats||[], recomputed:true
  }));
  /* 期間判定句：燈號、文字、理由全部來自後端 verdict，前端一個字都沒改寫 */
  if(v.text){
    bk.ss.push({
      id:'dv', t:v.text, origin:'engine', slot:'calculation', engine:'deadline',
      basis:v.basis, src:v.basis, l:v.lamp||'g', baseL:v.lamp||'g', why:v.why||'',
      refs:[], citations:[], placeholder:false, adversarial:false,
      steps:steps, caveats:r.caveats||[], recomputed:true, verdict:true
    });
  }
  rebuildSents();
  buildDraft(); buildRefs();
  if($('#rvinner').children.length)buildReview();
  /* 程序審查官卡片跟著更新，不然看板還停在原始那次的期滿日 */
  const ag=$('#ag-proc');
  if(ag&&ag.querySelector('.out'))
    ag.querySelector('.out').textContent=
      `期間已依修改後日期重算：期滿日 ${r.deadline||'—'}。${v.text||''}`;
  setRecalcNote('ok',
    `期間已由後端重算（<code>POST /api/deadline</code>）：`+
    `送達 ${esc(svc)} → 提起 ${esc(fil||'—')}，期滿日 <b>${esc(r.deadline||'—')}</b>，`+
    `本段 ${steps.length} 步算式與燈號皆為後端回傳。`+
    `<br><b>只有期間這一段重算過</b>——案型、檢索、引用查核、送出許可仍是原始那次執行的結果。`);
  toast('期間已依修改後日期重算');
  return true;
}

async function recomputeDeadlineLive(){
  if(!isLive)return;
  const svc=$('#f_d2').value, fil=$('#f_d3').value;
  if(!svc){setRecalcNote('error','合法送達日期未填，期間無從計算（系統不猜）。');return;}
  const K=CASE.intake||{};
  const seq=++recalcSeq;
  setRecalcNote('ok','重算中…');
  let r;
  try{
    const res=await fetch('api/deadline',{
      method:'POST',
      headers:{'Content-Type':'application/json','Accept':'application/json'},
      body:JSON.stringify({
        method:$('#f_sm').value||K.service_method||'personal', service:svc, filing:fil||null,
        transit:parseInt($('#f_transit').value,10)||0,
        interested:$('#f_interested').checked})});
    if(!res.ok)throw new Error('HTTP '+res.status);
    r=await res.json();
  }catch(e){
    setRecalcNote('error','期間重算失敗（'+esc(String(e&&e.message||e))+'）。畫面仍顯示原始執行結果，未自行改算。');
    return;
  }
  if(seq!==recalcSeq)return;   /* 舊請求慢回來就丟掉，不要蓋掉新的結果 */
  applyDeadlineRecompute(r,svc,fil);
}

if(isLive){
  ['#f_d2','#f_d3'].forEach(sel=>$(sel).addEventListener('change',()=>{
    clearTimeout(recalcTimer);
    recalcTimer=setTimeout(recomputeDeadlineLive,250);
  }));
}

/* 算式卡：把引擎每一步攤開，附法條依據（分層誠實第一級） */
function stepsHTML(s){
  if(!s.steps||!s.steps.length)return '';
  const steps=s.steps.map(x=>`<li><b>${esc(x.rule)}</b><span class="basis">${esc(x.basis)}</span><span class="val">${esc(x.value)}</span></li>`).join('');
  const cav=(s.caveats||[]).map(c=>`<p>⚠ ${esc(c)}</p>`).join('');
  return `<details class="calc"><summary>展開算式（${s.steps.length} 步・規則引擎驗算）</summary>
    <ol>${steps}</ol>${cav?`<div class="cav">${cav}</div>`:''}</details>`;
}

/* ================= 引用可驗：法條對照離線快照 ================= */
/* 「在庫」僅代表條號存在於本 prototype 之離線法規快照，
   「庫外」不等於引用錯誤，代表本庫無法驗證、需人工查證。
   **只在離線模式用**：live 模式的引用四態一律由後端守門節點判定（CONSTITUTION §2）。 */
function verifyLaw(title){
  const m=title.match(/^([\u4e00-\u9fa5]+法)/);
  if(!m)return null;
  const law=SNAP.laws[m[1]];
  if(!law)return{ok:false,text:`庫外・${m[1]}`,tip:`本 prototype 之法規快照未收錄${m[1]}，無法驗證條號，請人工查證。`};
  const arts=[...title.matchAll(/第\s*(\d+(?:之\d+)?)\s*條/g)].map(x=>x[1]);
  if(!arts.length)return null;
  const miss=arts.filter(a=>!law.articles.includes(a));
  return miss.length
    ? {ok:false,text:`庫外・查無第 ${miss.join('、')} 條`,tip:`${m[1]}快照（共 ${law.max} 條）查無此條號，請人工查證是否為舊條次或誤植。`}
    : {ok:true,text:`在庫 ✓ 第 ${arts.join('、')} 條`,tip:`已對照法規快照（${SNAP.generated}，${m[1]}共 ${law.max} 條）確認條號存在。「在庫」僅代表條號存在於離線快照，不代表適用射程正確。`};
}

/* ================= 左欄參考資料 ================= */
let LAWS=CASE.laws||[], CASES=CASE.cases||[], ISSUES=CASE.issues||[];
let ALLREFS=[...LAWS,...CASES,...ISSUES];
const refTab=id=>id[0]==='L'?'law':id[0]==='C'?'case':'issue';

function refHTML(o){
  const lamp=o.lamp?`<span class="tag ${o.lamp}">${LAMPNAME[o.lamp].split('　')[0]}</span>`:'';
  const sim=o.sim!=null?`<span class="sim"><span class="simbar"><i style="width:${o.sim}%"></i></span>相似度 ${o.sim}%</span>`:'';
  const users=SENTS.filter(s=>s.refs.includes(o.id)).length;
  /* live 模式不重算引用狀態：直接用後端給的 tag（✓ 在庫／✗ 查無此號／庫外…）與 note */
  const v=(!isLive&&o.id[0]==='L')?verifyLaw(o.t):null;
  const vf=v?`<span class="tag ${v.ok?'g':'y'}" tabindex="0" data-tip="${esc(v.tip)}">${esc(v.text)}</span>`:'';
  /* 法規卡的狀態徽章。獨立檢索後有三種：
     - cited_and_gated：草稿有引用、守門查核過 → 用守門發的燈號顏色
     - retrieved_not_cited：檢索到、草稿沒引用 → **中性徽章，不是紅黃綠**
       （燈號只屬於草稿裡的句子與引用，這張卡沒有可以發燈的對象）
     - unkeyed：組不出穩定鍵 → 這是缺陷，標紅 */
  const gs=o.gate_status;
  const tagCls=gs==='retrieved_not_cited'?'neutral'
              :gs==='unkeyed'?'r'
              :(isLive&&o.lamp)?o.lamp:'k';
  const tagTip=(isLive&&(o.gate_note||o.note))?` tabindex="0" data-tip="${esc(o.gate_note||o.note)}"`:'';
  return `<div class="ref" id="ref-${esc(o.id)}" data-ref="${esc(o.id)}">
    <div class="top"><span class="nm">${esc(o.t)}</span>${lamp}</div>
    ${o.q?`<q>${esc(o.q)}</q>`:''}${o.q_note&&!o.q?`<div style="font-size:11.5px;color:var(--ink-3);margin:4px 0 6px;line-height:1.65">${esc(o.q_note)}</div>`:''}${o.d?`<div style="font-size:12.5px;color:var(--ink-2);margin:4px 0 6px;line-height:1.65">${esc(o.d)}</div>`:''}
    <div class="foot">
      ${o.tag?`<span class="tag ${tagCls}"${tagTip}>${esc(o.tag)}</span>`:''}${vf}${sim}
      ${users?`<span class="tag n">本稿引用 ${users} 句</span>`:''}
      <span class="gap"></span><span>${esc(o.src||'')}</span>
    </div>
  </div>`;
}
/* 「草稿實際引用」清單（§6.2 citations[]：步驟2 側欄可列全案引用清單）。
   N4 改成獨立檢索之後，左欄的法規卡是「案情查出來的」，跟「草稿實際引用的」是兩份清單。
   只顯示前者的話，草稿引用了什麼、查核結果如何就整個看不到了——對抗案例那條假法條
   也會從左欄消失。兩份都列出來，並把差集講清楚。 */
function citationsHTML(){
  if(!isLive||!LIVE)return '';
  const cs=LIVE.citations||[], div=LIVE.retrieval_divergence||{};
  const seen=new Set(), rows=[];
  cs.forEach(c=>{
    if(seen.has(c.raw))return; seen.add(c.raw);
    const users=SENTS.filter(s=>(s.citations||[]).some(x=>x.raw===c.raw));
    rows.push(`<div class="cite-row" data-cite="${esc(c.raw)}">
      <span class="tag ${esc(c.lamp||'y')}">${esc(c.mark||c.state)}</span>
      <span class="nm">${esc(c.raw)}</span>
      ${users.length?`<span class="tag n">第 ${users.map(s=>SENTS.indexOf(s)+1).join('、')} 句</span>`:''}
      <div class="note">${esc(c.note||'')}</div></div>`);
  });
  const cnr=div.cited_not_retrieved||[], rnc=div.retrieved_not_cited||[];
  const divHTML=(cnr.length||rnc.length)?`<div class="diverge">
      <b>兩份清單的落差</b>
      ${cnr.length?`<div>草稿引用、獨立檢索未命中：${cnr.map(esc).join('、')}</div>`:''}
      ${rnc.length?`<div>獨立檢索命中、草稿未引用：${rnc.map(esc).join('、')}</div>`:''}
      <div class="why">${esc(div.note||'')}</div></div>`:'';
  return `<div class="refsec"><h4>草稿實際引用（守門逐句查核）</h4>
    ${rows.join('')||'<span style="font-size:12.5px;color:var(--ink-3)">草稿未附任何引用</span>'}
    ${divHTML}</div>`;
}

function buildRefs(){
  $('#tp-law').innerHTML=
    (isLive?'<div class="refsec-h">獨立檢索結果（查詢句由案情組成，未參考草稿）</div>':'')+
    (LAWS.map(refHTML).join('')||'<span style="font-size:12.5px;color:var(--ink-3)">本次執行無法規檢索結果</span>')
    +citationsHTML();
  $('#tp-case').innerHTML=CASES.map(refHTML).join('')
    ||'<span style="font-size:12.5px;color:var(--ink-3)">相似歷史案通道不可用（無資料集），本次回空並非「查無相似案」</span>';
  $('#tp-issue').innerHTML=ISSUES.map(refHTML).join('')||'<span style="font-size:12.5px;color:var(--ink-3)">未偵測到事實認定爭點</span>';
  /* 分頁上的數字跟著實際資料走，不寫死 */
  const nCite=(isLive&&LIVE)?new Set((LIVE.citations||[]).map(c=>c.raw)).size:0;
  const counts={law:isLive?`${LAWS.length}+${nCite}`:LAWS.length,case:CASES.length,issue:ISSUES.length};
  $$('.tab').forEach(b=>{const bd=b.querySelector('.badge');if(bd)bd.textContent=counts[b.dataset.t]});
}
$$('.tab').forEach(b=>b.onclick=()=>{
  $$('.tab').forEach(x=>x.setAttribute('aria-selected',x===b));
  $$('.tabpane').forEach(p=>p.classList.toggle('on',p.id==='tp-'+b.dataset.t));
});
function openTab(t){$$('.tab').forEach(x=>{const on=x.dataset.t===t;x.setAttribute('aria-selected',on)});$$('.tabpane').forEach(p=>p.classList.toggle('on',p.id==='tp-'+t));}

/* ================= 草稿：逐句可編輯 ================= */
function buildDraft(){
  const sh=$('#sheet'); sh.innerHTML='';
  DOC.forEach(bk=>{
    if(bk.ty==='title'){sh.insertAdjacentHTML('beforeend',`<div class="doc-title">${esc(bk.text||'訴願決定書')}</div>`);return;}
    if(bk.ty==='meta'){sh.insertAdjacentHTML('beforeend',`<p class="meta">${esc(bk.text)}</p>`);return;}
    if(bk.ty==='h'){sh.insertAdjacentHTML('beforeend',`<h4>${esc(bk.text)}</h4>`);return;}
    if(!bk.ss||!bk.ss.length)return;
    const inner=bk.ss.map(s=>`<span class="sent" contenteditable="${s.placeholder?'false':'true'}" spellcheck="false" data-id="${esc(s.id)}" data-l="${esc(s.l)}">${esc(s.t)}</span>`).join('');
    sh.insertAdjacentHTML('beforeend',`<p class="${bk.ind?'ind':''}">${inner}</p>`);
  });
  $('#senttotal').textContent=SENTS.length;
  updateWC();
}
function updateWC(){
  $('#wc').textContent=SENTS.reduce((n,s)=>n+s.t.replace(/\s/g,'').length,0)+' 字';
  const ed=SENTS.filter(s=>s.edited).length;
  const e=$('#editcount'); e.style.display=ed?'':'none'; e.textContent=ed+' 句已修改';
}
let selId=null;
function selectSent(id,{scrollRef=true}={}){
  selId=id; const s=byId(id); if(!s)return;
  $$('#sheet .sent').forEach(el=>el.classList.toggle('sel',el.dataset.id===id));
  $('#sentpos').textContent=SENTS.indexOf(s)+1;
  const lampTag=`<span class="tag ${s.l}">${LAMPNAME[s.l]}</span>`;
  const phTag=s.placeholder?'<span class="tag r">佔位・系統不生成</span>':'';
  const links=s.refs.length
    ? `<div class="lk">${s.refs.map(r=>{const o=ALLREFS.find(x=>x.id===r);return `<button data-goref="${esc(r)}" title="${esc(o?o.t:r)}">${esc(o?o.t:r)}</button>`}).join('')}</div>`
    : '<div class="lk"><span style="color:var(--ink-3)">無外部依據（本句以卷證或規則驗算為準）</span></div>';
  $('#inspect').innerHTML=`<div class="ln"><span class="k">第 ${SENTS.indexOf(s)+1} 句</span>${lampTag}${phTag}${advMark(s)}${s.edited?'<span class="tag r">已改寫</span>':''}</div>${advNote(s)}<span class="why">${esc(s.why)}</span>${links}${stepsHTML(s)}`;
  $$('.ref').forEach(el=>{
    const hit=s.refs.includes(el.dataset.ref);
    el.classList.toggle('hit',hit);
    el.classList.toggle('dim',s.refs.length>0&&!hit);
  });
  if(scrollRef&&s.refs.length){
    openTab(refTab(s.refs[0]));
    const el=$('#ref-'+s.refs[0]);
    if(el)el.scrollIntoView({block:'nearest',behavior:'smooth'});
  }
}
$('#sheet').addEventListener('click',e=>{const el=e.target.closest('.sent');if(el)selectSent(el.dataset.id)});
$('#sheet').addEventListener('focusin',e=>{const el=e.target.closest('.sent');if(el&&el.dataset.id!==selId)selectSent(el.dataset.id)});
$('#sheet').addEventListener('input',e=>{
  const el=e.target.closest('.sent'); if(!el)return;
  const s=byId(el.dataset.id); if(!s)return;
  s.t=el.textContent;
  const changed=s.t.trim()!==s.orig.trim();
  if(changed&&!s.edited){
    s.edited=true; s.l='r'; s.ack=false;
    s.why='本句經承辦人改寫，原燈號（'+LAMPNAME[s.baseL]+'）已失效，須重新核閱後確認。';
    el.dataset.l='r'; el.classList.add('edited');
    toast('本句已改寫，燈號轉為紅燈待複核');
  }else if(!changed&&s.edited){
    s.edited=false; s.l=s.baseL; s.ack=false;
    el.dataset.l=s.baseL; el.classList.remove('edited');
  }
  updateWC();
  if(selId===s.id)selectSent(s.id,{scrollRef:false});
});
/* 反查：點左側卡片 → 標出引用它的句子 */
document.addEventListener('click',e=>{
  const g=e.target.closest('[data-goref]');
  if(g){const el=$('#ref-'+g.dataset.goref);if(el){openTab(refTab(g.dataset.goref));el.scrollIntoView({block:'nearest',behavior:'smooth'});}return;}
  const c=e.target.closest('.ref');
  if(c&&c.dataset.ref){
    const users=SENTS.filter(s=>s.refs.includes(c.dataset.ref));
    $$('#sheet .sent').forEach(el=>el.classList.remove('flash'));
    users.forEach(s=>{const el=$(`#sheet .sent[data-id="${s.id}"]`);if(el)el.classList.add('flash')});
    if(users[0]){const el=$(`#sheet .sent[data-id="${users[0].id}"]`);el&&el.scrollIntoView({block:'center',behavior:'smooth'});}
    $('#reffoot').innerHTML=`<div class="ln"><span class="k">反查</span></div><span class="why">本依據被草稿引用 <b>${users.length}</b> 句${users.length?'，已於右側標示為黃底':'（尚未被引用）'}。</span>`;
  }
});
/* 分割拖曳 */
(function(){
  const sp=$('#split'), gr=$('#grip'); let drag=false;
  const set=px=>{const r=sp.getBoundingClientRect();let p=(px-r.left)/r.width;p=Math.min(.72,Math.max(.28,p));sp.style.gridTemplateColumns=`${p}fr 6px ${1-p}fr`;};
  gr.addEventListener('mousedown',()=>{drag=true;document.body.style.cursor='col-resize';document.body.style.userSelect='none'});
  window.addEventListener('mousemove',e=>{if(drag)set(e.clientX)});
  window.addEventListener('mouseup',()=>{drag=false;document.body.style.cursor='';document.body.style.userSelect=''});
  gr.addEventListener('keydown',e=>{
    const r=sp.getBoundingClientRect();
    if(e.key==='ArrowLeft')set(r.left+r.width*.4);
    if(e.key==='ArrowRight')set(r.left+r.width*.6);
  });
})();
$('#go3').onclick=()=>{buildReview();goto(3)};

/* ================= 4 燈號：全文逐句預覽 ================= */
function buildReview(){
  const box=$('#rvinner'); box.innerHTML='<div class="stamp">新北市政府<br>訴願決定</div>';
  DOC.forEach(bk=>{
    if(bk.ty==='title'){box.insertAdjacentHTML('beforeend',`<div class="doc-title">${esc(bk.text||'訴願決定書')}</div>`);return;}
    if(bk.ty==='meta'){box.insertAdjacentHTML('beforeend',`<p class="meta">${esc(bk.text)}</p>`);return;}
    if(bk.ty==='h'){box.insertAdjacentHTML('beforeend',`<h4>${esc(bk.text)}</h4>`);return;}
    if(!bk.ss||!bk.ss.length)return;
    const inner=bk.ss.map(s=>
      `<span class="rs" data-id="${esc(s.id)}" data-l="${esc(s.l)}" data-ack="${s.ack?1:0}" tabindex="0">${esc(s.t)}</span>${advMark(s)}`
    ).join('');
    box.insertAdjacentHTML('beforeend',`<p class="${bk.ind?'ind':''}">${inner}</p>`);
  });
  refreshCounts(); renderRedList(); renderHandoff(); refreshGate();
}
function refreshCounts(){
  $('#n_r').textContent=SENTS.filter(s=>s.l==='r').length;
  $('#n_y').textContent=SENTS.filter(s=>s.l==='y').length;
  $('#n_g').textContent=SENTS.filter(s=>s.l==='g').length;
  $('#n_t').textContent=SENTS.length;
}
function renderRedList(){
  const reds=SENTS.filter(s=>s.l==='r');
  const pend=reds.filter(s=>!s.ack).length;
  $('#redprog').textContent=(reds.length-pend)+' / '+reds.length;
  const ab=$('#ackall'); if(ab){ab.disabled=!pend;ab.textContent=pend?`一鍵確認（${pend}）`:'紅燈均已確認'}
  $('#redlist').innerHTML=reds.map(s=>{
    const i=SENTS.indexOf(s)+1;
    return `<button class="btn ghost sm" data-red="${esc(s.id)}" style="text-align:left;display:flex;gap:7px;align-items:center">
      <span style="width:8px;height:8px;border-radius:50%;background:${s.ack?'#3CBB73':'#E5484D'};flex:none"></span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">第 ${i} 句　${esc(s.t.slice(0,14))}…</span>
      <span style="color:${s.ack?'var(--green)':'var(--seal)'}">${s.ack?'已確認':'待核'}</span></button>`;
  }).join('')||'<span style="font-size:12.5px;color:var(--ink-3)">無紅燈句</span>';
}
/* 交接卡（US-8 AC-8.2）：問題與訊號一律取自後端 N6，前端不生成任何一題 */
function renderHandoff(){
  const card=$('#handoffcard'); if(!card)return;
  const ho=(isLive&&LIVE&&LIVE.handoff)||{};
  const qs=ho.questions||[], cr=ho.criterion||null, obs=ho.observations||[];
  if(!qs.length&&!(cr&&cr.blocked)){card.style.display='none';return;}
  card.style.display='';
  /* 封鎖判準與「另外偵測到的」分開講。
     覆核實測：把對抗案例的事實爭點全部拿掉，結論段仍然封鎖——真正的判準是
     「程序審查通過且須進入實體審查」。兩者並排列出會讀成「兩個都是原因」，那是不實的。
     criterion / observations / observations_label 全部由後端算好，前端不分類、不改寫。 */
  $('#handoffbody').innerHTML=
    (cr&&cr.blocked?`<div class="crit"><b>封鎖判準</b>${esc(cr.text)}</div>`:'')+
    (obs.length?`<div class="obs-h">${esc(ho.observations_label||'另外偵測到（提醒）')}</div>
       <div class="handoff-s">${obs.map(x=>`<span>▸ ${esc(x)}</span>`).join('')}</div>`:'')+
    (qs.length?`<div class="obs-h">請承辦人核對卷證後自行認定</div>
       <ol class="handoff-q">${qs.map(q=>`<li>${esc(q)}</li>`).join('')}</ol>`:'');
}
/* 送出閘門的阻擋原因（§6.2 blockers[]）：不能只 disable 按鈕，要說為什麼擋 */
function renderBlockers(){
  const box=$('#blockerlist'); if(!box)return;
  const bs=(isLive&&LIVE&&LIVE.blockers)||[];
  if(!bs.length){box.style.display='none';box.innerHTML='';return;}
  box.style.display='';
  box.innerHTML=`<div class="blockers">${bs.map(b=>{
    const no=b.sentence_id?sentNo(b.sentence_id):null;
    return `<div class="bk"><b>[${esc(b.reason)}]</b>${no?' 第 '+no+' 句':''}　${esc(b.detail||'')}</div>`;
  }).join('')}</div>`;
}
function showDetail(id){
  const s=byId(id); if(!s){console.warn('unknown sentence',id);return;}
  $$('.rs').forEach(el=>el.classList.toggle('sel',el.dataset.id===id));
  const i=SENTS.indexOf(s)+1;
  const refs=s.refs.map(r=>{const o=ALLREFS.find(x=>x.id===r);return o?`<div style="font-size:12px;color:var(--navy-l);margin-bottom:3px">▸ ${esc(o.t)}</div>`:''}).join('');
  $('#detbody').innerHTML=`
    <div style="display:flex;gap:7px;align-items:center;margin-bottom:9px;flex-wrap:wrap">
      <span class="tag ${s.l}">${LAMPNAME[s.l]}</span>
      <span style="font-family:var(--mono);font-size:11.5px;color:var(--ink-3)">第 ${i} / ${SENTS.length} 句</span>
      ${s.placeholder?'<span class="tag r">佔位・系統不生成</span>':''}
      ${advMark(s)}
      ${s.edited?'<span class="tag r">已人工改寫</span>':''}
    </div>
    ${advNote(s)}
    <p class="q">${esc(s.t)}</p>
    <p class="why">${esc(s.why)}</p>
    ${refs?`<div style="margin-bottom:9px">${refs}</div>`:''}
    <div class="src">${esc(s.src||'—')}</div>
    ${stepsHTML(s)}
    ${s.l==='r'?(s.ack?'<span class="ack">✓ 已核閱確認</span>':`<button class="btn sm" data-ackid="${esc(s.id)}">我已核閱卷證，確認本句</button>`):''}`;
}
$('#rvdoc').addEventListener('click',e=>{const el=e.target.closest('.rs');if(el)showDetail(el.dataset.id)});
$('#rvdoc').addEventListener('keydown',e=>{if(e.key==='Enter'){const el=e.target.closest('.rs');if(el)showDetail(el.dataset.id)}});
document.addEventListener('click',e=>{
  const r=e.target.closest('[data-red]');
  if(r){const el=$(`.rs[data-id="${r.dataset.red}"]`);if(el){el.scrollIntoView({block:'center',behavior:'smooth'});showDetail(r.dataset.red);}return;}
  const a=e.target.closest('[data-ackid]');
  if(a){
    const s=byId(a.dataset.ackid); if(!s)return;
    s.ack=true;
    const el=$(`.rs[data-id="${s.id}"]`); if(el)el.dataset.ack='1';
    showDetail(s.id); renderRedList(); refreshGate();
    toast('第 '+(SENTS.indexOf(s)+1)+' 句已確認');
  }
});

/* ---- 純文預覽切換 ---- */
function toggleClean(target,btn){
  const on=target.classList.toggle('clean');
  if(target.id==='sheet')$$('#sheet .sent').forEach(el=>el.contentEditable=on?'false':'true');
  btn.innerHTML=`<span class="mi" style="font-size:15px;vertical-align:-3px">${on?'edit':'visibility'}</span> ${on?(target.id==='sheet'?'回到編輯':'顯示燈號'):'純文預覽'}`;
}
$('#cleanview').onclick=()=>toggleClean($('#sheet'),$('#cleanview'));
$('#cleanview2').onclick=()=>toggleClean($('#rvdoc'),$('#cleanview2'));

$$('[data-lf]').forEach(b=>b.onclick=()=>{
  const f=b.dataset.lf, doc=$('#rvdoc');
  const already=b.getAttribute('aria-pressed')==='true';
  const next=(already||f==='all')?'all':f;
  doc.classList.remove('f-r','f-y','f-g');
  if(next!=='all')doc.classList.add('f-'+next);
  if(doc.classList.contains('clean'))toggleClean(doc,$('#cleanview2'));
  $$('[data-lf]').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.lf===next)));
});
function refreshGate(){
  renderBlockers();
  const reds=SENTS.filter(s=>s.l==='r'), left=reds.filter(s=>!s.ack).length;
  /* live 模式多一道：後端守門的 submit_allowed。紅燈全確認也不能繞過它。 */
  const gateBlocked=isLive&&LIVE&&LIVE.submit_allowed===false;
  const nBlockers=(isLive&&LIVE&&LIVE.blockers)?LIVE.blockers.length:0;
  $('#go4').disabled=left>0||gateBlocked;
  if(gateBlocked){
    $('#gatetitle').textContent=`後端守門阻擋送出（${nBlockers} 項）`;
    $('#gatemsg').textContent='本案未通過品管守門節點。按下送出時後端會重新判斷一次並以 409 拒絕；'+
      '阻擋原因如下，須先由承辦人處理。紅燈逐句確認不會解除它。';
  }else if(left>0){
    $('#gatetitle').textContent=`尚有 ${left} 句紅燈待確認`;
    $('#gatemsg').textContent='紅燈涉及事實認定、裁量或人工改寫，須由承辦人核閱卷證後逐句確認，系統不代為判斷。';
  }else{
    $('#gatetitle').textContent='紅燈句均已確認';
    $('#gatemsg').textContent=`黃燈 ${SENTS.filter(s=>s.l==='y').length} 句已附來源、綠燈 ${SENTS.filter(s=>s.l==='g').length} 句已通過規則驗算，可送出審議。`;
  }
}
/* 一鍵確認全部紅燈（含防呆 dialog） */
const mask=$('#mask');
function openAckAll(){
  const pend=SENTS.filter(s=>s.l==='r'&&!s.ack);
  if(!pend.length){toast('目前沒有待確認的紅燈句');return;}
  $('#m_n').textContent=pend.length;
  $('#m_list').innerHTML=pend.map(s=>`<li>第 ${SENTS.indexOf(s)+1} 句　${esc(s.t.slice(0,24))}…</li>`).join('');
  $('#m_chk').checked=false; $('#m_ok').disabled=true;
  mask.classList.add('on'); setTimeout(()=>$('#m_chk').focus(),80);
}
function closeAckAll(){mask.classList.remove('on')}
$('#ackall').onclick=openAckAll;
$('#m_chk').onchange=e=>$('#m_ok').disabled=!e.target.checked;
$('#m_cancel').onclick=closeAckAll;
mask.addEventListener('click',e=>{if(e.target===mask)closeAckAll()});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&mask.classList.contains('on'))closeAckAll()});
$('#m_ok').onclick=()=>{
  const pend=SENTS.filter(s=>s.l==='r'&&!s.ack);
  pend.forEach(s=>{s.ack=true;const el=$(`.rs[data-id="${s.id}"]`);if(el)el.dataset.ack='1'});
  closeAckAll(); renderRedList(); refreshGate();
  if(selId)showDetail(selId);
  toast(`已確認 ${pend.length} 句紅燈`);
};

/* 送出審議：live 模式一律打後端 POST /submit，**由後端重新判斷一次**。
   前端這顆按鈕的 disabled 只是提示；改 DOM 或直接打 API 都繞得過它，
   真正的守門在後端（409）。這也是「已標記為不得逕行送出」能改口的前提。 */
async function submitToBackend(){
  const btn=$('#go4');
  btn.disabled=true;
  const prev=$('#gatemsg').textContent;
  $('#gatemsg').textContent='送出中：後端重新判斷可否送出…';
  try{
    const res=await fetch('api/cases/'+encodeURIComponent(L.chosen)+'/submit',{
      method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},
      body:JSON.stringify({confirmed_intake:collectIntake()})});
    const data=await res.json().catch(()=>({}));
    if(res.status===409){
      $('#gatetitle').textContent='後端已拒絕送出（409）';
      $('#gatemsg').textContent='後端重跑六節點後判定不得送出，原因如下。前端顯示的狀態不算數，這一筆以後端為準。';
      LIVE.blockers=data.blockers||LIVE.blockers;
      LIVE.submit_allowed=false;
      renderBlockers(); btn.disabled=true;
      toast('後端拒絕送出（409）');
      return null;
    }
    if(!res.ok)throw new Error('HTTP '+res.status);
    return data;
  }catch(e){
    $('#gatetitle').textContent='送出失敗';
    $('#gatemsg').textContent='無法與後端完成送出（'+esc(String(e&&e.message||e))+'）。'+
      '本頁不會自行判定成功——沒有收到後端回應就是沒有送出。';
    btn.disabled=false;
    return null;
  }finally{
    if($('#gatemsg').textContent==='送出中：後端重新判斷可否送出…')$('#gatemsg').textContent=prev;
  }
}

$('#go4').onclick=async ()=>{
  if($('#go4').disabled)return;
  let receipt=null;
  if(isLive){
    receipt=await submitToBackend();
    if(!receipt)return;   /* 409 或失敗：留在燈號頁，不進完成頁 */
  }
  $('#r_no').textContent=$('#f_no').value||'—';
  $('#r_type').textContent='因'+($('#f_type').value||'—')+'提起訴願';
  $('#r_lamp').textContent=`紅 ${SENTS.filter(s=>s.l==='r').length}（已確認）／黃 ${SENTS.filter(s=>s.l==='y').length}／綠 ${SENTS.filter(s=>s.l==='g').length}　共 ${SENTS.length} 句`;
  $('#r_time').textContent=new Date().toLocaleString('zh-TW',{hour12:false});
  const lampTxt=`${SENTS.filter(s=>s.l==='r').length} / ${SENTS.filter(s=>s.l==='y').length} / ${SENTS.filter(s=>s.l==='g').length}`;
  $('#s_sent').textContent=SENTS.length+' 句';
  $('#s_lamp').textContent=lampTxt;
  if(isLive){
    /* 步驟 5 的統計吃後端權威值（§6.2 run_meta），不由前端自己算 startTime。
       只列這次執行真的量到的東西，不放沒有出處的對照數字。 */
    const rm=(LIVE&&LIVE.run_meta)||{};
    const nt=rm.node_timings||{};
    const total=Object.keys(nt).reduce((a,k)=>a+(nt[k]||0),0);
    /* 六節點在 fixture 檔位都是次毫秒，逐項四捨五入後合計會是 0——
       畫面上顯示「0 ms」看起來像壞掉，寫成 <1 ms 才是那個數字真正的意思。 */
    $('#s_min').textContent=(total>0?total+' ms':'<1 ms');
    /* 從 n2 續跑時 node_timings 只有重跑的那幾個節點。寫死「六節點」會變成
       「六節點合計（5 節點）」——看起來像有一個節點掛了。照實說是哪幾個。 */
    const nk=Object.keys(nt);
    $('#s_min_lb').textContent=nk.length>=6
      ? '後端六節點合計（'+nk.length+' 節點）'
      : '本次重跑 '+nk.length+' 節點合計（'+nk.join('、')+'；其餘沿用上一次執行）';
    $('#s_cite').textContent=((LIVE&&LIVE.citations)||[]).length+' 筆';
    $('#s_cite_lb').textContent='引用查核筆數（四態）';
    const con=SENTS.find(s=>s.slot==='conclusion');
    $('#r_result').textContent=con?con.t.slice(0,44):'—';
    $('#s_note').innerHTML='以上四項均為本次執行實際量到的值（run_id <code>'+esc(rm.run_id||'—')+
      '</code>，RUN_MODE='+esc(rm.run_mode||'?')+'）。'+
      '<b>fixture 檔位是離線重播，耗時不代表接上模型後的處理時間</b>，也不是與人工作業的對照。';
    /* 完成頁的措辭一律照後端收據講，不由前端自己宣稱送到了哪裡 */
    if(receipt){
      $('#r_title').textContent='已記錄為送出（後端回 200）';
      $('#r_desc').textContent=receipt.external_effect_note||
        '本 demo 沒有任何外部整合：沒有寄送郵件、沒有排入議程、沒有呼叫外部系統。';
      $('#r_server').innerHTML='後端收據：<code>'+esc(receipt.run_id||'—')+'</code>　'+
        '記錄時間 '+esc(receipt.recorded_at||'—')+'　'+
        '外部效果 <b>'+esc(receipt.external_effect||'none')+'</b>　'+
        '寫入 <code>'+esc(receipt.record_path||'—')+'</code>。'+
        '<br>可否送出由後端重跑六節點判定（<code>'+esc(receipt.recomputed_by||'backend')+'</code>），未採信前端的判斷。';
    }
  }else{
    const secs=Math.max(1,Math.round((Date.now()-S.startTime)/1000));
    $('#s_min').textContent=secs+' 秒';
    $('#s_min_lb').textContent='前端動線耗時（含展示動畫）';
    $('#s_cite').textContent=SENTS.reduce((n,s)=>n+s.refs.length,0)+' 筆';
    $('#s_cite_lb').textContent='逐句依據連結數';
    $('#s_note').textContent='離線 fixture 模式：以上為前端在本頁量到的值，不是後端執行結果。';
    $('#r_title').textContent='離線模式：未送出任何東西';
    $('#r_desc').textContent='本頁未連上後端，沒有呼叫送出端點，也沒有留下任何紀錄。這一步只是動線示意。';
    $('#r_server').textContent='';
  }
  goto(4);
};
$('#restart').onclick=()=>location.reload();

/* ---- 資料來源標記（CONSTITUTION 原則 3／6） ---- */
/* 橫幅一律照 payload.provenance 講：合成案是「合成測資」、上傳案是「上傳案件：…」。
   換過 payload（確認續跑、上傳建案）就要重畫一次，否則橫幅會停在上一個案件的來源上。 */
function renderProv(){
  const el=$('#prov'), pv=CASE.provenance||{};
  if(!el)return;
  el.textContent=pv.banner||'示範案件';
  if(pv.note)el.dataset.tip=pv.note; else delete el.dataset.tip;
}
renderProv();

updateGo1();
}  /* ← bootApp 結束 */

/* ---- tooltip：掛在 body，不受 overflow 裁切（與資料模式無關，先掛起來） ---- */
(function(){
  const tt=document.createElement('div'); tt.id='tt'; document.body.appendChild(tt);
  let cur=null;
  const show=el=>{
    cur=el; tt.textContent=el.dataset.tip; tt.classList.add('on');
    const r=el.getBoundingClientRect(), w=tt.offsetWidth, hgt=tt.offsetHeight;
    let x=r.left+r.width/2-w/2;
    x=Math.max(8,Math.min(x,innerWidth-w-8));
    let y=r.bottom+7;
    if(y+hgt>innerHeight-8)y=r.top-hgt-7;
    tt.style.left=x+'px'; tt.style.top=y+'px';
  };
  const hide=()=>{cur=null;tt.classList.remove('on')};
  document.addEventListener('mouseover',e=>{const el=e.target.closest('[data-tip]');if(el&&el!==cur)show(el);else if(!el&&cur)hide()});
  document.addEventListener('focusin',e=>{const el=e.target.closest('[data-tip]');if(el)show(el)});
  document.addEventListener('focusout',hide);
  window.addEventListener('scroll',hide,true);
})();

/* ---- 圖示字型保底：Material Symbols 載不到（離線／擋外連）時整批隱藏，
       不讓 "account_balance" 這種 ligature 字面漏到畫面上 ---- */
(function(){
  const root=document.documentElement;
  /* document.fonts.check() 對「整個 family 都不存在」會因系統 fallback 回報 true，不可靠；
     改量測 ligature 寬度：字型有載到會縮成單一字符，明顯窄於 monospace 的字面文字。 */
  const measure=fam=>{
    const s=document.createElement('span');
    s.style.cssText='position:absolute;left:-9999px;top:-9999px;font-size:80px;white-space:nowrap;font-family:'+fam;
    s.textContent='account_balance';
    document.body.appendChild(s);
    const w=s.offsetWidth; s.remove(); return w;
  };
  const loaded=()=>{try{return measure("'Material Symbols Outlined',monospace")<measure('monospace')*0.6}catch(e){return false}};
  const mark=()=>{
    if(root.classList.contains('icons-ready')||root.classList.contains('icons-off'))return;
    root.classList.add(loaded()?'icons-ready':'icons-off');
  };
  if(document.fonts&&document.fonts.ready)document.fonts.ready.then(mark);
  setTimeout(mark,3000);
})();

boot();
