<script setup>
import { computed } from 'vue'
import { ChevronLeft, Crosshair, Globe2, Info, RotateCcw, Waves } from '@lucide/vue'
import AnimatedNumber from './AnimatedNumber.vue'
import ChipIngest from '../ChipIngest.vue'
import MorphPanel from './MorphPanel.vue'
import ShareBar from './ShareBar.vue'
import { chooseScene, fetchAnalysis, hasData, isLive, resetRegion, state } from '../store'
import { km, number, percent } from '../lib/format'

const emit = defineEmits(['reset'])
const region = computed(() => state.analysis?.region)

const LAYERS = [
  { value: 'model', label: 'Маска', color: 'var(--water)', hint: 'Вода по модели: вероятность выше порога калибровки' },
  { value: 'probability', label: 'Вероятность', color: 'var(--water-soft)', hint: 'Непрерывная вероятность воды p от 0 до 1' },
  { value: 'uncertainty', label: 'Неуверенность', color: 'var(--uncertain)', hint: 'Разброс пяти моделей ансамбля: где они не согласны между собой' },
  { value: 'baseline', label: 'Порог VH', color: 'var(--risk)', hint: 'Классический метод для сравнения: VH < −23,6 dB' },
  { value: 'reference', label: 'Ручная метка', color: 'var(--reference)', hint: 'Разметка Sen1Floods11. Только для проверки, в расчёт не идёт' },
  { value: 'none', label: 'Без слоя', color: 'var(--surface-3)', hint: 'Подложка без модели; радар можно включить отдельно' },
]

const segments = computed(() => {
  const r = region.value
  if (!r) return []
  const water = r.model_water_area_km2
  const dry = Math.max(r.observed_area_km2 - water, 0)
  const total = r.observed_area_km2 || 1
  return [
    { key: 'water', label: 'Вода', value: km(water), share: water / total, color: 'var(--water)', hint: `${percent(water / total, 1)} наблюдаемой площади. Постоянные водоёмы не отделены от паводка` },
    { key: 'dry', label: 'Суша', value: km(dry), share: dry / total, color: 'var(--surface-3)', hint: `${percent(dry / total, 1)} наблюдаемой площади` },
  ]
})

function reset() {
  resetRegion()
  emit('reset')
}
function openScene(id) {
  if (!chooseScene(id)) fetchAnalysis()
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
        <span v-if="hasData" class="tabular">{{ number(region.model_water_area_km2, 1) }} км² воды</span>
        <span v-else>Вода</span>
      </button>
    </template>

    <header class="panel-head">
      <div class="panel-title">
        <h2>Где вода</h2>
        <p>{{ state.region ? 'Выбранная область' : 'Весь снимок' }} · {{ km(region?.area_km2) }}</p>
      </div>
      <button class="ghost-icon small" v-tip="'Свернуть, карта станет шире'" aria-label="Свернуть панель" @click="state.observeOpen = false"><ChevronLeft :size="17" /></button>
    </header>

    <div class="panel-scroll">
      <template v-if="hasData">
        <section class="block" v-tip="'Площадь пикселей с вероятностью воды выше порога ' + state.analysis.chip.model_threshold + ' внутри области'">
          <span class="caption">Вода по модели</span>
          <div class="hero">
            <AnimatedNumber class="display" :value="region.model_water_area_km2" :format="v => number(v, 1)" />
            <span class="unit">км²</span>
          </div>
          <ShareBar :segments="segments" />
        </section>

        <div class="rows">
          <div v-if="region.data_status === 'partial_coverage'" class="row coverage-warning"
            v-tip="'Наблюдение покрывает только валидные VV/VH пиксели. Пустая часть не входит в оценку воды и риска'">
            <span>Покрытие снимка</span><strong class="tabular">{{ percent(region.observed_area_km2 / region.area_km2, 1) }}</strong>
          </div>
          <div class="row" v-tip="'Средняя вероятность воды по пикселям области'">
            <span>Средняя вероятность</span><strong class="tabular">{{ percent(region.mean_probability, 1) }}</strong>
          </div>
          <div class="row" v-tip="'Доля пикселей, где модели ансамбля заметно расходятся. Там стоит доснять'">
            <span>Неоднозначных пикселей</span><strong class="tabular">{{ percent(region.uncertain_share, 1) }}</strong>
          </div>
          <div class="row" v-tip="'Столько воды находит классический порог VH < −23,6 dB на той же области'">
            <span>Порог VH нашёл бы</span><strong class="tabular">{{ km(region.baseline_water_area_km2) }}</strong>
          </div>
        </div>
      </template>

      <div v-else-if="state.liveStatus === 'searching' || state.liveStatus === 'preparing'" class="empty">
        <Globe2 :size="26" />
        <strong>{{ state.liveStatus === 'searching' ? 'Ищем VV/VH' : 'Читаем радар и считаем модель' }}</strong>
        <span>Снимок и его дата придут из каталога Sentinel‑1 RTC.</span>
      </div>
      <div v-else-if="state.liveStatus === 'error'" class="empty">
        <Info :size="26" />
        <strong>Область пока не готова</strong>
        <span>{{ state.liveError }}</span>
      </div>
      <div v-else-if="state.analysis" class="empty">
        <Globe2 :size="26" />
        <strong>Здесь нет снимка</strong>
        <span>Выделите другую область, выберите снимок сверху или рассчитайте официальный чип ниже.</span>
      </div>

      <section class="block">
        <span class="caption">Область анализа</span>
        <div class="button-row">
          <button class="tonal-button" :class="{ active: state.drawing }" @click="toggleDrawing">
            <Crosshair :size="16" />{{ state.drawing ? 'Отменить выбор' : 'Выделить на карте' }}
          </button>
          <button class="tonal-icon" v-tip="'Вернуться ко всему снимку'" aria-label="Вернуться ко всему снимку" @click="reset"><RotateCcw :size="16" /></button>
        </div>
      </section>

      <section class="block">
        <span class="caption">Другой снимок</span>
        <ChipIngest :scenes="state.analysis?.scenes || []" @ready="openScene" />
      </section>

      <section v-if="hasData" class="block">
        <span class="caption">Слой на карте</span>
        <div class="chip-group" role="group" aria-label="Слой карты">
          <button v-for="layer in LAYERS.filter(item => !isLive || item.value !== 'reference')" :key="layer.value" v-tip="layer.hint" class="chip"
            :class="{ selected: state.layerMode === layer.value }" :aria-pressed="state.layerMode === layer.value"
            @click="state.layerMode = layer.value">
            <i :style="{ background: layer.color }"></i>{{ layer.label }}
          </button>
        </div>
        <div class="toggles">
          <button class="toggle" :aria-pressed="state.showRadar" @click="state.showRadar = !state.showRadar">
            <span>Показать радар VH</span><span class="switch" :class="{ on: state.showRadar }"></span>
          </button>
          <button class="toggle" :aria-pressed="state.showZones" @click="state.showZones = !state.showZones">
            <span>Зоны съёмки</span><span class="switch" :class="{ on: state.showZones }"></span>
          </button>
        </div>
      </section>
    </div>

    <footer class="panel-foot">
      <span class="source" v-tip="isLive ? 'Снимок: Sentinel‑1 RTC. Маска и вероятности: замороженный ансамбль из пяти U‑Net ResNet34. Ручной метки для этой области нет' : 'Снимки и ручная метка: Sen1Floods11 v1.1. Маска и вероятности: замороженный ансамбль из пяти U‑Net ResNet34 с калибровкой Platt'">
        <Info :size="14" />{{ isLive ? 'Sentinel‑1 RTC · VV/VH · U‑Net' : 'Sen1Floods11 · U‑Net' }}
      </span>
    </footer>
  </MorphPanel>
</template>
