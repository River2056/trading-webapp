# Agent Instructions

- Read `CONTEXT.md`, the authoritative spec, and the assigned ticket before editing.
- Use strict TDD: add one failing behavior test, run it to verify RED, implement minimally, then verify GREEN.
- Prefer the Trading Engine as the highest-level test seam, with injected market-data, clock, and persistence adapters.
- This application is paper trading only. Never add exchange-order submission or exchange trading credentials.
- Run focused tests regularly and the full test, lint, type-check, and build suite before committing.
- Review the diff against the assigned ticket and spec before committing.
- Commit completed work locally; do not push.
