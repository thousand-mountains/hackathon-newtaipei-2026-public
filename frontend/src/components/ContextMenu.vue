<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'

const open = ref(false)
const items = ref([])
const pos = ref({ left: '0px', top: '0px' })
const menu = ref(null)

function show(e, list) {
  items.value = list
  open.value = true
  nextTick(() => {
    const w = menu.value?.offsetWidth || 160
    const h = menu.value?.offsetHeight || 120
    pos.value = {
      left: Math.min(e.clientX, innerWidth - w - 8) + 'px',
      top: Math.min(e.clientY, innerHeight - h - 8) + 'px',
    }
  })
}
function close() {
  open.value = false
}
function run(it) {
  close()
  it.fn()
}
function onDocClick() {
  close()
}
function onScroll() {
  close()
}
onMounted(() => {
  document.addEventListener('click', onDocClick)
  window.addEventListener('scroll', onScroll, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  window.removeEventListener('scroll', onScroll, true)
})
defineExpose({ show })
</script>

<template>
  <div ref="menu" class="ctxmenu" :class="{ open }" :style="pos">
    <template v-for="(it, i) in items" :key="i">
      <hr v-if="it === '-'" />
      <button v-else :class="{ danger: it.danger }" @click="run(it)">{{ it.label }}</button>
    </template>
  </div>
</template>
