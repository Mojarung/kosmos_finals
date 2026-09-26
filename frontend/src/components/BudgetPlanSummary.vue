<script setup>
import { computed } from 'vue'
import { Pin, X } from '@lucide/vue'
import BubbleTabs from './BubbleTabs.vue'
import AnimatedNumber from './AnimatedNumber.vue'
import Money from './Money.vue'
import { state } from '../store'
import { percent, plural } from '../lib/format'
const props = defineProps({ strategies: Object, plan: Object, maxBudget: Number, requiredId: String, requiredLabel: String, coveredCount: Number, objectWords: {type:Array,default:()=>['объект','объекта','объектов']}, doubtLabel:{type:String,default:'сомнительной площади'}, deliveryHours:{type:Object,default:()=>({optical:6,sar:4})} })
const emit = defineEmits(['clear-required'])
const STRATEGIES = { A:{name:'Не покупать',line:'ждём бесплатный пролёт Sentinel‑1'}, B:{name:'Снять всё',line:'вся область одним заказом'}, C:{name:'Только нужные зоны',line:'лучшее, что влезает в бюджет'} }
const PRIORITIES = [{value:'risk',label:'Ущерб'},{value:'information',label:'Сомнения'}]
const deadlineOptions = computed(()=>[4,8,12].map(value=>({value,label:value+' ч'})))
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
function pick(key) {state.strategy=key;state.showZones=true}
function strategyKeydown(event,key) {const step={ArrowDown:1,ArrowRight:1,ArrowUp:-1,ArrowLeft:-1}[event.key];if(!step)return;event.preventDefault();const keys=['A','B','C'];const next=keys[(keys.indexOf(key)+step+3)%3];pick(next);event.currentTarget.parentElement?.querySelector(`[data-key="${next}"]`)?.focus()}
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
      <span v-tip="`Через сколько часов нужно решение. Оптика 1 м приходит через ${deliveryHours.optical} ч, радар 3 м через ${deliveryHours.sar} ч: оба срока сценарные. Если оптика не успевает, план берёт радар`">Решение нужно через</span>
      <BubbleTabs v-model="state.deadline" :options="deadlineOptions" size="sm" label="Срок решения" />
    </div>
    <div class="control-row">
      <span>Что важнее</span>
      <BubbleTabs v-model="state.priority" :options="PRIORITIES" size="sm" label="Цель плана" />
    </div>

    <div v-if="requiredId" class="constraint">
      <Pin :size="14" />Обязательно: {{ requiredLabel }}
      <button class="ghost-icon tiny" aria-label="Снять условие" @click="emit('clear-required')"><X :size="14" /></button>
    </div>
  </section>

  <div v-if="strategies" class="rows strategy-rows" role="radiogroup" aria-label="Вариант закупки">
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
  <p v-if="strategies" class="meter-key"><i class="coverage"></i>ущерб в зонах <i class="doubt"></i>сомнения в зонах</p>

  <section v-if="plan" class="block">
    <div class="panel-summary"><strong><Money :value="plan?.data_cost_rub" /></strong><span>{{ plural(plan?.zone_ids?.length || 0, ['зона', 'зоны', 'зон']) }} · {{ plural(coveredCount, objectWords) }}</span></div>
    <p class="panel-note">В заказе {{ percent(plan?.coverage_share) }} суммы ожидаемого ущерба и {{ percent(plan?.covered_doubt_share) }} {{ doubtLabel }}.</p>
  </section>
  <p v-if="plan && plan.coverage_share == null" class="warn-note">В области нет объектов с оценённым ущербом, долю считать не от чего.</p>
  <p v-if="plan?.reason" class="warn-note">{{ plan.reason }}</p>

</template>
