<script setup>
import { gsap } from 'gsap'
import { Flip } from 'gsap/Flip'
import { nextTick, ref, watch } from 'vue'

gsap.registerPlugin(Flip)

const props = defineProps({
  open: { type: Boolean, default: true },
  layoutKey: { type: String, default: '' },
})
const shell = ref(null)
const body = ref(null)
const expanded = ref(props.open)

/*
 * Панель не исчезает, а стягивается в капсулу с главным числом: карта
 * получает место, а итог остаётся на виду. Flip анимирует геометрию между
 * двумя состояниями, содержимое проявляется следом.
 */
watch(() => [props.open, props.layoutKey], async ([open], [wasOpen]) => {
  if (!shell.value) return
  const snapshot = Flip.getState(shell.value)
  expanded.value = open
  await nextTick()
  Flip.from(snapshot, { duration: 0.62, ease: 'expo.out', scale: false, simple: true })
  if (open !== wasOpen && body.value) {
    gsap.from(body.value, { opacity: 0, y: open ? 10 : 0, duration: 0.4, delay: 0.12, ease: 'power2.out', clearProps: 'all' })
  }
})
</script>

<template>
  <section ref="shell" class="panel morph" :class="{ pill: !expanded }">
    <div ref="body" class="morph-body">
      <slot v-if="expanded" />
      <slot v-else name="pill" />
    </div>
  </section>
</template>
