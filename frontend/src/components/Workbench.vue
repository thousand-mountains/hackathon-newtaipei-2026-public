<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { state, wordCount, downloadWord, downloadPdf, removeLaw } from '../store/workbench.js'
import { lawById } from '../data/lawdb.js'
import { caseById, CASETEXT } from '../data/caseDb.js'
import { lawFullHTML } from '../utils/lawRender.js'
import LeftColumn from './LeftColumn.vue'
import DecisionSheet from './DecisionSheet.vue'
import AddLawModal from './AddLawModal.vue'
import FullTextModal from './FullTextModal.vue'
import ConfirmModal from './ConfirmModal.vue'

const dlOpen = ref(false)
const addLawOpen = ref(false)
const wk = ref(null)

// 全文 dialog（法條／案例共用 FullTextModal）
const full = ref({ open: false, title: '', meta: '', body: '', isCase: false })
// 確認框
const confirm = ref({ open: false, title: '', body: '', okText: '', cb: null })

function toggleDl() {
  dlOpen.value = !dlOpen.value
}
function pickWord() {
  dlOpen.value = false
  downloadWord()
}
function pickPdf() {
  dlOpen.value = false
  downloadPdf()
}

const esc = (s) => String(s || '').replace(/[&<>]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m]))

function openLaw(id) {
  // 有後端 payload 時一律用後端給的。**快照只索引條號、不含條文原文**，
  // 後端明講不代為補寫——前端也不拿內建 mock 的條文頂替（那會變成編造條文）。
  const card = state.payload && state.payload.laws.find((x) => x.id === id)
  if (card) {
    full.value = {
      open: true,
      title: card.law + '　' + card.art,
      meta: '<span style="font-size:12.5px;color:var(--ink-3)">' + esc(card.src) + '</span>',
      body: card.hasText
        ? '<div class="lawdoc">' + esc(card.keyPoint) + '</div>'
        : '<div class="lawdoc" style="color:#8a6320">' + esc(card.keyPoint) +
          '<p style="margin-top:10px">本系統不代為補寫條文文字。請對照全國法規資料庫確認。</p></div>',
      isCase: false,
    }
    return
  }
  const l = lawById(id)
  if (!l) return
  const { body, count } = lawFullHTML(l)
  full.value = {
    open: true,
    title: l.law,
    meta: '<span style="font-size:12.5px;color:var(--ink-3)">' + (l.src || '全國法規資料庫') + '　·　共 ' + count + ' 條（資料庫收錄，離線示範）</span>',
    body,
    isCase: false,
  }
}
function openCase(id) {
  const card = state.payload && state.payload.cases.find((x) => x.id === id)
  if (card) {
    full.value = {
      open: true,
      title: card.t,
      meta: '<span style="font-size:12.5px;color:var(--ink-3)">' + esc(card.src) +
            '　·　' + esc(card.provenanceLabel) + '　·　' + esc(card.simLabel) + ' ' + (card.sim ?? '—') + '%</span>',
      body: '<div class="lawdoc">' + esc(card.d || '（後端未回傳內文摘錄）') + '</div>',
      isCase: true,
    }
    return
  }
  const c = caseById(id)
  const d = CASETEXT[id]
  if (!c || !d) return
  let t = esc(d.text)
  ;(d.hl || []).forEach((k) => {
    const kk = esc(k)
    t = t.split(kk).join('<mark>' + kk + '</mark>')
  })
  full.value = {
    open: true,
    title: c.t,
    meta: '<span class="simbar"><i style="width:' + c.sim + '%"></i></span>相似度 ' + c.sim + '%　' + c.src,
    body: t,
    isCase: true,
  }
}
function closeFull() {
  full.value.open = false
}

function askRemoveLaw(id, used) {
  if (used) {
    confirm.value = {
      open: true,
      title: '移除法條',
      okText: '仍要移除',
      body: '草稿中有 <b>' + used + '</b> 句引用此法條，移除後這些句子將失去依據連結。',
      cb: () => { removeLaw(id); closeConfirm() },
    }
  } else {
    removeLaw(id)
  }
}
function askDiscardPicked(count, discardCb) {
  confirm.value = {
    open: true,
    title: '尚未加入已選法條',
    okText: '放棄並關閉',
    body: '你已勾選 <b>' + count + '</b> 條法條但尚未加入，關閉後選擇不會保留。',
    cb: () => { discardCb(); closeConfirm() },
  }
}
function confirmOk() {
  const f = confirm.value.cb
  if (f) f()
}
function closeConfirm() {
  confirm.value.open = false
  confirm.value.cb = null
}

// 分割拖曳
let dragging = false
function startDrag() {
  dragging = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', endDrag)
}
function onDrag(e) {
  if (!dragging || !wk.value) return
  const r = wk.value.getBoundingClientRect()
  let p = (e.clientX - r.left) / r.width
  p = Math.min(0.7, Math.max(0.28, p))
  wk.value.style.gridTemplateColumns = p + 'fr 6px ' + (1 - p) + 'fr'
}
function endDrag() {
  dragging = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', endDrag)
}

// 點空白處關閉下載選單；Esc 關閉所有 modal
function onDocClick() {
  dlOpen.value = false
}
function onKeydown(e) {
  if (e.key === 'Escape') {
    dlOpen.value = false
    full.value.open = false
    addLawOpen.value = false
    closeConfirm()
  }
}
onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <section class="page">
    <!-- 三個誠實維度：**分三句講，不合併**（合併正是今天修掉的 bug） -->
    <div v-if="state.provenance" class="prov">
      <div class="pv"><span class="pk">執行模式</span><span>{{ state.provenance.execution }}</span></div>
      <div class="pv"><span class="pk">資料性質</span><span>{{ state.provenance.data }}</span></div>
      <div class="pv"><span class="pk">檢索範圍</span><span>{{ state.provenance.retrieval }}</span></div>
      <div v-if="state.run.runId" class="pv"><span class="pk">本次執行</span><span class="mono">{{ state.run.runId }}</span></div>
    </div>
    <div v-if="state.conn.usingMock" class="prov mock">
      <b>離線示範資料</b>——畫面內容取自前端內建的假資料，<b>不是任何一次真實執行的結果</b>，且不提供下載。
    </div>

    <div class="wbar">
      <h1>訴願決定書草稿</h1>
      <span class="caseno">{{ state.caseno }}</span>
      <div class="sp"></div>
      <div class="seg">
        <button :aria-pressed="state.mode === 'view'" title="閱覽模式" @click="state.mode = 'view'">
          <span class="mi" style="font-size: 19px">visibility</span>閱覽
        </button>
        <button :aria-pressed="state.mode === 'edit'" title="編輯模式" @click="state.mode = 'edit'">
          <span class="mi" style="font-size: 19px">edit</span>編輯
        </button>
      </div>
      <div class="dl">
        <button class="btn" @click.stop="toggleDl">
          <span class="mi" style="font-size: 18px; vertical-align: -4px">download</span> 下載檔案
          <span class="mi" style="font-size: 18px; vertical-align: -4px">expand_more</span>
        </button>
        <div class="dlmenu" :class="{ on: dlOpen }" @click.stop>
          <button @click="pickPdf"><span class="mi">picture_as_pdf</span><span>下載 PDF<small>透過瀏覽器列印另存</small></span></button>
          <button @click="pickWord"><span class="mi">description</span><span>下載 Word<small>.doc，可直接編修套印</small></span></button>
        </div>
      </div>
    </div>

    <div class="work" id="wk" ref="wk">
      <LeftColumn
        @open-law="openLaw"
        @open-case="openCase"
        @add-law="addLawOpen = true"
        @remove-law="askRemoveLaw"
      />
      <div class="grip" role="separator" aria-orientation="vertical" tabindex="0" @mousedown="startDrag"></div>
      <div class="pane">
        <div class="hd">
          <span class="ttl">決定書</span>
          <span class="tag n">{{ wordCount }} 字</span>
          <span class="gap"></span>
          <span style="font-size: 12.5px; color: var(--ink-3)">點任一句可查看其依據</span>
        </div>
        <DecisionSheet />
      </div>
    </div>

    <!-- 送出守門結果（US-4）：**submit_allowed 只認後端**，409 帶著理由 -->
    <div v-if="state.gate.checking" class="gate busy">
      正在請後端重跑六節點複核可否送出…（約 1 分鐘，後端不採信前端的判斷）
    </div>
    <div v-else-if="state.gate.checked && !state.gate.allowed" class="gate bad">
      <div class="gh">
        <span class="mi">block</span>
        <b>後端拒絕送出，已阻擋下載</b>
      </div>
      <div v-if="state.gate.error" class="gm">{{ state.gate.error }}</div>
      <ul class="bl">
        <li v-for="(b, i) in state.gate.blockers" :key="i">
          <span class="sev">{{ b.severity || 'P0' }}</span>
          <code>{{ b.reason }}</code>
          <span v-if="b.sentence_id" class="sid">{{ b.sentence_id }}</span>
          <div class="bd2">{{ b.detail }}</div>
        </li>
      </ul>
      <div class="gm">
        此判斷由後端重跑六節點得出，回應原文：「未採信前端送來的任何判斷」。改 DOM 或直接打 API 都繞不過去。
      </div>
    </div>
    <div v-else-if="state.gate.checked && state.gate.allowed" class="gate ok">
      <b>後端允許送出</b>——本動作僅寫入本機紀錄，<b>沒有寄送任何郵件、沒有排入任何議程、沒有呼叫任何外部系統</b>。
    </div>

    <AddLawModal :open="addLawOpen" @close="addLawOpen = false" @confirm-discard="askDiscardPicked" />
    <FullTextModal
      :open="full.open"
      :title="full.title"
      :meta="full.meta"
      :body="full.body"
      :is-case="full.isCase"
      @close="closeFull"
    />
    <ConfirmModal
      :open="confirm.open"
      :title="confirm.title"
      :body="confirm.body"
      :ok-text="confirm.okText"
      @ok="confirmOk"
      @cancel="closeConfirm"
    />
  </section>
</template>

<style scoped>
.prov { display: grid; gap: 4px; background: #f7f6f1; border: 1px solid #e3e0d8; border-radius: 8px;
  padding: 10px 13px; margin-bottom: 10px; font-size: 12.5px; line-height: 1.8; }
.prov.mock { background: #fdf7ea; border-color: #ecdcbb; }
.pv { display: grid; grid-template-columns: 74px 1fr; gap: 10px; }
.pk { color: var(--ink-3); }
.mono { font-family: var(--mono); }
.gate { margin-top: 12px; border-radius: 8px; padding: 12px 14px; font-size: 13px; line-height: 1.8; }
.gate.busy { background: #f7f6f1; border: 1px solid #e3e0d8; }
.gate.ok { background: #f0f5f0; border: 1px solid #cfe0cf; }
.gate.bad { background: #fdf3f2; border: 1px solid #e9c9c5; }
.gh { display: flex; align-items: center; gap: 6px; font-size: 14px; }
.gm { color: var(--ink-3); font-size: 12.5px; margin-top: 6px; }
.bl { margin: 8px 0 0; padding-left: 0; list-style: none; }
.bl li { background: #fff; border: 1px solid #ecd9d5; border-radius: 6px; padding: 8px 10px; margin-bottom: 6px; }
.sev { background: #a03028; color: #fff; border-radius: 4px; padding: 1px 6px; font-size: 11px; margin-right: 6px; }
.sid { color: var(--ink-3); font-family: var(--mono); margin-left: 6px; }
.bd2 { margin-top: 4px; color: var(--ink-2); }
</style>
