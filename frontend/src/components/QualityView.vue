<script setup>
import { computed } from 'vue'
import { ArrowRight, Info } from '@lucide/vue'
import RingGauge from './RingGauge.vue'
import { state, testQuality } from '../store'
import { number, percent } from '../lib/format'

const region = computed(() => state.analysis.region)
const gain = computed(() => (testQuality.value.model.iou - testQuality.value.baseline.iou) * 100)
const METRICS = [
  ['f1', 'F1', 'Среднее гармоническое точности и полноты'],
  ['recall', 'Полнота', 'Какую долю настоящей воды нашёл метод'],
  ['precision', 'Точность', 'Какая доля найденной воды действительно вода'],
]
</script>

<template>
  <section class="block" v-tip="'IoU на независимой выборке test_core: 55 чипов, оба метода на одних и тех же валидных пикселях'">
    <span class="caption">Совпадение с ручной меткой, IoU</span>
    <div class="ring-pair">
      <RingGauge :value="testQuality.baseline.iou" label="порог VH" color="var(--risk)" :size="76" />
      <ArrowRight :size="18" class="muted" />
      <RingGauge :value="testQuality.model.iou" label="модель" :size="100" />
    </div>
    <p class="gain tabular">+{{ number(gain, 1) }} п.п. к пороговому методу</p>
  </section>

  <div class="rows">
    <div v-for="[key, label, hint] in METRICS" :key="key" v-tip="hint" class="row">
      <span>{{ label }}</span>
      <span class="compare tabular">{{ percent(testQuality.baseline[key], 1) }}<ArrowRight :size="12" /><strong>{{ percent(testQuality.model[key], 1) }}</strong></span>
    </div>
  </div>

  <section class="block">
    <span class="caption">На выбранном участке</span>
    <div class="mini-rows">
      <div><span>IoU</span><span class="compare tabular">{{ percent(region.baseline_quality?.iou, 1) }}<ArrowRight :size="12" /><b>{{ percent(region.model_quality?.iou, 1) }}</b></span></div>
      <div v-tip="'Пиксели настоящей воды, которые метод пропустил'"><span>Пропущено пикселей воды</span><span class="compare tabular">{{ number(region.baseline_quality?.fn) }}<ArrowRight :size="12" /><b>{{ number(region.model_quality?.fn) }}</b></span></div>
      <div><span>Пикселей с меткой</span><b class="tabular">{{ number(region.reference_pixels) }}</b></div>
    </div>
  </section>

  <section class="block">
    <span class="caption">Надёжность вероятностей</span>
    <div class="mini-rows">
      <div v-tip="'Ошибка калибровки: насколько p совпадает с реальной частотой воды. Меньше лучше'"><span>ECE до и после калибровки</span><span class="compare tabular">{{ number(testQuality.ece_raw, 3) }}<ArrowRight :size="12" /><b>{{ number(testQuality.ece_calibrated, 3) }}</b></span></div>
      <div v-tip="'Насколько разброс ансамбля указывает на ошибки модели. 0,5 — случайно, 1 — идеально'"><span>Неуверенность ловит ошибки</span><b class="tabular">AUROC {{ number(testQuality.uncertainty_error_auroc, 3) }}</b></div>
      <div v-tip="'Событие, которого не было в обучении'"><span>Новое событие Bolivia</span><b class="tabular">IoU {{ percent(state.analysis.quality.parts.bolivia.model.iou, 1) }}</b></div>
    </div>
  </section>

  <p class="source-line" v-tip="'Ручная метка не отделяет постоянную воду от паводковой. Учебные объекты и тариф не являются подтверждённым ущербом'">
    <Info :size="14" />Ограничения оценки
  </p>
</template>
