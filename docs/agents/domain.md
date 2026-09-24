# Domain Docs

This is a single-context repository.

## Before exploring

Read:

- `CONTEXT.md` at the repository root for canonical domain language.
- Relevant ADRs under `docs/adr/`, when that directory exists.
- The authoritative specification named by the assigned ticket or map.

If an ADR directory or referenced file does not yet exist, proceed silently. Create domain artifacts lazily only when a term or durable architectural decision is actually resolved.

## Use the glossary vocabulary

Use terms exactly as defined in `CONTEXT.md` in issue titles, specifications, tests, and code. When new work conflicts with an existing definition, surface the conflict and resolve the language before implementation.

`CONTEXT.md` is a glossary, not an implementation specification. Keep technology, algorithms, API shapes, and deployment choices out of it.

## ADRs

Use root-level `docs/adr/` for decisions that are hard to reverse, surprising without context, and the result of a genuine trade-off. Explicitly flag any proposed work that contradicts an existing ADR.
