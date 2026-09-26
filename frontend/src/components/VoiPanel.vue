<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Download, Info } from '@lucide/vue'
import { compactRub, plural, rub } from '../lib/format'
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
      || result.value.inputs.visit_cost_rub !== Number(visitCost.value)) {
    state.procurementOverlay = null
    return
  }
  const selected = new Set(result.value.plan.zone_ids)
  const zoneAssets = new Map(result.value.zones.map(zone => [zone.zone_id, zone.asset_ids]))
  state.procurementOverlay = state.analysis.zones
    .filter(zone => zone.kind !== 'broad' || selected.has(zone.id))
    .map(zone => ({ type: 'Feature', geometry: zone.geometry,
      properties: { id: zone.id, kind: zone.kind, active: selected.has(zone.id) ? 1 : 0,
        asset_ids: zoneAssets.get(zone.id) || [], hover: state.hoverZoneId === zone.id ? 1 : 0 } }))
}

async function load() {
  if (!props.active) { overlay(); return }
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
watch(() => [props.active, state.sceneId, state.region, state.deadline, state.mustCover, visitCost.value], () => {
  clearTimeout(timer)
  if (props.active) timer = setTimeout(load, 180)
  else { controller?.abort(); overlay() }
}, { immediate: true })
watch(() => state.analysis, overlay)
onBeforeUnmount(() => { clearTimeout(timer); controller?.abort(); state.procurementOverlay = null })

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
    <section class="block voi-intro">
      <span class="caption">ЭКОНОМИЧЕСКОЕ ПРАВИЛО</span>
      <h3>Стоит ли покупать снимок?</h3>
      <p>Сравниваем цену снимка с ожидаемой экономией решений о выезде к объектам.</p>
      <label for="visit-cost">Цена одного выезда, ₽ <span class="scenario-label">сценарий</span></label>
      <input id="visit-cost" v-model.number="visitCost" type="number" min="0" max="10000000" step="500" aria-label="Цена выезда" :aria-invalid="!validVisitCost" aria-describedby="visit-cost-hint" />
      <div class="voi-control"><span>Решение нужно через</span><BubbleTabs v-model="state.deadline" :options="deadlineOptions" size="sm" label="Срок решения" /></div>
      <label for="voi-must-cover">Обязательно проверить</label>
      <select id="voi-must-cover" v-model="mustCoverModel" aria-label="Обязательный объект">
        <option value="">Без условия</option>
        <option v-for="asset in state.analysis?.assets || []" :key="asset.id" :value="asset.id">{{ asset.name }}</option>
      </select>
      <p id="visit-cost-hint" class="voi-hint">Вероятность и V×q — из текущих объектов. Цена выезда, цена снимка и идеальность наблюдения — сценарии.</p>
      <p v-if="state.analysis?.chip.source_kind === 'live_sentinel1_rtc'" class="voi-hint">Для новой области калибровка вероятностей не проверена по ручной метке; оценка пользы особенно предварительна.</p>
    </section>

    <p v-if="loading" class="block" role="status">Пересчитываем все корзины…</p>
    <div v-if="error" class="block error-note" role="alert">{{ error }} <button @click="load">Повторить</button></div>
    <template v-if="result && !loading">
      <section class="block voi-result">
        <span class="caption">РЕШЕНИЕ ПРИ ЭТИХ ДОПУЩЕНИЯХ</span>
        <h3 v-if="result.plan.zone_ids.length">{{ plural(result.plan.zone_ids.length, ['зона', 'зоны', 'зон']) }} за {{ rub(result.plan.derived_budget_rub) }}</h3>
        <h3 v-else>Заказ не окупается</h3>
        <figure class="voi-chart" data-testid="voi-chart" aria-label="Сравнение пользы снимков и цены данных для выбранной корзины">
          <figcaption>Польза и цена выбранной корзины <small>₽ · один масштаб для обеих полос</small></figcaption>
          <div class="voi-chart-line"><span>Польза</span><b>{{ compactRub(result.plan.voi_rub) }}</b><div class="voi-track"><span class="voi-benefit-bar" :style="{ width: barWidth(result.plan.voi_rub, Math.max(result.plan.voi_rub, result.plan.derived_budget_rub)) }" /></div></div>
          <div class="voi-chart-line"><span>Цена данных</span><b>{{ compactRub(result.plan.derived_budget_rub) }}</b><div class="voi-track"><span class="voi-price-bar" :style="{ width: barWidth(result.plan.derived_budget_rub, Math.max(result.plan.voi_rub, result.plan.derived_budget_rub)) }" /></div></div>
        </figure>
        <p v-if="result.status === 'no_positive_net_basket' && state.mustCover">Обязательный объект нельзя покрыть с положительной чистой пользой при заданной цене выезда и сроке.</p>
        <p v-else-if="!result.plan.zone_ids.length">При выбранной цене выезда ни одна своевременная корзина не даёт положительной выгоды после цены снимков.</p>
        <div class="voi-totals">
          <div><span>Польза наблюдения</span><b>{{ rub(result.plan.voi_rub) }}</b></div>
          <div><span>Цена данных · выведенный бюджет</span><b>{{ rub(result.plan.derived_budget_rub) }}</b></div>
          <div class="voi-net"><span>Чистая польза после данных</span><b>{{ rub(result.plan.net_after_data_rub) }}</b></div>
        </div>
        <p class="voi-action">Ожидаемая стоимость решений о выездах: {{ compactRub(result.plan.action_cost_before_rub) }} без снимка → {{ compactRub(result.plan.action_cost_after_observation_rub) }} с идеальным снимком внутри заказа.</p>
        <p class="voi-action">Работа и обработка: {{ rub(result.plan.other_cost_rub) }} отдельно. Полная сценарная стоимость решения с данными: {{ rub(result.plan.decision_cost_rub) }}.</p>
      </section>

      <section class="block">
        <span class="caption">ПОЧЕМУ ЭТИ ЗОНЫ</span>
        <p v-if="chosen.length" class="voi-zone-guide">Нажмите зону, чтобы найти её на карте. Полосы сравнивают пользу и цену отдельного заказа; цена в корзине учитывает скидку.</p>
        <div v-if="!chosen.length" class="voi-hint">Выбранных зон нет. Измените цену выезда или срок, чтобы посмотреть другое решение.</div>
        <div class="voi-zones">
          <button v-for="zone in chosen" :key="zone.zone_id" class="voi-zone" @click="emit('zone', zone.zone_id)"
            @pointerenter="state.hoverZoneId = zone.zone_id" @pointerleave="state.hoverZoneId = null"
            @focus="state.hoverZoneId = zone.zone_id" @blur="state.hoverZoneId = null">
            <span class="voi-zone-main"><strong>{{ zoneName(zone) }}</strong><small>{{ plural(zone.asset_ids.length, ['объект', 'объекта', 'объектов']) }} · {{ reason(zone) }}</small>
              <span class="voi-zone-bars" aria-label="Польза и цена зоны отдельно">
                <span><small>Польза {{ rub(zone.voi_alone_rub) }}</small><span class="voi-track"><span class="voi-benefit-bar" :style="{ width: zoneWidth(zone, zone.voi_alone_rub) }" /></span></span>
                <span><small>Цена {{ rub(zone.price_alone_rub) }}</small><span class="voi-track"><span class="voi-price-bar" :style="{ width: zoneWidth(zone, zone.price_alone_rub) }" /></span></span>
              </span>
              <small>Цена в корзине со скидкой: {{ rub(lines.get(zone.candidate_id) ?? zone.price_alone_rub) }}</small>
            </span>
          </button>
        </div>
        <button v-if="rejected.length" class="voi-more" :aria-expanded="showRejected" @click="showRejected = !showRejected">{{ showRejected ? 'Скрыть' : 'Показать' }} остальные зоны ({{ rejected.length }})</button>
        <div v-if="showRejected" class="voi-zones">
          <button v-for="zone in rejected" :key="zone.zone_id" class="voi-zone" @click="emit('zone', zone.zone_id)"
            @pointerenter="state.hoverZoneId = zone.zone_id" @pointerleave="state.hoverZoneId = null"
            @focus="state.hoverZoneId = zone.zone_id" @blur="state.hoverZoneId = null">
            <span class="voi-zone-main"><strong>{{ zoneName(zone) }}</strong><small>{{ reason(zone) }}</small>
              <span class="voi-zone-bars" aria-label="Польза и цена зоны отдельно">
                <span><small>Польза {{ rub(zone.voi_alone_rub) }}</small><span class="voi-track"><span class="voi-benefit-bar" :style="{ width: zoneWidth(zone, zone.voi_alone_rub) }" /></span></span>
                <span><small>Цена {{ rub(zone.price_alone_rub) }}</small><span class="voi-track"><span class="voi-price-bar" :style="{ width: zoneWidth(zone, zone.price_alone_rub) }" /></span></span>
              </span>
              <small>Итог отдельного заказа: {{ rub(zone.net_alone_rub) }}</small>
            </span>
          </button>
        </div>
      </section>

      <section class="block voi-method">
        <details><summary>Как считаем пользу</summary>
          <p>Для объекта L = V×q. Без снимка выбираем меньшую стоимость: выезд C или ожидаемый пропуск p×L. Идеальный снимок оставляет ожидаемую стоимость p×min(C,L). Разница — ценность информации объекта. Для зоны складываем её по объектам без повторного счёта пересечений.</p>
          <p>Сервис перебирает все допустимые корзины и максимизирует ценность минус точную цену данных с групповой скидкой. Выбранная цена становится бюджетом. Цена выезда, V/q, тариф, доставка и идеальность снимка — сценарии. Цена из SkyFi и реальный ущерб этим расчётом не подтверждены.</p>
        </details>
        <a class="voi-export" :href="exportHref" download><Download :size="14" />Скачать расчёт зон и допущения</a>
        <p class="voi-caution"><Info :size="13" />Польза здесь означает меньше затрат на решение о выезде, а не предотвращённый ущерб от паводка.</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.voi h3 { margin: 6px 0 8px; font-size: 18px; letter-spacing: -.3px; }
.voi p { font-size: 12px; line-height: 1.5; }
.voi-intro label { display: block; margin: 14px 0 5px; font-size: 12px; font-weight: 650; }
.voi-intro select { width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--background); color: var(--foreground); font: inherit; font-size: 12px; }
.voi-control { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 12px; font-size: 12px; }
.voi-control :deep(.bubble-tabs) { flex-shrink: 0; }
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
