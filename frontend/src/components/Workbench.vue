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

function openLaw(id) {
  const l = lawById(id)
  if (!l) return
  const { body, count } = lawFullHTML(l)
  full.value = {
    open: true,
    title: l.law,
    meta: '<span style="font-size:12.5px;color:var(--ink-3)">' + (l.src || '全國法規資料庫') + '　·　共 ' + count + ' 條（資料庫收錄）</span>',
    body,
    isCase: false,
  }
}
function openCase(id) {
  const c = caseById(id)
  const d = CASETEXT[id]
  if (!c || !d) return
  const esc = (str) => str.replace(/[&<>]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m]))
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
