import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, expect, test, vi } from 'vitest'

import App from '../src/App.vue'

const jsonResponse = (body: object, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

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
    .mockImplementationOnce(() => jsonResponse({ desired_state: 'running', operational_state: 'running' }))
    .mockImplementationOnce(() => jsonResponse({ desired_state: 'stopped', operational_state: 'stopped' }))

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

test('degraded operational state is visibly paused with an execution alert', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementationOnce(() => jsonResponse({
    product: 'Paper Trading Only', desired_state: 'running', operational_state: 'degraded',
    configured_capital_ntd: '5000.00', current_capital_ntd: '5000.00', engine_health: 'degraded',
    planning_failure: null, market_data_incident: { cause: 'stale ticker', retry_count: 2,
      next_retry_at: '2026-01-01T00:01:00Z', recovered_at: null, active: 1 },
  }))
  render(App)
  expect(await screen.findByText('Paused — market data degraded')).toBeTruthy()
  expect(screen.getByText('Execution paused')).toBeTruthy()
  expect(screen.queryByText('Running')).toBeNull()
})

test('healthy dashboard still renders the latest recovered market-data incident', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementationOnce(() => jsonResponse({
    product: 'Paper Trading Only', desired_state: 'running', operational_state: 'running',
    configured_capital_ntd: '5000.00', current_capital_ntd: '5000.00', engine_health: 'healthy',
    planning_failure: null, market_data_incident: { cause: 'stale ticker',
      occurred_at: '2026-01-01T00:00:00Z', retry_count: 3, next_retry_at: null,
      recovered_at: '2026-01-01T00:03:00Z', active: 0 },
  })).mockImplementationOnce(() => jsonResponse({}))
  render(App)

  expect(await screen.findByText('Latest recovered market-data incident')).toBeTruthy()
  expect(screen.getByText('stale ticker')).toBeTruthy()
  expect(screen.getByText(/occurred 2026-01-01T00:00:00Z/i)).toBeTruthy()
  expect(screen.getByText(/3 retries/i)).toBeTruthy()
  expect(screen.getByText(/recovered 2026-01-01T00:03:00Z/i)).toBeTruthy()
})

test('running dashboard always renders latest bankruptcy reset history', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementationOnce(() => jsonResponse({
    product: 'Paper Trading Only', desired_state: 'running', operational_state: 'running',
    run_status: 'running', round_status: 'active', completed_round_count: 4,
    configured_capital_ntd: '5000.00', current_capital_ntd: '5000.00', engine_health: 'healthy',
    cycle_count: 3, days_since_bankruptcy: 12,
    bankruptcy: { reason: 'minimum notional unavailable', declared_at: '2026-01-01T00:00:00Z',
      ending_equity_ntd: '4.50' }, planning_failure: null, market_data_incident: null,
  })).mockImplementationOnce(() => jsonResponse({}))
  render(App)

  expect(await screen.findByText('Latest bankruptcy and reset')).toBeTruthy()
  expect(screen.getByText('minimum notional unavailable')).toBeTruthy()
  expect(screen.getByText(/cycle 3/i)).toBeTruthy()
  expect(screen.getByText(/12 days since bankruptcy/i)).toBeTruthy()
})
