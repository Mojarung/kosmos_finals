<script setup>
import { computed } from 'vue'
import { ArrowRight, Info, Satellite } from '@lucide/vue'
import CalibrationBadge from './CalibrationBadge.vue'
import RingGauge from './RingGauge.vue'
import { calibrationChecked, chooseScene, doubtThreshold, ingest, ingestRunning, isLive, runChip, state, testQuality } from '../store'
import { compactRub, number, percent } from '../lib/format'

const region = computed(() => state.analysis.region)
const quality = computed(() => state.analysis.quality)
const gain = computed(() => (testQuality.value.model.iou - testQuality.value.baseline.iou) * 100)
const flood = computed(() => quality.value.flood_only?.test_core)
const proxy = computed(() => state.analysis.asset_proxy_check)
// Как модель переносится на Sentinel-1 RTC: те же снимки test_core с разметкой, но из каталога RTC.
const transfer = computed(() => quality.value.rtc_transfer)
/*
 * Вариант перевода яркости RTC выбран кросс-валидацией по событиям train (leave-one-event-out),
 * test_core его только проверяет. Запасные числа на случай старого API: 0,585 и 0,319 из
 * ml/artifacts/rtc/harmonization.json (cv_scores.leave_one_event_out).
 */
const transferChoice = computed(() => {
  const cv = transfer.value?.cv_scores?.leave_one_event_out ?? { quantile: 0.5846, median: 0.3188 }
  const chosen = transfer.value?.mapping === 'median' ? 'median' : 'quantile'
  const other = chosen === 'median' ? 'quantile' : 'median'
  const name = { quantile: 'квантильный', median: 'медианный' }
  return `Вариант перевода (${name[chosen]}) выбран кросс-валидацией по событиям train: IoU ${number(cv[chosen], 3)} против ${number(cv[other], 3)} у ${other === 'median' ? 'медианного' : 'квантильного'}; test_core только проверяет`
})
// Порог сомнений выбран на validation; test_core о нём только сообщает.
const doubtHint = computed(() => {
  const q = testQuality.value
  const threshold = doubtThreshold.value
  return `Порог сомнений выбран на validation (шанс ошибки ≥ ${threshold == null ? 'н/д' : number(threshold, 4)}). `
    + `На независимом test_core сомнительны ${percent(q.doubtful_share, 1)} пикселей, из них ошибается ${percent(q.doubtful_error_rate, 1)}, в среднем ${percent(q.error_rate, 1)}`
})
const METRICS = [
  ['f1', 'F1', 'Среднее точности и полноты: одна цифра на оба вида ошибок'],
  ['recall', 'Полнота', 'Какую долю настоящей воды нашёл метод'],
  ['precision', 'Точность', 'Какая доля найденной воды действительно вода'],
]
// Слабое место модели, которое не прячем: единственный крупный чип, где порог лучше.
const WEAK_CHIP = 'Bolivia_242570'
const weakReady = computed(() => state.analysis.scenes.some(scene => scene.chip_id === WEAK_CHIP))
const weakRunning = computed(() => ingestRunning.value && ingest.chipId === WEAK_CHIP)
function openWeak() {
  if (weakReady.value) chooseScene(WEAK_CHIP)
  else if (!ingestRunning.value) runChip(WEAK_CHIP)
}
</script>

<template>
  <section class="block" v-tip="'Независимая проверка test_core: 55 снимков, которые модель не видела. Оба метода сравниваются на одних и тех же пикселях'">
    <span class="caption">Совпадение с ручной разметкой, IoU</span>
    <div class="ring-pair">
      <RingGauge :value="testQuality.baseline.iou" label="порог VH" color="var(--risk)" :size="64" />
      <ArrowRight :size="18" class="muted" />
      <RingGauge :value="testQuality.model.iou" label="наша модель" :size="84" />
    </div>
    <p class="gain tabular">+{{ number(gain, 1) }} п.п. к классическому порогу</p>
  </section>

  <div class="rows">
    <div v-for="[key, label, hint] in METRICS" :key="key" v-tip="hint" class="row">
      <span>{{ label }}</span>
      <span class="compare tabular">{{ percent(testQuality.baseline[key], 1) }}<ArrowRight :size="12" /><strong>{{ percent(testQuality.model[key], 1) }}</strong></span>
    </div>
    <div v-if="flood" class="row" v-tip="'Та же проверка без постоянной воды JRC. Паводок искать труднее, чем реку, но модель всё равно заметно лучше порога'">
      <span>Только паводок, IoU</span>
      <span class="compare tabular">{{ percent(flood.baseline.iou, 1) }}<ArrowRight :size="12" /><strong>{{ percent(flood.model.iou, 1) }}</strong></span>
    </div>
  </div>

  <section class="block">
    <span class="caption">{{ isLive ? 'В этой области нет ручной разметки' : 'На этом снимке' }}</span>
    <p v-if="isLive" class="source-line">Точность на новом снимке не измерена. Цифры выше относятся к независимой проверке Sen1Floods11, а не к этой территории.</p>
    <div v-if="isLive && transfer" class="mini-rows"
      v-tip="`Те же снимки test_core, но в виде Sentinel‑1 RTC, как у выбранной рамки. Перевод яркости RTC в шкалу обучения подобран на ${transfer.fit_chips} снимках train. ${transferChoice}. Вес снимка: число пикселей воды`">
      <div><span>Модель на тех же снимках, GRD</span><b class="tabular">IoU {{ percent(transfer.water_weighted_iou.grd_model, 1) }}</b></div>
      <div><span>На RTC без перевода</span><b class="tabular">IoU {{ percent(transfer.water_weighted_iou.rtc_model_raw, 1) }}</b></div>
      <div><span>На RTC после перевода</span><b class="tabular">IoU {{ percent(transfer.water_weighted_iou[transfer.mapping === 'median' ? 'rtc_model_median' : 'rtc_model_quantile'], 1) }}</b></div>
    </div>
    <div v-else class="mini-rows">
      <div><span>IoU, порог → модель</span><span class="compare tabular">{{ percent(region.baseline_quality?.iou, 1) }}<ArrowRight :size="12" /><b>{{ percent(region.model_quality?.iou, 1) }}</b></span></div>
      <div v-if="region.model_quality_flood" v-tip="'Без пикселей постоянной воды JRC'"><span>Только паводок</span><span class="compare tabular">{{ percent(region.baseline_quality_flood?.iou, 1) }}<ArrowRight :size="12" /><b>{{ percent(region.model_quality_flood?.iou, 1) }}</b></span></div>
      <div v-tip="'Пиксели настоящей воды, которые метод не нашёл'"><span>Пропущено пикселей воды</span><span class="compare tabular">{{ number(region.baseline_quality?.fn) }}<ArrowRight :size="12" /><b>{{ number(region.model_quality?.fn) }}</b></span></div>
      <div><span>Пикселей с разметкой</span><b class="tabular">{{ number(region.reference_pixels) }}</b></div>
    </div>
  </section>

  <section class="block">
    <span class="caption">Можно ли верить вероятностям</span>
    <template v-if="!calibrationChecked">
      <p class="calib-line"><CalibrationBadge /></p>
      <p class="source-line calib-note">Цифры ниже измерены на снимках Sen1Floods11 GRD (test_core). К этому кадру RTC они не относятся.</p>
    </template>
    <div class="mini-rows">
      <div v-tip="'Ошибка калибровки ECE: насколько вероятность совпадает с реальной частотой воды. Чем меньше, тем лучше'"><span>Ошибка калибровки</span><span class="compare tabular">{{ number(testQuality.ece_raw, 3) }}<ArrowRight :size="12" /><b>{{ number(testQuality.ece_calibrated, 3) }}</b></span></div>
      <div v-tip="'Как хорошо шанс ошибки карты отличает ошибочные пиксели от верных. 0,5 значит наугад, 1 значит без промахов. По этой мере строятся сомнительные участки. Мера и порог выбраны на validation, здесь проверка на test_core'"><span>Сомнения находят ошибки</span><b class="tabular">AUROC {{ number(testQuality.doubt_error_auroc ?? testQuality.uncertainty_error_auroc, 3) }}</b></div>
      <div v-if="testQuality.doubtful_error_rate != null" v-tip="doubtHint"><span>Ошибки в сомнительных пикселях</span><b class="tabular">{{ percent(testQuality.doubtful_error_rate, 1) }}</b></div>
      <div v-tip="'Паводок в Боливии, которого не было в обучении'"><span>Новое событие, Боливия</span><b class="tabular">IoU {{ percent(quality.parts.bolivia.model.iou, 1) }}</b></div>
    </div>
  </section>

  <section v-if="proxy && proxy.n_points" class="block"
    v-tip="'В точках объектов вероятность модели сравнивается с ручной разметкой воды, стоимость и уязвимость те же. Так проверяется, как ошибка карты переходит в ущерб. Денежный ущерб на месте не измерялся'">
    <span class="caption">{{ proxy.status === 'reference_chips_region_not_labeled'
      ? `Проверка ущерба на размеченных снимках · ${proxy.n_points} точек`
      : `Проверка ущерба по разметке · ${proxy.n_points} из 10 объектов` }}</span>
    <div class="mini-rows">
      <div><span>Средняя ошибка вероятности в точке</span><b class="tabular">{{ percent(proxy.mean_absolute_probability_error, 1) }}</b></div>
      <div><span>Средняя ошибка ущерба на объект</span><b class="tabular">{{ compactRub(proxy.mean_absolute_proxy_loss_error_rub) }}</b></div>
    </div>
    <p v-if="proxy.status === 'reference_chips_region_not_labeled'" class="source-line">Для этой области разметки нет. Цифры взяты с трёх размеченных снимков: {{ proxy.reference_chip_ids.join(', ') }}.</p>
  </section>

  <section class="block">
    <span class="caption">Где модель хуже порога</span>
    <p class="weak-note">На снимке {{ WEAK_CHIP }} IoU модели около 5%, у порога около 31%: модель нашла 6% воды, порог нашёл половину. Это единственный крупный снимок, где модель заметно хуже порога. Здесь тёмная и суша: 4,3% сухих пикселей темнее −26 дБ, на других снимках Боливии обычно 0,5%, и модель теряет воду даже там, где она темнее всего. Две трети пропусков попадают в сомнительные пиксели, туда и направляется съёмка.</p>
    <button class="tonal-button weak-button" :disabled="ingestRunning && !weakRunning" @click="openWeak">
      <Satellite :size="15" />{{ weakReady ? 'Открыть этот снимок' : weakRunning ? 'Считаем снимок…' : 'Рассчитать и открыть' }}
    </button>
  </section>

  <p class="source-line" v-tip="'Ручная разметка Sen1Floods11 не делит воду на постоянную и паводок; постоянная вода берётся из слоя JRC. Учебные объекты и тариф не подтверждают реальный ущерб'">
    <Info :size="14" />Ограничения оценки
  </p>
</template>
