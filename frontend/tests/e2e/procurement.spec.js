import { test, expect } from '@playwright/test'

// Real backend, real SkyFi and actual remote thumbnails; no substituted network responses.
async function open(page) {
  await page.goto('/')
  if (page.viewportSize().width < 1024) await page.getByRole('button', { name: /^EL / }).click()
  await page.getByRole('tab', { name:'Съёмка', exact:true }).click()
  await page.getByRole('button', { name:'Каталог SkyFi', exact:true }).click()
  await expect(page.getByTestId('imagery-gallery')).toBeVisible({ timeout:90000 })
  await expect(page.getByTestId('recommendation')).toHaveAttribute('aria-busy','false')
}
async function planAfter(page, action) {
  const response=page.waitForResponse(r=>r.url().endsWith('/api/procurement/plan') && r.request().method()==='POST')
  await action()
  const res=await response
  expect(res.ok()).toBeTruthy()
  const plan=await res.json()
  await expect(page.getByTestId('recommendation')).toHaveAttribute('aria-busy','false')
  return plan
}
async function loadedImages(page, minimum=1) {
  await expect.poll(()=>page.locator('.image-card img').evaluateAll(imgs=>imgs.filter(i=>i.complete && i.naturalWidth>0).length),{timeout:30000}).toBeGreaterThanOrEqual(minimum)
}

test('real previews, charts, geographic comparison and export',async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await open(page)
  const response=page.waitForResponse(r=>r.url().endsWith('/api/procurement/catalogs') && r.request().method()==='POST')
  await page.getByRole('button',{name:'Обновить из SkyFi',exact:true}).click()
  const res=await response;expect(res.ok()).toBeTruthy()
  const catalog=await res.json()
  expect(catalog.source).toBe('skyfi')
  expect(catalog.offers.length).toBeGreaterThan(0)
  expect(catalog.offers.every(o=>o.archive_id && o.price_source.includes('skyfi.com'))).toBeTruthy()
  await expect(page.getByTestId('recommendation')).toHaveAttribute('aria-busy','false')
  await loadedImages(page,3)
  await page.screenshot({path:test.info().outputPath('real-gallery-desktop.png')})
  await page.getByRole('button',{name:'Графики',exact:true}).click()
  await expect(page.getByRole('img',{name:'График цены снимков и покрытия территории'})).toBeVisible()
  await page.screenshot({path:test.info().outputPath('real-charts-desktop.png')})
  await page.getByRole('button',{name:/^Галерея /}).click()
  await page.getByRole('button',{name:'Увеличить превью'}).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const first=await planAfter(page,()=>page.locator('.compare-button').nth(0).click())
  const second=await planAfter(page,()=>page.locator('.compare-button').nth(1).click())
  expect(second.selected_ids).toHaveLength(2)
  expect(second.cost).toBeGreaterThanOrEqual(first.cost)
  expect(second.coverage_percent).toBeLessThanOrEqual(100)
  await page.getByRole('button',{name:/^Сравнение /}).click()
  await expect(page.getByTestId('portfolio')).toBeVisible()
  await page.getByTestId('comparison').scrollIntoViewIfNeeded()
  await page.screenshot({path:test.info().outputPath('real-comparison-desktop.png')})
  const download=page.waitForEvent('download')
  await page.getByRole('button',{name:'Скачать сравнение',exact:true}).click()
  const file=await download
  expect(file.suggestedFilename()).toMatch(/^imagery-.*\.zip$/)
  expect(await file.failure()).toBeNull()
  await file.saveAs(test.info().outputPath('real-comparison.zip'))
  await page.getByRole('button',{name:'На карте',exact:true}).click()
  await expect(page.getByRole('button',{name:'Открыть каталог',exact:true})).toBeVisible()
  expect(errors).toEqual([])
  console.log(JSON.stringify({scene:catalog.scene_id,realOffers:catalog.offers.length,cost:second.cost,coverage:second.coverage_percent}))
})

test('no zero dollar recommendation for unaffordable real offers',async({page})=>{
  await open(page)
  const r=await planAfter(page,()=>page.getByLabel('Бюджет закупки',{exact:true}).fill('0'))
  expect(r.recommendation).toBeNull();expect(r.cost).toBeNull()
  await expect(page.getByTestId('recommendation')).toContainText('Варианта в заданных условиях нет')
  expect(await page.locator('.image-card').count()).toBeGreaterThan(0)
  await page.getByRole('checkbox',{name:'Только в пределах лимита и фильтров'}).check()
  await expect(page.getByText('Фильтры скрыли все кадры.',{exact:false})).toBeVisible()
  await page.getByRole('button',{name:'Показать весь каталог'}).click()
  expect(await page.locator('.image-card').count()).toBeGreaterThan(0)
})

for(const [country,scene] of [['Гана','Ghana_313799'],['Меконг · Камбоджа','Mekong_333434']]) {
  test('real catalog in '+country,async({page})=>{
    await page.goto('/')
    await page.getByRole('tab',{name:country,exact:true}).click()
    await page.getByRole('tab',{name:'Съёмка',exact:true}).click()
    await page.getByRole('button',{name:'Каталог SkyFi',exact:true}).click()
    await expect(page.getByTestId('imagery-gallery')).toBeVisible({timeout:90000})
    await expect(page.getByTestId('procurement')).toContainText(scene)
    await expect(page.getByTestId('recommendation')).toHaveAttribute('aria-busy','false')
    await loadedImages(page)
    await page.screenshot({path:test.info().outputPath(scene+'-real.png')})
  })
}

test('real gallery on phone',async({page})=>{
  await page.setViewportSize({width:390,height:844})
  await open(page)
  await page.locator('.image-card').first().scrollIntoViewIfNeeded()
  await expect.poll(()=>page.locator('.image-card img').evaluateAll(imgs=>imgs.slice(0,2).filter(i=>i.complete && i.naturalWidth>0).length),{timeout:45000}).toBe(2)
  await page.screenshot({path:test.info().outputPath('real-gallery-phone.png')})
  const d=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))
  expect(d.scroll).toBeLessThanOrEqual(d.width)
})


test('real event period and restoration of the archive',async({page})=>{
  await open(page)
  const response=page.waitForResponse(r=>r.url().endsWith('/api/procurement/catalogs') && r.request().method()==='POST')
  await page.getByRole('button',{name:'Неделя паводка',exact:true}).click()
  const res=await response; expect(res.ok()).toBeTruthy()
  const catalog=await res.json()
  console.log(JSON.stringify({period:[catalog.from_date,catalog.to_date],realOffers:catalog.offers.length}))
  if(!catalog.offers.length) {
    await expect(page.getByText('В этом периоде снимков не найдено')).toBeVisible()
    await expect(page.getByTestId('recommendation')).toHaveCount(0)
    await expect(page.getByTestId('portfolio')).toHaveCount(0)
    await page.screenshot({path:test.info().outputPath('real-empty-event-period.png')})
    await page.getByRole('button',{name:'Показать весь архив',exact:true}).click()
    await expect(page.getByTestId('imagery-gallery')).toBeVisible({timeout:90000})
  }
})

test('real supplier price validation preserves the available catalog',async({page})=>{
  await open(page)
  const response=page.waitForResponse(r=>r.url().includes('/quotes/') && r.request().method()==='POST')
  await page.getByRole('button',{name:'Проверить цену в SkyFi',exact:false}).click()
  const res=await response
  console.log(JSON.stringify({quoteHttp:res.status()}))
  await expect(page.locator('.quote-message')).toBeVisible()
  await expect(page.getByTestId('imagery-gallery')).toBeVisible()
  if(res.ok()) await expect(page.locator('.quote-message')).toContainText('подтвердил')
  else await expect(page.locator('.quote-message')).toContainText('SkyFi')
  await page.screenshot({path:test.info().outputPath('real-quote-response.png')})
})
