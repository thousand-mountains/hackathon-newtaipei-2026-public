<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import {
  state, active, boot, toast, CASE_NO,
  availableEvidence, attachToPending, uploadToFolder,
  searchPool, addSearched, deleteCase, deleteFolder,
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
const searchState = reactive({ q: '', results: [], searching: false, checked: [], pool: [], have: null, group: null })
let searchDeb = null

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
    const { pool, have } = searchPool(g.key)
    Object.assign(searchState, { q: '', results: [], searching: false, checked: [], pool, have, group: g })
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
  searchDeb = setTimeout(() => {
    const term = searchState.q.trim().toLowerCase()
    const avail = searchState.pool.filter((p) => !searchState.have.has(p.name))
    searchState.results = avail.filter((p) => p.kw.includes(term))
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
const docView = reactive({ name: '', note: '', full: '', graph: false })
function viewDoc(it) {
  Object.assign(docView, { name: it.name, note: it.note || '', full: it.full || '', graph: !!it.graph })
  openSheet('doc', {})
}
function viewCase(k) {
  Object.assign(docView, { name: k.no + '　' + k.title, note: k.type + '．' + k.verdict, full: k.full, graph: false })
  openSheet('doc', {})
}
function viewGraph() {
  openSheet('graphBig', {})
}
function previewPaper(isPdf) {
  openSheet('paper', { isPdf })
}
function exportDownload() {
  toast('原型展示：正式系統將於此下載檔案')
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
        @preview-paper="previewPaper"
        @export-download="exportDownload"
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
    <p><span style="color: var(--muted); font-size: 12.5px">原型展示：以下為模擬的本機卷證檔案，勾選後將附加至訊息並觸發萃取工具。</span></p>
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
    <RelationGraph v-if="docView.graph" />
    <div v-else v-html="docView.full"></div>
  </Sheet>

  <!-- graph big -->
  <Sheet v-else-if="sheet.kind === 'graphBig'" title="案件關聯圖" :sub="'案號 ' + CASE_NO" xwide :actions="[{ label: '關閉', fn: closeSheet }]" @close="closeSheet">
    <RelationGraph />
  </Sheet>

  <!-- paper preview -->
  <Sheet
    v-else-if="sheet.kind === 'paper'"
    :title="sheet.data.isPdf ? 'PDF 版型預覽' : 'Word 版型預覽'"
    sub="訴願決定書．標準版型"
    wide
    :actions="[{ label: '關閉', fn: closeSheet }, { label: '下載檔案', pri: true, fn: () => { closeSheet(); toast('原型展示：正式系統將於此下載檔案') } }]"
    @close="closeSheet"
  >
    <div class="paper-scroll">
      <div class="paper" style="min-width: 520px">
        <div class="ph">新北市政府訴願決定書</div>
        <div class="pno">案號：{{ CASE_NO }} 號</div>
        <dl class="prow"><dt>訴願人</dt><dd>吉○實業有限公司</dd></dl>
        <dl class="prow"><dt>代表人</dt><dd>陳○德</dd></dl>
        <dl class="prow"><dt>原處分機關</dt><dd>新北市政府環境保護局</dd></dl>
        <p style="margin-top: 12px">上列訴願人因違反廢棄物清理法事件，不服原處分機關民國 114 年 5 月 20 日新北環稽字第 1140876543 號裁處書所為之處分，提起訴願一案，本府依法決定如下：</p>
        <div class="plabel">主文</div>
        <p>原處分撤銷，由原處分機關於 2 個月內另為適法之處分。</p>
        <div class="plabel">事實</div>
        <p class="indent">緣訴願人於本市林口區○○路 88 號設廠從事塑膠製品製造，為原處分機關列管之事業。原處分機關於民國（下同）114 年 4 月 9 日派員前往稽查，查得廠區東側露天堆置未分類之廢塑膠混合物及廢木材約 12 立方公尺⋯⋯（略）</p>
        <div class="plabel">理由</div>
        <p class="indent">一、按廢棄物清理法第 36 條第 1 項規定：「事業廢棄物之貯存、清除或處理方法及設施，應符合中央主管機關之規定。」⋯⋯</p>
        <p class="indent">五、惟查，遍查全卷，並無原處分機關依行政程序法第 102 條規定通知訴願人陳述意見之相關文書可稽⋯⋯</p>
        <p class="indent">七、綜上論結，本件訴願為有理由，依訴願法第 81 條第 1 項規定，決定如主文。</p>
        <div class="sig">
          訴願審議委員會主任委員　蔡○榕<br />
          委員　陳○燦　　委員　陳○夫　　委員　張○郁<br />
          委員　蔡○良　　委員　黃○銘　　委員　劉○德<br />
          委員　景○鳳　　委員　王○芸　　委員　李○裕
        </div>
        <div class="foot">如不服本決定，得於決定書送達之次日起 2 個月內向臺北高等行政法院（地址：臺北市士林區福國路 101 號）提起行政訴訟。<br /><br />中華民國 114 年 8 月 15 日</div>
      </div>
    </div>
  </Sheet>

  <div id="toast" role="status" aria-live="polite" :class="{ show: state.toastShow }">{{ state.toastMsg }}</div>
</template>
