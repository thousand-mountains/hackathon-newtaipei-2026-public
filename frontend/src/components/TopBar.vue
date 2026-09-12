<script setup>
import { computed } from 'vue'
import { state, toggleTheme, currentTheme } from '../store/app.js'

const dark = computed(() => (state.theme || currentTheme()) === 'dark')
const themeTitle = computed(() => (dark.value ? '切換為淺色' : '切換為深色'))
</script>

<template>
  <header class="topbar">
    <button class="icon-btn" aria-label="案件清單" @click="state.leftOpen = !state.leftOpen">☰</button>
    <div class="brand">
      <span class="mark" aria-hidden="true">
        <i style="border-color: #ffd400"></i><i style="border-color: #e5312b"></i><i style="border-color: #5aa84f"></i><i style="border-color: #2f7fbf"></i>
      </span>
      <span class="seal">新北市政府法制局</span>
      <span class="sys">訴願智慧輔助平台</span>
    </div>
    <span class="spacer"></span>
    <button class="theme-btn" :title="themeTitle" :aria-label="themeTitle" :aria-pressed="String(dark)" @click="toggleTheme">
      <svg class="tsw" viewBox="0 0 24 24" width="21" height="21" aria-hidden="true">
        <mask id="crescent" maskUnits="userSpaceOnUse" x="0" y="0" width="24" height="24">
          <rect width="24" height="24" fill="#fff" />
          <circle class="bite" cx="17.6" cy="8.4" r="6.6" fill="#000" />
        </mask>
        <g class="rays" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">
          <path d="M12 2.2v2.1M12 19.7v2.1M2.2 12h2.1M19.7 12h2.1M5.1 5.1l1.5 1.5M17.4 17.4l1.5 1.5M18.9 5.1l-1.5 1.5M6.6 17.4l-1.5 1.5" />
        </g>
        <circle class="disc" cx="12" cy="12" r="5.5" fill="currentColor" mask="url(#crescent)" />
      </svg>
    </button>
    <span class="demo-badge">原型展示 · 示範資料</span>
    <button class="icon-btn" aria-label="案件卷宗" @click="state.rightOpen = !state.rightOpen">▤</button>
  </header>
</template>
