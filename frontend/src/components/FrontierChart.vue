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
  frontier: { type: Array, required: true },
  points: { type: Array, required: true },
  budget: { type: Number, required: true },
})
const W = 300
const H = 128
const PAD = { left: 30, right: 12, top: 12, bottom: 22 }
const maxCost = computed(() => Math.max(...props.frontier.map(p => p.cost_rub), ...props.points.map(p => p.cost), props.budget, 1))
const x = cost => PAD.left + (cost / maxCost.value) * (W - PAD.left - PAD.right)
const y = share => H - PAD.bottom - share * (H - PAD.top - PAD.bottom)
const path = computed(() => {
  let d = ''
  let last = null
  for (const point of props.frontier) {
    const px = x(point.cost_rub).toFixed(1)
    const py = y(point.doubt_share).toFixed(1)
    d += last ? `H${px}V${py}` : `M${px} ${py}`
    last = point
  }
  return last ? d + `H${x(maxCost.value).toFixed(1)}` : ''
})
const budgetX = computed(() => x(Math.min(props.budget, maxCost.value)))
</script>

<template>
  <svg class="frontier" :viewBox="`0 0 ${W} ${H}`" role="img"
    :aria-label="'Доля сомнительной площади, которую закрывает лучший набор зон при каждой сумме заказа'">
    <line v-for="share in [0, 0.5, 1]" :key="share" class="grid" :x1="PAD.left" :x2="W - PAD.right" :y1="y(share)" :y2="y(share)" />
    <text v-for="share in [0, 0.5, 1]" :key="'t' + share" class="axis" :x="PAD.left - 5" :y="y(share) + 3" text-anchor="end">{{ percent(share) }}</text>
    <text class="axis" :x="PAD.left" :y="H - 6">0 ₽</text>
    <text class="axis" :x="W - PAD.right" :y="H - 6" text-anchor="end">{{ compactRub(maxCost) }}</text>
    <line class="budget" :x1="budgetX" :x2="budgetX" :y1="PAD.top - 4" :y2="H - PAD.bottom" />
    <text class="axis budget-label" :x="budgetX + 4" :y="PAD.top + 4">бюджет</text>
    <path class="curve" :d="path" />
    <g v-for="point in points" :key="point.key" :class="['point', point.key, { active: point.active }]">
      <circle :cx="x(point.cost)" :cy="y(point.share)" :r="point.active ? 6 : 4.5" />
      <text :x="x(point.cost) + (point.cost > maxCost * 0.85 ? -8 : 8)" :y="y(point.share) + (point.share > 0.85 ? 12 : -6)"
        :text-anchor="point.cost > maxCost * 0.85 ? 'end' : 'start'">{{ point.label }}</text>
    </g>
  </svg>
</template>
