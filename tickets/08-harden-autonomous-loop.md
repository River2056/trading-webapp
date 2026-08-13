# 08 — Harden the complete autonomous loop

**What to build:** A verified, documented local release demonstrates the complete indefinite paper-trading loop across authentication, selection, execution, failure recovery, round rollover, bankruptcy reset, analytics, and reporting.

**Blocked by:** 01–07 — all preceding tickets.

**Status:** complete

- [x] One documented command or workflow initializes, migrates, and runs the backend worker and Vue application locally.
- [x] A deterministic end-to-end scenario covers signup/login, configure/start, pair/strategy selection, fills, dashboard/history, report, stop/resume, round rollover, bankruptcy, and reset.
- [x] Restart and market-data fault scenarios prove no duplicate accounting and no trading on stale data.
- [x] Accounting/property tests prove reconciliation and configured exposure/loss invariants over varied event sequences.
- [x] Conservative numeric defaults and all assumptions are documented, validated, and clearly distinguish simulation from financial advice.
- [x] Database migration and upgrade tests preserve existing run history.
- [x] The full automated test, lint, type-check, and production-build workflow passes from a clean checkout.
- [x] No exchange-order capability, exchange credential input, or hidden dependency on an AI conversation exists.
