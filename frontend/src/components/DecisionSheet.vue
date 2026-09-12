<script setup>
import { computed } from 'vue'
import { state, lawLines, pcls, pickSent, clearSel, editSent, isSentSelected } from '../store/workbench.js'

const editing = computed(() => state.mode === 'edit')
const selected = computed(() => (state.selId ? state.SENTS.find((s) => s.id === state.selId) : null) || null)

// 抬頭一律由 payload 驅動。**後端沒給的欄位就不顯示**——舊版把案號、要旨、
// 發文日期、發文字號寫死成一份洗錢防制法的 mock，結果畫面上方顯示 mock 的洗錢案、
// 下方顯示 payload 的空污案，兩份互相矛盾的資料同時出現。
// 發文日期與發文字號後端目前沒有，所以這裡不列——不編一個看起來很像的。
const head = computed(() => {
  const p = state.payload
  if (!p) return { rows: [], title: '離線示範資料', no: '', mock: true }
  const i = p.intake || {}
  const rows = []
  if (i.no) rows.push(['案號', i.no])
  if (i.type) rows.push(['要旨', i.type])
  if (i.person) rows.push(['訴願人', i.person])
  if (i.org) rows.push(['原處分機關', i.org])
  rows.push(['案例識別', p.caseId])
  return { rows, title: (p.doc[0] && p.doc[0].text) || '訴願決定書', no: i.no || '', mock: false }
})

function onSentClick(id) {
  pickSent(id)
}
function onSheetClick(e) {
  if (!e.target.closest('.sent')) clearSel()
}
function onSentInput(e, id) {
  editSent(id, e.target.textContent)
}
</script>

<template>
  <div class="editor" id="editorwrap" @click="onSheetClick">
    <div class="sheet" id="sheet" :class="editing ? 'edit' : 'view'">
      <template v-for="(b, bi) in state.DOC" :key="bi">
        <p v-if="b.ty === 'gap'" class="gapline">　</p>

        <template v-else-if="b.ty === 'title'">
          <div class="exfm">
            <div class="rw" v-for="([k, v], i) in head.rows" :key="i">
              <span class="lb">{{ k }}</span><span class="vl">{{ v }}</span>
            </div>
            <div class="rw">
              <span class="lb">相關法條</span>
              <span class="vl"><span class="ln" v-for="(x, i) in lawLines" :key="i">{{ x }}</span></span>
            </div>
          </div>
          <div class="doc-title">
            <span>{{ head.title }}</span>
            <span v-if="head.no" class="no">案號：{{ head.no }}</span>
          </div>
        </template>

        <p v-else-if="b.ty === 'meta'" class="meta">{{ b.text }}</p>
        <h4 v-else-if="b.ty === 'h'">{{ b.text }}</h4>

        <p v-else :class="pcls(b)">
          <span
            v-for="s in b.ss"
            :key="s.id + '-' + (s.ver || 0)"
            class="sent"
            :data-id="s.id"
            :class="[{ sel: isSentSelected(s.id), ph: s.placeholder }, 'lamp-' + (s.lampClass || 'none')]"
            :title="s.originLabel ? s.originLabel + (s.why ? '｜' + s.why : '') : ''"
            spellcheck="false"
            :contenteditable="editing"
            @click="onSentClick(s.id)"
            @input="onSentInput($event, s.id)"
          >{{ s.t }}</span>
        </p>
      </template>

      <!-- 結論段被封鎖時，明說它為什麼不在這裡（不是漏了） -->
      <div v-if="state.payload && state.payload.procedure.requiresHumanConclusion" class="blocked">
        <b>結論段未生成</b>
        <div>{{ state.payload.handoff.note || '本案結論涉及法律判斷，系統已停止生成結論段。' }}</div>
        <div v-if="state.payload.handoff.criterion" class="crit">
          <b>封鎖判準</b>{{ state.payload.handoff.criterion.text }}
        </div>
        <ol v-if="(state.payload.handoff.questions || []).length" class="qs">
          <li v-for="(q, i) in state.payload.handoff.questions" :key="i">{{ q }}</li>
        </ol>
      </div>
    </div>

    <!-- 選中句子的依據（逐句可查，US-5／US-8） -->
    <div v-if="selected" class="basis">
      <div class="brow"><span class="bk">來源</span><span>{{ selected.originLabel }}</span></div>
      <div class="brow" v-if="selected.basis"><span class="bk">依據</span><span class="mono">{{ selected.basis }}</span></div>
      <div class="brow" v-if="selected.why"><span class="bk">查核說明</span><span>{{ selected.why }}</span></div>
      <div class="brow" v-if="selected.refs.length">
        <span class="bk">引用</span><span class="mono">{{ selected.refs.join('、') }}</span>
      </div>
      <div class="brow" v-if="selected.placeholder">
        <span class="bk">注意</span><span class="warn">本句為佔位文字，尚未有實質內容。</span>
      </div>
    </div>
  </div>
</template>


<style scoped>
.blocked { border: 1px solid #e9c9c5; background: #fdf3f2; border-radius: 8px;
  padding: 12px 14px; margin-top: 16px; font-size: 13px; line-height: 1.85; }
.blocked .crit { margin-top: 6px; color: var(--ink-2); }
.blocked .qs { margin: 8px 0 0 18px; }
.sent.ph { background: #fdf7ea; border-bottom: 1px dashed #c8a24a; }
.basis { border-top: 1px solid var(--line, #e3e0d8); margin-top: 12px; padding: 10px 14px;
  font-size: 12.5px; line-height: 1.8; background: #fafaf7; }
.brow { display: grid; grid-template-columns: 64px 1fr; gap: 10px; }
.bk { color: var(--ink-3); }
.mono { font-family: var(--mono); }
.warn { color: #8a6320; }
</style>
