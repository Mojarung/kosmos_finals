<script setup>
import { computed, nextTick, ref, toRaw, watch } from 'vue'
import { Building2, Download, Upload, MapPin } from '@lucide/vue'
import { buildings, selectedBuilding, topBuildings, loadBuildings, openBuildingDemo, importBuildings, updateRates, updateBuildingType, exportBuildings, buildingStatus } from '../lib/buildings'
import { planBuilding } from '../lib/buildingPlans'

const emit = defineEmits(['building'])
const fileInput = ref(null)
const filter = ref('all')
const listMode = ref('top')
const card = ref(null)
const search = ref('')
const searchOpen = ref(false)
const limit = ref(40)
const draft = ref({})
const rateError = ref('')
const data = computed(() => buildings.data)
const selected = computed(() => selectedBuilding.value?.properties)
const money = value => value == null ? 'н/д' : new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(value)
const amount = p => p.expected_loss_rub ?? p.conditional_damage_rub
const amountLabel = p => p.expected_loss_rub == null ? 'при затоплении' : p.synthetic_cost ? 'условная цена' : 'сценарий'
const number = (value, digits = 1) => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(value)
const percent = value => value == null ? 'нет данных' : number(value * 100) + '%'
const dirtyRates = computed(() => data.value && Object.entries(draft.value).some(([key, value]) => value.cost_rub_m2 !== data.value.rates[key].cost_rub_m2 || value.damage_ratio !== data.value.rates[key].damage_ratio * 100))
const selectedDraft = computed(() => draft.value[selected.value?.building_type])
const selectedRateDirty = computed(() => selectedDraft.value && rateDirty(selectedDraft.value, data.value?.rates[selected.value.building_type]))
const selectedTypeCount = computed(() => data.value?.features.filter(f => f.properties.building_type === selected.value?.building_type).length || 0)
function rateDirty(row, rate) {
  return rate && (row.cost_rub_m2 !== rate.cost_rub_m2 || row.damage_ratio !== rate.damage_ratio * 100)
}
function rateDraft(rates) {
  return Object.fromEntries(Object.entries(rates).map(([key, r]) => [key, { cost_rub_m2: r.cost_rub_m2, damage_ratio: r.damage_ratio * 100 }]))
}
watch(() => [data.value?.catalog_id, data.value?.rates], ([catalogId, rates], [oldCatalog, oldRates] = []) => {
  if (!rates) { draft.value = {}; return }
  const next = rateDraft(rates)
  // Saving one type must preserve unfinished edits for other types in this scene.
  if (catalogId === oldCatalog) {
    for (const [key, row] of Object.entries(draft.value)) {
      if (rateDirty(row, oldRates?.[key])) next[key] = row
    }
  }
  draft.value = next
}, { immediate: true })
watch([filter, search, () => data.value?.catalog_id], () => { limit.value = 40 })
watch(() => buildings.selectedId, async id => {
  if (id) { await nextTick(); card.value?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }
})
const filtered = computed(() => (data.value?.features || []).filter(({ properties: p }) =>
  (filter.value === 'all' || filter.value === 'water' && p.water_fraction > 0 || filter.value === 'unknown' && p.building_type === 'unknown' || filter.value === 'missing' && p.expected_loss_rub == null)
  && `${p.name} ${p.id} ${p.type_label}`.toLowerCase().includes(search.value.toLowerCase()))
  .sort((a, b) => (a.properties.risk_rank ?? Infinity) - (b.properties.risk_rank ?? Infinity)))
const topMode = computed(() => listMode.value === 'top' && filter.value === 'all' && !search.value)
const visibleBuildings = computed(() => topMode.value ? topBuildings.value : filtered.value.slice(0, limit.value))
const additional = computed(() => selected.value && !data.value?.top_building_ids.includes(selected.value.id))

function choose(feature) {
  buildings.selectedId = feature.id
  emit('building', feature)
}
async function upload(event) {
  const file = event.target.files?.[0]
  if (file) await importBuildings(file)
  event.target.value = ''
}
async function applyRates(keys = Object.keys(draft.value)) {
  rateError.value = ''
  const rates = { ...buildings.applied.rate_overrides }
  for (const key of keys) {
    const row = draft.value[key]
    if (row.cost_rub_m2 === '' || row.damage_ratio === '' || !Number.isFinite(Number(row.cost_rub_m2)) || !Number.isFinite(Number(row.damage_ratio)) || row.cost_rub_m2 < 0 || row.cost_rub_m2 > 10000000 || row.damage_ratio < 0 || row.damage_ratio > 100) {
      rateError.value = 'Ставка: от 0 до 10 000 000 ₽/м². Повреждение: от 0 до 100%.'; return
    }
    rates[key] = { cost_rub_m2: Number(row.cost_rub_m2), damage_ratio: Number(row.damage_ratio) / 100 }
  }
  await updateRates(rates)
}
async function resetRates() {
  rateError.value = ''
  const result = await updateRates({})
  if (result && result === toRaw(data.value)) draft.value = rateDraft(result.rates)
}
</script>

<template>
  <section class="buildings-view" aria-label="Оценка зданий" :aria-busy="buildings.busy">
    <input ref="fileInput" type="file" accept=".geojson,.json,application/geo+json" hidden aria-label="Загрузить контуры GeoJSON" @change="upload" />
    <p v-if="buildings.busy" role="status" class="building-note">{{ buildings.progress === 100 ? 'Считаем стоимость и ущерб…' : buildings.progress == null ? 'Подключаем загрузку зданий…' : `Загружено ${buildings.progress}% области. Собираем контуры зданий…` }}</p>
    <div v-if="buildings.error" role="alert" class="error-note">{{ buildings.error }}<div class="building-actions"><button class="ghost-button" :disabled="buildings.busy" @click="loadBuildings()">Повторить загрузку</button><button class="ghost-button" @click="openBuildingDemo">Пример: Пайпорта</button></div></div>
    <p v-if="data && !data.features.length" class="building-note" role="status">В OSM для этой области не найдено контуров зданий. Можно загрузить свой GeoJSON или перейти к точкам.</p>
    <template v-if="data">
      <template v-if="!selected">
        <div class="building-title"><Building2 :size="19" /><h2>Здания</h2><span class="building-count">{{ data.summary.count }}</span></div>
        <div class="building-summary" data-testid="building-summary">
          <span>{{ data.summary.expected_loss_rub == null ? 'Потери при затоплении · сценарий' : 'Ущерб по снимку · сценарий' }}</span>
          <strong>{{ money(data.summary.expected_loss_rub ?? data.summary.conditional_damage_rub ?? 0) }}</strong>
          <span>{{ data.summary.estimated }} в расчёте · {{ data.summary.synthetic_cost || 0 }} с условной ценой</span>
        </div>
        <div class="building-list-mode" role="group" aria-label="Список зданий">
          <button class="ghost-button" :aria-pressed="topMode" @click="listMode = 'top'; filter = 'all'; search = ''">Топ‑10 по риску</button>
          <button class="ghost-button" :aria-pressed="!topMode" @click="listMode = 'all'">Все здания</button>
          <button class="ghost-button search-toggle" :aria-expanded="searchOpen" @click="searchOpen = !searchOpen">Поиск</button>
        </div>
        <div v-if="searchOpen || search || filter !== 'all'" class="building-filters">
          <input v-model="search" type="search" placeholder="Название, тип или ID" aria-label="Поиск здания" />
          <select v-model="filter" aria-label="Фильтр зданий"><option value="all">Все типы</option><option value="water">Под маской воды</option><option value="unknown">Условная цена</option><option value="missing">Требуют проверки</option></select>
        </div>
        <div class="building-list" data-testid="building-top-list">
          <button v-for="feature in visibleBuildings" :key="feature.id" class="building-row" :data-building-id="feature.id" @click="choose(feature)">
            <b class="building-rank">{{ feature.properties.risk_rank || '·' }}</b>
            <span class="building-row-main"><strong>{{ feature.properties.tags.name || (feature.properties.building_type === 'unknown' ? 'Здание' : feature.properties.type_label) }}</strong><small>{{ number(feature.properties.area_m2) }} м² · сигнал {{ percent(feature.properties.water_probability_proxy) }}</small></span>
            <span class="building-row-amount">{{ money(amount(feature.properties)) }}<small>{{ amountLabel(feature.properties) }}</small></span>
          </button>
        </div>
        <p v-if="!visibleBuildings.length" class="building-note">{{ data.summary.count ? 'Нет зданий по выбранному фильтру. Откройте все здания.' : 'Контуры в этой области не найдены.' }}</p>
        <button v-if="!topMode && filtered.length > limit" class="ghost-button" @click="limit += 40">Показать ещё 40</button>
        <p class="building-note">Риск — по сигналу воды. Цены и ущерб сценарные.</p>
      </template>

      <article v-else ref="card" class="building-card" data-testid="building-card">
        <button class="ghost-button building-back" aria-label="Закрыть карточку здания" @click="buildings.selectedId = null">← Все здания</button>
        <div class="building-card-head"><h3>{{ selected.tags.name || (selected.building_type === 'unknown' ? 'Здание' : selected.type_label) }}</h3><span class="building-count">{{ additional ? 'На карте' : '№ ' + selected.risk_rank }}</span></div>
        <p class="building-note">{{ number(selected.area_m2) }} м² · сигнал воды {{ percent(selected.water_probability_proxy) }}</p>
        <div class="building-values">
          <div><span>Первый этаж · стоимость</span><strong>{{ money(selected.exposure_rub) }}</strong><small>{{ selected.synthetic_cost ? 'Условная цена' : 'Ставка по типу' }}</small></div>
          <div class="building-value-loss"><span>{{ selected.expected_loss_rub == null ? 'Потери при затоплении' : 'Сценарный ущерб' }}</span><strong>{{ money(amount(selected)) }}</strong><small>{{ selected.expected_loss_rub == null ? 'Без оценки воды по снимку' : 'С учётом сигнала воды' }}</small></div>
        </div>
        <div class="building-actions"><button class="tonal-button" @click="planBuilding(selected.id)">В план съёмки</button><button class="ghost-button" @click="emit('building', selectedBuilding, true)"><MapPin :size="14" /> Показать на карте</button></div>
        <details class="building-details">
          <summary>Тип и стоимость</summary>
          <label>Назначение здания<select :value="selected.building_type" :disabled="buildings.busy" aria-label="Тип выбранного здания" @change="updateBuildingType(selected.id, $event.target.value)"><option v-for="(rate, key) in data.rates" :key="key" :value="key">{{ rate.label }}</option></select></label>
          <p class="building-note">{{ selected.type_source }}</p>
          <template v-if="selectedDraft">
            <div class="building-inline-rates">
              <label>Базовая ставка, ₽/м²<input v-model.number="selectedDraft.cost_rub_m2" type="number" min="0" max="10000000" aria-label="Ставка выбранного типа, ₽/м²" :disabled="buildings.busy" /></label>
              <label>Повреждение, %<input v-model.number="selectedDraft.damage_ratio" type="number" min="0" max="100" aria-label="Повреждение выбранного типа, %" :disabled="buildings.busy" /></label>
            </div>
            <p v-if="selected.synthetic_cost" class="building-note">Тип пока неизвестен. Условная ставка: базовая цена × {{ number(selected.cost_factor, 2) }}. Разброс ±15% закреплён за ID здания.</p>
            <p class="building-note">Изменения применятся ко всем зданиям этого типа: {{ selectedTypeCount }}.</p>
            <p v-if="selectedRateDirty" class="building-note">Изменения ещё не применены.</p>
            <p v-if="rateError" role="alert" class="error-note">{{ rateError }}</p>
            <button class="tonal-button" :disabled="buildings.busy || !selectedRateDirty" @click="applyRates([selected.building_type])">Применить к этому типу</button>
          </template>
        </details>
        <details class="building-details">
          <summary>Как получилась сумма</summary>
          <ol class="calculation-steps" aria-label="Расчёт ущерба здания">
            <li><span>Стоимость первого этажа</span><strong>{{ money(selected.exposure_rub) }}</strong><small>{{ number(selected.area_m2) }} м² × {{ money(selected.cost_rub_m2) }}/м²</small></li>
            <li><span>Потери при повреждении</span><strong>{{ money(selected.conditional_damage_rub) }}</strong><small>{{ money(selected.exposure_rub) }} × {{ percent(selected.damage_ratio) }}</small></li>
            <li v-if="selected.expected_loss_rub != null" class="calculation-result"><span>Ущерб с учётом воды</span><strong>{{ money(selected.expected_loss_rub) }}</strong><small>{{ money(selected.conditional_damage_rub) }} × {{ percent(selected.water_probability_proxy) }}</small></li>
          </ol>
          <p v-if="selected.expected_loss_rub == null" class="building-note">{{ buildingStatus[selected.status] }}. Показаны условные потери при затоплении; в сумму ущерба по снимку они не включены.</p>
          <p class="building-note">{{ selected.cost_source }}. {{ selected.damage_source }}. Суммы округлены. Сигнал модели не равен вероятности затопления дома.</p>
          <p v-if="selected.water_fraction === 0 && selected.expected_loss_rub > 0" class="building-note">Маска воды не пересекает здание. Сумма рассчитана по сигналу модели ниже порога маски.</p>
        </details>
        <details class="building-details">
          <summary>Данные здания</summary>
          <dl class="building-facts">
            <div><dt>ID</dt><dd>{{ selected.id }}</dd></div>
            <div><dt>Высота на карте</dt><dd>{{ number(selected.height_m) }} м</dd></div>
            <div v-if="selected.levels"><dt>Этажей</dt><dd>{{ selected.levels }}</dd></div>
            <div><dt>Покрыто радаром</dt><dd>{{ percent(selected.radar_coverage) }}</dd></div>
            <div><dt>Под маской воды</dt><dd>{{ percent(selected.water_fraction) }}</dd></div>
          </dl>
          <p class="building-note">{{ selected.height_source }}. Высота влияет только на отображение.</p>
          <a v-if="selected.source_url" :href="selected.source_url" target="_blank" rel="noopener noreferrer" class="building-note">Контур в OpenStreetMap ↗</a>
        </details>
      </article>

      <details class="building-details building-settings">
        <summary>Данные и настройки</summary>
        <div class="building-actions"><button class="ghost-button" :disabled="buildings.busy" @click="loadBuildings()">Обновить здания</button><button class="ghost-button" @click="openBuildingDemo">Пример: Пайпорта</button><button class="ghost-button" :disabled="buildings.busy" @click="fileInput.click()"><Upload :size="13" /> GeoJSON</button></div>
        <p class="building-note">{{ data.source.name }} · {{ (data.source.retrieved_at || data.source.retrieved_date || '').slice(0, 10) }}. Снимок: {{ data.observed_at.slice(0, 10) }}.</p>
        <p class="building-note">{{ data.summary.estimated }} зданий в сумме по снимку; {{ data.summary.unestimated }} исключено из неё из-за покрытия радаром или пересечения контуров. Условные цены: {{ data.summary.synthetic_cost || 0 }}.</p>
        <p v-if="!data.permanent_separated" class="building-note">Постоянная вода не отделена от паводковой.</p>
        <details class="building-details">
          <summary>Стоимость и повреждение по типам</summary>
          <p class="building-note">Сценарные ставки команды. Для неуточнённого типа — базовая ставка с разбросом ±15% по ID.</p>
          <div class="building-rate-head"><span>Тип</span><span>₽/м²</span><span>Ущерб, %</span></div>
          <div v-for="(rate, key) in data.rates" :key="key" class="building-rate-row"><label :for="'rate-' + key">{{ rate.label }}</label><input :id="'rate-' + key" v-model.number="draft[key].cost_rub_m2" type="number" min="0" max="10000000" :aria-label="rate.label + ': ставка за м²'" /><input v-model.number="draft[key].damage_ratio" type="number" min="0" max="100" :aria-label="rate.label + ': доля повреждения, %'" /></div>
          <p v-if="rateError" role="alert" class="error-note">{{ rateError }}</p>
          <div class="building-actions"><button class="tonal-button" :disabled="buildings.busy || !dirtyRates" @click="applyRates()">Применить ставки</button><button class="ghost-button" :disabled="buildings.busy" @click="resetRates">Сбросить ставки</button></div>
        </details>
        <details class="building-details"><summary>Метод и ограничения</summary><p v-for="note in data.method.limitations" :key="note" class="building-note">{{ note }}</p><p class="building-note">{{ data.source.attribution || data.source.name }} · {{ data.source.license }}. {{ data.source.scope }}</p></details>
      </details>
      <p v-if="dirtyRates" class="building-note">Примените изменения ставок перед выгрузкой.</p>
      <button class="primary-button wide building-export" :disabled="buildings.busy || buildings.exporting || dirtyRates" @click="exportBuildings"><Download :size="16" />{{ buildings.exporting ? 'Готовим выгрузку…' : 'Скачать расчёт' }}</button>
    </template>
    <div v-else-if="!buildings.busy" class="building-empty"><Building2 :size="28" /><p>Выберите снимок — здания появятся автоматически.</p><div class="building-actions"><button class="tonal-button" @click="openBuildingDemo">Пример: Пайпорта</button><button class="ghost-button" @click="fileInput.click()">Загрузить GeoJSON</button></div></div>
  </section>
</template>

<style scoped>
.buildings-view { display: grid; gap: 12px; padding: 4px 0 16px; }
.building-title, .building-card-head { display: flex; gap: 9px; align-items: center; justify-content: space-between; }
.building-title { justify-content: flex-start; }.building-title h2 { font-size: 17px; margin: 0; }
.building-note { font-size: 11px; line-height: 1.55; opacity: .75; margin: 0; overflow-wrap: anywhere; }
.building-actions { display: flex; gap: 7px; flex-wrap: wrap; }.building-actions button { font-size: 11px; }
.building-summary { background: color-mix(in srgb, var(--accent, #377a84) 10%, transparent); border-radius: 13px; padding: 15px; display: grid; gap: 7px; font-size: 11px; }
.building-summary strong { font-size: 26px; letter-spacing: -.6px; }.building-warning { margin: 0; font-size: 11px; padding: 9px; border-left: 3px solid #b88734; }
.building-card { display: grid; gap: 12px; }
.building-card h3 { margin: 0; font-size: 15px; overflow-wrap: anywhere; }.building-card label { display: grid; gap: 5px; font-size: 11px; }
.building-facts { margin: 0; display: grid; gap: 7px; font-size: 11px; }.building-facts div { display: flex; justify-content: space-between; gap: 8px; }.building-facts dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }.building-facts dt { opacity: .75; }
.building-loss { display: grid; gap: 5px; border-top: 1px solid #82939840; padding-top: 10px; font-size: 11px; }.building-loss strong { font-size: 21px; }
.calculation-heading { margin: 6px 0 0; font-size: 13px; }
.calculation-steps { list-style: none; counter-reset: calculation; display: grid; gap: 9px; margin: 0; padding: 0; }
.calculation-steps li { counter-increment: calculation; position: relative; display: grid; gap: 6px; padding: 12px 10px 12px 38px; border-radius: 10px; background: #8293980c; font-size: 11px; }
.calculation-steps li::before { content: counter(calculation); position: absolute; left: 10px; top: 12px; display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; background: #42858e20; font-size: 10px; }
.calculation-steps strong { font-size: 18px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.calculation-steps small { font-size: 10px; line-height: 1.5; opacity: .8; }
.calculation-steps .calculation-result { background: #42858e18; }.calculation-result strong { font-size: 21px; }
.building-inline-rates { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 8px; margin: 10px 0; }
.building-details { border-top: 1px solid #82939840; padding-top: 11px; }.building-details summary { cursor: pointer; font-size: 12px; font-weight: 600; }.building-details p { margin: 10px 0; }
.building-rate-row, .building-rate-head { display: grid; grid-template-columns: minmax(0, 1fr) 88px 60px; gap: 6px; align-items: center; margin: 7px 0; font-size: 10px; }
.buildings-view input, .buildings-view select { width: 100%; min-width: 0; border: 1px solid #82939860; border-radius: 7px; padding: 8px; background: color-mix(in srgb, var(--panel, #f2f4ef) 55%, transparent); color: inherit; font: inherit; box-sizing: border-box; }
.building-filters { display: grid; gap: 6px; grid-template-columns: 1fr 1fr; font-size: 11px; }.building-rate-row input { padding: 6px; }
.building-row { background: transparent; color: inherit; border: 0; border-bottom: 1px solid #82939830; padding: 11px 5px; display: flex; text-align: left; gap: 8px; justify-content: space-between; width: 100%; cursor: pointer; font-size: 11px; }.building-row.selected { background: #42858e18; }.building-row strong { font-weight: 550; }.building-row span { min-width: 0; overflow-wrap: anywhere; }.building-row small { display: block; font-size: 10px; opacity: .65; margin-top: 4px; }.building-row-amount { text-align: right; flex-shrink: 0; max-width: 45%; }
.building-legend { display: flex; flex-wrap: wrap; gap: 8px 12px; font-size: 10px; }.building-legend span:before { content: ''; width: 8px; height: 8px; display: inline-block; margin-right: 4px; border-radius: 2px; }.water-dot:before { background: #d57442; }.dry-dot:before { background: #398b81; }.unknown-dot:before { background: #899199; }
.building-export { margin-top: 6px; }.ghost-button { display: inline-flex; align-items: center; gap: 5px; }
.building-rank { width: 20px; flex: 0 0 20px; opacity: .6; font-variant-numeric: tabular-nums; }.building-row-main { flex: 1; }.building-list-mode { display: flex; gap: 8px; }.building-list-mode [aria-pressed="true"] { background: #42858e20; border-radius: 8px; }
.building-count { margin-left: auto; border-radius: 20px; padding: 4px 8px; background: #82939818; font-size: 11px; white-space: nowrap; }
.building-back { justify-self: start; font-size: 12px; padding-left: 0; }
.building-values { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.building-values > div { display: grid; gap: 8px; align-content: start; padding: 13px 10px; background: #82939810; border-radius: 12px; font-size: 10px; }
.building-values strong { font-size: 19px; letter-spacing: -.4px; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.building-values small { font-size: 10px; opacity: .65; }.building-values .building-value-loss { background: #42858e18; }
.building-card > .tonal-button { display: flex; gap: 6px; justify-content: center; align-items: center; }
.building-card .building-details > label { margin-top: 12px; }.building-facts { margin-top: 12px; }
.building-list { max-height: clamp(220px, 42vh, 440px); overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; }
.building-row { font-size: 12px; padding: 10px 4px; }.building-row strong { font-weight: 550; }.building-row-amount { font-variant-numeric: tabular-nums; }
.building-list-mode button { padding: 7px 8px; font-size: 11px; }.search-toggle { margin-left: auto; }.building-settings > .building-actions { margin-top: 12px; }
.building-empty { display: grid; justify-items: center; text-align: center; gap: 10px; padding: 24px 0; font-size: 12px; }
.calculation-steps { margin-top: 12px; }
</style>
