<script setup>
import { computed, reactive } from 'vue'
import { Box, Globe2, LocateFixed, Moon, Sun } from '@lucide/vue'
import BubbleTabs from './BubbleTabs.vue'
import { chooseScene, state } from '../store'
import { toggleTheme } from '../lib/theme'
import { date, km } from '../lib/format'

const emit = defineEmits(['globe', 'focus-chip', 'tilt'])

const scenes = computed(() => (state.analysis?.scenes || []).map(scene => ({
  value: scene.chip_id,
  label: scene.event,
  hint: `${scene.origin === 'live' ? 'Живой расчёт · ' : ''}${date(scene.observed_at)} · ${scene.chip_id} · вода ${km(scene.model_water_area_km2)}`,
})))
const current = computed(() => state.analysis?.scenes.find(item => item.chip_id === state.sceneId) || state.analysis?.chip)
// Флаг страны снимка с бесплатного flagcdn.com (SVG без ключа). Код — по префиксу chip_id;
// Меконг — снимок из Камбоджи.
// Без сети или при сбое CDN флаг просто не показываем, название остаётся.
const failedFlags = reactive(new Set())
// Все страны каталога Sen1Floods11: любой официальный чип можно посчитать живьём.
const COUNTRY_CODES = {
  Bolivia: 'bo', Ghana: 'gh', India: 'in', Mekong: 'kh', Nigeria: 'ng', Pakistan: 'pk',
  Paraguay: 'py', Somalia: 'so', Spain: 'es', 'Sri-Lanka': 'lk', USA: 'us',
}
const flag = computed(() => {
  const code = COUNTRY_CODES[current.value?.chip_id?.split('_')[0]]
  return code ? `https://flagcdn.com/${code}.svg` : null
})
const scene = computed({
  get: () => state.sceneId,
  set: id => { if (!chooseScene(id)) emit('focus-chip') },
})
</script>

<template>
  <header class="topbar">
    <!-- Заголовок сцены: страна крупно, дата снимка под ней. Меняется сразу по клику,
         не дожидаясь расчёта, и прокручивается как табло. -->
    <div v-if="current" class="panel scene-title" data-enter
      v-tip="`Снимок ${current.chip_id}: радар Sentinel‑1, поляризации VV и VH`">
      <Transition name="roll" mode="out-in">
        <div :key="current.chip_id" class="scene-title-body">
          <h1 class="scene-country">
            <img v-if="flag && !failedFlags.has(flag)" :src="flag" class="scene-flag" alt="" width="30" height="20" decoding="async"
              @error="failedFlags.add(flag)" />{{ current.event }}
          </h1>
          <p class="scene-date"><span class="tabular">{{ date(current.observed_at) }}</span><span class="scene-source">Sentinel‑1</span></p>
        </div>
      </Transition>
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
