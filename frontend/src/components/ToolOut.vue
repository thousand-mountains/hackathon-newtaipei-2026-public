<script setup>
import { CASE_NO, CASE_POOL, LAW_POOL } from '../data/data.js'
import RelationGraph from './RelationGraph.vue'

const props = defineProps({ out: Object })
const emit = defineEmits(['view-case', 'view-graph', 'preview-paper', 'export-download'])
</script>

<template>
  <!-- 解析卷證結果 -->
  <template v-if="out.type === 'extract'">
    <div class="sec">
      <div class="sec-h">案件基本資訊</div>
      <dl class="kv">
        <dt>案號</dt><dd class="mono">{{ CASE_NO }}</dd>
        <dt>案件類型</dt><dd>違反廢棄物清理法事件　<span class="tag">分類信心 0.96</span></dd>
        <dt>訴願人</dt><dd>吉○實業有限公司</dd>
        <dt>原處分機關</dt><dd>新北市政府環境保護局</dd>
        <dt>原處分</dt><dd class="mono">114/05/20　新北環稽字第 1140876543 號</dd>
        <dt>裁罰內容</dt><dd>罰鍰新臺幣 6 萬元，並限期於 114/06/30 前完成改善</dd>
        <dt>適用法條</dt><dd>廢棄物清理法第 36 條第 1 項、第 52 條</dd>
      </dl>
    </div>
    <div class="sec">
      <div class="sec-h">事實經過</div>
      <p style="margin: 0; font-size: 13.5px">訴願人於本市林口區○○路 88 號設廠從事塑膠製品製造，為原處分機關列管之事業。原處分機關於 114 年 4 月 9 日派員稽查，查得廠區東側露天堆置未分類之廢塑膠混合物及廢木材約 12 立方公尺，未設置防止地面水、雨水流入之設施，認違反廢棄物清理法第 36 條第 1 項規定，爰依同法第 52 條裁處罰鍰 6 萬元並限期改善。訴願人不服，於 114 年 6 月 16 日提起本件訴願。</p>
    </div>
    <div class="sec">
      <div class="sec-h">訴願主張</div>
      <ol class="olist">
        <li>系爭堆置物為可回收再利用之產源物料，仍具經濟價值，非屬廢棄物清理法所稱之廢棄物。</li>
        <li>稽查當日正進行分類整理作業，屬作業中之暫時放置，並非長期貯存。</li>
        <li>原處分機關作成處分前未通知訴願人陳述意見，違反行政程序法第 102 條規定。</li>
        <li>訴願人為初次違規且已即時改善，原處分未審酌行政罰法第 18 條各款事由，裁罰過重。</li>
      </ol>
    </div>
    <div class="sec">
      <div class="sec-h">本案爭點</div>
      <ol class="olist">
        <li><b>爭點一</b>　系爭堆置物之法律性質，是否該當廢棄物清理法第 2 條第 1 項第 2 款所稱「減失原效用」之廢棄物。</li>
        <li><b>爭點二</b>　原處分機關未給予陳述意見之程序瑕疵，得否依行政程序法第 114 條第 1 項第 3 款於訴願程序終結前補正。</li>
        <li><b>爭點三</b>　罰鍰 6 萬元是否已踐行行政罰法第 18 條第 1 項之裁量義務並符合比例原則。</li>
      </ol>
    </div>
    <div class="sec">
      <div class="sec-h">程序審查</div>
      <div class="check"><span class="bdg ok">未逾期</span><span class="ct"><b>訴願期間</b><span>原處分 114/05/23 送達，法定 30 日期間至 114/06/22 屆滿；訴願人 114/06/16 提起，未逾訴願法第 14 條第 1 項所定期間。</span></span></div>
      <div class="check"><span class="bdg ok">合致</span><span class="ct"><b>當事人適格</b><span>訴願人為原處分之受處分人，具訴願能力；卷附委任書，代理權完備。</span></span></div>
      <div class="check"><span class="bdg ok">合致</span><span class="ct"><b>管轄機關</b><span>原處分機關為新北市政府環境保護局，依訴願法第 4 條第 3 款，本府為管轄之受理訴願機關。</span></span></div>
      <div class="check"><span class="bdg ok">未逾</span><span class="ct"><b>裁處權時效</b><span>違規行為日 114/04/09，處分日 114/05/20，未逾行政罰法第 27 條第 1 項 3 年期間。</span></span></div>
      <div class="check"><span class="bdg warn">應補正</span><span class="ct"><b>陳述意見程序</b><span>卷內未見原處分機關依行政程序法第 102 條所為之陳述意見通知書或紀錄，建議先函請原處分機關補提；如確未踐行且未於訴願程序終結前補正，將構成撤銷事由。</span></span></div>
      <div class="check"><span class="bdg alert">卷證不足</span><span class="ct"><b>廢棄物性質之證據</b><span>卷附採證照片僅呈現堆置外觀，未見物料成分、有無交易價值或再利用申報之調查資料，涉及爭點一之要件事實認定。</span></span></div>
    </div>
  </template>

  <!-- 相似案例 -->
  <template v-else-if="out.type === 'cases'">
    <div class="sec">
      <div class="sec-h">相似案例（依爭點重疊度排序）</div>
      <button v-for="k in CASE_POOL" :key="k.no" class="ccard" @click="emit('view-case', k)">
        <div class="r1"><span class="no">{{ k.no }}</span><span class="sim">相似度 {{ k.sim }}%</span></div>
        <div class="ttl">{{ k.title }}</div>
        <div class="r2">
          <span class="tag">{{ k.type }}</span><span class="tag">{{ k.verdict }}</span><span class="tag">{{ k.law }}</span>
          <span v-if="k.real" class="tag real">卷附實例</span><span v-else class="tag">示範資料</span>
        </div>
        <div class="simbar"><i :style="{ width: k.sim + '%' }"></i></div>
      </button>
    </div>
  </template>

  <!-- 相關法規 -->
  <template v-else-if="out.type === 'laws'">
    <div class="sec">
      <div class="sec-h">相關法規（皆為現行有效版本）</div>
      <div v-for="l in LAW_POOL" :key="l.no" class="lawrow">
        <span class="lno">{{ l.no }}</span>
        <span class="lt"><b style="font-weight: 500">{{ l.title }}</b><em>{{ l.note }}</em></span>
      </div>
    </div>
  </template>

  <!-- 關聯圖 -->
  <template v-else-if="out.type === 'graph'">
    <div class="sec">
      <div class="sec-h">案件關聯圖</div>
      <RelationGraph />
      <div style="display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap">
        <button class="btn pri" @click="emit('view-graph')">放大檢視</button>
      </div>
    </div>
  </template>

  <!-- 修潤對照 -->
  <template v-else-if="out.type === 'refine'">
    <div class="sec">
      <div class="sec-h">修潤對照　理由欄 第五點</div>
      <div class="draft" style="font-size: 14px">
        <p class="indent">五、惟查，原處分機關於作成系爭處分書前，<span class="diffdel">卷內看不到有通知訴願人陳述意見的資料</span><span class="diffadd">遍查全卷，並無依行政程序法第 102 條規定通知訴願人陳述意見之相關文書可稽</span><span class="cite">卷證·全卷</span>，<span class="diffdel">也沒有依第 39 條通知，或是舉行聽證</span><span class="diffadd">亦未依同法第 39 條規定通知陳述意見或決定舉行聽證</span><span class="cite">行程法§102</span>。<span class="diffadd">又原處分機關迄本件訴願程序終結前，仍未依同法第 114 條第 1 項第 3 款規定事後給予陳述意見之機會以為補正</span><span class="cite">行程法§114 I③</span>，<span class="diffdel">所以程序上有問題</span><span class="diffadd">其踐行之行政程序即難謂無瑕疵</span>。</p>
      </div>
    </div>
    <div class="sec">
      <div class="sec-h">修潤說明</div>
      <ol class="olist">
        <li>將口語敘述改為公文慣用之法律用語（「遍查全卷」「難謂無瑕疵」），並統一以「系爭處分書」指稱。</li>
        <li>補充行政程序法第 114 條第 1 項第 3 款之補正時限論述，使程序瑕疵之法律效果完整，此為撤銷主文之關鍵環節。</li>
        <li>新增之敘述皆對應卷宗內既有來源，未引入卷宗外之資料。</li>
      </ol>
    </div>
  </template>

  <!-- 匯出檔案 -->
  <template v-else-if="out.type === 'export'">
    <div>
      <div class="fitem" style="border: 1px solid var(--line); border-radius: 3px; padding: 10px 12px">
        <span class="ic">{{ out.isPdf ? 'pdf' : 'docx' }}</span>
        <span class="ft">{{ out.fname }}<small>版型：訴願決定書．{{ out.isPdf ? 'A4 直式．4 頁' : 'Word 相容．可續行編修' }}．約 {{ out.isPdf ? '268' : '41' }} KB</small></span>
      </div>
      <div style="display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap">
        <button class="btn pri" @click="emit('preview-paper', out.isPdf)">預覽版型</button>
        <button class="btn" @click="emit('export-download', out.isPdf)">下載檔案</button>
      </div>
    </div>
  </template>

  <!-- 純 HTML（草稿、其他） -->
  <div v-else-if="out.type === 'html'" v-html="out.html"></div>
</template>
