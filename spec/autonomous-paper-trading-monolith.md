# Autonomous Crypto Paper-Trading Monolith — Specification

## Problem Statement

A user with a small starting capital wants to evaluate whether disciplined crypto strategies can remain profitable across repeated seven-day rounds without risking real funds. They need one local application that continuously studies live markets, simulates trades, preserves an auditable history, reports performance, learns through deterministic round-to-round evaluation, survives restarts, and clearly shows when it cannot trade safely.

The current notes describe the goal and user interface but leave critical behavior undefined: whether trades are real, how markets and strategies are selected, what constitutes bankruptcy, how risk is controlled, how the loop survives failures, and how results are tested. Without these decisions, the system could produce irreproducible results or create financial risk.

## Solution

Build a single-user local monolith with a FastAPI backend, Vue/Vite frontend, and SQLite persistence. It will paper-trade—never submit exchange orders—using current and historical public Binance market data.

A continuous backend worker manages a Trading Run containing consecutive seven-day Trading Rounds. Before each round, it ranks liquid crypto markets using NTD-equivalent values, backtests deterministic strategy candidates on historical candles, selects five qualifying Trading Pairs, and locks the selected strategy configuration for that round. During the round, it evaluates signals and simulates fills under conservative configurable position, loss, fee, and slippage constraints.

Every selection, signal, rejected action, fill, portfolio valuation, failure, and retrospective is persisted. At round end, the system compares ending and starting capital, records what worked and did not, and carries ending capital into the next round. If virtual capital becomes insufficient for every permitted position, it records a bankruptcy retrospective and begins a new cycle using configured defaults while preserving the complete history.

The web application provides local authentication, run controls and settings, current status and health, capital and profit visualizations, active strategies and selected pairs, trade history, round history, and downloadable Markdown reports. Invalid or unavailable market data pauses trading and alerts the user; stale data is never used to place a simulated trade.

## User Stories

1. As the local operator, I want to sign up and log in, so that access to trading data and controls is gated.
2. As the local operator, I want the application to remain explicitly single-user, so that v1 avoids misleading multi-user guarantees.
3. As the local operator, I want to configure starting capital in NTD, so that I can control the experiment size.
4. As the local operator, I want the default starting capital to be 5,000 NTD, so that the documented experiment works without setup.
5. As the local operator, I want to configure round duration, so that I can run seven-day rounds by default or shorter development experiments.
6. As the local operator, I want to configure conservative risk limits, so that the simulated strategy cannot take unbounded exposure.
7. As the local operator, I want fees and slippage modeled, so that performance is not overstated.
8. As the local operator, I want to start and stop a Trading Run, so that I retain control of the autonomous worker.
9. As the local operator, I want stopped state persisted, so that restarting the server does not unexpectedly resume a deliberately stopped run.
10. As the local operator, I want an active run to resume safely after a process or machine restart, so that a seven-day round is not lost.
11. As the local operator, I want the engine to use public Binance data without trading credentials, so that paper trading cannot submit real orders.
12. As the local operator, I want markets ranked from sufficiently liquid Binance pairs using NTD-equivalent values, so that five suitable markets are selected automatically.
13. As the local operator, I want the ranking inputs and scores recorded, so that pair selection is explainable.
14. As the local operator, I want historical candle backtests before every round, so that strategies are evaluated before use.
15. As the local operator, I want deterministic strategy candidates such as RSI and MACD rules, so that results are reproducible.
16. As the local operator, I want backtest assumptions and metrics recorded, so that strategy selection can be audited.
17. As the local operator, I want the chosen five pairs and strategy versions locked for a round, so that rules do not change during evaluation.
18. As the local operator, I want entry and exit signals evaluated continuously at a configured cadence, so that paper trades follow the locked strategy.
19. As the local operator, I want every evaluated signal recorded, including actions rejected by risk rules, so that inactivity and decisions are explainable.
20. As the local operator, I want simulated fills based on fresh market data, so that the virtual portfolio approximates executable results.
21. As the local operator, I want maximum position allocation enforced, so that one market cannot consume all capital.
22. As the local operator, I want a maximum concurrent-position limit enforced, so that aggregate exposure remains controlled.
23. As the local operator, I want stop-loss and take-profit rules enforced, so that exits are systematic.
24. As the local operator, I want a daily loss limit enforced, so that the worker pauses new risk after a bad day.
25. As the local operator, I want capital and holdings valued consistently in NTD, so that round performance is comparable.
26. As the local operator, I want every trade to include timestamps, pair, side, quantity, price, fees, slippage, strategy, signal, and realized result, so that history is complete.
27. As the local operator, I want each round to close automatically at its configured duration, so that evaluation periods are consistent.
28. As the local operator, I want a round retrospective comparing starting and ending capital, so that improvement is evidence-based.
29. As the local operator, I want ending capital carried into the next round, so that the experiment compounds gains and losses.
30. As the local operator, I want bankruptcy defined as insufficient capital to open any permitted position after constraints and costs, so that reset behavior is deterministic.
31. As the local operator, I want a bankruptcy retrospective and completed-round count recorded, so that failed cycles remain useful.
32. As the local operator, I want a new cycle to reset to configured defaults after bankruptcy, so that the indefinite experiment continues.
33. As the local operator, I want all prior cycles preserved after reset, so that long-term comparisons remain possible.
34. As the local operator, I want trading to pause when market data is unavailable, invalid, or stale, so that fabricated fills cannot occur.
35. As the local operator, I want failures retried with bounded backoff and visible status, so that transient problems are recoverable and apparent.
36. As the local operator, I want the dashboard to show running, stopped, and degraded/paused states, so that engine health is unambiguous.
37. As the local operator, I want current total profit colored green when positive and red when negative, so that direction is immediately visible.
38. As the local operator, I want initial, current, and available capital shown, so that portfolio state is clear.
39. As the local operator, I want cycle and round counts plus days since the latest bankruptcy shown, so that experiment longevity is clear.
40. As the local operator, I want selected pairs and active strategy rules shown, so that I know what the worker is doing.
41. As the local operator, I want charts of equity, profit, exposure, and round performance, so that trends are visible.
42. As the local operator, I want searchable/filterable trade and round history, so that I can investigate outcomes.
43. As the local operator, I want settings validated before a run starts, so that invalid risk or timing parameters cannot enter the engine.
44. As the local operator, I want settings that affect strategy execution frozen during a round, so that evaluation remains fair.
45. As the local operator, I want a Markdown report for the ongoing run, so that I can download and review its configuration, selections, trades, metrics, failures, and retrospectives.
46. As the local operator, I want the report generated from persisted records, so that it remains reproducible after restart.
47. As a developer, I want market data, time, and persistence supplied through replaceable adapters, so that complete engine scenarios are deterministic in tests.
48. As a developer, I want SQLite state transitions to be atomic and idempotent, so that restart cannot duplicate trades or round closure.
49. As a developer, I want an explicit schema migration mechanism, so that persisted experiments survive application upgrades.
50. As a developer, I want secrets limited to local authentication material, so that exchange trading credentials are never required or stored.

## Implementation Decisions

- The product is a single-user local application. Authentication is an access gate, not a claim of multi-tenant isolation.
- The architecture is a monolith: FastAPI owns HTTP APIs, orchestration, engine execution, and SQLite access; Vue/Vite supplies the browser UI.
- All trades are paper trades. The system has no exchange-order adapter and accepts no exchange API trading credentials in v1.
- Binance public APIs provide historical candles, current prices, volume/liquidity inputs, and quote conversion data.
- Market-data access sits behind an adapter, allowing a future provider replacement without changing domain behavior.
- NTD is the reporting currency. Where Binance lacks direct NTD markets, values are converted through a recorded liquid quote path and timestamped conversion rate.
- A Trading Run is indefinite until manually stopped. It contains restartable cycles and consecutive fixed-duration Trading Rounds.
- A Trading Round defaults to seven days. Its selected pairs, strategy versions, backtest assumptions, and execution-affecting settings are immutable after activation.
- Before each round, the engine filters for data quality and minimum liquidity, ranks eligible markets, and chooses five. Ranking evidence is persisted.
- Strategy candidates are deterministic and versioned. Initial candidates use technical indicators such as RSI and MACD combined with explicit entry, exit, sizing, and risk rules.
- Every round performs a historical-candle backtest. Selection uses configured minimum qualification gates and deterministic ranking, not an LLM.
- The engine periodically evaluates the locked strategies against fresh data. All decisions are idempotently keyed to prevent duplicate simulated orders after restart.
- Conservative risk settings are configurable and receive safe defaults: position allocation, concurrent positions, stop-loss, take-profit, daily loss, modeled fee, and modeled slippage.
- Portfolio accounting distinguishes cash, open-position market value, realized profit/loss, unrealized profit/loss, costs, and total equity.
- Bankruptcy occurs when available equity cannot fund the minimum permitted new position after risk constraints, modeled costs, and exchange quantity constraints. Open positions are valued/closed according to deterministic round/run rules before declaring it.
- A completed round produces a deterministic retrospective from persisted metrics. No LLM-generated strategy changes occur in v1.
- Bankruptcy closes the current cycle, persists a cycle retrospective, and creates a new cycle with the user's configured default starting capital and settings. History is never deleted.
- The worker stores lifecycle checkpoints transactionally and resumes only runs whose persisted desired state is running. A manually stopped run remains stopped.
- Unavailable, malformed, out-of-order, or stale required market data changes the run to degraded/paused, prevents simulated trades, records the incident, and exposes it to the UI. Recovery requires fresh validated data; retries use bounded backoff.
- Settings are divided into defaults for future rounds and a frozen round configuration. Safe validation rejects contradictory or non-positive limits.
- SQLite uses explicit migrations, foreign-key enforcement, transactions, uniqueness constraints for idempotency, and UTC timestamps.
- Local passwords are stored only as strong salted password hashes. Session authentication uses secure local cookies or equivalent token handling suitable for a local deployment.
- The dashboard exposes status, health, capital, profit, cycles, rounds, longevity, strategies, pairs, equity/performance charts, and history.
- Markdown reports are generated server-side from persisted data and downloaded by the browser.
- The API separates authentication, settings/control, current dashboard snapshots, histories, and report export while sharing domain services with the worker.
- The application records enough provenance to reproduce decisions: source timestamps, candle interval, strategy version/configuration, ranking/backtest metrics, conversion rates, signal evidence, and modeled execution costs.

## Testing Decisions

- The primary testing seam is the highest practical seam: the Trading Engine service operating against injected market-data, clock, and persistence adapters.
- Engine tests exercise externally observable state transitions and ledger output rather than private calculation steps. Scenarios include selection/backtest, signals and fills, risk rejection, round rollover, bankruptcy/reset, stop/start, restart idempotency, stale-data pause, and recovery.
- Deterministic fixture candles and a controllable clock make multi-day behavior executable without wall-clock waits or Binance access.
- SQLite integration tests use the real schema and transactions to verify migrations, constraints, accounting invariants, checkpoints, and restart behavior.
- Binance adapter contract tests validate response mapping, timestamp/freshness checks, pagination/rate-limit behavior, conversion provenance, and error handling using recorded or stubbed HTTP responses. Live Binance tests are optional and never gate deterministic CI.
- FastAPI contract tests cover authentication, authorization, validation, run controls, dashboard projections, history pagination/filtering, status/error representation, and report downloads through public HTTP interfaces.
- Vue component tests cover status/profit presentation and settings validation where useful, while a small browser suite covers sign-up/login, configure/start, dashboard inspection, stop/resume, degraded status, histories, and report export.
- Accounting property/invariant tests verify that equity components reconcile, fees are never omitted, positions cannot exceed configured limits, and duplicate events cannot alter balances twice.
- Reports are snapshot-tested for required semantic sections and provenance, avoiding brittle checks of irrelevant formatting.
- No test asserts internal method calls, private module layout, exact SQL text, or chart-library implementation details.

## Out of Scope

- Real-money trading, exchange API keys, exchange order submission, deposits, or withdrawals.
- Claims of profitability, investment advice, or guaranteed capital preservation.
- Multi-user tenancy, role-based access control, hosted SaaS operation, or social features.
- Microservices, distributed queues, external databases, or cloud orchestration.
- LLM-selected trades, LLM-generated strategy mutations, reinforcement learning, or automatic code changes.
- Fundamental company analysis; v1 crypto selection and strategies use market data and deterministic technical rules.
- Mobile/native clients and push notifications.
- Tax accounting, regulatory reporting, and fiat settlement.
- Tick-perfect exchange simulation, order-book queue modeling, leverage, margin, derivatives, lending, or short selling.
- Additional market-data providers beyond the adapter boundary and initial Binance implementation.

## Further Notes

- “MACR” in the source note is interpreted as MACD.
- “Profitable pairs” means pairs ranked as promising under recorded historical/liquidity criteria; it does not imply future profitability.
- The application must display a clear paper-trading label throughout to avoid confusion with real execution.
- The continuous loop is implemented by durable application state and restart-safe orchestration, not by retaining an AI conversation context. It therefore does not need context-window resets.
- Initial numeric defaults for candle interval, lookback window, liquidity threshold, position limits, loss limits, fees, slippage, and polling cadence should be conservative, documented, and adjustable before a round starts.
