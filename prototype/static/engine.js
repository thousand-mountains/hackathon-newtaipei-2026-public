/* 訴願期間計算引擎（JS 鏡像版）。以 data/test-vectors.json 與 Python 版鎖定零分歧。
   法源註解見 engine/deadline.py。日期一律以 UTC 正午錨定避免時區位移。 */
const PERIOD_DAYS = 30;

function d(iso) { return new Date(iso + "T12:00:00Z"); }
function iso(date) { return date.toISOString().slice(0, 10); }
function addDays(date, n) { const x = new Date(date); x.setUTCDate(x.getUTCDate() + n); return x; }
function roc(date) { return `${date.getUTCFullYear() - 1911}/${date.getUTCMonth() + 1}/${date.getUTCDate()}`; }

function computeDeadline({ method, service, filing = null, transit = 0, interested = false }) {
  const r = { effective_date: null, deadline: null, overdue: null, steps: [], caveats: [] };
  if (method === "public") {
    r.caveats.push("公示送達：生效日為公告期滿日之認定涉及公告方式與刊登日，本引擎不自動判定，請人工確認後改以「本人簽收」模式輸入生效日。");
    return r;
  }
  const eff = d(service);
  if (method === "deposit") {
    r.steps.push({ rule: "寄存送達自寄存之日起發生效力（不論實際何時領取）", basis: "行政程序法 74；最高行 108 判 531 意旨", value: `生效日 = ${roc(eff)}` });
  } else {
    r.steps.push({ rule: "本人簽收，送達即生效", basis: "行政程序法 72", value: `生效日 = ${roc(eff)}` });
  }
  r.effective_date = iso(eff);

  const start = addDays(eff, 1);
  r.steps.push({ rule: "自送達生效之次日起算（始日不計入）", basis: "訴願法 14 I；民法 120 II", value: `起算日 = ${roc(start)}` });

  let deadline = addDays(start, PERIOD_DAYS - 1);
  r.steps.push({ rule: `法定期間 ${PERIOD_DAYS} 日`, basis: "訴願法 14 I", value: `第 ${PERIOD_DAYS} 日 = ${roc(deadline)}` });

  if (transit > 0) {
    deadline = addDays(deadline, transit);
    r.steps.push({ rule: `扣除在途期間 ${transit} 日（訴願人不在受理機關所在地）`, basis: "訴願法 16；訴願扣除在途期間辦法（日數由查表提供）", value: `加計後 = ${roc(deadline)}` });
  }

  let extended = false;
  while (deadline.getUTCDay() === 6 || deadline.getUTCDay() === 0) { deadline = addDays(deadline, 1); extended = true; }
  if (extended) r.steps.push({ rule: "期滿日為星期六/日，以次一工作日代之", basis: "訴願法 17 → 民法 122", value: `順延後期滿日 = ${roc(deadline)}` });
  r.deadline = iso(deadline);
  r.steps.push({ rule: "期滿日確定", basis: "以上各步", value: `訴願期間至 ${roc(deadline)} 屆滿` });

  if (filing) {
    const f = d(filing);
    r.overdue = f.getTime() > deadline.getTime();
    r.steps.push({ rule: "以受理機關收受訴願書之日為提起日", basis: "訴願法 14 III", value: `提起日 ${roc(f)} ${r.overdue ? ">" : "≤"} 期滿日 → ${r.overdue ? "逾期" : "未逾期"}` });
  }

  if (interested) r.caveats.push("訴願人主張為利害關係人：期間自「知悉時」起算（訴願法 14 II），知悉時點屬事實認定，本結果僅供對照，請人工確認。");
  r.caveats.push("國定假日順延未納入（v0 僅處理週六日），期滿日落在國定假日者請對照行政機關辦公日曆。");
  if (r.overdue) {
    r.caveats.push("縱屬逾期，原處分顯屬違法或不當者，機關仍得依職權撤銷或變更（訴願法 80），逾期≠案件終結。");
    r.caveats.push("訴願人如有不可歸責事由，得申請回復原狀（訴願法 15），是否受理屬人工判斷。");
  }
  return r;
}
if (typeof module !== "undefined") module.exports = { computeDeadline };
