import { test, expect } from '@playwright/test'

test('operator reads measured historical A/B/C check with limits and current chip', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await page.getByRole('tab', { name: 'Съёмка', exact: true }).click()
  const panel = page.getByTestId('backtest')
  await expect(panel).toContainText('70 чипов')
  await expect(panel).toContainText('60,1%')
  await expect(panel).toContainText('Текущий чип Nigeria_812045')
  await panel.getByText('По выборкам и как считали').click()
  await expect(panel).toContainText('55 чипов')
  await expect(panel).toContainText('15 чипов')
  await expect(panel).toContainText('не равна кадру 1 м')
  expect(errors).toEqual([])
})
