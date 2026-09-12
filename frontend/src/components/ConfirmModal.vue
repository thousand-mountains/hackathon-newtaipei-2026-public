<script setup>
defineProps({ open: Boolean, title: String, body: String, okText: String })
const emit = defineEmits(['ok', 'cancel'])
</script>

<template>
  <div class="mask" :class="{ on: open }" role="dialog" aria-modal="true" @click.self="emit('cancel')">
    <div class="modal" style="max-width: 430px; border-top-color: var(--seal)">
      <div class="mh"><span class="mi" style="color: var(--seal)">warning</span><h3>{{ title }}</h3></div>
      <div class="mb"><p style="margin: 0" v-html="body"></p></div>
      <div class="mf">
        <button class="btn ghost" @click="emit('cancel')">取消</button>
        <button class="btn" @click="emit('ok')">{{ okText || '確定' }}</button>
      </div>
    </div>
  </div>
</template>
