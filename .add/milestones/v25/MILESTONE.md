# MILESTONE: Tenant-managed provider credentials (BYOK)

goal: a tenant configures its own provider API keys in tenant settings, and every upstream LLM call authenticates with that tenant's keys — resolved per request, encrypted at rest — fully replacing the platform's system-env provider keys.

rationale: new-major (v25; project-lead/auto, 2026-06-16, Tin confirmed at intake). A new product pillar — per-tenant provider credentials (BYOK) — that no active milestone's goal covers. **SUPERSEDES** the locked 2026-06-10 Key Decision *"Commercial model: platform OpenRouter key + per-tenant markup"* (full-replace chosen by Tin at intake): the platform holds **no** default provider key; a tenant **must** configure its own keys to call any provider; the platform no longer fronts upstream cost, so markup decouples from cost-plus. The decision is recorded as a Key-Decision SUPERSESSION at v25 fold (the frozen row stays untouched). Reuses the existing Fernet reversible-encryption pattern (`oidc_provider_configs.client_secret_enc`) and the dynamic-credential seams already proven for Azure AAD + Bedrock SigV4. The UI↔BE coverage audit Tin requested alongside is sized as the separate follow-on line **v26** (roadmap stub on disk, opened after v25 closes).

stage: production · status: active · created: 2026-06-16

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
 (1) **Credential store** — a `tenant_provider_keys` table, Fernet-encrypted per `(tenant_id, provider)`
     at rest (BYTEA), mirroring `oidc_provider_configs`; credential value-objects per auth shape
     (Bearer string · AwsCredentials triple · Azure config / AzureAD config).
 (2) **Per-request resolution seam** — a `TenantCredentialResolver` port + request-scoped `tenant_id`
     contextvar + in-memory TTL cache; every adapter's auth derives the credential from
     `(tenant, provider)` AT CALL TIME instead of a boot-bound `self._api_key`. Designed-for-failure:
     bounded DB timeout, TTL cache (warm cache = no per-request DB hit), **fail-CLOSED**
     `ERR_PROVIDER_KEY_MISSING` when a tenant has no key, decrypt errors raised `from None`
     (no ciphertext / encryption key reachable on the exception chain — extends the v22 secret-chain floor).
 (3) **All 6 providers**: OpenRouter / OpenAI / Anthropic / Gemini (Bearer), Bedrock (SigV4),
     Azure (AAD + static api-key).
 (4) **Control plane + UI** — `/admin/providers` CRUD (list+status / set+rotate / delete; write-only
     secret, masked on read) + a dashboard Providers surface.
 (5) **Removal of the system-env provider-key path** — the `GATEWAY_*_API_KEY` Settings fields, the
     `validate_upstream_keys` boot guard, and env-gated adapter registration become tenant-resolved
     (providers are always wired; gated per-tenant at request time).
 (6) **Live double-pass** through the real edge: a tenant's configured key authenticates all 6
     providers; a tenant without a key fails closed.

Out:
 - Markup / billing **redesign**. Interim (Tin-confirmed): **Cost stays recorded from the pricing
   snapshot as upstream-list-price VISIBILITY** (informational; markup → decoupled, no cost-plus).
   The platform-fee commercial model is a separate later milestone.
 - Per-*key* (vs per-tenant) provider credentials.
 - Key rotation/versioning, KMS, envelope encryption (inherits the existing single-static-Fernet-key limitation).
 - Bring-your-own base-URL / self-hosted provider endpoints.
 - The other UI↔BE coverage gaps (alerts viewer, routing-write, catalog-sync, SSO login button,
   health view, rate-limit view) → **v26**.
 - Data migration of existing env keys into tenant rows (no production tenants yet — clean cutover).

## Shared decisions & glossary deltas   (living — every task must honor these)
- **BYOK** (new glossary term): a tenant's own upstream provider credential, stored Fernet-encrypted
  per `(tenant, provider)`, resolved per request — replaces the platform system key. Amend GLOSSARY:
  `Upstream` (no longer "the platform's single LLM provider"), `Markup` (decoupled from cost-plus),
  and add `Provider credential` + `ERR_PROVIDER_KEY_MISSING`.
- **Fail-CLOSED is the floor**: a request whose served-model provider has no configured tenant
  credential is rejected `ERR_PROVIDER_KEY_MISSING` (4xx). There is **no** platform-key fallback
  (full replace). A neutral-default fail-OPEN would be a correctness/security hole, not an availability win.
- **Secret hygiene**: provider secrets are write-only over the API (masked on read, like OIDC
  `client_secret`), Fernet-encrypted at rest (BYTEA), and decrypt / transport errors raise `from None`
  (the v22 project-wide secret-chain floor extends to the decrypt path — assert `__cause__ is None`).
- **Additive seam, not a signature change**: the credential reaches the adapter via the request-scoped
  contextvar + resolver port. The FROZEN ChatTranslator (v9) and UpstreamProvider (v7) contracts are NOT
  re-shaped (additive-capability pattern, per v8 `aorder` / v4 typed-extras seam).
- **Cost = upstream-list-price visibility** this milestone: the ledger stays append-only and accurate
  for the tenant's own observability; markup is decoupled (Key-Decision SUPERSESSION recorded at fold,
  frozen rows untouched).
- **Designed-for-failure on the resolver IO** (CLAUDE.md IO rule): bounded `asyncio` timeout on the
  DB fetch, in-memory TTL cache so a warm path makes zero DB calls, fail-closed on miss/error.

## Shared / risky contracts (freeze these first)
- `tenant_provider_keys` schema + the per-provider credential value-object shapes  →  owning task `provider-credential-store`.
- The `TenantCredentialResolver` port + fail-closed contract + contextvar seam + cache/timeout policy  →  owning task `credential-resolution-seam`.
- The `ERR_PROVIDER_KEY_MISSING` error-catalog entry (status + code literal)  →  owning task `credential-resolution-seam`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] provider-credential-store     depends-on: none — Alembic migration `tenant_provider_keys` (PK `(tenant_id, provider)`; `secret_enc BYTEA`, optional `extra_enc`/JSON for SigV4/Azure multi-field creds, `enabled`, timestamps); Fernet encrypt/decrypt repository reusing the OIDC pattern (likely a distinct `GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY`); per-provider credential value-objects (Bearer / AwsCredentials / AzureConfig+AzureADConfig). **FREEZE the schema + credential shapes.**
- [ ] credential-resolution-seam    depends-on: provider-credential-store — `TenantCredentialResolver` port + in-memory TTL cache + request-scoped `tenant_id` contextvar; `ERR_PROVIDER_KEY_MISSING` (fail-closed, no fallback); convert the 4 Bearer adapters (openrouter/openai/anthropic/gemini, chat + non-chat) to resolver-driven async auth; **remove `validate_upstream_keys` + env-gated registration**. Designed-for-failure on the resolver IO (timeout + cache + `from None` decrypt).
- [ ] dynamic-auth-byok             depends-on: credential-resolution-seam — per-tenant Bedrock SigV4 (AwsCredentials from store, sign per request) + Azure (per-tenant `AzureConfig`/`AzureADConfig`; AAD token cache keyed by tenant). The two non-Bearer / dynamic seams.
- [ ] provider-config-admin-api     depends-on: provider-credential-store — `/admin/providers` GET (list configured providers + status) · PUT (set/rotate, write-only secret) · DELETE; tenant-scoped; masked-on-read; mirrors `oidc_admin_router`.
- [ ] provider-config-ui            depends-on: provider-config-admin-api — dashboard Providers surface (settings tab or `/providers`): list the 6 providers + configured/active status, per-provider write-only key entry, remove; BFF via the existing `/api/gw/[...path]` catch-all (no new route handler).
- [ ] byok-live-verify              depends-on: dynamic-auth-byok, provider-config-ui — live double-pass through the edge: a configured tenant key authenticates all 6 providers; an unconfigured provider → `ERR_PROVIDER_KEY_MISSING`; independent-oracle stubs per the v20/v21 pattern.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A tenant can store a provider key that is Fernet-encrypted at rest and is never returned in plaintext.   (← provider-credential-store + provider-config-admin-api · verifier: DB row BYTEA ciphertext ≠ plaintext; GET returns a masked `"<stored>"`) — MET: `tenant_provider_keys` BYTEA secret_enc/extra_enc (migrations d8f3a1c9e5b2+e2b7f4c9a1d8); `ProviderKeyStatus` carries no secret.
- [x] Every upstream call authenticates with the CALLING tenant's resolved key, per request, proven for all 6 providers.   (← credential-resolution-seam + dynamic-auth-byok + byok-live-verify · verifier: live double-pass 6/6 ×2 through the edge) — MET: live `scripts/live_v25_verify.py` **17/17 ×2** through Envoy (6 provider chats authenticate with the tenant's PUT key + 4 bearer-stub cross-checks); run_ids 1781671154/1781671170.
- [x] A tenant with no configured key for the served provider is rejected `ERR_PROVIDER_KEY_MISSING` with NO platform-key fallback.   (← credential-resolution-seam · verifier: fail-closed unit test + live unconfigured-provider call) — MET: byok_verify BV8 + live tenant-B → 402 ERR_PROVIDER_KEY_MISSING.
- [x] The system-env provider-key path is gone: no `GATEWAY_*_API_KEY` Settings field or `validate_upstream_keys` guard remains; the gateway boots with zero provider env keys.   (← credential-resolution-seam · verifier: grep `GATEWAY_*_API_KEY` → zero in src; boot test with no provider env) — MET (substantive): zero `GATEWAY_*_API_KEY` Settings fields; `_UPSTREAM_KEY_ENV_VARS=()` so `validate_upstream_keys` is now a VESTIGIAL NO-OP over an empty set (guards no provider key); gateway boots with zero provider env keys. Follow-up: delete the vestigial guard function (harmless residue).
- [x] An owner/admin configures, rotates, and removes provider keys from the dashboard Providers surface.   (← provider-config-ui · verifier: jsdom suite + BFF body-capture asserting masked-on-read + write-only) — MET: "Provider Keys" Settings tab; provider-keys suite 14/14; write-only secret, masked on read.
- [x] The resolver IO path is designed-for-failure: bounded timeout, TTL cache (no per-request DB hit on a warm cache), decrypt errors raise `from None`.   (← credential-resolution-seam · verifier: cache-hit test [N requests, 1 DB call], timeout test, `__cause__ is None` test) — MET: `CachedTenantCredentialResolver` (positive-only TTL + bounded asyncio.timeout, fail-closed); decrypt errors `from None`.

## Status: DONE — 7/7 tasks gated PASS (1-6 + openai-chat-complete); goal MET; live double-pass 17/17 ×2 (2026-06-17). Branch feat/v25-byok-provider-credentials @ 08e87cd.
