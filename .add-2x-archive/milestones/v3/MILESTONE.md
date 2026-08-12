# MILESTONE: V3

goal: tenant-facing production parity, slice 1 of the LiteLLM main-feature goal: governed keys (budgets, expiry, model allowlists, rotation), app-level TPM/RPM rate limiting, rolling spend windows with soft-budget alerts and a spend query API, upstream health checks with webhook alerting, and runtime model management — all live-verified through the TLS edge
rationale: intake 2026-06-11 classified the standing goal "production grade to full main features of LiteLLM" (set by Tin Dang 2026-06-10, executed under delegated auto mode per "implement fully autonomous with your decision by deep think") as new-major. Scope drawn from .add/research/litellm-feature-inventory-2026-06-11.md Tier 1 (core production parity): the items a paying multi-tenant customer hits first. Teams/orgs hierarchy is Tier 1 but XL — deferred to v4 so v3 stays one coherent, shippable slice.
stage: production · status: active · created: 2026-06-11

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  per-key governance (hard/soft budget, expiry, model allowlist, rotation) · app-level
     TPM/RPM sliding-window limits per key+tenant+model (Redis Lua) · rolling spend windows
     + soft-budget alert events + spend query API · upstream/model health checks + webhook
     alerting (budget, breaker-open, drain-timeout, health-fail events) · runtime model
     enable/disable per tenant without restart · dashboard surfaces for key governance and
     spend queries
Out: teams/organizations hierarchy + SCIM (v4) · response caching (v4) · guardrails/PII
     framework (v4) · multi-provider routing/fallbacks/cooldowns beyond OpenRouter (v4+,
     needs the passthrough-upstream intake decision) · SSO/OIDC dashboard login (v4) ·
     OTel/Langfuse callback integrations (v4) · MCP gateway, policy engine, prompt
     management, RAG (v5+ long tail)

## Shared decisions & glossary deltas   (living — every task must honor these)
- Hard limits (402/429) are enforced fail-closed with respect to THEIR OWN state, but the
  advisory Redis counters backing them are fail-OPEN on Redis outage (availability over
  strictness; the Envoy edge 50/s limit remains the DDoS backstop) — amended 2026-06-11 at
  the rate-limits freeze, consistent with the v1 budget-counter precedent; soft signals
  never block the data path
- Sliding-window rate limiting lives in Redis Lua (atomicity); the Envoy edge limit stays as
  the outer DDoS backstop — two layers, different jobs
- Alert delivery is at-least-once via outbound webhook with bounded retry; alert events are
  also persisted (table alert_events) so a dead webhook never loses the signal
- New schema is ADDITIVE Alembic migrations with documented rollback (carried from v2)
- Every new admin surface goes through the BFF cookie session (no tokens in page JS — carried
  from v2 auth-bff)
- Limits hierarchy precedence (key < tenant < model-specific) resolves most-specific-wins;
  the GLOSSARY gains: budget_window, soft_budget, tpm/rpm_limit, model_allowlist, alert_event

## Shared / risky contracts (freeze these first)
- key-governance fields on /admin/keys (create/update) + enforcement error codes -> owning task key-governance
- Redis Lua sliding-window keys + 429 response shape (Retry-After) -> owning task rate-limits
- alert_events table + webhook payload schema -> owning task health-alerting
- spend query API response shape (/admin/spend?window=...) -> owning task spend-windows

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] key-governance    depends-on: none            — per-key hard/soft budget, expiry, model allowlist, rotation; enforcement in the proxy path (402/403)
- [x] rate-limits       depends-on: none            — Redis Lua sliding-window TPM/RPM per key/tenant/model; 429 + Retry-After; burst-tested
- [x] spend-windows     depends-on: key-governance  — rolling budget windows, soft-budget alert events, GET /admin/spend query API reconciled to the ledger
- [x] health-alerting   depends-on: none            — model/upstream health checks + alert_events + webhook delivery (budget/breaker/drain/health event types)
- [x] model-mgmt        depends-on: none            — runtime per-tenant model enable/disable + access groups over the synced catalog, no restart
- [x] dashboard-govern  depends-on: key-governance, spend-windows — dashboard surfaces: key budgets/allowlists editor + spend window views via BFF

## Exit criteria (observable; map each to the task that delivers it)
- [x] A key with a hard budget streams completions until the window spend crosses the cap, then receives 402 ERR_BUDGET_EXCEEDED — verified live through TLS-Envoy on the free model  (← key-governance, spend-windows)
- [x] A key restricted to model allowlist M gets 403 ERR_MODEL_NOT_ALLOWED for any other model; rotation invalidates the old secret within one request  (← key-governance)
- [x] A burst exceeding the key's RPM limit receives 429 with Retry-After while a sibling key is unaffected; counters verified against the Lua window  (← rate-limits)
- [x] Crossing a soft budget threshold persists an alert_event and delivers a webhook (received by a test sink) WITHOUT blocking the request  (← spend-windows, health-alerting)
- [x] GET /admin/spend returns windowed aggregates that reconcile exactly with usage_records for the same window  (← spend-windows)
- [x] Disabling a model for a tenant takes effect on the next request without gateway restart; breaker-open and drain-timeout each produce a delivered alert  (← model-mgmt, health-alerting)
- [x] Dashboard can edit key budgets/allowlists and display spend windows through the BFF session  (← dashboard-govern)
- [x] make ci green throughout; every task gated through the full ADD cycle at production depth  (← all)

## Milestone close — live verification record (2026-06-11)
All six exit criteria verified LIVE through the Envoy TLS edge (https://localhost:8443)
against real OpenRouter on the free model: scripts/live_v3_verify.py — 13/13 PASS
(commit dde80b3). Dispositions disclosed:
- C1: the free model spends $0, so the hard cap was set to 0.00 — the >= comparator makes
  the next request cross the cap; the full live path (Redis counter read -> 402 through TLS)
  is exercised; priced crossing dynamics are integration-tested with priced fixtures.
- C6 alert delivery: breaker-open + drain-timeout rows were inserted via psql and delivered
  by the LIVE dispatcher to the test sink; the emission paths for both event types are
  integration-tested (tests/health_alerting), since tripping a real breaker against the
  live upstream or forcing a real drain timeout would require degrading the shared upstream.
