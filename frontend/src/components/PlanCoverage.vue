<script setup>
import { computed, ref, watch } from 'vue'
import { MapPin, ChevronDown } from '@lucide/vue'
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
    <div class="coverage-heading"><h3>Зоны съёмки <span>{{ zones.length }}</span></h3><span v-if="busy" role="status">Обновляем…</span></div>
    <p v-if="!busy && !zones.length" class="panel-note">Нет зон при текущих условиях.</p>
    <div v-for="(zone,index) in shown" :key="zone.id" class="coverage-card" :class="{selected:state.selectedZoneId===zone.id}">
      <div class="coverage-card-head">
        <button class="coverage-location" :aria-label="`Показать зону ${index+1} на карте`" @click="focus(zone.id)"><span class="coverage-number"><MapPin :size="15" /></span><span><b>Зона {{ index+1 }}</b><small>{{ plural(zone.count,words) }} · {{ km(zone.area) }}</small></span></button>
        <strong>{{ rub(zone.cost) }}</strong><button class="coverage-expand" :aria-label="`Состав зоны ${index+1}`" :aria-expanded="openIds.has(zone.id)" @click="reveal(zone.id,!openIds.has(zone.id))"><ChevronDown :size="16" :class="{up:openIds.has(zone.id)}" /></button>
      </div>
      <div v-if="openIds.has(zone.id)" class="coverage-detail"><p>{{ zone.name }}<span v-if="zone.hours != null"> · {{ zone.hours }} ч</span></p><ul><li v-for="item in zone.objects" :key="item.id">{{ item.name }} <small>{{ item.id }}</small></li></ul><p v-if="!zone.objects?.length">Уточнение карты воды</p></div>
    </div>
    <button v-if="zones.length>3" class="link-row" @click="expanded=!expanded">{{ expanded?'Свернуть':`Ещё ${zones.length-3}` }}</button>
  </section>
</template>
