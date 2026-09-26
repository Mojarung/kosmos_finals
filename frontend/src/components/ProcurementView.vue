<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { Download, RefreshCw, ShoppingCart, Check, CircleAlert, ArrowRight } from '@lucide/vue'
import { state } from '../store'

const catalog = ref(null)
const result = ref(null)
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
const controls = reactive({ budget: 500, deadline_hours: 12, resolution_m: 1, mandatory: [], excluded: [], objective: 'cheapest' })
const showAllAssets = ref(false)
const showAlternatives = ref(false)
const showAllOffers = ref(false)
const basketRef = ref(null)
const selected = computed(() => result.value?.offers.filter(o => o.selected) || [])
const alternatives = computed(() => result.value?.offers.filter(o => !o.selected) || [])
const priorityAssets = computed(() => [...(catalog.value?.assets || [])].sort((a, b) => b.expected_loss_rub - a.expected_loss_rub))
const visibleAssets = computed(() => showAllAssets.value ? priorityAssets.value : priorityAssets.value.filter((a, index) => index < 4 || controls.mandatory.includes(a.id)))
const visibleAlternatives = computed(() => showAllOffers.value ? alternatives.value : alternatives.value.slice(0, 5))
const hasTask = computed(() => controls.objective === 'coverage' || controls.mandatory.length > 0)
const requiredCovered = computed(() => controls.mandatory.filter(id => result.value?.covered_ids.includes(id)).length)
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
  catalog.value = null
  await run(async () => {
    const next = await api('/catalogs', { scene_id: state.sceneId, source: source.value, market: market.value,
      ...(fromDate.value ? { from_date: fromDate.value } : {}), ...(toDate.value ? { to_date: toDate.value } : {}) })
    if (disposed) return
    catalog.value = next
    controls.mandatory = controls.mandatory.filter(id => next.assets.some(asset => asset.id === id))
    controls.excluded = []
    showAlternatives.value = false
    showAllOffers.value = false
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
function choosePriority() {
  controls.mandatory = priorityAssets.value.slice(0, 3).map(asset => asset.id)
  controls.objective = 'cheapest'
}
function scrollToResult() {
  basketRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
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
  if (!disposed) await search()
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
      <span class="caption">Закупка данных · {{ state.sceneId }}</span>
      <h2>Какие снимки нужны для проверки объектов?</h2>
      <p>Укажите, что важно проверить. Сервис соберёт самый дешёвый набор подходящих снимков и покажет его на карте.</p>
      <details class="procurement-source-settings">
        <summary>{{ catalog?.source === 'skyfi' ? 'SkyFi · API' : 'Демо · учебные цены' }} <span>Источник и даты</span></summary>
        <div class="procurement-source-form">
          <label>Источник<select v-model="source" :disabled="busy" aria-label="Источник предложений">
            <option value="demo">Учебный каталог</option><option value="skyfi" :disabled="!configured">SkyFi {{ configured ? '· API' : '· нужен ключ' }}</option>
          </select></label>
          <div class="procurement-dates">
            <label>Съёмка от<input v-model="fromDate" type="date" aria-label="Начало периода съёмки" :disabled="busy" /></label>
            <label>Съёмка до<input v-model="toDate" type="date" aria-label="Конец периода съёмки" :disabled="busy" /></label>
          </div>
          <label v-if="source === 'skyfi'">Тип предложений<select v-model="market" :disabled="busy" aria-label="Тип предложений"><option value="commercial">Коммерческие снимки</option><option value="open">Бесплатные данные</option><option value="all">Все предложения</option></select></label>
          <button class="tonal-button" :disabled="busy" @click="search"><RefreshCw :size="15" /> {{ busy ? 'Загружаем…' : 'Обновить предложения' }}</button>
          <p v-if="!configured" class="procurement-muted">Для SkyFi нужен ключ на сервере.</p>
        </div>
      </details>
    </section>
    <p v-if="error" role="alert" class="error-note">{{ error }}</p>
    <p v-if="!catalog && !error" class="block procurement-muted" role="status">Загружаем предложения для сцены…</p>
    <template v-if="catalog">
      <section class="block procurement-step">
        <div class="procurement-step-heading"><span class="procurement-step-number">1</span><div><h3>Что нужно проверить?</h3><p>Объекты с наибольшим ожидаемым ущербом — первыми.</p></div></div>
        <div class="procurement-mode" role="group" aria-label="Способ подбора">
          <button :class="{ active: controls.objective === 'cheapest' }" :disabled="busy" @click="controls.objective = 'cheapest'">Нужные объекты</button>
          <button :class="{ active: controls.objective === 'coverage' }" :disabled="busy" @click="controls.objective = 'coverage'">Максимум охвата</button>
        </div>
        <p class="procurement-muted">{{ controls.objective === 'cheapest' ? 'Отметьте обязательные объекты: найдём самый дешёвый набор для них.' : 'Найдём больше всего ценности для проверки в заданном бюджете. Отмеченные объекты останутся обязательными.' }}</p>
        <div class="procurement-assets">
          <label v-for="asset in visibleAssets" :key="asset.id" :class="{ picked: controls.mandatory.includes(asset.id) }">
            <input v-model="controls.mandatory" type="checkbox" :value="asset.id" :aria-label="'Обязательно: ' + asset.name" :disabled="busy" />
            <span>{{ asset.name }}</span>
          </label>
        </div>
        <div class="procurement-actions">
          <button class="procurement-link" :disabled="busy" @click="showAllAssets = !showAllAssets">{{ showAllAssets ? 'Свернуть список' : 'Все объекты (' + catalog.assets.length + ')' }}</button>
          <button class="procurement-link" :disabled="busy" @click="choosePriority">Выбрать 3 приоритетных</button>
        </div>
        <button v-if="hasTask && result && !planning" class="procurement-inline-result" @click="scrollToResult">
          <span>{{ result.mandatory_satisfied ? (controls.objective === 'cheapest' ? requiredCovered + ' из ' + controls.mandatory.length + ' выбрано для проверки' : result.covered_ids.length + ' объектов охвачено') : 'Условия пока не выполнены' }}</span>
          <b>{{ result.mandatory_satisfied ? dollars(result.cost) : 'Открыть план' }}</b>
          <ArrowRight :size="15" />
        </button>
      </section>
      <section class="block procurement-step">
        <div class="procurement-step-heading"><span class="procurement-step-number">2</span><div><h3>Условия закупки</h3><p>Лимит, срок получения и нужная детализация.</p></div></div>
        <div class="procurement-fields">
          <label>Лимит, $<input v-model.number="controls.budget" type="number" min="0" max="1000000000" aria-label="Бюджет закупки" :disabled="busy" /></label>
          <label>Получить за, ч<input v-model.number="controls.deadline_hours" type="number" min="1" max="8760" aria-label="Срок закупки" :disabled="busy" /></label>
          <label>Детализация<select v-model.number="controls.resolution_m" aria-label="Детализация закупки" :disabled="busy"><option :value="0.5">До 0,5 м</option><option :value="1">До 1 м</option><option :value="10">До 10 м</option></select></label>
        </div>
        <p class="procurement-muted">Лимит — максимальная сумма за снимки. Итоговая корзина может стоить меньше; покупка не совершается.</p>
      </section>
      <section v-if="result && !planning && !result.eligible_count" class="block procurement-diagnostics" role="status">
        <h3>{{ result.offers.length ? 'Пока нет пригодных снимков' : 'Снимков за этот период не найдено' }}</h3>
        <p>Попробуйте увеличить срок, ослабить детализацию или изменить период съёмки.</p>
        <details v-if="result.offers.length"><summary>Причины</summary><ul><li v-for="(count, reason) in result.blockers" :key="reason">{{ reason }} — {{ count }}</li></ul></details>
      </section>
      <section v-if="!hasTask" class="block procurement-empty" role="status">
        <ShoppingCart :size="20" />
        <div><b>Начните с объектов</b><p>Выберите один или нажмите «Выбрать 3 приоритетных» — тогда здесь появятся цена и состав заказа.</p></div>
      </section>
      <section v-else-if="result && !planning" ref="basketRef" class="block procurement-basket" data-testid="basket" aria-busy="false">
        <div class="procurement-step-heading"><span class="procurement-step-number">3</span><div><h3>Рекомендуемый набор</h3><p>{{ catalog.source === 'demo' ? 'Учебный расчёт · вымышленные цены' : 'Оценка по сохранённому каталогу' }}</p></div></div>
        <p class="procurement-check" :class="{ warning: !result.mandatory_satisfied || !selected.length }" role="status">
          <Check v-if="result.mandatory_satisfied && selected.length" :size="16" /><CircleAlert v-else :size="16" />
          {{ result.mandatory_satisfied ? (selected.length ? 'Подходящий набор найден' : 'Подходящих заказов не выбрано') : 'Все обязательные условия выполнить не удалось' }}
        </p>
        <div class="procurement-price-row"><div><span class="procurement-muted">Стоимость набора</span><strong class="procurement-price" data-testid="basket-price">{{ dollars(result.cost) }}</strong></div><span class="procurement-limit">Лимит {{ dollars(controls.budget) }}</span></div>
        <div class="procurement-stats">
          <div><b>{{ controls.objective === 'cheapest' ? requiredCovered + ' / ' + controls.mandatory.length : result.covered_ids.length + ' / ' + catalog.assets.length }}</b><span>{{ controls.objective === 'cheapest' ? 'нужных объектов' : 'объектов сцены' }}</span></div>
          <div><b>{{ selected.length }}</b><span>снимков в наборе</span></div>
          <div><b>{{ result.delivery_hours }} ч</b><span>получение данных</span></div>
        </div>
        <p v-if="result.covered_ids.length > controls.mandatory.length && controls.objective === 'cheapest'" class="procurement-extra">Вместе с нужными объектами снимки захватят ещё {{ result.covered_ids.length - requiredCovered }}.</p>
        <div v-if="!result.mandatory_satisfied" class="procurement-shortfall">
          <p>Без покрытия: {{ result.missing_ids.map(assetName).join(', ') }}. Показана только доступная часть.</p>
          <p v-if="result.minimum_cost != null">Чтобы закрыть все выбранные объекты, нужно минимум <b>{{ dollars(result.minimum_cost) }}</b>. Сверх лимита: {{ dollars(result.budget_gap) }}.</p>
          <p v-else>При заданном сроке и детализации подходящей комбинации нет.</p>
        </div>
        <div v-if="selected.length" class="procurement-selected-list">
          <div v-for="offer in selected" :key="offer.id" class="procurement-selected-item"><span>{{ offer.title }}</span><b>{{ dollars(offer.price) }}</b></div>
        </div>
        <p v-if="result.saving_vs_independent > 0" class="procurement-saving">Набор на {{ dollars(result.saving_vs_independent) }} дешевле подбора заказов по одному для каждого охваченного объекта.</p>
        <details class="procurement-method"><summary>Сравнить с другими планами</summary>
          <div class="procurement-comparison"><span>A · Без оплаты</span><b>{{ result.free_coverage_count }} объектов при этих условиях</b><span>B · Одним кадром</span><b>{{ result.broad_cost == null ? 'Нет подходящего' : dollars(result.broad_cost) }}</b><span>C · Рекомендуемый набор</span><b>{{ dollars(result.cost) }}</b></div>
          <button class="procurement-link" :disabled="busy || planning || !result.covered_ids.length" @click="preserveCoverage">Сохранить текущий охват и найти дешевле</button>
          <p>{{ result.comparison_note }}</p><p>{{ result.optimality }}</p>
          <p>Охват по ожидаемому ущербу задаёт приоритет проверки, а не размер предотвращённого ущерба. Налоги и сборы требуют подтверждения.</p>
        </details>
        <button class="primary-button wide" :disabled="busy || planning || !selected.length || !result.mandatory_satisfied" @click="save"><Download :size="15" />Скачать план закупки</button>
      </section>
      <p v-else-if="planning && hasTask" class="block" role="status">Подбираем снимки…</p>
      <section v-if="result && hasTask && !planning" class="block procurement-offers">
        <button class="procurement-alternatives-toggle" :aria-expanded="showAlternatives" @click="showAlternatives = !showAlternatives">
          <span>{{ showAlternatives ? 'Скрыть каталог' : 'Другие предложения (' + alternatives.length + ')' }}</span><ArrowRight :size="15" :class="{ open: showAlternatives }" />
        </button>
        <template v-if="showAlternatives">
          <p class="procurement-muted">Здесь можно сравнить предложения, исключить поставщика и проверить источник цены.</p>
          <button v-if="controls.excluded.length" class="procurement-link" @click="controls.excluded = []">Вернуть исключённые</button>
          <article v-for="offer in visibleAlternatives" :key="offer.id" class="procurement-offer" :class="{ excluded: controls.excluded.includes(offer.id) }" :data-testid="'offer-' + offer.id" @mouseenter="!planning && updateOverlay(offer.id)" @mouseleave="!planning && updateOverlay()" @focusin="!planning && updateOverlay(offer.id)">
            <div class="procurement-offer-title"><b>{{ offer.title }}</b><strong>{{ dollars(offer.price) }}</strong></div>
            <p>{{ offer.provider }} · {{ Number(offer.resolution_m.toFixed(2)) }} м · {{ offer.delivery_hours == null ? 'срок неизвестен' : offer.delivery_hours + ' ч' }}</p>
            <span class="procurement-offer-status">{{ offer.reason }}</span>
            <details><summary>Параметры и действия</summary>
              <img v-if="offer.preview_url" :src="offer.preview_url" :alt="'Превью ' + offer.title" loading="lazy" referrerpolicy="no-referrer" />
              <p>Снято {{ offer.capture_date }} · {{ offer.area_km2 }} км². {{ offer.full_scene_only ? 'Оплата целого кадра' : 'Заказ по контуру' }}.</p>
              <p v-if="offer.minimum_billable_km2">Минимум оплаты: {{ offer.minimum_billable_km2 }} км² на заказ.</p>
              <p>{{ offerStatuses[offer.price_status] }} · {{ dateLabel(offer.quote_at) }}</p>
              <p class="procurement-source-url">{{ offer.price_source }}</p>
              <p>Объекты: {{ offer.asset_ids.map(assetName).join(', ') || 'нет' }}</p>
              <button class="tonal-button" :disabled="busy || !controls.mandatory.length || offer.full_scene_only" @click="variant(offer)">Создать заказ на выбранные объекты</button>
              <p v-if="offer.quality_note">Проверка оператором: {{ offer.quality_note }}</p>
              <template v-if="catalog.source === 'skyfi'">
                <button class="tonal-button" :disabled="busy || !!offer.order_issue" @click="validate(offer)">Проверить цену без покупки</button>
                <label v-if="offer.quality === 'unknown'">Основание проверки пригодности<textarea v-model="reviewNotes[offer.id]" :aria-label="'Основание пригодности ' + offer.id" placeholder="Что проверено у объектов, по каким данным" maxlength="1000" /></label>
                <button v-if="offer.quality === 'unknown'" class="tonal-button" :disabled="busy || (reviewNotes[offer.id]?.trim().length || 0) < 12" @click="review(offer)">Пригодность проверена мной</button>
              </template>
              <button class="procurement-link" :disabled="busy" @click="excludeProvider(offer.provider)">Исключить поставщика</button>
              <label class="procurement-exclude"><input v-model="controls.excluded" type="checkbox" :value="offer.id" :disabled="busy" :aria-label="'Исключить ' + offer.title" /> Исключить предложение</label>
            </details>
          </article>
          <button v-if="alternatives.length > 5" class="procurement-link procurement-more" @click="showAllOffers = !showAllOffers">{{ showAllOffers ? 'Показать меньше' : 'Показать все ' + alternatives.length }}</button>
          <p v-if="!result.offers.length" class="procurement-muted">Предложений за выбранный период нет. Измените период или источник.</p>
        </template>
      </section>
      <details class="block procurement-meta"><summary>Данные и ограничения</summary>
        <p>{{ catalog.disclaimer }}</p>
        <p>Каталог сохранён {{ dateLabel(catalog.captured_at) }} · {{ catalog.offers.length }} предложений.</p>
        <p v-if="catalog.truncated || catalog.skipped">Выборка ограничена первой страницей. Пропущено некорректных записей: {{ catalog.skipped }}.</p>
      </details>
    </template>
    <details v-if="history.length" class="block procurement-meta"><summary>Сохранённые планы ({{ history.length }})</summary>
      <button v-for="item in history" :key="item.id" class="row procurement-history" :disabled="busy || planning" @click="restore(item.id)"><span>{{ dateLabel(item.saved_at) }}<small>{{ item.covered }} объектов</small></span><b>{{ dollars(item.cost) }}</b></button>
    </details>
  </div>
</template>

<style scoped>
.procurement { font-size: 13px; }
.procurement h2 { font-size: 23px; letter-spacing: -.7px; line-height: 1.15; margin: 8px 0; }
.procurement h3 { font-size: 15px; margin: 0; }
.procurement p { line-height: 1.45; margin: 6px 0; }
.procurement-intro > p, .procurement-muted { color: var(--muted-foreground); font-size: 11px; }
.procurement label { display: flex; flex-direction: column; gap: 5px; font-size: 11px; }
.procurement input:not([type=checkbox]), .procurement select, .procurement textarea { min-width: 0; width: 100%; border: 1px solid var(--border); border-radius: 9px; padding: 9px 7px; background: var(--background); color: inherit; font: inherit; }
.procurement input[type=checkbox] { accent-color: #32836b; }
.procurement-source-settings { margin-top: 12px; border: 1px solid var(--border); border-radius: 11px; padding: 9px 11px; }
.procurement-source-settings > summary { display: flex; justify-content: space-between; gap: 10px; font-size: 11px; font-weight: 600; }
.procurement-source-settings > summary span { color: var(--muted-foreground); font-weight: 400; }
.procurement-source-form { display: grid; gap: 9px; padding-top: 12px; }
.procurement-dates { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.procurement-step-heading { display: flex; align-items: flex-start; gap: 10px; }
.procurement-step-heading p { color: var(--muted-foreground); font-size: 11px; }
.procurement-step-number { flex: none; display: grid; place-items: center; width: 24px; height: 24px; border-radius: 8px; background: rgba(50,131,107,.14); color: #32836b; font-weight: 700; }
.procurement-mode { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; margin-top: 12px; padding: 3px; border-radius: 10px; background: var(--surface-2); }
.procurement-mode button { min-height: 34px; border: 0; border-radius: 8px; background: none; color: var(--muted-foreground); font: inherit; font-size: 11px; cursor: pointer; }
.procurement-mode button.active { color: var(--foreground); background: var(--background); box-shadow: 0 1px 3px rgba(0,0,0,.08); font-weight: 600; }
.procurement-assets { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; margin: 12px 0 9px; }
.procurement-assets label { flex-direction: row; align-items: center; min-width: 0; min-height: 39px; border: 1px solid var(--border); border-radius: 10px; padding: 7px 9px; cursor: pointer; font-size: 12px; }
.procurement-assets label span { overflow: hidden; text-overflow: ellipsis; }
.procurement-assets .picked { border-color: #32836b; background: rgba(50,131,107,.1); }
.procurement-actions { display: flex; justify-content: space-between; gap: 6px; }
.procurement-inline-result { display: flex; align-items: center; gap: 7px; width: 100%; margin-top: 12px; padding: 9px 10px; border: 1px solid rgba(50,131,107,.35); border-radius: 9px; background: rgba(50,131,107,.08); color: var(--foreground); font: inherit; font-size: 11px; cursor: pointer; text-align: left; }
.procurement-inline-result span { flex: 1; }
.procurement-inline-result b { white-space: nowrap; }
.procurement-link { background: none; border: 0; color: #32836b; cursor: pointer; font: inherit; font-size: 11px; padding: 2px 0; text-align: left; }
.procurement-fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 13px 0 7px; }
.procurement-empty { display: flex; gap: 11px; align-items: flex-start; color: var(--muted-foreground); }
.procurement-empty b { color: var(--foreground); }
.procurement-empty svg { flex: none; margin-top: 2px; }
.procurement-basket { border-radius: 15px; padding: 14px; margin: 10px -3px 4px; background: color-mix(in srgb, var(--primary-container) 35%, var(--background)); border: 1px solid color-mix(in srgb, var(--primary) 20%, var(--border)); }
.procurement-check { display: flex; gap: 7px; align-items: center; color: #32836b; font-size: 11px; font-weight: 600; }
.procurement-check.warning { color: #ad6826; }
.procurement-price-row { display: flex; align-items: end; justify-content: space-between; gap: 8px; margin-top: 13px; }
.procurement-price-row > div { display: flex; flex-direction: column; }
.procurement-price { font-size: 30px; line-height: 1.12; letter-spacing: -1px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.procurement-limit { color: var(--muted-foreground); font-size: 11px; white-space: nowrap; padding-bottom: 4px; }
.procurement-extra { color: var(--muted-foreground); font-size: 11px; }
.procurement-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 13px 0; padding: 11px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.procurement-stats div { display: flex; flex-direction: column; gap: 3px; }
.procurement-stats b { font-size: 17px; font-weight: 650; }
.procurement-stats span { font-size: 10px; color: var(--muted-foreground); }
.procurement-selected-list { display: grid; gap: 4px; margin: 11px 0; }
.procurement-selected-item { display: flex; justify-content: space-between; gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--border); font-size: 11px; }
.procurement-selected-item b { white-space: nowrap; }
.procurement-shortfall { padding: 8px 10px; border-radius: 10px; background: rgba(220,162,45,.11); font-size: 11px; }
.procurement-saving { color: #32836b; font-size: 11px; }
.procurement-method { margin: 11px 0; font-size: 11px; }
.procurement-comparison { display: grid; grid-template-columns: 1fr auto; gap: 8px; padding: 10px 0; }
.procurement-comparison span { color: var(--muted-foreground); }
.procurement-basket .primary-button { margin-top: 5px; }
.procurement-alternatives-toggle { display: flex; align-items: center; justify-content: space-between; width: 100%; padding: 4px 0; border: 0; background: transparent; color: var(--foreground); font: inherit; font-weight: 600; cursor: pointer; }
.procurement-alternatives-toggle svg { transition: transform .2s; }
.procurement-alternatives-toggle svg.open { transform: rotate(90deg); }
.procurement-offer { border: 1px solid var(--border); padding: 11px; border-radius: 11px; margin-top: 8px; }
.procurement-offer.excluded { opacity: .6; }
.procurement-offer-title { display: flex; justify-content: space-between; gap: 8px; }
.procurement-offer-title strong { white-space: nowrap; }
.procurement-offer p, .procurement-meta { font-size: 11px; color: var(--muted-foreground); }
.procurement-offer-status { display: inline-block; font-size: 10px; padding: 4px 6px; border-radius: 6px; background: rgba(128,128,128,.08); }
.procurement details { font-size: 11px; }
.procurement summary { cursor: pointer; }
.procurement-offer details { margin-top: 9px; }
.procurement-offer details img { width: 100%; max-height: 180px; object-fit: contain; border-radius: 8px; margin-top: 10px; }
.procurement-offer details button { margin: 8px 0; }
.procurement .procurement-exclude { flex-direction: row; align-items: center; color: var(--muted-foreground); }
.procurement-source-url { overflow-wrap: anywhere; }
.procurement-more { margin-top: 10px; }
.procurement-history { justify-content: space-between; width: 100%; }
.procurement-history small { display: block; color: var(--muted-foreground); margin-top: 3px; }
.procurement-diagnostics { background: rgba(220,162,45,.08); }
.procurement-diagnostics li { margin: 6px 0; }
@media (max-width: 390px) { .procurement-assets { grid-template-columns: 1fr; } .procurement-price { font-size: 25px; } }
</style>
