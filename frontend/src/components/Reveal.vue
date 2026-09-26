<script setup>
import { gsap } from 'gsap'
import { onBeforeUnmount } from 'vue'
import { reducedMotion } from '../lib/panelMotion'

const active = new Set()
function clear(el) {
  gsap.killTweensOf(el)
  gsap.set(el, { clearProps: 'height,opacity,paddingTop,paddingBottom,overflow' })
  active.delete(el)
}
function run(el, done, opening) {
  clear(el)
  if (reducedMotion()) { done(); return }
  const style = getComputedStyle(el)
  const full = { height: el.getBoundingClientRect().height, paddingTop: style.paddingTop, paddingBottom: style.paddingBottom, opacity: 1 }
  const empty = { height: 0, paddingTop: 0, paddingBottom: 0, opacity: 0 }
  active.add(el)
  gsap.set(el, { overflow: 'hidden' })
  gsap.fromTo(el, opening ? empty : full, {
    ...(opening ? full : empty), duration: .26, ease: 'power2.inOut',
    onComplete: () => { clear(el); done() },
  })
}
onBeforeUnmount(() => { for (const el of active) clear(el) })
</script>

<template>
  <Transition :css="false" @enter="(el, done) => run(el, done, true)" @leave="(el, done) => run(el, done, false)"
    @enter-cancelled="clear" @leave-cancelled="clear"><slot /></Transition>
</template>
