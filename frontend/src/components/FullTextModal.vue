<script setup>
import { nextTick, watch } from 'vue'

const props = defineProps({
  open: Boolean,
  title: String,
  meta: String, // 右上角資訊（html）
  body: String, // 內容 html
  isCase: Boolean,
})
const emit = defineEmits(['close'])

watch(
  () => props.open,
  (v) => {
    if (v && !props.isCase) {
      nextTick(() => {
        setTimeout(() => {
          const cur = document.querySelector('#fullbody .artfocus')
          if (cur) cur.scrollIntoView({ block: 'center' })
        }, 60)
      })
    }
  },
)
</script>

<template>
  <div class="mask" :class="{ on: open }" role="dialog" aria-modal="true" @click.self="emit('close')">
    <div class="modal" style="max-width: 800px">
      <div class="mh">
        <span class="mi">article</span>
        <h3>{{ title }}</h3>
        <span class="mi tip" tabindex="0" data-tip="黃底處為 AI 判定與本案相似／援用的關鍵段落。">info</span>
        <span style="flex: 1"></span>
        <span class="sim" v-html="meta"></span>
      </div>
      <div class="mb"><div id="fullbody" :class="isCase ? 'casedoc' : ''" v-html="body"></div></div>
      <div class="mf"><button class="btn" @click="emit('close')">關閉</button></div>
    </div>
  </div>
</template>
