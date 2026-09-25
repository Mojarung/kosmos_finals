<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as maplibregl from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import { state } from './store'

maplibregl.setWorkerUrl(workerUrl)

const emit = defineEmits(['region', 'asset', 'draw-complete', 'ready'])
const container = ref(null)
const pickStart = ref(null)
const mapError = ref(false)
let map
let ready = false
let loadedScene = ''
const markers = new Map()
const empty = { type: 'FeatureCollection', features: [] }
const rasterNames = ['radar', 'probability', 'model', 'baseline', 'reference', 'uncertainty']

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
  for (const id of ['chip', 'region', 'preview', 'zones']) map.addSource(id, { type: 'geojson', data: empty })
  const chipId = state.analysis?.chip.chip_id || 'Nigeria_812045'
  const imageCoords = coordinates(state.analysis?.chip.bbox || [5.795, 9.06, 5.841, 9.107])
  for (const name of rasterNames) {
    map.addSource(name, { type: 'image', url: '/api/demo/' + chipId + '/' + name + '.png', coordinates: imageCoords })
    map.addLayer({ id: name + '-layer', type: 'raster', source: name,
      paint: { 'raster-opacity': name === 'radar' ? 0.7 : 0.86, 'raster-fade-duration': 250 } }, before)
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
  map.setProjection({ type: 'globe' })
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
    loadedScene = chip.chip_id
    if (first || !state.region) focusChip()
  }
  map.setLayoutProperty('radar-layer', 'visibility', state.showRadar ? 'visible' : 'none')
  for (const name of rasterNames.filter(name => name !== 'radar')) {
    map.setLayoutProperty(name + '-layer', 'visibility', state.layerMode === name ? 'visible' : 'none')
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
  map.fitBounds([[w, s], [e, n]], { padding: padding(), maxZoom: 13.6, pitch: 22, duration: 1050, essential: false })
}
function showGlobe() { map?.flyTo({ center: [13, 11], zoom: 1.8, pitch: 0, duration: 1150, essential: false }) }
function focusAsset(asset) {
  map?.flyTo({ center: [asset.longitude, asset.latitude], zoom: 13.3, pitch: 20, duration: 750, essential: false })
}
defineExpose({ focusChip, showGlobe, focusAsset })

onMounted(() => {
  map = new maplibregl.Map({
    container: container.value, style: styleUrl(),
    center: [5.818, 9.084], zoom: 11.2, minZoom: 1, maxZoom: 17,
    attributionControl: false, canvasContextAttributes: { antialias: true },
  })
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'bottom-right')
  map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')
  map.on('error', error => { mapError.value = true; console.warn('Map layer:', error.error) })
  map.on('style.load', buildOverlays)
  map.on('click', event => {
    if (!state.drawing) return
    const point = [event.lngLat.lng, event.lngLat.lat]
    if (!pickStart.value) { pickStart.value = point; return }
    const west = Math.min(pickStart.value[0], point[0])
    const east = Math.max(pickStart.value[0], point[0])
    const south = Math.min(pickStart.value[1], point[1])
    const north = Math.max(pickStart.value[1], point[1])
    pickStart.value = null
    setData('preview', empty)
    if (east - west > 0.0003 && north - south > 0.0003) emit('region', [west, south, east, north])
    emit('draw-complete')
  })
  map.on('mousemove', event => {
    map.getCanvas().style.cursor = state.drawing ? 'crosshair' : 'grab'
    if (!state.drawing || !pickStart.value) return
    const [x, y] = pickStart.value
    const point = [event.lngLat.lng, event.lngLat.lat]
    setData('preview', collection([{ type: 'Feature', properties: {},
      geometry: polygon([Math.min(x, point[0]), Math.min(y, point[1]), Math.max(x, point[0]), Math.max(y, point[1])]) }]))
  })
})
onBeforeUnmount(() => { for (const entry of markers.values()) entry.marker.remove(); map?.remove() })
watch(() => [state.analysis, state.region, state.selectedAssetId, state.strategy,
  state.layerMode, state.showRadar, state.showZones], syncMap, { deep: true })
watch(() => state.theme, () => { ready = false; loadedScene = ''; map?.setStyle(styleUrl(), { diff: false }) })
watch(() => state.drawing, value => { if (!value) { pickStart.value = null; if (ready) setData('preview', empty) } })
</script>

<template>
  <div ref="container" class="globe-map" aria-label="Карта Sentinel‑1 и объектов"></div>
  <Transition name="pop">
    <div v-if="state.drawing" class="panel capsule map-hint">
      <span class="pulse"></span>{{ pickStart ? 'Кликните второй угол области' : 'Кликните первый угол области' }}
    </div>
  </Transition>
  <div v-if="mapError" class="panel capsule map-warning">Подложка недоступна. Радар и расчёты сохранены.</div>
</template>
