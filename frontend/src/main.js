import { createApp } from 'vue'
import App from './App.vue'
import { vTip } from './lib/tip'
import { initTheme } from './lib/theme'
import './style.css'

// Тема до монтирования: карта сразу грузит нужный стиль, а не перекрашивается после первого кадра.
initTheme()
createApp(App).directive('tip', vTip).mount('#app')
