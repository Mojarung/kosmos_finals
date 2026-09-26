import { test, expect } from '@playwright/test'

// Подсказка одна на всё приложение и живёт в body. Читалка должна слышать её
// как описание элемента, пока она открыта, и не находить её закрытой со старым текстом.
test('the hint reaches a screen reader only while it is open', async ({ page }) => {
  await page.goto('/')
  const satellite = page.getByRole('button', { name: 'Спутник', exact: true })
  await satellite.hover()
  await expect(page.getByRole('tooltip')).toHaveText(/Спутниковая подложка/)
  await expect(satellite).toHaveAccessibleDescription(/Спутниковая подложка/)

  await page.keyboard.press('Escape')
  await expect(page.getByRole('tooltip')).toHaveCount(0)
  await expect(satellite).toHaveAccessibleDescription('')
})
