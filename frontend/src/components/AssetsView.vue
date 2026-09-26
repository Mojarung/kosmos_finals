<script setup>
import Reveal from './Reveal.vue'
import { computed } from 'vue'
import { ArrowRight, Check, ChevronRight, Pin } from '@lucide/vue'
import AnimatedNumber from './AnimatedNumber.vue'
import CalibrationBadge from './CalibrationBadge.vue'
import { calibrationChecked, doubtThreshold, permanentSeparated, selectedAsset, state, toggleGuarantee } from '../store'
import { NA, compactRub, number, percent, plural } from '../lib/format'

const emit = defineEmits(['asset'])
const analysis = computed(() => state.analysis)
const maxLoss = computed(() => Math.max(...(analysis.value?.assets || []).map(a => a.expected_loss_rub || 0), 1))

const INPUTS = {
  p: { label: 'Вероятность воды', hint: 'p: вероятность воды из модели' },
  V: { label: 'Стоимость', hint: 'V: стоимость объекта, учебная' },
  q: { label: 'Уязвимость', hint: 'q: доля стоимости, которую объект теряет в воде' },
}
function sensitivity(id) {
  const rows = analysis.value?.sensitivity?.asset || []
  return ['p', 'V', 'q'].map(input => {
    const own = rows.filter(row => row.asset_id === id && row.input === input)
    return { input, low: own.find(row => row.factor === 0.8), high: own.find(row => row.factor === 1.2) }
  })
}
function rankRange({ low, high }) {
  if (!low || !high) return NA
  return low.rank === high.rank ? `место ${low.rank}` : `место ${low.rank} → ${high.rank}`
}
function status(asset) {
  if (asset.loss_status === 'permanent_water') return 'на реке'
  if (asset.p_flood == null) return 'нет данных'
  return 'вода ' + percent(asset.p_flood)
}
/*
 * Неопределённость рядом с вероятностью: шанс, что карта окрасила точку объекта
 * неправильно (1 − p над порогом маски, p под ним). От порога сомнений и выше
 * он фиолетовый, как сомнительные пиксели на карте.
 */
const errorText = value => value < 0.01 ? 'ошибка <1%' : 'ошибка ' + percent(value)
const doubtful = value => doubtThreshold.value != null && value >= doubtThreshold.value
const errorHint = computed(() => {
  const edge = doubtThreshold.value == null ? '' : ` От ${percent(doubtThreshold.value)} (порог ${number(doubtThreshold.value, 4)} с validation) точка сомнительная.`
  return 'Шанс ошибки карты в точке объекта: 1 − p, если карта показывает воду, и p, если сушу.' + edge
    + (calibrationChecked.value ? '' : ' Калибровка на этом снимке не проверена.')
})

/*
 * Где на независимых снимках test_core калиброванная p расходится с долей воды. Ущерб по формуле кейса
 * считается от p, поэтому пометка только предупреждает, число не меняется.
 */
const BIAS_LABEL = { overestimated: 'может быть завышена', underestimated: 'может быть занижена' }
const biasLabel = asset => BIAS_LABEL[asset.p_check?.status] || ''
function pHint(asset) {
  const check = asset.p_check
  const base = 'Вероятность воды в пикселе, где стоит объект'
  if (!check || !BIAS_LABEL[check.status]) return base
  return `${base}. На независимых снимках test_core пиксели с p от ${number(check.bin_p[0], 2)} до ${number(check.bin_p[1], 2)} `
    + `оказались водой в ${percent(check.observed_water_share)} случаев, а не в ${percent(check.bin_mean_p)}. `
    + `Ущерб считается по p, как требует кейс, поэтому он может быть ${check.status === 'overestimated' ? 'завышен' : 'занижен'}`
}

function select(asset) {
  if (state.selectedAssetId === asset.id) { state.selectedAssetId = null; return }
  emit('asset', asset.id)
}
</script>

<template>
  <section class="block" v-tip="'Сумма по объектам: вероятность воды × стоимость × уязвимость. Вероятность из модели, стоимость и уязвимость учебные'">
    <p v-if="analysis.chip.portfolio_status" class="source-line portfolio-note">10 учебных объектов: места и стоимости сценарные</p>
    <p v-if="!calibrationChecked" class="portfolio-note"><CalibrationBadge /></p>
    <p v-if="analysis.region.data_status === 'partial_coverage'" class="source-line portfolio-note">Снимок покрывает {{ percent(analysis.region.observed_area_km2 / analysis.region.area_km2, 1) }} рамки. В остальных точках ущерб не оценить.</p>
    <span class="caption">Ожидаемый ущерб · сценарий</span>
    <div class="hero">
      <AnimatedNumber class="display" :value="analysis.metrics.expected_loss_rub / 1e6" :format="v => v.toLocaleString('ru-RU', { maximumFractionDigits: 2 })" />
      <span class="unit">млн ₽</span>
      <span class="hero-aside">{{ analysis.metrics.high_probability_assets }} из {{ analysis.assets.length }}<br>в воде</span>
    </div>
    <p v-if="permanentSeparated && analysis.metrics.permanent_water_assets" class="source-line">Только паводок, без точек на постоянной воде JRC: {{ compactRub(analysis.metrics.flood_expected_loss_rub) }}. Число выше считает все 10 точек по формуле кейса p × V × q.</p>
    <p v-if="state.region" class="source-line">В рамке {{ analysis.metrics.assets_in_region }} из 10 объектов · {{ compactRub(analysis.metrics.region_expected_loss_rub) }}. План A/B/C сравнивает весь портфель.</p>
    <p v-if="analysis.metrics.unestimated_assets" class="warn-note"
      v-tip="'Радар не дал сигнала в точках этих объектов. Их стоимость × уязвимость показана как риск, который пока не оценён'">
      {{ plural(analysis.metrics.unestimated_assets, ['объект', 'объекта', 'объектов']) }} без данных: {{ compactRub(analysis.metrics.unestimated_exposure_rub) }} под риском не оценены
    </p>
  </section>

  <div class="rows asset-rows">
    <template v-for="(asset, index) in analysis.assets" :key="asset.id">
      <button class="row asset-row" :class="{ selected: state.selectedAssetId === asset.id }"
        :aria-expanded="state.selectedAssetId === asset.id" @click="select(asset)">
        <span class="rank tabular">{{ index + 1 }}</span>
        <span class="asset-main">
          <strong>{{ asset.name }}</strong>
          <span class="asset-meta">
            <span class="meter"><span :style="{ transform: `scaleX(${(asset.expected_loss_rub || 0) / maxLoss})` }"></span></span>
            <small class="tabular" :class="{ muted: asset.loss_status !== 'ok' }">{{ status(asset) }}</small>
            <small v-if="asset.map_error_probability != null" v-tip="errorHint" class="tabular asset-error"
              :class="{ doubtful: doubtful(asset.map_error_probability) }">{{ errorText(asset.map_error_probability) }}</small>
          </span>
        </span>
        <strong class="tabular">{{ compactRub(asset.expected_loss_rub) }}</strong>
        <ChevronRight :size="16" class="row-chevron" />
      </button>

      <Reveal>
        <div v-if="state.selectedAssetId === asset.id && selectedAsset" class="asset-detail">
          <p v-if="asset.flood_loss_status === 'permanent_water'" class="source-line">Точка на постоянной воде JRC. Обязательный ущерб p × V × q: {{ compactRub(asset.expected_loss_rub) }}; дополнительная оценка только паводка: {{ compactRub(asset.flood_expected_loss_rub) }}.</p>
          <div class="mini-rows">
            <div v-tip="pHint(asset)"><span>Вероятность воды<small v-if="biasLabel(asset)" class="bias-mark">{{ biasLabel(asset) }}</small></span><b class="tabular">{{ percent(asset.p_flood, 1) }}</b></div>
            <div v-tip="errorHint"><span>Шанс ошибки карты</span><b class="tabular">{{ percent(asset.map_error_probability, 1) }}</b></div>
            <div v-tip="'Различия прогнозов участников ансамбля в этой точке'"><span>Расхождение моделей</span><b class="tabular">{{ number((asset.uncertainty || 0) * 100, 1) }} п.п.</b></div>
            <div v-tip="'V × q: сколько объект теряет, если окажется в воде'"><span>Стоимость × уязвимость</span><b class="tabular">{{ compactRub(asset.value_rub) }} × {{ number(asset.vulnerability, 2) }}</b></div>
          </div>
          <span class="caption sensitivity-caption" v-tip="'Каждый вход по очереди меняется на −20% и +20%, остальные на месте. Видно, какой из них двигает объект в рейтинге'">Если вход ошибается на 20%</span>
          <div class="sensitivity">
            <div v-for="item in sensitivity(asset.id)" :key="item.input" v-tip="INPUTS[item.input].hint">
              <span class="input-key">{{ item.input }}</span>
              <span class="tabular">{{ compactRub(item.low?.expected_loss_rub) }} … {{ compactRub(item.high?.expected_loss_rub) }}</span>
              <span class="rank-shift tabular" :class="{ moved: item.low?.rank !== item.high?.rank }">{{ rankRange(item) }}</span>
            </div>
          </div>
          <button class="primary-button" :class="{ done: state.mustCover === asset.id }" @click="toggleGuarantee">
            <Check v-if="state.mustCover === asset.id" :size="16" /><Pin v-else :size="16" />
            {{ state.mustCover === asset.id ? 'Убрать из обязательных' : 'Обязательно доснять' }}
          </button>
        </div>
      </Reveal>
    </template>
  </div>

  <button class="next-row" @click="state.tab = 'plan'">Перейти к плану съёмки <ArrowRight :size="16" /></button>
</template>
