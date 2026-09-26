<script setup>
import { gsap } from 'gsap'
import { Flip } from 'gsap/Flip'
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { reducedMotion } from '../lib/panelMotion'

gsap.registerPlugin(Flip)

const props = defineProps({
  open: { type: Boolean, default: true },
  layoutClass: { type: String, default: '' },
})
const shell = ref(null)
const body = ref(null)
const expanded = ref(props.open)

const layout = ref(props.layoutClass)
let revision = 0
let animation

// Measure before applying layout classes: measuring the parent's new class
// made the old resize animation start and end at the same width.
watch(() => [props.open, props.layoutClass], async ([open, classes], [wasOpen]) => {
  if (!shell.value) return
  const current = ++revision
  animation?.progress(1).kill()
  gsap.killTweensOf(body.value)
  gsap.set(body.value, { clearProps: 'transform,opacity' })
  const snapshot = reducedMotion() ? null : Flip.getState(shell.value)
  expanded.value = open
  layout.value = classes
  await nextTick()
  if (current !== revision || !shell.value || !snapshot) return
  animation = Flip.from(snapshot, { duration: .38, ease: 'power3.inOut', scale: false, simple: true })
  if (open !== wasOpen) {
    gsap.fromTo(body.value, { opacity: 0, y: open ? 6 : 0 }, {
      opacity: 1, y: 0, duration: .22, delay: .08, ease: 'power2.out', clearProps: 'transform,opacity',
    })
  }
})
onBeforeUnmount(() => {
  revision++
  animation?.kill()
  gsap.killTweensOf(body.value)
})
</script>

<template>
  <section ref="shell" class="panel morph" :class="[layout, { pill: !expanded }]">
    <div ref="body" class="morph-body">
      <div v-show="expanded" class="morph-content"><slot /></div>
      <slot v-if="!expanded" name="pill" />
    </div>
  </section>
</template>
