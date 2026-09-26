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
</script>

<template>
  <section class="backtest block" data-testid="backtest" :aria-busy="loading">
    <div class="backtest-heading"><div><span class="caption">ПРОВЕРКА НА ПРОШЛЫХ ПАВОДКАХ</span><h3>Что исправил бы заказ</h3></div><span class="backtest-tag">ретроспектива</span></div>
    <p v-if="loading" role="status">Загружаем результаты 70 чипов…</p>
    <div v-else-if="error" role="alert"><p>{{ error }}</p><button type="button" class="backtest-action" @click="load">Повторить</button></div>
    <template v-else-if="summary">
      <p class="backtest-lead">При лимите <b>{{ rub(result.budget_rub) }}</b> и сроке <b>{{ result.deadline_hours }} ч</b> стратегия C снизила ошибку EL‑прокси на <strong>{{ fmt(summary.c_error_reduction_percent) }}</strong> за <strong>{{ fmt(summary.c_price_share_of_b_percent) }}</strong> цены широкой съёмки B.</p>
      <div class="backtest-numbers">
        <div><b>{{ compactRub(summary.error_a_rub) }} → {{ compactRub(summary.error_c_rub) }}</b><span>сумма |pVq − yVq|, A → C</span></div>
        <div><b>{{ summary.c_corrected_statuses }} из {{ summary.b_corrected_statuses }}</b><span>статусов вода/суша исправлено C от возможных у B</span></div>
        <div><b>{{ summary.labelled_assets }} из {{ summary.chips * 10 }}</b><span>объектов с валидной меткой</span></div>
      </div>
      <p class="backtest-range">Разброс по чипам: {{ fmt(summary.c_reduction_chip_bootstrap_95_percent[0]) }}–{{ fmt(summary.c_reduction_chip_bootstrap_95_percent[1]) }} (описательный 95% bootstrap). B стоит {{ compactRub(summary.b_cost_rub) }} на всех 70 чипах и может выходить за лимит; C — {{ compactRub(summary.c_cost_rub) }}.</p>
      <details class="backtest-details">
        <summary>По выборкам и как считали</summary>
        <div class="backtest-splits"><div v-for="part in ['test_core', 'bolivia']" :key="part"><b>{{ part === 'test_core' ? 'test_core' : 'Bolivia' }} · {{ result.parts[part].chips }} чипов</b><span>−{{ fmt(result.parts[part].c_error_reduction_percent) }} ошибки · C за {{ fmt(result.parts[part].c_price_share_of_b_percent) }} B</span></div></div>
        <p>Десять объектов на чип взяты из S1 без знания меток. По вероятностям построили зоны и выбрали C точным перебором; только после этого открыли ручную метку внутри купленных зон. На некупленных участках p оставили прежней. A не покупает снимок, B покрывает весь чип.</p>
        <p>Метка Sen1Floods11 построена по S2 с шагом около 10 м. Она заменяет предполагаемое наблюдение в эксперименте, но не равна кадру 1 м. Это ошибка прокси воды и учебного EL, а не измеренный ущерб или предотвращённые потери. Чипы одного события зависимы; test и Bolivia уже изучались.</p>
      </details>
      <p v-if="current" class="backtest-current">Текущий чип {{ current.chip_id }}: C исправила {{ current.C.corrected_statuses }} статусов среди {{ current.labelled_assets }} размеченных объектов; ошибка {{ compactRub(current.A.error_after_rub) }} → {{ compactRub(current.C.error_after_rub) }}. Это фиксированный тест при {{ rub(result.budget_rub) }}, а не текущие настройки ползунка.</p>
      <p v-else class="backtest-current">Для текущей области ручной метки нет. Числа выше относятся только к историческим чипам.</p>
    </template>
  </section>
</template>

<style scoped>
.backtest { border-left: 3px solid var(--reference); }
.backtest-heading { display: flex; justify-content: space-between; gap: 8px; align-items: start; }
.backtest h3 { margin: 5px 0 0; font-size: 17px; letter-spacing: -.25px; }
.backtest-tag { padding: 4px 7px; border: 1px solid var(--border); border-radius: 5px; font-size: 10px; color: var(--muted-foreground); }
.backtest-lead { margin: 13px 0; line-height: 1.5; }
.backtest-lead strong { font-size: 17px; color: var(--reference); font-variant-numeric: tabular-nums; }
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
