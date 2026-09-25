<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import {
  Activity, ArrowRight, Check, ChevronRight, Crosshair, Download, Eye,
  Globe2, Info, Layers, Map, Moon, RotateCcw, Sun, Waves, X,
} from '@lucide/vue'
import GlobeMap from './GlobeMap.vue'

const mapRef = ref(null)
const analysis = ref(null)
const error = ref('')
const loading = ref(false)
const sceneId = ref('Nigeria_812045')
const region = ref(null)
const drawing = ref(false)
const selectedAssetId = ref(null)
const mustCover = ref(null)
const layerMode = ref('model')
const showRadar = ref(true)
const showZones = ref(false)
const theme = ref('light')
const budget = ref(6500)
const deadline = ref(8)
const priority = ref('risk')
const strategy = ref('C')
const rightMode = ref('assets')
const mobilePane = ref('observe')
let requestController
let requestTimer

const hasData = computed(() => ['covered', 'partial_coverage'].includes(analysis.value?.region.data_status))
const selectedAsset = computed(() => analysis.value?.assets.find(item => item.id === selectedAssetId.value) || null)
const activePlan = computed(() => analysis.value?.strategies[strategy.value])
const testQuality = computed(() => analysis.value?.quality.parts.test_core)
const focusedZones = computed(() => {
  const ids = new Set(activePlan.value?.zone_ids || [])
  return analysis.value?.zones.filter(zone => ids.has(zone.id)) || []
})
const panelVisible = pane => mobilePane.value === pane

function number(value, digits = 0) {
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(value || 0)
}
function rub(value) { return number(value) + ' ₽' }
function compactRub(value) {
  if (value == null) return '—'
  if (Math.abs(value) >= 1_000_000) return number(value / 1_000_000, 2) + ' млн ₽'
  if (Math.abs(value) >= 1_000) return number(value / 1_000, 1) + ' тыс. ₽'
  return rub(value)
}
function km(value) { return value == null ? '—' : number(value, 2) + ' км²' }
function percent(value, digits = 0) { return value == null ? '—' : number(value * 100, digits) + '%' }

function parameters() {
  const params = new URLSearchParams({
    scene_id: sceneId.value, budget: String(budget.value),
    deadline: String(deadline.value), priority: priority.value,
  })
  if (region.value) params.set('bbox', region.value.join(','))
  if (mustCover.value) params.set('must_cover', mustCover.value)
  return params
}
async function fetchAnalysis() {
  requestController?.abort()
  const controller = new AbortController()
  requestController = controller
  loading.value = true
  error.value = ''
  try {
    const response = await fetch('/api/analysis?' + parameters(), { signal: controller.signal })
    if (!response.ok) throw new Error('API ' + response.status)
    analysis.value = await response.json()
    if (selectedAssetId.value && !analysis.value.assets.some(a => a.id === selectedAssetId.value)) selectedAssetId.value = null
  } catch (cause) {
    if (cause.name !== 'AbortError') error.value = 'Не удалось загрузить анализ. Проверьте API и повторите.'
  } finally {
    if (requestController === controller) loading.value = false
  }
}
function scheduleFetch() { clearTimeout(requestTimer); requestTimer = setTimeout(fetchAnalysis, 110) }
watch([sceneId, region, budget, deadline, priority, mustCover], scheduleFetch)
watch(theme, value => {
  document.documentElement.dataset.theme = value
  localStorage.setItem('floodvalue-theme', value)
})
watch(rightMode, value => { if (value === 'plan') showZones.value = true })
onMounted(() => {
  theme.value = localStorage.getItem('floodvalue-theme') === 'dark' ? 'dark' : 'light'
  fetchAnalysis()
})

function chooseScene(id) {
  if (sceneId.value === id) { mapRef.value?.focusChip(); return }
  sceneId.value = id
  region.value = null
  selectedAssetId.value = null
  mustCover.value = null
  layerMode.value = 'model'
  drawing.value = false
}
function chooseRegion(bounds) {
  region.value = bounds
  selectedAssetId.value = null
  mustCover.value = null
}
function resetRegion() {
  region.value = null
  drawing.value = false
  selectedAssetId.value = null
  mustCover.value = null
  mapRef.value?.focusChip()
}
function chooseAsset(id) {
  selectedAssetId.value = id
  rightMode.value = 'assets'
  mobilePane.value = 'assets'
  const asset = analysis.value?.assets.find(item => item.id === id)
  if (asset) mapRef.value?.focusAsset(asset)
}
function setPane(pane) {
  mobilePane.value = pane
  if (['assets', 'plan', 'quality'].includes(pane)) rightMode.value = pane
}
function toggleGuarantee() {
  if (!selectedAsset.value) return
  mustCover.value = mustCover.value === selectedAssetId.value ? null : selectedAssetId.value
  rightMode.value = 'plan'
  mobilePane.value = 'plan'
}
function exportDecision() { window.location.href = '/api/export?' + parameters() }
</script>

<template>
  <main class="atlas" :class="{ 'is-loading': loading }">
    <GlobeMap ref="mapRef" :analysis="analysis" :region="region" :drawing="drawing"
      :selected-asset="selectedAssetId" :active-strategy="strategy" :layer-mode="layerMode"
      :show-radar="showRadar" :show-zones="showZones" :theme="theme"
      @region="chooseRegion" @asset="chooseAsset" @draw-complete="drawing = false" />

    <header class="topbar">
      <div class="brand">
        <span class="brand-icon"><Waves :size="23" :stroke-width="2.2" /></span>
        <span class="brand-copy"><strong>FloodValue</strong><small>Паводок · решение по ДЗЗ</small></span>
      </div>
      <div class="workflow" aria-label="Путь решения">
        <span><b>01</b> Вода</span><ArrowRight :size="14" /><span><b>02</b> Объекты</span>
        <ArrowRight :size="14" /><span><b>03</b> Съёмка</span>
      </div>
      <div class="top-actions">
        <button class="icon-button globe-action" title="Показать Землю" aria-label="Показать Землю" @click="mapRef?.showGlobe()"><Globe2 :size="18" /></button>
        <button class="icon-button" :title="theme === 'light' ? 'Тёмная тема' : 'Светлая тема'"
          :aria-label="theme === 'light' ? 'Тёмная тема' : 'Светлая тема'"
          @click="theme = theme === 'light' ? 'dark' : 'light'"><Moon v-if="theme === 'light'" :size="18" /><Sun v-else :size="18" /></button>
        <button class="primary-button export-action" :disabled="!analysis" @click="exportDecision"><Download :size="16" /><span>Скачать решение</span></button>
      </div>
    </header>

    <section class="map-context" v-if="analysis">
      <span class="map-context-pulse"></span>
      <strong>{{ analysis.chip.event }}</strong>
      <span>{{ analysis.chip.observed_at }}</span>
      <span class="context-separator"></span>
      <span>Sentinel‑1 · {{ analysis.chip.chip_id }}</span>
    </section>

    <aside class="side-panel observation-panel" :class="{ 'mobile-hidden': !panelVisible('observe') }" aria-label="Наблюдение">
      <div class="panel-top">
        <div class="panel-kicker"><span>01 / Наблюдение</span><span class="source-chip">S1 + модель</span></div>
        <h1>Где находится вода</h1>
        <p>Выберите снимок или область на карте. Все числа ниже относятся к этой территории.</p>
      </div>
      <div class="panel-scroll">
        <section class="panel-section scenes-section">
          <div class="section-heading"><h2>События</h2><span>{{ analysis?.scenes.length || 0 }} чипа</span></div>
          <div class="scene-list">
            <button v-for="scene in analysis?.scenes || []" :key="scene.chip_id" class="scene-button"
              :class="{ selected: sceneId === scene.chip_id }" @click="chooseScene(scene.chip_id)">
              <span class="scene-glyph"><Map :size="16" /></span>
              <span class="scene-copy"><strong>{{ scene.event }}</strong><small>{{ scene.observed_at }} · {{ scene.chip_id }}</small></span>
              <span class="scene-water">{{ km(scene.model_water_area_km2) }}</span>
            </button>
          </div>
        </section>

        <section class="panel-section region-section">
          <div class="section-heading"><h2>Область анализа</h2><span>{{ region ? 'выбрана на карте' : 'весь чип' }}</span></div>
          <div class="region-measure">
            <strong>{{ km(analysis?.region.area_km2) }}</strong>
            <span :class="{ warning: !hasData }">{{ hasData ? 'Данные доступны' : 'Нет S1 в этой области' }}</span>
          </div>
          <div class="region-actions">
            <button class="outline-button" :class="{ active: drawing }" @click="drawing = !drawing"><Crosshair :size="16" />{{ drawing ? 'Отменить выбор' : 'Выделить на карте' }}</button>
            <button class="icon-button small" title="Вернуться к чипу" aria-label="Вернуться к чипу" @click="resetRegion"><RotateCcw :size="16" /></button>
          </div>
          <p v-if="region" class="region-hint">Область выбирается двумя углами. Вне трёх загруженных чипов сервис не придумывает прогноз.</p>
        </section>

        <template v-if="hasData">
          <section class="panel-section water-summary">
            <div class="section-heading"><h2>Карта воды</h2><span>модель · p ≥ {{ analysis?.chip.model_threshold }}</span></div>
            <div class="hero-metric" :key="sceneId + String(region)"><strong>{{ number(analysis?.region.model_water_area_km2, 2) }}</strong><span>км²</span></div>
            <p>Обнаружено на {{ km(analysis?.region.observed_area_km2) }} Sentinel‑1. Это вода, без отделения постоянных водоёмов.</p>
            <div class="measure-pair">
              <div><span>Средняя вероятность</span><strong>{{ percent(analysis?.region.mean_probability, 1) }}</strong></div>
              <div><span>Неоднозначные пиксели</span><strong>{{ percent(analysis?.region.uncertain_share, 1) }}</strong></div>
            </div>
          </section>
          <section class="panel-section layers-section">
            <div class="section-heading"><h2>Что видно на карте</h2><Layers :size="16" /></div>
            <div class="layer-grid" role="group" aria-label="Слой карты">
              <button v-for="layer in [
                ['model', 'Маска', 'blue'], ['probability', 'Вероятность', 'blue'],
                ['uncertainty', 'Неуверенность', 'amber'], ['baseline', 'Порог', 'ochre'],
                ['reference', 'Ручная метка', 'teal'], ['none', 'Без слоя', 'plain']
              ]" :key="layer[0]" :class="{ selected: layerMode === layer[0] }"
                @click="layerMode = layer[0]" :aria-pressed="layerMode === layer[0]">
                <span class="layer-dot" :class="layer[2]"></span>{{ layer[1] }}
              </button>
            </div>
            <div class="layer-switches">
              <button :aria-pressed="showRadar" @click="showRadar = !showRadar"><Eye :size="15" />Радар VH <span class="switch" :class="{ on: showRadar }"></span></button>
              <button :aria-pressed="showZones" @click="showZones = !showZones"><Eye :size="15" />Зоны съёмки <span class="switch" :class="{ on: showZones }"></span></button>
            </div>
          </section>
        </template>
        <div v-else class="empty-message"><Globe2 :size="26" /><strong>Область сохранена</strong><span>Сейчас доступны три чипа Sen1Floods11. Выберите один из них для расчёта воды, объектов и закупки.</span></div>
        <p class="panel-footnote"><Info :size="14" /> S1, ручная метка и чипы — Sen1Floods11 v1.1. Маска и вероятности получены замороженным ансамблем U‑Net.</p>
      </div>
    </aside>

    <aside class="side-panel decision-panel" :class="{ 'mobile-hidden': !['assets', 'plan', 'quality'].includes(mobilePane) }" aria-label="Решение">
      <div class="decision-tabs" role="tablist" aria-label="Раздел решения">
        <button v-for="tab in [['assets','Объекты'],['plan','Съёмка'],['quality','Качество']]" :key="tab[0]"
          role="tab" :aria-selected="rightMode === tab[0]" :class="{ selected: rightMode === tab[0] }"
          @click="rightMode = tab[0]; mobilePane = tab[0]">{{ tab[1] }}</button>
      </div>
      <div v-if="error" class="error-box">{{ error }} <button @click="fetchAnalysis">Повторить</button></div>
      <div v-if="!hasData && analysis" class="decision-empty">
        <Globe2 :size="31" /><h2>Пока нет наблюдения</h2>
        <p>Регион выделен, но в локальном каталоге нет снимка. Вернитесь к одному из загруженных чипов.</p>
        <button class="outline-button" @click="resetRegion(); mobilePane = 'observe'">К чипу <ArrowRight :size="15" /></button>
      </div>
      <div v-else-if="hasData" class="decision-scroll">
        <template v-if="rightMode === 'assets'">
          <div class="decision-heading"><span class="panel-kicker">02 / Приоритеты</span><h2>Что проверить первым</h2><p>Ожидаемый прямой ущерб по вероятностям модели и учебной стоимости объектов.</p></div>
          <div class="loss-total"><span>Сумма EL по портфелю</span><strong>{{ compactRub(analysis?.metrics.expected_loss_rub) }}</strong><small>Σ p × стоимость × уязвимость · {{ analysis?.assets.length }} из 10 объектов</small></div>
          <Transition name="detail-reveal" mode="out-in">
            <div v-if="selectedAsset" :key="selectedAsset.id" class="asset-detail">
              <div class="detail-heading"><span>{{ selectedAsset.id }} · {{ selectedAsset.name }}</span><button class="icon-button small" aria-label="Закрыть объект" @click="selectedAssetId = null"><X :size="15" /></button></div>
              <strong class="detail-loss">{{ compactRub(selectedAsset.expected_loss_rub) }}</strong>
              <div class="detail-metrics">
                <div><span>Вероятность воды</span><b>{{ percent(selectedAsset.p_flood, 1) }}</b></div>
                <div><span>Разброс ансамбля</span><b>{{ percent(selectedAsset.uncertainty, 1) }}</b></div>
                <div><span>Стоимость · уязвимость</span><b>{{ compactRub(selectedAsset.value_rub) }} · {{ selectedAsset.vulnerability }}</b></div>
              </div>
              <div class="sensitivity"><span>Если p изменится на ±20%</span><strong>{{ compactRub(selectedAsset.expected_loss_rub * 0.8) }} — {{ compactRub(Math.min(1, selectedAsset.p_flood * 1.2) * selectedAsset.value_rub * selectedAsset.vulnerability) }}</strong></div>
              <button class="accent-button" :class="{ active: mustCover === selectedAsset.id }" @click="toggleGuarantee">
                <Check v-if="mustCover === selectedAsset.id" :size="16" /><Crosshair v-else :size="16" />
                {{ mustCover === selectedAsset.id ? 'Объект закреплён в плане' : 'Включить в план съёмки' }}
              </button>
            </div>
          </Transition>
          <div class="list-heading"><span>Рейтинг риска</span><span>EL / p воды</span></div>
          <div class="asset-list">
            <button v-for="(asset, index) in analysis?.assets || []" :key="asset.id"
              class="asset-row" :class="{ selected: selectedAssetId === asset.id }" @click="chooseAsset(asset.id)">
              <span class="asset-rank">{{ String(index + 1).padStart(2, '0') }}</span>
              <span class="asset-row-main"><strong>{{ asset.name }}</strong><small>{{ asset.id }} · p {{ percent(asset.p_flood) }}</small></span>
              <span class="asset-row-loss">{{ compactRub(asset.expected_loss_rub) }}</span>
              <ChevronRight :size="15" />
            </button>
          </div>
          <button class="next-link" @click="rightMode = 'plan'; mobilePane = 'plan'">Перейти к плану съёмки <ArrowRight :size="16" /></button>
        </template>

        <template v-else-if="rightMode === 'plan'">
          <div class="decision-heading"><span class="panel-kicker">03 / Заказ ДЗЗ</span><h2>Купить только нужное</h2><p>Выберите бюджет и срок. Корзина C пересчитывается на той же карте вероятностей.</p></div>
          <div class="budget-block">
            <label for="budget"><span>Бюджет на данные</span><strong>{{ rub(budget) }}</strong></label>
            <input id="budget" v-model.number="budget" type="range" min="0" max="45000" step="250"
              :style="{ '--progress': (budget / 45000 * 100) + '%' }" />
            <div class="range-ends"><span>0 ₽</span><span>45 000 ₽</span></div>
          </div>
          <div class="plan-control"><span>Решение через</span><div class="segmented"><button v-for="hours in [4,8,12]" :key="hours" :class="{ selected: deadline === hours }" @click="deadline = hours">{{ hours }} ч</button></div></div>
          <div class="plan-control"><span>Приоритет</span><div class="segmented"><button :class="{ selected: priority === 'risk' }" @click="priority = 'risk'">Ущерб</button><button :class="{ selected: priority === 'information' }" @click="priority = 'information'">Уточнение</button></div></div>
          <div v-if="mustCover" class="constraint-row"><Check :size="15" /> В плане обязателен {{ mustCover }} <button @click="mustCover = null" aria-label="Снять ограничение"><X :size="15" /></button></div>
          <div class="section-heading strategy-heading"><h3>Сравнение A / B / C</h3><span>один портфель</span></div>
          <div class="strategy-list">
            <button v-for="key in ['A','B','C']" :key="key" :class="{ selected: strategy === key }"
              @click="strategy = key; showZones = true">
              <span class="strategy-key">{{ key }}</span>
              <span class="strategy-name"><strong>{{ key === 'A' ? 'Открытые данные' : key === 'B' ? 'Широкая съёмка' : 'Приоритетная' }}</strong><small>{{ !analysis?.strategies[key].timely ? 'позже срока' : !analysis?.strategies[key].budget_feasible ? 'сверх бюджета' : key === 'A' ? 'без покупки' : 'в бюджете' }}</small></span>
              <span class="strategy-cost"><strong>{{ compactRub(analysis?.strategies[key].data_cost_rub) }}</strong><small>{{ percent(analysis?.strategies[key].coverage_share) }} охват</small></span>
            </button>
          </div>
          <Transition name="detail-reveal" mode="out-in">
            <div :key="strategy" class="plan-result">
              <div class="result-top"><span>{{ strategy }} · {{ activePlan?.label }}</span><strong>{{ rub(activePlan?.data_cost_rub) }}</strong></div>
              <div class="coverage-track"><span :style="{ transform: 'scaleX(' + (activePlan?.coverage_share || 0) + ')' }"></span></div>
              <div class="result-line"><span>Охват ожидаемого ущерба</span><b>{{ percent(activePlan?.coverage_share) }}</b></div>
              <div class="result-line"><span>Уникальных объектов</span><b>{{ activePlan?.covered_asset_ids?.length || 0 }} / {{ analysis?.assets.length }}</b></div>
              <div class="result-line"><span>Зон к заказу</span><b>{{ activePlan?.zone_ids?.length || 0 }}</b></div>
              <p v-if="activePlan?.reason" class="plan-reason">{{ activePlan.reason }}</p>
            </div>
          </Transition>
          <div v-if="strategy === 'C' && analysis?.metrics.data_savings_vs_broad_rub != null" class="impact-strip">
            <span>Широкая B → выборочная C</span>
            <strong>−{{ compactRub(analysis?.metrics.data_savings_vs_broad_rub) }}</strong>
            <small>Затраты на данные · {{ percent(analysis?.strategies.C.coverage_share) }} охвата модельного EL</small>
          </div>
          <div v-if="focusedZones.length" class="zones-detail">
            <div class="section-heading"><h3>Зоны на карте</h3><span>{{ focusedZones.length }}</span></div>
            <div v-for="zone in focusedZones" :key="zone.id" class="zone-row"><span>{{ zone.id }} · {{ zone.name }}</span><b>{{ km(zone.area_km2) }}</b></div>
          </div>
          <p class="panel-footnote"><Info :size="14" /> Тариф 788,64 ₽/км² и поставка через 6 ч — сценарий по формуле кейса. Охват EL не означает предотвращённые потери.</p>
        </template>

        <template v-else>
          <div class="decision-heading"><span class="panel-kicker">04 / Проверка</span><h2>Насколько верить карте</h2><p>Оба метода оценены на одинаковых валидных пикселях независимой части test_core.</p></div>
          <div class="quality-hero"><span>IoU · 55 тестовых чипов</span><div><small>Порог<br>{{ percent(testQuality?.baseline.iou, 1) }}</small><ArrowRight :size="18" /><strong>{{ percent(testQuality?.model.iou, 1) }}</strong></div><p>+{{ number((testQuality?.model.iou - testQuality?.baseline.iou) * 100, 1) }} п.п. к пороговому методу</p></div>
          <div class="quality-rows">
            <div v-for="metric in [['f1','F1 / Dice'],['recall','Полнота'],['precision','Точность']]" :key="metric[0]">
              <span>{{ metric[1] }}</span><span>{{ percent(testQuality?.baseline[metric[0]], 1) }} <ArrowRight :size="12" /> <strong>{{ percent(testQuality?.model[metric[0]], 1) }}</strong></span>
            </div>
          </div>
          <div class="quality-sample"><strong>{{ number(testQuality?.model.n_pixels) }}</strong><span>валидных пикселей в test_core</span></div>
          <div class="quality-block">
            <div class="section-heading"><h3>На выбранном участке</h3><span>по ручной метке</span></div>
            <div class="quality-rows">
              <div><span>IoU</span><span>{{ percent(analysis?.region.baseline_quality?.iou, 1) }} <ArrowRight :size="12" /> <strong>{{ percent(analysis?.region.model_quality?.iou, 1) }}</strong></span></div>
              <div><span>Пропущено воды</span><span>{{ number(analysis?.region.baseline_quality?.fn) }} <ArrowRight :size="12" /> <strong>{{ number(analysis?.region.model_quality?.fn) }}</strong></span></div>
              <div><span>Пикселей с меткой</span><strong>{{ number(analysis?.region.reference_pixels) }}</strong></div>
            </div>
          </div>
          <div class="quality-block">
            <div class="section-heading"><h3>Надёжность вероятностей</h3></div>
            <div class="quality-rows">
              <div><span>Ошибка калибровки ECE</span><span>{{ number(testQuality?.ece_raw, 3) }} <ArrowRight :size="12" /> <strong>{{ number(testQuality?.ece_calibrated, 3) }}</strong></span></div>
              <div><span>Неопределённость указывает на ошибки · AUROC</span><strong>{{ number(testQuality?.uncertainty_error_auroc, 3) }}</strong></div>
              <div><span>IoU на новом событии Bolivia</span><strong>{{ percent(analysis?.quality.parts.bolivia.model.iou, 1) }}</strong></div>
            </div>
          </div>
          <p class="panel-footnote"><Info :size="14" /> Ручная метка не отделяет постоянную воду. Учебные объекты и тариф не являются подтверждённым ущербом или предложением поставщика.</p>
        </template>
      </div>
    </aside>

    <div class="map-legend" v-if="analysis"><span class="legend-water"></span>{{ layerMode === 'model' ? 'Вода · модель' : layerMode === 'baseline' ? 'Вода · порог' : layerMode === 'reference' ? 'Вода · ручная метка' : layerMode === 'uncertainty' ? 'Неопределённость' : layerMode === 'probability' ? 'p воды' : 'Слой выключен' }}<span class="legend-point"></span> Объект <span class="legend-zone"></span> Зона</div>
    <div v-if="loading && analysis" class="refresh-indicator"><span></span> Обновляем расчёт</div>
    <nav class="mobile-dock" aria-label="Разделы сервиса">
      <button v-for="item in [
        ['map','Карта',Map],['observe','Вода',Waves],['assets','Объекты',Activity],
        ['plan','Съёмка',Crosshair],['quality','Качество',Check]
      ]" :key="item[0]" :class="{ selected: mobilePane === item[0] }" @click="setPane(item[0])">
        <component :is="item[2]" :size="18" /><span>{{ item[1] }}</span>
      </button>
    </nav>
    <div v-if="!analysis && !error" class="boot-loader"><span class="loader-ring"></span><strong>FloodValue</strong><span>Открываем наблюдение Sentinel‑1</span></div>
  </main>
</template>
