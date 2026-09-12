// 訴願智慧輔助平台 — 應用狀態與辦案流程（單例 reactive store）
// 移植自 design/訴願智慧輔助平台.html 的命令式 JS，改為 Vue 響應式模型。
import { reactive, computed } from 'vue'
import { CASE_NO, EVIDENCE_POOL, LAW_POOL, CASE_POOL, TOOLS, GROUPS, ACKS } from '../data/data.js'
import { GNODES, GEDGES, DRAFT_HTML } from '../data/graph.js'

const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const pick = (a) => a[Math.floor(Math.random() * a.length)]

let uid = 0
const nid = (p) => p + ++uid
let caseSeq = 0
const autoName = () => '新案件 ' + ++caseSeq
const isAutoName = (n) => /^新案件 \d+$/.test(n)

function newCaseObj(name, folderId) {
  return {
    id: nid('c'),
    name: name || autoName(),
    folderId: folderId || null,
    started: false,
    stream: [], // 對話串訊息陣列
    flags: { extract: false, cases: false, laws: false, graph: false, draft: false, out: false },
    docs: { evidence: [], cases: [], laws: [], out: [] },
  }
}

export const state = reactive({
  folders: [],
  cases: [],
  activeId: null,
  pendingFiles: [],
  rootOpen: true,
  collapsed: {}, // 右欄群組收合
  busy: false,
  // 深色模式
  theme: null, // null=跟隨系統, 'light', 'dark'
  // 響應式側欄（窄螢幕）
  leftOpen: false,
  rightOpen: false,
  // toast
  toastMsg: '',
  toastShow: false,
})

export const active = () => state.cases.find((c) => c.id === state.activeId)

// ── toast ──
let toastT = null
export function toast(msg) {
  state.toastMsg = msg
  state.toastShow = true
  clearTimeout(toastT)
  toastT = setTimeout(() => (state.toastShow = false), 2400)
}

// ── 案件 / 資料夾 ──
export function createCase(name, folderId) {
  const c = newCaseObj(name, folderId)
  state.cases.push(c)
  return c
}
export function buckets() {
  return state.folders.concat([{ id: null, name: '未分類', fixed: true }])
}
export function bucketOpen(f) {
  return f.fixed ? state.rootOpen : f.open
}
export function toggleBucket(f) {
  if (f.fixed) state.rootOpen = !state.rootOpen
  else f.open = !f.open
}
export function casesIn(folderId) {
  return state.cases.filter((c) => c.folderId === folderId)
}
export function selectCase(id) {
  state.activeId = id
  state.leftOpen = false
  state.rightOpen = false
}
export function moveCase(c, folderId, folderName) {
  if (c.folderId !== folderId) {
    c.folderId = folderId
    toast('已移至「' + folderName + '」')
  }
}
export function renameCase(c, name) {
  c.name = name
}
export function duplicateCase(c) {
  const d = createCase(c.name + '（副本）', c.folderId)
  state.activeId = d.id
}
export function deleteCase(c) {
  if (state.cases.length === 1) {
    toast('至少須保留一件案件')
    return false
  }
  state.cases = state.cases.filter((x) => x.id !== c.id)
  if (state.activeId === c.id) state.activeId = state.cases[0].id
  toast('案件已刪除')
  return true
}
export function addFolder(name) {
  state.folders.push({ id: nid('f'), name, open: true })
}
export function renameFolder(f, name) {
  f.name = name
}
export function deleteFolder(f) {
  state.cases.forEach((c) => {
    if (c.folderId === f.id) c.folderId = null
  })
  state.folders = state.folders.filter((x) => x.id !== f.id)
  toast('資料夾已刪除')
}
export function newCaseInFolder(f) {
  const c = createCase(null, f.id)
  f.open = true
  state.activeId = c.id
}

// ── 右欄卷宗 ──
export { GROUPS }
let touched = []
export function addOne(c, key, item) {
  item = { ...item, _new: true }
  c.docs[key].push(item)
  if (!touched.includes(key)) touched.push(key)
  return item
}
export function removeDoc(key, index) {
  active().docs[key].splice(index, 1)
  toast('已自卷宗移除')
}
export const folderTotal = computed(() => {
  const c = active()
  if (!c) return 0
  return GROUPS.reduce((n, g) => n + c.docs[g.key].length, 0)
})
// flushTouched：展開被觸及的群組並閃光，1.5s 後清 _new
export function flushTouched() {
  if (!touched.length) return
  const keys = touched.slice()
  touched = []
  keys.forEach((k) => (state.collapsed[k] = false))
  // 交給元件做 glow / scroll（透過事件旗標）
  glowKeys.value = keys
  requestAnimationFrame(() => {
    keys.forEach((k) => {
      const g = document.querySelector('.fgroup[data-key="' + k + '"]')
      if (!g) return
      g.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      g.classList.add('glow')
      setTimeout(() => g.classList.remove('glow'), 1300)
    })
  })
  setTimeout(() => {
    const c = active()
    if (!c) return
    keys.forEach((k) => c.docs[k] && c.docs[k].forEach((it) => delete it._new))
    glowKeys.value = []
  }, 1500)
}
import { ref } from 'vue'
export const glowKeys = ref([])

// ── 對話串 model ──
// 每則訊息：{ id, who:'me'|'ai', kind, ... }
//   who='me': { text, files, isCmd }
//   who='ai', kind='html': { html }
//   who='ai', kind='tool': { api, name, steps:[{t,done}], done, out:{type,...} }
function push(c, msg) {
  msg.id = nid('m')
  c.stream.push(msg)
  scrollSoon()
  return msg
}
export function scrollSoon() {
  requestAnimationFrame(() => {
    const s = document.getElementById('stream')
    if (s) s.scrollTop = s.scrollHeight
  })
}
function userMsg(text, files, isCmd) {
  return push(active(), { who: 'me', kind: 'user', text, files: files || [], isCmd: !!isCmd })
}
function aiMsg(html) {
  return push(active(), { who: 'ai', kind: 'html', html })
}

// 工具區塊：逐步揭示 steps
async function toolBlock(c, tool, steps, ackText) {
  const msg = push(c, {
    who: 'ai',
    kind: 'tool',
    ack: ackText || null,
    api: tool.api,
    name: tool.name,
    steps: [],
    running: true,
    out: null,
  })
  for (const s of steps) {
    await sleep(s[1])
    msg.steps.push({ label: s[0], t: (s[1] / 1000).toFixed(1) })
    scrollSoon()
  }
  msg.running = false
  return msg
}

// ── 工具實作 ──
const IMPL = {
  async extract(c, tool, ack) {
    if (!c.docs.evidence.length) {
      aiMsg('<p>咦，卷宗還是空的——小願手上沒東西可以讀。先用輸入框左邊的「＋」把訴願書、原處分書和答辯書丟進來吧。</p>')
      return
    }
    const msg = await toolBlock(
      c,
      tool,
      [
        ['OCR 與版面還原（共 ' + c.docs.evidence.length + ' 份檔案）', 900],
        ['擷取當事人、處分文號、日期等結構欄位', 700],
        ['文本分類：判定案件類型', 600],
        ['爭點識別與程序要件檢核', 800],
      ],
      ack,
    )
    msg.head = `收到 ${c.docs.evidence.length} 份卷證，開始解析。`
    msg.out = { type: 'extract' }
    c.flags.extract = true
    aiMsg('<p>讀完了！本案程序要件大致齊備，惟<b>陳述意見程序</b>與<b>廢棄物性質之證據</b>兩項有待補強，將直接影響主文走向。建議接續進行案例比對與法規檢索，以確立論理架構。</p>')
  },
  async cases(c, tool, ack) {
    const msg = await toolBlock(
      c,
      tool,
      [
        ['以案件類型與爭點建立檢索向量', 600],
        ['檢索歷史訴願決定書資料庫（114 年度 1,566 件）', 900],
        ['依爭點重疊度重排序，取前 5 筆', 700],
        ['寫入右側卷宗「相關案例」', 300],
      ],
      ack,
    )
    msg.out = { type: 'cases' }
    const have = new Set(c.docs.cases.map((x) => x.name))
    CASE_POOL.forEach((k) => {
      const nm = k.no + '　' + k.title
      if (!have.has(nm)) addOne(c, 'cases', { name: nm, note: k.type + '．' + k.verdict + '．相似度 ' + k.sim + '%', ext: '例', full: k.full })
    })
    c.flags.cases = true
    aiMsg('<p>撈到 5 筆，都放進右側卷宗了，點卡片可以看全文。其中 <b>1143058447</b> 就爭點一提出「物理狀態＋交易價值＋處置意思」三要素判準，<b>1143041902</b> 就爭點二採撤銷另處之體例，二者最具參考價值。</p>')
  },
  async laws(c, tool, ack) {
    const msg = await toolBlock(
      c,
      tool,
      [
        ['自爭點萃取檢索關鍵字', 500],
        ['檢索全國法規資料庫與環境部函釋', 900],
        ['比對現行有效版本與修正沿革', 700],
        ['寫入右側卷宗「相關法規」', 300],
      ],
      ack,
    )
    msg.out = { type: 'laws' }
    const have = new Set(c.docs.laws.map((x) => x.name))
    LAW_POOL.forEach((l) => {
      const nm = l.no + '　' + l.title
      if (!have.has(nm))
        addOne(c, 'laws', {
          name: nm,
          note: l.note,
          ext: '法',
          full: `<p style="font-family:var(--serif);line-height:2">${esc(l.body)}</p><p style="color:var(--muted);font-size:12.5px;border-top:1px solid var(--line-soft);padding-top:10px">本案關聯：${esc(l.note)}</p>`,
        })
    })
    c.flags.laws = true
    aiMsg('<p>11 筆法規入袋，點右側項目可以看條文全文。可在卷宗中自行增減——<b>草稿生成只會引用卷宗內的條文</b>，卷宗即為引註來源的白名單。</p>')
  },
  async graph(c, tool, ack) {
    if (!c.flags.extract) {
      aiMsg('<p>還沒萃取，我手上沒有節點可以牽線。先跑一次<b>萃取答辯書</b>吧。</p>')
      return
    }
    const msg = await toolBlock(
      c,
      tool,
      [
        ['建立節點：卷證、事實、爭點、法規、結論', 700],
        ['以引用關係連結節點', 800],
        ['標示訴辯對立與證據不足之處', 600],
        ['版面配置與繪製', 500],
      ],
      ack,
    )
    msg.out = { type: 'graph' }
    c.flags.graph = true
    if (!c.docs.out.some((x) => x.name === '案件關聯圖'))
      addOne(c, 'out', { name: '案件關聯圖', note: GNODES.length + ' 節點．' + GEDGES.length + ' 條關聯', ext: '圖', graph: true })
    aiMsg('<p>畫好了！<b>兩條紅色虛線</b>都指向爭點一：答辯意旨與訴願意旨就堆置物性質直接對立，而稽查工作紀錄僅呈現外觀、不足以支持「減失原效用」之要件事實。這是本案最脆弱的一環，撰擬理由欄時應優先處理。</p>')
  },
  async draft(c, tool, ack) {
    if (!c.flags.laws || !c.flags.cases) {
      aiMsg('<p>先別急——草稿只認右側卷宗裡的東西當來源，現在還不夠。請先跑<b>查找相似案例</b>和<b>搜尋相關法規</b>，我才不會寫出沒憑沒據的句子。</p>')
      return
    }
    const msg = await toolBlock(
      c,
      tool,
      [
        ['載入卷宗來源：' + c.docs.evidence.length + ' 卷證／' + c.docs.cases.length + ' 案例／' + c.docs.laws.length + ' 法規', 700],
        ['套用訴願決定書格式範本', 500],
        ['依爭點順序建構理由欄論理', 1100],
        ['逐句比對來源，標註引註並清除無來源陳述', 900],
      ],
      ack,
    )
    msg.out = { type: 'html', html: DRAFT_HTML }
    c.flags.draft = true
    if (!c.docs.out.some((x) => x.name === '訴願決定書草稿 v1'))
      addOne(c, 'out', { name: '訴願決定書草稿 v1', note: 'AI 生成．引註 14 處．待承辦人審核', ext: '稿', full: DRAFT_HTML })
    aiMsg('<p>初稿出爐，已歸檔。全文 14 處引註皆可回溯至右側卷宗，未附來源之推論已一律略去。<b>擬採主文為「原處分撤銷，由原處分機關於 2 個月內另為適法之處分」</b>——理由在於程序瑕疵尚待補正、且廢棄物性質之要件事實調查未足，宜由原處分機關重為調查認定，而非逕予撤銷。如欲改採其他主文，請告知方向由我修潤。</p>')
  },
  async refine(c, tool, arg, ack) {
    if (!c.flags.draft) {
      aiMsg('<p>還沒有草稿可以改／可以匯出，先跑一次<b>草稿生成</b>吧。</p>')
      return
    }
    const dir = arg && arg.trim() ? arg.trim() : '語氣更嚴謹、論理更緊密'
    const msg = await toolBlock(
      c,
      tool,
      [
        ['定位待修潤段落（理由欄第五、六點）', 600],
        ['套用修潤方向並保留全部引註', 800],
        ['校對法律用語與機關慣用體例', 600],
      ],
      ack,
    )
    msg.dir = dir
    msg.out = { type: 'refine' }
    aiMsg('<p>改好了。<span style="background:var(--ok-wash);color:var(--ok);padding:0 4px;border-radius:2px">綠色</span>為新增、<span style="background:var(--alert-wash);color:var(--alert);padding:0 4px;border-radius:2px;text-decoration:line-through">紅色</span>為刪除。可繼續指定其他方向，例如「爭點三再加入比例原則的三階段審查」。</p>')
  },
  async pdf(c, tool, arg, ack) {
    await exportFile(c, tool, 'pdf', ack)
  },
  async doc(c, tool, arg, ack) {
    await exportFile(c, tool, 'doc', ack)
  },
}

async function exportFile(c, tool, kind, ack) {
  if (!c.flags.draft) {
    aiMsg('<p>還沒有草稿可以改／可以匯出，先跑一次<b>草稿生成</b>吧。</p>')
    return
  }
  const isPdf = kind === 'pdf'
  const msg = await toolBlock(
    c,
    tool,
    [
      ['載入訴願決定書標準版型', 500],
      ['排版：主文、事實、理由、委員署名、教示條款', 800],
      [isPdf ? '輸出 PDF／嵌入標楷體字型' : '輸出 DOCX／保留樣式與段落編號', 700],
    ],
    ack,
  )
  const fname = `新北府訴決字第1141234567號_訴願決定書草稿.${isPdf ? 'pdf' : 'docx'}`
  msg.out = { type: 'export', isPdf, fname }
  if (!c.docs.out.some((x) => x.name === fname))
    addOne(c, 'out', { name: fname, note: (isPdf ? 'PDF' : 'Word') + '．訴願決定書版型', ext: isPdf ? 'pdf' : 'docx' })
  c.flags.out = true
  aiMsg(`<p>${isPdf ? 'PDF' : 'Word 檔'}已產出並歸檔至右側「答辯書與產出」。委員署名欄依訴願審議委員會現行名單自動帶入，教示條款依主文自動選用。</p>`)
}

export async function runTool(id, arg, silent) {
  if (state.busy) return
  const c = active()
  const tool = TOOLS.find((t) => t.id === id)
  if (!silent) {
    userMsg(arg ? `${tool.cmd} ${arg}` : tool.cmd, null, true)
    await sleep(240)
  }
  const ack = pick(ACKS[id] || ['好的，這就處理。'])
  state.busy = true
  try {
    if (id === 'refine' || id === 'pdf' || id === 'doc') await IMPL[id](c, tool, arg, ack)
    else await IMPL[id](c, tool, ack)
  } finally {
    state.busy = false
    c.started = true
    flushTouched()
    scrollSoon()
  }
}

// ── 訊息處理 ──
function matchTool(text) {
  const t = text.toLowerCase()
  for (const tool of TOOLS) if (t.startsWith(tool.cmd.toLowerCase())) return tool
  for (const tool of TOOLS) if (tool.kw.some((k) => t.includes(k.toLowerCase()))) return tool
  return null
}

export async function send(text) {
  if (state.busy) return
  const c = active()
  const files = state.pendingFiles.slice()
  text = (text || '').trim()
  if (!text && !files.length) return
  state.pendingFiles = []
  userMsg(text || '（附加檔案）', files)
  if (files.length) {
    files.forEach((f) => {
      if (!c.docs.evidence.some((x) => x.name === f.name)) addOne(c, 'evidence', { name: f.name, note: f.note, ext: f.ext })
    })
    c.started = true
    if (isAutoName(c.name)) c.name = '吉○實業／違反廢清法'
    flushTouched()
  }
  await sleep(280)
  if (/你會什麼|會做什麼|有哪些工具|有什麼工具|能做什麼|功能|help|工具清單/i.test(text)) {
    push(c, { who: 'ai', kind: 'tools-help' })
    scrollSoon()
    return
  }
  const tool = text ? matchTool(text) : null
  if (tool) {
    let arg = ''
    if (tool.id === 'refine') arg = text.replace(tool.cmd, '').replace(/優化文案|優化|修潤|潤稿/g, '').trim()
    await runTool(tool.id, arg, true)
    return
  }
  if (files.length && !text) {
    aiMsg(`<p>已收到 ${files.length} 份卷證並歸檔至右側「卷證檔案」。是否要開始萃取？點選下方的「萃取答辯書」，或直接輸入 <code style="font-family:var(--mono);font-size:11.5px">/萃取答辯書</code>。</p>`)
    return
  }
  aiMsg('<p>這句我沒對到工具。可以按 <code style="font-family:var(--mono);font-size:11.5px">/</code> 查看可呼叫的工具清單，或輸入「你會什麼」讓我列出全部 7 支 API。</p>')
}

// ── 附加 / 上傳 ──
export function availableEvidence(includePending) {
  const c = active()
  const have = new Set(c.docs.evidence.map((x) => x.name).concat(includePending ? state.pendingFiles.map((x) => x.name) : []))
  return EVIDENCE_POOL.filter((f) => !have.has(f.name))
}
export function attachToPending(files) {
  files.forEach((f) => state.pendingFiles.push(f))
}
export function removePending(i) {
  state.pendingFiles.splice(i, 1)
}
export function uploadToFolder(files) {
  const c = active()
  let n = 0
  files.forEach((f) => {
    addOne(c, 'evidence', f)
    n++
  })
  if (n) {
    c.started = true
    if (isAutoName(c.name)) c.name = '吉○實業／違反廢清法'
  }
  flushTouched()
  toast(n ? `已上傳 ${n} 份卷證` : '未選擇任何檔案')
}

// ── 搜尋加入（右欄群組的 ＋） ──
export function searchPool(groupKey) {
  const c = active()
  const pool =
    groupKey === 'laws'
      ? LAW_POOL.map((l) => ({
          name: l.no + '　' + l.title,
          note: l.note,
          ext: '法',
          kw: (l.no + l.title + l.note + l.body).toLowerCase(),
          full: `<p style="font-family:var(--serif);line-height:2">${esc(l.body)}</p><p style="color:var(--muted);font-size:12.5px;border-top:1px solid var(--line-soft);padding-top:10px">本案關聯：${esc(l.note)}</p>`,
        }))
      : CASE_POOL.map((k) => ({
          name: k.no + '　' + k.title,
          note: k.type + '．' + k.verdict + '．相似度 ' + k.sim + '%',
          ext: '例',
          kw: (k.no + k.title + k.type + k.verdict + k.law).toLowerCase(),
          full: k.full,
        }))
  const have = new Set(c.docs[groupKey].map((x) => x.name))
  return { pool, have }
}
export function addSearched(groupKey, items) {
  const c = active()
  let n = 0
  items.forEach((item) => {
    addOne(c, groupKey, { name: item.name, note: item.note, ext: item.ext, full: item.full })
    n++
  })
  flushTouched()
  return n
}

// ── 建議 chips ──
export const chips = computed(() => {
  const c = active()
  if (!c) return []
  if (state.busy) return [{ ghost: true, label: '工具執行中⋯⋯' }]
  const f = c.flags
  const out = []
  const add = (label, action, lead) => out.push({ label, action, lead })
  if (!c.docs.evidence.length) {
    add('上傳卷證檔案', { t: 'attach' }, '＋')
    add('你會什麼？', { t: 'ask' }, '?')
    return out
  }
  if (!f.extract) add('萃取答辯書', { t: 'tool', id: 'extract' }, '▸')
  if (f.extract && !f.cases) add('查找相似案例', { t: 'tool', id: 'cases' }, '▸')
  if (f.extract && !f.laws) add('搜尋相關法規', { t: 'tool', id: 'laws' }, '▸')
  if (f.extract && !f.graph) add('生成關聯圖', { t: 'tool', id: 'graph' }, '▸')
  if (f.cases && f.laws && !f.draft) add('生成決定書草稿', { t: 'tool', id: 'draft' }, '▸')
  if (f.draft) {
    add('優化文案：語氣更嚴謹', { t: 'tool', id: 'refine', arg: '語氣更嚴謹、論理更緊密' }, '▸')
    add('補充相關法規', { t: 'search', key: 'laws' }, '＋')
    if (f.graph) add('重繪關聯圖', { t: 'tool', id: 'graph' }, '▸')
    if (!f.out) {
      add('生成 PDF', { t: 'tool', id: 'pdf' }, '▸')
      add('生成 DOC', { t: 'tool', id: 'doc' }, '▸')
    } else {
      add('重新生成 PDF', { t: 'tool', id: 'pdf' }, '▸')
      add('生成 DOC', { t: 'tool', id: 'doc' }, '▸')
    }
  }
  if (f.extract && !f.draft) add('再上傳卷證', { t: 'attach' }, '＋')
  if (!out.length) add('你會什麼？', { t: 'ask' }, '?')
  return out
})

export const stages = computed(() => {
  const c = active()
  if (!c) return []
  return ['extract', 'cases', 'laws', 'graph', 'draft', 'out'].map((k) => c.flags[k])
})

// ── 主題 ──
export function initTheme() {
  syncThemeAttr()
}
export function toggleTheme() {
  const cur = currentTheme()
  document.documentElement.setAttribute('data-theme', cur === 'dark' ? 'light' : 'dark')
  state.theme = cur === 'dark' ? 'light' : 'dark'
}
export function currentTheme() {
  return document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
}
function syncThemeAttr() {
  /* no-op：初始跟隨系統，data-theme 由 toggle 設定 */
}

// ── 啟動 ──
export function boot() {
  const first = createCase(null, null)
  state.activeId = first.id
}
export { CASE_NO, TOOLS }
export { esc, isAutoName }
