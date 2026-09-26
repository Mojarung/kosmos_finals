<script setup>
import { ref } from 'vue'
import { Upload, X } from '@lucide/vue'
import { uploadJob, uploadS1File } from '../store'

const emit = defineEmits(['close'])
const file = ref(null)
const observedAt = ref(new Date().toISOString().slice(0, 10))
const radiometry = ref('grd_db')
const error = ref('')

function selected(event) {
  file.value = event.target.files?.[0] || null
  error.value = ''
  uploadJob.error = ''
}
async function submit() {
  if (!file.value) { error.value = 'Выберите GeoTIFF.'; return }
  if (file.value.size > 100 * 1024 * 1024) { error.value = 'Максимальный размер — 100 МБ.'; return }
  error.value = ''
  try {
    await uploadS1File(file.value, observedAt.value, radiometry.value)
    emit('close')
  } catch (cause) {
    error.value = cause.message
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="upload-backdrop" @click.self="emit('close')">
      <form class="panel upload-dialog" role="dialog" aria-modal="true" aria-label="Загрузить Sentinel-1"
            @submit.prevent="submit" @keydown.esc="emit('close')">
        <div class="upload-head"><div><span class="eyebrow">СВОЙ СНИМОК</span><h2>Загрузить Sentinel‑1</h2></div>
          <button type="button" class="ghost-icon" aria-label="Закрыть" @click="emit('close')"><X :size="18" /></button>
        </div>
        <p>GeoTIFF с геопривязкой и двумя каналами: сначала VV, затем VH. Сервис построит карту воды, 10 учебных объектов и план A/B/C.</p>
        <label class="upload-field">Файл .tif/.tiff, до 100 МБ
          <input type="file" accept=".tif,.tiff,image/tiff" required @change="selected" />
        </label>
        <label class="upload-field">Дата съёмки
          <input v-model="observedAt" type="date" required :max="new Date().toISOString().slice(0, 10)" />
        </label>
        <label class="upload-field">Значения каналов
          <select v-model="radiometry">
            <option value="grd_db">GRD, децибелы (как Sen1Floods11)</option>
            <option value="rtc_linear">RTC, линейная шкала</option>
          </select>
        </label>
        <p class="source-line">Нужен обработанный двухканальный растр. Обычный PNG/JPEG и исходный архив SAFE не подходят. Для RTC применяется преобразование к шкале обучающих данных; точность на вашем снимке без ручной разметки неизвестна.</p>
        <p v-if="error || uploadJob.error" class="error-note" role="alert">{{ error || uploadJob.error }}</p>
        <p v-if="uploadJob.busy" role="status">{{ uploadJob.job?.message || 'Передаём файл…' }}</p>
        <button class="primary-button wide" type="submit" :disabled="uploadJob.busy">
          <Upload :size="17" />{{ uploadJob.busy ? 'Обрабатываем…' : 'Загрузить и рассчитать' }}
        </button>
      </form>
    </div>
  </Teleport>
</template>
