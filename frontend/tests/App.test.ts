import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, expect, test, vi } from 'vitest'

import App from '../src/App.vue'

const jsonResponse = (body: object, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

test('analytical dashboard shows signed profit, persisted charts, strategies and filterable histories', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockImplementationOnce(() => jsonResponse({
      product: 'Paper Trading Only', desired_state: 'running', operational_state: 'running',
      engine_health: 'healthy', initial_capital_ntd: '5000.00', configured_capital_ntd: '5000.00',
      current_capital_ntd: '5250.00', available_capital_ntd: '4700.00', realized_profit_ntd: '200.00',
      unrealized_profit_ntd: '50.00', total_profit_ntd: '250.00', total_profit_pct: '5.00',
      modeled_costs_ntd: '12.50', estimated_liquidation_equity_ntd: '5235.00',
      estimated_liquidation_profit_ntd: '235.00',
      profit_direction: 'positive', cycle_count: 2, completed_round_count: 3, days_since_bankruptcy: 9,
      round_status: 'active', planning_failure: null, market_data_incident: null,
      selected_pairs: [{ symbol: 'BTCUSDT', strategy_version: 'rsi-v1',
        strategy_config: { period: 14, entry_below: 30, exit_above: 70 } }],
      risk_settings: { max_position_allocation_pct: '10', stop_loss_pct: '5' },
    }))
    .mockImplementationOnce(() => jsonResponse({}))
    .mockImplementationOnce(() => jsonResponse({ equity: [{ at: '2026-01-01', value_ntd: '5250' }],
      profit: [{ at: '2026-01-01', value_ntd: '250' }], exposure: [], round_performance: [] }))
    .mockImplementationOnce(() => jsonResponse({ items: [{ id: 1, symbol: 'BTCUSDT', side: 'sell',
      realized_pnl_ntd: '200', executed_at: '2026-01-01', reason: 'take-profit' }], total: 1,
      page: 1, page_size: 10, pages: 1 }))
    .mockImplementationOnce(() => jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 }))
    .mockImplementationOnce(() => jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 }))
  render(App)
  expect(await screen.findByRole('navigation', { name: 'Primary navigation' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Overview' })).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Portfolio' })).toBeTruthy()
  expect(await screen.findByText('NT$250.00')).toBeTruthy()
  expect(screen.getByText('+5.00%')).toBeTruthy()
  expect(screen.getByText('Current-cycle profit')).toBeTruthy()
  expect(screen.queryByText('Total profit')).toBeNull()
  expect(screen.getByText('Estimated liquidation equity')).toBeTruthy()
  expect(screen.getByText('NT$5,235.00')).toBeTruthy()
  expect(screen.getByText('Estimated liquidation profit')).toBeTruthy()
  expect(screen.getByText('NT$235.00')).toBeTruthy()
  expect(screen.getByText('Modeled costs')).toBeTruthy()
  expect(screen.getByText('NT$12.50')).toBeTruthy()
  expect(screen.getByText('BTCUSDT')).toBeTruthy()
  expect(screen.getByText('rsi-v1')).toBeTruthy()
  expect(await screen.findByRole('img', { name: 'equity chart with 1 persisted observations' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Trade history' })).toBeTruthy()
  expect(await screen.findByText('take-profit')).toBeTruthy()
  expect(await screen.findByText('No completed rounds yet.')).toBeTruthy()
})

test('analytics resources fail independently and expose retry without blanking successful history', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = String(input)
    if (url === '/api/dashboard') return jsonResponse({ product: 'Paper Trading Only', desired_state: 'running',
      operational_state: 'running', engine_health: 'healthy', configured_capital_ntd: '5000',
      initial_capital_ntd: '5000', current_capital_ntd: '5000', planning_failure: null,
      market_data_incident: null })
    if (url === '/api/settings') return jsonResponse({})
    if (url === '/api/analytics/charts') return jsonResponse({ detail: 'failed' }, 500)
    if (url.startsWith('/api/history/trades')) return jsonResponse({ items: [{ id: 1, symbol: 'ETHUSDT',
      side: 'buy', realized_pnl_ntd: '0', executed_at: 'now', reason: 'entry', quantity: '1',
      market_price_ntd: '10', fill_price_ntd: '10', notional_ntd: '10', fee_ntd: '0.1',
      slippage_ntd: '0', strategy_version: 'v1', source_timestamp: 'source', signal: {
        action: 'buy', outcome: 'filled', market_evidence_json: '{"rsi":20}' } }],
      total: 1, page: 1, page_size: 10, pages: 1 })
    return jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 })
  })
  render(App)
  expect(await screen.findByText(/ETHUSDT/)).toBeTruthy()
  expect(await screen.findByRole('alert', { name: 'Charts error' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Retry charts' })).toBeTruthy()
})

test('history filters reset pages and all histories have operable pagination', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = String(input)
    if (url === '/api/dashboard') return jsonResponse({ product: 'Paper Trading Only', desired_state: 'running',
      operational_state: 'running', engine_health: 'healthy', configured_capital_ntd: '5000',
      initial_capital_ntd: '5000', current_capital_ntd: '5000', planning_failure: null,
      market_data_incident: null })
    if (url === '/api/settings') return jsonResponse({})
    if (url === '/api/analytics/charts') return jsonResponse({ equity: [], profit: [], exposure: [], round_performance: [] })
    return jsonResponse({ items: [], total: 20, page: url.includes('page=2') ? 2 : 1, page_size: 10, pages: 2 })
  })
  render(App)
  await screen.findByRole('button', { name: 'Next trades page' })
  await fireEvent.click(screen.getByRole('button', { name: 'Next trades page' }))
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/history/trades?') && String(url).includes('page=2'))).toBe(true)
  await fireEvent.update(screen.getByLabelText('Search trades'), '%')
  await fireEvent.click(screen.getByRole('button', { name: 'Filter trades' }))
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('q=%25') && String(url).includes('page=1'))).toBe(true)
  expect(screen.getByRole('button', { name: 'Next rounds page' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Next cycles page' })).toBeTruthy()
})

test('operator signs up and starts then stops the paper-trading run', async () => {
  let authenticated = false; let running = false
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, options) => {
    const url = String(input)
    if (url.includes('/api/auth/') && options?.method === 'POST') { authenticated = true; return jsonResponse({ status: 'created' }, 201) }
    if (!authenticated && url === '/api/dashboard') return jsonResponse({ detail: 'authentication required' }, 401)
    if (url === '/api/run/start') { running = true; return jsonResponse({ desired_state: 'running' }) }
    if (url === '/api/run/stop') { running = false; return jsonResponse({ desired_state: 'stopped' }) }
    if (url === '/api/dashboard') return jsonResponse({
      product: 'Paper Trading Only', desired_state: running ? 'running' : 'stopped',
      operational_state: running ? 'running' : 'stopped', engine_health: 'healthy',
      configured_capital_ntd: '5000.00', current_capital_ntd: '5000.00', planning_failure: null,
      market_data_incident: null,
    })
    if (url === '/api/settings' && options?.method === 'PUT') return jsonResponse({ starting_capital_ntd: '6000.00' })
    if (url === '/api/settings') return jsonResponse({
      starting_capital_ntd: '5000.00', round_duration_days: 7,
      strategy_cadence_seconds: 300, max_position_allocation_pct: '10.00',
      max_concurrent_positions: 3, stop_loss_pct: '5.00', take_profit_pct: '10.00',
      daily_loss_limit_pct: '3.00', fee_pct: '0.10', slippage_pct: '0.10',
    })
    if (url === '/api/analytics/charts') return jsonResponse({ equity: [], profit: [], exposure: [], round_performance: [] })
    return jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 })
  })

  render(App)
  await screen.findByRole('heading', { name: 'Local operator access' })
  await fireEvent.update(screen.getByLabelText('Password'), 'correct horse battery staple')
  await fireEvent.click(screen.getByRole('button', { name: 'Create local account' }))

  expect((await screen.findAllByText('NT$5,000.00')).length).toBeGreaterThanOrEqual(2)
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
  await waitFor(() => expect((document.querySelector('.action-lock') as HTMLFieldSetElement).disabled).toBe(false))
  await fireEvent.click(screen.getByRole('button', { name: 'Stop run' }))
  await waitFor(() => expect(screen.getByText('Stopped')).toBeTruthy())
})

test('refresh page control is placed beside the start or stop action', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockImplementationOnce(() => jsonResponse({
      product: 'Paper Trading Only', desired_state: 'stopped', operational_state: 'stopped',
      engine_health: 'healthy', configured_capital_ntd: '5000', current_capital_ntd: '5000',
      planning_failure: null, market_data_incident: null,
    }))
    .mockImplementationOnce(() => jsonResponse({ starting_capital_ntd: '5000' }))

  render(App)
  const startButton = await screen.findByRole('button', { name: 'Start run' })
  const refreshButton = screen.getByRole('button', { name: 'Refresh page' })

  expect(refreshButton.getAttribute('type')).toBe('button')
  expect(refreshButton.parentElement).toBe(startButton.parentElement)
})

test('fresh round is available while stopped and starts through its dedicated endpoint', async () => {
  let running = false
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input, options) => {
    const url = String(input)
    if (url === '/api/run/fresh-round' && options?.method === 'POST') {
      running = true
      return jsonResponse({ desired_state: 'running', round_id: 2 })
    }
    if (url === '/api/dashboard') return jsonResponse({
      product: 'Paper Trading Only', desired_state: running ? 'running' : 'stopped',
      operational_state: running ? 'running' : 'stopped', engine_health: 'healthy',
      configured_capital_ntd: '5000', current_capital_ntd: '5000', planning_failure: null,
      market_data_incident: null,
    })
    if (url === '/api/settings') return jsonResponse({ starting_capital_ntd: '5000' })
    return jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 })
  })

  render(App)
  await fireEvent.click(await screen.findByRole('button', { name: 'Fresh round' }))

  await screen.findByText('Running')
  expect(fetchMock.mock.calls.some(([url, options]) =>
    String(url) === '/api/run/fresh-round' && options?.method === 'POST')).toBe(true)
  expect(screen.queryByRole('button', { name: 'Fresh round' })).toBeNull()
})

test('portfolio follows run controls with profit first and report download last', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockImplementationOnce(() => jsonResponse({
      product: 'Paper Trading Only', desired_state: 'stopped', operational_state: 'stopped',
      engine_health: 'healthy', configured_capital_ntd: '5000', initial_capital_ntd: '5000',
      current_capital_ntd: '5250', available_capital_ntd: '4700', realized_profit_ntd: '200',
      unrealized_profit_ntd: '50', total_profit_ntd: '250', total_profit_pct: '5',
      planning_failure: null, market_data_incident: null,
    }))
    .mockImplementationOnce(() => jsonResponse({ starting_capital_ntd: '5000' }))
    .mockImplementationOnce(() => jsonResponse({ equity: [], profit: [], exposure: [], round_performance: [] }))
    .mockImplementationOnce(() => jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 }))
    .mockImplementationOnce(() => jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 }))
    .mockImplementationOnce(() => jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 }))

  render(App)
  await screen.findByRole('button', { name: 'Start run' })

  const layout = document.querySelector('.action-lock') as HTMLFieldSetElement
  const sections = Array.from(layout.children)
  expect(sections[0]?.classList.contains('hero')).toBe(true)
  expect(sections[1]?.getAttribute('id')).toBe('portfolio')
  expect(sections[sections.length - 1]?.classList.contains('report-download')).toBe(true)

  const portfolioLabels = Array.from(document.querySelectorAll('#portfolio article > p'))
    .map((element) => element.textContent)
  expect(portfolioLabels).toEqual([
    'Current-cycle profit', 'Realized / unrealized', 'Modeled costs',
    'Estimated liquidation equity', 'Estimated liquidation profit', 'Initial capital',
    'Current capital', 'Available capital', 'Current cycle', 'Current round', 'Bankruptcy',
  ])
})

test('start shows progress, blocks every other action, and refreshes live agent activity', async () => {
  let resolveStart!: (response: Response) => void
  let resolveStop!: (response: Response) => void
  let running = false
  const startResponse = new Promise<Response>((resolve) => { resolveStart = resolve })
  const stopResponse = new Promise<Response>((resolve) => { resolveStop = resolve })
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, options) => {
    const url = String(input)
    if (url === '/api/run/start' && options?.method === 'POST') return startResponse
    if (url === '/api/run/stop' && options?.method === 'POST') return stopResponse
    if (url === '/api/dashboard') return jsonResponse({
      product: 'Paper Trading Only', desired_state: running ? 'running' : 'stopped',
      operational_state: running ? 'running' : 'stopped', engine_health: 'healthy',
      configured_capital_ntd: '5000', current_capital_ntd: '5000', planning_failure: null,
      market_data_incident: null, agent_activity: running
        ? { status: 'monitoring', title: 'Monitoring active round',
          detail: 'Watching 5 selected markets for the next strategy evaluation.', updated_at: 'now' }
        : { status: 'idle', title: 'Agent stopped', detail: 'Start the run to begin trading.', updated_at: 'now' },
    })
    if (url === '/api/settings') return jsonResponse({ starting_capital_ntd: '5000' })
    return jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 })
  })

  render(App)
  expect(await screen.findByRole('heading', { name: 'Agent activity' })).toBeTruthy()
  expect(screen.getByText('Agent stopped')).toBeTruthy()
  await screen.findByRole('button', { name: 'Save settings' })

  await fireEvent.click(screen.getByRole('button', { name: 'Start run' }))
  expect((document.querySelector('.action-lock') as HTMLFieldSetElement).disabled).toBe(true)
  expect(screen.getByRole('button', { name: 'Starting agent…' })).toBeTruthy()
  expect(screen.getAllByText('Starting agent').length).toBeGreaterThanOrEqual(1)
  expect(screen.getByText('Ranking markets and preparing the first active round.')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Download Markdown run report' }).matches(':disabled')).toBe(true)
  expect((document.querySelector('.settings button') as HTMLButtonElement).matches(':disabled')).toBe(true)
  expect(screen.getByRole('status', { name: 'Run state change in progress' })).toBeTruthy()

  running = true
  resolveStart(await jsonResponse({ desired_state: 'running' }))
  expect(await screen.findByText('Monitoring active round')).toBeTruthy()
  expect((screen.getByRole('button', { name: 'Stop run' }) as HTMLButtonElement).disabled).toBe(false)

  await fireEvent.click(screen.getByRole('button', { name: 'Stop run' }))
  expect(screen.getByRole('button', { name: 'Stopping agent…' })).toBeTruthy()
  expect(screen.getAllByText('Stopping agent').length).toBeGreaterThanOrEqual(1)
  expect(screen.getByText('Persisting the stopped state and pausing new evaluations.')).toBeTruthy()
  running = false
  resolveStop(await jsonResponse({ desired_state: 'stopped' }))
  expect(await screen.findByText('Agent stopped')).toBeTruthy()
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

test('authenticated operator can download the complete Markdown run report with status feedback', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = String(input)
    if (url === '/api/dashboard') return jsonResponse({ product: 'Paper Trading Only', desired_state: 'stopped',
      operational_state: 'stopped', engine_health: 'healthy', configured_capital_ntd: '5000',
      initial_capital_ntd: '5000', current_capital_ntd: '5000', planning_failure: null,
      market_data_incident: null })
    if (url === '/api/settings') return jsonResponse({})
    if (url === '/api/reports/run.md') return Promise.resolve(new Response('# Paper Trading Run Report\n', {
      headers: { 'Content-Type': 'text/markdown; charset=utf-8' },
    }))
    if (url === '/api/analytics/charts') return jsonResponse({ equity: [], profit: [], exposure: [], round_performance: [] })
    return jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 })
  })
  const click = vi.fn()
  const createElement = document.createElement.bind(document)
  vi.spyOn(document, 'createElement').mockImplementation(((tagName: string) => {
    const element = createElement(tagName)
    if (tagName.toLowerCase() === 'a') element.click = click
    return element
  }) as typeof document.createElement)
  Object.defineProperty(window.URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:report') })
  Object.defineProperty(window.URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })

  render(App)
  const button = await screen.findByRole('button', { name: 'Download Markdown run report' })
  await fireEvent.click(button)
  expect((await screen.findByRole('status')).textContent).toBe('Markdown report downloaded.')

  expect(fetchMock).toHaveBeenCalledWith('/api/reports/run.md', expect.objectContaining({ credentials: 'same-origin' }))
  expect(window.URL.createObjectURL).toHaveBeenCalled()
  expect(click).toHaveBeenCalled()
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
