<script setup>
import {
  state, lawCards, caseCards, factCards, showAddLaw,
  openTab, linkToSentences, lawUsedCount,
} from '../store/workbench.js'
import ProcPanel from './ProcPanel.vue'
import ChatPanel from './ChatPanel.vue'

const emit = defineEmits(['open-law', 'open-case', 'add-law', 'remove-law'])

function tabClick(t) {
  openTab(t)
}
</script>

<template>
  <div class="leftcol">
    <div class="pane">
      <div class="tabs" role="tablist">
        <button class="tab" role="tab" :aria-selected="state.activeTab === 'fact'" @click="tabClick('fact')">
          案情與爭點<span class="badge">{{ factCards.length }}</span>
        </button>
        <button class="tab" role="tab" :aria-selected="state.activeTab === 'law'" @click="tabClick('law')">
          相關法條<span class="badge">{{ lawCards.length }}</span>
        </button>
        <button class="tab" role="tab" :aria-selected="state.activeTab === 'case'" @click="tabClick('case')">
          相似案例<span class="badge">{{ caseCards.length }}</span>
        </button>
        <span style="flex: 1"></span>
        <button v-show="showAddLaw" class="btn ghost sm" style="margin: 0 4px" @click="emit('add-law')">
          <span class="mi" style="font-size: 15px; vertical-align: -3px">add</span> 加入法條
        </button>
      </div>
      <div class="tabbody">
        <!-- 案情與爭點 -->
        <div v-show="state.activeTab === 'fact'">
          <ProcPanel />
          <div
            v-for="f in factCards"
            :key="f.id"
            class="itm"
            :id="'ref-' + f.id"
            :class="{ hit: state.hitRef === f.id }"
            style="cursor: pointer"
            @click="linkToSentences(f.id)"
          >
            <div class="row">
              <span class="nm">{{ f.k }}</span>
              <span v-if="f.users" class="tag n">引用 {{ f.users }} 句</span>
              <span class="tag k">{{ f.tag }}</span>
            </div>
            <div style="padding: 0 12px 11px; font-size: 13px; color: var(--ink-2); line-height: 1.8">
              {{ f.d }}
              <div style="margin-top: 7px; font-size: 12.5px; color: var(--ink-3); font-family: var(--mono)">{{ f.src }}</div>
            </div>
          </div>
        </div>

        <!-- 相關法條 -->
        <div v-show="state.activeTab === 'law'">
          <div
            v-for="l in lawCards"
            :key="l.id"
            class="itm"
            :id="'ref-' + l.id"
            data-law
            :class="{ hit: state.hitRef === l.id }"
            @click="linkToSentences(l.id)"
          >
            <div class="row">
              <span class="nm">{{ l.law }}　{{ l.art }}</span>
              <span v-if="l.users" class="tag n">引用 {{ l.users }} 句</span>
              <span v-if="l.mine" class="tag k2">自行加入</span>
              <span v-else class="tag ai">AI 建議</span>
              <button class="del" title="移除" @click.stop="emit('remove-law', l.id, lawUsedCount(l.id))">×</button>
            </div>
            <div style="padding: 0 12px 11px; font-size: 13px; color: var(--ink-2); line-height: 1.7">
              <div style="color: var(--ink); font-weight: 500; margin-bottom: 2px">{{ l.tag }}</div>
              {{ l.keyPoint }}
              <div class="aiwhy">
                <b>AI 採用理由</b>{{ l.mine ? '由承辦人手動加入，未經 AI 評估。' : l.why }}
              </div>
              <div style="margin-top: 8px; text-align: right">
                <span class="seefull" @click.stop="emit('open-law', l.id)">
                  看全文<span class="mi" style="font-size: 16px; vertical-align: -3px">chevron_right</span>
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- 相似案例 -->
        <div v-show="state.activeTab === 'case'">
          <div
            v-for="c in caseCards"
            :key="c.id"
            class="itm"
            :id="'ref-' + c.id"
            data-caserow
            :class="{ hit: state.hitRef === c.id }"
            @click="linkToSentences(c.id)"
          >
            <div class="row">
              <span class="nm">{{ c.t }}</span>
              <span v-if="c.users" class="tag n">引用 {{ c.users }} 句</span>
              <span class="tag ai">AI 建議</span>
            </div>
            <div style="padding: 0 12px 11px; font-size: 13px; color: var(--ink-2); line-height: 1.7">
              {{ c.d }}
              <div class="aiwhy"><b>AI 採用理由</b>{{ c.why }}</div>
              <div style="margin-top: 8px; display: flex; align-items: center; gap: 9px">
                <span class="sim"><span class="simbar"><i :style="{ width: c.sim + '%' }"></i></span>相似度 {{ c.sim }}%</span>
                <span style="margin-left: auto">
                  <span class="seefull" @click.stop="emit('open-case', c.id)">
                    看全文<span class="mi" style="font-size: 16px; vertical-align: -3px">chevron_right</span>
                  </span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <ChatPanel />
  </div>
</template>
