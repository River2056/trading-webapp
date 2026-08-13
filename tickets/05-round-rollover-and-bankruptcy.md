# 05 — Close rounds, carry capital, and reset after bankruptcy

**What to build:** Seven-day rounds close deterministically with a performance retrospective, carry their ending capital into the next round, and preserve a complete cycle review before resetting after bankruptcy.

**Blocked by:** 03 — Execute auditable risk-controlled paper trades; 04 — Survive restarts and unsafe market-data conditions.

**Status:** ready-for-agent

- [ ] A round closes exactly once when its configured duration elapses, with deterministic treatment of open positions.
- [ ] Persisted metrics compare starting/ending capital, returns, drawdown, costs, pairs, strategies, trades, wins/losses, and rejected actions.
- [ ] A deterministic retrospective records what performed well or poorly without LLM-generated trade decisions or code changes.
- [ ] Ending equity becomes the next round's starting capital and a new selection/backtest phase begins.
- [ ] Bankruptcy is declared only when capital cannot fund any minimum permitted position after constraints, costs, and quantity rules.
- [ ] Bankruptcy closes the cycle, records completed rounds and a cycle retrospective, then resets to configured defaults while preserving history.
- [ ] Controllable-clock tests verify rollover, restart at boundaries, capital carry, bankruptcy, reset, and no duplicate closure.
