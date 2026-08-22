/* 工作台前端邏輯。依 emil-design-eng：高頻操作不加動畫、結果進場 ease-out、按鈕 :active 縮放在 CSS。 */
"use strict";
const SNAP = JSON.parse(document.getElementById("snapshot-data").textContent);
const FIX = JSON.parse(document.getElementById("fixtures-data").textContent);
const $ = (s) => document.querySelector(s);
const rocIso = (i) => { if (!i) return "－"; const [y, m, d2] = i.split("-").map(Number); return `${y - 1911}/${m}/${d2}`; };

/* ── 分頁切換（鍵盤級高頻 → 無轉場延遲，僅 CSS 底線過渡）── */
document.querySelectorAll(".tabbtn").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".tabbtn").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("on", p.id === b.dataset.tab));
  });
});

/* ── 引擎自測（載入即跑，證明「可驗算」不是口號）── */
(function selfTest() {
  const vecs = __VECTORS__;
  let pass = 0;
  for (const v of vecs) {
    const r = computeDeadline({ method: v.in.method, service: v.in.service, filing: v.in.filing ?? null, transit: v.in.transit ?? 0 });
    if (r.deadline === v.out.deadline && r.overdue === v.out.overdue) pass++;
  }
  const el = $("#selftest");
  el.textContent = `引擎自測 ${pass}/${vecs.length}`;
  if (pass !== vecs.length) el.classList.add("fail");
})();

/* ── 分頁一：期間計算 ── */
function runDeadline() {
  const method = $("#f-method").value;
  const service = $("#f-service").value;
  if (!service) return;
  const filing = $("#f-filing").value || null;
  const transit = parseInt($("#f-transit").value || "0", 10);
  const r = computeDeadline({ method, service, filing, transit });
  const out = $("#f-out"); out.hidden = false;
  const steps = $("#f-steps"); steps.innerHTML = "";
  r.steps.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "step"; li.style.animationDelay = `${i * 45}ms`;
    li.innerHTML = `<div class="n">${i + 1}</div><div><div class="rule">${s.rule}</div><div class="basis">依據：${s.basis}</div><div class="val">${s.value}</div></div>`;
    steps.appendChild(li);
  });
  const v = $("#f-verdict");
  if (r.overdue === null || r.overdue === undefined) v.innerHTML = r.deadline ? "" : "";
  else v.innerHTML = `<div class="verdict ${r.overdue ? "overdue" : "ontime"}">${r.overdue ? "已逾法定期間" : "未逾法定期間"}（期滿日 ${rocIso(r.deadline)}）</div>`;
  $("#f-caveats").innerHTML = r.caveats.map((c) => `<div class="caveat"><b>人工判斷：</b>${c}</div>`).join("");
  out.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
$("#f-run").addEventListener("click", runDeadline);
$("#f-load-hist").addEventListener("click", () => {
  $("#f-method").value = "deposit"; $("#f-service").value = "2024-06-13";
  $("#f-filing").value = "2024-07-20"; $("#f-transit").value = "0";
  runDeadline();
});

/* ── 分頁二：引用檢查 ── */
const LAW_NAMES = Object.keys(SNAP.laws).sort((a, b) => b.length - a.length);
const LAW_RE = new RegExp(`(${LAW_NAMES.join("|")})\\s*第\\s*(\\d+)\\s*條(?:\\s*之\\s*(\\d+))?`, "g");
const PREC_RE = /(最高行政法院|臺北高等行政法院|高雄高等行政法院|臺中高等行政法院|臺灣新北地方法院)?\s*(\d{2,3})\s*年?\s*度?\s*(判|裁|訴|上|簡上|簡)\s*字\s*第\s*(\d+)\s*號/g;
const INTERP_RE = /釋字第\s*(\d+)\s*號/g;

function checkCitations(text) {
  const found = [];
  let m;
  LAW_RE.lastIndex = 0;
  while ((m = LAW_RE.exec(text))) {
    const [_, law, art, sub] = m;
    const key = sub ? `${art}之${sub}` : art;
    const disp = `${law}第${art}條${sub ? `之${sub}` : ""}`;
    const amend = (SNAP.amendments || []).find((a) => a.law === law && a.old === key);
    if (amend) found.push({ cls: "warn", mark: "⚠ 已修正", what: disp, note: `已改列第 ${amend.new} 條（${amend.date}）。${amend.note}` });
    else if (SNAP.laws[law].articles.includes(key)) found.push({ cls: "ok", mark: "✓ 在庫", what: disp, note: `條號存在於快照（該法最大條號 ${SNAP.laws[law].max}）` });
    else found.push({ cls: "bad", mark: "✗ 查無此條", what: disp, note: `快照中${law}無第 ${key} 條（最大條號 ${SNAP.laws[law].max}），請人工查證是否誤植` });
  }
  PREC_RE.lastIndex = 0;
  while ((m = PREC_RE.exec(text))) {
    const [_, court, yr, typ, no] = m;
    const disp = `${court || "（未標法院）"}${yr}年度${typ}字第${no}號`;
    const hit = SNAP.precedents.find((p) => p.year === +yr && p.type === typ && p.no === +no && (!court || p.court === court));
    if (hit) found.push({ cls: "ok", mark: "✓ 在庫", what: disp, note: `資料集判解白名單命中（${hit.court}）` });
    else found.push({ cls: "bad", mark: "✗ 庫內查無", what: disp, note: "不在 19 份判解白名單。可能為庫外真實判解，也可能為誤植或幻覺，送出前必須人工查證。" });
  }
  INTERP_RE.lastIndex = 0;
  while ((m = INTERP_RE.exec(text))) {
    const no = +m[1];
    const hit = (SNAP.interpretations || []).includes(no);
    found.push(hit ? { cls: "ok", mark: "✓ 在庫", what: `釋字第${no}號`, note: "資料集白名單命中" }
                   : { cls: "warn", mark: "？庫外", what: `釋字第${no}號`, note: "非資料集內釋字，請人工查證" });
  }
  return found;
}
function runCite() {
  const text = $("#c-text").value;
  const res = checkCitations(text);
  const out = $("#c-out"); out.hidden = false;
  const bad = res.filter((r) => r.cls === "bad").length, warn = res.filter((r) => r.cls === "warn").length;
  $("#c-summary").textContent = res.length
    ? `找到 ${res.length} 個引用：${res.length - bad - warn} 在庫、${warn} 需注意、${bad} 攔下`
    : "未偵測到法條或判解引用。";
  const list = $("#c-list"); list.innerHTML = "";
  res.forEach((r, i) => {
    const d = document.createElement("div");
    d.className = `cite ${r.cls}`; d.style.animationDelay = `${i * 40}ms`;
    d.innerHTML = `<span class="mark">${r.mark}</span><div><div class="what">${r.what}</div><div class="note">${r.note}</div></div>`;
    list.appendChild(d);
  });
  out.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
$("#c-run").addEventListener("click", runCite);
$("#c-load-real").addEventListener("click", () => {
  $("#c-text").value = "按洗錢防制法第22條規定，任何人不得將自己或他人向金融機構申請開立之帳戶交付、提供予他人使用。查訴願人之行為，原處分機關依同條第2項及第4項規定裁處告誡，經核與行政程序法第101條及訴願法第79條規定無違，並有最高行政法院108年度判字第531號行政判決、109年度上字第780號判決意旨可資參照。";
  runCite();
});
$("#c-load-fake").addEventListener("click", () => {
  $("#c-text").value = "另參照最高行政法院110年度判字第9999號判決意旨，及洗錢防制法第15條之2、噪音管制法第55條規定，本件應予駁回。";
  runCite();
});

/* ── 分頁三：草稿工作台（fixture 驅動）── */
$("#d-disclaimer").textContent = FIX.disclaimer;
const tabsEl = $("#d-tabs");
FIX.scenarios.forEach((sc, i) => {
  const b = document.createElement("button");
  b.className = "scen" + (i === 0 ? " on" : ""); b.textContent = sc.tab;
  b.addEventListener("click", () => {
    tabsEl.querySelectorAll(".scen").forEach((x) => x.classList.toggle("on", x === b));
    renderScenario(sc);
  });
  tabsEl.appendChild(b);
});
function citeChip(c) {
  const res = checkCitations(c);
  const cls = res.length ? res[0].cls : "warn";
  const title = res.length ? `${res[0].mark}：${res[0].note}` : "未能解析";
  return `<span class="chip" title="${title}">${c}${res.length && res[0].cls === "ok" ? " ✓" : ""}</span>`;
}
function renderScenario(sc) {
  const body = $("#d-body");
  let html = `<div class="facts"><b>事實摘要（synthetic）：</b>${sc.facts}<br><span style="font:11px var(--mono);color:var(--subtle)">${sc.basis_note}</span></div>`;
  if (sc.draft.main) html += `<div class="mainline">主文（草擬）：${sc.draft.main}</div>`;
  sc.draft.reasoning.forEach((s) => {
    const tier = sc.tier === "red" ? (s.cites.length ? "a" : "a") : sc.tier === "green" ? "g" : "a";
    html += `<div class="sent ${tier}">${s.text}${s.cites.length ? `<div class="chips">${s.cites.map(citeChip).join("")}</div>` : ""}</div>`;
  });
  if (sc.engine_input) {
    const r = computeDeadline(sc.engine_input);
    html += `<div class="sent g"><b>期間算式（規則引擎即時計算）：</b><ol class="steps">` +
      r.steps.map((s, i) => `<li class="step" style="animation-delay:${i * 40}ms"><div class="n">${i + 1}</div><div><div class="rule">${s.rule}</div><div class="basis">依據：${s.basis}</div><div class="val">${s.value}</div></div></li>`).join("") +
      `</ol><div class="verdict ${r.overdue ? "overdue" : "ontime"}" style="margin-top:8px">${r.overdue ? "已逾法定期間" : "未逾法定期間"}（期滿日 ${rocIso(r.deadline)}）</div></div>`;
  }
  if (sc.draft.redzone) {
    const z = sc.draft.redzone;
    html += `<div class="redzone"><h4>⛔ ${z.blocked_title}</h4><p>${z.why}</p>` +
      `<b style="font-size:12.5px;color:var(--red)">偵測訊號</b><ul>${z.signals.map((s) => `<li>${s}</li>`).join("")}</ul>` +
      `<div class="handover"><b>判斷交接卡（請承辦人決定）</b><ol>${z.handover.map((q) => `<li>${q}</li>`).join("")}</ol></div>` +
      `<div class="prov" style="margin-top:10px">${z.provenance}</div></div>`;
  }
  body.innerHTML = html;
}
renderScenario(FIX.scenarios[0]);

/* ── demo 頁序（hash 路由）：#t1-hist / #t2-real / #t2-fake / #t3-a|b|c ── */
function applyHash() {
  const h = location.hash.slice(1);
  if (!h) return;
  const [tab, arg] = h.split("-");
  const btn = document.querySelector(`.tabbtn[data-tab="${tab}"]`);
  if (btn) btn.click();
  if (tab === "t1" && arg === "hist") $("#f-load-hist").click();
  if (tab === "t2" && arg === "real") $("#c-load-real").click();
  if (tab === "t2" && arg === "fake") $("#c-load-fake").click();
  if (tab === "t3" && arg) {
    const idx = { a: 0, b: 1, c: 2 }[arg];
    if (idx !== undefined) tabsEl.querySelectorAll(".scen")[idx].click();
  }
}
window.addEventListener("hashchange", applyHash);
applyHash();
