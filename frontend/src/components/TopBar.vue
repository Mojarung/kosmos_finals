<script setup>
import { computed, reactive } from 'vue'
import { Box, Globe2, LocateFixed, Moon, Satellite, Sun } from '@lucide/vue'
import CountryPicker from './CountryPicker.vue'
import { chooseScene, loadLiveItem, state } from '../store'
import { toggleTheme } from '../lib/theme'
import { date } from '../lib/format'
import { eventKey, flagUrl } from '../lib/flags'

const emit = defineEmits(['globe', 'focus-chip', 'tilt'])

const current = computed(() => {
  if (['searching', 'preparing', 'error'].includes(state.liveStatus)) {
    return { chip_id: 'live_pending', event: 'Выбранная область', observed_at: null }
  }
  return state.analysis?.scenes.find(item => item.chip_id === state.sceneId) || state.analysis?.chip
})
// Без сети или при сбое CDN флаг просто не показываем, название остаётся.
const failedFlags = reactive(new Set())
const flag = computed(() => flagUrl(eventKey(current.value?.chip_id)))
// Живой регион (рамка на карте) заменяет переключатель стран выбором даты снимка.
const liveMode = computed(() => state.liveStatus !== 'idle' || state.liveCandidates.length > 0)
const utcTime = value => new Date(value).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })
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
          <p class="scene-date"><span class="tabular">{{ current.observed_at ? date(current.observed_at) : state.liveStatus === 'error' ? 'Нет снимка' : 'Ищем снимок' }}</span><span class="scene-source">Sentinel‑1</span></p>
        </div>
      </Transition>
    </div>

    <div class="topbar-center">
      <div v-if="liveMode" class="panel capsule live-scenes" data-enter>
        <Satellite :size="17" class="accent" />
        <span v-if="!state.liveCandidates.length" class="live-scenes-label">{{ state.liveStatus === 'searching' ? 'Ищем снимки…' : 'Новый регион' }}</span>
        <label v-else class="live-select-label">
          <span>Снимок</span>
          <select :value="state.liveItemId" aria-label="Дата снимка Sentinel-1" @change="loadLiveItem($event.target.value)">
            <option v-for="item in state.liveCandidates" :key="item.id" :value="item.id">{{ date(item.datetime) }} · {{ utcTime(item.datetime) }} UTC · {{ item.platform }}{{ item.item_count > 1 ? ` · ${item.item_count} кадра` : '' }}</option>
          </select>
        </label>
        <button class="live-demo-link" v-tip="'Вернуться к подготовленным снимкам Sen1Floods11'" @click="chooseScene('Nigeria_812045')">Готовые снимки</button>
      </div>
      <CountryPicker v-else @focus-chip="emit('focus-chip')" />
    </div>

    <div class="panel capsule actions" data-enter>
      <button v-tip="'Вернуться к снимку'" class="ghost-icon" aria-label="Показать весь снимок" @click="emit('focus-chip')"><LocateFixed :size="18" /></button>
      <button v-tip="state.globeView ? '3D-обзор доступен, когда карта приближена к территории' : state.tilted ? 'Вернуть вид сверху' : 'Наклонный 3D-обзор территории'"
        class="ghost-icon" :class="{ active: state.tilted }" :aria-pressed="state.tilted" aria-label="3D-обзор"
        :aria-disabled="state.globeView" @click="emit('tilt')"><Box :size="18" /></button>
      <button v-tip="'Вся Земля: где лежат снимки'" class="ghost-icon" aria-label="Показать Землю" @click="emit('globe')"><Globe2 :size="18" /></button>
      <button v-tip="state.theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'" class="ghost-icon" aria-label="Сменить тему" @click="toggleTheme">
        <Sun v-if="state.theme === 'dark'" :size="18" /><Moon v-else :size="18" />
      </button>
    </div>
  </header>
</template>
