/* 訴願決定書 AI 輔助撰擬系統 — 前端流程（5 步：收文→幕僚團→草稿→燈號→送出）。
   示範案件資料由 data/case-demo.json 注入；訴願期間由 engine.js（與 engine/deadline.py 同測試集鎖定）實算，
   不是寫死的字串——見 applyDeadlineEngine()。 */
const CASE = JSON.parse(document.getElementById('case-data').textContent);
const SNAP = JSON.parse(document.getElementById('snapshot-data').textContent);

/* ================= 狀態 ================= */
const S={step:0,unlocked:0,startTime:null};
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];

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
const DEMO_FILES=CASE.files;
function addFile(f,i){
  const li=document.createElement('li');
  li.style.animationDelay=(i*90)+'ms';
  li.innerHTML=`<span class="mi doc">description</span>
    <span class="meta"><b>${f.n}</b><span>${f.s}　·　${f.x}</span></span>
    <span class="ok">✓ 已解析</span><button aria-label="移除">×</button>`;
  li.querySelector('button').onclick=()=>{li.remove();updateGo1()};
  $('#filelist').appendChild(li);
  updateGo1();
}
function loadDemo(){
  $('#filelist').innerHTML='';
  DEMO_FILES.forEach(addFile);
  const K=CASE.intake;
  setTimeout(()=>{
    $('#f_no').value=K.no;
    $('#f_type').value=K.type;
    $('#f_person').value=K.person;
    $('#f_org').value=K.org;
    $('#f_d1').value=K.d1;
    $('#f_d2').value=K.d2;
    $('#f_d3').value=K.d3;
    $('#f_agent').value=K.agent;
    $('#f_note').value=K.note;
    updateGo1();
    K.auto_fields.forEach(id=>$('#'+id).textContent='自動擷取');
    toast(K.auto_toast);
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

/* ================= 2 幕僚團 ================= */
const AGENTS=CASE.agents;
let agentsRan=false;
function runAgents(){
  if(agentsRan)return; agentsRan=true;
  applyDeadlineEngine();   /* 先實算期間，幕僚敘述與燈號統計才有真數字可填 */
  const wrap=$('#agents'); wrap.innerHTML=''; $('#console').innerHTML='';
  AGENTS.forEach(a=>{
    const d=document.createElement('div');
    d.className='agent'; d.dataset.s='idle'; d.id='ag-'+a.k;
    d.innerHTML=`<span class="pill"></span>
      <div class="hd"><span class="ico"><span class="mi" style="font-size:17px;color:var(--navy)">${a.ico}</span></span><h3>${a.name}</h3></div>
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

  AGENTS.forEach((a,i)=>{
    const t0=400+i*1400;
    setTimeout(()=>{
      const el=$('#ag-'+a.k);
      el.dataset.s='run';
      el.querySelector('.out').textContent='分析中…';
      el.querySelector('.st').innerHTML='<span class="spin"></span>執行中';
      el.querySelector('.bar-in').style.width='18%';
      log(`▶ ${a.name} 開始作業`);
      a.logs.forEach((l,j)=>setTimeout(()=>{
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

/* 幕僚敘述裡的數字一律回填實際結果，避免看板說「紅燈 2」而審核頁顯示 4 這種自相矛盾 */
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
const DOC=CASE.doc;
const SENTS=[]; DOC.forEach(bk=>(bk.ss||[]).forEach(s=>{s.orig=s.t;s.baseL=s.l;SENTS.push(s)}));
const byId=id=>SENTS.find(s=>s.id===id);
const LAMPNAME={r:'紅燈　需人工審核',y:'黃燈　有證據',g:'綠燈　可被計算'};

/* ================= 規則引擎：訴願期間實算（零 LLM 依賴） ================= */
/* 綠燈句宣稱「可被計算」，就得真的算：此處呼叫 engine.js 的 computeDeadline，
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
/* 算式卡：把引擎每一步攤開，附法條依據（分層誠實第一級） */
function stepsHTML(s){
  if(!s.steps||!s.steps.length)return '';
  const steps=s.steps.map(x=>`<li><b>${x.rule}</b><span class="basis">${x.basis}</span><span class="val">${x.value}</span></li>`).join('');
  const cav=(s.caveats||[]).map(c=>`<p>⚠ ${c}</p>`).join('');
  return `<details class="calc"><summary>展開算式（${s.steps.length} 步・規則引擎驗算）</summary>
    <ol>${steps}</ol>${cav?`<div class="cav">${cav}</div>`:''}</details>`;
}

/* ================= 引用可驗：法條對照離線快照 ================= */
/* 「在庫」僅代表條號存在於本 prototype 之離線法規快照，
   「庫外」不等於引用錯誤，代表本庫無法驗證、需人工查證。 */
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
const LAWS=CASE.laws, CASES=CASE.cases, ISSUES=CASE.issues;
const ALLREFS=[...LAWS,...CASES,...ISSUES];
const refTab=id=>id[0]==='L'?'law':id[0]==='C'?'case':'issue';

function refHTML(o){
  const lamp=o.lamp?`<span class="tag ${o.lamp}">${LAMPNAME[o.lamp].split('　')[0]}</span>`:'';
  const sim=o.sim!=null?`<span class="sim"><span class="simbar"><i style="width:${o.sim}%"></i></span>相似度 ${o.sim}%</span>`:'';
  const users=SENTS.filter(s=>s.refs.includes(o.id)).length;
  const v=o.id[0]==='L'?verifyLaw(o.t):null;
  const vf=v?`<span class="tag ${v.ok?'g':'y'}" tabindex="0" data-tip="${v.tip}">${v.text}</span>`:'';
  return `<div class="ref" id="ref-${o.id}" data-ref="${o.id}">
    <div class="top"><span class="nm">${o.t}</span>${lamp}</div>
    ${o.q?`<q>${o.q}</q>`:''}${o.d?`<div style="font-size:12.5px;color:var(--ink-2);margin:4px 0 6px;line-height:1.65">${o.d}</div>`:''}
    <div class="foot">
      ${o.tag?`<span class="tag k">${o.tag}</span>`:''}${vf}${sim}
      ${users?`<span class="tag n">本稿引用 ${users} 句</span>`:''}
      <span class="gap"></span><span>${o.src||''}</span>
    </div>
  </div>`;
}
function buildRefs(){
  $('#tp-law').innerHTML=LAWS.map(refHTML).join('');
  $('#tp-case').innerHTML=CASES.map(refHTML).join('');
  $('#tp-issue').innerHTML=ISSUES.map(refHTML).join('');
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
    if(bk.ty==='title'){sh.insertAdjacentHTML('beforeend',`<div class="doc-title">訴願決定書</div>`);return;}
    if(bk.ty==='meta'){sh.insertAdjacentHTML('beforeend',`<p class="meta">${bk.text}</p>`);return;}
    if(bk.ty==='h'){sh.insertAdjacentHTML('beforeend',`<h4>${bk.text}</h4>`);return;}
    const inner=bk.ss.map(s=>`<span class="sent" contenteditable="true" spellcheck="false" data-id="${s.id}" data-l="${s.l}">${s.t}</span>`).join('');
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
  const links=s.refs.length
    ? `<div class="lk">${s.refs.map(r=>{const o=ALLREFS.find(x=>x.id===r);return `<button data-goref="${r}" title="${o?o.t:r}">${o?o.t:r}</button>`}).join('')}</div>`
    : '<div class="lk"><span style="color:var(--ink-3)">無外部依據（本句以卷證或規則驗算為準）</span></div>';
  $('#inspect').innerHTML=`<div class="ln"><span class="k">第 ${SENTS.indexOf(s)+1} 句</span>${lampTag}${s.edited?'<span class="tag r">已改寫</span>':''}</div><span class="why">${s.why}</span>${links}${stepsHTML(s)}`;
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
    if(bk.ty==='title'){box.insertAdjacentHTML('beforeend',`<div class="doc-title">訴願決定書</div>`);return;}
    if(bk.ty==='meta'){box.insertAdjacentHTML('beforeend',`<p class="meta">${bk.text}</p>`);return;}
    if(bk.ty==='h'){box.insertAdjacentHTML('beforeend',`<h4>${bk.text}</h4>`);return;}
    const inner=bk.ss.map(s=>`<span class="rs" data-id="${s.id}" data-l="${s.l}" data-ack="${s.ack?1:0}" tabindex="0">${s.t}</span>`).join('');
    box.insertAdjacentHTML('beforeend',`<p class="${bk.ind?'ind':''}">${inner}</p>`);
  });
  refreshCounts(); renderRedList(); refreshGate();
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
    return `<button class="btn ghost sm" data-red="${s.id}" style="text-align:left;display:flex;gap:7px;align-items:center">
      <span style="width:8px;height:8px;border-radius:50%;background:${s.ack?'#3CBB73':'#E5484D'};flex:none"></span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">第 ${i} 句　${s.t.slice(0,14)}…</span>
      <span style="color:${s.ack?'var(--green)':'var(--seal)'}">${s.ack?'已確認':'待核'}</span></button>`;
  }).join('')||'<span style="font-size:12.5px;color:var(--ink-3)">無紅燈句</span>';
}
function showDetail(id){
  const s=byId(id); if(!s){console.warn('unknown sentence',id);return;}
  $$('.rs').forEach(el=>el.classList.toggle('sel',el.dataset.id===id));
  const i=SENTS.indexOf(s)+1;
  const refs=s.refs.map(r=>{const o=ALLREFS.find(x=>x.id===r);return o?`<div style="font-size:12px;color:var(--navy-l);margin-bottom:3px">▸ ${o.t}</div>`:''}).join('');
  $('#detbody').innerHTML=`
    <div style="display:flex;gap:7px;align-items:center;margin-bottom:9px">
      <span class="tag ${s.l}">${LAMPNAME[s.l]}</span>
      <span style="font-family:var(--mono);font-size:11.5px;color:var(--ink-3)">第 ${i} / ${SENTS.length} 句</span>
      ${s.edited?'<span class="tag r">已人工改寫</span>':''}
    </div>
    <p class="q">${s.t}</p>
    <p class="why">${s.why}</p>
    ${refs?`<div style="margin-bottom:9px">${refs}</div>`:''}
    <div class="src">${s.src||'—'}</div>
    ${stepsHTML(s)}
    ${s.l==='r'?(s.ack?'<span class="ack">✓ 已核閱確認</span>':`<button class="btn sm" data-ackid="${s.id}">我已核閱卷證，確認本句</button>`):''}`;
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
/* ---- tooltip：掛在 body，不受 overflow 裁切 ---- */
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
  const reds=SENTS.filter(s=>s.l==='r'), left=reds.filter(s=>!s.ack).length;
  $('#go4').disabled=left>0;
  if(left>0){
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
  $('#m_list').innerHTML=pend.map(s=>`<li>第 ${SENTS.indexOf(s)+1} 句　${s.t.slice(0,24)}…</li>`).join('');
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
  const mins=Math.max(6,Math.round((Date.now()-S.startTime)/60000))||8;
  $('#r_no').textContent=$('#f_no').value||'—';
  $('#r_type').textContent='因'+($('#f_type').value||'—')+'提起訴願';
  $('#r_lamp').textContent=`紅 ${SENTS.filter(s=>s.l==='r').length}（已確認）／黃 ${SENTS.filter(s=>s.l==='y').length}／綠 ${SENTS.filter(s=>s.l==='g').length}　共 ${SENTS.length} 句`;
  $('#r_time').textContent=new Date().toLocaleString('zh-TW',{hour12:false});
  $('#s_min').textContent='約 '+mins+' 分';
  $('#s_cite').textContent=SENTS.reduce((n,s)=>n+s.refs.length,0)+' 筆';
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

updateGo1();

