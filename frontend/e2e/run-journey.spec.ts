import { expect, test } from '@playwright/test'

test('operator configures and controls a persisted paper-trading run', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Local operator access' })).toBeVisible()

  await page.getByLabel('Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: 'Create local account' }).click()

  await expect(page.getByText('Paper Trading Only')).toBeVisible()
  await expect(page.getByText('Stopped')).toBeVisible()
  await page.getByLabel('Starting capital (NTD)').fill('6000')
  await page.getByLabel('Round duration (days)').fill('3')
  await page.getByLabel('Maximum position allocation (%)').fill('15')
  await page.getByRole('button', { name: 'Save settings' }).click()
  await expect(page.getByText('Settings saved.')).toBeVisible()
  await expect(page.getByText('NT$6,000.00')).toHaveCount(2)

  await page.getByRole('button', { name: 'Start run' }).click()
  await expect(page.getByText('Running')).toBeVisible()
  await page.getByRole('button', { name: 'Stop run' }).click()
  await expect(page.getByText('Stopped')).toBeVisible()

  await page.reload()
  await expect(page.getByText('Stopped')).toBeVisible()
  await expect(page.getByText('NT$6,000.00')).toHaveCount(2)
})
