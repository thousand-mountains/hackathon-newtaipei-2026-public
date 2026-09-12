<script setup>
import { ref, watch, nextTick, computed } from 'vue'
import { state, CHIP_LABEL, chatChip, chatSend, selectedSentText } from '../store/workbench.js'

const input = ref('')
const chatBox = ref(null)
const chips = [
  { q: '精簡', label: '精簡' },
  { q: '加強', label: '加強論述' },
  { q: '回應', label: '補強對訴願主張之回應' },
  { q: '語氣', label: '改為公文正式語氣' },
]

const disabled = computed(() => state.mode !== 'edit')

const ctxLabel = computed(() => {
  if (!state.chatAll && state.selId) return null // 顯示選取句
  return '全文'
})

function send() {
  const v = input.value.trim()
  if (!v) return
  chatSend(v)
  input.value = ''
}
function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}
function toAll() {
  state.chatAll = true
}

watch(
  () => state.chat.length,
  () => nextTick(() => { if (chatBox.value) chatBox.value.scrollTop = 1e6 }),
)
</script>

<template>
  <div class="pane">
    <div class="hd">
      <span class="ttl">撰稿 AI</span>
      <span class="mi tip" tabindex="0" data-tip="只做文字層面的潤飾與改寫，不新增法律依據。要增刪法條請用左上角的「加入法條」。">info</span>
    </div>
    <div class="chatctx">
      <template v-if="!state.chatAll && state.selId">
        對象：<b>「{{ selectedSentText.slice(0, 12) }}…」</b>
        <button class="btn ghost sm" style="margin-left: auto; padding: 2px 9px" @click="toAll">改為全文</button>
      </template>
      <template v-else>
        對象：<b>全文</b>
        <span>　·　點右側任一句可只改該句</span>
      </template>
    </div>
    <div class="chips">
      <button v-for="c in chips" :key="c.q" :disabled="disabled" @click="chatChip(c.q)">{{ c.label }}</button>
    </div>
    <div class="chat" ref="chatBox">
      <div v-for="(m, i) in state.chat" :key="i" class="msg" :class="m.who" v-html="m.html"></div>
    </div>
    <div class="chatbar">
      <textarea v-model="input" rows="1" :disabled="disabled" placeholder="例：這句太長，拆成兩句" @keydown="onKeydown"></textarea>
      <button class="btn sm" style="padding: 9px 12px" :disabled="disabled" @click="send">
        <span class="mi" style="font-size: 17px">send</span>
      </button>
    </div>
  </div>
</template>
