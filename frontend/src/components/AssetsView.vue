<script setup>
import { computed } from 'vue'
import { ArrowRight, Check, ChevronRight, Pin } from '@lucide/vue'
import AnimatedNumber from './AnimatedNumber.vue'
import { selectedAsset, state, toggleGuarantee } from '../store'
import { compactRub, percent } from '../lib/format'

const emit = defineEmits(['asset'])
const analysis = computed(() => state.analysis)
const maxLoss = computed(() => Math.max(...(analysis.value?.assets || []).map(a => a.expected_loss_rub), 1))

function select(asset) {
  if (state.selectedAssetId === asset.id) { state.selectedAssetId = null; return }
  emit('asset', asset.id)
}
</script>

<template>
  <section class="block" v-tip="'Σ p × стоимость × уязвимость. Вероятность p из модели, стоимость и уязвимость объектов учебные'">
    <span class="caption">Ожидаемый ущерб портфеля</span>
    <div class="hero">
      <AnimatedNumber class="display" :value="analysis.metrics.expected_loss_rub / 1e6" :format="v => v.toLocaleString('ru-RU', { maximumFractionDigits: 2 })" />
      <span class="unit">млн ₽</span>
      <span class="hero-aside">{{ analysis.metrics.high_probability_assets }} из {{ analysis.assets.length }}<br>в воде</span>
    </div>
  </section>

  <div class="rows asset-rows">
    <template v-for="(asset, index) in analysis.assets" :key="asset.id">
      <button class="row asset-row" :class="{ selected: state.selectedAssetId === asset.id }"
        :aria-expanded="state.selectedAssetId === asset.id" @click="select(asset)">
        <span class="rank tabular">{{ index + 1 }}</span>
        <span class="asset-main">
          <strong>{{ asset.name }}</strong>
          <span class="asset-meta">
            <span class="meter"><span :style="{ transform: `scaleX(${asset.expected_loss_rub / maxLoss})` }"></span></span>
            <small class="tabular">p {{ percent(asset.p_flood) }}</small>
          </span>
        </span>
        <strong class="tabular">{{ compactRub(asset.expected_loss_rub) }}</strong>
        <ChevronRight :size="16" class="row-chevron" />
      </button>

      <Transition name="expand">
        <div v-if="state.selectedAssetId === asset.id && selectedAsset" class="asset-detail">
          <div class="mini-rows">
            <div v-tip="'Вероятность воды в сетке 3×3 пикселя вокруг объекта'"><span>Вероятность воды</span><b class="tabular">{{ percent(asset.p_flood, 1) }}</b></div>
            <div v-tip="'Насколько расходятся пять моделей ансамбля в этой точке'"><span>Разброс ансамбля</span><b class="tabular">{{ percent(asset.uncertainty, 1) }}</b></div>
            <div><span>Стоимость × уязвимость</span><b class="tabular">{{ compactRub(asset.value_rub) }} × {{ asset.vulnerability }}</b></div>
            <div v-tip="'Если вероятность модели ошибается на 20% в любую сторону'"><span>При p ± 20%</span><b class="tabular">{{ compactRub(asset.expected_loss_rub * 0.8) }} … {{ compactRub(Math.min(1, asset.p_flood * 1.2) * asset.value_rub * asset.vulnerability) }}</b></div>
          </div>
          <button class="primary-button" :class="{ done: state.mustCover === asset.id }" @click="toggleGuarantee">
            <Check v-if="state.mustCover === asset.id" :size="16" /><Pin v-else :size="16" />
            {{ state.mustCover === asset.id ? 'Закреплён в плане съёмки' : 'Обязательно доснять' }}
          </button>
        </div>
      </Transition>
    </template>
  </div>

  <button class="next-row" @click="state.tab = 'plan'">Собрать заказ съёмки <ArrowRight :size="16" /></button>
</template>
