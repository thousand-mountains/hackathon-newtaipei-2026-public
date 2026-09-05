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

function setBadge(mode,label,tip){
  const el=$('#modebadge'); if(!el)return;
  el.dataset.m=mode;
  el.innerHTML='<span class="dotm"></span>'+esc(label);
  if(tip)el.dataset.tip=tip;
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

    const rr=await fetch('api/cases/'+encodeURIComponent(chosen)+'/runs',
                         {method:'POST',headers:{'Accept':'application/json'}});
    if(!rr.ok)throw new Error('runs HTTP '+rr.status);
    payload=await rr.json();
  }catch(e){
    MODE='offline';
    setBadge('offline','離線 fixture（未接後端）',
      '本頁未連上後端 API（'+String(e&&e.message||e)+'）。畫面資料來自建置時內嵌的 data/case-demo.json，'+
      '期間由頁內 engine.js 實算、法條對照離線快照。這不是後端六節點的執行結果。');
    bootApp();
    return;
  }
  MODE='live'; LIVE=payload; CASE=adaptPayload(payload);
  setBadge('live','live 後端・'+chosen,
    '本頁資料來自後端六節點的一次實際執行（run_id '+(payload.run_id||'—')+'，'+
    'RUN_MODE='+((payload.run_meta&&payload.run_meta.run_mode)||'?')+'）。'+
    '燈號、引用狀態、送出許可全部由後端守門節點判定，前端不重算。');
  bootApp({caseIds:caseIds,chosen:chosen,health:health});
}

/* ================= 主程式 ================= */
function bootApp(live){
  const L=live||{};
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
const DEMO_FILES=CASE.files||[];
function addFile(f,i){
  const li=document.createElement('li');
  li.style.animationDelay=(i*90)+'ms';
  li.innerHTML=`<span class="mi doc">description</span>
    <span class="meta"><b>${esc(f.n)}</b><span>${esc(f.s)}　·　${esc(f.x)}</span></span>
    <span class="ok">✓ 已解析</span><button aria-label="移除">×</button>`;
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
function loadDemo(){
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
    $('#f_note').value=K.note||'';
    updateGo1();
    (K.auto_fields||[]).forEach(id=>{
      const dom=String(id).startsWith('a_')?id:AUTO_FIELD_DOM[id];
      const el=dom&&$('#'+dom); if(el)el.textContent='自動擷取';
    });
    if(K.auto_toast)toast(K.auto_toast);
  },700);
}
$('#demoload').onclick=loadDemo;
const drop=$('#drop');
drop.onclick=()=>$('#filein').click();
drop.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();loadDemo();}};
['dragenter','dragover'].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.add('hot')}));
['dragleave','drop'].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.remove('hot')}));
drop.addEventListener('drop',e=>{
  const fs=[...(e.dataTransfer?.files||[])];
  if(fs.length)fs.forEach((f,i)=>addFile({n:f.name,s:Math.round(f.size/1024)+' KB',x:'已上傳'},i));
  else loadDemo();
});
$('#filein').onchange=e=>[...e.target.files].forEach((f,i)=>addFile({n:f.name,s:Math.round(f.size/1024)+' KB',x:'已上傳'},i));

function updateGo1(){
  const files=$('#filelist').children.length;
  const no=$('#f_no').value.trim(), ty=$('#f_type').value;
  const ok=files>0&&no&&ty;
  $('#go1').disabled=!ok;
  $('#go1hint').textContent=!files?'請先上傳卷證':(!no||!ty)?'請填寫收文案號與案件類型':'';
}
['#f_no','#f_type'].forEach(s=>{$(s).addEventListener('input',updateGo1);$(s).addEventListener('change',updateGo1)});
$('#go1').onclick=()=>{
  if($('#go1').disabled)return;
  S.startTime=Date.now();
  goto(1); runAgents();
};

/* ---- live 模式：案例選單與資料來源說明 ---- */
(function(){
  const note=$('#sourcenote');
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
    if(note)note.innerHTML='資料來源：後端 <code>POST /api/cases/'+esc(L.chosen)+
      '/runs</code>（run_id <code>'+esc((LIVE&&LIVE.run_id)||'—')+'</code>）。'+
      '燈號與引用狀態由後端守門節點判定，本頁不重算。';
  }else if(note){
    note.textContent='資料來源：本頁內嵌的示範案件（data/case-demo.json）。未連上後端，'+
      '期間由頁內規則引擎實算、法條對照離線快照。';
  }
})();

/* ================= 2 幕僚團 ================= */
const AGENTS=CASE.agents||[];
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
const DOC=CASE.doc||[];
const SENTS=[]; DOC.forEach(bk=>(bk.ss||[]).forEach(s=>{s.orig=s.t;s.baseL=s.l;s.refs=s.refs||[];SENTS.push(s)}));
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
const LAWS=CASE.laws||[], CASES=CASE.cases||[], ISSUES=CASE.issues||[];
const ALLREFS=[...LAWS,...CASES,...ISSUES];
const refTab=id=>id[0]==='L'?'law':id[0]==='C'?'case':'issue';

function refHTML(o){
  const lamp=o.lamp?`<span class="tag ${o.lamp}">${LAMPNAME[o.lamp].split('　')[0]}</span>`:'';
  const sim=o.sim!=null?`<span class="sim"><span class="simbar"><i style="width:${o.sim}%"></i></span>相似度 ${o.sim}%</span>`:'';
  const users=SENTS.filter(s=>s.refs.includes(o.id)).length;
  /* live 模式不重算引用狀態：直接用後端給的 tag（✓ 在庫／✗ 查無此號／庫外…）與 note */
  const v=(!isLive&&o.id[0]==='L')?verifyLaw(o.t):null;
  const vf=v?`<span class="tag ${v.ok?'g':'y'}" tabindex="0" data-tip="${esc(v.tip)}">${esc(v.text)}</span>`:'';
  const tagCls=(isLive&&o.lamp)?o.lamp:'k';
  const tagTip=(isLive&&o.note)?` tabindex="0" data-tip="${esc(o.note)}"`:'';
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
function buildRefs(){
  $('#tp-law').innerHTML=LAWS.map(refHTML).join('')||'<span style="font-size:12.5px;color:var(--ink-3)">本次執行無法規檢索結果</span>';
  $('#tp-case').innerHTML=CASES.map(refHTML).join('')
    ||'<span style="font-size:12.5px;color:var(--ink-3)">相似歷史案通道不可用（無資料集），本次回空並非「查無相似案」</span>';
  $('#tp-issue').innerHTML=ISSUES.map(refHTML).join('')||'<span style="font-size:12.5px;color:var(--ink-3)">未偵測到事實認定爭點</span>';
  /* 分頁上的數字跟著實際資料走，不寫死 */
  const counts={law:LAWS.length,case:CASES.length,issue:ISSUES.length};
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
  const qs=ho.questions||[], sg=ho.signals||[];
  if(!qs.length&&!sg.length){card.style.display='none';return;}
  card.style.display='';
  $('#handoffbody').innerHTML=
    (ho.note?`<p class="why" style="font-size:12.5px;color:var(--ink-2);margin:0 0 9px;line-height:1.7">${esc(ho.note)}</p>`:'')+
    (sg.length?`<div class="handoff-s">${sg.map(x=>`<span>▸ ${esc(x)}</span>`).join('')}</div>`:'')+
    (qs.length?`<ol class="handoff-q">${qs.map(q=>`<li>${esc(q)}</li>`).join('')}</ol>`:'');
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
    $('#gatemsg').textContent='本案未通過品管守門節點，送出審議已鎖定。阻擋原因如下，須先由承辦人處理；'+
      '紅燈逐句確認不會解除此鎖定。';
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

$('#go4').onclick=()=>{
  if($('#go4').disabled)return;
  $('#r_no').textContent=$('#f_no').value||'—';
  $('#r_type').textContent='因'+($('#f_type').value||'—')+'提起訴願';
  $('#r_lamp').textContent=`紅 ${SENTS.filter(s=>s.l==='r').length}（已確認）／黃 ${SENTS.filter(s=>s.l==='y').length}／綠 ${SENTS.filter(s=>s.l==='g').length}　共 ${SENTS.length} 句`;
  $('#r_time').textContent=new Date().toLocaleString('zh-TW',{hour12:false});
  if(isLive){
    /* 步驟 5 的統計改吃後端權威值（§6.2 run_meta），不由前端自己算 startTime */
    const rm=(LIVE&&LIVE.run_meta)||{};
    $('#s_min').textContent=(rm.elapsed_ms!=null?rm.elapsed_ms+' ms':'—');
    const lb=$('#s_min').nextElementSibling; if(lb)lb.textContent='後端六節點實測（RUN_MODE='+(rm.run_mode||'?')+'）';
    $('#s_cite').textContent=((LIVE&&LIVE.citations)||[]).length+' 筆';
    const con=SENTS.find(s=>s.slot==='conclusion');
    $('#r_result').textContent=con?con.t.slice(0,44):'—';
  }else{
    const mins=Math.max(6,Math.round((Date.now()-S.startTime)/60000))||8;
    $('#s_min').textContent='約 '+mins+' 分';
    $('#s_cite').textContent=SENTS.reduce((n,s)=>n+s.refs.length,0)+' 筆';
  }
  goto(4);
};
$('#restart').onclick=()=>location.reload();

/* ---- 資料來源標記（CONSTITUTION 原則 3／6） ---- */
(function(){
  const el=$('#prov'), pv=CASE.provenance||{};
  if(!el)return;
  el.textContent=pv.banner||'示範案件';
  if(pv.note)el.dataset.tip=pv.note;
})();

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
