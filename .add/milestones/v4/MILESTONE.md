# MILESTONE: V4

goal: LiteLLM main-feature slice 2 — teams hierarchy with team-level governance, response caching, a guardrails framework with first guardrails, SSO/OIDC dashboard login, and OpenTelemetry trace export — all at production depth with live verification through the TLS edge
rationale: intake 2026-06-11 under the standing goal "production grade to full main features of LiteLLM" (Tin Dang, delegated auto mode). Scope confirmed by Tin Dang ("Draft + proceed", 2026-06-11): the Tier-1 remainder (teams/orgs hierarchy — XL, deferred from v3) plus the head of Tier 2 (response caching, guardrails core, SSO/OIDC, observability callbacks) from .add/research/litellm-feature-inventory-2026-06-11.md. Passthrough endpoints and secret managers stay out (passthrough needs its own upstream intake decision; secret managers are deployment-specific).
stage: production · status: active · created: 2026-06-11

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  teams within a tenant (CRUD, membership, roles, key→team attribution) · team-level
     budgets/limits enforced on the proxy path + team spend rollup in /admin/spend ·
     exact-match response caching (Redis) with TTL + per-key/tenant controls + bypass +
     hit/miss observability · guardrails framework (pre/post-call hooks) with first two
     guardrails (prompt-injection heuristic, PII detect/mask) configurable per tenant ·
     OIDC login for the dashboard (generic provider, e2e-verified against a test IdP)
     mapped into the existing BFF cookie session · OpenTelemetry trace export for the
     completion path (tenant/key/model/status attributes) to an OTLP collector
Out: semantic caching (v5; needs embeddings decision) · Presidio-based PII (python
     package weight; regex/heuristic first — revisit at v5 intake) · SCIM provisioning ·
     multi-provider routing/fallbacks + passthrough endpoints (own intake) · secret
     managers (deployment-specific) · Langfuse vendor callback (OTel is the contract;
     vendors consume OTLP) · MCP/policy/prompt-mgmt/RAG (Tier 3)

## Shared decisions & glossary deltas   (living — every task must honor these)
- Teams nest INSIDE tenants (tenant ≈ LiteLLM org): Tenant > Team > User/Key. No
  cross-tenant teams, ever. Tenant remains the isolation boundary; teams are a governance
  grouping within it.
- Budget precedence extends most-specific-wins: key < team < tenant. A key attributed to a
  team is checked against key budget first, then team, then tenant — same advisory Redis
  counter pattern (fail-OPEN on Redis outage, carried from v3 shared decision).
- Response cache is OPT-IN per key or tenant (default off) — billing-accuracy first: cached
  responses record usage with cost 0 and a cached=true marker in usage_records.raw; cache
  key = hash(tenant_id, model, canonicalized messages+params); streaming responses are NOT
  cached in v4 (replay fidelity risk — revisit v5).
- Guardrails run in the request path fail-CLOSED for BLOCK mode (a guardrail error blocks)
  and fail-OPEN for MASK/AUDIT modes (masking failure logs + passes through) — surfaced
  per-guardrail in config, frozen at the guardrails-core contract.
- OIDC maps to the EXISTING session: the BFF exchanges the IdP code server-side and mints
  the same ai_proxy_session cookie; no tokens in page JS (carried decision). First login
  auto-provisions a member-role user bound to a tenant by email-domain claim mapping;
  owner/admin roles are never auto-granted via SSO.
- OTel is additive observability: spans must never fail or slow a request (best-effort
  export, bounded queue); existing structlog + Prometheus surfaces stay authoritative.
- New schema stays ADDITIVE Alembic migrations with documented rollback (carried).
- GLOSSARY gains: team, team_budget_usd, cache_hit, guardrail, guardrail_mode
  (block|mask|audit), oidc_claim_mapping, otel_span.

## Shared / risky contracts (freeze these first)
- teams tables + /admin/teams API + key→team attribution field -> owning task teams-core
- team budget counter key + 402 semantics + spend rollup shape -> owning task team-governance
- cache key derivation + cached-response marker in usage_records -> owning task response-caching
- guardrail hook points + config schema + block error code -> owning task guardrails-core
- OIDC callback route + claim mapping + session minting -> owning task sso-oidc
- span names + attribute keys for the completion path -> owning task obs-callbacks

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] teams-core        depends-on: none        — teams within a tenant: CRUD + membership + roles + key→team attribution (/admin/teams, additive migration)
- [ ] team-governance   depends-on: teams-core  — team budget/limit enforcement on the proxy path (key<team<tenant precedence) + team spend rollup in /admin/spend
- [ ] response-caching  depends-on: none        — opt-in exact-match Redis response cache: TTL, bypass header, cost-0 usage marker, hit/miss metrics
- [ ] guardrails-core   depends-on: none        — pre/post-call guardrail framework + prompt-injection heuristic + PII detect/mask, per-tenant config
- [ ] sso-oidc          depends-on: none        — OIDC dashboard login through the BFF (server-side code exchange, claim mapping, auto-provisioned member)
- [ ] obs-callbacks     depends-on: none        — OpenTelemetry trace export for the completion path (OTLP, best-effort, bounded queue)

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A key attributed to a team whose team budget is capped receives 402 ERR_BUDGET_EXCEEDED while an un-teamed sibling key in the same tenant proceeds — verified live through TLS-Envoy on the free model  (← teams-core, team-governance)
- [ ] /admin/spend rolls up by team (group_by=team_id) and reconciles exactly with usage_records  (← team-governance)
- [ ] With caching enabled for a key, an identical completion is served from cache (upstream called exactly once across two requests, response marked cached, usage row records cost 0); a bypass header forces the upstream — verified live  (← response-caching)
- [ ] A prompt-injection payload is blocked pre-upstream with the contracted 4xx guardrail code in BLOCK mode; in MASK mode a PII-bearing prompt reaches the upstream with the PII redacted  (← guardrails-core)
- [ ] A dashboard login completes via an OIDC test IdP in the e2e stack — BFF session established, no password, no tokens in page JS  (← sso-oidc)
- [ ] A live completion produces an exported OTel trace (visible at an OTLP collector in the e2e stack) carrying tenant/key/model/status attributes, with zero added request failures when the collector is down  (← obs-callbacks)
- [ ] make ci green throughout; every task gated through the full ADD cycle at production depth  (← all)
