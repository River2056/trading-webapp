<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

type Dashboard = {
  product: string
  desired_state: 'running' | 'stopped'
  configured_capital_ntd: string
  current_capital_ntd: string
  engine_health: 'healthy' | 'degraded'
  operational_state: 'running' | 'stopped' | 'degraded'
  run_status?: 'running' | 'stopped' | 'bankrupt'
  round_status?: 'planning' | 'active' | 'completed' | 'failed' | null
  completed_round_count?: number
  cycle_count?: number
  days_since_bankruptcy?: number | null
  current_cycle?: { id: number; status: string; started_at: string; starting_capital_ntd: string } | null
  current_round?: { id: number; status: string; started_at: string } | null
  current_cycle_starting_capital_ntd?: string
  initial_capital_ntd?: string
  available_capital_ntd?: string
  realized_profit_ntd?: string
  unrealized_profit_ntd?: string
  total_profit_ntd?: string
  total_profit_pct?: string
  profit_direction?: 'positive' | 'negative' | 'neutral'
  selected_pairs?: Array<{ symbol: string; strategy_version: string; strategy_config: Record<string, unknown> }>
  risk_settings?: Record<string, unknown> | null
  bankruptcy?: { reason: string; declared_at: string; ending_equity_ntd: string } | null
  planning_failure: { reason: string; occurred_at: string } | null
  market_data_incident: {
    incident_kind: 'market_data' | 'database_lock'
    cause: string
    occurred_at: string
    retry_count: number
    next_retry_at: string | null
    recovered_at: string | null
    active: number
  } | null
  agent_activity?: {
    status: 'idle' | 'starting' | 'planning' | 'monitoring' | 'waiting' | 'attention'
    title: string
    detail: string
    updated_at: string | null
  }
}

type RunSettings = {
  starting_capital_ntd: string
  round_duration_days: number
  strategy_cadence_seconds: number
  max_position_allocation_pct: string
  max_concurrent_positions: number
  stop_loss_pct: string
  take_profit_pct: string
  daily_loss_limit_pct: string
  fee_pct: string
  slippage_pct: string
  candle_interval: string
  backtest_lookback_candles: number
  minimum_liquidity_ntd: string
  minimum_net_return_pct: string
  minimum_entry_count: number
  minimum_trade_count: number
  max_conversion_age_seconds: number
  max_candle_age_seconds: number
}

const dashboard = ref<Dashboard | null>(null)
const settings = ref<RunSettings | null>(null)
const needsAuthentication = ref(false)
const password = ref('')
const error = ref('')
const settingsMessage = ref('')
const reportStatus = ref('')
const reportError = ref('')
const reportDownloading = ref(false)
const stateChanging = ref<'start' | 'stop' | null>(null)
const stateChangeError = ref('')
let dashboardPoll: number | undefined
let dashboardRevision = 0
let latestPollRequest = 0
type ChartPoint = { at?: string; value_ntd?: string; round_id?: number; cycle_id?: number; baseline_ntd?: string; return_pct?: string }
type Page<T> = { items: T[]; page: number; page_size: number; total: number; pages: number }
type Trade = { id: number; round_id: number; symbol: string; side: string; quantity: string; market_price_ntd: string; fill_price_ntd: string; notional_ntd: string; fee_ntd: string; slippage_ntd: string; strategy_version: string; realized_pnl_ntd: string; executed_at: string; source_timestamp: string; reason: string; signal: { action: string; outcome: string; market_evidence_json: string } }
type Round = { id: number; cycle_id: number; status: string; started_at: string; ended_at?: string; ending_equity_ntd?: string; frozen_settings: Record<string, unknown>; retrospective: Record<string, unknown> | null }
type Cycle = { id: number; status: string; started_at: string; ended_at?: string; starting_capital_ntd: string; ending_capital_ntd?: string; round_count: number; end_reason?: string; evidence_json?: string; retrospective: Record<string, unknown> | null }
type Charts = { equity: ChartPoint[]; profit: ChartPoint[]; exposure: ChartPoint[]; round_performance: ChartPoint[] }
const charts = ref<Charts | null>(null)
const trades = ref<Page<Trade> | null>(null)
const rounds = ref<Page<Round> | null>(null)
const cycles = ref<Page<Cycle> | null>(null)
const tradePage = ref(1); const roundPage = ref(1); const cyclePage = ref(1)
const historySearch = ref(''); const historySide = ref('')
const roundStatus = ref(''); const roundCycleId = ref(''); const cycleStatus = ref('')
const loading = ref({ charts: false, trades: false, rounds: false, cycles: false })
const resourceError = ref({ charts: '', trades: '', rounds: '', cycles: '' })
const safeJson = (value: unknown) => { if (typeof value !== 'string') return value; try { return JSON.parse(value) } catch { return value } }
const auditValue = (value: unknown) => typeof safeJson(value) === 'object' ? JSON.stringify(safeJson(value), null, 2) : String(safeJson(value) ?? '—')
const label = (value: string) => value.replace(/_/g, ' ')
const isDatabaseContention = () => dashboard.value?.market_data_incident?.incident_kind === 'database_lock'
const activityTitle = () => stateChanging.value === 'start'
  ? 'Starting agent'
  : stateChanging.value === 'stop'
    ? 'Stopping agent'
    : dashboard.value?.agent_activity?.title || 'Activity unavailable'
const activityDetail = () => stateChanging.value === 'start'
  ? 'Ranking markets and preparing the first active round.'
  : stateChanging.value === 'stop'
    ? 'Persisting the stopped state and pausing new evaluations.'
    : dashboard.value?.agent_activity?.detail || 'Waiting for the next agent update.'

const money = (value: string | undefined) => Number(value || 0).toLocaleString('en-US', { style: 'currency', currency: 'TWD' })
const signedPct = (value: string | undefined) => `${Number(value || 0) > 0 ? '+' : ''}${Number(value || 0).toFixed(2)}%`
const chartPoints = (values: ChartPoint[]) => {
  if (!values.length) return ''
  const numbers = values.map((value) => Number(value.value_ntd ?? value.return_pct ?? 0))
  const low = Math.min(...numbers); const high = Math.max(...numbers); const span = high - low || 1
  return numbers.map((value, index) => `${(index / Math.max(numbers.length - 1, 1)) * 100},${45 - ((value - low) / span) * 40}`).join(' ')
}

async function request<T>(url: string, options?: globalThis.RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!response.ok) throw new Error(response.status === 401 ? 'authentication required' : 'request failed')
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

async function loadResource<K extends 'charts' | 'trades' | 'rounds' | 'cycles'>(key: K) {
  loading.value[key] = true; resourceError.value[key] = ''
  try {
    if (key === 'charts') charts.value = await request<Charts>('/api/analytics/charts')
    if (key === 'trades') {
      const query = new URLSearchParams({ page_size: '10', page: String(tradePage.value) })
      if (historySearch.value) query.set('q', historySearch.value)
      if (historySide.value) query.set('side', historySide.value)
      trades.value = await request<Page<Trade>>(`/api/history/trades?${query}`)
    }
    if (key === 'rounds') {
      const query = new URLSearchParams({ page_size: '10', page: String(roundPage.value) })
      if (roundStatus.value) query.set('status', roundStatus.value)
      if (roundCycleId.value) query.set('cycle_id', roundCycleId.value)
      rounds.value = await request<Page<Round>>(`/api/history/rounds?${query}`)
    }
    if (key === 'cycles') {
      const query = new URLSearchParams({ page_size: '10', page: String(cyclePage.value) })
      if (cycleStatus.value) query.set('status', cycleStatus.value)
      cycles.value = await request<Page<Cycle>>(`/api/history/cycles?${query}`)
    }
  } catch { resourceError.value[key] = `${key.charAt(0).toUpperCase()}${key.slice(1)} could not be loaded.` }
  finally { loading.value[key] = false }
}
const loadAnalytics = () => Promise.allSettled([
  loadResource('charts'), loadResource('trades'), loadResource('rounds'), loadResource('cycles'),
])
const filterTrades = () => { tradePage.value = 1; return loadResource('trades') }
const filterRounds = () => { roundPage.value = 1; return loadResource('rounds') }
const filterCycles = () => { cyclePage.value = 1; return loadResource('cycles') }
const movePage = (key: 'trades' | 'rounds' | 'cycles', delta: number) => {
  if (key === 'trades') tradePage.value += delta
  if (key === 'rounds') roundPage.value += delta
  if (key === 'cycles') cyclePage.value += delta
  return loadResource(key)
}

async function loadDashboard() {
  try {
    dashboard.value = await request<Dashboard>('/api/dashboard')
    settings.value = await request<RunSettings>('/api/settings')
    needsAuthentication.value = false
    if (dashboard.value.initial_capital_ntd !== undefined) await loadAnalytics()
  } catch {
    needsAuthentication.value = true
  }
}

async function refreshDashboard() {
  if (!dashboard.value || stateChanging.value) return
  const revision = dashboardRevision
  const requestId = ++latestPollRequest
  try {
    const refreshed = await request<Dashboard>('/api/dashboard')
    if (revision === dashboardRevision && requestId === latestPollRequest && !stateChanging.value) {
      dashboard.value = refreshed
    }
  } catch { /* retain last state */ }
}

async function authenticate(path: 'signup' | 'login') {
  error.value = ''
  try {
    await request(`/api/auth/${path}`, { method: 'POST', body: JSON.stringify({ password: password.value }) })
    await loadDashboard()
  } catch {
    error.value = path === 'signup' ? 'Could not create local account.' : 'Invalid password.'
  }
}

async function changeState(state: 'start' | 'stop') {
  if (stateChanging.value) return
  dashboardRevision += 1
  stateChanging.value = state; stateChangeError.value = ''
  try {
    await request(`/api/run/${state}`, { method: 'POST' })
    dashboard.value = await request<Dashboard>('/api/dashboard')
    settings.value = await request<RunSettings>('/api/settings')
    if (dashboard.value.initial_capital_ntd !== undefined) await loadAnalytics()
  } catch {
    stateChangeError.value = `Could not ${state} the agent.`
    try { dashboard.value = await request<Dashboard>('/api/dashboard') } catch { /* retain last state */ }
  } finally {
    stateChanging.value = null
  }
}

async function saveSettings() {
  if (!settings.value) return
  await request('/api/settings', { method: 'PUT', body: JSON.stringify(settings.value) })
  settingsMessage.value = 'Settings saved.'
  await loadDashboard()
}

async function downloadReport() {
  reportDownloading.value = true; reportStatus.value = ''; reportError.value = ''
  try {
    const response = await fetch('/api/reports/run.md', { credentials: 'same-origin' })
    if (!response.ok) throw new Error('request failed')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url; link.download = 'paper-trading-run-report.md'; link.click()
    URL.revokeObjectURL(url)
    reportStatus.value = 'Markdown report downloaded.'
  } catch {
    reportError.value = 'Markdown report could not be downloaded.'
  } finally {
    reportDownloading.value = false
  }
}

onMounted(() => {
  void loadDashboard()
  dashboardPoll = window.setInterval(() => { void refreshDashboard() }, 2000)
})
onUnmounted(() => { if (dashboardPoll !== undefined) window.clearInterval(dashboardPoll) })
</script>

<template>
  <main>
    <section v-if="needsAuthentication" class="card access">
      <p class="eyebrow">Single-user local application</p>
      <h1>Local operator access</h1>
      <p>Create the only account, or log in if it already exists.</p>
      <label for="password">Password</label>
      <input id="password" v-model="password" type="password" minlength="12" autocomplete="current-password">
      <p v-if="error" class="error">{{ error }}</p>
      <div class="actions">
        <button @click="authenticate('signup')">Create local account</button>
        <button class="secondary" @click="authenticate('login')">Log in</button>
      </div>
    </section>

    <template v-else-if="dashboard">
      <div class="app-shell">
        <aside class="sidebar">
          <a class="brand" href="#overview" aria-label="Paper Pilot home">
            <span class="brand-mark" aria-hidden="true">P</span>
            <span>Paper Pilot</span>
          </a>
          <nav aria-label="Primary navigation">
            <a class="active" href="#overview"><span aria-hidden="true">⌁</span>Overview</a>
            <a href="#portfolio"><span aria-hidden="true">◫</span>Portfolio</a>
            <a href="#strategies"><span aria-hidden="true">⌘</span>Strategies</a>
            <a href="#history"><span aria-hidden="true">↗</span>History</a>
            <a v-if="dashboard.desired_state === 'stopped' && settings" href="#settings"><span aria-hidden="true">⚙</span>Settings</a>
            <span v-else class="nav-disabled" aria-disabled="true"><span aria-hidden="true">⚙</span>Settings</span>
          </nav>
          <div class="sidebar-note">
            <span class="safe-icon" aria-hidden="true">✓</span>
            <div><strong>Simulation mode</strong><small>No exchange orders</small></div>
          </div>
        </aside>
        <div class="app-content">
          <header class="topbar">
            <div><p class="eyebrow">Autonomous crypto experiment</p><h1 id="overview">Overview</h1></div>
            <div class="topbar-actions">
              <a class="search-pill" href="#history"><span aria-hidden="true">⌕</span> Search activity</a>
              <span class="badge">No real orders</span>
              <span class="operator-dot" aria-label="Local operator">LO</span>
            </div>
          </header>
      <p v-if="stateChanging" class="sr-only" role="status" aria-label="Run state change in progress">{{ stateChanging === 'start' ? 'Starting agent' : 'Stopping agent' }}</p>
      <fieldset :disabled="stateChanging !== null" class="action-lock" :aria-busy="stateChanging !== null">
        <section class="card hero">
          <div class="hero-copy"><p class="eyebrow">{{ dashboard.product }}</p><h2>Trade the market.<br><span>Risk nothing real.</span></h2><p>Monitor your autonomous strategy, portfolio, and every persisted decision from one command center.</p></div>
          <div class="run-control"><p>Persisted run state</p><h3>{{ dashboard.operational_state === 'degraded' ? (isDatabaseContention() ? 'Paused — database contention' : 'Paused — market data degraded') : dashboard.operational_state === 'running' ? 'Running' : 'Stopped' }}</h3>
          <button v-if="dashboard.desired_state === 'stopped'" @click="changeState('start')">
            <span v-if="stateChanging === 'start'" class="spinner" aria-hidden="true" />{{ stateChanging === 'start' ? 'Starting agent…' : 'Start run' }}
          </button>
          <button v-else class="stop" @click="changeState('stop')">
            <span v-if="stateChanging === 'stop'" class="spinner" aria-hidden="true" />{{ stateChanging === 'stop' ? 'Stopping agent…' : 'Stop run' }}
          </button>
          </div>
        </section>
        <p v-if="stateChangeError" class="card error" role="alert">{{ stateChangeError }}</p>
        <section class="card agent-activity" aria-live="polite" aria-labelledby="agent-activity-heading">
          <div class="activity-heading">
            <div><p class="eyebrow">Live status</p><h2 id="agent-activity-heading">Agent activity</h2></div>
            <span class="live-indicator"><span class="live-dot" />Auto-refresh</span>
          </div>
          <strong>{{ activityTitle() }}</strong>
          <p>{{ activityDetail() }}</p>
          <small v-if="dashboard.agent_activity?.updated_at">Last update {{ dashboard.agent_activity.updated_at }}</small>
        </section>
        <section class="card report-download" aria-labelledby="run-report-heading">
          <div>
            <h2 id="run-report-heading">Run report</h2>
            <p>Download the complete persisted paper-trading audit as Markdown.</p>
          </div>
          <button :disabled="reportDownloading" @click="downloadReport">
            {{ reportDownloading ? 'Preparing Markdown report…' : 'Download Markdown run report' }}
          </button>
          <p v-if="reportStatus" role="status" class="success">{{ reportStatus }}</p>
          <p v-if="reportError" role="alert" class="error">{{ reportError }}</p>
        </section>
        <section v-if="dashboard.engine_health === 'degraded'" class="card error" role="alert">
          <strong>Execution paused</strong>
          <p v-if="isDatabaseContention()">Database access is locked; execution will retry automatically.</p>
          <p>{{ dashboard.market_data_incident?.cause || dashboard.planning_failure?.reason }}</p>
          <p v-if="dashboard.market_data_incident">
            Retry {{ dashboard.market_data_incident.retry_count }} · next attempt
            {{ dashboard.market_data_incident.next_retry_at }}
          </p>
        </section>
        <section v-else-if="dashboard.market_data_incident?.recovered_at" class="card incident-history">
          <strong>Latest recovered {{ isDatabaseContention() ? 'database-contention' : 'market-data' }} incident</strong>
          <p>{{ dashboard.market_data_incident.cause }}</p>
          <p>
            Occurred {{ dashboard.market_data_incident.occurred_at }} ·
            {{ dashboard.market_data_incident.retry_count }} retries ·
            recovered {{ dashboard.market_data_incident.recovered_at }}
          </p>
        </section>
        <section v-if="dashboard.bankruptcy" class="card incident-history">
          <strong>Latest bankruptcy and reset</strong>
          <p>{{ dashboard.bankruptcy.reason }}</p>
          <p>Ending equity NT${{ dashboard.bankruptcy.ending_equity_ntd }} · declared {{ dashboard.bankruptcy.declared_at }}</p>
          <p>Cycle {{ dashboard.cycle_count }} · {{ dashboard.days_since_bankruptcy }} days since bankruptcy</p>
        </section>
        <section id="portfolio" class="metrics capital-grid">
          <article class="card"><p>Initial capital</p><strong>{{ money(dashboard.initial_capital_ntd || dashboard.configured_capital_ntd) }}</strong></article>
          <article class="card"><p>Current capital</p><strong>{{ money(dashboard.current_capital_ntd) }}</strong></article>
          <article class="card"><p>Available capital</p><strong>{{ money(dashboard.available_capital_ntd || dashboard.current_capital_ntd) }}</strong></article>
          <article class="card profit"><p>Total profit</p><strong :class="dashboard.profit_direction || 'neutral'">{{ money(dashboard.total_profit_ntd) }}</strong><small :class="dashboard.profit_direction || 'neutral'">{{ signedPct(dashboard.total_profit_pct) }}</small></article>
          <article class="card"><p>Realized / unrealized</p><strong :class="Number(dashboard.realized_profit_ntd) > 0 ? 'positive' : Number(dashboard.realized_profit_ntd) < 0 ? 'negative' : 'neutral'">{{ money(dashboard.realized_profit_ntd) }}</strong><small :class="Number(dashboard.unrealized_profit_ntd) > 0 ? 'positive' : Number(dashboard.unrealized_profit_ntd) < 0 ? 'negative' : 'neutral'">{{ money(dashboard.unrealized_profit_ntd) }}</small></article>
          <article class="card"><p>Current cycle</p><strong>{{ dashboard.current_cycle ? `#${dashboard.current_cycle.id}` : 'None' }}</strong><small>{{ money(dashboard.current_cycle_starting_capital_ntd) }} start · {{ dashboard.current_cycle?.started_at || 'not started' }}</small></article>
          <article class="card"><p>Current round</p><strong>{{ dashboard.current_round ? `#${dashboard.current_round.id} · ${dashboard.current_round.status}` : 'None' }}</strong><small>{{ dashboard.completed_round_count || 0 }} completed · {{ dashboard.cycle_count || 0 }} total cycles</small></article>
          <article class="card"><p>Bankruptcy</p><strong>{{ dashboard.days_since_bankruptcy == null ? 'Never' : `${dashboard.days_since_bankruptcy} days ago` }}</strong></article>
        </section>
        <section class="workspace">
          <article id="strategies" class="card strategies">
            <h2>Active round plan</h2>
            <p v-if="dashboard.desired_state === 'stopped' || !dashboard.selected_pairs?.length" class="empty">No active pair selections.</p>
            <div v-for="pair in dashboard.desired_state === 'running' ? dashboard.selected_pairs : []" :key="pair.symbol" class="pair">
              <strong>{{ pair.symbol }}</strong><span>{{ pair.strategy_version }}</span>
              <small>{{ Object.entries(pair.strategy_config).map(([key, value]) => `${key}: ${value}`).join(' · ') }}</small>
            </div>
            <details v-if="dashboard.desired_state === 'running' && dashboard.risk_settings"><summary>Frozen risk settings</summary><dl><template v-for="(value, key) in dashboard.risk_settings" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></dl></details>
          </article>
          <article class="card charts">
            <h2>Persisted performance</h2>
            <p v-if="loading.charts" role="status">Loading analytics…</p>
            <div v-else-if="resourceError.charts" role="alert" aria-label="Charts error" class="error">{{ resourceError.charts }} <button class="secondary" @click="loadResource('charts')">Retry charts</button></div>
            <template v-else-if="charts">
              <div v-for="chart in ['equity', 'profit', 'exposure', 'round_performance'] as const" :key="chart" class="chart" role="img" :aria-label="`${chart.replace('_', ' ')} chart with ${charts[chart].length} persisted observations`">
                <h3>{{ chart.replace('_', ' ') }}</h3>
                <svg v-if="charts[chart].length" viewBox="0 0 100 50" preserveAspectRatio="none" aria-hidden="true"><polyline :points="chartPoints(charts[chart])" /></svg>
                <p v-else class="empty">No persisted observations yet.</p>
              </div>
            </template>
          </article>
        </section>
        <section id="history" class="card histories">
          <h2>Trade history</h2>
          <form class="filters" @submit.prevent="filterTrades">
            <label>Search trades<input v-model="historySearch" type="search" placeholder="Pair, strategy, reason"></label>
            <label>Side<select v-model="historySide"><option value="">All</option><option value="buy">Buy</option><option value="sell">Sell</option></select></label>
            <button>Filter trades</button>
          </form>
          <p v-if="loading.trades" role="status">Loading trades…</p><div v-else-if="resourceError.trades" role="alert">{{ resourceError.trades }} <button @click="loadResource('trades')">Retry trades</button></div>
          <div v-else class="audit-list">
            <details v-for="trade in trades?.items" :key="trade.id">
              <summary><span>{{ trade.executed_at }}</span> · {{ trade.symbol }} · {{ trade.side }} · <span :class="Number(trade.realized_pnl_ntd) > 0 ? 'positive' : Number(trade.realized_pnl_ntd) < 0 ? 'negative' : 'neutral'">{{ money(trade.realized_pnl_ntd) }}</span></summary>
              <dl><template v-for="key in ['round_id','quantity','market_price_ntd','fill_price_ntd','notional_ntd','fee_ntd','slippage_ntd','strategy_version','source_timestamp','executed_at','reason'] as const" :key="key"><dt>{{ label(key) }}</dt><dd>{{ auditValue(trade[key]) }}</dd></template></dl>
              <h4>Signal audit</h4><dl><dt>action</dt><dd>{{ trade.signal?.action || '—' }}</dd><dt>outcome</dt><dd>{{ trade.signal?.outcome || '—' }}</dd><dt>market evidence</dt><dd><pre>{{ auditValue(trade.signal?.market_evidence_json) }}</pre></dd></dl>
            </details>
          </div>
          <p v-if="trades && !trades.total" class="empty">No trades match these filters.</p>
          <nav v-if="trades" class="pagination" aria-label="Trade history pagination"><button :disabled="trades.page <= 1" @click="movePage('trades', -1)">Previous trades page</button><span>Page {{ trades.page }} of {{ Math.max(trades.pages, 1) }} · {{ trades.total }} trades</span><button :disabled="trades.page >= trades.pages" @click="movePage('trades', 1)">Next trades page</button></nav>
          <h2>Round & cycle history</h2>
          <div class="history-grid">
            <div>
              <h3>Rounds</h3>
              <form class="filters stacked" @submit.prevent="filterRounds"><label>Round status<select v-model="roundStatus"><option value="">All</option><option value="planning">Planning</option><option value="active">Active</option><option value="completed">Completed</option><option value="failed">Failed</option></select></label><label>Round cycle ID<input v-model="roundCycleId" type="number" min="1"></label><button>Filter rounds</button></form>
              <p v-if="loading.rounds" role="status">Loading rounds…</p><div v-else-if="resourceError.rounds" role="alert">{{ resourceError.rounds }} <button @click="loadResource('rounds')">Retry rounds</button></div>
              <p v-if="rounds && !rounds.items.some((round) => round.status === 'completed')" class="empty">No completed rounds yet.</p>
              <details v-for="round in rounds?.items" :key="round.id"><summary>Round {{ round.id }} · {{ round.status }}</summary><dl><template v-for="key in ['cycle_id','started_at','ended_at','ending_equity_ntd'] as const" :key="key"><dt>{{ label(key) }}</dt><dd>{{ auditValue(round[key]) }}</dd></template><dt>frozen settings</dt><dd><pre>{{ auditValue(round.frozen_settings) }}</pre></dd><template v-for="(value, key) in round.retrospective || {}" :key="key"><dt>{{ label(String(key)) }}</dt><dd><pre>{{ auditValue(value) }}</pre></dd></template></dl></details>
              <nav v-if="rounds" class="pagination" aria-label="Round history pagination"><button :disabled="rounds.page <= 1" @click="movePage('rounds', -1)">Previous rounds page</button><span>Page {{ rounds.page }} of {{ Math.max(rounds.pages, 1) }}</span><button :disabled="rounds.page >= rounds.pages" @click="movePage('rounds', 1)">Next rounds page</button></nav>
            </div>
            <div>
              <h3>Cycles</h3>
              <form class="filters stacked" @submit.prevent="filterCycles"><label>Cycle status<select v-model="cycleStatus"><option value="">All</option><option value="active">Active</option><option value="completed">Completed</option></select></label><button>Filter cycles</button></form>
              <p v-if="loading.cycles" role="status">Loading cycles…</p><div v-else-if="resourceError.cycles" role="alert">{{ resourceError.cycles }} <button @click="loadResource('cycles')">Retry cycles</button></div>
              <p v-if="cycles && !cycles.total" class="empty">No cycles recorded.</p><details v-for="cycle in cycles?.items" :key="cycle.id"><summary>Cycle {{ cycle.id }} · {{ cycle.status }}</summary><dl><template v-for="key in ['starting_capital_ntd','ending_capital_ntd','started_at','ended_at','round_count','end_reason','evidence_json'] as const" :key="key"><dt>{{ label(key) }}</dt><dd><pre>{{ auditValue(cycle[key]) }}</pre></dd></template><template v-for="(value, key) in cycle.retrospective || {}" :key="key"><dt>{{ label(String(key)) }}</dt><dd><pre>{{ auditValue(value) }}</pre></dd></template></dl></details>
              <nav v-if="cycles" class="pagination" aria-label="Cycle history pagination"><button :disabled="cycles.page <= 1" @click="movePage('cycles', -1)">Previous cycles page</button><span>Page {{ cycles.page }} of {{ Math.max(cycles.pages, 1) }}</span><button :disabled="cycles.page >= cycles.pages" @click="movePage('cycles', 1)">Next cycles page</button></nav>
            </div>
          </div>
        </section>
        <section v-if="dashboard.desired_state === 'stopped' && settings" id="settings" class="card settings">
          <h2>Run settings</h2>
          <div class="settings-grid">
            <label>Starting capital (NTD)<input v-model="settings.starting_capital_ntd" type="number" min="0.01" step="0.01"></label>
            <label>Round duration (days)<input v-model.number="settings.round_duration_days" type="number" min="1" max="365"></label>
            <label>Strategy cadence (seconds)<input v-model.number="settings.strategy_cadence_seconds" type="number" min="10" max="86400"></label>
            <label>Maximum position allocation (%)<input v-model="settings.max_position_allocation_pct" type="number" min="0.01" max="100" step="0.01"></label>
            <label>Maximum concurrent positions<input v-model.number="settings.max_concurrent_positions" type="number" min="1" max="5"></label>
            <label>Stop loss (%)<input v-model="settings.stop_loss_pct" type="number" min="0.01" max="99.99" step="0.01"></label>
            <label>Take profit (%)<input v-model="settings.take_profit_pct" type="number" min="0.01" max="1000" step="0.01"></label>
            <label>Daily loss limit (%)<input v-model="settings.daily_loss_limit_pct" type="number" min="0.01" max="99.99" step="0.01"></label>
            <label>Fee (%)<input v-model="settings.fee_pct" type="number" min="0" max="9.99" step="0.01"></label>
            <label>Slippage (%)<input v-model="settings.slippage_pct" type="number" min="0" max="9.99" step="0.01"></label>
            <label>Candle interval<input v-model="settings.candle_interval" type="text"></label>
            <label>Backtest candles<input v-model.number="settings.backtest_lookback_candles" type="number" min="30" max="1000"></label>
            <label>Minimum liquidity (NTD)<input v-model="settings.minimum_liquidity_ntd" type="number" min="0"></label>
            <label>Minimum net return (%)<input v-model="settings.minimum_net_return_pct" type="number" step="0.01"></label>
            <label>Minimum entries<input v-model.number="settings.minimum_entry_count" type="number" min="1"></label>
            <label>Minimum fills/trades<input v-model.number="settings.minimum_trade_count" type="number" min="1"></label>
            <label>Maximum conversion age (seconds)<input v-model.number="settings.max_conversion_age_seconds" type="number" min="1"></label>
            <label>Maximum candle age (seconds)<input v-model.number="settings.max_candle_age_seconds" type="number" min="1"></label>
          </div>
          <button @click="saveSettings">Save settings</button>
          <span class="success">{{ settingsMessage }}</span>
        </section>
      </fieldset>
        </div>
      </div>
    </template>
  </main>
</template>

<style scoped>
:global(*) { box-sizing: border-box; }
:global(html) { scroll-behavior: smooth; }
:global(body) { margin: 0; color: #f7f7fb; background: #0b0b18; font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
:global(body::before) { position: fixed; inset: 0; z-index: -1; content: ''; background: radial-gradient(circle at 58% -10%, #b6a8ff 0, #5d5682 25%, #17182b 56%, #080916 86%); }
main { min-height: 100vh; padding: 40px; }
.app-shell { display: grid; grid-template-columns: 248px minmax(0, 1fr); width: min(1480px, 100%); min-height: calc(100vh - 80px); margin: 0 auto; border: 1px solid #29293a; border-radius: 34px; background: #080910; box-shadow: 0 40px 120px #0009; }
.sidebar { position: relative; display: flex; flex-direction: column; min-width: 0; padding: 30px 20px; border-right: 1px solid #282834; border-radius: 34px 0 0 34px; background: #06070d; }
.brand { display: flex; align-items: center; gap: 12px; margin: 0 8px 42px; color: white; font-size: 1.15rem; font-weight: 750; text-decoration: none; letter-spacing: -.02em; }
.brand-mark { display: grid; width: 36px; height: 36px; place-items: center; border-radius: 10px 4px 10px 4px; color: #080910; background: #b5a7ff; font-weight: 900; }
.sidebar nav { display: grid; gap: 8px; }
.sidebar nav a, .sidebar nav .nav-disabled { display: flex; align-items: center; gap: 12px; padding: 12px 14px; border: 1px solid transparent; border-radius: 12px; color: #9595a3; font-size: .88rem; text-decoration: none; transition: .2s ease; }
.sidebar nav a span, .sidebar nav .nav-disabled span { width: 22px; color: #c8c6d2; font-size: 1.1rem; text-align: center; }
.sidebar nav a:hover, .sidebar nav a.active { border-color: #30303b; color: #fff; background: linear-gradient(90deg, #24242d, #15151c); }
.sidebar nav .nav-disabled { opacity: .42; }
.sidebar-note { display: flex; align-items: center; gap: 10px; margin-top: auto; padding: 14px; border: 1px solid #33323f; border-radius: 14px; background: radial-gradient(circle at 85% 10%, #8275d744, transparent 48%), #13131b; }
.sidebar-note .safe-icon { display: grid; width: 32px; height: 32px; flex: 0 0 auto; place-items: center; border-radius: 10px; color: #080910; background: #b5a7ff; }
.sidebar-note strong { margin: 0; font-size: .78rem; }
.sidebar-note small { display: block; margin-top: 3px; color: #858592; font-size: .66rem; }
.app-content { min-width: 0; padding: 30px 32px 42px; }
.topbar, .topbar-actions, .hero, .actions { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.topbar { min-height: 62px; }
h1 { margin: 2px 0 0; font-size: clamp(1.8rem, 3vw, 2.4rem); letter-spacing: -.045em; }
h2 { margin: 4px 0 18px; font-size: 1.35rem; letter-spacing: -.035em; }
h3 { letter-spacing: -.02em; }
p { color: #aaaab7; line-height: 1.55; }
.eyebrow { margin: 0; color: #a99cf5; text-transform: uppercase; letter-spacing: .16em; font-size: .67rem; font-weight: 800; }
.search-pill { min-width: 220px; padding: 11px 16px; border: 1px solid #353541; border-radius: 14px; color: #83838f; font-size: .78rem; text-decoration: none; }
.search-pill span { float: right; color: #dddce6; font-size: 1.2rem; }
.operator-dot { display: grid; width: 42px; height: 42px; place-items: center; border: 1px solid #b6a8ff; border-radius: 50%; color: #d9d2ff; background: #292537; font-size: .68rem; font-weight: 800; }
.badge { padding: 8px 12px; border: 1px solid #474450; border-radius: 99px; color: #bcb8c9; font-size: .7rem; text-transform: uppercase; letter-spacing: .08em; }
.action-lock { min-width: 0; margin: 0; padding: 0; border: 0; }
.action-lock[disabled] { cursor: wait; }
.action-lock[disabled] > *:not(.hero) { opacity: .72; }
.card { min-width: 0; border: 1px solid #30303b; border-radius: 24px; padding: 24px; background: #111119; box-shadow: 0 16px 50px #0003; }
.hero { position: relative; min-height: 250px; margin-top: 28px; overflow: hidden; padding: 38px 42px; background: radial-gradient(circle at 78% 35%, #4c3e7855 0 2%, transparent 2.5%), radial-gradient(circle at 82% 40%, transparent 0 13%, #77718424 13.3% 13.7%, transparent 14%), radial-gradient(circle at 82% 40%, transparent 0 25%, #7771841b 25.3% 25.7%, transparent 26%), linear-gradient(110deg, #181820 0%, #111119 58%, #191622 100%); }
.hero::after { position: absolute; right: -60px; bottom: -150px; width: 470px; height: 330px; border: 1px solid #948da733; border-radius: 50%; content: ''; transform: rotate(-12deg); }
.hero-copy { position: relative; z-index: 1; max-width: 580px; }
.hero-copy h2 { margin: 12px 0; font-size: clamp(2rem, 4.2vw, 3.7rem); line-height: .98; }
.hero-copy h2 span { color: #b5a7ff; }
.hero-copy > p:last-child { max-width: 520px; margin-bottom: 0; }
.run-control { position: relative; z-index: 1; min-width: 210px; padding: 22px; border: 1px solid #3b3948; border-radius: 18px; background: #0a0a10cc; backdrop-filter: blur(10px); }
.run-control p { margin: 0; font-size: .75rem; }
.run-control h3 { margin: 5px 0 18px; font-size: 1.25rem; }
.run-control button { width: 100%; }
.agent-activity { margin-top: 18px; background: linear-gradient(120deg, #13131b, #111119 65%, #1a1722); }
.activity-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.activity-heading h2, .activity-heading p, .agent-activity > p { margin-bottom: 0; }
.agent-activity > strong { font-size: 1.35rem; }
.live-indicator { display: inline-flex; align-items: center; gap: 8px; color: #58e0ae; font-size: .67rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
.live-dot { width: 8px; height: 8px; border-radius: 50%; background: #58e0ae; box-shadow: 0 0 0 5px #58e0ae18; }
.spinner { display: inline-block; width: 1em; height: 1em; margin-right: 8px; vertical-align: -.15em; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: spin .7s linear infinite; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
.report-download { display: flex; flex-wrap: wrap; align-items: center; gap: 20px; margin-top: 18px; }
.report-download > div { flex: 1; }.report-download h2, .report-download p { margin: 0; }
.report-download .success, .report-download .error { flex-basis: 100%; }
.metrics, .settings, .workspace, .histories { margin-top: 18px; }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.metrics article { position: relative; min-height: 155px; overflow: hidden; padding: 22px; background: radial-gradient(circle at 92% 8%, #39e7ad18, transparent 34%), #111119; }
.metrics article::after { position: absolute; right: -12%; bottom: -48%; width: 88%; height: 78%; border: 1px solid #48e8b855; border-radius: 50%; content: ''; transform: rotate(-9deg); }
.metrics article:nth-child(2n)::after { border-color: #ff536e55; }
.metrics article:nth-child(3n)::after { border-color: #ff9b4255; }
.metrics article p { margin: 0; color: #bcbcc5; font-size: .78rem; }
.metrics article strong { position: relative; z-index: 1; margin-top: 20px; font-size: clamp(1.15rem, 2.1vw, 1.7rem); }
.metrics article small { position: relative; z-index: 1; display: block; margin-top: 8px; }
.workspace, .history-grid { display: grid; grid-template-columns: minmax(250px, .85fr) minmax(0, 1.5fr); gap: 18px; }
.pair { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; padding: 13px 0; border-bottom: 1px solid #292934; }
.pair strong { margin: 0; font-size: .92rem; }.pair span { color: #b5a7ff; font-size: .8rem; }.pair small { grid-column: 1 / -1; color: #82828f; }
details { margin-top: 12px; } summary { cursor: pointer; font-weight: 700; } dl { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; font-size: .8rem; } dd { margin-left: 12px; color: #bbb9c5; text-align: right; }
.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }.charts h2 { grid-column: 1/-1; }
.chart { border: 1px solid #2c2c37; border-radius: 15px; padding: 14px; min-height: 125px; background: #0d0d14; }.chart h3 { margin: 0; color: #b7b7c2; font-size: .8rem; text-transform: capitalize; }.chart svg { width: 100%; height: 72px; overflow: visible; }.chart polyline { fill: none; stroke: #59e4b2; stroke-width: 2; filter: drop-shadow(0 0 5px #59e4b277); vector-effect: non-scaling-stroke; }
.positive { color: #58e0ae; }.negative, .error { color: #ff667e; }.neutral { color: #d7d5df; }.profit small { display: block; font-weight: 800; }
.filters { display: flex; gap: 12px; align-items: end; }.filters label { flex: 1; color: #b8b7c2; font-size: .78rem; }.filters input, .filters select { display: block; width: 100%; margin-top: 7px; padding: 11px; color: white; border: 1px solid #393945; border-radius: 10px; outline: none; background: #090910; }
.filters input:focus, .filters select:focus, .settings input:focus, .access input:focus { border-color: #9f91f1; box-shadow: 0 0 0 3px #9f91f11a; }
.audit-list details { padding: 14px 4px; border-bottom: 1px solid #292934; }.audit-list pre, dd pre { max-width: 100%; margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }.pagination { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 14px; }.pagination button:disabled { cursor: not-allowed; opacity: .4; }.empty, .pagination { color: #848491; }
.settings-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 18px; }
.settings label, .settings input { display: block; width: 100%; }
.settings label { color: #b8b7c2; font-size: .78rem; }
.settings input, .access input { margin-top: 8px; border: 1px solid #393945; border-radius: 10px; padding: 12px; color: #fff; outline: none; background: #090910; }
.success { margin-left: 14px; color: #58e0ae; }
strong { display: block; margin-top: 8px; font-size: 1.6rem; }
button { border: 0; border-radius: 11px; padding: 12px 18px; color: #090811; background: #b5a7ff; font: inherit; font-size: .78rem; font-weight: 850; cursor: pointer; transition: transform .15s ease, filter .15s ease; }
button:hover { filter: brightness(1.08); transform: translateY(-1px); }
button:disabled { cursor: wait; opacity: .65; transform: none; }
button.secondary { color: #eceaf4; background: #292934; }
button.stop { color: #fff; background: #d84d66; }
.access { max-width: 520px; margin: 6vh auto; }
.access label, .access input { display: block; width: 100%; }
.access label { margin: 28px 0 8px; }
.access .actions { justify-content: flex-start; margin-top: 18px; }
.incident-history { margin-top: 18px; }
.error { color: #ff7a90; }
@media (max-width: 1120px) { main { padding: 20px; } .app-shell { grid-template-columns: 82px minmax(0, 1fr); min-height: calc(100vh - 40px); } .sidebar { padding-inline: 14px; } .brand { justify-content: center; margin-inline: 0; } .brand > span:last-child, .sidebar nav a, .sidebar nav .nav-disabled { font-size: 0; } .sidebar nav a, .sidebar nav .nav-disabled { justify-content: center; padding-inline: 8px; } .sidebar nav a span, .sidebar nav .nav-disabled span { font-size: 1.15rem; } .sidebar-note { justify-content: center; padding: 10px; } .sidebar-note div { display: none; } .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } .settings-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 760px) { main { padding: 0; } .app-shell { display: block; min-height: 100vh; border: 0; border-radius: 0; } .sidebar { position: sticky; top: 0; z-index: 10; display: flex; flex-direction: row; align-items: center; padding: 10px 14px; border-right: 0; border-bottom: 1px solid #292934; border-radius: 0; } .brand { margin: 0 12px 0 0; } .brand-mark { width: 32px; height: 32px; } .sidebar nav { display: flex; flex: 1; justify-content: space-around; } .sidebar nav a { padding: 8px; } .sidebar nav a span { width: auto; } .sidebar-note { display: none; } .app-content { padding: 22px 16px 36px; } .topbar, .hero, .report-download, .filters, .pagination { align-items: stretch; flex-direction: column; } .topbar-actions { justify-content: flex-start; flex-wrap: wrap; } .search-pill { display: none; } .hero { min-height: 0; padding: 28px 22px; } .hero-copy h2 { font-size: 2.35rem; } .run-control { width: 100%; } .metrics, .workspace, .history-grid, .charts, .settings-grid { grid-template-columns: minmax(0, 1fr); } .charts h2 { grid-column: 1; } .card { min-width: 0; padding: 20px; border-radius: 18px; } strong { overflow-wrap: anywhere; } .activity-heading { align-items: flex-start; flex-direction: column; } }
</style>
