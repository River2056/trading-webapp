# 03 — Execute auditable risk-controlled paper trades

**What to build:** An active round evaluates its locked strategies against fresh market data and executes only permitted simulated trades, producing a reconcilable virtual portfolio and complete decision history.

**Blocked by:** 02 — Select five markets and lock a backtested round plan.

**Status:** ready-for-agent

- [ ] Strategy signals are evaluated at the configured cadence using the frozen round plan.
- [ ] Every signal and rejected action records market evidence, strategy version, reason, and source timestamp.
- [ ] Simulated fills use fresh prices and model configured fees and slippage; no exchange order path or trading credentials exist.
- [ ] Position allocation, concurrent-position, stop-loss, take-profit, and daily-loss limits are enforced.
- [ ] Trade records include pair, side, quantity, price, costs, timestamps, signal provenance, and realized result.
- [ ] Cash, positions, realized/unrealized P&L, costs, available capital, and total equity reconcile in NTD.
- [ ] Idempotency identifiers prevent the same market interval or signal from altering balances twice.
- [ ] Deterministic engine and accounting-invariant tests verify fills, exits, rejections, limits, and reconciliation.
