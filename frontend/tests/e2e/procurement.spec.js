import { test, expect } from '@playwright/test'

async function openProcurement(page) {
  await page.goto('/')
  await page.getByRole('tab', { name: 'Съёмка', exact: true }).click()
  await page.getByRole('button', { name: 'Подобрать снимки', exact: true }).click()
  await expect(page.getByTestId('procurement')).toBeVisible()
  await page.getByRole('button', { name: 'Найти предложения', exact: true }).click()
  await expect(page.getByTestId('basket')).toHaveAttribute('aria-busy', 'false')
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

test('real UI → API → optimizer → immutable ZIP; exclusions and impossible requirements', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await openProcurement(page)
  await expect(page.getByText('Учебные предложения и цены. Не предложения поставщиков.', { exact: true })).toBeVisible()
  const before = await page.getByTestId('basket-price').innerText()
  const excluded = await settled(page, () => page.getByRole('checkbox', { name: 'Исключить Вся область · один заказ', exact: true }).check())
  expect(excluded.selected_ids).not.toContain('wide')
  expect(await page.getByTestId('basket-price').innerText()).not.toBe(before)
  await settled(page, () => page.getByRole('checkbox', { name: 'Обязательно: Склад', exact: true }).check())
  const broke = await settled(page, () => page.getByLabel('Бюджет закупки', { exact: true }).fill('0'))
  expect(broke.mandatory_satisfied).toBe(false)
  await expect(page.getByText('Все обязательные условия выполнить не удалось', { exact: true })).toBeVisible()
  const free = await settled(page, () => page.getByLabel('Детализация закупки', { exact: true }).selectOption('10'))
  expect(free.selected_ids).toEqual(['free'])
  await expect(page.getByText('Обязательные условия выполнены', { exact: true })).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Сохранить и скачать заявку', exact: true }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toMatch(/^procurement-.*\.zip$/)
  expect(await download.failure()).toBeNull()
  await download.saveAs(test.info().outputPath('request.zip'))
  await page.getByTestId('basket').scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('procurement-desktop.png') })
  expect(errors).toEqual([])
})

test('deadline failure, empty cheapest basket, and switching back to case calculation', async ({ page }) => {
  await openProcurement(page)
  await settled(page, () => page.getByRole('checkbox', { name: 'Обязательно: Склад', exact: true }).check())
  const impossible = await settled(page, () => page.getByLabel('Срок закупки', { exact: true }).fill('1'))
  expect(impossible.minimum_cost).toBeNull()
  await expect(page.getByText('В этом каталоге нет пригодной комбинации.', { exact: false })).toBeVisible()
  await settled(page, () => page.getByRole('checkbox', { name: 'Обязательно: Склад', exact: true }).uncheck())
  const empty = await settled(page, () => page.getByLabel('Цель закупки', { exact: true }).selectOption('cheapest'))
  expect(empty.cost).toBe(0)
  await page.getByRole('button', { name: 'Расчёт по ТЗ', exact: true }).click()
  await expect(page.getByRole('radiogroup', { name: 'Вариант закупки' })).toBeVisible()
})

test('compact viewport keeps procurement usable', async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 800 })
  await openProcurement(page)
  await expect(page.getByLabel('Бюджет закупки', { exact: true })).toBeVisible()
  const width = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth }))
  expect(width.scroll).toBeLessThanOrEqual(width.viewport)
})

test('new order geometry, marginal cost, preserved coverage and restored decision', async ({ page }) => {
  await openProcurement(page)
  await settled(page, () => page.getByRole('checkbox', { name: 'Обязательно: Склад', exact: true }).check())
  await settled(page, () => page.getByLabel('Цель закупки', { exact: true }).selectOption('cheapest'))
  const wide = page.getByTestId('offer-wide')
  await wide.getByText('Параметры и источник цены', { exact: true }).click()
  const variantPlan = await settled(page, () => wide.getByRole('button', { name: 'Создать заказ на обязательные объекты', exact: true }).click())
  expect(variantPlan.offers.some(o => o.id.startsWith('zone-'))).toBe(true)
  for (const id of variantPlan.covered_ids) expect(variantPlan.incremental_cost[id]).toBe(0)
  const same = await settled(page, () => page.getByRole('button', { name: 'Сохранить охват и найти дешевле', exact: true }).click())
  expect(same.covered_ids).toEqual(expect.arrayContaining(variantPlan.covered_ids))
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Сохранить и скачать заявку', exact: true }).click()
  await download
  await settled(page, () => page.getByLabel('Бюджет закупки', { exact: true }).fill('0'))
  const restored = await settled(page, () => page.locator('.procurement-history').first().click())
  expect(restored.cost).toBe(same.cost)
  expect(restored.selected_ids).toEqual(same.selected_ids)
})

test('phone viewport can open and use the procurement panel', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page.getByRole('button', { name: /^EL / }).click()
  await page.getByRole('tab', { name: 'Съёмка', exact: true }).click()
  await page.getByRole('button', { name: 'Подобрать снимки', exact: true }).click()
  await page.getByRole('button', { name: 'Найти предложения', exact: true }).click()
  await expect(page.getByTestId('basket')).toHaveAttribute('aria-busy', 'false')
  await page.getByTestId('basket').scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('procurement-phone.png') })
  const width = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth }))
  expect(width.scroll).toBeLessThanOrEqual(width.viewport)
})

for (const [country, scene] of [['Гана', 'Ghana_313799'], ['Меконг · Камбоджа', 'Mekong_333434']]) {
  test(`procurement constraints and map selection in ${country}`, async ({ page }) => {
    await openProcurement(page)
    await page.getByRole('tab', { name: country, exact: true }).click()
    await expect(page.getByTestId('procurement')).toContainText(scene)
    const search = page.waitForResponse(r => r.url().endsWith('/api/procurement/catalogs') && r.request().method() === 'POST')
    await page.getByRole('button', { name: 'Найти предложения', exact: true }).click()
    const c = await (await search).json()
    expect(c.scene_id).toBe(scene)
    await expect(page.getByTestId('basket')).toHaveAttribute('aria-busy','false')
    await settled(page, () => page.getByRole('checkbox', { name: 'Обязательно: Склад', exact: true }).check())
    const plan = await settled(page, () => page.getByLabel('Бюджет закупки', { exact: true }).fill('100'))
    expect(plan.cost).toBeLessThanOrEqual(100)
    expect(plan.mandatory_satisfied).toBe(true)
    await page.getByTestId('basket').scrollIntoViewIfNeeded()
    await page.screenshot({ path: test.info().outputPath(scene + '.png') })
  })
}
