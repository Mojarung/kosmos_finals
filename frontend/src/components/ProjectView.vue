<script setup>
import { computed, onMounted, ref } from 'vue'
import { ArrowLeft, FileUp, Save, FolderPlus } from '@lucide/vue'
import { compactRub, percent, plural } from '../lib/format'
import { createProject, importProjectAssets, loadProjects, openProject, saveProjectDecision, state, workspace } from '../store'

const name = ref('')
const busy = ref(false)
const mine = computed(() => workspace.projects.filter(project => project.scene_id === state.sceneId))

onMounted(loadProjects)

async function run(action) {
  busy.value = true
  workspace.error = ''
  workspace.notice = ''
  try { await action() } catch (error) { workspace.error = error.message }
  finally { busy.value = false }
}
function create() {
  if (!name.value.trim()) return
  run(async () => { await createProject(name.value); name.value = '' })
}
function upload(event) {
  const file = event.target.files?.[0]
  if (file) run(() => importProjectAssets(file))
  event.target.value = ''
}
</script>

<template>
  <section class="project-intro">
    <span class="caption">Рабочее пространство</span>
    <h3>Ваши объекты и решения</h3>
    <p>Учебные 10 точек остаются в сравнении A/B/C. Здесь можно оценить свой список на том же снимке.</p>
  </section>

  <div v-if="workspace.error" class="error-note" role="alert">{{ workspace.error }}</div>
  <div v-if="workspace.notice" class="source-line" role="status">{{ workspace.notice }}</div>

  <template v-if="!workspace.active || workspace.active.scene_id !== state.sceneId">
    <section class="block">
      <label class="caption" for="project-name">Новый проект для этого снимка</label>
      <form class="project-create" @submit.prevent="create">
        <input id="project-name" v-model="name" maxlength="100" placeholder="Название проекта" required />
        <button class="tonal-icon" type="submit" :disabled="busy" aria-label="Создать проект"><FolderPlus :size="18" /></button>
      </form>
    </section>
    <section v-if="mine.length" class="block">
      <span class="caption">Сохранённые проекты</span>
      <button v-for="project in mine" :key="project.id" class="row project-list-row" @click="run(() => openProject(project.id))">
        <strong>{{ project.name }}</strong>
        <small>{{ plural(project.asset_count, ['объект', 'объекта', 'объектов']) }} · {{ plural(project.decision_count, ['решение', 'решения', 'решений']) }}</small>
      </button>
    </section>
  </template>

  <template v-else>
    <section class="block project-active">
      <button class="project-back" @click="workspace.active = null"><ArrowLeft :size="15" /> Все проекты</button>
      <h3>{{ workspace.active.name }}</h3>
      <p class="source-line">{{ plural(workspace.active.assets.length, ['объект', 'объекта', 'объектов']) }} · {{ plural(workspace.active.decisions.length, ['решение', 'решения', 'решений']) }}</p>
      <label class="tonal-button project-upload" :class="{ disabled: busy }">
        <FileUp :size="16" /> Импорт CSV или GeoJSON
        <input type="file" accept=".csv,.geojson,.json,text/csv,application/geo+json" :disabled="busy" @change="upload" />
      </label>
      <p class="project-help">CSV: name, longitude, latitude, value_rub, vulnerability, value_source. Для GeoJSON — Point и те же свойства. Источник цены обязателен.</p>
    </section>

    <section v-if="workspace.active.assets.length" class="block">
      <span class="caption">Реальные активы · {{ workspace.active.assets.filter(a => a.p_water != null).length }} оценено</span>
      <div v-for="asset in workspace.active.assets" :key="asset.id" class="project-asset">
        <div><strong>{{ asset.name }}</strong><small>{{ asset.value_source }}</small></div>
        <div class="project-asset-value"><b>{{ compactRub(asset.expected_loss_rub) }}</b><small>{{ asset.p_water == null ? 'вне покрытия' : percent(asset.p_water, 1) + ' воды' }}</small></div>
      </div>
      <p class="project-help">Стоимость и уязвимость берём из вашего файла. Вероятность воды — из текущего снимка. Это оценка риска, а не подтверждённый ущерб.</p>
    </section>

    <section class="block">
      <span class="caption">История решений</span>
      <button class="primary-button wide" :disabled="busy" @click="run(saveProjectDecision)"><Save :size="16" /> Сохранить текущие A/B/C</button>
      <div v-for="decision in workspace.active.decisions" :key="decision.id" class="project-decision">
        <strong>{{ new Date(decision.created_at).toLocaleString('ru-RU') }}</strong>
        <small>Бюджет {{ compactRub(decision.inputs.budget_rub) }} · C {{ compactRub(decision.strategies.C.data_cost_rub) }}</small>
        <small>{{ plural(decision.asset_count, ['свой объект', 'своих объекта', 'своих объектов']) }} · риск {{ compactRub(decision.operator_expected_loss_rub) }}</small>
      </div>
      <p v-if="!workspace.active.decisions.length" class="project-help">Сохраните решение, чтобы потом сравнить бюджеты и выбранные зоны.</p>
    </section>
  </template>
</template>

<style scoped>
.project-intro{padding:20px 20px 9px}.project-intro h3,.project-active h3{margin:5px 0;font-size:20px;letter-spacing:-.03em}.project-intro p,.project-help{font-size:12px;line-height:1.5;color:var(--muted-foreground);margin:7px 0}.project-create{display:flex;gap:8px;margin-top:10px}.project-create input{flex:1;min-width:0;border:1px solid var(--border);border-radius:12px;padding:10px 12px;background:var(--surface-1);color:var(--foreground);font:inherit}.project-list-row{width:100%;display:flex;flex-direction:column;align-items:flex-start;gap:4px}.project-list-row small,.project-asset small,.project-decision small{color:var(--muted-foreground)}.project-back{display:flex;align-items:center;gap:6px;padding:0;border:0;background:none;color:var(--primary);font:inherit;cursor:pointer}.project-upload{position:relative;display:inline-flex;margin:10px 0 4px;cursor:pointer}.project-upload input{position:absolute;width:1px;height:1px;opacity:0}.project-upload.disabled{opacity:.5;pointer-events:none}.project-asset{display:flex;justify-content:space-between;gap:10px;padding:11px 0;border-bottom:1px solid var(--border);font-size:13px}.project-asset div,.project-asset-value,.project-decision{display:flex;flex-direction:column;gap:4px}.project-asset-value{text-align:right;white-space:nowrap}.project-decision{padding:11px 0;border-bottom:1px solid var(--border);font-size:12px}
</style>
