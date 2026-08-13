# 04 — Survive restarts and unsafe market-data conditions

**What to build:** The continuous worker safely resumes active paper trading after interruption and refuses to trade whenever required Binance data is missing, invalid, out of order, or stale.

**Blocked by:** 03 — Execute auditable risk-controlled paper trades.

**Status:** ready-for-agent

- [ ] The backend continuously advances runs whose persisted desired state is running without requiring an open browser.
- [ ] Lifecycle checkpoints and trade transitions are atomic and idempotent across process interruption and restart.
- [ ] Restart resumes the current round without duplicate signals, fills, or closure and never resumes a deliberately stopped run.
- [ ] Missing, malformed, out-of-order, or stale required data changes the run to a recorded degraded/paused state before any trade.
- [ ] Retries use bounded backoff, preserve the last committed state, and require fresh validated data before recovery.
- [ ] API and dashboard projections expose incident cause, timestamp, retry/recovery state, and current health.
- [ ] Fault-injection tests verify crashes at transaction boundaries, repeated events, data failures, pause, and recovery.
