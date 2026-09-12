<script setup>
import { computed } from 'vue'
import {
  state, intakeDraft, CONFIRMABLE_FIELDS, REQUIRED_FIELDS,
  unconfirmedSet, confirmIntakeAndRerun, emptyFieldsToConfirm, stillUnconfirmed,
  dateOrderWarnings,
} from '../store/workbench.js'

const confirmed = computed(() => state.payload?.procedure?.inputsConfirmed === true)
const running = computed(() => state.run.phase === 'running')
const isUnconfirmed = (k) => unconfirmedSet.value.has(k)
const isRequired = (k) => REQUIRED_FIELDS.includes(k)
const missingRequired = computed(() =>
  REQUIRED_FIELDS.filter((k) => {
    const v = intakeDraft[k]
    return v === '' || v === undefined || v === null
  }),
)
// `respondent_name` 結構上不在訴願書裡、只記載於原處分書，所以抽取器多半拿不到
// （`n1_extract.md` 明文要求抽不到就給 null／conf 0.0，且「代稱不是名字」）。
// 它的正常來源是承辦人手填，不是模型。
const HINT = { respondent_name: '需自原處分書取得，模型多半抽不到' }
</script>

<template>
  <div v-if="state.payload" class="intake itm">
    <div class="ph">
      <span class="mi">fact_check</span>
      <span class="ttl">收文欄位確認</span>
      <span class="gap"></span>
      <span class="tag" :class="confirmed ? 'ok' : 'warn'">
        {{ confirmed ? '已確認' : '尚未確認' }}
      </span>
    </div>
    <div class="bd">
      <p class="lead">
        以下欄位由模型自卷證抽取。<b>它們決定期間計算、訴願法 77 條款次與爭點偵測</b>，
        未經你確認前，後端會維持結論段交人工——<b>那是正確行為，不是故障</b>。
      </p>

      <div class="grid">
        <label v-for="[k, label] in CONFIRMABLE_FIELDS" :key="k" :class="{ unconf: isUnconfirmed(k) }">
          <span class="fk">
            {{ label }}
            <span v-if="isRequired(k)" class="star" title="必填">*</span>
            <span v-if="isUnconfirmed(k)" class="dot" title="此欄位尚未經你確認">●</span>
          </span>
          <select v-if="k === 'interested_party'" v-model="intakeDraft[k]">
            <option :value="false">否</option>
            <option :value="true">是</option>
          </select>
          <input v-else v-model="intakeDraft[k]" :placeholder="HINT[k] || '（模型未抽到）'"
                 :class="{ req: isRequired(k), empty: isRequired(k) && missingRequired.includes(k) }" />
        </label>
      </div>

      <div v-if="missingRequired.length" class="reqwarn">
        <span class="mi" style="font-size: 15px; vertical-align: -3px">error</span>
        <b>原處分相對人為必填</b>，未填不得送出確認。
        它是當事人適格（訴願法 §77-3）唯一的關鍵輸入，
        <b>結構上不在訴願書裡</b>、只記載於原處分書，所以要你手動填。
        未填時系統對適格一律回「需人工認定」。
      </div>

      <!-- 日期時序不可能的組合：**最常見的抽取錯誤，而算式看起來照樣正常** -->
      <div v-if="dateOrderWarnings.length" class="datewarn">
        <span class="mi" style="font-size: 15px; vertical-align: -3px">event_busy</span>
        <b>日期時序有問題</b>
        <ul><li v-for="(w, i) in dateOrderWarnings" :key="i">{{ w }}</li></ul>
      </div>

      <!-- 按確認會以「空值」送出的欄位：**承辦人要知道他在背書什麼** -->
      <div v-if="emptyFieldsToConfirm.length" class="emptynote">
        <b>以下欄位目前沒有內容</b>：{{ emptyFieldsToConfirm.join('、') }}。
        按下確認等於你確認<b>它們確實沒有內容</b>（而不是還沒填）。若有內容請先補上。
      </div>

      <!-- 確認過一輪仍未解除時，**要說出原因，不能只顯示「尚未確認」** -->
      <div v-if="stillUnconfirmed.length" class="stillwarn">
        <span class="mi" style="font-size: 15px; vertical-align: -3px">report</span>
        <b>已送出確認，但後端仍將這些欄位視為未確認</b>：{{ stillUnconfirmed.join('、') }}。
        後端刻意不接受 <code>null</code>（避免「送一個空值就宣稱有人確認過」），
        所以這些欄位需要實際有值、或由後端放寬。<b>結論封鎖在此之前不會解除。</b>
      </div>

      <div class="act">
        <button class="btn" :disabled="running" @click="confirmIntakeAndRerun">
          {{ running ? '重新執行中…' : '我已核對這些欄位，重新生成 →' }}
        </button>
        <span class="anote">
          「確認」的意思是<b>你看過這些值</b>——即使一個字都沒改也要按，
          因為差別在於現在有人為它背書。按下後會以確認過的欄位重跑一次（約 1 分鐘）。
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.intake { margin-bottom: 10px; }
.lead { font-size: 12.5px; line-height: 1.8; color: var(--ink-2); margin: 0 0 9px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px 10px; }
.grid label, .pending label { display: grid; gap: 3px; font-size: 12.5px; }
.fk { color: var(--ink-3); }
.dot { color: #c0872a; font-size: 10px; vertical-align: 2px; }
.grid label.unconf input, .grid label.unconf select { border-color: #ddb968; background: #fffdf6; }
.grid input, .grid select, .pending input {
  font: inherit; font-size: 12.5px; padding: 5px 7px;
  border: 1px solid var(--line, #e3e0d8); border-radius: 5px; background: #fff; width: 100%;
}
.pending { margin-top: 11px; padding: 9px 10px; border: 1px dashed #d8d4c8; border-radius: 6px; background: #fafaf7; }
.phd { font-size: 12.5px; font-weight: 600; margin-bottom: 5px; }
.pnote { font-size: 12px; line-height: 1.7; color: var(--ink-3); margin: 6px 0 0; }
.act { margin-top: 11px; display: grid; gap: 6px; }
.anote { font-size: 12px; line-height: 1.7; color: var(--ink-3); }
.tag.ok { background: #e6efe6; color: #2e5e3f; }
.tag.warn { background: #f6ecd9; color: #8a6320; }
.star { color: #a03028; font-weight: 700; }
.grid input.req { border-color: #c8b7d8; }
.grid input.empty { border-color: #a03028; background: #fdf3f2; }
.reqwarn { margin-top: 10px; padding: 8px 10px; border-radius: 6px;
  background: #fdf3f2; border: 1px solid #e9c9c5; font-size: 12.5px; line-height: 1.8; }
.emptynote { margin-top: 10px; padding: 8px 10px; border-radius: 6px;
  background: #fdf7ea; border: 1px solid #ecdcbb; font-size: 12.5px; line-height: 1.8; }
.stillwarn { margin-top: 10px; padding: 8px 10px; border-radius: 6px;
  background: #fbe4e0; border: 1px solid #e0b3ab; font-size: 12.5px; line-height: 1.8; }
.datewarn { margin-top: 10px; padding: 8px 10px; border-radius: 6px;
  background: #fbe4e0; border: 1px solid #e0b3ab; font-size: 12.5px; line-height: 1.8; }
.datewarn ul { margin: 4px 0 0 16px; }
</style>
