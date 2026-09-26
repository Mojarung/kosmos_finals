<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ArrowUpRight, Check, Cloud, Download, Expand, Map as MapIcon, RefreshCw, Satellite, X } from '@lucide/vue'
import { state, openProject, loadProjects, workspace } from '../store'
import { compactRub, plural } from '../lib/format'

const project = ref(null)
const projectDeadline = ref(24)
const projectMode = computed(() => !!project.value)
const catalog = ref(null)
const result = ref(null)
const focusedId = ref(null)
const selectedIds = ref([])
const busy = ref(false)
const planning = ref(false)
const error = ref('')
// Без ключа каталог SkyFi недоступен: не шлём заведомо проваленных запросов и объясняем, что делать.
const configured = ref(true)
const quoteMessage = ref('')
const enlarged = ref(false)
const view = ref('gallery')
const year = ref(null)
const sort = ref('recommended')
const onlyMatches = ref(false)
const failedImages = reactive(new Set())
const loadedPreviews = reactive(new Set())
const today = new Date().toISOString().slice(0, 10)
const eventDate = () => (state.analysis?.scenes?.find(s => s.chip_id === state.sceneId)?.observed_at || (state.analysis?.chip.chip_id === state.sceneId ? state.analysis.chip.observed_at : ''))?.slice(0, 10) || '2018-09-21'
const query = reactive({ from_date: eventDate(), to_date: today, market: 'commercial' })
const filters = reactive({ budget: 500, resolution_m: 1, max_cloud_percent: 30 })
const offers = computed(() => result.value?.offers || catalog.value?.offers || [])
const focused = computed(() => offers.value.find(o => o.id === focusedId.value) || offers.value[0])
const selected = computed(() => offers.value.filter(o => selectedIds.value.includes(o.id)))
const chosen = computed(() => offers.value.filter(o => result.value?.selected_ids.includes(o.id)))
const money = n => n == null ? '—' : new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(n)
const num = (n, digits = 1) => n == null ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(n)
const date = s => s ? new Date(s).toLocaleDateString('ru-RU') : '—'
const minPrice = computed(() => catalog.value?.offers.length ? Math.min(...catalog.value.offers.map(o => o.price)) : null)
const bestGsd = computed(() => catalog.value?.offers.length ? Math.min(...catalog.value.offers.map(o => o.resolution_m)) : null)
const maxPrice = computed(() => Math.max(1, ...offers.value.map(o => o.price), filters.budget))
const years = computed(() => {
  const counts = new Map()
  for (const o of offers.value) counts.set(o.capture_date.slice(0, 4), (counts.get(o.capture_date.slice(0, 4)) || 0) + 1)
  return [...counts].sort(([a], [b]) => a.localeCompare(b)).map(([label, count]) => ({ label, count }))
})
const maxYearCount = computed(() => Math.max(1, ...years.value.map(y => y.count)))
const shown = computed(() => {
  const list = offers.value.filter(o => (!year.value || o.capture_date.startsWith(year.value)) && (!onlyMatches.value || (projectMode.value ? o.project_eligible : o.eligible) && o.within_budget))
  return [...list].sort((a, b) => sort.value === 'price' ? a.price - b.price : sort.value === 'date' ? b.capture_date.localeCompare(a.capture_date) : sort.value === 'detail' ? a.resolution_m - b.resolution_m : Number(!!b.eligible && b.within_budget) - Number(!!a.eligible && a.within_budget) || (projectMode.value ? (b.covered_expected_loss_rub - a.covered_expected_loss_rub) : (b.coverage_percent - a.coverage_percent)) || (a.cloud_percent ?? 101) - (b.cloud_percent ?? 101) || a.price - b.price)
})
const eventGap = computed(() => {
  const days = focused.value?.days_from_event
  if (days == null) return ''
  if (days === 0) return 'Снято в день исходной сцены паводка.'
  return `Снято ${days > 0 ? 'после' : 'до'} исходной сцены: ${Math.abs(days)} дн. Этот кадр не подтверждает состояние паводка ${date(catalog.value.event_date)}.`
})
let disposed = false
let timer
let generation = 0
let controller

async function api(path, body, signal) {
  const response = await fetch('/api/procurement' + path, { method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Проверьте даты и параметры территории')
  return data
}
async function projectApi(path, body, signal) {
  const response = await fetch(`/api/projects/${project.value.id}/imagery${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось подготовить проектную заявку')
  return data
}
function payload() { return { catalog_id: catalog.value.id, ...filters, selected_ids: [...selectedIds.value],
  ...(projectMode.value ? { deadline_hours: projectDeadline.value } : {}) } }
function backToProject() { state.procurementOpen = false; state.procurementProjectId = null; state.tab = 'projects' }
async function calculate() {
  if (!catalog.value || disposed) return
  const token = ++generation
  controller?.abort()
  controller = new AbortController()
  planning.value = true
  try {
    const next = projectMode.value ? await projectApi('/plan', payload(), controller.signal) : await api('/plan', payload(), controller.signal)
    if (disposed || token !== generation) return
    result.value = next
    if (!focusedId.value) focusedId.value = next.recommendation?.ids[0] || shown.value[0]?.id
    updateMap()
  } catch (e) {
    if (e.name !== 'AbortError' && token === generation) error.value = e.message
  } finally { if (token === generation) planning.value = false }
}
async function search() {
  busy.value = true
  error.value = ''
  quoteMessage.value = ''
  generation++
  controller?.abort()
  clearTimeout(timer)
  try {
    const next = await api('/catalogs', { scene_id: state.sceneId, source: 'skyfi', ...query,
      ...(state.region ? { bbox: state.region } : {}) })
    if (disposed) return
    catalog.value = next
    result.value = null
    selectedIds.value = []
    focusedId.value = null
    year.value = null
    await calculate()
  } catch (e) { if (!disposed) error.value = e.message }
  finally { busy.value = false }
}
function focus(offer) {
  focusedId.value = offer.id
  quoteMessage.value = ''
  updateMap()
}
function toggle(offer) {
  if (selectedIds.value.includes(offer.id)) selectedIds.value = selectedIds.value.filter(id => id !== offer.id)
  else if (selectedIds.value.length < 4) selectedIds.value = [...selectedIds.value, offer.id]
  focus(offer)
}
function updateMap() {
  const ids = new Set([...selectedIds.value, focusedId.value])
  state.procurementOverlay = (catalog.value?.offers || []).filter(o => ids.has(o.id)).map(o => ({ type: 'Feature', geometry: o.coverage_geometry,
    properties: { id: o.id, kind: 'object', active: 1, hover: o.id === focusedId.value ? 1 : 0 } }))
  state.showZones = true
}
function useRecommendation() {
  selectedIds.value = [...(result.value?.recommendation?.ids || [])]
  view.value = 'compare'
}
async function quote() {
  if (!focused.value) return
  busy.value = true
  quoteMessage.value = ''
  try {
    const next = await api('/catalogs/' + catalog.value.id + '/quotes/' + encodeURIComponent(focused.value.id), {})
    if (disposed) return
    catalog.value = next
    await calculate()
    quoteMessage.value = 'SkyFi подтвердил оценку цены для этого контура.'
  } catch (e) { quoteMessage.value = e.message }
  finally { busy.value = false }
}
async function download() {
  busy.value = true
  error.value = ''
  try {
    if (projectMode.value) {
      const saved = await projectApi('/requests', payload())
      await openProject(project.value.id)
      await loadProjects()
      workspace.notice = `Черновик заявки ${saved.id} сохранён. Заказ не отправлен.`
      backToProject()
      return
    }
    const saved = await api('/decisions', payload())
    const a = document.createElement('a')
    a.href = '/api/procurement/decisions/' + saved.id + '/export'
    a.download = 'imagery-' + saved.id + '.zip'
    a.click()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
function eventPeriod() {
  const end = new Date(eventDate())
  end.setUTCDate(end.getUTCDate() + 7)
  query.from_date = eventDate()
  query.to_date = end.toISOString().slice(0, 10) > today ? today : end.toISOString().slice(0, 10)
  search()
}
function onKeydown(event) { if (event.key === 'Escape') enlarged.value = false }
function entireArchive() { query.from_date = eventDate(); query.to_date = today; search() }
function mapPath(geometry) {
  if (!geometry || !catalog.value) return ''
  const [w, s, e, n] = catalog.value.bbox
  const project = ([x, y]) => [35 + (x - w) / (e - w) * 250, 140 - (y - s) / (n - s) * 110]
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : []
  return polygons.flatMap(poly => poly.map(ring => ring.map((point, i) => (i ? 'L' : 'M') + project(point).map(v => v.toFixed(2)).join(',')).join(' ') + 'Z')).join(' ')
}
const chartX = price => 40 + price / maxPrice.value * 510
const chartY = coverage => 155 - coverage / 100 * 125
function color(o) { return o.id === focusedId.value ? '#e4ad6a' : o.eligible ? '#56b6ae' : '#71818f' }
watch([filters, selectedIds, projectDeadline], () => {
  if (busy.value) return
  generation++
  controller?.abort()
  planning.value = true
  clearTimeout(timer)
  timer = setTimeout(calculate, 180)
}, { deep: true })
onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
  state.procurementMapMode = false
  try {
    if (state.procurementProjectId) {
      const response = await fetch('/api/projects/' + encodeURIComponent(state.procurementProjectId))
      if (!response.ok) throw new Error('Проект не найден. Вернитесь к списку проектов.')
      const loaded = await response.json()
      if (loaded.scene_id !== state.sceneId) throw new Error('Проект относится к другому снимку.')
      if (!loaded.assets?.length) throw new Error('Импортируйте объекты проекта перед подбором снимков.')
      project.value = loaded
    }
    configured.value = (await api('/status').catch(() => ({ skyfi_configured: true }))).skyfi_configured !== false
    if (!configured.value || disposed) return
    if (state.region) { await search(); return }
    const saved = await api('/catalogs/latest?scene_id=' + encodeURIComponent(state.sceneId))
    if (disposed) return
    catalog.value = saved
    Object.assign(query, { market: saved.market, from_date: saved.from_date, to_date: saved.to_date })
    await calculate()
  } catch (cause) {
    if (disposed) return
    if (projectMode.value || !state.procurementProjectId) await search()
    else error.value = cause.message
  }
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  disposed = true
  clearTimeout(timer)
  controller?.abort()
  state.procurementOverlay = null
  state.procurementMapMode = false
})
</script>

<template>
  <div class="imagery" :class="{ 'map-mode': state.procurementMapMode }" data-testid="procurement">
    <header class="imagery-heading">
      <div><span class="eyebrow"><Satellite :size="13" /> SKYFI · РЕАЛЬНЫЙ КАТАЛОГ</span><h2>{{ projectMode ? 'Снимки для объектов' : 'Снимки для территории' }}</h2><p>{{ state.sceneId }} <template v-if="catalog">· {{ num(catalog.area_km2, 2) }} км²</template></p></div>
      <button class="quiet-button" @click="state.procurementMapMode = !state.procurementMapMode"><MapIcon :size="15" />{{ state.procurementMapMode ? 'Открыть каталог' : 'На карте' }}</button>
    </header>
    <div v-if="projectMode" class="project-context" data-testid="project-imagery-context">
      <div><span class="eyebrow">ПРОЕКТ · {{ project.name }}</span><strong>{{ plural(result?.asset_count ?? project.assets.length, ['объект', 'объекта', 'объектов']) }} · EL {{ compactRub(result?.total_expected_loss_rub ?? project.assets.reduce((sum, asset) => sum + (asset.expected_loss_rub || 0), 0)) }}</strong><p>Риск оценён для {{ result?.estimated_asset_count ?? project.assets.filter(asset => asset.expected_loss_rub != null).length }} объектов. Архив в USD, A/B/C в ₽. Заказ не отправляется.</p></div>
      <button class="text-button" @click="backToProject">Вернуться к проекту</button>
    </div>
    <div v-if="!state.procurementMapMode" class="search-toolbar">
      <details class="search-settings"><summary>{{ date(query.from_date) }} … {{ date(query.to_date) }} · {{ query.market === 'commercial' ? 'Коммерческий архив' : 'Открытые данные' }} <span>Изменить</span></summary><div class="search-fields">
      <label>Снято с<input type="date" v-model="query.from_date" aria-label="Начало периода съёмки" :disabled="busy" /></label>
      <label>По<input type="date" v-model="query.to_date" :max="today" aria-label="Конец периода съёмки" :disabled="busy" /></label>
      <label>Каталог<select v-model="query.market" aria-label="Тип предложений" :disabled="busy"><option value="commercial">Коммерческий</option><option value="open">Открытые данные</option></select></label>
      </div></details>
      <button class="primary-button" :disabled="busy || !configured" @click="search"><RefreshCw :size="15" :class="{ spin: busy }" />{{ busy ? 'Поиск в SkyFi…' : 'Обновить из SkyFi' }}</button>
    </div>
    <div v-if="!state.procurementMapMode" class="source-strip"><span>{{ catalog ? 'Ответ SkyFi от ' + date(catalog.captured_at) + ', ' + new Date(catalog.captured_at).toLocaleTimeString('ru-RU') : configured ? 'Ищем доступные архивные кадры и превью поставщиков' : 'Каталог SkyFi не подключён на этом сервере' }}</span><button :disabled="busy || !configured" @click="eventPeriod">Неделя паводка</button><button :disabled="busy || !configured" @click="entireArchive">Весь архив территории</button></div>
    <div v-if="!configured && !catalog" class="catalog-empty" role="status" data-testid="skyfi-not-configured"><Satellite :size="36" /><h3>Каталог SkyFi не подключён</h3><p>Реальные архивные снимки и цены поставщиков приходят из SkyFi по ключу API: укажите SKYFI_API_KEY в .env рядом с compose.yaml (образец в .env.example) и перезапустите сервис. Покупок сервис не делает. Без ключа работает расчёт по ТЗ: A/B/C в рублях по ПП № 840 и выгрузка комплекта.</p><button class="primary-button" @click="projectMode ? backToProject() : (state.procurementOpen = false)">{{ projectMode ? 'Вернуться к проекту' : 'Открыть расчёт по ТЗ' }}</button></div>
    <p v-if="error" class="imagery-error" role="alert">{{ error }}</p>
    <div v-if="!catalog && busy" class="catalog-loading" role="status"><Satellite :size="36" /><h3>Получаем снимки из SkyFi</h3><p>Запрашиваем реальные контуры, цены и изображения поставщиков.</p></div>
    <div v-else-if="catalog && !catalog.offers.length" class="catalog-empty" role="status"><Satellite :size="36" /><h3>В этом периоде снимков не найдено</h3><p>Исходная сцена паводка снята {{ date(catalog.event_date) }}. Можно посмотреть весь доступный архив территории.</p><button class="primary-button" :disabled="busy" @click="entireArchive">Показать весь архив</button></div>
    <template v-else-if="catalog">
      <div v-if="!state.procurementMapMode" class="catalog-metrics">
        <div><b>{{ catalog.offers.length }}</b><span>снимков из SkyFi</span></div><div><b>{{ money(minPrice) }}</b><span>минимальная цена в выборке</span></div><div><b>{{ num(bestGsd, 2) }} м</b><span>лучшая детализация</span></div><div><b>{{ projectMode ? (result?.offers || []).filter(o => o.covered_asset_ids?.length).length : catalog.offers.filter(o => o.coverage_percent >= 95).length }}</b><span>{{ projectMode ? 'покрывают ваши объекты' : 'покрывают ≥95% территории' }}</span></div>
      </div>
      <div class="imagery-layout">
        <main v-if="!state.procurementMapMode" class="catalog-main">
          <div class="catalog-filters">
            <label>Лимит на данные, $<input v-model.number="filters.budget" type="number" min="0" max="1000000000" aria-label="Бюджет закупки" :disabled="busy" /></label>
            <label>Детализация<select v-model.number="filters.resolution_m" aria-label="Детализация закупки" :disabled="busy"><option :value="0.5">До 0,5 м</option><option :value="1">До 1 м</option><option :value="10">До 10 м</option><option :value="100">Любая</option></select></label>
            <label v-if="projectMode">Получить за, ч<select v-model.number="projectDeadline" aria-label="Срок получения снимка" :disabled="busy"><option :value="8">8 часов</option><option :value="12">12 часов</option><option :value="24">24 часа</option><option :value="48">48 часов</option><option :value="168">7 дней</option></select></label>
            <label>Облачность<select v-model.number="filters.max_cloud_percent" aria-label="Облачность" :disabled="busy"><option :value="10">До 10%</option><option :value="30">До 30%</option><option :value="60">До 60%</option><option :value="100">Любая / неизвестна</option></select></label>
          </div>
          <div v-if="result" class="recommendation" :class="{ pending: planning }" data-testid="recommendation" :aria-busy="planning">
            <template v-if="result.recommendation"><div><span class="eyebrow">ВАРИАНТ ПО УСЛОВИЯМ</span><b v-if="projectMode">{{ plural(result.recommendation.covered_asset_count, ['объект', 'объекта', 'объектов']) }} · {{ num(result.recommendation.covered_el_percent) }}% EL за {{ money(result.recommendation.cost) }}</b><b v-else>{{ num(result.recommendation.coverage_percent) }}% территории за {{ money(result.recommendation.cost) }}</b><p>{{ result.recommendation.ids.length }} снимка · {{ projectMode ? 'приоритет осмотра, не предотвращённый ущерб' : 'оценка по тарифам каталога' }}</p></div><button class="quiet-button" :disabled="busy || planning" @click="useRecommendation">Сравнить <ArrowUpRight :size="16" /></button></template>
            <template v-else><div><b>Варианта в заданных условиях нет</b><p v-if="projectMode">Проверьте бюджет, срок получения и пересечение кадров с объектами. Без подходящего кадра черновик можно собрать вручную.</p><p v-else-if="result.minimum_full_cost != null">Покрытие ≥95% доступно от {{ money(result.minimum_full_cost) }}. Сейчас лимит {{ money(filters.budget) }}.</p><p v-else>Посмотрите облачность, детализацию и ограничения контура на карточках. Все найденные снимки остаются доступны ниже.</p></div></template>
          </div>
          <div class="gallery-toolbar"><div class="catalog-tabs"><button :class="{ active: view === 'gallery' }" @click="view = 'gallery'">Галерея <span>{{ shown.length }}</span></button><button :class="{ active: view === 'compare' }" @click="view = 'compare'">Сравнение <span>{{ selected.length }}</span></button><button :class="{ active: view === 'analytics' }" @click="view = 'analytics'">Графики</button></div><select v-if="view === 'gallery'" v-model="sort" aria-label="Сортировка снимков"><option value="recommended">Сначала подходящие</option><option value="date">Сначала новые</option><option value="price">По цене</option><option value="detail">По детализации</option></select></div>
          <label v-if="view === 'gallery'" class="matches-toggle"><input v-model="onlyMatches" type="checkbox" />Только в пределах лимита и фильтров</label>
          <div v-if="view === 'gallery'" class="imagery-gallery" data-testid="imagery-gallery">
            <article v-for="(o, index) in shown" :key="o.id" class="image-card" :class="{ focused: o.id === focusedId, compared: selectedIds.includes(o.id) }" :data-testid="'image-' + o.id">
              <button class="image-card-main" :aria-label="'Открыть ' + o.title + ' ' + o.capture_date" @click="focus(o)"><div class="image-cover"><img v-if="o.preview_url && !failedImages.has(o.id)" :src="o.preview_url" :alt="o.title + ' · ' + o.capture_date" :loading="index < 6 ? 'eager' : 'lazy'" :fetchpriority="index < 2 ? 'high' : 'auto'" @load="loadedPreviews.add(o.id)" referrerpolicy="no-referrer" @error="failedImages.add(o.id)" /><div v-else class="image-unavailable"><Satellite :size="27" /><span>Превью поставщика недоступно</span></div><div v-if="o.preview_url && !failedImages.has(o.id) && !loadedPreviews.has(o.id)" class="preview-loading"><Satellite :size="21" /><span>Загружаем превью SkyFi…</span></div><span class="provider-chip">{{ o.provider }}</span><span class="resolution-chip">{{ num(o.resolution_m, 2) }} м</span></div><div class="image-card-info"><div><b>{{ o.title }}</b><span>{{ date(o.capture_date) }}</span></div><div class="image-price"><strong>{{ money(o.price) }}</strong><span>{{ projectMode ? o.covered_asset_ids?.length + ' ваших объектов' : num(o.coverage_percent) + '% территории' }}</span></div><div class="image-meter"><i :style="{ width: (projectMode ? o.covered_el_percent || 0 : o.coverage_percent) + '%' }" /></div><p><Cloud :size="12" />{{ o.cloud_percent == null ? 'Облачность неизвестна' : num(o.cloud_percent) + '% облачности' }}<span v-if="o.filter_reasons?.length" class="filter-warning">Вне условий</span></p></div></button>
              <button class="compare-button" :disabled="busy || selected.length >= 4 && !selectedIds.includes(o.id)" :aria-pressed="selectedIds.includes(o.id)" @click="toggle(o)"><Check v-if="selectedIds.includes(o.id)" :size="14" />{{ selectedIds.includes(o.id) ? 'В сравнении' : 'Добавить к сравнению' }}</button>
            </article>
            <div v-if="!shown.length" class="no-filter-results">Фильтры скрыли все кадры. <button class="text-button" @click="onlyMatches = false; year = null">Показать весь каталог</button></div>
          </div>
          <div v-else-if="view === 'analytics'" class="catalog-charts">
            <section class="scatter-chart"><div class="section-heading"><h3>Цена и покрытие</h3><span>Нажмите на точку → снимок</span></div>
              <svg viewBox="0 0 580 190" role="img" aria-label="График цены снимков и покрытия территории">
                <g v-for="level in [0, 50, 100]" :key="level"><line x1="40" x2="550" :y1="chartY(level)" :y2="chartY(level)" class="chart-grid" /><text x="32" :y="chartY(level) + 4" text-anchor="end">{{ level }}%</text></g>
                <line :x1="chartX(filters.budget)" :x2="chartX(filters.budget)" y1="18" y2="155" class="budget-line" /><text :x="Math.min(515, chartX(filters.budget) + 5)" y="15" class="budget-label">Лимит</text>
                <g v-for="o in offers" :key="o.id" class="chart-dot" role="button" tabindex="0" :aria-label="o.title + ', ' + date(o.capture_date) + ', ' + money(o.price)" @click="focus(o)" @keydown.enter="focus(o)" @keydown.space.prevent="focus(o)"><circle :cx="chartX(o.price)" :cy="chartY(o.coverage_percent)" :r="o.id === focusedId ? 8 : 5" :fill="color(o)" /><title>{{ o.title }} · {{ date(o.capture_date) }} · {{ money(o.price) }} · {{ o.coverage_percent }}%</title></g>
                <text x="40" y="180">$0</text><text x="300" y="180" text-anchor="middle">{{ money(maxPrice / 2) }}</text><text x="550" y="180" text-anchor="end">{{ money(maxPrice) }}</text>
              </svg><div class="chart-legend"><span><i class="teal" />Проходит фильтры</span><span><i />Вне условий</span><span><i class="gold" />Выбранный кадр</span></div>
            </section>
            <section class="timeline-chart"><div class="section-heading"><h3>Когда снимали</h3><button v-if="year" class="text-button" @click="year = null">Все даты</button></div><div class="timeline-bars"><button v-for="y in years" :key="y.label" :class="{ active: year === y.label }" :aria-label="'Снимки за ' + y.label" @click="year = year === y.label ? null : y.label; view = 'gallery'"><span>{{ y.count }}</span><i :style="{ height: 14 + y.count / maxYearCount * 68 + 'px' }" /><small>{{ y.label }}</small></button></div><p>Исходный паводок: <b>{{ date(catalog.event_date) }}</b>. Даты архива видны на каждой карточке.</p></section>
          </div>
          <div v-else class="comparison-view" data-testid="comparison">
            <div v-if="!selected.length" class="catalog-empty"><h3>Выберите до четырёх снимков</h3><p>Добавьте кадры из галереи, чтобы сравнить изображения, цену и фактическое покрытие.</p><button class="quiet-button" @click="view = 'gallery'">Открыть галерею</button></div>
            <template v-else><div class="compare-grid" :style="{ '--columns': selected.length }"><article v-for="o in selected" :key="o.id"><div class="comparison-image"><img v-if="o.preview_url && !failedImages.has(o.id)" :src="o.preview_url" :alt="o.title" referrerpolicy="no-referrer" @error="failedImages.add(o.id)" /><button :aria-label="'Убрать ' + o.title" @click="toggle(o)"><X :size="14" /></button></div><b>{{ o.title }}</b><span>{{ date(o.capture_date) }}</span><strong>{{ money(o.price) }}</strong><dl><dt>Детализация</dt><dd>{{ num(o.resolution_m, 2) }} м</dd><dt>Территория</dt><dd>{{ num(o.coverage_percent) }}%</dd><template v-if="projectMode"><dt>Ваши объекты</dt><dd>{{ o.covered_asset_ids?.length || 0 }} · EL {{ compactRub(o.covered_expected_loss_rub) }}</dd></template><dt>Облачность</dt><dd>{{ o.cloud_percent == null ? 'Неизвестна' : num(o.cloud_percent) + '%' }}</dd><dt>Получение</dt><dd>{{ o.delivery_hours == null ? 'Неизвестно' : num(o.delivery_hours) + ' ч' }}</dd></dl><button class="text-button" @click="focus(o)">Контур и источник</button></article></div>
            <div v-if="result && !planning" class="portfolio" data-testid="portfolio"><svg viewBox="0 0 120 120" aria-label="Доля территории, покрытой выбранными снимками"><circle cx="60" cy="60" r="45" class="donut-track" /><circle cx="60" cy="60" r="45" class="donut-value" :stroke-dasharray="(result.coverage_percent / 100 * 282.74) + ' 282.74'" /><text x="60" y="65" text-anchor="middle">{{ num(result.coverage_percent) }}%</text></svg><div><span>Все выбранные снимки вместе</span><strong>{{ money(result.cost) }}</strong><p v-if="projectMode"><b>{{ result.covered_asset_count }} из {{ result.asset_count }}</b> ваших объектов · EL {{ compactRub(result.covered_expected_loss_rub) }} · {{ num(result.covered_el_percent) }}% суммы EL</p><p>{{ num(result.coverage_km2, 2) }} км² охвачено · {{ num(result.uncovered_km2, 2) }} км² осталось</p><p>{{ num(result.overlap_km2, 2) }} км² повторного покрытия учтено один раз в охвате.</p><p v-for="blocker in result.blockers || []" :key="blocker" class="filter-warning">{{ blocker }}</p><p v-if="result.cost > filters.budget" class="filter-warning">Превышение лимита: {{ money(result.cost - filters.budget) }}</p></div></div></template>
          </div>
          <p class="catalog-footnote">Цены из SkyFi: предварительный расчёт для пересечения кадра с территорией. Облачность относится ко всему кадру. {{ catalog.truncated ? 'Показаны первые ' + catalog.pages + ' страницы каталога.' : 'Все страницы ответа загружены.' }} <span v-if="catalog.skipped">Пропущено некорректных записей: {{ catalog.skipped }}.</span></p>
        </main>
        <aside v-if="focused" class="image-inspector" data-testid="image-inspector">
          <div class="inspector-heading"><div><span class="eyebrow">ВЫБРАННЫЙ СНИМОК</span><h3>{{ focused.title }} <span>{{ focused.provider }}</span></h3></div><button class="icon-button" aria-label="Увеличить превью" @click="enlarged = true" :disabled="!focused.preview_url"><Expand :size="17" /></button></div>
          <button class="inspector-preview" @click="enlarged = true" :disabled="!focused.preview_url"><img v-if="focused.preview_url && !failedImages.has(focused.id)" :src="focused.preview_url" :alt="'Превью ' + focused.title" referrerpolicy="no-referrer" @error="failedImages.add(focused.id)" /><div v-else class="image-unavailable"><Satellite :size="36" /><span>Поставщик не предоставил доступное превью</span></div><span>{{ date(focused.capture_date) }} · превью целого кадра</span></button>
          <div class="inspector-price"><div><span>{{ focused.quote_validated ? 'Оценка проверена SkyFi' : 'Оценка по каталогу' }}</span><strong>{{ money(focused.price) }}</strong></div><div><span>Детализация</span><b>{{ num(focused.resolution_m, 2) }} м</b></div></div>
          <div v-if="projectMode" class="project-offer-metrics"><b>{{ plural(focused.covered_asset_ids?.length || 0, ['ваш объект', 'ваших объекта', 'ваших объектов']) }}</b><span>EL {{ compactRub(focused.covered_expected_loss_rub) }} · {{ num(focused.covered_el_percent) }}%</span><small v-if="!focused.timely" class="filter-warning">Не укладывается в срок {{ projectDeadline }} ч</small></div>
          <div class="footprint-heading"><b>Пересечение с территорией</b><span>{{ num(focused.coverage_percent) }}%</span></div>
          <svg class="footprint-map" viewBox="0 0 320 170" role="img" aria-label="Контур покрытия выбранного снимка"><defs><pattern id="footprint-grid" width="25" height="22" patternUnits="userSpaceOnUse"><path d="M 25 0 L 0 0 0 22" fill="none" stroke="currentColor" stroke-width=".5" opacity=".2" /></pattern><clipPath id="footprint-clip"><rect width="320" height="170" /></clipPath></defs><rect width="320" height="170" fill="url(#footprint-grid)" /><g clip-path="url(#footprint-clip)"><path :d="mapPath(focused.footprint)" fill="none" stroke="#e4ad6a" stroke-width="1.5" stroke-dasharray="4 4" /><rect x="35" y="30" width="250" height="110" class="aoi-rect" /><path :d="mapPath(focused.coverage_geometry)" class="footprint-fill" /></g><text x="12" y="18">N ↑</text><text x="160" y="160" text-anchor="middle">{{ num(focused.coverage_km2, 2) }} из {{ num(catalog.area_km2, 2) }} км²</text></svg>
          <div class="inspector-facts"><div><Cloud :size="14" /><span>Облачность</span><b>{{ focused.cloud_percent == null ? 'Неизвестна' : num(focused.cloud_percent) + '%' }}</b></div><div><span>Получение после заказа</span><b>{{ focused.delivery_hours == null ? 'Неизвестно' : num(focused.delivery_hours) + ' ч' }}</b></div><div><span>{{ focused.full_scene_only ? 'Целый кадр' : 'Тариф за км²' }}</span><b>{{ focused.full_scene_only ? money(focused.price) : money(focused.unit_price) }}</b></div></div>
          <p class="date-context">{{ eventGap }}</p>
          <p v-if="focused.order_issue" class="imagery-warning">{{ focused.order_issue }}. Эта позиция доступна для сравнения, но не включается в автоматический подбор.</p>
          <button class="primary-button wide" :disabled="busy || selected.length >= 4 && !selectedIds.includes(focused.id)" @click="toggle(focused)">{{ selectedIds.includes(focused.id) ? 'Убрать из сравнения' : 'Добавить к сравнению' }}</button>
          <button class="quiet-button wide" :disabled="busy || !!focused.order_issue" @click="quote">Проверить цену в SkyFi <ArrowUpRight :size="14" /></button>
          <p v-if="quoteMessage" role="status" class="quote-message">{{ quoteMessage }}</p>
          <button class="quiet-button wide" :disabled="busy || planning || !selected.length" @click="download"><Download v-if="!projectMode" :size="14" /><Check v-else :size="14" />{{ projectMode ? 'Сохранить черновик заявки' : 'Скачать сравнение' }}</button>
          <details class="source-details"><summary>Источник и метод расчёта</summary><p>SkyFi API · получено {{ date(catalog.captured_at) }}</p><p class="archive-id">Archive ID: {{ focused.archive_id }}</p><p>Контур и цена относятся к территории {{ num(catalog.area_km2, 2) }} км². Готовность изображения к анализу затопления по превью не подтверждается.</p><p>{{ result?.method }}</p><a href="https://app.skyfi.com" target="_blank" rel="noopener noreferrer">Открыть SkyFi ↗</a></details>
        </aside>
      </div>
    </template>
    <Teleport to="body"><div v-if="enlarged && focused" class="image-lightbox" role="dialog" aria-modal="true" aria-label="Превью спутникового снимка" @click.self="enlarged = false" @keydown.esc="enlarged = false"><button autofocus class="lightbox-close" aria-label="Закрыть превью" @click="enlarged = false"><X :size="24" /></button><img :src="focused.preview_url" :alt="focused.title + ' ' + date(focused.capture_date)" referrerpolicy="no-referrer" /><div><b>{{ focused.title }} · {{ date(focused.capture_date) }}</b><p>Превью SkyFi · {{ focused.provider }} · {{ num(focused.resolution_m, 2) }} м · {{ money(focused.price) }}</p></div></div></Teleport>
  </div>
</template>

<style scoped>
.imagery { font-size: 13px; --image-accent: #267b78; --image-gold: #b87b3b; }
.imagery-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 0 12px; }
.eyebrow { display: flex; align-items: center; gap: 6px; font-size: 10px; letter-spacing: 1.1px; font-weight: 650; color: var(--image-accent); }
.imagery h2 { font-size: 27px; letter-spacing: -.9px; margin: 5px 0; }
.imagery h3 { font-size: 14px; margin: 0; }
.imagery p { line-height: 1.5; }
.imagery-heading p { margin: 0; color: var(--muted-foreground); font-size: 12px; }
.project-context { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 13px 0; border-block: 1px solid var(--border); } .project-context strong { display: block; margin-top: 5px; font-size: 16px; } .project-context p { color: var(--muted-foreground); font-size: 11px; margin: 5px 0 0; } .project-context .text-button { white-space: nowrap; } @media (max-width: 760px) { .project-context { align-items: flex-start; flex-direction: column; } }
.project-offer-metrics { display: flex; flex-direction: column; gap: 5px; padding: 12px 0; border-bottom: 1px solid var(--border); } .project-offer-metrics span { color: var(--muted-foreground); font-size: 11px; } .project-offer-metrics small { font-size: 10px; }
.imagery button { cursor: pointer; }
.imagery button:disabled { cursor: default; opacity: .5; }
.imagery button:focus-visible, .imagery [tabindex]:focus-visible { outline: 2px solid var(--image-accent); outline-offset: 3px; }
.search-toolbar { display: flex; align-items: start; gap: 10px; }.search-settings { flex: 1; border: 1px solid var(--border); border-radius: 8px; padding: 11px; min-width: 0; }.search-settings summary { font-size: 11px; cursor: pointer; }.search-settings summary span { color: var(--image-accent); margin-left: 8px; }.search-fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; padding-top: 12px; }
.imagery label { display: flex; flex-direction: column; gap: 5px; font-size: 11px; color: var(--muted-foreground); }
.imagery input:not([type=checkbox]), .imagery select { width: 100%; min-width: 0; background: var(--background); border: 1px solid var(--border); border-radius: 8px; color: var(--foreground); padding: 9px 8px; font: inherit; font-size: 12px; }
.quiet-button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; padding: 9px 12px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface-2); color: var(--foreground); font: inherit; font-size: 12px; }
.source-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 10px 0; font-size: 10px; color: var(--muted-foreground); }
.source-strip span { margin-right: auto; }
.source-strip button, .text-button { color: var(--image-accent); background: none; border: 0; padding: 2px 0; font: inherit; }
.catalog-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; padding: 12px 0 16px; border-top: 1px solid var(--border); }
.catalog-metrics div { display: flex; flex-direction: column; gap: 5px; border-left: 2px solid var(--image-accent); padding-left: 12px; }
.catalog-metrics div:nth-child(2) { border-color: #c38a50; }.catalog-metrics div:nth-child(3) { border-color: #7195b9; }.catalog-metrics div:nth-child(4) { border-color: #969761; }
.catalog-metrics b { font-size: 24px; letter-spacing: -.6px; font-variant-numeric: tabular-nums; }
.catalog-metrics span { font-size: 10px; color: var(--muted-foreground); }
.imagery-layout { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 24px; }
.catalog-main { min-width: 0; }
.catalog-charts { display: grid; grid-template-columns: minmax(0, 1fr); gap: 26px; padding: 15px 0; border-bottom: 1px solid var(--border); }
.section-heading { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.section-heading span { color: var(--muted-foreground); font-size: 9px; }
.scatter-chart svg { width: 100%; max-height: 240px; display: block; margin-top: 9px; overflow: visible; }
.scatter-chart text { font-size: 12px; fill: var(--muted-foreground); }
.chart-grid { stroke: var(--border); stroke-width: 1; }.budget-line { stroke: #b87b3b; stroke-dasharray: 4 4; opacity: .7; }.scatter-chart .budget-label { fill: #b87b3b; }
.chart-dot { cursor: pointer; }.chart-dot circle { stroke: var(--background); stroke-width: 2; transition: r .2s; }
.chart-dot:hover circle { r: 9; }
.chart-legend { display: flex; gap: 12px; font-size: 9px; color: var(--muted-foreground); }.chart-legend span { display: flex; gap: 4px; align-items: center; }.chart-legend i { width: 6px; height: 6px; border-radius: 50%; background: #71818f; }.chart-legend i.teal { background: #56b6ae; }.chart-legend i.gold { background: #e4ad6a; }
.timeline-bars { display: flex; align-items: end; gap: 5px; height: 110px; }
.timeline-bars button { display: flex; flex-direction: column; align-items: center; justify-content: end; gap: 5px; flex: 1; min-width: 0; border: 0; padding: 0; background: none; font: inherit; color: var(--muted-foreground); font-size: 10px; }
.timeline-bars i { width: 100%; max-width: 30px; display: block; background: #9bb7b8; border-radius: 3px 3px 0 0; }.timeline-bars button.active i { background: var(--image-gold); }.timeline-bars small { font-size: 9px; }.timeline-chart p { font-size: 9px; color: var(--muted-foreground); margin: 9px 0 0; }
.catalog-filters { display: grid; grid-template-columns: repeat(auto-fit, minmax(115px, 1fr)); gap: 10px; margin: 12px 0 10px; }
.recommendation { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 9px 12px; background: color-mix(in srgb, var(--image-accent) 8%, transparent); border-left: 3px solid var(--image-accent); border-radius: 0 8px 8px 0; }
.recommendation b { display: block; margin: 5px 0; font-size: 16px; }.recommendation p { font-size: 10px; margin: 0; color: var(--muted-foreground); }.recommendation .eyebrow { font-size: 8px; }.pending { opacity: .55; pointer-events: none; }
.gallery-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 14px 0 8px; }.gallery-toolbar > select { width: auto; font-size: 11px; padding: 6px; }
.catalog-tabs { display: flex; gap: 16px; }.catalog-tabs button { border: 0; background: none; padding: 7px 0; border-bottom: 2px solid transparent; color: var(--muted-foreground); font: inherit; font-weight: 600; }.catalog-tabs button.active { color: var(--foreground); border-color: var(--image-accent); }.catalog-tabs span { margin-left: 3px; font-size: 10px; color: var(--muted-foreground); }
.imagery .matches-toggle { flex-direction: row; align-items: center; margin-bottom: 13px; font-size: 10px; }.matches-toggle input { accent-color: var(--image-accent); }
.imagery-gallery { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.image-card { min-width: 0; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: var(--background); transition: border-color .2s; }.image-card.focused { border-color: var(--image-gold); box-shadow: 0 0 0 1px var(--image-gold); }.image-card.compared { border-color: var(--image-accent); }
.image-card-main { width: 100%; display: block; padding: 0; border: 0; background: none; color: var(--foreground); text-align: left; font: inherit; }
.preview-loading { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 9px; color: #b6cdce; font-size: 10px; background: #15262c; }.preview-loading svg { animation: spin 3s linear infinite; }
.image-cover { position: relative; aspect-ratio: 1.38; background: #15262c; overflow: hidden; }.image-cover img { width: 100%; height: 100%; object-fit: contain; }.provider-chip, .resolution-chip { position: absolute; padding: 4px 6px; border-radius: 4px; color: #fff; font-size: 9px; background: #122229c9; backdrop-filter: blur(5px); }.provider-chip { left: 7px; top: 7px; }.resolution-chip { right: 7px; bottom: 7px; }
.image-card-info { padding: 9px; }.image-card-info > div:first-child { display: flex; justify-content: space-between; gap: 6px; font-size: 11px; }.image-card-info > div:first-child span { color: var(--muted-foreground); font-size: 9px; }.image-price { display: flex; align-items: baseline; flex-wrap: wrap; justify-content: space-between; gap: 4px; margin-top: 10px; }.image-price strong { font-size: 16px; letter-spacing: -.3px; }.image-price span { font-size: 9px; color: var(--muted-foreground); }.image-meter { height: 3px; background: var(--border); margin: 8px 0; border-radius: 2px; }.image-meter i { display: block; height: 100%; background: var(--image-accent); border-radius: 2px; }.image-card-info p { display: flex; align-items: center; gap: 4px; margin: 0; font-size: 9px; color: var(--muted-foreground); flex-wrap: wrap; }.filter-warning { color: #ab691f; font-size: 10px; }
.compare-button { display: flex; justify-content: center; align-items: center; gap: 4px; width: 100%; border: 0; border-top: 1px solid var(--border); padding: 8px 4px; color: var(--image-accent); background: none; font: inherit; font-size: 10px; }.compare-button[aria-pressed=true] { background: color-mix(in srgb, var(--image-accent) 10%, transparent); }
.image-inspector { align-self: start; position: sticky; top: 0; border-left: 1px solid var(--border); padding-left: 20px; min-width: 0; }
.inspector-heading { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 0 0 10px; }.inspector-heading h3 { font-size: 17px; margin-top: 5px; }.inspector-heading h3 span { font-size: 10px; color: var(--muted-foreground); font-weight: 400; }.inspector-heading .eyebrow { font-size: 8px; }.icon-button { border: 0; background: none; color: var(--foreground); padding: 5px; }
.inspector-preview { display: block; position: relative; width: 100%; background: #15262c; padding: 0; border: 0; border-radius: 8px; overflow: hidden; aspect-ratio: 1.4; }.inspector-preview img { width: 100%; height: 100%; object-fit: contain; }.inspector-preview > span { position: absolute; bottom: 0; left: 0; right: 0; padding: 7px; color: #fff; background: #122229c9; font-size: 9px; }
.inspector-price { display: flex; justify-content: space-between; gap: 8px; padding: 15px 0; border-bottom: 1px solid var(--border); }.inspector-price div { display: flex; flex-direction: column; gap: 5px; }.inspector-price span { font-size: 10px; color: var(--muted-foreground); }.inspector-price strong { font-size: 26px; letter-spacing: -.6px; }.inspector-price b { font-size: 20px; }
.footprint-heading { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; padding: 14px 0 7px; }.footprint-heading span { color: var(--image-accent); }.footprint-map { width: 100%; border-radius: 7px; background: color-mix(in srgb, var(--image-accent) 6%, var(--background)); color: var(--muted-foreground); }.footprint-map text { fill: var(--muted-foreground); font-size: 10px; }.aoi-rect { fill: none; stroke: #79949f; stroke-width: 1.3; }.footprint-fill { fill: #4bada757; stroke: #267b78; stroke-width: 1.3; }
.inspector-facts { display: grid; gap: 9px; padding: 12px 0; border-bottom: 1px solid var(--border); }.inspector-facts > div { display: flex; align-items: center; gap: 5px; font-size: 11px; }.inspector-facts span { color: var(--muted-foreground); }.inspector-facts b { margin-left: auto; font-weight: 550; }.date-context { color: var(--muted-foreground); font-size: 10px; margin: 10px 0 12px; }.image-inspector > button { margin-top: 7px; }.source-details { margin-top: 13px; font-size: 10px; color: var(--muted-foreground); }.source-details summary { cursor: pointer; }.archive-id { overflow-wrap: anywhere; }.source-details a { color: var(--image-accent); }
.catalog-footnote { font-size: 10px; color: var(--muted-foreground); padding: 14px 0; }.imagery-error, .imagery-warning, .quote-message { background: #d9a14e18; padding: 10px 12px; border-left: 3px solid #c99247; font-size: 11px; line-height: 1.5; }.imagery-error { color: #a8562b; }.catalog-empty, .catalog-loading { display: flex; flex-direction: column; align-items: center; text-align: center; padding: 50px 24px; gap: 12px; color: var(--muted-foreground); }.catalog-empty h3, .catalog-loading h3 { font-size: 20px; color: var(--foreground); }.catalog-empty p, .catalog-loading p { max-width: 440px; margin: 0; }.image-unavailable { height: 100%; display: flex; align-items: center; justify-content: center; flex-direction: column; padding: 12px; gap: 9px; color: #b6c7ce; font-size: 10px; }.no-filter-results { grid-column: 1 / -1; padding: 30px 10px; text-align: center; color: var(--muted-foreground); }
.compare-grid { display: grid; grid-template-columns: repeat(var(--columns), minmax(0, 1fr)); gap: 12px; overflow-x: auto; }.compare-grid article { min-width: 120px; }.comparison-image { position: relative; aspect-ratio: 1.25; background: #15262c; margin-bottom: 10px; border-radius: 8px; overflow: hidden; }.comparison-image img { width: 100%; height: 100%; object-fit: contain; }.comparison-image button { position: absolute; top: 5px; right: 5px; border: 0; padding: 4px; border-radius: 50%; background: #122229c9; color: white; }.compare-grid article > b, .compare-grid article > span, .compare-grid article > strong { display: block; margin: 5px 0; }.compare-grid article > span { font-size: 10px; color: var(--muted-foreground); }.compare-grid article > strong { font-size: 20px; }.compare-grid dl { display: grid; gap: 4px; font-size: 10px; }.compare-grid dt { margin-top: 8px; color: var(--muted-foreground); }.compare-grid dd { margin: 0; font-weight: 600; }
.portfolio { display: flex; align-items: center; gap: 20px; padding: 20px 0; margin-top: 15px; border-top: 1px solid var(--border); }.portfolio svg { width: 120px; flex: none; }.donut-track { fill: none; stroke: var(--border); stroke-width: 9; }.donut-value { fill: none; stroke: var(--image-accent); stroke-width: 9; transform: rotate(-90deg); transform-origin: 60px 60px; }.portfolio text { font-size: 21px; font-weight: 650; fill: var(--foreground); }.portfolio span { font-size: 11px; color: var(--muted-foreground); }.portfolio strong { display: block; font-size: 28px; margin: 4px 0; }.portfolio p { font-size: 10px; margin: 5px 0; }
.map-mode .imagery-layout { display: block; }.map-mode .image-inspector { padding: 0; border: 0; position: static; }.map-mode h2 { font-size: 20px; }.map-mode .imagery-heading { flex-wrap: wrap; }
.image-lightbox { position: fixed; z-index: 1000; inset: 0; display: flex; align-items: center; justify-content: center; flex-direction: column; background: #08181df5; padding: 50px 30px 25px; color: #f0f6f7; }.image-lightbox > img { max-width: 90vw; max-height: 75vh; width: auto; height: auto; object-fit: contain; }.image-lightbox > div { text-align: center; margin-top: 20px; }.image-lightbox p { color: #abc1c9; font-size: 12px; }.lightbox-close { position: absolute; right: 25px; top: 20px; border: 0; background: none; color: white; cursor: pointer; }
@media (max-width: 1250px) { .imagery-gallery { grid-template-columns: repeat(2, minmax(0, 1fr)); }.imagery-layout { grid-template-columns: minmax(0, 1fr) 275px; gap: 18px; }.catalog-charts { grid-template-columns: 1fr; }.timeline-bars { height: 105px; }.timeline-chart { display: none; } }
@media (max-width: 760px) { .imagery-layout { grid-template-columns: 1fr; }.image-inspector { border: 0; border-top: 1px solid var(--border); padding: 20px 0; position: static; }.catalog-metrics { grid-template-columns: 1fr 1fr; gap: 15px; }.search-toolbar { flex-wrap: wrap; }.search-settings { flex-basis: 100%; }.search-fields { grid-template-columns: 1fr 1fr; }.imagery h2 { font-size: 22px; }.imagery-heading { align-items: flex-start; }.catalog-metrics b { font-size: 21px; }.catalog-charts { display: block; }.timeline-chart { display: block; margin-top: 20px; }.gallery-toolbar { flex-wrap: wrap; }.catalog-filters { gap: 5px; }.imagery-gallery { gap: 8px; }.portfolio { gap: 10px; }.portfolio svg { width: 90px; }.compare-grid { grid-template-columns: repeat(var(--columns), minmax(145px, 1fr)); }.source-strip { gap: 8px; } }
</style>
