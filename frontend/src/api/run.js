// 一次執行的完整生命週期：202 → 逐節點進度 → 取結果。
//
// **SSE 是加值層，不是取結果的路徑。** 後端說得很明白：事件只活在那個 process 的
// 記憶體裡，沒有持久化也沒有回放，process 重啟事件就沒了，但結果還在 runstore。
// 所以這裡的規則是：
//   1. 事件流只用來更新進度文字
//   2. 收到 run_done 仍然要去 GET result_url 拿 payload
//   3. **事件流斷掉一律退回輪詢，不得當成執行失敗**
//      ——事件沒了不代表執行沒了，把它當失敗就是前端自己編一個後端沒說的結論
//
// 輪詢逾時用 10 分鐘：ALB idle timeout 是 900 秒，客戶端先放棄才會走到我們自己的
// 錯誤訊息；反過來的話使用者看到的是 502 閘道錯誤，那句話不是我們寫的。

import { pollRun, startRun, ApiError, RUN_FAILED } from './client.js'

const POLL_INTERVAL_MS = 2000
const POLL_TIMEOUT_MS = 10 * 60 * 1000

/** 六節點的人話名稱，只用於進度顯示。 */
export const NODE_LABELS = {
  n1: '讀卷證、抽取欄位',
  n2: '案件分類',
  n3: '程序審查與期間計算',
  n4: '檢索法條與相似案例',
  n5: '撰寫決定書草稿',
  n6: '逐句查核引用與燈號',
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * 接 SSE 更新進度。**任何失敗都只是靜靜關掉，不往外丟**——呼叫端的輪詢才是真相來源。
 * 回傳一個關閉函式。
 */
function attachEvents(eventsUrl, onProgress) {
  if (!eventsUrl || typeof EventSource === 'undefined') return () => {}
  let es
  try {
    es = new EventSource(eventsUrl)
  } catch {
    return () => {}
  }
  es.onmessage = (ev) => {
    let d
    try {
      d = JSON.parse(ev.data)
    } catch {
      return
    }
    const node = d?.node
    if (!node) return
    // 只回報進度文字；結果一律由輪詢取得
    onProgress?.({ node, label: NODE_LABELS[node] || node, raw: d })
  }
  // 斷線不處理：EventSource 自己會重連，重連不上也沒關係——輪詢照跑。
  es.onerror = () => {}
  return () => {
    try {
      es.close()
    } catch {
      /* 關不掉就算了，不影響取結果 */
    }
  }
}

/**
 * 跑一次完整執行，回傳 payload。
 *
 * @param {string} caseId
 * @param {object} opts
 * @param {object} opts.body          `POST /runs` 的 body（可帶 confirmed_intake）
 * @param {(p:{node:string,label:string})=>void} opts.onProgress 逐節點進度
 * @param {(s:string)=>void} opts.onPhase 粗粒度階段文字（沒有 SSE 時也會有）
 * @param {AbortSignal} opts.signal
 */
export async function executeRun(caseId, { body = {}, onProgress, onPhase, signal } = {}) {
  onPhase?.('送出執行請求…')
  const started = await startRun(caseId, body)

  // fixture 檔位同步跑完，直接就是 payload——沒有 run_id 可輪詢，也沒有事件流。
  if (started.done) return started.payload

  const { run_id: runId, events_url: eventsUrl } = started
  onPhase?.('後端已接受，執行中…')
  const detach = attachEvents(eventsUrl, onProgress)

  try {
    const deadline = Date.now() + POLL_TIMEOUT_MS
    for (;;) {
      if (Date.now() > deadline) {
        throw new ApiError(
          RUN_FAILED,
          `等待逾時（超過 ${POLL_TIMEOUT_MS / 60000} 分鐘）。執行可能仍在後端進行中，` +
            `結果可稍後以 run_id 取回：${runId}`,
          { runId },
        )
      }
      await sleep(POLL_INTERVAL_MS)
      if (signal?.aborted) throw new DOMException('已取消', 'AbortError')
      const r = await pollRun(runId, { signal })
      if (r.status === 'done') return r.payload
      // r.status === 'running' → 繼續等。409 不是錯誤。
    }
  } finally {
    detach()
  }
}
