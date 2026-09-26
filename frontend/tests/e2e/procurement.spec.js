import { test, expect } from '@playwright/test'

async function openProcurement(page) {
  await page.goto('/')
  await page.getByRole('tab', { name: 'Съёмка', exact: true }).click()
  await page.getByRole('button', { name: 'Подобрать снимки', exact: true }).click()
  await expect(page.getByTestId('procurement')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Выбрать 3 приоритетных' })).toBeVisible()
}

async function settled(page, action) {
  const response = page.waitForResponse(r => r.url().endsWith('/api/procurement/plan') && r.request().method() === 'POST')
  await action()
  const r = await response
  expect(r.ok()).toBeTruthy()
  const plan = await r.json()
  await expect(page.getByTestId('basket')).toHaveAttribute('aria-busy', 'false')
  return plan
}

test('addressed purchase has a clear empty state, real price changes and ZIP export', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await openProcurement(page)
  await expect(page.getByText('Начните с объектов')).toBeVisible()
  await expect(page.getByTestId('basket')).toHaveCount(0)
  await expect(page.getByText(/\+0,00/)).toHaveCount(0)
  await page.screenshot({ path: test.info().outputPath('procurement-start.png') })
  let delayed = false
  await page.route('**/api/procurement/plan', async route => {
    if (!delayed && route.request().postDataJSON().mandatory?.length === 3) {
      delayed = true
      await new Promise(resolve => setTimeout(resolve, 600))
    }
    await route.continue()
  })
  const chosen = await settled(page, async () => {
    await page.getByRole('button', { name: 'Выбрать 3 приоритетных' }).click()
    await expect(page.getByText('Подбираем снимки…')).toBeVisible()
    await expect(page.getByTestId('basket')).toHaveCount(0)
  })
  expect(chosen.request.objective).toBe('cheapest')
  expect(chosen.request.mandatory).toHaveLength(3)
  expect(chosen.cost).toBeGreaterThan(0)
  await expect(page.getByText('Подходящий набор найден')).toBeVisible()
  await expect(page.getByText('Стоимость набора')).toBeVisible()
  const broke = await settled(page, () => page.getByLabel('Бюджет закупки', { exact: true }).fill('0'))
  expect(broke.mandatory_satisfied).toBe(false)
  await expect(page.getByText('Чтобы закрыть все выбранные объекты, нужно минимум', { exact: false })).toBeVisible()
  const free = await settled(page, () => page.getByLabel('Детализация закупки', { exact: true }).selectOption('10'))
  expect(free.selected_ids).toEqual(['free'])
  await expect(page.getByText('Подходящий набор найден')).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Скачать план закупки' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toMatch(/^procurement-.*\.zip$/)
  expect(await download.failure()).toBeNull()
  await download.saveAs(test.info().outputPath('request.zip'))
  expect(errors).toEqual([])
})

test('limits, mode and alternatives work without a long initial offer list', async ({ page }) => {
  await openProcurement(page)
  await expect(page.locator('.procurement-offer')).toHaveCount(0)
  await page.getByRole('button', { name: 'Все объекты (10)' }).click()
  const plan = await settled(page, () => page.getByRole('checkbox', { name: 'Обязательно: Склад', exact: true }).check())
  expect(plan.request.mandatory).toHaveLength(1)
  const impossible = await settled(page, () => page.getByLabel('Срок закупки', { exact: true }).fill('1'))
  expect(impossible.minimum_cost).toBeNull()
  await expect(page.getByText('При заданном сроке и детализации подходящей комбинации нет.')).toBeVisible()
  await settled(page, () => page.getByLabel('Срок закупки', { exact: true }).fill('12'))
  await page.getByRole('button', { name: 'Другие предложения', exact: false }).click()
  await expect(page.locator('.procurement-offer')).toHaveCount(5)
  await page.getByRole('button', { name: 'Показать все', exact: false }).click()
  await expect(page.getByTestId('offer-wide')).toBeVisible()
  const wide = page.getByTestId('offer-wide')
  await wide.getByText('Параметры и действия', { exact: true }).click()
  const variant = await settled(page, () => wide.getByRole('button', { name: 'Создать заказ на выбранные объекты' }).click())
  expect(variant.offers.some(o => o.id.startsWith('zone-'))).toBe(true)
  await page.getByText('Сравнить с другими планами').click()
  await expect(page.getByText('A · Без оплаты')).toBeVisible()
  await settled(page, () => page.getByRole('button', { name: 'Максимум охвата' }).click())
  await expect(page.getByRole('button', { name: 'Максимум охвата' })).toHaveClass(/active/)
  await page.getByRole('button', { name: 'Расчёт по ТЗ', exact: true }).click()
  await expect(page.getByRole('radiogroup', { name: 'Вариант закупки' })).toBeVisible()
})

test('compact and phone viewports keep the flow usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('https://tiles.openfreemap.org/styles/**', route => route.abort())
  await page.goto('/')
  await page.getByRole('button', { name: /^EL / }).click()
  await page.getByRole('tab', { name: 'Съёмка', exact: true }).click()
  await page.getByRole('button', { name: 'Подобрать снимки', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Выбрать 3 приоритетных' })).toBeVisible()
  await settled(page, () => page.getByRole('button', { name: 'Выбрать 3 приоритетных' }).click())
  await page.getByTestId('basket').scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('procurement-phone.png') })
  const width = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth }))
  expect(width.scroll).toBeLessThanOrEqual(width.viewport)
})

for (const [country, scene] of [['Гана', 'Ghana_313799'], ['Меконг · Камбоджа', 'Mekong_333434']]) {
  test(`auto catalogue and addressed plan in ${country}`, async ({ page }) => {
    await openProcurement(page)
    await page.getByRole('tab', { name: country, exact: true }).click()
    await expect(page.getByTestId('procurement')).toContainText(scene)
    await expect(page.getByRole('button', { name: 'Выбрать 3 приоритетных' })).toBeVisible()
    const plan = await settled(page, () => page.getByRole('button', { name: 'Выбрать 3 приоритетных' }).click())
    expect(plan.request.mandatory).toHaveLength(3)
    expect(plan.cost).toBeLessThanOrEqual(500)
    await page.getByTestId('basket').scrollIntoViewIfNeeded()
    await page.screenshot({ path: test.info().outputPath(scene + '.png') })
  })
}
