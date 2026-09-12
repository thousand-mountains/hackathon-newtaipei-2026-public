// 訴願智慧輔助平台 — 應用狀態與辦案流程（單例 reactive store）
// 移植自 design/訴願智慧輔助平台.html 的命令式 JS，改為 Vue 響應式模型。
import { reactive, computed } from 'vue'
import { CASE_NO, EVIDENCE_POOL, LAW_POOL, CASE_POOL, TOOLS, GROUPS, ACKS } from '../data/data.js'
import { GNODES, GEDGES, DRAFT_HTML } from '../data/graph.js'
import { api } from '../api/index.js'
import { diffToHtml } from '../api/diff.js'

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
// esc 的逆運算：把逐字串接後的 HTML 還原成純文字，供 diff 比對用。
const unesc = (s) =>
  String(s).replace(/<br>/g, '\n').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&amp;/g, '&')
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
  let toolMsg = null
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

  const appendToken = (text) => {
    clearThinking()
    if (!tokenMsg) tokenMsg = push(c, { who: 'ai', kind: 'html', html: '' })
    tokenMsg.html += esc(text)
    scrollSoon()
  }

  let stream
  try {
    stream = api.chat(c.caseId, payload)
  } catch (e) {
    clearThinking()
    aiMsg('<p>連線建立失敗，可稍後重問。</p>')
    return
  }

  try {
    for await (const { event, data } of stream) {
      if (event === 'ack') {
        if (data.session_id) c.sessionId = data.session_id
        if (uiTool) {
          clearThinking()
          toolMsg = push(c, {
            who: 'ai',
            kind: 'tool',
            ack: data.text || null,
            api: TOOL_API[uiTool] || '',
            name: (TOOLS.find((t) => t.id === uiTool) || {}).name || '',
            steps: [],
            running: true,
            out: null,
          })
        } else if (data.text) {
          // 純問答：ack 是一句口白，先顯示，但 thinking 泡泡保留到首個 token／done——
          // 因為真正的答案還在後面（5–15s），這段等待才是要 loading 的地方。
          aiMsg('<p>' + esc(data.text) + '</p>')
          // 把 thinking 移到 ack 之後（保持在串流最底）
          if (thinkingMsg) {
            const i = c.stream.indexOf(thinkingMsg)
            if (i >= 0) c.stream.splice(i, 1)
            c.stream.push(thinkingMsg)
            scrollSoon()
          }
        }
      } else if (event === 'tool_call') {
        clearThinking()
        if (!toolMsg) toolMsg = push(c, { who: 'ai', kind: 'tool', ack: null, api: data.tool, name: data.label || '', steps: [], running: true, out: null })
        else toolMsg.api = data.tool
      } else if (event === 'tool_step') {
        if (!toolMsg) continue
        const key = data.call_id + ':' + data.step
        if (data.status === 'running' && stepIdx[key] == null) {
          stepIdx[key] = toolMsg.steps.push({ label: data.label, t: '', degraded: !!data.degraded }) - 1
        } else if (data.status === 'done') {
          const row = { label: data.label, t: data.elapsed_ms ? (data.elapsed_ms / 1000).toFixed(1) : '', degraded: !!data.degraded }
          if (stepIdx[key] != null) toolMsg.steps[stepIdx[key]] = row
          else toolMsg.steps.push(row)
        }
        scrollSoon()
      } else if (event === 'tool_result') {
        applyToolResult(c, toolMsg, data)
      } else if (event === 'token') {
        appendToken(data.text || '')
      } else if (event === 'done') {
        if (data.session_id) c.sessionId = data.session_id
        // 沒有 token 也沒有工具卡、但有 answer → 用 answer 補一則（否則泡泡會空白）
        if (!tokenMsg && !toolMsg && data.answer) {
          clearThinking()
          tokenMsg = push(c, { who: 'ai', kind: 'html', html: '' })
        }
        clearThinking()
        // 優化文案（real 路徑）：改寫後的新版文字走 token 串回（契約 §3.4）。
        // 若工具卡還沒有 diff（mock 走 refined_text 已在 tool_result 設好），這裡用最終文字補算。
        if (uiTool === 'refine' && toolMsg && !(toolMsg.out && toolMsg.out.type === 'diff')) {
          const streamedText = tokenMsg ? unesc(tokenMsg.html.replace(/^<p>/, '').replace(/<\/p>$/, '')) : ''
          const next = (data.answer || streamedText || '').trim()
          if (next) {
            const prev = loadDraftText(c.caseId)
            toolMsg.out = { type: 'diff', html: diffToHtml(prev, next), instruction: (payload.args && payload.args.instruction) || '' }
            saveDraftText(c.caseId, next)
            // 收掉獨立的 token 泡泡（改寫文字改以 diff 呈現，不重複顯示原文）
            if (tokenMsg) {
              const i = c.stream.indexOf(tokenMsg)
              if (i >= 0) c.stream.splice(i, 1)
              tokenMsg = null
            }
          }
        }
        if (tokenMsg) {
          // 校正 token 串接：以 done.answer 為準，但「不得比已顯示的內容更短」——
          // 避免 answer 缺漏／截斷時反而把完整的逐字內容蓋掉（話被 cut 的第二個來源）。
          const streamed = tokenMsg.html.replace(/^<p>/, '').replace(/<\/p>$/, '')
          const answer = data.answer ? esc(data.answer) : ''
          const best = answer.length >= streamed.length ? answer : streamed
          tokenMsg.html = '<p>' + best + '</p>'
        }
        if (data.dropped_refs && data.dropped_refs.length && tokenMsg)
          tokenMsg.html += '<p style="color:var(--muted);font-size:12px;margin-top:6px">此則含無法對應的引用，請勿直接採用。</p>'
        if (toolMsg) toolMsg.running = false
        return
      } else if (event === 'error') {
        clearThinking()
        if (toolMsg) toolMsg.running = false
        // 契約 §2.3：error.stage ∈ tool|model|transport|internal。transport 不當模型錯誤。
        aiMsg('<p>' + (data.stage === 'transport' ? '連線中斷，可重問。' : '執行時發生問題：' + esc(data.error || '未知錯誤')) + '</p>')
        toast(data.stage === 'transport' ? '連線中斷，可重問' : '執行發生問題')
        return
      }
    }
  } catch (e) {
    clearThinking()
    if (toolMsg) toolMsg.running = false
    aiMsg('<p>連線中斷，可重問。</p>')
    toast('連線中斷，可重問')
  }
}

// tool_result → 設定工具卡 out（ToolOut.vue 依 type 呈現）＋依 §3 歸檔到右欄卷宗
function applyToolResult(c, toolMsg, data) {
  const tool = data.tool
  if (data.status === 'failed' || data.status === 'empty') {
    if (toolMsg) {
      toolMsg.running = false
      const fb = data.status === 'failed' ? '查詢來源失敗，可重試。' : '這個條件下沒有找到。'
      toolMsg.out = { type: 'html', html: '<p>' + esc(data.note || fb) + '</p>' }
    }
    return
  }

  // ok：設定 out.type（內容用既有 demo 資料，畫面形狀不變）
  if (toolMsg) {
    if (tool === 'generate_decision_draft') toolMsg.out = { type: 'html', html: data.html || DRAFT_HTML }
    else if (OUT_TYPE[tool]) toolMsg.out = { type: OUT_TYPE[tool] }
    toolMsg.running = false
  }

  // 歸檔 + 更新旗標（契約 §3.0「歸檔到」欄位）
  if (tool === 'extract_case_document') {
    if (data.run_id) c.runId = data.run_id
    c.flags.extract = true
  } else if (tool === 'search_similar_decisions') {
    archiveHits(c, 'cases', data.hits, (h) => ({ name: h.t, note: h.note || '向量相似度（非法律相似度）', ext: '例', full: h._full }))
    c.flags.cases = true
  } else if (tool === 'search_regulations') {
    archiveHits(c, 'laws', data.hits, (h) => ({
      name: h.t,
      note: h.note,
      ext: '法',
      full: h._body
        ? `<p style="font-family:var(--serif);line-height:2">${esc(h._body)}</p><p style="color:var(--muted);font-size:12.5px;border-top:1px solid var(--line-soft);padding-top:10px">本案關聯：${esc(h.note || '')}</p>`
        : '',
    }))
    c.flags.laws = true
  } else if (tool === 'build_relation_graph') {
    c.flags.graph = true
    if (!c.docs.out.some((x) => x.name === '案件關聯圖'))
      addOne(c, 'out', { name: '案件關聯圖', note: GNODES.length + ' 節點．' + GEDGES.length + ' 條關聯', ext: '圖', graph: true })
  } else if (tool === 'generate_decision_draft') {
    if (data.run_id) c.runId = data.run_id
    c.flags.draft = true
    if (!c.docs.out.some((x) => x.name === '訴願決定書草稿 v1'))
      addOne(c, 'out', { name: '訴願決定書草稿 v1', note: `AI 生成．引註 ${data.cite_count || 14} 處．待承辦人審核`, ext: '稿', full: data.html || DRAFT_HTML, _artifactId: data.artifact_id })
    // 重新生成草稿＝新的一版，先清掉舊基準再存新版，避免優化文案跨版本誤比。
    clearDraftText(c.caseId)
    saveDraftText(c.caseId, draftHtmlToText(data.html || DRAFT_HTML))
  } else if (tool === 'refine_text') {
    // 優化文案：拿上一版（localStorage）與後端回傳新版做 diff，畫成前後對照。
    const prev = loadDraftText(c.caseId)
    const next = data.refined_text || esc(data.answer || '')
    if (next) {
      if (toolMsg) toolMsg.out = { type: 'diff', html: diffToHtml(prev, next), instruction: (data.instruction || '') }
      saveDraftText(c.caseId, next) // 新版成為下次 diff 的基準
    }
  }
}

function archiveHits(c, key, hits, mapper) {
  const have = new Set(c.docs[key].map((x) => x.name))
  ;(hits || []).forEach((h) => {
    const item = mapper(h)
    if (!have.has(item.name)) addOne(c, key, item)
  })
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
  const draft = c.docs.out.find((x) => x.name === '訴願決定書草稿 v1')
  const artifactId = draft && draft._artifactId
  state.busy = true
  // 先放一張 running 的工具卡（匯出可能慢，要有 loading）
  const card = push(c, { who: 'ai', kind: 'tool', ack: pick(ACKS[id] || ['好的。']), api: tool.api, name: tool.name, steps: [], running: true, out: null })
  try {
    const res = await api.exportArtifact(c.caseId, artifactId, format)
    const fname = res.filename || `新北府訴決字第1141234567號_訴願決定書草稿.${format}`
    card.running = false
    card.out = { type: 'export', isPdf, fname }
    if (res.blob && !res._mock) triggerDownload(res.blob, fname)
    if (!c.docs.out.some((x) => x.name === fname))
      addOne(c, 'out', { name: fname, note: (isPdf ? 'PDF' : 'Word') + '．訴願決定書版型', ext: isPdf ? 'pdf' : 'docx' })
    c.flags.out = true
    aiMsg(`<p>${isPdf ? 'PDF' : 'Word 檔'}已產出並歸檔至右側「答辯書與產出」。</p>`)
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
        addOne(c, 'evidence', { name: f.name, note: f.note, ext: f.ext })
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
    // 樂觀更新：本地已加，背景同步後端（契約 #8 multipart files）。
    // mock 只是回聲；real 會真的持久化。失敗不回捲，demo 以本地為準。
    syncUploadFiles(c, files)
  }
  flushTouched()
  toast(n ? `已上傳 ${n} 份卷證` : '未選擇任何檔案')
}

async function syncUploadFiles(c, files) {
  try {
    const form = new FormData()
    files.forEach((f) => form.append('files', new File([''], f.name)))
    await api.uploadFiles(c.caseId, form)
  } catch {
    // 樂觀更新已顯示成功，僅在背景同步失敗時輕提示（不回捲，demo 以本地為準）
    toast('卷證同步後端失敗，畫面仍可操作')
  }
}

// ── 搜尋加入（右欄群組的 ＋） ──
// 母庫查詢的候選清單。id 對齊契約母庫 id（擬 S3 相對路徑），供 addSearched 送真後端。
// demo 用本地目錄即為母庫；real 模式下 addSearched 會用這裡的 id 打 #13/#18。
export function searchPool(groupKey) {
  const c = active()
  const pool =
    groupKey === 'laws'
      ? LAW_POOL.map((l) => ({
          id: `kb/public/相關法規_全量/${l.title}.txt`,
          name: l.no + '　' + l.title,
          note: l.note,
          ext: '法',
          kw: (l.no + l.title + l.note + l.body).toLowerCase(),
          full: `<p style="font-family:var(--serif);line-height:2">${esc(l.body)}</p><p style="color:var(--muted);font-size:12.5px;border-top:1px solid var(--line-soft);padding-top:10px">本案關聯：${esc(l.note)}</p>`,
        }))
      : CASE_POOL.map((k) => ({
          id: `kb/public/新北訴願決定書_環保局全量/${k.no}_${k.verdict}.txt`,
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
    addOne(c, groupKey, { name: item.name, note: item.note, ext: item.ext, full: item.full, _libId: item.id })
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
  if (!f.extract) add('解析卷證檔案', { t: 'tool', id: 'extract' }, '▸')
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
