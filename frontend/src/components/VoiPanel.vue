<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { compactRub, plural, rub } from '../lib/format'
import PlanCoverage from './PlanCoverage.vue'
import Money from './Money.vue'
import InformationValueResult from './InformationValueResult.vue'
import BubbleTabs from './BubbleTabs.vue'
import { state } from '../store'

const props = defineProps({ active: { type: Boolean, required: true } })
const emit = defineEmits(['zone'])
const visitCost = ref(10000)
const result = ref(null)
const loading = ref(false)
const error = ref('')
const showRejected = ref(false)
const validVisitCost = computed(() => visitCost.value !== '' && Number.isFinite(Number(visitCost.value))
  && Number(visitCost.value) >= 0 && Number(visitCost.value) <= 10000000)
const mustCoverModel = computed({ get: () => state.mustCover || '', set: value => { state.mustCover = value || null } })
const deadlineOptions = [4, 8, 12].map(value => ({ value, label: value + ' ч' }))
let timer
let controller

const params = computed(() => {
  const query = new URLSearchParams({ scene_id: state.sceneId, visit_cost: String(visitCost.value),
    deadline: String(state.deadline) })
  if (state.region) query.set('bbox', state.region.join(','))
  if (state.mustCover) query.set('must_cover', state.mustCover)
  if (state.pointTargetIds !== null) query.set('target_ids', state.pointTargetIds.join(','))
  return query
})
const exportHref = computed(() => '/api/voi/export?' + params.value)
const chosen = computed(() => result.value?.zones.filter(zone => zone.selected) || [])
const rejected = computed(() => result.value?.zones.filter(zone => !zone.selected) || [])
const lines = computed(() => new Map((result.value?.plan.quote.lines || []).map(line => [line.candidate_id, line.cost_rub])))
const barWidth = (value, maximum) => `${maximum ? Math.max(value > 0 ? 2 : 0, value / maximum * 100) : 0}%`
const zoneWidth = (zone, value) => barWidth(value, Math.max(zone.voi_alone_rub, zone.price_alone_rub))

function overlay() {
  if (!props.active || !result.value || result.value.scene_id !== state.sceneId || !state.analysis
      || JSON.stringify(result.value.inputs.bbox) !== JSON.stringify(state.region)
      || result.value.inputs.deadline_hours !== state.deadline
      || result.value.inputs.must_cover !== state.mustCover
      || JSON.stringify(result.value.inputs.target_ids ?? null) !== JSON.stringify(state.pointTargetIds)
      || result.value.inputs.visit_cost_rub !== Number(visitCost.value)) {
    state.procurementOverlay = null
    state.voiExportHref = null
    return
  }
  state.voiExportHref = exportHref.value
  const selected = new Set(result.value.plan.zone_ids)
  const zoneAssets = new Map(result.value.zones.map(zone => [zone.zone_id, zone.asset_ids]))
  state.procurementOverlay = state.analysis.zones
    .filter(zone => zone.kind !== 'broad' || selected.has(zone.id))
    .map(zone => ({ type: 'Feature', geometry: zone.geometry,
      properties: { id: zone.id, kind: zone.kind, active: selected.has(zone.id) ? 1 : 0,
        asset_ids: zoneAssets.get(zone.id) || [], hover: state.hoverZoneId === zone.id ? 1 : 0 } }))
}

async function load() {
  if (!props.active || state.pointTargetIds?.length === 0) { result.value = null; overlay(); return }
  controller?.abort()
  if (!validVisitCost.value) {
    error.value = 'Введите стоимость выезда от 0 до 10 млн ₽.'
    result.value = null
    loading.value = false
    overlay()
    return
  }
  const current = new AbortController()
  controller = current
  loading.value = true
  error.value = ''
  state.procurementOverlay = null
  try {
    const response = await fetch('/api/voi?' + params.value, { signal: current.signal })
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Ошибка расчёта')
    result.value = data
    overlay()
  } catch (cause) {
    if (cause.name !== 'AbortError') { error.value = cause.message; result.value = null; overlay() }
  } finally {
    if (controller === current) loading.value = false
  }
}
watch(() => [props.active, state.sceneId, state.region, state.deadline, state.mustCover, visitCost.value, state.pointTargetIds], () => {
  clearTimeout(timer)
  state.voiExportHref = null
  if (props.active) timer = setTimeout(load, 180)
  else { controller?.abort(); overlay() }
}, { immediate: true })
watch(() => state.analysis, overlay)
onBeforeUnmount(() => { clearTimeout(timer); controller?.abort(); state.procurementOverlay = null; state.voiExportHref = null })

const coverage = computed(()=>chosen.value.map(z=>({id:z.zone_id,name:zoneName(z),count:z.asset_ids.length,objects:z.asset_ids.map(id=>state.analysis?.assets.find(a=>a.id===id)).filter(Boolean),area:z.area_km2,cost:lines.value.get(z.candidate_id)??z.price_alone_rub,hours:z.delivery_hours,note:reason(z)})))
function zoneName(zone) {
  if (zone.kind === 'broad') return 'Вся область'
  if (zone.kind === 'uncertainty') return zone.name
  const asset = state.analysis?.assets.find(item => item.id === 'A' + zone.zone_id.slice(1))
  return asset ? `Вокруг «${asset.name}»` : zone.name
}
function reason(zone) {
  if (zone.reason === 'late') return 'не успеет к решению'
  if (zone.reason === 'no_new_information') return 'не добавит проверяемых объектов'
  if (zone.reason === 'selected') return 'в экономической корзине'
  return 'другая корзина даёт больше чистой пользы'
}
</script>

<template>
  <div class="voi" data-testid="voi-panel">
    <section v-if="state.planStep==='plan'" class="block voi-intro">
      <h3>Окупится ли снимок?</h3>
      <label for="visit-cost">Цена выезда к одному объекту, ₽</label>
      <input id="visit-cost" v-model.number="visitCost" type="number" min="0" max="10000000" step="500" aria-label="Цена выезда" :aria-invalid="!validVisitCost" />
      <div class="voi-control"><span>Получить за</span><BubbleTabs v-model="state.deadline" :options="deadlineOptions" size="sm" label="Срок решения" /></div>
    </section>
    <p v-if="loading" class="block" role="status">Считаем…</p>
    <div v-if="error" class="block error-note" role="alert">{{ error }} <button @click="load">Повторить</button></div>
    <div v-if="result" class="voi-output" :class="{ dimmed: loading }" :inert="loading || undefined">
      <InformationValueResult v-if="state.planStep==='plan'" :result="result" :required-id="state.mustCover" />
      <PlanCoverage v-if="state.planStep==='plan'" :zones="coverage" :busy="loading" :words="['точка','точки','точек']" @zone="id=>emit('zone',id)" />
      <div v-if="state.planStep==='details'">
      <details class="disclosure voi-settings"><summary>Условия расчёта</summary><div class="disclosure-body voi-intro">
        <label for="voi-must-cover">Обязательно проверить</label>
        <select id="voi-must-cover" v-model="mustCoverModel" aria-label="Обязательный объект"><option value="">Без условия</option><option v-for="asset in state.analysis?.assets || []" :key="asset.id" :value="asset.id">{{ asset.name }}</option></select>
        <p class="panel-note">Цена выезда, тариф и ущерб — допущения. Снимок считается идеальным: точно показывает воду у объекта.</p>
        <p v-if="['live_sentinel1_rtc', 'uploaded_sentinel1_geotiff'].includes(state.analysis?.chip.source_kind)" class="panel-note">Точность вероятностей на этой новой территории ещё не проверена.</p>
      </div></details>
      <details class="disclosure"><summary>Выбранные зоны · {{ chosen.length }}</summary><div class="disclosure-body">
        <p class="panel-note">Нажмите на зону, чтобы найти её на карте.</p>
        <div v-if="!chosen.length" class="panel-note">Нет выгодных зон при этих условиях.</div>
        <div class="voi-zones">
          <button v-for="zone in chosen" :key="zone.zone_id" class="voi-zone" :aria-pressed="state.selectedZoneId === zone.zone_id" @click="emit('zone', zone.zone_id)" @pointerenter="state.hoverZoneId = zone.zone_id" @pointerleave="state.hoverZoneId = null" @focus="state.hoverZoneId = zone.zone_id" @blur="state.hoverZoneId = null">
            <span class="voi-zone-main"><strong>{{ zoneName(zone) }}</strong><small>{{ plural(zone.asset_ids.length, ['объект', 'объекта', 'объектов']) }}</small>
              <span class="voi-zone-bars" aria-label="Польза и цена зоны отдельно">
                <span><small>Польза {{ rub(zone.voi_alone_rub) }}</small><span class="voi-track"><span class="voi-benefit-bar" :style="{ width: zoneWidth(zone, zone.voi_alone_rub) }" /></span></span>
                <span><small>Цена {{ rub(zone.price_alone_rub) }}</small><span class="voi-track"><span class="voi-price-bar" :style="{ width: zoneWidth(zone, zone.price_alone_rub) }" /></span></span>
              </span><small>Вместе с другими: {{ rub(lines.get(zone.candidate_id) ?? zone.price_alone_rub) }}</small>
            </span>
          </button>
        </div>
        <button v-if="rejected.length" class="voi-more" :aria-expanded="showRejected" @click="showRejected = !showRejected">{{ showRejected ? 'Скрыть' : 'Показать' }} остальные зоны ({{ rejected.length }})</button>
        <div v-if="showRejected" class="voi-zones"><button v-for="zone in rejected" :key="zone.zone_id" class="voi-zone" :aria-pressed="state.selectedZoneId === zone.zone_id" @click="emit('zone', zone.zone_id)"><span class="voi-zone-main"><strong>{{ zoneName(zone) }}</strong><small>{{ reason(zone) }}</small><small>Польза {{ rub(zone.voi_alone_rub) }} · цена {{ rub(zone.price_alone_rub) }}</small></span></button></div>
      </div></details>
      <details class="disclosure"><summary>Расходы до и после</summary><div class="disclosure-body">
        <div class="voi-totals"><div><span>Решения без снимков</span><b><Money :value="result.plan.action_cost_before_rub" /></b></div><div><span>Решения со снимками</span><b><Money :value="result.plan.action_cost_after_observation_rub" /></b></div><div><span>Снимки и обработка</span><b><Money :value="result.plan.decision_cost_rub" /></b></div></div>
        <p class="panel-note">В расходах на решения учтены выезды и ожидаемые потери от пропуска объекта. Подробный расчёт — в выгрузке.</p>
      </div></details>
      </div>
    </div>
  </div>
</template>

<style>
.voi h3 { margin: 6px 0 8px; font-size: 18px; letter-spacing: -.3px; }
.voi p { font-size: 12px; line-height: 1.5; }
.voi-intro label { display: block; margin: 14px 0 5px; font-size: 12px; font-weight: 650; }
.voi-intro select { width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--background); color: var(--foreground); font: inherit; font-size: 12px; }
.voi-control { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 12px; font-size: 12px; }
.voi-control .bubble-tabs { flex-shrink: 0; }
.scenario-label { margin-left: 4px; color: var(--muted-foreground); font-size: 10px; font-weight: 400; }
.voi-intro input { width: 100%; padding: 9px; border: 1px solid var(--border); border-radius: 8px; background: var(--background); color: var(--foreground); font: inherit; }
.voi-hint { color: var(--muted-foreground); font-size: 11px !important; }
.voi-result { border-left: 3px solid var(--reference); }
.voi-chart { margin: 13px 0 7px; padding: 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); }
.voi-chart figcaption { margin-bottom: 9px; font-size: 12px; font-weight: 700; }
.voi-chart figcaption small { display: block; margin-top: 2px; color: var(--muted-foreground); font-size: 10px; font-weight: 400; }
.voi-chart-line { display: grid; grid-template-columns: 72px 1fr; gap: 4px 9px; margin-top: 8px; align-items: baseline; font-size: 11px; }
.voi-chart-line b { text-align: right; font-variant-numeric: tabular-nums; }
.voi-chart-line .voi-track { grid-column: 1 / -1; }
.voi-track { display: block; height: 7px; border-radius: 8px; background: var(--border); overflow: hidden; }
.voi-track > span { display: block; height: 100%; border-radius: inherit; }
.voi-benefit-bar { background: var(--reference); }
.voi-price-bar { background: #a67733; }
.voi-totals { display: grid; gap: 1px; margin: 12px 0; }
.voi-totals div { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0; border-bottom: 1px solid var(--border); font-size: 11px; }
.voi-totals b { font-variant-numeric: tabular-nums; white-space: nowrap; }
.voi-net { font-weight: 700; }
.voi-action { color: var(--muted-foreground); margin: 7px 0; }
.voi-zone-guide { margin: 8px 0; color: var(--muted-foreground); font-size: 11px !important; }
.voi-zones { display: grid; gap: 6px; margin-top: 10px; }
.voi-zone { width: 100%; display: block; padding: 10px; text-align: left; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); color: var(--foreground); font: inherit; cursor: pointer; }
.voi-zone:hover, .voi-zone:focus-visible { border-color: var(--reference); }
.voi-zone-main { display: grid; gap: 5px; min-width: 0; }
.voi-zone strong { font-size: 12px; }
.voi-zone small { color: var(--muted-foreground); font-size: 10px; line-height: 1.35; }
.voi-zone-bars { display: grid; gap: 5px; margin: 5px 0; }
.voi-zone-bars > span { display: grid; grid-template-columns: 112px 1fr; align-items: center; gap: 7px; }
.voi-zone-bars small { white-space: nowrap; font-variant-numeric: tabular-nums; }
.voi-more { margin-top: 12px; padding: 0; border: 0; background: transparent; color: var(--reference); font: inherit; font-size: 11px; cursor: pointer; }
.voi-method summary { cursor: pointer; font-size: 12px; }
.voi-method details p { color: var(--muted-foreground); font-size: 11px; }
.voi-export { display: inline-flex; align-items: center; gap: 7px; margin-top: 15px; color: var(--reference); font-size: 12px; }
.voi-caution { display: flex; gap: 6px; color: var(--muted-foreground); font-size: 11px !important; }
</style>

<style>
.voi-result { border-left: 0; }
.voi-chart { padding: 0; border: 0; background: none; }
.voi-chart-line { grid-template-columns: 1fr auto; font-size: 13px; margin-top: 14px; }
.voi-chart figcaption { font-size: 13px; }
.voi-chart figcaption small { display: inline; margin-left: 8px; font-size: 12px; }
.voi-zone { background: none; border: 0; border-bottom: 1px solid var(--border); border-radius: 0; padding: 12px 0; }
.voi-zone small, .voi-totals div, .voi-more { font-size: 12px; }
.voi-zone-bars > span { grid-template-columns: 135px 1fr; }
.voi-result .panel-note { font-size: 12px; }
.voi-export { margin: 16px 0; }
</style>
