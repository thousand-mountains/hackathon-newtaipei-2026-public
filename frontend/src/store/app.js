// 訴願智慧輔助平台 — 應用狀態與辦案流程（單例 reactive store）
// 移植自 design/訴願智慧輔助平台.html 的命令式 JS，改為 Vue 響應式模型。
import { reactive, computed } from 'vue'
import { CASE_NO, TOOLS, GROUPS, ACKS } from '../data/data.js'
import { DEMO_CASE_FILES } from '../data/demo-case.js'
import { api } from '../api/index.js'
import { ApiError } from '../api/http.js'
import { corpusScoreCaption } from '../api/ranker.js'

// ── 草稿版本快取（localStorage）──
// 優化文案的 diff 需要「上一版文字」。生成草稿／每次潤稿成功後，把新版純文字存進 localStorage；
// 下次潤稿時讀出來當 diff 的基準（舊版），與後端回傳的新版比對。
const DRAFT_KEY = (caseId) => `xy.draft.${caseId}`
function loadDraftText(caseId) {
  try {
    return localStorage.getItem(DRAFT_KEY(caseId)) || ''
  } catch {
    return ''
  }
}
function saveDraftText(caseId, text) {
  try {
    localStorage.setItem(DRAFT_KEY(caseId), text || '')
  } catch {
    /* localStorage 不可用時忽略，diff 退化為與空字串比 */
  }
}
function clearDraftText(caseId) {
  try {
    localStorage.removeItem(DRAFT_KEY(caseId))
  } catch {
    /* ignore */
  }
}
// 從草稿 HTML 抽純文字（去標籤、去引註標記），供 diff 使用。
function draftHtmlToText(html) {
  return String(html || '')
    .replace(/<span class="cite">[\s\S]*?<\/span>/g, '') // 去引註 chip
    .replace(/<h4[^>]*>[\s\S]*?<\/h4>/g, '') // 去小標
    .replace(/<[^>]+>/g, '') // 去其餘標籤
    .replace(/\s+\n/g, '\n')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

// tool id（前端）↔ 後端長名（契約 §3.0）與工具卡結果 out.type 的對照。
// 之所以留這張表：UI 元件（ToolOut.vue）認的是 out.type，後端事件認的是長名，
// store 在中間翻譯，兩邊都不動。
const TOOL_API = {
  extract: 'extract_case_document',
  cases: 'search_similar_decisions',
  laws: 'search_regulations',
  graph: 'build_relation_graph',
  draft: 'generate_decision_draft',
  refine: 'refine_text',
  pdf: null, // 匯出走下載端點，不是 chat 工具
  doc: null,
}
const OUT_TYPE = {
  extract_case_document: 'extract',
  search_similar_decisions: 'cases',
  search_regulations: 'laws',
  build_relation_graph: 'graph',
  // refine_text 不在此：它的 out 由 applyToolResult 依 diff 結果動態設為 type:'diff'
}

const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

// agent 的回覆是 markdown（粗體、編號清單、換行）。**先 esc 再套最小格式**，
// 不然畫面上出現的是字面的 `**法規依據**`，而且整段擠成一坨沒有換行
//（實測「還沒有解析過卷證」那則回覆就是這樣）。只處理粗體與換行，
// 不引入 markdown 套件——輸入已經 escape 過，這裡不會開出 HTML 注入的口子。
// 行內標記：粗體與行內程式碼。**一定要先 esc**，這裡只認這兩種，不開 HTML 的口子。
const inlineMd = (s) =>
  esc(s)
    .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')

const isTableRow = (l) => /^\s*\|.*\|\s*$/.test(l)
const isTableSep = (l) => /^\s*\|[\s:|-]*-[\s:|-]*\|\s*$/.test(l)
const tableCells = (l) => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((x) => x.trim())

// agent 回覆是 markdown。**表格一定要畫成表格。**
// `refine_text` 回的是「原句／建議／說明」三欄的修改建議表——那是這個產品要的形狀
// （承辦人自己決定採不採納，而不是拿到一份已經被改好的稿），但以純文字吐出來時
// 滿畫面的 `|` 跟 `---` 根本讀不了。查相似案例、列工具清單也都會用表格。
// 只實作 agent 真的會用的子集：表格、粗體、行內碼、引言、換行。不引 markdown 套件。
function mdToHtml(raw) {
  const lines = String(raw || '').replace(/\r\n/g, '\n').split('\n')
  const html = []
  let para = [] // 累積中的一般文字行
  const flushPara = () => {
    if (!para.length) return
    html.push('<p>' + para.map(inlineMd).join('<br>') + '</p>')
    para = []
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (isTableRow(line) && isTableSep(lines[i + 1] || '')) {
      flushPara()
      const head = tableCells(line)
      i += 2
      const body = []
      while (i < lines.length && isTableRow(lines[i])) body.push(tableCells(lines[i++]))
      i-- // 迴圈結尾還會 ++
      html.push(
        '<div class="mdtable-wrap"><table class="mdtable"><thead><tr>' +
          head.map((h) => '<th>' + inlineMd(h) + '</th>').join('') +
          '</tr></thead><tbody>' +
          body
            .map(
              (r) =>
                '<tr>' +
                // 欄數不一致時補空格，不要讓表格塌掉
                head.map((_, j) => '<td>' + inlineMd(r[j] == null ? '' : r[j]) + '</td>').join('') +
                '</tr>',
            )
            .join('') +
          '</tbody></table></div>',
      )
      continue
    }
    if (/^\s*>\s?/.test(line)) {
      flushPara()
      html.push('<blockquote>' + inlineMd(line.replace(/^\s*>\s?/, '')) + '</blockquote>')
      continue
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      flushPara()
      html.push('<hr class="mdrule">')
      continue
    }
    if (line.trim() === '') {
      flushPara()
      continue
    }
    para.push(line)
  }
  flushPara()
  return html.join('')
}
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
    caseId: nid('upload'), // 後端案件 id（mock 與 real 共用；契約案件層級資源）
    sessionId: null, // chat 對話記憶（done.session_id 帶回來）
    runId: null, // 最後一次成功 run（latest_run_id），生成草稿等工具用
    name: name || autoName(),
    folderId: folderId || null,
    started: false,
    _loaded: false, // 是否已從後端載入彙整版（getCase）
    _loadTask: null, // 該次 getCase 的 Promise；chat 送出前要等它（見 ensureServerCase）
    _serverCreated: false, // 是否已在後端建案（第一次上傳走 createCase，之後走 uploadFiles）
    stream: [], // 對話串訊息陣列
    flags: { extract: false, cases: false, laws: false, graph: false, draft: false, out: false },
    docs: { evidence: [], cases: [], laws: [], out: [] },
    // 契約 §3.3／§4（2026-09-13 後端補上）：彙整版一律帶這四個鍵，
    // **取不到是 null 不是省略**，所以這裡預設也用 null，不要用 {}／[]——
    // 用空物件的話「還沒跑過」與「跑過但是空的」就分不出來了。
    intake: null,
    facts: null,
    issues: null,
    screen: null,
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
  // 後端檔位（/health 回的 run_mode）；離線／非 live 檔位時聊天不可用（契約 §5）
  runMode: null,
  healthOk: null, // null=未檢查, true/false
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
  const c = state.cases.find((x) => x.id === id)
  if (c) loadCase(c) // 首次選到就從後端載入彙整版（契約 §4 開案首載）
}

// 從後端載入單一案件的彙整版（getCase #4），填四群組。已載過就不重打。
// 只有「已在後端建案」的案子才載；純本地空案（還沒上傳）沒得載。
function loadCase(c) {
  if (c._loaded || !c._serverCreated) return c._loadTask
  c._loadTask = (async () => {
    try {
      const r = await api.getCase(c.caseId)
      c._loaded = true
      if (r.case && r.case.name) c.name = r.case.name
      if (r.case && r.case.latest_run_id) c.runId = r.case.latest_run_id
      fillDocsFromServer(c, r)
    } catch {
      /* 載入失敗維持現狀，不阻斷操作 */
    }
  })()
  return c._loadTask
}

// 強制重載彙整版（解析卷證跑完之後要用——那一輪之後 intake／facts_excerpt／
// issues／screen 才會有值，而 loadCase 有 _loaded 快取不會再打）。
export async function refreshCase(c) {
  if (!c._serverCreated) return
  try {
    const r = await api.getCase(c.caseId)
    c._loaded = true
    if (r.case && r.case.latest_run_id) c.runId = r.case.latest_run_id
    fillDocsFromServer(c, r)
  } catch {
    /* 重載失敗維持現狀，不阻斷操作 */
  }
}

// 把彙整版的四類資源映射回 docs 結構（name/note/ext/full/_libId/_artifactId）
function fillDocsFromServer(c, r) {
  // readable 要一路帶著：重開一個舊案時，讀不到的卷證也必須照樣標出來（契約 §4.1／§6）
  if (Array.isArray(r.files))
    c.docs.evidence = r.files.map((f) => ({ name: f.name, note: f.note || '', ext: f.ext || '', readable: f.readable !== false, _libId: f.id }))
  if (Array.isArray(r.laws)) c.docs.laws = r.laws.map(lawItem)
  if (Array.isArray(r.references)) c.docs.cases = r.references.map(decisionItem)
  if (Array.isArray(r.artifacts)) {
    // 匯出的 PDF／DOCX **不是 artifact**：後端只把草稿寫進 manifest
    //（`backend/dossier/store.py:524` 的 kind 只有 "draft"），匯出走的是下載端點。
    // 所以彙整版回來時直接整包覆蓋，會把這個 session 剛匯出的那幾筆從右欄抹掉，
    // 連帶把 `flags.out` 打回 false（「重新生成 PDF」變回「生成 PDF」）。
    // 本地那幾筆沒有 `_artifactId`，用這個分辨並保留。
    const sessionExports = (c.docs.out || []).filter((x) => !x._artifactId && EXPORT_EXTS.includes(x.ext))
    c.docs.out = r.artifacts
      .map((a) => ({
        name: a.name,
        note: a.note || '',
        ext: a.kind === 'graph' ? '圖' : a.kind === 'draft' ? '稿' : '檔',
        _artifactId: a.id,
        graph: a.kind === 'graph',
      }))
      .concat(sessionExports)
  }
  // 四塊（契約 §3.3）。`latest_run_id` 是 null ＝ 還沒跑過（正常，四塊本來就該是 null）；
  // 有值而四塊是 null ＝ run 讀不回來（異常）。這個區分推導得出來，後端沒有為它加新鍵。
  c.intake = r.intake || null
  c.facts = Array.isArray(r.facts_excerpt) ? r.facts_excerpt : null
  c.issues = Array.isArray(r.issues) ? r.issues : null
  c.screen = r.screen || null
  c.runStale = !!(r.case && r.case.latest_run_id) && !c.screen

  // **旗標也要從後端還原。** 只還原 docs 不還原 flags 的話，重新整理之後
  // 後端明明已經有草稿，畫面卻回到「還沒有草稿可以匯出，先跑一次草稿生成吧」，
  // 而且建議 chips 也不會出現匯出與優化文案。旗標推得出來就推，不要等使用者重跑。
  c.flags.extract = !!(r.case && r.case.latest_run_id)
  c.flags.laws = (c.docs.laws || []).length > 0
  c.flags.cases = (c.docs.cases || []).length > 0
  c.flags.draft = (c.docs.out || []).some((x) => x.ext === '稿')
  // 匯出項的 ext 是 'pdf'／'docx'（`runTool` 的 export 分支那樣寫進去的），
  // 不是彙整版那三種（圖／稿／檔）——`EXPORT_EXTS` 是這兩處唯一的共同定義。
  // 重新整理之後這一格必然是 false，因為匯出沒有落地在後端，**這是真的，不要假裝有**。
  c.flags.out = (c.docs.out || []).some((x) => EXPORT_EXTS.includes(x.ext))
}

export function moveCase(c, folderId, folderName) {
  if (c.folderId !== folderId) {
    c.folderId = folderId
    toast('已移至「' + folderName + '」')
  }
}
export function renameCase(c, name) {
  c.name = name
  if (c._serverCreated) {
    api.renameCase(c.caseId, name).catch(() => toast('改名同步後端失敗，畫面仍可操作'))
  }
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
  if (state.activeId === c.id) selectCase(state.cases[0].id)
  if (c._serverCreated) api.deleteCase(c.caseId).catch(() => toast('刪除同步後端失敗'))
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
//: 工具結果狀態 → 畫面文字。**全前端唯一一份**，工具卡頭（Chat.vue）與結果區
//: （ToolOut.vue）共用。
//:
//: **`empty` 刻意不說「查無」。** 契約 §2.3 把 `empty` 定義成「查無，hits[] 為空」，
//: 但後端同一個值也拿來表示「這次**根本還沒查**」——查詢詞導不出來時
//: （`backend/llm/chat.py:1424`），而它在同一段還特地叫模型
//: 「不要說查無相似案例——這次根本還沒查」。前端把徽章寫死成「查無結果」，
//: 等於在大標把後端的實話原地推翻，而承辦人先看到的是大標。
//:
//: 兩者**在 `status` 上分不出來，靠 `note` 也分不出來**：真的查無時後端的 note
//: 就是「…查無結果。」（`chat.py:947`），兩種情況的 note 都非空。唯一的區分方式
//: 是比對後端那串中文，下次改字就悄悄失效——那種比對不做。
//: 所以這裡用一句**兩種情況都成立**的話，具體原因交給後端的 note 逐字講
//: （它本來就每種情況都寫得很具體，而且就顯示在正下方）。
//:
//: 真正的修法是契約多一個態（例如 `not_attempted`），要動後端與契約，已回報。
export const TOOL_STATUS = {
  ok: { head: '完成', chip: '完成', mark: '✓', tone: 'ok' },
  // 做了、結果是沒有。`empty` 不再兼差當「還沒做」——那是下面那一格。
  empty: { head: '未取得結果', chip: '未取得結果', mark: '○', tone: 'warn' },
  // 沒做，因為前置條件不成立（契約 §2.3 第四個值，後端 `chat.py:913`）。
  // 目前兩個情境：chip 導不出查詢詞、沒有 run 畫不了圖。
  //
  // **文案用「尚未執行」不用「還沒查」**：畫不出關聯圖那條不是「查」。
  // 一格文案要同時蓋住兩個情境，講「這支工具一次都沒被呼叫」才是兩邊都真的那句。
  // **為什麼不在這裡加一句解釋**：後端的 `note` 已經逐字寫了為什麼還沒做
  //（「查詢詞由卷證解析的結果組出來……目前這些都還是空的」），而它就顯示在正下方。
  // 前端再寫一句就是第二份文案來源。
  not_attempted: { head: '尚未執行', chip: '尚未執行', mark: '—', tone: 'warn' },
  failed: { head: '工具執行失敗', chip: '失敗', mark: '✕', tone: 'alert' },
}

//: 查不到的 status 走這裡。**以前的 fallback 是 `TOOL_STATUS.ok`**——也就是把一個
//: 我們看不懂的狀態畫成綠勾「完成」。`not_attempted` 上線前如果沒改這裡，那兩個
//: 「一次都沒被呼叫」的情境會在畫面上顯示「✓ 完成」：不是難看，是**謊報成功**。
//: 契約日後再加值域時，這一格保證它至少不會假裝順利跑完。
const TOOL_STATUS_UNKNOWN = { head: '狀態不明', chip: '狀態不明', mark: '？', tone: 'warn' }

//: 圖示與顏色也一起從這張表拿，不要在元件裡另寫 v-if 串——分開寫的話，
//: 新增一個值時很容易文字更新了、圖示還停在舊的那串判斷式的 `v-else`（綠勾）。
export const toolStatus = (status) => TOOL_STATUS[status] || TOOL_STATUS_UNKNOWN
export const toolStatusText = (status, where) => toolStatus(status)[where]

export { GROUPS }
//: 匯出產出在右欄用的 ext。寫在一處，`fillDocsFromServer` 與 `flags.out` 共用——
//: 分開寫的結果是 `flags.out` 拿彙整版的「檔／稿／圖」去比 'pdf'／'docx'，恆為 false。
const EXPORT_EXTS = ['pdf', 'docx']
let touched = []
export function addOne(c, key, item) {
  item = { ...item, _new: true }
  c.docs[key].push(item)
  if (!touched.includes(key)) touched.push(key)
  return item
}
export function removeDoc(key, index) {
  const c = active()
  const [item] = c.docs[key].splice(index, 1)
  // 背景同步後端刪除（契約 #9 files / #14 laws / #19 references / #22 artifacts）。
  syncRemoveDoc(c, key, item)
  toast('已自卷宗移除')
}

async function syncRemoveDoc(c, key, item) {
  const id = item && (item._libId || item._artifactId || item.id)
  if (!id) return
  try {
    if (key === 'evidence') await api.deleteFile(c.caseId, id)
    else if (key === 'laws') await api.removeCaseLaw(c.caseId, id)
    else if (key === 'cases') await api.removeReference(c.caseId, id)
    else if (key === 'out' && item._artifactId) await api.deleteArtifact(c.caseId, id)
  } catch {
    toast('移除同步後端失敗，畫面仍可操作')
  }
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
  // **回傳陣列裡那個 reactive proxy，不是傳進來的原始物件。**
  // `c.stream` 是 reactive 陣列，改原始物件雖然值會變，但不會通知 Vue 重繪。
  // 症狀很隱蔽：extract／查法規那幾條路徑因為順便改了 `c.flags`／`c.docs`（走 proxy）
  // 而附帶觸發重繪，看起來一切正常；只有 build_relation_graph 回 empty 這種
  // 「只改工具卡、不動其他狀態」的情況會露餡——卡片永遠停在「執行中」，
  // 而 SSE 四個事件其實都收到了、busy 也解開了。
  return c.stream[c.stream.length - 1]
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
// ── chat SSE 驅動：消費 api.chat() 事件流，翻譯成畫面上的工具卡／訊息 ──
//
// 事件時序（契約 §2.3）：ack → tool_call → tool_step* → tool_result → token* → done|error。
// 對應到既有 stream model：
//   ack        → 有工具時開工具卡（帶 ack 口白）；純問答時作前導訊息
//   tool_call  → 確認工具卡（api / name）
//   tool_step  → 往卡的 steps[] 加行（僅 extract/draft 會有；done 帶 elapsed_ms）
//   tool_result→ 設定 out.type（依工具）＋依 §3 歸檔到右欄；status!=ok 顯示 note
//   token      → 累積成一則 ai html 訊息，逐字 append
//   done       → 記 session_id / run_id；dropped_refs 非空標「引用有問題」（§2.4.1）
//   error      → stage=transport 走「連線中斷可重問」，不當模型錯誤
async function driveChat(c, payload, uiTool) {
  await ensureServerCase(c) // 確保建案已完成、caseId 是真的
  let toolMsg = null // 最後一張工具卡（refine 的 diff、done 收尾用）
  const cards = {} // call_id -> 工具卡
  let tokenMsg = null
  const stepIdx = {} // call_id:step -> steps[] 索引

  // loading 指示：一送出就先放一則「思考中」泡泡（打字點點），有工具卡或首個 token 進來時撤掉。
  // 這是「chat needs loading」的核心——尤其純問答沒有工具卡，靠它讓使用者看到系統在動。
  let thinkingMsg = push(c, { who: 'ai', kind: 'thinking' })
  const clearThinking = () => {
    if (!thinkingMsg) return
    const i = c.stream.indexOf(thinkingMsg)
    if (i >= 0) c.stream.splice(i, 1)
    thinkingMsg = null
  }

  // **逐字串流保留原始文字（`raw`），畫面由 mdToHtml 從 raw 重算。**
  // 之前是直接把 esc 過的片段往 html 後面接，於是 (a) markdown 永遠沒機會被解析
  //（表格、換行、粗體全是字面值），(b) done 要拿 answer 校正時只能比字串長度，
  // 而 esc 與 fmt 的輸出長度不同尺，得多繞一層。存 raw 兩個問題一起沒了。
  const appendToken = (text) => {
    clearThinking()
    if (!tokenMsg) tokenMsg = push(c, { who: 'ai', kind: 'html', html: '', raw: '' })
    tokenMsg.raw += text
    tokenMsg.html = mdToHtml(tokenMsg.raw)
    scrollSoon()
  }

  // 注意：real 的 api.chat 是 async generator（lazy），fetch 與開串流前的 JSON 錯誤
  // 會在下面 for-await 首次迭代時才拋，統一由該 catch 依 ApiError 分流處理。
  try {
    for await (const { event, data } of api.chat(c.caseId, payload)) {
      if (event === 'ack') {
        if (data.session_id) c.sessionId = data.session_id
        // **ack 不開工具卡。** 開卡等於在畫面上宣稱「系統正在跑這支工具」，
        // 但 `tool_hint` 只是提示——實測 `/搜尋相關法規`、`/優化文案` 兩次 agent
        // 都決定不呼叫工具（改成回問使用者）。那種情況下 ack 開的卡會停在畫面上，
        // 標著工具名、空的、還打著「✓ 完成」，說了一件沒發生的事。
        // 卡等 `tool_call` 再開（下一個分支），ack 的口白照舊顯示——那是 agent 真的說的。
        if (data.text) {
          aiMsg('<p>' + esc(data.text) + '</p>')
          // 把 thinking 移到 ack 之後（保持在串流最底）：真正的答案還在後面
          // （解析 10–11s／草稿 24–72s／純問答 5–15s），這段等待才是要 loading 的地方。
          if (thinkingMsg) {
            const i = c.stream.indexOf(thinkingMsg)
            if (i >= 0) c.stream.splice(i, 1)
            c.stream.push(thinkingMsg)
            scrollSoon()
          }
        }
      } else if (event === 'tool_call') {
        clearThinking()
        // **一回合可能呼叫同一支工具兩次以上**（契約 §2.3 ②，實測「查建築法25條和訴願法14條」
        // 就會發 tc-1／tc-2 兩組）。以前這裡只有一張 toolMsg，第二組結果會把第一組蓋掉，
        // 使用者看到的命中數比實際少。改成依 call_id 一組一張卡。
        if (!cards[data.call_id]) {
          cards[data.call_id] = push(c, {
            who: 'ai', kind: 'tool', ack: null, callId: data.call_id,
            // 工具名用**後端帶的 label**，不是前端 TOOLS 表裡那個——
            // agent 實際挑的工具可能跟 tool_hint 不同，照前端的表寫會標錯。
            api: data.tool, name: data.label || '', steps: [], running: true, out: null,
          })
        }
        toolMsg = cards[data.call_id]
      } else if (event === 'tool_step') {
        const card = cards[data.call_id] || toolMsg
        if (!card) continue
        const key = data.call_id + ':' + data.step
        if (data.status === 'running' && stepIdx[key] == null) {
          stepIdx[key] = card.steps.push({ label: data.label, t: '', degraded: !!data.degraded }) - 1
        } else if (data.status === 'done') {
          // **`0` 是一個真的值，不是「沒有值」。** n2（案件分類）與 n3（程序審查）
          // 是純規則運算，後端實測回的就是 `elapsed_ms: 0`（QA 雲上存檔 r05）。
          // 用 falsy 判斷會把那兩行的秒數吃成空白，看起來像「這一步沒跑」——
          // 而那正是這個產品要秀的東西：規則層零毫秒、零模型。
          // 真的沒有這個鍵時（running 事件）才留白，用 `== null` 分。
          const ms = data.elapsed_ms
          const row = { label: data.label, t: ms == null ? '' : (ms / 1000).toFixed(1), degraded: !!data.degraded }
          if (stepIdx[key] != null) card.steps[stepIdx[key]] = row
          else card.steps.push(row)
        }
        scrollSoon()
      } else if (event === 'tool_result') {
        applyToolResult(c, cards[data.call_id] || toolMsg, data)
      } else if (event === 'token') {
        appendToken(data.text || '')
      } else if (event === 'done') {
        if (data.session_id) c.sessionId = data.session_id
        // 沒有逐字串流但有 answer → 補一則泡泡。
        // **判斷不能含「也沒有工具卡」。** 實測 /優化文案：agent 開了工具卡的 ack、
        // 但沒真的呼叫工具（它回問「要改哪段文字」），零個 token、零個 tool_result——
        // 畫面上就只剩一張空的、標著「✓ 完成」的工具卡，agent 的回話整段被丟掉。
        if (!tokenMsg && data.answer) {
          clearThinking()
          tokenMsg = push(c, { who: 'ai', kind: 'html', html: '', raw: '' })
        }
        clearThinking()
        // 優化文案：**不要自動把 done.answer 當成「改寫後全文」去做 diff。**
        // 契約 §3.4 寫「改寫後的文字走 token」，但實測 token 串回來的是 agent 的
        // **修改建議表格**（「原句／建議／說明」三欄），不是改寫後的草稿。
        // 拿它去跟上一版逐詞比對，畫出來的是一團把舊草稿和建議表交織在一起的紅綠字，
        // 看起來像「草稿被改成這樣了」——那比沒有對照功能糟。
        // 改寫文字目前沒有契約定義的欄位能可靠取得，已回報；在那之前照實顯示回覆本身。
        // 紅線一（契約 §2.4.1 一、CONSTITUTION §4）：redirect 非 null →
        // 期限／天數這類問題交給規則引擎，**不得顯示 agent 講的任何天數**。
        // 這裡刻意**不寫 data.answer**（answer 裡就是 LLM 算的天數），只放 reason + CTA。
        //
        // **只在「純問答」回合套用。** 機械規則對「問題」做字面比對、寧可誤判（後端交接文件明說），
        // 所以潤稿／生成草稿這類工具回合，內容裡出現「期間／天數」字眼也可能被帶上 redirect——
        // 那時若把整個工具結果換成期限提示，會把好好的潤稿／草稿蓋掉（實測 refine 就中招）。
        // 有工具卡的回合＝這輪的主體是工具產出，不套 redirect 覆蓋。
        const hasToolThisTurn = Object.keys(cards).length > 0 || !!toolMsg
        if (data.redirect && !hasToolThisTurn) {
          if (!tokenMsg) tokenMsg = push(c, { who: 'ai', kind: 'html', html: '' })
          const reason = data.redirect.reason || '期間計算由程序審查的規則引擎負責，聊天不計算期限。'
          tokenMsg.kind = 'redirect'
          tokenMsg.reason = reason
          tokenMsg.cta = data.redirect.cta || '查看程序審查的算式'
          return
        }
        if (tokenMsg) {
          // 校正 token 串接：以 done.answer 為準，但「不得比已顯示的內容更短」——
          // 避免 answer 缺漏／截斷時反而把完整的逐字內容蓋掉（話被 cut 的第二個來源）。
          // 兩邊都是原始文字，直接比長度就對，不必再換算標籤。
          const streamedRaw = tokenMsg.raw || ''
          const answerRaw = data.answer || ''
          tokenMsg.raw = answerRaw.length >= streamedRaw.length ? answerRaw : streamedRaw
          tokenMsg.html = mdToHtml(tokenMsg.raw)
        }
        // 紅線二（第二件）：dropped_refs 非空 → 標「引用有問題」。
        if (data.dropped_refs && data.dropped_refs.length && tokenMsg)
          tokenMsg.html += '<p style="color:var(--muted);font-size:12px;margin-top:6px">此則含無法對應的引用，請勿直接採用。</p>'
        Object.values(cards).forEach((k) => (k.running = false))
        if (toolMsg) toolMsg.running = false
        return
      } else if (event === 'error') {
        clearThinking()
        Object.values(cards).forEach((k) => (k.running = false))
        if (toolMsg) toolMsg.running = false
        // 契約 §2.3：error.stage ∈ tool|model|transport|internal。transport 不當模型錯誤。
        aiMsg('<p>' + (data.stage === 'transport' ? '連線中斷，可重問。' : '執行時發生問題：' + esc(data.error || '未知錯誤')) + '</p>')
        toast(data.stage === 'transport' ? '連線中斷，可重問' : '執行發生問題')
        return
      }
    }
  } catch (e) {
    clearThinking()
    Object.values(cards).forEach((k) => (k.running = false))
    if (toolMsg) toolMsg.running = false
    // 開串流前的 JSON 錯誤（契約 §2.2）：503 非 live 檔位 / 404 案件不存在 / 400 訊息空。
    // 顯示後端給的 detail，不要一律說「連線中斷」——尤其 fixture 檔位要說「聊天需要即時模型」。
    if (e instanceof ApiError) {
      const detail = (e.body && e.body.detail) || '請求失敗'
      if (e.status === 503) {
        aiMsg('<p>' + esc(detail) + '</p>')
        toast('聊天需要即時模型檔位')
      } else if (e.status === 404) {
        aiMsg('<p>' + esc(detail) + '</p>')
        toast('案件不存在')
      } else {
        aiMsg('<p>' + esc(detail) + '</p>')
        toast('請求失敗，可重試')
      }
    } else {
      aiMsg('<p>連線中斷，可重問。</p>')
      toast('連線中斷，可重問')
    }
  }
}

// redirect 的 CTA（契約 §2.4.1 一）：捲到**規則引擎算好的期間計算算式**。
// 這是 CONSTITUTION §4 紅線的下半截——上半截是「不顯示 agent 算的天數」，
// 下半截是「給承辦人一個可以自己逐步驗算的算式」。只做上半截等於把問題吞掉。
// 算式是 `screen.deadline.steps`（每步含 rule／basis 法條依據／value）。
export function gotoProcedureCheck() {
  const c = active()
  if (!c) return
  if (!c.screen) {
    toast(c.runId ? '讀不回這次執行的程序審查，請重跑一次解析卷證' : '這件案子還沒有解析過卷證，先跑一次「解析卷證檔案」')
    return
  }
  // 串流裡已經有程序審查（獨立卡或解析卷證的工具卡）就捲過去，沒有就補一張。
  // 補一張是必要的：對話紀錄只存在記憶體，重新整理之後串流是空的，
  // 但 screen 來自 manifest 一直都在——這時候不補就等於 CTA 按了沒反應。
  let target = [...c.stream].reverse().find(
    (m) => m.kind === 'screen' || (m.kind === 'tool' && m.api === 'extract_case_document'),
  )
  if (!target) target = push(c, { who: 'ai', kind: 'screen' })
  requestAnimationFrame(() => {
    const el = document.querySelector(`[data-msg="${target.id}"]`)
    if (!el) return
    const box = el.querySelector('[data-anchor="deadline"]') || el
    box.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('glow')
    setTimeout(() => el.classList.remove('glow'), 1600)
  })
}

// tool_result → 設定工具卡 out（ToolOut.vue 依 type 呈現）＋依 §3 歸檔到右欄卷宗
//
// **工具卡只畫 tool_result 真的帶回來的東西。** 2026-09-13 前這裡把 out 設成
// `{type:'laws'}` 這種「只有型別、沒有資料」的物件，ToolOut.vue 就去讀 data.js 的
// LAW_POOL／CASE_POOL／寫死的解析結果——畫面上出現的是一件**跟本案無關的廢清法案子**
// （實際載入的是建築法案），而且看起來完全像真的。這比少一塊功能嚴重得多。
function applyToolResult(c, toolMsg, data) {
  const tool = data.tool
  // **白名單：只有 `ok` 走下面的「正常結果」路徑，其餘一律畫成狀態卡。**
  // 原本這裡列舉 `failed || empty`，於是契約 2026-09-13 新增 `not_attempted` 之後，
  // 它從縫隙掉進 ok 路徑——畫面上是「✓ 完成」＋「檢索命中（0 筆）」，
  // 也就是把一次**根本沒發生的檢索**報告成「查完了，沒有」。實測命中過。
  // 列舉「哪些不正常」永遠會漏掉下一個新值；列舉「哪一個是正常」不會。
  if (data.status && data.status !== 'ok') {
    if (toolMsg) {
      toolMsg.running = false
      // 卡頭的狀態也跟著改，不能各種狀態都顯示綠勾「完成」。文字走 TOOL_STATUS。
      toolMsg.status = data.status
      // note 是空的時候才補一句。**只補這兩種本來就有的**——
      // 對 `not_attempted` 說「這個條件下沒有找到」是在描述一件沒發生的事，
      // 而後端那兩個情境都一定帶 note，補了反而是多一份會過期的文案。
      const fb = data.status === 'failed' ? '查詢來源失敗，可重試。' : data.status === 'empty' ? '這個條件下沒有找到。' : ''
      toolMsg.out = { type: 'status', status: data.status, note: data.note || fb }
    }
    return
  }

  // ok：把 tool_result 的真值放進 out，ToolOut.vue 只認 out 裡的東西。
  if (toolMsg) {
    // 這裡只剩 status 是 `ok` 或後端沒帶 status 兩種，寫 'ok' 是對的；
    // 但**不要無條件寫死**——上面那道白名單改了之後這行才安全。
    toolMsg.status = data.status || 'ok'
    if (tool === 'search_regulations' || tool === 'search_similar_decisions' || tool === 'retrieve_refs') {
      toolMsg.out = { type: OUT_TYPE[tool] || 'hits', hits: data.hits || [], pickedLaws: data.picked_laws || null }
    } else if (tool === 'build_relation_graph') {
      toolMsg.out = { type: 'graph', graph: data.graph || null }
    } else if (tool === 'extract_case_document') {
      toolMsg.out = { type: 'extract', runId: data.run_id || null, state: data.state || '' }
    } else if (tool === 'generate_decision_draft') {
      toolMsg.out = {
        type: 'draft',
        artifactId: data.artifact_id || null,
        runId: data.run_id || null,
        citeCount: typeof data.cite_count === 'number' ? data.cite_count : null,
        state: data.state || '',
        sections: null, // 全文走 #21 getArtifact 取，見下方 loadDraftSections
      }
      loadDraftSections(c, toolMsg.out)
    }
    toolMsg.running = false
  }

  // 歸檔 + 更新旗標（契約 §3.0「歸檔到」欄位）
  if (tool === 'extract_case_document') {
    if (data.run_id) c.runId = data.run_id
    c.flags.extract = true
    // 卷內四塊（案由／事實摘錄／爭點／程序審查）要**跑完之後**打彙整版才拿得到
    // （契約 §3.3）。`tool_result` 只帶 run_id 與 state，不含內容。
    refreshCase(c).then(() => {
      if (toolMsg && toolMsg.out && toolMsg.out.type === 'extract') toolMsg.out.loaded = true
    })
  } else if (tool === 'search_similar_decisions') {
    c.flags.cases = true
    reloadGroup(c, 'cases') // 後端已歸檔（listReferences #17），整組重載，跟 reload 走同一條路
  } else if (tool === 'search_regulations') {
    c.flags.laws = true
    reloadGroup(c, 'laws') // 同上（listCaseLaws #12）
  } else if (tool === 'build_relation_graph') {
    c.flags.graph = true
    // 關聯圖**不歸檔**（契約 §3.0）：它是同一份 run 的視圖，不是新的產出物，
    // 每次呼叫都從當下 payload 重算。之前這裡往「答辯書與產出」塞一筆，
    // 而且節點／關聯數是 data/graph.js 假資料的長度，不是這張圖的 stats。
    const st = (data.graph && data.graph.stats) || null
    const gi = c.docs.out.find((x) => x.graph)
    if (gi && st) gi.note = `${st.nodes} 節點．${st.edges} 條關聯`
  } else if (tool === 'generate_decision_draft') {
    if (data.run_id) c.runId = data.run_id
    c.flags.draft = true
    // 引註數用 cite_count 真值（契約 §3.5）。拿不到就不講數字，**不要沿用設計稿的「14 處」**。
    const citeNote = typeof data.cite_count === 'number' ? `引註 ${data.cite_count} 處．` : ''
    // **以 artifact_id 去重，不要用名字；名字也跟後端一致。**
    // 之前寫死「訴願決定書草稿 v1」＋用名字比對，有兩個問題：
    // (1) 生第二份草稿時它一樣被叫「v1」——後端那兩份 artifact 是不同的 run，
    //     畫面卻把第二份標成第一版；(2) 重新整理後 manifest 的名字是「訴願決定書草稿」，
    //     名字對不上就沒去重效果。id 才是身分，名字跟後端對齊。
    const exist = data.artifact_id && c.docs.out.find((x) => x._artifactId === data.artifact_id)
    if (exist) exist.note = `AI 生成．${citeNote}待承辦人審核`
    else
      addOne(c, 'out', {
        name: '訴願決定書草稿',
        note: `AI 生成．${citeNote}待承辦人審核`,
        ext: '稿',
        full: '',
        _artifactId: data.artifact_id,
      })
    // 重新生成草稿＝新的一版，舊基準先清掉，避免優化文案跨版本誤比。
    // 新基準等 #21 取回 sections[] 再存（見 loadDraftSections），這裡不塞假草稿。
    clearDraftText(c.caseId)
  } else if (tool === 'read_case') {
    // 契約 §3.6：`read_case` 的**內容走 token**，工具卡只負責讓人看得出「它讀了卷」。
    // 沒有這個分支時 `out` 是 undefined，卡片就只剩一張「✓ 完成」的空殼，
    // 後端寫給承辦人的那句 note 整段被丟掉（2026-09-13 實測一輪對話出現三張空卡）。
    // **只畫 note 那一句**，不要把讀回來的內容也塞進卡片——那是 token 的工作。
    // note 已經是給人看的中文（後端 `chat.py:1360`：「已讀取收文欄位（1 筆）。」），
    // 前端不再翻譯一次。status 非 ok 的兩條路在本函式最上面就處理掉了。
    if (toolMsg) toolMsg.out = { type: 'status', status: 'ok', note: data.note || '已讀取卷內資料。' }
  } else if (tool === 'refine_text') {
    // 契約 §3.4：`hits: []`，改寫文字無出處，本則標「請人工判斷」。
    // **前端不自己做 diff**：後端已把「改寫後的文章＋改了哪裡」用 token 串流出來，
    //  由 token 泡泡呈現即可；前端再算一次會多餘且可能把建議表亂比成紅綠字。
    if (toolMsg) toolMsg.out = { type: 'status', status: 'ok', note: data.note || '已改寫。改寫文字無出處，請人工判斷。' }
  }
}

// 草稿全文：`tool_result` 只帶 artifact_id／cite_count，**全文在 #21**（契約 §4.4）。
// 取回 sections[] 放進工具卡，同時當成優化文案 diff 的新基準。
// 取不到就留 null——工具卡會說「草稿已產出，展開右欄可看全文」，而不是畫一份寫死的草稿。
async function loadDraftSections(c, out) {
  if (!out.artifactId) return
  try {
    const a = await api.getArtifact(c.caseId, out.artifactId)
    const item = c.docs.out.find((x) => x._artifactId === out.artifactId)
    if (Array.isArray(a.sections) && a.sections.length) {
      out.sections = a.sections
      out.title = a.title || ''
      if (typeof a.cite_count === 'number') out.citeCount = a.cite_count
      if (item) {
        item.full = sectionsToHtml(a)
        // 引註數以 #21 的 `cite_count` 為準，並回寫右欄。
        // ⚠️ 實測 `tool_result.cite_count` 與 #21 的 `cite_count` **對不上**
        //（同一份草稿：工具回 5、#21 與匯出的 X-Cite-Count 都是 7，而 sections[]
        //  裡實際數得出 7）。兩個數字同時出現在畫面上會讓人不知道該信哪個，
        //  所以兩處都用 #21 的值——那也是匯出檔會帶出去的那個。後端不一致已回報。
        if (typeof a.cite_count === 'number') item.note = `AI 生成．引註 ${a.cite_count} 處．待承辦人審核`
      }
      saveDraftText(c.caseId, sectionsToText(a.sections)) // 潤稿 diff 的基準
    } else if (a.html) {
      // mock（及未回 sections 的後端）回 html：聊天室的草稿卡直接畫這份全文，
      // 並存純文字基準（否則潤稿沒得比對）。
      // 剝掉 html 自帶的最外層標題（.sec-h），避免與工具卡外層標題重複顯示。
      out.html = String(a.html).replace(/<div class="sec-h">[\s\S]*?<\/div>/, '')
      out.title = a.title || out.title
      if (item) item.full = a.html
      saveDraftText(c.caseId, htmlToPlainText(a.html))
    }
  } catch {
    /* 取不到全文不影響工具卡其餘資訊；不要用假草稿補 */
  }
}

// 從草稿 HTML 抽純文字（去引註 chip、去標籤），供潤稿 diff 的基準用。
function htmlToPlainText(html) {
  return String(html || '')
    .replace(/<span class="cite">[\s\S]*?<\/span>/g, '')
    .replace(/<h4[^>]*>[\s\S]*?<\/h4>/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

// sections[] → 顯示用 HTML。`title`／`meta` 是文件抬頭，與 sections[] 平行，不是 section（契約 §4.4）。
export function sectionsToHtml(a) {
  const head = a && a.title ? `<h4>${esc(a.title)}</h4>` : ''
  return (
    head +
    ((a && a.sections) || [])
      .map(
        (s) =>
          `<h4>${esc(s.h || '')}</h4>` +
          (s.blocks || [])
            .map((b) => {
              const cites = (b.cites || []).map((x) => `<span class="cite">${esc(x.label || x.id || '')}</span>`).join('')
              return `<p>${esc(b.text || '')}${cites}</p>`
            })
            .join(''),
      )
      .join('')
  )
}
function sectionsToText(sections) {
  return (sections || [])
    .map((s) => (s.blocks || []).map((b) => b.text || '').join('\n'))
    .join('\n\n')
    .trim()
}

//: 本案清單一筆 → 右欄項目。**這兩支是唯一的映射**，開案首載（彙整版 #4）與
//: 工具查完的重載（#12／#17）共用——兩邊回的是後端同一份 `manifest` 陣列
//: （`backend/api/dossier.py:151-152` 直接回 `m["laws"]`／`m["references"]`），
//: 所以同一份映射對兩條路都成立。
//:
//: **2026-09-13 之前不是這樣**：工具查完時前端自己從 `tool_result.hits[]` 組一份，
//: 讀的是 `h._full`／`h._libId`／`h._body`——那三個鍵是 `mock.js` 自己造的，
//: **真後端的 hits[] 只有契約 §3.2 那九個鍵**。於是 chat 查完點進去一片空白，
//: 重新整理之後才有內容（Ci 實際撞到）。兩條路兩份映射，一條對一條錯。
function lawItem(l) {
  return {
    name: l.t,
    // 契約 §3.5.2：手動挑進來的法規要**持續**標著它走到哪（matched／query_only／unused）。
    // 文案用後端的 retrieval_note，**不在前端另寫一份**——兩份比對規則遲早會兜不起來，
    // 出現「chat 說這條沒進查表、右欄卻標著進了」，使用者無從判斷哪個是真的。
    note: l.retrieval_note || l.note || '',
    status: l.retrieval_status || 'unknown',
    ext: '法',
    _libId: l.id,
    // `channel` 是契約 §4.0 定義的欄位，兩條通道的保證等級不同：
    //   `lawtable`（查法條 chip）→ `body_cached` **必然是空的**，快照只索引條號。
    //   `corpus`（母庫）→ 有全文（抓得到的話）。
    // 所以查表那批直接把後端那句「條號存在性驗證，非法條全文」當內容——
    // **留白的話 App.vue 會去打 §4.2 的全文端點，而 `lawtable:` 開頭的 id 打不到**，
    // 結果是一片空白加一句「取法規全文失敗」，而其實一點資料都沒掉。
    // 母庫那批 `body_cached` 空時仍然留白，維持即時取全文那條路。
    full: l.body_cached
      ? `<p style="font-family:var(--serif);line-height:2">${esc(l.body_cached)}</p><p style="color:var(--muted);font-size:12.5px;border-top:1px solid var(--line-soft);padding-top:10px">本案關聯：${esc(l.note || '')}</p>`
      : l.channel === 'lawtable'
        // 這裡取 `note` 而不是 `retrieval_note`：前者才是**解釋為什麼沒有全文**的那句
        // （「條號存在性驗證，非法條全文（快照只索引條號）」），後者講的是這一條
        // 走到哪個檢索狀態，已經顯示在右欄那一行了。
        ? `<p style="color:var(--muted);font-size:13px">${esc(l.note || l.retrieval_note || '')}</p>`
        : '',
  }
}
function decisionItem(d) {
  // 後端存的 `note` 是空字串（`backend/api/dossier.py:355`、`llm/chat.py:1224`），
  // 判決結果與分類存在 `verdict`／`category` 兩個鍵。只讀 note 的話右欄說明整排消失。
  //
  // **這裡沒有相似度，是對的。** 兩條歸檔路徑（REST 與 chat）都刻意存 `score: None`
  //（`dossier.py:329`、`chat.py:1227`）：分數是某一次查詢的相似度、不是這份決定書的
  // 屬性。百分比仍然出現在工具卡的命中清單上——那裡才有對應的查詢。
  return {
    name: d.t,
    note: d.note || decisionNote({ category: d.category, verdict: d.verdict }),
    ext: '例',
    _libId: d.id,
    full: d.full_cached || '',
  }
}

//: 工具自動歸檔到本案清單之後，**整組從後端重載**——不要再從 `hits[]` 自己組一份。
//: 歸檔是後端做的（2026-09-13 起），所以後端那份才是真相：`full_cached`、
//: 真 id（移除要用）、`verdict`／`category`、`retrieval_status` 一次到位。
//:
//: 代價是多一次往返（實測 < 1 秒）。**沒有先樂觀顯示再校正**：樂觀那份能填的欄位
//: 正好就是會出錯的那幾個（全文、id），畫出來的是一筆點不開、刪不掉的項目，
//: 比晚一秒出現糟。工具卡上的命中清單在這一秒裡已經看得到結果了。
const GROUP_RELOAD = {
  laws: (id) => api.listCaseLaws(id).then((r) => (r.laws || []).map(lawItem)),
  cases: (id) => api.listReferences(id).then((r) => (r.references || []).map(decisionItem)),
}
async function reloadGroup(c, key) {
  if (!c._serverCreated || !GROUP_RELOAD[key]) return
  try {
    const items = await GROUP_RELOAD[key](c.caseId)
    c.docs[key] = items
    if (!touched.includes(key)) touched.push(key)
    flushTouched()
  } catch {
    /* 重載失敗維持現狀：工具卡上的命中清單仍然看得到這次查到什麼 */
  }
}

// runTool：組 payload → 走 chat 串流。pdf/doc 例外走匯出下載（契約 §1.5 #23）。
export async function runTool(id, arg, silent) {
  if (state.busy) return
  const c = active()
  const tool = TOOLS.find((t) => t.id === id)
  if (!silent) {
    userMsg(arg ? `${tool.cmd} ${arg}` : tool.cmd, null, true)
    await sleep(240)
  }

  if (id === 'pdf' || id === 'doc') {
    await runExport(c, tool, id)
    return
  }

  const payload = {
    message: tool.cmd + (arg ? ' ' + arg : ''),
    tool_hint: TOOL_API[id],
    session_id: c.sessionId || undefined,
    run_id: c.runId || undefined,
  }
  if (id === 'refine') {
    payload.args = { instruction: arg && arg.trim() ? arg.trim() : '語氣更嚴謹、論理更緊密' }
    // 把上一版草稿文字帶給後端當改寫基準（契約 §2.1 context）；前端據此與新版做 diff。
    const prev = loadDraftText(c.caseId)
    if (prev) payload.context = { scope: 'all', text: prev }
  }

  state.busy = true
  try {
    await driveChat(c, payload, id)
  } finally {
    state.busy = false
    c.started = true
    flushTouched()
    scrollSoon()
  }
}

// 匯出：打 #23 export 端點。real 回真檔觸發下載；mock 回提示 blob（不下載，只歸檔）。
async function runExport(c, tool, id) {
  if (!c.flags.draft) {
    aiMsg('<p>還沒有草稿可以匯出，先跑一次<b>草稿生成</b>吧。</p>')
    return
  }
  const isPdf = id === 'pdf'
  const format = isPdf ? 'pdf' : 'docx'
  // 依**種類**找草稿，不要用寫死的名字比對——後端回的 artifact name 不一定叫
  // 「訴願決定書草稿 v1」，重新整理後用名字找就會拿到 undefined，
  // 然後打出 `/artifacts/undefined/export` 得到 400，畫面只說「匯出失敗，可重試」。
  // 取**最後一份**草稿，不是第一份——同一件案子可以生很多份，
  // `find` 會抓到最舊的那份，承辦人按「生成 PDF」拿到的會是上一版。
  const drafts = c.docs.out.filter((x) => x.ext === '稿' && x._artifactId)
  const draft = drafts[drafts.length - 1] || [...c.docs.out].reverse().find((x) => x._artifactId)
  const artifactId = draft && draft._artifactId
  if (!artifactId) {
    aiMsg('<p>找不到可匯出的草稿（沒有 artifact id），請重新生成一次草稿。</p>')
    return
  }
  state.busy = true
  // 先放一張 running 的工具卡（匯出可能慢，要有 loading）
  const card = push(c, { who: 'ai', kind: 'tool', ack: pick(ACKS[id] || ['好的。']), api: tool.api, name: tool.name, steps: [], running: true, out: null })
  try {
    const res = await api.exportArtifact(c.caseId, artifactId, format)
    // 檔名用後端 Content-Disposition 的真值；拿不到就用中性檔名，**不要編一個假案號**。
    const fname = res.filename || `訴願決定書草稿.${format}`
    card.running = false
    card.status = 'ok'
    card.out = {
      type: 'export',
      isPdf,
      fname,
      // 大小、引註數、對不回來的引註數都取自實際回應（X-Cite-Count／X-Unresolved-Cites），
      // 不是設計稿寫死的「A4 直式．4 頁．約 268 KB」。
      kb: res.blob && res.blob.size ? Math.max(1, Math.round(res.blob.size / 1024)) : null,
      citeCount: typeof res.citeCount === 'number' ? res.citeCount : null,
      unresolved: res.unresolved || 0,
      // 把檔案 blob 暫存在卡上，讓「下載」按鈕點了才存檔——**不自動下載**。
      // mock 沒有真檔（_mock），下載按鈕會走 reExport 重跑端點。
      _blob: res._mock ? null : res.blob || null,
    }
    if (!c.docs.out.some((x) => x.name === fname))
      addOne(c, 'out', { name: fname, note: (isPdf ? 'PDF' : 'Word') + '．訴願決定書版型', ext: isPdf ? 'pdf' : 'docx' })
    c.flags.out = true
    // 匯出的引用警示（交接文件第二件的匯出部分）：X-Unresolved-Cites 非 0 要警示；
    // X-Export-Warning 是後端做過編碼的中文，http.js 已 decode。
    let msg = `<p>${isPdf ? 'PDF' : 'Word 檔'}已產出並歸檔至右側「草稿文件產出」，點上方「下載檔案」即可存檔。</p>`
    if (res.unresolved > 0)
      msg += `<p style="color:var(--muted);font-size:12px;margin-top:6px">此檔含 ${res.unresolved} 處無法對應的引用，送簽前請先核對。</p>`
    if (res.warning)
      msg += `<p style="color:var(--muted);font-size:12px;margin-top:4px">${esc(res.warning)}</p>`
    aiMsg(msg)
    if (res.unresolved > 0) toast(`匯出完成，但有 ${res.unresolved} 處引用待核對`)
  } catch (e) {
    card.running = false
    card.out = { type: 'html', html: '<p>匯出失敗，可重試。</p>' }
    toast('匯出失敗，可重試')
  } finally {
    state.busy = false
    c.started = true
    flushTouched()
    scrollSoon()
  }
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// 匯出卡「下載檔案」按鈕：不自動下載，按了才存檔。
// 有暫存 blob（real）→ 直接存；沒有（mock 沒真檔）→ 重跑一次匯出端點。
export function downloadExport(out) {
  if (out && out._blob) triggerDownload(out._blob, out.fname || 'download')
  else runTool(out && out.isPdf ? 'pdf' : 'doc', '', true)
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
    const added = []
    files.forEach((f) => {
      if (!c.docs.evidence.some((x) => x.name === f.name)) {
        addOne(c, 'evidence', { name: f.name, note: f.note, ext: f.ext, _file: f._file })
        added.push(f)
      }
    })
    c.started = true
    if (isAutoName(c.name)) c.name = '吉○實業／違反廢清法'
    if (added.length) syncUploadFiles(c, added) // 背景同步後端（契約 #8）
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
    aiMsg(`<p>已收到 ${files.length} 份卷證並歸檔至右側「卷證檔案」。是否要開始解析？點選下方的「解析卷證檔案」，或直接輸入 <code style="font-family:var(--mono);font-size:11.5px">/解析卷證檔案</code>。</p>`)
    return
  }
  // 沒對到 /命令 的自由問句 → 走 chat 端點（不帶 tool_hint，由後端自行判斷是否用工具）。
  // matchTool 只是前端加速，後端仍會自己判（契約 §2.1）。
  const payload = { message: text, session_id: c.sessionId || undefined, run_id: c.runId || undefined }
  state.busy = true
  try {
    await driveChat(c, payload, null)
  } finally {
    state.busy = false
    c.started = true
    flushTouched()
    scrollSoon()
  }
}

// ── 附加 / 上傳 ──
// 這一段處理的是**真的 File 物件**（來自 <input type="file">）。本機只知道檔名與大小；
// 一份卷證讀不讀得到（`readable`）一律等後端回（契約 §4.1），前端不拿 File 的屬性去猜。

//: 對齊 `backend/intake/uploads.py` 的 `MAX_BYTES`。前端擋是為了不讓人傳了 30 秒才看到 400，
//: **權威仍在後端**——後端回的 detail 會照原文顯示，不會被這個常數蓋掉。
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

export function humanSize(n) {
  if (!Number.isFinite(n)) return ''
  if (n < 1024) return `${n} bytes`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
function extOf(name = '') {
  const m = /\.([a-z0-9]+)$/i.exec(name)
  return m ? m[1].toLowerCase() : ''
}
// File → 畫面用的卷證項目。`_file` 帶著真的 bytes，送 multipart 時用它。
// note 先寫本機看得到的大小並標「待後端判讀」，後端回來後由 reconcileFiles 整批換掉。
export function fileItem(file) {
  return { name: file.name, note: `${humanSize(file.size)}．待後端判讀`, ext: extOf(file.name), _file: file }
}
export function attachToPending(files) {
  files.forEach((f) => state.pendingFiles.push(fileItem(f)))
}
export function removePending(i) {
  state.pendingFiles.splice(i, 1)
}
export function uploadToFolder(files) {
  const c = active()
  const have = new Set(c.docs.evidence.map((x) => x.name))
  const dup = files.filter((f) => have.has(f.name))
  const added = []
  files.forEach((f) => {
    // 同名檔後端會回 409（清理後同名會互相覆蓋），先在這裡擋掉，訊息說得出是哪幾份。
    // ⚠️ 這道前置檢查讓補件的 409 **從 UI 走不到**（2026-09-13 實測時想觸發，被自己攔下）。
    // `errText` 的 409 分支沒有測試不是漏寫——要驗它得繞過這裡直接打端點。
    // 這道檢查若被拿掉，409 的顯示路徑要補驗一次。
    if (have.has(f.name)) return
    have.add(f.name)
    added.push(addOne(c, 'evidence', fileItem(f)))
  })
  const n = added.length
  if (dup.length) toast(`卷宗內已有同名卷證，略過 ${dup.length} 份：${dup.map((f) => f.name).join('、')}`)
  if (n) {
    c.started = true
    if (isAutoName(c.name)) c.name = '吉○實業／違反廢清法'
    // 樂觀更新：本地已加，背景同步後端（契約 #8 multipart files）。
    // mock 只是回聲；real 會真的持久化。失敗不回捲，demo 以本地為準。
    syncUploadFiles(c, added)
  }
  flushTouched()
  if (n) toast(`已送出 ${n} 份卷證，等後端判讀`)
  else if (!dup.length) toast('未選擇任何檔案')
}

// 一鍵示範：把合成卷證組成真的 File，走**同一條** uploadToFolder。
//
// 兩條路唯一的差別是 File 從哪來（模組字串 vs 使用者的硬碟），之後完全一樣：
// 一樣送真 bytes、一樣打 createCase／uploadFiles、一樣以 listFiles 對帳、
// 一樣吃後端判的 readable。**刻意不給示範檔另開一條管線**——分岔的那條
// 遲早會有一邊沒人測到，而 demo 當天跑的偏偏是那一邊。
//
// `new File([text], name)` 帶的是真內容（UTF-8 編碼後的真 bytes），
// 跟這次修掉的 `new File([''], name)` 是兩回事：那個是空殼配假檔名。
export function uploadDemoCase() {
  uploadToFolder(DEMO_CASE_FILES.map((d) => new File([d.text], d.name, { type: d.type })))
}

// 首次上傳＝建案（契約 §1.1「上傳卷證即建案」，走 createCase multipart 拿真 id）；
// 之後的上傳才走 uploadFiles。都是背景同步，失敗只提示不回捲。
// 建案會設 c._createTask（Promise），chat/runTool 前會 await 它，確保拿到真 caseId 再打後端。
function syncUploadFiles(c, files) {
  const task = (async () => {
    // 送的是使用者選的那個 File，不是同名的空殼——空殼上傳會建出一個有案號、有進度、
    // 卷證卻是 0 bytes 的案子，抽取什麼都抽不到，而失敗看起來完全像成功。
    const real = files.map((f) => f && f._file).filter(Boolean)
    if (!real.length) return
    try {
      const form = new FormData()
      real.forEach((f) => form.append('files', f, f.name))
      if (!c._serverCreated) {
        // 真後端 POST /api/cases 回 {case_id, files, provenance, next}（非 {case:{...}}）。
        const r = await api.createCase(form)
        c._serverCreated = true
        if (r && r.case_id) c.caseId = r.case_id
        // 建案回應沒有彙整版四群組，之後選案時再 getCase 補齊
        c._loaded = false
      } else {
        await api.uploadFiles(c.caseId, form)
      }
      // 建案回應的 files[] 是 {n,s,x}、**沒有 readable**；補件回應只含新上傳那幾份。
      // 兩條路徑統一再拉一次 listFiles（契約 §4.1 的 {id,name,ext,note,readable}），
      // 畫面上的卷證狀態就只有後端這一個來源。
      await reconcileFiles(c)
    } catch (e) {
      markUnsynced(files, e)
      toast(`卷證上傳失敗：${errText(e)}`)
    }
  })()
  c._createTask = task
  return task
}

// 後端的錯誤原文（ApiError.message 已是 body.detail）。**不吞掉**——20 MB 上限、
// 同名 409、合成案不能補件，這三種都只有後端說得出是哪一份、為什麼。
function errText(e) {
  const detail = (e && e.message) || ''
  if (detail) return detail
  return e && e.status ? `HTTP ${e.status}` : '後端無回應'
}

// 上傳失敗時不把使用者選的檔從畫面上抹掉，但要標明它**還沒進後端**，
// 不能讓它看起來跟已經收下的卷證一樣。
function markUnsynced(files, e) {
  const why = errText(e)
  files.forEach((f) => {
    f._unsynced = why
    f.note = `未同步到後端：${why}`
  })
}

// 以後端的卷證清單覆蓋本地（含 readable 與讀不到的原因）。拉不回就維持現狀。
export async function reconcileFiles(c) {
  try {
    const r = await api.listFiles(c.caseId)
    if (!r || !Array.isArray(r.files)) return
    c.docs.evidence = r.files.map((f) => ({
      name: f.name,
      note: f.note || '',
      ext: f.ext || '',
      readable: f.readable !== false,
      _libId: f.id,
    }))
  } catch {
    /* 清單拉不回來就維持現狀：上一步的成功不會因此被說成失敗 */
  }
}

// chat/工具打後端前呼叫：等建案與開案首載都完成，確保 caseId 與 runId 都是真的。
//
// **為什麼要等 `_loadTask`**：`GET /api/cases`（開機那次）沒有 `latest_run_id`，
// `runId` 是 `loadCase` 打 `getCase` 補上的，而 `selectCase` 呼叫它時不等回來。
// 選完案子立刻送出的 chat 會帶 `run_id: undefined`，後端就少了 run context。
// 平常看不出來是因為 getCase 通常比人打字快——**那是碰運氣，不是正確**。
async function ensureServerCase(c) {
  for (const task of [c._createTask, c._loadTask]) {
    if (!task) continue
    try {
      await task
    } catch {
      /* 建案失敗已在 syncUploadFiles 提示；載入失敗本來就不阻斷操作 */
    }
  }
}

// ── 搜尋加入（右欄群組的 ＋） ──
// 開對話框時取「本案已加入的名單」，避免重複加入。實際搜尋改走 searchLibrary（async，打後端）。
export function searchHave(groupKey) {
  const c = active()
  return new Set(c.docs[groupKey].map((x) => x.name))
}

// 看全文：打 getLaw #11 / getDecision #16 取母庫全文，組成顯示 HTML。
// real 模式本案清單項目的 full 為空，靠這裡即時取；mock 也回，故兩邊一致。
// 母庫全文兩種來源格式不同：真後端 getLaw/getDecision 回 S3 **純文字**（含 \n 斷行）；
// mock 的示範資料是**已排好的 HTML**。純文字要 escape 後把換行還原成段落／<br>，
// 否則塞進 v-html 會擠成一整段或把字面 \n 印出來；已是 HTML 的則原樣用。
function isHtml(s) {
  return /<(p|div|br|h[1-6]|ul|ol|li|table)\b/i.test(String(s || ''))
}
function fullToHtml(text) {
  if (isHtml(text)) return String(text) // 已排版的 HTML（mock 示範資料）
  const paras = String(text || '')
    .replace(/\r\n/g, '\n')
    .split(/\n{2,}/) // 空行分段
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => `<p>${esc(p).replace(/\n/g, '<br>')}</p>`) // 段內單換行 → <br>
    .join('')
  return paras
}
// 取全文失敗／端點回空時，顯示提示而不是空白 sheet（real 母庫需連 S3，取不到時尤其重要）。
function unavailableFull(what) {
  return `<p style="color:var(--muted);font-size:13px;line-height:1.8">目前無法取得${what}。母庫全文由後端即時載入，可能是連線或檔位（未接上母庫）問題，稍後再試。</p>`
}
export async function viewLawFull(libId) {
  try {
    const l = await api.getLaw(libId)
    const body = (l && l.body) || ''
    if (!body.trim()) return unavailableFull('條文全文') // 端點回了但沒內容
    return `<div style="font-family:var(--serif);line-height:2">${fullToHtml(body)}</div>`
  } catch {
    toast('取法規全文失敗')
    return unavailableFull('條文全文')
  }
}
export async function viewDecisionFull(libId) {
  try {
    const d = await api.getDecision(libId)
    if (d.full && String(d.full).trim()) return `<div style="font-family:var(--serif);line-height:2">${fullToHtml(d.full)}</div>`
    // 沒有全文時，至少把拿得到的結果／案型顯示出來，仍取不到才給提示
    const meta = (d.verdict || '') + ' ' + (d.category || '')
    return meta.trim() ? `<p style="color:var(--muted)">${esc(meta)}</p>` : unavailableFull('決定書全文')
  } catch {
    toast('取決定書全文失敗')
    return unavailableFull('決定書全文')
  }
}
// 產出（草稿）看全文：打 getArtifact #21 取草稿結構／HTML。
export async function viewArtifactFull(artifactId) {
  try {
    const a = await api.getArtifact(active().caseId, artifactId)
    // sections[] 結構 → HTML（契約 §4.4）。與工具卡共用 sectionsToHtml，
    // 兩處各寫一份轉換就會出現「同一份草稿在兩個地方長得不一樣」。
    if (Array.isArray(a.sections) && a.sections.length) return sectionsToHtml(a)
    // mock（或未回 sections 的後端）回 html：剝掉自帶最外層標題後直接用。
    if (a.html) return String(a.html).replace(/<div class="sec-h">[\s\S]*?<\/div>/, '')
    return unavailableFull('產出全文')
  } catch {
    toast('取產出全文失敗')
    return unavailableFull('產出全文')
  }
}

// 母庫查（server-side）：laws → searchLaws #10、cases → searchDecisions #15。
// 回統一形狀 [{id,name,note,ext}]（id＝母庫 id，供 addSearched 送 #13/#18）。
// 看全文的 body/full 母庫查不回，點看全文時再打 getLaw/getDecision（§4.2/§4.3）。
export async function searchLibrary(groupKey, q) {
  const query = (q || '').trim()
  if (!query) return []
  try {
    if (groupKey === 'laws') {
      const r = await api.searchLaws(query)
      return (r.results || []).map((x) => ({ id: x.id, name: x.t, note: x.src || '', ext: '法' }))
    } else {
      const r = await api.searchDecisions(query)
      return (r.results || []).map((x) => ({
        id: x.id,
        name: x.t,
        // 母庫查走 `search_corpus`，**那條通道沒有重排**（只做 doc_kind filter、
        // 去重、截 limit，見 backend/retrieval/kb.py:334 的 docstring），
        // 所以分數確實是向量距離。即使如此也不在這裡自己寫一句文案——
        // 全前端只有 api/ranker.js 那一份，改了一處才不會漏掉另一處。
        note: decisionNote({
          category: x.category,
          verdict: x.verdict,
          scoreCaption: x.score != null ? corpusScoreCaption(x.score) : '',
        }),
        ext: '例',
      }))
    }
  } catch {
    toast('搜尋失敗，可重試')
    return []
  }
}
//: 相似案例說明欄的**唯一**組法。兩個地方要它：剛搜尋加入時（帶那一次查詢的相似度）
//: 與重新整理後從後端還原時（沒有分數）。原本分開寫，結果是 reload 之後說明整排消失。
//:
//: **還原時不補分數。** `backend/api/dossier.py:329` 說明得很清楚：score 是某一次查詢
//: 的相似度、不是這份決定書的屬性，加進卷宗之後就沒有對應的查詢了，所以後端刻意存 null。
//: 為了版面好看補一個舊分數回去，會讓人以為那是「這份跟本案的相似度」——
//: 少一段是誠實的，補回去不是。
function decisionNote({ category, verdict, scoreCaption } = {}) {
  return [category, verdict, scoreCaption].filter(Boolean).join('．')
}

export function addSearched(groupKey, items) {
  const c = active()
  let n = 0
  items.forEach((item) => {
    // full 留空：看全文時再打 getLaw/getDecision 取（§4.2/§4.3）。_libId 供查全文與同步用。
    addOne(c, groupKey, { name: item.name, note: item.note, ext: item.ext, full: item.full || '', _libId: item.id })
    n++
  })
  flushTouched()
  // 背景同步後端（契約 #13 laws / #18 references）。item.id 是母庫 id（mock 帶 _libId）。
  syncAddSearched(c, groupKey, items)
  return n
}

async function syncAddSearched(c, groupKey, items) {
  const ids = items.map((it) => it.id).filter(Boolean)
  if (!ids.length) return
  try {
    if (groupKey === 'laws') await api.addCaseLaws(c.caseId, ids)
    else if (groupKey === 'cases') await api.addReferences(c.caseId, ids)
  } catch {
    toast('加入卷宗同步後端失敗，畫面仍可操作')
  }
}

// 生成草稿的前置條件檢查（交接文件第三件）：三者缺一都不該讓草稿按下去。
// 回傳「還缺什麼」的中文清單，空陣列＝可生成。
export function draftBlockers(c) {
  const miss = []
  if (!c.flags.extract) miss.push('解析卷證')
  if (!c.docs.laws.length) miss.push('相關法規')
  if (!c.docs.cases.length) miss.push('相關案例')
  return miss
}

// ── 建議 chips ──
export const chips = computed(() => {
  const c = active()
  if (!c) return []
  if (state.busy) return [{ ghost: true, label: '工具執行中⋯⋯' }]
  const f = c.flags
  const out = []
  const add = (label, action, lead, opts) => out.push({ label, action, lead, ...(opts || {}) })
  if (!c.docs.evidence.length) {
    add('上傳卷證檔案', { t: 'attach' }, '＋')
    // 合成測資（CONSTITUTION §3）。label 就寫「合成」，不要讓它看起來像真案子。
    add('載入合成示範卷證', { t: 'demo' }, '▸')
    add('你會什麼？', { t: 'ask' }, '?')
    return out
  }
  if (!f.extract) add('解析卷證檔案', { t: 'tool', id: 'extract' }, '▸')
  if (f.extract && !f.cases) add('查找相似案例', { t: 'tool', id: 'cases' }, '▸')
  if (f.extract && !f.laws) add('搜尋相關法規', { t: 'tool', id: 'laws' }, '▸')
  if (f.extract && !f.graph) add('生成關聯圖', { t: 'tool', id: 'graph' }, '▸')
  // 生成草稿：解析後就顯示，但三個前置條件（解析／法規／案例）不齊時 disable 並說明缺什麼
  // （交接文件第三件）。缺法規或案例時草稿其實生得出來，但引用會被清空、看起來像成功，
  // 所以要在按之前就擋住。
  if (f.extract && !f.draft) {
    const miss = draftBlockers(c)
    if (miss.length) add('生成決定書草稿', { t: 'tool', id: 'draft' }, '▸', { disabled: true, hint: '還需要：' + miss.join('、') })
    else add('生成決定書草稿', { t: 'tool', id: 'draft' }, '▸')
  }
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
// **同步的那一段不能省。** boot() 從同步改成 async 之後（26f063e），首次 render 時
// state.cases 還是空的，`active()` 回 undefined，Chat.vue 的 computed 讀
// `c.value.stream.length` 當場拋——Vue 把整個 App 的掛載中止，畫面是**全白**，
// 而且 console 只有一行 `[Vue warn] Unhandled error during execution of render function`。
// mock 與 real 都會白。所以這裡先同步建一個本地空案並選取，保證首次 render 一定有 active()，
// 非同步的載入才接在後面。
export function boot() {
  const placeholder = createCase(null, null)
  state.activeId = placeholder.id
  return bootAsync(placeholder)
}

async function bootAsync(placeholder) {
  // 開機檢查檔位（health #1）＋載入既有案件清單（listCases #2）。
  // mock 模式一樣會回應（run_mode:'mock'），流程一致；real 模式才是真的檢查後端。
  await checkHealth()
  let loadedAny = false
  try {
    const r = await api.listCases()
    const list = (r && r.cases) || []
    list.forEach((cs) => {
      const c = newCaseObj(cs.name, null)
      c.caseId = cs.id
      c._serverCreated = true
      if (cs.latest_run_id) c.runId = cs.latest_run_id
      state.cases.push(c)
    })
    loadedAny = list.length > 0
  } catch {
    /* 後端不可用時退回本地空案（placeholder 已經在了） */
  }
  // 後端有既有案件 → 丟掉本地佔位空案，選第一件；沒有就留著 placeholder 讓使用者開始上傳。
  if (loadedAny) {
    state.cases = state.cases.filter((x) => x.id !== placeholder.id)
    selectCase(state.cases[0].id)
  }
}

// health #1：把 run_mode／可用性存進 state，供 UI 判斷聊天是否可用（契約 §5）
export async function checkHealth() {
  try {
    const h = await api.health()
    state.healthOk = true
    state.runMode = (h && (h.run_mode || (h.provenance && h.provenance.run_mode))) || null
  } catch {
    state.healthOk = false
    state.runMode = null
  }
}
export { CASE_NO, TOOLS }
export { esc, isAutoName, inlineMd }
