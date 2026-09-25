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
function finishDrawing() {
  state.drawing = false
  if (window.innerWidth < 1024) state.observeOpen = true
}

/*
 * Заставка уходит, когда готовы и карта, и расчёт. Панели после неё
 * выезжают по очереди с лёгкой пружиной, а не появляются разом.
 */
/*
 * Срабатывает ровно один раз. Раньше геттер возвращал новый массив на каждый
 * ответ API, и въезд панелей перезапускался при смене страны и даже при
 * движении слайдера бюджета: всё гасло до нуля и выезжало заново.
 */
const stopBoot = watch(() => mapReady.value && state.analysis != null, async ready => {
  if (!ready || !splash.value) return
  stopBoot()
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
      @draw-complete="finishDrawing" />

    <div ref="chrome" class="chrome">
      <TopBar @globe="mapRef?.showGlobe()" @focus-chip="mapRef?.focusChip()" @tilt="mapRef?.toggleTilt()" />
      <Transition name="pop">
        <div v-if="state.liveStatus === 'searching' || state.liveStatus === 'preparing'" class="panel capsule live-flow" role="status">
          <MorphLoader :size="25" />
          <span>{{ state.liveStatus === 'searching' ? 'Ищем свежие VV/VH снимки' : 'Читаем COG · считаем ансамбль' }}</span>
        </div>
        <div v-else-if="state.liveStatus === 'ready' && state.analysis?.chip.source_kind === 'live_sentinel1_rtc'" class="panel capsule live-flow ready" role="status">
          <span class="live-flow-dot"></span>
          <span>Sentinel‑1 RTC · {{ state.analysis.chip.source_item_ids?.length || 1 }} кадр{{ (state.analysis.chip.source_item_ids?.length || 1) > 1 ? 'а' : '' }} · {{ state.analysis.chip.pixel_size_m_approx }} м/пикс. · учебные объекты</span>
        </div>
      </Transition>
      <div class="panels">
        <ObservePanel @reset="mapRef?.focusChip()" />
        <DecisionPanel @asset="chooseAsset" @reset="mapRef?.focusChip()" />
      </div>
      <div class="bottom-row">
        <div v-if="state.analysis" class="panel map-console" data-enter>
          <div class="map-console-layer">
            <span class="legend-item"><i class="legend-water" :class="state.layerMode"></i>{{ legend }}</span>
            <span class="legend-item asset-key"><i class="legend-asset"></i>Объект</span>
            <span v-if="state.showZones" class="legend-item zone-key"><i class="legend-zone"></i>Зона</span>
          </div>
          <div class="map-console-tools">
            <div class="basemap-switch" role="group" aria-label="Подложка карты">
              <button :class="{ active: state.basemap === 'street' }" :aria-pressed="state.basemap === 'street'"
                @click="state.basemap = 'street'">Схема</button>
              <button v-tip="'Спутниковая подложка для ориентации. Её дата может отличаться от Sentinel‑1; расчёт сделан по радару'"
                :class="{ active: state.basemap === 'satellite' }" :aria-pressed="state.basemap === 'satellite'"
                @click="state.basemap = 'satellite'">Спутник</button>
            </div>
            <label class="opacity-control">
              <span>Слой <b class="tabular">{{ state.overlayOpacity }}%</b></span>
              <input v-model.number="state.overlayOpacity" type="range" min="0" max="100" step="1"
                aria-label="Непрозрачность слоя модели" :style="{ '--progress': state.overlayOpacity + '%' }" />
            </label>
          </div>
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
