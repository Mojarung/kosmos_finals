<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as maplibregl from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'

maplibregl.setWorkerUrl(workerUrl)

const props = defineProps({
  analysis: { type: Object, default: null },
  region: { type: Array, default: null },
  drawing: { type: Boolean, default: false },
  selectedAsset: { type: String, default: null },
  activeStrategy: { type: String, default: 'C' },
  showRadar: { type: Boolean, default: true },
  showWater: { type: Boolean, default: true },
  showCandidates: { type: Boolean, default: false },
})
const emit = defineEmits(['region', 'asset', 'draw-complete'])
const container = ref(null)
const mapReady = ref(false)
const pickStart = ref(null)
const mapError = ref(false)
let map

const empty = { type: 'FeatureCollection', features: [] }
const chip = computed(() => props.analysis?.chip)

function polygon(bounds) {
  const [w, s, e, n] = bounds
  return { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] }
}
function featureCollection(items) { return { type: 'FeatureCollection', features: items } }
function setData(id, data) { map?.getSource(id)?.setData(data) }
function imageCoordinates(bounds) {
  const [w, s, e, n] = bounds
  return [[w, n], [e, n], [e, s], [w, s]]
}
function syncMap() {
  if (!mapReady.value || !chip.value) return
  const bounds = chip.value.bbox
  const imageCoords = imageCoordinates(bounds)
  if (!map.getSource('radar')) {
    map.addSource('radar', { type: 'image', url: '/api/demo/radar.png', coordinates: imageCoords })
    map.addLayer({ id: 'radar-layer', type: 'raster', source: 'radar', paint: { 'raster-opacity': 0.9, 'raster-fade-duration': 300 } }, 'chip-outline')
  }
  if (!map.getSource('water')) {
    map.addSource('water', { type: 'image', url: '/api/demo/water.png', coordinates: imageCoords })
    map.addLayer({ id: 'water-layer', type: 'raster', source: 'water', paint: { 'raster-opacity': 0.95, 'raster-fade-duration': 300 } }, 'chip-outline')
  }
  map.setLayoutProperty('radar-layer', 'visibility', props.showRadar ? 'visible' : 'none')
  map.setLayoutProperty('water-layer', 'visibility', props.showWater ? 'visible' : 'none')

  setData('chip', featureCollection([{ type: 'Feature', geometry: polygon(bounds), properties: {} }]))
  setData('region', props.region ? featureCollection([{ type: 'Feature', geometry: polygon(props.region), properties: {} }]) : empty)
  const activeIds = new Set(props.analysis.strategies[props.activeStrategy]?.zone_ids || [])
  const zones = props.analysis.zones.filter(z => activeIds.has(z.id) || (props.showCandidates && z.id !== 'ZALL'))
  setData('zones', featureCollection(zones.map(z => ({ type: 'Feature', geometry: z.geometry,
    properties: { id: z.id, active: activeIds.has(z.id) ? 1 : 0 } }))))
  setData('assets', featureCollection(props.analysis.assets.map(a => ({ type: 'Feature',
    geometry: { type: 'Point', coordinates: [a.longitude, a.latitude] },
    properties: { id: a.id, selected: a.id === props.selectedAsset ? 1 : 0, name: a.name } }))))
}

function focusChip() {
  if (!map || !chip.value) return
  map.fitBounds([[chip.value.bbox[0], chip.value.bbox[1]], [chip.value.bbox[2], chip.value.bbox[3]]],
    { padding: 45, maxZoom: 14, duration: 1300, essential: true })
}
function showGlobe() { map?.flyTo({ center: [0, 11], zoom: 1.7, duration: 1400, essential: true }) }
function focusAsset(asset) { map?.flyTo({ center: [asset.longitude, asset.latitude], zoom: 12.3, duration: 800, essential: true }) }
defineExpose({ focusChip, showGlobe, focusAsset })

onMounted(() => {
  map = new maplibregl.Map({
    container: container.value,
    style: {
      version: 8,
      sources: { world: { type: 'vector', url: 'https://demotiles.maplibre.org/tiles/tiles.json',
        attribution: '© MapLibre contributors' } },
      layers: [
        { id: 'background', type: 'background', paint: { 'background-color': '#132b35' } },
        { id: 'land', type: 'fill', source: 'world', 'source-layer': 'countries', paint: { 'fill-color': '#28413b', 'fill-opacity': 0.94 } },
        { id: 'borders', type: 'line', source: 'world', 'source-layer': 'countries', paint: { 'line-color': '#8ca99a', 'line-width': 0.7, 'line-opacity': 0.64 } },
        { id: 'geolines', type: 'line', source: 'world', 'source-layer': 'geolines', paint: { 'line-color': '#618587', 'line-width': 0.8, 'line-opacity': 0.3, 'line-dasharray': [2, 3] } },
      ],
    },
    center: [0, 11], zoom: 1.7, minZoom: 1, maxZoom: 17,
    attributionControl: false,
  })
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'bottom-right')
  map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')
  map.on('error', error => { if (!mapReady.value) mapError.value = true; console.warn('Map layer:', error.error) })
  map.on('load', () => {
    map.setProjection({ type: 'globe' })
    for (const id of ['chip', 'region', 'zones', 'assets']) map.addSource(id, { type: 'geojson', data: empty })
    map.addLayer({ id: 'chip-outline', type: 'line', source: 'chip', paint: { 'line-color': '#b4f072', 'line-width': 2.5, 'line-dasharray': [2, 1.2] } })
    map.addLayer({ id: 'region-fill', type: 'fill', source: 'region', paint: { 'fill-color': '#b4f072', 'fill-opacity': 0.12 } })
    map.addLayer({ id: 'region-outline', type: 'line', source: 'region', paint: { 'line-color': '#d4fc93', 'line-width': 2.5 } })
    map.addLayer({ id: 'zone-fill', type: 'fill', source: 'zones', paint: { 'fill-color': ['case', ['==', ['get', 'active'], 1], '#27d6df', '#a7b8b8'], 'fill-opacity': ['case', ['==', ['get', 'active'], 1], 0.22, 0.04] } })
    map.addLayer({ id: 'zone-outline', type: 'line', source: 'zones', paint: { 'line-color': ['case', ['==', ['get', 'active'], 1], '#49e6ec', '#90a4a5'], 'line-width': ['case', ['==', ['get', 'active'], 1], 2.3, 1], 'line-dasharray': [2, 1] } })
    map.addLayer({ id: 'asset-halo', type: 'circle', source: 'assets', paint: { 'circle-radius': ['case', ['==', ['get', 'selected'], 1], 16, 11], 'circle-color': '#b4f072', 'circle-opacity': 0.16 } })
    map.addLayer({ id: 'asset-dots', type: 'circle', source: 'assets', paint: { 'circle-radius': ['case', ['==', ['get', 'selected'], 1], 7, 5], 'circle-color': ['case', ['==', ['get', 'selected'], 1], '#e3ffa8', '#b4f072'], 'circle-stroke-color': '#10211d', 'circle-stroke-width': 2 } })
    mapReady.value = true
    syncMap()
  })
  map.on('click', event => {
    if (props.drawing) {
      const point = [event.lngLat.lng, event.lngLat.lat]
      if (!pickStart.value) { pickStart.value = point; return }
      const west = Math.min(pickStart.value[0], point[0]); const east = Math.max(pickStart.value[0], point[0])
      const south = Math.min(pickStart.value[1], point[1]); const north = Math.max(pickStart.value[1], point[1])
      pickStart.value = null
      if (east - west > 0.0003 && north - south > 0.0003) emit('region', [west, south, east, north])
      emit('draw-complete')
      return
    }
    const hit = map.queryRenderedFeatures(event.point, { layers: ['asset-dots'] })[0]
    if (hit) emit('asset', hit.properties.id)
  })
  map.on('mousemove', event => {
    map.getCanvas().style.cursor = props.drawing ? 'crosshair' :
      (map.queryRenderedFeatures(event.point, { layers: ['asset-dots'] }).length ? 'pointer' : 'grab')
  })
})
onBeforeUnmount(() => map?.remove())
watch(() => [props.analysis, props.region, props.selectedAsset, props.activeStrategy,
  props.showRadar, props.showWater, props.showCandidates], syncMap, { deep: true })
watch(() => props.drawing, value => { if (!value) pickStart.value = null })
</script>

<template>
  <div ref="container" class="globe-map" aria-label="Интерактивная 3D карта Земли"></div>
  <div v-if="drawing" class="draw-hint"><span class="draw-pulse"></span>{{ pickStart ? 'Теперь второй угол' : 'Выберите первый угол региона' }}</div>
  <div v-if="mapError" class="map-warning">Подложка карты недоступна; данные чипа остаются доступны.</div>
</template>
