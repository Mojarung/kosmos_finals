import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60000,
  expect: { timeout: 15000 },
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:5178', viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure', screenshot: 'only-on-failure',
    launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] } },
  webServer: [
    { command: 'cd ../backend && FLOODVALUE_PROCUREMENT_DB=/tmp/floodvalue-procurement-e2e.sqlite3 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18031', url: 'http://127.0.0.1:18031/api/health', reuseExistingServer: false, timeout: 60000 },
    { command: 'VITE_API_TARGET=http://127.0.0.1:18031 npm run dev -- --host 127.0.0.1 --port 5178 --strictPort', url: 'http://127.0.0.1:5178', reuseExistingServer: false, timeout: 60000 },
  ],
})
