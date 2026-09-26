<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { Building2, Download, MapPin, ChevronLeft, ChevronRight, Satellite, Settings2 } from '@lucide/vue'
import BacktestPanel from './BacktestPanel.vue'
import FrontierChart from './FrontierChart.vue'
import BubbleTabs from './BubbleTabs.vue'
import ProcurementView from './ProcurementView.vue'
import Money from './Money.vue'
import SuppliersView from './SuppliersView.vue'
import PlanCostDetails from './PlanCostDetails.vue'
import { state, workspace, loadProjects } from '../store'
import { buildings, loadBuildings } from '../lib/buildings'
import { buildingPlan, buildingPlanRequest, buildingTargets, buildingScope, currentBuildingPlan, buildingPlanZones, downloadBuildingPlan, loadBuildingPlan, toggleBuildingTarget, startBuildingSelection, saveBuildingPlan } from '../lib/buildingPlans'
import { compactRub, rub, percent, plural } from '../lib/format'

const emit = defineEmits(['zone'])
const section = ref('plan')
const root = ref(null)
watch(section, async () => { await nextTick(); root.value?.closest('.panel-scroll')?.scrollTo({top:0}) })
watch(() => buildingPlan.picking, (picking, previous) => { if (previous && !picking) section.value = 'plan' })
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
const sections = computed(() => [{value:'plan',label:'План'}, {value:'buildings',label:'Здания'}, {value:'zones',label:`Зоны · ${zones.value.length}`}, {value:'settings',label:'Настройки'}])
const rejected = computed(() => buildingPlanZones.value.filter(z => !plan.value?.zone_ids.includes(z.id)))
const mustCover = computed({get: () => buildingPlan.mustCover || '', set: value => { buildingPlan.mustCover = value || null }})
const zoneReason = zone => ({ selected: 'В заказе', comparison: 'Другой вариант съёмки; для оптимального подбора выберите «Рекомендация»', late: 'Позже срока решения', plan_infeasible: 'Обязательное здание не покрыто при этих условиях', nothing_new: 'Не добавляет охвата по выбранной цели', over_budget: `Заказ с этой зоной: ${rub(zone.price_with_rub)} — выше бюджета`, lower_net_benefit: 'Другой набор даёт больше чистой пользы', cheaper_equal: 'Другой набор даёт не меньший результат дешевле' }[zone.reason] || '')
function focusZone(zone) { state.showZones = true; state.selectedZoneId = zone.id; emit('zone', zone.id) }
const scopes = computed(() => [{value:'all',label:'Все здания'}, {value:'selected',label:`Выбранные · ${buildingPlan.selectedIds.length}`}])
const chartPoints = computed(() => result.value ? ['A','B','C'].map(key => ({key:key.toLowerCase(),label:key,cost:result.value.strategies[key].data_cost_rub,share:result.value.strategies[key].coverage_share,active:state.strategy === key})) : [])
const strategyNames = { A:'Без съёмки', B:'Снять всё', C:'Рекомендация' }
function showCoverage() { state.showZones = true; emit('zone','__all__') }
function pickBuilding(id) { buildingPlan.scope = 'selected'; toggleBuildingTarget(id) }
function openCatalog() { state.suppliersOpen = false; state.procurementOpen = true }
function pickStrategy(key) { state.strategy = key; state.showZones = true }
</script>

<template>
  <section ref="root" class="building-plan" data-testid="building-plan">
    <template v-if="state.suppliersOpen">
      <button class="plan-back" @click="state.suppliersOpen = false"><ChevronLeft :size="15" />К плану зданий</button>
      <SuppliersView v-if="result && !buildingPlan.busy" :key="JSON.stringify(result.inputs)" :building-request="buildingPlanRequest" :building-zones="zones" />
      <p v-else class="plan-note">Готовим план зданий…</p>
    </template>
    <template v-else-if="state.procurementOpen">
      <button class="plan-back" @click="state.procurementOpen = false"><ChevronLeft :size="15" />К плану зданий</button>
      <ProcurementView v-if="result" :key="JSON.stringify(buildingScope)" :building-context="buildingScope" :building-bounds="result.scope.bbox" :building-summary="result.scope" />
      <p v-else class="plan-note">{{ buildingPlan.error || 'Готовим контуры для поиска снимков…' }}</p>
    </template>
    <template v-else>
      <header class="plan-heading"><Building2 :size="18" /><h2>Съёмка зданий</h2></header>
      <div class="strategy-switch"><button @click="openCatalog" :disabled="!result">Каталог снимков</button><button @click="state.suppliersOpen = true; state.procurementOpen = false" :disabled="!result">Поставщики и файлы</button></div>
      <BubbleTabs v-model="section" :options="sections" stretch label="Раздел плана зданий" />
      <p v-if="buildings.busy" class="plan-note" role="status">{{ buildings.progress === 100 ? 'Считаем стоимость и ущерб…' : buildings.progress == null ? 'Загружаем здания…' : `Загружено ${buildings.progress}% области…` }}</p>
      <div v-if="buildings.error || buildingPlan.error" class="error-note" role="alert">{{ buildings.error || buildingPlan.error }}<button class="ghost-button" @click="buildings.error ? loadBuildings() : loadBuildingPlan()">Повторить</button></div>
      <p v-if="!buildings.busy && !buildings.data?.features.length" class="plan-note">Пока нет контуров для расчёта. Загрузите GeoJSON в разделе «Объекты» или выберите другую область.</p>
      <p v-if="buildingPlan.busy" class="plan-note" role="status">Подбираем съёмку…</p>

      <template v-if="section === 'plan' && buildings.data?.features.length">
        <button class="scope-line" @click="section = 'buildings'"><span>{{ buildingPlan.scope === 'all' ? 'Все здания области' : 'Ваша подборка' }} · {{ buildingTargets.length }}</span><span>Изменить <ChevronRight :size="13" /></span></button>
        <template v-if="buildingTargets.length">
          <BubbleTabs v-model="state.procurementMethod" :options="[{value:'budget',label:'По бюджету'},{value:'value',label:'По пользе'}]" stretch label="Способ подбора съёмки" />
          <div class="plan-controls">
            <template v-if="state.procurementMethod === 'budget'">
            <label class="control-line" for="building-budget"><span>Бюджет</span><b>{{ rub(state.budget) }}</b></label>
            <input id="building-budget" v-model.number="state.budget" class="range" type="range" min="0" :max="maxBudget" step="250" :style="{'--progress':state.budget / maxBudget * 100 + '%'}" />
            </template>
            <label v-else class="plan-field">Выезд к одному зданию, ₽<input v-model.number="buildingPlan.visitCost" type="number" min="0" max="10000000" step="500" /></label>
            <div class="control-line"><span>Получить за</span><BubbleTabs v-model="state.deadline" :options="[4,8,12].map(value => ({value,label:value + ' ч'}))" size="sm" label="Срок съёмки зданий" /></div>
          </div>
          <div v-if="result && plan" class="plan-summary">
            <div class="summary-top"><span class="caption">{{ strategyNames[state.strategy] }}</span><span>{{ plural(zones.length,['зона','зоны','зон']) }}</span></div>
            <strong class="summary-price"><Money :value="plan.data_cost_rub" /></strong>
            <div class="coverage-line"><b>{{ plan.covered_building_ids.length }} / {{ result.scope.count }}</b><span>зданий в охвате</span></div>
            <div class="plan-meter"><i :style="{width:100 * plan.covered_building_ids.length / result.scope.count + '%'}" /></div>
            <p class="plan-note">{{ percent(plan.coverage_share) }} ущерба · {{ compactRub(plan.covered_expected_loss_rub) }}</p>
            <p v-if="state.procurementMethod === 'value'" class="plan-note">Польза после цены снимков: {{ rub(plan.net_after_data_rub) }}</p>
            <button class="primary-button wide" :disabled="!zones.length" @click="showCoverage"><MapPin :size="16" />Показать покрытие на карте</button>
          </div>
          <p v-if="plan?.reason" class="plan-warning" role="status">{{ plan.reason }}</p>
          <p v-else-if="plan && (!plan.timely || (state.procurementMethod === 'budget' && !plan.budget_feasible))" class="plan-warning">{{ !plan.timely ? 'Этот вариант не успевает к сроку.' : 'Этот вариант превышает бюджет.' }}</p>
          <p v-else-if="plan && !zones.length" class="plan-note">{{ state.procurementMethod === 'value' ? 'При этих условиях съёмка не окупается по сценарию.' : 'Нет полезной съёмки в рамках бюджета и срока.' }}</p>
          <div v-if="result" class="strategy-switch" role="group" aria-label="Варианты съёмки зданий"><button v-for="key in ['A','C','B']" :key="key" :aria-pressed="state.strategy === key" @click="pickStrategy(key)">{{ strategyNames[key] }}</button></div>
          <details v-if="plan && state.procurementMethod === 'value'" class="plan-details"><summary>Экономия, расходы и смета</summary><div class="detail-content"><PlanCostDetails :plan="plan" :baseline="result.strategies.A" value-mode /></div></details>
          <PlanCostDetails v-else-if="plan" :plan="plan" :baseline="result.strategies.A" :value-mode="false" />
          <p v-if="buildingPlan.mustCover" class="plan-note">Обязательное здание: {{ buildingPlan.mustCover }} <button class="plan-link" @click="buildingPlan.mustCover = null">Снять условие</button></p>
          <div class="plan-footer"><button class="tonal-button" :disabled="!result || buildingPlan.busy || buildingPlan.exporting" @click="downloadBuildingPlan"><Download :size="15" />{{ buildingPlan.exporting ? 'Готовим…' : 'Скачать план' }}</button><button class="ghost-button" @click="section = 'settings'"><Settings2 :size="15" />Расчёт и сохранение</button></div>
        </template>
        <template v-else><p class="plan-note">В подборке пока нет зданий.</p><button class="primary-button wide" @click="startBuildingSelection"><MapPin :size="16" />Выбрать здания на карте</button></template>
      </template>

      <template v-if="section === 'buildings' && buildings.data?.features.length">
        <BubbleTabs v-model="buildingPlan.scope" :options="scopes" stretch label="Какие здания снимать" />
        <button class="tonal-button wide" @click="startBuildingSelection"><MapPin :size="15" />{{ buildingPlan.selectedIds.length ? 'Изменить выбор на карте' : 'Выбрать здания на карте' }}</button>
        <p class="plan-note">{{ buildingPlan.scope === 'all' ? 'В расчёте все загруженные дома. Отметьте нужные для ручной подборки.' : 'В расчёте только отмеченные дома.' }}</p>
        <input v-model="search" class="plan-input" type="search" placeholder="Название, тип или ID" aria-label="Поиск здания для съёмки" />
        <div class="target-list"><label v-for="feature in visibleBuildings" :key="feature.id" class="target-row"><input type="checkbox" :checked="selectedIds.has(feature.id)" :aria-label="'Выбрать ' + feature.id" @change="pickBuilding(feature.id)" /><span><b>{{ feature.properties.tags.name || feature.properties.type_label }}</b><small>{{ feature.id }}</small></span><small>{{ compactRub(feature.properties.expected_loss_rub) }}</small></label></div>
        <p v-if="!matches.length" class="plan-note">Зданий по запросу не найдено.</p>
        <nav class="pagination" aria-label="Страницы зданий"><button class="ghost-icon small" :disabled="page === 0" aria-label="Предыдущие здания" @click="page--"><ChevronLeft :size="16" /></button><span>{{ page + 1 }} / {{ pages }} · {{ matches.length }} зданий</span><button class="ghost-icon small" :disabled="page + 1 >= pages" aria-label="Следующие здания" @click="page++"><ChevronRight :size="16" /></button></nav>
        <button class="primary-button wide" @click="section = 'plan'">К плану · {{ plural(buildingTargets.length,['здание','здания','зданий']) }}</button>
      </template>

      <template v-if="section === 'zones'">
        <p class="plan-note">Один снимок может покрывать много домов. На карте его границы показаны голубой рамкой.</p>
        <button class="tonal-button wide" :disabled="!zones.length" @click="showCoverage"><MapPin :size="15" />Показать все зоны</button>
        <div class="zone-list"><details v-for="(zone,index) in zones" :key="zone.id" class="plan-details"><summary>{{ index + 1 }} · {{ zone.building_count }} зданий · {{ rub(zone.cost_rub) }}</summary><div class="detail-content">
          <p class="plan-note">{{ zone.name }} · {{ zone.area_km2.toLocaleString('ru-RU',{maximumFractionDigits:2}) }} км² · {{ zoneReason(zone) }}</p>
          <p class="plan-note">{{ zone.product.sensor_type === 'sar' ? 'Радар 3 м' : 'Оптика 1 м' }} · {{ zone.product.delivery_hours }} ч · {{ zone.product.delivery_hours <= state.deadline ? 'успевает' : 'опоздает' }}</p>
          <div class="control-line"><span>В заказе / отдельно</span><b>{{ rub(zone.cost_rub) }} / {{ rub(zone.price_alone_rub) }}</b></div>
          <div class="control-line"><span>Ущерб в зоне</span><b>{{ compactRub(zone.expected_loss_rub) }}</b></div>
          <div v-if="state.procurementMethod === 'value'" class="control-line"><span>Польза зоны отдельно</span><b>{{ rub(zone.voi_alone_rub) }}</b></div>
          <button class="tonal-button" @click="focusZone(zone)">Показать зону на карте</button>
        </div></details></div>
        <details v-if="rejected.length" class="plan-details"><summary>Не вошли в заказ · {{ rejected.length }}</summary><div class="detail-content">
          <details v-for="zone in rejected" :key="zone.id" class="plan-details"><summary>{{ zone.name }} · {{ rub(zone.price_alone_rub) }}</summary><div class="detail-content">
            <p class="plan-note">{{ zoneReason(zone) }}</p><p class="plan-note">{{ zone.building_count }} зданий · {{ zone.product.delivery_hours }} ч · отдельно {{ rub(zone.price_alone_rub) }}; заказ с зоной {{ rub(zone.price_with_rub) }}.</p>
            <p v-if="state.procurementMethod === 'value'" class="plan-note">Польза зоны отдельно: {{ rub(zone.voi_alone_rub) }}. При пересечениях с заказом здания повторно не учитываются.</p>
            <button class="tonal-button" @click="focusZone(zone)">Показать на карте</button>
          </div></details>
        </div></details>
        <p v-if="!zones.length && !buildingPlan.busy" class="plan-note">В текущем варианте зон нет. Измените бюджет, срок или вариант съёмки.</p>
        <button class="ghost-button" :disabled="!result" @click="openCatalog"><Satellite :size="15" />Найти снимки у поставщиков</button>
      </template>

      <template v-if="section === 'settings'">
        <label class="plan-field">Обязательно проверить<select v-model="mustCover"><option value="">Без условия</option><option v-for="feature in buildingTargets" :key="feature.id" :value="feature.id">{{ feature.properties.tags.name || feature.properties.type_label }} · {{ feature.id }}</option></select></label>
        <span class="caption">Как выбирать съёмку</span>
        <BubbleTabs v-model="state.procurementMethod" :options="[{value:'budget',label:'По бюджету'},{value:'value',label:'По пользе'}]" stretch label="Подбор съёмки зданий" />
        <div v-if="state.procurementMethod === 'budget'" class="control-line"><span>Что важнее</span><BubbleTabs v-model="state.priority" :options="[{value:'risk',label:'Ущерб'},{value:'information',label:'Сомнения'}]" size="sm" label="Цель плана зданий" /></div>
        <label v-else class="plan-field">Выезд к одному зданию, ₽<input v-model.number="buildingPlan.visitCost" type="number" min="0" max="10000000" step="500" aria-label="Стоимость выезда к зданию" /></label>
        <details class="plan-details"><summary>Цена и обработка снимков</summary><div class="detail-content"><label class="plan-field">Базовая ставка, ₽/км²<input v-model.number="buildingPlan.baseRate" type="number" min="0" max="1000000" aria-label="Базовая ставка съёмки зданий" /></label><label class="plan-field">Обработка<select v-model="buildingPlan.processing"><option value="default">По продукту</option><option>L0</option><option>L1</option><option>L2</option></select></label><label class="plan-field">Использование<select v-model="buildingPlan.usage"><option value="internal">Внутреннее</option><option value="limited">Ограниченное</option><option value="unrestricted">Без ограничений</option></select></label><p class="plan-note">Сценарный тариф. Минимум 1 км² на зону; скидка по площади заказа.</p></div></details>
        <details v-if="result && plan" class="plan-details"><summary>Сравнение вариантов и бюджетов</summary><div class="detail-content"><div v-for="key in ['A','B','C']" :key="key" class="control-line"><span>{{ key }} · {{ strategyNames[key] }}</span><b>{{ rub(result.strategies[key].data_cost_rub) }} · {{ percent(result.strategies[key].coverage_share) }}</b></div><FrontierChart :frontier="result.frontier || []" :points="chartPoints" :budget="state.budget" description="Охват ущерба зданий при разных бюджетах" /></div></details>
        <details v-if="result && plan" class="plan-details"><summary>Расчёт и допущения</summary><div class="detail-content"><p class="plan-note">В расчёте {{ result.scope.count }} зданий. С оценкой ущерба: {{ result.scope.estimated }}; с условной ценой: {{ result.scope.synthetic_cost }}.</p><div class="control-line"><span>Снимки и обработка</span><b>{{ rub(plan.decision_cost_rub) }}</b></div><div class="control-line"><span>Сомнения в охвате</span><b>{{ percent(plan.covered_doubt_share) }}</b></div><p v-for="note in result.method" :key="note" class="plan-note">{{ note }}</p></div></details>
        <details class="plan-details"><summary>Проверка модели на снимке</summary><div class="detail-content"><BacktestPanel :scene-id="state.sceneId" /><p class="plan-note">Проверяется карта воды, а не денежный ущерб отдельного дома.</p></div></details>
        <details class="plan-details"><summary>Сохранить план в проект</summary><div class="detail-content"><select v-model="projectId" class="plan-input" aria-label="Проект для плана зданий"><option value="">Создать проект для зданий</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select><button class="tonal-button wide" :disabled="!result || buildingPlan.saving || buildingPlan.busy || buildings.busy" @click="saveBuildingPlan(projectId)">{{ buildingPlan.saving ? 'Сохраняем…' : 'Сохранить текущий план' }}</button><p v-if="buildingPlan.notice" class="plan-note" role="status">{{ buildingPlan.notice }}</p></div></details>
      </template>
    </template>
  </section>
</template>

<style scoped>
.building-plan { display: grid; gap: 12px; padding: 6px 0 12px; }
.plan-heading { display: flex; align-items: center; gap: 8px; }.plan-heading h2 { font-size: 16px; margin: 0; font-weight: 650; }.plan-heading > button { margin-left: auto; }
.plan-heading button,.plan-footer button,.plan-back { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; }
.plan-note { font-size: 11px; line-height: 1.5; color: var(--muted-foreground); margin: 0; }.plan-warning { font-size: 12px; line-height: 1.5; border-left: 2px solid var(--uncertain, #b88734); padding-left: 9px; margin: 0; }
.scope-line { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; font-size: 11px; padding: 4px 0; }.scope-line > span:last-child { display: flex; align-items: center; color: var(--primary); }
.plan-controls { display: grid; gap: 8px; }.control-line { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; }.control-line b { text-align: right; font-variant-numeric: tabular-nums; }
.plan-summary { padding: 14px; border: 1px solid var(--border); border-radius: 18px; background: var(--surface-2); display: grid; gap: 9px; }.summary-top { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted-foreground); }.summary-price { font-size: 30px; letter-spacing: -.7px; line-height: 1.15; }.coverage-line { display: flex; gap: 7px; align-items: baseline; font-size: 12px; }.coverage-line > span { color: var(--muted-foreground); }.plan-meter { height: 4px; border-radius: 8px; background: var(--border); overflow: hidden; }.plan-meter i { display: block; height: 100%; background: var(--primary); border-radius: inherit; }
.strategy-switch { display: flex; gap: 3px; padding: 3px; border-radius: 12px; background: var(--surface-2); }.strategy-switch button { flex: 1; padding: 7px 3px; border-radius: 9px; font-size: 11px; color: var(--muted-foreground); }.strategy-switch button[aria-pressed=true] { background: var(--surface-1); color: var(--foreground); box-shadow: 0 1px 4px #00000008; }
.plan-footer { display: flex; justify-content: space-between; gap: 5px; }.plan-link { color: var(--primary); text-decoration: underline; }.plan-back { width: fit-content; color: var(--primary); }
.plan-input,.plan-field input,.plan-field select { width: 100%; box-sizing: border-box; padding: 9px 10px; border: 1px solid var(--border); border-radius: 10px; color: var(--foreground); background: var(--surface-1); font: inherit; font-size: 12px; }.plan-field { display: grid; gap: 6px; font-size: 12px; }
.target-list { display: grid; }.target-row { display: flex; align-items: center; gap: 9px; padding: 9px 0; border-bottom: 1px solid var(--border); cursor: pointer; }.target-row > span { flex: 1; min-width: 0; display: grid; gap: 3px; }.target-row b { font-size: 12px; font-weight: 550; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.target-row small { font-size: 10px; color: var(--muted-foreground); }.target-row input { accent-color: var(--primary); }.pagination { display: flex; justify-content: space-between; align-items: center; font-size: 11px; color: var(--muted-foreground); }
.zone-list { display: grid; }.zone-row { display: flex; align-items: center; gap: 10px; padding: 12px 0; border-bottom: 1px solid var(--border); text-align: left; }.zone-row > span:nth-child(2) { display: grid; gap: 5px; flex: 1; }.zone-row b { font-size: 12px; font-weight: 550; }.zone-row small { font-size: 11px; color: var(--muted-foreground); }.zone-number { width: 26px; height: 26px; display: grid; place-items: center; border-radius: 9px; background: var(--surface-2); font-size: 12px; }
.plan-details { border-top: 1px solid var(--border); padding-top: 12px; }.plan-details summary { cursor: pointer; font-size: 12px; font-weight: 550; }.detail-content { display: grid; gap: 12px; padding-top: 12px; }
</style>
