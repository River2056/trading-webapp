import { expect, test } from '@playwright/test'

const password = 'correct horse battery staple'

async function createAccount(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Create local account' }).click()
  await expect(page.getByText('Paper Trading Only')).toBeVisible()
}

async function login(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Log in' }).click()
  await expect(page.getByText('Paper Trading Only')).toBeVisible()
}

test('operator configures and controls a persisted paper-trading run', async ({ page }) => {
  await createAccount(page)
  await expect(page.getByText('Stopped')).toBeVisible()
  await page.getByLabel('Starting capital (NTD)').fill('6000')
  await page.getByLabel('Round duration (days)').fill('3')
  await page.getByLabel('Maximum position allocation (%)').fill('15')
  await page.getByRole('button', { name: 'Save settings' }).click()
  await expect(page.getByText('Settings saved.')).toBeVisible()

  await page.getByRole('button', { name: 'Start run' }).click()
  await expect(page.getByText('Running')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Active round plan' })).toBeVisible()
  await page.getByRole('button', { name: 'Stop run' }).click()
  await expect(page.getByText('Stopped')).toBeVisible()
  await expect(page.getByText('No active pair selections.')).toBeVisible()

  await page.reload()
  await expect(page.getByText('Stopped')).toBeVisible()
  await expect(page.getByText('NT$6,000.00').first()).toBeVisible()
})

test('persisted analytics support audit, filters, pagination, degradation and mobile', async ({ page, request }) => {
  await login(page)
  await page.getByRole('button', { name: 'Start run' }).click()
  await expect(page.getByText('Running')).toBeVisible()
  const seeded = await request.post('http://127.0.0.1:8000/__e2e__/seed-analytics')
  expect(seeded.ok()).toBeTruthy()
  await page.reload()

  await expect(page.getByText('Paused — market data degraded')).toBeVisible()
  await expect(page.getByText('NT$250.00')).toBeVisible()
  await expect(page.getByText('NT$200.00')).toBeVisible()
  await expect(page.getByText('NT$50.00')).toBeVisible()
  const negativeSeed = await request.post('http://127.0.0.1:8000/__e2e__/seed-negative-analytics')
  expect(negativeSeed.ok()).toBeTruthy()
  await page.reload()
  const totalProfit = page.locator('.profit').filter({ hasText: 'Total profit' })
  await expect(totalProfit.getByText('-NT$1,200.00')).toHaveClass(/negative/)
  const splitProfit = page.locator('.card').filter({ hasText: 'Realized / unrealized' })
  await expect(splitProfit.getByText('-NT$150.00')).toHaveClass(/negative/)
  await expect(splitProfit.getByText('-NT$50.00')).toHaveClass(/negative/)
  await expect(page.getByRole('img', { name: /equity chart with 2 persisted/ })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Next trades page' })).toBeEnabled()
  await page.getByRole('button', { name: 'Next trades page' }).click()
  await expect(page.getByText(/Page 2 of 2/)).toBeVisible()
  await page.getByLabel('Search trades').fill('fixture audit 0')
  await page.getByLabel('Side').selectOption('buy')
  await page.getByRole('button', { name: 'Filter trades' }).click()
  const trade = page.locator('.audit-list details').filter({ hasText: 'BTCUSDT' }).first()
  await expect(trade).toBeVisible()
  await trade.locator('summary').click()
  await expect(trade.getByText('fixture audit 0')).toBeVisible()
  await expect(page.getByText('market price ntd')).toBeVisible()
  await expect(page.getByText('Signal audit')).toBeVisible()

  await page.reload()
  await expect(page.getByText('-NT$1,200.00')).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByLabel('Round status')).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
  expect(overflow).toBeFalsy()
})
