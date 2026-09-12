// 畫面上那個百分比是**誰算出來的**——這個檔是全前端唯一一份答案。
//
// 為什麼要有這個檔：後端開了重排之後，`score` 會被換成 cross-encoder 的相關性分數
// （`backend/retrieval/kb.py:546` 設 `payload["ranked_by"]="rerank"`）。重排分數與
// embedding 向量距離**都是 0–100、長得一模一樣，意思完全不同**。畫面寫死「向量相似度」
// 在開了重排之後就是不實陳述，而它就在 demo 主畫面上（CONSTITUTION §1）。
//
// **文案只能寫在這裡。** `backend/tests/test_live_plumbing.py` 的
// `test_frontend_similarity_caption_follows_the_ranker` 會掃 `frontend/src/` 底下每一個檔，
// 只要在這個檔以外看到這幾句文案就判失敗——兩處各寫一份，漂移只是時間問題，
// 而這一份字串是「對承辦人解釋畫面上那個數字是什麼」，漂了就是說謊。

/** `ranked_by` 正規化後的值域。**只認精確值**——冒出沒看過的排序器時落到 `unknown`，
 *  而不是猜它是 embedding。猜錯的代價是替一個不知道怎麼來的數字背書。 */
export const RANKER = {
  RERANK: 'rerank',
  EMBEDDING: 'embedding',
  LAWTABLE: 'lawtable', // `ranked_by === null`：法條查表，score 固定 1.0
  UNKNOWN: 'unknown', // 欄位不存在（後端還沒帶出來）
}

/** 每一種排序器的說法。`caption` 接在數字後面，`note` 是卡片下方的說明。 */
export const RANKER_CAPTION = {
  [RANKER.RERANK]: {
    caption: '重排模型判定',
    note: '這個分數由重排模型（cross-encoder）判定語意相關性，不是法律相似度，也未對資料集實檔驗證。',
  },
  [RANKER.EMBEDDING]: {
    caption: '向量比對',
    note: '這個分數是向量比對（embedding 距離），不是法律相似度，也未對資料集實檔驗證。',
  },
  [RANKER.LAWTABLE]: {
    caption: '查表命中',
    // 查表是二元的（條號在不在快照裡），score 固定 1.0。把它畫成 100% 會被讀成
    // 「完全相似」，那是這張卡上最容易產生的誤解，所以這一種**不顯示百分比**。
    note: '法條查表是二元命中（條號在不在快照裡），沒有相似度可言。',
  },
  [RANKER.UNKNOWN]: {
    caption: '未標明排序來源',
    note: '這次回傳沒有標明這個分數由哪一個排序器產生，因此它不能當成法律相似度，也不能當成向量相似度採用。',
  },
}

/** 一筆 hit → 正規化的排序器種類。 */
export function rankerOf(hit) {
  if (!hit || !('ranked_by' in hit)) return RANKER.UNKNOWN
  const v = hit.ranked_by
  if (v === null) return RANKER.LAWTABLE
  if (v === RANKER.RERANK) return RANKER.RERANK
  if (v === RANKER.EMBEDDING) return RANKER.EMBEDDING
  return RANKER.UNKNOWN
}

/** 這一種排序器該不該顯示百分比。查表那種不顯示（見上）。 */
export function showsPercent(kind) {
  return kind !== RANKER.LAWTABLE
}

/** 數字後面那一行，例：「重排模型判定 79%」。拿不到分數就只回說法。 */
export function scoreCaption(hit) {
  const kind = rankerOf(hit)
  const { caption } = RANKER_CAPTION[kind]
  if (!showsPercent(kind) || typeof (hit && hit.score) !== 'number') return caption
  return `${caption} ${Math.round(hit.score * 100)}%`
}

/** 卡片下方的說明。 */
export function scoreNote(hit) {
  return RANKER_CAPTION[rankerOf(hit)].note
}

/** 母庫查（`GET /api/laws`、`GET /api/decisions`）用這個。
 *
 * **那是另一條通道，不經重排**：`backend/retrieval/kb.py` 的 `search_corpus`
 * 只做三件事（server-side `doc_kind` filter、來源去重、截 limit），
 * 與 `KBRetriever._retrieve`（有前綴白名單、兩批配額、**重排**、exclude_case）是兩支。
 * 所以那邊的 `score` 確實是向量距離，沒有 `ranked_by` 欄位也不需要有。
 * 這個函式讓那條路徑照樣**從這份唯一的文案表取字**，不要自己寫一句。
 */
export function corpusScoreCaption(score) {
  const { caption } = RANKER_CAPTION[RANKER.EMBEDDING]
  return typeof score === 'number' ? `${caption} ${Math.round(score * 100)}%` : caption
}
