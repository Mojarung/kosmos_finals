import { computed, reactive, watch } from 'vue'
import { state, createProject, loadProjects, openProject } from '../store'
import { buildings } from './buildings'

export const buildingPlan = reactive({ baseRate: 788.64, processing: 'default', usage: 'internal', mustCover: null, notice: '', saving: false, scope: 'all', picking: false, selectedIds: [], visitCost: 10000, result: null, busy: false, error: '', exporting: false })
export const buildingPlanMode = computed(() => state.tab === 'plan' && state.objectMode === 'buildings' && !state.procurementProjectId)
export const buildingTargets = computed(() => (buildings.data?.features || []).filter(f => buildingPlan.scope === 'all' || buildingPlan.selectedIds.includes(f.id)))
export const buildingScope = computed(() => buildings.applied ? { ...buildings.applied, target_ids: buildingPlan.scope === 'all' ? null : [...buildingPlan.selectedIds].sort() } : null)
export const currentBuildingPlan = computed(() => buildingPlan.result?.strategies[state.strategy] || null)
export const buildingPlanZones = computed(() => buildingPlan.result?.zones || [])
export const buildingPlanOverlay = computed(() => {
  if (!buildingPlanMode.value || buildingPlan.picking || state.procurementOpen || !state.showZones || buildingPlan.busy) return []
  const chosen = new Set(currentBuildingPlan.value?.zone_ids || [])
  return buildingPlanZones.value.filter(z => chosen.has(z.id) || z.id === state.selectedZoneId).map(z => ({ type: 'Feature', geometry: z.geometry,
    properties: { building_plan: 1, building_count: z.building_count, id: z.id, kind: z.kind, active: chosen.has(z.id) ? 1 : 0, hover: z.id === state.hoverZoneId ? 1 : 0, building_ids: z.building_ids } }))
})
export function toggleBuildingTarget(id) {
  if (buildingPlan.selectedIds.includes(id)) buildingPlan.selectedIds = buildingPlan.selectedIds.filter(value => value !== id)
  else buildingPlan.selectedIds = [...buildingPlan.selectedIds, id]
  if (buildingPlan.mustCover === id && !buildingPlan.selectedIds.includes(id)) buildingPlan.mustCover = null
}
export function startBuildingSelection() {
  if (buildingPlan.mustCover && !buildingPlan.selectedIds.includes(buildingPlan.mustCover)) toggleBuildingTarget(buildingPlan.mustCover)
  buildingPlan.scope = 'selected'
  buildingPlan.picking = true
  buildings.selectedId = null
  state.decisionOpen = false
  state.observeOpen = false
}
export function finishBuildingSelection() {
  buildingPlan.picking = false
  state.decisionOpen = true
}
watch(buildingPlanMode, active => { if (!active) buildingPlan.picking = false })
export function requireBuilding(id) {
  buildingPlan.mustCover = buildingPlan.mustCover === id ? null : id
  if (buildingPlan.scope === 'selected' && !buildingPlan.selectedIds.includes(id)) toggleBuildingTarget(id)
  state.objectMode = 'buildings'
  state.procurementProjectId = null
  state.procurementOpen = false
  state.strategy = 'C'
  state.tab = 'plan'
  state.decisionOpen = true
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
watch(() => [buildingPlan.scope, buildingPlan.selectedIds], () => {
  if (buildingPlan.scope === 'selected' && !buildingPlan.selectedIds.includes(buildingPlan.mustCover)) buildingPlan.mustCover = null
})
let generation = 0, controller, timer
watch(() => [state.sceneId, buildings.data?.catalog_id], ([scene, catalogId], [oldScene, oldCatalog] = []) => {
  if (scene !== oldScene || catalogId !== oldCatalog) {
    buildingPlan.mustCover = null
    buildingPlan.notice = ''
    buildingPlan.selectedIds = []
    buildingPlan.picking = false
    buildingPlan.scope = 'all'
  }
})
export const buildingPlanRequest = computed(() => ({ buildings: buildingScope.value, budget_rub: Number(state.budget), deadline_hours: state.deadline,
  base_rate_rub_km2: Number(buildingPlan.baseRate), processing_level: buildingPlan.processing, usage_type: buildingPlan.usage, priority: state.priority, must_cover: buildingPlan.mustCover, method: state.procurementMethod, visit_cost_rub: Number(buildingPlan.visitCost), strategy: state.strategy }))
const key = computed(() => JSON.stringify([buildingScope.value, buildings.data?.raster_sha256, state.budget, state.deadline, state.procurementMethod, state.strategy, buildingPlan.visitCost, state.priority, buildingPlan.mustCover, buildingPlan.baseRate, buildingPlan.processing, buildingPlan.usage]))
watch([key, buildingPlanMode, () => buildings.busy, () => buildingPlan.picking], () => {
  ++generation
  controller?.abort()
  clearTimeout(timer)
  buildingPlan.result = null
  buildingPlan.busy = false
  buildingPlan.error = ''
  buildingPlan.notice = ''
  state.selectedZoneId = null
  if (buildingPlanMode.value && buildingScope.value && !buildings.busy && !buildingPlan.picking) {
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
  if (buildingPlan.baseRate === '' || !Number.isFinite(Number(buildingPlan.baseRate)) || Number(buildingPlan.baseRate) < 0 || Number(buildingPlan.baseRate) > 1000000) {
    buildingPlan.error = 'Базовая ставка: от 0 до 1 млн ₽/км².'
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
    const response = await fetch('/api/building-plans', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildingPlanRequest.value), signal: controller.signal })
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
    const response = await fetch('/api/building-plans/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildingPlanRequest.value) })
    if (!response.ok) throw new Error('Не удалось выгрузить план')
    const blob = await response.blob()
    if (current !== generation) return
    const url = URL.createObjectURL(blob), link = document.createElement('a')
    link.href = url; link.download = 'building-imagery-plan.zip'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) { if (current === generation) buildingPlan.error = error.message }
  finally { buildingPlan.exporting = false }
}

export async function saveBuildingPlan(projectId) {
  if (!buildingPlan.result || buildingPlan.busy || buildingPlan.saving) return
  const payload = JSON.parse(JSON.stringify(buildingPlanRequest.value))
  buildingPlan.saving = true
  buildingPlan.error = ''
  try {
    if (!projectId) projectId = (await createProject('Здания · ' + state.sceneId)).id
    const response = await fetch(`/api/building-plans/projects/${projectId}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    const result = await response.json()
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Не удалось сохранить план')
    await loadProjects()
    await openProject(projectId)
    buildingPlan.notice = 'План сохранён. Он доступен в разделе «Проекты» вместе с исходными контурами и расчётом.'
  } catch (error) { buildingPlan.error = error.message }
  finally { buildingPlan.saving = false }
}
