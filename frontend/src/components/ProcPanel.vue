<script setup>
import {
  state, procKuanOptions, procMainOptions, procNote, mainPreview,
  onProcChange, linkToSentences, procedure, procConclusionOrigin,
} from '../store/workbench.js'

import { computed } from 'vue'

const cc = computed(() => procedure.value && procedure.value.crossCheck)
// **badge 必須讀 `compared`，不能只讀 `agree`。**
// 對抗式稽核實測：案件剛進來、還沒填提起日時兩套引擎都沒判逾期，
// 舊版把它折算成 `agree=true` 並亮綠燈說「兩套引擎一致」——一次比對都沒做。
// 那是這個系統最常見的畫面上的假綠燈。所以先看「比過什麼」再看「結果如何」。
const ccLabel = computed(() => {
  const c = cc.value
  if (!c) return ''
  if ((c.sharedBlindSpot || []).length) return '兩套共用同一盲點，一致不足採信'
  const cmp = c.compared || []
  if (!cmp.length) return '尚未比對'
  if (c.agree === true) return '兩套引擎一致'
  if (c.agree === false) return '兩套引擎不一致'
  if (cmp.includes('deadline')) return '屆滿日一致，是否逾期尚未判定'
  return '無法判定是否一致'
})
const ccClass = computed(() => {
  const c = cc.value
  if (!c) return ''
  // **共用盲點優先於一切**：compared 可能是滿的、agree 甚至可能是 true，
  // 但那個一致沒有證據力（兩套用同一份錯誤知識）。**絕不給綠。**
  if ((c.sharedBlindSpot || []).length) return 'blind'
  if (!(c.compared || []).length) return 'none'   // 沒比過 → 不給顏色，絕不給綠
  return c.agree === true ? 'ok' : c.agree === false ? 'bad' : 'unknown'
})
/** 第二意見的逐步算式（可驗算層的實質內容，demo 主秀之一）。 */
const second = computed(() => (cc.value && cc.value.engines && cc.value.engines.second_opinion) || null)
function ccText(d) {
  if (typeof d === 'string') return d
  return d.why || d.note || d.field || JSON.stringify(d)
}

function panelClick(e) {
  if (e.target.closest('select,option,label,input,textarea,button')) return
  linkToSentences('proc')
}
</script>

<template>
  <div class="proc itm" id="ref-proc" :class="{ hit: state.hitRef === 'proc' }" @click="panelClick">
    <div class="ph">
      <span class="mi">gavel</span>
      <span class="ttl">程序審查</span>
      <span class="gap"></span>
      <span class="tag n">版式 {{ state.PROC.con }}</span>
    </div>
    <div class="bd">
      <!-- 期間計算：規則引擎輸出，零模型、同輸入必同輸出（可驗算層） -->
      <div v-if="procedure" class="engine">
        <div class="ehd">
          <span class="mi" style="font-size: 15px; vertical-align: -3px">calculate</span>
          期間計算<span class="badge rule">規則引擎</span>
        </div>
        <div class="erow"><span>送達生效日</span><b>{{ procedure.effectiveDate || '未能計算' }}</b></div>
        <div class="erow"><span>期滿日</span><b>{{ procedure.deadline || '未能計算' }}</b></div>
        <div class="erow">
          <span>是否逾期</span>
          <b :class="procedure.overdue ? 'bad' : 'ok'">
            {{ procedure.overdue === true ? '已逾期' : procedure.overdue === false ? '未逾期' : '無法判定' }}
          </b>
        </div>
        <details v-if="procedure.steps.length" class="steps">
          <summary>攤開算式（{{ procedure.steps.length }} 步）</summary>
          <ol><li v-for="(s, i) in procedure.steps" :key="i">{{ s.text || s.t || JSON.stringify(s) }}</li></ol>
        </details>
      </div>

      <!-- 第二意見交叉比對。**三類語意不同，顏色也不同**：
           disagreement 真分歧＝告警／not_comparable 輸入不對等＝說明／
           refusal_asymmetry 一方拒答＝未知，不得把有答案的那方當佐證。 -->
      <div v-if="procedure && procedure.crossCheck" class="cc">
        <div class="cchd">
          第二意見交叉比對
          <span class="badge" :class="ccClass">{{ ccLabel }}</span>
        </div>
        <!-- **共用盲點放最前面、最醒目**：它長得最像沒事（compared 滿的、
             agree 可能為 true），但那個一致只代表兩套錯在同一處。 -->
        <div v-if="(procedure.crossCheck.sharedBlindSpot || []).length" class="ccblk blind">
          <b>⚠ 兩套引擎共用同一盲點 {{ procedure.crossCheck.sharedBlindSpot.length }} 項</b>
          <ul><li v-for="(d, i) in procedure.crossCheck.sharedBlindSpot" :key="i">{{ ccText(d) }}</li></ul>
          <div class="ccnote">
            <b>此處「兩套一致」沒有證據力</b>——它們用的是同一份不完整的知識，
            相符只代表錯在同一處。請自行對照行政院人事行政總處辦公日曆表認定。
          </div>
        </div>
        <div v-if="procedure.crossCheck.disagreement.length" class="ccblk warn">
          <b>真分歧 {{ procedure.crossCheck.disagreement.length }} 項</b>
          <ul><li v-for="(d, i) in procedure.crossCheck.disagreement" :key="i">{{ ccText(d) }}</li></ul>
        </div>
        <div v-if="procedure.crossCheck.refusalAsymmetry.length" class="ccblk unknown">
          <b>一方拒答 {{ procedure.crossCheck.refusalAsymmetry.length }} 項</b>
          <ul><li v-for="(d, i) in procedure.crossCheck.refusalAsymmetry" :key="i">{{ ccText(d) }}</li></ul>
          <div class="ccnote">另一方雖有答案，<b>不得視為本系統的佐證</b>——本系統拒答的理由正是對方繞過的前提。</div>
        </div>
        <div v-if="procedure.crossCheck.notComparable.length" class="ccblk info">
          <b>輸入不對等 {{ procedure.crossCheck.notComparable.length }} 項（說明，非告警）</b>
          <ul><li v-for="(d, i) in procedure.crossCheck.notComparable" :key="i">{{ ccText(d) }}</li></ul>
        </div>
        <div v-if="(procedure.crossCheck.compared || []).length" class="cmp">
          已比對維度：{{ (procedure.crossCheck.compared || []).join('、') }}
        </div>
        <details v-if="second && (second.steps || []).length" class="second">
          <summary>第二意見的逐步算式（{{ (second.steps || []).length }} 步）</summary>
          <div class="sname">{{ second.name }}</div>
          <div class="srow"><span>屆滿日</span><b>{{ second.deadline || '未能計算' }}</b></div>
          <div class="srow"><span>是否逾期</span><b>{{ second.overdue === true ? '已逾期' : second.overdue === false ? '未逾期' : '未判定' }}</b></div>
          <ol><li v-for="(s, i) in second.steps" :key="i">{{ ccText(s) }}</li></ol>
          <div v-if="(second.legal_refs || []).length" class="srefs">
            法條依據：{{ second.legal_refs.join('、') }}
          </div>
          <div v-if="second.legal_ref_caveat" class="smiss">{{ second.legal_ref_caveat }}</div>
          <div v-if="(second.missing || []).length" class="smiss">
            第二意見缺少的輸入：{{ second.missing.join('、') }}（缺欄位時它不判定，與本系統一樣不猜）
          </div>
        </details>
        <div class="ccnote">{{ procedure.crossCheck.note }}</div>
      </div>

      <!-- 審查結論：**誰認定的必須說出來** -->
      <div class="fld">
        <label>審查結論</label>
        <select v-model="state.PROC.con" @change="onProcChange">
          <option value="A">程序不合 → 訴願不受理（77 條各款）</option>
          <option value="B">程序合法，應為實體審查 → 訴願無理由，駁回（79 I）</option>
          <option value="C">程序合法，應為實體審查 → 訴願有理由，撤銷／撤銷另處（81 I）</option>
          <option value="D">部分程序不合、部分無理由 → 部分不受理、部分駁回</option>
        </select>
      </div>

      <div class="origin" :class="procConclusionOrigin.by">
        <span class="badge" :class="procConclusionOrigin.by">{{ procConclusionOrigin.label }}</span>
        <span>{{ procConclusionOrigin.text }}</span>
      </div>

      <div class="fld" v-show="state.PROC.con === 'A'">
        <label>不受理款次（訴願法第 77 條）</label>
        <select v-model="state.PROC.kuan" @change="onProcChange">
          <option value="">（後端未判定）</option>
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

      <div class="mainbox" :class="{ blocked: procedure && procedure.requiresHumanConclusion }">
        <span class="lb">主　文</span>
        <span class="vl">{{ mainPreview }}</span>
      </div>

      <!-- 系統明確不自動判定的部分：放在面板上，不是塞 tooltip -->
      <div v-if="procedure && procedure.notAutoScreenedReason" class="nota">
        <span class="mi" style="font-size: 15px; vertical-align: -3px">block</span>
        <b>本系統不自動判定的部分</b>
        <div>{{ procedure.notAutoScreenedReason }}</div>
        <div v-if="procedure.notAutoScreened.length" class="kuans">
          未自動篩選款次：{{ procedure.notAutoScreened.join('、') }}
        </div>
      </div>

      <ul class="note">
        <li v-for="(x, i) in procNote" :key="i">{{ x }}</li>
      </ul>

      <div v-if="procedure && !procedure.inputsConfirmed" class="warn unconf">
        <span class="mi">warning</span>
        <span>
          期間算式的輸入欄位<b>尚未經你確認</b>（{{ procedure.unconfirmedFields.join('、') }}），
          結論段維持交人工。請在上方「案情與爭點」核對欄位後確認。
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.engine { border: 1px solid var(--line, #e3e0d8); border-radius: 7px; padding: 9px 11px; margin-bottom: 10px; background: #fafaf7; }
.ehd { font-weight: 600; font-size: 13px; margin-bottom: 6px; }
.erow { display: flex; justify-content: space-between; font-size: 13px; padding: 2px 0; }
.erow .ok { color: #2e5e3f; }
.erow .bad { color: #a03028; }
.steps { margin-top: 6px; font-size: 12.5px; }
.steps ol { margin: 6px 0 0 18px; line-height: 1.9; }
.badge { font-size: 11px; padding: 1px 6px; border-radius: 4px; margin-left: 6px; }
.badge.rule, .origin .badge.rule { background: #e6efe6; color: #2e5e3f; }
.badge.human, .origin .badge.human { background: #f6ecd9; color: #8a6320; }
.origin { display: flex; gap: 8px; align-items: flex-start; font-size: 12.5px; line-height: 1.75;
  padding: 8px 10px; border-radius: 6px; margin: 6px 0 10px; }
.origin.rule { background: #f1f6f1; }
.origin.human { background: #fdf7ea; }
.mainbox.blocked .vl { color: #a03028; font-weight: 500; }
.nota { background: #fbf6f5; border: 1px solid #ecd9d5; border-radius: 6px; padding: 8px 10px;
  font-size: 12.5px; line-height: 1.75; margin: 8px 0; }
.nota .kuans { color: var(--ink-3); margin-top: 4px; font-family: var(--mono); }
.warn.unconf { background: #fdf7ea; border: 1px solid #ecdcbb; border-radius: 6px; padding: 8px 10px; }
.cc { border: 1px solid var(--line, #e3e0d8); border-radius: 7px; padding: 9px 11px; margin: 10px 0; }
.cchd { font-weight: 600; font-size: 13px; margin-bottom: 6px; }
.badge.ok { background: #e6efe6; color: #2e5e3f; }
.badge.bad { background: #fbe7e4; color: #a03028; }
.badge.unknown { background: #eceaf2; color: #5a5470; }
.ccblk { border-radius: 6px; padding: 7px 9px; margin-bottom: 6px; font-size: 12.5px; line-height: 1.75; }
.ccblk.warn { background: #fbe9e6; }
.ccblk.unknown { background: #f0eef5; }
.ccblk.info { background: #f5f5f1; }
.ccblk ul { margin: 4px 0 0 16px; }
.ccnote { color: var(--ink-3); font-size: 12px; line-height: 1.7; margin-top: 4px; }
.badge.none { background: #eeece6; color: #6b675e; }
.cmp { font-size: 12px; color: var(--ink-3); font-family: var(--mono); margin-top: 4px; }
.second { margin-top: 7px; font-size: 12.5px; }
.second .sname { color: var(--ink-3); font-size: 12px; margin: 5px 0; }
.second .srow { display: flex; justify-content: space-between; padding: 1px 0; }
.second ol { margin: 6px 0 0 17px; line-height: 1.85; }
.second .srefs, .second .smiss { color: var(--ink-3); font-size: 12px; margin-top: 5px; line-height: 1.7; }
.badge.blind { background: #f3e0e0; color: #8a2b22; font-weight: 700; }
.ccblk.blind { background: #fbe4e0; border: 1px solid #e0b3ab; }
</style>
