<script setup>
import { gsap } from 'gsap'
import { onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  value: { type: Number, default: 0 },
  format: { type: Function, default: value => Math.round(value).toLocaleString('ru-RU') },
  duration: { type: Number, default: 0.9 },
})

// Число доезжает до нового значения, а не подменяется: видно, куда и насколько сдвинулось.
const current = { value: 0 }
const text = ref(props.format(0))
let tween = null

watch(() => props.value, value => {
  tween?.kill()
  tween = gsap.to(current, {
    value: value ?? 0, duration: props.duration, ease: 'power3.out',
    onUpdate: () => { text.value = props.format(current.value) },
  })
}, { immediate: true })

onBeforeUnmount(() => tween?.kill())
</script>

<template><span class="tabular">{{ text }}</span></template>
