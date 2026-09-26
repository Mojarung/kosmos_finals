<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { Download, RefreshCw, ShoppingCart, Check, CircleAlert, ArrowRight } from '@lucide/vue'
import { state } from '../store'

const catalog = ref(null)
const result = ref(null)
const previous = ref(null)
const history = ref([])
const error = ref('')
const busy = ref(false)
const planning = ref(false)
const configured = ref(false)
const source = ref('demo')
const market = ref('commercial')
const sourceDate = () => (state.analysis?.scenes.find(scene => scene.chip_id === state.sceneId)?.observed_at
  || (state.analysis?.chip.chip_id === state.sceneId ? state.analysis.chip.observed_at : ''))?.slice(0, 10) || ''
const fromDate = ref(sourceDate())
const toDate = ref(sourceDate())
const reviewNotes = reactive({})
const controls = reactive({ budget: 500, deadline_hours: 12, resolution_m: 1, mandatory: [], excluded: [], objective: 'coverage' })
const selected = computed(() => result.value?.offers.filter(o => o.selected) || [])
const offerStatuses = { scenario: 'Учебная цена', catalog_estimate: 'Расчёт по каталогу', provider_estimate: 'Оценка API поставщика' }
const dollars = value => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value)
const dateLabel = value => value ? new Date(value).toLocaleString('ru-RU') : 'неизвестно'
const assetName = id => catalog.value?.assets.find(a => a.id === id)?.name || id
let generation = 0
let timer
let controller
let disposed = false

async function api(path, body, signal) {
  const response = await fetch('/api/procurement' + path, { method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Проверьте параметры запроса')
  return data
}
const request = () => ({ catalog_id: catalog.value.id, ...JSON.parse(JSON.stringify(controls)) })
function invalidate() {
  generation++
  controller?.abort()
  clearTimeout(timer)
  planning.value = true
  state.procurementOverlay = []
}
async function recalculate() {
  if (!catalog.value || disposed) return
  const turn = ++generation
  controller?.abort()
  controller = new AbortController()
  planning.value = true
  error.value = ''
  try {
    const next = await api('/plan', request(), controller.signal)
    if (turn !== generation || disposed) return
    previous.value = result.value
    result.value = next
    updateOverlay()
  } catch (e) {
    if (turn === generation && e.name !== 'AbortError') { error.value = e.message; result.value = null }
  } finally { if (turn === generation) planning.value = false }
}
function updateOverlay(hoverId = null) {
  state.procurementOverlay = (result.value?.offers || []).filter(o => o.selected || o.id === hoverId)
    .map(o => ({ type: 'Feature', geometry: o.geometry,
      properties: { id: o.id, kind: 'object', active: o.selected ? 1 : 0, hover: o.id === hoverId ? 1 : 0 } }))
}
async function run(fn) {
  busy.value = true
  error.value = ''
  try { await fn() } catch (e) { if (!disposed) error.value = e.message } finally { busy.value = false }
}
async function search() {
  invalidate()
  result.value = null
  previous.value = null
  catalog.value = null
  await run(async () => {
    const next = await api('/catalogs', { scene_id: state.sceneId, source: source.value, market: market.value,
      ...(fromDate.value ? { from_date: fromDate.value } : {}), ...(toDate.value ? { to_date: toDate.value } : {}) })
    if (disposed) return
    catalog.value = next
    controls.mandatory = []
    controls.excluded = []
    await recalculate()
  })
  if (!catalog.value) planning.value = false
}
async function loadHistory() {
  const data = await api('/decisions?scene_id=' + encodeURIComponent(state.sceneId))
  if (!disposed) history.value = data.decisions
}
async function save() {
  await run(async () => {
    const snapshot = await api('/decisions', request())
    await loadHistory()
    const a = document.createElement('a')
    a.href = '/api/procurement/decisions/' + snapshot.id + '/export'
    a.download = 'procurement-' + snapshot.id + '.zip'
    a.click()
  })
}
async function restore(id) {
  invalidate()
  await run(async () => {
    const snapshot = await api('/decisions/' + id)
    if (disposed) return
    catalog.value = snapshot.catalog
    source.value = snapshot.catalog.source
    market.value = snapshot.catalog.market || 'all'
    fromDate.value = snapshot.catalog.from_date
    toDate.value = snapshot.catalog.to_date
    const { catalog_id, ...settings } = snapshot.plan.request
    Object.assign(controls, settings)
    await recalculate()
  })
}
async function validate(o) {
  await run(async () => {
    const next = await api('/catalogs/' + catalog.value.id + '/quotes/' + encodeURIComponent(o.id), {})
    if (disposed) return
    catalog.value = next
    await recalculate()
  })
}
async function variant(o) {
  await run(async () => {
    const next = await api('/catalogs/' + catalog.value.id + '/variants', { offer_id: o.id, asset_ids: controls.mandatory })
    if (disposed) return
    catalog.value = next
    await recalculate()
  })
}
async function review(o) {
  await run(async () => {
    const next = await api('/catalogs/' + catalog.value.id + '/review', { offer_id: o.id, note: reviewNotes[o.id] })
    if (disposed) return
    catalog.value = next
    await recalculate()
  })
}
function excludeProvider(provider) {
  const ids = result.value.offers.filter(o => o.provider === provider).map(o => o.id)
  controls.excluded = [...new Set([...controls.excluded, ...ids])]
}
function preserveCoverage() {
  controls.mandatory = [...new Set([...controls.mandatory, ...result.value.covered_ids])]
  controls.objective = 'cheapest'
}
watch(controls, () => {
  if (!catalog.value || busy.value) return
  invalidate()
  timer = setTimeout(recalculate, 200)
}, { deep: true })
onMounted(async () => {
  state.procurementOverlay = []
  await run(async () => {
    const [status] = await Promise.all([api('/status'), loadHistory()])
    configured.value = status.skyfi_configured
  })
})
onUnmounted(() => {
  disposed = true
  generation++
  clearTimeout(timer)
  controller?.abort()
  state.procurementOverlay = null
})
</script>

<template>
  <div class="procurement" data-testid="procurement">
    <section class="block procurement-intro">
      <span class="caption">Данные для решения</span>
      <h2>Что купить, чтобы<br>проверить нужные объекты?</h2>
      <p>Подбор для всей сцены · {{ state.sceneId }}. Ретроспективный разбор: покупаем архив сегодня.</p>
      <div class="procurement-fields">
        <label>Источник<select v-model="source" :disabled="busy" aria-label="Источник предложений">
          <option value="demo">Учебный каталог</option><option value="skyfi" :disabled="!configured">SkyFi {{ configured ? '· API' : '· нужен ключ' }}</option>
        </select></label>
        <label>Съёмка от<input v-model="fromDate" type="date" aria-label="Начало периода съёмки" :disabled="busy" /></label>
        <label>Съёмка до<input v-model="toDate" type="date" aria-label="Конец периода съёмки" :disabled="busy" /></label>
      </div>
      <label v-if="source === 'skyfi'">Тип предложений<select v-model="market" :disabled="busy" aria-label="Тип предложений"><option value="commercial">Коммерческие снимки</option><option value="open">Бесплатные данные</option><option value="all">Все предложения</option></select></label>
      <p v-if="source === 'skyfi'" class="procurement-note">Коммерческие снимки ищутся отдельно, чтобы бесплатные Sentinel не вытесняли их с первой страницы.</p>
      <button class="primary-button wide" :disabled="busy" @click="search"><RefreshCw :size="15" />{{ busy ? 'Загружаем…' : 'Найти предложения' }}</button>
      <p v-if="!configured" class="procurement-note">Для SkyFi задайте SKYFI_API_KEY на сервере. Учебный каталог работает без ключа; цены и сцены предложений вымышлены.</p>
    </section>
    <p v-if="error" role="alert" class="error-note">{{ error }}</p>
    <template v-if="catalog">
      <section class="block procurement-source">
        <b>{{ catalog.disclaimer }}</b>
        <span>Каталог сохранён {{ dateLabel(catalog.captured_at) }} · {{ catalog.offers.length }} предложений</span>
        <span v-if="catalog.truncated || catalog.skipped">Выборка ограничена первой страницей. Пропущено некорректных записей: {{ catalog.skipped }}.</span>
      </section>
      <section v-if="result && !planning && !result.eligible_count" class="block procurement-diagnostics" role="status">
        <h3>{{ result.offers.length ? 'Предложения найдены, но пока не готовы к подбору' : 'Снимков за этот период не найдено' }}</h3>
        <p v-if="!result.offers.length">Попробуйте другой период или тип предложений. Более поздние снимки не подтверждают состояние паводка {{ catalog.event_date }}.</p>
        <ul><li v-for="(count, reason) in result.blockers" :key="reason">{{ reason }} — {{ count }}</li></ul>
        <p v-if="result.offers.length">Откройте параметры предложения ниже: проверьте цену и укажите основание проверки пригодности. Неизвестное качество не считается подтверждённым.</p>
      </section>
      <section class="block">
        <div class="procurement-fields">
          <label>Бюджет, USD<input v-model.number="controls.budget" type="number" min="0" max="1000000000" aria-label="Бюджет закупки" :disabled="busy" /></label>
          <label>Получить за, ч<input v-model.number="controls.deadline_hours" type="number" min="1" max="8760" aria-label="Срок закупки" :disabled="busy" /></label>
          <label>Детализация<select v-model.number="controls.resolution_m" aria-label="Детализация закупки" :disabled="busy"><option :value="0.5">До 0,5 м</option><option :value="1">До 1 м</option><option :value="10">До 10 м</option></select></label>
        </div>
        <div class="procurement-section-title"><b>Обязательные объекты</b><span>{{ controls.mandatory.length }} выбрано</span></div>
        <div class="procurement-assets">
          <label v-for="asset in catalog.assets" :key="asset.id" :class="{ picked: controls.mandatory.includes(asset.id) }">
            <input v-model="controls.mandatory" type="checkbox" :value="asset.id" :aria-label="'Обязательно: ' + asset.name" :disabled="busy" />{{ asset.name }}
            <small v-if="result && !planning">{{ result.incremental_cost[asset.id] == null ? 'нужна проверка условий' : '+' + dollars(result.incremental_cost[asset.id]) }}</small>
          </label>
        </div>
        <p class="procurement-note">Доплата у объекта — минимальная стоимость его добавления с сохранением текущего охвата.</p>
        <label class="procurement-objective">Цель подбора<select v-model="controls.objective" :disabled="busy" aria-label="Цель закупки"><option value="coverage">Больше охвата EL в бюджете</option><option value="cheapest">Дешевле выполнить обязательное</option></select></label>
        <p v-if="controls.objective === 'cheapest' && !controls.mandatory.length" class="procurement-note">Отметьте обязательные объекты. Без требований самая дешёвая корзина пуста.</p>
      </section>
      <section v-if="result" class="block procurement-basket" :class="{ stale: planning }" data-testid="basket" :aria-busy="planning">
        <p class="procurement-note">{{ catalog.source === 'demo' ? 'Учебная корзина · вымышленные цены' : 'Оценка закупки · сохранённые предложения' }}</p>
        <div class="procurement-section-title"><span class="caption"><ShoppingCart :size="14" /> {{ planning ? 'Пересчитываем…' : 'Ваша корзина' }}</span><span>{{ selected.length }} заказов</span></div>
        <div class="procurement-price" data-testid="basket-price">{{ dollars(result.cost) }}</div>
        <p v-if="previous && previous.catalog_id === result.catalog_id && previous.cost !== result.cost" class="procurement-delta">{{ previous.cost < result.cost ? '+' : '−' }}{{ dollars(Math.abs(result.cost - previous.cost)) }} к предыдущему варианту</p>
        <div class="procurement-stats"><div><b>{{ result.covered_ids.length }} / {{ catalog.assets.length }}</b><span>объектов охвачено</span></div><div><b>{{ result.delivery_hours }} ч</b><span>оценка доставки</span></div><div><b>{{ result.coverage_share == null ? '—' : Math.round(result.coverage_share * 100) + '%' }}</b><span>исходного EL</span></div></div>
        <div class="procurement-comparison"><span>A · Бесплатно: {{ result.free_coverage_count }} объектов</span><span>B · Вся область одним заказом: {{ result.broad_cost == null ? 'нет подходящего' : dollars(result.broad_cost) }}</span><span>C · Ваша корзина: {{ dollars(result.cost) }}</span></div>
        <button class="procurement-link" :disabled="busy || planning || !result.covered_ids.length" @click="preserveCoverage">Сохранить охват и найти дешевле</button>
        <p class="procurement-check" :class="{ warning: !result.mandatory_satisfied }" role="status"><Check v-if="result.mandatory_satisfied" :size="16" /><CircleAlert v-else :size="16" />{{ result.mandatory_satisfied ? (selected.length ? 'Обязательные условия выполнены' : 'Корзина пуста: подходящих заказов не выбрано') : 'Все обязательные условия выполнить не удалось' }}</p>
        <div v-if="!result.mandatory_satisfied" class="procurement-shortfall">
          <p>Без покрытия: {{ result.missing_ids.map(assetName).join(', ') }}. Показана частичная корзина.</p>
          <p v-if="result.minimum_cost != null">Минимум для обязательных объектов: <b>{{ dollars(result.minimum_cost) }}</b>. Не хватает {{ dollars(result.budget_gap) }}.</p>
          <p v-else>В этом каталоге нет пригодной комбинации. Измените срок, требования или исключения.</p>
        </div>
        <div v-if="result.billable_km2 != null && result.billable_km2 > result.ordered_km2 + .01" class="procurement-area"><span>Контуры заказов <b>{{ result.ordered_km2 }} км²</b></span><ArrowRight :size="16" /><span>К оплате <b>{{ result.billable_km2 }} км²</b></span></div>
        <p v-if="result.saving_vs_independent > 0" class="procurement-saving">На {{ dollars(result.saving_vs_independent) }} дешевле отдельного подбора для охваченных объектов.</p>
        <details><summary>Как сравнили стоимость</summary><p>{{ result.comparison_note }}</p><p>{{ result.optimality }}</p></details>
        <p class="procurement-note">Охват EL — приоритет проверки, а не предотвращённый ущерб. Покупка снимков не означает устранение сомнений. Налоги и сборы требуют подтверждения.</p>
        <button class="primary-button wide" :disabled="busy || planning" @click="save"><Download :size="15" />Сохранить и скачать заявку</button>
      </section>
      <p v-else-if="planning" class="block" role="status">Собираем корзину…</p>
      <section v-if="result" class="block procurement-offers">
        <div class="procurement-section-title"><h3>Предложения</h3><button v-if="controls.excluded.length" class="procurement-link" @click="controls.excluded = []">Вернуть исключённые</button></div>
        <article v-for="offer in result.offers" :key="offer.id" class="procurement-offer" :class="{ selected: offer.selected, excluded: controls.excluded.includes(offer.id) }" :data-testid="'offer-' + offer.id" @mouseenter="!planning && updateOverlay(offer.id)" @mouseleave="!planning && updateOverlay()" @focusin="!planning && updateOverlay(offer.id)">
          <div class="procurement-section-title"><b>{{ offer.title }}</b><span>{{ dollars(offer.price) }}</span></div>
          <p>{{ offer.provider }} · {{ Number(offer.resolution_m.toFixed(2)) }} м · {{ offer.delivery_hours == null ? 'срок неизвестен' : offer.delivery_hours + ' ч' }}</p>
          <span class="procurement-offer-status">{{ offer.selected ? '✓ ' : '' }}{{ offer.reason }}</span>
          <details><summary>Параметры и источник цены</summary>
            <img v-if="offer.preview_url" :src="offer.preview_url" :alt="'Превью ' + offer.title" loading="lazy" referrerpolicy="no-referrer" />
            <p>Снято {{ offer.capture_date }} · {{ offer.area_km2 }} км². {{ offer.full_scene_only ? 'Оплата целого кадра' : 'Заказ по контуру' }}.</p>
            <p v-if="offer.minimum_billable_km2">Минимум оплаты: {{ offer.minimum_billable_km2 }} км² на заказ.</p>
            <p>{{ offerStatuses[offer.price_status] }} · {{ dateLabel(offer.quote_at) }}</p>
            <p class="procurement-source-url">{{ offer.price_source }}</p>
            <p>Объекты: {{ offer.asset_ids.map(assetName).join(', ') || 'нет' }}</p>
            <button class="tonal-button" :disabled="busy || !controls.mandatory.length || offer.full_scene_only" @click="variant(offer)">Создать заказ на обязательные объекты</button>
            <p v-if="offer.quality_note">Проверка оператором: {{ offer.quality_note }}</p>
            <template v-if="catalog.source === 'skyfi'">
              <button class="tonal-button" :disabled="busy || !!offer.order_issue" @click="validate(offer)">Проверить цену без покупки</button>
              <label v-if="offer.quality === 'unknown'">Основание проверки пригодности<textarea v-model="reviewNotes[offer.id]" :aria-label="'Основание пригодности ' + offer.id" placeholder="Что проверено у объектов, по каким данным" maxlength="1000" /></label>
              <button v-if="offer.quality === 'unknown'" class="tonal-button" :disabled="busy || (reviewNotes[offer.id]?.trim().length || 0) < 12" @click="review(offer)">Пригодность проверена мной</button>
            </template>
            <button class="procurement-link" :disabled="busy" @click="excludeProvider(offer.provider)">Исключить поставщика</button>
          </details>
          <label class="procurement-exclude"><input v-model="controls.excluded" type="checkbox" :value="offer.id" :disabled="busy" :aria-label="'Исключить ' + offer.title" /> Исключить предложение</label>
        </article>
        <p v-if="!result.offers.length" class="procurement-note">Предложений за выбранный период нет. Измените период или источник.</p>
      </section>
    </template>
    <section v-if="history.length" class="block">
      <h3>Сохранённые закупки</h3>
      <button v-for="item in history" :key="item.id" class="row procurement-history" :disabled="busy || planning" @click="restore(item.id)"><span>{{ dateLabel(item.saved_at) }}<small>{{ item.covered }} объектов</small></span><b>{{ dollars(item.cost) }}</b></button>
    </section>
  </div>
</template>

<style scoped>
.procurement { font-size: 13px; }
.procurement h2 { font-size: 23px; letter-spacing: -.7px; line-height: 1.15; margin: 10px 0; }
.procurement h3 { font-size: 15px; margin: 0; }
.procurement p { line-height: 1.5; margin: 8px 0; }
.procurement-intro > p, .procurement-note { color: var(--muted-foreground); font-size: 11px; }
.procurement-fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 14px 0; }
.procurement label { display: flex; flex-direction: column; gap: 6px; font-size: 11px; }
.procurement input:not([type=checkbox]), .procurement select, .procurement textarea { min-width: 0; width: 100%; border: 1px solid var(--border); border-radius: 9px; padding: 9px 6px; background: var(--background); color: inherit; font: inherit; }
.procurement-diagnostics { background: rgba(220,162,45,.08); }
.procurement-diagnostics li { margin: 6px 0; }
.procurement-source { display: grid; gap: 6px; font-size: 11px; background: rgba(220, 162, 45, .09); }
.procurement-source span { color: var(--muted-foreground); }
.procurement-section-title { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.procurement-section-title > span { white-space: nowrap; font-size: 11px; }
.procurement-section-title .caption { display: flex; gap: 6px; align-items: center; }
.procurement-assets { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 14px; }
.procurement-assets label { flex-direction: row; align-items: center; border: 1px solid var(--border); border-radius: 16px; padding: 6px 9px; cursor: pointer; }
.procurement-assets .picked { border-color: #32836b; background: rgba(50,131,107,.1); }
.procurement-assets small { opacity: .7; }
.procurement-comparison { display: grid; gap: 6px; padding: 10px 0; font-size: 11px; color: var(--muted-foreground); }
.procurement input[type=checkbox] { accent-color: #32836b; }
.procurement-price { font-size: 36px; letter-spacing: -1.5px; font-variant-numeric: tabular-nums; margin-top: 10px; }
.procurement-delta { color: var(--muted-foreground); font-size: 11px; }
.procurement-stats { display: flex; justify-content: space-between; padding: 16px 0; }
.procurement-stats div { display: flex; flex-direction: column; gap: 5px; }
.procurement-stats b { font-size: 18px; font-weight: 550; }
.procurement-stats span { font-size: 10px; color: var(--muted-foreground); }
.procurement-check { display: flex; gap: 7px; align-items: center; color: #32836b; }
.procurement-check.warning { color: #ad6826; }
.procurement-shortfall { padding: 4px 12px; border-radius: 12px; background: rgba(220,162,45,.1); }
.procurement-area { display: flex; align-items: center; gap: 10px; padding: 14px 0; }
.procurement-area span { display: flex; flex-direction: column; gap: 4px; font-size: 11px; }
.procurement-saving { color: #32836b; }
.procurement details { margin: 10px 0; font-size: 11px; }
.procurement summary { cursor: pointer; }
.procurement details img { width: 100%; max-height: 180px; object-fit: contain; border-radius: 8px; margin-top: 10px; }
.procurement details button { margin: 8px 0; }
.procurement-offer { border: 1px solid var(--border); padding: 13px; border-radius: 15px; margin-top: 10px; }
.procurement-offer.selected { border-color: #32836b; background: rgba(50,131,107,.04); }
.procurement-offer.excluded { opacity: .6; }
.procurement-offer p { font-size: 11px; color: var(--muted-foreground); }
.procurement-offer-status { display: inline-block; font-size: 11px; padding: 5px 7px; border-radius: 7px; background: rgba(128,128,128,.08); }
.procurement .procurement-exclude { flex-direction: row; align-items: center; color: var(--muted-foreground); }
.procurement-source-url { overflow-wrap: anywhere; }
.procurement-link { background: none; border: 0; color: #32836b; cursor: pointer; font: inherit; font-size: 11px; padding: 0; }
.procurement-history { justify-content: space-between; width: 100%; }
.procurement-history small { display: block; color: var(--muted-foreground); margin-top: 3px; }
.stale { opacity: .5; }
</style>
