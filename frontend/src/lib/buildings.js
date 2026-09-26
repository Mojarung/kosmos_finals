import { computed, reactive, watch } from 'vue'
import { chooseScene, hasData, state } from '../store'

const STORAGE = 'floodvalue-building-rates-v1'
function savedRates() {
  try { return JSON.parse(localStorage.getItem(STORAGE)) || {} } catch { return {} }
}
export const buildings = reactive({ data: null, busy: false, exporting: false, error: '', selectedId: null, applied: null, rates: savedRates() })
export const selectedBuilding = computed(() => buildings.data?.features.find(f => f.id === buildings.selectedId) || null)
export const buildingMode = computed(() => state.tab === 'buildings' || ['assets', 'plan'].includes(state.tab) && state.objectMode === 'buildings' && !(state.tab === 'plan' && state.procurementProjectId))
watch(() => state.tab, tab => { if (tab === 'buildings') { state.tab = 'assets'; state.objectMode = 'buildings' } }, { immediate: true })
export const topBuildings = computed(() => {
  const features = buildings.data?.features || []
  return (buildings.data?.top_building_ids || []).map(id => features.find(f => f.id === id)).filter(Boolean)
})
let generation = 0
let controller
let attemptedContext = null
const context = computed(() => state.sceneId + '|' + (state.region || []).join(','))
const pending = computed(() => ['searching', 'preparing', 'error'].includes(state.liveStatus))

function reset() {
  ++generation
  controller?.abort()
  attemptedContext = null
  Object.assign(buildings, { data: null, busy: false, exporting: false, error: '', selectedId: null, applied: null })
}
watch([context, pending], reset, { flush: 'sync' })
// Load once when this scene's radar is ready, including when its object panel is folded.
// A failed source request is retried explicitly, not on every budget change.
watch(() => [context.value, pending.value, state.analysis?.chip.chip_id, state.loading, hasData.value], () => {
  if (!pending.value && !state.loading && hasData.value && state.analysis?.chip.chip_id === state.sceneId
      && !buildings.busy && !buildings.data && attemptedContext !== context.value) loadBuildings()
}, { immediate: true })
// Separating permanent water changes the raster used in the calculation.
watch(() => state.analysis?.chip.permanent_status, (value, old) => {
  if (old !== undefined && value !== old && buildings.applied) run('/evaluate', buildings.applied)
})

async function json(url, options) {
  const response = await fetch('/api/buildings' + url, options)
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Проверьте типы и числовые значения ставок')
  return data
}

async function run(url, body) {
  controller?.abort()
  controller = new AbortController()
  const current = ++generation, scene = state.sceneId
  buildings.busy = true
  buildings.error = ''
  try {
    const data = await json(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal })
    if (current !== generation || scene !== state.sceneId) return
    buildings.data = data
    buildings.applied = { scene_id: scene, catalog_id: data.catalog_id, rate_overrides: body.rate_overrides || {}, type_overrides: data.type_overrides }
    buildings.rates = body.rate_overrides || {}
    try { localStorage.setItem(STORAGE, JSON.stringify(buildings.rates)) } catch { /* private browser storage */ }
    if (!data.features.some(f => f.id === buildings.selectedId)) buildings.selectedId = null
    return data
  } catch (error) {
    if (current === generation && error.name !== 'AbortError') buildings.error = error.message
  } finally {
    if (current === generation) buildings.busy = false
  }
}

export function loadBuildings(source = 'auto') {
  attemptedContext = context.value
  return run('/load', { scene_id: state.sceneId, source, rate_overrides: buildings.rates })
}
export async function openBuildingDemo() {
  buildings.error = ''
  const current = generation
  try {
    const demo = await json('/demo')
    if (current !== generation) return
    if (!demo.scene_id) throw new Error('Пример не установлен на сервере')
    const changed = chooseScene(demo.scene_id)
    state.objectMode = 'buildings'
    state.tab = 'assets'
    if (!changed) return await loadBuildings(demo.source)
  } catch (error) { buildings.error = error.message }
}
export async function importBuildings(file) {
  const scene = state.sceneId, current = generation
  if (file.size > 12_000_000) { buildings.error = 'Файл должен быть меньше 12 МБ'; return }
  const content = await file.text()
  if (scene !== state.sceneId || current !== generation) return
  return run('/import', { scene_id: scene, content, rate_overrides: buildings.rates })
}
export function updateRates(rates) {
  return run('/evaluate', { ...buildings.applied, rate_overrides: rates })
}
export function updateBuildingType(id, type) {
  return run('/evaluate', { ...buildings.applied, type_overrides: { ...buildings.applied.type_overrides, [id]: type } })
}
export async function exportBuildings() {
  if (!buildings.applied || buildings.busy || buildings.exporting) return
  const current = generation
  buildings.exporting = true
  try {
    const response = await fetch('/api/buildings/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildings.applied) })
    if (!response.ok) throw new Error('Не удалось выгрузить расчёт. Повторите.')
    const blob = await response.blob()
    if (current !== generation) return
    const url = URL.createObjectURL(blob), link = document.createElement('a')
    link.href = url; link.download = 'building-damage.zip'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) { if (current === generation) buildings.error = error.message }
  finally { if (current === generation) buildings.exporting = false }
}

export const buildingStatus = {
  ok: 'Сценарная оценка', unknown_type: 'Уточните тип', no_radar: 'Нет данных радара',
  partial_radar: 'Неполное покрытие радаром', overlap: 'Контуры пересекаются — уточните геометрию',
}
