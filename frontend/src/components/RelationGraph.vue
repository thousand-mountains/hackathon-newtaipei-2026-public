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
const nodes = computed(() => {
  const perCol = {}
  return (props.graph.nodes || []).map((n) => {
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
          <text :x="gx(n.c) + 11" :y="gy(n._r) + 17" fill="var(--ink)" font-weight="500">{{ n.t }}</text>
          <text class="gt2" :x="gx(n.c) + 11" :y="gy(n._r) + 32" fill="var(--muted)">{{ n.s || '' }}</text>
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
        圖中共 {{ (graph.stats && graph.stats.nodes) ?? nodes.length }} 個節點、{{ (graph.stats && graph.stats.edges) ?? edges.length }} 條關聯。點選任一節點可追蹤其關聯路徑。
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
