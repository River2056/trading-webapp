# 07 — Export reproducible Markdown run reports

**What to build:** The operator can download a detailed Markdown report for the ongoing Trading Run, reconstructed from persisted records rather than transient worker memory.

**Blocked by:** 05 — Close rounds, carry capital, and reset after bankruptcy; 06 — Deliver the complete analytical dashboard and histories.

**Status:** ready-for-agent

- [ ] An authenticated dashboard action downloads a Markdown report with a stable filename and content type.
- [ ] The report identifies paper-trading mode and includes run/cycle/round status, timing, settings, capital, profit, and risk metrics.
- [ ] It includes selected-pair ranking, conversion and source provenance, backtest assumptions/results, and locked strategy versions.
- [ ] It summarizes trades, rejected decisions, costs, incidents, completed-round retrospectives, and bankruptcy events.
- [ ] Report values reconcile with API/dashboard projections and can be reproduced after process restart.
- [ ] Contract and semantic snapshot tests verify required sections, authorization, no-data states, and historical consistency.
