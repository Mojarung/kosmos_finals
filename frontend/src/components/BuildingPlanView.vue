<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { MapPin, ChevronLeft, ChevronRight, Satellite } from '@lucide/vue'
import BacktestPanel from './BacktestPanel.vue'
import FrontierChart from './FrontierChart.vue'
import BubbleTabs from './BubbleTabs.vue'
import ProcurementView from './ProcurementView.vue'
import SuppliersView from './SuppliersView.vue'
import PlanCostDetails from './PlanCostDetails.vue'
import BudgetPlanSummary from './BudgetPlanSummary.vue'
import InformationValueResult from './InformationValueResult.vue'
import { state, workspace, loadProjects } from '../store'
import { buildings, loadBuildings } from '../lib/buildings'
import { buildingPlan, buildingPlanRequest, buildingTargets, buildingScope, currentBuildingPlan, buildingPlanZones, downloadBuildingPlan, loadBuildingPlan, toggleBuildingTarget, startBuildingSelection, saveBuildingPlan } from '../lib/buildingPlans'
import { compactRub, rub, percent, plural } from '../lib/format'

const emit = defineEmits(['zone'])
const selectionOpen = ref(false)
watch(() => buildingPlan.picking, (picking, previous) => { if (previous && !picking) selectionOpen.value = false })
const projectId = ref('')
const search = ref('')
const page = ref(0)
const pageSize = 6
onMounted(loadProjects)
const projects = computed(() => workspace.projects.filter(p => p.scene_id === state.sceneId))
const result = computed(() => buildingPlan.result)
const plan = computed(() => currentBuildingPlan.value)
const zones = computed(() => buildingPlanZones.value.filter(z => plan.value?.zone_ids.includes(z.id)))
const maxBudget = computed(() => Math.max(10000, Math.ceil((result.value?.strategies.B.data_cost_rub || 0) / 1000) * 1000, state.budget))
const selectedIds = computed(() => new Set(buildingPlan.selectedIds))
const matches = computed(() => (buildings.data?.features || []).filter(f => `${f.properties.name} ${f.id} ${f.properties.type_label}`.toLowerCase().includes(search.value.toLowerCase())))
const pages = computed(() => Math.max(1, Math.ceil(matches.value.length / pageSize)))
const visibleBuildings = computed(() => matches.value.slice(page.value * pageSize, (page.value + 1) * pageSize))
watch([search, () => buildings.data?.catalog_id], () => { page.value = 0 })
const rejected = computed(() => buildingPlanZones.value.filter(z => !plan.value?.zone_ids.includes(z.id)))
const mustCover = computed({get: () => buildingPlan.mustCover || '', set: value => { buildingPlan.mustCover = value || null }})
const zoneReason = zone => ({ selected: 'В заказе', comparison: 'Другой вариант съёмки; для оптимального подбора выберите «Рекомендация»', late: 'Позже срока решения', plan_infeasible: 'Обязательное здание не покрыто при этих условиях', nothing_new: 'Не добавляет охвата по выбранной цели', over_budget: `Заказ с этой зоной: ${rub(zone.price_with_rub)} — выше бюджета`, lower_net_benefit: 'Другой набор даёт больше чистой пользы', cheaper_equal: 'Другой набор даёт не меньший результат дешевле' }[zone.reason] || '')
function focusZone(zone) { state.showZones = true; state.selectedZoneId = zone.id; emit('zone', zone.id) }
const scopes = computed(() => [{value:'all',label:'Все здания'}, {value:'selected',label:`Выбранные · ${buildingPlan.selectedIds.length}`}])
const chartPoints = computed(() => result.value ? ['A','B','C'].map(key => ({key:key.toLowerCase(),label:key,cost:result.value.strategies[key].data_cost_rub,share:result.value.strategies[key].coverage_share,active:state.strategy === key})) : [])
const strategyNames = { A:'Не покупать', B:'Снять всё', C:'Только нужные зоны' }
function showCoverage() { state.showZones = true; emit('zone','__all__') }
function pickBuilding(id) { buildingPlan.scope = 'selected'; toggleBuildingTarget(id) }
function openCatalog() { state.suppliersOpen = false; state.procurementOpen = true }
const valueResult = computed(() => plan.value ? { plan: {...plan.value, derived_budget_rub:plan.value.data_cost_rub}, status:plan.value.zone_ids.length?'positive_net_basket':'no_positive_net_basket' } : null)
watch(() => state.procurementMethod, method => { if (method === 'value') state.strategy = 'C' }, { immediate: true })
</script>

<template>
  <section class="building-plan" data-testid="building-plan">
    <p v-if="buildings.busy" class="panel-note" role="status">{{ buildings.progress === 100 ? 'Считаем стоимость и ущерб…' : buildings.progress == null ? 'Загружаем здания…' : `Загружено ${buildings.progress}% области…` }}</p>
    <div v-if="buildings.error || buildingPlan.error" class="error-note" role="alert">{{ buildings.error || buildingPlan.error }}<button class="ghost-button" @click="buildings.error ? loadBuildings() : loadBuildingPlan()">Повторить</button></div>
    <p v-if="!buildings.busy && !buildings.data?.features.length" class="panel-note">Пока нет контуров. Загрузите GeoJSON в разделе «Объекты» или выберите другую область.</p>
    <p v-if="buildingPlan.busy" class="panel-note" role="status">Подбираем съёмку…</p>
    <SuppliersView v-if="state.suppliersOpen && result && !buildingPlan.busy" :key="JSON.stringify(result.inputs)" :building-request="buildingPlanRequest" :building-zones="zones" />
    <ProcurementView v-else-if="state.procurementOpen && result" :key="JSON.stringify(buildingScope)" :building-context="buildingScope" :building-bounds="result.scope.bbox" :building-summary="result.scope" />
    <template v-else-if="!state.suppliersOpen && !state.procurementOpen">
      <details v-if="buildings.data?.features.length" class="disclosure" :open="selectionOpen" @toggle="selectionOpen = $event.target.open">
        <summary>{{ buildingPlan.scope === 'all' ? 'Все здания' : 'Выбранные здания' }} · {{ buildingTargets.length }} · Изменить</summary>
        <div class="disclosure-body detail-content">
        <BubbleTabs v-model="buildingPlan.scope" :options="scopes" stretch label="Какие здания снимать" />
        <button class="tonal-button wide" @click="startBuildingSelection"><MapPin :size="15" />{{ buildingPlan.selectedIds.length ? 'Изменить выбор на карте' : 'Выбрать здания на карте' }}</button>
        <p class="panel-note">{{ buildingPlan.scope === 'all' ? 'В расчёте все загруженные дома. Отметьте нужные для ручной подборки.' : 'В расчёте только отмеченные дома.' }}</p>
        <input v-model="search" class="plan-input" type="search" placeholder="Название, тип или ID" aria-label="Поиск здания для съёмки" />
        <div class="target-list"><label v-for="feature in visibleBuildings" :key="feature.id" class="target-row"><input type="checkbox" :checked="selectedIds.has(feature.id)" :aria-label="'Выбрать ' + feature.id" @change="pickBuilding(feature.id)" /><span><b>{{ feature.properties.tags.name || feature.properties.type_label }}</b><small>{{ feature.id }}</small></span><small>{{ compactRub(feature.properties.expected_loss_rub) }}</small></label></div>
        <p v-if="!matches.length" class="panel-note">Зданий по запросу не найдено.</p>
        <nav class="pagination" aria-label="Страницы зданий"><button class="ghost-icon small" :disabled="page === 0" aria-label="Предыдущие здания" @click="page--"><ChevronLeft :size="16" /></button><span>{{ page + 1 }} / {{ pages }} · {{ matches.length }} зданий</span><button class="ghost-icon small" :disabled="page + 1 >= pages" aria-label="Следующие здания" @click="page++"><ChevronRight :size="16" /></button></nav>
        <button class="primary-button wide" @click="selectionOpen = false">К плану · {{ plural(buildingTargets.length,['здание','здания','зданий']) }}</button>
        </div>
      </details>
      <template v-if="buildingTargets.length">
        <BudgetPlanSummary v-if="state.procurementMethod === 'budget'" :strategies="result?.strategies" :plan="plan" :max-budget="maxBudget" :required-id="buildingPlan.mustCover" :required-label="buildingPlan.mustCover" :covered-count="plan?.covered_building_ids.length || 0" :object-words="['здание','здания','зданий']" doubt-label="сомнений по зданиям" @clear-required="buildingPlan.mustCover = null" />
        <div v-if="state.procurementMethod === 'value'" class="voi">
          <section class="block voi-intro"><h3>Окупится ли снимок?</h3><label for="building-visit-cost">Цена выезда к одному объекту, ₽</label><input id="building-visit-cost" v-model.number="buildingPlan.visitCost" type="number" min="0" max="10000000" step="500" aria-label="Цена выезда" /><div class="voi-control"><span>Получить за</span><BubbleTabs v-model="state.deadline" :options="[4,8,12].map(value => ({value,label:value + ' ч'}))" size="sm" label="Срок решения" /></div></section>
          <InformationValueResult v-if="valueResult" :result="valueResult" :required-id="buildingPlan.mustCover" />
          <p v-if="plan?.reason" class="warn-note">{{ plan.reason }}</p>
        </div>
        <details class="disclosure"><summary>Состав заказа · {{ plural(zones.length,['зона','зоны','зон']) }}</summary><div class="disclosure-body detail-content">
        <p class="panel-note">Один снимок может покрывать много домов. На карте его границы показаны голубой рамкой.</p>
        <button class="tonal-button wide" :disabled="!zones.length" @click="showCoverage"><MapPin :size="15" />Показать все зоны</button>
        <div class="zone-list"><details v-for="(zone,index) in zones" :key="zone.id" class="disclosure"><summary>{{ index + 1 }} · {{ zone.building_count }} зданий · {{ rub(zone.cost_rub) }}</summary><div class="detail-content">
          <p class="panel-note">{{ zone.name }} · {{ zone.area_km2.toLocaleString('ru-RU',{maximumFractionDigits:2}) }} км² · {{ zoneReason(zone) }}</p>
          <p class="panel-note">{{ zone.product.sensor_type === 'sar' ? 'Радар 3 м' : 'Оптика 1 м' }} · {{ zone.product.delivery_hours }} ч · {{ zone.product.delivery_hours <= state.deadline ? 'успевает' : 'опоздает' }}</p>
          <div class="control-line"><span>В заказе / отдельно</span><b>{{ rub(zone.cost_rub) }} / {{ rub(zone.price_alone_rub) }}</b></div>
          <div class="control-line"><span>Ущерб в зоне</span><b>{{ compactRub(zone.expected_loss_rub) }}</b></div>
          <div v-if="state.procurementMethod === 'value'" class="control-line"><span>Польза зоны отдельно</span><b>{{ rub(zone.voi_alone_rub) }}</b></div>
          <button class="tonal-button" @click="focusZone(zone)">Показать зону на карте</button>
        </div></details></div>
        <details v-if="rejected.length" class="disclosure"><summary>Не вошли в заказ · {{ rejected.length }}</summary><div class="detail-content">
          <details v-for="zone in rejected" :key="zone.id" class="disclosure"><summary>{{ zone.name }} · {{ rub(zone.price_alone_rub) }}</summary><div class="detail-content">
            <p class="panel-note">{{ zoneReason(zone) }}</p><p class="panel-note">{{ zone.building_count }} зданий · {{ zone.product.delivery_hours }} ч · отдельно {{ rub(zone.price_alone_rub) }}; заказ с зоной {{ rub(zone.price_with_rub) }}.</p>
            <p v-if="state.procurementMethod === 'value'" class="panel-note">Польза зоны отдельно: {{ rub(zone.voi_alone_rub) }}. При пересечениях с заказом здания повторно не учитываются.</p>
            <button class="tonal-button" @click="focusZone(zone)">Показать на карте</button>
          </div></details>
        </div></details>
        <p v-if="!zones.length && !buildingPlan.busy" class="panel-note">В текущем варианте зон нет. Измените бюджет, срок или вариант съёмки.</p>
        <button class="ghost-button" :disabled="!result" @click="openCatalog"><Satellite :size="15" />Найти снимки у поставщиков</button>
        </div></details>
        <details v-if="plan" class="disclosure"><summary>{{ state.procurementMethod === 'value' ? 'Расходы до и после · смета' : 'Как посчитана цена' }}</summary><div class="disclosure-body"><PlanCostDetails :plan="plan" :baseline="result.strategies.A" :value-mode="state.procurementMethod === 'value'" /></div></details>
        <details v-if="result && plan" class="disclosure"><summary>Сравнение вариантов и бюджетов</summary><div class="detail-content"><div v-for="key in ['A','B','C']" :key="key" class="control-line"><span>{{ key }} · {{ strategyNames[key] }}</span><b>{{ rub(result.strategies[key].data_cost_rub) }} · {{ percent(result.strategies[key].coverage_share) }}</b></div><FrontierChart :frontier="result.frontier || []" :points="chartPoints" :budget="state.budget" description="Охват ущерба зданий при разных бюджетах" /></div></details>
        <details class="disclosure"><summary>Проверка модели на снимке</summary><div class="detail-content"><BacktestPanel :scene-id="state.sceneId" /><p class="panel-note">Проверяется карта воды, а не денежный ущерб отдельного дома.</p></div></details>
        <details class="disclosure"><summary>Условия расчёта и сохранение</summary><div class="disclosure-body detail-content">
        <label class="plan-field">Обязательно проверить<select v-model="mustCover"><option value="">Без условия</option><option v-for="feature in buildingTargets" :key="feature.id" :value="feature.id">{{ feature.properties.tags.name || feature.properties.type_label }} · {{ feature.id }}</option></select></label>
        <details class="disclosure"><summary>Цена и обработка снимков</summary><div class="detail-content"><label class="plan-field">Базовая ставка, ₽/км²<input v-model.number="buildingPlan.baseRate" type="number" min="0" max="1000000" aria-label="Базовая ставка съёмки зданий" /></label><label class="plan-field">Обработка<select v-model="buildingPlan.processing"><option value="default">По продукту</option><option>L0</option><option>L1</option><option>L2</option></select></label><label class="plan-field">Использование<select v-model="buildingPlan.usage"><option value="internal">Внутреннее</option><option value="limited">Ограниченное</option><option value="unrestricted">Без ограничений</option></select></label><p class="panel-note">Сценарный тариф. Минимум 1 км² на зону; скидка по площади заказа.</p></div></details>
        <details v-if="result && plan" class="disclosure"><summary>Расчёт и допущения</summary><div class="detail-content"><p class="panel-note">В расчёте {{ result.scope.count }} зданий. С оценкой ущерба: {{ result.scope.estimated }}; с условной ценой: {{ result.scope.synthetic_cost }}.</p><div class="control-line"><span>Снимки и обработка</span><b>{{ rub(plan.decision_cost_rub) }}</b></div><div class="control-line"><span>Сомнения в охвате</span><b>{{ percent(plan.covered_doubt_share) }}</b></div><p v-for="note in result.method" :key="note" class="panel-note">{{ note }}</p></div></details>
        <details class="disclosure"><summary>Сохранить план в проект</summary><div class="detail-content"><select v-model="projectId" class="plan-input" aria-label="Проект для плана зданий"><option value="">Создать проект для зданий</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select><button class="tonal-button wide" :disabled="!result || buildingPlan.saving || buildingPlan.busy || buildings.busy" @click="saveBuildingPlan(projectId)">{{ buildingPlan.saving ? 'Сохраняем…' : 'Сохранить текущий план' }}</button><p v-if="buildingPlan.notice" class="panel-note" role="status">{{ buildingPlan.notice }}</p></div></details>
        </div></details>
      </template>
      <p v-else class="panel-note">Отметьте здания в блоке «Изменить» или выберите их на карте.</p>
    </template>
  </section>
</template>
<style scoped>
.building-plan { display:block; }
.detail-content { display:grid; gap:12px; }.control-line { display:flex; justify-content:space-between; gap:8px; font-size:13px; }.control-line b { text-align:right; }
.plan-input,.plan-field input,.plan-field select { width:100%; box-sizing:border-box; padding:9px 10px; border:1px solid var(--border); border-radius:10px; color:var(--foreground); background:var(--surface-1); font:inherit; font-size:13px; }.plan-field { display:grid; gap:6px; font-size:13px; }
.target-list { display:grid; }.target-row { display:flex; align-items:center; gap:9px; padding:9px 0; border-bottom:1px solid var(--border); cursor:pointer; }.target-row > span { flex:1; min-width:0; display:grid; gap:3px; }.target-row b { font-size:13px; font-weight:550; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.target-row small { font-size:11px; color:var(--muted-foreground); }.target-row input { accent-color:var(--primary); }.pagination { display:flex; justify-content:space-between; align-items:center; font-size:12px; color:var(--muted-foreground); }
</style>
