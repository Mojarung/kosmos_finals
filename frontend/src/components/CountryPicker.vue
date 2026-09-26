<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ChevronDown, LoaderCircle, Plus, Satellite, Search, TriangleAlert } from '@lucide/vue'
import BubbleTabs from './BubbleTabs.vue'
import { catalog, chooseScene, ingest, ingestRunning, loadCatalog, runChip, state } from '../store'
import { eventKey, flagUrl } from '../lib/flags'
import { sceneCollection, COLLECTIONS } from '../lib/sceneCollections'
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

const allScenes = computed(() => state.analysis?.scenes || [])
const collection = ref('official')
const remembered = reactive({})
const scenes = computed(() => allScenes.value.filter(scene => sceneCollection(scene) === collection.value))
const sceneKey = scene => sceneCollection(scene) === 'official' ? eventKey(scene.chip_id) : scene.chip_id
const currentKey = computed(() => collection.value === 'official' ? eventKey(state.sceneId) : state.sceneId)
const collectionHelp = computed(() => collection.value === 'official' ? 'Sen1Floods11 · обязательный расчёт по ТЗ' : 'Реальные города · дополнительные примеры. Вода по модели, не подтверждённый ущерб.')
watch(() => state.sceneId, id => {
  const scene = allScenes.value.find(item => item.chip_id === id) || {chip_id:id}
  collection.value = sceneCollection(scene)
  remembered[collection.value] = id
}, {immediate:true})
const collectionModel = computed({get:()=>collection.value, set:value=>{
  collection.value=value;query.value='';expanded.value=null
  const candidates=allScenes.value.filter(scene=>sceneCollection(scene)===value)
  const target=candidates.find(scene=>scene.chip_id===remembered[value]) || candidates[0]
  if(target) openScene(target.chip_id)
}})

const preparedByEvent = computed(() => {
  const groups = new Map()
  for (const scene of scenes.value) {
    const key = sceneKey(scene)
    groups.set(key, [...(groups.get(key) || []), scene])
  }
  return groups
})

// Вкладки: страны с готовыми снимками в порядке каталога сцен; текущая видна всегда.
const quick = computed(() => {
  const keys = [...preparedByEvent.value].filter(([key,list]) => list[0].demo_recommended !== false || key === currentKey.value).map(([key]) => key)
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
const hidden = computed(() => Math.max(0, (collection.value === 'official' && catalog.loaded ? catalog.events.length : preparedByEvent.value.size) - quick.value.length))
const tab = computed({
  get: () => currentKey.value,
  set: key => {
    if (key === currentKey.value) return emit('focus-chip')
    openScene(preparedByEvent.value.get(key)[0].chip_id)
  },
})

// Следующий участок страны для расчёта: не посчитанный, лучший по части выборки.
function nextChip(key) {
  if (collection.value !== 'official') return null
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
  const events = collection.value === 'official' && catalog.loaded ? catalog.events : [...preparedByEvent.value].map(([key, [first]]) =>
    ({ key, label: first.event, location: key, observed_at: first.observed_at }))
  const text = query.value.trim().toLowerCase()
  return events
    .filter(event => !text || [event.label, event.key, event.location].some(name => startsWord(name, text)))
    .map(event => ({ ...event, prepared: preparedByEvent.value.get(event.key) || [], next: nextChip(event.key) }))
    // Готовые страны сверху: между ними переключаются чаще всего.
    .sort((a, b) => (b.prepared.length > 0) - (a.prepared.length > 0) || a.label.localeCompare(b.label, 'ru'))
})

function flag(key) {
  const url = flagUrl(collection.value === 'showcase' ? eventKey(key) : key)
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
  if (collection.value !== 'official' || !/\d/.test(text)) return []
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
  <div v-if="allScenes.length" ref="root" class="country-picker" data-enter>
    <div class="panel capsule scene-collections"><BubbleTabs v-model="collectionModel" :options="COLLECTIONS" stretch label="Набор снимков" /></div>
    <div v-if="quick.length" class="panel capsule scenes">
      <div v-if="collection === 'showcase'" class="demo-scene-tabs" role="tablist" aria-label="Демо-сцена">
        <button v-for="scene in quick" :key="scene.value" type="button" role="tab"
          :aria-selected="tab === scene.value" :class="{ selected: tab === scene.value }"
          v-tip="scene.hint" @click="tab = scene.value">{{ scene.label }}</button>
      </div>
      <BubbleTabs v-else :key="collection" v-model="tab" :options="quick" label="Страна" />
      <button class="all-countries" :class="{ open, busy: ingestRunning }" :aria-expanded="open" aria-controls="country-menu"
        v-tip="open ? '' : ingestRunning ? 'Идёт расчёт снимка' : collection === 'official' ? 'Все страны и расчёт нового снимка' : 'Все подготовленные демо-сцены'" @click="open = !open">
        <LoaderCircle v-if="ingestRunning" :size="14" class="spin" />
        <span v-if="hidden > 0" class="tabular">Ещё {{ hidden }}</span><span v-else>Все</span>
        <ChevronDown :size="15" class="all-chevron" />
      </button>
    </div>

    <Transition name="drop">
      <div v-if="open" id="country-menu" class="panel country-menu" role="dialog" :aria-label="collection === 'official' ? 'Снимки по ТЗ' : 'Наши демо-сцены'">
        <p class="country-note">{{ collectionHelp }}</p>
        <label class="country-search">
          <Search :size="15" />
          <input ref="input" v-model="query" type="search" autocomplete="off" spellcheck="false"
            :placeholder="collection === 'official' ? 'Страна или ID чипа' : 'Название примера'" aria-label="Найти снимок" @keydown.enter.prevent="submit" />
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
                <span class="country-name">{{ country.label }}<small v-if="collection === 'showcase'" class="demo-water">{{ country.prepared[0]?.demo_recommended === false ? 'Архив · мало воды' : 'Вода по модели' }}: {{ km(country.prepared[0]?.model_water_area_km2 || 0) }}</small></span>
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
