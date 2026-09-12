<script setup>
import { ref, computed, nextTick } from 'vue'
import { state, active, chips, TOOLS, runTool, send, removePending } from '../store/app.js'

const emit = defineEmits(['sheet', 'search'])

const text = ref('')
const ta = ref(null)
const slashOpen = ref(false)
const slashSel = ref(0)

const slashItems = computed(() => {
  if (!slashOpen.value) return []
  const filter = text.value.length > 1 ? text.value : ''
  return TOOLS.filter((t) => !filter || t.cmd.includes(filter) || t.name.includes(filter))
})

// 送出鍵：對話框有文字或已附檔、且非忙碌時才可按
const canSend = computed(() => !state.busy && (text.value.trim().length > 0 || state.pendingFiles.length > 0))

function autosize() {
  const n = ta.value
  if (!n) return
  n.style.height = 'auto'
  n.style.height = Math.min(n.scrollHeight, 150) + 'px'
}
function onInput() {
  autosize()
  if (text.value.startsWith('/')) {
    slashOpen.value = true
    slashSel.value = 0
  } else slashOpen.value = false
}
function fillSlash(t) {
  text.value = t.cmd + ' '
  slashOpen.value = false
  nextTick(() => {
    ta.value.focus()
    autosize()
  })
}
function onKeydown(e) {
  if (slashOpen.value && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
    e.preventDefault()
    const len = slashItems.value.length
    slashSel.value = (slashSel.value + (e.key === 'ArrowDown' ? 1 : -1) + len) % len
    return
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    if (e.repeat) return
    if (slashOpen.value && slashItems.value.length) {
      fillSlash(slashItems.value[slashSel.value] || slashItems.value[0])
      return
    }
    doSend()
  }
}
async function doSend() {
  if (state.busy) return
  const v = text.value
  text.value = ''
  nextTick(autosize)
  slashOpen.value = false
  await send(v)
}
function onChip(chip) {
  const a = chip.action
  if (a.t === 'tool') runTool(a.id, a.arg)
  else if (a.t === 'attach') emit('sheet', { kind: 'attach' })
  else if (a.t === 'search') emit('sheet', { kind: 'search', groupKey: a.key })
  else if (a.t === 'ask') send('你會什麼？')
}
</script>

<template>
  <div class="composer-wrap">
    <div class="composer-inner">
      <div class="chips">
        <template v-for="(chip, i) in chips" :key="i">
          <span v-if="chip.ghost" class="chip ghost">{{ chip.label }}</span>
          <button v-else class="chip" @click="onChip(chip)">
            <span v-if="chip.lead" class="lead">{{ chip.lead }}</span>{{ chip.label }}
          </button>
        </template>
      </div>
      <div class="composer">
        <div class="slash" :class="{ open: slashOpen && slashItems.length }">
          <div class="slash-h">可呼叫的工具</div>
          <div class="slash-list">
            <button
              v-for="(t, i) in slashItems"
              :key="t.id"
              class="slash-item"
              :class="{ sel: i === slashSel }"
              @click="fillSlash(t)"
              @mouseenter="slashSel = i"
            >
              <span class="cmd">{{ t.cmd }}</span>
              <span class="d">{{ t.name }}<small>{{ t.desc }}</small></span>
            </button>
          </div>
        </div>
        <div class="pending">
          <span v-for="(f, i) in state.pendingFiles" :key="i" class="file-chip">
            <span class="ext">{{ f.ext }}</span>{{ f.name }}<button aria-label="移除" @click="removePending(i)">×</button>
          </span>
        </div>
        <div class="composer-row">
          <button class="cbtn" :class="{ hintme: !active().docs.evidence.length }" title="上傳卷證或相關檔案" aria-label="上傳卷證" @click="emit('sheet', { kind: 'attach' })">＋</button>
          <textarea id="input" ref="ta" v-model="text" rows="1" placeholder="輸入指令，或以「/」呼叫工具⋯⋯" @input="onInput" @keydown="onKeydown"></textarea>
          <button class="cbtn send" aria-label="送出" :disabled="!canSend" @click="doSend">↑</button>
        </div>
      </div>
      <div class="hint">按 <code>/</code> 列出工具　·　也可直接輸入「你會什麼」查看全部 API</div>
    </div>
  </div>
</template>
