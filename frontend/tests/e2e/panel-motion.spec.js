import { test, expect } from '@playwright/test'

async function openPlan(page, width = 1440) {
  await page.setViewportSize({ width, height: 900 })
  await page.goto('/')
  if (width < 1024) await page.getByRole('button', { name: /^EL / }).click()
  await page.getByRole('tab', { name: 'Съёмка', exact: true }).click()
  await expect(page.getByLabel('Бюджет на снимки')).toBeVisible()
}

for (const reduced of [false, true]) {
  test(`panel and disclosures survive repeated clicks${reduced ? ' with reduced motion' : ''}`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: reduced ? 'reduce' : 'no-preference' })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await openPlan(page)
    const panel = page.locator('.decision-panel')
    await expect(panel.getByRole('tablist', { name: 'Раздел решения' }).getByRole('tab')).toHaveText(['Объекты', 'Съёмка', 'Проекты'])
    const before = await panel.boundingBox()
    // Dispatch back-to-back input without Playwright waiting for animation stability.
    const resize = panel.locator('.panel-resize')
    await resize.dispatchEvent('click')
    await expect.poll(async () => (await panel.boundingBox()).width).toBeGreaterThan(before.width + 250)
    for (let i = 0; i < 3; i++) await resize.dispatchEvent('click')
    await expect.poll(async () => Math.abs((await panel.boundingBox()).width - before.width)).toBeLessThan(1)
    await expect(panel).not.toHaveClass(/decision-expanded/)
    const disclosure = page.getByTestId('backtest')
    const summary = disclosure.locator(':scope > summary')
    await summary.press('Enter')
    await expect(page.getByTestId('backtest-chart')).toBeVisible()
    await summary.dispatchEvent('click')
    await summary.dispatchEvent('click')
    await expect.poll(() => disclosure.evaluate(el => el.getAnimations().length)).toBe(0)
    await expect(disclosure).toHaveAttribute('open', '')
    await summary.press('Space')
    await expect(disclosure).not.toHaveAttribute('open', '')
    await expect(page.getByTestId('backtest-chart')).toBeHidden()
    await expect(disclosure).not.toHaveAttribute('style', /height/)
    for (const name of ['Проекты', 'Объекты', 'Съёмка', 'Объекты']) {
      await panel.getByRole('tab', { name, exact: true }).dispatchEvent('click')
    }
    await expect(panel.locator('.asset-rows')).toBeVisible()
    const asset = panel.locator('.asset-row').first()
    await asset.click()
    await expect(panel.locator('.asset-detail')).toBeVisible()
    await asset.click()
    await expect(panel.locator('.asset-detail')).toHaveCount(0)
    await expect.poll(() => panel.locator('.panel-scroll > div').first().evaluate(el => getComputedStyle(el).opacity)).toBe('1')
    expect(errors).toEqual([])
  })
}

for (const width of [375, 1440]) {
  test(`zone selection makes the map visible and retains selection at ${width}px`, async ({ page }) => {
    await openPlan(page, width)
    await page.getByRole('button', { name: 'По ценности информации' }).click()
    await expect(page.getByTestId('voi-chart')).toBeVisible()
    const recalculation = page.waitForResponse(response => response.url().includes('/api/voi?') && response.url().includes('visit_cost=5000'))
    await page.getByLabel('Цена выезда').fill('5000')
    await recalculation
    await expect(page.getByTestId('voi-panel').locator('[inert]')).toHaveCount(0)
    await page.getByRole('button', { name: 'Развернуть панель', exact: true }).click()
    await page.getByText(/^Выбранные зоны ·/).click()
    await page.locator('.voi-zone').first().click()
    const panel = page.locator('.decision-panel')
    await expect(panel).not.toHaveClass(/decision-expanded/)
    if (width < 1024) {
      await expect(panel).toHaveClass(/pill/)
      await page.getByRole('button', { name: /^EL / }).click()
      await expect(page.getByTestId('voi-chart')).toBeVisible()
    }
    await expect(page.getByLabel('Цена выезда')).toHaveValue('5000')
    await expect.poll(() => panel.evaluate(el => el.style.width)).toBe('')
    await page.locator('.voi-zone').first().scrollIntoViewIfNeeded()
    await expect(page.locator('.voi-zone').first()).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('.voi-zone[aria-pressed="true"]')).toHaveCount(1)
    await page.screenshot({ path: test.info().outputPath(`selected-zone-${width}.png`) })
  })
}

// Failure injection for the browser capability only; analysis and exports use real APIs.
test('unavailable WebGL does not trap the operator behind the loading screen', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext
    HTMLCanvasElement.prototype.getContext = function (type, ...args) {
      return /webgl/.test(type) ? null : original.call(this, type, ...args)
    }
  })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await openPlan(page)
  await expect(page.getByText('Карта недоступна: браузер не запустил WebGL. Расчёты и выгрузка работают.')).toBeVisible()
  await expect(page.getByRole('status', { name: 'Загрузка', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Скачать решение', exact: true })).toBeEnabled()
  expect(errors).toEqual([])
})
