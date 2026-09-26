<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { Building2, Download, Upload, MapPin } from '@lucide/vue'
import { buildings, selectedBuilding, topBuildings, loadBuildings, openBuildingDemo, importBuildings, updateRates, updateBuildingType, exportBuildings, buildingStatus } from '../lib/buildings'

const emit = defineEmits(['building'])
const fileInput = ref(null)
const filter = ref('all')
const listMode = ref('top')
const card = ref(null)
const search = ref('')
const limit = ref(40)
const draft = ref({})
const rateError = ref('')
const data = computed(() => buildings.data)
const selected = computed(() => selectedBuilding.value?.properties)
const money = value => value == null ? 'Нет оценки' : new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(value)
const number = value => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value)
const percent = value => value == null ? 'нет данных' : number(value * 100) + '%'
const dirtyRates = computed(() => data.value && Object.entries(draft.value).some(([key, value]) => value.cost_rub_m2 !== data.value.rates[key].cost_rub_m2 || value.damage_ratio !== data.value.rates[key].damage_ratio * 100))
watch(() => data.value?.rates, rates => {
  if (rates) draft.value = Object.fromEntries(Object.entries(rates).map(([key, r]) => [key, { cost_rub_m2: r.cost_rub_m2, damage_ratio: r.damage_ratio * 100 }]))
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
async function applyRates() {
  rateError.value = ''
  const rates = {}
  for (const [key, row] of Object.entries(draft.value)) {
    if (row.cost_rub_m2 === '' || row.damage_ratio === '' || !Number.isFinite(Number(row.cost_rub_m2)) || !Number.isFinite(Number(row.damage_ratio)) || row.cost_rub_m2 < 0 || row.cost_rub_m2 > 10000000 || row.damage_ratio < 0 || row.damage_ratio > 100) {
      rateError.value = 'Ставка: от 0 до 10 000 000 ₽/м². Повреждение: от 0 до 100%.'; return
    }
    rates[key] = { cost_rub_m2: Number(row.cost_rub_m2), damage_ratio: Number(row.damage_ratio) / 100 }
  }
  await updateRates(rates)
}
</script>

<template>
  <section class="buildings-view" aria-label="Оценка зданий" :aria-busy="buildings.busy">
    <div class="building-title"><Building2 :size="20" /><h2>Топ‑10 зданий по риску</h2></div>
    <p class="building-note">Здания загружаются автоматически. Выше — больший сигнал воды в контуре. Нажмите на любое здание на карте, чтобы открыть его отдельно.</p>
    <div class="building-actions">
      <button class="tonal-button" @click="openBuildingDemo">Пример: Пайпорта</button>
      <button class="ghost-button" :disabled="buildings.busy" @click="loadBuildings()">Повторить загрузку</button>
      <button class="ghost-button" :disabled="buildings.busy" @click="fileInput.click()"><Upload :size="14" /> Свой GeoJSON</button>
      <input ref="fileInput" type="file" accept=".geojson,.json,application/geo+json" hidden aria-label="Загрузить контуры GeoJSON" @change="upload" />
    </div>
    <p v-if="buildings.busy" role="status" class="building-note">Загружаем контуры и пересчитываем ущерб…</p>
    <p v-if="buildings.error" role="alert" class="error-note">{{ buildings.error }}</p>
    <template v-if="data">
      <div class="building-summary" data-testid="building-summary">
        <span>Сценарный ущерб · {{ data.summary.estimated }} из {{ data.summary.count }} зданий</span>
        <strong>{{ money(data.summary.expected_loss_rub) }}</strong>
        <span>{{ data.summary.unestimated }} без оценки · {{ data.summary.with_water }} с пересечением маски воды</span>
      </div>
      <p class="building-note">{{ data.source.name }} · контуры {{ (data.source.retrieved_at || data.source.retrieved_date || '').slice(0, 10) }}<br />Радар: {{ data.observed_at.slice(0, 10) }}. Контуры и назначение на дату снимка не подтверждены.</p>
      <p v-if="!data.permanent_separated" class="building-warning">Постоянная вода не отделена: расчёт использует сигнал всей воды.</p>
      <div class="building-legend"><span class="water-dot">Есть сигнал воды</span><span class="dry-dot">Нет пересечения маски</span><span class="unknown-dot">Нет данных радара</span></div>

      <article v-if="selected" ref="card" class="building-card" data-testid="building-card">
        <span class="building-note">{{ additional ? 'Дополнительное здание · выбрано отдельно от топ‑10' : 'Место в рейтинге: ' + selected.risk_rank }}</span>
        <div class="building-card-head"><h3>{{ selected.name }}</h3><button class="ghost-button" aria-label="Закрыть карточку здания" @click="buildings.selectedId = null">×</button></div>
        <label>Тип здания
          <select :value="selected.building_type" :disabled="buildings.busy" aria-label="Тип выбранного здания" @change="updateBuildingType(selected.id, $event.target.value)">
            <option value="unknown">Тип не определён</option>
            <option v-for="(rate, key) in data.rates" :key="key" :value="key">{{ rate.label }}</option>
          </select>
        </label>
        <p class="building-note">{{ selected.type_source }}<br /><a v-if="selected.source_url" :href="selected.source_url" target="_blank" rel="noopener noreferrer">Контур в OpenStreetMap ↗</a></p>
        <dl class="building-facts">
          <div><dt>Площадь контура</dt><dd>{{ number(selected.area_m2) }} м²</dd></div>
          <div v-if="selected.levels"><dt>Этажей по источнику</dt><dd>{{ selected.levels }}</dd></div>
          <div><dt>Высота на карте</dt><dd>{{ number(selected.height_m) }} м{{ selected.height_estimated ? ' ≈' : '' }}</dd></div>
          <div><dt>Покрыто радаром</dt><dd>{{ percent(selected.radar_coverage) }}</dd></div>
          <div><dt>Контур под маской воды</dt><dd>{{ percent(selected.water_fraction) }}</dd></div>
          <div><dt>Средний сигнал воды, p</dt><dd>{{ percent(selected.water_probability_proxy) }}</dd></div>
          <div><dt>Ставка первого этажа</dt><dd>{{ selected.cost_rub_m2 == null ? 'Выберите тип' : money(selected.cost_rub_m2) + '/м²' }}</dd></div>
          <div><dt>Стоимость первого этажа</dt><dd>{{ money(selected.exposure_rub) }}</dd></div>
          <div><dt>Доля повреждения, q</dt><dd>{{ percent(selected.damage_ratio) }}</dd></div>
          <div><dt>Ущерб при затоплении, V × q</dt><dd>{{ money(selected.conditional_damage_rub) }}</dd></div>
        </dl>
        <p class="building-note">{{ selected.height_source }}. Высота влияет только на вид здания.</p>
        <div class="building-loss"><span>{{ buildingStatus[selected.status] }}</span><strong>{{ money(selected.expected_loss_rub) }}</strong></div>
        <p v-if="selected.expected_loss_rub != null" class="building-note">{{ number(selected.area_m2) }} м² × {{ money(selected.cost_rub_m2) }}/м² × {{ percent(selected.damage_ratio) }} × {{ percent(selected.water_probability_proxy) }}</p>
        <p class="building-note">p — среднее по пикселям внутри контура. Это не подтверждение затопления или повреждения дома.</p>
        <button class="ghost-button" @click="emit('building', selectedBuilding, true)"><MapPin :size="14" /> Показать на карте</button>
      </article>

      <div class="building-filters">
        <input v-model="search" type="search" placeholder="Название, тип или ID" aria-label="Поиск здания" />
        <select v-model="filter" aria-label="Фильтр зданий"><option value="all">Все типы</option><option value="water">С сигналом воды</option><option value="unknown">Неизвестный тип</option><option value="missing">Без оценки</option></select>
      </div>
      <div class="building-list-mode" role="group" aria-label="Список зданий">
        <button class="ghost-button" :aria-pressed="topMode" @click="listMode = 'top'; filter = 'all'; search = ''">Топ‑10</button>
        <button class="ghost-button" :aria-pressed="!topMode" @click="listMode = 'all'">Все {{ data.summary.count }}</button>
      </div>
      <p class="building-note">{{ topMode ? topBuildings.length + ' зданий с наибольшим сигналом воды' : filtered.length + ' зданий по фильтру' }}. Рейтинг не подтверждает повреждение здания.</p>
      <div class="building-list" data-testid="building-top-list">
        <button v-for="feature in visibleBuildings" :key="feature.id" class="building-row" :data-building-id="feature.id" :class="{ selected: feature.id === buildings.selectedId }" @click="choose(feature)">
          <b class="building-rank">{{ feature.properties.risk_rank || '—' }}</b>
          <span class="building-row-main"><strong>{{ feature.properties.tags.name || feature.properties.type_label }}</strong><small>{{ feature.properties.tags.name ? feature.properties.type_label + ' · ' : '' }}{{ number(feature.properties.area_m2) }} м² · p воды {{ percent(feature.properties.water_probability_proxy) }}</small></span>
          <span class="building-row-amount">{{ money(feature.properties.expected_loss_rub) }}<small>{{ feature.properties.expected_loss_rub == null ? buildingStatus[feature.properties.status] : 'сценарий' }}</small></span>
        </button>
      </div>
      <p v-if="!visibleBuildings.length" class="building-note">{{ data.summary.count ? 'Для этого списка нет зданий с данными. Откройте все здания или смените фильтр.' : 'В этой области контуры не найдены. Можно открыть другой снимок или загрузить GeoJSON.' }}</p>
      <button v-if="!topMode && filtered.length > limit" class="ghost-button" @click="limit += 40">Показать ещё 40</button>

      <details class="building-details">
        <summary>Стоимость и повреждение по типам</summary>
        <p class="building-note">Сценарные ставки команды в рублях. Это не рыночная оценка недвижимости. Изменения применяются ко всем зданиям выбранного типа.</p>
        <div class="building-rate-head"><span>Тип</span><span>₽/м²</span><span>Ущерб, %</span></div>
        <div v-for="(rate, key) in data.rates" :key="key" class="building-rate-row">
          <label :for="'rate-' + key">{{ rate.label }}</label>
          <input :id="'rate-' + key" v-model.number="draft[key].cost_rub_m2" type="number" min="0" max="10000000" :aria-label="rate.label + ': ставка за м²'" />
          <input v-model.number="draft[key].damage_ratio" type="number" min="0" max="100" :aria-label="rate.label + ': доля повреждения, %'" />
        </div>
        <p v-if="dirtyRates" class="building-note">Изменения ещё не применены к расчёту.</p>
        <p v-if="rateError" role="alert" class="error-note">{{ rateError }}</p>
        <div class="building-actions"><button class="tonal-button" :disabled="buildings.busy || !dirtyRates" @click="applyRates">Применить ставки</button><button class="ghost-button" :disabled="buildings.busy" @click="updateRates({})">Сбросить ставки</button></div>
      </details>

      <details class="building-details">
        <summary>Как считается и что пока неизвестно</summary>
        <p class="building-note">Ущерб = площадь контура × ставка типа × доля повреждения × средний сигнал воды. Пиксели учитываются пропорционально пересечению с контуром. Верхние этажи не умножаются на стоимость.</p>
        <p class="building-note">Тип берётся из назначения здания в OSM. Неизвестные типы можно уточнить вручную. Сумма не считается при пропусках радара или наложении контуров.</p>
        <p v-for="note in data.method.limitations" :key="note" class="building-note">{{ note }}</p>
        <p class="building-note">Источник контуров: {{ data.source.attribution || data.source.name }}. {{ data.source.license }}. {{ data.source.scope }}</p>
      </details>

      <button class="primary-button wide building-export" :disabled="buildings.busy || buildings.exporting || dirtyRates" @click="exportBuildings"><Download :size="16" />{{ buildings.exporting ? 'Готовим выгрузку…' : 'Скачать здания и расчёт' }}</button>
      <p class="building-note">ZIP: GeoJSON, CSV, применённые ставки, источники и формула.</p>
    </template>
    <p v-else-if="!buildings.busy" class="building-note">Контуры появятся после загрузки снимка. Если источник недоступен, повторите загрузку или откройте сохранённый пример Пайпорты.</p>
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
.building-card { padding: 13px; border: 1px solid color-mix(in srgb, currentColor 20%, transparent); border-radius: 13px; display: grid; gap: 10px; }
.building-card h3 { margin: 0; font-size: 15px; overflow-wrap: anywhere; }.building-card label { display: grid; gap: 5px; font-size: 11px; }
.building-facts { margin: 0; display: grid; gap: 7px; font-size: 11px; }.building-facts div { display: flex; justify-content: space-between; gap: 8px; }.building-facts dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }.building-facts dt { opacity: .75; }
.building-loss { display: grid; gap: 5px; border-top: 1px solid #82939840; padding-top: 10px; font-size: 11px; }.building-loss strong { font-size: 21px; }
.building-details { border-top: 1px solid #82939840; padding-top: 11px; }.building-details summary { cursor: pointer; font-size: 12px; font-weight: 600; }.building-details p { margin: 10px 0; }
.building-rate-row, .building-rate-head { display: grid; grid-template-columns: minmax(0, 1fr) 88px 60px; gap: 6px; align-items: center; margin: 7px 0; font-size: 10px; }
.buildings-view input, .buildings-view select { width: 100%; min-width: 0; border: 1px solid #82939860; border-radius: 7px; padding: 8px; background: color-mix(in srgb, var(--panel, #f2f4ef) 55%, transparent); color: inherit; font: inherit; box-sizing: border-box; }
.building-filters { display: grid; gap: 6px; grid-template-columns: 1fr 1fr; font-size: 11px; }.building-rate-row input { padding: 6px; }
.building-row { background: transparent; color: inherit; border: 0; border-bottom: 1px solid #82939830; padding: 11px 5px; display: flex; text-align: left; gap: 8px; justify-content: space-between; width: 100%; cursor: pointer; font-size: 11px; }.building-row.selected { background: #42858e18; }.building-row strong { font-weight: 550; }.building-row span { min-width: 0; overflow-wrap: anywhere; }.building-row small { display: block; font-size: 10px; opacity: .65; margin-top: 4px; }.building-row-amount { text-align: right; flex-shrink: 0; max-width: 45%; }
.building-legend { display: flex; flex-wrap: wrap; gap: 8px 12px; font-size: 10px; }.building-legend span:before { content: ''; width: 8px; height: 8px; display: inline-block; margin-right: 4px; border-radius: 2px; }.water-dot:before { background: #d57442; }.dry-dot:before { background: #398b81; }.unknown-dot:before { background: #899199; }
.building-export { margin-top: 6px; }.ghost-button { display: inline-flex; align-items: center; gap: 5px; }
.building-rank { width: 20px; flex: 0 0 20px; opacity: .6; font-variant-numeric: tabular-nums; }.building-row-main { flex: 1; }.building-list-mode { display: flex; gap: 8px; }.building-list-mode [aria-pressed="true"] { background: #42858e20; border-radius: 8px; }
</style>
