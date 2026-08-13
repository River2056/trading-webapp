# 02 — Select five markets and lock a backtested round plan

**What to build:** Starting a round obtains valid public Binance data, ranks liquid markets in NTD-equivalent terms, backtests deterministic strategy candidates, selects five qualifying pairs, and activates an immutable, auditable round plan.

**Blocked by:** 01 — Bootstrap a secure, observable paper-trading run.

**Status:** ready-for-agent

- [ ] Public Binance historical/current market data is accessed through a replaceable adapter without exchange credentials.
- [ ] Data quality, liquidity, and quote-conversion rules deterministically produce an eligible market universe and NTD-equivalent ranking.
- [ ] Ranking inputs, timestamps, conversion rates, scores, exclusions, and selected five pairs are persisted.
- [ ] Versioned RSI/MACD strategy candidates are backtested on configured historical candles using recorded assumptions and metrics.
- [ ] Deterministic qualification and ranking select a strategy configuration for each selected pair.
- [ ] The selected pairs, strategy versions/configuration, risk settings, and backtest provenance are frozen when the round activates.
- [ ] Engine-level deterministic tests and Binance adapter contract tests cover success, insufficient candidates, malformed data, and stable selection.
