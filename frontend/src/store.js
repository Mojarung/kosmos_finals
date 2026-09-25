import { computed, reactive, watch } from 'vue'

/*
 * Раньше всё состояние жило двадцатью ref в App.vue и расходилось по
 * компонентам пропсами. Панелей стало несколько, поэтому состояние общее.
 */
export const state = reactive({
  analysis: null,
  error: '',
  loading: false,
  sceneId: 'Nigeria_812045',
  region: null,
  drawing: false,
  selectedAssetId: null,
  mustCover: null,
  layerMode: 'model',
  basemap: 'satellite',
  overlayOpacity: 58,
  showRadar: false,
  showZones: false,
  tilted: false,
  theme: 'light',
  budget: 6500,
  deadline: 8,
  priority: 'risk',
  strategy: 'C',
  tab: 'assets',
  observeOpen: true,
  decisionOpen: true,
})

export const hasData = computed(() => ['covered', 'partial_coverage'].includes(state.analysis?.region.data_status))
export const selectedAsset = computed(() => state.analysis?.assets.find(item => item.id === state.selectedAssetId) || null)
export const activePlan = computed(() => state.analysis?.strategies[state.strategy])
export const testQuality = computed(() => state.analysis?.quality.parts.test_core)

let controller
let timer

export function parameters() {
  const params = new URLSearchParams({
    scene_id: state.sceneId, budget: String(state.budget),
    deadline: String(state.deadline), priority: state.priority,
  })
  if (state.region) params.set('bbox', state.region.join(','))
  if (state.mustCover) params.set('must_cover', state.mustCover)
  return params
}

export async function fetchAnalysis() {
  controller?.abort()
  const current = new AbortController()
  controller = current
  state.loading = true
  state.error = ''
  try {
    const response = await fetch('/api/analysis?' + parameters(), { signal: current.signal })
    if (!response.ok) throw new Error('API ' + response.status)
    state.analysis = await response.json()
    if (state.selectedAssetId && !state.analysis.assets.some(a => a.id === state.selectedAssetId)) state.selectedAssetId = null
  } catch (cause) {
    if (cause.name !== 'AbortError') state.error = 'Не удалось загрузить анализ. Проверьте, что API запущен.'
  } finally {
    if (controller === current) state.loading = false
  }
}

watch(() => [state.sceneId, state.region, state.budget, state.deadline, state.priority, state.mustCover], () => {
  clearTimeout(timer)
  timer = setTimeout(fetchAnalysis, 110)
})
watch(() => state.tab, value => { if (value === 'plan') state.showZones = true })

export function chooseScene(id) {
  if (state.sceneId === id) return false
  Object.assign(state, { sceneId: id, region: null, selectedAssetId: null, mustCover: null, layerMode: 'model', drawing: false })
  return true
}
export function chooseRegion(bounds) {
  Object.assign(state, { region: bounds, selectedAssetId: null, mustCover: null })
}
export function resetRegion() {
  Object.assign(state, { region: null, drawing: false, selectedAssetId: null, mustCover: null })
}
export function toggleGuarantee() {
  if (!selectedAsset.value) return
  state.mustCover = state.mustCover === state.selectedAssetId ? null : state.selectedAssetId
  state.tab = 'plan'
}
export function exportDecision() { window.location.href = '/api/export?' + parameters() }
