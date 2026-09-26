import { test, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'

// A recorded, unmodified SkyFi response seeds the backend's normal SQLite catalog.
// All application calls below use the running API; no browser request is intercepted.
test.beforeAll(() => {
  const apiPort = process.env.E2E_API_PORT || '18031'
  execFileSync('.venv/bin/python', ['-c', `
import json
from pathlib import Path
from app import procurement
catalog = json.loads(Path('tests/fixtures/skyfi_Nigeria_812045.json').read_text())
procurement.persist('catalog', catalog)
`], { cwd: '../backend', env: { ...process.env, PYTHONPATH: '.', FLOODVALUE_PROCUREMENT_DB: `/tmp/floodvalue-imagery-e2e-${apiPort}.sqlite3` } })
})

test('operator imports assets, selects real catalog coverage, saves and exports a draft request', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const apiPort = process.env.E2E_API_PORT || '18031'
  const analysis = await (await page.request.get(`http://127.0.0.1:${apiPort}/api/analysis?scene_id=Nigeria_812045`)).json()
  const points = Object.fromEntries(analysis.assets.map(asset => [asset.id, asset]))
  await page.goto('/')
  await page.getByRole('tab', { name: 'Проекты', exact: true }).click()
  await page.getByPlaceholder('Название проекта').fill(`Заявка E2E ${Date.now()}`)
  await page.getByRole('button', { name: 'Создать проект' }).click()
  const csv = 'name,longitude,latitude,value_rub,vulnerability,value_source\n'
    + `Склад,${points.A10.longitude},${points.A10.latitude},2000000,0.3,Реестр\n`
    + `Подстанция,${points.A07.longitude},${points.A07.latitude},4000000,0.4,Реестр\n`
  await page.locator('.project-upload input[type=file]').setInputFiles({
    name: 'assets.csv', mimeType: 'text/csv', buffer: Buffer.from(csv),
  })
  await expect(page.getByTestId('project-imagery-entry')).toBeVisible()
  await page.getByRole('button', { name: /Подобрать снимки SkyFi/ }).click()
  await expect(page.getByTestId('project-imagery-context')).toContainText('2 объекта')
  await expect(page.getByTestId('recommendation')).toHaveAttribute('aria-busy', 'false')
  await expect(page.getByTestId('recommendation')).toContainText('100% EL')
  await expect(page.getByTestId('recommendation')).toContainText('$')
  await page.screenshot({ path: test.info().outputPath('project-imagery-catalog.png') })
  await page.getByLabel('Бюджет закупки').fill('0')
  await expect(page.getByTestId('recommendation')).toContainText('Варианта в заданных условиях нет')
  await page.getByLabel('Бюджет закупки').fill('500')
  await expect(page.getByTestId('recommendation')).toContainText('100% EL')
  const planResponse = await page.request.get(`http://127.0.0.1:${apiPort}/api/procurement/catalogs/latest?scene_id=Nigeria_812045`)
  expect(planResponse.ok()).toBeTruthy()
  expect((await planResponse.json()).source).toBe('skyfi')
  await page.getByTestId('recommendation').getByRole('button', { name: /Сравнить/ }).click()
  await expect(page.getByTestId('portfolio')).toContainText('2 из 2')
  await page.getByRole('button', { name: 'Сохранить черновик заявки' }).click()
  await expect(page.getByTestId('project-imagery-history')).toBeVisible()
  await expect(page.getByTestId('project-imagery-history')).toContainText('заказ не отправлен')
  await expect(page.getByTestId('project-imagery-history')).toContainText('100% EL')
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: 'Скачать черновик' }).click()
  const file = await download
  expect(file.suggestedFilename()).toMatch(/^project-imagery-.*\.zip$/)
  expect(await file.failure()).toBeNull()
  await page.screenshot({ path: test.info().outputPath('project-imagery-history.png') })
  expect(errors).toEqual([])
})
