<script setup>
import { gsap } from 'gsap'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  options: { type: Array, required: true },
  size: { type: String, default: 'md' },
  stretch: { type: Boolean, default: false },
  label: { type: String, default: '' },
})
const model = defineModel({ required: true })

const root = ref(null)
const bubble = ref(null)
const buttons = ref([])
let placed = false
let observer

/*
 * Подложка не прыгает к выбранной вкладке, а перетекает: сначала растягивается
 * в сторону цели и сплющивается, потом упруго садится на место.
 */
function move(animate) {
  const index = props.options.findIndex(option => option.value === model.value)
  const target = buttons.value[index]
  if (!target || !bubble.value) return
  const next = { x: target.offsetLeft, width: target.offsetWidth }
  if (!animate || !placed) {
    gsap.killTweensOf(bubble.value)
    gsap.set(bubble.value, { ...next, opacity: 1, scaleY: 1 })
    placed = true
    return
  }
  const from = Number(gsap.getProperty(bubble.value, 'x'))
  gsap.timeline({ defaults: { overwrite: 'auto' } })
    .to(bubble.value, { x: Math.min(from, next.x), width: Math.abs(next.x - from) * 0.35 + next.width, scaleY: 0.86, duration: 0.16, ease: 'power2.out' })
    .to(bubble.value, { ...next, scaleY: 1, duration: 0.55, ease: 'elastic.out(1, 0.62)' })
}

onMounted(() => {
  nextTick(() => move(false))
  observer = new ResizeObserver(() => move(false))
  observer.observe(root.value)
})
onBeforeUnmount(() => observer?.disconnect())
watch(model, () => move(true), { flush: 'post' })
watch(() => props.options.map(option => option.value).join(), () => nextTick(() => move(false)))
</script>

<template>
  <div ref="root" class="bubble-tabs" :class="[size, { stretch }]" role="tablist" :aria-label="label || undefined">
    <span ref="bubble" class="bubble" aria-hidden="true"></span>
    <button v-for="(option, index) in options" :key="option.value"
      :ref="el => { if (el) buttons[index] = el }" v-tip="option.hint"
      type="button" role="tab" :aria-selected="model === option.value"
      :class="{ selected: model === option.value }" @click="model = option.value">
      <component :is="option.icon" v-if="option.icon" :size="15" />
      <span>{{ option.label }}</span>
      <span v-if="option.badge != null" class="badge">{{ option.badge }}</span>
    </button>
  </div>
</template>
