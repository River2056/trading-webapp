# Trading Domain Glossary

## Trading Run

A long-lived experiment that begins with configured starting capital and contains consecutive trading rounds. If its paper-trading capital is exhausted, the run records a bankruptcy review and restarts from its configured defaults. A run can be running or stopped.

## Trading Round

A fixed-duration evaluation period, initially seven days. It starts with the capital carried from the preceding round (or the run's configured starting capital), records simulated trades, and ends with performance analysis whose ending capital funds the next round.

## Paper Trade

A simulated crypto-market order priced from live market data. It changes only the application's virtual portfolio and never submits an order to an exchange.

## Trading Pair

A crypto market selected as one of up to five concurrently studied and traded markets during a round.

## Strategy

A versioned set of entry, exit, position-sizing, and risk-management rules whose signals and outcomes can be evaluated from recorded evidence.

## Trading Agent

An automated participant that selects one portfolio-level action from a bounded set of deterministic, already risk-compliant candidates during each Decision Cycle. It has no authority to construct execution parameters, bypass policy, risk, freshness, or execution controls, and does not possess exchange credentials.

## Trade Intent

A structured portfolio-level proposal selected by the Trading Agent during one Decision Cycle from deterministic, already risk-compliant candidates across all allowed Trading Pairs. The MVP candidate types are `HOLD`, `OPEN_POSITION`, and `CLOSE_POSITION`; it does not pyramid positions or make partial discretionary reductions. At most one accepted Trade Intent may cause a discretionary portfolio mutation per Decision Cycle. A Trade Intent is not an order and has no portfolio effect unless deterministic controls accept it; mandatory risk exits and reconciliation actions remain outside the Trading Agent.

## Decision Cycle

One scheduled evaluation of the current approved market and portfolio snapshot, run every five minutes after the corresponding five-minute market candle is finalized and freshness checks pass. It may accept at most one discretionary Trade Intent.

## Decision Snapshot

A bounded, versioned set of derived market, liquidity, volatility, portfolio-exposure, position, and candidate-rationale features supplied to the Trading Agent during one Decision Cycle. It uses opaque candidate identifiers and excludes credentials, account identifiers, exact wealth, raw free text, and arithmetic that deterministic code can perform.

## Live Trade

A real exchange order created only from an accepted Trade Intent while live mode is explicitly enabled. It can affect real assets and must remain within the configured live risk envelope.

## Live Trading Gate

The evidence-based promotion boundary that prevents live trading until required safety, reliability, accounting, and risk-adjusted paper-performance criteria have been satisfied.

## Bankruptcy

The condition in which remaining virtual capital is insufficient to open any permitted position after fees and risk constraints. It ends the current run cycle, triggers a retrospective, and resets virtual capital to configured defaults.
