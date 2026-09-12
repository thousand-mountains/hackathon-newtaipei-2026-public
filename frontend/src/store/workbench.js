// 工作台狀態與互動邏輯（單例 store）。
//
// **資料來源是後端 payload，不是 src/data/*。** 那些 mock 只在「完全連不上後端」時
// 當明示的離線備援，而且畫面必須說出它是 mock（見 state.conn）。
// run 失敗時**不得**退回 mock 演完全程——那是後端活著、只是這次跑失敗了。

import { reactive, computed } from 'vue'
import { LAWDB, lawById } from '../data/lawdb.js'
import { CASEDB, caseById, isCase } from '../data/caseDb.js'
import { FACTDB, isFact } from '../data/factDb.js'
import { PROC_MAINS_C, PROC_KUAN, PROC_NOTE } from '../data/procedure.js'
import { buildDOC, mainText as mainTextOf } from '../data/docBuilders.js'
import { lawKeyPoint } from '../utils/lawRender.js'
import { getHealth, listCases, createCase, submitCase, ApiError } from '../api/client.js'
import { executeRun } from '../api/run.js'
import { adaptPayload, toProvenance, RELEVANCE_UNKNOWN } from '../api/adapt.js'

export { RELEVANCE_UNKNOWN }

export const CHIP_LABEL = { 精簡: '精簡', 加強: '加強論述', 回應: '補強對訴願主張之回應', 語氣: '改為公文正式語氣' }

// 「撰稿 AI」在本版**沒有接後端**。舊版那四個動作是寫死的正則字串替換
// （例如無條件在句尾補「核與卷附事證相符，堪予認定」——那是一句事實認定，
// 由正則加上去、沒有任何人看過卷證）。後端目前也沒有改寫端點可接。
// 所以這一版把它停用並照實說明，不讓它看起來像模型在改稿。
export const REWRITE_ENABLED = false
export const REWRITE_DISABLED_NOTE =
  '改寫功能尚未接上後端，本版停用。舊版的「改寫」是前端寫死的字串替換，不是模型產出——' +
  '讓它看起來像 AI 改稿會誤導，所以在後端提供改寫端點之前先停用。'

export const state = reactive({
  page: 'up', // 'up' | 'work'
  caseno: '',

  // ── 連線與執行狀態（兩者必須分開，見 US-3）────────────────────
  conn: {
    checked: false,
    online: false, // 後端連得上嗎
    health: null, // /api/health 的 body
    error: '', // 連不上的原因
    usingMock: false, // 是否退回離線 mock（**畫面必須明示**）
  },
  run: {
    phase: 'idle', // 'idle' | 'running' | 'done' | 'failed'
    progress: '',
    nodes: [], // 逐節點進度（SSE 有接到才會有）
    error: null, // {kind, message, node}
    runId: '',
    caseId: '',
    // 本次執行用的 confirmed_intake。**/submit 會重跑六節點，必須帶同一份**——
    // 不帶的話後端重跑時沒有人確認過欄位，`conclusion_requires_human` 會再次成立，
    // 於是「確認過的案子」在下載閘門前照樣被擋。2026-09-12 端到端實測抓到。
    confirmedIntake: null,
  },

  payload: null, // adaptPayload() 的結果
  provenance: null, // 三個誠實維度

  // 程序審查。**A（程序不合）由後端規則引擎判定**；B／C 是實體法律判斷，
  // 系統不做，由承辦人自行認定——畫面要說清楚（見 procConclusionOrigin）。
  PROC: { con: 'A', kuan: '6', main: 0, dkuan: '8' },
  PICK: { laws: [], cases: [], user: [] },

  DOC: [],
  SENTS: [],
  selId: null,
  hitRef: null,
  mode: 'edit',
  activeTab: 'fact',

  chatAll: true,
  chat: [],

  // 送出守門（US-4）。**submit_allowed 只認後端。**
  gate: { checking: false, checked: false, allowed: false, blockers: [], receipt: null, error: '' },

  toastMsg: '',
  toastOn: false,
})

let toastTimer = null
export function toast(m) {
  state.toastMsg = m
  state.toastOn = true
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (state.toastOn = false), 2200)
}

// ── 連線探測（**只探測，不觸發任何執行**，見 US-2）────────────────
export async function probeBackend() {
  try {
    const h = await getHealth()
    state.conn.checked = true
    state.conn.online = true
    state.conn.health = h.body
    state.conn.error = h.ok ? '' : '後端健康檢查未通過（見 checks）'
    state.provenance = toProvenance(h.body?.provenance)
    return h
  } catch (e) {
    state.conn.checked = true
    state.conn.online = false
    state.conn.health = null
    state.conn.error = e?.message || String(e)
    state.provenance = null
    return null
  }
}

export async function availableCases() {
  try {
    return (await listCases()).cases || []
  } catch {
    return []
  }
}

// ── 執行一次分析（**唯一會呼叫模型的入口**）──────────────────────
let inFlight = false

export async function runCase(caseId, { confirmedIntake } = {}) {
  if (inFlight) return // 互斥閘：連點兩次不得發兩個 run
  inFlight = true
  state.run.phase = 'running'
  state.run.error = null
  state.run.nodes = []
  state.run.progress = '準備中…'
  state.run.caseId = caseId
  state.gate = { checking: false, checked: false, allowed: false, blockers: [], receipt: null, error: '' }

  const body = {}
  if (confirmedIntake && Object.keys(confirmedIntake).length) body.confirmed_intake = confirmedIntake
  // 記住它：`/submit` 重跑時要帶同一份，否則守門看到的是「沒人確認過」的狀態
  state.run.confirmedIntake = body.confirmed_intake || null

  try {
    const payload = await executeRun(caseId, {
      body,
      onPhase: (s) => (state.run.progress = s),
      onProgress: (p) => {
        state.run.progress = p.label
        if (!state.run.nodes.some((n) => n.node === p.node)) state.run.nodes.push(p)
      },
    })
    applyPayload(payload)
    state.run.phase = 'done'
    state.run.progress = ''
    state.page = 'work'
    return payload
  } catch (e) {
    // **這裡是 US-3 的核心**：失敗一律停在錯誤狀態，不載入任何 mock。
    state.run.phase = 'failed'
    state.run.progress = ''
    state.run.error = {
      kind: e instanceof ApiError ? e.kind : 'unknown',
      // 連不上後端 vs 後端跑失敗：兩種狀態、兩種文案
      isOffline: e instanceof ApiError ? e.isOffline : false,
      message: e?.message || String(e),
      node: e?.node ?? null,
      status: e?.status ?? null,
    }
    return null
  } finally {
    inFlight = false
  }
}

export function applyPayload(payload) {
  const a = adaptPayload(payload)
  state.payload = a
  state.provenance = a.provenance || state.provenance
  state.run.runId = a.runId
  state.caseno = [a.caseId, a.intake?.no, a.intake?.type].filter(Boolean).join('　·　')
  state.DOC = a.doc
  state.SENTS = a.doc.flatMap((b) => b.ss || [])
  state.PICK.laws = a.laws.map((l) => l.id)
  state.PICK.cases = a.cases.map((c) => c.id)
  state.PICK.user = []
  state.selId = null
  state.hitRef = null
  state.selSet = new Set()
  state.conn.usingMock = false
  // 程序結論：後端有算出款次就用它；沒有就維持未判定，**不預設一個**
  state.PROC.kuan = a.procedure.clause ? String(a.procedure.clause).replace(/^77-/, '') : ''
  seedIntakeDraft(a.intake)
  state.chat = []
  state.chatAll = true
  pushMsg(
    'ai',
    '草稿已由後端生成，共 ' + state.SENTS.length + ' 句。<br>' +
      (REWRITE_ENABLED ? '' : REWRITE_DISABLED_NOTE),
  )
}

/** 離線備援：**只在完全連不上後端時使用，而且畫面必須標示。** */
export function loadOfflineMock() {
  state.conn.usingMock = true
  state.payload = null
  state.DOC = buildDOC(state.PROC)
  const sents = []
  state.DOC.forEach((b) => (b.ss || []).forEach((s) => { s.orig = s.t; s.ver = 0; sents.push(s) }))
  state.SENTS = sents
  state.PICK.laws = LAWDB.slice(0, 8).map((l) => l.id)
  state.PICK.cases = CASEDB.map((c) => c.id)
  state.page = 'work'
  state.caseno = '離線示範資料（未連上後端）'
}

// ── 上傳建案 ──────────────────────────────────────────────────
export async function uploadAndRun(files, { confirmedIntake } = {}) {
  state.run.phase = 'running'
  state.run.progress = '上傳卷證中…'
  try {
    const meta = await createCase(files)
    return await runCase(meta.case_id, { confirmedIntake })
  } catch (e) {
    state.run.phase = 'failed'
    state.run.error = {
      kind: e instanceof ApiError ? e.kind : 'unknown',
      isOffline: e instanceof ApiError ? e.isOffline : false,
      message: e?.message || String(e),
      node: null,
      status: e?.status ?? null,
    }
    return null
  }
}

// ── 承辦人確認抽取欄位（US-6／US-9）────────────────────────────
//
// 後端 `CONFIRMABLE_INTAKE_FIELDS` 的白名單（settings.py:381-384）。
// **`_apply_confirmed_intake()` 是白名單硬拒絕**：送任何不在名單內的 key
// 會 raise ValueError → HTTP 400，整次執行失敗。所以這份清單必須與後端一致，
// 前端不得自行擴充——要加欄位得後端先放行。
export const CONFIRMABLE_FIELDS = [
  ['no', '案號'], ['type', '案由'], ['person', '訴願人'], ['org', '原處分機關'],
  ['d1', '處分日期'], ['d2', '送達日期'], ['d3', '提起日期'], ['agent', '代理人'],
  ['service_method', '送達方式'], ['transit_days', '在途期間（天）'],
  ['interested_party', '利害關係人'], ['note', '備註／訴願主張'],
  ['respondent_name', '原處分相對人'],
]

// **必填欄位，而且「必填」是在前端實作的。**
// `respondent_name` 刻意**不放進 N1 的 REQUIRED_FIELDS**：後端實測放進去會讓
// `run_case()` 停在 NEEDS_INPUT、N2–N6 全不跑，而且承辦人填了也救不回來
// （`_apply_confirmed_intake` 跑在 N1 之後，N1 用自己的抽取結果重算 missing），
// 前端只從 n1 起跑、沒有逃生口 → 永久卡死。
// 所以擋在這裡：送出確認前驗證它非空。拿不到時後端的 §77-3 會回「需人工認定」。
export const REQUIRED_FIELDS = ['respondent_name']

// 目前沒有待放行欄位（`respondent_name` 已於 2026-09-12 進後端白名單，13 欄）。
export const PENDING_FIELDS = []

export const intakeDraft = reactive({})
export const pendingDraft = reactive({})

/** 把 payload 的 intake 灌進可編輯草稿。每次 applyPayload 後呼叫。 */
function seedIntakeDraft(intake) {
  for (const [k] of CONFIRMABLE_FIELDS) {
    const v = intake?.[k]
    intakeDraft[k] = v === null || v === undefined ? '' : v
  }
}

export const unconfirmedSet = computed(() => new Set(state.payload?.procedure?.unconfirmedFields || []))

/** 按下確認時會以「空值」送出的欄位——承辦人要知道他在背書「這裡本來就沒有內容」。 */
export const emptyFieldsToConfirm = computed(() =>
  CONFIRMABLE_FIELDS.filter(([k]) => {
    const v = intakeDraft[k]
    return v === '' || v === undefined || v === null
  }).map(([, label]) => label),
)

/**
 * 日期順序的**純邏輯**檢查——不是法律判斷，只是時序上不可能的組合。
 *
 * 存在的理由是一次實跑事故（2026-09-12，也是後端 settings.py:393 記載的同一現象）：
 * N1 把 `d2`（送達日）抽成提起日的值、`d3` 抽成送達日的值，conf 還給 1.0。
 * 兩個日期對調之後期間引擎照樣算得出漂亮的算式與綠燈——**引擎沒錯，錯的是輸入**，
 * 而那六句期間計算是全畫面最像已經驗證過的東西。
 *
 * 這裡只擋「時序不可能」：處分日不該晚於送達日、送達日不該晚於提起日。
 * 擋不住「兩個都錯但順序合理」的情形，所以它是提示不是保證。
 */
export const dateOrderWarnings = computed(() => {
  const out = []
  const d = (k) => {
    const v = intakeDraft[k]
    return v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : null
  }
  const d1 = d('d1'), d2 = d('d2'), d3 = d('d3')
  if (d1 && d2 && d1 > d2) out.push(`處分日期（${d1}）晚於送達日期（${d2}）——處分不可能在送達之後作出。`)
  if (d2 && d3 && d2 > d3) {
    out.push(
      `送達日期（${d2}）晚於提起日期（${d3}）——不可能在收到處分前就提起訴願。` +
      `**這兩欄很可能被抽反了**，請對照卷證確認；期間計算會照你確認的值算，算式漂亮不代表輸入正確。`,
    )
  }
  return out
})

/** 確認過一輪之後仍未解除的欄位。**有值就要在畫面上說出來，不能默默顯示「尚未確認」。** */
export const stillUnconfirmed = computed(() => {
  if (!state.run.confirmedIntake) return []          // 還沒確認過，不算異常
  return state.payload?.procedure?.unconfirmedFields || []
})

/**
 * 送出確認並重跑。**「確認」的定義是「人看過那個值」**——即使一個字都沒改
 * 也要送，因為差別在於現在有人為它背書了（後端 graph.py:143-145 的說明）。
 */
export async function confirmIntakeAndRerun() {
  const caseId = state.payload?.caseId
  if (!caseId) {
    toast('尚無案件可確認')
    return null
  }
  // 必填檢查。**擋在送出前，不是擋在 N1**（見 REQUIRED_FIELDS 的說明）。
  const missing = REQUIRED_FIELDS.filter((k) => {
    const v = intakeDraft[k]
    return v === '' || v === undefined || v === null
  })
  if (missing.length) {
    const labels = missing.map((k) => (CONFIRMABLE_FIELDS.find(([kk]) => kk === k) || [k, k])[1])
    toast(`請先填寫必填欄位：${labels.join('、')}`)
    return null
  }

  // **空欄位也要送。** 舊版跳過空值，結果模型沒抽到的欄位永遠無法被確認——
  // 而 `note` 屬於 BLOCK_DECISION_INPUT_FIELDS，於是結論封鎖永遠解不開，
  // 畫面只是默默又顯示「尚未確認」、不說原因。2026-09-12 端到端實測抓到。
  //
  // 送空字串而不是 null：後端**刻意拒絕 null**（判斷卡 7，「送一個空值就能宣稱
  // 有人確認過」正是它要防的）。空字串代表「承辦人看過，確認它本來就沒有內容」，
  // 那是一個真實的人工判斷——但**必須讓承辦人知道他在背書什麼**，
  // 所以 `emptyFieldsToConfirm` 會把這些欄位列在畫面上。
  const body = {}
  for (const [k] of CONFIRMABLE_FIELDS) {
    let v = intakeDraft[k]
    if (v === undefined || v === null) v = ''
    if (k === 'transit_days') {
      if (v === '') { body[k] = 0; continue }
      const n = parseInt(v, 10)
      if (Number.isNaN(n)) { toast('在途期間必須是整數'); return null }
      v = n
    }
    if (k === 'interested_party') v = v === true || v === 'true'
    body[k] = v
  }
  return runCase(caseId, { confirmedIntake: body })
}

// ── 送出守門（US-4）──────────────────────────────────────────
// **前端不自行判定可否送出。** 這裡打的是後端的 /submit，它會重跑六節點再決定，
// 回應裡明寫「未採信前端送來的任何判斷」。409 不是錯誤，是帶著理由的拒絕。
export async function checkSubmitGate() {
  const caseId = state.payload?.caseId
  if (!caseId) {
    state.gate.error = '沒有可送出的案件（尚未執行過分析）'
    return false
  }
  state.gate.checking = true
  state.gate.error = ''
  try {
    // **帶上本次執行用的 confirmed_intake。** /submit 是後端重跑六節點再判斷，
    // 不帶就等於送一份「沒人確認過欄位」的請求去複核，結論封鎖會再次成立。
    const sbody = {}
    if (state.run.confirmedIntake) sbody.confirmed_intake = state.run.confirmedIntake
    const { accepted, body } = await submitCase(caseId, sbody)
    state.gate.checked = true
    state.gate.allowed = accepted
    state.gate.blockers = body?.blockers || []
    state.gate.receipt = accepted ? body : null
    return accepted
  } catch (e) {
    state.gate.checked = true
    state.gate.allowed = false
    state.gate.error = e?.message || String(e)
    return false
  } finally {
    state.gate.checking = false
  }
}

// ── 文件（保留舊簽名，離線 mock 才會用到）────────────────────────
export function buildDoc(regen) {
  loadOfflineMock()
  if (!regen) {
    state.chat = []
    state.chatAll = true
  }
}

export function startWork() {
  state.page = 'work'
}

// ── 相關法條欄 ────────────────────────────────────────────────
export const lawLines = computed(() => {
  const cited = new Set()
  state.SENTS.forEach((s) => (s.refs || []).forEach((r) => cited.add(r)))
  const src = state.payload ? state.payload.laws : state.PICK.laws.map(lawById).filter(Boolean)
  const used = src.filter((l) => cited.has(l.id))
  const laws = used.length ? used : src
  const g = {}
  laws.forEach((l) => {
    const key = l.law || ''
    const art = String(l.art || l.article || '').replace(/[^0-9\-]/g, '')
    if (!key || !art) return
    ;(g[key] = g[key] || []).push(art)
  })
  return Object.keys(g).map(
    (k) => k + '　第 ' + [...new Set(g[k])].sort((a, b) => parseInt(a) - parseInt(b)).join('、') + ' 條',
  )
})

// ── 左欄卡片（優先用 payload；沒有 payload 才用離線 mock）──────────
const countUsers = (id) => state.SENTS.filter((s) => (s.refs || []).includes(id)).length

export const lawCards = computed(() => {
  if (state.payload) {
    return state.payload.laws.map((l) => ({ ...l, users: countUsers(l.id) }))
  }
  return state.PICK.laws
    .map(lawById)
    .filter(Boolean)
    .sort((a, b) => a.rank - b.rank)
    .map((l) => ({
      ...l,
      art: l.art,
      users: countUsers(l.id),
      mine: state.PICK.user.includes(l.id),
      keyPoint: lawKeyPoint(l),
      verify: { label: '離線示範資料', lampClass: 'none', why: '未連上後端，此為內建示範資料。' },
      relevance: RELEVANCE_UNKNOWN,
    }))
})

export const caseCards = computed(() => {
  if (state.payload) {
    return state.payload.cases.map((c) => ({ ...c, users: countUsers(c.id) }))
  }
  return state.PICK.cases
    .map(caseById)
    .filter(Boolean)
    .map((c) => ({ ...c, users: countUsers(c.id), provenanceLabel: '離線示範資料', relevance: RELEVANCE_UNKNOWN }))
})

export const factCards = computed(() => {
  const src = state.payload ? state.payload.facts : FACTDB
  return src.map((f) => ({ ...f, users: countUsers(f.id) }))
})

export const wordCount = computed(() => state.SENTS.reduce((n, s) => n + s.t.replace(/\s/g, '').length, 0))

// ── 程序審查 ──────────────────────────────────────────────────
export const procKuanOptions = PROC_KUAN.map((k) => ({ value: k[0], label: '第 ' + k[0] + ' 款　' + k[1] }))
export const procMainOptions = PROC_MAINS_C.map((m, i) => ({ value: i, label: m }))

/** 後端規則引擎算出來的程序審查結果（可驗算層）。沒有 payload 就是沒有，不編。 */
export const procedure = computed(() => state.payload?.procedure || null)

/**
 * 這個結論是誰認定的——**畫面上必須說得出來**。
 * A（程序不合）可由規則引擎的期間計算與 77 條款次支撐；
 * B／C（實體有無理由）是法律判斷，系統不做，是承辦人自己選的。
 */
export const procConclusionOrigin = computed(() => {
  const c = state.payload?.procedure?.conclusion
  // 後端只在「程序不合 → 不受理」這一種給出結論（value 只可能是 "A" 或 null）。
  // 承辦人選的若正好等於後端判定的那一個，才算規則引擎判定；其餘一律是人自己選的。
  if (c && c.by === 'rule' && c.value && c.value === state.PROC.con) {
    return { by: 'rule', label: '規則引擎判定', text: c.why || c.basis || '' }
  }
  if (c && c.by === 'unavailable' && state.PROC.con === 'A') {
    return { by: 'human', label: '承辦人自行認定', text: c.why || '後端本次未判定不受理款次。' }
  }
  return {
    by: 'human',
    label: '承辦人自行認定',
    text:
      '訴願有無理由屬實體法律判斷，本系統不代為認定。此選項是承辦人自己選的，' +
      '不是系統判定的結果。',
  }
})

export const procNote = computed(() => {
  const p = state.payload?.procedure
  if (!p) return PROC_NOTE[state.PROC.con]
  const out = []
  if (p.effectiveDate) out.push(`送達生效日 ${p.effectiveDate}`)
  if (p.deadline) out.push(`期滿日 ${p.deadline}　${p.overdue ? '→ 已逾期' : '→ 未逾期'}`)
  if (p.clause) out.push(`訴願法第 77 條命中款次：${p.clause}`)
  if (p.notAutoScreenedReason) out.push(p.notAutoScreenedReason)
  for (const c of p.caveats) out.push('引擎提醒：' + c)
  return out.length ? out : PROC_NOTE[state.PROC.con]
})

/** 主文預覽：**有 payload 時一律以後端為準**，被封鎖就說被封鎖，不用模板補一句。 */
export const mainPreview = computed(() => {
  const p = state.payload
  if (!p) return mainTextOf(state.PROC)
  if (p.procedure.requiresHumanConclusion) {
    return '系統未生成結論段——本案結論涉及法律判斷，已停止生成（C 型封鎖）。請依交接卡認定後自行完成。'
  }
  const conc = state.SENTS.filter((s) => s.slot === 'conclusion')
  return conc.length ? conc.map((s) => s.t).join('') : '後端本次未產出結論段。'
})

/** 變更審查結論**不再重新生成草稿**——草稿只能來自後端。 */
export function onProcChange() {
  state.activeTab = 'fact'
  const o = procConclusionOrigin.value
  toast(o.by === 'human' ? '已記錄承辦人認定（不影響後端草稿）' : '已套用規則引擎判定')
}

// ── 分頁 ──────────────────────────────────────────────────────
export function openTab(t) {
  state.activeTab = t
}
export const showAddLaw = computed(() => state.activeTab === 'law')

// ── 句 ↔ 依據 ─────────────────────────────────────────────────
const isCaseRef = (r) => (state.payload ? state.payload.cases.some((c) => c.id === r) : isCase(r))
const isFactRef = (r) => (state.payload ? state.payload.facts.some((f) => f.id === r) : isFact(r))

export function selectSent(id) {
  const s = state.SENTS.find((x) => x.id === id)
  if (!s) return
  state.selId = id
  state.hitRef = (s.refs || []).length ? s.refs[0] : null
  if ((s.refs || []).length) {
    const r = s.refs[0]
    state.activeTab = r === 'proc' || isFactRef(r) ? 'fact' : isCaseRef(r) ? 'case' : 'law'
    scrollToRef(r)
  }
  state.chatAll = false
}

export function clearSel() {
  state.selId = null
  state.hitRef = null
  state.chatAll = true
}

export function linkToSentences(refId) {
  const users = state.SENTS.filter((s) => (s.refs || []).includes(refId))
  state.selId = null
  state.hitRef = refId
  if (!users.length) {
    toast('草稿中尚無句子引用此依據')
    return
  }
  state.selSet = new Set(users.map((u) => u.id))
  scrollToSent(users[0].id)
}
state.selSet = new Set()

export function isSentSelected(id) {
  if (state.selId === id) return true
  return state.selSet && state.selSet.has(id)
}

function scrollToRef(refId) {
  requestAnimationFrame(() => {
    const el = document.getElementById('ref-' + refId)
    if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  })
}
function scrollToSent(id) {
  requestAnimationFrame(() => {
    const el = document.querySelector('#sheet .sent[data-id="' + id + '"]')
    if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  })
}

export function pickSent(id) {
  state.selSet = new Set()
  selectSent(id)
}
export function editSent(id, text) {
  const s = state.SENTS.find((x) => x.id === id)
  if (s) s.t = text
}

// ── 加入／移除法條 ────────────────────────────────────────────
// 手動加入的法條**不進引用驗證**（後端沒看過它），卡片要標示清楚。
export function addLaws(ids) {
  state.PICK.laws = [...state.PICK.laws, ...ids]
  state.PICK.user = [...state.PICK.user, ...ids]
  if (state.payload) {
    const add = ids.map(lawById).filter(Boolean).map((l) => ({
      id: l.id,
      law: l.law,
      art: l.art,
      title: l.law + l.art,
      keyPoint: lawKeyPoint(l),
      mine: true,
      users: 0,
      verify: { label: '未經後端驗證', lampClass: 'none', verified: false, why: '由承辦人手動加入，後端本次執行未檢索到此條，引用驗證未涵蓋。' },
      relevance: RELEVANCE_UNKNOWN,
    }))
    state.payload.laws = [...state.payload.laws, ...add]
  }
  state.activeTab = 'law'
  toast('已加入 ' + ids.length + ' 條法條（未經後端驗證）')
}
export function removeLaw(id) {
  state.PICK.laws = state.PICK.laws.filter((x) => x !== id)
  state.PICK.user = state.PICK.user.filter((x) => x !== id)
  if (state.payload) state.payload.laws = state.payload.laws.filter((l) => l.id !== id)
  state.SENTS.forEach((s) => (s.refs = (s.refs || []).filter((x) => x !== id)))
  toast('已移除該法條')
}
export function lawUsedCount(id) {
  return countUsers(id)
}
export function lawSearch(q) {
  q = q.trim().replace(/\s+/g, '')
  const fuzzy = (l) => {
    if (!q) return true
    const hay = (l.law + l.art + l.tag + l.id + l.paras.map((p) => p.t + (p.ks || []).map((k) => k.t).join('')).join('')).replace(/\s/g, '')
    const num = q.match(/\d+/g) || []
    const zh = q.replace(/\d+/g, '')
    return (!zh || [...zh].every((c) => hay.includes(c))) && (!num.length || num.every((n) => l.art.includes(n) || l.id.includes(n)))
  }
  return LAWDB.filter((l) => !state.PICK.laws.includes(l.id) && fuzzy(l))
}

// ── 撰稿 AI（本版停用，見 REWRITE_DISABLED_NOTE）──────────────────
export function pushMsg(who, html) {
  state.chat.push({ who, html })
}
export function doRewrite() {
  pushMsg('ai', REWRITE_DISABLED_NOTE)
}
export function chatChip(kind) {
  pushMsg('me', (state.chatAll ? '（全文）' : '（本句）') + CHIP_LABEL[kind])
  setTimeout(() => doRewrite(), 300)
}
export function chatSend(text) {
  const v = (text || '').trim()
  if (!v) return
  pushMsg('me', v)
  setTimeout(() => doRewrite(), 300)
}
export const selectedSentText = computed(() => {
  const s = state.selId ? state.SENTS.find((x) => x.id === state.selId) : null
  return s ? s.t : ''
})

// ── 下載（**必須先過後端守門**）────────────────────────────────
export function docHTMLString() {
  let h = ''
  state.DOC.forEach((b) => {
    if (b.ty === 'gap') { h += '<p>　</p>'; return }
    if (b.ty === 'title') { h += '<div class="t"><span>' + (b.text || '訴願決定書') + '</span></div>'; return }
    if (b.ty === 'meta') { h += '<p class="m">' + (b.text || '') + '</p>'; return }
    if (b.ty === 'h') { h += '<h4>' + (b.text || '') + '</h4>'; return }
    const num = /^[一二三四五六]、/.test((b.ss && b.ss[0] && b.ss[0].t) || '') ? ' class="num"' : ''
    h += '<p' + num + '>' + (b.ss || []).map((s) => s.t).join('') + '</p>'
  })
  return h
}

/** 下載前一律先問後端。**409 就不下載**，並把 blockers 顯示出來。 */
async function guardDownload() {
  if (state.conn.usingMock) {
    toast('離線示範資料不提供下載')
    return false
  }
  const ok = await checkSubmitGate()
  if (!ok) toast('後端拒絕送出，已列出原因')
  return ok
}

export async function downloadWord() {
  if (!(await guardDownload())) return
  const css =
    'body{font-family:"標楷體",serif;font-size:14pt;line-height:2.2}' +
    '.t{display:flex;justify-content:space-between;font-size:16pt;font-weight:700}' +
    'h4{font-size:14pt;letter-spacing:.5em;padding-left:2em;margin:0}' +
    'p{margin:0;text-align:justify}p.num{padding-left:2.1em;text-indent:-2.1em}p.m{padding-left:2em}'
  const html =
    '<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word">' +
    '<head><meta charset="utf-8"><style>' + css + '</style></head><body>' + docHTMLString() + '</body></html>'
  const blob = new Blob(['﻿' + html], { type: 'application/msword' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = '訴願決定書_' + (state.payload?.caseId || 'draft') + '.doc'
  a.click()
  URL.revokeObjectURL(a.href)
  toast('已下載 Word 檔')
}

export async function downloadPdf() {
  if (!(await guardDownload())) return
  toast('已開啟列印視窗，請選擇「另存為 PDF」')
  setTimeout(() => window.print(), 300)
}

export function pcls(b) {
  return /^[一二三四五六]、/.test((b.ss && b.ss[0] && b.ss[0].t) || '') ? 'num' : ''
}
