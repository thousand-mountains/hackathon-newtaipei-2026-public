<script setup>
// 工具卡的結果區。**這裡只畫 store 從 `tool_result` 放進 out 的東西。**
//
// 2026-09-13 之前這個檔直接 import data.js 的 CASE_POOL／LAW_POOL 與一整份寫死的
// 解析結果，不管後端回什麼都照畫。實測載入建築法案件、按下「解析卷證檔案」，
// 畫面上出現的是一件廢棄物清理法的案子（訴願人、案號、日期、爭點、程序審查全是假的），
// 而且看起來跟真的一模一樣。示範資料一律移除，拿不到就說拿不到。
import RelationGraph from './RelationGraph.vue'
import ProcedureCheck from './ProcedureCheck.vue'
import { scoreCaption, scoreNote, rankerOf, showsPercent } from '../api/ranker.js'
import { active, toolStatusText, inlineMd, downloadExport } from '../store/app.js'

// 解析卷證的四塊（案由／事實摘錄／爭點／程序審查）**不在 tool_result 裡**——
// `tool_result` 只帶 run_id 與 state，內容要跑完之後打彙整版拿（契約 §3.3）。
// store 在 refreshCase 裡填進 case，所以這裡從 case 讀，不是從 out 讀。
const cur = () => active() || {}
const INTAKE_LABEL = {
  no: '案號', type: '案件類型', person: '訴願人', org: '原處分機關',
  d1: '原處分日', d2: '送達日', d3: '收文日', agent: '代理人',
  service_method: '送達方式', transit_days: '在途期間', respondent_name: '原處分相對人',
}
const SERVICE = { personal: '本人簽收', deposit: '寄存送達', public: '公示送達' }
// 收文日期統一用民國顯示。後端 intake 給的是 ISO（2025-03-14），但同一張卡下面的
// 期間計算算式是民國（114/3/14），混用會讓承辦人沒辦法一眼對上是不是同一天。
// **只換顯示格式，不動值**；原始 ISO 留在 title，滑過去可核對。
const DATE_KEYS = ['d1', 'd2', 'd3']
const roc = (iso) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''))
  return m ? `${Number(m[1]) - 1911}/${Number(m[2])}/${Number(m[3])}` : iso
}
//: 空欄文案的**唯一**出處。這個檔是全前端唯一畫 intake 的地方（2026-09-13 grep 過），
//: 要顯示空欄的第二個地方出現時，從這裡拿，不要再寫一份。
//:
//: 為什麼是「未取得」而不是「卷證未載」：`backend/llm/prompts/n1_extract.md:24` 規定
//: 「卷證沒寫的欄位，value 給 null、conf 給 0.0，不要猜」，所以 null **應該**代表
//: 卷證裡沒有依據。但那是對模型下的指令，不是保證——模型也可能只是沒抽到。
//: 「未取得」說的是系統這一端確定的事（我沒拿到），沒有替卷證作保。
const INTAKE_EMPTY = '未取得'
const INTAKE_EMPTY_WHY =
  '抽取時未從卷證取得此欄。依抽取規則，沒有原文依據就留空不猜——這不等於卷證裡一定沒有。'

const intakeRows = () => {
  const it = cur().intake
  if (!it) return []
  return ['no', 'type', 'person', 'org', 'd1', 'd2', 'd3', 'agent', 'service_method', 'transit_days']
    .map((k) => {
      let v = it[k]
      const raw = v
      if (k === 'service_method') v = SERVICE[v] || v
      if (k === 'transit_days' && typeof v === 'number') v = v + ' 日'
      if (DATE_KEYS.includes(k)) v = roc(v)
      return { k, label: INTAKE_LABEL[k] || k, raw: DATE_KEYS.includes(k) ? String(raw || '') : '',
               v: v === null || v === undefined || v === '' ? null : String(v),
               // auto_fields＝由卷證自動擷取、**還沒有人確認**。要標出來。
               auto: (it.auto_fields || []).includes(k) }
    })
  // **不要再把 v === null 的列濾掉。** 2026-09-13 之前這裡有一行 `.filter(r => r.v !== null)`，
  // 抽不到的欄位整列從表格消失、沒有任何訊號。當時看不出問題，是因為抽不到必填欄位時
  // 整條 run 會停在 NEEDS_INPUT，承辦人被別的地方擋住了；`no`（收文案號）降成非必填之後
  // （它不進任何規則運算），案子會一路綠燈跑完，而表格**直接少一列案號**——
  // 從「擋住你」變成「靜默丟棄」，而且這種更糟看起來像變好（紅燈不見了）。
}

//: 這一輪有幾欄是空的。0 時不顯示那句說明——沒有空欄還講一句「空欄不是略過」只是噪音。
const emptyIntakeCount = () => intakeRows().filter((r) => r.v === null).length

const props = defineProps({ out: Object })
const emit = defineEmits(['view-case', 'view-graph', 'preview-paper', 'export-download'])

// 契約 §3.2：分數的說法一律走 api/ranker.js——那裡是全前端唯一一份文案，
// 因為「這個數字是誰算的」開了重排前後是兩件事（見該檔檔頭）。
// 進度條寬度：查表那種不顯示百分比，條也不畫。
const barWidth = (h) => (showsPercent(rankerOf(h)) && typeof h.score === 'number' ? Math.round(h.score * 100) : 0)
// provenance 可能是 null（法條查表的 Hit 沒有這個鍵），要處理。
const PROV = { official: '賽方資料集', public_crawl: '市府公開爬蟲' }
const provLabel = (p) => PROV[p] || '出處未標示'
</script>

<template>
  <!-- 解析卷證：run_id／state 來自 tool_result，四塊內容來自彙整版（契約 §3.3） -->
  <template v-if="out.type === 'extract'">
    <div class="sec">
      <div class="sec-h">案件基本資訊</div>
      <dl v-if="intakeRows().length" class="kv">
        <template v-for="r in intakeRows()" :key="r.k">
          <dt>{{ r.label }}</dt>
          <dd
            :class="{ mono: r.v !== null && ['no', 'd1', 'd2', 'd3'].includes(r.k) }"
            :title="r.v === null ? INTAKE_EMPTY_WHY : r.raw ? '原始值 ' + r.raw : null"
          >
            <span v-if="r.v === null" class="tag none">{{ INTAKE_EMPTY }}</span>
            <template v-else>{{ r.v }}<span v-if="r.auto" class="tag">自動擷取．待確認</span></template>
          </dd>
        </template>
      </dl>
      <!-- 這條 v-else 只在 intake 整包是 null 時走到（契約 §4：取不到是 null 不是省略）。
           「有 intake 但某幾欄是空的」不走這裡——那些欄位上面照樣逐列畫出來。 -->
      <p v-else style="margin: 0; color: var(--muted); font-size: 12.5px">這次執行沒有收文欄位。</p>
      <p v-if="emptyIntakeCount()" style="margin: 8px 0 0; color: var(--muted); font-size: 11.5px">
        有 {{ emptyIntakeCount() }} 欄沒有值。<b>空欄是抽取沒拿到，不是系統略過不顯示</b>；要不要補、怎麼補由承辦人判斷。
      </p>
      <p style="margin: 8px 0 0; color: var(--faint); font-size: 11.5px">
        run <span class="mono">{{ out.runId || '—' }}</span>．終態 <span class="mono">{{ out.state || '—' }}</span>．這個 run 只跑到程序審查，還沒有草稿。
      </p>
    </div>

    <div v-if="(cur().facts || []).length" class="sec">
      <div class="sec-h">事實摘錄</div>
      <p v-for="(f, i) in cur().facts" :key="i" style="margin: 0 0 8px; font-size: 13.5px">
        {{ f.text }}
        <span v-if="f.quote_ref" class="cite">{{ f.quote_ref }}</span>
      </p>
    </div>

    <div v-if="(cur().issues || []).length" class="sec">
      <div class="sec-h">本案爭點</div>
      <div v-for="iss in cur().issues" :key="iss.id" class="check">
        <span class="bdg" :class="iss.severity === 'high' ? 'alert' : iss.severity === 'mid' ? 'warn' : 'ok'">{{ iss.tag || iss.severity }}</span>
        <span class="ct">
          <b>{{ iss.t }}</b>
          <span>{{ iss.q }}</span>
          <span v-if="iss.src" style="color: var(--faint)">{{ iss.src }}</span>
        </span>
      </div>
    </div>

    <ProcedureCheck v-if="cur().screen" :screen="cur().screen" />
    <!-- 這張卡是「解析卷證**跑完了**」才會出現的，所以走到這裡就代表有 run
         （`out.runId`）——此時拿不到 screen 一律是異常，不是「還沒跑過」。
         不要靜默留白：留白會讓人以為這件案子沒有程序爭點。 -->
    <div v-else-if="cur().runStale || out.runId" class="sec">
      <div class="sec-h">程序審查</div>
      <p style="margin: 0; color: var(--warn); font-size: 12.5px">
        這次執行有紀錄（{{ out.runId || cur().runId }}），但讀不回程序審查內容。<b style="font-weight: 500">不顯示任何期間推算</b>，請重跑一次解析卷證。
      </p>
    </div>
  </template>

  <!-- 檢索類：相似案例 -->
  <template v-else-if="out.type === 'cases'">
    <div class="sec">
      <div class="sec-h">相似訴願決定（{{ (out.hits || []).length }} 筆）</div>
      <p v-if="!(out.hits || []).length" style="margin: 0; color: var(--muted); font-size: 12.5px">這次沒有回傳命中。</p>
      <button v-for="h in out.hits" :key="h.id" class="ccard" @click="emit('view-case', h)">
        <div class="r1">
          <span class="no">{{ h.id }}</span>
          <span class="sim">{{ scoreCaption(h) }}</span>
        </div>
        <div class="ttl">{{ h.t }}</div>
        <div class="r2">
          <span class="tag">{{ provLabel(h.provenance) }}</span>
          <span v-if="h.note" class="tag">{{ h.note }}</span>
        </div>
        <div class="simbar"><i :style="{ width: barWidth(h) + '%' }"></i></div>
      </button>
      <p v-for="n in [...new Set((out.hits || []).map(scoreNote))]" :key="n" style="margin: 10px 0 0; color: var(--muted); font-size: 12px">
        {{ n }}
      </p>
      <p style="margin: 6px 0 0; color: var(--muted); font-size: 12px">本欄為參考資料，不影響草稿生成。</p>
    </div>
  </template>

  <!-- 檢索類：法規／判解函釋 -->
  <template v-else-if="out.type === 'laws' || out.type === 'hits'">
    <div class="sec">
      <div class="sec-h">檢索命中（{{ (out.hits || []).length }} 筆）</div>
      <p v-if="!(out.hits || []).length" style="margin: 0; color: var(--muted); font-size: 12.5px">這次沒有回傳命中。</p>
      <div v-for="h in out.hits" :key="h.id" class="lawrow">
        <span class="lno">{{ h.t }}</span>
        <span class="lt">
          <b style="font-weight: 500">{{ h.verified ? '字號對得回法規快照' : '庫外，本系統無法驗證' }}</b>
          <em>{{ h.note || h.src || '' }}</em>
        </span>
      </div>
      <p style="margin: 10px 0 0; color: var(--muted); font-size: 12px">
        「對得回快照」只代表<b style="font-weight: 500">字號存在</b>，不代表與本案相關（relevance 一律 unknown）。
      </p>
    </div>
    <!-- 契約 §3.5.2：手動挑的法規逐筆回報走到哪（三態），文案由後端帶 -->
    <div v-if="out.pickedLaws && out.pickedLaws.length" class="sec">
      <div class="sec-h">你手動挑的法規</div>
      <div v-for="p in out.pickedLaws" :key="p.id" class="lawrow">
        <span class="lno">{{ p.t }}</span>
        <span class="lt"><b style="font-weight: 500">{{ p.state }}</b><em>{{ p.note }}</em></span>
      </div>
    </div>
  </template>

  <!-- 關聯圖：形狀見契約 §3.7，資料全部來自 tool_result.graph -->
  <template v-else-if="out.type === 'graph'">
    <div class="sec">
      <div class="sec-h">案件關聯圖</div>
      <RelationGraph v-if="out.graph" :graph="out.graph" />
      <p v-else style="margin: 0; color: var(--muted); font-size: 12.5px">這次沒有回傳圖。</p>
      <div v-if="out.graph" style="display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap">
        <button class="btn pri" @click="emit('view-graph', out.graph)">放大檢視</button>
      </div>
    </div>
  </template>

  <!-- 草稿：tool_result 帶 artifact_id／cite_count，全文走 #21 -->
  <template v-else-if="out.type === 'draft'">
    <div class="sec">
      <div class="sec-h">
        {{ out.title || '訴願決定書草稿' }}
        <span v-if="out.citeCount != null" style="font-weight: 400; color: var(--muted); letter-spacing: 0">　引註 {{ out.citeCount }} 處</span>
      </div>
      <!-- 後端回 sections[]（契約 §4.4）→ 逐段畫，引註掛在句尾 -->
      <div v-if="out.sections" class="draft">
        <template v-for="(s, i) in out.sections" :key="i">
          <h4>{{ s.h }}</h4>
          <p v-for="(b, j) in s.blocks" :key="j" class="indent">
            {{ b.text }}
            <span v-for="cc in b.cites || []" :key="cc.id" class="cite">{{ cc.label || cc.id }}</span>
          </p>
        </template>
      </div>
      <!-- 未回 sections、但有 html 全文（mock 或未回 sections 的後端）→ 直接在聊天室攤出完整草稿。
           不外套 .draft：html 內容本身已自帶版面結構（.draft／段落），重複套會雙層縮排。 -->
      <div v-else-if="out.html" v-html="out.html"></div>
      <p v-else style="margin: 0; color: var(--muted); font-size: 12.5px">
        草稿已產出（{{ out.artifactId || out.runId || '—' }}），全文載入中或載入失敗，可從右欄「草稿文件產出」開啟。
      </p>
      <p style="margin: 10px 0 0; color: var(--muted); font-size: 12px">AI 生成，待承辦人審核。引註以草稿內標註為準。</p>
    </div>
  </template>

  <!-- 工具回 empty / failed（契約 §2.3）：兩者畫面上要分得出來 -->
  <template v-else-if="out.type === 'status'">
    <div class="sec">
      <!-- 文案走 store 的 TOOL_STATUS（全前端唯一一份）。`empty` 不說「查無」的
           理由寫在那裡：後端同一個值也代表「還沒查」，而它正下方的 note 會逐字說明。 -->
      <!-- `ok` 的大標不畫：卡頭已經有「✓ 完成」，再寫一次「完成」會像渲染出錯，
           而這張卡要讓人看的是下面那一句 note（`read_case` 就是這種）。
           其餘狀態的大標帶著真的資訊（尚未執行／未取得結果／工具執行失敗），照畫。 -->
      <div v-if="out.status !== 'ok'" class="sec-h">{{ toolStatusText(out.status, 'head') }}</div>
      <!-- 只顯示後端的 note，**不要再加一句固定的解釋**。契約 §2.3 把 failed 描述成
           「工具本身失敗（KB 打不到、快照載不動、pipeline 炸了）」，但後端也用 failed
           回前置條件沒滿足（實測「還沒有解析過卷證。」）。對那種情況說
           「查詢來源本身失敗，可以重試」是錯的——重試一百次也不會過。
           note 本來就每種情況都寫得具體，讓它自己說。 -->
      <!-- note 是空的就不要畫一個空段落（`not_attempted` 這類新狀態前端不補固定句）。
           後端的 note 帶 markdown 粗體（「這次**沒有去查**」），用跟程序審查卡同一支
           行內渲染器畫出來——純文字輸出會把星號原樣印在承辦人面前。
           **不要因此改後端的字**：那個粗體標的正是這句話的重點。 -->
      <p v-if="out.note" style="margin: 0; font-size: 13.5px" v-html="inlineMd(out.note)"></p>
    </div>
  </template>

  <!-- 修潤對照（前端 diff：上一版 vs 後端新版）-->
  <template v-else-if="out.type === 'diff'">
    <div class="sec">
      <div class="sec-h">
        修潤對照
        <span v-if="out.instruction" style="font-weight: 400; color: var(--muted); letter-spacing: 0">　方向：{{ out.instruction }}</span>
      </div>
      <div class="draft" style="font-size: 14px">
        <p class="indent" v-html="out.html"></p>
      </div>
      <p style="margin-top: 8px; color: var(--muted); font-size: 12px">
        <span class="diffadd" style="padding: 0 4px; border-radius: 2px">綠色</span> 為新增、<span class="diffdel" style="padding: 0 4px; border-radius: 2px">紅色</span> 為刪除。上一版取自本機暫存，逐詞與新版比對而得。
      </p>
    </div>
  </template>

  <!-- 匯出檔案：檔名、大小、引註數都取自後端回應標頭，不寫死 -->
  <template v-else-if="out.type === 'export'">
    <div>
      <div class="fitem" style="border: 1px solid var(--line); border-radius: 3px; padding: 10px 12px">
        <span class="ic">{{ out.isPdf ? 'pdf' : 'docx' }}</span>
        <span class="ft">{{ out.fname }}<small>{{ out.isPdf ? 'PDF' : 'Word 相容．可續行編修' }}<template v-if="out.kb">．約 {{ out.kb }} KB</template><template v-if="out.citeCount != null">．引註 {{ out.citeCount }} 處</template></small></span>
      </div>
      <p v-if="out.unresolved" style="margin: 8px 0 0; color: var(--muted); font-size: 12px">
        此檔含 {{ out.unresolved }} 處無法對應的引用，送簽前請先核對。
      </p>
      <div style="display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap">
        <button class="btn pri" @click="downloadExport(out)">下載檔案</button>
      </div>
    </div>
  </template>

  <!-- 純 HTML（其他） -->
  <div v-else-if="out.type === 'html'" v-html="out.html"></div>
</template>
