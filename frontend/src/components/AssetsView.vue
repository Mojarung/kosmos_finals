<script setup>
import { computed } from 'vue'
import { ArrowRight, Check, ChevronRight, Pin } from '@lucide/vue'
import AnimatedNumber from './AnimatedNumber.vue'
import { permanentSeparated, selectedAsset, state, toggleGuarantee } from '../store'
import { compactRub, number, percent, plural } from '../lib/format'

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
  if (!low || !high) return '—'
  return low.rank === high.rank ? `место ${low.rank}` : `место ${low.rank} → ${high.rank}`
}
function status(asset) {
  if (asset.loss_status === 'permanent_water') return 'на реке'
  if (asset.p_flood == null) return 'нет данных'
  return 'вода ' + percent(asset.p_flood)
}

function select(asset) {
  if (state.selectedAssetId === asset.id) { state.selectedAssetId = null; return }
  emit('asset', asset.id)
}
</script>

<template>
  <section class="block" v-tip="'Сумма по объектам: вероятность воды × стоимость × уязвимость. Вероятность из модели, стоимость и уязвимость учебные'">
    <p v-if="analysis.chip.portfolio_status" class="source-line portfolio-note">10 учебных объектов: места и стоимости сценарные</p>
    <p v-if="analysis.region.data_status === 'partial_coverage'" class="source-line portfolio-note">Снимок покрывает {{ percent(analysis.region.observed_area_km2 / analysis.region.area_km2, 1) }} рамки. В остальных точках ущерб не оценить.</p>
    <span class="caption">{{ permanentSeparated ? 'Ожидаемый ущерб от паводка' : 'Ожидаемый ущерб' }}</span>
    <div class="hero">
      <AnimatedNumber class="display" :value="analysis.metrics.expected_loss_rub / 1e6" :format="v => v.toLocaleString('ru-RU', { maximumFractionDigits: 2 })" />
      <span class="unit">млн ₽</span>
      <span class="hero-aside">{{ analysis.metrics.high_probability_assets }} из {{ analysis.assets.length }}<br>в воде</span>
    </div>
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
          </span>
        </span>
        <strong class="tabular">{{ compactRub(asset.expected_loss_rub) }}</strong>
        <ChevronRight :size="16" class="row-chevron" />
      </button>

      <Transition name="expand">
        <div v-if="state.selectedAssetId === asset.id && selectedAsset" class="asset-detail">
          <p v-if="asset.loss_status === 'permanent_water'" class="source-line">Точка стоит на постоянной воде JRC: вода здесь была и до паводка, ущерб от паводка не считается.</p>
          <div class="mini-rows">
            <div v-tip="'Вероятность воды в пикселе, где стоит объект'"><span>Вероятность воды</span><b class="tabular">{{ percent(asset.p_flood, 1) }}</b></div>
            <div v-tip="'Вероятность, что карта окрасила точку объекта неправильно'"><span>Шанс ошибки карты</span><b class="tabular">{{ percent(asset.map_error_probability, 1) }}</b></div>
            <div v-tip="'Насколько расходятся пять моделей в этой точке, в процентных пунктах вероятности'"><span>Разброс пяти моделей</span><b class="tabular">{{ number((asset.uncertainty || 0) * 100, 1) }} п.п.</b></div>
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
      </Transition>
    </template>
  </div>

  <button class="next-row" @click="state.tab = 'plan'">Перейти к плану съёмки <ArrowRight :size="16" /></button>
</template>
