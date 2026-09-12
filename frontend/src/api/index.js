// 統一 API 門面：依 VITE_API_MODE 選 mock 或 real。
//
// mock 與 real 兩邊「函式簽名完全一致」，所以 store 只認識這個 `api`，
// 切換後端只需在 .env 設 VITE_API_MODE=real（見 src/api/config.js），store 一行都不用改。
//
// 24 支端點 + 1 支 SSE chat，對照契約 v2 §1／§2：
//   health, listCases, createCase, getCase, renameCase, deleteCase
//   listFiles, uploadFiles, deleteFile
//   searchLaws, getLaw, listCaseLaws, addCaseLaws, removeCaseLaw
//   searchDecisions, getDecision, listReferences, addReferences, removeReference
//   listArtifacts, getArtifact, deleteArtifact, exportArtifact
//   chat（回 async generator，逐一 yield {event,data}）
import { API_MODE, IS_MOCK } from './config.js'
import { mock } from './mock.js'
import { real } from './real.js'

export const api = IS_MOCK ? mock : real
export { API_MODE, IS_MOCK }
