<script setup>
// 程序審查卡。資料全部來自彙整版的 `screen`（契約 §3.3／§4），零前端推算。
//
// **這張卡是 CONSTITUTION §4 紅線的下半截。** 上半截是「使用者問期限時不顯示 agent 算的天數」
// （`redirect`，已做）；下半截是「給他一個可以自己逐步驗算的算式」。所以期間計算那一段
// **每一步都必須把法條依據 `basis` 一起顯示** —— 承辦人要能逐步核對，
// 而不是看到一個結論然後決定要不要相信它。只顯示「114/4/14 屆滿」就退回成另一種黑箱。
//
// `screen` 後端是**整包**給的（十個鍵），由前端挑要顯示什麼。這裡挑的五塊都是
// 「承辦人核對程序要件時真的會看」的：期間計算、第二意見比對、§77 篩檢、
// 當事人適格、程序結論。刻意**不裁掉**那些「系統不判定」的留白與 caveat ——
// 那些正是這個產品跟「看起來很確定的 AI」的差別。
import { inlineMd } from '../store/app.js'

const props = defineProps({ screen: { type: Object, required: true } })

// 後端這幾段說明文字裡有 markdown（`**「需人工認定」是正當結果，不是失敗**`）與
// 反引號的程式碼參照。**不要改後端的字**——這些是誠實揭露的措辭，改了就變成我在替它講話。
// 用跟聊天回覆同一支行內渲染器畫出來即可（先 esc 再套粗體／行內碼，不開注入的口子）。

const dl = () => props.screen.deadline || null
const cross = () => props.screen.deadline_cross_check || null
const art77 = () => props.screen.art77 || null
const ps = () => props.screen.party_standing || null
const concl = () => props.screen.procedure_conclusion || null

//: 程序結論代碼 → 承辦人看得懂的字。**這一份是全前端唯一的對照表。**
//:
//: 代碼的意思查得到、不是猜的：
//:   `backend/nodes/n3_procedure.py:348` —「只在『程序不合 → 不受理』這一種由規則
//:     直接給出……value 只可能是 "A" 或 None，永遠不會出現 B／C／D」
//:   `backend/tests/test_cross_check.py:142` —「B（駁回）／C（撤銷）／D 屬實體判斷」
//: 所以 A ＝ 不受理。B/C/D 後端保證不會產出，這裡也不列——列了等於宣告我們會給
//: 實體結論。真的收到 B/C/D 時走 fallback 原樣顯示，讓它看起來就是不對勁。
const CONCLUSION_LABEL = { A: '不受理' }

//: `value` 是 null 不是「沒算出來」，是**刻意不判定**（後端 `by:"unavailable"`，
//: `why` 裡寫「此處留白不是失敗，是刻意不猜」）。所以文案講的是系統的立場，
//: 不是資料的缺漏——這跟收文欄位的「未取得」是兩件不同的事，不共用一份文案。
const CONCLUSION_NONE = '本系統不判定'

const conclLabel = () => {
  const v = concl() && concl().value
  if (!v) return CONCLUSION_NONE
  return CONCLUSION_LABEL[v] || v
}

// 民國年顯示用；後端 steps 的 value 已經是民國，這裡只處理 deadline/effective_date 的西元。
const roc = (iso) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''))
  return m ? `${Number(m[1]) - 1911}/${Number(m[2])}/${Number(m[3])}` : iso || '—'
}
</script>

<template>
  <div class="sec" data-anchor="deadline">
    <div class="sec-h">期間計算（規則引擎逐步驗算）</div>
    <template v-if="dl()">
      <div class="check" style="margin-bottom: 8px">
        <span class="bdg" :class="dl().overdue ? 'alert' : 'ok'">{{ dl().overdue ? '已逾期' : '未逾期' }}</span>
        <span class="ct">
          <b>訴願期間至 {{ roc(dl().deadline) }} 屆滿</b>
          <span>送達生效日 {{ roc(dl().effective_date) }}。以下每一步都附法條依據，可逐步核對。</span>
        </span>
      </div>
      <ol class="calc">
        <li v-for="(st, i) in dl().steps || []" :key="i">
          <span class="cr">{{ st.rule }}</span>
          <span class="cv mono">{{ st.value }}</span>
          <span class="cb">依據：{{ st.basis }}</span>
        </li>
      </ol>
      <p v-for="(cv, i) in dl().caveats || []" :key="'cv' + i" class="caveat">⚠ {{ cv }}</p>
    </template>
    <p v-else style="margin: 0; color: var(--muted); font-size: 12.5px">這次執行沒有期間計算結果。</p>
  </div>

  <!-- 第二意見比對：兩具獨立引擎算同一件事，一致與否本身就是給人看的訊號 -->
  <div v-if="cross()" class="sec">
    <div class="sec-h">第二意見比對</div>
    <div class="check">
      <span class="bdg" :class="cross().agree ? 'ok' : 'alert'">{{ cross().agree ? '兩具引擎一致' : '兩具引擎不一致' }}</span>
      <span class="ct">
        <b>比對項目：{{ (cross().compared || []).join('、') || '—' }}</b>
        <span v-if="cross().engines">
          主引擎 {{ cross().engines.primary && cross().engines.primary.name }}；第二意見
          {{ cross().engines.second_opinion && cross().engines.second_opinion.name }}
        </span>
      </span>
    </div>
    <p v-if="(cross().shared_blind_spot || []).length" class="caveat">
      ⚠ 兩具引擎的共同盲點：{{ cross().shared_blind_spot.join('、') }}
    </p>
  </div>

  <!-- §77 篩檢：只自動判第 2 款，其餘明講不判 -->
  <div v-if="art77()" class="sec">
    <div class="sec-h">訴願法 §77 不受理事由篩檢</div>
    <div class="check">
      <span class="bdg" :class="(art77().hits || []).length ? 'alert' : 'ok'">
        {{ (art77().hits || []).length ? '有不受理事由' : '未命中自動篩檢款項' }}
      </span>
      <span class="ct">
        <b>已自動篩檢：{{ (art77().screened || []).join('、') || '—' }}</b>
        <span v-html="inlineMd(art77().basis)"></span>
      </span>
    </div>
    <p v-if="art77().not_auto_screened_reason" class="caveat">⚠ <span v-html="inlineMd(art77().not_auto_screened_reason)"></span></p>
  </div>

  <!-- 當事人適格：三態，「需人工認定」是正當結果不是失敗 -->
  <div v-if="ps()" class="sec">
    <div class="sec-h">當事人適格（§77 第 3 款）</div>
    <div class="check">
      <span class="bdg" :class="ps().verdict === '適格' ? 'ok' : ps().verdict === '不適格' ? 'alert' : 'warn'">{{ ps().verdict }}</span>
      <span class="ct">
        <b>{{ (ps().legal_refs || []).join('、') }}</b>
        <span>訴願人：{{ (ps().inputs && ps().inputs.appellant_name) || '—' }}　原處分相對人：{{ (ps().inputs && ps().inputs.respondent_name) || '（卷內未載）' }}</span>
      </span>
    </div>
    <ol class="calc" v-if="(ps().steps || []).length">
      <li v-for="(st, i) in ps().steps" :key="i"><span class="cr">{{ st }}</span></li>
    </ol>
    <p v-if="(ps().missing || []).length" class="caveat">⚠ 尚缺：{{ ps().missing.join('、') }}</p>
    <p v-if="ps().note" class="caveat" v-html="inlineMd(ps().note)"></p>
  </div>

  <!-- 程序結論：留白時要說明為什麼留白 -->
  <div v-if="concl()" class="sec">
    <div class="sec-h">程序結論</div>
    <div class="check">
      <span class="bdg" :class="concl().value ? 'ok' : 'warn'" :title="concl().value ? '程序結論代碼 ' + concl().value : null">{{ conclLabel() }}</span>
      <span class="ct">
        <b v-if="concl().basis">{{ concl().basis }}</b>
        <span v-html="inlineMd(concl().why)"></span>
      </span>
    </div>
  </div>

  <div v-if="(screen.unconfirmed_procedural_fields || []).length" class="sec">
    <div class="sec-h">待承辦人確認的收文欄位</div>
    <p style="margin: 0; font-size: 12.5px; color: var(--muted)">
      這些欄位由卷證自動擷取、<b style="font-weight:500;color:var(--ink-2)">尚未經人確認</b>，期間計算與適格判斷都以它們為輸入：
      <span class="mono">{{ screen.unconfirmed_procedural_fields.join('、') }}</span>
    </p>
  </div>
</template>
