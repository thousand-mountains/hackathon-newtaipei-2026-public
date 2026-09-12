<script setup>
defineProps({ title: String, sub: String, wide: Boolean, xwide: Boolean, actions: { type: Array, default: () => [] } })
const emit = defineEmits(['close'])
</script>

<template>
  <div class="scrim" @click.self="emit('close')">
    <div class="sheet" :class="{ wide, xwide }">
      <div class="sheet-h">
        <h3>{{ title }}</h3>
        <span class="sub">{{ sub }}</span>
        <button class="x" aria-label="關閉" @click="emit('close')">×</button>
      </div>
      <div class="sheet-b"><slot /></div>
      <div class="sheet-f">
        <button v-for="(a, i) in actions" :key="i" class="btn" :class="{ danger: a.danger, pri: a.pri }" @click="a.fn">{{ a.label }}</button>
      </div>
    </div>
  </div>
</template>
