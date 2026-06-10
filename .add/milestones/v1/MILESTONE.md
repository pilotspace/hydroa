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
- [ ] tenant-identity     depends-on: none                          — signup (tenant+owner atomic), login → JWT, roles
- [ ] model-catalog       depends-on: none                          — OpenRouter catalog sync, pricing snapshots, marked-up /v1/models
- [ ] api-keys            depends-on: tenant-identity               — key issue/revoke (shown once, argon2), /internal/authz for Envoy
- [ ] proxy-completions   depends-on: api-keys,model-catalog        — /v1/chat/completions SSE pass-through; timeout/retry/circuit-breaker
- [ ] usage-metering      depends-on: proxy-completions             — usage capture incl. streaming, Redis write-behind → ledger, marked-up cost
- [ ] budgets             depends-on: usage-metering                — tenant monthly ceiling, Redis spend counter, ERR_BUDGET_EXCEEDED
- [ ] edge-envoy          depends-on: api-keys                      — envoy.yaml (TLS, jwt_authn, ext_authz, rate limit) + docker-compose stack
- [ ] dashboard-shell     depends-on: tenant-identity,api-keys      — Next.js app: signup/login, key management
- [ ] dashboard-usage     depends-on: usage-metering,budgets        — catalog, usage & cost analytics, budget setting

## Exit criteria (observable; map each to the task that delivers it)
- [ ] User can sign up (tenant + owner created atomically) and log in, receiving a JWT        (← tenant-identity)
- [ ] Owner can create and revoke an API key; the secret is displayed exactly once           (← api-keys)
- [ ] `curl` with a valid key through Envoy streams a chat completion from any catalog model (← proxy-completions, edge-envoy)
- [ ] /v1/models lists the synced catalog with the tenant's marked-up prices                 (← model-catalog)
- [ ] Every proxied request produces exactly one ledger row with cost = upstream × (1+markup)(← usage-metering)
- [ ] A request beyond the tenant's monthly budget is rejected with ERR_BUDGET_EXCEEDED      (← budgets)
- [ ] Owner signs up, manages keys in the dashboard UI                                       (← dashboard-shell)
- [ ] Owner sees usage/cost totals + per-request list and sets the budget in the UI          (← dashboard-usage)
