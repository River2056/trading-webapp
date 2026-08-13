<script setup lang="ts">
import { onMounted, ref } from 'vue'

type Dashboard = {
  product: string
  desired_state: 'running' | 'stopped'
  configured_capital_ntd: string
  current_capital_ntd: string
  engine_health: 'healthy' | 'degraded'
  planning_failure: { reason: string; occurred_at: string } | null
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

async function loadDashboard() {
  try {
    dashboard.value = await request<Dashboard>('/api/dashboard')
    settings.value = await request<RunSettings>('/api/settings')
    needsAuthentication.value = false
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
  const result = await request<{ desired_state: 'running' | 'stopped' }>(`/api/run/${state}`, { method: 'POST' })
  if (dashboard.value) dashboard.value.desired_state = result.desired_state
}

async function saveSettings() {
  if (!settings.value) return
  await request('/api/settings', { method: 'PUT', body: JSON.stringify(settings.value) })
  settingsMessage.value = 'Settings saved.'
  if (dashboard.value) {
    dashboard.value.configured_capital_ntd = Number(settings.value.starting_capital_ntd).toFixed(2)
    dashboard.value.current_capital_ntd = Number(settings.value.starting_capital_ntd).toFixed(2)
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
        <div><p>Persisted run state</p><h2>{{ dashboard.desired_state === 'running' ? 'Running' : 'Stopped' }}</h2></div>
        <button v-if="dashboard.desired_state === 'stopped'" @click="changeState('start')">Start run</button>
        <button v-else class="stop" @click="changeState('stop')">Stop run</button>
      </section>
      <section v-if="dashboard.engine_health === 'degraded'" class="card error" role="alert">
        <strong>Planning health degraded</strong>
        <p>{{ dashboard.planning_failure?.reason }}</p>
      </section>
      <section class="metrics">
        <article class="card"><p>Configured capital</p><strong>{{ Number(dashboard.configured_capital_ntd).toLocaleString('en-US', { style: 'currency', currency: 'TWD' }) }}</strong></article>
        <article class="card"><p>Current capital</p><strong>{{ Number(dashboard.current_capital_ntd).toLocaleString('en-US', { style: 'currency', currency: 'TWD' }) }}</strong></article>
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
main { width: min(960px, calc(100% - 32px)); margin: 0 auto; padding: 64px 0; }
header, .hero, .actions, .metrics { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
h1 { margin: 4px 0 0; font-size: clamp(2rem, 6vw, 4rem); letter-spacing: -.05em; }
h2 { margin: 4px 0 16px; font-size: 2rem; }
.eyebrow { color: #70e1bd; text-transform: uppercase; letter-spacing: .16em; font-size: .75rem; font-weight: 700; }
.card { border: 1px solid #24364f; border-radius: 18px; padding: 28px; background: #0d1b2d; box-shadow: 0 18px 60px #0005; }
.hero { margin-top: 42px; }
.metrics, .settings { margin-top: 20px; }
.metrics article { flex: 1; }
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
@media (max-width: 600px) { header, .hero, .metrics { align-items: stretch; flex-direction: column; } .settings-grid { grid-template-columns: 1fr; } }
</style>
