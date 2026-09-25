<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { Check, LoaderCircle, Plus, Satellite, Search, TriangleAlert, X } from '@lucide/vue'

const props = defineProps({ scenes: { type: Array, default: () => [] } })
const emit = defineEmits(['ready'])

const open = ref(false)
const query = ref('')
const catalog = ref(null)
const loadError = ref('')
const selected = ref(null)
const job = ref(null)
let pollTimer

const STEPS = [
  ['download', 'Скачивание S1'],
  ['inference', 'Ансамбль U-Net'],
  ['prepare', 'Объекты и зоны'],
  ['done', 'Готово'],
]
const PART_LABELS = {
  test_core: 'тест', bolivia: 'новое событие', test_adjacent: 'тест, соседний',
  train: 'обучение', valid: 'валидация',
}
const INDEPENDENT = new Set(['test_core', 'bolivia'])

const prepared = computed(() => new Set(props.scenes.map(scene => scene.chip_id)))
const matches = computed(() => {
  const chips = catalog.value?.chips || []
  const words = query.value.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const found = words.length
    ? chips.filter(chip => words.every(word => (chip.chip_id + ' ' + chip.event).toLowerCase().includes(word)))
    : chips.filter(chip => INDEPENDENT.has(chip.split_part))
  return found.slice(0, 8)
})
const running = computed(() => job.value && !['done', 'error'].includes(job.value.stage))
const stepIndex = computed(() => STEPS.findIndex(([stage]) => stage === job.value?.stage))

function partLabel(part) { return PART_LABELS[part] || 'вне списков' }
function seconds(value) { return value == null ? '' : value.toFixed(1).replace('.', ',') + ' с' }
function stepState(index) {
  if (!job.value) return 'pending'
  if (job.value.stage === 'error') return index < Math.max(stepIndex.value, 0) ? 'done' : index === failedStep.value ? 'error' : 'pending'
  if (job.value.stage === 'done') return 'done'
  if (index < stepIndex.value) return 'done'
  return index === stepIndex.value ? 'active' : 'pending'
}
const failedStep = computed(() => {
  const timings = job.value?.timings || {}
  return STEPS.findIndex(([stage]) => stage !== 'done' && timings[stage] == null)
})

async function toggle() {
  open.value = !open.value
  if (open.value && !catalog.value) {
    try {
      const response = await fetch('/api/chips')
      if (!response.ok) throw new Error('API ' + response.status)
      catalog.value = await response.json()
      loadError.value = ''
    } catch {
      loadError.value = 'Каталог чипов недоступен. Проверьте, что API запущен.'
    }
  }
}
function choose(chip) {
  selected.value = chip
  if (!running.value) job.value = null
}
function stopPolling() { clearInterval(pollTimer); pollTimer = undefined }
async function poll(id) {
  try {
    const response = await fetch('/api/jobs/' + id)
    if (!response.ok) throw new Error('API ' + response.status)
    job.value = await response.json()
  } catch {
    job.value = { ...(job.value || {}), stage: 'error', message: 'Связь с API потеряна. Повторите запуск.' }
  }
  if (!running.value) {
    stopPolling()
    if (job.value.stage === 'done') emit('ready', job.value.scene_id)
  }
}
async function run() {
  if (!selected.value || running.value) return
  if (prepared.value.has(selected.value.chip_id)) { emit('ready', selected.value.chip_id); return }
  stopPolling()
  job.value = { stage: 'queued', message: 'Отправляем запрос', timings: {} }
  try {
    const response = await fetch('/api/scenes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chip_id: selected.value.chip_id }),
    })
    if (!response.ok) throw new Error(response.status === 404 ? 'Чип не найден в официальном списке' : 'API ' + response.status)
    job.value = await response.json()
    if (job.value.stage === 'done') { emit('ready', job.value.scene_id); return }
    pollTimer = setInterval(() => poll(job.value.id), 800)
  } catch (cause) {
    job.value = { stage: 'error', message: 'Запуск не принят: ' + cause.message, timings: {} }
  }
}
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="ingest" :class="{ open }">
    <button class="ingest-toggle" :aria-expanded="open" aria-controls="chip-ingest" @click="toggle">
      <component :is="open ? X : Plus" :size="15" />
      {{ open ? 'Свернуть' : 'Рассчитать другой чип Sen1Floods11' }}
    </button>
    <div v-if="open" id="chip-ingest" class="ingest-body">
      <label class="ingest-label" for="chip-search">Официальный чип с ручной меткой</label>
      <div class="ingest-search">
        <Search :size="15" />
        <input id="chip-search" v-model="query" type="search" autocomplete="off" spellcheck="false"
          placeholder="Bolivia_103757 или «Испания»" />
      </div>
      <p v-if="loadError" class="ingest-error"><TriangleAlert :size="14" /> {{ loadError }}</p>
      <p v-else-if="!query && catalog" class="ingest-hint">Независимые чипы (тест и Боливия). Поиск откроет все {{ catalog.chips.length }}.</p>
      <ul v-if="catalog" class="ingest-list" aria-label="Найденные чипы">
        <li v-for="chip in matches" :key="chip.chip_id">
          <button :class="{ selected: selected?.chip_id === chip.chip_id }" :aria-pressed="selected?.chip_id === chip.chip_id" @click="choose(chip)">
            <span class="ingest-id">{{ chip.chip_id }}</span>
            <span class="ingest-meta">{{ chip.event }}, {{ chip.observed_at }}</span>
            <span class="ingest-part" :class="{ risky: !['test_core', 'bolivia'].includes(chip.split_part) }">
              {{ prepared.has(chip.chip_id) ? 'уже на карте' : partLabel(chip.split_part) }}
            </span>
          </button>
        </li>
        <li v-if="!matches.length" class="ingest-empty">Ничего не найдено. Формат: Событие_номер.</li>
      </ul>
      <div v-if="selected" class="ingest-selected">
        <p v-if="!['test_core', 'bolivia'].includes(selected.split_part)" class="ingest-warning">
          <TriangleAlert :size="14" /> Чип из части «{{ partLabel(selected.split_part) }}»: модель его видела или он соседствует с обучением, поэтому качество на нём не независимо.
        </p>
        <button class="primary-button ingest-run" :disabled="running" @click="run">
          <LoaderCircle v-if="running" :size="16" class="spin" /><Satellite v-else :size="16" />
          {{ prepared.has(selected.chip_id) ? 'Открыть ' + selected.chip_id : running ? 'Идёт расчёт' : 'Рассчитать ' + selected.chip_id }}
        </button>
      </div>
      <div class="ingest-progress" aria-live="polite">
        <template v-if="job">
          <ol>
            <li v-for="(step, index) in STEPS" :key="step[0]" :class="stepState(index)">
              <span class="ingest-dot"><Check v-if="stepState(index) === 'done'" :size="11" /><X v-else-if="stepState(index) === 'error'" :size="11" /></span>
              <span>{{ step[1] }}</span>
              <small>{{ seconds(job.timings?.[step[0]]) }}</small>
            </li>
          </ol>
          <p :class="{ 'ingest-error': job.stage === 'error' }">{{ job.message }}</p>
          <button v-if="job.stage === 'error'" class="tonal-button" @click="run">Повторить</button>
        </template>
      </div>
    </div>
  </div>
</template>
