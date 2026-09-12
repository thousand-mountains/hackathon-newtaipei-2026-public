// REST + SSE 傳輸層（real 模式用）。
// mock 模式不會走到這裡；但簽名與回傳形狀刻意跟 mock backend 對齊。
import { API_BASE } from './config.js'

/** 契約 §2.2：開串流前的 JSON 錯誤（400/404/503）攤平成一個 Error 物件。 */
export class ApiError extends Error {
  constructor(status, body) {
    super((body && body.detail) || `HTTP ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.body = body || {}
  }
}

async function parse(res) {
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) return res.json()
  return res.text()
}

async function request(method, path, { json, form, signal } = {}) {
  const opts = { method, headers: {}, signal }
  if (json !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(json)
  } else if (form !== undefined) {
    opts.body = form // FormData，瀏覽器自動帶 multipart boundary
  }
  const res = await fetch(API_BASE + path, opts)
  if (!res.ok) {
    let body = null
    try {
      body = await res.json()
    } catch {
      /* 非 JSON 錯誤 */
    }
    throw new ApiError(res.status, body)
  }
  if (res.status === 204) return null
  return parse(res)
}

export const http = {
  get: (path, opts) => request('GET', path, opts),
  post: (path, opts) => request('POST', path, opts),
  patch: (path, opts) => request('PATCH', path, opts),
  del: (path, opts) => request('DELETE', path, opts),
}

/**
 * 下載類（匯出）：回 Blob、檔名，以及契約的三個 X-* 標頭。
 * - X-Unresolved-Cites：數字，非 0 代表有引用對不上，前端要警示。
 * - X-Export-Warning：中文說明，HTTP 標頭只吃 latin-1，後端做過百分比編碼，要 decode。
 * - X-Cite-Count：引用總數。
 */
export async function download(path) {
  const res = await fetch(API_BASE + path)
  if (!res.ok) throw new ApiError(res.status, null)
  const cd = res.headers.get('content-disposition') || ''
  // **先看 `filename*`（RFC 5987，帶中文檔名），再退回 ASCII 的 `filename`。**
  // 後端兩個都送，而 ASCII 那個是 `draft.pdf`——照原本的寫法會先命中它，
  // 承辦人存下來的檔案全都叫 draft.pdf，案號與檔名都沒了。
  const star = /filename\*=\s*UTF-8''([^;\r\n]+)/i.exec(cd)
  const plain = /filename=\s*["']?([^"';\r\n]+)/i.exec(cd)
  let filename = 'download'
  if (star) {
    try {
      filename = decodeURIComponent(star[1])
    } catch {
      filename = star[1]
    }
  } else if (plain) {
    filename = plain[1]
  }
  const unresolved = parseInt(res.headers.get('x-unresolved-cites') || '0', 10) || 0
  const citeCount = parseInt(res.headers.get('x-cite-count') || '0', 10) || 0
  let warning = res.headers.get('x-export-warning') || ''
  if (warning) {
    try {
      warning = decodeURIComponent(warning)
    } catch {
      /* 已是純文字或解碼失敗，原樣用 */
    }
  }
  const blob = await res.blob()
  return { blob, filename, unresolved, citeCount, warning }
}

/**
 * SSE 讀取器：把 `event:`／`data:` 幀解析成 {event,data} 物件的 async generator。
 * 契約 §2.3 wire 格式：`event: <名稱>\ndata: <一行 JSON>\n\n`。
 * 用 fetch + ReadableStream（不用 EventSource，因為要 POST body）。
 */
export async function* sse(path, { json, signal } = {}) {
  const res = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(json || {}),
    signal,
  })
  if (!res.ok) {
    let body = null
    try {
      body = await res.json()
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, body)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) {
      // 收尾（flush）：把 decoder 內殘存的位元組吐完，並解析 buffer 裡最後一幀。
      // 這是「話講一半被 cut」的根因——最後的 token／done 幀常常沒有結尾空行，
      // 若不在這裡補解析就會被整個丟掉。
      buf += decoder.decode()
      buf = buf.replace(/\r\n/g, '\n') // 容忍 CRLF（部分代理／伺服器會送）
      for (const frame of buf.split('\n\n')) {
        const evt = parseFrame(frame)
        if (evt) yield evt
      }
      break
    }
    buf += decoder.decode(value, { stream: true })
    buf = buf.replace(/\r\n/g, '\n')
    let idx
    // 一幀以空行（\n\n）分隔
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const evt = parseFrame(frame)
      if (evt) yield evt
    }
  }
}

function parseFrame(frame) {
  let event = 'message'
  const dataLines = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return null
  let data = dataLines.join('\n')
  try {
    data = JSON.parse(data)
  } catch {
    /* 保留字串 */
  }
  return { event, data }
}
