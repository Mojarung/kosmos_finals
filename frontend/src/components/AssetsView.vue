<script setup>
import { computed } from 'vue'
import { ArrowRight, Check, ChevronRight, Pin } from '@lucide/vue'
import AnimatedNumber from './AnimatedNumber.vue'
import { selectedAsset, state, toggleGuarantee } from '../store'
import { compactRub, number, percent } from '../lib/format'

const emit = defineEmits(['asset'])
const analysis = computed(() => state.analysis)
const maxLoss = computed(() => Math.max(...(analysis.value?.assets || []).map(a => a.expected_loss_rub), 1))

const INPUTS = {
  p: { hint: 'p: вероятность воды из модели' },
  V: { hint: 'V: стоимость объекта, учебная' },
  q: { hint: 'q: уязвимость, доля стоимости под ударом воды' },
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

function select(asset) {
  if (state.selectedAssetId === asset.id) { state.selectedAssetId = null; return }
  emit('asset', asset.id)
}
</script>

<template>
  <section class="block" v-tip="'Σ p × стоимость × уязвимость. Вероятность p из модели, стоимость и уязвимость объектов учебные'">
    <p v-if="analysis.chip.portfolio_status" class="source-line portfolio-note">Учебный портфель · 10 сценарных точек и стоимостей</p>
    <p v-if="analysis.region.data_status === 'partial_coverage'" class="source-line portfolio-note">Снимок покрывает {{ percent(analysis.region.observed_area_km2 / analysis.region.area_km2, 1) }} рамки. В остальных точках p не рассчитывается.</p>
    <span class="caption">Ожидаемый ущерб портфеля</span>
    <div class="hero">
      <AnimatedNumber class="display" :value="analysis.metrics.expected_loss_rub / 1e6" :format="v => v.toLocaleString('ru-RU', { maximumFractionDigits: 2 })" />
      <span class="unit">млн ₽</span>
      <span class="hero-aside">{{ analysis.metrics.high_probability_assets }} из {{ analysis.assets.length }}<br>в воде</span>
    </div>
  </section>

  <div class="rows asset-rows">
    <template v-for="(asset, index) in analysis.assets" :key="asset.id">
      <button class="row asset-row" :class="{ selected: state.selectedAssetId === asset.id }"
        :aria-expanded="state.selectedAssetId === asset.id" @click="select(asset)">
        <span class="rank tabular">{{ index + 1 }}</span>
        <span class="asset-main">
          <strong>{{ asset.name }}</strong>
          <span class="asset-meta">
            <span class="meter"><span :style="{ transform: `scaleX(${asset.expected_loss_rub / maxLoss})` }"></span></span>
            <small class="tabular">p {{ percent(asset.p_flood) }}</small>
          </span>
        </span>
        <strong class="tabular">{{ compactRub(asset.expected_loss_rub) }}</strong>
        <ChevronRight :size="16" class="row-chevron" />
      </button>

      <Transition name="expand">
        <div v-if="state.selectedAssetId === asset.id && selectedAsset" class="asset-detail">
          <div class="mini-rows">
            <div v-tip="'Вероятность воды в сетке 3×3 пикселя вокруг объекта'"><span>Вероятность воды</span><b class="tabular">{{ percent(asset.p_flood, 1) }}</b></div>
            <div v-tip="'Насколько расходятся пять моделей ансамбля в этой точке'"><span>Разброс ансамбля</span><b class="tabular">{{ percent(asset.uncertainty, 1) }}</b></div>
            <div><span>Стоимость × уязвимость</span><b class="tabular">{{ compactRub(asset.value_rub) }} × {{ number(asset.vulnerability, 2) }}</b></div>
          </div>
          <span class="caption sensitivity-caption" v-tip="'Каждый вход меняется на −20% и +20% по отдельности, остальные на месте. Видно, какой из них сильнее двигает объект в рейтинге'">Что сдвигает приоритет, ±20%</span>
          <div class="sensitivity">
            <div v-for="item in sensitivity(asset.id)" :key="item.input" v-tip="INPUTS[item.input].hint">
              <span class="input-key">{{ item.input }}</span>
              <span class="tabular">{{ compactRub(item.low?.expected_loss_rub) }} … {{ compactRub(item.high?.expected_loss_rub) }}</span>
              <span class="rank-shift tabular" :class="{ moved: item.low?.rank !== item.high?.rank }">{{ rankRange(item) }}</span>
            </div>
          </div>
          <button class="primary-button" :class="{ done: state.mustCover === asset.id }" @click="toggleGuarantee">
            <Check v-if="state.mustCover === asset.id" :size="16" /><Pin v-else :size="16" />
            {{ state.mustCover === asset.id ? 'Закреплён в плане съёмки' : 'Обязательно доснять' }}
          </button>
        </div>
      </Transition>
    </template>
  </div>

  <button class="next-row" @click="state.tab = 'plan'">Собрать заказ съёмки <ArrowRight :size="16" /></button>
</template>
