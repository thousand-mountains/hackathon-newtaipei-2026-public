// 真後端綁定：契約 v2 的 24 支 REST 端點 + 1 支 SSE chat。
// 路徑、方法、body 形狀全部照 docs/handoff/2026-09-12-frontend-contract-v2.md §1。
// 與 mock.js 對齊同一組函式簽名，store 切換零改碼。
import { http, download, sse } from './http.js'

const enc = encodeURIComponent
// 母庫 id（law_id／decision_id／本案清單成員 id）是「完整 S3 key，含斜線」，
// 後端路由用 {…:path} converter，需要真正的斜線，不能整段 encodeURIComponent（會變 %2F 打不到）。
// 逐段編碼再用 / 接回：段內特殊字元照編，斜線保留。
const encPath = (s) => String(s).split('/').map(encodeURIComponent).join('/')

export const real = {
  // ── §1.1 系統與案件 ──
  health: () => http.get('/health'), // #1
  listCases: () => http.get('/cases'), // #2
  createCase: (formData) => http.post('/cases', { form: formData }), // #3 multipart files[]
  getCase: (id) => http.get(`/cases/${enc(id)}`), // #4 彙整版 {case,files,laws,references,artifacts}
  renameCase: (id, name) => http.patch(`/cases/${enc(id)}`, { json: { name } }), // #5
  deleteCase: (id) => http.del(`/cases/${enc(id)}`), // #6

  // ── §1.2 卷證檔案（本案專屬 C/R/D）──
  listFiles: (id) => http.get(`/cases/${enc(id)}/files`), // #7
  uploadFiles: (id, formData) => http.post(`/cases/${enc(id)}/files`, { form: formData }), // #8 multipart
  deleteFile: (id, fileId) => http.del(`/cases/${enc(id)}/files/${enc(fileId)}`), // #9

  // ── §1.3 相關法規（母庫唯讀 + 本案清單 C/R/D）──
  searchLaws: (q) => http.get(`/laws?q=${enc(q)}`), // #10 母庫查
  getLaw: (lawId) => http.get(`/laws/${encPath(lawId)}`), // #11 看全文（id 為含斜線的 S3 key）
  listCaseLaws: (id) => http.get(`/cases/${enc(id)}/laws`), // #12 本案清單
  addCaseLaws: (id, lawIds) => http.post(`/cases/${enc(id)}/laws`, { json: { law_ids: lawIds } }), // #13
  removeCaseLaw: (id, lawId) => http.del(`/cases/${enc(id)}/laws/${encPath(lawId)}`), // #14

  // ── §1.4 相似案例／參考決定（母庫唯讀 + 本案清單 C/R/D）──
  searchDecisions: (q) => http.get(`/decisions?q=${enc(q)}`), // #15 母庫查
  getDecision: (decisionId) => http.get(`/decisions/${encPath(decisionId)}`), // #16 看全文（含斜線 S3 key）
  listReferences: (id) => http.get(`/cases/${enc(id)}/references`), // #17 本案清單
  addReferences: (id, decisionIds) =>
    http.post(`/cases/${enc(id)}/references`, { json: { decision_ids: decisionIds } }), // #18
  removeReference: (id, refId) => http.del(`/cases/${enc(id)}/references/${encPath(refId)}`), // #19

  // ── §1.5 產出（草稿與匯出）──
  listArtifacts: (id) => http.get(`/cases/${enc(id)}/artifacts`), // #20
  getArtifact: (id, artifactId) => http.get(`/cases/${enc(id)}/artifacts/${enc(artifactId)}`), // #21
  deleteArtifact: (id, artifactId) => http.del(`/cases/${enc(id)}/artifacts/${enc(artifactId)}`), // #22
  exportArtifact: (id, artifactId, format = 'docx') =>
    download(`/cases/${enc(id)}/artifacts/${enc(artifactId)}/export?format=${enc(format)}`), // #23

  // ── §1.6 / §2 辦案對話（唯一串流）#24 ──
  // 回傳 async generator，逐一 yield {event,data}：
  //   ack / tool_call / tool_step / tool_result / token / done / error
  chat: (id, payload, { signal } = {}) =>
    sse(`/cases/${enc(id)}/chat`, { json: payload, signal }),

  // 斷線退化：契約 §5，?stream=0 一次回 done 攤平 + events[]。
  chatOnce: (id, payload) =>
    http.post(`/cases/${enc(id)}/chat?stream=0`, { json: payload }),
}
