<script setup>
import { ref } from 'vue'
import { startWork } from '../store/workbench.js'

const DEMO_FILES = [
  { n: '訴願書_1147101517.pdf', s: '412 KB', x: '訴願人 · 3 頁' },
  { n: '原處分書_書面告誡_11312180226-03.pdf', s: '186 KB', x: '警察局汐止分局 · 1 頁' },
  { n: '送達證書.pdf', s: '94 KB', x: '掛號回執 · 1 頁' },
  { n: '撤銷函_新北警汐刑字第11442604401號.pdf', s: '152 KB', x: '原處分機關 · 1 頁' },
]

const files = ref([])
const hot = ref(false)
const generating = ref(false)
const fileInput = ref(null)

function loadDemo() {
  files.value = DEMO_FILES.map((f) => ({ ...f }))
}
function addFiles(list) {
  for (const f of list) files.value.push({ n: f.name, s: Math.round(f.size / 1024) + ' KB', x: '已上傳' })
}
function onDrop(e) {
  hot.value = false
  const fs = [...(e.dataTransfer?.files || [])]
  if (fs.length) addFiles(fs)
  else loadDemo()
}
function onPick(e) {
  addFiles(e.target.files)
}
function removeAt(i) {
  files.value.splice(i, 1)
}
function confirm() {
  if (!files.value.length || generating.value) return
  generating.value = true
  setTimeout(() => {
    generating.value = false
    startWork()
    window.scrollTo({ top: 0 })
  }, 1400)
}
</script>

<template>
  <section class="page">
    <div class="uwrap">
      <h1>上傳卷證</h1>
      <p class="sub">上傳訴願書、原處分書、送達證書與答辯書，確認後系統將直接生成決定書草稿。</p>
      <div
        class="drop"
        :class="{ hot }"
        tabindex="0"
        role="button"
        aria-label="上傳卷證檔案"
        @click="fileInput.click()"
        @keydown.enter.prevent="loadDemo"
        @keydown.space.prevent="loadDemo"
        @dragenter.prevent="hot = true"
        @dragover.prevent="hot = true"
        @dragleave.prevent="hot = false"
        @drop.prevent="onDrop"
      >
        <span class="mi" style="font-size: 44px; color: var(--navy); opacity: 0.5">upload_file</span>
        <div class="big">拖曳檔案至此，或點擊選擇</div>
        <div class="sm">PDF／DOCX／掃描影像，單檔 20MB</div>
        <input ref="fileInput" type="file" multiple hidden @change="onPick" />
      </div>
      <ul class="files">
        <li v-for="(f, i) in files" :key="i" :style="{ animationDelay: i * 80 + 'ms' }">
          <span class="mi doc">description</span>
          <span class="meta"><b>{{ f.n }}</b><span>{{ f.s }}　·　{{ f.x }}</span></span>
          <span class="ok">✓ 已上傳</span>
          <button aria-label="移除" @click="removeAt(i)">×</button>
        </li>
      </ul>
      <div class="ubar">
        <button class="btn ghost sm" @click="loadDemo">載入示範案件</button>
        <div class="gap"></div>
        <span style="font-size: 13px; color: var(--ink-3)">
          {{ files.length ? files.length + ' 份卷證待解析' : '請先上傳卷證' }}
        </span>
        <button class="btn" :disabled="!files.length || generating" @click="confirm">
          {{ generating ? '生成草稿中…' : '確認上傳，生成草稿 →' }}
        </button>
      </div>
    </div>
  </section>
</template>
