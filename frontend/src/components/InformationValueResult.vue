<script setup>
import Money from './Money.vue'
import { plural, rub } from '../lib/format'
defineProps({result:Object,requiredId:String})
const barWidth=(value,maximum)=>`${maximum?Math.max(value>0?2:0,value/maximum*100):0}%`
</script>
<template>
      <section class="block voi-result">
        <h3 v-if="result.plan.zone_ids.length">{{ plural(result.plan.zone_ids.length, ['зона', 'зоны', 'зон']) }} за {{ rub(result.plan.derived_budget_rub) }}</h3>
        <h3 v-else>Заказ не окупается</h3>
        <figure class="voi-chart" data-testid="voi-chart" aria-label="Ожидаемая экономия на решениях и цена снимков в рублях">
          <figcaption>Экономия и цена <small>ожидаемые суммы, ₽</small></figcaption>
          <div class="voi-chart-line"><span>Экономия на решениях</span><b><Money :value="result.plan.voi_rub" compact /></b><div class="voi-track"><span class="voi-benefit-bar" :style="{ width: barWidth(result.plan.voi_rub, Math.max(result.plan.voi_rub, result.plan.derived_budget_rub)) }" /></div></div>
          <div class="voi-chart-line"><span>Цена снимков</span><b><Money :value="result.plan.derived_budget_rub" compact /></b><div class="voi-track"><span class="voi-price-bar" :style="{ width: barWidth(result.plan.derived_budget_rub, Math.max(result.plan.voi_rub, result.plan.derived_budget_rub)) }" /></div></div>
        </figure>
        <div class="voi-totals"><div class="voi-net"><span>Выгода после снимков</span><b><Money :value="result.plan.net_after_data_rub" /></b></div></div>
        <p class="panel-note">Сценарий: снимок помогает избежать лишних выездов и пропусков затопленных объектов. Обработка — ещё {{ rub(result.plan.other_cost_rub) }}.</p>
        <p v-if="result.status === 'no_positive_net_basket' && requiredId" class="panel-note">Проверить обязательный объект с положительной выгодой в этот срок не получится.</p>
      </section>
</template>
