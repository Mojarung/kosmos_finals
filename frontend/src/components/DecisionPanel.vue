<script setup>
import { gsap } from 'gsap'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { reducedMotion, useDisclosureMotion } from '../lib/panelMotion'
import { Activity, ChevronRight, Download, Globe2, Maximize2, Minimize2 } from '@lucide/vue'
import AssetsView from './AssetsView.vue'
import BuildingsView from './BuildingsView.vue'
import BubbleTabs from './BubbleTabs.vue'
import MorphLoader from './MorphLoader.vue'
import MorphPanel from './MorphPanel.vue'
import PlanView from './PlanView.vue'
import ProjectView from './ProjectView.vue'
import { exportDecision, fetchAnalysis, hasData, resetRegion, retryRegion, state } from '../store'
import { compactRub } from '../lib/format'
import { buildingMode, buildings } from '../lib/buildings'
import { buildingPlanMode, buildingPlan, downloadBuildingPlan } from '../lib/buildingPlans'

const emit = defineEmits(['asset', 'zone', 'building', 'reset'])
const TABS = [
  { value: 'assets', label: 'Объекты', hint: 'Какие объекты под водой и во сколько обойдётся ущерб' },
  { value: 'plan', label: 'Съёмка', hint: 'Какие снимки купить под бюджет и срок, и почему именно их' },
  { value: 'projects', label: 'Проекты', hint: 'Свои объекты и история решений' },
]
const expanded = computed({ get: () => state.decisionExpanded, set: value => { state.decisionExpanded = value } })
const layoutClass = computed(() => [expanded.value && 'decision-expanded', state.tab === 'plan' && state.procurementOpen && 'procurement-expanded', state.procurementMapMode && 'procurement-map-mode'].filter(Boolean).join(' '))
const disclosures = useDisclosureMotion()
const scroller = ref(null)
const content = ref(null)

watch(() => `${state.tab}:${state.planStep}`, (tab, previous) => {
  disclosures.stop()
  scroller.value?.scrollTo({ top: 0 })
  if (!content.value) return
  gsap.killTweensOf(content.value)
  gsap.set(content.value, { clearProps: 'transform,opacity' })
  if (!reducedMotion()) {
    const direction = TABS.findIndex(item => item.value === tab.split(':')[0]) >= TABS.findIndex(item => item.value === previous.split(':')[0]) ? 1 : -1
    gsap.fromTo(content.value, { x: direction * 10, opacity: 0 }, { x: 0, opacity: 1, duration: .24, ease: 'power2.out', clearProps: 'transform,opacity' })
  }
}, { flush: 'post' })

onBeforeUnmount(() => gsap.killTweensOf(content.value))

function back() {
  resetRegion()
  emit('reset')
}
</script>

<template>
  <MorphPanel :open="state.decisionOpen" class="decision-panel" :layout-class="layoutClass" data-enter>
    <template #pill>
      <button class="pill-button" v-tip="'Развернуть решение'" @click="state.decisionOpen = true">
        <Activity :size="16" class="accent" />
        <span v-if="buildingMode" class="tabular">Здания{{ buildings.data?.summary.expected_loss_rub != null ? ' · ' + compactRub(buildings.data.summary.expected_loss_rub) : '' }}</span>
        <span v-else-if="hasData" class="tabular">EL {{ compactRub(state.analysis.metrics.expected_loss_rub) }}</span>
        <span v-else>Решение</span>
      </button>
    </template>

    <header class="panel-head">
      <BubbleTabs v-model="state.tab" :options="TABS" stretch label="Раздел решения" />
      <button class="ghost-icon small panel-resize" :aria-label="expanded ? 'Сузить панель' : 'Развернуть панель'" :aria-expanded="expanded" @click="expanded = !expanded"><Minimize2 v-if="expanded" :size="17" /><Maximize2 v-else :size="17" /></button>
      <button class="ghost-icon small" v-tip="'Свернуть, карта станет шире'" aria-label="Свернуть панель" @click="state.decisionOpen = false"><ChevronRight :size="17" /></button>
    </header>

    <div ref="scroller" class="panel-scroll" @click="disclosures.click">
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
        <template v-if="state.tab === 'assets' || state.tab === 'buildings'">
          <BubbleTabs v-model="state.objectMode" class="object-mode" :options="[{ value: 'buildings', label: 'Здания', hint: 'Реальные здания и топ‑10 по риску' }, { value: 'points', label: 'Точки', hint: 'Учебные точечные объекты и обязательный расчёт' }]" stretch label="Вид объектов" />
          <BuildingsView v-if="buildingMode" @building="(feature, reveal) => emit('building', feature, reveal)" />
          <AssetsView v-else @asset="id => emit('asset', id)" />
        </template>
        <PlanView v-else-if="state.tab === 'plan'" @zone="id => emit('zone', id)" />
        <ProjectView v-else-if="state.tab === 'projects'" />
      </div>
      <Transition name="pop">
        <div v-if="state.loading && state.analysis" class="corner-loader"><MorphLoader :size="26" /></div>
      </Transition>
    </div>

    <footer v-if="(!buildingMode || buildingPlanMode) && !(state.tab === 'plan' && state.procurementOpen)" class="panel-foot">
      <button v-if="buildingPlanMode" class="primary-button wide" :disabled="!buildingPlan.result || buildingPlan.busy || buildings.busy || buildingPlan.exporting" @click="downloadBuildingPlan"><Download :size="17" />{{ buildingPlan.exporting ? 'Готовим…' : state.procurementMethod === 'value' ? 'Скачать расчёт зон и допущения' : 'Скачать решение' }}</button>
      <a v-else-if="state.tab === 'plan' && state.procurementMethod === 'value'" class="primary-button wide export-link" :href="state.voiExportHref || undefined" :aria-disabled="!state.voiExportHref || state.pointTargetIds?.length === 0" download @click="(!state.voiExportHref || state.pointTargetIds?.length === 0) && $event.preventDefault()"><Download :size="17" />Скачать расчёт зон и допущения</a>
      <button v-else class="primary-button wide" :disabled="!hasData || state.loading || (state.tab === 'plan' && state.pointTargetIds?.length === 0)" v-tip="'Один ZIP: карты воды, вероятности, сомнений и постоянной воды, таблица объектов, план закупки с причинами по каждой зоне, сравнение A/B/C, метрики и обоснование параметров'" @click="exportDecision">
        <Download :size="17" />Скачать решение
      </button>
    </footer>
  </MorphPanel>
</template>
