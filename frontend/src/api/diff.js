// 前端文字 diff（契約 §3.4：後端沒有 diff，拿原文跟新文字在前端比）。
//
// 用途：優化文案時，把「上一版草稿文字」（存 localStorage）與「後端回傳的新版文字」
// 做逐詞比對，產生刪除／新增標記，畫成前後對照。
//
// 演算法：以中文為主的分詞（標點與空白切開，中文逐字），走 LCS，
// 產出 [{op:'keep'|'del'|'add', text}] 序列。輕量、無外部相依。

// 分詞：中文逐字、英數與標點成塊，保留空白。
function tokenize(s) {
  return String(s).match(/[\u4e00-\u9fff]|[A-Za-z0-9]+|\s+|[^\s\u4e00-\u9fff]/g) || []
}

// LCS 表（標準動態規劃）
function lcs(a, b) {
  const n = a.length
  const m = b.length
  const dp = Array.from({ length: n + 1 }, () => new Int32Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  return dp
}

/**
 * 比對 oldText → newText，回傳 op 序列。
 * op: 'keep'（兩版皆有）｜'del'（舊有新無）｜'add'（新有舊無）
 */
export function diffOps(oldText, newText) {
  const a = tokenize(oldText)
  const b = tokenize(newText)
  const dp = lcs(a, b)
  const ops = []
  let i = 0
  let j = 0
  const pushOp = (op, text) => {
    const last = ops[ops.length - 1]
    if (last && last.op === op) last.text += text
    else ops.push({ op, text })
  }
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      pushOp('keep', a[i]); i++; j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      pushOp('del', a[i]); i++
    } else {
      pushOp('add', b[j]); j++
    }
  }
  while (i < a.length) { pushOp('del', a[i]); i++ }
  while (j < b.length) { pushOp('add', b[j]); j++ }
  return ops
}

const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

/**
 * 把 op 序列轉成對照 HTML：刪除套 .diffdel、新增套 .diffadd（樣式已存在於 main.css）。
 * 換行轉 <br>，維持段落。
 */
export function diffToHtml(oldText, newText) {
  const ops = diffOps(oldText, newText)
  return ops
    .map((o) => {
      const html = esc(o.text).replace(/\n/g, '<br>')
      if (o.op === 'del') return `<span class="diffdel">${html}</span>`
      if (o.op === 'add') return `<span class="diffadd">${html}</span>`
      return html
    })
    .join('')
}

// 統計（可選）：新增／刪除的 token 數，給「本次修改 N 處」用。
export function diffStats(oldText, newText) {
  const ops = diffOps(oldText, newText)
  let added = 0
  let removed = 0
  for (const o of ops) {
    if (o.op === 'add') added += o.text.length
    else if (o.op === 'del') removed += o.text.length
  }
  return { added, removed }
}
