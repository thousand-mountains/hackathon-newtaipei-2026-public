<script setup>
import { state, procKuanOptions, procMainOptions, procNote, mainPreview, onProcChange, linkToSentences } from '../store/workbench.js'

function panelClick(e) {
  // 點程序審查面板（非下拉/表單）→ 連結到主文
  if (e.target.closest('select,option,label,input,textarea')) return
  linkToSentences('proc')
}
</script>

<template>
  <div class="proc itm" id="ref-proc" :class="{ hit: state.hitRef === 'proc' }" @click="panelClick">
    <div class="ph">
      <span class="mi">gavel</span>
      <span class="ttl">程序審查</span>
      <span class="mi tip" tabindex="0" data-tip="程序審查結果直接決定決定書版式與主文用語。主文由規則查表產生，非模型自由生成，變更後右側草稿會即時重組。">info</span>
      <span class="gap"></span>
      <span class="tag n">版式 {{ state.PROC.con }}</span>
    </div>
    <div class="bd">
      <div class="fld">
        <label>審查結論</label>
        <select v-model="state.PROC.con" @change="onProcChange">
          <option value="A">程序不合 → 訴願不受理（77 條各款）</option>
          <option value="B">程序合法，應為實體審查 → 訴願無理由，駁回（79 I）</option>
          <option value="C">程序合法，應為實體審查 → 訴願有理由，撤銷／撤銷另處（81 I）</option>
          <option value="D">部分程序不合、部分無理由 → 部分不受理、部分駁回（77 條某款＋79 I）</option>
        </select>
      </div>
      <div class="fld" v-show="state.PROC.con === 'A'">
        <label>不受理款次（訴願法第 77 條）</label>
        <select v-model="state.PROC.kuan" @change="onProcChange">
          <option v-for="o in procKuanOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </div>
      <div class="fld" v-show="state.PROC.con === 'C'">
        <label>主文用語（撤銷型 6 種變體）</label>
        <select v-model.number="state.PROC.main" @change="onProcChange">
          <option v-for="o in procMainOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </div>
      <div class="fld" v-show="state.PROC.con === 'D'">
        <label>不受理部分款次（訴願法第 77 條）</label>
        <select v-model="state.PROC.dkuan" @change="onProcChange">
          <option v-for="o in procKuanOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </div>
      <div class="mainbox">
        <span class="lb">主　文</span>
        <span class="vl" v-html="mainPreview"></span>
      </div>
      <ul class="note">
        <li v-for="(x, i) in procNote" :key="i">{{ x }}</li>
      </ul>
      <div class="warn">
        <span class="mi">info</span>
        <span>變更審查結論會重新生成整份決定書的主文、理由與結構。</span>
      </div>
    </div>
  </div>
</template>
