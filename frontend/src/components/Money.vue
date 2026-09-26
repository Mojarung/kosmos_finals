<script setup>
import { computed } from 'vue'
import { compactRub, rub } from '../lib/format'
import { localMoney, localMoneyTip } from '../lib/currency'

// Сумма в рублях, под ней бледно — справочно в валюте страны снимка, если она известна.
// localOnly: только бледная строка — под числом, которое выводит другой компонент (анимированный итог).
const props = defineProps({ value: { type: Number, default: null }, compact: Boolean, localOnly: Boolean })
const local = computed(() => localMoney(props.value, props.compact))
</script>

<template>
  <small v-if="localOnly" v-show="local" v-tip="localMoneyTip()" class="money-local tabular">{{ local }}</small>
  <span v-else class="money">{{ compact ? compactRub(value) : rub(value) }}<small v-if="local" v-tip="localMoneyTip()" class="money-local tabular">{{ local }}</small></span>
</template>

<style scoped>
/* inline-flex: базовая линия по рублёвой строке, подписи рядом не съезжают вниз. */
.money { display: inline-flex; flex-direction: column; }
.money-local {
  margin-top: 2px;
  color: var(--muted-foreground);
  font-size: clamp(9px, .62em, 12px);
  font-weight: 400;
  letter-spacing: 0;
  line-height: 1.2;
  opacity: .75;
  white-space: nowrap;
}
</style>
