<script setup>
import { gsap } from 'gsap'
import { onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({ size: { type: Number, default: 56 }, label: { type: String, default: '' } })

/*
 * Капля, которая переливается между формами с разным числом «волн» и медленно
 * вращается. У krasnoyarsk это был язык пламени; здесь форма спокойнее, как
 * поверхность воды: меньше лепестков, мягче амплитуда.
 */
const POINTS = 72
const RADIUS = 38
const SHAPES = [[0, 0], [3, 0.14], [5, 0.08], [2, 0.12], [6, 0.06]]

const path = ref(null)
const group = ref(null)
let timeline = null
let spin = null

function radii([lobes, amplitude]) {
  return Array.from({ length: POINTS }, (_, i) => RADIUS * (1 + amplitude * Math.cos((lobes * i * 2 * Math.PI) / POINTS)))
}
function toPath(values) {
  const points = values.map((r, i) => {
    const angle = (i * 2 * Math.PI) / POINTS
    return [50 + r * Math.cos(angle), 50 + r * Math.sin(angle)]
  })
  const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
  const start = mid(points[POINTS - 1], points[0])
  let d = `M${start[0].toFixed(2)} ${start[1].toFixed(2)}`
  for (let i = 0; i < POINTS; i++) {
    const next = mid(points[i], points[(i + 1) % POINTS])
    d += `Q${points[i][0].toFixed(2)} ${points[i][1].toFixed(2)} ${next[0].toFixed(2)} ${next[1].toFixed(2)}`
  }
  return d + 'Z'
}

onMounted(() => {
  const shapes = SHAPES.map(radii)
  const live = { values: [...shapes[0]] }
  const render = () => path.value?.setAttribute('d', toPath(live.values))
  render()
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  timeline = gsap.timeline({ repeat: -1 })
  shapes.forEach((_, index) => {
    timeline.to(live.values, { endArray: shapes[(index + 1) % shapes.length], duration: 0.7, ease: 'back.inOut(1.4)', onUpdate: render }, '>0.08')
  })
  spin = gsap.to(group.value, { rotation: 360, duration: 4.2, ease: 'none', repeat: -1, svgOrigin: '50 50' })
})
onBeforeUnmount(() => { timeline?.kill(); spin?.kill() })
</script>

<template>
  <div class="morph-loader" role="status" :aria-label="props.label || 'Загрузка'">
    <svg :width="props.size" :height="props.size" viewBox="0 0 100 100" aria-hidden="true">
      <g ref="group"><path ref="path" fill="var(--primary)" /></g>
    </svg>
    <span v-if="props.label">{{ props.label }}</span>
  </div>
</template>
