<script setup>
import { computed, ref } from 'vue'
import { ChevronDown, Info, Pin, X } from '@lucide/vue'
import AnimatedNumber from './AnimatedNumber.vue'
import ProcurementView from './ProcurementView.vue'
import BacktestPanel from './BacktestPanel.vue'
import VoiPanel from './VoiPanel.vue'
import BubbleTabs from './BubbleTabs.vue'
import FrontierChart from './FrontierChart.vue'
import { activePlan, state } from '../store'
import { compactRub, date, km, number, percent, plural, rub } from '../lib/format'

const emit = defineEmits(['zone'])
const procurementMethod = computed({ get: () => state.procurementMethod, set: value => { state.procurementMethod = value } })
const analysis = computed(() => state.analysis)
const broadCost = computed(() => analysis.value?.strategies.B.data_cost_rub || 0)
// Слайдер доходит до цены широкой съёмки: дальше бюджет ничего не меняет.
const maxBudget = computed(() => Math.max(10000, Math.ceil(broadCost.value / 1000) * 1000))

const STRATEGIES = {
  A: { name: 'Не покупать', line: 'ждём бесплатный пролёт Sentinel‑1' },
  B: { name: 'Снять всё', line: 'вся область одним заказом' },
  C: { name: 'Только нужные зоны', line: 'лучшее, что влезает в бюджет' },
}
/*
 * Продукты каталога: новая оптика 1 м (L2), новый радар 3 м (L1, в любую погоду)
 * и архив оптики 1 м только для контекста. Правило плана: оптика, если успевает
 * к сроку решения, иначе радар. Сроки поставки читаем из каталога API.
 */
const SENSOR = { optical: 'оптика', sar: 'радар' }
const USAGE = { internal: 'только для своих нужд', limited: 'ограниченное использование', unrestricted: 'без ограничений' }
const capital = text => (text ? text[0].toUpperCase() + text.slice(1) : text)
function productName(item) {
  if (!item?.sensor_type) return null
  const base = `${SENSOR[item.sensor_type] || item.sensor_type} ${number(item.resolution_m)} м`
  return item.acquisition_type === 'archive' || item.data_role === 'context' ? `архив, ${base}, только контекст` : base
}
const deliveryHours = computed(() => {
  const find = sensor => analysis.value?.catalog?.find(item => item.sensor_type === sensor && item.acquisition_type === 'new')?.delivery_hours
  return { optical: find('optical') ?? 6, sar: find('sar') ?? 4 }
})
const deadlineOptions = computed(() => [4, 8, 12].map(hours => {
  const { optical, sar } = deliveryHours.value
  const hint = hours >= optical ? `Успевает оптика 1 м: она приходит через ${optical} ч`
    : hours >= sar ? `Оптика приходит через ${optical} ч и не успевает. План берёт радар 3 м: он приходит через ${sar} ч`
      : `К этому сроку не успевает ни оптика (${optical} ч), ни радар (${sar} ч)`
  return { value: hours, label: hours + ' ч', hint }
}))
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
  if (!plan.budget_feasible) return 'дороже бюджета'
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
// Решения по зонам посчитаны для этих бюджета и срока: берём их из ответа, а не из ползунка.
const budgetRub = computed(() => analysis.value.inputs?.budget_rub ?? state.budget)
const deadlineHours = computed(() => analysis.value.inputs?.deadline_hours ?? state.deadline)

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
      return `не влезла: заказ вырос бы до ${rub(row.price_with_rub)} при бюджете ${rub(budgetRub.value)}`
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
const costTip = computed(() => {
  const costs = plan.value?.other_costs
  if (!costs) return null
  const parts = [`данные ${rub(plan.value.data_cost_rub)}`,
    `работа аналитика ${rub(costs.review_rub)} (${number(costs.review_hours, 2)} ч по 1 500 ₽)`]
  if (costs.processing_rub) parts.push(`обработка радара L1 ${rub(costs.processing_rub)} (${number(costs.processing_hours, 2)} ч)`)
  parts.push(`вычисления ${number(costs.compute_rub, 2)} ₽`)
  return `Сценарий: ${parts.join(' + ')}. Выезды на объекты и цена ошибочных действий не оценены. Бюджет ограничивает только стоимость данных.`
})
const residual = computed(() => {
  const item = plan.value
  const before = state.analysis?.strategies?.A?.residual_uncertainty
  if (!item || item.uncertainty_status === 'not_estimated' || item.residual_uncertainty == null) {
    return { text: 'не оценена', tip: 'Для этой области шанс ошибки карты не посчитан' }
  }
  if (item.uncertainty_status === 'baseline') {
    return { text: percent(item.residual_uncertainty, 1), tip: 'Средний шанс ошибки карты по области без новых снимков. Это исходная неопределённость' }
  }
  return {
    text: `${percent(before, 1)} → ${percent(item.residual_uncertainty, 1)}`,
    tip: `Сценарий, не измерение: если новые снимки снимут ошибки карты внутри заказанных зон, средний шанс ошибки по области станет ${percent(item.residual_uncertainty, 1)} вместо ${percent(before, 1)}. Внутри зон лежит ${percent(item.covered_chance_share)} всей массы шанса ошибки. Новых наблюдений пока нет.`,
  }
})
const planKey = computed(() => state.strategy)
const pricing = computed(() => plan.value?.quote)

/*
 * Цена и срок каждой зоны. В заказе зона стоит по строке сметы: скидка считается
 * на площадь группы в корзине, поэтому там она дешевле, чем отдельно. Для зоны
 * вне заказа API даёт её строку в корзине, куда её добавили бы (cost_rub).
 * Срок: продукт зоны приходит через delivery_hours, решение нужно через срок из ответа API.
 */
const basketLines = computed(() => new Map((pricing.value?.lines || []).map(line => [line.candidate_id, line.cost_rub])))
const sameMoney = (a, b) => a != null && b != null && Math.abs(a - b) < 0.5
function price(row) {
  const alone = rub(row.price_alone_rub)
  const inBasket = basketLines.value.get(row.candidate_id ?? row.zone_id)
  if (inBasket != null) {
    return `Цена: ${rub(inBasket)} в заказе, отдельно ${sameMoney(inBasket, row.price_alone_rub) ? 'столько же' : alone}`
  }
  if (row.cost_rub != null && row.price_with_rub != null) {
    return `Цена: в заказе была бы ${rub(row.cost_rub)}, отдельно ${sameMoney(row.cost_rub, row.price_alone_rub) ? 'столько же' : alone}`
  }
  if (row.price_with_rub != null) return `Цена: заказ с ней ${rub(row.price_with_rub)}, отдельно ${alone}`
  return `Цена: отдельно ${alone}`
}
function term(row) {
  const product = productName(row) || productName(row.zone) || 'снимок'
  const hours = row.delivery_hours ?? row.zone?.delivery_hours
  const deadline = deadlineHours.value
  if (hours == null) return { text: `Срок: ${product}, поставка н/д, решение через ${deadline} ч`, late: false }
  const late = row.timely != null ? !row.timely : hours > deadline
  return { text: `Срок: ${product} приходит через ${hours} ч, решение через ${deadline} ч: ${late ? 'опоздает' : 'успевает'}`, late }
}
// Архивный кадр по тем же контурам: дешевле, но снят до события, поэтому только контекст.
const archive = computed(() => {
  const items = (analysis.value?.catalog || []).filter(item => item.acquisition_type === 'archive')
  if (!items.length) return null
  const prices = items.map(item => item.price_alone_rub).filter(value => value != null)
  const first = items[0]
  return { taken: first.observation_at, from: Math.min(...prices), to: Math.max(...prices),
    name: `${SENSOR[first.sensor_type] === 'радар' ? 'радара' : 'оптики'} ${number(first.resolution_m)} м` }
})

// Как посчитана цена: по каждой группе скидки корзины (обычно одна: все зоны одним продуктом).
function freshnessName(line) {
  if (line.acquisition_type === 'new') return 'новая съёмка события'
  return (line.freshness_coef ?? 1) < 1 ? 'архив старше 90 дней' : 'снимок не старше 90 дней'
}
const priceGroups = computed(() => {
  const quote = pricing.value
  if (!quote?.lines?.length) return []
  const groups = quote.groups?.length ? quote.groups : [{
    discount_group_id: quote.discount_group_id, candidate_ids: quote.lines.map(line => line.candidate_id),
    sensor_type: quote.sensor_type || 'optical', resolution_m: quote.resolution_m ?? 1,
    group_area_km2: quote.group_area_km2, previous_group_area_km2: quote.previous_group_area_km2,
    order_year: quote.order_year, discount_formula: null,
  }]
  return groups.map(group => {
    const lines = quote.lines.filter(line => group.candidate_ids?.includes(line.candidate_id))
    const first = { ...quote, ...(lines[0] || quote.lines[0]) }
    const level = first.processing_level || 'L2'
    const formula = (group.discount_formula || '').replace(/^\w+:\s*/, '')
    return {
      id: group.discount_group_id, lines: lines.length ? lines : quote.lines, first, level,
      title: `${capital(productName({ ...first, ...group }) || 'снимок')}, ${level}, ${freshnessName(first)}`,
      usage: USAGE[first.usage_type || 'internal'] || first.usage_type,
      area: group.group_area_km2 ?? first.group_area_km2,
      hint: `Скидка считается по площади группы в корзине${group.previous_group_area_km2 ? ' вместе с прежними заказами' : ''}: `
        + `чем больше площадь, тем дешевле каждый км². Группа скидки: ${productName({ ...first, ...group, acquisition_type: 'new' })}, `
        + `заказы ${group.order_year ?? quote.order_year ?? ''} года` + (formula ? `. Формула: ${formula}` : ''),
    }
  })
})
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
  <div class="procurement-mode-switch">
    <!-- Два разных расчёта: сценарные рубли по ПП 840 идут в A/B/C и в ZIP, доллары SkyFi живут отдельно. -->
    <button aria-label="Расчёт по ТЗ" :class="{ active: !state.procurementOpen }" v-tip="'Цены в рублях по ПП № 840, базовая ставка сценарная. Этот расчёт идёт в сравнение A/B/C и в «Скачать решение»'"
      @click="state.procurementOpen = false">Расчёт по ТЗ<span class="mode-currency" aria-hidden="true">₽</span></button>
    <button aria-label="Каталог SkyFi" :class="{ active: state.procurementOpen }" v-tip="'Реальные предложения SkyFi в долларах США. Отдельный подбор: в сравнение A/B/C и в основной комплект не входит, у него своя выгрузка'"
      @click="state.procurementOpen = true">Каталог SkyFi<span class="mode-currency" aria-hidden="true">$</span></button>
  </div>
  <ProcurementView v-if="state.procurementOpen" :key="`${state.sceneId}:${state.procurementProjectId || 'case'}`" />
  <template v-else>
  <div class="procurement-mode-switch" role="group" aria-label="Правило выбора зон">
    <button :class="{ active: procurementMethod === 'budget' }" :aria-pressed="procurementMethod === 'budget'" @click="procurementMethod = 'budget'">По бюджету</button>
    <button :class="{ active: procurementMethod === 'value' }" :aria-pressed="procurementMethod === 'value'" @click="procurementMethod = 'value'">По ценности информации</button>
  </div>
  <VoiPanel v-show="procurementMethod === 'value'" :active="procurementMethod === 'value'" @zone="id => emit('zone', id)" />
  <template v-if="procurementMethod === 'budget'">
  <section class="block">
    <label class="budget-label" for="budget">
      <span class="caption">Бюджет на снимки</span>
      <span class="budget-value"><AnimatedNumber :value="state.budget" :duration="0.4" /> ₽</span>
    </label>
    <input id="budget" v-model.number="state.budget" class="range" type="range" min="0" :max="maxBudget" step="250"
      :style="{ '--progress': Math.min(state.budget / maxBudget * 100, 100) + '%' }" />
    <div class="control-row">
      <span v-tip="`Через сколько часов нужно решение. Оптика 1 м приходит через ${deliveryHours.optical} ч, радар 3 м через ${deliveryHours.sar} ч: оба срока сценарные. Если оптика не успевает, план берёт радар`">Решение нужно через</span>
      <BubbleTabs v-model="state.deadline" :options="deadlineOptions" size="sm" label="Срок решения" />
    </div>
    <div class="control-row">
      <span>Что важнее</span>
      <BubbleTabs v-model="state.priority" :options="PRIORITIES" size="sm" label="Цель плана" />
    </div>

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

  <section class="block">
    <div class="panel-summary"><strong>{{ rub(plan?.data_cost_rub) }}</strong><span>{{ plural(plan?.zone_ids?.length || 0, ['зона', 'зоны', 'зон']) }} · {{ plural(plan?.covered_asset_ids?.length || 0, ['объект', 'объекта', 'объектов']) }}</span></div>
    <p class="panel-note">В заказе {{ percent(plan?.coverage_share) }} суммы ожидаемого ущерба и {{ percent(plan?.covered_doubt_share) }} сомнительной площади.</p>
  </section>
  <p v-if="plan?.coverage_share == null" class="warn-note">В области нет объектов с оценённым ущербом, долю считать не от чего.</p>
  <p v-if="plan?.reason" class="warn-note">{{ plan.reason }}</p>

  <BacktestPanel :scene-id="state.sceneId" />

  <details v-if="chart" class="disclosure"><summary>Цена и охват зон</summary><div class="disclosure-body">
    <span class="caption" v-tip="'Линия: лучший набор зон для каждой суммы заказа по доле сомнительной площади. Точки: варианты A, B, C и все сомнительные участки вместе'">Сколько сомнений закрывает каждый рубль</span>
    <FrontierChart :frontier="chart.frontier" :points="chart.points" :budget="state.budget" />
  </div></details>

  <details v-if="state.strategy !== 'A' && decisions.length" class="disclosure"><summary>Состав заказа · {{ plural(plan?.zone_ids?.length || 0, ['зона', 'зоны', 'зон']) }}</summary><div class="disclosure-body">
    <div class="zone-list">
      <button v-for="row in (state.strategy === 'C' ? chosen : decisions.filter(item => plan.zone_ids.includes(item.zone_id)))"
        :key="row.zone_id" class="zone-item chosen" :class="row.kind"
        @pointerenter="hover(row.zone_id)" @pointerleave="hover(null)" @focus="hover(row.zone_id)" @blur="hover(null)"
        @click="emit('zone', row.zone_id)">
        <i class="zone-swatch"></i>
        <span class="zone-text">
          <strong>{{ zoneTitle(row.zone) }}</strong>
          <small>{{ state.strategy === 'C' ? why(row) : `${percent(row.doubt_share)} сомнительной площади` }}</small>
          <small class="zone-facts tabular">{{ price(row) }}</small>
          <small class="zone-facts tabular" :class="{ late: term(row).late }">{{ term(row).text }}</small>
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
              <small class="zone-facts tabular">{{ price(row) }}</small>
              <small class="zone-facts tabular" :class="{ late: term(row).late }">{{ term(row).text }}</small>
            </span>
            <span class="tabular zone-area">{{ km(row.area_km2) }}</span>
          </button>
        </div>
      </Transition>
    </template>
    <p v-if="archive" class="source-line archive-note"
      v-tip="'Кадр снят до события, поэтому воду на момент паводка не показывает. Его можно купить для сравнения, но в охват ущерба и сомнений он не идёт'">
      Архивный кадр {{ archive.name }} снят {{ date(archive.taken) }}, до события: {{ archive.from === archive.to ? rub(archive.from) : `${rub(archive.from)} … ${rub(archive.to)}` }} за зону. Только контекст, в охват не входит.
    </p>
  </div></details>

  <details v-if="pricing?.lines?.length" class="block price-details disclosure">
    <summary>Как посчитана цена</summary>
    <template v-for="group in priceGroups" :key="group.id || 'group'">
      <p class="price-product">{{ group.title }}</p>
      <p class="formula tabular">
        {{ number(group.first.base_rate_rub_km2, 2) }} ₽ × {{ number(group.first.processing_coef, 1) }} × {{ number(group.first.usage_coef, 1) }}
        × {{ number(group.first.freshness_coef, 1) }} × {{ number(group.first.discount_coef, 4) }} = <b>{{ number(group.first.unit_price_rub_km2, 2) }} ₽ за км²</b>
      </p>
      <div class="mini-rows price-rows">
        <div><span>Б · базовая ставка, сценарная</span><b class="tabular">{{ number(group.first.base_rate_rub_km2, 2) }} ₽/км²</b></div>
        <div><span>О · обработка {{ group.level }}</span><b class="tabular">× {{ number(group.first.processing_coef, 1) }}</b></div>
        <div><span>П · {{ group.usage }}</span><b class="tabular">× {{ number(group.first.usage_coef, 1) }}</b></div>
        <div><span>Т · {{ freshnessName(group.first) }}</span><b class="tabular">× {{ number(group.first.freshness_coef, 1) }}</b></div>
        <div v-tip="group.hint">
          <span>Р · скидка за {{ km(group.area) }}</span><b class="tabular">× {{ number(group.first.discount_coef, 4) }}</b></div>
        <div v-for="line in group.lines" :key="line.candidate_id">
          <span>{{ line.candidate_id }} · {{ km(line.area_km2) }}</span><b class="tabular">{{ rub(line.cost_rub) }}</b></div>
      </div>
    </template>
    <div class="mini-rows price-rows">
      <div class="total"><span>Снимки</span><b class="tabular">{{ rub(pricing.total_rub) }}</b></div>
      <div v-if="plan?.decision_cost_rub != null" v-tip="costTip"><span>С обработкой · сценарий</span><b class="tabular">{{ rub(plan.decision_cost_rub) }}</b></div>
      <div v-tip="residual.tip"><span>Остаток сомнений · сценарий</span><b class="tabular">{{ residual.text }}</b></div>
    </div>
    <p class="source-line">Формула ПП РФ №840 в редакции от 27.08.2025. Группа скидки {{ priceGroups.map(group => group.id).filter(Boolean).join(', ') || 'н/д' }}.</p>
  </details>

  <details v-if="state.strategy === 'C' && scenarios.length" class="disclosure"><summary>Если бюджет изменится</summary><div class="disclosure-body">
    <span class="caption" v-tip="'План C пересчитан заново, когда бюджет или тариф меняется на 20%. Остальное на месте'">Если бюджет или тариф другие</span>
    <div class="mini-rows scenario-rows">
      <div class="scenario-head"><span></span><span>заказ</span><span>ущерб в зонах</span></div>
      <div v-for="item in scenarios" :key="item.input + item.factor">
        <span>{{ item.input === 'budget' ? 'Бюджет' : 'Тариф' }} {{ item.factor < 1 ? '−20%' : '+20%' }}</span>
        <span class="scenario-zones">{{ plural(item.zone_ids.length, ['зона', 'зоны', 'зон']) }} за {{ compactRub(item.data_cost_rub) }}</span>
        <b class="tabular" v-tip="'Ожидаемый ущерб в зонах такого заказа'">{{ compactRub(item.covered_expected_loss_rub) }}</b>
      </div>
    </div>
  </div></details>


  <p class="source-line" v-tip="`Ставка 788,64 ₽/км² взята из контрольного примера постановки, это не цена поставщика. Сроки поставки (оптика ${deliveryHours.optical} ч, радар ${deliveryHours.sar} ч) тоже допущение, как и то, что над зоной оптики не будет облаков: сквозь них она воду не видит, радар видит. Ущерб в зонах показывает, что проверять первым, а не сколько потерь удалось избежать`">
    <Info :size="14" />Ставка и срок поставки сценарные
  </p>
  </template>
</template>
</template>
