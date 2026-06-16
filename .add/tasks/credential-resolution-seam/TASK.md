# TASK: Credential Resolution Seam

slug: credential-resolution-seam · created: 2026-06-16 · stage: production · risk: high
autonomy: conservative   <!-- LOWERED from auto: removes the platform system-env provider-key path and re-routes ALL upstream auth through per-request tenant secret resolution (fail-closed). Same security class as provider-credential-store (task 1). Per v21 fold, any auth/secret task's verify gate runs an INDEPENDENT adversarial security subagent + a human gate (risk:high+auto trips unguarded_high_risk_auto). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- **Where tenant_id is known** — `proxy/application/use_cases.py:406` `CompletionUseCase._authenticate()` → `result.tenant_id` (`keys/domain/entities.py:74` `AuthzResult.tenant_id: uuid.UUID`); today bound only to structlog ctx (`use_cases.py:421`), NOT a Python contextvar. The `authz` result threads through `complete()`/`stream()` as a local. THIS is where the seam resolves the credential (tenant_id known; provider resolved at dispatch).
- **Where provider is known** — `proxy/infrastructure/provider_aware_upstream.py:52` `provider = await self._resolver.provider_for(model)` (`catalog_provider_resolver.py:57` pure in-memory map, no DB on hot path; port `proxy/domain/ports.py:306` `ProviderResolver.provider_for(model_id)->str`). The dispatch wrapper does NOT see tenant_id today (⚠ key seam gap).
- **The 4 Bearer adapters this task converts** (auth injected per-request in `_auth_headers()`, key boot-bound in `__init__` as `self._api_key`; the httpx client carries NO boot auth — safe to convert):
  - `openrouter_upstream.py:66` `self._api_key=api_key` → `:84` `{"Authorization": f"Bearer {self._api_key}"}` (chat; non-chat facade `openrouter_upstream_provider.py` wraps same instance).
  - `openai_provider.py:59` `self._api_key` → `:75` `{"Authorization": f"Bearer {self._api_key}"}` (non-chat: embeddings/images/audio; OpenAI chat rides OpenRouter).
  - `anthropic_upstream.py:523` `self._api_key` → `:544` `{"x-api-key": self._api_key, "anthropic-version":…}` (chat).
  - `gemini_upstream.py:509` (`GeminiCompletionUpstream`, chat) + `:664` (`GoogleEmbeddingsProvider`, embeddings) → `{"x-goog-api-key": self._api_key}` (`:528`,`:679`). TWO separate instances, both convert.
- **Deferred to task 3 (dynamic-auth-byok), seam must ACCOMMODATE not convert here**: Bedrock SigV4 (`bedrock_upstream.py:437` `self._credentials: AwsCredentials` + `self._region` → `sign_request(...credentials=self._credentials,region=self._region)` `:490`; embeddings `bedrock_embeddings.py:111`) · Azure (`azure_upstream.py:67` `self._config: AzureConfig` + `self._token_provider: AzureADTokenProvider|None` → `_auth_headers` `:85` Bearer-from-AAD-cache OR static `api-key`; embeddings `azure_embeddings.py:66`). ⚠ Azure AAD token cache is boot-singleton shared chat+embeddings (`main.py:467`) — per-tenant cache is a task-3 problem, but the contextvar seam must not preclude it.
- **The env-key path to REMOVE** (milestone Scope item 5):
  - `core/config.py:41` `validate_upstream_keys(env)` (guard list `:29` `_UPSTREAM_KEY_ENV_VARS`); invoked unconditionally `main.py:182`.
  - env-gated registration `main.py:399-488` (`_chat_adapters`: openrouter always; `if settings.anthropic_api_key:` `:405`, `if settings.google_api_key:` `:420`, `if _aws_creds:` `:435`, `if _azure_cfg:` `:452`) + `main.py:578-610` (`_providers` registry, same gating).
  - Settings fields `core/config.py`: `openrouter_api_key:131` `openai_api_key:204` `anthropic_api_key:213` `google_api_key:226` `bedrock_access_key_id:237` `bedrock_secret_access_key:240` `bedrock_session_token:245` `azure_api_key:254` `azure_client_secret:270` (+ non-secret azure_endpoint/version/deployment_map/tenant_id/client_id, bedrock_region — these describe the upstream, not the SECRET; their disposition is a §3 decision).
- **Injection of task-1 store** — `proxy/domain/provider_credentials.py:241` `TenantProviderKeyStore(Protocol)` + `proxy/infrastructure/tenant_provider_key_store.py:190` `DbTenantProviderKeyStore(sessionmaker, settings)` exist but are NOT wired in main.py yet. Wire on `app.state` beside `main.py:495-505` (`usage_recorder`/`budget_guard` use `redis=…, session_factory=app.state.sessionmaker`). The `TenantProviderKeyRow` side-effect import already at `main.py:63`.

Context (working folder):
- GLOSSARY delta (milestone): add `Provider credential`, `ERR_PROVIDER_KEY_MISSING`; amend `Upstream`/`Markup`. Task-1 froze the value-objects (`BearerCredential`/`BedrockCredential`/`AzureCredential` + `ProviderCredential` union) + `.get()` returning `None` for absent-or-disabled.
- Port+fake pattern (recon Q7): define Protocol in `domain/ports.py`; wire real on `app.state.<name>` in `create_app()`; tests override `app.state.<name>` post-create with a structural fake (e.g. `app.state.budget_guard = PassthroughBudgetGuard()`, `tests/.../test_key_governance.py:599`).

Honors (patterns / conventions):
- **Additive seam, NOT a signature change** (milestone-locked): the credential reaches the adapter via a request-scoped `contextvar` set by the use-case; the FROZEN `CompletionUpstream`(v7)/`ChatTranslator`(v9)/`UpstreamProvider` Protocols are unchanged (v8 `aorder` / v4 typed-extras precedent).
- **Fail-CLOSED floor**: served-model provider with no configured tenant credential → reject `ERR_PROVIDER_KEY_MISSING` (4xx), NO platform-key fallback.
- **Designed-for-failure on resolver IO** (CLAUDE.md): bounded `asyncio` timeout on the DB `get`, in-memory TTL cache (warm path = zero DB hit), decrypt errors already raise `from None` in the store (v22 floor).
- Domain ports are `typing.Protocol` + `app.state` fakes (foundation v1, locked).

Anchors the contract cites:
- `TenantCredentialResolver(Protocol)` — `async resolve(tenant_id, provider) -> ProviderCredential` (raises `ProviderCredentialMissing`/`ERR_PROVIDER_KEY_MISSING` on miss); TTL-cache + bounded-timeout wrapper over `TenantProviderKeyStore.get`.
- The request-scoped contextvar carrying the resolved credential (name + payload type — **[contract flag]**: resolved `ProviderCredential` in the contextvar vs `tenant_id` only).
- `ERR_PROVIDER_KEY_MISSING` error-catalog entry (code literal + HTTP status).
- The 4 Bearer `_auth_headers()` conversions (read contextvar credential instead of `self._api_key`).
- Removal: `validate_upstream_keys` + env-gated registration + the 9 secret Settings fields.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-request tenant credential-resolution seam — a `TenantCredentialResolver` (TTL-cache + bounded-timeout wrapper over the task-1 store) + a request-scoped contextvar carrying the resolved credential, set by `CompletionUseCase` after auth and provider-resolution; the 4 **Bearer** adapters read it at call time instead of a boot-bound `self._api_key`. Fail-CLOSED `ERR_PROVIDER_KEY_MISSING` when the calling tenant has no enabled credential. Bedrock/Azure dynamic auth (SigV4 per-tenant region, AAD per-tenant token cache) is task 3 — this task BUILDS the seam they will plug into but does NOT convert them.

Framings weighed: request-scoped contextvar carries the RESOLVED `ProviderCredential`; the use-case resolves ONCE (tenant_id from authz × provider from the resolver) and sets it; adapters read it in `_auth_headers()` **(chosen — no FROZEN-Protocol signature change; adapters stay store-agnostic; one resolve per request)** · pass `tenant_id` into `complete()/stream()` (reshapes the FROZEN `CompletionUpstream` Protocol — rejected, milestone-locked) · contextvar carries `tenant_id`, each adapter calls the resolver itself (adapters depend on the resolver, N calls/request, harder to fake — rejected).

Must:
<must>
  - A request whose served-model provider has a CONFIGURED + ENABLED tenant credential authenticates the upstream call with THAT tenant's credential, resolved per request from `(tenant_id, provider)` — never a platform/shared key.
  - The credential reaches the adapter via a request-scoped contextvar SET by the use-case after auth + provider-resolution and RESET in a finally after the call — no credential leaks across requests or tasks (each request sets its own; a missing token reads as unset).
  - `TenantCredentialResolver.resolve(tenant_id, provider)` wraps `TenantProviderKeyStore.get` with an in-memory TTL cache (warm path makes ZERO DB calls within the TTL) and a bounded `asyncio` timeout on the cold DB fetch.
  - The 4 Bearer adapters read the contextvar credential's `secret` (openrouter chat + its non-chat facade · openai non-chat · anthropic chat · gemini chat + google embeddings); their `__init__` no longer requires `api_key`; registration is UNCONDITIONAL (per-tenant gating moves from boot to resolve time).
  - Remove the BEARER system-env path: the `openrouter/openai/anthropic/google` secret Settings fields, their `_UPSTREAM_KEY_ENV_VARS` boot-guard entries, and their env-gated registration. Bedrock/Azure env path STAYS (task 3 removes it) so their models keep working until converted.
  - Designed-for-failure: bounded timeout on the resolver DB fetch; a decrypt/corrupt error from the store surfaces as a fail-closed reject (the store already raises `from None`); a resolver timeout fails CLOSED (never a fallback key).
</must>
Reject:
<reject>
  - Served-model provider has no configured/enabled tenant credential (absent row OR `enabled=false` OR store returns None) -> "ERR_PROVIDER_KEY_MISSING" (HTTP 402, frozen at §3; NO platform-key fallback).
  - Resolver DB fetch exceeds the bounded timeout -> "ERR_PROVIDER_KEY_MISSING" (fail-closed; the timeout is an availability event, never an auth bypass).
  - An adapter's `_auth_headers()` runs with NO credential in the contextvar (a wiring bug / unset token) -> raise (never emit an unauthenticated or platform-keyed upstream call).
</reject>
After:
<after>
  - Every Bearer upstream call carries the calling tenant's resolved secret; the gateway boots with ZERO Bearer provider env keys; `validate_upstream_keys` no longer guards the 4 Bearer vars.
  - A warm-cache repeat request for the same `(tenant, provider)` issues no new DB query within the TTL.
  - No Bearer secret is ever logged, repr'd, or JSON-serialized (SecretStr end-to-end); the contextvar is cleared after each request.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ✅ CONFIRMED (Tin, 2026-06-16): STAGED env-path removal — task 2 removes ONLY the 4 Bearer providers' env path and leaves Bedrock/Azure on the env path until task 3 (keeps each task build-green + behavior-correct; no silent-misroute window via `provider_aware_upstream.py:53` `or self._adapters[self._default]`). The full env path is gone after task 3. Was the lowest-confidence fork; resolved at the pre-contract checkpoint.
  - [ ] Contextvar carries the resolved `ProviderCredential` (not `tenant_id`) — confirm: keeps adapters store-agnostic and is the cleanest read; the [contract flag] item.
  - [ ] `ERR_PROVIDER_KEY_MISSING` is a 4xx the caller can act on (configure a key), distinct from a 5xx upstream error — confirm the exact status at §3.
  - [ ] The contextvar is the right carrier vs `request.state` — confirm: the upstream adapters are reached deep below the request object (no `request` in `_auth_headers()`), so a `contextvars.ContextVar` is the only seam that reaches them without a signature change.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Bearer call authenticates with the calling tenant's resolved key
  Given tenant T has an enabled openrouter BearerCredential in the store
  And a request from T for an openrouter-served model
  When the use-case resolves the credential and the openrouter adapter builds auth headers
  Then the Authorization header is "Bearer <T's secret>" (resolved per request, not a boot key)
  And no GATEWAY_OPENROUTER_API_KEY is read anywhere in the path

Scenario: Resolved credential reaches the adapter via the request-scoped contextvar
  Given the use-case has resolved T's credential for the dispatched provider
  When the adapter's _auth_headers() runs deep below the request object
  Then it reads the credential from the contextvar (no signature change to CompletionUpstream)
  And the contextvar is reset after the request so it does not leak to the next request

Scenario: Warm cache serves a repeat (tenant, provider) without a DB hit
  Given T's openrouter credential was resolved once within the TTL window
  When a second request for the same (tenant, provider) resolves
  Then the resolver returns the cached credential
  And TenantProviderKeyStore.get is NOT called a second time within the TTL

Scenario: Unconfigured provider fails closed (no platform fallback)
  Given tenant T has NO enabled credential for the served provider (absent or enabled=false)
  When T sends a request for that provider's model
  Then the request is rejected "ERR_PROVIDER_KEY_MISSING" (4xx)
  And no upstream call is made and no platform/shared key is used

Scenario: Resolver DB timeout fails closed
  Given the credential store fetch exceeds the resolver's bounded timeout
  When T sends a request for an unwarmed (tenant, provider)
  Then the request is rejected "ERR_PROVIDER_KEY_MISSING" (fail-closed)
  And no upstream call is made (a timeout never falls back to a key)

Scenario: Missing contextvar credential never emits an unauthenticated call
  Given a wiring path reaches a Bearer adapter with the contextvar unset
  When _auth_headers() runs
  Then it raises (no header built)
  And no request is sent with an empty/absent or platform credential

Scenario: Bearer env path is gone; gateway boots with zero Bearer provider keys
  Given the environment defines none of the 4 Bearer GATEWAY_*_API_KEY vars
  When the gateway boots and registers adapters
  Then validate_upstream_keys does not guard the 4 Bearer vars and boot succeeds
  And the 4 Bearer adapters are registered unconditionally (gating moved to resolve time)

Scenario: Bedrock/Azure remain on the env path this task (staged)
  Given Bedrock/Azure env credentials are configured
  When a request for a Bedrock or Azure model is dispatched
  Then it still authenticates via the existing env-bound path (unchanged by task 2)
  And it does NOT silently misroute to the default openrouter adapter

Scenario: No secret is logged or serialized in the resolution path
  Given a credential is resolved and used for an upstream call
  When request logs and any error responses are produced
  Then the secret never appears (SecretStr masking end-to-end)
  And ERR_PROVIDER_KEY_MISSING responses carry no secret or ciphertext
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This task freezes a SEAM shape (port + contextvar + error + adapter behavior), not an HTTP path
(like task 1's schema+port; v6 cooldown / v8 deployment precedent).

```
# ── Port (proxy/domain/ports.py) ──────────────────────────────────────────
class TenantCredentialResolver(Protocol):
    async def resolve(self, tenant_id: UUID, provider: str) -> ProviderCredential: ...
        # returns the tenant's enabled credential for (tenant_id, provider);
        # raises ProviderKeyMissing on absent/disabled/None/timeout — NEVER a fallback.

# ── Error (proxy/domain/…) ────────────────────────────────────────────────
class ProviderKeyMissing(Exception):
    code = "ERR_PROVIDER_KEY_MISSING"          # HTTP 402 (BYOK provisioning gate; tenant must configure a key)
    # carries provider only — NEVER tenant secret / ciphertext / encryption key.

# ── Request-scoped contextvar (proxy/domain/credential_context.py) ─────────
# Domain module (the payload is a domain ProviderCredential) so BOTH the
# application use-case (sets) and infrastructure adapters (read) import it
# without a layering cycle.
current_provider_credential: ContextVar[ProviderCredential | None] = ContextVar(
    "current_provider_credential", default=None)
def set_provider_credential(cred: ProviderCredential) -> Token[...]: ...
def get_provider_credential() -> ProviderCredential | None: ...   # adapters call this
def reset_provider_credential(token) -> None: ...                  # finally-reset per request

# ── Resolution flow (shared helper resolve_provider_credential, use_cases.py) ─
#   ALL modalities resolve via the same module-level helper after auth + provider
#   resolution (single source of truth):
#     token = await resolve_provider_credential(resolver, tenant_id, provider)
#     try: <dispatch upstream call>            finally: reset_provider_credential(token)
#   chat   complete()/stream()  : provider from provider_resolver.provider_for(model_id)
#   non-chat embeddings/images/audio (transcription + speech) : provider from the catalog
#     ModelRow.provider; the use-case resolves+sets the contextvar around its upstream
#     call (streaming TTS keeps the contextvar live across the StreamingResponse boundary
#     and resets in a generator-wrapper finally). [AMENDED 2026-06-16 — see note below]
#   STAGED gate: resolve ONLY for BYOK_BEARER_PROVIDERS = {openrouter, openai, anthropic,
#     google} (provider_credentials.py). A non-listed provider (bedrock/azure) SKIPS
#     resolution (token=None) so its env-bound adapter still authenticates — NO 402 for a
#     still-env-bound provider. Task 3 extends the set to the full PROVIDER_VALUE_SET.
#   ProviderKeyMissing -> 402 {"error": {"code": "ERR_PROVIDER_KEY_MISSING", ...}}

# ── Bearer adapter _auth_headers() contract (the 4 converted adapters) ─────
#   cred = get_provider_credential()
#   if not isinstance(cred, BearerCredential): raise ProviderKeyMissing(provider)
#   header uses cred.secret.get_secret_value()  (Authorization: Bearer … | x-api-key | x-goog-api-key)
#   __init__ no longer takes/stores api_key; registration is UNCONDITIONAL.

# ── Resolver impl policy (CachedTenantCredentialResolver, infrastructure) ──
#   wraps TenantProviderKeyStore.get; in-memory per-process TTL cache keyed (tenant_id, provider);
#   POSITIVE results cached for provider_credential_cache_ttl_s (default 60.0) — a MISS is NOT
#   cached (a freshly-configured key takes effect immediately); bounded asyncio.timeout
#   provider_credential_resolve_timeout_s (default 2.0) on the cold store.get → timeout = fail-closed
#   ProviderKeyMissing. Staleness after upsert/delete bounded by TTL (no active cross-process
#   invalidation this task). Store decrypt errors already raise from None (v22 floor) → fail-closed.
```

Schema: NO new table. Reads tenant_provider_keys via the task-1 store (PK lookup). New Settings:
provider_credential_cache_ttl_s: float = 60.0 · provider_credential_resolve_timeout_s: float = 2.0.
REMOVES (Bearer only this task): Settings openrouter_api_key/openai_api_key/anthropic_api_key/
google_api_key; their 4 entries in _UPSTREAM_KEY_ENV_VARS; their env-gated registration (now
unconditional). Bedrock/Azure Settings + env path UNCHANGED (task 3). Wire app.state.
tenant_provider_key_store (DbTenantProviderKeyStore) + app.state.tenant_credential_resolver
(CachedTenantCredentialResolver) in create_app; tests override both post-create with fakes.

Least-sure flag surfaced at freeze: [contract] the HTTP status for ERR_PROVIDER_KEY_MISSING —
RESOLVED at freeze to 402 Payment-Required (Tin, 2026-06-16): frames BYOK as a provisioning/payment
gate, sharing the status with the per-key-budget 402 while the distinct .code disambiguates (403 was
the drafted alternative). Second-least-sure: [contract] not negative-caching misses trades a
per-request PK lookup for an unconfigured tenant against immediate new-key visibility — chosen for
correctness; if miss-traffic becomes a DB-load concern, add a short negative-TTL later (additive).

Status: FROZEN @ v2 — approved by Tin Dang, 2026-06-16 (delegated auto mode + verify-gate decision).
  v1 (status → 402 chosen at the freeze gate) — approved Tin Dang 2026-06-16.
  v2 AMENDMENT (change-request from the verify gate, 2026-06-16): the v1 §3 resolution-flow
  named ONLY the chat use-case (complete()/stream()) while §0/§1-Must required the non-chat
  Bearer adapters (openai non-chat, google embeddings, the openrouter facade) to read the
  contextvar — an internal inconsistency that left embeddings/images/audio fail-closed (402)
  in production. v2 completes the seam: the non-chat use-cases set the contextvar too (shared
  helper resolve_provider_credential), and a STAGED gate (BYOK_BEARER_PROVIDERS) skips
  Bedrock/Azure so they keep authenticating env-bound until task 3 (fixes a second latent gap:
  v1 would have 402'd Bedrock/Azure chat). Tin chose "complete non-chat now" at the gate
  (the full-replace-BYOK invariant forbids leaving non-chat on platform env keys). ADDITIVE —
  no v1 rule weakened; tightens correctness toward §1-Must + the milestone invariant.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (seam-critical; mirrors task 1)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_bearer_resolves_per_tenant_key: fake store has T's openrouter key / resolve+adapter build headers / Authorization == "Bearer <T secret>"; no env var read
  - test_credential_reaches_adapter_via_contextvar: set credential in ctx / call _auth_headers deep / header built from ctx; CompletionUpstream signature unchanged (introspect)
  - test_warm_cache_no_second_db_hit: resolve twice within TTL / counting fake store / store.get called exactly once
  - test_unconfigured_fails_closed: store returns None / resolve / raises ProviderKeyMissing(code==ERR_PROVIDER_KEY_MISSING); no upstream call
  - test_disabled_fails_closed: store has enabled=false row (get→None) / resolve / ProviderKeyMissing
  - test_timeout_fails_closed: store.get sleeps past timeout / resolve / ProviderKeyMissing; never returns a credential
  - test_missing_contextvar_raises: ctx unset / _auth_headers / raises (no header, no empty/platform key)
  - test_bearer_env_removed_boots_clean: no Bearer env vars / create_app / boots; validate_upstream_keys ignores the 4 Bearer vars; 4 Bearer adapters registered
  - test_bedrock_azure_unchanged_staged: Bedrock/Azure env configured / dispatch a bedrock model / still env-auth, NOT misrouted to openrouter default
  - test_no_secret_in_logs_or_error: resolve+use / secret absent from structlog capture; ERR_PROVIDER_KEY_MISSING body carries no secret/ciphertext
  - test_error_maps_to_402: ProviderKeyMissing surfaces as HTTP 402 with code ERR_PROVIDER_KEY_MISSING (frozen status)
</test_plan>

Tests live in: `./tests/credential_resolution_seam/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/domain/ports.py` `apps/gateway/src/gateway/proxy/domain/credential_context.py` `apps/gateway/src/gateway/proxy/domain/provider_credentials.py` `apps/gateway/src/gateway/proxy/infrastructure/cached_tenant_credential_resolver.py` `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py` `apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py` `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py` `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/infrastructure/provider_aware_upstream.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/anthropic_provider/` `apps/gateway/tests/gemini_provider/` `apps/gateway/tests/gemini_embed_tokens/` `apps/gateway/tests/secret_chain_hardening/` `apps/gateway/tests/upstream_base_url/` `apps/gateway/tests/provider_seam/` `apps/gateway/tests/routing_admin/` `apps/gateway/tests/empty_key_boot_guard/` `apps/gateway/tests/azure_chat/` `apps/gateway/tests/azure_auth_routing/` `apps/gateway/tests/azure_aad/` `apps/gateway/tests/azure_embeddings/` `apps/gateway/src/gateway/proxy/application/embeddings_use_case.py` `apps/gateway/src/gateway/proxy/application/images_use_case.py` `apps/gateway/src/gateway/proxy/application/audio_use_case.py` `apps/gateway/src/gateway/proxy/api/` `apps/gateway/src/gateway/keys/domain/services.py` `apps/gateway/tests/conftest.py` `apps/gateway/tests/retry_policy/` `apps/gateway/tests/credential_resolution_nonchat/` `apps/gateway/tests/credential_stub.py` `apps/gateway/tests/guardrails/` `apps/gateway/tests/obs_callbacks/` `apps/gateway/tests/pii_v2/` `apps/gateway/tests/response_caching/` `apps/gateway/tests/semantic_cache/`
<!-- BLAST-RADIUS scope (12 existing test dirs, pre-declared from a build-time grep): removing the 4 Bearer secret Settings + dropping `api_key` from the 4 Bearer adapter __init__s + trimming `validate_upstream_keys` BREAKS existing tests that (a) construct a Bearer adapter with `api_key=` [gemini_embed_tokens, secret_chain_hardening, upstream_base_url, gemini_provider], (b) reference a removed Bearer Settings field [anthropic_provider, provider_seam, routing_admin, gemini_provider], or (c) assert `validate_upstream_keys`/Bearer-env behavior [empty_key_boot_guard, azure_chat, azure_auth_routing, azure_aad, azure_embeddings]. These are CONVERSION updates (set the contextvar / drop the removed field), NOT weakenings — the FROZEN credential_resolution_seam bundle is the only tripwire'd test set and is NEVER edited by the build. NO Bedrock/Azure src touched (staged → task 3); the azure TEST dirs are in scope only for env/guard-reference fixups. -->
<!-- NOTE the new tests/credential_resolution_seam/ is the FROZEN red bundle (tripwire) — the build makes it green by writing SRC, never by editing it; hence it is NOT a 'may touch' build target. -->
<!-- DRAFT scope finalized at the tests->build crossing below. -->
<!-- DRAFT scope (finalize before the tests->build re-snapshot — that crossing freezes the anchor). ProviderKeyMissing lives in provider_credentials.py (beside ProviderCredentialError); the contextvar + helpers in the NEW credential_context.py; the resolver Protocol in ports.py; the impl in the NEW cached_tenant_credential_resolver.py. use_cases.py: resolve + set/reset ctx + map ProviderKeyMissing→ProblemError(402) (shares the per-key-budget 402 status; .code disambiguates). config.py: +2 cache/timeout Settings, −4 Bearer secret fields + their _UPSTREAM_KEY_ENV_VARS entries. main.py: wire app.state.tenant_provider_key_store + tenant_credential_resolver, unconditional Bearer registration. NO Bedrock/Azure adapter or Settings touched (staged → task 3). If tests need a manifest/sibling edit (task-1 pattern), declare it here + re-snapshot. -->
Strategy (ordered batches): 1. domain: ProviderKeyMissing + credential_context contextvar + TenantCredentialResolver Protocol (ports.py). 2. infra: CachedTenantCredentialResolver (TTL cache + asyncio.timeout over the store). 3. the 4 Bearer adapters read get_provider_credential() (drop api_key from __init__). 4. use_cases.py: resolve→set ctx→try/finally reset→map miss to 402. 5. config.py Settings (+2/−4) + validate_upstream_keys Bearer entries. 6. main.py wiring (store + resolver on app.state; unconditional Bearer registration).
Safety rule (feature-specific): the credential lives ONLY in the contextvar for the request's duration; ALWAYS reset in a finally (no cross-request leak); a resolver timeout or a None from the store fails CLOSED (ProviderKeyMissing) — NEVER a fallback/empty/platform key; SecretStr end-to-end (no secret in logs/errors).
Code lives in: `apps/gateway/src/gateway/proxy/` (+ core/config.py, main.py).
Constraints: do NOT change any test or the contract; allow-list packages only (no new dep — stdlib `contextvars`/`asyncio` + existing); do NOT touch Bedrock/Azure (staged to task 3); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full backend suite **1064 passed, 0 failed** (PG :5433 + Redis :6380, `uv run pytest -q`)
- [x] coverage did not decrease — +9 new tests in `credential_resolution_nonchat/` pin the shared gate/402/no-secret; non-chat use-cases gained explicit credential paths exercised by the embeddings/images/audio endpoint suites via the stub resolver
- [x] no test or contract was altered during build — §3 contract amended PRE-build (v2, Tin-approved "Complete non-chat now"); contract re-frozen md5 `432af02a`; the frozen `credential_resolution_seam/` bundle is UNTOUCHED. NOTE: the engine test-tamper tripwire is INERT for this slug (hyphen slug vs underscore test dir → empty tests snapshot); the two independent reviews below are the compensating control
- [x] the green was EARNED, not gamed — adversarial test-integrity review (general-purpose, sonnet): **CLEAN**. All 8 blast-radius test edits are legit `api_key=` removals + contextvar set/reset; the 3 `not in`→`in` inversions correctly track unconditional Bearer registration; secret-leak/auth-header asserts preserved. `test_error_maps_to_402` confirmed tautological (frozen, left untouched) but compensated by `credential_resolution_nonchat`. Stub does not mask a real fail-open
- [x] concurrency / timing of the risky operation is safe — contextvar set in `complete()`/`stream()` body, reset exactly-once on every path (complete `finally`; stream `_wrapped()` generator `finally`; TTS `_stream_resetting_credential` wrapper), cross-context reset tolerated (`try/except ValueError`); `CachedTenantCredentialResolver` bounded by `asyncio.timeout` (fail-closed → `ProviderKeyMissing`), positive-only caching; security review measured the timeout firing ~51ms for a 50ms bound
- [x] no exposed secrets, injection openings, or unexpected dependencies — adversarial security review (security-expert, sonnet): **SECURE**. `SecretStr` masking verified live (`SecretStr('**********')`); `.get_secret_value()` only at header build; `ProviderKeyMissing`/`ProblemError` carry no secret/ciphertext, `from None` severs the chain (secret-chain floor, v22); all 4 Bearer adapters fail-closed on unset/non-Bearer credential
- [x] layering & dependencies follow CONVENTIONS.md — shared `resolve_provider_credential` lives in `proxy/application` (use-case layer); contextvar in `proxy/domain/credential_context.py`; resolver behind the `TenantCredentialResolver` port; deps factories read `app.state` only; FROZEN `CompletionUpstream`(v7)/`ChatTranslator`(v9) Protocols unchanged (contextvar seam avoids touching them)
- [ ] a person reviewed and approved the change — **PENDING Tin (risk:high human gate)**

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `resolve_provider_credential` referenced by chat (`CompletionUseCase._resolve_credential`) + all 4 non-chat use-cases; `tenant_credential_resolver` wired in `main.py` (`CachedTenantCredentialResolver` over `DbTenantProviderKeyStore`) and read by embeddings/images/audio deps factories; 4 Bearer adapters read `get_provider_credential()` in `_auth_headers()`. Confirmed by full-suite green + both reviews tracing call sites
- [x] DEAD-CODE (code) — no orphaned symbol: `BYOK_BEARER_PROVIDERS`, `credential_context` helpers, `_stream_resetting_credential`, `keys/domain/services.py` shim all referenced (shim satisfies the frozen test import); confirmed via grep + reviews
- [x] SEMANTIC (prose / non-code) — read §1–§5 of this TASK.md in full + the v2 amendment note: the staged-gate (Bedrock/Azure env-bound until task 3) and non-chat completion match the frozen §3 flow; §5 scope re-snapshotted (38 declared) and reconciled against touched files

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-06-16
Notes: risk:high human gate. Suite 1064 passed / 0 failed (PG+Redis); ruff + pyright(src) clean.
Two independent adversarial reviews — Security (security-expert): SECURE; Test-integrity
(general-purpose): CLEAN — stand in for the inert test-tamper tripwire (hyphen-slug vs
underscore-test-dir). 3 build defects (A: feature-suite stub; B: non-chat 402-in-prod;
C: Bedrock/Azure staged-gate) found and fixed pre-gate. §3 contract amended v2 PRE-build
("Complete non-chat now", Tin-approved); re-frozen md5 432af02a; §5 scope re-snapshotted (38).

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
