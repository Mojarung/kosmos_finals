<script setup>
import { computed } from 'vue'
import { ChevronLeft, Crosshair, Globe2, Info, LoaderCircle, RotateCcw, Waves } from '@lucide/vue'
import AnimatedNumber from './AnimatedNumber.vue'
import CalibrationBadge from './CalibrationBadge.vue'
import MorphPanel from './MorphPanel.vue'
import ShareBar from './ShareBar.vue'
import { calibrationChecked, doubtThreshold, hasData, isLive, permanentJob, permanentSeparated, resetRegion, retryRegion, state, testQuality } from '../store'
import { km, number, percent } from '../lib/format'

const emit = defineEmits(['reset'])
const region = computed(() => state.analysis?.region)
const livePending = computed(() => ['searching', 'preparing', 'error'].includes(state.liveStatus))

const LAYERS = [
  { value: 'model', label: 'Вода', color: 'var(--water)', hint: 'Вода по модели. Паводок и постоянная вода окрашены по-разному' },
  { value: 'probability', label: 'Вероятность', color: 'var(--water-soft)', hint: 'Вероятность воды в каждой точке, от 0 до 100%' },
  { value: 'uncertainty', label: 'Сомнения', color: 'var(--uncertain)', hint: 'Где карта может ошибаться. Туда в первую очередь нужен новый снимок' },
  { value: 'baseline', label: 'Порог VH', color: 'var(--risk)', hint: 'Классический метод для сравнения: вода там, где VH ниже −23,6 дБ' },
  { value: 'reference', label: 'Ручная метка', color: 'var(--reference)', hint: 'Разметка Sen1Floods11. Только для проверки, в расчёт не идёт' },
  { value: 'none', label: 'Без слоя', color: 'var(--surface-3)', hint: 'Только подложка. Радар можно включить отдельно' },
]

// Главное число показывает паводок: постоянная вода к ущербу не относится.
const headline = computed(() => {
  const r = region.value
  if (!r) return null
  return permanentSeparated.value
    ? { caption: 'Паводок по модели', value: r.flood_water_area_km2,
        hint: r.permanent_status === 'jrc_gsw'
          ? 'Вода по модели минус постоянная вода JRC Global Surface Water: реки и озёра, которые были водой и в 1984, и в 2021 году'
          : 'Вода по модели минус постоянная вода JRC (реки и озёра, которые были водой и в 1984, и в 2018 году)' }
    : { caption: 'Вода по модели', value: r.model_water_area_km2,
        hint: 'Постоянная вода для этой области не отделена: число включает реки и озёра' }
})
// Порог маски стоит рядом с площадью: число посчитано ровно по этой маске.
const maskThreshold = computed(() => state.analysis?.chip.model_threshold)
const maskHint = computed(() => maskThreshold.value == null ? '' : ` Маска воды: калиброванная p ≥ ${number(maskThreshold.value, 2)}, порог выбран на validation и зафиксирован.`)

// Сомнительная площадь: порог с validation; ошибки test_core только для проверенной калибровки.
const doubtHint = computed(() => {
  const threshold = doubtThreshold.value
  const edge = threshold == null ? 'Площадь, где карта может ошибаться.'
    : `Площадь, где шанс ошибки карты ${percent(threshold)} и выше (порог ${number(threshold, 4)} выбран на validation).`
  if (!calibrationChecked.value) return `${edge} Калибровка на этом снимке не проверена: сколько здесь ошибок на самом деле, не измерено`
  const rate = testQuality.value?.doubtful_error_rate
  return rate == null ? edge : `${edge} На независимом test_core из таких пикселей ошибается ${percent(rate, 1)}`
})

const segments = computed(() => {
  const r = region.value
  if (!r) return []
  const total = r.observed_area_km2 || 1
  const water = r.model_water_area_km2
  const dry = Math.max(r.observed_area_km2 - water, 0)
  const parts = permanentSeparated.value
    ? [{ key: 'flood', label: 'Паводок', value: r.flood_water_area_km2, color: 'var(--water-shallow)' },
       { key: 'river', label: 'Река, озеро', value: r.permanent_water_area_km2, color: 'var(--water-deep)' }]
    : [{ key: 'water', label: 'Вода', value: water, color: 'var(--water)' }]
  return [...parts, { key: 'dry', label: 'Суша', value: dry, color: 'var(--surface-3)' }]
    .map(part => ({ ...part, share: part.value / total, value: km(part.value), hint: `${percent(part.value / total, 1)} снятой площади` }))
})

function reset() {
  resetRegion()
  emit('reset')
}
function toggleDrawing() {
  state.drawing = !state.drawing
  if (state.drawing && window.innerWidth < 1024) state.observeOpen = false
}
</script>

<template>
  <MorphPanel :open="state.observeOpen" class="observe-panel" data-enter>
    <template #pill>
      <button class="pill-button" v-tip="'Развернуть панель воды'" @click="state.observeOpen = true">
        <Waves :size="16" class="accent" />
        <span v-if="hasData && headline" class="tabular">{{ number(headline.value, headline.value < 10 ? 2 : 1) }} км² {{ permanentSeparated ? 'паводка' : 'воды' }}</span>
        <span v-else>Вода</span>
      </button>
    </template>

    <header class="panel-head">
      <div class="panel-title">
        <h2>Где вода</h2>
        <p>{{ livePending ? 'Выбранная область · ' + (state.liveStatus === 'error' ? 'нет результата' : 'загружаем снимок') : (state.region ? 'Выбранная область' : 'Весь снимок') + ' · ' + km(region?.area_km2) }}</p>
      </div>
      <button class="ghost-icon small" v-tip="'Свернуть, карта станет шире'" aria-label="Свернуть панель" @click="state.observeOpen = false"><ChevronLeft :size="17" /></button>
    </header>

    <div class="panel-scroll">
      <template v-if="hasData">
        <section class="block" v-tip="headline.hint + maskHint">
          <span class="caption caption-row">{{ headline.caption }}<span v-if="maskThreshold != null" class="caption-note tabular">маска: p ≥ {{ number(maskThreshold, 2) }}</span></span>
          <div class="hero">
            <AnimatedNumber class="display" :value="headline.value" :format="v => number(v, headline.value < 10 ? 2 : 1)" />
            <span class="unit">км²</span>
          </div>
          <ShareBar :segments="segments" />
          <p v-if="state.analysis.chip.timing?.analysis_mode === 'retrospective_dataset_demo'" class="source-line temporal-note"
            v-tip="state.analysis.chip.timing.water_reference_note">Исторический пример · JRC не обязательно был доступен на дату паводка</p>
        </section>

        <div class="rows">
          <div v-if="region.data_status === 'partial_coverage'" class="row coverage-warning"
            v-tip="'Радар дал сигнал только на части рамки. Пустая часть не входит ни в воду, ни в ущерб'">
            <span>Снимок покрывает</span><strong class="tabular">{{ percent(region.observed_area_km2 / region.area_km2, 1) }}</strong>
          </div>
          <div v-if="isLive" class="row" v-tip="'Доля пикселей, где у радарного снимка есть оба канала VV и VH'"><span>Покрытие радара</span><strong class="tabular">{{ percent(region.observed_area_km2 / region.area_km2, 1) }}</strong></div>
          <div v-if="isLive" class="row" v-tip="'Физический размер пикселя меняется незначительно из-за округления сетки до 32 пикселей'"><span>Разрешение анализа</span><strong class="tabular">≈ {{ state.analysis.chip.pixel_size_m_approx }} м/пикс.</strong></div>
          <p v-if="isLive" class="source-line">Ручной метки нет: IoU/F1 для этой области не посчитать. Проверьте карту по независимому источнику.</p>
          <div v-if="isLive && permanentJob.sceneId === state.sceneId && permanentJob.status !== 'done'" class="row permanent-job"
            v-tip="permanentJob.status === 'error' ? permanentJob.message : 'Берём из архива JRC воду, которая была и в 1984, и в 2021 году, и вычитаем её из паводка'">
            <span>{{ permanentJob.status === 'error' ? 'Постоянную воду отделить не удалось' : 'Ищем реки и озёра в архиве JRC…' }}</span>
            <LoaderCircle v-if="permanentJob.status === 'running'" :size="15" class="spin accent" />
          </div>
          <div class="row" v-tip="doubtHint">
            <span>Сомнительная площадь</span><strong class="tabular">{{ km(region.doubtful_area_km2) }}</strong>
          </div>
          <div v-if="!calibrationChecked" class="badge-line"><CalibrationBadge /></div>
          <div class="row" v-tip="'Столько воды находит классический метод на той же площади: VH ниже −23,6 дБ'">
            <span>Порог VH нашёл бы</span><strong class="tabular">{{ km(region.baseline_water_area_km2) }}</strong>
          </div>
        </div>
      </template>

      <div v-else-if="state.liveStatus === 'searching' || state.liveStatus === 'preparing'" class="empty">
        <Globe2 :size="26" />
        <strong>{{ state.liveStage || 'Загружаем радар' }}</strong>
        <span v-if="state.liveProgress" class="tabular">{{ Math.round(state.liveProgress * 100) }}% обработки</span>
        <span>Снимок и его дата придут из каталога Sentinel‑1 RTC.</span>
      </div>
      <div v-else-if="state.liveStatus === 'error'" class="empty">
        <Info :size="26" />
        <strong>Область пока не готова</strong>
        <span>{{ state.liveError }}</span>
        <button class="tonal-button" @click="retryRegion">Повторить загрузку</button>
      </div>
      <div v-else-if="state.analysis" class="empty">
        <Globe2 :size="26" />
        <strong>Здесь нет снимка</strong>
        <span>Выделите другую область на карте или выберите страну в шапке.</span>
      </div>

      <section class="block">
        <span class="caption">Область на карте</span>
        <div class="button-row">
          <button class="tonal-button" :class="{ active: state.drawing }" @click="toggleDrawing">
            <Crosshair :size="16" />{{ state.drawing ? 'Отменить рамку' : 'Выделить рамкой' }}
          </button>
          <button class="tonal-icon" v-tip="'Вернуться ко всему снимку'" aria-label="Вернуться ко всему снимку" @click="reset"><RotateCcw :size="16" /></button>
        </div>
      </section>

      <section v-if="hasData" class="block">
        <span class="caption">Что показать на карте</span>
        <div class="chip-group" role="group" aria-label="Слой карты">
          <button v-for="layer in LAYERS.filter(item => !isLive || item.value !== 'reference')" :key="layer.value" v-tip="layer.hint" class="chip"
            :class="{ selected: state.layerMode === layer.value }" :aria-pressed="state.layerMode === layer.value"
            @click="state.layerMode = layer.value">
            <i :style="{ background: layer.color }"></i>{{ layer.label }}
          </button>
        </div>
        <div class="toggles">
          <button class="toggle" :aria-pressed="state.showRadar" @click="state.showRadar = !state.showRadar">
            <span>Радарный снимок VH</span><span class="switch" :class="{ on: state.showRadar }"></span>
          </button>
          <button class="toggle" :aria-pressed="state.showZones" @click="state.showZones = !state.showZones">
            <span>Зоны для заказа съёмки</span><span class="switch" :class="{ on: state.showZones }"></span>
          </button>
        </div>
      </section>
    </div>

    <footer class="panel-foot">
      <span class="source" v-tip="'Замороженная модель next_optical. S2 используется только в дату радара или за 1–2 дня до него; иначе работает радарная ветка.'">
        <Info :size="14" />Модель {{ state.analysis?.chip.model?.mode === 'S1+S2' ? 'S1 + S2' : 'S1 · без оптики' }}
      </span>
    </footer>
  </MorphPanel>
</template>
