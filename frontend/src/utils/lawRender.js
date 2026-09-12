// 法規全國法規資料庫格式渲染 — 自 design/petition-ai-demo.html 原樣移植
import { LAWDB } from '../data/lawdb.js'

// 取法條「重點」：優先取螢光筆標記的項/款，否則取首項或 lead
export function lawKeyPoint(l) {
  const rel = l.rel || []
  for (const p of l.paras) {
    if (p.t && rel.includes(p.n)) return p.t
    for (const k of p.ks || []) if (rel.includes(k.n)) return k.n + '、' + k.t
  }
  if (l.lead) return l.lead
  const p0 = l.paras[0]
  return p0.t || (p0.ks || [])[0] ? (p0.t || p0.ks[0].n + '、' + p0.ks[0].t) : ''
}

// 單一條文渲染（供全文列表用）：highlight 只在 focus 條文套用
export function lawArticleHTML(l, focused) {
  const hl = (k) => (focused && (l.rel || []).includes(k) ? ' hl' : '')
  let h = ''
  if (l.ch) h += '<div class="ch">' + l.ch + '</div>'
  if (l.se) h += '<div class="ch">' + l.se + '</div>'
  h += '<div class="art' + (focused ? ' artfocus' : '') + '">' + l.art + (focused ? '　<span class="artcur">本件援用</span>' : '') + '</div>'
  if (l.lead) h += '<div>' + l.lead + '</div>'
  const multi = l.paras.length > 1
  l.paras.forEach((p) => {
    let inner = ''
    if (p.t) inner += '<span class="' + hl(p.n).trim() + '">' + p.t + '</span>'
    ;(p.ks || []).forEach((k) => {
      inner += '<span class="kk' + hl(k.n) + '">' + k.n + '、' + k.t + '</span>'
    })
    if (multi && p.n) h += '<div class="pa"><span class="pn">' + p.n + '</span><span>' + inner + '</span></div>'
    else h += '<div>' + inner + '</div>'
  })
  return h
}

// 法條全文：列出該法規在資料庫內全部條文（依條號排序），本件援用的條文高亮
export function lawFullHTML(focus) {
  const artNum = (a) => {
    const m = (a || '').match(/\d+/)
    return m ? parseInt(m[0]) : 9999
  }
  const arts = LAWDB.filter((x) => x.law === focus.law).sort((a, b) => artNum(a.art) - artNum(b.art))
  const body =
    '<div class="lawdoc" style="border:0;padding:0;background:none">' +
    '<div class="fm">法規名稱：<b>' + focus.law + '</b><br>修正日期：<b>' + focus.date + '</b></div>' +
    arts.map((a) => lawArticleHTML(a, a.id === focus.id)).join('') +
    '</div>'
  return { body, count: arts.length }
}
