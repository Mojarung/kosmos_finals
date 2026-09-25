<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import GlobeMap from './GlobeMap.vue'

const mapRef = ref(null)
const analysis = ref(null)
const error = ref('')
const loading = ref(false)
const budget = ref(6500)
const deadline = ref(8)
const priority = ref('risk')
const region = ref(null)
const drawing = ref(false)
const selectedAssetId = ref(null)
const mustCover = ref(null)
const strategy = ref('C')
const showRadar = ref(true)
const showWater = ref(true)
const showCandidates = ref(false)
let requestController
let requestTimer

const selectedAsset = computed(() => analysis.value?.assets.find(a => a.id === selectedAssetId.value) || null)
const activePlan = computed(() => analysis.value?.strategies[strategy.value])
const regionHasData = computed(() => analysis.value?.region.data_status === 'covered')
const sourceBadge = computed(() => region.value ? 'ВЫБРАННЫЙ РЕГИОН' : 'ДЕМО-ЧИП')

function rub(value) { return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(value || 0) + ' ₽' }
function compactRub(value) {
  if (Math.abs(value || 0) >= 1_000_000) return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(value / 1_000_000) + ' млн ₽'
  if (Math.abs(value || 0) >= 1_000) return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value / 1_000) + ' тыс. ₽'
  return rub(value)
}
function km(value) { return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(value || 0) + ' км²' }
function percent(value) { return Math.round((value || 0) * 100) + '%' }

function params() {
  const p = new URLSearchParams({ budget: String(budget.value), deadline: String(deadline.value), priority: priority.value })
  if (region.value) p.set('bbox', region.value.join(','))
  if (mustCover.value) p.set('must_cover', mustCover.value)
  return p
}
async function fetchAnalysis() {
  requestController?.abort()
  requestController = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const response = await fetch('/api/analysis?' + params(), { signal: requestController.signal })
    if (!response.ok) throw new Error(`API: ${response.status}`)
    analysis.value = await response.json()
    if (selectedAssetId.value && !analysis.value.assets.some(a => a.id === selectedAssetId.value)) selectedAssetId.value = null
  } catch (cause) {
    if (cause.name !== 'AbortError') error.value = 'Не удалось загрузить анализ. Проверьте API и повторите.'
  } finally {
    if (!requestController.signal.aborted) loading.value = false
  }
}
function scheduleFetch() { clearTimeout(requestTimer); requestTimer = setTimeout(fetchAnalysis, 160) }
watch([budget, deadline, priority, region, mustCover], scheduleFetch)
onMounted(fetchAnalysis)

function setRegion(bounds) { region.value = bounds; selectedAssetId.value = null; mustCover.value = null }
function resetRegion() { region.value = null; drawing.value = false; selectedAssetId.value = null; mustCover.value = null; mapRef.value?.focusChip() }
function chooseAsset(id) { selectedAssetId.value = id; const asset = analysis.value?.assets.find(a => a.id === id); if (asset) mapRef.value?.focusAsset(asset) }
function exportDecision() { window.location.href = '/api/export?' + params() }
function toggleGuarantee() { mustCover.value = mustCover.value === selectedAssetId.value ? null : selectedAssetId.value }
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand" aria-label="FloodValue">
        <span class="brand-symbol"><svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M4 21c5-8 8-13 12-13 5 0 7 10 12 15" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/><path d="M4 25c5-5 8-8 12-8 5 0 7 5 12 8" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/><circle cx="23" cy="7" r="2" fill="currentColor"/></svg></span>
        <span><strong>FloodValue</strong><small>оперативный атлас</small></span>
      </div>
      <div class="header-context"><span class="live-dot"></span> Сценарий оператора <span class="separator"></span> Sen1Floods11 · v1.1</div>
      <div class="header-actions">
        <button class="header-link" @click="mapRef?.showGlobe()" title="Показать Землю">Планета</button>
        <button class="header-link" @click="mapRef?.focusChip()" title="Показать демо-чип">К чипу ↗</button>
        <button class="export-button" @click="exportDecision" :disabled="!analysis">↓ <span>Экспорт решения</span></button>
      </div>
    </header>

    <main class="workspace">
      <aside class="left-panel panel">
        <div class="panel-head">
          <div class="eyebrow">01 / ТЕРРИТОРИЯ</div>
          <h1>Что происходит<br>на местности</h1>
          <p>Выберите любую область на Земле. Для загруженного чипа доступны радар и ручная карта воды.</p>
        </div>

        <div class="territory-block">
          <div class="section-title"><span>Область анализа</span><span class="tiny-mark">↗</span></div>
          <div class="region-status">
            <span class="region-icon">⌖</span>
            <div><strong>{{ region ? 'Произвольный регион' : 'Гана · демонстрационный чип' }}</strong><small>{{ regionHasData ? analysis?.chip.observed_at : 'нет наблюдения' }} · {{ sourceBadge }}</small></div>
          </div>
          <div class="two-actions">
            <button class="soft-button" :class="{ active: drawing }" @click="drawing = !drawing">{{ drawing ? 'Отмена выбора' : '▢ Выделить' }}</button>
            <button class="soft-button" @click="resetRegion">Сбросить</button>
          </div>
          <div v-if="region" class="region-coords">{{ region.map(v => v.toFixed(3)).join(' · ') }}</div>
          <div v-if="analysis && !regionHasData" class="no-data"><strong>Данных пока нет</strong><span>Регион выбран. Демо-набор покрывает только один чип в Гане; водные метрики здесь не подменяются оценкой.</span></div>
        </div>

        <div class="layers-block">
          <div class="section-title">Слои карты <span class="section-note">S1 / LABEL</span></div>
          <button class="layer-row" @click="showRadar = !showRadar" :aria-pressed="showRadar"><span class="layer-swatch radar-swatch"></span><span><strong>Радар Sentinel-1</strong><small>VH · фактический снимок</small></span><span class="toggle" :class="{ on: showRadar }"></span></button>
          <button class="layer-row" @click="showWater = !showWater" :aria-pressed="showWater"><span class="layer-swatch water-swatch"></span><span><strong>Вода</strong><small>ручная эталонная разметка</small></span><span class="toggle" :class="{ on: showWater }"></span></button>
          <button class="layer-row" @click="showCandidates = !showCandidates" :aria-pressed="showCandidates"><span class="layer-swatch zones-swatch"></span><span><strong>Зоны съёмки</strong><small>все кандидаты / выбранные</small></span><span class="toggle" :class="{ on: showCandidates }"></span></button>
        </div>

        <div class="asset-block">
          <div class="section-title">Объекты <span class="count-badge">{{ analysis?.assets.length || 0 }} / 10</span></div>
          <div v-if="analysis?.assets.length" class="asset-list">
            <button v-for="asset in analysis.assets" :key="asset.id" class="asset-item" :class="{ selected: selectedAssetId === asset.id }" @click="chooseAsset(asset.id)">
              <span class="asset-index">{{ asset.id }}</span>
              <span class="asset-name">{{ asset.name }}<small>p сценарная · {{ Math.round(asset.scenario_probability * 100) }}%</small></span>
              <span class="asset-value">{{ compactRub(asset.expected_loss_rub) }}</span>
            </button>
          </div>
          <p v-else class="quiet-empty">В выделенной области нет объектов учебного портфеля.</p>
        </div>
      </aside>

      <section class="map-stage" aria-label="Карта и наблюдения">
        <GlobeMap ref="mapRef" :analysis="analysis" :region="region" :drawing="drawing" :selected-asset="selectedAssetId" :active-strategy="strategy" :show-radar="showRadar" :show-water="showWater" :show-candidates="showCandidates" @region="setRegion" @asset="chooseAsset" @draw-complete="drawing = false" />
        <div class="map-topline"><span class="scan-line"></span><span>ОПЕРАТИВНАЯ КАРТА</span><span class="map-separator">/</span><span>{{ analysis ? (regionHasData ? analysis.chip.chip_id : 'ВНЕ ПОКРЫТИЯ') : 'ЗАГРУЗКА' }}</span></div>
        <div class="map-metrics" v-if="analysis">
          <div><small>ВЫБРАНО</small><strong>{{ km(analysis.region.area_km2) }}</strong></div>
          <div><small>НАБЛЮДАЕТСЯ</small><strong>{{ km(analysis.region.observed_area_km2) }}</strong></div>
          <div><small>ВОДА · МЕТКА</small><strong>{{ analysis.region.water_area_km2 == null ? '—' : km(analysis.region.water_area_km2) }}</strong></div>
        </div>
        <div class="map-bottomline"><span class="legend-dot"></span> Вода по ручной разметке <span class="legend-asset"></span> Учебный объект <span class="legend-zone"></span> План съёмки</div>
        <div class="map-status" v-if="loading">Пересчёт плана…</div>
      </section>

      <aside class="right-panel panel">
        <div class="decision-head"><div class="eyebrow">02 / РЕШЕНИЕ</div><h2>План наблюдения</h2><p>Что посмотреть дополнительно в пределах бюджета и срока.</p></div>
        <div v-if="error" class="error-box">{{ error }} <button @click="fetchAnalysis">Повторить</button></div>
        <div v-if="analysis && !regionHasData" class="decision-empty"><span class="empty-orbit">⌖</span><strong>Наблюдений нет</strong><p>Для этого региона нет чипа в локальном демо. Область выбрана: {{ km(analysis.region.area_km2) }}. Откройте загруженный чип для расчёта плана.</p><button class="soft-button" @click="resetRegion">Открыть демо-чип</button></div>
        <template v-if="regionHasData">

        <div class="decision-kpis" v-if="analysis">
          <div class="primary-kpi"><small>ОЦЕНОЧНАЯ ЭКСПОЗИЦИЯ · СЦЕНАРИЙ</small><strong>{{ compactRub(analysis.metrics.scenario_exposure_rub) }}</strong><span>Σ p × стоимость × уязвимость</span></div>
          <div class="kpi-pair"><div><small>ОХВАЧЕНО ПЛАНОМ C</small><strong>{{ percent(analysis.strategies.C.coverage_share) }}</strong></div><div><small>ЭКОНОМИЯ К B</small><strong>{{ analysis.strategies.B.timely && analysis.strategies.C.feasible ? compactRub(analysis.comparison.data_savings_vs_broad_rub) : '—' }}</strong></div></div>
        </div>

        <div class="control-section">
          <div class="section-title">Условия заказа <span class="section-note">СЦЕНАРИЙ</span></div>
          <label class="budget-label" for="budget"><span>Бюджет на данные</span><strong>{{ rub(budget) }}</strong></label>
          <input id="budget" v-model.number="budget" class="budget-range" type="range" min="0" max="45000" step="250" :style="{ '--progress': (budget / 45000 * 100) + '%' }" />
          <div class="range-ends"><span>0 ₽</span><span>45 000 ₽</span></div>
          <div class="control-line"><span>Решение через</span><div class="segmented"><button v-for="hours in [4, 8, 12]" :key="hours" :class="{ active: deadline === hours }" @click="deadline = hours">{{ hours }} ч</button></div></div>
          <div class="control-line"><span>Приоритет</span><div class="segmented"><button :class="{ active: priority === 'risk' }" @click="priority = 'risk'">Ущерб</button><button :class="{ active: priority === 'information' }" @click="priority = 'information'">Уточнение</button></div></div>
        </div>

        <div class="strategies" v-if="analysis">
          <div class="section-title">Стратегии <span class="section-note">ОДИН ПОРТФЕЛЬ</span></div>
          <div class="strategy-tabs" role="tablist" aria-label="Сравнение стратегий">
            <button v-for="key in ['A', 'B', 'C']" :key="key" role="tab" :aria-selected="strategy === key" :class="{ active: strategy === key }" @click="strategy = key"><b>{{ key }}</b><span>{{ key === 'A' ? 'Открытые' : key === 'B' ? 'Широкая' : 'Выборочная' }}</span></button>
          </div>
          <Transition mode="out-in" name="slide-fade"><div :key="strategy" class="strategy-detail">
            <div class="strategy-main"><div><small>СТОИМОСТЬ ДАННЫХ</small><strong>{{ rub(activePlan?.data_cost_rub) }}</strong></div><span class="strategy-state" :class="{ alert: !activePlan?.budget_feasible || !activePlan?.timely }">{{ !activePlan?.timely ? 'Позже срока' : !activePlan?.budget_feasible ? 'Вне бюджета' : 'В бюджете' }}</span></div>
            <div class="strategy-progress"><span :style="{ width: percent(activePlan?.coverage_share) }"></span></div>
            <div class="strategy-foot"><span>Охват экспозиции</span><strong>{{ percent(activePlan?.coverage_share) }}</strong></div>
            <div class="strategy-foot"><span>Зон в корзине</span><strong>{{ activePlan?.zone_ids?.length || 0 }}</strong></div>
            <p v-if="activePlan?.reason" class="plan-reason">{{ activePlan.reason }}</p>
          </div></Transition>
          <p class="method-note">Охват — сумма сценарного EL уникальных объектов под съёмкой. Это не предотвращённый ущерб.</p>
        </div>

        <div class="selected-detail" v-if="selectedAsset">
          <div class="section-title">В фокусе <button class="text-close" @click="selectedAssetId = null">×</button></div>
          <h3>{{ selectedAsset.name }}</h3>
          <div class="detail-grid"><span>Сценарный EL</span><strong>{{ compactRub(selectedAsset.expected_loss_rub) }}</strong><span>Стоимость · q</span><strong>{{ compactRub(selectedAsset.value_rub) }} · {{ selectedAsset.vulnerability }}</strong><span>Неопределённость*</span><strong>{{ selectedAsset.attention_score }}</strong></div>
          <button class="guarantee-button" :class="{ active: mustCover === selectedAsset.id }" @click="toggleGuarantee">{{ mustCover === selectedAsset.id ? '✓ Объект обязателен' : '+ Включить в план' }}</button>
          <small class="detail-footnote">* p·(1−p) нормировано; показатель пока не проверен на ошибках.</small>
        </div>
        </template>

        <div class="evidence-footer"><span class="evidence-icon">i</span><span>Радар и метка — Sen1Floods11. Вероятности и ставка заказа сценарные. ML-оценка будет подключена отдельно.</span></div>
      </aside>
    </main>
  </div>
</template>
