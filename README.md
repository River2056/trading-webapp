# Trading Webapp

A local, single-user FastAPI/Vue/SQLite monolith for autonomous **paper trading only**. It has no exchange-order adapter, accepts no exchange credentials, and can never submit a real trade.

The authoritative product specification is in `spec/autonomous-paper-trading-monolith.md`; implementation tickets are in `tickets/`.

## Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and npm

## Run locally

```bash
make install
make dev
```

Open <http://localhost:5173>. Vite proxies `/api` requests to FastAPI at <http://127.0.0.1:8000>. API documentation is at <http://127.0.0.1:8000/docs>.

On first use, create the single local operator account with a password of at least 12 characters. The password is salted and hashed with Argon2. Authentication uses a random, SHA-256-at-rest, HTTP-only, SameSite=strict session cookie. Because this workflow uses local HTTP, the cookie is not marked `Secure`; use an HTTPS reverse proxy before exposing the app beyond localhost.

SQLite data is stored at `data/paper-trading.sqlite3`. Explicit SQL migrations run transactionally at startup, enable foreign-key checking for every application connection, and preserve the desired run state across restarts. Delete that local database only when intentionally resetting all local state.

## Defaults and controls

New installations begin stopped with 5,000 NTD virtual capital, seven-day rounds, a five-minute strategy cadence, and conservative position, concurrent-position, stop-loss, take-profit, daily-loss, fee, and slippage limits. Settings are validated and can only change while the run is stopped. Start/stop changes the persisted desired state. The in-process worker resumes only persisted running runs, serializes SQLite state transitions, retries transient market-data and database-lock failures with bounded backoff, and shuts down only after an in-flight transaction completes.

All amounts and fills are conservative simulation assumptions, not executable quotes or financial advice. Default maximum allocation is 10% per position, at most three positions, 5% stop loss, 10% take profit, 3% daily realized-loss pause, 0.10% fee, and 0.10% slippage. Public market data can be delayed or unavailable; unsafe inputs pause simulated trading and are shown as degraded health. Bankruptcy is declared only when every otherwise-qualified candidate has valid exchange rules and is proven unfundable; unknown data remains a recoverable degradation.

## Quality checks

```bash
make verify
```

This runs backend and frontend tests, Python and TypeScript type checks, linting, and production builds.
