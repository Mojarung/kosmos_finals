import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'

const envFile = existsSync(new URL('../.env', import.meta.url)) ? ' --env-file ../.env' : ''

const apiPort = process.env.E2E_API_PORT || '18031'
const webPort = process.env.E2E_WEB_PORT || '5178'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 120000,
  expect: { timeout: 15000 },
  workers: 1,
  use: { baseURL: `http://127.0.0.1:${webPort}`, viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure', screenshot: 'only-on-failure',
    launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] } },
  webServer: [
    { command: `cd ../backend && FLOODVALUE_PROCUREMENT_DB=/tmp/floodvalue-imagery-e2e-${apiPort}.sqlite3 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port ${apiPort}` + envFile, url: `http://127.0.0.1:${apiPort}/api/health`, reuseExistingServer: false, timeout: 60000 },
    { command: `VITE_API_TARGET=http://127.0.0.1:${apiPort} npm run dev -- --host 127.0.0.1 --port ${webPort} --strictPort`, url: `http://127.0.0.1:${webPort}`, reuseExistingServer: false, timeout: 60000 },
  ],
})
