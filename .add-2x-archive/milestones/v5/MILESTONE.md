# MILESTONE: LiteLLM parity slice 3 — intelligence & hardening

goal: a tenant gets semantically-cached responses, hardened PII protection, cryptographic OIDC verification with per-tenant IdP config, and team-attributed historical usage — under the Hydroa name internally
rationale: new-major — the confirmed remainder of the LiteLLM feature-inventory Tier-2 (standing goal "production grade to full main features of litellm"); each item deepens a v4 surface rather than opening a new one, but the bundle is a versioned scope, not a task. Scoped per Tin Dang's "Draft + proceed" confirmation (delegated auto mode, 2026-06-11).
stage: production · status: active · created: 2026-06-11

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  semantic response caching (similarity layer over the v4 exact-match cache);
     PII detection v2 (expanded built-in types + per-tenant custom patterns);
     OIDC RS256/JWKS ID-token signature verification (cryptography/pyjwt to the
     allowlist — the v5 TODO pinned in sso-oidc §3); per-tenant OIDC IdP config
     (DB-backed, env mapping becomes the fallback); historical team attribution
     (team_id persisted on usage ledger rows + ledger-derived team rollups);
     internal ai-proxy → hydroa rename pass; live-harness isolation fix
     (per-run-unique e2e identities).
Out: passthrough endpoints (needs its own upstream intake — carried v4 decision);
     Presidio/NER-based PII (package weight: spacy models; regex-v2 first —
     revisit only if a task spec proves regex insufficient); embedding-model
     hosting of our own; SCIM/SAML; wire-breaking renames of public identifiers
     (cookie name `ai_proxy_session`, `gateway_*` metric names, `ai_proxy.*`
     span attribute keys) unless the rename task proves a safe migration.

## Shared decisions & glossary deltas   (living — every task must honor these)
- Semantic cache is a LAYER over the v4 exact-match cache, same opt-in surface and
  cached=true/cost-0 ledger semantics (GLOSSARY: cache_hit); similarity strategy is
  decided at the task spec — embedding-free heuristics are acceptable; any new
  package goes through `.add/dependencies.allowlist` + orchestrator review. A
  semantic hit must NEVER cross tenants; cache keys stay tenant-scoped.
- PII v2 keeps the v4 guardrail_mode semantics (block fail-CLOSED / mask fail-OPEN /
  audit) and the literal `[X_REDACTED]` placeholder convention; per-tenant custom
  patterns are validated at PUT time (re-compile, length/complexity caps — a
  tenant-supplied regex is untrusted input: ReDoS is a security surface).
- OIDC signature verification is DEFENSE-IN-DEPTH on top of the v4 TLS-channel
  sanction (OIDC Core 1.0 §3.1.3.7(6)) — the pinned preconditions in sso-oidc §3
  stay in force; JWKS fetch gets timeout + bounded retry + cache with kid-miss
  refresh; verification failure is `ERR_OIDC_TOKEN_INVALID` 401, fail-CLOSED.
- Per-tenant OIDC config is tenant-owned data (tenant_id-scoped, additive
  migration); `client_secret` is encrypted at rest or write-only — NEVER returned
  by GET, NEVER logged (carried HARD-STOP).
- Team attribution: typed-extras seam already carries team_id to the recorder
  (foundation v5 seam rule); the ledger gains a nullable `team_id` column via
  additive Alembic migration with documented rollback; existing rows stay NULL
  (no invented history) — "historical" starts at deploy.
- Rename pass: internal identifiers only (pyproject name, compose project, docs,
  README, dashboard branding); every wire-visible identifier kept or migrated
  with an explicit compat note in the contract.
- GLOSSARY gains: semantic_cache_hit, pii_pattern (built-in vs custom),
  jwks (kid, key rotation), oidc_provider_config, team attribution (ledger).

## Shared / risky contracts (freeze these first)
- usage_records.team_id column + ledger-derived team rollup shape -> owning task team-attribution
- tenants OIDC provider-config table + admin API shape -> owning task oidc-tenant-config
- JWKS verification failure semantics (`ERR_OIDC_TOKEN_INVALID`, fail-CLOSED) -> owning task oidc-jwks
- guardrail_configs JSONB extension for custom PII patterns -> owning task pii-v2
- semantic-cache key/similarity contract (tenant-scoped, threshold, opt-in flag) -> owning task semantic-cache

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] oidc-jwks         depends-on: none           — RS256/JWKS ID-token signature verification (cryptography to allowlist); live-harness IdP grows a JWKS endpoint
- [x] team-attribution  depends-on: none           — persist team_id on usage ledger rows; ledger-derived per-team historical rollups
- [x] pii-v2            depends-on: none           — expanded built-in PII types + validated per-tenant custom patterns
- [x] semantic-cache    depends-on: none           — similarity layer over the exact-match response cache, tenant-scoped, opt-in
- [x] oidc-tenant-config depends-on: oidc-jwks     — DB-backed per-tenant IdP config (issuer/client/secret/domain mapping), env config as fallback
- [x] rename-hydroa     depends-on: all-of-above   — internal ai-proxy → hydroa rename pass + live-harness isolation fix (unique per-run e2e identities)

## Exit criteria (observable; map each to the task that delivers it)
- [x] An OIDC callback presenting an ID token with an invalid signature is rejected 401 `ERR_OIDC_TOKEN_INVALID`; a valid RS256 token verified against the IdP JWKS logs in — proven live through the TLS edge (← oidc-jwks)
- [x] A teamed key's proxied request writes a ledger row carrying its team_id, and a ledger-derived per-team rollup reconciles with the Redis spend counters (← team-attribution)
- [x] A v2 built-in PII type AND a tenant-defined custom pattern are masked live with their literal placeholders; an invalid/dangerous custom regex is rejected 422 at PUT (← pii-v2)
- [x] A semantically similar but non-identical prompt is served from cache with cached=true and cost 0, tenant-scoped (a second tenant with the same prompt misses) (← semantic-cache)
- [x] Two tenants authenticate via two DIFFERENT IdP configs in one deployment; GET of the config never returns the client_secret (← oidc-tenant-config)
- [x] Internal naming reads hydroa (pyproject, compose, docs, README, dashboard title); full suite + `make ci` green; zero wire-visible identifiers changed without a compat note; live harness re-runs clean twice in a row (isolation fix) (← rename-hydroa)

## Close record (2026-06-12)
All 6 exit criteria verified LIVE through the TLS edge (https://localhost:8443, Envoy,
hydroa-e2e stack, 3 overlays) by scripts/live_v5_verify.py — TWO consecutive clean
passes (24/24 each, run_id 1781222733 then 1781222773 against the same long-lived
stack: per-run identity isolation proven, closing the rename-hydroa §6 deferred check).
Evidence highlights: forged RS256 token → 401 ERR_OIDC_TOKEN_INVALID + valid token
verified against the IdP JWKS; teamed ledger row + ledger/counter reconcile; IBAN
built-in + tenant custom pattern masked live + dangerous regex 422; normalization
variant → X-Cache: semantic_hit with cost 0 + second tenant miss; two tenants on two
DIFFERENT IdPs (distinct RSA keys/kids/issuers) in one deployment, client_secret never
returned; containers named hydroa-e2e-*, make ci green (399 tests).
Live verification additionally surfaced and fixed two production defects in
oidc-tenant-config wiring (see its TASK.md §7 defect record + fix(auth) commit) —
the close was held until both fixes were re-proven live.
