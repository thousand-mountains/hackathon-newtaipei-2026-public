// 工作台狀態與互動邏輯（單例 store）— 移植自 design 的命令式 JS，改為 Vue 響應式
import { reactive, computed } from 'vue'
import { LAWDB, lawById } from '../data/lawdb.js'
import { CASEDB, caseById, isCase } from '../data/caseDb.js'
import { FACTDB, isFact } from '../data/factDb.js'
import { PROC_MAINS_C, PROC_KUAN, PROC_NOTE } from '../data/procedure.js'
import { buildDOC, mainText as mainTextOf } from '../data/docBuilders.js'
import { lawKeyPoint } from '../utils/lawRender.js'

const REWRITE = {
  精簡: (t) => t.replace(/，惟/g, '，但').replace(/尚難以/g, '難以').replace(/係指/g, '指').replace(/云云/g, '').replace(/，並無溯及適用之情事，/g, '，無溯及適用，'),
  加強: (t) => t.replace(/。$/, '，核與卷附事證相符，堪予認定。'),
  回應: (t) => t.replace(/。$/, '；訴願人所執前詞，均難謂有據，附此敘明。'),
  語氣: (t) => t.replace(/不符/g, '尚有未合').replace(/沒有/g, '並無'),
}
export const CHIP_LABEL = { 精簡: '精簡', 加強: '加強論述', 回應: '補強對訴願主張之回應', 語氣: '改為公文正式語氣' }

export const state = reactive({
  page: 'up', // 'up' | 'work'
  caseno: '',
  // 程序審查
  PROC: { con: 'A', kuan: '6', main: 0, dkuan: '8' },
  // 依據選用
  PICK: { laws: ['訴77', '訴1', '訴2', '訴3', '洗22', '訴79', '訴81', '審26'], cases: ['C1', 'C2', 'C3', 'C4', 'C5'], user: [] },
  // 文件
  DOC: [],
  SENTS: [],
  selId: null,
  hitRef: null, // 目前高亮的依據 id（左側卡）
  mode: 'edit', // 'edit' | 'view'
  activeTab: 'fact', // 'fact' | 'law' | 'case'
  // 撰稿 AI
  chatAll: true,
  chat: [], // {who:'ai'|'me', html}
  // 對話框
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

// ---- 文件組裝 ----
export function buildDoc(regen) {
  state.DOC = buildDOC(state.PROC)
  const sents = []
  state.DOC.forEach((b) => (b.ss || []).forEach((s) => { s.orig = s.t; s.ver = 0; sents.push(s) }))
  state.SENTS = sents
  state.selId = null
  state.hitRef = null
  if (!regen) {
    state.chat = []
    state.chatAll = true
    pushMsg('ai', '草稿已生成，共 ' + state.SENTS.length + ' 句，每一句都已連結到左側的案情、法條或案例。<br>我只做文字層面的潤飾與改寫；要增刪法條，請切到「相關法條」分頁用「加入法條」。')
  }
}

export function startWork() {
  state.page = 'work'
  state.caseno = '案號 1147101517　·　違反洗錢防制法事件'
  state.PROC.con = 'A'; state.PROC.kuan = '6'; state.PROC.main = 0; state.PROC.dkuan = '8'
  buildDoc()
}

// ---- 相關法條欄（右欄表頭「相關法條」）：只列草稿實際引用到的法條 ----
export const lawLines = computed(() => {
  const cited = new Set()
  state.SENTS.forEach((s) => s.refs.forEach((r) => cited.add(r)))
  let laws = state.PICK.laws.map(lawById).filter((l) => l && cited.has(l.id))
  if (!laws.length) laws = state.PICK.laws.map(lawById).filter(Boolean)
  const g = {}
  laws.forEach((l) => { (g[l.law] = g[l.law] || []).push(l.art.replace(/[^0-9\-]/g, '')) })
  return Object.keys(g).map((k) => k + '　第 ' + [...new Set(g[k])].sort((a, b) => parseInt(a) - parseInt(b)).join('、') + ' 條')
})

// ---- 左欄卡片資料（供 template v-for） ----
export const lawCards = computed(() =>
  state.PICK.laws.map(lawById).filter(Boolean).sort((a, b) => a.rank - b.rank).map((l) => ({
    ...l,
    users: state.SENTS.filter((s) => s.refs.includes(l.id)).length,
    mine: state.PICK.user.includes(l.id),
    keyPoint: lawKeyPoint(l),
  })),
)
export const caseCards = computed(() =>
  state.PICK.cases.map(caseById).filter(Boolean).map((c) => ({
    ...c,
    users: state.SENTS.filter((s) => s.refs.includes(c.id)).length,
  })),
)
export const factCards = computed(() =>
  FACTDB.map((f) => ({ ...f, users: state.SENTS.filter((s) => s.refs.includes(f.id)).length })),
)
export const wordCount = computed(() => state.SENTS.reduce((n, s) => n + s.t.replace(/\s/g, '').length, 0))

// ---- 程序審查 ----
export const procKuanOptions = PROC_KUAN.map((k) => ({ value: k[0], label: '第 ' + k[0] + ' 款　' + k[1] }))
export const procMainOptions = PROC_MAINS_C.map((m, i) => ({ value: i, label: m }))
export const procNote = computed(() => PROC_NOTE[state.PROC.con])
export const mainPreview = computed(() => mainTextOf(state.PROC))

export function onProcChange() {
  buildDoc(true)
  state.activeTab = 'fact'
  const lbl = { A: '版式 A · 程序不受理', B: '版式 B · 實體駁回', C: '版式 C · 撤銷／撤銷另處', D: '版式 D · 部分駁回部分不受理' }[state.PROC.con]
  toast('已依「' + lbl + '」重新生成決定書')
}

// ---- 分頁 ----
export function openTab(t) {
  state.activeTab = t
}
export const showAddLaw = computed(() => state.activeTab === 'law')

// ---- 句 ↔ 依據 ----
export function selectSent(id) {
  const s = state.SENTS.find((x) => x.id === id)
  if (!s) return
  state.selId = id
  // 高亮第一個依據卡
  state.hitRef = s.refs.length ? s.refs[0] : null
  if (s.refs.length) {
    const r = s.refs[0]
    state.activeTab = r === 'proc' || isFact(r) ? 'fact' : isCase(r) ? 'case' : 'law'
    scrollToRef(r)
  }
  state.chatAll = false
}

export function clearSel() {
  state.selId = null
  state.hitRef = null
  state.chatAll = true
}

// 連結依據卡 → 右側引用句（點卡片／程序審查）
export function linkToSentences(refId) {
  const users = state.SENTS.filter((s) => s.refs.includes(refId))
  state.selId = null
  state.hitRef = refId
  if (!users.length) {
    toast('草稿中尚無句子引用此依據')
    return
  }
  // 用 selId 集合表示多句選取：改以一個 Set 記錄
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

// 點句子時同步清掉多句選取
export function pickSent(id) {
  state.selSet = new Set()
  selectSent(id)
}
export function editSent(id, text) {
  const s = state.SENTS.find((x) => x.id === id)
  if (s) s.t = text
}

// ---- 加入 / 移除法條 ----
export function addLaws(ids) {
  state.PICK.laws = [...state.PICK.laws, ...ids]
  state.PICK.user = [...state.PICK.user, ...ids]
  state.activeTab = 'law'
  toast('已加入 ' + ids.length + ' 條法條')
}
export function removeLaw(id) {
  state.PICK.laws = state.PICK.laws.filter((x) => x !== id)
  state.PICK.user = state.PICK.user.filter((x) => x !== id)
  state.SENTS.forEach((s) => (s.refs = s.refs.filter((x) => x !== id)))
  toast('已移除該法條')
}
export function lawUsedCount(id) {
  return state.SENTS.filter((s) => s.refs.includes(id)).length
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

// ---- 撰稿 AI ----
export function pushMsg(who, html) {
  state.chat.push({ who, html })
}
function applyOne(s, after) {
  if (after === s.t) return false
  s.t = after
  s.ver = (s.ver || 0) + 1 // 程式改寫才 bump，使 DOM 重新渲染（使用者打字不 bump）
  flashSent(s.id)
  return true
}
function flashSent(id) {
  requestAnimationFrame(() => {
    const el = document.querySelector('#sheet .sent[data-id="' + id + '"]')
    if (el) {
      el.classList.add('flash')
      setTimeout(() => el.classList.remove('flash'), 1200)
    }
  })
}
export function doRewrite(kind, free) {
  if (state.mode !== 'edit') {
    pushMsg('ai', '目前是閱覽模式，請切換到編輯模式後我才能改稿。')
    return
  }
  if (!kind && /法條|條文|加.*條|引用.*法/.test(free || '')) {
    pushMsg('ai', '增刪法條不在我的職責內——我只做文字層面的潤飾。<br>請用左上角的<b>「加入法條」</b>，加入後再回來請我把它寫進理由欄。')
    return
  }
  const fn = kind
    ? REWRITE[kind]
    : /拆|兩句|太長/.test(free || '')
      ? (t) => t.replace(/，(?=[^，]{12,})/, '。')
      : REWRITE.精簡
  if (state.chatAll) {
    const targets = state.SENTS.filter((s) => !/^(訴願駁回|訴願不受理|原處分撤銷)/.test(s.t))
    let n = 0
    targets.forEach((s) => { if (applyOne(s, fn(s.t))) n++ })
    pushMsg('ai', n
      ? '已對全文 ' + targets.length + ' 句套用「' + (CHIP_LABEL[kind] || '精簡') + '」，其中 <b>' + n + ' 句</b>有實際變動。主文未動。'
      : '全文掃過一遍，沒有需要調整的句子。')
    return
  }
  const s = state.selId ? state.SENTS.find((x) => x.id === state.selId) : null
  if (!s) {
    pushMsg('ai', '請先在右側點選一句。')
    return
  }
  const before = s.t
  if (!applyOne(s, fn(before))) {
    pushMsg('ai', '這句已經很精簡，我沒有做更動。')
    return
  }
  pushMsg('ai', '已改寫。<span class="qt">原文：' + before + '</span><div class="diff">' + s.t + '</div>')
}
export function chatChip(kind) {
  pushMsg('me', (state.chatAll ? '（全文）' : '（本句）') + CHIP_LABEL[kind])
  setTimeout(() => doRewrite(kind), 380)
}
export function chatSend(text) {
  const v = (text || '').trim()
  if (!v) return
  pushMsg('me', v)
  setTimeout(() => doRewrite(null, v), 380)
}
export const selectedSentText = computed(() => {
  const s = state.selId ? state.SENTS.find((x) => x.id === state.selId) : null
  return s ? s.t : ''
})

// ---- 下載 ----
export function docHTMLString() {
  let h = ''
  state.DOC.forEach((b) => {
    if (b.ty === 'gap') { h += '<p>　</p>'; return }
    if (b.ty === 'title') { h += '<div class="t"><span>新北市政府訴願決定書</span><span>案號：1147101517 號</span></div>'; return }
    if (b.ty === 'meta') { h += '<p class="m">' + b.text + '</p>'; return }
    if (b.ty === 'h') { h += '<h4>' + b.text + '</h4>'; return }
    const num = /^[一二三四五六]、/.test((b.ss && b.ss[0] && b.ss[0].t) || '') ? ' class="num"' : ''
    h += '<p' + num + '>' + b.ss.map((s) => s.t).join('') + '</p>'
  })
  return h
}
export function downloadWord() {
  const css =
    'body{font-family:"標楷體",serif;font-size:14pt;line-height:2.2}' +
    '.t{display:flex;justify-content:space-between;font-size:16pt;font-weight:700}' +
    'h4{font-size:14pt;letter-spacing:.5em;padding-left:2em;margin:0}' +
    'p{margin:0;text-align:justify}p.num{padding-left:2.1em;text-indent:-2.1em}p.m{padding-left:2em}'
  const html =
    '<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word">' +
    '<head><meta charset="utf-8"><style>' + css + '</style></head><body>' + docHTMLString() + '</body></html>'
  const blob = new Blob(['\ufeff' + html], { type: 'application/msword' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = '訴願決定書_1147101517.doc'
  a.click()
  URL.revokeObjectURL(a.href)
  toast('已下載 Word 檔')
}
export function downloadPdf() {
  toast('已開啟列印視窗，請選擇「另存為 PDF」')
  setTimeout(() => window.print(), 300)
}

// 段落 class（一、二、… 起首縮排）
export function pcls(b) {
  return /^[一二三四五六]、/.test((b.ss && b.ss[0] && b.ss[0].t) || '') ? 'num' : ''
}
