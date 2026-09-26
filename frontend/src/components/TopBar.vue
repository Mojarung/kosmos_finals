<script setup>
import { computed, reactive, ref } from 'vue'
import { Box, Globe2, LocateFixed, Moon, Satellite, Sun, Upload } from '@lucide/vue'
import CountryPicker from './CountryPicker.vue'
import PlaceSearch from './PlaceSearch.vue'
import UploadS1 from './UploadS1.vue'
import { chooseScene, loadLiveItem, state } from '../store'
import { toggleTheme } from '../lib/theme'
import { date } from '../lib/format'
import { eventKey, flagUrl } from '../lib/flags'

const emit = defineEmits(['globe', 'focus-chip', 'tilt', 'place'])
const uploadOpen = ref(false)

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

/*
 * Что именно открыто: идентификатор S1-чипа Sen1Floods11 или кадр Sentinel-1 RTC.
 * У кадра RTC спутник и абсолютная орбита читаются из идентификатора STAC:
 * S1A_IW_GRDH_1SDV_<начало>_<конец>_<орбита>_<datatake>_rtc.
 */
const origin = computed(() => {
  const scene = current.value
  if (!scene || scene.chip_id === 'live_pending') return null
  const chip = state.analysis?.chip?.chip_id === scene.chip_id ? state.analysis.chip : null
  if (chip?.source_kind === 'uploaded_sentinel1_geotiff') {
    return { label: 'Свой GeoTIFF', value: chip.source_filename || scene.chip_id,
      tip: `Загруженный двухканальный Sentinel‑1 GeoTIFF · VV/VH · дата ${date(scene.observed_at)}. Источник и лицензия указаны пользователем, локальная точность не измерена.` }
  }
  if (!scene.chip_id.startsWith('live_') && chip?.source_kind !== 'live_sentinel1_rtc') {
    return { label: 'S1‑чип', value: scene.chip_id,
      tip: `S1‑чип ${scene.chip_id} из Sen1Floods11 v1.1: радар Sentinel‑1 GRD, поляризации VV и VH, снимок ${date(scene.observed_at)}` }
  }
  const item = chip?.source_item_id || ''
  const parts = item.split('_')
  const known = parts.length >= 8 && /^S1[A-D]$/.test(parts[0]) && /^\d{6}$/.test(parts[6])
  const orbit = known ? Number(parts[6]) : null
  const when = chip?.observed_at ? `${date(chip.observed_at)}, ${utcTime(chip.observed_at)} UTC` : 'дата н/д'
  return {
    label: 'RTC‑кадр', value: known ? `${parts[0]}, орбита ${orbit}` : 'Sentinel‑1',
    tip: `Кадр Sentinel‑1 RTC ${item || 'н/д'}. Снят ${when}` + (known ? `, спутник ${parts[0]}, абсолютная орбита ${orbit}` : '') + '. Ручной разметки для него нет',
  }
})
</script>

<template>
  <header class="topbar">
    <!-- Заголовок сцены: страна крупно, дата снимка под ней. Меняется сразу по клику,
         не дожидаясь расчёта, и прокручивается как табло. -->
    <div v-if="current" class="panel scene-title" data-enter
      v-tip="origin?.tip || 'Радар Sentinel‑1, поляризации VV и VH'">
      <Transition name="roll" mode="out-in">
        <div :key="current.chip_id" class="scene-title-body">
          <h1 class="scene-country">
            <img v-if="flag && !failedFlags.has(flag)" :src="flag" class="scene-flag" alt="" width="30" height="20" decoding="async"
              @error="failedFlags.add(flag)" />{{ current.event }}
          </h1>
          <p class="scene-date"><span class="tabular">{{ current.observed_at ? date(current.observed_at) : state.liveStatus === 'error' ? 'Нет снимка' : 'Ищем снимок' }}</span><span
              v-if="origin" class="scene-source"><span class="scene-kind">{{ origin.label }}</span> <span class="scene-chip tabular">{{ origin.value }}</span></span><span
              v-else class="scene-source">Sentinel‑1</span><span v-if="state.analysis?.chip?.chip_id === current.chip_id && state.analysis.chip.timing?.analysis_mode === 'retrospective_dataset_demo'"
              class="scene-timing" v-tip="state.analysis.chip.timing.availability_note">Ретроспектива</span></p>
        </div>
      </Transition>
    </div>

    <div class="topbar-center">
      <div v-if="liveMode" class="panel capsule live-scenes" data-enter>
        <Satellite :size="17" class="accent" />
        <span v-if="!state.liveCandidates.length" class="live-scenes-label">{{ state.liveStatus === 'searching' ? 'Ищем снимки…' : state.analysis?.chip.source_kind === 'uploaded_sentinel1_geotiff' ? 'Загруженный снимок' : 'Новый регион' }}</span>
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
      <PlaceSearch @place="emit('place', $event)" />
      <button v-tip="'Загрузить свой двухканальный Sentinel‑1 GeoTIFF'" class="ghost-icon"
        aria-label="Загрузить снимок Sentinel-1" @click="uploadOpen = true"><Upload :size="18" /></button>
      <button v-tip="'Вернуться к снимку'" class="ghost-icon" aria-label="Показать весь снимок" @click="emit('focus-chip')"><LocateFixed :size="18" /></button>
      <button v-tip="state.globeView ? '3D-обзор доступен, когда карта приближена к территории' : state.tilted ? 'Вернуть вид сверху' : 'Наклонный 3D-обзор территории'"
        class="ghost-icon" :class="{ active: state.tilted }" :aria-pressed="state.tilted" aria-label="3D-обзор"
        :aria-disabled="state.globeView" @click="emit('tilt')"><Box :size="18" /></button>
      <button v-tip="'Вся Земля: где лежат снимки'" class="ghost-icon" aria-label="Показать Землю" @click="emit('globe')"><Globe2 :size="18" /></button>
      <button v-tip="state.theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'" class="ghost-icon" aria-label="Сменить тему" @click="toggleTheme">
        <Sun v-if="state.theme === 'dark'" :size="18" /><Moon v-else :size="18" />
      </button>
    </div>
    <UploadS1 v-if="uploadOpen" @close="uploadOpen = false" />
  </header>
</template>
