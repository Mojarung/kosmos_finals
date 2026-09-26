<script setup>
import Money from './Money.vue'
import { number, percent } from '../lib/format'
defineProps({ plan: Object, baseline: Object, valueMode: Boolean })
const width = (value, other) => `${Math.max(value, other) > 0 ? Math.max(0, value) / Math.max(value, other) * 100 : 0}%`
</script>

<template>
  <div v-if="plan" class="cost-details">
    <template v-if="valueMode">
      <div class="cost-row"><span>Экономия на решениях</span><b><Money :value="plan.voi_rub" /></b></div>
      <div class="cost-meter"><i :style="{width:width(plan.voi_rub, plan.data_cost_rub)}" /></div>
      <div class="cost-row"><span>Цена снимков</span><b><Money :value="plan.data_cost_rub" /></b></div>
      <div class="cost-meter price"><i :style="{width:width(plan.data_cost_rub, plan.voi_rub)}" /></div>
      <div class="cost-row"><strong>Выгода после снимков</strong><b><Money :value="plan.net_after_data_rub" /></b></div>
      <p>Сценарий идеального снимка: меньше лишних выездов и пропущенных затоплений. Проверка, обработка и вычисления — ещё <Money :value="plan.other_cost_rub" />.</p>
      <details><summary>Расходы до и после</summary><div class="cost-body">
        <div class="cost-row"><span>Решения без снимков</span><b><Money :value="plan.action_cost_before_rub" /></b></div>
        <div class="cost-row"><span>Решения со снимками</span><b><Money :value="plan.action_cost_after_rub" /></b></div>
        <div class="cost-row"><span>Снимки и работа с ними</span><b><Money :value="plan.decision_cost_rub" /></b></div>
        <div class="cost-row"><span>Всего без покупки</span><b><Money :value="plan.action_cost_before_rub + (baseline?.other_cost_rub || 0)" /></b></div>
        <div class="cost-row"><span>Всего с текущим планом</span><b><Money :value="plan.action_cost_after_rub + plan.decision_cost_rub" /></b></div>
        <p>Расходы на решения включают выезды и ожидаемые потери от пропуска здания. Подбор максимизирует экономию за вычетом снимков; прочие затраты показаны отдельно.</p>
      </div></details>
    </template>
    <details><summary>Смета и формула цены</summary><div class="cost-body">
      <details v-for="line in plan.quote.lines" :key="line.candidate_id"><summary>{{ line.candidate_id }} · {{ number(line.area_km2, 2) }} км² · {{ number(line.cost_rub, 2) }} ₽</summary><div class="cost-body">
        <p>{{ line.sensor_type === 'sar' ? 'Радар' : 'Оптика' }} · {{ line.resolution_m }} м · {{ line.processing_level }}</p>
        <div class="cost-row"><span>Базовая ставка, ₽/км²</span><b>{{ number(line.base_rate_rub_km2, 2) }}</b></div>
        <div class="cost-row"><span>Обработка</span><b>× {{ number(line.processing_coef, 2) }}</b></div>
        <div class="cost-row"><span>Использование</span><b>× {{ number(line.usage_coef, 2) }}</b></div>
        <div class="cost-row"><span>Свежесть</span><b>× {{ number(line.freshness_coef, 2) }}</b></div>
        <div class="cost-row"><span>Скидка группы · {{ number(line.group_area_km2, 2) }} км²</span><b>× {{ number(line.discount_coef, 4) }}</b></div>
        <p>Произведение ставки и коэффициентов: {{ number(line.unit_price_rub_km2, 2) }} ₽/км² × {{ number(line.area_km2, 2) }} км² = {{ number(line.cost_rub, 2) }} ₽. Скидка зависит от суммарной площади группы заказа.</p>
      </div></details>
      <div class="cost-row"><span>Снимки</span><b><Money :value="plan.data_cost_rub" /></b></div>
      <div class="cost-row"><span>Работа аналитика</span><b><Money :value="plan.other_costs.review_rub" /></b></div>
      <div class="cost-row"><span>Обработка радара</span><b><Money :value="plan.other_costs.processing_rub" /></b></div>
      <div class="cost-row"><span>Вычисления</span><b><Money :value="plan.other_costs.compute_rub" /></b></div>
      <div class="cost-row"><strong>Всего за снимки и работу</strong><b><Money :value="plan.decision_cost_rub" /></b></div>
      <p>Тариф и трудозатраты сценарные. Бюджет ограничивает цену снимков. Покрыто {{ percent(plan.covered_doubt_share) }} сомнений по зданиям; это прокси модели, а не измеренное улучшение точности.</p>
    </div></details>
  </div>
</template>

<style scoped>
.cost-details,.cost-body { display:grid; gap:10px; }.cost-body { padding-top:10px; }.cost-row { display:flex; justify-content:space-between; gap:12px; font-size:12px; }.cost-row b { text-align:right; }p { margin:0; font-size:11px; line-height:1.5; color:var(--muted-foreground); }details { border-top:1px solid var(--border); padding-top:10px; }summary { cursor:pointer; font-size:12px; font-weight:550; }.cost-meter { height:5px; border-radius:5px; background:var(--border); overflow:hidden; }.cost-meter i { display:block; height:100%; background:var(--primary); }.cost-meter.price i { background:#a67733; }
</style>
