import { test, expect } from '@playwright/test'

test('operator imports an asset, calculates a separate plan and saves its snapshot', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const apiPort = process.env.E2E_API_PORT || '18031'
  const analysis = await (await page.request.get(`http://127.0.0.1:${apiPort}/api/analysis?scene_id=Nigeria_812045`)).json()
  const point = analysis.assets[0]
  await page.goto('/')
  await page.getByRole('tab', { name: 'Проекты', exact: true }).click()
  await page.getByPlaceholder('Название проекта').fill(`E2E ${Date.now()}`)
  await page.getByRole('button', { name: 'Создать проект' }).click()
  await expect(page.getByText('Импорт CSV или GeoJSON')).toBeVisible()
  const csv = `name,longitude,latitude,value_rub,vulnerability,value_source,source_date\n`
    + `Реальный склад,${point.longitude},${point.latitude},2000000,0.3,Реестр оператора,2026-09-01\n`
  await page.locator('.project-upload input[type=file]').setInputFiles({
    name: 'assets.csv', mimeType: 'text/csv', buffer: Buffer.from(csv),
  })
  await expect(page.getByTestId('operator-plan')).toBeVisible()
  await expect(page.getByText('Реальный склад')).toBeVisible()
  await page.getByRole('button', { name: 'Сравнить A / B / C для моего реестра' }).click()
  const plan = page.getByTestId('operator-plan')
  await expect(plan).toContainText('1 объект оценён')
  await expect(plan.locator('.project-plan-row')).toHaveCount(3)
  await expect(plan).toContainText('Контуры показаны на карте')
  await page.getByRole('button', { name: 'Сохранить проектный план' }).click()
  await expect(page.getByText(/Планы по своему реестру/)).toBeVisible()
  await expect(page.getByText(/Проектный план .* сохранён/)).toBeVisible()
  expect(errors).toEqual([])
})
