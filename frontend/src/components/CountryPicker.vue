<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ChevronDown, LoaderCircle, Plus, Satellite, Search, TriangleAlert } from '@lucide/vue'
import BubbleTabs from './BubbleTabs.vue'
import { catalog, chooseScene, ingest, ingestRunning, loadCatalog, runChip, state } from '../store'
import { eventKey, flagUrl } from '../lib/flags'
import { date, km, monthYear, plural } from '../lib/format'

/*
 * Переключатель стран по центру шапки. Вкладка — страна, а не чип: иначе два
 * посчитанных участка Ганы дали бы две вкладки «Гана». Вкладок не больше QUICK,
 * остальные страны Sen1Floods11 — в списке с поиском.
 *
 * Аналитику не нужно знать про чипы Sen1Floods11. Страна без готового снимка
 * считается по клику: участок выбирается сам, сначала из тех, которых модель
 * не видела при обучении. В интерфейсе это «участок», номер чипа только в подсказке.
 */
const QUICK = 4
const INDEPENDENT = ['test_core', 'bolivia']
// Порядок, в котором берём участки: сначала честная проверка, потом всё остальное.
const PART_ORDER = ['test_core', 'bolivia', 'test_adjacent', 'valid', 'train']
const STAGES = {
  queued: 'Ставим в очередь', download: 'Скачиваем снимок', inference: 'Модель ищет воду',
  prepare: 'Ставим объекты и зоны', done: 'Готово',
}
const emit = defineEmits(['focus-chip'])

const root = ref(null)
const input = ref(null)
const open = ref(false)
const query = ref('')
const expanded = ref(null)
const failedFlags = reactive(new Set())

const scenes = computed(() => state.analysis?.scenes || [])
const currentKey = computed(() => eventKey(state.sceneId))

const preparedByEvent = computed(() => {
  const groups = new Map()
  for (const scene of scenes.value) {
    const key = eventKey(scene.chip_id)
    groups.set(key, [...(groups.get(key) || []), scene])
  }
  return groups
})

// Вкладки: страны с готовыми снимками в порядке каталога сцен; текущая видна всегда.
const quick = computed(() => {
  const keys = [...preparedByEvent.value.keys()]
  const shown = keys.slice(0, QUICK)
  if (currentKey.value && keys.includes(currentKey.value) && !shown.includes(currentKey.value)) shown[QUICK - 1] = currentKey.value
  return shown.map(key => {
    const list = preparedByEvent.value.get(key)
    return {
      value: key,
      label: list[0].event,
      badge: list.length > 1 ? list.length : null,
      hint: `${date(list[0].observed_at)}${list.length > 1 ? ' · ' + plural(list.length, ['участок', 'участка', 'участков']) : ''}`,
    }
  })
})
const hidden = computed(() => (catalog.loaded ? catalog.events.length : preparedByEvent.value.size) - quick.value.length)
const tab = computed({
  get: () => currentKey.value,
  set: key => {
    if (key === currentKey.value) return emit('focus-chip')
    openScene(preparedByEvent.value.get(key)[0].chip_id)
  },
})

// Следующий участок страны для расчёта: не посчитанный, лучший по части выборки.
function nextChip(key) {
  const done = new Set((preparedByEvent.value.get(key) || []).map(scene => scene.chip_id))
  return catalog.chips
    .filter(chip => chip.event_key === key && !done.has(chip.chip_id))
    .sort((a, b) => PART_ORDER.indexOf(a.split_part) - PART_ORDER.indexOf(b.split_part))[0] || null
}
const jobKey = computed(() => (ingest.job && ingest.job.stage !== 'done' ? eventKey(ingest.chipId) : null))

// «ис» находит Испанию, но не Пакистан: совпадение с начала любого слова названия.
const startsWord = (name, text) => name?.toLowerCase().split(/[\s·-]+/).some(word => word.startsWith(text)) || name?.toLowerCase().startsWith(text)
// Без каталога (API недоступен) список строится из готовых сцен.
const countries = computed(() => {
  const events = catalog.loaded ? catalog.events : [...preparedByEvent.value].map(([key, [first]]) =>
    ({ key, label: first.event, location: key, observed_at: first.observed_at }))
  const text = query.value.trim().toLowerCase()
  return events
    .filter(event => !text || [event.label, event.key, event.location].some(name => startsWord(name, text)))
    .map(event => ({ ...event, prepared: preparedByEvent.value.get(event.key) || [], next: nextChip(event.key) }))
    // Готовые страны сверху: между ними переключаются чаще всего.
    .sort((a, b) => (b.prepared.length > 0) - (a.prepared.length > 0) || a.label.localeCompare(b.label, 'ru'))
})

function flag(key) {
  const url = flagUrl(key)
  return url && !failedFlags.has(url) ? url : null
}
function runHint(country) {
  const chip = country.next
  if (!chip) return country.prepared.length ? 'Все участки страны уже посчитаны' : ''
  if (ingestRunning.value && jobKey.value !== country.key) return 'Сначала дождитесь текущего расчёта'
  const honest = INDEPENDENT.includes(chip.split_part)
    ? 'Модель этот участок не видела'
    : 'Модель видела этот участок при обучении: качество на нём может быть завышено'
  return `${country.prepared.length ? 'Рассчитать ещё участок' : 'Рассчитать снимок'} · ${honest} · ${chip.chip_id}`
}

/*
 * Скрытый поиск по номеру чипа для тех, кто знает Sen1Floods11: запрос с цифрой
 * ищет ещё и по chip_id. В подсказке поля про это ни слова, аналитику не мешает.
 */
const chipMatches = computed(() => {
  const text = query.value.trim().toLowerCase()
  if (!/\d/.test(text)) return []
  const ready = new Set(scenes.value.map(scene => scene.chip_id))
  return catalog.chips
    .filter(chip => chip.chip_id.toLowerCase().includes(text))
    .slice(0, 6)
    .map(chip => ({ ...chip, ready: ready.has(chip.chip_id) }))
})
function pickChip(chip) {
  if (chip.ready) return openScene(chip.chip_id)
  if (!ingestRunning.value) runChip(chip.chip_id)
}

function close() { open.value = false }
function openScene(id) {
  close()
  if (!chooseScene(id)) emit('focus-chip')
}
function compute(country) {
  if (!country.next || ingestRunning.value) return
  runChip(country.next.chip_id)
}
// Готовая страна открывается, остальные считаются. Несколько участков раскрываются списком.
function pick(country) {
  if (jobKey.value === country.key) return
  if (!country.prepared.length) return compute(country)
  if (country.prepared.length > 1 && expanded.value !== country.key) { expanded.value = country.key; return }
  const current = country.prepared.find(scene => scene.chip_id === state.sceneId)
  openScene((current || country.prepared[0]).chip_id)
}
function submit() {
  const first = countries.value[0]
  if (first) pick(first)
  else if (chipMatches.value[0]) pickChip(chipMatches.value[0])
}

function onPointerDown(event) { if (!root.value?.contains(event.target)) close() }
function onKey(event) {
  if (event.key !== 'Escape') return
  close()
  root.value?.querySelector('.all-countries')?.focus()
}
watch(open, async value => {
  if (!value) {
    document.removeEventListener('pointerdown', onPointerDown, true)
    document.removeEventListener('keydown', onKey)
    return
  }
  loadCatalog()
  query.value = ''
  expanded.value = null
  document.addEventListener('pointerdown', onPointerDown, true)
  document.addEventListener('keydown', onKey)
  await nextTick()
  input.value?.focus()
})
// Расчёт закончился и снимок открылся сам: список больше не нужен.
watch(() => ingest.job?.stage, stage => { if (stage === 'done') close() })
// Каталог нужен сразу: по нему считается «Ещё N» на кнопке списка.
loadCatalog()
onBeforeUnmount(close)
</script>

<template>
  <div v-if="quick.length" ref="root" class="country-picker" data-enter>
    <div class="panel capsule scenes">
      <BubbleTabs v-model="tab" :options="quick" label="Страна" />
      <button class="all-countries" :class="{ open, busy: ingestRunning }" :aria-expanded="open" aria-controls="country-menu"
        v-tip="open ? '' : ingestRunning ? 'Идёт расчёт снимка' : 'Все страны и расчёт нового снимка'" @click="open = !open">
        <LoaderCircle v-if="ingestRunning" :size="14" class="spin" />
        <span v-if="hidden > 0" class="tabular">Ещё {{ hidden }}</span><span v-else>Все</span>
        <ChevronDown :size="15" class="all-chevron" />
      </button>
    </div>

    <Transition name="drop">
      <div v-if="open" id="country-menu" class="panel country-menu" role="dialog" aria-label="Страны">
        <label class="country-search">
          <Search :size="15" />
          <input ref="input" v-model="query" type="search" autocomplete="off" spellcheck="false"
            placeholder="Страна" aria-label="Найти страну" @keydown.enter.prevent="submit" />
        </label>
        <p v-if="catalog.error" class="country-note warn">{{ catalog.error }}</p>

        <ul class="country-list">
          <li v-for="country in countries" :key="country.key" class="country-row"
            :class="{ current: country.key === currentKey, idle: !country.prepared.length }">
            <div class="country-line">
              <button class="country-main" :aria-disabled="!country.prepared.length && (!country.next || (ingestRunning && jobKey !== country.key))"
                v-tip="country.prepared.length ? '' : runHint(country)" @click="pick(country)">
                <img v-if="flag(country.key)" :src="flag(country.key)" class="country-flag" alt="" width="20" height="14"
                  decoding="async" @error="failedFlags.add(flagUrl(country.key))" />
                <span v-else class="country-flag"></span>
                <span class="country-name">{{ country.label }}</span>
                <span class="country-date tabular">{{ monthYear(country.observed_at) }}</span>

                <span v-if="jobKey === country.key" class="country-state" :class="{ warn: ingest.job.stage === 'error' }">
                  <template v-if="ingest.job.stage === 'error'"><TriangleAlert :size="14" />Ошибка</template>
                  <template v-else><LoaderCircle :size="14" class="spin" />{{ STAGES[ingest.job.stage] || 'Считаем' }}</template>
                </span>
                <span v-else-if="!country.prepared.length" class="country-state action"><Satellite :size="14" />Рассчитать</span>
                <span v-else-if="country.prepared.length > 1" class="country-state">
                  {{ plural(country.prepared.length, ['участок', 'участка', 'участков']) }}<ChevronDown :size="14" class="country-caret" :class="{ up: expanded === country.key }" />
                </span>
              </button>
              <button v-if="country.prepared.length && country.next && jobKey !== country.key" class="ghost-icon tiny country-more"
                :aria-disabled="ingestRunning" :aria-label="'Рассчитать ещё участок: ' + country.label" v-tip="runHint(country)"
                @click="!ingestRunning && compute(country)">
                <Plus :size="15" />
              </button>
            </div>

            <p v-if="jobKey === country.key && ingest.job.stage === 'error'" class="country-note warn">
              <span v-tip="ingest.job.message">Не удалось рассчитать снимок.</span> <button class="link-button" @click="compute(country)">Повторить</button>
            </p>

            <ul v-if="expanded === country.key && country.prepared.length > 1" class="country-scenes" aria-label="Участки">
              <li v-for="(scene, index) in country.prepared" :key="scene.chip_id">
                <button :class="{ selected: scene.chip_id === state.sceneId }" v-tip="scene.chip_id" @click="openScene(scene.chip_id)">
                  <span>Участок {{ index + 1 }}</span>
                  <span class="country-water tabular">вода {{ km(scene.model_water_area_km2) }}</span>
                </button>
              </li>
            </ul>
          </li>
          <li v-if="!countries.length && !chipMatches.length" class="country-note">Ничего не нашлось</li>
        </ul>

        <template v-if="chipMatches.length">
          <span class="caption country-caption">Участки</span>
          <ul class="country-list">
            <li v-for="chip in chipMatches" :key="chip.chip_id" class="country-row" :class="{ current: chip.chip_id === state.sceneId }">
              <button class="country-main" :aria-disabled="!chip.ready && ingestRunning && ingest.chipId !== chip.chip_id"
                @click="pickChip(chip)">
                <img v-if="flag(chip.event_key)" :src="flag(chip.event_key)" class="country-flag" alt="" width="20" height="14" decoding="async" />
                <span v-else class="country-flag"></span>
                <span class="country-name tabular">{{ chip.chip_id }}</span>
                <span class="country-date">{{ chip.event }}</span>
                <span v-if="ingest.chipId === chip.chip_id && ingestRunning" class="country-state"><LoaderCircle :size="14" class="spin" />{{ STAGES[ingest.job.stage] || 'Считаем' }}</span>
                <span v-else-if="!chip.ready" class="country-state action"><Satellite :size="14" />Рассчитать</span>
              </button>
            </li>
          </ul>
        </template>
      </div>
    </Transition>
  </div>
</template>
