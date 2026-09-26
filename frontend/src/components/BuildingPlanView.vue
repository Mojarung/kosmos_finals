<script setup>
import { computed, onMounted, ref } from 'vue'
import { Building2, Download, MapPin } from '@lucide/vue'
import BacktestPanel from './BacktestPanel.vue'
import FrontierChart from './FrontierChart.vue'
import BubbleTabs from './BubbleTabs.vue'
import ProcurementView from './ProcurementView.vue'
import { state, workspace, loadProjects } from '../store'
import { buildings, loadBuildings, openBuildingDemo } from '../lib/buildings'
import { buildingPlan, buildingTargets, buildingScope, currentBuildingPlan, buildingPlanZones, downloadBuildingPlan, loadBuildingPlan, toggleBuildingTarget, startBuildingSelection, saveBuildingPlan } from '../lib/buildingPlans'
import { compactRub, rub, percent, plural } from '../lib/format'
const projectId = ref('')
onMounted(loadProjects)
const projects = computed(() => workspace.projects.filter(p => p.scene_id === state.sceneId))
const emit = defineEmits(['zone'])
const mode = computed({ get: () => state.procurementOpen ? 'skyfi' : state.procurementMethod, set: value => { state.procurementOpen = value === 'skyfi'; if (value !== 'skyfi') state.procurementMethod = value } })
const result = computed(() => buildingPlan.result)
const plan = computed(() => currentBuildingPlan.value)
const zones = computed(() => buildingPlanZones.value.filter(z => plan.value?.zone_ids.includes(z.id)))
const chosenBuildings = computed(() => buildings.data?.features.filter(f => buildingPlan.selectedIds.includes(f.id)) || [])
const maxBudget = computed(() => Math.max(10000, Math.ceil((result.value?.strategies.B.data_cost_rub || 0) / 1000) * 1000, state.budget))
const modes = [{ value: 'budget', label: 'По бюджету' }, { value: 'value', label: 'По пользе' }, { value: 'skyfi', label: 'SkyFi' }]
const scopes = computed(() => [{ value: 'all', label: 'Все здания' }, { value: 'selected', label: `Выбранные · ${buildingPlan.selectedIds.length}` }])
const chartPoints = computed(() => result.value ? ['A', 'B', 'C'].map(key => ({key:key.toLowerCase(),label:key,cost:result.value.strategies[key].data_cost_rub,share:result.value.strategies[key].coverage_share,active:state.strategy === key})) : [])
const strategyNames = { A: 'Без съёмки', B: 'Все здания', C: 'Рекомендация' }
function selectStrategy(value) { state.strategy = value; state.showZones = true }
</script>

<template>
  <section class="building-plan" data-testid="building-plan">
    <div class="plan-heading"><Building2 :size="19" /><h2>Съёмка зданий</h2><span>{{ buildingTargets.length }}</span></div>
    <BubbleTabs v-model="buildingPlan.scope" :options="scopes" stretch label="Какие здания снимать" />
    <p v-if="buildings.busy" role="status" class="plan-note">{{ buildings.progress === 100 ? 'Считаем стоимость и ущерб…' : buildings.progress == null ? 'Подключаем загрузку зданий…' : `Загружено ${buildings.progress}% области. Собираем контуры зданий…` }}</p>
    <div v-else-if="!buildings.data" class="plan-note">{{ buildings.error || 'Ожидаем здания для этого снимка.' }}<div class="plan-actions"><button class="ghost-button" @click="loadBuildings()">Повторить загрузку</button><button class="ghost-button" @click="openBuildingDemo">Пример: Пайпорта</button></div></div>
    <template v-else>
      <p v-if="!buildings.data.features.length" class="plan-empty">В OSM нет контуров зданий для этой области. <button class="ghost-button" @click="state.objectMode = 'points'">Перейти к точкам</button></p>
      <button v-if="buildings.data.features.length" class="tonal-button wide" @click="startBuildingSelection"><MapPin :size="16" />{{ buildingPlan.selectedIds.length ? 'Изменить выбор на карте' : 'Выбрать здания на карте' }}</button>
      <p class="plan-note" v-if="buildings.data.features.length">{{ buildingPlan.scope === 'all' ? 'Считаем съёмку для всех загруженных зданий. «Выбранные» — ваша ручная подборка.' : buildingTargets.length ? 'Считаем съёмку только для вашей подборки.' : 'Подборка пока пустая. Нажмите «Выбрать здания на карте» и отметьте дома.' }}</p>
      <details v-if="buildingPlan.scope === 'selected' && chosenBuildings.length" class="plan-details"><summary>Здания в подборке · {{ chosenBuildings.length }}</summary><div v-for="feature in chosenBuildings" :key="feature.id" class="target-row"><span>{{ feature.properties.tags.name || feature.properties.type_label }}<small>{{ feature.id }}</small></span><button class="ghost-button" :aria-label="'Убрать из плана ' + feature.id" @click="toggleBuildingTarget(feature.id)">×</button></div></details>
      <BubbleTabs v-model="mode" :options="modes" stretch label="Подбор съёмки зданий" />
      <div v-if="buildingPlan.error" role="alert" class="error-note">{{ buildingPlan.error }} <button @click="loadBuildingPlan">Повторить</button></div>
      <p v-if="buildingPlan.busy" role="status" class="plan-note">Подбираем зоны по контурам и стоимости зданий…</p>
      <ProcurementView v-if="mode === 'skyfi' && result" :key="JSON.stringify(buildingScope)" :building-context="buildingScope" :building-bounds="result.scope.bbox" :building-summary="result.scope" />
      <template v-else-if="mode !== 'skyfi' && buildingTargets.length">
        <div class="plan-controls">
          <label class="plan-control-title" for="building-budget"><span>Бюджет на снимки</span><b>{{ rub(state.budget) }}</b></label>
          <input id="building-budget" v-model.number="state.budget" class="range" type="range" min="0" :max="maxBudget" step="250" :style="{ '--progress': state.budget / maxBudget * 100 + '%' }" />
          <div class="plan-control-title"><span>Получить за</span><BubbleTabs v-model="state.deadline" :options="[4, 8, 12].map(value => ({ value, label: value + ' ч' }))" size="sm" label="Срок съёмки зданий" /></div>
          <div v-if="mode === 'budget'" class="plan-control-title"><span>Что важнее</span><BubbleTabs v-model="state.priority" :options="[{value:'risk',label:'Ущерб'}, {value:'information',label:'Сомнения'}]" size="sm" label="Цель плана зданий" /></div>
          <p v-if="buildingPlan.mustCover" class="plan-note">Обязательно доснять: {{ buildingPlan.mustCover }} <button class="ghost-button" @click="buildingPlan.mustCover = null">Снять условие</button></p>
          <label v-if="mode === 'value'" class="plan-visit">Выезд к одному зданию, ₽<input v-model.number="buildingPlan.visitCost" type="number" min="0" max="10000000" step="500" aria-label="Стоимость выезда к зданию" /></label>
        </div>
        <details class="plan-details"><summary>Настроить цену снимков</summary>
          <label class="plan-visit">Базовая ставка, ₽/км²<input v-model.number="buildingPlan.baseRate" type="number" min="0" max="1000000" aria-label="Базовая ставка съёмки зданий" /></label>
          <label class="plan-visit">Обработка<select v-model="buildingPlan.processing"><option value="default">По продукту</option><option>L0</option><option>L1</option><option>L2</option></select></label>
          <label class="plan-visit">Использование<select v-model="buildingPlan.usage"><option value="internal">Внутреннее</option><option value="limited">Ограниченное</option><option value="unrestricted">Без ограничений</option></select></label>
          <p class="plan-note">Сценарный тариф, как для точек. Скидка считается по площади группы заказа.</p>
        </details>
        <template v-if="result && plan">
          <div class="plan-strategies" role="group" aria-label="Варианты съёмки зданий"><button v-for="key in ['A', 'C', 'B']" :key="key" :aria-pressed="state.strategy === key" @click="selectStrategy(key)"><span>{{ strategyNames[key] }}</span><b>{{ compactRub(result.strategies[key].data_cost_rub) }}</b><small>{{ !result.strategies[key].mandatory_satisfied ? 'Условие не выполнено' : !result.strategies[key].timely ? 'Позже срока' : !result.strategies[key].budget_feasible ? 'Выше бюджета' : plural(result.strategies[key].covered_building_ids.length, ['здание', 'здания', 'зданий']) }}</small></button></div>
          <div class="plan-summary">
            <span>{{ plural(zones.length, ['зона', 'зоны', 'зон']) }} съёмки</span><strong>{{ rub(plan.data_cost_rub) }}</strong>
            <button class="tonal-button wide" :disabled="!zones.length" @click="state.showZones = true; emit('zone', '__all__')"><MapPin :size="15" />Показать покрытие на карте</button>
            <div class="plan-control-title"><span>Зданий в охвате</span><b>{{ plan.covered_building_ids.length }} / {{ result.scope.count }}</b></div>
            <div class="plan-meter"><i :style="{ width: 100 * plan.covered_building_ids.length / result.scope.count + '%' }" /></div>
            <div class="plan-control-title"><span>Ущерб в охвате</span><b>{{ compactRub(plan.covered_expected_loss_rub) }} · {{ percent(plan.coverage_share) }}</b></div>
            <div class="plan-control-title"><span>Сомнения в охвате зданий</span><b>{{ percent(plan.covered_doubt_share) }}</b></div>
            <div v-if="mode === 'value'" class="plan-control-title"><span>Польза после цены снимков</span><b>{{ rub(plan.net_after_data_rub) }}</b></div>
          </div>
          <p v-if="plan.reason" role="status" class="plan-note plan-warning">{{ plan.reason }}</p>
          <p v-if="!plan.timely || !plan.budget_feasible" class="plan-note plan-warning">Этот вариант {{ !plan.timely ? 'не успевает к сроку' : 'превышает бюджет' }}. Он показан для сравнения.</p>
          <p v-else-if="!zones.length && !plan.reason" class="plan-note">{{ mode === 'value' ? 'В этом бюджете нет корзины с положительной пользой.' : 'Покупка не добавляет охваченного ущерба либо не помещается в бюджет.' }}</p>
          <div v-if="zones.length" class="plan-zones"><button v-for="zone in zones" :key="zone.id" @click="emit('zone', zone.id)" @mouseenter="state.hoverZoneId = zone.id" @mouseleave="state.hoverZoneId = null"><MapPin :size="16" /><span><b>Зона {{ zones.indexOf(zone) + 1 }} · {{ zone.area_km2.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) }} км²</b><small>{{ plural(zone.building_count, ['здание', 'здания', 'зданий']) }} · {{ zone.product.sensor_type === 'sar' ? 'Радар 3 м' : 'Оптика 1 м' }} · {{ zone.product.delivery_hours }} ч</small></span><span>↗</span></button></div>
          <p class="plan-note">Зона — участок снимка, внутри которого может быть много домов. Голубая рамка показывает покрытие. Сценарная цена — за зону от 1 км². Одно здание в нескольких зонах учитывается один раз.</p>
          <details class="plan-details"><summary>Сравнить A / B / C и бюджеты</summary>
            <div v-for="key in ['A','B','C']" :key="key" class="plan-control-title"><span>{{ key }} · {{ strategyNames[key] }}</span><b>{{ rub(result.strategies[key].data_cost_rub) }} · {{ percent(result.strategies[key].coverage_share) }} ущерба</b></div>
            <FrontierChart :frontier="result.frontier || []" :points="chartPoints" :budget="state.budget" description="Доля ущерба зданий, охваченная лучшим набором зон при каждой стоимости" />
            <p class="plan-note">Линия показывает максимальный охват ущерба при разных бюджетах с текущим сроком и обязательным зданием.</p>
          </details>
          <details class="plan-details"><summary>Расходы и допущения</summary><p class="plan-note">В расчёте {{ result.scope.count }} зданий; сигнал и ущерб доступны у {{ result.scope.estimated }}. Условная цена у {{ result.scope.synthetic_cost }}.</p><div class="plan-control-title"><span>Снимки и обработка</span><b>{{ rub(plan.decision_cost_rub) }}</b></div><div v-if="mode === 'value'" class="plan-control-title"><span>Экономия на решениях</span><b>{{ rub(plan.voi_rub) }}</b></div><p v-for="note in result.method" :key="note" class="plan-note">{{ note }}</p></details>
          <details class="plan-details"><summary>Проверка модели на снимке</summary><BacktestPanel :scene-id="state.sceneId" /><p class="plan-note">Эта проверка относится к карте воды. Она не подтверждает денежный ущерб отдельного здания.</p></details>
          <details class="plan-details"><summary>Сохранить план в проект</summary><select v-model="projectId" aria-label="Проект для плана зданий"><option value="">Создать проект для зданий</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select><button class="tonal-button wide" :disabled="buildingPlan.saving || buildingPlan.busy || buildings.busy" @click="saveBuildingPlan(projectId)">{{ buildingPlan.saving ? 'Сохраняем…' : 'Сохранить текущий план' }}</button><p v-if="buildingPlan.notice" role="status" class="plan-note">{{ buildingPlan.notice }}</p></details>
          <button class="primary-button wide" :disabled="buildingPlan.busy || buildings.busy || buildingPlan.exporting" @click="downloadBuildingPlan"><Download :size="16" />{{ buildingPlan.exporting ? 'Готовим…' : 'Скачать план по зданиям' }}</button>
        </template>
      </template>
    </template>
  </section>
</template>

<style scoped>
.building-plan { display: grid; gap: 13px; padding: 5px 0 16px; }.plan-heading { display: flex; align-items: center; gap: 8px; }.plan-heading h2 { font-size: 17px; margin: 0; }.plan-heading > span { margin-left: auto; font-size: 11px; background: #82939818; padding: 4px 8px; border-radius: 16px; }
.plan-note { font-size: 11px; line-height: 1.55; opacity: .75; margin: 0; }.plan-actions { display: flex; gap: 8px; margin-top: 10px; }.plan-empty { padding: 16px; text-align: center; font-size: 12px; background: #8293980c; border-radius: 10px; }
.plan-controls { display: grid; gap: 12px; }.plan-control-title { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 11px; }.plan-control-title b { font-variant-numeric: tabular-nums; text-align: right; }.plan-visit { display: grid; gap: 5px; font-size: 11px; }.plan-visit input { border: 1px solid #82939860; padding: 9px; background: transparent; border-radius: 8px; width: 100%; color: inherit; box-sizing: border-box; }
.plan-strategies { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 6px; }.plan-strategies button { display: grid; gap: 7px; background: #8293980a; color: inherit; border: 1px solid #82939825; border-radius: 10px; padding: 10px 5px; font-size: 10px; cursor: pointer; }.plan-strategies button[aria-pressed="true"] { background: #42858e18; border-color: #42858e80; }.plan-strategies b { font-size: 12px; }.plan-strategies small { font-size: 10px; opacity: .65; }
.plan-summary { display: grid; gap: 10px; padding: 15px; border-radius: 13px; background: #42858e12; font-size: 11px; }.plan-summary > strong { font-size: 26px; letter-spacing: -.6px; }.plan-meter { height: 5px; border-radius: 9px; background: #82939825; overflow: hidden; }.plan-meter i { display: block; height: 100%; background: #398b81; }
.plan-zones { display: grid; gap: 5px; }.plan-zones button { display: flex; align-items: center; gap: 8px; padding: 10px 3px; border: 0; border-bottom: 1px solid #82939825; background: transparent; color: inherit; text-align: left; cursor: pointer; }.plan-zones button > span:nth-child(2) { flex: 1; display: grid; gap: 4px; font-size: 12px; }.plan-zones small { opacity: .65; font-size: 10px; }
.plan-details select { width: 100%; box-sizing: border-box; padding: 9px; margin: 9px 0; border: 1px solid #82939860; border-radius: 8px; background: transparent; color: inherit; }
.plan-details { border-top: 1px solid #82939825; padding-top: 12px; }.plan-details summary { cursor: pointer; font-size: 12px; font-weight: 600; }.plan-details p, .plan-details > div { margin-top: 12px; }.target-row { display: flex; align-items: center; justify-content: space-between; font-size: 11px; }.target-row small { display: block; opacity: .6; margin-top: 4px; }.plan-warning { border-left: 2px solid #b88734; padding-left: 9px; }
</style>
