<script setup>
import { ref } from 'vue'
import {
  state, buckets, bucketOpen, toggleBucket, casesIn, selectCase,
  moveCase, createCase, addFolder, newCaseInFolder,
} from '../store/app.js'
import { IconNewCase, IconNewFolder } from './icons.js'
import ContextMenu from './ContextMenu.vue'

const emit = defineEmits(['sheet'])
const ctx = ref(null)
const dragId = ref(null)
const dropFid = ref(undefined)

function onDragStart(c) {
  dragId.value = c.id
}
function onDragEnd() {
  dragId.value = null
  dropFid.value = undefined
}
function onDrop(f) {
  dropFid.value = undefined
  const c = state.cases.find((x) => x.id === dragId.value)
  if (c) moveCase(c, f.id, f.name)
  dragId.value = null
}
function onDragOver(f) {
  if (!dragId.value) return
  dropFid.value = f.id
  if (!bucketOpen(f)) toggleBucket(f)
}

function newCaseTop() {
  const c = createCase(null, state.cases.find((x) => x.id === state.activeId)?.folderId || null)
  state.activeId = c.id
  window.__toast && window.__toast('已建立「' + c.name + '」，上傳卷證後會自動命名')
}
function newFolder() {
  emit('sheet', { kind: 'prompt', title: '新增資料夾', label: '資料夾名稱', value: '新資料夾', fn: (v) => addFolder(v) })
}

function caseMenu(e, c) {
  e.preventDefault()
  ctx.value.show(e, [
    { label: '重新命名', fn: () => emit('sheet', { kind: 'prompt', title: '重新命名案件', label: '案件名稱', value: c.name, fn: (v) => (c.name = v) }) },
    { label: '移至資料夾⋯', fn: () => emit('sheet', { kind: 'move', case: c }) },
    { label: '建立副本', fn: () => { const d = createCase(c.name + '（副本）', c.folderId); state.activeId = d.id } },
    '-',
    { label: '刪除案件', danger: true, fn: () => emit('sheet', { kind: 'delCase', case: c }) },
  ])
}
function folderMenu(e, f) {
  if (f.fixed) return
  e.preventDefault()
  ctx.value.show(e, [
    { label: '重新命名', fn: () => emit('sheet', { kind: 'prompt', title: '重新命名資料夾', label: '資料夾名稱', value: f.name, fn: (v) => (f.name = v) }) },
    { label: '在此新增案件', fn: () => newCaseInFolder(f) },
    '-',
    { label: '刪除資料夾', danger: true, fn: () => emit('sheet', { kind: 'delFolder', folder: f }) },
  ])
}
</script>

<template>
  <aside class="rail rail-left" :class="{ open: state.leftOpen }">
    <div class="rail-head">
      <span class="rail-title">案件清單</span>
      <span class="spacer"></span>
      <button class="rail-act" title="新增案件" aria-label="新增案件" @click="newCaseTop"><IconNewCase /></button>
      <button class="rail-act" title="新增資料夾" aria-label="新增資料夾" @click="newFolder"><IconNewFolder /></button>
    </div>
    <div class="rail-scroll">
      <div
        v-for="f in buckets()"
        :key="f.id ?? 'root'"
        class="tree-folder"
        :class="{ drop: dropFid === f.id }"
        @dragover.prevent="onDragOver(f)"
        @dragleave="dropFid = undefined"
        @drop.prevent="onDrop(f)"
      >
        <div class="tree-row" :class="{ collapsed: !bucketOpen(f), 'is-fixed': f.fixed }" @click="toggleBucket(f)" @contextmenu="folderMenu($event, f)">
          <span class="tw">▼</span>
          <span class="fname">{{ f.name }}</span>
          <span class="cnt">{{ casesIn(f.id).length }}</span>
        </div>
        <div class="tree-children" :class="{ hidden: !bucketOpen(f) }">
          <div v-if="!casesIn(f.id).length" class="tree-empty">（拖曳案件到這裡）</div>
          <div
            v-for="c in casesIn(f.id)"
            :key="c.id"
            class="case-item"
            :class="{ active: c.id === state.activeId, 'has-work': c.started, dragging: dragId === c.id }"
            draggable="true"
            @click="selectCase(c.id)"
            @contextmenu="caseMenu($event, c)"
            @dragstart="onDragStart(c)"
            @dragend="onDragEnd"
          >
            <span class="dot"></span>
            <span class="cname">{{ c.name }}</span>
            <span class="grip">⠿</span>
          </div>
        </div>
      </div>
    </div>
    <ContextMenu ref="ctx" />
  </aside>
</template>
