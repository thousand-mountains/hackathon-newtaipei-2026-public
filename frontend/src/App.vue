<script setup>
import { state } from './store/workbench.js'
import UploadPage from './components/UploadPage.vue'
import Workbench from './components/Workbench.vue'
import TheTooltip from './components/TheTooltip.vue'
</script>

<template>
  <header class="top">
    <div class="in">
      <span class="mi crest">account_balance</span>
      <div class="brand">
        <div class="org">新北市政府法制局　訴願審議會</div>
        <div class="sys">訴願決定書 AI 輔助撰擬系統</div>
      </div>
      <div class="sp"></div>
      <div class="who">承辦人<b>法制局 訴願科　林○玲</b></div>
    </div>
  </header>

  <main>
    <UploadPage v-if="state.page === 'up'" />
    <Workbench v-else />
  </main>

  <TheTooltip />
  <div class="toast" :class="{ on: state.toastOn }">{{ state.toastMsg }}</div>
</template>
