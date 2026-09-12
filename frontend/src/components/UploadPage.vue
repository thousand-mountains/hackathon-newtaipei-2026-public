<script setup>
import { ref, onMounted, computed } from 'vue'
import { state, probeBackend, availableCases, runCase, uploadAndRun, loadOfflineMock } from '../store/workbench.js'

const files = ref([])          // File 物件（真正要上傳的）
const hot = ref(false)
const fileInput = ref(null)
const cases = ref([])
const picked = ref('')

// **進頁面只探測 /api/health 與 /api/cases，絕不發 POST /runs。**
// 舊前端在 boot() 就自動跑一次六節點：每開一次／每重整一次燒一次 Opus、等約 60 秒。
onMounted(async () => {
  await probeBackend()
  if (state.conn.online) {
    cases.value = await availableCases()
    picked.value = cases.value.includes('synthetic-ordinary-01') ? 'synthetic-ordinary-01' : cases.value[0] || ''
  }
})

const running = computed(() => state.run.phase === 'running')
const failed = computed(() => state.run.phase === 'failed')
const err = computed(() => state.run.error)

function addFiles(list) {
  for (const f of list) files.value.push(f)
}
function onDrop(e) {
  hot.value = false
  addFiles([...(e.dataTransfer?.files || [])])
}
function onPick(e) {
  addFiles(e.target.files)
}
function removeAt(i) {
  files.value.splice(i, 1)
}
const sizeOf = (f) => Math.round(f.size / 1024) + ' KB'

/** 唯一會呼叫模型的動作。 */
function generate() {
  if (running.value) return
  if (files.value.length) uploadAndRun(files.value)
  else if (picked.value) runCase(picked.value)
}
</script>

<template>
  <section class="page">
    <div class="uwrap">
      <h1>上傳卷證</h1>
      <p class="sub">上傳訴願書、原處分書、送達證書與答辯書，確認後系統將呼叫後端生成決定書草稿。</p>

      <!-- 連線狀態：**連不上後端**與**這次跑失敗**是兩種狀態，文案不同 -->
      <div v-if="state.conn.checked && !state.conn.online" class="notice bad">
        <b>未連上後端</b>
        <div>{{ state.conn.error }}</div>
        <div class="hint">
          可以載入離線示範資料瀏覽畫面，但那是內建的假資料、<b>不是這次執行的結果</b>，且不提供下載。
        </div>
        <button class="btn ghost sm" @click="loadOfflineMock">載入離線示範資料（明示為假資料）</button>
      </div>

      <div v-else-if="state.conn.checked && state.conn.online" class="notice ok">
        <b>已連上後端</b>
        <span v-if="state.provenance">
          　·　{{ state.provenance.runMode }} 檔位　·　檢索 {{ state.provenance.retriever }}
        </span>
        <div v-if="state.provenance" class="hint">{{ state.provenance.execution }}</div>
      </div>

      <!-- run 失敗：**後端活著、只是這次跑失敗**。不得說成離線、不得載入 mock 演完全程。 -->
      <div v-if="failed" class="notice bad">
        <b v-if="err.isOffline">無法連上後端</b>
        <b v-else>後端已連上，但這次執行失敗</b>
        <div class="errmsg">{{ err.message }}</div>
        <div v-if="err.node" class="hint">失敗節點：<code>{{ err.node }}</code></div>
        <div class="hint">
          <b>畫面沒有顯示任何草稿，是因為這次真的沒有產出。</b>系統不會拿內建示範資料頂替。
        </div>
      </div>

      <div
        class="drop"
        :class="{ hot }"
        tabindex="0"
        role="button"
        aria-label="上傳卷證檔案"
        @click="fileInput.click()"
        @dragenter.prevent="hot = true"
        @dragover.prevent="hot = true"
        @dragleave.prevent="hot = false"
        @drop.prevent="onDrop"
      >
        <span class="mi" style="font-size: 44px; color: var(--navy); opacity: 0.5">upload_file</span>
        <div class="big">拖曳檔案至此，或點擊選擇</div>
        <div class="sm">PDF／TXT，單檔 20MB</div>
        <input ref="fileInput" type="file" multiple accept=".pdf,.txt" hidden @change="onPick" />
      </div>

      <ul class="files">
        <li v-for="(f, i) in files" :key="i" :style="{ animationDelay: i * 80 + 'ms' }">
          <span class="mi doc">description</span>
          <span class="meta"><b>{{ f.name }}</b><span>{{ sizeOf(f) }}</span></span>
          <button aria-label="移除" @click="removeAt(i)">×</button>
        </li>
      </ul>

      <div v-if="!files.length && cases.length" class="picker">
        <label>或選擇後端既有案例</label>
        <select v-model="picked">
          <option v-for="c in cases" :key="c" :value="c">{{ c }}</option>
        </select>
      </div>

      <div class="ubar">
        <div class="gap"></div>
        <span v-if="running" style="font-size: 13px; color: var(--ink-3)">
          {{ state.run.progress || '執行中…' }}
          <span v-if="state.run.nodes.length">（已完成 {{ state.run.nodes.length }} 個節點）</span>
        </span>
        <span v-else style="font-size: 13px; color: var(--ink-3)">
          {{ files.length ? files.length + ' 份卷證待解析' : (picked ? '將執行 ' + picked : '請先上傳卷證') }}
        </span>
        <button class="btn" :disabled="running || (!files.length && !picked)" @click="generate">
          {{ running ? '生成草稿中…（約 1 分鐘）' : '確認上傳，生成草稿 →' }}
        </button>
      </div>
      <p v-if="!running" class="sub" style="margin-top: 8px; font-size: 12.5px">
        按下按鈕才會呼叫模型。開啟或重新整理本頁不會觸發任何執行。
      </p>
    </div>
  </section>
</template>

<style scoped>
.notice { border-radius: 8px; padding: 12px 14px; margin: 12px 0; font-size: 13.5px; line-height: 1.8; }
.notice.ok { background: #f0f5f0; border: 1px solid #cfe0cf; }
.notice.bad { background: #fdf3f2; border: 1px solid #e9c9c5; }
.notice .hint { color: var(--ink-3); font-size: 12.5px; margin-top: 4px; }
.notice .errmsg { font-family: var(--mono); font-size: 12.5px; background: #fff; border: 1px solid #e6d6d3; border-radius: 5px; padding: 8px 10px; margin-top: 6px; white-space: pre-wrap; word-break: break-all; }
.picker { display: flex; align-items: center; gap: 10px; margin: 10px 0; font-size: 13px; }
.picker select { padding: 6px 8px; }
</style>
