# JEV integration feasibility for the trading webapp

**Research date:** 2026-09-23  
**Scope:** Primary TypeSafe AI sources only; repository analysis; no application-code changes.

## Executive conclusion

The product is **JEV** (all capitals), not a different model spelled “Jev.” TypeSafe AI describes JEV as a small, text-only “System 1” language model intended for fast, narrow, repeatable workloads rather than broad reasoning.[1][4][8]

A network integration is technically straightforward from this Python 3.11/FastAPI application: call TypeSafe AI's hosted REST API with a bearer API key, or install the official `typesafeai` Python SDK and pass the same key.[2][3][5] However, **JEV must not be placed in this repository's trade-selection, strategy-mutation, or order-execution path under the current specification**. The authoritative spec requires deterministic strategy selection, explicitly says selection is “not an LLM,” says no LLM-generated strategy changes occur in v1, places LLM-selected trades out of scope, and prohibits real-money trading and exchange API keys (`../../spec/autonomous-paper-trading-monolith.md`, especially lines 84, 89, 116, and 120). `../../AGENTS.md` independently says the application is paper-trading only and must never add exchange-order submission or exchange credentials.

The feasible current-scope use is an **optional, non-authoritative analysis layer**: summarize persisted round evidence, classify incidents, draft human-readable report commentary, or run in shadow mode to compare model suggestions with deterministic outcomes. Its output must not alter positions, balances, risk controls, frozen round plans, or simulated fills.

## Verified facts

### Product identity and intended workload

- TypeSafe AI launched the product under the exact name **JEV** and describes it as a production-grade small language model for low-latency, repeatable tasks.[1]
- The model catalog lists `JEV-1` as the production model, with `JEV-0` deprecated.[4]
- Official guidance characterizes JEV as a “single System 1 LLM” optimized for one narrow task at a time; it recommends explicit prompts, few-shot examples, and workflows that break complex work into smaller steps.[8]
- `JEV-1` is text-only, has a 131,072-token context window, and the model table marks image, audio, video, reasoning, temperature control, and logits as unsupported.[4]

### Hosted API and authentication

- The current base URL is `https://www.typesafe.ai/api/v1`; TypeSafe AI's changelog says the API moved there on 2026-09-15.[3][11]
- Text generation uses `POST /text/chatcompletion` with JSON containing `model`, `messages`, and optional `max_tokens`; the response contains `id`, `created`, `model`, `object`, `usage`, and `choices`.[3]
- Authentication uses the HTTP `Authorization` header with the Bearer scheme and the `TS_API_KEY` value.[2][3]
- The API reference says `max_tokens` defaults to 16,384, cannot exceed the model's context length, and HTTP 429 covers rate-limit or quota exhaustion.[3]
- JEV currently returns non-streaming responses: the model page marks streaming unsupported, and the SDK's streaming method exists only for future compatibility and presently yields the complete response once.[4][5]

### Official Python SDK

- The official Python package is installed with `pip install typesafeai`; the synchronous and asynchronous clients are `TypeSafeAI` and `AsyncTypeSafeAI`.[2][5]
- The client can take `api_key` explicitly or read `TS_API_KEY`; the documented call is `client.text.chatcompletion(model="JEV-1", messages=[...])`.[2][5]
- The SDK exposes typed request/response objects and documented exception categories for authentication, rate limiting, validation, and generic API failures.[5]
- The SDK supports a configurable request timeout; the documented default is 120 seconds.[5]

### Structured output and tool use

- JEV does **not** advertise native JSON/structured-output mode. TypeSafe AI's model table marks JSON output unsupported.[4]
- The official function-calling guide is not an OpenAI-compatible native tool-call response protocol. It is an application-side pattern: prompt JEV to emit a tool name plus arguments, parse the text with regular expressions, invoke the tool in application code, then optionally send the result back to JEV for formatting.[7]
- The same guide recommends providing explicit tool definitions, few-shot examples, constrained output formats, input validation, graceful parse failure, and logging.[7]
- For trading, this means JEV text must be treated as **untrusted input**. A parser can validate a proposal schema, but the model cannot be relied upon to produce schema-valid output on every request, and it must never receive a callable exchange-order tool.

### Confidence and consistency controls

- TypeSafe AI documents “jaggedness” as the risk of inconsistent answers to equivalent prompts and provides a separate build-time tool that repeatedly tests a prompt and returns a score from 0 to 1; lower is more consistent.[10]
- Their guide recommends using representative real inputs, specifying a JSON schema when applicable, and aiming for jaggedness below 0.1 for production deployments.[10]
- TypeSafe AI also publishes a prompt-based confidence-scoring pattern, but explicitly warns that self-reported confidence can be miscalibrated and should be calibrated against ground truth, used for routing/review, and combined with jaggedness.[9]
- Neither mechanism is proof that a trading decision is correct. At most, they can support abstention, review, and regression testing around a narrowly defined auxiliary task.[9][10]

### Deployment, service model, and licensing

- The public integration documentation describes a TypeSafe AI-hosted HTTPS service and official Python SDK. No primary source reviewed for this note documents downloadable JEV weights, on-premises deployment, VPC/private deployment, regional endpoint selection, or customer-managed inference.
- The public legal index identifies the Master Customer Agreement, Privacy Policy, Acceptable Use Policy, and Data Processing Agreement as the applicable documents.[6]
- Those documents govern service use, acceptable use, personal data, and customer-data processing; the reviewed sources do not present JEV under an open-source model license.[12][13][14]
- TypeSafe AI's DPA separately governs its processing of customer personal data.[15]
- Under the Master Customer Agreement, TypeSafe AI retains rights in the service; the customer retains rights in customer content; output may not be unique; and the service is provided without a guarantee that output will be accurate or complete.[14]
- The DPA says TypeSafe AI processes customer-provided data to provide the service, may use subprocessors, applies security measures described in an attached exhibit, and provides deletion/return mechanisms on termination subject to stated exceptions.[15]
- The Privacy Policy says TypeSafe AI collects prompts and uploaded content, stores/uses data in the United States, uses AWS among its service providers, and applies technical/administrative/organizational safeguards while acknowledging that no transmission or storage method is fully secure.[13]
- The Acceptable Use Policy prohibits unauthorized financial activity and providing personalized financial guidance or advice without qualified professional involvement. It also restricts certain high-impact automated decisions and requires compliance with applicable laws.[12]

### Latency and operational characteristics

- TypeSafe AI markets JEV for low-latency workloads, but the reviewed public docs do not publish a latency SLA, geographic latency figures, sustained throughput guarantee, or rate-limit numbers.[1][3]
- Calls are remote, non-streaming, and may fail with authentication, validation, rate-limit/quota, server, or network errors.[3][4][5]
- Consequently, a synchronous JEV call inside the five-minute trading cadence would add a new external availability and timing dependency. A 120-second SDK default timeout is too large for a safety-critical engine step unless explicitly reduced.[5]

## Unknowns requiring vendor confirmation or a measured proof of concept

1. **Self-hosting/private deployment:** no reviewed primary source documents model-weight access, on-premises deployment, private networking, data residency choice, or a dedicated tenant.
2. **Retention and model-training treatment:** the legal documents describe processing and deletion rights, but the reviewed public pages do not state a simple API-specific prompt-retention duration or an explicit, unconditional “API data is never used for training” commitment. Confirm contractually before sending proprietary strategies or account data.
3. **Capacity:** exact per-account rate limits, concurrency limits, token-per-minute quotas, uptime/SLA, and production support response targets are not public in the reviewed docs.
4. **Latency:** no official p50/p95/p99 end-to-end API latency for representative prompt/output sizes was found. Measure from the intended deployment region.
5. **Trading quality:** no TypeSafe AI primary source reviewed here evaluates JEV on market forecasting, portfolio construction, risk control, financial calibration, or profitability. General benchmark results would not validate trading performance.
6. **Determinism controls:** the model table says temperature is unsupported, and no seed or deterministic-decoding parameter is documented.[3][4] Jaggedness testing can measure consistency but does not create deterministic replay.
7. **Native structured output/function calling:** JSON output is marked unsupported and the function-calling guide uses text parsing; there is no documented schema-enforced response or signed tool-call object.[4][7]
8. **Commercial terms:** current pricing, committed capacity, indemnities appropriate to financial use, and any negotiated security/SLA terms require an order form or vendor discussion.[14]
9. **Regulatory fit:** whether a specific trading-analysis use is “personalized financial guidance,” regulated investment advice, or another restricted activity depends on deployment, users, jurisdiction, and professional oversight. Obtain legal/compliance review before any use beyond private paper-trading research.[12]

## Architectural recommendations

### 1. Preserve the deterministic engine as the sole decision authority

Do not replace or augment pair ranking, backtest qualification, signal generation, position sizing, stop-loss/take-profit enforcement, bankruptcy logic, or fill simulation with JEV under the current spec. The engine's reproducibility and persisted provenance are product requirements; JEV has no documented deterministic seed, native structured output, or trading-specific validation.

### 2. If pursued, integrate only through a non-authoritative adapter

Add a future interface such as `NarrativeAnalysisProvider` or `ResearchAssistant`, not an exchange or execution adapter. Its input should be a minimal, redacted snapshot built from already-persisted evidence. Its result should be stored separately as commentary with:

- provider and model (`TypeSafe AI`, `JEV-1`);
- prompt-template version and prompt hash;
- request/response timestamps and latency;
- source record identifiers and market-data timestamps;
- raw response, parsed representation, validation outcome, and rejection reason;
- token usage and API error category;
- an explicit `non_authoritative=true` / `paper_trading_only=true` marker.

A failure, timeout, invalid parse, quota error, or low-confidence/high-jaggedness result must degrade only the optional commentary feature. It must not pause or change the deterministic trading engine.

### 3. Prefer asynchronous offline jobs over the live cadence

Good candidate tasks are round-report narrative drafts, incident classification, explanation of already-made deterministic decisions, or shadow-mode comparison after a signal is persisted. Run them outside the transaction that evaluates signals or writes simulated fills. Configure a short timeout, bounded retries with jitter for transient failures, and no retry for authentication/validation failures.

### 4. Constrain and validate every output

Because native JSON mode is unavailable, request a small line-oriented or JSON-like payload, parse it strictly, reject unknown fields/actions, enforce numeric bounds, and never execute free-form output. Any proposed `buy`, `sell`, allocation, stop, or target value should remain observational and be evaluated against deterministic ground truth—not forwarded to the portfolio mutation path.

### 5. Keep secrets and financial data separated

Store `TS_API_KEY` as a server-side deployment secret, never in Vue code, SQLite, reports, prompts, or logs. Send only the minimum market/strategy evidence needed. Do not send local-auth material, exchange credentials, personally identifiable information, or full proprietary strategy history without an approved retention/security agreement.

### 6. Gate a proof of concept with measurable acceptance criteria

Before any product integration, run a fixture-based shadow evaluation that measures:

- schema/parse success rate;
- p50/p95/p99 latency and timeout rate;
- cost/token usage;
- repeatability and jaggedness over paraphrased equivalent prompts;
- abstention behavior on stale, incomplete, contradictory, and adversarial inputs;
- factual agreement with persisted deterministic evidence;
- prompt-injection resistance when market/news text contains instructions;
- zero mutation of run, round, portfolio, signal, or fill state.

The proof of concept should be rejected if it cannot fail closed or if it creates a dependency in paper-trading execution.

## Precise scope conflict

The separate desire to use an API key for **real trading** conflicts directly with this repository's governing documents:

- `../../AGENTS.md`: “This application is paper trading only. Never add exchange-order submission or exchange trading credentials.”
- `../../CONTEXT.md`: a paper trade “never submits an order to an exchange.”
- `../../spec/autonomous-paper-trading-monolith.md`: all trades are paper trades; no exchange-order adapter or exchange trading credentials; real-money trading and exchange API keys are out of scope.

A TypeSafe AI API key is an inference-service credential and could be used for the optional analysis integration described above. An **exchange** API key, exchange-order adapter, or model-triggered real order must not be added. Supporting real trading would require a separate product/repository or an explicit rewrite and approval of the authoritative spec and safety model; it is not a permissible integration detail in this codebase.

## Feasibility verdict

| Question | Verdict |
|---|---|
| Can this Python backend call JEV? | **Yes**, through the hosted REST API or official sync/async Python SDK.[2][3][5] |
| Can it run JEV locally from documented weights? | **Not verified**; no official self-hosting or weights path was found. |
| Does JEV provide native JSON/schema output? | **No, according to the model catalog.**[4] |
| Does it provide native tool/function calls? | **Not documented.** The official pattern is prompt + regex parsing + application-side execution.[7] |
| Is it suitable as the authoritative trading-decision engine? | **No evidence supports that use**, and it conflicts with the current repository spec. |
| Is it suitable for optional paper-trading analysis/reporting? | **Potentially yes**, behind a fail-closed, non-authoritative adapter and after security/latency evaluation. |
| Can it be connected to real exchange execution here? | **No.** Repository rules explicitly prohibit it. |

## Sources

[1] https://www.typesafe.ai/blog/launching-jev — Launching JEV
[2] https://docs.typesafe.ai/introduction/quickstart — Quickstart
[3] https://docs.typesafe.ai/api — API Reference
[4] https://docs.typesafe.ai/models — Models
[5] https://docs.typesafe.ai/sdk — SDK Reference
[6] https://docs.typesafe.ai/legal — Legal
[7] https://docs.typesafe.ai/guides/function-calling — Function calling with JEV
[8] https://docs.typesafe.ai/guides/how-to-build-with-jev — How to Build with JEV
[9] https://docs.typesafe.ai/guides/confidence-score — Confidence scoring
[10] https://docs.typesafe.ai/guides/jaggedness-score — Jaggedness score
[11] https://docs.typesafe.ai/changelog/overview — Changelog
[12] https://www.typesafe.ai/acceptable-use — Acceptable Use Policy
[13] https://www.typesafe.ai/privacy — Privacy Policy
[14] https://www.typesafe.ai/mca — Master Customer Agreement
[15] https://www.typesafe.ai/dpa — Data Processing Agreement
