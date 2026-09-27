<script setup>
import { computed, ref, watch } from 'vue'
import { ChevronRight, Crosshair } from '@lucide/vue'
import { state } from '../store'
import { buildings } from '../lib/buildings'
import { buildingPlan, buildingTargets, currentBuildingPlan } from '../lib/buildingPlans'
import { activePlan } from '../store'
import { compactRub, percent, plural } from '../lib/format'
import Money from './Money.vue'
import FrontierChart from './FrontierChart.vue'
import ZoneMatrix from './ZoneMatrix.vue'

const props = defineProps({ view: { type: String, required: true }, building: Boolean })
const emit = defineEmits(['object', 'zones', 'zone'])
const focusScale = ref(true)
const zoneMetric = ref('loss')
const riskMetric = ref('loss')
const matrixMetric = computed(() => props.building ? 'loss' : zoneMetric.value)
const budgetDraft = ref(state.budget)
watch(() => state.budget, value => { budgetDraft.value = value })
function commitBudget() {
  const value = Number(budgetDraft.value)
  if (!Number.isFinite(value)) { budgetDraft.value = state.budget; return }
  budgetDraft.value = Math.max(0, Math.min(10000000, value))
  state.budget = budgetDraft.value
}
const strategies = computed(() => props.building ? buildingPlan.result?.strategies : state.analysis?.strategies)
const plan = computed(() => props.building ? currentBuildingPlan.value : activePlan.value)
const allCount = computed(() => props.building ? buildings.data?.features.length || 0 : (state.analysis?.all_assets || state.analysis?.assets || []).length)
const items = computed(() => props.building
  ? buildingTargets.value.map(feature => ({
      id: feature.id, name: feature.properties.tags?.name || feature.properties.type_label || 'Здание',
      type: feature.properties.building_type || 'unknown', label: feature.properties.type_label || 'Тип не определён',
      loss: feature.properties.expected_loss_rub || 0, value: feature.properties.exposure_rub || 0,
      synthetic: !!feature.properties.synthetic_cost, feature,
    }))
  : (state.analysis?.assets || []).map(asset => ({
      id: asset.id, name: asset.name, type: asset.class, label: asset.name,
      loss: asset.expected_loss_rub || 0, value: asset.value_rub || 0, synthetic: false, asset,
    })))
const totalLoss = computed(() => items.value.reduce((sum, item) => sum + item.loss, 0))
const totalValue = computed(() => items.value.reduce((sum, item) => sum + item.value, 0))
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
    const current = grouped.get(item.type) || { key: item.type, label: item.label, loss: 0, value: 0, count: 0 }
    current.loss += item.loss
    current.value += item.value
    current.count++
    grouped.set(item.type, current)
  }
  return [...grouped.values()].sort((a, b) => b.loss - a.loss)
})
const filtered = computed(() => items.value.filter(item => !props.building || !state.planRiskFilter || item.type === state.planRiskFilter)
  .sort((a, b) => b.loss - a.loss))
const maxGroupLoss = computed(() => Math.max(1, ...groups.value.map(group => group.loss)))
const maxItemLoss = computed(() => Math.max(1, ...filtered.value.map(item => item.loss)))
const frontier = computed(() => props.building ? buildingPlan.result?.frontier || [] : state.analysis?.doubt?.frontier || [])
const chartMeasure = computed(() => props.building ? 'охват ущерба' : 'охват сомнений')
const chartPoints = computed(() => choices.map(choice => ({
  key: choice.key.toLowerCase(), label: choice.key,
  cost: strategies.value?.[choice.key]?.data_cost_rub || 0,
  share: (props.building ? strategies.value?.[choice.key]?.coverage_share : strategies.value?.[choice.key]?.covered_doubt_share) || 0,
  active: state.strategy === choice.key,
})))
const coveredShare = computed(() => totalLoss.value ? Math.min(1, coveredLoss.value / totalLoss.value) : 0)
const uncoveredLoss = computed(() => Math.max(0, totalLoss.value - coveredLoss.value))
const costPerMillion = computed(() => coveredLoss.value > 0 ? (plan.value?.data_cost_rub || 0) / (coveredLoss.value / 1_000_000) : null)
const fullChartCost = computed(() => Math.max(1, state.budget, ...frontier.value.map(point => point.cost_rub), ...chartPoints.value.map(point => point.cost)))
const focusChartCost = computed(() => {
  const nextStep = frontier.value.find(point => point.cost_rub > state.budget)?.cost_rub || 0
  return Math.min(fullChartCost.value, Math.max(10000, state.budget * 1.8, (strategies.value?.C?.data_cost_rub || 0) * 1.4, nextStep * 1.05))
})
const chartLimit = computed(() => focusScale.value ? focusChartCost.value : fullChartCost.value)
const riskTotal = computed(() => riskMetric.value === 'value' ? totalValue.value : totalLoss.value)
const riskRows = computed(() => [...(props.building ? groups.value : items.value)].sort((a, b) => metricValue(b) - metricValue(a)).slice(0, 6))
const maxRisk = computed(() => Math.max(1, ...riskRows.value.map(metricValue)))
function metricValue(row) { return riskMetric.value === 'value' ? row.value : row.loss }
const ZONE_REASONS = { late: 'Не успеет к сроку', over_budget: 'Выше бюджета', nothing_new: 'Не добавляет охват', comparison: 'Другой вариант плана', cheaper_equal: 'Не улучшает план', plan_infeasible: 'Условие не выполнено', lower_net_benefit: 'Польза ниже цены' }
const zoneCandidates = computed(() => {
  const source = props.building ? buildingPlan.result?.zones || [] : state.analysis?.zones || []
  const selected = new Set(plan.value?.zone_ids || [])
  const decisions = new Map((state.analysis?.zone_decisions || []).map(row => [row.zone_id, row]))
  return source.map((zone, index) => {
    const decision = decisions.get(zone.id)
    const asset = !props.building && state.analysis?.assets?.find(item => item.id === zone.asset_id)
    const label = zone.kind === 'broad' ? 'Вся область' : props.building ? zone.name || `Зона ${index + 1}`
      : zone.kind === 'uncertainty' ? `Сомнительный участок ${zone.id.slice(1)}` : asset?.name || `Зона ${index + 1}`
    const inPlan = selected.has(zone.id)
    return { id: zone.id, number: index + 1, label, selected: inPlan,
      price: zone.price_alone_rub ?? decision?.price_alone_rub ?? zone.cost_rub ?? decision?.cost_rub ?? 0,
      loss: zone.expected_loss_rub ?? decision?.expected_loss_rub ?? 0,
      doubt: decision?.doubt_share ?? null, area: zone.area_km2,
      status: inPlan ? 'В заказе' : ZONE_REASONS[zone.reason || decision?.reason] || 'Вне заказа' }
  })
})
const matrixLimit = computed(() => focusScale.value
  ? Math.max(chartLimit.value, ...zoneCandidates.value.filter(zone => zone.selected || zone.id === state.selectedZoneId).map(zone => zone.price), 1)
  : null)
function selectType(key) { state.planRiskFilter = state.planRiskFilter === key ? null : key }
function selectObject(item) { emit('object', props.building ? item.feature : item.id) }
</script>

<template>
  <section class="plan-insights" :class="{ 'analytics-dashboard': view === 'charts' }">
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
    <template v-else-if="view === 'charts'">
      <div class="analytics-title"><div><h3>Аналитика съёмки</h3><span>{{ building ? 'Здания' : 'Точки' }} · вариант {{ state.strategy }} · {{ plural(plan?.zone_ids?.length || 0, ['зона','зоны','зон']) }}</span></div><div class="analytics-title-actions"><label>Бюджет <input v-model="budgetDraft" type="number" min="0" max="10000000" step="1" aria-label="Бюджет аналитики в рублях" @change="commitBudget" @blur="commitBudget" @keydown.enter="commitBudget" /> ₽</label><button @click="emit('zones')">Настроить зоны <ChevronRight :size="16" /></button></div></div>
      <div class="analytics-kpis">
        <div><small>В охвате</small><strong>{{ percent(coveredShare) }}</strong><span>{{ coveredCount }} из {{ items.length }} · {{ compactRub(coveredLoss) }}</span></div>
        <div><small>Вне охвата</small><strong>{{ compactRub(uncoveredLoss) }}</strong><span>ожидаемый ущерб</span></div>
        <div><small>Цена снимков</small><strong>{{ compactRub(plan?.data_cost_rub || 0) }}</strong><span>при бюджете {{ compactRub(state.budget) }}</span></div>
        <div v-tip="'Цена снимков на каждый 1 млн ₽ ожидаемого ущерба в охвате. Это не предотвращённые потери и не окупаемость.'"><small>Цена охвата 1 млн ₽</small><strong>{{ compactRub(costPerMillion) }}</strong><span>сравнение затрат и риска</span></div>
      </div>
      <div class="analytics-grid">
        <section class="analytics-card analytics-card-wide">
          <div class="analytics-card-head"><div><h3>Бюджет → {{ chartMeasure }}</h3><span>{{ building ? 'Лучшая комбинация зон по ущербу' : 'Лучшая комбинация зон по сомнительной площади' }}</span></div><div class="analytics-switch" role="group" aria-label="Масштаб бюджета"><button :class="{active:focusScale}" :aria-pressed="focusScale" @click="focusScale=true">Около бюджета</button><button :class="{active:!focusScale}" :aria-pressed="!focusScale" @click="focusScale=false">Вся шкала</button></div></div>
          <div class="analytics-plot"><FrontierChart v-if="frontier.length" :frontier="frontier" :points="chartPoints" :budget="state.budget" :max-cost-rub="chartLimit" wide :description="`Лучший ${chartMeasure} при разном бюджете`" /><p v-else class="insights-caveat">Для этой области кривая бюджета недоступна.</p></div>
          <div class="analytics-card-foot">Вертикальная линия — текущий бюджет. A, B, C — варианты заказа; весь диапазон покажет и дорогие варианты.</div>
        </section>
        <section class="analytics-card">
          <div class="analytics-card-head"><div><h3>{{ building ? 'По типам зданий' : 'По объектам' }}</h3><span>Доля от {{ compactRub(riskTotal) }} {{ riskMetric==='value' ? 'сценарной стоимости' : 'ожидаемого ущерба' }}</span></div><div class="analytics-switch" role="group" aria-label="Показатель объектов"><button :class="{active:riskMetric==='loss'}" :aria-pressed="riskMetric==='loss'" @click="riskMetric='loss'">Ущерб</button><button :class="{active:riskMetric==='value'}" :aria-pressed="riskMetric==='value'" @click="riskMetric='value'">Стоимость</button></div></div>
          <div class="analytics-risk-list" :class="{value:riskMetric==='value'}"><button v-for="row in riskRows" :key="row.key || row.id" @click="building ? selectType(row.key) : selectObject(row)"><span><b>{{ row.label || row.name }}</b><strong>{{ percent(riskTotal ? metricValue(row)/riskTotal : 0) }}</strong></span><i><span :style="{width:`${metricValue(row)/maxRisk*100}%`}" /></i><small>{{ compactRub(metricValue(row)) }}<template v-if="building"> · {{ plural(row.count, ['здание','здания','зданий']) }}</template></small></button></div>
          <div class="analytics-card-foot">{{ building ? 'Стоимость оценена по типу и площади. Нажмите на тип, чтобы выделить здания.' : 'Стоимость объектов сценарная. Нажмите на строку, чтобы показать точку на карте.' }}</div>
        </section>
        <section class="analytics-card">
          <div class="analytics-card-head"><div><h3>Зоны-кандидаты</h3><span>Цена отдельной зоны и {{ matrixMetric==='doubt' ? 'сомнения' : 'ущерб внутри неё' }}</span></div><div v-if="!building" class="analytics-switch" role="group" aria-label="Показатель зон"><button :class="{active:zoneMetric==='loss'}" :aria-pressed="zoneMetric==='loss'" @click="zoneMetric='loss'">Ущерб</button><button :class="{active:zoneMetric==='doubt'}" :aria-pressed="zoneMetric==='doubt'" @click="zoneMetric='doubt'">Сомнения</button></div></div>
          <ZoneMatrix :zones="zoneCandidates" :metric="matrixMetric" :max-price-rub="matrixLimit" :selected-zone-id="state.selectedZoneId" @zone="id => emit('zone', id)" />
          <div class="analytics-card-foot">Заливка — в заказе. Зоны могут пересекаться: суммы ущерба на графике не складываются.</div>
        </section>
      </div>
      <p class="insights-caveat">Ущерб в охвате — приоритет проверки, не предотвращённые потери. Цена рассчитана по сценарному тарифу.</p>
    </template>
  </section>
</template>
