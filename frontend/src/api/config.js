// API 執行模式與端點基底。
//
// 切換真假後端只改這裡（或 .env）：
//   VITE_API_MODE = 'mock'（預設）｜'real'
//   VITE_API_BASE = REST／SSE 的路徑前綴，預設 '/api'（vite dev proxy 已代理到後端）
//
// mock 模式：完全在前端跑，回應形狀「照契約 v2」做（docs/handoff/2026-09-12-frontend-contract-v2.md）。
// real 模式：直接打後端 24 支端點與 SSE chat。store 兩邊共用同一組函式簽名，故切換零改碼。

const env = import.meta.env || {}

export const API_MODE = (env.VITE_API_MODE || 'mock').toLowerCase() === 'real' ? 'real' : 'mock'
export const API_BASE = (env.VITE_API_BASE || '/api').replace(/\/$/, '')

export const IS_MOCK = API_MODE === 'mock'

// mock 模式下模擬網路／運算耗時（毫秒）。real 模式忽略。
// 依契約 §1.6 實測：解析卷證 ~10–11s、生成草稿 ~24–72s、純問答 5–15s；
// demo 用不需要真的等那麼久，等比壓縮成「看得出有在跑」即可。
export const MOCK_TIMING = {
  ack: 220,
  step: 520, // 每個 tool_step 之間
  toolResult: 300,
  tokenChunk: 26, // 逐字 append 間隔
  rest: 180, // 一般 REST 往返
  upload: 420,
}
