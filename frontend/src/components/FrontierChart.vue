<script setup>
import { computed } from 'vue'
import { compactRub, percent } from '../lib/format'

/*
 * Цена уверенности: для каждой суммы берётся лучший набор зон и показывается,
 * какую долю сомнительной площади он закрывает. Точки A, B, C и «все сомнительные участки» стоят на
 * тех же осях, бюджет отмечен вертикальной чертой. Видно, где рубль ещё работает,
 * а где начинается масштабная закупка.
 */
const props = defineProps({
  description: { type: String, default: 'Доля сомнительной площади, которую закрывает лучший набор зон при каждой сумме заказа' },
  frontier: { type: Array, required: true },
  points: { type: Array, required: true },
  budget: { type: Number, required: true },
  maxCostRub: { type: Number, default: null },
  wide: Boolean,
})
const W = computed(() => props.wide ? 560 : 300)
const H = computed(() => props.wide ? 190 : 128)
const PAD = computed(() => props.wide ? { left: 38, right: 18, top: 15, bottom: 27 } : { left: 30, right: 12, top: 12, bottom: 22 })
const fullCost = computed(() => Math.max(...props.frontier.map(p => p.cost_rub), ...props.points.map(p => p.cost), props.budget, 1))
const maxCost = computed(() => Math.max(1, props.budget, props.maxCostRub ?? fullCost.value))
const visibleFrontier = computed(() => props.frontier.filter(point => point.cost_rub <= maxCost.value))
const visiblePoints = computed(() => props.points.filter(point => point.cost <= maxCost.value))
const x = cost => PAD.value.left + (cost / maxCost.value) * (W.value - PAD.value.left - PAD.value.right)
const y = share => H.value - PAD.value.bottom - share * (H.value - PAD.value.top - PAD.value.bottom)
const path = computed(() => {
  let d = ''
  let last = null
  for (const point of visibleFrontier.value) {
    const px = x(point.cost_rub).toFixed(1)
    const py = y(point.doubt_share).toFixed(1)
    d += last ? `H${px}V${py}` : `M${px} ${py}`
    last = point
  }
  return last ? d + `H${x(maxCost.value).toFixed(1)}` : ''
})
const budgetX = computed(() => x(Math.min(props.budget, maxCost.value)))
const budgetLabelRight = computed(() => budgetX.value > W.value - PAD.value.right - 48)
// Подпись уходит влево у правого края и там, где точку пересекает линия бюджета.
const leftLabel = point => point.cost > maxCost.value * 0.85 || (x(point.cost) < budgetX.value && budgetX.value - x(point.cost) < 18)
</script>

<template>
  <svg class="frontier" :viewBox="`0 0 ${W} ${H}`" role="img"
    :aria-label="description">
    <line v-for="share in [0, 0.5, 1]" :key="share" class="grid" :x1="PAD.left" :x2="W - PAD.right" :y1="y(share)" :y2="y(share)" />
    <text v-for="share in [0, 0.5, 1]" :key="'t' + share" class="axis" :x="PAD.left - 5" :y="y(share) + 3" text-anchor="end">{{ percent(share) }}</text>
    <text class="axis" :x="PAD.left" :y="H - 6">0 ₽</text>
    <text class="axis" :x="W - PAD.right" :y="H - 6" text-anchor="end">{{ compactRub(maxCost) }}</text>
    <line class="budget" :x1="budgetX" :x2="budgetX" :y1="PAD.top - 4" :y2="H - PAD.bottom" />
    <text class="axis budget-label" :x="budgetX + (budgetLabelRight ? -4 : 4)" :y="PAD.top + 4" :text-anchor="budgetLabelRight ? 'end' : 'start'">бюджет</text>
    <path class="curve" :d="path" />
    <g v-for="point in visiblePoints" :key="point.key" :class="['point', point.key, { active: point.active }]">
      <circle :cx="x(point.cost)" :cy="y(point.share)" :r="point.active ? 6 : 4.5" />
      <text :x="x(point.cost) + (leftLabel(point) ? -8 : 8)" :y="y(point.share) + (point.share > 0.85 ? 12 : -6)"
        :text-anchor="leftLabel(point) ? 'end' : 'start'">{{ point.label }}</text>
    </g>
  </svg>
</template>
