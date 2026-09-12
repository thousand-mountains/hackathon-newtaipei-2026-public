<script setup>
// 工具卡的結果區。**這裡只畫 store 從 `tool_result` 放進 out 的東西。**
//
// 2026-09-13 之前這個檔直接 import data.js 的 CASE_POOL／LAW_POOL 與一整份寫死的
// 解析結果，不管後端回什麼都照畫。實測載入建築法案件、按下「解析卷證檔案」，
// 畫面上出現的是一件廢棄物清理法的案子（訴願人、案號、日期、爭點、程序審查全是假的），
// 而且看起來跟真的一模一樣。示範資料一律移除，拿不到就說拿不到。
import RelationGraph from './RelationGraph.vue'

const props = defineProps({ out: Object })
const emit = defineEmits(['view-case', 'view-graph', 'preview-paper', 'export-download'])

// 契約 §3.2：score 顯示為相似度時必標「向量相似度，非法律相似度」（誠實紅線 §6-2）。
const pct = (s) => (typeof s === 'number' ? Math.round(s * 100) + '%' : '—')
// provenance 可能是 null（法條查表的 Hit 沒有這個鍵），要處理。
const PROV = { official: '賽方資料集', public_crawl: '市府公開爬蟲' }
const provLabel = (p) => PROV[p] || '出處未標示'
</script>

<template>
  <!-- 解析卷證：tool_result 只帶 run_id 與 state（契約 §3.3），內容由 agent 逐字說明 -->
  <template v-if="out.type === 'extract'">
    <div class="sec">
      <div class="sec-h">解析結果</div>
      <dl class="kv">
        <dt>run</dt><dd class="mono">{{ out.runId || '—' }}</dd>
        <dt>終態</dt><dd class="mono">{{ out.state || '—' }}</dd>
      </dl>
      <p style="margin: 8px 0 0; color: var(--muted); font-size: 12.5px">
        這個 run 只跑到程序審查，還沒有草稿。卷內各欄（案由、事實摘錄、爭點、程序審查）由小願在下方逐項說明，右欄「卷證檔案」是本案實際收到的檔案。
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
          <span class="sim">向量相似度 {{ pct(h.score) }}</span>
        </div>
        <div class="ttl">{{ h.t }}</div>
        <div class="r2">
          <span class="tag">{{ provLabel(h.provenance) }}</span>
          <span v-if="h.note" class="tag">{{ h.note }}</span>
        </div>
        <div class="simbar"><i :style="{ width: (typeof h.score === 'number' ? Math.round(h.score * 100) : 0) + '%' }"></i></div>
      </button>
      <p style="margin: 10px 0 0; color: var(--muted); font-size: 12px">
        分數是<b style="font-weight: 500">向量相似度，不是法律相似度</b>；KB 命中未對資料集實檔驗證。本欄為參考資料，不影響草稿生成。
      </p>
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
      <div v-if="out.sections" class="draft">
        <template v-for="(s, i) in out.sections" :key="i">
          <h4>{{ s.h }}</h4>
          <p v-for="(b, j) in s.blocks" :key="j" class="indent">
            {{ b.text }}
            <span v-for="cc in b.cites || []" :key="cc.id" class="cite">{{ cc.label || cc.id }}</span>
          </p>
        </template>
      </div>
      <p v-else style="margin: 0; color: var(--muted); font-size: 12.5px">
        草稿已產出（{{ out.artifactId || out.runId || '—' }}），全文載入中或載入失敗，可從右欄「答辯書與產出」開啟。
      </p>
      <p style="margin: 10px 0 0; color: var(--muted); font-size: 12px">AI 生成，待承辦人審核。引註以草稿內標註為準。</p>
    </div>
  </template>

  <!-- 工具回 empty / failed（契約 §2.3）：兩者畫面上要分得出來 -->
  <template v-else-if="out.type === 'status'">
    <div class="sec">
      <div class="sec-h">{{ out.status === 'failed' ? '工具執行失敗' : out.status === 'empty' ? '查無結果' : '完成' }}</div>
      <p style="margin: 0; font-size: 13.5px">{{ out.note }}</p>
      <p v-if="out.status === 'failed'" style="margin: 8px 0 0; color: var(--muted); font-size: 12px">
        這是<b style="font-weight: 500">查詢來源本身失敗</b>，不是資料庫裡沒有這筆資料，可以重試。
      </p>
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
        <button class="btn" @click="emit('export-download', out.isPdf)">重新下載</button>
      </div>
    </div>
  </template>

  <!-- 純 HTML（其他） -->
  <div v-else-if="out.type === 'html'" v-html="out.html"></div>
</template>
