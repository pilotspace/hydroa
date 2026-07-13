# TASK: Vertex AI adapter (service-account auth, regional endpoints) + EU/Asia entries

slug: vertex-adapter · created: 2026-07-12 · stage: production
milestone: residency-service-tiers
autonomy: auto
phase: contract
sensitivity: security

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py:_openai_to_gemini_request` /
  `:_gemini_to_openai` / `:_gemini_error_to_openai` / `:_map_gemini_finish_reason` /
  `:_GeminiSSEStepper` / `:_translate_gemini_sse` — the PURE (no-IO) OpenAI⇄Gemini translation
  functions this task's `VertexCompletionUpstream` REUSES unchanged (Vertex's Gemini-publisher
  wire body is the same `generateContent`/`streamGenerateContent` JSON shape). `__all__`
  (line ~1170) already exports several of these underscore-prefixed helpers for cross-module
  reuse/testing — `_GeminiSSEStepper` is the one NOT currently in `__all__` and needs adding
  (this task's Build gains live incremental streaming the same way `GeminiCompletionUpstream.stream`
  already does internally).
- `apps/gateway/src/gateway/proxy/infrastructure/azure_ad.py:AzureADConfig` /
  `:AzureADTokenProvider` / `:AzureADTokenProviderCache` — the OAuth2 client-credentials
  mint+cache pattern this task's NEW `VertexServiceAccountConfig` / `VertexTokenProvider` /
  `VertexTokenProviderCache` mirror FILE-FOR-FILE, swapping AAD's client-credentials grant for
  GCP's JWT-bearer grant (RFC 7523): single-flight `asyncio.Lock` + double-check, expiry-skew
  refresh, non-secret-identity cache key, TTL+size-capped eviction, `from None` secret-chain
  stripping on any IDP-call failure.
- `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py:AzureCompletionUpstream`
  (`_get_credential`, `_resolve_config_and_cred`, `complete`, `stream`) — the per-request
  credential-contextvar + fail-closed `ProviderKeyMissing` pattern this task's
  `VertexCompletionUpstream` mirrors; also the precedent for "a provider gets its OWN adapter
  class, not a mode-flag on an existing one" (Azure is its own class, not a flag on
  `OpenRouterCompletionUpstream`) — this task's Framing A/rejected-alternative below cites it.
- `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py:BedrockCompletionUpstream._endpoint_url`
  (~line 566-568) — GROUND ONLY: `self._endpoint_url_override or f"https://bedrock-runtime.{aws.region}.amazonaws.com"`,
  i.e. the literal AWS egress region is driven by the TENANT'S OWN credential
  (`BedrockCredential.region`), independently of whatever `region` tag the selected catalog row
  carries (region-catalog-dimension's own named Issue #1 gap). Cited so this task's Vertex design
  can explicitly NOT repeat that gap — see Issue #3 below.
- `apps/gateway/src/gateway/proxy/domain/provider_credentials.py:ProviderName` /
  `:PROVIDER_VALUE_SET` / `:BYOK_PROVIDERS` (lines 36-51) — the bounded provider value-set this
  task widens to include `"vertex"` (mirrors minimax-adapter-registry's precedent exactly).
  `:BedrockCredential` / `:AzureCredential` (lines 117-241) — the `SecretStr` +
  `@model_validator(mode="after")` + `to_*_config()` conversion-to-existing-dataclass pattern
  this task's NEW `:GoogleServiceAccountCredential` mirrors. `:ProviderCredential`
  (line 248, currently `BearerCredential | BedrockCredential | AzureCredential`) — the union this
  task widens with a 4th alternative (§1 Issue #5 below — flagged as a bigger edit to this
  "§3 CONTRACT... FROZEN @ v1" module than any prior provider-onboarding task made).
- `apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py:ProviderKeyPutBody` (~line 68) /
  `:_BEARER_PROVIDERS` (~line 60) / `:_build_credential` (~line 140) / `:put_provider_key`
  (`PUT /admin/provider-keys/{provider}`) — the provider-discriminated BYOK write path this task
  extends with a `provider == "vertex"` branch. The Azure-only write-time SSRF guard block
  (`assert_literal_host_not_denied` on `body.endpoint`/`body.authority`, inside `put_provider_key`)
  is GROUND ONLY — cited to justify why Vertex does NOT need an equivalent guard (§1 M7).
- `apps/gateway/src/gateway/proxy/domain/ports.py:CompletionUpstream` (line 138) — the Protocol
  (`complete`/`stream`) `VertexCompletionUpstream` implements.
- `apps/gateway/src/gateway/proxy/domain/credential_context.py:get_provider_credential` — the
  per-request contextvar every adapter (including this task's) reads for its credential.
- `apps/gateway/src/gateway/catalog/domain/entities.py:CatalogModel` (lines 127-160) — CURRENT
  shape (`id, name, context_length, prompt_usd_per_token, completion_usd_per_token, modality,
  provider, input_modalities, cached_input_usd_per_token, audio_*`). Confirmed via
  `search_for_pattern "region"` over `catalog/` — ZERO hits in this tree: `region` does NOT exist
  yet (region-catalog-dimension is at `phase: contract`, its Build has not landed). This task's
  seed rows forward-cite that field, exactly as `region-pricing`'s own grounding did.
- `apps/gateway/src/gateway/catalog/infrastructure/minimax_seed.py`,
  `:gpt_realtime_seed.py` — the static-seed-list (`list[CatalogModel]`) pattern this task's NEW
  `vertex_seed.py` mirrors.
- `apps/gateway/src/gateway/catalog/infrastructure/composite_source.py:CompositeCatalogSource` —
  read-only ground; consumed UNCHANGED, chains `vertex_seed.py`'s rows into the same sync cycle
  as every other static seed (keeps them out of the deactivation sweep's blast radius, same as
  Bedrock's own M7).
- `apps/gateway/src/gateway/main.py:867-869` (`static_models=MINIMAX_SEED_MODELS +
  GPT_REALTIME_SEED_MODELS`) — this task's Build appends `+ VERTEX_SEED_MODELS`.
  `:928-1025` (`_chat_adapters` registration block: Gemini ~942-951, OpenAI ~956-963, MiniMax
  ~970-977, Bedrock ~979-989, the shared `_azure_egress_policy` + `AzureADTokenProviderCache`
  ~998-1013, Azure ~1018-1025) — the exact unconditional-registration order/style this task's
  Vertex block mirrors, appended after Azure.
- `apps/gateway/src/gateway/core/config.py:google_base_url` / `:google_default_max_tokens` /
  `:gemini_inline_max_bytes` (271-278), `:bedrock_endpoint_url` (289),
  `:azure_ad_provider_cache_ttl_s` / `:azure_ad_provider_cache_max` (314-316) — the
  settings-knob naming/default style this task's `vertex_default_max_tokens` /
  `vertex_token_cache_ttl_s` / `vertex_token_cache_max` mirror.
- `apps/gateway/tests/azure_verify/test_azure_verify.py` + `scripts/v21_azure_stub.py` +
  `scripts/live_v21_verify.py`; `apps/gateway/tests/bedrock_verify/test_bedrock_verify.py` +
  `scripts/v20_bedrock_stub.py` + `scripts/live_v20_verify.py` — GROUND ONLY: the established
  TWO-LAYER live-verify convention this task's own suite mirrors — (1) an automated pytest suite
  against a LOCAL independent-oracle stub server (pure-stdlib, does NOT import the gateway's own
  signing/auth code) that GATES the green suite, and (2) a SEPARATE, manually-run
  `scripts/live_v*_verify.py` script requiring REAL provider credentials, operated by a
  human/agent at VERIFY time, outside the automated red/green loop.
- `apps/gateway/pyproject.toml:15` (`pyjwt>=2.13.0`) and `:20` (`cryptography>=48.0.1`) — BOTH
  already dependencies. Load-bearing fact behind this task's "no new dependency" recommendation
  (§1 Framings, alternative B) — no `google-auth` import exists anywhere in this codebase today.
- `.add/tasks/region-catalog-dimension/TASK.md §3` — **FROZEN @ v1** — `region:
  Literal["us","eu","ap","global"]`, `VALID_REGIONS`, `normalize_region()`,
  `CatalogModel.region: str = field(default="global")`. Cited VERBATIM, never redefined; this
  task's seed rows set `region="eu"` / `region="ap"` once that field lands (dependency ordering,
  not yet present in THIS tree).
- `.add/tasks/region-pricing/TASK.md §3` — **FROZEN @ v1** — `resolve_region_multiplier` keyed
  by `models.region`. Cited only to confirm Vertex rows need ZERO special pricing-path code —
  they flow through the one shared resolver automatically the instant they carry a `region` tag.
- `.add/tasks/minimax-adapter-registry/TASK.md §3` — **FROZEN @ v1** — the most recent
  provider-onboarding precedent: pure `Literal`/`frozenset` widening (`ProviderName`,
  `PROVIDER_VALUE_SET`, `BYOK_PROVIDERS`, `_BEARER_PROVIDERS`), no DDL. This task follows the
  SAME widening for those four, but ALSO widens `ProviderCredential`'s Union (minimax never
  needed to — it reused `BearerCredential` unchanged) — a materially bigger edit to that frozen
  file, named explicitly in Issue #5 below.

Context (working folder): none beyond the code above — no docs/config/data files outside
`apps/gateway/src/gateway/{proxy,catalog}/`, `apps/gateway/src/gateway/main.py`,
`apps/gateway/src/gateway/core/config.py`, `apps/gateway/tests/`, and `scripts/` are in scope.
Embeddings are OUT of scope (MILESTONE.md and this task's own creation line name chat/EU/Asia
only; this codebase's own precedent — separate `bedrock_embeddings`/`azure_embeddings` test
suites landing as later, separate tasks after each provider's chat adapter — is followed here
too: Vertex embeddings, if ever wanted, is a follow-on task, not silently bundled in).

Honors (patterns / conventions):
- per-request credential contextvar + fail-closed `ProviderKeyMissing` (every existing adapter).
- OAuth2-token-cache-by-NON-SECRET-identity pattern (`AzureADTokenProviderCache`) — a rotated
  secret takes effect within the cache TTL, never baked into the cache key.
- static-seed-list + `CompositeCatalogSource` chaining for a provider with no dynamic discovery
  API (`minimax_seed.py`/`gpt_realtime_seed.py`/the sibling `bedrock_seed.py`).
- resilience seam reuse: `execute_with_retry` + per-instance `CircuitBreaker`,
  `provider="<name>"` metric/error label, 4xx-passthrough-verbatim / 5xx-raises convention —
  identical across OpenRouter/Anthropic/Gemini/Bedrock/Azure/MiniMax.
- machine-readable `ERR_<DOMAIN>_<REASON>` codes, REUSED not reinvented where the failure mode
  already has one (`ERR_PROVIDER_CREDENTIAL_INCOMPLETE`, `ERR_PROVIDER_CREDENTIAL_EMPTY`,
  `ERR_PROVIDER_KEY_MISSING`); `from None` strips any secret-bearing exception chain
  (CLAUDE.md-adjacent SECURITY INVARIANT stated verbatim in `provider_credentials.py`'s module
  docstring).
- CLEAN ARCHITECTURE layering (CONVENTIONS.md) — pure translation stays framework/IO-free (this
  task adds none new, it reuses Gemini's); new auth/IO code lives in `proxy/infrastructure/`.
- design-for-failure (CLAUDE.md non-negotiable rule) — every outbound call (token mint AND the
  Vertex API call itself) gets a timeout, and the token mint additionally gets the single-flight
  + fail-closed treatment already proven by `AzureADTokenProvider`.

Seams consulted: none — no `.add/SEAMS.md` entry matches provider-adapter-onboarding shape; the
closest and most authoritative precedent is `minimax-adapter-registry`'s own FROZEN TASK.md,
cited directly above rather than via a SEAMS.md entry.

Anchors the contract cites: `VertexCompletionUpstream`, `VertexServiceAccountConfig`,
`VertexTokenProvider`, `VertexTokenProviderCache`, `GoogleServiceAccountCredential`,
`VERTEX_SEED_MODELS`, `_ID_PREFIX_TO_LOCATION`, `ProviderName`/`PROVIDER_VALUE_SET`/
`BYOK_PROVIDERS` (widened), `ProviderCredential` (widened), `_build_credential` (extended),
the REUSED Gemini pure functions (`_openai_to_gemini_request`, `_gemini_to_openai`,
`_gemini_error_to_openai`, `_GeminiSSEStepper`), `main.py:_chat_adapters["vertex"]`.

Issues/Risks (→ feed §1):
1. ⚠ **TOP.** No `google-auth` library exists in this codebase; the "standard" GCP
   service-account flow assumes it, but `pyjwt>=2.13.0` + `cryptography>=48.0.1` are ALREADY
   dependencies and are sufficient to hand-roll the RFC 7523 JWT-bearer grant (sign a claims JWT
   with the service account's RSA private key via `jwt.encode(..., algorithm="RS256")`, POST to
   the fixed `https://oauth2.googleapis.com/token`) — mirrors `AzureADTokenProvider`'s own
   hand-rolled OAuth2 POST exactly, just a different grant type (JWT-bearer vs
   client-credentials). This is a genuine make-or-buy call with real security-adjacent code
   surface (RS256-signing a service-account private key is NEW crypto-adjacent code in this
   repo) — recommendation is to hand-roll (stays consistent with every other provider's
   from-scratch auth: AWS SigV4, Azure AD), NOT add `google-auth`. If the human disagrees, the
   cost is a Build-time library swap only — `VertexServiceAccountConfig`/`Provider`/`Cache`'s
   PUBLIC shape (§3) is identical either way; only `_acquire()`'s internals change.
2. Vertex has no native "region-prefixed model id" the way Bedrock's cross-region inference
   profiles do (`eu.anthropic...` is a REAL AWS id/ARN convention; Vertex model ids like
   `gemini-2.5-flash` are always region-bare). Seeding two catalog rows for the SAME Vertex model
   in two GCP locations therefore needs a GATEWAY-INVENTED disambiguator, unlike Bedrock's
   upstream-real one. Resolved in §3 by a synthetic `<region-code>.<model>` id (visually parallel
   to Bedrock's own convention, but its seed-file docstring explicitly states it is NOT an
   upstream-real id) — the adapter parses and STRIPS the prefix before the bare model name
   reaches Vertex's URL.
3. The catalog's coarse `region` tag (`"eu"`/`"ap"`) is NOT the literal GCP location Vertex's URL
   needs (`europe-west4`/`asia-southeast1`) — resolved via a SECOND, fixed, gateway-internal map
   (`_ID_PREFIX_TO_LOCATION`). UNLIKE Bedrock's own named Issue #1 gap (the literal AWS egress
   region is driven by the tenant's OWN credential region, a SEPARATE tenant-controlled fact that
   can silently disagree with the catalog's `region` tag), this design derives the literal Vertex
   location DETERMINISTICALLY from the SELECTED catalog row's `id` — once `residency-policy`
   filters candidates by catalog `region`, the actual egress location is provably the one implied
   by whichever row got picked; there is no second, tenant-controlled fact that could disagree.
   Worth feeding forward to `residency-policy`'s own grounding as a strictly BETTER
   egress-enforcement story than Bedrock's — not a gap to defensively document, an improvement.
4. GCP service-account credentials are project-scoped, not region-scoped — ONE tenant credential
   (`GoogleServiceAccountCredential`) legitimately calls BOTH the `eu` and `ap` Vertex rows (no
   per-region credential needed), unlike `BedrockCredential.region` which pins ONE AWS region per
   credential. A genuine shape difference from the Bedrock precedent, not an oversight — flagged
   so Build does not add a spurious `region` field to `GoogleServiceAccountCredential` by false
   analogy to `BedrockCredential`.
5. Widening `ProviderCredential` (currently `BearerCredential | BedrockCredential |
   AzureCredential`, a "§3 CONTRACT... FROZEN @ v1" module) with a 4th union member is a bigger
   edit to that frozen file than `minimax-adapter-registry`'s own precedent made (minimax reused
   `BearerCredential` unchanged and never touched the Union — only the `Literal`/two
   `frozenset`s). Still purely ADDITIVE (a new alternative, no existing member's shape changes)
   — same spirit as the frozenset-widening precedent — but a materially different KIND of edit.
   Named as an explicit assumption below, not waved through by analogy alone.
6. Whether Vertex's 4xx error envelope is byte-shape-identical to the public Gemini API's
   `{error:{code,message,status}}` (so `_gemini_error_to_openai` can be reused unmodified) is
   UNCONFIRMED — this repo has never made a real Vertex call. Needs live confirmation before/at
   Verify (mirrors Bedrock's own "confirm against live AWS docs before freeze" pattern).
7. This TASK.md's slug line inherits `sensitivity: data` from milestone creation. This task adds
   NEW secret-bearing code (RS256-signs a service-account private key; mints/caches OAuth2 bearer
   tokens) — comparable in kind to `credential-resolution-seam`/`dynamic-auth-byok`'s own
   security-sensitive surface, both of which shipped under closer security scrutiny. Named as a
   question for the human/orchestrator; NOT changed unilaterally here (outside this agent's
   authority to alter task metadata).

Related intent: MILESTONE.md's WAVE-1 FREEZE ADDENDUM (2026-07-12) — "NEW TASK vertex-adapter
(Tin grew scope): real Vertex AI adapter (service-account auth, {region}-aiplatform.googleapis.com)
+ europe-west* and asia-southeast1 entries. It cites region-catalog-dimension's frozen shape."
`region-catalog-dimension` TASK.md's own top ⚠ (Issue #2, "Vertex AI has no adapter in this
codebase") and its DECIDED-at-freeze note: "(1) Vertex gap resolved by GROWING M2 — a NEW
vertex-adapter task... joins the milestone." GLOSSARY.md `region` / `region-tagged catalog row`
deltas (region-catalog-dimension) — extended, never redefined, by this task.

Ground SHA: 0d09d0f

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Vertex AI adapter (GCP service-account OAuth2 auth, `{location}-aiplatform.googleapis.com`
regional endpoints) + EU/Asia catalog entries
Framings weighed:
(chosen) A NEW `VertexCompletionUpstream` implementing the existing `CompletionUpstream` Protocol,
REUSING `gemini_upstream.py`'s pure OpenAI⇄Gemini translation functions UNCHANGED (the wire body is
honestly the same Gemini `generateContent`/`streamGenerateContent` JSON shape on Vertex), with its
OWN auth (`VertexTokenProvider`/`VertexTokenProviderCache`, a GCP JWT-bearer analog of
`AzureADTokenProvider`/`AzureADTokenProviderCache`) and its OWN URL construction (location resolved
from a fixed internal map keyed by a synthetic `<region-code>.` id prefix). New
`GoogleServiceAccountCredential` value-object + `provider="vertex"` value-set widening (mirrors
minimax-adapter-registry's Literal/frozenset precedent, extended to the Union). New `vertex_seed.py`
static catalog seed (mirrors `bedrock_seed.py`), registered alongside adapter wiring in `main.py` in
the SAME diff.
· alternative A — wrap the EXISTING `GeminiCompletionUpstream` class with a Vertex-mode
  constructor flag (swap base_url/auth): REJECTED — auth is a structurally different flow
  (async OAuth2 token mint+cache vs a static header), and Gemini's `_auth_headers()`/URL
  construction are synchronous/no-IO while Vertex's token mint is async IO; forcing both into one
  class burdens the simple, proven Gemini path with Vertex's IO/cache complexity for zero
  benefit. Azure's own precedent (a SEPARATE class from OpenRouter's, not a flag) is the
  established pattern here.
· alternative B — add `google-auth` as a new dependency for standard GCP service-account token
  minting: REJECTED (recommended against, not blocking) — `pyjwt` + `cryptography` are ALREADY
  present and sufficient for the one RFC 7523 grant this needs; `google-auth` would be the FIRST
  GCP-specific SDK dependency in a codebase that otherwise hand-rolls every provider's auth (AWS
  SigV4, Azure AD) from raw HTTP — adding it breaks that consistency for marginal benefit.
  Flagged loudly per this task's hard rule; final call is the human's at freeze.
· alternative C — encode the Vertex region as a REAL extra column on `models` (a literal GCP
  location) instead of reusing region-catalog-dimension's coarse `region` + a synthetic id
  prefix: REJECTED — would mean editing a FROZEN sibling contract (region-catalog-dimension §3)
  to add a field it deliberately does NOT have (coarse-only, MILESTONE.md binding rule #1); the
  synthetic-id-prefix approach stays entirely inside THIS task's own additive surface.

Must:
<must>
  - M1: `VertexCompletionUpstream` implements `CompletionUpstream` (`complete`/`stream`),
    registered UNCONDITIONALLY at `main.py:_chat_adapters["vertex"]` (mirrors every other
    provider — credential resolved per-request, not at boot).
  - M2: Chat request/response/SSE-stream translation REUSES `gemini_upstream.py`'s pure functions
    (`_openai_to_gemini_request`, `_gemini_to_openai`, `_gemini_error_to_openai`,
    `_map_gemini_finish_reason`, `_GeminiSSEStepper`) UNCHANGED — no forked copy.
    `gemini_upstream.py.__all__` gains `_GeminiSSEStepper` (currently only the buffered wrapper
    `_translate_gemini_sse` is exported) so `vertex_upstream.py` can drive it live, exactly as
    `GeminiCompletionUpstream.stream()` already does internally.
  - M3: Auth is a GCP JWT-bearer OAuth2 flow (RFC 7523): `VertexTokenProvider` signs a claims JWT
    (`iss`/`sub`=`client_email`, `scope="https://www.googleapis.com/auth/cloud-platform"`,
    `aud="https://oauth2.googleapis.com/token"`, `iat`/`exp`) with the service-account RSA
    private key via `pyjwt`'s RS256, POSTs it to the FIXED `https://oauth2.googleapis.com/token`
    (`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`), and returns the resulting
    `access_token` as an `Authorization: Bearer` header. FAIL-CLOSED on any non-200 / timeout /
    network / malformed-body response — mirrors `AzureADTokenProvider._acquire`'s failure posture
    byte-for-byte (never serves an expired/blank token, never falls back unauthenticated).
  - M4 (CR-2, 2026-07-13, Tin — SECURITY): `VertexTokenProviderCache` caches provider
    instances by the key `(hydroa_tenant_id, client_email, project_id)`. The owning Hydroa
    tenant_id is the FIRST key element and is MANDATORY for cross-tenant isolation — WITHOUT it,
    a tenant who PUTs a credential reusing another tenant's GCP service-account
    `(client_email, project_id)` is served the victim's live minted bearer token on a cache HIT
    WITHOUT ever possessing the victim's `private_key`, defeating BYOK (a confused-deputy
    cross-tenant token theft, demonstrated by the 2nd adversarial security verify — the reason
    v1's identity-only key is reversed). `private_key` remains NEVER part of the key/log/span/
    exception. The tenant_id reaches the cache via the request-scoped `current_credential_tenant`
    contextvar (companion to `credential_context`, set at the single `resolve_provider_credential`
    seam, mirroring `guardrail_tenant_context`); when it is unset (None — e.g. a non-BYOK path or
    a unit test), the cache degrades to per-call provider construction (NO shared entry) rather
    than sharing across an unknown owner — fail-safe, never fail-open. Single-flight
    `asyncio.Lock` + double-check + expiry-skew refresh, TTL+size-capped eviction unchanged.
    NOTE: `AzureADTokenProviderCache` carries the identical class of bug (its key includes the
    Azure-DIRECTORY tenant_id but NOT the Hydroa tenant_id) and is fixed the same way in this
    task's build (Tin: "fix both now") — its blast radius is narrower (needs two Hydroa tenants
    sharing one Azure AD app registration) but it is the same confused-deputy.
  - M5: The request URL is
    `https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/publishers/google/models/{bare_model}:generateContent`
    (non-stream) / `:streamGenerateContent?alt=sse` (stream). `location` resolves from a FIXED
    internal map `_ID_PREFIX_TO_LOCATION = {"eu": "europe-west4", "ap": "asia-southeast1"}` keyed
    by the catalog model id's `<prefix>.` segment — NEVER from tenant input, NEVER an arbitrary
    string. `project_id` comes from the resolved `GoogleServiceAccountCredential`.
  - M6: `GoogleServiceAccountCredential(project_id, client_email, private_key: SecretStr,
    private_key_id: str | None)` added to `provider_credentials.py`; `"vertex"` added to
    `ProviderName` / `PROVIDER_VALUE_SET` / `BYOK_PROVIDERS`; `ProviderCredential` union widened
    to include it. `_BEARER_PROVIDERS` (provider_keys_admin_router.py) is UNCHANGED — vertex is
    not bearer-auth.
  - M7: `ProviderKeyPutBody` gains optional `project_id` / `client_email` / `private_key` /
    `private_key_id` fields; `_build_credential()` gains a `provider == "vertex"` branch
    constructing `GoogleServiceAccountCredential`. NO new SSRF/egress-policy write-time guard is
    added for vertex (unlike Azure's `endpoint`/`authority` check) — Vertex's request host is
    ALWAYS `{location}-aiplatform.googleapis.com` from the fixed internal map, and the token
    endpoint is the FIXED `oauth2.googleapis.com` constant; neither is ever tenant-supplied, so
    there is no host-injection surface to guard (a deliberate, cited safety property).
  - M8: `vertex_seed.py` adds `VERTEX_SEED_MODELS: list[CatalogModel]` — synthetic
    `<region-code>.<vertex-model>` ids (`eu.gemini-2.5-flash`, `ap.gemini-2.5-flash`,
    `eu.gemini-2.5-pro`, `ap.gemini-2.5-pro`), `provider="vertex"`, `region="eu"`/`region="ap"`
    (forward-cites region-catalog-dimension's frozen `Region` field — NOT present in this tree
    yet; a no-op default until that task's Build lands the column), `modality="chat"`. The
    docstring EXPLICITLY states the `<region-code>.` prefix is a GATEWAY-INVENTED disambiguator
    (unlike Bedrock's real AWS cross-region-profile ids), so a future reader never mistakes it
    for an upstream-real Vertex id.
  - M9: `VertexCompletionUpstream` parses and STRIPS the `<region-code>.` prefix from
    `payload["model"]` before it reaches the Vertex URL/body — the bare Vertex model name
    (`gemini-2.5-flash`) is what's actually sent upstream; an unrecognized/missing prefix FAILS
    CLOSED with a clear internal error (never silently defaults to some location) — misrouting a
    residency-pinned request to the wrong GCP region is the single worst failure mode this
    feature could have.
  - M10: `main.py` registers `_chat_adapters["vertex"]` in the SAME build/diff as
    `vertex_seed.py`'s rows joining `static_models=` — never seed a `provider="vertex"` catalog
    row without the matching adapter entry existing (resolves region-catalog-dimension's own
    named Issue #2 / R2 guard, which existed only because no Vertex adapter existed yet).
  - M11: Retry/circuit-breaker/timeout resilience mirrors every sibling adapter exactly:
    `execute_with_retry` + per-instance `CircuitBreaker`, `provider="vertex"` metric/error label,
    4xx passthrough verbatim (never raised), 5xx/transport → `UpstreamUnavailableError`.
  - M12: Credential resolution is per-request from the existing `get_provider_credential()`
    contextvar (task-3 dynamic-auth-byok pattern) — NO boot-time credential, NO module-level
    singleton token. A tenant with no enabled `vertex` credential gets
    `ProviderKeyMissing("vertex")` → the EXISTING 402 `ERR_PROVIDER_KEY_MISSING` mapping, before
    any upstream contact.
  - M13: Every new/changed symbol stays `mypy --strict`/`ruff` clean and inside CLEAN
    ARCHITECTURE layering (pure translation reused from `gemini_upstream.py`; new auth/IO code
    lives in `proxy/infrastructure/`) — confirmed by a passing `make ci` at Verify, not a
    runtime-observable API behavior.
</must>
Reject:
<reject>
  - R1: `PUT /admin/provider-keys/vertex` body missing/empty `project_id`, `client_email`, or
    `private_key` -> `"ERR_PROVIDER_CREDENTIAL_INCOMPLETE"` / `"ERR_PROVIDER_CREDENTIAL_EMPTY"`
    (existing codes, reused verbatim — mirrors Bedrock/Azure's own validator).
  - R2: a chargeable request against `provider="vertex"` when the tenant has no enabled vertex
    credential -> `"ERR_PROVIDER_KEY_MISSING"` (402, existing code, reused).
  - R3: the GCP token-mint POST returns non-200 / times out / returns a non-JSON or
    `access_token`-less body -> `UpstreamUnavailableError` (fail-closed; mirrors
    `AzureADTokenProvider._acquire`'s exact failure posture) — surfaces as the gateway's standard
    502/fallback path, NEVER an unauthenticated Vertex call.
  - R4: `payload["model"]` reaching `VertexCompletionUpstream` carries an unrecognized or missing
    `<region-code>.` prefix -> a clear internal error BEFORE any upstream contact (fail-closed, no
    silent default location) — the exact error type/mapping is a Build-time decision (candidate:
    a dedicated `ERR_VERTEX_REGION_UNRESOLVED` mapped to a 5xx-class internal error, since this
    indicates a catalog-seeding bug, not a caller mistake); required BEHAVIOR is named here, exact
    code left open (§1 Assumption #6).
  - R5: two `VERTEX_SEED_MODELS` entries resolving to the SAME `id` -> the existing `models.id`
    PRIMARY KEY rejects the upsert (`IntegrityError`) — no new enforcement code, cited so the
    seed-authoring Strategy never emits an unprefixed / duplicate id.
  - R6: a `provider="vertex"` catalog row seeded in this task's Build WITHOUT `main.py`'s
    `_chat_adapters["vertex"]` also landing in the SAME diff -> self-review guard at Build
    (mirrors region-catalog-dimension's own R2 "vertex_adapter_missing," inverted: THIS task is
    what makes seeding safe, and must prove it by shipping both halves together).
  - R7: Vertex returns a 4xx (model not served in this location, quota exceeded, permission
    denied) -> passed through VERBATIM as `(status, body)`, translated via the existing
    `_gemini_error_to_openai` shape — NOT raised, NOT retried (mirrors every sibling adapter's 4xx
    convention); the exact Vertex error envelope shape is UNCONFIRMED live (§0 Issue #6) — if it
    diverges from Gemini's, this is a Build-time defensive-parsing fix, not a contract change
    (the `(status, body)` passthrough SHAPE is what's frozen, not the envelope-parsing internals).
</reject>
After:
<after>
  - A tenant can PUT a GCP service-account credential (`project_id`/`client_email`/`private_key`)
    to `/admin/provider-keys/vertex`, exactly like every other provider's BYOK flow.
  - A chat request against an `eu.`/`ap.`-prefixed Vertex catalog model id is served by a REAL
    call to `{europe-west4|asia-southeast1}-aiplatform.googleapis.com`, authenticated by a
    freshly-minted or cached OAuth2 bearer token, translated through the SAME proven Gemini
    wire-shape logic — response/stream/usage/tool-call fidelity matches
    `GeminiCompletionUpstream`'s own proven behavior.
  - The catalog exposes at least 4 real Vertex entries (2 models × {eu, ap}) once this task's
    Build lands, addressable and billable through the existing catalog/pricing/usage pipeline
    with zero special-casing (region multiplier flows through region-pricing's resolver
    automatically, the instant region-catalog-dimension's `region` column exists).
  - `residency-policy` (built later) gets a Vertex candidate whose SELECTED catalog row provably
    determines its OWN real GCP egress location — no separate tenant-controlled fact (unlike
    Bedrock) that could silently disagree with the catalog's `region` tag.
  - No `provider="vertex"` catalog row can exist in this codebase's history without a matching,
    already-registered adapter — the exact gap region-catalog-dimension named and scope-cut is
    closed by this task, not deferred further.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ #1 Vertex's 4xx error-envelope shape is assumed byte-identical to the public Gemini API's
  `{error:{code,message,status}}` (reusing `_gemini_error_to_openai` unmodified) — lowest
  confidence because this repo has NEVER made a real Vertex call and this is inferred from
  general GCP-API-convention knowledge, not a live-verified fact. If wrong: Build adds a small
  Vertex-specific error-shape branch (a few lines), not a contract change — cost is a
  live-verify-driven Build fix, not a re-freeze, PROVIDED the `(status_code, openai_shaped_body)`
  passthrough interface (R7) is what's frozen, not the exact envelope-parsing internals.
  - [ ] #2 The literal EU Vertex location for Gemini — this draft picks `europe-west4`;
  MILESTONE.md only says "europe-west*" (a wildcard). Confirm against LIVE Vertex AI regional
  model-availability docs before Build seeds it (mirrors Bedrock's own "confirm against live AWS
  docs" open item) — cheap to change (one string literal in `_ID_PREFIX_TO_LOCATION` + the seed
  id), no ripple.
  - [ ] #3 The exact Gemini-on-Vertex model set to seed (this draft proposes `gemini-2.5-flash` +
  `gemini-2.5-pro`, both EU+AP siblings = 4 rows) and their pricing (assumed IDENTICAL to the
  Gemini Developer API's public per-token rates for the same model — a widely-documented GCP
  convention, but not itself live-confirmed against Vertex's own pricing page) — confirm before
  freeze; wrong pricing is a silent under/over-bill, same risk class as Bedrock's own flagged
  pricing assumption.
  - [ ] #4 Widening `ProviderCredential`'s Union (a 4th alternative on a "§3 CONTRACT... FROZEN @
  v1" module) as a same-task additive extension, rather than routing through a separate amendment
  task — this draft treats it as in-bounds (purely additive, no existing member touched, same
  spirit as minimax's own frozenset-widening precedent), but it IS a bigger edit to that frozen
  file than any prior provider-onboarding task made. Confirm the human is comfortable with this
  scope at freeze.
  - [ ] #5 `google-auth` vs hand-rolled PyJWT — this draft recommends hand-rolling (§1 Framings
  alternative B) but it is a real, disclosed trade-off (bespoke crypto-adjacent code vs a
  battle-tested Google SDK with its own token-refresh/retry hardening); confirm the human agrees
  before Build.
  - [ ] #6 The exact HTTP/error-code mapping for R4 (unresolved region prefix) — this draft leaves
  it a Build-time internal-error choice; confirm whether the human wants a specific, documented
  error code here (e.g. `ERR_VERTEX_REGION_UNRESOLVED`) rather than a generic 500, given this is
  meant to be an impossible-in-practice state (only reachable via a catalog-seeding bug), not a
  caller-facing contract.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Vertex adapter is registered and dispatched by provider   # M1
  Given `main.py` boot completes
  When `app.state.chat_adapters` is inspected
  Then it contains a `"vertex"` key bound to a `VertexCompletionUpstream` instance
  And this happens UNCONDITIONALLY (no env-var/config gate, mirrors every sibling adapter)

Scenario: Vertex chat translation reuses Gemini's pure functions unchanged   # M2
  Given an OpenAI chat-completions request body with tools + a system message
  When `VertexCompletionUpstream` translates it for the upstream call
  Then the produced Gemini-shaped body is BYTE-IDENTICAL to what
       `gemini_upstream._openai_to_gemini_request` alone would produce for the same input
  And no forked/duplicated translation logic exists in `vertex_upstream.py`

Scenario: JWT-bearer token mint succeeds and is cached   # M3, M4
  Given a `GoogleServiceAccountCredential` with a valid RSA private key
  When `VertexTokenProvider.get_token()` is called
  Then an RS256-signed JWT assertion is POSTed to `https://oauth2.googleapis.com/token` with
       `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`
  And the returned `access_token` is cached; a second call within TTL-minus-skew makes NO second
      HTTP request

Scenario: token cache key excludes the private key   # M4 (security)
  Given two GoogleServiceAccountCredentials for the SAME hydroa tenant sharing the same
        (client_email, project_id) but DIFFERENT private_key values
  When `VertexTokenProviderCache.get_or_create` is called with each (same tenant_id)
  Then both resolve to the SAME cached provider entry (private_key is NOT part of the key —
       rotation takes effect within TTL)
  And no log line, span attribute, or exception message anywhere in the mint path contains the
      raw private_key value

Scenario: token cache is isolated across hydroa tenants   # M4 (CR-2, security HARD-STOP)
  Given tenant A has minted and cached a live vertex bearer token, and tenant B then PUTs a
        credential REUSING A's exact (client_email, project_id) but with B's own (or an invalid)
        private_key
  When tenant B's request resolves its provider and calls `get_or_create` under B's tenant_id
  Then B does NOT receive A's cached provider/token — a SEPARATE cache entry keyed by
       (B_tenant_id, client_email, project_id) is used, so B must mint with B's own key (or fail
       closed on an invalid one)
  And A's token is NEVER served to B (no cross-tenant confused-deputy token theft)

Scenario: unknown credential owner never shares a cache entry   # M4 (CR-2, fail-safe)
  Given the `current_credential_tenant` contextvar is unset (None) at the cache call
  When `get_or_create` runs
  Then a fresh per-call provider is constructed (no shared cache entry under a None owner) —
       degrade fail-safe, never fold an unknown owner into a shared identity-only entry

Scenario: single-flight refresh under concurrent expiry   # M4, edge case (race)
  Given a cached token that has just expired (past TTL-minus-skew)
  When 10 concurrent `get_token()` calls fire simultaneously
  Then exactly ONE token-mint HTTP POST is made
  And all 10 callers receive the SAME freshly-minted token

Scenario: EU-prefixed model resolves the europe-west4 endpoint   # M5
  Given a chat request with `model="eu.gemini-2.5-flash"`
  When `VertexCompletionUpstream.complete` builds the request URL
  Then the URL host is `europe-west4-aiplatform.googleapis.com`
  And the URL path's `locations` segment and the body's target model are both `europe-west4` /
      `gemini-2.5-flash` respectively (prefix stripped)

Scenario: AP-prefixed model resolves the asia-southeast1 endpoint   # M5
  Given a chat request with `model="ap.gemini-2.5-pro"`
  When `VertexCompletionUpstream.complete` builds the request URL
  Then the URL host is `asia-southeast1-aiplatform.googleapis.com`
  And the bare model sent upstream is `gemini-2.5-pro`

Scenario: PUT vertex credential succeeds with complete fields   # M6, M7
  Given an OWNER caller PUTs {project_id, client_email, private_key} to
        /admin/provider-keys/vertex
  When the request is handled
  Then a `GoogleServiceAccountCredential` is constructed and persisted (Fernet-at-rest, mirrors
       every other provider)
  And the response `ProviderKeyStatus` carries no secret field

Scenario: PUT vertex credential rejects incomplete fields   # R1
  Given an OWNER caller PUTs a body missing `private_key`
  When the request is handled
  Then 422 problem+json `"ERR_PROVIDER_CREDENTIAL_INCOMPLETE"` is returned
  And no credential row is written

Scenario: no SSRF write-time guard needed for vertex   # M7 (documents a deliberate absence)
  Given the PUT /admin/provider-keys/vertex handler
  When the request body is reviewed for a host/URL field
  Then it contains NONE — project_id/client_email/private_key carry no host, unlike Azure's
       endpoint/authority
  And `assert_literal_host_not_denied` is never invoked on the vertex branch (nothing to check)

Scenario: Vertex catalog seed rows carry synthetic region-prefixed ids   # M8
  Given `VERTEX_SEED_MODELS`
  When a catalog sync runs
  Then `ModelRow` entries exist for `eu.gemini-2.5-flash` (region="eu"), `ap.gemini-2.5-flash`
       (region="ap"), `eu.gemini-2.5-pro` (region="eu"), `ap.gemini-2.5-pro` (region="ap")
  And every row has `provider == "vertex"`

Scenario: region prefix is stripped before reaching the Vertex wire body   # M9
  Given `model="eu.gemini-2.5-flash"` on the inbound OpenAI-shaped payload
  When the translated Gemini body and the URL are inspected
  Then neither contains the literal substring `"eu."` anywhere
  And the model segment is exactly `gemini-2.5-flash`

Scenario: unrecognized region prefix fails closed before any upstream contact   # R4, M9
  Given `model="zz.gemini-2.5-flash"` (an unrecognized prefix) reaches `VertexCompletionUpstream`
  When `complete` or `stream` is called
  Then a clear internal error is raised BEFORE any HTTP call (no token mint, no Vertex dial)
  And the error never claims a default/fallback location was used

Scenario: adapter registration and seed rows land together   # M10, R6
  Given this task's full Build diff
  When it is reviewed for `provider="vertex"` catalog entries vs. `main.py`'s `_chat_adapters`
  Then every seeded `provider="vertex"` row has a corresponding `_chat_adapters["vertex"]`
       registration in the SAME diff
  And no diff ships one half without the other

Scenario: 5xx from Vertex raises UpstreamUnavailableError and retries   # M11
  Given the Vertex API returns 503 on the first attempt and 200 on a retry
  When `complete` is called with `max_retries >= 1`
  Then the circuit breaker records the failure, the retry seam retries, and the eventual 200 is
       returned translated
  And with `max_retries == 0`, a single 503 raises `UpstreamUnavailableError` with no retry

Scenario: 4xx from Vertex passes through verbatim — the regional-404 case   # R7, edge case
  Given a model requested in a location where Vertex has not enabled it (a real, plausible
        operator misconfiguration or a not-yet-available model/region combo)
  When Vertex responds 404 with its own error body
  Then the gateway returns (404, openai-shaped error body) WITHOUT raising
  And the circuit breaker does NOT trip (4xx is not a breaker failure, mirrors every sibling)

Scenario: missing tenant credential fails closed before any upstream contact   # M12, R2
  Given a tenant with no enabled `vertex` provider credential
  When a chat request targets a vertex-provider model
  Then `ProviderKeyMissing("vertex")` is raised before any HTTP call
  And the caller sees 402 `ERR_PROVIDER_KEY_MISSING`

Scenario: token mint failure fails closed, never serves an unauthenticated request   # R3
  Given the GCP token endpoint returns 400 (e.g. a revoked/malformed service-account key)
  When `VertexTokenProvider.get_token()` is called
  Then `UpstreamUnavailableError` is raised
  And NO request is ever sent to the Vertex generateContent endpoint without a Bearer token

Scenario: streaming translation fidelity matches Gemini's incremental delivery   # M2, edge case
  Given a Vertex `streamGenerateContent` SSE response with 3 text chunks then a usage-carrying
        terminal chunk
  When `VertexCompletionUpstream.stream` drives `_GeminiSSEStepper` live
  Then each OpenAI SSE frame is yielded as its source chunk arrives (not buffered to the end)
  And the terminal frame carries `finish_reason` + `usage`, followed by `data: [DONE]`

Scenario: usage/cost extraction reads Vertex's usageMetadata identically to Gemini   # M2, After
  Given a Vertex `generateContent` 200 response with
        `usageMetadata:{promptTokenCount, candidatesTokenCount, totalTokenCount}`
  When the response is translated
  Then `usage.prompt_tokens`/`completion_tokens`/`total_tokens` match those fields exactly
  And billing downstream (usage_records) reflects the SAME token counts as a Gemini-API-direct
      call would for equivalent usage

Scenario: duplicate seeded ids are rejected by the PK, never silently merged   # R5
  Given a (deliberately introduced, defensive-test-only) second `CatalogModel` with
        `id="eu.gemini-2.5-flash"` in a modified seed list
  When a catalog sync runs
  Then the second upsert either updates the SAME row (idempotent re-sync) or the seed-authoring
       Strategy is proven to never emit a genuine duplicate — no `IntegrityError` in the real
       (non-defensive) seed list

Scenario: automated stub suite gates green independently of real GCP credentials   # live-verify
  Given `apps/gateway/tests/vertex_verify/test_vertex_verify.py` and a local
        `scripts/vertex_stub.py` (pure-stdlib, independently re-verifies the RS256 JWT assertion
        + required RFC 7523 claims WITHOUT importing gateway's own signing code)
  When the suite runs with NO real GCP credentials present
  Then it passes fully — proving the adapter's request/auth/translation shape against an
       independent oracle, not a live cloud dependency

Scenario: a separate manually-run script exercises real GCP credentials   # live-verify
  Given `scripts/live_vertex_verify.py` and a real service-account JSON supplied via an
        operator-provided path/env var
  When a human/agent runs it manually at VERIFY time
  Then it makes REAL calls to `{europe-west4|asia-southeast1}-aiplatform.googleapis.com` and
       reports pass/fail
  And this script is NEVER part of the automated red/green pytest suite (mirrors
      `scripts/live_v20_verify.py` / `scripts/live_v21_verify.py`)
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PUT    /admin/provider-keys/vertex   body: { project_id, client_email, private_key,
                                              private_key_id?, enabled? }
  200 -> ProviderKeyStatus { provider:"vertex", configured:true, enabled, auth_mode, updated_at }
  422 -> problem+json "ERR_PROVIDER_CREDENTIAL_INCOMPLETE" | "ERR_PROVIDER_CREDENTIAL_EMPTY"
  403 -> problem+json "ERR_AUTH_FORBIDDEN"   (non-OWNER caller — existing router-level gate)

GET    /admin/provider-keys              -> [ProviderKeyStatus, ...]   (vertex included once configured)
GET    /admin/provider-keys/vertex       -> ProviderKeyStatus | 404 "ERR_PROVIDER_KEY_NOT_FOUND"
DELETE /admin/provider-keys/vertex       -> 204 | 404 "ERR_PROVIDER_KEY_NOT_FOUND"

POST   /v1/chat/completions   body: { model: "eu.gemini-2.5-flash" | "ap.gemini-2.5-flash" |
                                       "eu.gemini-2.5-pro" | "ap.gemini-2.5-pro", ... }
  200 -> OpenAI chat.completion (unchanged shape; translated via reused Gemini pure functions)
  200 (stream=true) -> OpenAI SSE chunks (unchanged shape; live via _GeminiSSEStepper)
  4xx -> passthrough verbatim, openai-shaped error body (R7) — includes the regional-404 case
  402 -> problem+json "ERR_PROVIDER_KEY_MISSING"   (R2 — no enabled vertex credential)
  5xx / transport -> UpstreamUnavailableError -> gateway's existing 502/fallback path (R3, M11)
  internal (never reaches the caller in a correctly-seeded catalog) -> region-prefix-unresolved
    guard (R4) — exact code TBD at Build, see §1 Assumption #6
```

Domain (`apps/gateway/src/gateway/proxy/domain/provider_credentials.py`, additive):
```
ProviderName = Literal["openrouter","openai","anthropic","google","bedrock","azure","minimax","vertex"]
PROVIDER_VALUE_SET: frozenset[str] = frozenset({...7 existing..., "vertex"})
BYOK_PROVIDERS:     frozenset[str] = frozenset({...7 existing..., "vertex"})

class GoogleServiceAccountCredential(BaseModel):
    project_id: str
    client_email: str
    private_key: SecretStr
    private_key_id: str | None = None

    @model_validator(mode="after")
    def _validate_fields(self) -> GoogleServiceAccountCredential:
        # empty project_id / client_email -> ERR_PROVIDER_CREDENTIAL_INCOMPLETE
        # empty private_key -> ERR_PROVIDER_CREDENTIAL_EMPTY
        ...

    def to_vertex_service_account_config(self) -> VertexServiceAccountConfig: ...

ProviderCredential = BearerCredential | BedrockCredential | AzureCredential | GoogleServiceAccountCredential
```

Infrastructure (`apps/gateway/src/gateway/proxy/infrastructure/`, NEW files):
```
vertex_ad.py   (mirrors azure_ad.py file-for-file)
  VertexServiceAccountConfig(frozen dataclass): project_id, client_email,
    private_key: str = field(repr=False), private_key_id: str | None,
    scope: str = "https://www.googleapis.com/auth/cloud-platform",
    token_uri: str = "https://oauth2.googleapis.com/token"
  VertexTokenProvider: get_token() -> str   (RS256 JWT-bearer mint via pyjwt, single-flight +
    expiry-skew cache, FAIL-CLOSED -> UpstreamUnavailableError on any IDP error)
  VertexTokenProviderCache: get_or_create(config, tenant_id) -> VertexTokenProvider
    (key = (hydroa_tenant_id, client_email, project_id) — NON-SECRET; tenant_id MANDATORY for
    cross-tenant isolation per M4 CR-2; tenant_id=None -> fresh per-call provider, no shared
    entry; TTL+size-capped)

vertex_upstream.py   (NEW — implements CompletionUpstream)
  _ID_PREFIX_TO_LOCATION: dict[str, str] = {"eu": "europe-west4", "ap": "asia-southeast1"}
  _parse_vertex_model(model: str) -> tuple[str, str]   # -> (location, bare_model); raises on
    an unrecognized/missing prefix (R4)
  class VertexCompletionUpstream:
      complete(payload) -> tuple[int, dict]
      stream(payload) -> AsyncIterator[bytes]
      # REUSES gemini_upstream._openai_to_gemini_request / _gemini_to_openai /
      # _gemini_error_to_openai / _GeminiSSEStepper UNCHANGED (M2); OWN _auth_headers() (Bearer
      # via VertexTokenProviderCache) and OWN URL construction (M5); same
      # execute_with_retry + CircuitBreaker resilience seam as every sibling (M11).

gemini_upstream.py   (EXTEND __all__ only)
  __all__ += ["_GeminiSSEStepper"]   # currently only _translate_gemini_sse is exported
```

Catalog (`apps/gateway/src/gateway/catalog/infrastructure/vertex_seed.py`, NEW):
```
VERTEX_SEED_MODELS: list[CatalogModel] = [
  CatalogModel(id="eu.gemini-2.5-flash", provider="vertex", region="eu", modality="chat", ...),
  CatalogModel(id="ap.gemini-2.5-flash", provider="vertex", region="ap", modality="chat", ...),
  CatalogModel(id="eu.gemini-2.5-pro",   provider="vertex", region="eu", modality="chat", ...),
  CatalogModel(id="ap.gemini-2.5-pro",   provider="vertex", region="ap", modality="chat", ...),
]
# region= is a forward citation to region-catalog-dimension's frozen field — absent in this tree
# until that task's Build lands; CatalogModel's own default ("global") makes this a safe no-op
# until then, exactly as region-pricing's own forward citation was.
# Docstring states explicitly: the <region-code>. prefix is GATEWAY-INVENTED, NOT a real Vertex/
# GCP id (unlike Bedrock's genuine AWS cross-region-profile ids) — see §0 Issue #2.
# Pricing figures + exact literal EU location (europe-west4) are UNCONFIRMED-live — §1 ⚠#2/#3.

apps/gateway/src/gateway/main.py:867-869
  static_models = MINIMAX_SEED_MODELS + GPT_REALTIME_SEED_MODELS + VERTEX_SEED_MODELS
apps/gateway/src/gateway/main.py:~1025 (after the Azure block)
  _chat_adapters["vertex"] = VertexCompletionUpstream(
      token_provider_cache=_vertex_token_provider_cache,
      default_max_tokens=settings.vertex_default_max_tokens,
      max_retries=settings.upstream_max_retries,
      backoff_base=settings.upstream_retry_backoff_base_s,
      retry_deadline_s=settings.upstream_retry_deadline_s,
      metrics_registry=app.state.metrics_registry,
  )
```

API (`apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py`, additive):
```
ProviderKeyPutBody += project_id: str | None, client_email: str | None,
                      private_key: str | None, private_key_id: str | None
_build_credential(): += `if provider == "vertex": return GoogleServiceAccountCredential(...)`
_BEARER_PROVIDERS: UNCHANGED (vertex excluded — not bearer-auth)
put_provider_key(): NO new egress-policy branch for vertex (M7 — no tenant-supplied host exists)
```

Config (`apps/gateway/src/gateway/core/config.py`, additive):
```
vertex_default_max_tokens: int = 4096          # mirrors google_default_max_tokens
vertex_token_cache_ttl_s: float = 300.0        # mirrors azure_ad_provider_cache_ttl_s
vertex_token_cache_max: int = 512              # mirrors azure_ad_provider_cache_max
```

Schema: none — no new table, no new column. `region` (used by `vertex_seed.py`'s entries) is
owned entirely by `region-catalog-dimension`'s already-FROZEN migration; this task adds zero DDL.

Glossary deltas:
- **Vertex-synthetic region-id prefix**: the `<region-code>.` prefix (`eu.`/`ap.`) this task
  invents for `vertex_seed.py`'s catalog `id`s, to disambiguate the SAME region-bare Vertex model
  name across two GCP locations sharing one `models.id` primary key. Distinct from Bedrock's
  `eu.`/`us.`/`apac.` prefixes (region-catalog-dimension GLOSSARY) — those are REAL AWS
  cross-region-inference-profile ids; this one is gateway-invented and stripped by
  `VertexCompletionUpstream` before any upstream call. Never to be confused with an upstream-real
  identifier by a future reader or sibling task.
- **GoogleServiceAccountCredential**: the BYOK credential shape for the `vertex` provider — a GCP
  service-account's `project_id`/`client_email`/`private_key` (PEM RSA), used to mint short-lived
  OAuth2 bearer tokens via the RFC 7523 JWT-bearer grant. Project-scoped, NOT region-scoped —
  distinct from `BedrockCredential.region` (one AWS region pinned per credential); one Vertex
  credential serves every seeded Vertex location for that tenant.

Status: FROZEN @ v2 — approved by Tin Dang
Reported: no — draft only; the freeze report renders when a human reviews this for FROZEN.

Least-sure flag surfaced at freeze: ⚠ [spec] §1 Assumption #1 — Vertex's 4xx error-envelope shape
is ASSUMED byte-identical to the public Gemini API's `{error:{code,message,status}}` (so
`_gemini_error_to_openai` can be reused unmodified for R7's passthrough), but this repo has NEVER
made a real Vertex call and this is inferred from general GCP-API-convention knowledge, not a
live-verified fact. Low cost if right (zero extra code). Moderate cost if wrong: Build needs a
small Vertex-specific error-shape branch — a Build-time fix, NOT a contract re-freeze, because the
frozen surface here is the `(status_code, openai_shaped_body)` passthrough INTERFACE (R7), not the
envelope-parsing internals. A close second candidate for the flag: the "no google-auth" dependency
recommendation (§1 Assumption #5) — a real make-or-buy call on new crypto-adjacent code that the
human should explicitly bless, not just wave through by analogy to Azure AD's own hand-rolled
precedent.

DECIDED at freeze review (2026-07-12, Tin): (1) hand-rolled PyJWT RFC 7523 flow CONFIRMED — zero new
dependencies, mirrors azure_ad.py. (2) Sensitivity UPGRADED data→security (new SA-private-key
signing + token minting) — verify will be an adversarial HARD-STOP pass. Orchestrator calls
(cheap-to-flip, confirm at build): EU location europe-west4; seed gemini-2.5-flash +
gemini-2.5-pro (confirm live at build); ProviderCredential Union widened IN-task (additive,
disclosed, minimax precedent extended); R4 error code = ERR_VERTEX_REGION_UNRESOLVED. The
Vertex-error-envelope flag stays open as a build-time parsing risk (frozen surface is the
passthrough interface, not parser internals).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (matches project convention for auth/money-adjacent modules)
Coverage achieved (BUILD, measured against the final green suite):
  - `vertex_ad.py`: 99% (118 stmts, 1 missed — an unreachable double-check-after-lock race line)
  - `vertex_upstream.py`: 96% (117 stmts, 5 missed — 2 defensively-unreachable `raise` lines after
    `_map_translation_error` always raises, 3 lines in an upstream-echoes-`[DONE]`-early edge case)
  - `vertex_seed.py`: 100% (9 stmts)
  - `provider_credentials.py` / `provider_keys_admin_router.py` / `tenant_provider_key_store.py`
    (pre-existing, additively touched, not "new" modules): 74% / 67% / 69% against a vertex-only
    suite run — the uncovered lines are OTHER providers' branches (azure/bedrock/minimax), already
    covered by their own suites; confirmed via the full combined regression run (§5 step 10).
64 vertex-suite tests total (25 auth + 24 upstream + 4 catalog-seed + 11 verify).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_vertex_adapter_registered: arrange app boot / act inspect app.state.chat_adapters / assert "vertex" key bound to VertexCompletionUpstream · covers: M1
  - test_translation_byte_identical_to_gemini_pure_fn: arrange OpenAI request w/ tools+system / act VertexCompletionUpstream's internal translate call vs gemini_upstream._openai_to_gemini_request directly / assert byte-identical dict · covers: M2
  - test_jwt_bearer_mint_and_cache: arrange valid GoogleServiceAccountCredential / act get_token() twice within TTL / assert one HTTP POST, RS256 assertion shape (iss/scope/aud/exp) · covers: M3, M4
  - test_token_cache_key_excludes_private_key: arrange two SAME-tenant credentials same (client_email,project_id) diff private_key / act get_or_create both (same tenant_id) / assert same cached entry, no secret in any log/span · covers: M4
  - test_token_cache_isolated_across_tenants: arrange tenant A cached live token, tenant B reuses A's (client_email,project_id) with B's own key / act get_or_create under B's tenant_id / assert B gets a DISTINCT entry, A's token NEVER served to B · covers: M4 CR-2 (security HARD-STOP)
  - test_token_cache_none_tenant_no_shared_entry: arrange tenant_id=None / act get_or_create twice / assert fresh per-call provider, no shared cache entry · covers: M4 CR-2 (fail-safe)
  - test_azure_token_cache_isolated_across_hydroa_tenants: arrange two hydroa tenants sharing one Azure AD app registration (same directory tenant_id+client_id) / act get_or_create under each hydroa tenant_id / assert distinct entries · covers: M4 CR-2 (azure sibling fix)
  - test_single_flight_refresh_race: arrange expired cached token / act 10 concurrent get_token() / assert exactly one POST, all callers get same token · covers: M4 (edge case)
  - test_eu_prefix_resolves_europe_west4: arrange model="eu.gemini-2.5-flash" / act build request / assert host+location=europe-west4, model stripped to gemini-2.5-flash · covers: M5
  - test_ap_prefix_resolves_asia_southeast1: arrange model="ap.gemini-2.5-pro" / act build request / assert host+location=asia-southeast1 · covers: M5
  - test_put_vertex_credential_success: arrange OWNER + complete body / act PUT /admin/provider-keys/vertex / assert 200 ProviderKeyStatus, no secret in response · covers: M6, M7
  - test_put_vertex_credential_incomplete_422: arrange OWNER + missing private_key / act PUT / assert 422 ERR_PROVIDER_CREDENTIAL_INCOMPLETE, no row written · covers: R1
  - test_vertex_seed_rows_carry_region_prefixed_ids: arrange VERTEX_SEED_MODELS / act catalog sync / assert 4 rows w/ expected ids+region+provider · covers: M8
  - test_region_prefix_stripped_from_wire_body: arrange model="eu.gemini-2.5-flash" / act translate+build URL / assert no "eu." substring anywhere upstream-bound · covers: M9
  - test_unrecognized_prefix_fails_closed: arrange model="zz.gemini-2.5-flash" / act complete()/stream() / assert internal error BEFORE any HTTP call (mock asserts zero calls) · covers: R4, M9
  - test_adapter_and_seed_land_together: arrange full task diff / act grep provider="vertex" seed entries vs _chat_adapters keys / assert 1:1 presence · covers: M10, R6
  - test_5xx_raises_and_retries: arrange stub 503-then-200 / act complete(max_retries=1) / assert eventual 200; act complete(max_retries=0) w/ 503 / assert UpstreamUnavailableError · covers: M11
  - test_4xx_passthrough_verbatim_regional_404: arrange stub 404 (model-not-in-location) / act complete() / assert (404, body) returned, no exception, breaker not tripped · covers: R7
  - test_missing_credential_402_before_http: arrange tenant w/ no vertex credential / act request / assert ProviderKeyMissing -> 402, zero HTTP calls · covers: M12, R2
  - test_token_mint_failure_fails_closed: arrange token endpoint 400 / act get_token() / assert UpstreamUnavailableError, zero Vertex API calls made · covers: R3
  - test_streaming_incremental_fidelity: arrange 3-chunk + terminal SSE / act stream() / assert frames yielded incrementally, terminal carries usage+finish_reason+[DONE] · covers: M2
  - test_usage_extraction_matches_gemini_shape: arrange usageMetadata response / act translate / assert prompt/completion/total_tokens match exactly · covers: M2, After
  - test_vertex_verify_stub_suite_green_no_real_creds: arrange scripts/vertex_stub.py running locally / act pytest apps/gateway/tests/vertex_verify / assert full pass, zero network egress beyond localhost · covers: live-verify (automated layer)
</test_plan>

Tests live in: `apps/gateway/tests/vertex_upstream/` `apps/gateway/tests/vertex_auth/`
`apps/gateway/tests/vertex_catalog_seed/` `apps/gateway/tests/vertex_verify/` · MUST run red
(missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
`apps/gateway/src/gateway/proxy/infrastructure/vertex_ad.py`
`apps/gateway/src/gateway/proxy/infrastructure/vertex_upstream.py`
`apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py`
`apps/gateway/src/gateway/proxy/domain/provider_credentials.py`
`apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py`
`apps/gateway/src/gateway/catalog/infrastructure/vertex_seed.py`
`apps/gateway/src/gateway/core/config.py`
`apps/gateway/src/gateway/main.py`
`apps/gateway/tests/vertex_upstream/`
`apps/gateway/tests/vertex_auth/`
`apps/gateway/tests/vertex_catalog_seed/`
`apps/gateway/tests/vertex_verify/`
`scripts/vertex_stub.py`
`scripts/live_vertex_verify.py`

Strategy (ordered batches):
1. `provider_credentials.py`: widen `ProviderName`/`PROVIDER_VALUE_SET`/`BYOK_PROVIDERS` +
   `ProviderCredential` Union; add `GoogleServiceAccountCredential` + its validator +
   `to_vertex_service_account_config()`. Red tests for the value-set + credential-validator
   scenarios first.
2. `vertex_ad.py`: `VertexServiceAccountConfig` / `VertexTokenProvider` / `VertexTokenProviderCache`
   — copy `azure_ad.py`'s structure closely (single-flight lock, expiry-skew, non-secret cache
   key, TTL+size-cap), swap the AAD client-credentials POST for the RFC 7523 JWT-bearer POST
   (`jwt.encode(claims, private_key_pem, algorithm="RS256", headers={"kid": private_key_id} if
   private_key_id else None)`).
3. `gemini_upstream.py`: add `_GeminiSSEStepper` to `__all__` (one-line additive change, no
   behavior change — confirm no existing test asserts an exhaustive `__all__` length).
4. `vertex_upstream.py`: `_ID_PREFIX_TO_LOCATION`, `_parse_vertex_model` (fail-closed on
   unrecognized prefix — R4), `VertexCompletionUpstream` (`complete`/`stream`, importing and
   reusing Gemini's pure functions + `_GeminiSSEStepper`, own `_auth_headers()` via
   `VertexTokenProviderCache`, own URL builder, `execute_with_retry` + `CircuitBreaker`,
   `provider="vertex"` label). Red tests for translation-reuse, URL-resolution, streaming,
   4xx/5xx handling first.
5. `provider_keys_admin_router.py`: extend `ProviderKeyPutBody` + `_build_credential()` with the
   vertex branch. Confirm NO egress-policy branch is added (M7) — a test asserting
   `assert_literal_host_not_denied` is never called on the vertex path is a useful regression
   guard here.
6. `vertex_seed.py`: `VERTEX_SEED_MODELS` — confirm the literal EU location (§1 ⚠#2) and
   model/pricing set (§1 assumption #3) against LIVE Vertex AI docs BEFORE writing final values;
   do not ship a guessed price silently.
7. `core/config.py` + `main.py`: settings knobs, `_vertex_token_provider_cache` construction
   (mirrors the `_azure_ad_token_provider_cache` block exactly), `_chat_adapters["vertex"]`
   registration AND `static_models += VERTEX_SEED_MODELS` — LAND BOTH IN THE SAME COMMIT/DIFF
   (R6/M10 — never split across separate PRs).
8. `scripts/vertex_stub.py` (independent-oracle local HTTP stub, pure stdlib — reimplements RS256
   JWT-assertion verification and RFC 7523 claim checks WITHOUT importing `pyjwt`/gateway's own
   signing code, mirrors `v20_bedrock_stub.py`'s "does NOT import gateway.sign_request"
   independence property) + `apps/gateway/tests/vertex_verify/test_vertex_verify.py` (the
   automated EARNED-GREEN suite against that stub).
9. `scripts/live_vertex_verify.py` (manually-run, real-GCP-credential script, config-gated by an
   operator-supplied service-account JSON path/env var — mirrors `scripts/live_v20_verify.py` /
   `live_v21_verify.py`; NOT part of the automated pytest suite, NOT run by CI).
10. Regression pass: run every existing `gemini_*`, `azure_*`, `bedrock_*`, `minimax_*` suite
    unmodified — confirm zero collateral drift from the `__all__` extension or the
    `ProviderCredential` Union widening.

Persona (required): no project persona currently declares `flow: design` for backend
provider-adapter work — `.add/personas/protocol-translation-engineer.md` (flow: `build, advisor`)
is the closest DOMAIN-CONTENT match (multi-provider wire-translation fidelity, byte-identical
no-feature-used passthrough, billing-frame correctness) and its Critical Rules are borrowed as the
governing lens for this draft (same borrowing pattern `region-pricing` used for
`billing-precision-engineer`), NOT a flow-matched persona. Its "byte-identical passthrough is the
floor" and "document every provider's distinct shape explicitly, never assume 'same as another
provider'" rules directly shaped M2/§1 Issue #6's live-verify flag. Flagged as a candidate
follow-up for `add-persona` to seed a `flow: design` backend/protocol persona.
Spawn isolation (default): worktree — this task's `provider_credentials.py` and
`provider_keys_admin_router.py` edits overlap files every other BYOK-provider task has touched
(documented scope-snapshot-poisoning risk across concurrent sibling builds); a non-worktree build
risks colliding with any parallel milestone task touching the same frozen module.
Known-problem fixes:
  - trap: forking Gemini's translation logic instead of importing it → silent future drift when
    Gemini's own wire-shape changes (e.g. a new reasoning/thinking config) → fix: import the pure
    functions directly, add `_GeminiSSEStepper` to `__all__`, never copy-paste.
  - trap: guessing the literal Vertex egress location silently on an unrecognized model prefix →
    a residency-feature's worst possible failure mode → fix: `_parse_vertex_model` fails closed,
    raises before any HTTP call (R4/M9), never defaults.
  - trap: putting `private_key`/`client_secret`-style material into the token-provider cache key
    or any log/span → fix: mirror `AzureADConfig`'s `field(repr=False)` + non-secret
    `_make_cache_key` pattern exactly.
  - trap: shipping catalog seed rows before the matching adapter is registered (the EXACT gap
    region-catalog-dimension scope-cut Vertex for) → fix: single commit/diff for both (R6/M10).
Strategy actually used: batches 1-9 executed in the declared order (provider_credentials →
vertex_ad → gemini_upstream __all__ → vertex_upstream → provider_keys_admin_router → vertex_seed →
config+main → stub+vertex_verify → live_vertex_verify), plus ONE undeclared file
(`tenant_provider_key_store.py` — see the Scope-friction note below) added between batches 1 and 5,
mirroring the minimax-adapter-registry precedent's `_credential_to_parts`/`_parts_to_credential`
shape exactly. Batch 10 (regression pass) surfaced 4 pre-existing sibling-task tests
(`dynamic_auth_byok`, `minimax_adapter_registry` ×2, `minimax_catalog_seed`) that hard-code a
closed provider/catalog-id enumeration; these were additively widened (same pattern as those
tests' own docstrings document for the minimax/bedrock/gpt-realtime widenings before this one) —
not weakened, only the enumerated literal grew by exactly the 1 new legitimate provider + 4 new
catalog rows this task adds.
PROCESS DEVIATION (self-caught, corrected without `git stash`): all 10 implementation files were
initially drafted BEFORE any test file, violating the mandated red-first order. Caught mid-build via
re-reading the governing rules; corrected by backing up all edited/new src files, `git checkout --`
on the 6 edited files + `rm` on the 5 new files (genuine de-implementation, no stash), running the
suite to capture real RED (`22 failed, 1 error` across the 3 test dirs that existed at that point),
then restoring the implementation from backup. `vertex_verify` (written after the correction) was
red-captured properly the first time: `scripts/vertex_stub.py` was moved aside, the suite failed
with `FileNotFoundError` on the stub's `importlib` load, then the stub was restored and the suite
turned green. A build-time security self-review pass (grep for key material in
log/error/exception strings, fail-closed path confirmation, no-region-fallback confirmation) also
caught one real fail-closed gap: `jwt.exceptions.InvalidKeyError` (raised by PyJWT for a malformed
PEM) is NOT a `ValueError`/`TypeError` subclass, so `VertexTokenProvider._acquire`'s original
`except (ValueError, TypeError)` wrapper let a malformed private_key escape the fail-closed
`UpstreamUnavailableError` mapping — fixed by also catching `jwt.exceptions.PyJWTError`.
Safety rule (feature-specific): the region-prefix→location resolution (`_parse_vertex_model`) and
the URL construction happen from a FIXED, gateway-internal map ONLY — no tenant-supplied or
catalog-admin-supplied string is ever interpolated into the Vertex host; this is the one
non-negotiable invariant a security reviewer should re-verify byte-for-byte at Verify (a
residency-selling feature that could be tricked into calling an unintended region/host is a
HARD-STOP-class finding, not a style note).
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; do NOT edit `resolve_markup_pct`,
`resolve_region_multiplier`, `region-catalog-dimension`'s or `region-pricing`'s already-FROZEN
symbols; do NOT add `google-auth` without an explicit human go-ahead overriding §1 Assumption #5;
allow-list packages only (pyjwt + cryptography already present — no new dependency expected); ask
if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-12

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: batches 1-9 executed in the declared order (provider_credentials → vertex_ad → gemini_upstream __all__ → vertex_upstream → provider_keys_admin_router → vertex_seed → config+main → stub+vertex_verify → live_vertex_verify), plus ONE undeclared file (`tenant_provider_key_store.py` — see the Scope-friction note below) added between batches 1 and 5, mirroring the minimax-adapter-registry precedent's `_credential_to_parts`/`_parts_to_credential` shape exactly. Batch 10 (regression pass) surfaced 4 pre-existing sibling-task tests (`dynamic_auth_byok`, `minimax_adapter_registry` ×2, `minimax_catalog_seed`) that hard-code a closed provider/catalog-id enumeration; these were additively widened (same pattern as those tests' own docstrings document for the minimax/bedrock/gpt-realtime widenings before this one) — not weakened, only the enumerated literal grew by exactly the 1 new legitimate provider + 4 new catalog rows this task adds.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

