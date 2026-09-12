<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import {
  state, active, boot, toast, runTool,
  availableEvidence, attachToPending, uploadToFolder,
  searchHave, searchLibrary, addSearched, deleteCase, deleteFolder,
  viewLawFull, viewDecisionFull, viewArtifactFull,
} from './store/app.js'
import TopBar from './components/TopBar.vue'
import CaseTree from './components/CaseTree.vue'
import FoldersRail from './components/FoldersRail.vue'
import Chat from './components/Chat.vue'
import Sheet from './components/Sheet.vue'
import RelationGraph from './components/RelationGraph.vue'

boot()

// 供 CaseTree 內非 emit 的 toast 呼叫
window.__toast = toast




// 窄螢幕側欄：以 body class 對應原設計 CSS
watch(() => state.leftOpen, (v) => document.body.classList.toggle('left-open', v))
watch(() => state.rightOpen, (v) => document.body.classList.toggle('right-open', v))
function closeRails() {
  state.leftOpen = false
  state.rightOpen = false
}

// ── Sheet 狀態機 ──
const sheet = reactive({ kind: null, data: null })
function openSheet(k, data) {
  sheet.kind = k
  sheet.data = data || {}
}
function closeSheet() {
  sheet.kind = null
  sheet.data = null
}

// prompt / rename
const promptVal = ref('')
// pick (attach/upload)
const pickState = reactive({ items: [], checked: [] })
// search
const searchState = reactive({ q: '', results: [], searching: false, checked: [], have: null, group: null })
let searchDeb = null
let searchSeq = 0

function onSheetRequest(req) {
  if (req.kind === 'prompt') {
    promptVal.value = req.value || ''
    openSheet('prompt', req)
  } else if (req.kind === 'move') {
    openSheet('move', { case: req.case })
  } else if (req.kind === 'delCase') {
    openSheet('confirm', {
      title: '刪除案件',
      body: `確定要刪除「${req.case.name}」嗎？案件內的卷證、案例、法規與草稿都會一併消失，此動作無法復原。`,
      confirmLabel: '刪除案件',
      fn: () => deleteCase(req.case),
    })
  } else if (req.kind === 'delFolder') {
    const n = state.cases.filter((x) => x.folderId === req.folder.id).length
    openSheet('confirm', {
      title: '刪除資料夾',
      body: n
        ? `確定要刪除「${req.folder.name}」嗎？裡面的 ${n} 件案件會移至「未分類」，資料夾本身無法復原。`
        : `確定要刪除「${req.folder.name}」嗎？此動作無法復原。`,
      confirmLabel: '刪除資料夾',
      fn: () => deleteFolder(req.folder),
    })
  } else if (req.kind === 'attach') {
    pickState.items = availableEvidence(true)
    pickState.checked = pickState.items.map((_, i) => i)
    openSheet('attach', {})
  } else if (req.kind === 'upload') {
    pickState.items = availableEvidence(false)
    pickState.checked = pickState.items.map((_, i) => i)
    openSheet('upload', {})
  } else if (req.kind === 'search') {
    const g = req.group || { key: req.groupKey, name: req.groupKey === 'laws' ? '相關法規' : '相關案例' }
    Object.assign(searchState, { q: '', results: [], searching: false, checked: [], have: searchHave(g.key), group: g })
    openSheet('search', {})
  }
}

// ── prompt ──
function submitPrompt() {
  const v = promptVal.value.trim()
  if (v && sheet.data.fn) sheet.data.fn(v)
  closeSheet()
}
// ── move ──
function moveTo(folderId) {
  const c = sheet.data.case
  c.folderId = folderId
  const f = state.folders.find((x) => x.id === folderId)
  if (f) f.open = true
}
// ── confirm ──
function confirmOk() {
  const fn = sheet.data.fn
  closeSheet()
  fn && fn()
}
// ── attach ──
function submitAttach() {
  const files = pickState.checked.map((i) => pickState.items[i]).filter(Boolean)
  attachToPending(files)
  closeSheet()
}
// ── upload ──
function submitUpload() {
  const files = pickState.checked.map((i) => pickState.items[i]).filter(Boolean)
  uploadToFolder(files)
  closeSheet()
}
function toggleCheck(i) {
  const idx = pickState.checked.indexOf(i)
  if (idx >= 0) pickState.checked.splice(idx, 1)
  else pickState.checked.push(i)
}
// ── search ──
function onSearchInput() {
  clearTimeout(searchDeb)
  if (!searchState.q.trim()) {
    searchState.searching = false
    searchState.results = []
    return
  }
  searchState.searching = true
  searchDeb = setTimeout(async () => {
    const q = searchState.q
    const seq = ++searchSeq
    const results = await searchLibrary(searchState.group.key, q)
    if (seq !== searchSeq) return // 有更新的查詢在進行，丟棄這次舊結果
    // 濾掉本案已加入的（避免重複）
    searchState.results = results.filter((r) => !searchState.have.has(r.name))
    searchState.searching = false
  }, 420)
}
function toggleSearchCheck(name) {
  const idx = searchState.checked.indexOf(name)
  if (idx >= 0) searchState.checked.splice(idx, 1)
  else searchState.checked.push(name)
}
function submitSearch() {
  const items = searchState.results.filter((r) => searchState.checked.includes(r.name))
  const n = addSearched(searchState.group.key, items)
  closeSheet()
  toast(n ? `已加入 ${n} 項至「${searchState.group.name}」` : '未選擇任何項目')
}

// ── 文件檢視 / 案例 / 圖 / 版型 ──
const docView = reactive({ name: '', note: '', full: '', graph: false, loading: false })
async function viewDoc(it) {
  Object.assign(docView, { name: it.name, note: it.note || '', full: it.full || '', graph: !!it.graph, loading: false })
  openSheet('doc', {})
  // 沒有快取全文時即時打後端取：法規/案例 → getLaw/getDecision；產出草稿 → getArtifact
  if (!it.full && !it.graph && (it._libId || it._artifactId)) {
    docView.loading = true
    let html = ''
    if (it._artifactId) html = await viewArtifactFull(it._artifactId)
    else if (it.ext === '例') html = await viewDecisionFull(it._libId)
    else html = await viewLawFull(it._libId)
    docView.full = html
    docView.loading = false
  }
}
// 工具卡的案例卡。chat 的 hit **只有 §3.2 那幾個欄位**：`id` 是 RefBook 本回合編號
// （c1／c2…），不是母庫 id，所以**這裡打不到 §4.3 的全文端點**。要看全文得從右欄
// 「相關案例」開（那筆才帶得到母庫 id）。不要用 hit 拼一份看起來像全文的東西。
function viewCase(h) {
  const meta = [
    h.src ? '出處 ' + h.src : '',
    typeof h.score === 'number' ? '向量相似度 ' + Math.round(h.score * 100) + '%（非法律相似度）' : '',
    h.provenance ? '來源 ' + h.provenance : '來源未標示',
  ].filter(Boolean).join('　·　')
  Object.assign(docView, {
    name: h.t || h.id,
    note: meta,
    full:
      (h.note ? '<p>' + h.note + '</p>' : '') +
      '<p style="color:var(--muted);font-size:12.5px">本回合檢索命中的摘要資訊。全文請從右欄「相關案例」開啟——那裡才有母庫識別碼。</p>',
    graph: false,
    loading: false,
  })
  openSheet('doc', {})
}
// 匯出卡的「重新下載」：重跑一次匯出端點拿檔案，不是假裝下載。
function reExport(isPdf) {
  runTool(isPdf ? 'pdf' : 'doc', '', true)
}
function viewGraph(graph) {
  openSheet('graphBig', { graph })
}

// Esc 關閉
onMounted(() => {
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSheet()
  })
})

const moveFolders = computed(() => state.folders)
</script>

<template>
  <div class="app">
    <TopBar />
    <div class="workspace">
      <CaseTree @sheet="onSheetRequest" />
      <Chat
        @sheet="onSheetRequest"
        @view-case="viewCase"
        @view-graph="viewGraph"
        @export-download="reExport"
      />
      <FoldersRail @sheet="onSheetRequest" @view-doc="viewDoc" />
    </div>
  </div>

  <div class="scrim-rail" v-show="state.leftOpen || state.rightOpen" @click="closeRails"></div>

  <!-- ══ Sheets ══ -->
  <!-- prompt / rename -->
  <Sheet
    v-if="sheet.kind === 'prompt'"
    :title="sheet.data.title"
    :actions="[{ label: '取消', fn: closeSheet }, { label: '確定', pri: true, fn: submitPrompt }]"
    @close="closeSheet"
  >
    <label class="flabel">{{ sheet.data.label }}</label>
    <input class="field" v-model="promptVal" @keydown.enter="submitPrompt" v-focus />
  </Sheet>

  <!-- confirm -->
  <Sheet
    v-else-if="sheet.kind === 'confirm'"
    :title="sheet.data.title"
    :actions="[{ label: '取消', fn: closeSheet }, { label: sheet.data.confirmLabel, danger: true, fn: confirmOk }]"
    @close="closeSheet"
  >
    <p style="margin: 0; font-size: 13.5px; line-height: 1.75; color: var(--ink-2)">{{ sheet.data.body }}</p>
  </Sheet>

  <!-- move to folder -->
  <Sheet
    v-else-if="sheet.kind === 'move'"
    title="移至資料夾"
    :sub="sheet.data.case.name"
    :actions="[{ label: '完成', pri: true, fn: closeSheet }]"
    @close="closeSheet"
  >
    <template v-if="!moveFolders.length">
      <p style="font-size: 13px; color: var(--muted); margin: 0">尚未建立任何資料夾。請先於左下角點選「新增資料夾」。</p>
    </template>
    <template v-else>
      <label v-for="f in moveFolders" :key="f.id" class="pickrow">
        <input type="radio" name="mv" :checked="sheet.data.case.folderId === f.id" @change="moveTo(f.id)" />
        <span class="pt">{{ f.name }}</span>
      </label>
      <label class="pickrow">
        <input type="radio" name="mv" :checked="!sheet.data.case.folderId" @change="moveTo(null)" />
        <span class="pt">未分類</span>
      </label>
    </template>
  </Sheet>

  <!-- attach (附加至訊息) -->
  <Sheet
    v-else-if="sheet.kind === 'attach'"
    title="選擇卷證檔案"
    sub="本機檔案"
    :actions="[{ label: '取消', fn: closeSheet }, { label: '附加至訊息', pri: true, fn: submitAttach }]"
    @close="closeSheet"
  >
    <p><span style="color: var(--muted); font-size: 12.5px">原型展示：以下為模擬的本機卷證檔案，勾選後將附加至訊息並觸發解析卷證工具。</span></p>
    <p v-if="!pickState.items.length"><span style="color: var(--faint); font-size: 12.5px">示範檔案皆已上傳。</span></p>
    <label v-for="(f, i) in pickState.items" :key="i" class="pickrow">
      <input type="checkbox" :checked="pickState.checked.includes(i)" @change="toggleCheck(i)" />
      <span class="pt">{{ f.name }}<small>{{ f.note }}</small></span>
    </label>
  </Sheet>

  <!-- upload (直接入卷宗) -->
  <Sheet
    v-else-if="sheet.kind === 'upload'"
    title="上傳卷證檔案"
    sub="卷證檔案"
    :actions="[{ label: '取消', fn: closeSheet }, { label: '上傳', pri: true, fn: submitUpload }]"
    @close="closeSheet"
  >
    <p><span style="color: var(--muted); font-size: 12.5px">原型展示：以下為模擬的本機卷證檔案，勾選後直接上傳至卷宗。</span></p>
    <p v-if="!pickState.items.length"><span style="color: var(--faint); font-size: 12.5px">示範檔案皆已上傳。</span></p>
    <label v-for="(f, i) in pickState.items" :key="i" class="pickrow">
      <input type="checkbox" :checked="pickState.checked.includes(i)" @change="toggleCheck(i)" />
      <span class="pt">{{ f.name }}<small>{{ f.note }}</small></span>
    </label>
  </Sheet>

  <!-- search add -->
  <Sheet
    v-else-if="sheet.kind === 'search'"
    :title="'搜尋並加入' + searchState.group.name"
    :sub="searchState.group.name + '．後端資料庫'"
    :actions="[{ label: '取消', fn: closeSheet }, { label: '加入所選', pri: true, fn: submitSearch }]"
    @close="closeSheet"
  >
    <div class="ssearch">
      <input class="field" v-model="searchState.q" placeholder="輸入關鍵字搜尋，例如法條字號、案由、爭點⋯⋯" autocomplete="off" @input="onSearchInput" v-focus />
      <span v-if="searchState.searching" class="ssspin"><span class="spin"></span>搜尋中⋯⋯</span>
    </div>
    <div class="sslist" :class="{ searching: searchState.searching }">
      <p v-if="!searchState.q.trim()"><span style="color: var(--faint); font-size: 12.5px; display: block; padding: 14px 2px">輸入關鍵字開始搜尋⋯⋯</span></p>
      <p v-else-if="!searchState.results.length"><span style="color: var(--faint); font-size: 12.5px; display: block; padding: 14px 2px">沒有符合的結果，換個關鍵字試試。</span></p>
      <label v-for="p in searchState.results" :key="p.name" class="pickrow">
        <input type="checkbox" :checked="searchState.checked.includes(p.name)" @change="toggleSearchCheck(p.name)" />
        <span class="pt">{{ p.name }}<small>{{ p.note || '' }}</small></span>
      </label>
    </div>
  </Sheet>

  <!-- doc / case full text -->
  <Sheet
    v-else-if="sheet.kind === 'doc'"
    :title="docView.name"
    :sub="docView.note"
    :xwide="docView.graph"
    :actions="[{ label: '關閉', fn: closeSheet }]"
    @close="closeSheet"
  >
    <div v-if="docView.loading" class="typing" aria-label="載入全文中"><span></span><span></span><span></span></div>
    <div v-else v-html="docView.full"></div>
  </Sheet>

  <!-- graph big -->
  <Sheet v-else-if="sheet.kind === 'graphBig'" title="案件關聯圖" :sub="active() ? active().name : ''" xwide :actions="[{ label: '關閉', fn: closeSheet }]" @close="closeSheet">
    <RelationGraph v-if="sheet.data.graph" :graph="sheet.data.graph" />
  </Sheet>

  <div id="toast" role="status" aria-live="polite" :class="{ show: state.toastShow }">{{ state.toastMsg }}</div>
</template>
