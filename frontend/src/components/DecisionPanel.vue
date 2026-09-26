<script setup>
import { gsap } from 'gsap'
import { ref, watch } from 'vue'
import { Activity, ChevronRight, Download, Globe2 } from '@lucide/vue'
import AssetsView from './AssetsView.vue'
import BubbleTabs from './BubbleTabs.vue'
import MorphLoader from './MorphLoader.vue'
import MorphPanel from './MorphPanel.vue'
import PlanView from './PlanView.vue'
import QualityView from './QualityView.vue'
import ProjectView from './ProjectView.vue'
import { exportDecision, fetchAnalysis, hasData, resetRegion, retryRegion, state } from '../store'
import { compactRub } from '../lib/format'

const emit = defineEmits(['asset', 'zone', 'reset'])
const TABS = [
  { value: 'assets', label: 'Объекты', hint: 'Какие объекты под водой и во сколько обойдётся ущерб' },
  { value: 'plan', label: 'Съёмка', hint: 'Какие снимки купить под бюджет и срок, и почему именно их' },
  { value: 'quality', label: 'Качество', hint: 'Насколько можно верить карте воды' },
  { value: 'projects', label: 'Проекты', hint: 'Свои объекты и история решений' },
]
const scroller = ref(null)
const content = ref(null)

watch(() => state.tab, () => {
  scroller.value?.scrollTo({ top: 0 })
  if (content.value) gsap.from(content.value, { y: 12, opacity: 0, duration: 0.35, ease: 'power2.out', clearProps: 'all' })
}, { flush: 'post' })

function back() {
  resetRegion()
  emit('reset')
}
</script>

<template>
  <MorphPanel :open="state.decisionOpen" class="decision-panel" :class="{ 'procurement-expanded': state.tab === 'plan' && state.procurementOpen, 'procurement-map-mode': state.procurementMapMode }" data-enter>
    <template #pill>
      <button class="pill-button" v-tip="'Развернуть решение'" @click="state.decisionOpen = true">
        <Activity :size="16" class="accent" />
        <span v-if="hasData" class="tabular">EL {{ compactRub(state.analysis.metrics.expected_loss_rub) }}</span>
        <span v-else>Решение</span>
      </button>
    </template>

    <header class="panel-head">
      <BubbleTabs v-model="state.tab" :options="TABS" stretch label="Раздел решения" />
      <button class="ghost-icon small" v-tip="'Свернуть, карта станет шире'" aria-label="Свернуть панель" @click="state.decisionOpen = false"><ChevronRight :size="17" /></button>
    </header>

    <div ref="scroller" class="panel-scroll">
      <div v-if="state.error" class="error-note">{{ state.error }} <button @click="fetchAnalysis">Повторить</button></div>
      <div v-if="state.liveStatus === 'searching' || state.liveStatus === 'preparing'" class="empty">
        <Globe2 :size="26" />
        <strong>{{ state.liveStage || 'Готовим анализ' }}</strong>
        <span>Когда модель найдёт воду, здесь появятся объекты, зоны и три варианта закупки.</span>
      </div>
      <div v-else-if="state.liveStatus === 'error'" class="empty">
        <Globe2 :size="26" />
        <strong>Анализ не готов</strong>
        <span>{{ state.liveError }}</span>
        <button class="tonal-button" @click="retryRegion">Повторить</button>
        <button class="tonal-button" @click="back">Вернуться к демо</button>
      </div>
      <div v-else-if="state.analysis && !hasData" class="empty">
        <Globe2 :size="26" />
        <strong>Пока нет наблюдения</strong>
        <span>Для выделенной области нет снимка Sentinel‑1, поэтому считать нечего.</span>
        <button class="tonal-button" @click="back">Вернуться к снимку</button>
      </div>
      <div v-else-if="hasData" ref="content" :class="{ dimmed: state.loading }">
        <AssetsView v-if="state.tab === 'assets'" @asset="id => emit('asset', id)" />
        <PlanView v-else-if="state.tab === 'plan'" @zone="id => emit('zone', id)" />
        <QualityView v-else-if="state.tab === 'quality'" />
        <ProjectView v-else />
      </div>
      <Transition name="pop">
        <div v-if="state.loading && state.analysis" class="corner-loader"><MorphLoader :size="26" /></div>
      </Transition>
    </div>

    <footer v-if="!(state.tab === 'plan' && state.procurementOpen)" class="panel-foot">
      <button class="primary-button wide" :disabled="!hasData" v-tip="'Один ZIP: карты воды, вероятности, сомнений и постоянной воды, таблица объектов, план закупки с причинами по каждой зоне, сравнение A/B/C, метрики и обоснование параметров'" @click="exportDecision">
        <Download :size="17" />Скачать решение
      </button>
    </footer>
  </MorphPanel>
</template>
