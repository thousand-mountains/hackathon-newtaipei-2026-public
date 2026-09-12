<script setup>
import { computed } from 'vue'
import { state, lawLines, pcls, pickSent, clearSel, editSent, isSentSelected } from '../store/workbench.js'

const editing = computed(() => state.mode === 'edit')

function onSentClick(id) {
  pickSent(id)
}
function onSheetClick(e) {
  if (!e.target.closest('.sent')) clearSel()
}
function onSentInput(e, id) {
  editSent(id, e.target.textContent)
}
</script>

<template>
  <div class="editor" id="editorwrap" @click="onSheetClick">
    <div class="sheet" id="sheet" :class="editing ? 'edit' : 'view'">
      <template v-for="(b, bi) in state.DOC" :key="bi">
        <!-- 空行 -->
        <p v-if="b.ty === 'gap'" class="gapline">　</p>
        <!-- 抬頭（全國法規資料庫格式） + 標題 -->
        <template v-else-if="b.ty === 'title'">
          <div class="exfm">
            <div class="rw"><span class="lb">案號</span><span class="vl">1147101517</span></div>
            <div class="rw"><span class="lb">要旨</span><span class="vl">因違反洗錢防制法事件提起訴願</span></div>
            <div class="rw"><span class="lb">發文日期</span><span class="vl">民國 114 年 12 月 31 日</span></div>
            <div class="rw"><span class="lb">發文字號</span><span class="vl">新北府訴決字第 1142270177 號</span></div>
            <div class="rw">
              <span class="lb">相關法條</span>
              <span class="vl"><span class="ln" v-for="(x, i) in lawLines" :key="i">{{ x }}</span></span>
            </div>
            <div class="rw"><span class="lb">全文</span><span class="vl"></span></div>
          </div>
          <div class="doc-title"><span>新北市政府訴願決定書</span><span class="no">案號：1147101517 號</span></div>
        </template>
        <!-- meta 行 -->
        <p v-else-if="b.ty === 'meta'" class="meta">{{ b.text }}</p>
        <!-- 段落標題（主文／事實／理由） -->
        <h4 v-else-if="b.ty === 'h'">{{ b.text }}</h4>
        <!-- 一般段落（逐句） -->
        <p v-else :class="pcls(b)">
          <span
            v-for="s in b.ss"
            :key="s.id + '-' + (s.ver || 0)"
            class="sent"
            :data-id="s.id"
            :class="{ sel: isSentSelected(s.id) }"
            spellcheck="false"
            :contenteditable="editing"
            @click="onSentClick(s.id)"
            @input="onSentInput($event, s.id)"
          >{{ s.t }}</span>
        </p>
      </template>
    </div>
  </div>
</template>
