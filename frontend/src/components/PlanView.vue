<script setup>
import { computed } from 'vue'
import { Info, Pin, X } from '@lucide/vue'
import AnimatedNumber from './AnimatedNumber.vue'
import BubbleTabs from './BubbleTabs.vue'
import RingGauge from './RingGauge.vue'
import { activePlan, state } from '../store'
import { compactRub, km, number, percent, plural, rub } from '../lib/format'

const MAX_BUDGET = 45000
const analysis = computed(() => state.analysis)
const STRATEGIES = {
  A: { name: 'Открытые данные', hint: 'Ждать следующий бесплатный пролёт Sentinel‑1, ничего не покупать' },
  B: { name: 'Широкая съёмка', hint: 'Купить съёмку всего снимка целиком' },
  C: { name: 'Приоритетная', hint: 'Купить только зоны вокруг объектов с наибольшим ожидаемым ущербом' },
}
const DEADLINES = [4, 8, 12].map(hours => ({ value: hours, label: hours + ' ч' }))
const PRIORITIES = [
  { value: 'risk', label: 'Ущерб', hint: 'Покрыть как можно больше ожидаемого ущерба' },
  { value: 'information', label: 'Уточнение', hint: 'Доснять места, где модель менее всего уверена' },
]
const focusedZones = computed(() => {
  const ids = new Set(activePlan.value?.zone_ids || [])
  return analysis.value?.zones.filter(zone => ids.has(zone.id)) || []
})
function status(key) {
  const plan = analysis.value.strategies[key]
  if (!plan.timely) return 'позже срока'
  if (key === 'C' && plan.constraints_feasible === false) return 'условие не выполнено'
  if (!plan.budget_feasible) return 'сверх бюджета'
  return key === 'A' ? 'без покупки' : 'в бюджете'
}
const scenarios = computed(() => (analysis.value?.sensitivity?.plan || []).filter(row => row.factor !== 1))
const evidence = computed(() => analysis.value?.decision_evidence)
const PRIORITY_NAMES = { risk: 'Ущерб', information: 'Уточнение' }
function pick(key) {
  state.strategy = key
  state.showZones = true
}
</script>

<template>
  <section class="block">
    <label class="budget-label" for="budget">
      <span class="caption">Бюджет на данные</span>
      <span class="budget-value"><AnimatedNumber :value="state.budget" :duration="0.4" /> ₽</span>
    </label>
    <input id="budget" v-model.number="state.budget" class="range" type="range" min="0" :max="MAX_BUDGET" step="250"
      :style="{ '--progress': (state.budget / MAX_BUDGET * 100) + '%' }" />
    <div class="control-row">
      <span v-tip="'Через сколько часов нужно решение. Коммерческая съёмка приходит через 6 ч'">Решение через</span>
      <BubbleTabs v-model="state.deadline" :options="DEADLINES" size="sm" label="Срок решения" />
    </div>
    <div class="control-row">
      <span>Цель</span>
      <BubbleTabs v-model="state.priority" :options="PRIORITIES" size="sm" label="Цель плана" />
    </div>
    <div v-if="state.mustCover" class="constraint">
      <Pin :size="14" />Обязательно: {{ state.mustCover }}
      <button class="ghost-icon tiny" aria-label="Снять ограничение" @click="state.mustCover = null"><X :size="14" /></button>
    </div>
  </section>

  <div class="rows strategy-rows" role="radiogroup" aria-label="Стратегия">
    <button v-for="key in ['A', 'B', 'C']" :key="key" v-tip="STRATEGIES[key].hint" class="row strategy-row"
      role="radio" :aria-checked="state.strategy === key" :class="{ selected: state.strategy === key }" @click="pick(key)">
      <span class="strategy-key">{{ key }}</span>
      <span class="asset-main">
        <strong>{{ STRATEGIES[key].name }}</strong>
        <span class="asset-meta">
          <span class="meter coverage"><span :style="{ transform: `scaleX(${analysis.strategies[key].coverage_share || 0})` }"></span></span>
          <small :class="{ warn: status(key) !== 'в бюджете' && status(key) !== 'без покупки' }">{{ status(key) }}</small>
        </span>
      </span>
      <strong class="tabular">{{ compactRub(analysis.strategies[key].data_cost_rub) }}</strong>
    </button>
  </div>

  <Transition name="swap" mode="out-in">
    <section :key="state.strategy" class="block plan-result">
      <RingGauge :value="activePlan?.coverage_share" label="ущерба под наблюдением" color="var(--reference)" />
      <div class="mini-rows">
        <div><span>Стоимость</span><b class="tabular">{{ rub(activePlan?.data_cost_rub) }}</b></div>
        <div><span>Объектов в зонах</span><b class="tabular">{{ activePlan?.covered_asset_ids?.length || 0 }} из {{ analysis.assets.length }}</b></div>
        <div><span>Зон к заказу</span><b class="tabular">{{ activePlan?.zone_ids?.length || 0 }}</b></div>
      </div>
    </section>
  </Transition>
  <p v-if="activePlan?.reason" class="warn-note">{{ activePlan.reason }}</p>

  <section v-if="state.strategy === 'C' && analysis.metrics.data_savings_vs_broad_rub != null" class="block savings"
    v-tip="'Разница затрат на данные между широкой съёмкой B и выборочной C при том же тарифе. Это не предотвращённый ущерб'">
    <span class="caption">Экономия против широкой съёмки</span>
    <div class="hero small">
      <span class="display tabular">−{{ number(analysis.metrics.data_savings_vs_broad_rub / 1000, 1) }}</span>
      <span class="unit">тыс. ₽</span>
      <span class="hero-aside">{{ percent(analysis.metrics.savings_share_vs_broad) }}<br>бюджета B</span>
    </div>
  </section>

  <section v-if="scenarios.length" class="block">
    <span class="caption" v-tip="'План C пересчитан при изменении одного входа на 20%. Охваченный ущерб — риск под наблюдением, а не предотвращённые потери'">Устойчивость плана, ±20%</span>
    <div class="mini-rows scenario-rows">
      <div v-for="item in scenarios" :key="item.input + item.factor">
        <span>{{ item.input === 'budget' ? 'Бюджет' : 'Тариф' }} {{ item.factor < 1 ? '−20%' : '+20%' }}</span>
        <span class="scenario-zones">{{ plural(item.zone_ids.length, ['зона', 'зоны', 'зон']) }} ·{{ compactRub(item.data_cost_rub) }}</span>
        <b class="tabular">{{ compactRub(item.covered_expected_loss_rub) }}</b>
      </div>
    </div>
  </section>

  <section v-if="evidence" class="block" v-tip="'Цель «Уточнение» опирается на разброс моделей ансамбля. Сколько на самом деле даст новый снимок, ещё не измерено'">
    <span class="caption">Если цель «{{ PRIORITY_NAMES[evidence.alternate_priority] }}»</span>
    <div class="evidence">
      <strong>{{ evidence.priority_changes_order ? 'Другой набор зон' : 'Те же зоны' }}</strong>
      <span class="tabular">{{ evidence.alternate_zone_ids.join(', ') }} · охват {{ compactRub(evidence.alternate_covered_expected_loss_rub) }}</span>
    </div>
  </section>

  <div v-if="focusedZones.length" class="rows zone-rows">
    <div v-for="zone in focusedZones" :key="zone.id" class="row static">
      <span>{{ zone.name }}</span><strong class="tabular">{{ km(zone.area_km2) }}</strong>
    </div>
  </div>

  <p class="source-line" v-tip="'Тариф 788,64 ₽/км² по формуле постановления №840, поставка через 6 ч. Это сценарий кейса, а не предложение поставщика. Охват ущерба не означает предотвращённые потери'">
    <Info :size="14" />Тариф и срок поставки сценарные
  </p>
</template>
