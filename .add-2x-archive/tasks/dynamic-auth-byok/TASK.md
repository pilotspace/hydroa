# TASK: Dynamic Auth Byok

slug: dynamic-auth-byok · created: 2026-06-16 · stage: production · risk: high
autonomy: conservative   <!-- LOWERED from auto: converts Bedrock SigV4 (per-tenant AWS access-key/secret/session-token + region) and Azure AAD (per-tenant client-secret → token cache) from boot-env credentials to per-request tenant secret resolution. Secret material on the request path — same security class as credential-resolution-seam (task 2) and provider-credential-store (task 1). Per the v21 fold, any auth/secret task's verify gate runs an INDEPENDENT adversarial security subagent + a human gate (risk:high+auto trips unguarded_high_risk_auto). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Task: convert the TWO staged providers — AWS Bedrock (SigV4) and Azure OpenAI (AAD / api-key) —
from BOOT-ENV credentials to PER-REQUEST per-tenant credential resolution, plugging into the
contextvar seam task 2 built. Extends `BYOK_BEARER_PROVIDERS` (or adds a parallel non-Bearer
gate) so the resolver stops skipping bedrock/azure. Full-replace: no platform env fallback.

Touches (files · symbols · signatures):
- **Bedrock chat** — `proxy/infrastructure/bedrock_upstream.py:423` `BedrockCompletionUpstream.__init__` (boot-stores `self._credentials: AwsCredentials` :437, `self._region: str` :438); SigV4 at `sign_request(...)` :490 (non-stream, in `_do_request`) + :548 (stream, in `_gen`), both read `self._credentials`/`self._region`. Reads NO contextvar today.
- **Bedrock embeddings** — `proxy/infrastructure/bedrock_embeddings.py:101` `BedrockEmbeddingsProvider.__init__` (`self._credentials` :111, `self._region` :112); `sign_request(...)` :171 in `post_json`. No contextvar today.
- **SigV4 signer (unchanged, stateless)** — `proxy/infrastructure/bedrock_sigv4.py:170` `sign_request(*, method, url, body, service, region, credentials: AwsCredentials, timestamp) -> dict[str,str]`; `AwsCredentials` frozen dataclass :36 (`access_key_id, secret_access_key(repr=False), region, session_token|None`); `resolve_aws_credentials(settings) -> AwsCredentials|None` :60 (env path to retire).
- **Azure chat** — `proxy/infrastructure/azure_upstream.py:55` `AzureCompletionUpstream.__init__` (`self._config: AzureConfig` :64, `self._token_provider: AzureADTokenProvider|None` :65); `_auth_headers()` :81 → `Bearer {await token_provider.get_token()}` when provider set, else `{"api-key": self._config.api_key}`. No contextvar today.
- **Azure embeddings** — `proxy/infrastructure/azure_embeddings.py:58` `AzureEmbeddingsProvider.__init__` (same config/token_provider); `_auth_headers()` :79 identical semantics.
- **Azure AAD token provider/cache** — `proxy/infrastructure/azure_ad.py:81` `AzureADTokenProvider.__init__(*, config: AzureADConfig, now_fn, expiry_skew_s=60, metrics_registry)` :88; internal cache `self._token/_expires_at/_lock` + own `httpx.AsyncClient`; `AzureADConfig` frozen dataclass :43 (`tenant_id, client_id, client_secret(repr=False), scope, authority`). ⚠ BOOT SINGLETON — one instance at `main.py:474`, shared by BOTH chat (:480) + embeddings (:611).
- **The gate** — `proxy/domain/provider_credentials.py:50` `BYOK_BEARER_PROVIDERS = frozenset({openrouter,openai,anthropic,google})` (bedrock/azure deliberately ABSENT); `ProviderName` Literal :36 includes all six; `ProviderCredential` union :243 = `BearerCredential|BedrockCredential|AzureCredential`.
- **The resolver entrypoint** — `proxy/application/use_cases.py:382` `resolve_provider_credential(resolver, tenant_id, provider) -> object|None` (gates `provider not in BYOK_BEARER_PROVIDERS → return None`); `CompletionUseCase._resolve_credential` :765. Non-chat use-cases call the same module helper (task 2).
- **Adapter contextvar read** — `proxy/domain/credential_context.py:60` `get_provider_credential() -> ProviderCredential|None` (set/reset :42/:71); the 4 Bearer adapters already read it in `_auth_headers()` — the pattern Bedrock/Azure must adopt.
- **main.py boot guards to relax** — `main.py:442` `_aws_creds = resolve_aws_credentials(settings)` → `if _aws_creds:` chat :443 / embeddings :598; `_azure_cfg = resolve_azure_config(settings)` :459 → `if _azure_cfg:` chat :472 / embeddings :608; AAD singleton :474. Task-2 resolver wiring already on `app.state` (`tenant_provider_key_store` :618, `tenant_credential_resolver` :622).
- **core/config.py env path to retire** — `_UPSTREAM_KEY_ENV_VARS` :32 still lists the 4 Bedrock/Azure secret vars; Settings fields: `bedrock_access_key_id` :232 / `bedrock_secret_access_key`(secret) :235 / `bedrock_region` :237 / `bedrock_session_token`(secret) :240 / `bedrock_endpoint_url` :243; `azure_api_key`(secret) :249 / `azure_endpoint` :252 / `azure_api_version` :255 / `azure_deployment_map` :258 / `azure_tenant_id` :261 / `azure_client_id` :262 / `azure_client_secret`(secret) :265 / `azure_ad_scope` :267 / `azure_ad_authority` :270. (Disposition of the SECRET vs descriptive fields is a §3 decision — endpoint/version/region describe the upstream, not the secret.)

Context (working folder):
- **Store is ALREADY task-3-ready** — `proxy/infrastructure/tenant_provider_key_store.py:133` `_parts_to_credential` already builds `BedrockCredential` (provider=="bedrock") + `AzureCredential` (provider=="azure", `auth_mode` column → aad/api_key); `DbTenantProviderKeyStore.get(tenant_id, provider) -> ProviderCredential|None` :271. `CachedTenantCredentialResolver.resolve(tenant_id, provider) -> ProviderCredential` :92 (TTL + bounded `asyncio.timeout`, fail-closed). The ONLY resolution gap is the `BYOK_BEARER_PROVIDERS` skip — NOT the store.
- **Value-object converters exist (frozen task 1)** — `BedrockCredential.to_aws_credentials() -> AwsCredentials` `provider_credentials.py:143`; `AzureCredential.to_azure_config() -> AzureConfig` + `.to_azure_ad_config() -> AzureADConfig` :164+. Adapters convert the contextvar credential via these.
- GLOSSARY (milestone): `Provider credential` / `ERR_PROVIDER_KEY_MISSING` already added (task 2). No new term expected unless the AAD per-tenant cache earns a name.

Honors (patterns / conventions):
- **Additive seam, NOT a signature change** (milestone-locked): credential reaches the adapter via the request-scoped contextvar; FROZEN `CompletionUpstream`(v7)/`UpstreamProvider`/`ChatTranslator`(v9) Protocols stay unchanged — exactly as the 4 Bearer adapters do.
- **Fail-CLOSED floor**: bedrock/azure model with no configured tenant credential → `ERR_PROVIDER_KEY_MISSING` (402), NO env fallback. Unset/wrong-type contextvar credential in `_auth_headers`/signer → raise, never an unsigned/unauth request.
- **Designed-for-failure on resolver + AAD IO** (CLAUDE.md): resolver already bounded+cached; the AAD token mint must stay bounded + cached per-tenant (a per-request mint would hammer AAD and add latency) and fail closed — the boot singleton cannot multiplex tenants.
- **Secret-chain floor (v22)**: every transport/decrypt error in the new paths raises `from None`; `SecretStr` everywhere; `.get_secret_value()` only at the signer/header boundary.
- **Independent-oracle test pattern (v20/v21)**: Bedrock SigV4 has an independent get-vanilla oracle (`tests/bedrock_sigv4/`); Azure AAD has a token-provider oracle (`tests/azure_aad/`). Task 3 reuses these; the signer/token-provider internals are NOT re-derived.

Anchors the contract cites:
- The gate change: `BYOK_BEARER_PROVIDERS` extended to include bedrock+azure, OR a new sibling set / unified `BYOK_PROVIDERS` that `resolve_provider_credential` consults (⚠ [contract flag] — Bearer-only name vs all-six name).
- `get_provider_credential()` read + value-object conversion in: `BedrockCompletionUpstream`/`BedrockEmbeddingsProvider` (→ `BedrockCredential.to_aws_credentials()` → `sign_request`) and `AzureCompletionUpstream`/`AzureEmbeddingsProvider` (→ `AzureCredential` → `_auth_headers`).
- The per-tenant Azure AAD token-provider strategy (⚠ [contract flag] — the bundle's lowest-confidence point): a per-tenant `AzureADTokenProvider` cache (keyed by tenant/AAD-config identity, on `app.state`) vs per-request mint. Names the cache symbol + its bound/TTL.
- `ERR_PROVIDER_KEY_MISSING` (402) reused for bedrock/azure (no new error code expected).
- Removal/relaxation: `main.py` Bedrock/Azure boot guards → unconditional registration; retire the 4 Bedrock/Azure entries in `_UPSTREAM_KEY_ENV_VARS` + the corresponding SECRET Settings fields (`bedrock_access_key_id`, `bedrock_secret_access_key`, `bedrock_session_token`, `azure_api_key`, `azure_client_secret`).
- Tests to invert (no longer "staged-skip"): `tests/credential_resolution_seam/...::test_bedrock_azure_unchanged_staged` (:516) + `tests/credential_resolution_nonchat/...::test_staged_provider_skips_resolution[bedrock|azure]` (:87).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-tenant dynamic auth for Bedrock (SigV4) + Azure (AAD / api-key) — convert the two
staged providers to resolve credentials PER-REQUEST from the contextvar seam (task 2), completing
full-replace BYOK across all six providers. Unlike the Bearer providers (shared public endpoint,
only the key is per-tenant), Bedrock/Azure are per-tenant in BOTH secret AND upstream addressing
(region / endpoint / deployment) — grounded in the task-1 value-objects, which already carry those
fields (`BedrockCredential.region`, `AzureCredential.endpoint/api_version/deployment_map`).

Framings weighed: extend the existing contextvar seam — adapters read `get_provider_credential()`
at call time, convert via the frozen value-object converters, sign/auth per-request; per-tenant AAD
tokens cached on `app.state` (chosen) · inject a per-provider resolver into each adapter ctor
(rejected — breaks the additive-seam invariant, changes FROZEN adapter construction) · mint an AAD
token per request with no cache (rejected — latency + hammers Azure AD, violates designed-for-failure).

Must:
<must>
  - A served bedrock-provider request (chat non-stream, chat stream, embeddings) authenticates with
    the calling tenant's `BedrockCredential` from the contextvar: the SigV4 signer uses
    `credential.to_aws_credentials()` for BOTH the signing key/secret/session-token AND the
    signing region; the request host/region is derived from the credential's region — never boot env.
  - A served azure-provider request (chat non-stream, chat stream, embeddings) authenticates with the
    tenant's `AzureCredential`: api_key mode → `api-key` header from the credential; aad mode → an
    OAuth2 Bearer token minted for THAT tenant's `to_azure_ad_config()`. Endpoint, api_version and
    deployment mapping are taken from the credential, not boot env.
  - `resolve_provider_credential` STOPS skipping bedrock/azure — they resolve like the Bearer
    providers (chat + all non-chat use-cases share the one module helper); a missing/disabled tenant
    key → `ERR_PROVIDER_KEY_MISSING` (402), no env fallback.
  - Azure AAD tokens are cached PER TENANT (keyed so no two tenants ever share a token) and refreshed
    before expiry; the cache lookup + mint is bounded (timeout) and fails CLOSED. The boot-singleton
    AAD provider is replaced by this per-tenant cache.
  - Bedrock and Azure adapters register UNCONDITIONALLY at boot (no env-credential guard), exactly as
    the 4 Bearer adapters now do; per-tenant gating happens at resolve time.
  - The platform env credential path for bedrock/azure SECRETS is retired (full-replace): the secret
    Settings fields (`bedrock_access_key_id`, `bedrock_secret_access_key`, `bedrock_session_token`,
    `azure_api_key`, `azure_client_secret`) + their `_UPSTREAM_KEY_ENV_VARS` entries are removed.
  - Credential lifecycle matches the Bearer seam: set after auth + provider-resolution, reset
    exactly-once on every path including streaming generators (no cross-request/tenant leak).
</must>
Reject:
<reject>
  - bedrock/azure model, calling tenant has no enabled credential -> "ERR_PROVIDER_KEY_MISSING" (402)
  - contextvar credential absent or WRONG TYPE for the provider at signer/`_auth_headers` time
    (e.g. a BearerCredential on the bedrock path) -> "ERR_PROVIDER_KEY_MISSING" (402, fail-closed) —
    NEVER an unsigned/unauthenticated upstream call (mirrors the Bearer adapters' unset/non-Bearer raise)
  - azure aad mode, token mint to Azure AD fails or times out -> "ERR_UPSTREAM_UNAVAILABLE" (configured
    but AAD unreachable is an upstream-IO failure, distinct from 402 "not configured"); fail-closed,
    NO fallback to api_key, NO unauthenticated call
</reject>
After:
<after>
  - Every upstream Bedrock/Azure call (chat stream + non-stream + embeddings) authenticates with the
    calling tenant's resolved credential and addresses the tenant's own region/endpoint/deployment;
    no process-wide env credential is consulted on the request path.
  - BYOK covers all six providers; the staged bedrock/azure skip is gone.
  - Per-tenant AAD tokens are isolated per tenant and reused within their lifetime; a mint failure
    fails the request closed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Per-tenant Azure AAD token-cache strategy — a bounded cache of `AzureADTokenProvider` instances on
    `app.state`, keyed by AAD-config identity (tenant_id + client_id + authority + scope), each holding
    its own token + refresh lock, capped/evicted. LOWEST confidence: it introduces NEW shared state the
    Bearer path never had (a cache of cache-holders) with lifecycle/eviction/concurrency questions; if
    wrong → either a token-isolation bug (one tenant's token served to another = security HARD-STOP) or
    unbounded growth / per-request mint (latency + AAD rate-limit). Cost: re-shape the contract + the
    build's one genuinely new component. THIS is the bundle's headline freeze flag.
  ⚠ Gate naming — rename `BYOK_BEARER_PROVIDERS` → `BYOK_PROVIDERS` (now all six) vs add bedrock/azure
    to the set under the old name vs a sibling set. Low-ish confidence: mechanical but it touches the
    task-2 symbol + its tests; if wrong → a misleading name / churn. Recommend rename to `BYOK_PROVIDERS`.
  - [ ] Wrong-type/unset contextvar credential reuses `ProviderKeyMissing`→402 rather than a distinct
    invariant-violation code — confirm at contract (recommend reuse, symmetric with Bearer).
  - [ ] AAD mint IO failure maps to the existing `ERR_UPSTREAM_UNAVAILABLE` (not 402) — confirm the exact
    existing error symbol/status during contract/tests.
  - [ ] Descriptive (non-secret) Settings fields — `bedrock_region`, `bedrock_endpoint_url`,
    `azure_endpoint`, `azure_api_version`, `azure_deployment_map`, `azure_tenant_id`, `azure_client_id`,
    `azure_ad_scope`, `azure_ad_authority` — disposition: they become per-tenant via the credential, so
    boot fields are vestigial; recommend KEEP as harmless boot defaults / test overrides (remove only
    the 5 SECRET fields) to bound blast radius. Confirm full-replace scope = secrets-only.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Bedrock chat signs with the tenant's own key and region          # M1
  Given tenant T has a bedrock BYOK credential (access_key_id "AKIA-T", region "eu-west-1")
  When T calls a bedrock-provider chat model (non-streaming)
  Then the upstream request carries a SigV4 Authorization header computed from "AKIA-T"
  And the request host/credential-scope is "bedrock-runtime.eu-west-1.amazonaws.com" (the tenant region)
  And no platform/env AWS credential appears in the signature

Scenario: Bedrock streaming and embeddings sign per-request too             # M1
  Given tenant T's bedrock BYOK credential
  When T calls a bedrock streaming chat model and a bedrock embeddings model
  Then each upstream request is SigV4-signed with T's credential and region
  And no boot/env AWS credential is consulted

Scenario: Azure api_key mode uses the tenant endpoint and key              # M2
  Given tenant T has an azure BYOK credential mode="api_key" (endpoint "https://t.openai.azure.com", key "K-T")
  When T calls an azure-provider chat model
  Then the upstream request targets "https://t.openai.azure.com" with header "api-key: K-T"
  And no env azure key or endpoint is consulted

Scenario: Azure aad mode mints a Bearer token for the tenant              # M2
  Given tenant T has an azure BYOK credential mode="aad" (T's tenant_id/client_id/client_secret, endpoint "https://t.openai.azure.com")
  When T calls an azure-provider chat model
  Then a Bearer token minted for T's AAD config is attached as "Authorization: Bearer <token>"
  And the request targets T's endpoint and deployment

Scenario: Azure AAD tokens are isolated per tenant and reused             # M4
  Given two tenants T1 and T2 with distinct azure aad credentials
  When T1, then T2, then T1 again each call an azure model
  Then T1's requests carry T1's token and T2's carry T2's — never crossed
  And T1's second call reuses the cached token (no second mint within the token lifetime)

Scenario: AAD mint and cache lookup are bounded and fail closed           # M4
  Given tenant T's azure aad credential and a token endpoint that hangs past the bound
  When T calls an azure model
  Then the mint is abandoned at the timeout and the request fails closed
  And no partial/empty Bearer header is sent

Scenario: The resolver resolves bedrock and azure (no longer staged-skip) # M3
  Given the credential resolver and a tenant with a bedrock (and an azure) BYOK key
  When resolve_provider_credential(resolver, tenant, "bedrock") and (..., "azure") run
  Then each consults the resolver and sets the matching credential in the contextvar
  And the resolver is no longer skipped for these two providers

Scenario: Bedrock and Azure adapters register unconditionally             # M5
  Given an app booted with NO bedrock/azure env credentials set
  When the app starts
  Then the bedrock and azure adapters are present in the chat and provider registries
  And a request for them still fails closed with 402 when the tenant has no key (no silent absent-adapter fallback)

Scenario: The env secret path for bedrock/azure is retired               # M6
  Given the Settings model and the boot key-guard
  When they are inspected
  Then there is no bedrock_secret_access_key / bedrock_access_key_id / bedrock_session_token / azure_api_key / azure_client_secret field
  And _UPSTREAM_KEY_ENV_VARS no longer lists the bedrock/azure secret vars

Scenario: Credential is reset exactly once with no cross-tenant leak      # M7
  Given tenant T1 completes a bedrock request (success or error, streaming or not)
  When the request finishes
  Then get_provider_credential() is None afterward
  And a subsequent request by tenant T2 never observes T1's credential

Scenario: Missing tenant credential is rejected before any upstream call  # R1
  Given tenant T has NO bedrock credential configured
  When T calls a bedrock-provider model
  Then the response is 402 "ERR_PROVIDER_KEY_MISSING"
  And no upstream request is attempted (no signature is computed)

Scenario: Wrong-type or unset contextvar credential fails closed          # R2
  Given the bedrock signer / azure _auth_headers path with the contextvar unset or holding a BearerCredential
  When it builds the request
  Then it raises and maps to 402 "ERR_PROVIDER_KEY_MISSING"
  And no unsigned / unauthenticated upstream request is sent

Scenario: Azure AAD mint failure is an upstream-unavailable, not a 402    # R3
  Given tenant T's azure aad credential and an Azure AD token endpoint that errors
  When T calls an azure model
  Then the response is "ERR_UPSTREAM_UNAVAILABLE"
  And no fallback to the api-key header occurs and no unauthenticated request is sent
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This task adds NO new HTTP endpoint — it changes the per-request auth behavior behind the
existing `/v1/chat/completions` and `/v1/embeddings` paths for provider ∈ {bedrock, azure}.
The contract freezes the SEAM shape (symbols + behavior + error mapping), not a new route.

```
# Affected request paths (provider routed to bedrock|azure) — behavior delta only:
POST /v1/chat/completions   (stream + non-stream)
POST /v1/embeddings
  200 -> unchanged success bodies (provider-native, already frozen v9/v20/v21)
  402 -> { error: "ERR_PROVIDER_KEY_MISSING" }   # tenant has no enabled bedrock/azure key,
                                                 # OR contextvar credential unset/wrong-type at sign time
  502 -> { error: "ERR_UPSTREAM_UNAVAILABLE" }   # azure aad-mode token mint to Azure AD failed/timed out

# 1 · GATE (proxy/domain/provider_credentials.py)
RENAME  BYOK_BEARER_PROVIDERS -> BYOK_PROVIDERS : frozenset[str]
        = {"openrouter","openai","anthropic","google","bedrock","azure"}   # all six
        resolve_provider_credential() and CompletionUseCase._resolve_credential() gate on it
        unchanged otherwise (still returns None when resolver is None / provider absent).
        (task-2 call sites + tests that reference BYOK_BEARER_PROVIDERS update to the new name.)

# 2 · BEDROCK adapters (bedrock_upstream.py, bedrock_embeddings.py)
        __init__ DROPS `credentials`/`region` (no boot creds; full-replace).
        Per request: cred = get_provider_credential(); if not isinstance(cred, BedrockCredential):
          raise ProviderKeyMissing("bedrock")            # -> 402, fail-closed, no signature attempted
        aws = cred.to_aws_credentials(); sign_request(..., region=aws.region, credentials=aws)
        Request host/endpoint derived from aws.region (bedrock-runtime.<region>.amazonaws.com),
        unless `bedrock_endpoint_url` boot override is set (test/e2e only).

# 3 · AZURE adapters (azure_upstream.py, azure_embeddings.py)
        __init__ DROPS the boot `config`/`token_provider`; gains `token_provider_cache: AzureADTokenProviderCache`.
        Per request: cred = get_provider_credential(); if not isinstance(cred, AzureCredential):
          raise ProviderKeyMissing("azure")              # -> 402, fail-closed
        endpoint/api_version/deployment from cred.to_azure_config(); auth:
          mode=="api_key" -> {"api-key": cred.api_key}
          mode=="aad"     -> tp = token_provider_cache.get_or_create(cred.to_azure_ad_config())
                             {"Authorization": f"Bearer {await tp.get_token()}"}
                             # tp.get_token() already fail-closed: UpstreamUnavailableError -> 502

# 4 · NEW: AzureADTokenProviderCache (proxy/infrastructure/azure_ad.py)  [Tin freeze decision: TTL, no secret in key]
        get_or_create(config: AzureADConfig) -> AzureADTokenProvider
        Backing store: mapping keyed by the NON-SECRET AzureADConfig identity
          key = (config.tenant_id, config.client_id, config.authority, config.scope)
          value = (AzureADTokenProvider, created_monotonic)
          -> NO client_secret in the key (memory-hygiene; the secret lives only inside the
             provider it authenticates).
        Eviction: per-entry TTL (GATEWAY_AZURE_AD_PROVIDER_CACHE_TTL_S, default 300.0s).
          On get_or_create: reuse the entry iff (now - created) < TTL; else close the old
          entry's httpx client and construct a fresh provider (this is ALSO how a ROTATED
          client_secret takes effect — bounded staleness <= TTL, mirroring the resolver's
          60s positive credential cache). Lazy eviction on access + a soft max-size backstop
          (GATEWAY_AZURE_AD_PROVIDER_CACHE_MAX, default 512; oldest-created evicted+closed when
          exceeded) so a churn of distinct tenants cannot grow the map without bound.
          Construction is concurrency-safe (asyncio.Lock); fail-closed on mint (the wrapped
          AzureADTokenProvider already raises UpstreamUnavailableError from None).
        Isolation: different platform tenants configure different (tenant_id, client_id) ->
          different keys -> different providers -> tokens NEVER crossed.
        Note: the inner token is also reused within its own AAD expiry; the outer TTL governs
          only how fast a secret rotation is picked up (it may discard a still-valid token at
          most once/TTL per active tenant — accepted cost for rotation responsiveness).
        Wired ONCE on app.state in main.py; injected into both azure adapters (replaces the
          boot-singleton AzureADTokenProvider at main.py:474).

# 5 · main.py registration
        bedrock + azure adapters register UNCONDITIONALLY (drop `if _aws_creds:` / `if _azure_cfg:`),
        exactly as the 4 Bearer adapters now do. resolve_aws_credentials/resolve_azure_config env
        assembly for SECRETS is removed from the request path.

# 6 · core/config.py (full-replace, SECRETS ONLY)
        REMOVE Settings fields: bedrock_access_key_id, bedrock_secret_access_key,
          bedrock_session_token, azure_api_key, azure_client_secret
        REMOVE their entries from _UPSTREAM_KEY_ENV_VARS (the 4 bedrock/azure secret vars).
        KEEP descriptive fields (bedrock_region, bedrock_endpoint_url, azure_endpoint,
          azure_api_version, azure_deployment_map, azure_tenant_id, azure_client_id,
          azure_ad_scope, azure_ad_authority) as harmless boot defaults / test overrides —
          they describe the upstream, not the secret; removing them is out of scope (bounds blast radius).
```

Schema: NO new tables/columns. Reuses task-1 `tenant_provider_keys` (tenant_id, provider) BYTEA
secret_enc/extra_enc + auth_mode; `_parts_to_credential` already returns BedrockCredential/AzureCredential.
Access pattern unchanged: CachedTenantCredentialResolver.resolve(tenant_id, provider) (TTL + bounded timeout).

Status: FROZEN @ v1 — approved by Tin Dang, 2026-06-16 (delegated auto mode + freeze gate)

Least-sure flag surfaced at freeze: [contract] the Azure AAD per-tenant token-cache strategy (#4) —
RESOLVED at freeze (Tin, 2026-06-16) to a TTL cache keyed by the NON-SECRET AzureADConfig identity
(tenant_id, client_id, authority, scope), NOT the secret-bearing full-config LRU originally drafted;
secret never in the key, rotation handled as bounded staleness (<= GATEWAY_AZURE_AD_PROVIDER_CACHE_TTL_S,
default 300s), soft size cap as a growth backstop. Wrong if a tenant-isolation bug ships (one tenant's
token served to another = security HARD-STOP) — mitigated by keying on tenant_id+client_id. Second-least-sure:
[contract] the gate RENAME BYOK_BEARER_PROVIDERS -> BYOK_PROVIDERS (all six) — approved as drafted; churns
the task-2 symbol + its tests (mechanical). Also [scenario] §6 env-secret removal cascades into 2 more src
modules + ~11 test files — Tin chose full removal now (honor §6) over deferring to a cleanup task.
<!-- Freeze flags surfaced + resolved at the gate:
  [contract #4 — headline] Azure AAD per-tenant token cache. Tin DECISION: TTL cache keyed by the
    NON-SECRET AzureADConfig identity (tenant_id, client_id, authority, scope) — NOT the secret-bearing
    full-config LRU originally drafted. Secret never in the key; rotation handled as bounded staleness
    (<= GATEWAY_AZURE_AD_PROVIDER_CACHE_TTL_S, default 300s). Soft size cap as a growth backstop.
  [contract #1] Gate rename BYOK_BEARER_PROVIDERS -> BYOK_PROVIDERS (all six) — approved as drafted.
  [contract resolved] AAD mint failure -> 502 ERR_UPSTREAM_UNAVAILABLE (verified: error_catalog.py:324 +
    AzureADTokenProvider._acquire already raises UpstreamUnavailableError from None). Missing/wrong-type/
    unset credential -> 402 ERR_PROVIDER_KEY_MISSING (ProviderKeyMissing.code, provider_credentials.py:88).
  [contract resolved] Full-replace scope = SECRETS ONLY (5 fields + their env-var entries); descriptive
    Settings fields kept as boot defaults / test overrides to bound blast radius.
  Changing this frozen contract = change request back to SPECIFY. -->
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥ 90% on the new/changed code (cache + adapter credential branches + gate); suite-wide coverage must not decrease.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  # tests/dynamic_auth_byok/test_dynamic_auth_byok.py — seam behavior (captures the signed/auth'd
  # upstream request via an httpx MockTransport on the adapter; contextvar set as in a real request)
  - test_bedrock_chat_signs_with_tenant_key_and_region (M1): arrange tenant BedrockCredential
    (AKIA-T, region eu-west-1) in contextvar / store; act bedrock chat non-stream; assert upstream
    Authorization is SigV4 over Credential=AKIA-T/.../eu-west-1/bedrock + host bedrock-runtime.eu-west-1;
    assert NO boot/env AWS key in the signature.
  - test_bedrock_stream_and_embeddings_sign_per_request (M1): act bedrock streaming chat + embeddings;
    assert each upstream request SigV4-signed with T's key+region; assert credential reset after stream close.
  - test_azure_api_key_mode_uses_tenant_endpoint_and_key (M2): arrange AzureCredential mode=api_key
    (endpoint https://t.openai.azure.com, key K-T); act azure chat; assert request URL host = t's endpoint
    AND header api-key: K-T; assert no env azure key/endpoint consulted.
  - test_azure_aad_mode_mints_bearer_for_tenant (M2): arrange AzureCredential mode=aad (T's ids) + a
    stub AAD token endpoint; act azure chat; assert Authorization: Bearer <minted>; assert request targets
    T's endpoint/deployment.
  - test_resolver_resolves_bedrock_and_azure (M3): act resolve_provider_credential(resolver, tenant,
    "bedrock") and (...,"azure"); assert resolver consulted + matching credential set in contextvar
    (NO longer None) — the inverse of the task-2 staged-skip.
  - test_bedrock_azure_register_unconditionally (M5): arrange create_app with NO bedrock/azure env creds;
    assert both adapters present in chat + provider registries; assert a request still 402s when the tenant
    has no key (not a silent absent-adapter fallback).
  - test_env_secret_path_retired (M6): assert Settings has no bedrock_access_key_id/bedrock_secret_access_key/
    bedrock_session_token/azure_api_key/azure_client_secret attribute; assert _UPSTREAM_KEY_ENV_VARS lacks the
    4 bedrock/azure secret vars.
  - test_credential_reset_no_cross_tenant_leak (M7): act T1 bedrock request (success + error variants);
    assert get_provider_credential() is None after; assert a following request sees no T1 credential.
  - test_missing_credential_returns_402 (R1): arrange tenant with NO bedrock key; act bedrock request;
    assert 402 ERR_PROVIDER_KEY_MISSING; assert upstream MockTransport received ZERO requests (no signature).
  - test_wrong_type_or_unset_credential_fails_closed (R2): arrange contextvar unset / holding a
    BearerCredential; act the bedrock signer + azure _auth_headers path; assert raises ProviderKeyMissing
    (→402) and the MockTransport saw no unsigned/unauth request.
  - test_azure_aad_mint_failure_maps_to_502 (R3): arrange aad credential + AAD token endpoint returning 500;
    act azure chat; assert 502 ERR_UPSTREAM_UNAVAILABLE; assert NO api-key fallback header and no unauth call.

  # tests/dynamic_auth_byok/test_azure_ad_provider_cache.py — the new component (M4), unit-level
  - test_same_identity_reuses_provider_within_ttl: two get_or_create with the same AzureADConfig return
    the SAME provider instance (one mint).
  - test_distinct_tenants_get_distinct_providers: configs differing by tenant_id/client_id return DIFFERENT
    providers; tokens never crossed.
  - test_ttl_expiry_rebuilds_and_closes_old: advance now_fn past TTL → get_or_create builds a NEW provider
    and closes the old one's httpx client.
  - test_key_excludes_secret: two configs identical except client_secret map to the SAME key within TTL
    (rotation = bounded staleness ≤ TTL), and the key tuple contains no secret value.
  - test_size_cap_evicts_oldest_and_closes: exceeding GATEWAY_AZURE_AD_PROVIDER_CACHE_MAX evicts the
    oldest-created entry and closes its client.
  - test_mint_failure_is_fail_closed: a provider whose AAD endpoint errors raises UpstreamUnavailableError
    (from None) out of get_token; the cache does not cache a failed token.
  - test_concurrent_get_or_create_single_construction: concurrent calls for one identity construct exactly
    one provider (lock).

  # INVERSIONS (track the frozen task-3 contract superseding task-2 staged-skip) — declared in §5 scope:
  - credential_resolution_seam/test_credential_resolution_seam.py::test_bedrock_azure_unchanged_staged
    → assert bedrock/azure NOW resolve via the seam (was: assert staged-skip).
  - credential_resolution_nonchat/test_credential_resolution_nonchat.py::test_staged_provider_skips_resolution
    → becomes test_bedrock_azure_resolve (assert resolver consulted + credential set for bedrock/azure).
</test_plan>

Tests live in: `apps/gateway/tests/dynamic_auth_byok/` · `apps/gateway/tests/credential_resolution_seam/test_credential_resolution_seam.py` · `apps/gateway/tests/credential_resolution_nonchat/test_credential_resolution_nonchat.py` · MUST run red (missing implementation) before Build.
<!-- These tokens make the tamper tripwire ACTIVE (unlike task 2's inert hyphen-slug/underscore-dir
     mismatch): _declared_test_files resolves project-root tokens containing "/" and hashes them at the
     tests→build snapshot. The new task-3 dir + the 2 inverted files are frozen against build-time edits. -->
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/domain/provider_credentials.py` · `apps/gateway/src/gateway/proxy/application/use_cases.py` · `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py` · `apps/gateway/src/gateway/proxy/infrastructure/bedrock_embeddings.py` · `apps/gateway/src/gateway/proxy/infrastructure/bedrock_sigv4.py` · `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py` · `apps/gateway/src/gateway/proxy/infrastructure/azure_embeddings.py` · `apps/gateway/src/gateway/proxy/infrastructure/azure_config.py` · `apps/gateway/src/gateway/proxy/infrastructure/azure_ad.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/src/gateway/core/config.py`
<!-- All MODIFICATIONS to existing src files (no new src file: AzureADTokenProviderCache lands in azure_ad.py).
     Tin freeze decision: FULL removal of the env-secret path — bedrock_sigv4.py (resolve_aws_credentials),
     azure_config.py (resolve_azure_config), azure_ad.py (resolve_azure_ad_config) are in scope so the 3
     now-unused resolve_* helpers + the boot-singleton AAD provider are deleted alongside the 5 secret fields.
     Test files are written/inverted/updated in THIS tests phase (in the snapshot baseline) — the build does
     NOT touch them. Scope-anchor frozen at the tests→build advance; gate enforces touched ⊆ declared. -->
Strategy (ordered batches):
  1. Gate: rename BYOK_BEARER_PROVIDERS→BYOK_PROVIDERS (all six) in provider_credentials.py + update the
     references in use_cases.py (the helper + _resolve_credential). Run the inverted resolver tests → green.
  2. AzureADTokenProviderCache in azure_ad.py: TTL+size-capped, keyed by non-secret AzureADConfig identity,
     lock-guarded, closes evicted clients. Run test_azure_ad_provider_cache.py → green.
  3. Bedrock adapters: drop boot creds; read BedrockCredential from contextvar, sign via to_aws_credentials()
     (key + region), fail-closed wrong-type/unset. Both chat (stream+non-stream) + embeddings.
  4. Azure adapters: drop boot config/token_provider, inject the cache; read AzureCredential, derive
     endpoint/deployment, api_key vs aad auth, fail-closed; 502 on aad mint failure.
  5. main.py: register bedrock/azure unconditionally; wire the cache on app.state; inject into azure adapters;
     delete the boot-singleton AAD provider + the resolve_aws_credentials/resolve_azure_config gating calls.
  6. Full env-secret removal: config.py drops the 5 SECRET fields + their _UPSTREAM_KEY_ENV_VARS entries +
     adds the cache TTL/MAX knobs; delete the now-unused resolve_aws_credentials (bedrock_sigv4.py),
     resolve_azure_config (azure_config.py), resolve_azure_ad_config (azure_ad.py).
     Run the full dynamic_auth_byok suite + the broader bedrock_*/azure_*/empty_key_boot_guard suites → green.
Safety rule (feature-specific): fail-CLOSED on every credential path — an unset/wrong-type contextvar
  credential or an AAD mint failure NEVER produces an unsigned/unauthenticated/api-key-fallback upstream
  request; secrets stay in SecretStr and never enter a cache KEY, log, span, or error chain (raise from None).
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new deps expected); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — **1072 passed / 0 failed**, 19 deselected (`uv run pytest tests/`, PG :5433 + Redis :6380). ruff clean; pyright 0 errors.
- [x] coverage did not decrease — **86.11%** (≥ 80% floor); the 2 added tests pin an already-covered failure branch, so the line total held.
- [x] no test or contract was altered during build — the FROZEN §3 contract is intact; the FROZEN §4 test set (`tests/dynamic_auth_byok/` + the 2 inverted files) was NOT edited (tamper tripwire clean on build→verify advance). Two corrections were made, both ADDITIVE/faithful, never a weakening: (1) reverted a build-introduced regression that had changed the FROZEN `AzureCredential.authority` default to `""` + a `to_azure_ad_config()` endpoint fallback — restored to the task-1 `DEFAULT_AUTHORITY` semantics (no endpoint fallback); (2) `tests/azure_verify::_az_aad_cred` now sets `authority=base_url` explicitly (adaptation to the restored semantics; no assertion removed).
- [x] the green was EARNED — adversarial refute-read (independent sonnet subagent) found no overfit / vacuous-assert / stubbed-logic cheats in src, and confirmed each new test would fail against a broken impl. It surfaced ONE valid coverage gap: the chat adapter's §2-R3 (AAD mint failure → 502, no api-key fallback) was unpinned (the embeddings adapter was already covered by `azure_embeddings::test_token_failure_fails_closed_no_post`). CLOSED via honest redo: added `azure_aad::test_adapter_aad_mint_failure_fails_closed_no_post` (+ streaming variant), proven RED against the forbidden-fallback mutation and GREEN against real src (red/green TDD).
- [x] concurrency / timing safe — `AzureADTokenProvider.get_token` is single-flight (asyncio.Lock + post-lock cache re-check → one mint under a stampede); `AzureADTokenProviderCache.get_or_create` is sync with no await → check-create-insert is atomic within one event-loop tick; the credential ContextVar is set/reset exactly-once per request across complete/stream/embeddings (independently traced by the security review, incl. the Starlette-drain cross-context reset); tenant isolation via the non-secret cache key.
- [x] no exposed secrets, injection openings, or unexpected dependencies — independent security review (sonnet) verdict **SECURE**: all 4 adapters proven fail-closed (unset/wrong-type contextvar → `ProviderKeyMissing` before any network; AAD mint failure → `UpstreamUnavailableError`, never api-key fallback / blank Bearer); AAD cache key is the NON-SECRET `(tenant_id, client_id, authority, scope)`; `raise … from None` floor across all 8 transport/decrypt sites; no secret in URL/metric/span/error; no new dependency; all 5 env secret fields + `_UPSTREAM_KEY_ENV_VARS` entries removed.
- [x] layering & dependencies follow CONVENTIONS.md — the per-request credential ContextVar seam (domain) keeps the FROZEN CompletionUpstream(v7)/ChatTranslator(v9)/UpstreamProvider Protocols unchanged; adapters stay in infrastructure, the BYOK gate in application, value objects in domain.
- [ ] a person reviewed and approved the change — **risk:high → Tin Dang (pending; brought to the gate)**

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced (reference grep): `AzureADTokenProviderCache` → main.py:449 + both Azure adapters' ctors; `app.state.azure_ad_token_provider_cache` → main.py:454 (asserted by M7); `azure_ad_provider_cache_ttl_s`/`_max` → config.py:256/258 read at main.py:450/451; `BYOK_PROVIDERS` → use_cases.py:73,404; `AzureADTokenProvider.aclose` → called by the cache's evict path.
- [x] DEAD-CODE (code) — the removed symbols (`resolve_aws_credentials`, `resolve_azure_config`, `resolve_azure_ad_config`, `BYOK_BEARER_PROVIDERS`) have ZERO live references — they appear only in explanatory test comments / RIGHT-REASON-RED annotations, and ZERO in `src/` (pyright 0-errors confirms no dangling import).
- [x] SEMANTIC — the two adversarial reviews were read in full; their verdicts (SECURE; green-earned after the one gap was closed) are reconciled above.

### Follow-ups (deltas, non-blocking — out of this task's §5 scope)
- Stale comments in `proxy/application/{embeddings,images,audio}_use_case.py` say "Bedrock/Azure skip (env-bound, task 3)" — inaccurate after the gate rename, zero behavioral effect (these call `resolve_provider_credential` which uses live `BYOK_PROVIDERS`). Fix in a follow-on cleanup.
- `azure_ad_provider_cache` PC6 names "not cached" but asserts only fail-propagation — strengthen to assert the failed provider is not pinned.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-06-16

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
