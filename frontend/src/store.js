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
  liveCandidates: [],
  liveItemId: null,
  liveStatus: 'idle',
  liveError: '',
  drawing: false,
  selectedAssetId: null,
  mustCover: null,
  layerMode: 'model',
  basemap: 'satellite',
  overlayOpacity: 58,
  showRadar: false,
  showZones: false,
  tilted: false,
  // Камера отдалена до масштаба глобуса: наклон там бессмыслен, кнопка 3D выключена.
  globeView: false,
  theme: 'light',
  budget: 6500,
  deadline: 8,
  priority: 'risk',
  strategy: 'C',
  tab: 'assets',
  observeOpen: true,
  decisionOpen: true,
})

export const hasData = computed(() => !['searching', 'preparing', 'error'].includes(state.liveStatus) &&
  ['covered', 'partial_coverage'].includes(state.analysis?.region.data_status))
export const isLive = computed(() => state.analysis?.chip.source_kind === 'live_sentinel1_rtc')
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
  if (state.liveStatus === 'searching' || state.liveStatus === 'preparing') return
  clearTimeout(timer)
  timer = setTimeout(fetchAnalysis, 110)
})
watch(() => state.tab, value => { if (value === 'plan') state.showZones = true })

export function chooseScene(id) {
  if (state.sceneId === id) return false
  Object.assign(state, { sceneId: id, region: null, selectedAssetId: null, mustCover: null, layerMode: 'model', drawing: false,
    liveCandidates: [], liveItemId: null, liveStatus: 'idle', liveError: '', overlayOpacity: 58 })
  return true
}
let regionRequest = 0
async function apiJson(url, options) {
  const response = await fetch(url, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `API ${response.status}`)
  return data
}
export async function loadLiveItem(itemId) {
  if (!state.region) return
  const request = ++regionRequest
  Object.assign(state, { liveItemId: itemId, liveStatus: 'preparing', liveError: '',
    selectedAssetId: null, mustCover: null, tab: 'assets' })
  try {
    const params = new URLSearchParams({ bbox: state.region.join(','), item_id: itemId })
    const prepared = await apiJson('/api/live/prepare?' + params, { method: 'POST' })
    if (request !== regionRequest) return
    state.sceneId = prepared.scene_id
    await fetchAnalysis()
    if (request === regionRequest) state.liveStatus = state.error ? 'error' : 'ready'
  } catch (error) {
    if (request === regionRequest) Object.assign(state, { liveStatus: 'error', liveError: error.message })
  }
}
export async function chooseRegion(bounds) {
  const request = ++regionRequest
  Object.assign(state, { region: bounds, selectedAssetId: null, mustCover: null, layerMode: 'model', overlayOpacity: 78, liveStatus: 'searching',
    liveError: '', liveCandidates: [], liveItemId: null })
  try {
    const data = await apiJson('/api/live/discover?' + new URLSearchParams({ bbox: bounds.join(',') }))
    if (request !== regionRequest) return
    state.liveCandidates = data.scenes
    if (!data.scenes.length) throw new Error('Для области нет полного снимка VV/VH. Выберите соседний участок или другую рамку.')
    await loadLiveItem(data.scenes[0].id)
  } catch (error) {
    if (request === regionRequest) Object.assign(state, { liveStatus: 'error', liveError: error.message })
  }
}
export function resetRegion() {
  ++regionRequest
  Object.assign(state, { region: null, drawing: false, selectedAssetId: null, mustCover: null,
    liveStatus: state.sceneId.startsWith('live_') ? 'ready' : 'idle', liveError: '' })
}
export function toggleGuarantee() {
  if (!selectedAsset.value) return
  state.mustCover = state.mustCover === state.selectedAssetId ? null : state.selectedAssetId
  state.tab = 'plan'
}
export function exportDecision() { window.location.href = '/api/export?' + parameters() }
