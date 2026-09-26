<script setup>
import { computed, ref, watch } from 'vue'
import { MapPin, Layers } from '@lucide/vue'
import { state } from '../store'
import { km, rub, plural } from '../lib/format'
const props=defineProps({zones:{type:Array,default:()=>[]},words:{type:Array,default:()=>['объект','объекта','объектов']},busy:Boolean})
const emit=defineEmits(['zone'])
const expanded=ref(false)
const openIds=ref(new Set())
const shown=computed(()=>expanded.value?props.zones:props.zones.slice(0,3))
watch(()=>state.sceneId,()=>{expanded.value=false;openIds.value=new Set()})
function focus(id){state.showZones=true;emit('zone',id)}
function reveal(id,open){const next=new Set(openIds.value);open?next.add(id):next.delete(id);openIds.value=next}
</script>
<template>
  <section class="plan-coverage" :aria-busy="busy">
    <div class="coverage-heading"><div><span class="eyebrow">Покрытие снимками</span><h3>{{ plural(zones.length,['зона','зоны','зон']) }}</h3></div><Layers :size="24" /></div>
    <p class="panel-note">Одна зона может покрывать несколько объектов. Значок карты покажет её границы.</p>
    <p v-if="busy" class="panel-note" role="status">Обновляем зоны…</p>
    <p v-else-if="!zones.length" class="panel-note">Зон в заказе нет. Измените бюджет, срок или вариант съёмки.</p>
    <div v-for="(zone,index) in shown" :key="zone.id" class="coverage-card" :class="{selected:state.selectedZoneId===zone.id}">
      <div class="coverage-card-head"><span class="coverage-number">{{ index+1 }}</span><div><b>{{ zone.name || `Зона ${index+1}` }}</b><small>{{ plural(zone.count,words) }} · {{ km(zone.area) }}</small></div><strong>{{ rub(zone.cost) }}</strong><button class="ghost-icon small" :aria-label="`Показать зону ${index+1} на карте`" @click="focus(zone.id)"><MapPin :size="17" /></button></div>
      <details @toggle="reveal(zone.id,$event.target.open)"><summary>Объекты и срок <span>{{ zone.hours == null ? '' : zone.hours+' ч' }}</span></summary><div v-if="openIds.has(zone.id)" class="coverage-detail"><p v-if="zone.note">{{ zone.note }}</p><ul><li v-for="item in zone.objects" :key="item.id">{{ item.name }} <small>{{ item.id }}</small></li></ul><p v-if="!zone.objects?.length">В зоне нет выбранных объектов; она уточняет карту воды.</p></div></details>
    </div>
    <button v-if="zones.length>3" class="link-row" @click="expanded=!expanded">{{ expanded?'Свернуть список':`Все зоны (${zones.length})` }}</button>
  </section>
</template>
