<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Flag, Globe2, LoaderCircle, MapPin, Search } from '@lucide/vue'

const emit = defineEmits(['place'])
const open = ref(false)
const query = ref('')
const active = ref(0)
const input = ref(null)
const root = ref(null)
// Страны и города Natural Earth (scripts/build_places.py): грузятся один раз при первом открытии.
const places = ref(null)
// Остальные места ищет OpenStreetMap Nominatim, только по явному запросу: автодополнение через него запрещено правилами сервиса.
const world = ref([])
const worldState = ref('')

const norm = text => text.toLowerCase().replaceAll('ё', 'е').replace(/[\s\-‐'’.,()]+/g, ' ').trim()

async function load() {
  if (places.value) return
  const rows = (await import('../data/places.json')).default
  places.value = rows.map(([ru, en, country, lon, lat, population, bbox]) => ({
    ru, en, country, center: [lon, lat], population, bbox, keys: [norm(ru), norm(en)],
  }))
}

// Точное совпадение выше начала слова, страна выше города при равенстве, дальше по населению.
const local = computed(() => {
  const q = norm(query.value)
  if (!q || !places.value) return []
  const scored = []
  for (const place of places.value) {
    let score = -1
    for (const key of place.keys) {
      if (key === q) score = Math.max(score, 3)
      else if (key.startsWith(q)) score = Math.max(score, 2)
      else if (q.length > 2 && key.includes(' ' + q)) score = Math.max(score, 1)
    }
    if (score >= 0) scored.push([score + (place.bbox ? 0.5 : 0), place.population, place])
  }
  scored.sort((a, b) => b[0] - a[0] || b[1] - a[1])
  return scored.slice(0, 8).map(item => item[2])
})
const items = computed(() => (world.value.length ? world.value : local.value))
watch(query, () => { active.value = 0; world.value = []; worldState.value = '' })

async function searchWorld() {
  const q = query.value.trim()
  if (q.length < 2 || worldState.value === 'busy') return
  worldState.value = 'busy'
  try {
    const params = new URLSearchParams({ q, format: 'jsonv2', limit: '6', 'accept-language': 'ru' })
    const response = await fetch('https://nominatim.openstreetmap.org/search?' + params)
    if (!response.ok) throw new Error('Nominatim ' + response.status)
    world.value = (await response.json()).map(row => {
      const [south, north, west, east] = row.boundingbox.map(Number)
      const parts = row.display_name.split(', ')
      return { ru: row.name || parts[0], country: parts.slice(1).slice(-2).join(', '), center: [Number(row.lon), Number(row.lat)],
        bbox: [west, south, east, north], population: 0, osm: true }
    })
    worldState.value = world.value.length ? '' : 'empty'
    active.value = 0
  } catch {
    worldState.value = 'error'
  }
}

function choose(place) {
  if (!place) return
  emit('place', { center: place.center, bbox: place.bbox || null, city: !place.bbox || place.osm })
  open.value = false
  query.value = ''
}
function submit() {
  if (items.value.length) choose(items.value[active.value])
  else searchWorld()
}
function move(step) {
  const count = items.value.length
  if (count) active.value = (active.value + step + count) % count
}
async function show() {
  open.value = true
  await nextTick()
  input.value?.focus()
  load()
}
function toggle() {
  if (open.value) open.value = false
  else show()
}
function onKey(event) {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable
  if ((event.key === 'k' && (event.ctrlKey || event.metaKey)) || (event.key === '/' && !typing)) {
    event.preventDefault()
    show()
  }
}
function onPointer(event) {
  if (open.value && !root.value?.contains(event.target)) open.value = false
}
onMounted(() => {
  // Список грузится заранее, пока страница простаивает, чтобы подсказки были с первой буквы.
  const idle = window.requestIdleCallback || (callback => setTimeout(callback, 2500))
  idle(() => load(), { timeout: 5000 })
  window.addEventListener('keydown', onKey)
  window.addEventListener('pointerdown', onPointer, { capture: true })
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('pointerdown', onPointer, { capture: true })
})
</script>

<template>
  <div ref="root" class="place-search">
    <button v-tip="open ? '' : 'Найти страну или город (Ctrl+K)'" class="ghost-icon" :class="{ active: open }"
      aria-label="Найти страну или город" :aria-expanded="open" aria-controls="place-menu" @click="toggle">
      <Search :size="18" />
    </button>
    <Transition name="drop">
      <div v-if="open" id="place-menu" class="panel place-menu" role="dialog" aria-label="Поиск места">
        <label class="country-search">
          <Search :size="15" />
          <input ref="input" v-model="query" type="search" autocomplete="off" spellcheck="false"
            placeholder="Страна или город" aria-label="Страна или город" role="combobox" aria-controls="place-list"
            :aria-activedescendant="items.length ? 'place-' + active : undefined"
            @keydown.down.prevent="move(1)" @keydown.up.prevent="move(-1)"
            @keydown.enter.prevent="submit" @keydown.esc.prevent="open = false" />
          <LoaderCircle v-if="!places || worldState === 'busy'" :size="15" class="spin" />
        </label>

        <ul v-if="items.length" id="place-list" class="country-list" role="listbox">
          <li v-for="(place, index) in items" :id="'place-' + index" :key="place.ru + place.center.join()"
            role="option" :aria-selected="index === active">
            <button class="place-row" :class="{ active: index === active }" @pointerenter="active = index" @click="choose(place)">
              <Flag v-if="place.bbox && !place.osm" :size="15" class="place-kind" />
              <MapPin v-else :size="15" class="place-kind" />
              <span class="country-name">{{ place.ru }}</span>
              <span class="place-country">{{ place.bbox && !place.osm ? 'страна' : place.country }}</span>
            </button>
          </li>
        </ul>
        <p v-else-if="worldState === 'empty'" class="country-note">Ничего не нашлось и в OpenStreetMap.</p>
        <p v-else-if="worldState === 'error'" class="country-note warn">OpenStreetMap не ответил. Повторите через несколько секунд.</p>
        <p v-else-if="!query.trim()" class="country-note">Начните вводить название: страны и крупные города находятся сразу, остальное по Enter через OpenStreetMap.</p>

        <button v-if="query.trim().length >= 2 && !world.length && worldState !== 'busy'" class="place-row place-world" @click="searchWorld">
          <Globe2 :size="15" class="place-kind" /><span class="country-name">Искать «{{ query.trim() }}» в OpenStreetMap</span>
        </button>
      </div>
    </Transition>
  </div>
</template>
