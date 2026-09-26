<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Download, SlidersHorizontal } from '@lucide/vue'
import { state } from '../store'
import { compactRub, number, percent, rub } from '../lib/format'

const selectedId = ref(null)
const drafts = reactive({})
const baseRate = ref(788.64)
const processing = ref('default')
const usage = ref('internal')
const result = ref(null)
const submitted = ref(null)
const busy = ref(false)
const error = ref('')
const assets = computed(() => state.analysis?.chip?.chip_id === state.sceneId ? state.analysis.assets : [])
const selected = computed(() => assets.value.find(asset => asset.id === selectedId.value))
const names = computed(() => new Map(assets.value.map(asset => [asset.id, asset.name])))
const assetChanges = computed(() => Object.entries(drafts).flatMap(([assetId, row]) => {
  const original = assets.value.find(asset => asset.id === assetId)
  if (!original) return []
  const change = { asset_id: assetId }
  for (const [formKey, apiKey, originalKey] of [
    ['p', 'p_flood', 'p_flood'], ['value', 'value_rub', 'value_rub'], ['q', 'vulnerability', 'vulnerability'],
  ]) {
    if (row[formKey] !== '' && Number(row[formKey]) !== original[originalKey]) change[apiKey] = Number(row[formKey])
  }
  return Object.keys(change).length > 1 ? [change] : []
}))

function select(id) {
  selectedId.value = id
  if (drafts[id]) return
  const asset = assets.value.find(item => item.id === id)
  if (asset) drafts[id] = {
    p: asset.p_flood == null ? '' : String(asset.p_flood),
    value: String(asset.value_rub), q: String(asset.vulnerability),
  }
}
function reset() {
  for (const key of Object.keys(drafts)) delete drafts[key]
  selectedId.value = null
  result.value = null
  submitted.value = null
  error.value = ''
  if (assets.value.length) select(assets.value[0].id)
}
watch(() => state.sceneId, reset)
watch(() => state.analysis?.region?.bounds?.join(','), (current, previous) => {
  if (current !== previous) reset()
})
watch(assets, value => {
  if (value.length && !value.some(asset => asset.id === selectedId.value)) reset()
}, { immediate: true })
watch([drafts, baseRate, processing, usage,
  () => state.budget, () => state.deadline, () => state.priority, () => state.mustCover,
  () => state.region, () => state.analysis], () => { result.value = null; submitted.value = null; ++revision }, { deep: true })

let revision = 0
const REASONS = { selected: 'в заказе', over_budget: 'превышает бюджет', late: 'позже срока',
  nothing_new: 'не добавляет охвата', cheaper_equal: 'равный результат дороже',
  plan_infeasible: 'условия плана невыполнимы' }

function payload() {
  return {
    scene_id: state.sceneId, bbox: state.region, target_ids: state.pointTargetIds,
    budget_rub: state.budget, deadline_hours: state.deadline,
    priority: state.priority, must_cover: state.mustCover,
    base_rate_rub_km2: Number(baseRate.value),
    processing_level: processing.value, usage_type: usage.value,
    asset_changes: assetChanges.value,
  }
}
async function calculate() {
  const run = ++revision
  busy.value = true
  error.value = ''
  result.value = null
  try {
    const input = payload()
    if (!Number.isFinite(input.base_rate_rub_km2) || input.base_rate_rub_km2 < 0 || input.base_rate_rub_km2 > 1_000_000)
      throw new Error('Базовая ставка должна быть от 0 до 1 000 000 ₽/км².')
    for (const item of input.asset_changes) {
      if (item.p_flood != null && (item.p_flood < 0 || item.p_flood > 1) ||
          item.vulnerability != null && (item.vulnerability < 0 || item.vulnerability > 1) ||
          item.value_rub != null && (item.value_rub < 0 || item.value_rub > 1_000_000_000))
        throw new Error('Для объекта p и q должны быть от 0 до 1, V — от 0 до 1 млрд ₽.')
    }
    const response = await fetch('/api/economics/scenario', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Ошибка расчёта сценария')
    if (run === revision) { submitted.value = input; result.value = data }
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
async function download() {
  if (!submitted.value) return
  busy.value = true
  error.value = ''
  try {
    const response = await fetch('/api/economics/scenario/export', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(submitted.value),
    })
    if (!response.ok) throw new Error('Не удалось выгрузить сценарий')
    const href = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = href
    link.download = `floodvalue-economic-scenario-${state.sceneId}.zip`
    link.click()
    setTimeout(() => URL.revokeObjectURL(href), 1000)
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
const zoneList = ids => ids.length ? ids.join(', ') : 'нет заказа'
</script>

<template>
  <details class="disclosure economic-scenario">
    <summary><SlidersHorizontal :size="16" /> Свой экономический сценарий</summary>
    <div class="disclosure-body">
      <p class="panel-note">Поменяйте допущения и пересчитайте A/B/C. Исходные 10 объектов, вероятностная карта и основной ZIP кейса сохраняются.</p>
      <div class="economic-controls">
        <label>Базовая ставка B, ₽/км²
          <input v-model.number="baseRate" type="number" min="0" max="1000000" step="0.01" />
        </label>
        <label>Обработка O
          <select v-model="processing">
            <option value="default">Как в кейсе: L2 оптика, L1 радар</option>
            <option value="L0">L0 · 1</option><option value="L1">L1 · 1</option><option value="L2">L2 · 1,2</option>
          </select>
        </label>
        <label>Использование P
          <select v-model="usage">
            <option value="internal">Внутреннее · 1</option>
            <option value="limited">Ограниченное · 1,2</option>
            <option value="unrestricted">Неограниченное · 1,5</option>
          </select>
        </label>
      </div>
      <p class="panel-note">Стоимость объекта V и уязвимость q — учебные допущения. p ниже меняет только оценку ущерба объекта, а не пиксели карты модели.</p>
      <label class="economic-asset-select">Объект для изменения p · V · q
        <select :value="selectedId || ''" @change="select($event.target.value)">
          <option v-for="asset in assets" :key="asset.id" :value="asset.id">{{ asset.name }} · {{ asset.id }}</option>
        </select>
      </label>
      <div v-if="selected && drafts[selectedId]" class="economic-controls">
        <label>p · вероятность воды
          <input v-model="drafts[selectedId].p" type="number" min="0" max="1" step="0.0001" :placeholder="selected.p_flood == null ? 'нет данных' : String(selected.p_flood)" />
        </label>
        <label>V · стоимость, ₽
          <input v-model="drafts[selectedId].value" type="number" min="0" max="1000000000" step="0.01" />
        </label>
        <label>q · уязвимость
          <input v-model="drafts[selectedId].q" type="number" min="0" max="1" step="0.0001" />
        </label>
      </div>
      <p class="panel-note">Изменено объектов: {{ assetChanges.length }}. Выберите другой объект, чтобы изменить несколько. Бюджет {{ rub(state.budget) }}, срок {{ state.deadline }} ч и цель берутся из плана выше.</p>
      <button class="primary-button" :disabled="busy || !assets.length" @click="calculate">{{ busy ? 'Считаем…' : 'Пересчитать A/B/C' }}</button>
      <p v-if="error" class="error-note" role="alert">{{ error }}</p>
      <template v-if="result">
        <div class="economic-outcome">
          <span>Ожидаемый ущерб портфеля</span>
          <strong>{{ compactRub(result.case.expected_loss_rub) }} → {{ compactRub(result.scenario.expected_loss_rub) }}</strong>
        </div>
        <div class="economic-results">
          <div v-for="key in ['A', 'B', 'C']" :key="key" class="economic-result">
            <strong>{{ key }} · {{ { A: 'Не покупать', B: 'Вся область', C: 'Оптимальный заказ' }[key] }}</strong>
            <div><span>Цена снимков</span><span>{{ compactRub(result.case.strategies[key].data_cost_rub) }} → {{ compactRub(result.scenario.strategies[key].data_cost_rub) }}</span></div>
            <div><span>Решение целиком</span><span>{{ compactRub(result.case.strategies[key].decision_cost_rub) }} → {{ compactRub(result.scenario.strategies[key].decision_cost_rub) }}</span></div>
            <div><span>Ущерб в зонах</span><span>{{ compactRub(result.case.strategies[key].covered_expected_loss_rub) }} → {{ compactRub(result.scenario.strategies[key].covered_expected_loss_rub) }}</span></div>
            <div><span>Доля охвата</span><span>{{ percent(result.case.strategies[key].coverage_share) }} → {{ percent(result.scenario.strategies[key].coverage_share) }}</span></div>
            <small v-if="key !== 'A'">{{ zoneList(result.scenario.strategies[key].zone_ids) }} · {{ result.scenario.strategies[key].budget_feasible ? 'в бюджете' : 'выше бюджета' }}{{ result.scenario.strategies[key].timely ? '' : ' · позже срока' }}</small>
            <small v-if="key === 'C' && !result.scenario.strategies.C.constraints_feasible">Обязательное условие не выполнить при этом бюджете и сроке.</small>
          </div>
        </div>
        <p class="panel-note">Стрелка: кейс → ваш сценарий. Для C корзина {{ result.changes.basket_changed ? 'изменилась' : 'не изменилась' }}{{ result.changes.basket_changed ? ` (${zoneList(result.case.strategies.C.zone_ids)} → ${zoneList(result.scenario.strategies.C.zone_ids)})` : '' }}. Покрытый ожидаемый ущерб — приоритет наблюдения, а не предотвращённый ущерб.</p>
        <details class="economic-breakdown"><summary>Расчёт по объектам и цена заказа</summary>
          <div v-for="asset in result.assets" :key="asset.asset_id" class="economic-breakdown-row">
            <span>{{ asset.rank ? `${asset.rank}. ` : '' }}{{ names.get(asset.asset_id) || asset.asset_id }}</span>
            <span>p {{ asset.p_flood == null ? 'н/д' : number(asset.p_flood, 4) }} × V {{ rub(asset.value_rub) }} × q {{ number(asset.vulnerability, 4) }} = {{ compactRub(asset.expected_loss_rub) }}</span>
          </div>
          <p class="panel-note">C · цена по формуле {{ result.scenario.strategies.C.quote.formula || 'B × K × O × P × T × R' }}:</p>
          <div v-for="line in result.scenario.strategies.C.quote.lines" :key="line.candidate_id" class="economic-breakdown-row">
            <span>{{ line.candidate_id }} · {{ number(line.area_km2, 3) }} км² → {{ rub(line.cost_rub) }}</span>
            <span>B {{ rub(line.base_rate_rub_km2) }}/км² × O {{ number(line.processing_coef, 2) }} × P {{ number(line.usage_coef, 2) }} × T {{ number(line.freshness_coef, 2) }} × R {{ number(line.discount_coef, 4) }}; округление до копеек</span>
          </div>
          <p class="panel-note">Зоны C:</p>
          <div v-for="row in result.scenario.zone_decisions" :key="row.zone_id" class="economic-breakdown-row">
            <span>{{ row.zone_id }} · {{ REASONS[row.reason] || row.reason }}</span>
            <span>Отдельно {{ rub(row.price_alone_rub) }} · ущерб {{ compactRub(row.expected_loss_rub) }}</span>
          </div>
          <p class="panel-note">Полные строки цены и параметры сохранены в отдельном ZIP сценария.</p>
        </details>
        <button class="primary-button" :disabled="busy" @click="download"><Download :size="16" /> Скачать отдельный сценарий ZIP</button>
      </template>
    </div>
  </details>
</template>
