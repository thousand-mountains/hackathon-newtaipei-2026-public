// 後端 API 層。所有 fetch 都在這裡，元件與 store 不直接碰網路。
//
// **本檔最重要的一件事：把「連不上後端」與「連上了但這次跑失敗」分成兩種錯誤。**
// 舊前端（prototype/static/app.js:182-211）把 health 探測與 postRun 放在同一個 try、
// 共用同一個 catch，於是任何執行失敗都退回內嵌 fixture，徽章還寫死「離線 fixture（未接後端）」——
// 後端明明活著，只是這次跑失敗了。那是分層誠實的直球違規。
//
// 這裡用 `kind` 把兩者從型別上分開，呼叫端沒辦法把它們混在一起處理。

/** 連不上後端：網路層失敗、CORS、服務沒起來。**這種才可以說「未連上後端」。** */
export const OFFLINE = 'offline'
/** 連上了，但後端回了錯誤狀態碼。**這種一律不得說成離線。** */
export const BACKEND_ERROR = 'backend_error'
/** 連上了、後端也跑了，但這次執行在某個節點失敗（502）。錯誤原文由後端給。 */
export const RUN_FAILED = 'run_failed'

export class ApiError extends Error {
  constructor(kind, message, extra = {}) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    Object.assign(this, extra)
  }
  /** 畫面要不要說「未連上後端」——只有這個為 true 時才可以。 */
  get isOffline() {
    return this.kind === OFFLINE
  }
}

async function request(path, { method = 'GET', body, headers, signal } = {}) {
  let res
  try {
    res = await fetch(path, {
      method,
      headers: { Accept: 'application/json', ...(headers || {}) },
      body,
      signal,
    })
  } catch (e) {
    // fetch 本身 reject ＝ 根本沒連上（或被中止）。這是唯一能稱為「離線」的情況。
    if (e?.name === 'AbortError') throw e
    throw new ApiError(OFFLINE, `無法連上後端：${e?.message || e}`)
  }
  return res
}

/** 把後端的錯誤回應翻成可讀訊息。**優先用後端自己說的話，不要自己編一句。** */
async function detailOf(res) {
  let payload = null
  try {
    payload = await res.json()
  } catch {
    return `HTTP ${res.status}`
  }
  const d = payload?.detail
  if (typeof d === 'string') return d
  if (d) return JSON.stringify(d)
  if (payload?.error) return payload.error
  return `HTTP ${res.status}`
}

async function json(path, opts) {
  const res = await request(path, opts)
  if (!res.ok) {
    throw new ApiError(BACKEND_ERROR, await detailOf(res), { status: res.status })
  }
  return res.json()
}

// ── 端點 ────────────────────────────────────────────────────────────

/** `/api/health` 也可能回 503（檢查沒過）——那不是離線，要照實把 checks 帶出來。 */
export async function getHealth() {
  const res = await request('/api/health')
  let body = null
  try {
    body = await res.json()
  } catch {
    throw new ApiError(BACKEND_ERROR, `健康檢查回應無法解析（HTTP ${res.status}）`, { status: res.status })
  }
  return { ok: res.ok && body?.ok === true, status: res.status, body }
}

export function listCases() {
  return json('/api/cases')
}

/** 上傳卷證建案（multipart）。回 `{case_id, files, provenance, next}`。 */
export function createCase(fileList) {
  const fd = new FormData()
  for (const f of fileList) fd.append('files', f, f.name)
  return json('/api/cases', { method: 'POST', body: fd })
}

/**
 * 啟動一次執行。
 *
 * **live 檔位回 202 不是 200**（`backend/api/app.py` 的 `create_run`），
 * 所以這裡兩種都要吃：200 ＝ fixture 檔位同步跑完，直接就是 payload；
 * 202 ＝ 背景執行，回 `{run_id, result_url, events_url}`，要輪詢或接 SSE。
 */
export async function startRun(caseId, body = {}) {
  const res = await request(`/api/cases/${encodeURIComponent(caseId)}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (res.status === 200) return { done: true, payload: await res.json() }
  if (res.status === 202) return { done: false, ...(await res.json()) }
  throw new ApiError(BACKEND_ERROR, await detailOf(res), { status: res.status })
}

/**
 * 輪詢一次執行結果。三種狀態要分得清楚：
 * - 409 → 還在跑（**不是錯誤**，後端刻意用 409 而不是 200＋狀態欄位，
 *   就是為了讓前端沒辦法把「還沒跑完」誤讀成一份空的分析結果）
 * - 502 → 這次執行失敗，帶失敗節點與原始錯誤字串
 * - 200 → 完整 payload
 */
export async function pollRun(runId, { signal } = {}) {
  const res = await request(`/api/runs/${encodeURIComponent(runId)}`, { signal })
  if (res.status === 409) return { status: 'running' }
  if (res.status === 502) {
    const b = await res.json().catch(() => ({}))
    throw new ApiError(RUN_FAILED, b?.error || '執行失敗（後端未提供原因）', {
      status: 502,
      node: b?.node ?? null,
    })
  }
  if (!res.ok) throw new ApiError(BACKEND_ERROR, await detailOf(res), { status: res.status })
  return { status: 'done', payload: await res.json() }
}

/**
 * 送出審議。**這是唯一有意義的守門點**——後端會重跑六節點再判斷，
 * 完全不採信前端送來的 `submit_allowed`（`backend/api/app.py` 的 `submit_case`）。
 *
 * 409 不是例外情況，是**正常且重要的回應**：它帶著完整 blockers。
 * 所以這裡不丟錯，回 `{accepted, body}` 讓呼叫端顯示理由。
 */
export async function submitCase(caseId, body = {}) {
  const res = await request(`/api/cases/${encodeURIComponent(caseId)}/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (res.status === 200 || res.status === 409) {
    const b = await res.json()
    return { accepted: res.status === 200, body: b }
  }
  throw new ApiError(BACKEND_ERROR, await detailOf(res), { status: res.status })
}
