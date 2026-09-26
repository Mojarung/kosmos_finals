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
  // Зона под курсором в списке плана: карта подсвечивает её контур.
  hoverZoneId: null,
  mustCover: null,
  layerMode: 'model',
  // Сколько пикселей снимка без сигнала радара: карта рисует их серым, легенда называет.
  radarGaps: 0,
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
export const permanentSeparated = computed(() => ['jrc_waterhand', 'jrc_gsw'].includes(state.analysis?.region.permanent_status))
/*
 * Калибровка p и смысл «шанса ошибки» проверены только на снимках Sen1Floods11 GRD.
 * Для кадра RTC API говорит false: статистику Sen1Floods11 к нему не прикладываем.
 */
export const calibrationChecked = computed(() => state.analysis?.doubt?.calibration_checked_here ?? !isLive.value)
// Порог сомнений (шанс ошибки карты), выбранный на validation: 0,0826.
export const doubtThreshold = computed(() => state.analysis?.doubt?.threshold ?? state.analysis?.quality.doubt_threshold?.map_error_chance_threshold)

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
    if (cause.name !== 'AbortError') state.error = 'Сервер расчёта не отвечает. Проверьте, что он запущен, и повторите.'
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
    // Сразу за водой вычитаем постоянную воду: без неё ущерб считался бы и от реки.
    if (request === regionRequest && state.analysis?.region.permanent_status === 'not_separated') separatePermanent(prepared.scene_id)
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
    if (!data.scenes.length) throw new Error('Для этой рамки нет снимка Sentinel‑1 с обеими поляризациями. Сдвиньте рамку или уменьшите её.')
    await loadLiveItem(data.scenes[0].id)
  } catch (error) {
    if (request === regionRequest) Object.assign(state, { liveStatus: 'error', liveError: error.message })
  }
}
/*
 * Постоянная вода для выбранной рамки из архива JRC Global Surface Water (вода
 * и в 1984, и в 2021 году). Живёт в хранилище, чтобы ход расчёта пережил сворачивание панели.
 */
export const permanentJob = reactive({ sceneId: null, status: 'idle', message: '' })
export async function separatePermanent(sceneId) {
  if (permanentJob.sceneId === sceneId && permanentJob.status === 'running') return
  Object.assign(permanentJob, { sceneId, status: 'running', message: '' })
  try {
    await apiJson('/api/live/permanent?' + new URLSearchParams({ scene_id: sceneId }), { method: 'POST' })
    if (permanentJob.sceneId !== sceneId) return
    permanentJob.status = 'done'
    if (state.sceneId === sceneId) await fetchAnalysis()
  } catch (error) {
    if (permanentJob.sceneId === sceneId) Object.assign(permanentJob, { status: 'error', message: error.message })
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

/*
 * Каталог официальных чипов Sen1Floods11 и живой расчёт одного из них. Живут
 * здесь, а не в компоненте: список стран в шапке закрывается, а расчёт идёт
 * дальше, и при следующем открытии прогресс на месте.
 */
export const catalog = reactive({ events: [], chips: [], loaded: false, error: '' })
let catalogRequest
export function loadCatalog() {
  if (catalog.loaded) return Promise.resolve()
  catalogRequest ??= fetch('/api/chips')
    .then(response => { if (!response.ok) throw new Error('API ' + response.status); return response.json() })
    .then(data => Object.assign(catalog, { events: data.events, chips: data.chips, loaded: true, error: '' }))
    .catch(() => { catalog.error = 'Список стран не загрузился: сервер расчёта не отвечает.' })
    .finally(() => { catalogRequest = undefined })
  return catalogRequest
}

export const ingest = reactive({ chipId: null, job: null })
export const ingestRunning = computed(() => ingest.job != null && !['done', 'error'].includes(ingest.job.stage))
let pollTimer

/** Открыть готовую сцену. Если она уже открыта, перечитать анализ: список сцен мог пополниться. */
export function openScene(id) {
  if (!chooseScene(id)) fetchAnalysis()
}
function stopPolling() { clearInterval(pollTimer); pollTimer = undefined }
function finish() {
  if (ingestRunning.value) return
  stopPolling()
  if (ingest.job.stage === 'done') openScene(ingest.job.scene_id)
}
async function poll(id) {
  try {
    const response = await fetch('/api/jobs/' + id)
    if (!response.ok) throw new Error('API ' + response.status)
    ingest.job = await response.json()
  } catch {
    ingest.job = { ...(ingest.job || {}), stage: 'error', message: 'Связь с сервером потеряна. Запустите расчёт ещё раз.' }
  }
  finish()
}
export async function runChip(chipId) {
  if (ingestRunning.value) return
  stopPolling()
  ingest.chipId = chipId
  ingest.job = { stage: 'queued', message: 'Отправляем запрос', timings: {} }
  try {
    const response = await fetch('/api/scenes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chip_id: chipId }),
    })
    if (!response.ok) throw new Error(response.status === 404 ? 'Такого снимка нет в наборе Sen1Floods11' : 'API ' + response.status)
    ingest.job = await response.json()
    if (ingestRunning.value) pollTimer = setInterval(() => poll(ingest.job.id), 800)
    else finish()
  } catch (cause) {
    ingest.job = { stage: 'error', message: 'Расчёт не запустился: ' + cause.message, timings: {} }
  }
}
