import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  optimizeDeps: { exclude: ['maplibre-gl'] },
  build: {
    // Больше 500 кБ здесь только сама maplibre-gl (1,04 МБ, gzip 280 кБ) и её воркер: сторонний код целиком,
    // дробить нечего. Код приложения и прочие библиотеки лежат отдельными чанками меньше 200 кБ.
    chunkSizeWarningLimit: 1100,
    rolldownOptions: {
      output: {
        // Библиотеки отдельно от кода приложения: браузер качает их параллельно и держит в кэше между релизами.
        codeSplitting: {
          groups: [
            { name: 'maplibre', test: /node_modules[\\/]maplibre-gl/, priority: 20 },
            { name: 'vendor', test: /node_modules/, priority: 10 },
          ],
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    proxy: { '/api': process.env.VITE_API_TARGET || 'http://127.0.0.1:8000' },
  },
})
