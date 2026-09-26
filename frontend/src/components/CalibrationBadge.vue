<script setup>
import { ShieldQuestion } from '@lucide/vue'

/*
 * Критерий 10: уверенность модели не выдаётся за проверенную неопределённость.
 * Калибровка p и смысл «шанса ошибки» проверены только на Sen1Floods11 GRD,
 * поэтому на кадре RTC рядом с этими числами стоит эта плашка.
 */
const HINT = 'Вероятность p и шанс ошибки карты откалиброваны и проверены только на снимках Sen1Floods11 GRD. '
  + 'На этом кадре Sentinel‑1 RTC проверки нет: шанс ошибки показывает, где модель не уверена, а не измеренную долю ошибок'
</script>

<template>
  <span v-tip="HINT" class="calib-badge"><ShieldQuestion :size="14" aria-hidden="true" />калибровка на этом снимке не проверена</span>
</template>
