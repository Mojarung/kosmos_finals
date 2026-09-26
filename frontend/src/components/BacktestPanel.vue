<script setup>
import { computed, onMounted, ref } from 'vue'
import { compactRub, number, rub } from '../lib/format'

const props = defineProps({ sceneId: { type: String, required: true } })
const result = ref(null)
const error = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const response = await fetch('/api/backtest')
    if (!response.ok) throw new Error(String(response.status))
    result.value = await response.json()
  } catch {
    error.value = 'Не удалось загрузить ретроспективный расчёт.'
  } finally {
    loading.value = false
  }
}
onMounted(load)
const summary = computed(() => result.value?.summary)
const current = computed(() => result.value?.chips.find(item => item.chip_id === props.sceneId))
const fmt = value => value == null ? 'нет оценки' : number(value, 1) + '%'
const comparisons = computed(() => {
  if (!summary.value) return []
  const s = summary.value
  return [
    { key: 'A', name: 'без заказа', error: s.error_a_rub, cost: 0 },
    { key: 'B', name: 'вся область', error: s.error_b_rub, cost: s.b_cost_rub },
    { key: 'C', name: 'выбранные зоны', error: s.error_c_rub, cost: s.c_cost_rub },
  ]
})
const barWidth = (value, maximum) => `${maximum ? Math.max(value > 0 ? 2 : 0, value / maximum * 100) : 0}%`
</script>

<template>
  <details class="backtest disclosure" data-testid="backtest" :aria-busy="loading">
    <summary>Проверка на 70 участках <span v-if="summary">−{{ fmt(summary.c_error_reduction_percent) }} ошибки</span></summary>
    <div class="disclosure-body">
      <p v-if="loading" role="status">Загружаем результаты…</p>
      <div v-else-if="error" role="alert"><p>{{ error }}</p><button type="button" class="backtest-action" @click="load">Повторить</button></div>
      <template v-else-if="summary">
        <p class="panel-note">Исторический тест прежней модели S1. На каждом участке выбирали зоны в пределах {{ rub(result.budget_rub) }}, затем сверяли прогноз с ручной разметкой.</p>
        <figure class="backtest-chart" data-testid="backtest-chart" aria-label="Ошибка расчётного ущерба и цена на 70 участках">
          <figcaption>Суммы за 70 участков</figcaption>
          <div class="backtest-axis"><span>Заказ</span><span>Ошибка оценки</span><span>Цена снимков</span></div>
          <div v-for="row in comparisons" :key="row.key" class="backtest-chart-row">
            <span class="backtest-chart-name"><b>{{ row.key }}</b><small>{{ row.name }}</small></span>
            <div class="backtest-measure"><b>{{ compactRub(row.error) }}</b><div class="backtest-track"><span class="backtest-error-bar" :style="{ width: barWidth(row.error, summary.error_a_rub) }" /></div></div>
            <div class="backtest-measure"><b>{{ compactRub(row.cost) }}</b><div class="backtest-track"><span class="backtest-cost-bar" :style="{ width: barWidth(row.cost, summary.b_cost_rub) }" /></div></div>
          </div>
        </figure>
        <p class="panel-note">Выбор зон C: на {{ fmt(summary.c_error_reduction_percent) }} меньше ошибки за {{ fmt(summary.c_price_share_of_b_percent) }} цены съёмки всей области B. Это уточнение оценки, а не предотвращённый ущерб.</p>
        <details class="backtest-details"><summary>Выборка и ограничения</summary>
          <p>{{ summary.labelled_assets }} размеченных объектов; исправлено {{ summary.c_corrected_statuses }} из {{ summary.b_corrected_statuses }} ошибочных статусов «вода / суша».</p>
          <p>Разброс: {{ fmt(summary.c_reduction_chip_bootstrap_95_percent[0]) }}–{{ fmt(summary.c_reduction_chip_bootstrap_95_percent[1]) }} (описательный 95% bootstrap).</p>
          <div v-for="part in ['test_core', 'bolivia']" :key="part">{{ part === 'test_core' ? 'test_core' : 'Bolivia' }}: {{ result.parts[part].chips }} участков · −{{ fmt(result.parts[part].c_error_reduction_percent) }} ошибки.</div>
          <p>Метка по S2 с шагом 10 м заменяет покупку в эксперименте; это не проверка снимка 1 м. B открывает метку везде и может превышать бюджет. Участки одного события зависимы.</p>
          <p v-if="current">{{ current.chip_id }} в старом тесте: ошибка {{ compactRub(current.A.error_after_rub) }} → {{ compactRub(current.C.error_after_rub) }}.</p>
          <p>Это фиксированный тест при {{ rub(result.budget_rub) }} и {{ result.deadline_hours }} ч. Ползунок и новая модель эти результаты не меняют.</p>
        </details>
      </template>
    </div>
  </details>
</template>

<style scoped>
.backtest { border-left: 3px solid var(--reference); }
.backtest-heading { display: flex; justify-content: space-between; gap: 8px; align-items: start; }
.backtest h3 { margin: 5px 0 0; font-size: 17px; letter-spacing: -.25px; }
.backtest-tag { padding: 4px 7px; border: 1px solid var(--border); border-radius: 5px; font-size: 10px; color: var(--muted-foreground); }
.backtest-lead { margin: 13px 0; line-height: 1.5; }
.backtest-lead strong { font-size: 17px; color: var(--reference); font-variant-numeric: tabular-nums; }
.backtest-chart { margin: 14px 0; padding: 11px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); }
.backtest-chart figcaption { font-size: 12px; font-weight: 700; }
.backtest-chart figcaption small { display: block; margin-top: 2px; font-size: 10px; font-weight: 400; color: var(--muted-foreground); }
.backtest-axis, .backtest-chart-row { display: grid; grid-template-columns: minmax(68px, .8fr) repeat(2, minmax(0, 1fr)); align-items: center; gap: 9px; }
.backtest-axis { margin-top: 12px; color: var(--muted-foreground); font-size: 9px; line-height: 1.2; }
.backtest-chart-row { min-height: 49px; border-top: 1px solid var(--border); }
.backtest-chart-name b, .backtest-chart-name small, .backtest-measure b { display: block; }
.backtest-chart-name b { font-size: 13px; }
.backtest-chart-name small { color: var(--muted-foreground); font-size: 9px; line-height: 1.2; }
.backtest-measure b { margin-bottom: 5px; font-size: 10px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.backtest-track { height: 7px; border-radius: 8px; background: var(--border); overflow: hidden; }
.backtest-track span { display: block; height: 100%; border-radius: inherit; }
.backtest-error-bar { background: var(--reference); }
.backtest-cost-bar { background: #a67733; }
.backtest-chart p { margin: 9px 0 0; color: var(--muted-foreground); font-size: 10px; line-height: 1.35; }
.backtest-numbers { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
.backtest-numbers div { min-width: 0; padding: 10px 8px; background: var(--surface-2); border-radius: 6px; }
.backtest-numbers b, .backtest-numbers span { display: block; }
.backtest-numbers b { font-size: 12px; font-variant-numeric: tabular-nums; }
.backtest-numbers span { margin-top: 5px; font-size: 10px; line-height: 1.35; color: var(--muted-foreground); }
.backtest-range, .backtest-current, .backtest-details { font-size: 11px; line-height: 1.5; color: var(--muted-foreground); }
.backtest-current { border-top: 1px solid var(--border); padding-top: 9px; }
.backtest-details summary { cursor: pointer; color: var(--foreground); }
.backtest-splits { display: grid; gap: 6px; margin-top: 10px; }
.backtest-splits div { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 4px; }
.backtest-action { border: 0; background: transparent; padding: 0; color: var(--reference); text-decoration: underline; cursor: pointer; }
@media (max-width: 520px) { .backtest-numbers { grid-template-columns: 1fr; } }
</style>

<style scoped>
.backtest { border-left: 0; }
.backtest > summary { flex-wrap: wrap; }
.backtest > summary span { font-size: 12px; color: var(--reference); }
.backtest-chart { border: 0; background: none; padding: 0; }
.backtest-chart-name small, .backtest-measure b, .backtest-axis { font-size: 12px; }
.backtest-details { font-size: 12px; }
.backtest-details summary { padding-block: 10px; }
</style>
