<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'

const tip = ref(null)
const on = ref(false)
const style = ref({ left: '0px', top: '0px' })
let cur = null

function place(el) {
  cur = el
  tip.value = el.dataset.tip
  on.value = true
  requestAnimationFrame(() => {
    const node = document.getElementById('tt')
    if (!node) return
    const r = el.getBoundingClientRect()
    const w = node.offsetWidth
    const hg = node.offsetHeight
    let x = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, innerWidth - w - 8))
    let y = r.bottom + 7
    if (y + hg > innerHeight - 8) y = r.top - hg - 7
    style.value = { left: x + 'px', top: y + 'px' }
  })
}
function hide() {
  cur = null
  on.value = false
}
function onOver(e) {
  const el = e.target.closest('[data-tip]')
  if (el && el !== cur) place(el)
  else if (!el && cur) hide()
}
function onFocusIn(e) {
  const el = e.target.closest('[data-tip]')
  if (el) place(el)
}

onMounted(() => {
  document.addEventListener('mouseover', onOver)
  document.addEventListener('focusin', onFocusIn)
  document.addEventListener('focusout', hide)
  window.addEventListener('scroll', hide, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('mouseover', onOver)
  document.removeEventListener('focusin', onFocusIn)
  document.removeEventListener('focusout', hide)
  window.removeEventListener('scroll', hide, true)
})
</script>

<template>
  <div id="tt" :class="{ on }" :style="style">{{ tip }}</div>
</template>
