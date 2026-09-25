<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as maplibregl from 'maplibre-gl'
import { Box, ScanSearch, X } from '@lucide/vue'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'

maplibregl.setWorkerUrl(workerUrl)

const props = defineProps({
  analysis: { type: Object, default: null },
  region: { type: Array, default: null },
  drawing: { type: Boolean, default: false },
  selectedAsset: { type: String, default: null },
  activeStrategy: { type: String, default: 'C' },
  layerMode: { type: String, default: 'model' },
  showRadar: { type: Boolean, default: true },
  showZones: { type: Boolean, default: true },
  theme: { type: String, default: 'light' },
})
const emit = defineEmits(['region', 'asset', 'draw-complete', 'ready'])
const container = ref(null)
const pickStart = ref(null)
const mapError = ref(false)
const tilted = ref(false)
const probe = ref(null)
const probeLoading = ref(false)
let map
let ready = false
let loadedScene = ''
const markers = new Map()
const empty = { type: 'FeatureCollection', features: [] }
const rasterNames = ['radar', 'probability', 'model', 'baseline', 'reference', 'uncertainty']

function polygon(bounds) {
  const [w, s, e, n] = bounds
  return { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] }
}
function collection(features) { return { type: 'FeatureCollection', features } }
function coordinates(bounds) {
  const [w, s, e, n] = bounds
  return [[w, n], [e, n], [e, s], [w, s]]
}
function styleUrl() { return 'https://tiles.openfreemap.org/styles/' + (props.theme === 'dark' ? 'dark' : 'bright') }
function beforeLabels() { return map.getStyle().layers.find(layer => layer.type === 'symbol')?.id }
function setData(id, data) { map.getSource(id)?.setData(data) }

function buildOverlays() {
  if (!map) return
  const before = beforeLabels()
  for (const id of ['chip', 'region', 'preview', 'zones', 'probe']) map.addSource(id, { type: 'geojson', data: empty })
  const bounds = props.analysis?.chip.bbox || [5.795, 9.06, 5.841, 9.107]
  const imageCoords = coordinates(bounds)
  for (const name of rasterNames) {
    const url = props.analysis ? '/api/demo/' + props.analysis.chip.chip_id + '/' + name + '.png' : '/api/demo/Nigeria_812045/' + name + '.png'
    map.addSource(name, { type: 'image', url, coordinates: imageCoords })
    map.addLayer({ id: name + '-layer', type: 'raster', source: name,
      paint: { 'raster-opacity': name === 'radar' ? 0.68 : 0.88, 'raster-fade-duration': 250 } }, before)
  }
  map.addLayer({ id: 'chip-fill', type: 'fill', source: 'chip', paint: { 'fill-color': '#3e80d5', 'fill-opacity': 0.035 } }, before)
  map.addLayer({ id: 'chip-outline', type: 'line', source: 'chip',
    paint: { 'line-color': '#286ebc', 'line-width': 2.3, 'line-opacity': 0.8 } }, before)
  map.addLayer({ id: 'zone-fill', type: 'fill', source: 'zones',
    paint: { 'fill-color': ['case', ['==', ['get', 'active'], 1], '#edab4c', '#4c7fb8'],
      'fill-opacity': ['case', ['==', ['get', 'active'], 1], 0.24, 0.06] } }, before)
  map.addLayer({ id: 'zone-outline', type: 'line', source: 'zones',
    paint: { 'line-color': ['case', ['==', ['get', 'active'], 1], '#c37724', '#507da8'],
      'line-width': ['case', ['==', ['get', 'active'], 1], 2.4, 1.1],
      'line-dasharray': [2, 1.3] } }, before)
  map.addLayer({ id: 'region-fill', type: 'fill', source: 'region',
    paint: { 'fill-color': '#f2bc54', 'fill-opacity': 0.14 } }, before)
  map.addLayer({ id: 'region-outline', type: 'line', source: 'region',
    paint: { 'line-color': '#c87518', 'line-width': 3 } }, before)
  map.addLayer({ id: 'preview-fill', type: 'fill', source: 'preview',
    paint: { 'fill-color': '#efa330', 'fill-opacity': 0.1 } }, before)
  map.addLayer({ id: 'preview-line', type: 'line', source: 'preview',
    paint: { 'line-color': '#d17e1c', 'line-width': 2, 'line-dasharray': [1.5, 1.5] } }, before)
  map.addLayer({ id: 'probe-halo', type: 'circle', source: 'probe',
    paint: { 'circle-radius': 13, 'circle-color': '#e9a84c', 'circle-opacity': 0.2 } }, before)
  map.addLayer({ id: 'probe-point', type: 'circle', source: 'probe',
    paint: { 'circle-radius': 5, 'circle-color': '#ecab4d', 'circle-stroke-width': 2,
      'circle-stroke-color': '#ffffff' } }, before)
  map.setProjection({ type: 'globe' })
  ready = true
  syncMap()
  emit('ready')
}

function updateMarkers() {
  if (!map || !props.analysis) return
  const currentIds = new Set()
  for (const asset of props.analysis.assets) {
    currentIds.add(asset.id)
    let entry = markers.get(asset.id)
    if (!entry) {
      const element = document.createElement('button')
      element.type = 'button'
      element.className = 'asset-marker'
      element.textContent = asset.id
      element.setAttribute('aria-label', asset.name + ', вероятность воды ' + Math.round(asset.p_flood * 100) + '%')
      element.addEventListener('click', event => { event.stopPropagation(); emit('asset', asset.id) })
      const marker = new maplibregl.Marker({ element, anchor: 'center' })
        .setLngLat([asset.longitude, asset.latitude]).addTo(map)
      entry = { marker, element }
      markers.set(asset.id, entry)
    }
    entry.marker.setLngLat([asset.longitude, asset.latitude])
    entry.element.classList.toggle('selected', asset.id === props.selectedAsset)
    entry.element.classList.toggle('high-risk', asset.p_flood >= 0.41)
    entry.element.setAttribute('aria-label', asset.name + ', вероятность воды ' + Math.round(asset.p_flood * 100) + '%')
  }
  for (const [id, entry] of markers) {
    if (!currentIds.has(id)) { entry.marker.remove(); markers.delete(id) }
  }
}

function syncMap() {
  if (!ready || !props.analysis) return
  const chip = props.analysis.chip
  if (loadedScene !== chip.chip_id) {
    const imageCoords = coordinates(chip.bbox)
    for (const name of rasterNames) {
      map.getSource(name).updateImage({
        url: '/api/demo/' + chip.chip_id + '/' + name + '.png',
        coordinates: imageCoords,
      })
    }
    loadedScene = chip.chip_id
    probe.value = null
    setData('probe', empty)
    focusChip()
  }
  map.setLayoutProperty('radar-layer', 'visibility', props.showRadar ? 'visible' : 'none')
  for (const name of rasterNames.filter(name => name !== 'radar')) {
    map.setLayoutProperty(name + '-layer', 'visibility', props.layerMode === name ? 'visible' : 'none')
  }
  setData('chip', collection([{ type: 'Feature', geometry: polygon(chip.bbox), properties: {} }]))
  setData('region', props.region ? collection([{ type: 'Feature', geometry: polygon(props.region), properties: {} }]) : empty)
  const active = new Set(props.analysis.strategies[props.activeStrategy]?.zone_ids || [])
  const zones = props.showZones
    ? props.analysis.zones.filter(zone => active.has(zone.id) || zone.id !== 'ZALL')
    : []
  setData('zones', collection(zones.map(zone => ({ type: 'Feature', geometry: zone.geometry,
    properties: { id: zone.id, active: active.has(zone.id) ? 1 : 0 } }))))
  updateMarkers()
}

function focusChip() {
  if (!map || !props.analysis) return
  const [w, s, e, n] = props.analysis.chip.bbox
  const desktop = window.innerWidth >= 1180
  const tablet = window.innerWidth >= 700 && !desktop
  map.fitBounds([[w, s], [e, n]], {
    padding: desktop ? { left: 338, right: 378, top: 120, bottom: 110 }
      : tablet ? { left: 320, right: 48, top: 116, bottom: 90 }
        : { left: 28, right: 28, top: 105, bottom: 285 },
    maxZoom: 13.6, pitch: 22, duration: 1050, essential: false,
  })
}
function showGlobe() { map?.flyTo({ center: [13, 11], zoom: 1.8, pitch: 0, duration: 1150, essential: false }) }
function toggleTilt() {
  tilted.value = !tilted.value
  map?.easeTo({ pitch: tilted.value ? 58 : 0,
    bearing: tilted.value ? -18 : 0,
    duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 700,
    essential: false })
}
function clearProbe() {
  probe.value = null
  if (ready) setData('probe', empty)
}
async function inspectAt(longitude, latitude) {
  probeLoading.value = true
  setData('probe', collection([{ type: 'Feature', geometry: { type: 'Point', coordinates: [longitude, latitude] }, properties: {} }]))
  try {
    const params = new URLSearchParams({ scene_id: props.analysis.chip.chip_id,
      longitude: String(longitude), latitude: String(latitude) })
    const response = await fetch('/api/pixel?' + params)
    if (!response.ok) throw new Error('pixel API ' + response.status)
    probe.value = await response.json()
  } catch {
    probe.value = { status: 'error' }
  } finally { probeLoading.value = false }
}
function focusAsset(asset) {
  if (!map) return
  map.flyTo({ center: [asset.longitude, asset.latitude], zoom: 13.3, pitch: 20,
    duration: 750, essential: false })
}
function focusRegion() {
  if (!map || !props.region) return
  const [w, s, e, n] = props.region
  map.fitBounds([[w, s], [e, n]], { padding: 105, maxZoom: 13.8, duration: 750, essential: false })
}
defineExpose({ focusChip, showGlobe, focusAsset, focusRegion })

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
    if (!props.drawing) {
      if (props.analysis) inspectAt(event.lngLat.lng, event.lngLat.lat)
      return
    }
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
    map.getCanvas().style.cursor = props.drawing ? 'crosshair' : 'grab'
    if (!props.drawing || !pickStart.value) return
    const [x, y] = pickStart.value
    const point = [event.lngLat.lng, event.lngLat.lat]
    const bounds = [Math.min(x, point[0]), Math.min(y, point[1]),
      Math.max(x, point[0]), Math.max(y, point[1])]
    setData('preview', collection([{ type: 'Feature', geometry: polygon(bounds), properties: {} }]))
  })
})
onBeforeUnmount(() => { for (const entry of markers.values()) entry.marker.remove(); map?.remove() })
watch(() => [props.analysis, props.region, props.selectedAsset, props.activeStrategy,
  props.layerMode, props.showRadar, props.showZones], syncMap, { deep: true })
watch(() => props.theme, () => { ready = false; loadedScene = ''; map?.setStyle(styleUrl(), { diff: false }) })
watch(() => props.drawing, value => { if (!value) { pickStart.value = null; if (ready) setData('preview', empty) } })
</script>

<template>
  <div ref="container" class="globe-map" aria-label="Карта Sentinel-1 и объектов"></div>
  <div class="map-tools" role="group" aria-label="Режим карты">
    <button type="button" :class="{ active: tilted }" :aria-pressed="tilted"
      title="Наклонный обзор территории" @click="toggleTilt"><Box :size="16" />{{ tilted ? 'Сверху' : '3D-обзор' }}</button>
  </div>
  <div v-if="probe || probeLoading" class="pixel-card" aria-live="polite">
    <div class="pixel-card-head"><span><ScanSearch :size="15" /> Пиксель S1</span><button aria-label="Закрыть данные пикселя" @click="clearProbe"><X :size="15" /></button></div>
    <span v-if="probeLoading" class="pixel-message">Читаем карту…</span>
    <template v-else-if="probe?.status === 'ok'">
      <strong>{{ Math.round(probe.p_flood * 100) }}% <small>p воды</small></strong>
      <div><span>Модель / порог</span><b>{{ probe.model_water ? 'вода' : 'суша' }} / {{ probe.baseline_water ? 'вода' : 'суша' }}</b></div>
      <div><span>Разброс ансамбля</span><b>{{ (probe.uncertainty * 100).toFixed(1) }} п.п.</b></div>
      <div><span>Ручная метка</span><b>{{ probe.reference_water == null ? 'нет' : probe.reference_water ? 'вода' : 'суша' }}</b></div>
      <small>Метка — только контроль качества. {{ probe.longitude.toFixed(4) }}, {{ probe.latitude.toFixed(4) }}</small>
    </template>
    <span v-else class="pixel-message">{{ probe?.status === 'outside_coverage' ? 'Нет снимка в этой точке' : probe?.status === 'no_valid_pixel' ? 'Пиксель без валидного сигнала' : 'Не удалось прочитать пиксель' }}</span>
  </div>
  <div v-if="drawing" class="draw-hint"><span class="draw-pulse"></span>{{ pickStart ? 'Выберите второй угол' : 'Выберите первый угол области' }}</div>
  <div v-if="mapError" class="map-warning">Подложка недоступна. Радар и расчёты сохранены.</div>
</template>
