<script setup>
import { computed, ref } from 'vue'
import { ChevronDown, Info, Pin, X } from '@lucide/vue'
import AnimatedNumber from './AnimatedNumber.vue'
import BubbleTabs from './BubbleTabs.vue'
import FrontierChart from './FrontierChart.vue'
import RingGauge from './RingGauge.vue'
import { activePlan, state } from '../store'
import { compactRub, km, number, percent, plural, rub } from '../lib/format'

const emit = defineEmits(['zone'])
const analysis = computed(() => state.analysis)
const broadCost = computed(() => analysis.value?.strategies.B.data_cost_rub || 0)
// Слайдер доходит до цены широкой съёмки: дальше бюджет ничего не меняет.
const maxBudget = computed(() => Math.max(10000, Math.ceil(broadCost.value / 1000) * 1000))

const STRATEGIES = {
  A: { name: 'Не покупать', line: 'ждём бесплатный пролёт Sentinel‑1' },
  B: { name: 'Снять всё', line: 'вся область одним заказом' },
  C: { name: 'Только нужные зоны', line: 'лучшее, что влезает в бюджет' },
}
const DEADLINES = [4, 8, 12].map(hours => ({ value: hours, label: hours + ' ч',
  hint: hours < 6 ? 'Коммерческий снимок приходит через 6 ч: к этому сроку купить нечего' : 'Снимок успевает: он приходит через 6 ч' }))
const PRIORITIES = [
  { value: 'risk', label: 'Ущерб' },
  { value: 'information', label: 'Сомнения' },
]
const GOAL_LINE = {
  risk: 'Берём зоны, где под водой больше всего ожидаемого ущерба.',
  information: 'Берём зоны, где карта чаще всего может ошибаться: там новый снимок изменит больше всего.',
}

function zoneTitle(zone) {
  if (zone.kind === 'broad') return 'Вся область'
  if (zone.kind === 'uncertainty') return 'Сомнительный участок ' + zone.id.slice(1)
  const asset = analysis.value.assets.find(item => item.id === zone.asset_id)
  return 'Вокруг объекта «' + (asset?.name || zone.asset_id) + '»'
}
function assetNames(ids) {
  return ids.map(id => analysis.value.assets.find(item => item.id === id)?.name).filter(Boolean).join(', ')
}
function status(key) {
  const plan = analysis.value.strategies[key]
  if (!plan.timely) return 'не успеет к сроку'
  if (key === 'C' && plan.constraints_feasible === false) return 'условие не выполнить'
  if (!plan.budget_feasible) return 'дороже бюджета, для сравнения'
  return key === 'A' ? 'бесплатно' : 'в бюджете'
}
const warn = key => !['в бюджете', 'бесплатно'].includes(status(key))

const decisions = computed(() => {
  const zones = new Map(analysis.value.zones.map(zone => [zone.id, zone]))
  return (analysis.value.zone_decisions || []).map(row => ({ ...row, zone: zones.get(row.zone_id) }))
})
const chosen = computed(() => decisions.value.filter(row => row.selected))
const rejected = computed(() => decisions.value.filter(row => !row.selected && row.kind !== 'broad'))
const showRejected = ref(false)

function why(row) {
  const doubt = row.doubt_share ? `${percent(row.doubt_share)} сомнительной площади` : ''
  const loss = row.expected_loss_rub ? `${compactRub(row.expected_loss_rub)} ущерба` : ''
  // У зоны вокруг объекта его имя уже в заголовке: перечисляем только соседей.
  const names = assetNames(row.asset_ids.filter(id => id !== row.zone?.asset_id))
  const prefix = row.zone?.kind === 'object' ? 'ещё' : 'объекты'
  switch (row.reason) {
    case 'selected':
      return [names && `${prefix}: ${names}`, loss, doubt].filter(Boolean).join(' · ')
    case 'over_budget':
      return `не влезла: заказ вырос бы до ${rub(row.price_with_rub)} при бюджете ${rub(state.budget)}`
    case 'late':
      return 'снимок придёт позже срока решения'
    case 'nothing_new':
      return state.priority === 'risk' ? 'ничего не добавит: её объекты уже в заказе или без ущерба' : 'ничего не добавит: её сомнения уже закрыты'
    case 'plan_infeasible':
      return 'ни один набор зон не выполняет условия'
    default:
      return 'тот же результат дешевле без неё'
  }
}

const plan = computed(() => activePlan.value)
const planKey = computed(() => state.strategy)
const pricing = computed(() => plan.value?.quote)
const chart = computed(() => {
  const doubt = analysis.value.doubt
  if (!doubt?.frontier?.length) return null
  const s = analysis.value.strategies
  const every = doubt.all_uncertainty_zones
  return {
    frontier: doubt.frontier,
    points: [
      { key: 'a', label: 'A', cost: 0, share: 0, active: planKey.value === 'A' },
      ...(every ? [{ key: 'u', label: 'все U', cost: every.data_cost_rub, share: every.covered_doubt_share }] : []),
      { key: 'c', label: 'C', cost: s.C.data_cost_rub, share: s.C.covered_doubt_share || 0, active: planKey.value === 'C' },
      { key: 'b', label: 'B', cost: s.B.data_cost_rub, share: s.B.covered_doubt_share || 0, active: planKey.value === 'B' },
    ],
  }
})
// Главная цифра, ради которой считается план: сколько покрывает умная закупка и насколько она дешевле.
const pitch = computed(() => {
  const s = analysis.value.strategies
  const every = analysis.value.doubt?.all_uncertainty_zones
  const lines = []
  if (s.C.feasible && s.C.zone_ids.length && s.B.data_cost_rub) {
    lines.push(`C закрывает ${percent(s.C.coverage_share, 1)} ущерба и ${percent(s.C.covered_doubt_share)} сомнительной площади за ${compactRub(s.C.data_cost_rub)}: на ${percent(1 - s.C.data_cost_rub / s.B.data_cost_rub)} дешевле, чем снять всё.`)
  }
  if (every && every.savings_share_vs_broad > 0) {
    const many = every.zone_ids.length > 1
    lines.push(`${many ? 'Сомнительные участки' : 'Сомнительный участок'} ${every.zone_ids.join(', ')} ${many ? 'закрывают' : 'закрывает'} ${percent(every.covered_doubt_share)} сомнительной площади за ${compactRub(every.data_cost_rub)}. Снять всё стоит ${compactRub(s.B.data_cost_rub)}: ${many ? 'участки дешевле' : 'участок дешевле'} на ${percent(every.savings_share_vs_broad)}.`)
  }
  return lines
})
const scenarios = computed(() => (analysis.value?.sensitivity?.plan || []).filter(row => row.factor !== 1))
const evidence = computed(() => analysis.value?.decision_evidence)
const alternateNames = computed(() => {
  const zones = new Map(analysis.value.zones.map(zone => [zone.id, zone]))
  return (evidence.value?.alternate_zone_ids || []).map(id => zoneTitle(zones.get(id))).join('; ')
})

function pick(key) {
  state.strategy = key
  state.showZones = true
}
function hover(id) { state.hoverZoneId = id }
</script>

<template>
  <section class="block">
    <label class="budget-label" for="budget">
      <span class="caption">Бюджет на снимки</span>
      <span class="budget-value"><AnimatedNumber :value="state.budget" :duration="0.4" /> ₽</span>
    </label>
    <input id="budget" v-model.number="state.budget" class="range" type="range" min="0" :max="maxBudget" step="250"
      :style="{ '--progress': Math.min(state.budget / maxBudget * 100, 100) + '%' }" />
    <div class="control-row">
      <span v-tip="'Через сколько часов нужно решение. Коммерческий снимок приходит через 6 ч, это сценарное допущение'">Решение нужно через</span>
      <BubbleTabs v-model="state.deadline" :options="DEADLINES" size="sm" label="Срок решения" />
    </div>
    <div class="control-row">
      <span>Что важнее</span>
      <BubbleTabs v-model="state.priority" :options="PRIORITIES" size="sm" label="Цель плана" />
    </div>
    <p class="goal-line">{{ GOAL_LINE[state.priority] }}</p>
    <div v-if="state.mustCover" class="constraint">
      <Pin :size="14" />Обязательно: {{ analysis.assets.find(a => a.id === state.mustCover)?.name || state.mustCover }}
      <button class="ghost-icon tiny" aria-label="Снять условие" @click="state.mustCover = null"><X :size="14" /></button>
    </div>
  </section>

  <div class="rows strategy-rows" role="radiogroup" aria-label="Вариант закупки">
    <button v-for="key in ['A', 'B', 'C']" :key="key" class="row strategy-row"
      role="radio" :aria-checked="state.strategy === key" :class="{ selected: state.strategy === key }" @click="pick(key)">
      <span class="strategy-key">{{ key }}</span>
      <span class="asset-main">
        <strong>{{ STRATEGIES[key].name }}</strong>
        <small class="strategy-line">{{ STRATEGIES[key].line }}</small>
        <span class="strategy-meters">
          <span class="meter coverage" v-tip="'Доля ожидаемого ущерба в зонах заказа'"><span :style="{ transform: `scaleX(${analysis.strategies[key].coverage_share || 0})` }"></span></span>
          <span class="meter doubt" v-tip="'Доля сомнительной площади в зонах заказа'"><span :style="{ transform: `scaleX(${analysis.strategies[key].covered_doubt_share || 0})` }"></span></span>
        </span>
      </span>
      <span class="strategy-cost">
        <strong class="tabular">{{ compactRub(analysis.strategies[key].data_cost_rub) }}</strong>
        <small :class="{ warn: warn(key) }" v-tip="!analysis.strategies[key].budget_feasible ? 'Контрфактический вариант: в бюджет не входит и показан только для сравнения цены и охвата' : null">{{ status(key) }}</small>
      </span>
    </button>
  </div>
  <p class="meter-key"><i class="coverage"></i>ущерб в зонах <i class="doubt"></i>сомнения в зонах</p>

  <Transition name="swap" mode="out-in">
    <section :key="state.strategy" class="block plan-result">
      <RingGauge :value="plan?.coverage_share" label="ущерба в зонах заказа" color="var(--reference)" />
      <div class="mini-rows">
        <div v-tip="'Цена снимков. Обработка занимает секунды на CPU, проверку специалистом не оцениваем, поэтому прочие затраты не добавлены'"><span>Стоимость данных</span><b class="tabular">{{ rub(plan?.data_cost_rub) }}</b></div>
        <div><span>Объектов в зонах</span><b class="tabular">{{ plan?.covered_asset_ids?.length || 0 }} из {{ analysis.assets.length }}</b></div>
        <div v-tip="'Доля площади, где карта может ошибаться, которая попадает в заказанные зоны'"><span>Сомнений в зонах</span><b class="tabular">{{ percent(plan?.covered_doubt_share) }}</b></div>
        <div><span>Зон в заказе</span><b class="tabular">{{ plan?.zone_ids?.length || 0 }}</b></div>
        <div v-tip="plan?.uncertainty_status === 'baseline' ? 'Средний шанс ошибки карты по области без новых снимков. Это исходная неопределённость' : 'Новых снимков ещё нет, поэтому снижение неопределённости не заявляем'"><span>Остаточная неопределённость</span><b class="tabular">{{ plan?.uncertainty_status === 'baseline' ? percent(plan.residual_uncertainty, 1) : 'не оценена' }}</b></div>
      </div>
    </section>
  </Transition>
  <p v-if="plan?.coverage_share == null" class="warn-note">В области нет объектов с оценённым ущербом, долю считать не от чего.</p>
  <p v-if="plan?.reason" class="warn-note">{{ plan.reason }}</p>

  <section v-if="pitch.length" class="block pitch">
    <p v-for="line in pitch" :key="line">{{ line }}</p>
  </section>

  <section v-if="chart" class="block">
    <span class="caption" v-tip="'Линия: лучший набор зон для каждой суммы заказа по доле сомнительной площади. Точки: варианты A, B, C и все сомнительные участки вместе'">Сколько сомнений закрывает каждый рубль</span>
    <FrontierChart :frontier="chart.frontier" :points="chart.points" :budget="state.budget" />
  </section>

  <section v-if="state.strategy !== 'A' && decisions.length" class="block">
    <span class="caption">{{ state.strategy === 'C' ? 'Почему эти зоны' : 'Что входит в заказ' }}</span>
    <div class="zone-list">
      <button v-for="row in (state.strategy === 'C' ? chosen : decisions.filter(item => plan.zone_ids.includes(item.zone_id)))"
        :key="row.zone_id" class="zone-item chosen" :class="row.kind"
        @pointerenter="hover(row.zone_id)" @pointerleave="hover(null)" @focus="hover(row.zone_id)" @blur="hover(null)"
        @click="emit('zone', row.zone_id)">
        <i class="zone-swatch"></i>
        <span class="zone-text">
          <strong>{{ zoneTitle(row.zone) }}</strong>
          <small>{{ state.strategy === 'C' ? why(row) : `${km(row.area_km2)} · ${percent(row.doubt_share)} сомнительной площади` }}</small>
        </span>
        <span class="tabular zone-area">{{ km(row.area_km2) }}</span>
      </button>
    </div>
    <template v-if="state.strategy === 'C' && rejected.length">
      <button class="link-row" :aria-expanded="showRejected" @click="showRejected = !showRejected">
        {{ showRejected ? 'Скрыть зоны, которые не вошли' : 'Показать зоны, которые не вошли' }} ({{ rejected.length }})
        <ChevronDown :size="14" :class="{ up: showRejected }" class="link-caret" />
      </button>
      <Transition name="expand">
        <div v-if="showRejected" class="zone-list">
          <button v-for="row in rejected" :key="row.zone_id" class="zone-item" :class="row.kind"
            @pointerenter="hover(row.zone_id)" @pointerleave="hover(null)" @focus="hover(row.zone_id)" @blur="hover(null)"
            @click="emit('zone', row.zone_id)">
            <i class="zone-swatch"></i>
            <span class="zone-text">
              <strong>{{ zoneTitle(row.zone) }}</strong>
              <small>{{ why(row) }}</small>
            </span>
            <span class="tabular zone-area">{{ compactRub(row.price_alone_rub) }}</span>
          </button>
        </div>
      </Transition>
    </template>
  </section>

  <details v-if="pricing?.lines?.length" class="block price-details">
    <summary>Как посчитана цена</summary>
    <p class="formula tabular">
      {{ number(pricing.base_rate_rub_km2, 2) }} ₽ × {{ number(pricing.processing_coef, 1) }} × {{ number(pricing.usage_coef, 1) }}
      × {{ number(pricing.freshness_coef, 1) }} × {{ number(pricing.discount_coef, 4) }} = <b>{{ number(pricing.unit_price_rub_km2, 2) }} ₽ за км²</b>
    </p>
    <div class="mini-rows price-rows">
      <div><span>Б · базовая ставка, сценарная</span><b class="tabular">{{ number(pricing.base_rate_rub_km2, 2) }} ₽/км²</b></div>
      <div><span>О · обработка L2</span><b class="tabular">× {{ number(pricing.processing_coef, 1) }}</b></div>
      <div><span>П · только для своих нужд</span><b class="tabular">× {{ number(pricing.usage_coef, 1) }}</b></div>
      <div><span>Т · новая съёмка события</span><b class="tabular">× {{ number(pricing.freshness_coef, 1) }}</b></div>
      <div v-tip="'Скидка считается по площади всей корзины сразу: с каждой новой зоной дешевеет каждый километр. Группа скидки: оптика 1 м, заказы одного календарного года'">
        <span>Р · скидка за {{ km(pricing.group_area_km2) }}</span><b class="tabular">× {{ number(pricing.discount_coef, 4) }}</b></div>
      <div v-for="line in pricing.lines" :key="line.candidate_id">
        <span>{{ line.candidate_id }} · {{ km(line.area_km2) }}</span><b class="tabular">{{ rub(line.cost_rub) }}</b></div>
      <div class="total"><span>Итого</span><b class="tabular">{{ rub(pricing.total_rub) }}</b></div>
    </div>
    <p class="source-line">Формула ПП РФ №840 в редакции от 27.08.2025. Группа скидки {{ pricing.discount_group_id }}.</p>
  </details>

  <section v-if="state.strategy === 'C' && scenarios.length" class="block">
    <span class="caption" v-tip="'План C пересчитан заново, когда бюджет или тариф меняется на 20%. Остальное на месте'">Если бюджет или тариф другие</span>
    <div class="mini-rows scenario-rows">
      <div class="scenario-head"><span></span><span>заказ</span><span>ущерб в зонах</span></div>
      <div v-for="item in scenarios" :key="item.input + item.factor">
        <span>{{ item.input === 'budget' ? 'Бюджет' : 'Тариф' }} {{ item.factor < 1 ? '−20%' : '+20%' }}</span>
        <span class="scenario-zones">{{ plural(item.zone_ids.length, ['зона', 'зоны', 'зон']) }} за {{ compactRub(item.data_cost_rub) }}</span>
        <b class="tabular" v-tip="'Ожидаемый ущерб в зонах такого заказа'">{{ compactRub(item.covered_expected_loss_rub) }}</b>
      </div>
    </div>
  </section>

  <p v-if="state.strategy === 'C' && evidence?.priority_changes_order" class="source-line alternate">
    С целью «{{ evidence.alternate_priority === 'risk' ? 'Ущерб' : 'Сомнения' }}» в заказ вошли бы: {{ alternateNames }}.
  </p>

  <p class="source-line" v-tip="'Ставка 788,64 ₽/км² взята из контрольного примера постановки, это не цена поставщика. Срок поставки 6 ч тоже допущение, как и то, что над зоной не будет облаков: оптика сквозь них воду не видит, при сплошной облачности нужен радар. Ущерб в зонах показывает, что проверять первым, а не сколько потерь удалось избежать'">
    <Info :size="14" />Ставка и срок поставки сценарные
  </p>
</template>
