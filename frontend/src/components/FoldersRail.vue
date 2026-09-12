<script setup>
import { state, active, GROUPS, folderTotal, removeDoc } from '../store/app.js'
import { IconUpload } from './icons.js'

const emit = defineEmits(['sheet', 'view-doc'])

function toggle(key) {
  state.collapsed[key] = !state.collapsed[key]
}
// 卷證檔案沒有 full 內容時，臨時組一份檔案資訊預覽供點擊檢視
function withDetail(it, g) {
  if (it.full || it.graph) return it
  const ext = (it.ext || g.abbr || '').toUpperCase()
  const full =
    `<dl class="kv"><dt>檔名</dt><dd>${it.name}</dd>` +
    `<dt>類型</dt><dd class="mono">${ext}</dd>` +
    (it.note ? `<dt>說明</dt><dd>${it.note}</dd>` : '') +
    `</dl>` +
    `<p style="color:var(--muted);font-size:12.5px;border-top:1px solid var(--line-soft);padding-top:10px;margin-top:12px">原型展示：此為模擬卷證檔案，正式系統於此顯示 OCR 全文與採證影像。</p>`
  return { ...it, full }
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
          <div v-if="!active().docs[g.key].length" class="fempty">{{ g.empty }}</div>
          <div v-for="(it, i) in active().docs[g.key]" :key="i" class="fitem" :class="{ 'fitem-new': it._new }">
            <span class="ic">{{ it.ext || g.abbr }}</span>
            <button v-if="it.full || it.graph || g.key === 'evidence'" class="ft" @click="emit('view-doc', withDetail(it, g))">
              {{ it.name }}<small v-if="it.note">{{ it.note }}</small>
            </button>
            <div v-else class="ft">{{ it.name }}<small v-if="it.note">{{ it.note }}</small></div>
            <button class="rm" title="移除" @click="removeDoc(g.key, i)">×</button>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>
