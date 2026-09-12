<script setup>
import { ref, computed } from 'vue'
import { GNODES, GEDGES, GCOLS, GKIND } from '../data/graph.js'

const GW = 150, GH = 46, GAPX = 54, GAPY = 64, PADX = 18, PADT = 40
const gx = (c) => PADX + c * (GW + GAPX)
const gy = (r) => PADT + r * GAPY
const GVW = PADX * 2 + 5 * GW + 4 * GAPX
const GVH = PADT + 5.4 * GAPY + GH + 26

const map = Object.fromEntries(GNODES.map((n) => [n.id, n]))
const KC = { doc: 'n-doc', fact: 'n-fact', issue: 'n-issue', law: 'n-law', out: 'n-out' }

const edges = GEDGES.map(([a, b, type]) => {
  const A = map[a], B = map[b]
  const x1 = gx(A.c) + GW, y1 = gy(A.r) + GH / 2, x2 = gx(B.c), y2 = gy(B.r) + GH / 2
  return { a, b, type, d: `M${x1},${y1} C${x1 + 42},${y1} ${x2 - 42},${y2} ${x2},${y2}` }
})

const sel = ref(null)
const linked = computed(() => {
  if (!sel.value) return null
  const set = new Set([sel.value])
  edges.forEach((p) => { if (p.a === sel.value) set.add(p.b); if (p.b === sel.value) set.add(p.a) })
  return set
})
function select(id) {
  sel.value = id
}
function edgeClass(p) {
  if (!sel.value) return 'edge ' + p.type
  const hot = p.a === sel.value || p.b === sel.value
  return 'edge ' + p.type + (hot ? ' hot' : ' dim')
}
function nodeClass(n) {
  if (!sel.value) return 'gnode'
  return 'gnode' + (linked.value.has(n.id) ? '' : ' dim') + (n.id === sel.value ? ' sel' : '')
}
const detail = computed(() => {
  if (!sel.value) return null
  const n = map[sel.value]
  return { ...n, rel: linked.value.size - 1 }
})
</script>

<template>
  <div class="graphwrap">
    <div class="graphbar">
      <div class="legend">
        <span><i style="background: var(--n-doc-bg); border: 1px solid var(--n-doc)"></i>卷證</span>
        <span><i style="background: var(--n-fact-bg); border: 1px solid var(--n-fact)"></i>事實</span>
        <span><i style="background: var(--n-issue-bg); border: 1px solid var(--n-issue)"></i>爭點</span>
        <span><i style="background: var(--n-law-bg); border: 1px solid var(--n-law)"></i>法規</span>
        <span><i style="background: var(--n-out-bg); border: 1px solid var(--n-out)"></i>結論</span>
        <span><i class="ln" style="border-top-color: var(--acc-2)"></i>支持</span>
        <span><i class="ln dash"></i>關聯</span>
        <span><i class="ln bad"></i>爭執／不足</span>
      </div>
      <span class="gb-note">點選節點可追蹤關聯路徑．圖面可左右捲動</span>
    </div>
    <div class="gscroll">
      <svg class="gsvg" :viewBox="`0 0 ${GVW} ${GVH}`" :width="GVW" :height="GVH" role="img" aria-label="訴願案件卷證、事實、爭點、法規與結論之關聯圖">
        <text v-for="(n, i) in GCOLS" :key="'c' + i" class="gcol" :x="gx(i)" :y="20">{{ n }}</text>
        <g>
          <path v-for="(p, i) in edges" :key="'e' + i" :class="edgeClass(p)" :d="p.d" />
        </g>
        <g
          v-for="n in GNODES"
          :key="n.id"
          :class="nodeClass(n)"
          tabindex="0"
          role="button"
          :aria-label="`${GKIND[n.k]}：${n.t}`"
          @click="select(n.id)"
          @keydown.enter.prevent="select(n.id)"
          @keydown.space.prevent="select(n.id)"
        >
          <rect :x="gx(n.c)" :y="gy(n.r)" :width="GW" :height="GH" rx="3" :fill="`var(--n-${n.k}-bg)`" :stroke="`var(--n-${n.k})`" />
          <text :x="gx(n.c) + 11" :y="gy(n.r) + 17" fill="var(--ink)" font-weight="500">{{ n.t }}</text>
          <text class="gt2" :x="gx(n.c) + 11" :y="gy(n.r) + 32" fill="var(--muted)">{{ n.s }}</text>
        </g>
      </svg>
    </div>
    <div class="gdetail">
      <template v-if="detail">
        <div class="gd-h">
          <span class="gd-k" :style="{ background: `var(--${KC[detail.k]}-bg)`, color: `var(--${KC[detail.k]})` }">{{ GKIND[detail.k] }}</span>
          <span class="gd-t">{{ detail.t }}</span>
        </div>
        <div class="gd-b">{{ detail.d }}</div>
        <div class="gd-s">來源：{{ detail.src }}　·　關聯節點 {{ detail.rel }} 個</div>
      </template>
      <div v-else class="gd-b" style="color: var(--muted)">
        圖中共 {{ GNODES.length }} 個節點、{{ GEDGES.length }} 條關聯。<b style="color: var(--ink); font-weight: 500">紅色虛線</b>標示卷內互相爭執或證據不足之處——本案有 2 條，皆指向爭點一之要件事實認定。點選任一節點可追蹤其關聯路徑。
      </div>
    </div>
  </div>
</template>
