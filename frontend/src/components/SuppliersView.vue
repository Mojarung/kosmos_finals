<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowUpRight, FolderUp, Upload } from '@lucide/vue'
import { activePlan, state } from '../store'
import { date, km, number, percent, plural } from '../lib/format'

// Где купить снимки плана и что пришло от поставщика. Сервис ничего не заказывает сам.
const props = defineProps({ buildingRequest: Object, buildingZones: Array })
const catalog = ref(null)
const deliveries = ref({ deliveries: [], zones_covered: [] })
const providerId = ref('roscosmos_geoportal')
const acquiredAt = ref('')
const progress = ref(null)
const error = ref('')
const folderInput = ref(null)
const filesInput = ref(null)
const SENSOR = { sar: 'радар', optical: 'оптика' }

// Какие сенсоры нужны текущему плану: по ним подсвечиваются подходящие поставщики.
const needed = computed(() => {
  const zones = new Map((props.buildingZones || state.analysis?.zones || []).map(zone => [zone.id, zone]))
  return new Set((planZones.value).map(id => zones.get(id)?.product?.sensor_type || zones.get(id)?.sensor_type).filter(Boolean))
})
const providers = computed(() => (catalog.value?.providers || []).map(row => ({
  ...row, fits: row.sensors.some(sensor => needed.value.has(sensor)) && !row.acquisition.includes('free'),
})))
const planZones = computed(() => props.buildingZones ? props.buildingZones.map(z => z.id) : activePlan.value?.zone_ids || [])
const closed = computed(() => deliveries.value.zones_covered.filter(id => planZones.value.includes(id)))

async function json(url, options) {
  const response = await fetch(url, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Сервер не принял запрос')
  return data
}
async function refresh() {
  deliveries.value = props.buildingRequest
    ? await json('/api/building-plans/deliveries/coverage', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(props.buildingRequest) })
    : await json(`/api/procurement/deliveries?scene_id=${encodeURIComponent(state.sceneId)}`)
}
async function upload(fileList) {
  const files = [...fileList].filter(file => !file.name.startsWith('.'))
  if (!files.length) return
  error.value = ''
  progress.value = { done: 0, total: files.length }
  try {
    const delivery = await json(props.buildingRequest ? '/api/building-plans/deliveries' : '/api/procurement/deliveries', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...(props.buildingRequest ? { plan: props.buildingRequest } : { scene_id: state.sceneId }), provider_id: providerId.value, acquired_at: acquiredAt.value || null }),
    })
    for (const file of files) {
      const path = file.webkitRelativePath || file.name
      await json(`/api/procurement/deliveries/${delivery.id}/files?path=${encodeURIComponent(path)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
      })
      progress.value = { done: progress.value.done + 1, total: files.length }
    }
    await refresh()
  } catch (reason) {
    error.value = reason.message
  } finally {
    progress.value = null
    if (folderInput.value) folderInput.value.value = ''
    if (filesInput.value) filesInput.value.value = ''
  }
}
const STATUS = { not_georeferenced: 'нет привязки', unreadable: 'не читается' }
function zonesLine(entry) {
  if (entry.status !== 'georeferenced') return STATUS[entry.status] || ''
  if (!entry.zones.length) return 'вне зон заказа'
  return ''
}

onMounted(async () => {
  try {
    catalog.value = await json('/api/procurement/providers')
    await refresh()
  } catch (reason) {
    error.value = reason.message
  }
})
watch(() => [state.sceneId, props.buildingRequest], () => refresh().catch(reason => { error.value = reason.message }))
</script>

<template>
  <section class="block suppliers">
    <span class="caption">Где купить снимки плана<template v-if="needed.size"> · нужен {{ [...needed].map(s => SENSOR[s]).join(' и ') }}</template></span>
    <details class="source-details"><summary>Поставщики · {{ providers.length }} · открыть список</summary>
    <ul class="supplier-list">
      <li v-for="p in providers" :key="p.id" :class="{ fits: p.fits }">
        <div class="supplier-head">
          <b>{{ p.name }}</b>
          <a :href="p.url" target="_blank" rel="noopener noreferrer" :aria-label="'Открыть ' + p.name">{{ p.acquisition.includes('free') ? 'Скачать' : 'Заказать' }} <ArrowUpRight :size="13" /></a>
        </div>
        <p>{{ p.products }}</p>
        <p class="supplier-meta">{{ p.country }} · {{ p.order }} · {{ p.price_basis }} · <span :class="{ unverified: p.russia.includes('не проверяли') }">{{ p.russia }}</span></p>
      </li>
    </ul>
    </details>
    <details class="source-details"><summary>Источники, проверено {{ catalog ? date(catalog.checked_at) : '…' }}</summary>
      <p v-for="p in providers" :key="p.id">{{ p.name }}: <a v-for="(url, i) in p.sources" :key="url" :href="url" target="_blank" rel="noopener noreferrer">[{{ i + 1 }}]</a></p>
    </details>
  </section>

  <section class="block suppliers">
    <span class="caption">Полученные снимки · из папки поставщика</span>
    <div class="delivery-form">
      <select v-model="providerId" aria-label="Поставщик">
        <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
        <option value="other">Другой поставщик</option>
      </select>
      <input v-model="acquiredAt" type="date" aria-label="Дата съёмки" v-tip="'Дата съёмки из паспорта снимка, если она известна'" />
    </div>
    <div class="delivery-buttons">
      <button class="primary-button" :disabled="!!progress" @click="folderInput.click()"><FolderUp :size="15" />Выбрать папку</button>
      <button class="quiet-button" :disabled="!!progress" @click="filesInput.click()"><Upload :size="15" />Файлы</button>
      <input ref="folderInput" type="file" webkitdirectory multiple hidden @change="upload($event.target.files)" />
      <input ref="filesInput" type="file" multiple hidden accept=".tif,.tiff,.jp2,.img,.ntf,.nitf,.xml,.txt,.json" @change="upload($event.target.files)" />
    </div>
    <p v-if="progress" class="source-line" role="status">Загружаем {{ progress.done }} из {{ progress.total }}…</p>
    <p v-if="error" class="source-line error-line" role="alert">{{ error }}</p>
    <p v-if="planZones.length" class="source-line">Закрыто зон плана: <b>{{ closed.length }} из {{ planZones.length }}</b><template v-if="closed.length"> · {{ closed.join(', ') }}</template></p>
    <p v-if="buildingRequest && deliveries.building_count != null" class="source-line">Контуры зданий в полученных файлах: <b>{{ deliveries.buildings_covered }} / {{ deliveries.building_count }}</b>. Проверка геометрии; дата, облачность и качество отдельно не подтверждены. Зона считается покрытой от 95% площади, здание — только целиком.</p>
    <details class="source-details"><summary>Полученные поставки · {{ deliveries.deliveries.length }}</summary>
    <div v-for="d in deliveries.deliveries" :key="d.id" class="delivery">
      <p class="supplier-meta"><b>{{ d.provider_name }}</b><template v-if="d.acquired_at"> · съёмка {{ date(d.acquired_at) }}</template> · {{ plural(d.files.length, ['файл', 'файла', 'файлов']) }}</p>
      <div class="mini-rows">
        <div v-for="f in d.files.filter(f => f.kind === 'raster')" :key="f.path">
          <span :title="f.path">{{ f.path.split('/').pop() }}<template v-if="f.status === 'georeferenced'"> · {{ number(f.resolution_m, 1) }} м · {{ km(f.area_km2) }}</template></span>
          <b v-if="f.zones?.length" class="zone-shares">
            <span v-for="z in f.zones" :key="z.zone_id" @mouseenter="state.hoverZoneId = z.zone_id" @mouseleave="state.hoverZoneId = null">{{ z.zone_id }} {{ percent(z.share, 0) }}</span>
          </b>
          <b v-else class="muted">{{ zonesLine(f) }}</b>
        </div>
      </div>
    </div>
    </details>
    <p class="source-line">Файлы из папки хранятся на сервере отдельно от расчёта. Растр сверяется с зонами по контуру; план и цены по ПП № 840 не меняются.</p>
  </section>
</template>
