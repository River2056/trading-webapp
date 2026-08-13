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

## Bankruptcy

The condition in which remaining virtual capital is insufficient to open any permitted position after fees and risk constraints. It ends the current run cycle, triggers a retrospective, and resets virtual capital to configured defaults.
