<script setup>
import { computed } from 'vue'
import { percent } from '../lib/format'

const props = defineProps({
  value: { type: Number, default: null },
  label: { type: String, default: '' },
  size: { type: Number, default: 84 },
  color: { type: String, default: 'var(--primary)' },
})
const RADIUS = 42
const LENGTH = 2 * Math.PI * RADIUS
const offset = computed(() => LENGTH * (1 - Math.min(Math.max(props.value ?? 0, 0), 1)))
</script>

<template>
  <div class="ring-gauge">
    <div class="ring" :style="{ width: size + 'px', height: size + 'px' }">
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle cx="50" cy="50" :r="RADIUS" class="ring-track" />
        <circle cx="50" cy="50" :r="RADIUS" class="ring-value" :stroke="color"
          :stroke-dasharray="LENGTH" :stroke-dashoffset="offset" />
      </svg>
      <span class="tabular">{{ percent(value, 1) }}</span>
    </div>
    <span v-if="label" class="ring-label">{{ label }}</span>
  </div>
</template>
