<script setup>
import { computed } from 'vue'
import { state, active, stages, TOOLS, runTool, gotoProcedureCheck, toolStatusText } from '../store/app.js'
import ToolOut from './ToolOut.vue'
import ProcedureCheck from './ProcedureCheck.vue'
import Composer from './Composer.vue'

const emit = defineEmits(['view-case', 'view-graph', 'preview-paper', 'export-download', 'sheet'])

const greeting = computed(() => {
  const h = new Date().getHours()
  return h < 5 ? '夜深了' : h < 11 ? '早安' : h < 18 ? '午安' : '晚安'
})
const c = computed(() => active())
const empty = computed(() => !c.value.stream.length)
// 案件識別碼用後端的 caseId 真值。之前這裡寫死「案號 1143062584」，
// 載入 synthetic-blocked-01 時畫面照樣顯示那個號，等於對承辦人報了一個不存在的案號。
const caseMeta = computed(() =>
  c.value.docs.evidence.length ? `${c.value.caseId}．卷證 ${c.value.docs.evidence.length} 份` : '尚未載入卷證',
)
</script>

<template>
  <main class="chat">
    <div class="chat-head">
      <h1>{{ c.name }}</h1>
      <span class="meta">{{ caseMeta }}</span>
      <span class="spacer"></span>
      <div class="stage-strip" title="辦案進度：解析 → 案例 → 法規 → 關聯圖 → 草稿 → 產出">
        <span v-for="(on, i) in stages" :key="i" class="stage-pip" :class="{ on }"></span>
      </div>
    </div>

    <div class="stream" id="stream">
      <div v-if="empty" class="empty">
        <h2 class="eh">{{ greeting }}，我是訴小願。</h2>
        <p class="es">你的訴願小助手。從下面的 <span class="kbd">＋</span> 開始，把卷證丟進來吧。</p>
      </div>
      <div v-else class="stream-inner">
        <div v-for="m in c.stream" :key="m.id" :data-msg="m.id" class="msg" :class="{ me: m.who === 'me' }">
          <!-- 使用者訊息 -->
          <template v-if="m.who === 'me'">
            <div class="body">
              <p :class="{ cmdline: m.isCmd }" v-html="String(m.text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>')"></p>
              <div v-if="m.files.length" class="attach-line">
                <span v-for="(f, i) in m.files" :key="i" class="file-chip"><span class="ext">{{ f.ext }}</span>{{ f.name }}</span>
              </div>
            </div>
          </template>
          <!-- AI 訊息 -->
          <template v-else>
            <div class="av ai">小願</div>
            <div class="body">
              <!-- 思考中（loading）：純問答／等待首個事件時的打字點點 -->
              <div v-if="m.kind === 'thinking'" class="typing" aria-label="小願正在思考">
                <span></span><span></span><span></span>
              </div>
              <!-- 純 HTML 回覆 -->
              <div v-else-if="m.kind === 'html'" v-html="m.html"></div>
              <!-- 程序審查卡（redirect 的 CTA 目標）。對話紀錄只存在記憶體，
                   重新整理之後串流是空的，但 screen 來自 manifest 一直都在——
                   所以 CTA 找不到既有卡片時會補一張，就是這個 kind。 -->
              <template v-else-if="m.kind === 'screen'">
                <p>以下是規則引擎算出來的程序審查結果，每一步都附法條依據，可逐步核對。</p>
                <div class="tool">
                  <div class="tool-head">
                    <span class="api">程序審查</span>
                    <span class="nm">規則引擎．零 LLM</span>
                    <span class="st"><span style="color: var(--ok)">✓</span> 完成</span>
                  </div>
                  <div class="tool-out">
                    <ProcedureCheck v-if="c.screen" :screen="c.screen" />
                  </div>
                </div>
              </template>
              <!-- 期限／天數類問題：規則引擎接手（契約 §2.4.1 一）。
                   **這一則刻意不顯示任何天數**——agent 算出來的天數在 done.answer 裡，
                   期間算錯在訴願案有實質後果，所以整段不落地，只給理由與一顆 CTA。 -->
              <template v-else-if="m.kind === 'redirect'">
                <p>{{ m.reason }}</p>
                <p style="color: var(--muted); font-size: 12px; margin-top: 6px">期限請以「程序審查」的規則引擎算式為準；本則不顯示聊天推算的天數。</p>
                <button class="btn pri" style="margin-top: 8px" @click="gotoProcedureCheck()">{{ m.cta }}</button>
              </template>
              <!-- 工具清單卡 -->
              <template v-else-if="m.kind === 'tools-help'">
                <p>我是訴願案件的辦案助理。目前開放 7 支工具（API），可用「/」呼叫，也可以直接用中文描述需求：</p>
                <div class="toolgrid">
                  <button v-for="(t, i) in TOOLS" :key="t.id" class="tcard" @click="runTool(t.id)">
                    <div class="n"><span class="idx">{{ String(i + 1).padStart(2, '0') }}</span><span class="nm">{{ t.name }}</span></div>
                    <div class="api">{{ t.api }}({{ t.param }})</div>
                    <div class="ds">{{ t.desc }}</div>
                  </button>
                </div>
                <p><span style="color: var(--muted); font-size: 12.5px; display: block; margin-top: 12px">工具會依卷宗狀態自動檢核前提條件——例如未備妥法規與案例時，草稿生成會拒絕執行，以避免產生無來源之論述。</span></p>
              </template>
              <!-- 工具區塊 -->
              <template v-else-if="m.kind === 'tool'">
                <p v-if="m.ack">{{ m.ack }}</p>
                <p v-if="m.head">{{ m.head }}</p>
                <div class="tool">
                  <div class="tool-head">
                    <span class="api">{{ m.api }}()</span>
                    <span class="nm">{{ m.name }}</span>
                    <!-- 契約 §2.3：ok／empty／failed 三種在畫面上要分得出來，
                         不能三種都畫成綠勾「完成」——那會把「查詢來源壞了」說成「查完了」。 -->
                    <span class="st">
                      <template v-if="m.running"><span class="spin"></span>執行中</template>
                      <template v-else-if="m.status === 'failed'"><span style="color: var(--alert)">✕</span> {{ toolStatusText('failed', 'chip') }}</template>
                      <template v-else-if="m.status === 'empty'"><span style="color: var(--warn)">○</span> {{ toolStatusText('empty', 'chip') }}</template>
                      <template v-else><span style="color: var(--ok)">✓</span> {{ toolStatusText('ok', 'chip') }}</template>
                    </span>
                  </div>
                  <div class="tool-steps">
                    <!-- elapsed_ms 只有 status=done 才有；**沒有**這個鍵才留白，
                         不要印一個孤零零的「s」。但 `0` 是一個真的值：n2／n3 是純規則
                         運算，後端回的就是 0 ms（QA 雲上存檔 r05），用 falsy 判斷會把
                         那兩行吃成空白、看起來像沒跑。store 已改用 `== null` 區分，
                         這裡也用 `!== ''` 而不是 truthy。
                         degraded=true 是後端說「這一節點降級跑完」，標黃。 -->
                    <div v-for="(s, i) in m.steps" :key="i" class="step" :class="{ degraded: s.degraded }">
                      <span class="tick">✓</span><span>{{ s.label }}<template v-if="s.degraded">（降級）</template></span>
                      <span v-if="s.t !== ''" class="t">{{ s.t }}s</span>
                    </div>
                  </div>
                  <div class="tool-out">
                    <ToolOut
                      v-if="m.out"
                      :out="m.out"
                      @view-case="emit('view-case', $event)"
                      @view-graph="emit('view-graph')"
                      @preview-paper="emit('preview-paper', $event)"
                      @export-download="emit('export-download', $event)"
                    />
                  </div>
                </div>
              </template>
            </div>
          </template>
        </div>
      </div>
    </div>

    <Composer @sheet="emit('sheet', $event)" />
  </main>
</template>
