import { computed, reactive, watch } from 'vue'
import { state } from '../store'
import { buildings } from './buildings'

export const buildingPlan = reactive({ scope: 'all', selectedIds: [], visitCost: 10000, result: null, busy: false, error: '', exporting: false })
export const buildingPlanMode = computed(() => state.tab === 'plan' && state.objectMode === 'buildings' && !state.procurementProjectId)
export const buildingTargets = computed(() => (buildings.data?.features || []).filter(f => buildingPlan.scope === 'all' || buildingPlan.selectedIds.includes(f.id)))
export const buildingScope = computed(() => buildings.applied ? { ...buildings.applied, target_ids: buildingPlan.scope === 'all' ? null : [...buildingPlan.selectedIds].sort() } : null)
export const currentBuildingPlan = computed(() => buildingPlan.result?.strategies[state.strategy] || null)
export const buildingPlanZones = computed(() => buildingPlan.result?.zones || [])
export const buildingPlanOverlay = computed(() => {
  if (!buildingPlanMode.value || state.procurementOpen || !state.showZones || buildingPlan.busy) return []
  const chosen = new Set(currentBuildingPlan.value?.zone_ids || [])
  return buildingPlanZones.value.filter(z => z.kind !== 'broad' || chosen.has(z.id)).map(z => ({ type: 'Feature', geometry: z.geometry,
    properties: { id: z.id, kind: z.kind, active: chosen.has(z.id) ? 1 : 0, hover: z.id === state.hoverZoneId ? 1 : 0, building_ids: z.building_ids } }))
})
export function toggleBuildingTarget(id) {
  if (buildingPlan.selectedIds.includes(id)) buildingPlan.selectedIds = buildingPlan.selectedIds.filter(value => value !== id)
  else buildingPlan.selectedIds = [...buildingPlan.selectedIds, id]
}
export function planBuilding(id) {
  if (!buildingPlan.selectedIds.includes(id)) toggleBuildingTarget(id)
  buildingPlan.scope = 'selected'
  state.objectMode = 'buildings'
  state.procurementProjectId = null
  state.procurementOpen = false
  state.strategy = 'C'
  state.tab = 'plan'
  state.decisionOpen = true
}
let generation = 0, controller, timer
watch(() => [state.sceneId, buildings.data?.catalog_id], ([scene, catalogId], [oldScene, oldCatalog] = []) => {
  if (scene !== oldScene || catalogId !== oldCatalog) {
    buildingPlan.selectedIds = []
    buildingPlan.scope = 'all'
  }
})
const body = computed(() => ({ buildings: buildingScope.value, budget_rub: Number(state.budget), deadline_hours: state.deadline,
  method: state.procurementMethod, visit_cost_rub: Number(buildingPlan.visitCost), strategy: state.strategy }))
const key = computed(() => JSON.stringify([buildingScope.value, buildings.data?.raster_sha256, state.budget, state.deadline, state.procurementMethod, buildingPlan.visitCost]))
watch([key, buildingPlanMode, () => buildings.busy], () => {
  ++generation
  controller?.abort()
  clearTimeout(timer)
  buildingPlan.result = null
  buildingPlan.busy = false
  buildingPlan.error = ''
  state.selectedZoneId = null
  if (buildingPlanMode.value && buildingScope.value && !buildings.busy) {
    buildingPlan.busy = true
    timer = setTimeout(loadBuildingPlan, 220)
  }
}, { immediate: true })

export async function loadBuildingPlan() {
  const current = ++generation
  controller?.abort()
  controller = new AbortController()
  buildingPlan.error = ''
  if (!buildingScope.value || !buildingTargets.value.length) {
    buildingPlan.busy = false
    return
  }
  if (buildingPlan.visitCost === '' || !Number.isFinite(Number(buildingPlan.visitCost)) || Number(buildingPlan.visitCost) < 0 || Number(buildingPlan.visitCost) > 10000000) {
    buildingPlan.error = 'Стоимость выезда: от 0 до 10 млн ₽.'
    buildingPlan.busy = false
    return
  }
  buildingPlan.busy = true
  try {
    const response = await fetch('/api/building-plans', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body.value), signal: controller.signal })
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Проверьте параметры расчёта')
    if (current === generation) buildingPlan.result = data
  } catch (error) { if (current === generation && error.name !== 'AbortError') buildingPlan.error = error.message }
  finally { if (current === generation) buildingPlan.busy = false }
}

export async function downloadBuildingPlan() {
  if (!buildingPlan.result || buildingPlan.busy || buildings.busy || buildingPlan.exporting) return
  const current = generation
  buildingPlan.exporting = true
  try {
    const response = await fetch('/api/building-plans/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body.value) })
    if (!response.ok) throw new Error('Не удалось выгрузить план')
    const blob = await response.blob()
    if (current !== generation) return
    const url = URL.createObjectURL(blob), link = document.createElement('a')
    link.href = url; link.download = 'building-imagery-plan.zip'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) { if (current === generation) buildingPlan.error = error.message }
  finally { buildingPlan.exporting = false }
}
