<script setup>
import { gsap } from 'gsap'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import GlobeMap from './GlobeMap.vue'
import DecisionPanel from './components/DecisionPanel.vue'
import MorphLoader from './components/MorphLoader.vue'
import ObservePanel from './components/ObservePanel.vue'
import TopBar from './components/TopBar.vue'
import { chooseRegion, fetchAnalysis, state } from './store'
import { initTheme } from './lib/theme'

const mapRef = ref(null)
const chrome = ref(null)
const splash = ref(null)
const mapReady = ref(false)

const LEGEND = {
  model: 'Вода · модель', probability: 'Вероятность воды', uncertainty: 'Неуверенность ансамбля',
  baseline: 'Вода · порог VH', reference: 'Вода · ручная метка', none: 'Слой выключен',
}
const legend = computed(() => LEGEND[state.layerMode])

function chooseAsset(id) {
  state.selectedAssetId = id
  state.tab = 'assets'
  state.decisionOpen = true
  const asset = state.analysis?.assets.find(item => item.id === id)
  if (asset) mapRef.value?.focusAsset(asset)
}

/*
 * Заставка уходит, когда готовы и карта, и расчёт. Панели после неё
 * выезжают по очереди с лёгкой пружиной, а не появляются разом.
 */
watch(() => [mapReady.value, state.analysis != null], async ([map, data]) => {
  if (!map || !data || !splash.value) return
  await nextTick()
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const timeline = gsap.timeline()
  timeline.to(splash.value, { opacity: 0, scale: 1.04, duration: reduced ? 0.01 : 0.6, ease: 'power2.inOut', onComplete: () => splash.value?.remove() })
  if (!reduced && chrome.value) {
    timeline.from(chrome.value.querySelectorAll('[data-enter]'),
      { opacity: 0, y: 22, scale: 0.95, duration: 0.85, ease: 'elastic.out(1, 0.8)', stagger: 0.08, clearProps: 'all' }, '-=0.3')
  }
})

onMounted(() => {
  initTheme()
  // На планшете и телефоне панели стартуют свёрнутыми: карта важнее.
  if (window.innerWidth < 1024) Object.assign(state, { observeOpen: false, decisionOpen: false })
  fetchAnalysis()
})
</script>

<template>
  <main class="atlas">
    <GlobeMap ref="mapRef" @ready="mapReady = true" @region="chooseRegion" @asset="chooseAsset"
      @draw-complete="state.drawing = false" />

    <div ref="chrome" class="chrome">
      <TopBar @globe="mapRef?.showGlobe()" @focus-chip="mapRef?.focusChip()" @tilt="mapRef?.toggleTilt()" />
      <div class="panels">
        <ObservePanel @reset="mapRef?.focusChip()" />
        <DecisionPanel @asset="chooseAsset" @reset="mapRef?.focusChip()" />
      </div>
      <div class="bottom-row">
        <div v-if="state.analysis" class="panel capsule legend" data-enter>
          <span class="legend-item"><i class="legend-water" :class="state.layerMode"></i>{{ legend }}</span>
          <span class="legend-item"><i class="legend-asset"></i>Объект</span>
          <span v-if="state.showZones" class="legend-item"><i class="legend-zone"></i>Зона съёмки</span>
        </div>
      </div>
    </div>

    <div ref="splash" class="splash">
      <MorphLoader :size="84" />
      <strong class="display">FloodValue</strong>
      <p>{{ state.error || 'Открываем наблюдение Sentinel‑1' }}</p>
      <button v-if="state.error" class="primary-button" @click="fetchAnalysis">Повторить</button>
    </div>
  </main>
</template>
