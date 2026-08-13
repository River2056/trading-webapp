<script setup lang="ts">
import { onMounted, ref } from 'vue'

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
    cause: string
    occurred_at: string
    retry_count: number
    next_retry_at: string | null
    recovered_at: string | null
    active: number
  } | null
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
  await request(`/api/run/${state}`, { method: 'POST' })
  await loadDashboard()
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

onMounted(loadDashboard)
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
      <header>
        <div><p class="eyebrow">Autonomous crypto experiment</p><h1>{{ dashboard.product }}</h1></div>
        <span class="badge">No real orders</span>
      </header>
      <section class="card hero">
        <div><p>Persisted run state</p><h2>{{ dashboard.operational_state === 'degraded' ? 'Paused — market data degraded' : dashboard.operational_state === 'running' ? 'Running' : 'Stopped' }}</h2></div>
        <button v-if="dashboard.desired_state === 'stopped'" @click="changeState('start')">Start run</button>
        <button v-else class="stop" @click="changeState('stop')">Stop run</button>
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
        <p>{{ dashboard.market_data_incident?.cause || dashboard.planning_failure?.reason }}</p>
        <p v-if="dashboard.market_data_incident">
          Retry {{ dashboard.market_data_incident.retry_count }} · next attempt
          {{ dashboard.market_data_incident.next_retry_at }}
        </p>
      </section>
      <section v-else-if="dashboard.market_data_incident?.recovered_at" class="card incident-history">
        <strong>Latest recovered market-data incident</strong>
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
      <section class="metrics capital-grid">
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
        <article class="card strategies">
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
      <section class="card histories">
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
      <section v-if="dashboard.desired_state === 'stopped' && settings" class="card settings">
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
    </template>
  </main>
</template>

<style scoped>
:global(*) { box-sizing: border-box; }
:global(body) { margin: 0; color: #e7edf7; background: #07111f; font-family: Inter, ui-sans-serif, system-ui; }
main { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 48px 0; }
header, .hero, .actions { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
h1 { margin: 4px 0 0; font-size: clamp(2rem, 6vw, 4rem); letter-spacing: -.05em; }
h2 { margin: 4px 0 16px; font-size: 2rem; }
.eyebrow { color: #70e1bd; text-transform: uppercase; letter-spacing: .16em; font-size: .75rem; font-weight: 700; }
.card { border: 1px solid #24364f; border-radius: 18px; padding: 28px; background: #0d1b2d; box-shadow: 0 18px 60px #0005; }
.hero { margin-top: 42px; }
.report-download { display: flex; align-items: center; gap: 20px; margin-top: 20px; }
.report-download > div { flex: 1; }.report-download h2, .report-download p { margin: 0; }
.report-download .success, .report-download .error { flex-basis: 100%; }
.metrics, .settings, .workspace, .histories { margin-top: 20px; }
.metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.metrics article { min-width: 0; }
.workspace, .history-grid { display: grid; grid-template-columns: 1fr 1.4fr; gap: 20px; }
.pair { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; padding: 12px 0; border-bottom: 1px solid #24364f; }
.pair strong { margin: 0; font-size: 1rem; }.pair small { grid-column: 1 / -1; color: #9eb0c8; }
details { margin-top: 12px; } summary { cursor: pointer; font-weight: 700; } dl { display: grid; grid-template-columns: 1fr 1fr; font-size: .8rem; } dd { text-align: right; }
.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }.charts h2 { grid-column: 1/-1; }
.chart { border: 1px solid #24364f; border-radius: 12px; padding: 12px; min-height: 120px; }.chart h3 { margin: 0; text-transform: capitalize; }.chart svg { width: 100%; height: 70px; overflow: visible; }.chart polyline { fill: none; stroke: #70e1bd; stroke-width: 2; vector-effect: non-scaling-stroke; }
.positive { color: #70e1bd; }.negative, .error { color: #ff6b82; }.neutral { color: #d5deea; }.profit small { display: block; font-weight: 800; }
.filters { display: flex; gap: 12px; align-items: end; }.filters label { flex: 1; }.filters input, .filters select { display: block; width: 100%; margin-top: 6px; padding: 10px; color: white; border: 1px solid #3b506e; border-radius: 8px; background: #07111f; }
.audit-list details { padding: 12px 0; border-bottom: 1px solid #24364f; }.audit-list pre, dd pre { max-width: 100%; margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }.pagination { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 14px; }.pagination button:disabled { cursor: not-allowed; opacity: .4; }.empty, .pagination { color: #9eb0c8; }
.settings-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; margin-bottom: 18px; }
.settings label, .settings input { display: block; width: 100%; }
.settings input, .access input { margin-top: 8px; border: 1px solid #3b506e; border-radius: 10px; padding: 12px; color: #fff; background: #07111f; }
.success { margin-left: 14px; color: #70e1bd; }
strong { display: block; margin-top: 8px; font-size: 1.8rem; }
.badge { padding: 8px 12px; border: 1px solid #70e1bd; border-radius: 99px; color: #70e1bd; }
button { border: 0; border-radius: 10px; padding: 12px 18px; background: #70e1bd; color: #06110d; font: inherit; font-weight: 800; cursor: pointer; }
button.secondary { color: #e7edf7; background: #24364f; }
button.stop { color: #fff; background: #cf4c62; }
.access { max-width: 520px; margin: 6vh auto; }
.access label, .access input { display: block; width: 100%; }
.access label { margin: 28px 0 8px; }
.access .actions { justify-content: flex-start; margin-top: 18px; }
.error { color: #ff8799; }
@media (max-width: 720px) { header, .hero, .report-download, .filters, .pagination { align-items: stretch; flex-direction: column; } .metrics, .workspace, .history-grid, .charts, .settings-grid { grid-template-columns: minmax(0, 1fr); } .charts h2 { grid-column: 1; } .card { min-width: 0; padding: 20px; } strong { overflow-wrap: anywhere; } }
</style>
