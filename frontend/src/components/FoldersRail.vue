<script setup>
import { state, active, GROUPS, folderTotal, removeDoc } from '../store/app.js'
import { IconUpload } from './icons.js'

const emit = defineEmits(['sheet', 'view-doc'])

function toggle(key) {
  state.collapsed[key] = !state.collapsed[key]
}
// 卷證檔案沒有 full 內容時，臨時組一份檔案資訊預覽供點擊檢視。
// 這裡**只講後端說過的事**：檔名、副檔名、後端給的 note、以及 readable。
// 不做 OCR，所以不在這裡假裝有全文（契約 §6「已上傳，可直接改」那一列）。
function withDetail(it, g) {
  if (it.full || it.graph) return it
  const ext = (it.ext || g.abbr || '').toUpperCase()
  const state =
    it._unsynced
      ? `<p style="color:var(--alert);font-size:12.5px;border-top:1px solid var(--line-soft);padding-top:10px;margin-top:12px"><b>尚未同步到後端</b>：${it._unsynced}。這份卷證只存在於畫面上，不會進入抽取。</p>`
      : it.readable === false
        ? `<p style="color:var(--alert);font-size:12.5px;border-top:1px solid var(--line-soft);padding-top:10px;margin-top:12px"><b>後端判定無法辨讀</b>：${it.note || '後端未說明原因'}。目前不做 OCR，這份卷證抽不出文字。</p>`
        : `<p style="color:var(--muted);font-size:12.5px;border-top:1px solid var(--line-soft);padding-top:10px;margin-top:12px">此處只顯示卷證的檔案資訊。抽取出來的內容請看「解析卷證檔案」的產出。</p>`
  const full =
    `<dl class="kv"><dt>檔名</dt><dd>${it.name}</dd>` +
    `<dt>類型</dt><dd class="mono">${ext}</dd>` +
    (it.note ? `<dt>說明</dt><dd>${it.note}</dd>` : '') +
    `</dl>` +
    state
  return { ...it, full }
}
// 契約 §3.5.2 的三態。`unknown`（還沒跑過草稿）刻意不標——
// 「沒查過」與「查不到」是兩件事，合成一個值會讓剛加入的法規當場被標成「沒用到」。
const PICK_LABEL = {
  matched: '已進法條查表',
  query_only: '僅用於相似案檢索',
  unused: '本次未送進檢索',
}

// 有沒有要顯示的第二行。readable:false 一定要標出來，即使後端沒給 note——
// 靜默把讀不到的卷證畫成正常卷證，承辦人會以為它的內容有進草稿。
function hasSub(it) {
  return !!(it.note || it.status || it._unsynced || it.readable === false)
}

function headAdd(g) {
  if (g.key === 'evidence') emit('sheet', { kind: 'upload' })
  else emit('sheet', { kind: 'search', group: g })
}
</script>

<template>
  <aside class="rail rail-right">
    <div class="rail-head">
      <span class="rail-title">案件卷宗</span>
      <span class="spacer"></span>
      <span class="rail-title" style="letter-spacing: 0.04em">{{ folderTotal ? folderTotal + ' 項' : '' }}</span>
    </div>
    <div class="rail-scroll">
      <div v-for="g in GROUPS" :key="g.key" class="fgroup" :class="{ collapsed: state.collapsed[g.key] }" :data-key="g.key">
        <div class="fgroup-h" @click="toggle(g.key)">
          <span class="tw">▼</span>
          <span class="gn">{{ g.name }}</span>
          <span class="gc">{{ active().docs[g.key].length }}</span>
          <button
            v-if="g.key !== 'out'"
            class="add"
            :class="{ upload: g.key === 'evidence' }"
            :title="g.key === 'evidence' ? '上傳卷證檔案' : '搜尋並加入'"
            @click.stop="headAdd(g)"
          >
            <IconUpload v-if="g.key === 'evidence'" />
            <template v-else>＋</template>
          </button>
        </div>
        <div class="fgroup-body">
          <div v-if="g.key === 'cases' && active().docs[g.key].length" class="gnote">參考資料，不影響草稿生成</div>
          <div v-if="!active().docs[g.key].length" class="fempty">{{ g.empty }}</div>
          <div v-for="(it, i) in active().docs[g.key]" :key="i" class="fitem" :class="{ 'fitem-new': it._new }">
            <span class="ic">{{ it.ext || g.abbr }}</span>
            <button v-if="it.full || it.graph || it._libId || it._artifactId || g.key === 'evidence'" class="ft" @click="emit('view-doc', withDetail(it, g))">
              {{ it.name }}<small v-if="hasSub(it)"><span v-if="it._unsynced" class="tag alert">未同步後端</span><span v-else-if="it.readable === false" class="tag alert">無法辨讀</span><span v-if="PICK_LABEL[it.status]" class="tag">{{ PICK_LABEL[it.status] }}</span>{{ it.note }}</small>
            </button>
            <div v-else class="ft">{{ it.name }}<small v-if="hasSub(it)"><span v-if="it._unsynced" class="tag alert">未同步後端</span><span v-else-if="it.readable === false" class="tag alert">無法辨讀</span><span v-if="PICK_LABEL[it.status]" class="tag">{{ PICK_LABEL[it.status] }}</span>{{ it.note }}</small></div>
            <button class="rm" title="移除" @click="removeDoc(g.key, i)">×</button>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>
