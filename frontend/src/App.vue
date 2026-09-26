<script setup>
import { gsap } from 'gsap'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import GlobeMap from './GlobeMap.vue'
import DecisionPanel from './components/DecisionPanel.vue'
import MorphLoader from './components/MorphLoader.vue'
import ObservePanel from './components/ObservePanel.vue'
import TopBar from './components/TopBar.vue'
import { calibrationChecked, chooseRegion, doubtThreshold, fetchAnalysis, permanentSeparated, state, testQuality } from './store'
import { number, percent } from './lib/format'

const mapRef = ref(null)
const chrome = ref(null)
const splash = ref(null)
const mapReady = ref(false)
const creditsOpen = ref(false)

// Порог маски воды: зафиксирован на validation и одинаков для всех снимков.
const maskThreshold = computed(() => state.analysis?.chip.model_threshold)
const maskNote = computed(() => maskThreshold.value == null || !['model', 'probability'].includes(state.layerMode) ? null : {
  label: `маска: p ≥ ${number(maskThreshold.value, 2)}`,
  hint: `Пиксель закрашен водой, если калиброванная вероятность p не ниже ${number(maskThreshold.value, 2)}. Порог выбран на validation по максимуму IoU и для этого снимка не менялся`,
})
// Сомнения: пиксели, где шанс ошибки карты не ниже порога с validation. Статистику test_core
// показываем, только если калибровка проверена на таком снимке (Sen1Floods11 GRD).
const doubtHint = computed(() => {
  const threshold = doubtThreshold.value
  const edge = threshold == null ? '' : `шанс ошибки карты ${percent(threshold)} и выше (порог ${number(threshold, 4)} выбран на validation)`
  if (!calibrationChecked.value) {
    return `Яркие пиксели: ${edge || 'высокий шанс ошибки карты'}. Калибровка на этом снимке не проверена: сколько здесь ошибок на самом деле, не измерено`
  }
  const rate = testQuality.value?.doubtful_error_rate
  return `Яркие пиксели: ${edge || 'высокий шанс ошибки карты'}.` + (rate == null ? '' : ` На независимом test_core из таких пикселей ошибается ${percent(rate, 1)}`)
})

// Легенда называет ровно то, что сейчас нарисовано на карте.
const legend = computed(() => {
  const water = permanentSeparated.value
    ? [{ key: 'flood', label: 'Паводок', hint: 'Вода, которой нет на карте постоянной воды JRC' },
       { key: 'river', label: 'Река, озеро', hint: state.analysis?.region.permanent_status === 'jrc_gsw'
         ? 'Постоянная вода JRC Global Surface Water. В обязательном p × V × q она не обнуляется; поправка только на паводок показана отдельно'
         : 'Постоянная вода JRC. В обязательном p × V × q она не обнуляется; поправка только на паводок показана отдельно' }]
    : [{ key: 'flood', label: 'Вода по модели', hint: 'Постоянная вода для этой области не отделена: карта показывает всю воду' }]
  return {
    model: water,
    probability: [{ key: 'probability', label: 'Вероятность воды', hint: 'Чем насыщеннее цвет, тем выше вероятность' }],
    uncertainty: [{ key: 'uncertainty', label: 'Карта может ошибаться', hint: doubtHint.value }],
    baseline: [{ key: 'baseline', label: 'Вода по порогу VH', hint: 'Классический метод для сравнения: VH ниже −23,6 дБ' }],
    reference: [{ key: 'reference', label: 'Вода по ручной метке', hint: 'Разметка Sen1Floods11. Только для проверки' }],
    none: [{ key: 'none', label: 'Слой выключен', hint: '' }],
  }[state.layerMode]
})

function chooseAsset(id) {
  state.selectedAssetId = id
  state.tab = 'assets'
  state.decisionOpen = true
  const asset = state.analysis?.assets.find(item => item.id === id)
  if (asset) mapRef.value?.focusAsset(asset)
}
function chooseZone(id) {
  const zone = state.analysis?.zones.find(item => item.id === id)
  if (zone) mapRef.value?.focusZone(zone)
}
function finishDrawing() {
  state.drawing = false
  if (window.innerWidth < 1024) state.observeOpen = true
}

/*
 * Заставка уходит, когда готовы и карта, и расчёт. Панели после неё
 * выезжают по очереди с лёгкой пружиной, а не появляются разом, и вода
 * разливается по снимку уже на глазах. Срабатывает ровно один раз.
 */
const stopBoot = watch(() => mapReady.value && state.analysis != null, async ready => {
  if (!ready || !splash.value) return
  stopBoot()
  await nextTick()
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const timeline = gsap.timeline()
  timeline.to(splash.value, { opacity: 0, scale: 1.04, duration: reduced ? 0.01 : 0.6, ease: 'power2.inOut', onComplete: () => splash.value?.remove() })
  timeline.call(() => mapRef.value?.playReveal(), [], reduced ? 0 : 0.25)
  if (!reduced && chrome.value) {
    timeline.from(chrome.value.querySelectorAll('[data-enter]'),
      { opacity: 0, y: 22, scale: 0.95, duration: 0.85, ease: 'elastic.out(1, 0.8)', stagger: 0.08, clearProps: 'all' }, '-=0.3')
  }
})

onMounted(() => {
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
          <span>{{ state.liveStage || 'Загружаем снимок' }}<b v-if="state.liveProgress" class="tabular"> · {{ Math.round(state.liveProgress * 100) }}%</b></span>
        </div>
        <div v-else-if="state.liveStatus === 'ready' && state.analysis?.chip.source_kind === 'live_sentinel1_rtc'" class="panel capsule live-flow ready" role="status">
          <span class="live-flow-dot"></span>
          <span>Sentinel‑1 RTC · {{ state.analysis.chip.source_item_ids?.length || 1 }} кадр{{ (state.analysis.chip.source_item_ids?.length || 1) > 1 ? 'а' : '' }} · {{ state.analysis.chip.pixel_size_m_approx }} м на пиксель · объекты учебные</span>
        </div>
      </Transition>
      <div class="panels">
        <ObservePanel @reset="mapRef?.focusChip()" />
        <DecisionPanel @asset="chooseAsset" @zone="chooseZone" @reset="mapRef?.focusChip()" />
      </div>
      <div class="bottom-row">
        <!-- Атрибуция подложки всегда на виду: схема из OpenStreetMap через OpenFreeMap,
             спутник Esri World Imagery (адреса тайлов в GlobeMap.vue). Подписи OSM лежат
             поверх и спутника, поэтому строка OSM есть в обоих режимах. -->
        <!-- Одной строкой: Vue убирает пробелы-переводы строк между ссылками, и они слипались. -->
        <!-- Источники прячутся за кнопкой «©» на всех ширинах: строка занимала низ экрана. -->
        <button class="credits-toggle" type="button" :aria-expanded="creditsOpen" aria-controls="map-credits"
          aria-label="Источники подложки и данных" @click="creditsOpen = !creditsOpen">©</button>
        <p id="map-credits" class="map-credits" :class="{ open: creditsOpen }" aria-label="Источники подложки"><span v-if="state.basemap === 'satellite'" class="credits-note">Подложка: современный снимок, только для ориентира, в расчёт не входит. </span><template v-if="state.basemap === 'satellite'">Снимок подложки ©&nbsp;<a href="https://www.esri.com/" target="_blank" rel="noopener">Esri</a>, Maxar, Earthstar Geographics, GIS User Community; </template><a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">©&nbsp;OpenMapTiles</a>, данные <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">©&nbsp;участники OpenStreetMap</a></p>
        <div v-if="state.analysis" class="panel map-console" data-enter>
          <div class="map-console-layer">
            <span v-for="item in legend" :key="item.key" v-tip="item.hint" class="legend-item"><i class="legend-water" :class="item.key"></i>{{ item.label }}</span>
            <span v-if="maskNote" v-tip="maskNote.hint" class="legend-item legend-note tabular">{{ maskNote.label }}</span>
            <span v-if="state.radarGaps && state.layerMode !== 'none'" class="legend-item"
              v-tip="`Серые пиксели: радар Sentinel‑1 здесь не дал сигнала (${number(state.radarGaps)} пикс., например шов на стыке кадров). Вода там или суша, неизвестно; в площадь воды и ущерб они не входят`"><i class="legend-water nodata"></i>Нет сигнала</span>
            <span class="legend-item asset-key" v-tip="'Номер показывает место объекта в рейтинге ущерба. Оранжевый кружок: объект, скорее всего, в воде. Зелёное кольцо: объект внутри зоны заказа'"><i class="legend-asset"></i>Объект</span>
            <span v-if="state.showZones" class="legend-item zone-key" v-tip="'Пунктиром показаны зоны, которые можно заказать: оранжевые вокруг объектов, фиолетовые там, где карта может ошибаться'"><i class="legend-zone"></i>Зона</span>
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
              <span>Видимость слоя <b class="tabular">{{ state.overlayOpacity }}%</b></span>
              <input v-model.number="state.overlayOpacity" type="range" min="0" max="100" step="1"
                aria-label="Видимость слоя на карте" :style="{ '--progress': state.overlayOpacity + '%' }" />
            </label>
          </div>
        </div>
      </div>
    </div>

    <div ref="splash" class="splash">
      <MorphLoader :size="84" />
      <strong class="display">FloodValue</strong>
      <p>{{ state.error || 'Открываем снимок Sentinel‑1' }}</p>
      <button v-if="state.error" class="primary-button" @click="fetchAnalysis">Повторить</button>
    </div>
  </main>
</template>
