<script setup>
// 案件關聯圖。**全部資料來自 `tool_result.graph`（契約 §3.7），沒有示範資料。**
//
// 三件跟契約有關、不能改回去的事：
// 1. **沒有「矛盾／爭執」這種邊**（§6 誠實紅線第 4 條）。後端沒有任何偵測矛盾的機制，
//    畫了就是編。第三種線型改用真的有的東西：`cite` 邊的 `state`／`lamp`，非 ok 畫成警示色。
// 2. **四種邊湊不出同一份 run**（§3.7 實測：掃過 9,421 份 VERIFIED run 一份都沒有）。
//    所以圖例只列**這張圖真的有**的線型，不是四種都畫上去。
// 3. **`unlinked` 與 `flagged` 要畫出來。** 「查到了但沒用上」與「草稿引了、檢索沒找到」
//    正是承辦人最需要知道的兩件事，不要靜默丟掉。
import { ref, computed } from 'vue'

const props = defineProps({ graph: { type: Object, required: true } })

const GW = 150, GH = 46, GAPX = 54, GAPY = 64, PADX = 18, PADT = 40
const gx = (c) => PADX + c * (GW + GAPX)
const gy = (r) => PADT + r * GAPY

const KIND = { doc: '卷證', fact: '事實', issue: '爭點', law: '法規', out: '結論' }
const KC = { doc: 'n-doc', fact: 'n-fact', issue: 'n-issue', law: 'n-law', out: 'n-out' }
// rel → CSS class。`cite` 依 state 分兩種：ok 走 support，其餘走 conflict（警示色虛線）。
const REL_LABEL = { quote: '卷證引錄', trigger: '觸發爭點', address: '回應爭點', cite: '引用依據' }

const cols = computed(() => props.graph.cols || [])

// 契約的 node 只有欄位 `c`（第幾欄），沒有列號——列號由前端在同一欄內依序排。
//
// **相似案例不進圖。** 後端把相似訴願決定也建成 `k:"law"` 節點放在法規欄，
// 只用 `origin:"similar_case"` 區分（`backend/graph/relation.py`）。
// 案例是給人參考的資料、不是本案的法規依據，混在同一欄會讓人以為草稿引了那些案號。
// 連到被濾掉節點的邊會在下面 edges 的 `if (!A || !B)` 自動略過。
const nodes = computed(() => {
  const perCol = {}
  return (props.graph.nodes || [])
    .filter((n) => n.origin !== 'similar_case')
    .map((n) => {
      const c = n.c || 0
      perCol[c] = (perCol[c] || 0) + 1
      return { ...n, _r: perCol[c] - 1 }
    })
})
const rows = computed(() => nodes.value.reduce((m, n) => Math.max(m, n._r + 1), 1))
const GVW = computed(() => PADX * 2 + Math.max(cols.value.length, 1) * GW + Math.max(cols.value.length - 1, 0) * GAPX)
const GVH = computed(() => PADT + rows.value * GAPY + GH)

const map = computed(() => Object.fromEntries(nodes.value.map((n) => [n.id, n])))

const edges = computed(() =>
  (props.graph.edges || [])
    .map((e) => {
      const A = map.value[e.from], B = map.value[e.to]
      if (!A || !B) return null // 端點不在圖裡就不畫，不要畫一條連到不存在節點的線
      const x1 = gx(A.c) + GW, y1 = gy(A._r) + GH / 2, x2 = gx(B.c), y2 = gy(B._r) + GH / 2
      const flagged = e.rel === 'cite' && e.state && e.state !== 'ok'
      return {
        ...e,
        a: e.from,
        b: e.to,
        cls: flagged ? 'conflict' : e.rel === 'cite' ? 'support' : 'relate',
        d: `M${x1},${y1} C${x1 + 42},${y1} ${x2 - 42},${y2} ${x2},${y2}`,
      }
    })
    .filter(Boolean),
)

// 圖例只列這張圖真的出現過的線型。
const legendRels = computed(() => {
  const seen = new Map()
  edges.value.forEach((e) => {
    const key = e.cls === 'conflict' ? 'flagged' : e.rel
    if (!seen.has(key)) seen.set(key, e.cls)
  })
  return [...seen].map(([key, cls]) => ({ cls, label: key === 'flagged' ? '引用有疑慮' : REL_LABEL[key] || key }))
})
const legendKinds = computed(() => [...new Set(nodes.value.map((n) => n.k))])

const sel = ref(null)
const linked = computed(() => {
  if (!sel.value) return null
  const set = new Set([sel.value])
  edges.value.forEach((p) => { if (p.a === sel.value) set.add(p.b); if (p.b === sel.value) set.add(p.a) })
  return set
})
function select(id) {
  sel.value = id
}
function edgeClass(p) {
  if (!sel.value) return 'edge ' + p.cls
  const hot = p.a === sel.value || p.b === sel.value
  return 'edge ' + p.cls + (hot ? ' hot' : ' dim')
}
function nodeClass(n) {
  if (!sel.value) return 'gnode'
  return 'gnode' + (linked.value.has(n.id) ? '' : ' dim') + (n.id === sel.value ? ' sel' : '')
}
const detail = computed(() => {
  if (!sel.value) return null
  const n = map.value[sel.value]
  if (!n) return null
  const rels = edges.value.filter((p) => p.a === sel.value || p.b === sel.value)
  return { ...n, rel: linked.value.size - 1, bases: [...new Set(rels.map((r) => r.basis).filter(Boolean))] }
})

// SVG <text> 不會自動換行或裁切，長標題會直接畫到框外（爆版）。
// 依框寬換算可容納的視覺寬度後截斷，完整文字放 <title>（hover 可見）與右下詳情面板。
// 全形字寬約等於 font-size，半形約一半，用這個比例估。
const VIS = (s) => [...String(s || '')].reduce((w, ch) => w + (/[\x00-\xff]/.test(ch) ? 0.55 : 1), 0)
function clampText(s, maxVis) {
  const str = String(s || '')
  if (VIS(str) <= maxVis) return str
  let out = ''
  let w = 0
  for (const ch of str) {
    const cw = /[\x00-\xff]/.test(ch) ? 0.55 : 1
    if (w + cw > maxVis - 1) break // 留 1 個字寬給省略號
    out += ch
    w += cw
  }
  return out + '…'
}
// 框寬 GW 扣掉左右內距（左 11 + 右 8），字級取自 main.css：.gnode text 11.5px、.gt2 10px
const titleText = (s) => clampText(s, (GW - 19) / 11.5)
const subText = (s) => clampText(s, (GW - 19) / 10)

const unlinkedLaws = computed(() => (props.graph.unlinked && props.graph.unlinked.laws) || [])
const unlinkedIssues = computed(() => (props.graph.unlinked && props.graph.unlinked.issues) || [])
const flagged = computed(() => props.graph.flagged || [])
</script>

<template>
  <div class="graphwrap">
    <div class="graphbar">
      <div class="legend">
        <span v-for="k in legendKinds" :key="k">
          <i :style="{ background: `var(--n-${k}-bg)`, border: `1px solid var(--n-${k})` }"></i>{{ KIND[k] || k }}
        </span>
        <span v-for="(l, i) in legendRels" :key="'r' + i">
          <i class="ln" :class="{ dash: l.cls === 'relate', bad: l.cls === 'conflict' }"
             :style="l.cls === 'support' ? 'border-top-color:var(--acc-2)' : ''"></i>{{ l.label }}
        </span>
      </div>
      <span class="gb-note">點選節點可追蹤關聯路徑．圖面可左右捲動</span>
    </div>
    <div class="gscroll">
      <svg class="gsvg" :viewBox="`0 0 ${GVW} ${GVH}`" :width="GVW" :height="GVH" role="img"
           aria-label="訴願案件卷證、事實、爭點、法規與結論之關聯圖">
        <text v-for="(n, i) in cols" :key="'c' + i" class="gcol" :x="gx(i)" :y="20">{{ n }}</text>
        <g>
          <path v-for="(p, i) in edges" :key="'e' + i" :class="edgeClass(p)" :d="p.d" />
        </g>
        <g
          v-for="n in nodes"
          :key="n.id"
          :class="nodeClass(n)"
          tabindex="0"
          role="button"
          :aria-label="`${KIND[n.k] || n.k}：${n.t}`"
          @click="select(n.id)"
          @keydown.enter.prevent="select(n.id)"
          @keydown.space.prevent="select(n.id)"
        >
          <rect :x="gx(n.c)" :y="gy(n._r)" :width="GW" :height="GH" rx="3" :fill="`var(--n-${n.k}-bg)`" :stroke="`var(--n-${n.k})`" />
          <!-- 文字依框寬截斷（SVG 不會自己 wrap/clip），完整內容放 <title> 與詳情面板 -->
          <title>{{ n.t }}{{ n.s ? '　' + n.s : '' }}</title>
          <text :x="gx(n.c) + 11" :y="gy(n._r) + 17" fill="var(--ink)" font-weight="500">{{ titleText(n.t) }}</text>
          <text class="gt2" :x="gx(n.c) + 11" :y="gy(n._r) + 32" fill="var(--muted)">{{ subText(n.s) }}</text>
        </g>
      </svg>
    </div>
    <div class="gdetail">
      <template v-if="detail">
        <div class="gd-h">
          <span class="gd-k" :style="{ background: `var(--${KC[detail.k]}-bg)`, color: `var(--${KC[detail.k]})` }">{{ KIND[detail.k] || detail.k }}</span>
          <span class="gd-t">{{ detail.t }}</span>
        </div>
        <div class="gd-b">{{ detail.d }}</div>
        <div class="gd-s">
          來源：{{ detail.src || '—' }}　·　關聯節點 {{ detail.rel }} 個<template v-if="detail.bases.length">　·　依據：{{ detail.bases.join('、') }}</template>
        </div>
      </template>
      <div v-else class="gd-b" style="color: var(--muted)">
        <!-- 用實際畫出的數量，不用 stats：相似案例節點已被濾掉（見 nodes computed），
             沿用後端 stats 會出現「說 24 個節點卻只看到 20 個」。 -->
        圖中共 {{ nodes.length }} 個節點、{{ edges.length }} 條關聯。點選任一節點可追蹤其關聯路徑。
      </div>
      <!-- 查到了但沒有被任何句子引用的法規／沒被回應的爭點 -->
      <div v-if="unlinkedLaws.length || unlinkedIssues.length" class="gd-s">
        未被引用：<template v-if="unlinkedLaws.length">法規 {{ unlinkedLaws.join('、') }}</template>
        <template v-if="unlinkedIssues.length">　爭點 {{ unlinkedIssues.join('、') }}</template>
        <template v-if="graph.unlinked && graph.unlinked.note">（{{ graph.unlinked.note }}）</template>
      </div>
      <!-- 紅線：草稿引了、檢索沒找到的條號，不靜默丟棄 -->
      <div v-if="flagged.length" class="gd-s" style="color: var(--alert)">
        引用有疑慮 {{ flagged.length }} 處：{{ flagged.map((f) => `${f.raw}（${f.state}）`).join('、') }}
      </div>
    </div>
  </div>
</template>
