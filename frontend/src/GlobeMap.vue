<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as maplibregl from 'maplibre-gl'
import { ScanSearch, X } from '@lucide/vue'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import { state } from './store'
import { number } from './lib/format'

maplibregl.setWorkerUrl(workerUrl)

const emit = defineEmits(['region', 'asset', 'draw-complete', 'ready'])
const container = ref(null)
const dragging = ref(false)
const mapError = ref(false)
const probe = ref(null)
const probeLoading = ref(false)
let map
let ready = false
let loadedScene = ''
let dragStart = null
let dragEnd = null
const markers = new Map()
const empty = { type: 'FeatureCollection', features: [] }
const rasterNames = ['radar', 'probability', 'model', 'baseline', 'reference', 'uncertainty']
const satelliteTiles = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

// Цвета оверлеев в тон интерфейсу: контур снимка — акцент, зоны и выделение — янтарь риска.
const COLORS = {
  light: { chip: '#1d6a80', zone: '#d88a2c', zoneLine: '#b8691a', zoneIdle: '#5b8797', region: '#e0a23c', regionLine: '#b8691a' },
  dark: { chip: '#7cc9df', zone: '#f0b057', zoneLine: '#f0b057', zoneIdle: '#6e98a6', region: '#f0b057', regionLine: '#f5c374' },
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
function beforeLabels() { return map.getStyle().layers.find(layer => layer.type === 'symbol')?.id }
function setData(id, data) { map.getSource(id)?.setData(data) }

function buildOverlays() {
  if (!map) return
  const c = COLORS[state.theme === 'dark' ? 'dark' : 'light']
  const before = beforeLabels()
  for (const id of ['chip', 'region', 'preview', 'zones', 'probe']) map.addSource(id, { type: 'geojson', data: empty })
  map.addSource('satellite', {
    type: 'raster', tiles: [satelliteTiles], tileSize: 256,
    attribution: 'Imagery © Esri, Maxar, Earthstar Geographics and the GIS User Community',
  })
  map.addLayer({ id: 'satellite-layer', type: 'raster', source: 'satellite',
    layout: { visibility: state.basemap === 'satellite' ? 'visible' : 'none' },
    paint: { 'raster-fade-duration': 180 } }, before)
  const chipId = state.analysis?.chip.chip_id || 'Nigeria_812045'
  const imageCoords = coordinates(state.analysis?.chip.bbox || [5.795, 9.06, 5.841, 9.107])
  for (const name of rasterNames) {
    map.addSource(name, { type: 'image', url: '/api/demo/' + chipId + '/' + name + '.png', coordinates: imageCoords })
    map.addLayer({ id: name + '-layer', type: 'raster', source: name,
      paint: { 'raster-opacity': name === 'radar' ? 0.7 : state.overlayOpacity / 100,
        'raster-opacity-transition': { duration: 220 }, 'raster-fade-duration': 250 } }, before)
  }
  map.addLayer({ id: 'chip-outline', type: 'line', source: 'chip',
    paint: { 'line-color': c.chip, 'line-width': 2, 'line-opacity': 0.9 } }, before)
  map.addLayer({ id: 'zone-fill', type: 'fill', source: 'zones',
    paint: { 'fill-color': ['case', ['==', ['get', 'active'], 1], c.zone, c.zoneIdle],
      'fill-opacity': ['case', ['==', ['get', 'active'], 1], 0.22, 0.05] } }, before)
  map.addLayer({ id: 'zone-outline', type: 'line', source: 'zones',
    paint: { 'line-color': ['case', ['==', ['get', 'active'], 1], c.zoneLine, c.zoneIdle],
      'line-width': ['case', ['==', ['get', 'active'], 1], 2.2, 1],
      'line-dasharray': [2, 1.3] } }, before)
  map.addLayer({ id: 'region-fill', type: 'fill', source: 'region', paint: { 'fill-color': c.region, 'fill-opacity': 0.12 } }, before)
  map.addLayer({ id: 'region-outline', type: 'line', source: 'region', paint: { 'line-color': c.regionLine, 'line-width': 2.5 } }, before)
  map.addLayer({ id: 'preview-fill', type: 'fill', source: 'preview', paint: { 'fill-color': c.region, 'fill-opacity': 0.1 } }, before)
  map.addLayer({ id: 'preview-line', type: 'line', source: 'preview',
    paint: { 'line-color': c.regionLine, 'line-width': 2, 'line-dasharray': [1.5, 1.5] } }, before)
  map.addLayer({ id: 'probe-halo', type: 'circle', source: 'probe',
    paint: { 'circle-radius': 14, 'circle-color': c.chip, 'circle-opacity': 0.22 } }, before)
  map.addLayer({ id: 'probe-point', type: 'circle', source: 'probe',
    paint: { 'circle-radius': 5, 'circle-color': c.chip, 'circle-stroke-width': 2, 'circle-stroke-color': '#ffffff' } }, before)
  if (probe.value?.longitude != null) setProbePoint(probe.value.longitude, probe.value.latitude)
  map.setProjection({ type: 'globe' })
  // MapLibre раскрывает длинную атрибуцию при добавлении нового raster-source.
  // Оставляем доступную кнопку «i», а сам текст показываем по запросу.
  requestAnimationFrame(() => {
    const attribution = map?.getContainer().querySelector('.maplibregl-ctrl-attrib')
    attribution?.removeAttribute('open')
    attribution?.classList.remove('maplibregl-compact-show')
  })
  ready = true
  syncMap()
  emit('ready')
}

function updateMarkers() {
  if (!map || !state.analysis) return
  const currentIds = new Set()
  const ranks = new Map(state.analysis.assets.map((asset, index) => [asset.id, index + 1]))
  for (const asset of state.analysis.assets) {
    currentIds.add(asset.id)
    let entry = markers.get(asset.id)
    if (!entry) {
      const element = document.createElement('button')
      element.type = 'button'
      element.className = 'asset-marker'
      element.addEventListener('click', event => { event.stopPropagation(); emit('asset', asset.id) })
      const marker = new maplibregl.Marker({ element, anchor: 'center' }).setLngLat([asset.longitude, asset.latitude]).addTo(map)
      entry = { marker, element }
      markers.set(asset.id, entry)
    }
    // На маркере место в рейтинге ущерба, а не служебный ID: сразу видно, куда смотреть первым.
    entry.element.textContent = String(ranks.get(asset.id))
    entry.marker.setLngLat([asset.longitude, asset.latitude])
    entry.element.classList.toggle('selected', asset.id === state.selectedAssetId)
    entry.element.classList.toggle('high-risk', asset.p_flood >= state.analysis.chip.model_threshold)
    entry.element.setAttribute('aria-label', `${ranks.get(asset.id)}. ${asset.name}, вероятность воды ${Math.round(asset.p_flood * 100)}%`)
  }
  for (const [id, entry] of markers) {
    if (!currentIds.has(id)) { entry.marker.remove(); markers.delete(id) }
  }
}

function syncMap() {
  if (!ready || !state.analysis) return
  const chip = state.analysis.chip
  if (loadedScene !== chip.chip_id) {
    const imageCoords = coordinates(chip.bbox)
    for (const name of rasterNames) {
      map.getSource(name).updateImage({ url: '/api/demo/' + chip.chip_id + '/' + name + '.png', coordinates: imageCoords })
    }
    const first = !loadedScene
    if (loadedScene && loadedScene !== chip.chip_id) clearProbe()
    loadedScene = chip.chip_id
    if (first || !state.region) focusChip()
  }
  map.setLayoutProperty('radar-layer', 'visibility', state.showRadar ? 'visible' : 'none')
  map.setLayoutProperty('satellite-layer', 'visibility', state.basemap === 'satellite' ? 'visible' : 'none')
  for (const name of rasterNames.filter(name => name !== 'radar')) {
    map.setLayoutProperty(name + '-layer', 'visibility', state.layerMode === name ? 'visible' : 'none')
    map.setPaintProperty(name + '-layer', 'raster-opacity', state.overlayOpacity / 100)
  }
  setData('chip', collection([{ type: 'Feature', geometry: polygon(chip.bbox), properties: {} }]))
  setData('region', state.region ? collection([{ type: 'Feature', geometry: polygon(state.region), properties: {} }]) : empty)
  const active = new Set(state.analysis.strategies[state.strategy]?.zone_ids || [])
  const zones = state.showZones ? state.analysis.zones.filter(zone => active.has(zone.id) || zone.id !== 'ZALL') : []
  setData('zones', collection(zones.map(zone => ({ type: 'Feature', geometry: zone.geometry,
    properties: { id: zone.id, active: active.has(zone.id) ? 1 : 0 } }))))
  updateMarkers()
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
function focusChip() {
  if (!map || !state.analysis) return
  const [w, s, e, n] = state.analysis.chip.bbox
  map.setProjection({ type: 'mercator' })
  map.fitBounds([[w, s], [e, n]], { padding: padding(), maxZoom: 14.2, pitch: state.tilted ? 55 : 0,
    duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1050, essential: false })
}
function showGlobe() {
  if (!map) return
  state.tilted = false
  map.setProjection({ type: 'globe' })
  map.flyTo({ center: [13, 11], zoom: 1.8, pitch: 0,
    duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1150, essential: false })
}
function focusAsset(asset) {
  map?.flyTo({ center: [asset.longitude, asset.latitude], zoom: 13.3, pitch: 20, duration: 750, essential: false })
}
function toggleTilt() {
  if (state.globeView) return
  state.tilted = !state.tilted
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  map?.easeTo({ pitch: state.tilted ? 58 : 0, bearing: state.tilted ? -18 : 0, duration: reduced ? 0 : 800, essential: false })
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
  no_valid_pixel: 'Пиксель без валидного сигнала',
  error: 'Не удалось прочитать пиксель',
}
const water = value => value == null ? 'нет' : value ? 'вода' : 'суша'

defineExpose({ focusChip, showGlobe, focusAsset, toggleTilt })

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
  map = new maplibregl.Map({
    container: container.value, style: styleUrl(),
    center: [5.818, 9.084], zoom: 11.2, minZoom: 1, maxZoom: 17,
    attributionControl: false, canvasContextAttributes: { antialias: true },
  })
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'bottom-right')
  map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')
  map.on('error', error => {
    if (error.error?.message?.includes('Worker failed')) mapError.value = true
    console.warn('Map layer:', error.error)
  })
  map.on('style.load', buildOverlays)
  // Порог по реальному масштабу, а не по кнопке «Глобус»: выключается и при отдалении колесом.
  const GLOBE_ZOOM = 5
  map.on('zoom', () => {
    const globe = map.getZoom() < GLOBE_ZOOM
    if (globe === state.globeView) return
    state.globeView = globe
    if (globe) state.tilted = false
  })
  map.on('click', event => {
    if (!state.drawing && state.analysis) inspectAt(event.lngLat.lng, event.lngLat.lat)
  })
  map.on('mousemove', event => {
    map.getCanvas().style.cursor = state.drawing ? 'crosshair' : 'grab'
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
onBeforeUnmount(() => { for (const entry of markers.values()) entry.marker.remove(); map?.remove() })
watch(() => [state.analysis, state.region, state.selectedAssetId, state.strategy,
  state.layerMode, state.basemap, state.overlayOpacity, state.showRadar, state.showZones], syncMap, { deep: true })
watch(() => state.theme, () => { ready = false; loadedScene = ''; map?.setStyle(styleUrl(), { diff: false }) })
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
  <Transition name="pop">
    <div v-if="state.drawing" class="panel capsule map-hint">
      <span class="pulse"></span>{{ dragging ? 'Отпустите, чтобы проанализировать область' : 'Протяните рамку по карте' }}
    </div>
  </Transition>
  <Transition name="rise">
    <div v-if="probe || probeLoading" class="panel pixel-card" aria-live="polite">
      <div class="pixel-head">
        <span><ScanSearch :size="15" />Пиксель Sentinel‑1</span>
        <button class="ghost-icon tiny" aria-label="Закрыть данные пикселя" @click="clearProbe"><X :size="15" /></button>
      </div>
      <p v-if="probeLoading" class="pixel-message">Читаем снимок…</p>
      <template v-else-if="probe?.status === 'ok'">
        <div class="hero small">
          <span class="display">{{ Math.round(probe.p_flood * 100) }}%</span>
          <span class="unit">вероятность воды</span>
        </div>
        <div class="mini-rows">
          <div v-tip="'Решение модели при пороге p ≥ ' + probe.model_threshold + ' и классического порога VH'"><span>Модель / порог VH</span><b>{{ water(probe.model_water) }} / {{ water(probe.baseline_water) }}</b></div>
          <div><span>Разброс ансамбля</span><b class="tabular">{{ number(probe.uncertainty * 100, 1) }} п.п.</b></div>
          <div v-tip="'Ручная метка Sen1Floods11 только для контроля качества, в расчёт не идёт'"><span>Ручная метка</span><b>{{ water(probe.reference_water) }}</b></div>
        </div>
        <p class="pixel-coords tabular">{{ probe.latitude.toFixed(4) }}, {{ probe.longitude.toFixed(4) }}</p>
      </template>
      <p v-else class="pixel-message">{{ PROBE_MESSAGES[probe?.status] || PROBE_MESSAGES.error }}</p>
    </div>
  </Transition>
  <div v-if="mapError" class="panel capsule map-warning">Подложка недоступна. Радар и расчёты сохранены.</div>
</template>
