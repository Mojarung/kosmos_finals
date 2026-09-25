<script setup>
/*
 * Доли одного целого одной полосой: длина кодирует долю, подпись под
 * сегментом называет число. Процент уходит в подсказку, чтобы не было
 * трёх изображений одной величины.
 */
defineProps({
  segments: { type: Array, required: true },
})
</script>

<template>
  <div class="share-bar">
    <div class="share-track">
      <span v-for="segment in segments" :key="segment.key" class="share-segment"
        :style="{ flexGrow: Math.max(segment.share, 0.006), background: segment.color }"></span>
    </div>
    <div class="share-legend" :style="{ gridTemplateColumns: `repeat(${segments.length}, 1fr)` }">
      <div v-for="segment in segments" :key="segment.key" v-tip="segment.hint">
        <span class="share-name"><i :style="{ background: segment.color }"></i>{{ segment.label }}</span>
        <strong class="tabular">{{ segment.value }}</strong>
      </div>
    </div>
  </div>
</template>
