// Mock 後端：在前端記憶體裡模擬契約 v2 的全部端點與 chat SSE 串流。
//
// 設計目標：**回應形狀「照契約做」**，讓 store 用跟 real.js 完全相同的方式消費。
// 之後 VITE_API_MODE=real 就直接換掉整包，store 一行都不用改。
//
// 資料來源沿用現有 demo 資料（data.js / graph.js），但欄位名對齊契約 §3／§4：
//   - 檢索 hits[]：{id,t,src,score,verified,note,provenance,origin}（§3.2）
//   - tool_result：{seq,turn_id,call_id,tool,status,note,hits,run_id,graph}（§3.1）
//   - done：{session_id,answer,elapsed_ms,redirect,dropped_refs}（§2.4）
//   - 母庫查：{results:[{id,t,src,score,doc_kind,...}]}（§4.2/§4.3）
import { EVIDENCE_POOL, LAW_POOL, CASE_POOL, ACKS } from '../data/data.js'
import { GNODES, GEDGES, GCOLS, DRAFT_HTML } from '../data/graph.js'
import { MOCK_TIMING } from './config.js'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const pick = (a) => a[Math.floor(Math.random() * a.length)]
let seq = 0
const nid = (p) => `${p}-${Date.now().toString(36)}-${++seq}`
const now = () => new Date().toISOString()

// ── 記憶體「資料庫」：一案一份 manifest（契約 §4.0）──
const DB = {
  cases: new Map(), // id -> {id,name,created_at,latest_run_id,files,laws,references,artifacts}
  sessions: new Map(), // session_id -> {caseId, turns}
}

function ensureCase(id, name) {
  if (!DB.cases.has(id)) {
    DB.cases.set(id, {
      id,
      name: name || '未命名案件',
      created_at: now(),
      latest_run_id: null,
      files: [],
      laws: [],
      references: [],
      artifacts: [],
    })
  }
  return DB.cases.get(id)
}

// ── 母庫（唯讀）：把 demo 資料轉成契約形狀的「庫」──
// 法規母庫：id = 擬 S3 相對路徑（契約規定 id 就是 rel path）
const LAW_LIBRARY = LAW_POOL.map((l) => ({
  id: `kb/public/相關法規_全量/${l.title}.txt`,
  t: `${l.no}　${l.title}`,
  src: '全國法規資料庫',
  score: null,
  doc_kind: 'statute',
  verified: false, // KB 全文恆 false（契約 §4.2）
  relevance: 'unknown',
  body: l.body,
  note: l.note,
}))

// 案例母庫：provenance 依 real 旗標分流（賽方資料集 vs 公開爬蟲）
const DECISION_LIBRARY = CASE_POOL.map((k) => ({
  id: `kb/public/新北訴願決定書_環保局全量/${k.no}_${k.verdict}.txt`,
  t: `${k.no}　${k.title}`,
  src: k.real ? '賽方資料集' : '市府公開全量爬蟲',
  score: k.sim / 100,
  verdict: k.verdict,
  category: k.type,
  provenance: k.real ? 'official' : 'public_crawl',
  doc_kind: 'decision',
  verified: false,
  full: k.full,
}))

// ── 工具中文標籤（契約 §3.0；後端會帶，mock 也帶）──
const TOOL_LABEL = {
  extract_case_document: '解析卷證檔案',
  search_regulations: '查法條',
  search_similar_decisions: '查相似訴願決定',
  retrieve_refs: '查判解與函釋',
  generate_decision_draft: '生成草稿',
  refine_text: '潤稿',
  build_relation_graph: '產生案件關聯圖',
  read_case: '讀卷內',
}

// pipeline 節點步驟（契約 §2.3 tool_step：只有 extract / draft 會發）
const STEPS_EXTRACT = [
  { step: 'n1', label: '讀卷抽取', ms: 3200 },
  { step: 'n2', label: '案件分類', ms: 2100 },
  { step: 'n3', label: '程序審查', ms: 2600 },
]
const STEPS_DRAFT = [
  { step: 'n4', label: '檢索法條與相似案', ms: 4200 },
  { step: 'n5', label: '草稿撰寫', ms: 6800 },
  { step: 'n6', label: '引用守門', ms: 2400 },
]

// ── SSE 事件建構器 ──
function makeEmitter() {
  const events = []
  const turnId = nid('turn')
  let s = 0
  const emit = (event, data) => events.push({ event, data: { seq: s++, turn_id: turnId, ...data } })
  return { events, emit, turnId }
}

// 把 CASE_POOL / LAW_POOL 轉成契約 §3.2 的 hits[]
function caseHits() {
  return DECISION_LIBRARY.map((d, i) => ({
    id: `c${i + 1}`,
    t: d.t,
    src: d.src,
    score: d.score,
    verified: false,
    note: 'KB 命中，未對資料集實檔驗證',
    provenance: d.provenance,
    origin: 'retrieval',
    _libId: d.id,
    _verdict: d.verdict,
    _category: d.category,
    _full: d.full,
  }))
}
function lawHits() {
  return LAW_LIBRARY.map((l, i) => ({
    id: `L${i + 1}`,
    t: l.t,
    src: l.src,
    score: null,
    verified: false, // 查表通道才可能 true；KB 全文恆 false
    note: l.note,
    provenance: null, // 法條查表 Hit 沒有這個鍵（契約明列，前端要處理 null）
    origin: 'retrieval',
    _libId: l.id,
    _body: l.body,
  }))
}

// ── chat：核心 SSE 產生器。依 tool_hint / message 決定 agent 呼叫哪支工具 ──
// 回傳 async generator，逐一 yield {event,data}，時序照契約 §2.3。
async function* chatStream(caseId, payload) {
  const c = ensureCase(caseId)
  const started = Date.now()
  const em = makeEmitter()
  const sessionId = payload.session_id || nid('sess')
  if (!DB.sessions.has(sessionId)) DB.sessions.set(sessionId, { caseId, turns: 0 })

  const tool = decideTool(payload, c)

  // 1) ack（帶 session_id，契約 ② 提前）
  const ackText = pick(ACKS[shortId(tool)] || ['好的，這就處理。'])
  em.emit('ack', { text: ackText, session_id: sessionId })
  yield await drain(em)
  await sleep(MOCK_TIMING.ack)

  // 沒對到工具 → 純問答：只吐 token + done
  if (!tool) {
    const answer = fallbackAnswer(payload.message)
    for await (const t of tokenize(em, answer)) yield t
    em.emit('done', { session_id: sessionId, answer, elapsed_ms: Date.now() - started, redirect: null, dropped_refs: [] })
    yield await drain(em)
    return
  }

  // 2) tool_call
  const callId = nid('tc')
  em.emit('tool_call', { call_id: callId, tool, label: TOOL_LABEL[tool], args: payload.args || {} })
  yield await drain(em)

  // 3) tool_step（僅 extract / draft）
  const stepPlan = tool === 'extract_case_document' ? STEPS_EXTRACT : tool === 'generate_decision_draft' ? STEPS_DRAFT : null
  if (stepPlan) {
    for (const st of stepPlan) {
      em.emit('tool_step', { call_id: callId, step: st.step, label: st.label, status: 'running', degraded: false })
      yield await drain(em)
      await sleep(MOCK_TIMING.step)
      em.emit('tool_step', { call_id: callId, step: st.step, label: st.label, status: 'done', elapsed_ms: st.ms, degraded: false })
      yield await drain(em)
    }
  } else {
    await sleep(MOCK_TIMING.step)
  }

  // 4) tool_result（形狀依工具，並歸檔到 manifest；契約 §3）
  const result = buildToolResult(c, tool, callId, payload)
  if (result.precheckFail) {
    em.emit('tool_result', { call_id: callId, tool, status: 'failed', note: result.precheckFail, hits: [], run_id: null, graph: null })
    yield await drain(em)
    const answer = result.precheckFail
    for await (const t of tokenize(em, answer)) yield t
    em.emit('done', { session_id: sessionId, answer, elapsed_ms: Date.now() - started, redirect: null, dropped_refs: [] })
    yield await drain(em)
    return
  }
  em.emit('tool_result', {
    call_id: callId,
    tool,
    status: result.status,
    note: result.note || '',
    hits: result.hits || [],
    run_id: result.run_id || null,
    graph: result.graph || null,
    ...(result.extra || {}),
  })
  yield await drain(em)
  await sleep(MOCK_TIMING.toolResult)

  // 5) token（agent 文字回覆逐字）
  for await (const t of tokenize(em, result.answer)) yield t

  // 6) done（唯一終止事件；本期前端只讀 session_id/answer/elapsed_ms + redirect/dropped_refs）
  em.emit('done', {
    session_id: sessionId,
    answer: result.answer,
    elapsed_ms: Date.now() - started,
    redirect: result.redirect || null,
    dropped_refs: result.dropped_refs || [],
  })
  yield await drain(em)
}

// emitter 累積的事件一次吐一顆（保持 generator 逐事件節奏）
async function drain(em) {
  return em.events.shift()
}

async function* tokenize(em, text) {
  // 把回答切成幾段逐字 append（模擬 token 串流）
  const chunks = String(text).match(/[\s\S]{1,8}/g) || []
  for (const ch of chunks) {
    em.emit('token', { text: ch })
    await sleep(MOCK_TIMING.tokenChunk)
    while (em.events.length) yield em.events.shift()
  }
}

function shortId(tool) {
  return {
    extract_case_document: 'extract',
    search_similar_decisions: 'cases',
    search_regulations: 'laws',
    build_relation_graph: 'graph',
    generate_decision_draft: 'draft',
    refine_text: 'refine',
  }[tool]
}

// tool_hint 優先；否則用關鍵字粗判（後端本來就會自己判，mock 從簡）
function decideTool(payload, c) {
  if (payload.tool_hint) return payload.tool_hint
  const t = (payload.message || '').toLowerCase()
  const kw = [
    ['extract_case_document', ['解析', '萃取', '讀卷', '看卷', '分析卷']],
    ['search_similar_decisions', ['相似', '案例', '前例', '類似', '判決']],
    ['search_regulations', ['法規', '法條', '條文', '函釋', '依據']],
    ['build_relation_graph', ['關聯圖', '心智圖', '關係圖', '脈絡', 'graph']],
    ['generate_decision_draft', ['草稿', '擬稿', '起草', '撰擬', '決定書', 'draft']],
    ['refine_text', ['優化', '修潤', '潤稿', '改寫', '精簡']],
  ]
  for (const [tool, words] of kw) if (words.some((w) => t.includes(w.toLowerCase()))) return tool
  return null
}

// ── 每支工具的 tool_result + answer + 歸檔（契約 §3.2–§3.7 + §4）──
function buildToolResult(c, tool, callId, payload) {
  switch (tool) {
    case 'extract_case_document': {
      if (!c.files.length)
        return { precheckFail: '咦，卷宗還是空的——先用輸入框左邊的「＋」把訴願書、原處分書和答辯書丟進來吧。' }
      const runId = nid('run')
      c.latest_run_id = runId
      return {
        status: 'ok',
        run_id: runId,
        extra: { state: 'SCREENED' },
        answer:
          '讀完了！本案程序要件大致齊備，惟陳述意見程序與廢棄物性質之證據兩項有待補強，將直接影響主文走向。建議接續進行案例比對與法規檢索。',
      }
    }
    case 'search_similar_decisions': {
      const hits = caseHits()
      hits.forEach((h) =>
        archive(c, 'references', {
          id: h.id,
          t: h.t,
          src: h.src,
          note: `${h._category}．${h._verdict}．向量相似度 ${Math.round(h.score * 100)}%（非法律相似度）`,
          score: h.score,
          provenance: h.provenance,
          doc_kind: 'decision',
          full_cached: h._full,
        }),
      )
      return {
        status: hits.length ? 'ok' : 'empty',
        hits,
        answer: `撈到 ${hits.length} 筆相似訴願決定，都放進右側卷宗了，點卡片可看全文。相似度為向量相似度，非法律見解相似度。`,
      }
    }
    case 'search_regulations': {
      const hits = lawHits()
      hits.forEach((h) =>
        archive(c, 'laws', {
          id: h.id,
          t: h.t,
          src: h.src,
          note: h.note,
          verified: false,
          relevance: 'unknown',
          body_cached: h._body,
        }),
      )
      return {
        status: hits.length ? 'ok' : 'empty',
        hits,
        answer: `${hits.length} 筆法規入袋，點右側項目可看條文全文。草稿只會引用卷宗內的條文，卷宗即為引註白名單。`,
      }
    }
    case 'retrieve_refs': {
      return { status: 'empty', hits: [], answer: '本案暫無可對應的判解或函釋。' }
    }
    case 'build_relation_graph': {
      if (!c.latest_run_id)
        return { precheckFail: '要先生成草稿才畫得出完整關聯——先跑一次解析卷證與生成草稿吧。' }
      const graph = buildGraph()
      archive(c, 'artifacts', {
        id: nid('art'),
        name: '案件關聯圖',
        kind: 'graph',
        note: `${graph.stats.nodes} 節點．${graph.stats.edges} 條關聯`,
        created_at: now(),
        run_id: c.latest_run_id,
        _graph: graph,
      })
      return {
        status: 'ok',
        graph,
        answer: '畫好了！關聯圖已歸檔。非 ok 的引用邊以警示色標示，代表該引用有疑慮，撰擬理由時應優先處理。',
      }
    }
    case 'generate_decision_draft': {
      const hasLaws = c.laws.length
      const hasCases = c.references.length
      if (!hasLaws || !hasCases)
        return { precheckFail: '草稿只認右側卷宗裡的東西當來源。請先查相似案例與相關法規，我才不會寫出沒憑沒據的句子。' }
      const runId = nid('run')
      c.latest_run_id = runId
      const artId = nid('art')
      const citeCount = (DRAFT_HTML.match(/class="cite"/g) || []).length
      archive(c, 'artifacts', {
        id: artId,
        name: '訴願決定書草稿 v1',
        kind: 'draft',
        note: `AI 生成．引註 ${citeCount} 處．待承辦人審核`,
        created_at: now(),
        run_id: runId,
        _html: DRAFT_HTML,
      })
      return {
        status: 'ok',
        run_id: runId,
        extra: { state: 'VERIFIED', artifact_id: artId, cite_count: citeCount, html: DRAFT_HTML },
        answer: `初稿出爐，已歸檔。全文 ${citeCount} 處引註皆可回溯至右側卷宗，未附來源之推論已略去。擬採主文為「原處分撤銷，由原處分機關於 2 個月內另為適法之處分」。`,
      }
    }
    case 'refine_text': {
      const instr = (payload.args && payload.args.instruction) || '語氣更嚴謹、論理更緊密'
      // 潤稿要有「新版文字」讓前端跟舊版做 diff（契約 §3.4：diff 在前端算）。
      // 前端會把上一版文字放進 context.text 傳來；mock 據以產出一段改寫。
      const base = (payload.context && payload.context.text) || ''
      const refined = mockRewrite(base, instr)
      return {
        status: 'ok',
        hits: [],
        // refined_text：本工具改寫後的新版全文（real 走 token 串回，mock 直接給）
        extra: { instruction: instr, refined_text: refined },
        // 潤稿本回合強制紅燈（契約 §3.4）
        dropped_refs: [],
        answer: refined || `改好了（${instr}）。`,
      }
    }
    case 'read_case': {
      return { status: 'ok', hits: [], answer: '（讀卷內）本案卷內摘要如上。' }
    }
    default:
      return { status: 'failed', note: '未知工具', answer: '這個工具我還不會。' }
  }
}

// 歸檔到 manifest（去重）
function archive(c, key, item) {
  const list = c[key]
  if (!list.some((x) => x.id === item.id || x.name === item.name)) list.push(item)
}

// 關聯圖（契約 §3.7 形狀；由現有 GNODES/GEDGES 轉）
function buildGraph() {
  const relMap = { support: 'cite', relate: 'address', conflict: 'cite' }
  const nodes = GNODES.map((n) => ({
    id: n.id.toUpperCase(),
    k: n.k,
    c: n.c,
    t: n.t,
    d: n.d || '',
    s: n.s || '',
    src: n.src || '',
    origin: { doc: 'record', fact: 'record', issue: 'rule', law: 'retrieval', out: 'llm' }[n.k] || 'llm',
  }))
  const edges = GEDGES.map(([from, to, rel]) => ({
    from: from.toUpperCase(),
    to: to.toUpperCase(),
    rel: relMap[rel] || 'cite',
    basis: 'citations[].sentence_id→resolved_id',
    ...(rel === 'conflict' ? { state: 'out_of_scope', lamp: 'y' } : { state: 'ok', lamp: 'g' }),
  }))
  return {
    run_id: nid('run'),
    generated: now(),
    cols: GCOLS,
    nodes,
    edges,
    unlinked: { laws: [], issues: [], note: '' },
    stats: { nodes: nodes.length, edges: edges.length, edges_flagged: edges.filter((e) => e.lamp !== 'g').length },
  }
}

// mock 潤稿：對傳入文字做幾處口語→公文體改寫，讓前端 diff 有可見差異。
// real 後端會回真正改寫後的文字，這裡只是產一個形狀對的替身。
function mockRewrite(text, instr) {
  if (!text) {
    return `依「${instr}」修潤如下：本件訴願人所訴各節，經核與卷內事證未合，難認有理由。`
  }
  let out = text
  // 這些詞在示範草稿裡實際會出現，換掉才看得到「刪除＋新增」的對照效果。
  const subs = [
    [/查得/g, '查獲'],
    [/固非無據/g, '固有所本'],
    [/尚有未足/g, '仍嫌不足'],
    [/爰將/g, '爰予'],
    [/難謂無瑕疵/g, '難認為適法'],
    [/看不到|沒看到/g, '查無'],
    [/沒有/g, '並無'],
    [/所以/g, '爰'],
    [/有問題/g, '尚有瑕疵'],
  ]
  for (const [re, rep] of subs) out = out.replace(re, rep)
  // 若完全沒命中，補一句收尾，確保 diff 一定看得到差異
  if (out === text) out = text.replace(/。\s*$/, '') + '，核與相關規定尚無不合。'
  return out
}

function fallbackAnswer(msg) {
  if (/你會什麼|會做什麼|有哪些工具|能做什麼|功能|help|工具清單/i.test(msg || ''))
    return '我能：解析卷證、查相似案例、查相關法規、產生關聯圖、生成決定書草稿、潤稿。按「/」可看工具清單。'
  return '這句我沒對到工具。可以按「/」查看可呼叫的工具，或輸入「你會什麼」讓我列出全部。'
}

// ── REST 端點（回應形狀照契約 §4）──
async function rest(fn) {
  await sleep(MOCK_TIMING.rest)
  return fn()
}

function caseSummary(c) {
  return { id: c.id, name: c.name, created_at: c.created_at, latest_run_id: c.latest_run_id }
}

export const mock = {
  // §1.1
  health: () => rest(() => ({ status: 'ok', run_mode: 'mock', provenance: { kind: 'mock', run_mode: 'mock' } })),
  listCases: () => rest(() => ({ cases: [...DB.cases.values()].map(caseSummary) })),
  createCase: (formData) =>
    rest(() => {
      const files = extractFiles(formData)
      const id = nid('upload')
      const c = ensureCase(id, deriveName(files))
      files.forEach((f) => c.files.push(f))
      // 對齊真後端 POST /api/cases 形狀：{case_id, files, provenance, next}
      return { case_id: id, files: c.files, provenance: { kind: 'mock' }, next: `/api/cases/${id}/runs` }
    }),
  getCase: (id) =>
    rest(() => {
      const c = ensureCase(id)
      return { case: caseSummary(c), files: c.files, laws: c.laws, references: c.references, artifacts: stripArtifacts(c.artifacts) }
    }),
  renameCase: (id, name) => rest(() => { const c = ensureCase(id); c.name = name; return { id, name: c.name, created_at: c.created_at } }),
  deleteCase: (id) => rest(() => { DB.cases.delete(id); return null }),

  // §1.2 卷證
  listFiles: (id) => rest(() => ({ files: ensureCase(id).files })),
  uploadFiles: (id, formData) =>
    rest(() => {
      const c = ensureCase(id)
      const files = extractFiles(formData)
      files.forEach((f) => archive(c, 'files', f))
      return { files }
    }),
  deleteFile: (id, fileId) => rest(() => { const c = ensureCase(id); c.files = c.files.filter((f) => f.id !== fileId); return null }),

  // §1.3 法規
  searchLaws: (q) => rest(() => ({ results: filterLib(LAW_LIBRARY, q).map(libLawResult) })),
  getLaw: (lawId) =>
    rest(() => {
      const l = LAW_LIBRARY.find((x) => x.id === lawId) || {}
      return { id: lawId, t: l.t || '', src: l.src || '', body: l.body || '', verified: false, relevance: 'unknown' }
    }),
  listCaseLaws: (id) => rest(() => ({ laws: ensureCase(id).laws })),
  addCaseLaws: (id, lawIds) =>
    rest(() => {
      const c = ensureCase(id)
      lawIds.forEach((lid) => {
        const l = LAW_LIBRARY.find((x) => x.id === lid)
        if (l) archive(c, 'laws', { id: l.id, t: l.t, src: l.src, note: l.note, verified: false, relevance: 'unknown', body_cached: l.body })
      })
      return { laws: c.laws }
    }),
  removeCaseLaw: (id, lawId) => rest(() => { const c = ensureCase(id); c.laws = c.laws.filter((l) => l.id !== lawId); return null }),

  // §1.4 案例
  searchDecisions: (q) => rest(() => ({ results: filterLib(DECISION_LIBRARY, q).map(libDecisionResult) })),
  getDecision: (decisionId) =>
    rest(() => {
      const d = DECISION_LIBRARY.find((x) => x.id === decisionId) || {}
      return { id: decisionId, t: d.t || '', src: d.src || '', verdict: d.verdict || null, category: d.category || null, full: d.full || '' }
    }),
  listReferences: (id) => rest(() => ({ references: ensureCase(id).references })),
  addReferences: (id, decisionIds) =>
    rest(() => {
      const c = ensureCase(id)
      decisionIds.forEach((did) => {
        const d = DECISION_LIBRARY.find((x) => x.id === did)
        if (d)
          archive(c, 'references', {
            id: d.id,
            t: d.t,
            src: d.src,
            note: `${d.category}．${d.verdict}．向量相似度 ${Math.round(d.score * 100)}%`,
            score: d.score,
            provenance: d.provenance,
            doc_kind: 'decision',
            full_cached: d.full,
          })
      })
      return { references: c.references }
    }),
  removeReference: (id, refId) => rest(() => { const c = ensureCase(id); c.references = c.references.filter((r) => r.id !== refId); return null }),

  // §1.5 產出
  listArtifacts: (id) => rest(() => ({ artifacts: stripArtifacts(ensureCase(id).artifacts) })),
  getArtifact: (id, artifactId) =>
    rest(() => {
      const c = ensureCase(id)
      const a = c.artifacts.find((x) => x.id === artifactId)
      if (!a) return { artifact_id: artifactId, title: '', sections: [], cite_count: 0 }
      if (a._graph) return { artifact_id: a.id, title: a.name, run_id: a.run_id, graph: a._graph }
      return { artifact_id: a.id, title: a.name, run_id: a.run_id, html: a._html || '', cite_count: (a._html?.match(/class="cite"/g) || []).length }
    }),
  deleteArtifact: (id, artifactId) => rest(() => { const c = ensureCase(id); c.artifacts = c.artifacts.filter((a) => a.id !== artifactId); return null }),
  exportArtifact: (id, artifactId, format = 'docx') =>
    rest(() => {
      // mock 不產真檔，回一個提示用的 text blob（store 收到就 toast，不觸發真下載）
      const c = ensureCase(id)
      const a = c.artifacts.find((x) => x.id === artifactId)
      const name = (a?.name || '訴願決定書草稿').replace(/\s+/g, '_')
      return { blob: new Blob([`（mock 匯出）${name}`], { type: 'text/plain' }), filename: `${name}.${format}`, _mock: true }
    }),

  // §1.6 chat（SSE async generator）
  chat: (id, payload) => chatStream(id, payload),
}

// ── helpers ──
function extractFiles(formData) {
  // FormData 或 [{name,ext,note}] 都接受
  if (formData && typeof formData.getAll === 'function') {
    return formData.getAll('files').map((f) => ({ id: nid('file'), name: f.name || String(f), ext: extOf(f.name), note: '', readable: true }))
  }
  if (Array.isArray(formData)) return formData.map((f) => ({ id: nid('file'), name: f.name, ext: f.ext || extOf(f.name), note: f.note || '', readable: true }))
  return []
}
function extOf(name = '') { const m = /\.([a-z0-9]+)$/i.exec(name); return m ? m[1].toLowerCase() : '' }
function deriveName(files) { return files.length ? '吉○實業／違反廢清法' : '新案件' }
function filterLib(lib, q) {
  if (!q) return lib
  const s = q.toLowerCase()
  return lib.filter((x) => (x.t + (x.body || '') + (x.full || '') + (x.note || '')).toLowerCase().includes(s))
}
function libLawResult(l) { return { id: l.id, t: l.t, src: l.src, score: l.score, doc_kind: 'statute' } }
function libDecisionResult(d) { return { id: d.id, t: d.t, src: d.src, score: d.score, verdict: d.verdict, category: d.category, provenance: d.provenance, doc_kind: 'decision' } }
// 對外的 artifact 清單不外洩內部 _html/_graph
function stripArtifacts(list) { return list.map(({ _html, _graph, ...a }) => a) }
