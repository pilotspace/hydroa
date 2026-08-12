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
- [x] teams-core        depends-on: none        — teams within a tenant: CRUD + membership + roles + key→team attribution (/admin/teams, additive migration)
- [x] team-governance   depends-on: teams-core  — team budget/limit enforcement on the proxy path (key<team<tenant precedence) + team spend rollup in /admin/spend
- [x] response-caching  depends-on: none        — opt-in exact-match Redis response cache: TTL, bypass header, cost-0 usage marker, hit/miss metrics
- [x] guardrails-core   depends-on: none        — pre/post-call guardrail framework + prompt-injection heuristic + PII detect/mask, per-tenant config
- [x] sso-oidc          depends-on: none        — OIDC dashboard login through the BFF (server-side code exchange, claim mapping, auto-provisioned member)
- [x] obs-callbacks     depends-on: none        — OpenTelemetry trace export for the completion path (OTLP, best-effort, bounded queue)

## Exit criteria (observable; map each to the task that delivers it)
- [x] A key attributed to a team whose team budget is capped receives 402 ERR_BUDGET_EXCEEDED while an un-teamed sibling key in the same tenant proceeds — verified live through TLS-Envoy on the free model  (← teams-core, team-governance)
- [x] /admin/spend rolls up by team (group_by=team_id) and reconciles exactly with usage_records  (← team-governance)
- [x] With caching enabled for a key, an identical completion is served from cache (upstream called exactly once across two requests, response marked cached, usage row records cost 0); a bypass header forces the upstream — verified live  (← response-caching)
- [x] A prompt-injection payload is blocked pre-upstream with the contracted 4xx guardrail code in BLOCK mode; in MASK mode a PII-bearing prompt reaches the upstream with the PII redacted  (← guardrails-core)
- [x] A dashboard login completes via an OIDC test IdP in the e2e stack — BFF session established, no password, no tokens in page JS  (← sso-oidc)
- [x] A live completion produces an exported OTel trace (visible at an OTLP collector in the e2e stack) carrying tenant/key/model/status attributes, with zero added request failures when the collector is down  (← obs-callbacks)
- [x] make ci green throughout; every task gated through the full ADD cycle at production depth  (← all)


## Close record (2026-06-11, approved by Tin Dang — delegated auto mode)

All 6 tasks gated PASS through the full ADD cycle at production depth; root
`make ci` green throughout (final: 326 passed, coverage 80.27%).

Live verification through TLS-Envoy on the free model
(scripts/live_v4_verify.py + infra/docker-compose.e2e.v4.yml; embedded OTLP
sink :4318, webhook sink :9909, HS256 test OIDC IdP :9910):
- C1 teamed key at 0.00 team budget → 402 ERR_BUDGET_EXCEEDED; un-teamed
  sibling in the same tenant → 200.
- C2 /admin/spend totals reconcile exactly with usage_records (api == db);
  group_by=team_id breakdown returned.
- C3 identical completion: miss → hit (X-Cache), upstream called exactly once
  (1 miss + 1 cached=true cost-0 row); Cache-Control: no-cache → bypass.
- C4 injection payload in BLOCK mode → 400 ERR_GUARDRAIL_BLOCKED pre-upstream;
  PII prompt in MASK mode → 200 with raw.pii_masked=true marker. NOTE: the
  marker was a PRODUCT DEFECT found BY this live run (mask-path success
  records skipped the marker — frozen suite never asserted it); fixed and
  re-verified live (commit "fix(proxy): record pii_masked marker...").
- C5 OIDC browser-flow against the test IdP: /auth/oidc/login 302 with
  state/nonce cookies → callback 302 minting ai_proxy_session (httpOnly), no
  tokens in any response body, member user row with the !sso-no-password
  sentinel. Re-run note: a second run with the same e2e email correctly hit
  ERR_OIDC_TENANT_CONFLICT (cross-tenant guard working live) — harness
  isolation footnote, not a defect.
- C6 completion → OTLP sink received proxy.completion span with
  ai_proxy.tenant_id/key_id/model/status_code; with the collector DOWN the
  next completion still returned 200 (zero added failures).

Notable dispositions during the milestone (full detail in each TASK.md §6):
response-caching S12 fake-green override REJECTED + frozen-arrange
disposition; guardrails S11 frozen payload matched no frozen pattern
(contract-loosening rejected, arrange disposition); sso-oidc ID-token
signature replaced by TLS-channel validation per OIDC Core §3.1.3.7(6) with
pinned preconditions (v5: cryptography + RS256 JWKS); obs-callbacks builder
root-conftest bridge relocated + error_code plumbed into OTLP status.message;
UsageRecordExtras TypedDict capability seam replaced inspect.signature
introspection (requested by Tin Dang mid-milestone).
