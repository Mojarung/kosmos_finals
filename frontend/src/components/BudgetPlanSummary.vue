<script setup>
import { ref, watch } from 'vue'
import { Pin, X } from '@lucide/vue'
import Money from './Money.vue'
import { state } from '../store'
import { percent, plural } from '../lib/format'
const props = defineProps({ strategies: Object, plan: Object, maxBudget: Number, requiredId: String, requiredLabel: String, coveredCount: Number, objectWords: {type:Array,default:()=>['объект','объекта','объектов']}, doubtLabel:{type:String,default:'сомнительной площади'}, deliveryHours:{type:Object,default:()=>({optical:6,sar:4})} })
const strategyDisclosure = ref(null)
const draftBudget = ref(state.budget)
const dragging = ref(false)
const sliderMax = ref(Math.max(10000, props.maxBudget || 0))
const knownBroadCost = ref(props.strategies?.B?.data_cost_rub || 0)
watch(() => props.strategies?.B?.data_cost_rub, cost => {
  if (cost != null) knownBroadCost.value = cost
  if (cost != null && !dragging.value) sliderMax.value = Math.max(10000, Math.ceil(cost / 1000) * 1000, state.budget)
}, { immediate: true })
watch(() => state.budget, value => {
  if (dragging.value) return
  draftBudget.value = value
  sliderMax.value = Math.max(10000, Math.ceil(knownBroadCost.value / 1000) * 1000, value)
})
function updateDraft(event) { draftBudget.value = Number(event.target.value) }
function commitBudget(event) {
  const value = Number(event?.target?.value ?? draftBudget.value)
  dragging.value = false
  if (!Number.isFinite(value)) { draftBudget.value = state.budget; return }
  draftBudget.value = Math.max(0, Math.min(10000000, value))
  sliderMax.value = Math.max(10000, Math.ceil(knownBroadCost.value / 1000) * 1000, draftBudget.value)
  state.budget = draftBudget.value
}
function cancelBudget() { dragging.value = false; draftBudget.value = state.budget }
const emit = defineEmits(['clear-required'])
const STRATEGIES = { A:{name:'Не покупать',line:'ждём бесплатный пролёт Sentinel‑1'}, B:{name:'Снять всё',line:'вся область одним заказом'}, C:{name:'Только нужные зоны',line:'лучшее, что влезает в бюджет'} }
function status(key) { const row=props.strategies[key]; return !row.budget_feasible?'дороже бюджета':!row.timely?'не успеет к сроку':row.constraints_feasible===false || row.mandatory_satisfied===false && key==='C'?'условие не выполнить':key==='A'?'без покупки':'в бюджете' }
const warn = key => !['в бюджете','без покупки'].includes(status(key))
function statusTip(key) {
  const row = props.strategies[key]
  const parts = []
  if (!row.budget_feasible) parts.push('В бюджет не входит; показано для сравнения цены и охвата')
  if (!row.timely) parts.push(`Снимки придут позже срока решения (${state.deadline} ч), поэтому в охват не идут`)
  if (key === 'A') parts.push('Платных снимков нет; работа аналитика учитывается отдельно')
  if (row.reason) parts.push(row.reason)
  return parts.join('. ') || null
}
function pick(key, close=true) {state.strategy=key;state.showZones=true;if(close && strategyDisclosure.value)strategyDisclosure.value.open=false}
function strategyKeydown(event,key) {const step={ArrowDown:1,ArrowRight:1,ArrowUp:-1,ArrowLeft:-1}[event.key];if(!step)return;event.preventDefault();const keys=['A','B','C'];const next=keys[(keys.indexOf(key)+step+3)%3];pick(next,false);event.currentTarget.parentElement?.querySelector(`[data-key="${next}"]`)?.focus()}
</script>
<template>
  <section v-if="plan" class="plan-price-hero">
    <span class="plan-price-label">Стоимость снимков</span>
    <strong class="plan-price"><Money :value="plan.data_cost_rub" /></strong>
    <div class="plan-price-meta"><span>{{ plural(plan.zone_ids?.length || 0, ['зона','зоны','зон']) }} · {{ plural(coveredCount,objectWords) }}</span><span v-tip="`В зонах ${percent(plan.coverage_share)} ожидаемого ущерба и ${percent(plan.covered_doubt_share)} ${doubtLabel}`">{{ percent(plan.coverage_share) }} ущерба в охвате</span></div>
  </section>
  <section class="plan-controls">
    <div class="budget-label"><label for="budget">Бюджет</label><label class="budget-number"><input v-model="draftBudget" aria-label="Бюджет в рублях" type="number" min="0" max="10000000" step="1" @change="commitBudget" @blur="commitBudget" @keydown.enter="commitBudget" /> ₽</label></div>
    <input id="budget" :value="draftBudget" class="range" type="range" min="0" :max="sliderMax" step="1" :style="{ '--progress': Math.min(Number(draftBudget)/sliderMax*100,100)+'%' }" @pointerdown="dragging=true" @input="updateDraft" @change="commitBudget" @pointerup="commitBudget" @pointercancel="cancelBudget" />
    <div class="plan-control-grid">
      <label><span>Получить за</span><select v-model.number="state.deadline" aria-label="Срок решения"><option v-for="hours in [4,8,12]" :key="hours" :value="hours">{{ hours }} ч</option></select></label>
      <label><span>Приоритет</span><select v-model="state.priority" aria-label="Цель плана"><option value="risk">Ущерб</option><option value="information">Сомнения</option></select></label>
    </div>
    <div v-if="requiredId" class="constraint">
      <Pin :size="14" />Обязательно: {{ requiredLabel }}
      <button class="ghost-icon tiny" aria-label="Снять условие" @click="emit('clear-required')"><X :size="14" /></button>
    </div>
  </section>

  <details v-if="strategies" ref="strategyDisclosure" class="strategy-picker"><summary><span><b>{{ STRATEGIES[state.strategy].name }}</b></span><strong :class="{warn:warn(state.strategy)}">{{ status(state.strategy) }}</strong></summary>
  <div class="rows strategy-rows" role="radiogroup" aria-label="Вариант закупки">
    <button v-for="key in ['A', 'B', 'C']" :key="key" class="row strategy-row" :data-key="key"
      role="radio" :aria-checked="state.strategy === key" :tabindex="state.strategy === key ? 0 : -1"
      :class="{ selected: state.strategy === key }" @click="pick(key)" @keydown="strategyKeydown($event, key)">
      <span class="strategy-key">{{ key }}</span>
      <span class="asset-main">
        <strong>{{ STRATEGIES[key].name }}</strong>
        <small class="strategy-line">{{ STRATEGIES[key].line }}</small>
        <span class="strategy-meters">
          <span class="meter coverage" v-tip="'Доля ожидаемого ущерба в зонах заказа'"><span :style="{ transform: `scaleX(${strategies[key].coverage_share || 0})` }"></span></span>
          <span class="meter doubt" v-tip="'Доля сомнений в зонах заказа'"><span :style="{ transform: `scaleX(${strategies[key].covered_doubt_share || 0})` }"></span></span>
        </span>
      </span>
      <span class="strategy-cost">
        <strong class="tabular"><Money :value="strategies[key].data_cost_rub" compact /></strong>
        <small :class="{ warn: warn(key) }" v-tip="statusTip(key)">{{ status(key) }}</small>
      </span>
    </button>
  </div>
  <p class="meter-key"><i class="coverage"></i>ущерб в зонах <i class="doubt"></i>сомнения в зонах</p>
  </details>

  <p v-if="plan && plan.coverage_share == null" class="warn-note">Охват ущерба не оценён.</p>
  <p v-if="plan?.reason" class="warn-note">{{ plan.reason }}</p>

</template>
