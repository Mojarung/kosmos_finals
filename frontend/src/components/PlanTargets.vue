<script setup>
import { computed, ref, watch } from 'vue'
import { MapPin, ChevronLeft, ChevronRight, Search } from '@lucide/vue'
import { state, togglePointTarget } from '../store'
import { buildings } from '../lib/buildings'
import { buildingPlan, buildingTargets, toggleBuildingTarget, startBuildingSelection } from '../lib/buildingPlans'
import { compactRub } from '../lib/format'
const isBuilding = computed(() => state.objectMode === 'buildings')
const search = ref('')
const page = ref(0)
const onlySelected = ref(false)
const rows = computed(() => isBuilding.value
  ? (buildings.data?.features || []).map(f => ({ id:f.id, name:f.properties.tags?.name || f.properties.type_label || 'Здание', loss:f.properties.expected_loss_rub, type:f.properties.type_label }))
  : (state.analysis?.all_assets || state.analysis?.assets || []).map(a => ({id:a.id,name:a.name,loss:a.expected_loss_rub,type:'Точка'})))
const ids = computed(() => new Set(isBuilding.value ? buildingTargets.value.map(f=>f.id) : state.pointTargetIds ?? rows.value.map(a=>a.id)))
const scope = computed(() => isBuilding.value ? buildingPlan.scope : state.pointScope)
const matches = computed(() => rows.value.filter(row => (!onlySelected.value || ids.value.has(row.id)) && `${row.name} ${row.id} ${row.type}`.toLowerCase().includes(search.value.toLowerCase())).sort((a,b)=>(b.loss||0)-(a.loss||0)))
const pages = computed(() => Math.max(1,Math.ceil(matches.value.length/6)))
const visible = computed(() => matches.value.slice(page.value*6,page.value*6+6))
watch([search, onlySelected, isBuilding, () => matches.value.length], () => page.value=0)
function preset(value) {
  if (isBuilding.value) {
    if (value === 'selected' && buildingPlan.scope !== 'selected') buildingPlan.selectedIds = [...ids.value]
    buildingPlan.scope=value
    if (!buildingTargets.value.some(f=>f.id===buildingPlan.mustCover)) buildingPlan.mustCover=null
  } else {
    state.pointScope = value
    state.pointTargetIds = value === 'all' ? null : value === 'top' ? [...rows.value].sort((a,b)=>(b.loss||0)-(a.loss||0)).slice(0,10).map(a=>a.id) : [...ids.value]
    if (state.mustCover && !ids.value.has(state.mustCover)) state.mustCover=null
  }
}
function toggle(id) {
  if (isBuilding.value) {
    if (buildingPlan.scope !== 'selected') buildingPlan.selectedIds=[...ids.value]
    buildingPlan.scope='selected'; toggleBuildingTarget(id)
  } else togglePointTarget(id)
}
function pickOnMap() {
  if (isBuilding.value) startBuildingSelection()
  else { preset('selected'); state.pointPicking=true; state.showZones=false; state.decisionOpen=false; state.observeOpen=false }
}
function clear(){if(isBuilding.value){buildingPlan.scope='selected';buildingPlan.selectedIds=[];buildingPlan.mustCover=null}else {state.pointScope='selected';state.pointTargetIds=[];state.mustCover=null}}
</script>
<template>
  <section class="plan-targets">
    <div class="target-presets" role="group" aria-label="Выбор объектов для съёмки">
      <button :class="{selected:scope==='top'}" :aria-pressed="scope==='top'" v-tip="'10 объектов с наибольшим ожидаемым ущербом'" @click="preset('top')">Топ‑10</button>
      <button :class="{selected:scope==='all'}" :aria-pressed="scope==='all'" @click="preset('all')">Все <span>{{ rows.length.toLocaleString('ru-RU') }}</span></button>
      <button :disabled="!rows.length" @click="pickOnMap"><MapPin :size="14" />На карте</button>
    </div>
    <label class="target-search-field"><Search :size="16" /><input v-model="search" type="search" placeholder="Найти объект" aria-label="Поиск объектов для съёмки" /></label>
    <div class="target-list-tools"><label><input v-model="onlySelected" type="checkbox" />Только выбранные <b>{{ ids.size }}</b></label><button :disabled="!ids.size" @click="clear">Сбросить</button></div>
    <div class="target-column-caption"><span>Объект</span><span v-tip="'Ожидаемый ущерб, не цена здания или снимка'">Ущерб, ₽</span></div>
    <div class="compact-target-list"><label v-for="row in visible" :key="row.id" :class="{selected:ids.has(row.id)}"><input type="checkbox" :checked="ids.has(row.id)" :aria-label="`Выбрать ${row.name} ${row.id}`" @change="toggle(row.id)" /><span><b>{{ row.name }}</b><small>{{ row.id }}</small></span><strong>{{ compactRub(row.loss) }}</strong></label></div>
    <p v-if="!matches.length" class="panel-note">{{ rows.length ? 'Ничего не найдено' : isBuilding && buildings.busy ? 'Загружаем здания…' : 'Нет объектов в области' }}</p>
    <nav v-if="pages>1" class="target-pagination" aria-label="Страницы объектов"><button class="plan-settings" :disabled="!page" aria-label="Предыдущие объекты" @click="page--"><ChevronLeft :size="16" /></button><span>{{ page+1 }} / {{ pages }}</span><button class="plan-settings" :disabled="page+1>=pages" aria-label="Следующие объекты" @click="page++"><ChevronRight :size="16" /></button></nav>
  </section>
</template>
