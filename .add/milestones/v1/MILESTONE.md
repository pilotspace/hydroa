# MILESTONE: MVP: metered multi-tenant AI proxy

goal: a tenant owner can sign up, issue an API key, call any OpenRouter model through the OpenAI-compatible proxy, and see every request's billable cost — with budget enforcement
rationale: intake 2026-06-10 classified "setup project scope and roadmap" as new-major — first product theme, no prior milestone; v1 delivers the whole MVP goal. Roadmap beyond v1 named but not created (v2 production hardening · v3 enterprise), per one-milestone-at-a-time versioning. Confirmed by Tin Dang.
stage: mvp · status: active · created: 2026-06-10

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  self-serve signup + JWT login · API keys (issue/revoke, argon2-hashed, shown once) ·
     model catalog with per-tenant marked-up prices · streaming + non-streaming
     /v1/chat/completions pass-through to OpenRouter · append-only usage ledger
     (Redis write-behind) · tenant monthly budgets (near-real-time enforcement) ·
     Envoy edge (TLS, jwt_authn, ext_authz, rate limit) + local docker-compose stack ·
     dashboard (signup/login, key management, catalog, usage & cost analytics, budget setting)
Out: email verification · SSO/OIDC · per-key budgets · per-tenant model allowlists ·
     BYOK / hybrid credentials · invoicing & export · multi-region · hard-cap budget
     escrow (near-real-time with small overage is the v1 semantic) · prompt
     logging/observability product features

## Shared decisions & glossary deltas   (living — every task must honor these)
- Every tenant-owned row carries `tenant_id`; every query is tenant-scoped (GLOSSARY: Tenant)
- Usage record ledger is append-only; raw upstream payload stored so Cost is always recomputable
- Cost = upstream cost × (1 + tenant Markup); prices come from the Pricing snapshot effective at request time
- Errors are machine-readable `ERR_<DOMAIN>_<REASON>` codes as RFC 9457 problem+json — never free text
- Every outbound IO: timeout + bounded jittered retry (idempotent only — never retry a completion) + circuit breaker on Upstream
- Budget checks are near-real-time (Redis spend counter, pre-flight); small in-flight overage is accepted
- API keys exist only as argon2 hashes at rest; plaintext shown exactly once at creation

## Shared / risky contracts (freeze these first)
- usage-record schema + cost semantics (incl. streaming usage capture) -> owning task usage-metering
- ext_authz request/response shape (Edge ↔ Gateway) -> owning task api-keys
- OpenAI-compatible proxy surface /v1/chat/completions + /v1/models -> owning task proxy-completions

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] tenant-identity     depends-on: none                          — signup (tenant+owner atomic), login → JWT, roles
- [x] model-catalog       depends-on: none                          — OpenRouter catalog sync, pricing snapshots, marked-up /v1/models
- [x] api-keys            depends-on: tenant-identity               — key issue/revoke (shown once, argon2), /internal/authz for Envoy
- [x] proxy-completions   depends-on: api-keys,model-catalog        — /v1/chat/completions SSE pass-through; timeout/retry/circuit-breaker
- [x] usage-metering      depends-on: proxy-completions             — usage capture incl. streaming, Redis write-behind → ledger, marked-up cost
- [x] budgets             depends-on: usage-metering                — tenant monthly ceiling, Redis spend counter, ERR_BUDGET_EXCEEDED
- [x] edge-envoy          depends-on: api-keys                      — envoy.yaml (TLS, jwt_authn, ext_authz, rate limit) + docker-compose stack
- [x] dashboard-shell     depends-on: tenant-identity,api-keys      — Next.js app: signup/login, key management
- [x] dashboard-usage     depends-on: usage-metering,budgets        — catalog, usage & cost analytics, budget setting

## Exit criteria (observable; map each to the task that delivers it)
- [x] User can sign up (tenant + owner created atomically) and log in, receiving a JWT        (← tenant-identity)
- [x] Owner can create and revoke an API key; the secret is displayed exactly once           (← api-keys)
- [x] `curl` with a valid key through Envoy streams a chat completion from any catalog model (← proxy-completions, edge-envoy)
      evidence is composite: e2e S8 proves the edge path (valid Bearer key passes Envoy
      ext_authz and reaches the proxy handler) and the frozen proxy tests prove byte-identical
      SSE streaming against the upstream port; a live OpenRouter smoke needs a real
      GATEWAY_OPENROUTER_API_KEY — recorded as v2 residue, not silently waived
- [x] /v1/models lists the synced catalog with the tenant's marked-up prices                 (← model-catalog)
- [x] Every proxied request produces exactly one ledger row with cost = upstream × (1+markup)(← usage-metering)
- [x] A request beyond the tenant's monthly budget is rejected with ERR_BUDGET_EXCEEDED      (← budgets)
- [x] Owner signs up, manages keys in the dashboard UI                                       (← dashboard-shell)
- [x] Owner sees usage/cost totals + per-request list and sets the budget in the UI          (← dashboard-usage)

## Wave log (append-only; integration-Verify records)

### Wave 1 — closed 2026-06-10
- base: 87353f13cd9fc382c4019d7ad8feec862d05dc7d
- roster: api-keys → wt-keys (fork 87353f1 == base ✓, opus) · model-catalog → wt-catalog (fork 87353f1 == base ✓, sonnet); autonomy auto, private test DBs (gateway_test_keys / gateway_test_catalog)
- merge order executed: api-keys → model-catalog; main.py router registration merged by orchestrator (only overlapping file)
- integration Verify: PASS — 49 tests green on merged tree, coverage 87.40%, ruff+mypy+allowlist clean, `make ci` exit 0
- residue: none; worker deviations: keys worker reformatted (whitespace-only) two test files — dropped at merge, canonical tests kept; catalog worker omitted SUMMARY.md — reconstructed by orchestrator from verdict
- gates: api-keys PASS · model-catalog PASS (auto-resolved, delegated auto mode)

### Wave 2 — closed 2026-06-10 (SEQUENTIAL fallback)
- planned parallel roster (proxy-completions · usage-metering front agents) aborted: all 3
  spawned front agents died (2 API socket errors, 1 watchdog stall) → circuit-breaker rule
  from streams.md applied: fell back to sequential execution on the main worktree
- proxy-completions: TASK.md salvaged from dead agent's disk state; red suite written by the
  orchestrator; build+verify sequential — gate PASS (11 tests; SSE byte-identical tee;
  circuit breaker 5-failures→30s)
- usage-metering: sequential front+build — gate PASS (9 tests; Redis Stream write-behind →
  append-only ledger; uuid5(stream-id) idempotent flush)
- budgets: sequential — gate PASS (9 tests; pre-flight BudgetGuard, 402 ERR_BUDGET_EXCEEDED,
  fail-open on Redis outage); orchestrator fixed a red-for-wrong-reason test bug
  (create_token → issue) before freeze
- integration Verify after each merge-equivalent commit: make ci exit 0 throughout

### Wave 3 — closed 2026-06-10 (sequential, final)
- edge-envoy: builder agent died mid-e2e-iteration after writing all artifacts; orchestrator
  diagnosed and fixed envoy.yaml (jwt_authn exemptions for /admin/auth/signup|login,
  ext_authz path_prefix semantics + additive gateway authz subpath route, ext_authz disable
  on the /internal direct-response route) — gate PASS (92 non-e2e green; 10/10 e2e against
  real Envoy v1.29; e2e_edge.sh exit 0)
- dashboard-shell: front+build agents green — gate PASS (19 vitest; CJS require interception
  solved additively in test-support/ without touching frozen tests)
- dashboard-usage: front+build agents (Fable) — gate PASS (35 vitest); verify review caught a
  semantic divergence (catalog row hid the model ID to dodge an RTL duplicate match) and fixed
  it in code before gating; two §7 deltas opened on UI red-suite assertion scoping
- integration Verify on final tree: make ci exit 0 · dashboard 35/35 + build + lint clean ·
  e2e edge suite 10/10
