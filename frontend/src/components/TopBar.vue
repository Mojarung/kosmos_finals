<script setup>
import { computed } from 'vue'
import { Box, Globe2, LocateFixed, Moon, Sun, Waves } from '@lucide/vue'
import BubbleTabs from './BubbleTabs.vue'
import { chooseScene, state } from '../store'
import { toggleTheme } from '../lib/theme'
import { date, km } from '../lib/format'

const emit = defineEmits(['globe', 'focus-chip', 'tilt'])

const scenes = computed(() => (state.analysis?.scenes || []).map(scene => ({
  value: scene.chip_id,
  label: scene.event,
  hint: `${date(scene.observed_at)} · ${scene.chip_id} · вода ${km(scene.model_water_area_km2)}`,
})))
const scene = computed({
  get: () => state.sceneId,
  set: id => { if (!chooseScene(id)) emit('focus-chip') },
})
</script>

<template>
  <header class="topbar">
    <div class="panel capsule brand" data-enter
      v-tip="'Оперативный атлас паводка: вода по Sentinel‑1, риск для объектов и заказ доснимки под бюджет'">
      <span class="brand-mark"><Waves :size="17" :stroke-width="2.2" /></span>
      <span class="brand-copy">
        <strong>FloodValue</strong>
        <small v-if="state.analysis">{{ state.analysis.chip.event }}, {{ date(state.analysis.chip.observed_at) }}</small>
        <small v-else>Атлас паводка</small>
      </span>
    </div>

    <div class="topbar-center">
      <div v-if="scenes.length" class="panel capsule scenes" data-enter>
        <BubbleTabs v-model="scene" :options="scenes" label="Событие" />
      </div>
    </div>

    <div class="panel capsule actions" data-enter>
      <button v-tip="'Вернуться к снимку'" class="ghost-icon" aria-label="Показать весь снимок" @click="emit('focus-chip')"><LocateFixed :size="18" /></button>
      <button v-tip="state.globeView ? '3D-обзор доступен, когда карта приближена к территории' : state.tilted ? 'Вернуть вид сверху' : 'Наклонный 3D-обзор территории'"
        class="ghost-icon" :class="{ active: state.tilted }" :aria-pressed="state.tilted" aria-label="3D-обзор"
        :aria-disabled="state.globeView" @click="emit('tilt')"><Box :size="18" /></button>
      <button v-tip="'Вся Земля: где лежат три сцены'" class="ghost-icon" aria-label="Показать Землю" @click="emit('globe')"><Globe2 :size="18" /></button>
      <button v-tip="state.theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'" class="ghost-icon" aria-label="Сменить тему" @click="toggleTheme">
        <Sun v-if="state.theme === 'dark'" :size="18" /><Moon v-else :size="18" />
      </button>
    </div>
  </header>
</template>
