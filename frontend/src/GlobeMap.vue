<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as maplibregl from 'maplibre-gl'
import { ScanSearch, X } from '@lucide/vue'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import { calibrationChecked, state } from './store'
import { buildingMode, buildings } from './lib/buildings'
import { compactRub, number, percent } from './lib/format'
import CalibrationBadge from './components/CalibrationBadge.vue'
import { waterField } from './lib/water'
import { WaterLayer } from './lib/waterLayer'
import { bindTip } from './lib/tip'
import MorphLoader from './components/MorphLoader.vue'
import { preloadGlobe, preloadJourney, preloadScene, preloadTilt } from './lib/prefetch'

maplibregl.setWorkerUrl(workerUrl)

const emit = defineEmits(['region', 'asset', 'building', 'draw-complete', 'ready'])
const container = ref(null)
const dragging = ref(false)
const mapError = ref(false)
const mapUnavailable = ref(false)
const probe = ref(null)
const probeLoading = ref(false)
const veiled = ref(false)
const veilLoader = ref(false)
const journey = ref(null)
let map
let ready = false
let loadedScene = ''
let buildingCameraScene = ''
// Вода перерисовывается и без смены сцены: когда для рамки нашлась постоянная вода.
let loadedWater = ''
// Стиль перезагружен сменой темы: снимки кладём заново, а камеру не трогаем.
let keepCamera = false
let dragStart = null
let dragEnd = null
// Во время маршрута старый снимок скрыт; 'in' означает, что новый кадр собирается перед раскрытием.
let phase = null
let flight = 0
let waterLoad = Promise.resolve(true)
let tileErrorSeen = false
let styleTimer
let fallbackStyle = false
const markers = new Map()
const empty = { type: 'FeatureCollection', features: [] }
// Маску воды рисует WebGL-слой воды, остальные слои приходят готовыми PNG.
const rasterNames = ['radar', 'probability', 'baseline', 'reference', 'uncertainty']
const rasterFile = { radar: 'radar', probability: 'probability', baseline: 'baseline', reference: 'reference', uncertainty: 'doubt' }
const satelliteTiles = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
const water = new WaterLayer('water-layer', { onError: error => console.warn(error) })
// Прозрачный пиксель: слой «нет сигнала» пуст, пока поле воды для снимка не готово.
const EMPTY_IMAGE = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='

// Цвета оверлеев в тон интерфейсу: объекты и их зоны янтарные, как риск, неуверенность фиолетовая.
const COLORS = {
  light: { chip: '#3d7686', object: '#c98049', doubt: '#7a5bd0', broad: '#3d7686', region: '#e3b26f', regionLine: '#c98049' },
  dark: { chip: '#a6d3df', object: '#efc68e', doubt: '#b9a2f0', broad: '#a6d3df', region: '#efc68e', regionLine: '#f4d3a4' },
}

function rasterLook(name) {
  const dark = state.theme === 'dark'
  return {
    'raster-resampling': name === 'uncertainty' ? 'nearest' : 'linear',
    'raster-brightness-min': name === 'uncertainty' ? 0 : dark ? 0.18 : 0.3,
    ...(name === 'probability' ? { 'raster-hue-rotate': -8, 'raster-saturation': -0.1 } : {}),
  }
}
const theme = () => (state.theme === 'dark' ? 'dark' : 'light')
const layerUrl = (chipId, name) => '/api/demo/' + chipId + '/' + rasterFile[name] + '.png'

let waterToken = 0
async function loadWater(chip, reveal) {
  const token = ++waterToken
  water.clear()
  setNoData(null, chip.bbox)
  try {
    const permanent = ['jrc_waterhand', 'jrc_gsw'].includes(chip.permanent_status) ? '/api/demo/' + chip.chip_id + '/permanent.png' : null
    const field = await waterField('/api/demo/' + chip.chip_id + '/model.png', permanent, layerUrl(chip.chip_id, 'radar'))
    if (token !== waterToken) return false
    const [w, s, e, n] = chip.bbox
    const meters = (e - w) * 111320 * Math.cos(((s + n) / 2) * Math.PI / 180) / field.width
    water.setScene(field, chip.bbox, meters, { reveal })
    setNoData(field.noData, chip.bbox)
    return true
  } catch (error) {
    console.warn('Water field:', error)
    return false
  }
}
/*
 * Пиксели без сигнала радара закрашены серым поверх данных: иначе сквозь них видна
 * подложка, и шов на стыке кадров Sentinel-1 выглядел как дорога поверх паводка.
 */
function setNoData(noData, bbox) {
  state.radarGaps = noData?.count || 0
  if (!ready) return
  map.getSource('nodata')?.updateImage({ url: noData?.url || EMPTY_IMAGE, coordinates: coordinates(bbox) })
  syncNoData()
}
function syncNoData() {
  if (!ready || !map.getLayer('nodata-layer')) return
  const pending = ['searching', 'preparing', 'error'].includes(state.liveStatus)
  const shown = state.radarGaps > 0 && state.layerMode !== 'none' && !pending
  map.setLayoutProperty('nodata-layer', 'visibility', shown ? 'visible' : 'none')
  map.setPaintProperty('nodata-layer', 'raster-opacity', phase !== null ? 0 : state.overlayOpacity / 100)
}
function polygon(bounds) {
  const [w, s, e, n] = bounds
  return { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] }
}
function collection(features) { return { type: 'FeatureCollection', features } }
function coordinates(bounds) {
  const [w, s, e, n] = bounds
  return [[w, n], [e, n], [e, s], [w, s]]
}
function styleUrl() { return 'https://tiles.openfreemap.org/styles/' + (state.theme === 'dark' ? 'dark' : 'positron') }
function installFallbackStyle() {
  if (!map || ready || fallbackStyle) return
  fallbackStyle = true
  clearTimeout(styleTimer)
  map.setStyle({ version: 8, sources: {}, layers: [
    { id: 'offline-background', type: 'background', paint: { 'background-color': state.theme === 'dark' ? '#172528' : '#dce7e8' } },
  ] }, { diff: false })
}
function armStyleFallback() {
  clearTimeout(styleTimer)
  styleTimer = setTimeout(installFallbackStyle, 8000)
}
/*
 * Данные кладём под подписи, но над всеми линиями подложки. В тёмном стиле
 * первые подписи (названия морей) идут раньше дорог и границ, и вставка перед
 * ними оставила бы чёрные границы стран поверх спутника и воды.
 */
function beforeLabels() {
  const layers = map.getStyle().layers
  const lastShape = layers.findLastIndex(layer => layer.type !== 'symbol')
  return layers.slice(lastShape + 1).find(layer => layer.type === 'symbol')?.id
}
function setData(id, data) { map.getSource(id)?.setData(data) }

function buildOverlays() {
  if (!map) return
  const c = COLORS[theme()]
  const before = beforeLabels()
  for (const id of ['chip', 'region', 'preview', 'zones', 'focused-zone', 'probe', 'journey', 'buildings', 'selected-building']) map.addSource(id, { type: 'geojson', data: empty })
  map.addSource('satellite', {
    type: 'raster', tiles: [satelliteTiles], tileSize: 256,
    attribution: 'Imagery © Esri, Maxar, Earthstar Geographics and the GIS User Community',
  })
  map.addLayer({ id: 'satellite-layer', type: 'raster', source: 'satellite',
    layout: { visibility: state.basemap === 'satellite' ? 'visible' : 'none' },
    paint: { 'raster-fade-duration': 180 } }, before)
  const chip = state.analysis?.chip
  const chipId = chip?.chip_id || 'Nigeria_812045'
  const imageCoords = coordinates(chip?.bbox || [5.795, 9.06, 5.841, 9.107])
  for (const name of rasterNames) {
    map.addSource(name, { type: 'image', url: layerUrl(chipId, name), coordinates: imageCoords })
    map.addLayer({ id: name + '-layer', type: 'raster', source: name,
      paint: { 'raster-opacity': name === 'radar' ? 0.7 : state.overlayOpacity / 100,
        'raster-opacity-transition': { duration: 350 }, 'raster-fade-duration': 250,
        ...(name === 'radar' ? {} : rasterLook(name)) } }, before)
  }
  water.set({ theme: theme() })
  map.addLayer(water, before)
  const buildingColor = ['case', ['==', ['get', 'water_probability_proxy'], null], '#899199', ['>', ['coalesce', ['get', 'water_fraction'], 0], 0], '#d57442', '#398b81']
  map.addLayer({ id: 'building-fill', type: 'fill-extrusion', source: 'buildings', paint: {
    'fill-extrusion-color': buildingColor, 'fill-extrusion-opacity': .78,
    'fill-extrusion-height': ['coalesce', ['get', 'height_m'], 6], 'fill-extrusion-base': 0,
    'fill-extrusion-vertical-gradient': true,
  } }, before)
  map.addLayer({ id: 'building-outline', type: 'line', source: 'buildings', paint: {
    'line-color': buildingColor, 'line-width': ['case', ['<=', ['coalesce', ['get', 'risk_rank'], 999999], 10], 2.5, .7],
  } }, before)
  map.addLayer({ id: 'building-selected', type: 'fill-extrusion', source: 'selected-building', paint: {
    'fill-extrusion-color': '#ffbd5b', 'fill-extrusion-opacity': .95,
    'fill-extrusion-height': ['+', ['coalesce', ['get', 'height_m'], 6], .15],
    'fill-extrusion-vertical-gradient': true,
  } }, before)
  map.addSource('nodata', { type: 'image', url: EMPTY_IMAGE, coordinates: imageCoords })
  map.addLayer({ id: 'nodata-layer', type: 'raster', source: 'nodata', layout: { visibility: 'none' },
    paint: { 'raster-resampling': 'nearest', 'raster-fade-duration': 0, 'raster-opacity-transition': { duration: 350 } } }, before)
  map.addLayer({ id: 'chip-outline', type: 'line', source: 'chip',
    paint: { 'line-color': c.chip, 'line-width': 2, 'line-opacity': 0.9 } }, before)
  const zoneColor = ['match', ['get', 'kind'], 'uncertainty', c.doubt, 'broad', c.broad, c.object]
  map.addLayer({ id: 'zone-fill', type: 'fill', source: 'zones',
    paint: { 'fill-color': zoneColor,
      'fill-opacity': ['case', ['==', ['get', 'kind'], 'broad'], ['case', ['==', ['get', 'active'], 1], 0.06, 0],
        ['==', ['get', 'hover'], 1], 0.34, ['==', ['get', 'active'], 1], 0.2, 0.03] } }, before)
  map.addLayer({ id: 'zone-outline', type: 'line', source: 'zones',
    paint: { 'line-color': zoneColor,
      'line-width': ['case', ['==', ['get', 'hover'], 1], 3.2, ['==', ['get', 'active'], 1], 2.2, 1],
      'line-opacity': ['case', ['==', ['get', 'active'], 1], 1, 0.55],
      'line-dasharray': [2, 1.3] } }, before)
  map.addLayer({ id: 'focused-zone-halo', type: 'line', source: 'focused-zone',
    paint: { 'line-color': c.object, 'line-width': 11, 'line-opacity': .18 } }, before)
  map.addLayer({ id: 'focused-zone-outline', type: 'line', source: 'focused-zone',
    paint: { 'line-color': c.object, 'line-width': 3, 'line-opacity': .95 } }, before)
  map.addLayer({ id: 'region-fill', type: 'fill', source: 'region', paint: { 'fill-color': c.region, 'fill-opacity': 0.12 } }, before)
  map.addLayer({ id: 'region-outline', type: 'line', source: 'region', paint: { 'line-color': c.regionLine, 'line-width': 2.5 } }, before)
  map.addLayer({ id: 'preview-fill', type: 'fill', source: 'preview', paint: { 'fill-color': c.region, 'fill-opacity': 0.1 } }, before)
  map.addLayer({ id: 'preview-line', type: 'line', source: 'preview',
    paint: { 'line-color': c.regionLine, 'line-width': 2, 'line-dasharray': [1.5, 1.5] } }, before)
  map.addLayer({ id: 'probe-halo', type: 'circle', source: 'probe',
    paint: { 'circle-radius': 14, 'circle-color': c.chip, 'circle-opacity': 0.22 } }, before)
  map.addLayer({ id: 'probe-point', type: 'circle', source: 'probe',
    paint: { 'circle-radius': 5, 'circle-color': c.chip, 'circle-stroke-width': 2, 'circle-stroke-color': '#ffffff' } }, before)
  map.addLayer({ id: 'journey-points', type: 'circle', source: 'journey', filter: ['==', ['geometry-type'], 'Point'],
    paint: { 'circle-radius': ['case', ['==', ['get', 'end'], 1], 8, 5], 'circle-color': c.chip,
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2.5 } }, before)
  if (probe.value?.longitude != null) setProbePoint(probe.value.longitude, probe.value.latitude)
  // Проекция всегда глобус: на крупном масштабе MapLibre сам рисует его плоским,
  // а при отдалении колесом карта плавно сворачивается в шар.
  map.setProjection({ type: 'globe' })
  ready = true
  syncMap()
  emit('ready')
}

function markerTip(asset, rank) {
  const where = asset.p_flood == null ? 'нет данных радара'
    : `${asset.permanent_water_at_point ? 'постоянная вода' : 'вода'} ${percent(asset.p_flood)} · ущерб ${compactRub(asset.expected_loss_rub)}`
  return `${rank}. ${asset.name}: ${where}`
}

function updateMarkers() {
  if (!map || !state.analysis) return
  const currentIds = new Set()
  const ranks = new Map(state.analysis.assets.map((asset, index) => [asset.id, index + 1]))
  const coveredIds = state.tab === 'plan' && !state.procurementOpen && state.procurementOverlay !== null
    ? new Set(state.procurementOverlay.filter(zone => zone.properties.active).flatMap(zone => zone.properties.asset_ids || []))
    : new Set(state.analysis.strategies[state.strategy]?.covered_asset_ids || [])
  for (const asset of buildingMode.value ? [] : state.analysis.assets) {
    currentIds.add(asset.id)
    let entry = markers.get(asset.id)
    if (!entry) {
      // MapLibre двигает маркер через transform самого элемента. Переход на transform у кнопки
      // (увеличение при наведении) тогда тянул бы кружок за картой с задержкой, поэтому
      // позицию держит голая обёртка, а оформление и анимации живут на кнопке внутри.
      const pin = document.createElement('div')
      pin.className = 'asset-pin'
      const element = document.createElement('button')
      element.type = 'button'
      element.className = 'asset-marker'
      element.addEventListener('click', event => { event.stopPropagation(); emit('asset', asset.id) })
      pin.append(element)
      const marker = new maplibregl.Marker({ element: pin, anchor: 'center' }).setLngLat([asset.longitude, asset.latitude]).addTo(map)
      entry = { marker, element, untip: null }
      markers.set(asset.id, entry)
    }
    // На маркере место в рейтинге ущерба, а не служебный ID: сразу видно, куда смотреть первым.
    const rank = ranks.get(asset.id)
    entry.element.textContent = String(rank)
    entry.marker.setLngLat([asset.longitude, asset.latitude])
    entry.element.classList.toggle('selected', asset.id === state.selectedAssetId)
    entry.element.classList.toggle('high-risk', asset.loss_status === 'ok' && asset.p_flood >= state.analysis.chip.model_threshold)
    entry.element.classList.toggle('covered', coveredIds.has(asset.id) && state.showZones)
    const text = markerTip(asset, rank)
    entry.element.setAttribute('aria-label', text)
    entry.untip?.()
    entry.untip = bindTip(entry.element, text)
  }
  for (const [id, entry] of markers) {
    if (!currentIds.has(id)) { entry.untip?.(); entry.marker.remove(); markers.delete(id) }
  }
}

function zoneFeatures() {
  if (buildingMode.value) return []
  if (state.tab === 'plan' && state.procurementOverlay !== null) return state.procurementOverlay
  if (state.tab === 'projects' && state.projectOverlay !== null) return state.projectOverlay
  if (!state.showZones) return []
  const plan = state.analysis.strategies[state.strategy]
  const active = new Set(plan?.zone_ids || [])
  // Широкая зона видна только у стратегии B: на остальных она закрыла бы весь снимок.
  return state.analysis.zones
    .filter(zone => zone.kind !== 'broad' || active.has(zone.id))
    .map(zone => ({ type: 'Feature', geometry: zone.geometry,
      properties: { id: zone.id, kind: zone.kind, active: active.has(zone.id) ? 1 : 0, hover: state.hoverZoneId === zone.id ? 1 : 0 } }))
}

function syncMap() {
  if (!ready || !state.analysis) return
  syncBuildings()
  if (['searching', 'preparing', 'error'].includes(state.liveStatus)) {
    for (const name of rasterNames) map.setLayoutProperty(name + '-layer', 'visibility', 'none')
    water.set({ visible: false })
    syncNoData()
    setData('chip', empty)
    setData('zones', empty)
    setData('region', state.region ? collection([{ type: 'Feature', geometry: polygon(state.region), properties: {} }]) : empty)
    for (const entry of markers.values()) { entry.untip?.(); entry.marker.remove() }
    markers.clear()
    return
  }
  const chip = state.analysis.chip
  // Пока камера отъезжает от старой сцены, снимки не меняем: иначе новый квадрат
  // появился бы на другом конце карты, а старое место опустело бы посреди полёта.
  if (loadedScene !== chip.chip_id && phase !== 'out') {
    const imageCoords = coordinates(chip.bbox)
    for (const name of rasterNames) map.getSource(name).updateImage({ url: layerUrl(chip.chip_id, name), coordinates: imageCoords })
    const first = !loadedScene
    // Вода разливается при каждой новой сцене; после смены темы она уже была на экране.
    waterLoad = loadWater(chip, !keepCamera)
    loadedWater = chip.chip_id + '|' + chip.permanent_status
    if (loadedScene && loadedScene !== chip.chip_id) clearProbe()
    loadedScene = chip.chip_id
    if (!phase && !keepCamera && (first || !state.region)) focusChip()
    keepCamera = false
  }
  if (phase !== 'out' && loadedWater !== chip.chip_id + '|' + chip.permanent_status) {
    loadedWater = chip.chip_id + '|' + chip.permanent_status
    waterLoad = loadWater(chip, false)
  }
  const hidden = phase !== null
  map.setLayoutProperty('radar-layer', 'visibility', state.showRadar ? 'visible' : 'none')
  map.setPaintProperty('radar-layer', 'raster-opacity', hidden ? 0 : 0.7)
  map.setLayoutProperty('satellite-layer', 'visibility', state.basemap === 'satellite' ? 'visible' : 'none')
  for (const name of rasterNames.filter(name => name !== 'radar')) {
    map.setLayoutProperty(name + '-layer', 'visibility', state.layerMode === name ? 'visible' : 'none')
    map.setPaintProperty(name + '-layer', 'raster-opacity', hidden ? 0 : state.overlayOpacity / 100)
  }
  water.set({ visible: state.layerMode === 'model' && !hidden, opacity: state.overlayOpacity / 100 })
  syncNoData()
  map.setPaintProperty('chip-outline', 'line-opacity', hidden ? 0 : 0.9)
  setData('chip', hidden ? empty : collection([{ type: 'Feature', geometry: polygon(chip.bbox), properties: {} }]))
  setData('region', hidden || !state.region ? empty : collection([{ type: 'Feature', geometry: polygon(state.region), properties: {} }]))
  setData('zones', hidden ? empty : collection(zoneFeatures()))
  const focused = state.analysis.zones.find(zone => zone.id === state.selectedZoneId)
  setData('focused-zone', hidden || !focused ? empty : collection([{ type: 'Feature', geometry: focused.geometry, properties: {} }]))
  if (!hidden) updateMarkers()
  else for (const entry of markers.values()) entry.element.style.visibility = 'hidden'
  if (!hidden) for (const entry of markers.values()) entry.element.style.visibility = ''
}

function syncBuildings() {
  if (!ready) return
  const visible = buildingMode.value && buildings.data?.scene_id === state.sceneId && !['searching', 'preparing', 'error'].includes(state.liveStatus)
  const features = visible ? buildings.data.features : []
  setData('buildings', collection(features))
  setData('selected-building', collection(features.filter(f => f.id === buildings.selectedId)))
  if (visible && features.length && !phase && !state.globeView && buildingCameraScene !== state.sceneId) {
    buildingCameraScene = state.sceneId
    state.tilted = true
    map.easeTo({ pitch: 55, duration: reducedMotion() ? 0 : 400 })
  }
}
function focusBuilding(feature) {
  if (!map || !feature) return
  clearProbe()
  const positions = feature.geometry.coordinates.flat(feature.geometry.type === 'MultiPolygon' ? 2 : 1)
  const xs = positions.map(p => p[0]), ys = positions.map(p => p[1])
  map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], { padding: padding(), maxZoom: 18, pitch: state.tilted ? 55 : 0, duration: reducedMotion() ? 0 : 700 })
}

// Отступы учитывают, открыты ли панели: свёрнутая панель отдаёт место снимку.
function padding() {
  const width = window.innerWidth
  if (width < 1024) return { left: 24, right: 24, top: 140, bottom: 90 }
  return {
    left: state.observeOpen ? 420 : 60,
    right: state.decisionOpen ? 440 : 60,
    top: 100, bottom: 80,
  }
}
function fitChip(duration) {
  const [w, s, e, n] = state.analysis.chip.bbox
  map.fitBounds([[w, s], [e, n]], { padding: padding(), maxZoom: 14.2, pitch: state.tilted ? 55 : 0, bearing: 0, duration, essential: false })
}
function focusChip() {
  if (!map || !state.analysis) return
  // С глобуса камера пролетела бы 12 зумов, и тайлы каждого проступали бы кусками: только через шторку.
  if (state.globeView) return veiledMove(() => fitChip(0))
  fitChip(reducedMotion() ? 0 : 1050)
}
function showGlobe() {
  if (!map || state.globeView) return
  state.tilted = false
  veiledMove(() => {
    map.jumpTo({ center: [13, 11], zoom: 1.8, pitch: 0, bearing: 0 })
  }, { from: 'far', limit: 1500 })
}
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
/*
 * Ждём только тайлы. Событие idle ждёт ещё расстановку и проявление подписей и
 * все переходы карты — на глобусе это добавляло заметную паузу, а для «кадр
 * без дыр» хватает загруженных тайлов. Потолок — чтобы медленная сеть не
 * держала экран закрытым.
 */
async function tilesReady(limit) {
  const deadline = performance.now() + limit
  // Кадр после прыжка: MapLibre должен успеть запросить тайлы нового вида.
  await wait(32)
  while (!map.areTilesLoaded() && performance.now() < deadline) await wait(50)
  return map.areTilesLoaded()
}
function analysisFor(id) {
  if (state.analysis?.chip.chip_id === id) return Promise.resolve(true)
  return new Promise(resolve => {
    const timer = setTimeout(() => finish(false), 12000)
    const stop = watch(() => [state.analysis?.chip.chip_id, state.sceneId, state.error], ([loaded, selected, error]) => {
      if (loaded === id) finish(true)
      else if (selected !== id || error) finish(false)
    })
    function finish(ok) { clearTimeout(timer); stop(); resolve(ok) }
  })
}

function sceneCenter(scene) {
  const [w, s, e, n] = scene.bbox
  return [(w + e) / 2, (s + n) / 2]
}
function journeyFeatures(from, to) {
  return collection([
    { type: 'Feature', geometry: { type: 'Point', coordinates: from }, properties: { end: 0 } },
    { type: 'Feature', geometry: { type: 'Point', coordinates: to }, properties: { end: 1 } },
  ])
}
async function moveCamera(center, zoom, duration, easing = t => t) {
  if (reducedMotion()) { map.jumpTo({ center, zoom, pitch: 0, bearing: 0 }); return }
  await new Promise(resolve => {
    const timer = setTimeout(done, duration + 300)
    function done() { clearTimeout(timer); map.off('moveend', done); resolve() }
    map.once('moveend', done)
    map.easeTo({ center, zoom, pitch: 0, bearing: 0, duration, easing, essential: false })
  })
}
async function orbitGlobe(from, to, zoom, token) {
  if (reducedMotion()) return moveCamera(to, zoom, 0)
  // MapLibre выбирает кратчайшую дугу между центрами. Промежуточные точки
  // меньше 90° заставляют глобус пройти через дальнюю сторону даже для
  // соседних стран, сохраняя непрерывное направление вращения.
  const shortArc = ((to[0] - from[0] + 540) % 360) - 180
  const longArc = shortArc >= 0 ? shortArc - 360 : shortArc + 360
  const steps = Math.ceil(Math.abs(longArc) / 65)
  for (let step = 1; step <= steps && token === flight; step++) {
    const progress = step / steps
    const center = [from[0] + longArc * progress, from[1] + (to[1] - from[1]) * progress]
    await moveCamera(center, zoom, 2300 / steps, step === steps ? t => t * (2 - t) : t => t)
  }
}
async function sceneFrameReady(token, limit = 8000) {
  const deadline = performance.now() + limit
  const waterReady = state.layerMode === 'model'
    ? await Promise.race([waterLoad, wait(limit).then(() => false)]) : true
  if (token !== flight || !waterReady) return false
  await wait(32)
  while (token === flight && performance.now() < deadline) {
    const images = [state.showRadar && 'radar', state.layerMode !== 'model' && state.layerMode !== 'none' && state.layerMode]
      .filter(Boolean)
    if (tileErrorSeen) return false
    if (map.areTilesLoaded() && images.every(name => map.isSourceLoaded(name))) {
      await wait(32)
      if (!tileErrorSeen && map.areTilesLoaded() && images.every(name => map.isSourceLoaded(name))) return true
    }
    await wait(80)
  }
  return false
}

/*
 * Большие переходы камеры — шторка, а не перелёт. Анимация через 10+ зумов
 * всегда упирается в загрузку тайлов: MapLibre запрашивает их для каждого
 * промежуточного масштаба на всю ширину экрана, и на лету видны пустые
 * прямоугольники. Поэтому карта закрывается вуалью, под ней камера мгновенно
 * встаёт на место, и вуаль уходит только после загрузки тайлов. Движение
 * даёт CSS-масштаб готового кадра.
 *
 * whenCovered — что дождаться под вуалью до прыжка (расчёт для новой сцены).
 * from: 'near' — кадр «наезжает» (к снимку), 'far' — «отъезжает» (к глобусу).
 */
async function veiledMove(move, { whenCovered, from = 'near', limit = 3000, layersChange = false } = {}) {
  if (!map || !ready) return
  const token = ++flight
  const quick = reducedMotion()
  veiled.value = true
  // Капля появляется, только если ожидание затянулось: на быстрой сети её не видно.
  const loaderTimer = setTimeout(() => { if (token === flight) veilLoader.value = true }, 450)
  const aborted = () => { if (token !== flight) { clearTimeout(loaderTimer); return true } return false }
  await wait(quick ? 0 : 200)
  if (aborted()) return
  if (whenCovered) await whenCovered()
  if (aborted()) return
  map.stop()
  move()
  await tilesReady(limit)
  if (aborted()) return
  // Слои проявляются ещё под вуалью, чтобы кадр открылся уже целым. Ждать их
  // есть смысл только при смене сцены: у глобуса снимки не меняются.
  phase = null
  syncMap()
  await wait(quick || !layersChange ? 0 : 260)
  if (aborted()) return
  clearTimeout(loaderTimer)
  veilLoader.value = false
  veiled.value = false
  // Вода разливается, когда шторка уже поднялась, а не под ней.
  if (layersChange) water.playReveal()
  if (!quick) {
    const scale = from === 'far' ? 0.955 : 1.045
    container.value?.animate(
      [{ transform: `scale(${scale})`, filter: 'blur(3px)' }, { transform: 'scale(1)', filter: 'blur(0)' }],
      { duration: 700, easing: 'cubic-bezier(0.2, 0, 0, 1)' },
    )
  }
}

async function revealScene(token) {
  if (token !== flight) return
  phase = null
  syncMap()
  await wait(reducedMotion() ? 0 : 260)
  if (token !== flight) return
  veiled.value = false
  veilLoader.value = false
  journey.value = null
  setData('journey', empty)
  water.playReveal()
  scheduleWarmUp()
}

async function retryJourney() {
  if (!journey.value || journey.value.stage !== 'stalled') return
  const token = flight
  journey.value.stage = 'arrive'
  tileErrorSeen = false
  const chip = state.analysis.chip
  const imageCoords = coordinates(chip.bbox)
  for (const name of rasterNames) {
    map.getSource(name)?.updateImage({ url: layerUrl(chip.chip_id, name), coordinates: imageCoords })
  }
  waterLoad = loadWater(chip, false)
  map.refreshTiles()
  if (await sceneFrameReady(token)) await revealScene(token)
  else if (token === flight) journey.value.stage = 'stalled'
}

async function flyToScene(id) {
  const target = state.analysis?.scenes.find(scene => scene.chip_id === id)
  if (!map || !ready || !target || id === loadedScene) return
  const token = ++flight
  const originId = loadedScene
  const origin = state.analysis.scenes.find(scene => scene.chip_id === originId) || state.analysis.chip
  const from = sceneCenter(origin)
  const to = sceneCenter(target)
  const zoom = 1.8
  const [w, s, e, n] = target.bbox
  const camera = map.cameraForBounds([[w, s], [e, n]], { padding: padding(), maxZoom: 14.2 })
  const analysisReady = analysisFor(id)
  preloadScene(map, target, camera?.zoom ?? 13).catch(error => console.warn('Scene preload:', error))
  const globeReady = preloadJourney(map, from, to, zoom)
    .catch(error => console.warn('Journey preload:', error))

  phase = 'out'
  journey.value = { from: origin.event, to: target.event, stage: 'depart' }
  syncMap()
  veiled.value = true
  await wait(reducedMotion() ? 0 : 210)
  if (token !== flight) return
  map.stop()
  setData('journey', journeyFeatures(from, to))
  map.jumpTo({ center: from, zoom, pitch: 0, bearing: 0 })
  await globeReady
  // Прогреты тайлы всего глобуса; остальные источники могут ещё догружать
  // старый снимок и не должны скрывать сам облёт.
  await tilesReady(1800)
  if (token !== flight) return
  journey.value.stage = 'travel'
  veiled.value = false
  if (!reducedMotion()) await wait(200)
  if (token !== flight) return
  await orbitGlobe(from, to, zoom, token)
  if (token !== flight) return
  journey.value.stage = 'arrive'
  if (!await analysisReady) {
    phase = null
    journey.value = null
    veiled.value = false
    setData('journey', empty)
    state.sceneId = originId
    syncMap()
    fitChip(0)
    return
  }
  veiled.value = true
  await wait(reducedMotion() ? 0 : 180)
  if (token !== flight) return
  const retryCachedTiles = tileErrorSeen
  tileErrorSeen = false
  phase = 'in'
  syncMap()
  fitChip(0)
  if (retryCachedTiles) map.refreshTiles()
  if (await sceneFrameReady(token)) await revealScene(token)
  else if (token === flight) journey.value.stage = 'stalled'
}

/*
 * Когда камера успокоилась на снимке, в фоне готовим обзор и несколько
 * соседних готовых сцен. Остальные сцены начинают прогреваться по выбору.
 * Повторяем только при смене сцены, подложки или масштаба.
 */
let warmTimer
let warmedKey = ''
function scheduleWarmUp() {
  clearTimeout(warmTimer)
  warmTimer = setTimeout(() => {
    if (!ready || phase || !state.analysis || map.getZoom() < 9) return
    const [w, s, e, n] = state.analysis.chip.bbox
    const center = [(w + e) / 2, (s + n) / 2]
    const zoom = map.getZoom()
    const key = `${state.analysis.chip.chip_id}|${state.basemap}|${Math.round(zoom)}`
    if (key === warmedKey) return
    warmedKey = key
    preloadGlobe(map, center, zoom).then(() => ready && preloadTilt(map, center, zoom))
      .catch(error => console.warn('Map warm-up:', error))
    const nextScenes = state.analysis.scenes.filter(scene => scene.chip_id !== state.analysis.chip.chip_id).slice(0, 3)
    nextScenes.reduce((previous, scene) => previous.then(() => {
      if (!ready) return
      const [west, south, east, north] = scene.bbox
      const camera = map.cameraForBounds([[west, south], [east, north]], { padding: padding(), maxZoom: 14.2 })
      return preloadScene(map, scene, camera?.zoom ?? 13)
    }), Promise.resolve()).catch(error => console.warn('Scene warm-up:', error))
  }, 1200)
}

function focusAsset(asset) {
  if (!map) return
  const camera = { center: [asset.longitude, asset.latitude], zoom: 13.3, pitch: 20 }
  if (state.globeView) {
    return veiledMove(() => map.jumpTo({ ...camera, bearing: 0 }))
  }
  map.flyTo({ ...camera, duration: 750, essential: false })
}
function focusZone(zone) {
  if (!map || !zone) return
  const [w, s, e, n] = zone.bounds
  const options = { padding: padding(), maxZoom: 14.6, duration: reducedMotion() ? 0 : 650, easing: t => 1 - Math.pow(1 - t, 3), essential: false }
  if (state.globeView) return veiledMove(() => map.fitBounds([[w, s], [e, n]], { ...options, duration: 0 }))
  map.fitBounds([[w, s], [e, n]], options)
}
function toggleTilt() {
  if (state.globeView) return
  state.tilted = !state.tilted
  map?.easeTo({ pitch: state.tilted ? 58 : 0, bearing: state.tilted ? -18 : 0, duration: reducedMotion() ? 0 : 800, essential: false })
}

function setProbePoint(longitude, latitude) {
  setData('probe', collection([{ type: 'Feature', geometry: { type: 'Point', coordinates: [longitude, latitude] }, properties: {} }]))
}
function clearProbe() {
  probe.value = null
  if (ready) setData('probe', empty)
}
// Клик по карте читает один пиксель снимка: p, решение модели и порога, ручную метку.
async function inspectAt(longitude, latitude) {
  probeLoading.value = true
  setProbePoint(longitude, latitude)
  try {
    const params = new URLSearchParams({ scene_id: state.analysis.chip.chip_id, longitude: String(longitude), latitude: String(latitude) })
    const response = await fetch('/api/pixel?' + params)
    if (!response.ok) throw new Error('pixel API ' + response.status)
    probe.value = await response.json()
  } catch {
    probe.value = { status: 'error' }
  } finally {
    probeLoading.value = false
  }
}
const PROBE_MESSAGES = {
  outside_coverage: 'В этой точке нет снимка',
  no_valid_pixel: 'Радар здесь не дал сигнала',
  error: 'Не удалось прочитать пиксель',
}
const water3 = value => value == null ? 'нет разметки' : value ? 'вода' : 'суша'
function waterKind(pixel) {
  if (!pixel.model_water) return 'суша'
  if (pixel.permanent_water == null) return 'вода'
  return pixel.permanent_water ? 'постоянная вода' : 'паводок'
}

defineExpose({ focusChip, showGlobe, focusAsset, focusZone, focusBuilding, toggleTilt, playReveal: () => water.playReveal() })

function previewSelection(start, end) {
  const bounds = [Math.min(start.lng, end.lng), Math.min(start.lat, end.lat),
    Math.max(start.lng, end.lng), Math.max(start.lat, end.lat)]
  setData('preview', collection([{ type: 'Feature', properties: {}, geometry: polygon(bounds) }]))
  return bounds
}
function finishSelection(end) {
  if (!dragStart) return
  const bounds = previewSelection(dragStart, end || dragEnd || dragStart)
  dragStart = null
  dragEnd = null
  dragging.value = false
  if (bounds[2] - bounds[0] > 0.0003 && bounds[3] - bounds[1] > 0.0003) {
    emit('region', bounds)
    emit('draw-complete')
  } else setData('preview', empty)
}

onMounted(() => {
  try {
    map = new maplibregl.Map({
      container: container.value, style: styleUrl(),
      center: [5.818, 9.084], zoom: 11.2, minZoom: 1, maxZoom: 17,
      attributionControl: false, canvasContextAttributes: { antialias: true },
      // По умолчанию MapLibre держит в памяти тайлы не дальше 5 зумов от текущего.
      // Глобус (зум ~2) и снимок (~14) разделяют 12 зумов, и глобус вычищался при
      // каждом возврате к снимку. 300 готовых тайлов на все зумы — десятки МБ.
      maxTileCacheSize: 300, maxTileCacheZoomLevels: 16,
    })
  } catch (error) {
    mapUnavailable.value = true
    console.warn('Map initialization:', error.message)
    emit('ready')
    return
  }
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'bottom-right')
  // Атрибуцию подложки показывает строка под картой (App.vue, .map-credits): кнопка «i»
  // MapLibre пряталась под панелью решения, а на телефоне была выключена совсем.
  map.on('error', error => {
    // MapLibre считает errored-тайл «загруженным» в areTilesLoaded().
    if (error.tile) tileErrorSeen = true
    if (error.error?.message?.includes('Worker failed')) mapError.value = true
    if (error.error?.message?.includes('tiles.openfreemap.org/styles/')) installFallbackStyle()
    console.warn('Map layer:', error.error)
  })
  map.on('style.load', () => { clearTimeout(styleTimer); buildOverlays() })
  armStyleFallback()
  // Порог по реальному масштабу, а не по кнопке «Глобус»: выключается и при отдалении колесом.
  const GLOBE_ZOOM = 5
  map.on('zoom', () => {
    const globe = map.getZoom() < GLOBE_ZOOM
    if (globe === state.globeView) return
    state.globeView = globe
    if (globe) state.tilted = false
  })
  // Пока карта движется, стеклу не нужно размытие: иначе backdrop-filter пересчитывается
  // под каждой панелью на каждом кадре. Снимаем с задержкой, чтобы зум колесом не мигал.
  // Класс ставим кадром позже: resize окна (фуллскрин) шлёт movestart, move и moveend
  // синхронно подряд, и без отсрочки стекло моргало бы сплошной заливкой.
  let movingTimer
  let movingFrame
  const root = document.documentElement
  map.on('movestart', () => {
    clearTimeout(movingTimer)
    cancelAnimationFrame(movingFrame)
    movingFrame = requestAnimationFrame(() => root.classList.add('map-moving'))
  })
  map.on('moveend', () => {
    cancelAnimationFrame(movingFrame)
    clearTimeout(movingTimer)
    movingTimer = setTimeout(() => root.classList.remove('map-moving'), 150)
    scheduleWarmUp()
  })
  map.on('click', event => {
    if (!state.drawing && buildingMode.value && ready && map.getLayer('building-fill')) {
      const hit = map.queryRenderedFeatures(event.point, { layers: ['building-selected', 'building-fill'] })[0]
      const feature = buildings.data?.features.find(f => f.id === hit?.properties.id)
      if (feature) { buildings.selectedId = feature.id; clearProbe(); emit('building', feature); return }
    }
    if (!state.drawing && state.analysis) inspectAt(event.lngLat.lng, event.lngLat.lat)
  })
  map.on('mousemove', event => {
    const overBuilding = buildingMode.value && ready && map.getLayer('building-fill') && map.queryRenderedFeatures(event.point, { layers: ['building-fill'] }).length
    map.getCanvas().style.cursor = state.drawing ? 'crosshair' : overBuilding ? 'pointer' : 'grab'
    if (dragStart) { dragEnd = event.lngLat; previewSelection(dragStart, dragEnd) }
  })
  map.on('mousedown', event => {
    if (!state.drawing) return
    dragStart = event.lngLat
    dragEnd = event.lngLat
    dragging.value = true
    event.preventDefault()
  })
  map.on('mouseup', event => { if (dragStart) finishSelection(event.lngLat) })
  map.on('touchstart', event => {
    if (!state.drawing) return
    dragStart = event.lngLat
    dragEnd = event.lngLat
    dragging.value = true
    event.originalEvent.preventDefault()
  })
  map.on('touchmove', event => {
    if (dragStart && event.lngLat) { dragEnd = event.lngLat; previewSelection(dragStart, dragEnd) }
  })
  map.on('touchend', event => { if (dragStart) finishSelection(event.lngLat) })
})
onBeforeUnmount(() => { ready = false; flight++; clearTimeout(warmTimer); clearTimeout(styleTimer); for (const entry of markers.values()) { entry.untip?.(); entry.marker.remove() } map?.remove() })
watch(() => [state.analysis, state.region, state.selectedAssetId, state.strategy, state.hoverZoneId, state.selectedZoneId,
  state.layerMode, state.basemap, state.overlayOpacity, state.showRadar, state.showZones, state.liveStatus, state.tab, state.objectMode,
  state.procurementOverlay, state.projectOverlay], syncMap, { deep: true })
watch(() => state.basemap, () => scheduleWarmUp())
watch(() => [buildings.data, buildings.selectedId, buildingMode.value, state.sceneId, state.liveStatus], syncBuildings)
// classList, а не :class: Vue перезаписал бы className и стёр классы, которые ставит MapLibre.
watch(veiled, value => container.value?.classList.toggle('scene-moving', value))
// Шторка закрывается сразу по клику: пока она опускается, расчёт для новой сцены успевает прийти.
watch(() => state.sceneId, id => flyToScene(id))
watch(() => state.theme, () => {
  if (!map) return
  ready = false
  fallbackStyle = false
  // Камеру держим, только если снимок уже был на экране; иначе первая сцена не наехала бы.
  keepCamera = Boolean(loadedScene)
  loadedScene = ''
  map.setStyle(styleUrl(), { diff: false })
  armStyleFallback()
})
watch(() => state.drawing, value => {
  if (!map) return
  if (value) { map.dragPan.disable(); map.touchZoomRotate.disable(); map.getCanvas().style.cursor = 'crosshair' }
  else {
    dragStart = null; dragEnd = null; dragging.value = false
    map.dragPan.enable(); map.touchZoomRotate.enable(); map.getCanvas().style.cursor = ''
    if (ready) setData('preview', empty)
  }
})
</script>

<template>
  <div ref="container" class="globe-map" aria-label="Карта Sentinel‑1 и объектов"></div>
  <div class="map-veil" :class="{ on: veiled }" aria-hidden="true">
    <Transition name="veil-loader"><MorphLoader v-if="veilLoader" :size="44" /></Transition>
  </div>
  <Transition name="rise">
    <div v-if="journey" class="journey-card panel" role="status" aria-live="polite">
      <span class="journey-place">{{ journey.from }}</span>
      <span class="journey-route" aria-hidden="true"><i></i></span>
      <span class="journey-place">{{ journey.to }}</span>
      <span v-if="journey.stage === 'arrive'" class="journey-detail">Открываем участок…</span>
      <template v-else-if="journey.stage === 'stalled'">
        <span class="journey-detail">Карта не загрузилась</span>
        <button class="link-button" @click="retryJourney">Повторить</button>
      </template>
    </div>
  </Transition>
  <Transition name="pop">
    <div v-if="state.drawing" class="panel capsule map-hint">
      <span class="pulse"></span>{{ dragging ? 'Отпустите, чтобы посчитать область' : 'Протяните рамку по карте' }}
    </div>
  </Transition>
  <Transition name="rise">
    <div v-if="probe || probeLoading" class="panel pixel-card" aria-live="polite">
      <div class="pixel-head">
        <span><ScanSearch :size="15" />Точка на снимке</span>
        <button class="ghost-icon tiny" aria-label="Закрыть данные точки" @click="clearProbe"><X :size="15" /></button>
      </div>
      <p v-if="probeLoading" class="pixel-message">Читаем снимок…</p>
      <template v-else-if="probe?.status === 'ok'">
        <div class="hero small">
          <span class="display">{{ Math.round(probe.p_flood * 100) }}%</span>
          <span class="unit">вероятность воды</span>
        </div>
        <div class="mini-rows">
          <div v-tip="'Что показывает карта в этой точке. Постоянная вода: река или озеро по слою JRC, это не паводок'"><span>На карте</span><b>{{ waterKind(probe) }}</b></div>
          <div v-tip="'Вероятность, что карта окрасила эту точку неправильно'"><span>Шанс ошибки карты</span><b class="tabular">{{ percent(probe.map_error_probability, 1) }}</b></div>
          <div v-tip="'Классический метод для сравнения: вода там, где VH ниже −23,6 дБ'"><span>Порог VH</span><b>{{ water3(probe.baseline_water) }}</b></div>
          <div v-tip="['live_sentinel1_rtc', 'uploaded_sentinel1_geotiff'].includes(state.analysis?.chip.source_kind) ? 'Для нового снимка ручной разметки нет' : 'Разметка Sen1Floods11. Только для проверки, в расчёт не идёт'"><span>Ручная метка</span><b>{{ water3(probe.reference_water) }}</b></div>
          <div v-tip="'Насколько расходятся модели ансамбля, в процентных пунктах вероятности'"><span>Разброс моделей</span><b class="tabular">{{ number(probe.uncertainty * 100, 1) }} п.п.</b></div>
        </div>
        <p v-if="!calibrationChecked" class="pixel-calib"><CalibrationBadge /></p>
        <p class="pixel-coords tabular">{{ probe.latitude.toFixed(4) }}, {{ probe.longitude.toFixed(4) }}</p>
      </template>
      <p v-else class="pixel-message">{{ PROBE_MESSAGES[probe?.status] || PROBE_MESSAGES.error }}</p>
    </div>
  </Transition>
  <div v-if="mapUnavailable" class="panel capsule map-warning" role="status">Карта недоступна: браузер не запустил WebGL. Расчёты и выгрузка работают.</div>
  <div v-else-if="mapError" class="panel capsule map-warning">Подложка недоступна. Радар и расчёты сохранены.</div>
</template>
