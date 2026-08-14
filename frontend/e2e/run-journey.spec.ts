import { expect, test } from '@playwright/test'

const password = 'correct horse battery staple'
const backend = 'http://127.0.0.1:8000'

test('complete autonomous paper-trading release journey from clean reset', async ({ page, request, context }) => {
  const reset = await request.post(`${backend}/__e2e__/reset`)
  expect(reset.ok()).toBeTruthy()

  await page.goto('/')
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Create local account' }).click()
  await expect(page.getByText('Paper Trading Only')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Stopped' })).toBeVisible()

  // Prove the account can log in, not only sign up.
  await context.clearCookies()
  await page.reload()
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Log in' }).click()
  await expect(page.getByText('Paper Trading Only')).toBeVisible()

  await page.getByLabel('Starting capital (NTD)').fill('6000')
  await page.getByLabel('Round duration (days)').fill('1')
  await page.getByLabel('Maximum position allocation (%)').fill('100')
  await page.getByLabel('Maximum concurrent positions').fill('1')
  await page.getByRole('button', { name: 'Save settings' }).click()
  await expect(page.getByText('Settings saved.')).toBeVisible()

  await page.getByRole('button', { name: 'Start run' }).click()
  await expect(page.getByText('Running')).toBeVisible()
  const selectedPairs = page.locator('.pair')
  await expect(selectedPairs).toHaveCount(5)
  await expect(selectedPairs.first()).toContainText(/USDT/)
  await expect(selectedPairs.first()).toContainText(/rsi-v1|macd-v1/)

  const filled = await request.post(`${backend}/__e2e__/worker-step?mode=entry`)
  expect(filled.ok()).toBeTruthy()
  expect((await filled.json()).paper_trades).toBeGreaterThan(0)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Trade history' })).toBeVisible()
  await expect(page.locator('.audit-list details').first()).toBeVisible()
  await expect(page.getByRole('img', { name: /equity chart with 1 persisted/ })).toBeVisible()

  await page.getByRole('button', { name: 'Stop run' }).click()
  await expect(page.getByRole('heading', { name: 'Stopped' })).toBeVisible()
  await expect(page.getByText('No active pair selections.')).toBeVisible()
  await page.getByRole('button', { name: 'Start run' }).click()
  await expect(page.getByText('Running')).toBeVisible()
  await expect(selectedPairs).toHaveCount(5)

  // A real worker step closes/liquidates the due round, proves bankruptcy at crash prices,
  // and automatically plans a fresh cycle with configured default capital.
  const rollover = await request.post(`${backend}/__e2e__/worker-step?mode=crash&advance_days=1`)
  expect(rollover.ok()).toBeTruthy()
  const rolloverState = await rollover.json()
  expect(rolloverState.outcome).toBe('rolled_over')
  expect(rolloverState.bankruptcies).toBe(1)
  expect(rolloverState.cycles).toBe(2)
  expect(rolloverState.trading_round).toBe(2)

  await page.reload()
  await expect(page.getByText('Latest bankruptcy and reset')).toBeVisible()
  await expect(page.getByText(/1 completed · 2 total cycles/)).toBeVisible()
  await expect(page.getByText('NT$6,000.00').nth(1)).toBeVisible()
  await expect(page.getByText(/Round 1 · completed/)).toBeVisible()
  await expect(page.getByText(/Cycle 1 · completed/)).toBeVisible()
  await expect(page.getByText(/Cycle 2 · active/)).toBeVisible()
  await expect(selectedPairs).toHaveCount(5)

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download Markdown run report' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('paper-trading-run-report.md')
  const content = await download.createReadStream().then(async (stream) => {
    const chunks: Buffer[] = []
    for await (const chunk of stream) chunks.push(Buffer.from(chunk))
    return Buffer.concat(chunks).toString('utf8')
  })
  expect(content).toContain('# Paper Trading Run Report')
  expect(content).toContain('PAPER TRADING ONLY')
  expect(content).toContain('### Bankruptcy 1')
  expect(content).toContain('bankruptcy reset')
  expect(content).toContain('completed')
  expect(content).toContain('### Trading cycle 2')
  await expect(page.getByRole('status')).toHaveText('Markdown report downloaded.')
})

test('database contention is visibly distinct and actionable', async ({ page, request }) => {
  await request.post(`${backend}/__e2e__/reset`)
  await page.goto('/')
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Create local account' }).click()
  await expect(page.getByText('Paper Trading Only')).toBeVisible()
  await page.getByRole('button', { name: 'Start run' }).click()
  await expect(page.getByText('Running')).toBeVisible()
  await request.post(`${backend}/__e2e__/seed-analytics`)
  await page.reload()
  await expect(page.getByText('Paused — database contention')).toBeVisible()
  await expect(page.getByText('Database access is locked; execution will retry automatically.')).toBeVisible()
  await expect(page.getByRole('alert').getByText('database is locked')).toBeVisible()
})
