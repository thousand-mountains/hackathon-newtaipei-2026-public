// 後端 payload → 畫面用的 view model。
//
// **這一層的職責是「照實搬」，不是「補完」。** 後端沒給的欄位一律留空或標成未知，
// 不由前端推測、不拿 mock 補位。前端補一個看起來合理的值，就是在畫面上製造一個
// 系統其實不知道的事實。
//
// 三個誠實維度在這裡被保留成結構，而不是被壓成一句話：
//   verify    —— 字號能不能對回法規快照（後端 lamp / tag 給的）
//   relevance —— 這筆依據跟本案相不相關（**後端目前無法保證，一律 unknown**）
//   origin    —— 這句話是誰產出的（卷證直錄 / 規則引擎 / 模型）

/** 判解／函釋引用的相關性目前沒有任何一層保證得了——見 §F-1a。 */
export const RELEVANCE_UNKNOWN = {
  known: false,
  label: '相關性未經驗證',
  why:
    '綠燈只代表這個字號能對回法規快照（引用可驗），**不代表系統確認它與本案相關**。' +
    '守門節點查核的是字號存在，不查核相關性。請自行核對內容是否切合本案爭點。',
}

const lampClass = (l) => ({ g: 'ok', y: 'warn', r: 'bad' })[l] || 'none'

// ── 左欄①：案情與爭點 ──────────────────────────────────────────
export function toFactCards(payload) {
  const cards = []
  const intake = payload?.intake || {}
  const excerpts = payload?.facts_excerpt || []

  if (excerpts.length) {
    cards.push({
      id: 'X1',
      k: '事實經過',
      tag: '卷證直錄',
      d: excerpts.map((e) => e.text).join('\n'),
      src: excerpts.map((e) => e.quote_ref).filter(Boolean).join('　·　') || '卷證',
      origin: 'record',
    })
  }
  if (intake.note) {
    cards.push({
      id: 'X2',
      k: '訴願主張',
      tag: '模型抽取',
      d: intake.note,
      src: '由 N1 自卷證抽取——**尚未經承辦人確認**',
      origin: 'llm',
    })
  }

  // 爭點：後端 issues[] 固定紅燈（事實認定不由系統代為判斷）
  for (const i of payload?.issues || []) {
    cards.push({
      id: i.id,
      k: i.t,
      tag: i.tag || '需人工認定',
      d: i.q || '',
      src: i.src || '',
      lamp: i.lamp,
      lampClass: lampClass(i.lamp),
      origin: i.origin,
    })
  }
  return cards
}

// ── 左欄②：相關法條 ────────────────────────────────────────────
export function toLawCards(payload) {
  return (payload?.laws || []).map((l) => ({
    id: l.id,
    law: l.law || '',
    art: l.article ? `第 ${l.article} 條` : l.t || '',
    title: l.t,
    // 後端的 tag 是「✓ 在庫 / 庫外，未驗證」——那是**字號可驗性**，不是相關性
    verify: {
      lamp: l.lamp,
      lampClass: lampClass(l.lamp),
      label: l.tag || '未標示',
      why: l.note || l.gate_note || '',
      verified: l.verified === true,
    },
    relevance: RELEVANCE_UNKNOWN,
    // 快照只索引條號、不含條文原文；後端明講不代為補寫，前端也不補
    keyPoint: l.q || l.q_note || '',
    hasText: Boolean(l.q),
    src: l.src || '',
    gateStatus: l.gate_status || null,
    mine: false,
    users: 0,
  }))
}

// ── 左欄③：相似案例 ────────────────────────────────────────────
export function toCaseCards(payload) {
  return (payload?.cases || []).map((c) => ({
    id: c.id,
    t: c.t,
    // sim 不是法律上的相似度——文案要說清楚。而且**它是誰算的會變**：
    // 開了重排就是 cross-encoder 判的語意相關性，沒開才是 embedding 的向量距離。
    // 兩者都是 0–100 的數字，看起來一樣，意思不一樣；後端用 `ranked_by` 說明是哪一種，
    // 這裡照著換文案。寫死「向量比對」會在開了重排之後變成不實陳述（CONSTITUTION §1）。
    sim: typeof c.sim === 'number' ? c.sim : null,
    rankedBy: c.ranked_by === 'rerank' ? 'rerank' : 'embedding',
    simLabel:
      c.ranked_by === 'rerank'
        ? '語意相關性（重排模型判定，非法律見解相似度）'
        : '檢索相似度（向量比對，非法律見解相似度）',
    outcome: c.outcome || null,
    // provenance 兩批來源必須分得出來
    provenance: c.provenance || 'unknown',
    provenanceLabel:
      { official: '賽方資料集', public_crawl: '市府公開全量爬蟲' }[c.provenance] || '來源未標示',
    src: c.src || '',
    d: c.d || c.text || '',
    // 後端明說 KB 命中未對資料集實檔驗證
    verify: { verified: c.verified === true, why: c.note || '' },
    relevance: RELEVANCE_UNKNOWN,
    users: 0,
  }))
}

// ── 右欄：決定書草稿 ───────────────────────────────────────────
const ORIGIN_LABEL = {
  record: '卷證直錄',
  rule: '規則引擎',
  llm: '模型生成',
  static: '固定文字',
  human: '承辦人',
}

export function toDoc(payload) {
  return (payload?.doc || []).map((b) => ({
    ty: b.ty,
    text: b.text || '',
    ind: b.ind,
    ss: (b.ss || []).map((s) => ({
      id: s.id,
      t: s.t,
      orig: s.t,
      ver: 0,
      refs: s.refs || [],
      slot: s.slot,
      basis: s.basis || '',
      why: s.why || '',
      origin: s.origin,
      originLabel: ORIGIN_LABEL[s.origin] || s.origin || '未標示',
      lamp: s.l,
      lampClass: lampClass(s.l),
      placeholder: s.placeholder === true,
    })),
  }))
}

// ── 程序審查（規則引擎輸出，前端只負責畫，不重算） ──────────────
export function toProcedure(payload) {
  const screen = payload?.screen || {}
  const dl = screen.deadline || {}
  const art77 = screen.art77 || {}
  return {
    deadline: dl.deadline || null,
    effectiveDate: dl.effective_date || null,
    overdue: dl.overdue,
    steps: dl.steps || [],
    caveats: dl.caveats || [],
    clause: art77.clause || null,
    clauseBasis: art77.basis || '',
    notAutoScreened: art77.not_auto_screened || [],
    notAutoScreenedReason: art77.not_auto_screened_reason || '',
    requiresHumanConclusion: screen.requires_human_conclusion === true,
    humanSignals: screen.human_conclusion_signals || [],
    inputsConfirmed: screen.procedural_inputs_confirmed === true,
    unconfirmedFields: screen.unconfirmed_procedural_fields || [],

    // 程序審查結論（2026-09-12 新增）。**`by` 由後端給，前端不自己推。**
    // `value` 只可能是 "A" 或 null——訴願有無理由屬實體法律判斷，後端不代為認定。
    conclusion: screen.procedure_conclusion || null,

    // 第二意見交叉比對。三個陣列的語意不同，**畫面上不得混為一談**：
    //   disagreement      真分歧 → 值得跳警告
    //   not_comparable    兩邊輸入不對等 → 是說明不是告警，**不得渲染成警示色**
    //   refusal_asymmetry 一方拒答、另一方有答案 → agree 為 null（未知），
    //                     **不得把有答案的那一方當成另一方的佐證**
    //
    // **`agree === true` 不等於「比對過且一致」。** 2026-09-12 對抗式稽核抓到：
    // 案件剛進來、承辦人還沒填提起日時，兩套引擎都沒判逾期，舊版折算成
    // `agree=true` → 畫面亮綠燈說「兩套引擎一致」，而一次比對都沒做。
    // 那是這個系統**最常見的畫面**上的假綠燈。
    // 所以要讀 `compared`（實際比對過哪幾維）才能決定 badge，見 ProcPanel。
    crossCheck: screen.deadline_cross_check
      ? {
          agree: screen.deadline_cross_check.agree,
          compared: screen.deadline_cross_check.compared || [],
          disagreement: screen.deadline_cross_check.disagreement || [],
          notComparable: screen.deadline_cross_check.not_comparable || [],
          refusalAsymmetry: screen.deadline_cross_check.refusal_asymmetry || [],
          // **共用盲點**：兩套引擎用同一份錯誤知識算出同一個答案。
          // 它跟 disagreement 的風險方向相反——分歧會讓人警覺，共用盲點
          // **長得最像沒事**（compared 滿的、agree 為 true 過的那種），
          // 所以畫面上要比分歧更醒目。
          sharedBlindSpot: screen.deadline_cross_check.shared_blind_spot || [],
          note: screen.deadline_cross_check.note || '',
          // 第二意見的逐步算式與法條依據——**可驗算層的實質內容**。
          // 只給一個 true/false 而不給算式，正是「形式具備、實質不具備」。
          engines: screen.deadline_cross_check.engines || {},
        }
      : null,
  }
}

// ── 三個誠實維度（來自 /api/health 或 payload 的 provenance） ────
export function toProvenance(prov) {
  if (!prov) return null
  return {
    execution: prov.execution_note || '',
    data: prov.data_note || '',
    retrieval: prov.retrieval_note || '',
    banner: prov.banner || '',
    kind: prov.kind || '',
    runMode: prov.run_mode || '',
    retriever: prov.retriever || '',
  }
}

/** 整包映射。**不接受半份 payload**——缺 doc 就是沒有草稿，不用 mock 頂替。 */
export function adaptPayload(payload) {
  return {
    caseId: payload?.case_id || '',
    runId: payload?.run_id || '',
    state: payload?.state || '',
    intake: payload?.intake || {},
    intakeConfirmed: payload?.intake_confirmed || [],
    facts: toFactCards(payload),
    laws: toLawCards(payload),
    cases: toCaseCards(payload),
    doc: toDoc(payload),
    procedure: toProcedure(payload),
    provenance: toProvenance(payload?.provenance),
    blockers: payload?.blockers || [],
    submitAllowed: payload?.submit_allowed === true,
    lampStats: payload?.lamp_stats || {},
    citationCounts: payload?.citation_counts || {},
    handoff: payload?.handoff || {},
    runMeta: payload?.run_meta || {},
    tiers: payload?.tiers || {},
  }
}
