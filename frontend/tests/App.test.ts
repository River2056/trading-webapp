import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { expect, test, vi } from 'vitest'

import App from '../src/App.vue'

const jsonResponse = (body: object, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

test('operator signs up and starts then stops the paper-trading run', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch')
  fetchMock
    .mockImplementationOnce(() => jsonResponse({ detail: 'authentication required' }, 401))
    .mockImplementationOnce(() => jsonResponse({ status: 'created' }, 201))
    .mockImplementationOnce(() => jsonResponse({
      product: 'Paper Trading Only', desired_state: 'stopped',
      configured_capital_ntd: '5000.00', current_capital_ntd: '5000.00',
    }))
    .mockImplementationOnce(() => jsonResponse({
      starting_capital_ntd: '5000.00', round_duration_days: 7,
      strategy_cadence_seconds: 300, max_position_allocation_pct: '10.00',
      max_concurrent_positions: 3, stop_loss_pct: '5.00', take_profit_pct: '10.00',
      daily_loss_limit_pct: '3.00', fee_pct: '0.10', slippage_pct: '0.10',
    }))
    .mockImplementationOnce(() => jsonResponse({ starting_capital_ntd: '6000.00' }))
    .mockImplementationOnce(() => jsonResponse({ desired_state: 'running' }))
    .mockImplementationOnce(() => jsonResponse({ desired_state: 'stopped' }))

  render(App)
  await screen.findByRole('heading', { name: 'Local operator access' })
  await fireEvent.update(screen.getByLabelText('Password'), 'correct horse battery staple')
  await fireEvent.click(screen.getByRole('button', { name: 'Create local account' }))

  expect(await screen.findAllByText('NT$5,000.00')).toHaveLength(2)
  expect(screen.getByText('Paper Trading Only')).toBeTruthy()
  expect(screen.getByText('Stopped')).toBeTruthy()
  expect(screen.getByLabelText('Round duration (days)')).toBeTruthy()
  expect(screen.getByLabelText('Strategy cadence (seconds)')).toBeTruthy()
  expect(screen.getByLabelText('Maximum position allocation (%)')).toBeTruthy()
  expect(screen.getByLabelText('Maximum concurrent positions')).toBeTruthy()
  expect(screen.getByLabelText('Stop loss (%)')).toBeTruthy()
  expect(screen.getByLabelText('Take profit (%)')).toBeTruthy()
  expect(screen.getByLabelText('Daily loss limit (%)')).toBeTruthy()
  expect(screen.getByLabelText('Fee (%)')).toBeTruthy()
  expect(screen.getByLabelText('Slippage (%)')).toBeTruthy()

  await fireEvent.update(screen.getByLabelText('Starting capital (NTD)'), '6000')
  await fireEvent.click(screen.getByRole('button', { name: 'Save settings' }))
  await screen.findByText('Settings saved.')

  await fireEvent.click(screen.getByRole('button', { name: 'Start run' }))
  await screen.findByText('Running')
  await fireEvent.click(screen.getByRole('button', { name: 'Stop run' }))
  await waitFor(() => expect(screen.getByText('Stopped')).toBeTruthy())
})
