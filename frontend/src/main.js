import { createApp } from 'vue'
import App from './App.vue'
import { vTip } from './lib/tip'
import './style.css'

createApp(App).directive('tip', vTip).mount('#app')
