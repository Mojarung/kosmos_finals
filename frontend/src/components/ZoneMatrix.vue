<script setup>
import { computed, ref } from 'vue'
import { compactRub, percent, plural } from '../lib/format'

const props = defineProps({
  zones: { type: Array, required: true },
  metric: { type: String, default: 'loss' },
  maxPriceRub: { type: Number, default: null },
  selectedZoneId: { type: String, default: null },
})
const emit = defineEmits(['zone'])
const hoverId = ref(null)
const W = 360, H = 230
const PAD = { left: 57, right: 16, top: 18, bottom: 35 }
const fullPrice = computed(() => Math.max(1, ...props.zones.map(zone => zone.price)))
const minPrice = computed(() => props.zones.length ? Math.min(...props.zones.map(zone => zone.price)) : 0)
const maxPrice = computed(() => Math.max(1, minPrice.value, props.maxPriceRub ?? fullPrice.value))
const visible = computed(() => props.zones.filter(zone => zone.price <= maxPrice.value))
const maxValue = computed(() => Math.max(1e-9, ...visible.value.map(zone => value(zone))))
const hiddenCount = computed(() => props.zones.length - visible.value.length)
const active = computed(() => visible.value.find(zone => zone.id === hoverId.value)
  || visible.value.find(zone => zone.id === props.selectedZoneId)
  || visible.value.find(zone => zone.selected)
  || visible.value[0])
const metricName = computed(() => props.metric === 'doubt' ? 'Сомнительная площадь' : 'Ущерб внутри зоны')
function value(zone) { return props.metric === 'doubt' ? zone.doubt || 0 : zone.loss || 0 }
function x(price) { return PAD.left + (price / maxPrice.value) * (W - PAD.left - PAD.right) }
function y(amount) { return H - PAD.bottom - (amount / maxValue.value) * (H - PAD.top - PAD.bottom) }
function formatValue(amount) { return props.metric === 'doubt' ? percent(amount, 1) : compactRub(amount) }
function choose(zone) { emit('zone', zone.id) }
</script>

<template>
  <div v-if="zones.length" class="zone-matrix">
    <svg :viewBox="`0 0 ${W} ${H}`" role="group" :aria-label="`Зоны: цена отдельного снимка и ${metricName.toLowerCase()}`">
      <line v-for="tick in [0, .5, 1]" :key="tick" class="zone-matrix-grid" :x1="PAD.left" :x2="W - PAD.right" :y1="y(maxValue * tick)" :y2="y(maxValue * tick)" />
      <text v-for="tick in [0, .5, 1]" :key="`y${tick}`" class="zone-matrix-axis" :x="PAD.left - 7" :y="y(maxValue * tick) + 4" text-anchor="end">{{ formatValue(maxValue * tick) }}</text>
      <text class="zone-matrix-axis" :x="PAD.left" :y="H - 10">0 ₽</text>
      <text class="zone-matrix-axis" :x="W - PAD.right" :y="H - 10" text-anchor="end">{{ compactRub(maxPrice) }}</text>
      <g v-for="zone in visible" :key="zone.id" class="zone-matrix-point" :class="{ selected:zone.selected, active:active?.id===zone.id }"
        role="button" tabindex="0" :aria-label="`${zone.label}, ${compactRub(zone.price)} за отдельную зону, ${metricName.toLowerCase()} ${formatValue(value(zone))}, ${zone.status}`"
        @mouseenter="hoverId=zone.id" @mouseleave="hoverId=null" @focus="hoverId=zone.id" @blur="hoverId=null"
        @click="choose(zone)" @keydown.enter.prevent="choose(zone)" @keydown.space.prevent="choose(zone)">
        <circle :cx="x(zone.price)" :cy="y(value(zone))" :r="active?.id===zone.id ? 12 : 10" />
        <text :x="x(zone.price)" :y="y(value(zone)) + 3.5" text-anchor="middle">{{ zone.number }}</text>
      </g>
    </svg>
    <div class="zone-matrix-caption"><span>Цена отдельной зоны →</span><span v-if="hiddenCount">{{ plural(hiddenCount, ['кандидат','кандидата','кандидатов']) }} за пределом шкалы</span><span v-else>● в заказе · ○ вне заказа</span></div>
    <button v-if="active" class="zone-matrix-detail" @click="choose(active)">
      <span><b>{{ active.label }}</b><small>{{ active.status }} · {{ active.area != null ? `${active.area.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} км²` : 'площадь н/д' }}</small></span>
      <span><strong>{{ compactRub(active.price) }}</strong><small>{{ metricName }}: {{ formatValue(value(active)) }}</small></span>
    </button>
  </div>
  <p v-else class="insights-caveat">Для этой области нет зон-кандидатов.</p>
</template>
