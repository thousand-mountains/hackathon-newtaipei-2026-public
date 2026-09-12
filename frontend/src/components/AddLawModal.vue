<script setup>
import { ref, watch, nextTick } from 'vue'
import { lawSearch, addLaws } from '../store/workbench.js'
import { lawById } from '../data/lawdb.js'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['close', 'confirm-discard'])

const q = ref('')
const picked = ref([])
const results = ref([])
const searching = ref(false)
const searchInput = ref(null)
let timer = null

watch(
  () => props.open,
  (v) => {
    if (v) {
      picked.value = []
      q.value = ''
      results.value = []
      nextTick(() => setTimeout(() => searchInput.value?.focus(), 80))
    }
  },
)

watch(q, () => {
  clearTimeout(timer)
  if (!q.value.trim()) {
    searching.value = false
    results.value = []
    return
  }
  searching.value = true
  timer = setTimeout(() => {
    results.value = lawSearch(q.value)
    searching.value = false
  }, 350)
})

function toggle(id, checked) {
  if (checked) {
    if (!picked.value.includes(id)) picked.value.push(id)
  } else picked.value = picked.value.filter((x) => x !== id)
}
function unpick(id) {
  picked.value = picked.value.filter((x) => x !== id)
  results.value = lawSearch(q.value)
}
function ok() {
  const n = picked.value.length
  if (!n) return
  addLaws([...picked.value])
  picked.value = []
  emit('close')
}
function tryClose() {
  if (picked.value.length) {
    emit('confirm-discard', picked.value.length, () => {
      picked.value = []
      emit('close')
    })
    return
  }
  emit('close')
}
function preview(l) {
  return (l.lead || l.paras[0].t).slice(0, 54) + '…'
}
</script>

<template>
  <div class="mask" :class="{ on: open }" role="dialog" aria-modal="true" @click.self="tryClose">
    <div class="modal">
      <div class="mh"><span class="mi">library_add</span><h3>加入法條</h3></div>
      <div class="mb">
        <input ref="searchInput" v-model="q" type="search" placeholder="輸入法規名稱或條號，例：訴願 77、行政程序 96、洗錢" autocomplete="off" />
        <div style="margin-top: 12px">
          <p v-if="searching" style="font-size: 13px; color: var(--ink-3); text-align: center; padding: 22px">搜尋中…</p>
          <template v-else-if="!q.trim()">
            <p style="font-size: 13px; color: var(--ink-3); text-align: center; padding: 24px 10px; line-height: 1.9">
              輸入關鍵字開始搜尋<br />
              <span style="font-size: 12.5px">支援法規名稱與條號，例：<b>訴願 77</b>、<b>程序 96</b>、<b>廢棄物</b></span>
            </p>
          </template>
          <template v-else-if="results.length">
            <p style="font-size: 12.5px; color: var(--ink-3); margin: 0 0 8px">找到 {{ results.length }} 條</p>
            <label class="res" v-for="l in results" :key="l.id">
              <input type="checkbox" :checked="picked.includes(l.id)" @change="toggle(l.id, $event.target.checked)" />
              <span class="t"><b>{{ l.law }}　{{ l.art }}</b><span>{{ preview(l) }}</span></span>
              <span class="tag k">{{ l.tag }}</span>
            </label>
          </template>
          <p v-else style="font-size: 13px; color: var(--ink-3); text-align: center; padding: 20px">查無符合的法條。</p>
        </div>
        <div class="picked" v-show="picked.length" style="display: flex">
          <span style="font-size: 12.5px; color: var(--ink-3); align-self: center">已選 {{ picked.length }} 條：</span>
          <span class="pchip" v-for="id in picked" :key="id">
            {{ lawById(id).law }} {{ lawById(id).art }}<button @click="unpick(id)">×</button>
          </span>
        </div>
      </div>
      <div class="mf">
        <button class="btn ghost" @click="tryClose">取消</button>
        <button class="btn" :disabled="!picked.length" @click="ok">{{ picked.length ? '加入 ' + picked.length + ' 條' : '加入' }}</button>
      </div>
    </div>
  </div>
</template>
