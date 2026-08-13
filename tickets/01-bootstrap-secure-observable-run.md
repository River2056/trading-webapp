# 01 — Bootstrap a secure, observable paper-trading run

**What to build:** A locally runnable FastAPI/Vue/SQLite monolith where one operator can sign up, log in, configure a paper-trading run, start or stop it, and see its persisted lifecycle state.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] FastAPI, Vue/Vite, and SQLite run together through a documented local workflow.
- [ ] Explicit migrations create the schema with foreign keys, UTC timestamps, transactions, and constraints.
- [ ] A single local account can sign up and log in using a strong salted password hash and protected session handling.
- [ ] Settings accept starting capital (default 5,000 NTD), round duration (default seven days), strategy cadence, and conservative risk/cost limits with validation.
- [ ] Start and stop controls persist desired run state; a deliberately stopped run remains stopped after restart.
- [ ] The dashboard displays paper-trading identity plus running/stopped state and basic configured/current capital.
- [ ] Public API and browser tests verify authentication, settings validation, controls, persistence, and the primary journeys.
