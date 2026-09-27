<script setup>
import { computed } from 'vue'
import { ChevronRight, Crosshair } from '@lucide/vue'
import { state } from '../store'
import { buildings } from '../lib/buildings'
import { buildingPlan, buildingTargets, currentBuildingPlan } from '../lib/buildingPlans'
import { activePlan } from '../store'
import { compactRub, percent, plural } from '../lib/format'
import Money from './Money.vue'

const props = defineProps({ view: { type: String, required: true }, building: Boolean })
const emit = defineEmits(['object', 'zones'])
const strategies = computed(() => props.building ? buildingPlan.result?.strategies : state.analysis?.strategies)
const plan = computed(() => props.building ? currentBuildingPlan.value : activePlan.value)
const allCount = computed(() => props.building ? buildings.data?.features.length || 0 : (state.analysis?.all_assets || state.analysis?.assets || []).length)
const items = computed(() => props.building
  ? buildingTargets.value.map(feature => ({
      id: feature.id, name: feature.properties.tags?.name || feature.properties.type_label || 'Здание',
      type: feature.properties.building_type || 'unknown', label: feature.properties.type_label || 'Тип не определён',
      loss: feature.properties.expected_loss_rub || 0, synthetic: !!feature.properties.synthetic_cost, feature,
    }))
  : (state.analysis?.assets || []).map(asset => ({
      id: asset.id, name: asset.name, type: asset.class, label: asset.name,
      loss: asset.expected_loss_rub || 0, synthetic: false, asset,
    })))
const totalLoss = computed(() => items.value.reduce((sum, item) => sum + item.loss, 0))
const coveredLoss = computed(() => plan.value?.covered_expected_loss_rub || 0)
const coveredCount = computed(() => props.building ? plan.value?.covered_building_ids?.length || 0 : plan.value?.covered_asset_ids?.length || 0)
const syntheticCount = computed(() => items.value.filter(item => item.synthetic).length)
const maxPrice = computed(() => Math.max(1, ...Object.values(strategies.value || {}).map(item => item.data_cost_rub || 0)))
const choices = [
  { key: 'A', label: 'Без покупки' },
  { key: 'B', label: 'Вся область' },
  { key: 'C', label: 'Точные зоны' },
]
const groups = computed(() => {
  const grouped = new Map()
  for (const item of items.value) {
    const current = grouped.get(item.type) || { key: item.type, label: item.label, loss: 0, count: 0 }
    current.loss += item.loss
    current.count++
    grouped.set(item.type, current)
  }
  return [...grouped.values()].sort((a, b) => b.loss - a.loss)
})
const filtered = computed(() => items.value.filter(item => !props.building || !state.planRiskFilter || item.type === state.planRiskFilter)
  .sort((a, b) => b.loss - a.loss))
const maxGroupLoss = computed(() => Math.max(1, ...groups.value.map(group => group.loss)))
const maxItemLoss = computed(() => Math.max(1, ...filtered.value.map(item => item.loss)))
function selectType(key) { state.planRiskFilter = state.planRiskFilter === key ? null : key }
function selectObject(item) { emit('object', props.building ? item.feature : item.id) }
</script>

<template>
  <section class="plan-insights">
    <div class="insights-scope"><span>{{ items.length.toLocaleString('ru-RU') }} из {{ allCount.toLocaleString('ru-RU') }} {{ building ? 'зданий' : 'точек' }}</span><span v-if="building && syntheticCount" v-tip="'Стоимость этих зданий оценена по типу и площади; это сценарное допущение'">{{ syntheticCount }} с условной ценой</span></div>
    <template v-if="view === 'overview'">
      <div class="insight-kpis">
        <div class="insight-kpi insight-kpi-main"><span>Ожидаемый ущерб</span><strong>{{ compactRub(totalLoss) }}</strong><small>у выбранных объектов</small></div>
        <div class="insight-kpi"><span>В зонах</span><strong>{{ compactRub(coveredLoss) }}</strong><small>{{ coveredCount }} из {{ items.length }} · {{ percent(plan?.coverage_share) }}</small></div>
        <div class="insight-kpi"><span>Цена снимков</span><strong><Money :value="plan?.data_cost_rub || 0" compact /></strong><small>{{ plural(plan?.zone_ids?.length || 0, ['зона','зоны','зон']) }}</small></div>
      </div>
      <div class="insights-heading"><h3>Три варианта</h3><span>цена / охват ущерба</span></div>
      <div v-if="strategies" class="insight-options" role="group" aria-label="Вариант съёмки">
        <button v-for="choice in choices" :key="choice.key" class="insight-option" :class="{active:state.strategy===choice.key}" :aria-pressed="state.strategy===choice.key" :aria-label="`${choice.label}: ${compactRub(strategies[choice.key].data_cost_rub)}, ${percent(strategies[choice.key].coverage_share)} ущерба в охвате`" @click="state.strategy=choice.key;state.showZones=true">
          <span class="insight-option-head"><b>{{ choice.key }} · {{ choice.label }}</b><strong>{{ compactRub(strategies[choice.key].data_cost_rub) }}</strong></span>
          <span class="insight-option-bars"><i><span :style="{width:`${Math.max(0,strategies[choice.key].data_cost_rub/maxPrice*100)}%`}" /></i><i><span :style="{width:`${Math.max(0,(strategies[choice.key].coverage_share || 0)*100)}%`}" /></i></span>
          <span class="insight-option-foot"><b>{{ percent(strategies[choice.key].coverage_share) }} ущерба</b><small v-if="choice.key !== 'A' && !strategies[choice.key].budget_feasible">выше бюджета</small></span>
        </button>
      </div>
      <button class="insights-next" @click="emit('zones')">Настроить съёмку и посмотреть зоны <ChevronRight :size="16" /></button>
      <p class="insights-caveat" v-tip="'Охват показывает, какие объекты можно проверить новым снимком. Он не означает, что этот ущерб будет предотвращён.'">Ущерб в охвате — приоритет проверки, не предотвращённые потери.</p>
    </template>
    <template v-else-if="view === 'risks'">
      <div class="insights-heading"><h3>{{ building ? 'По типам зданий' : 'Крупнейший риск' }}</h3><span>ожидаемый ущерб</span></div>
      <div class="risk-bars">
        <button v-for="row in building ? groups.slice(0,6) : filtered.slice(0,6)" :key="row.key || row.id" :class="{active:building && state.planRiskFilter===row.key}" :aria-pressed="building ? state.planRiskFilter===row.key : undefined" @click="building ? selectType(row.key) : selectObject(row)">
          <span><b>{{ row.label || row.name }}</b><strong>{{ compactRub(row.loss) }}</strong></span>
          <i><span :style="{width:`${Math.max(2,row.loss/(building ? maxGroupLoss : maxItemLoss)*100)}%`}" /></i>
          <small v-if="building">{{ row.count }} {{ row.count===1 ? 'здание' : 'зданий' }}</small>
        </button>
      </div>
      <div v-if="building" class="insights-heading"><h3>{{ state.planRiskFilter ? 'Здания этого типа' : 'Топ зданий' }}</h3><button v-if="state.planRiskFilter" @click="state.planRiskFilter=null">Все типы</button></div>
      <div v-if="building" class="risk-top">
        <button v-for="(item,index) in filtered.slice(0,5)" :key="item.id" @click="selectObject(item)"><span class="risk-rank">{{ index+1 }}</span><span class="risk-top-name"><b>{{ item.name }}</b><small>{{ item.id }}</small></span><strong>{{ compactRub(item.loss) }}</strong><Crosshair :size="14" /></button>
      </div>
      <p class="insights-caveat">Нажмите на {{ building ? 'тип, чтобы выделить здания на карте, или на здание для оценки' : 'объект, чтобы увидеть его на карте и открыть оценку' }}.</p>
    </template>
  </section>
</template>
