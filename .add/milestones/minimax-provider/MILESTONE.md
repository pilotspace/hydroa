# MILESTONE: MiniMax provider integration

goal: A client can call the proxy with a MiniMax-hosted model (chat, and any other modality MiniMax's OpenAI-compatible API exposes) and get a real response back, billed correctly via BYOK credentials, live-verified against api.minimax.io/v1.
rationale: intake bucket=sub-milestone — a slice of the existing "provider breadth" theme (openrouter/openai/anthropic/google/bedrock/azure already integrated as v9/v20/v21 etc.), too big for one task. Relationship to the milestone map: *extends* the provider-breadth line; MiniMax is OpenAI-wire-compatible (POST /v1/chat/completions, Bearer auth, OpenAI-shaped usage block — confirmed via /websites/platform_minimax_io_api-reference), so it is lighter-weight than Bedrock (SigV4) or Azure (AAD) but still spans adapter+catalog+BYOK+live-verify, matching the v20/v21 shape rather than a single task.
stage: mvp · status: active · created: 2026-07-01T07:14:17+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## §0 GROUND (milestone scope)
- Touches: `proxy/domain/provider_credentials.py` (ProviderName Literal + PROVIDER_VALUE_SET) ·
  `proxy/infrastructure/provider_registry.py` (ProviderRegistry/select_provider) ·
  `proxy/infrastructure/openai_provider.py` (OpenAIDirectProvider — the OpenAI-wire adapter shape
  to generalize/extend) · `proxy/infrastructure/tenant_provider_key_store.py` (BYOK Fernet-at-rest
  store) · `catalog/infrastructure/*_seed.py` / `*_source.py` (catalog registration) ·
  `apps/dashboard` BYOK provider picker (if it enumerates PROVIDER_VALUE_SET)
- Context: MiniMax API reference (ctx7 `/websites/platform_minimax_io_api-reference`) —
  `POST /v1/chat/completions` OpenAI-compatible (Bearer auth, `stream`, OpenAI-shaped
  `usage:{prompt_tokens,completion_tokens,total_tokens}`), `GET /v1/models`. Live-verify key
  supplied by Tin for `https://api.minimax.io/v1` (BYOK secret — see Shared decisions).
- Honors: PROJECT.md invariants — provider is catalog metadata, never client-specified; every
  proxied request produces exactly one usage record; no outbound IO without timeout+retry+breaker;
  BYOK secrets Fernet-at-rest (v25); billing keys on the SERVED model id with native usage tokens.
- Anchors: `ProviderName = Literal["openrouter","openai","anthropic","google","bedrock","azure"]`
  + `PROVIDER_VALUE_SET` (provider_credentials.py:36-42) · `OpenAIDirectProvider` (openai_provider.py,
  `_DEFAULT_BASE_URL`/`_auth_headers`/`complete`/`stream`) · `ProviderRegistry.get`/`select_provider`
  (provider_registry.py)

## Scope
In:  MiniMax joins the provider registry (provider="minimax") via its OpenAI-compatible
     `/v1/chat/completions` surface; BYOK credential storage for a tenant's MiniMax key;
     at least one MiniMax chat model registered in the catalog (correct provider+modality+pricing);
     a real live-verification pass against `https://api.minimax.io/v1` using the supplied key
     (billed chat call + exactly one usage_records row), mirroring the openrouter-embeddings
     live-verify pattern.
Out: embeddings/image/audio modalities (no MiniMax OpenAI-compatible endpoint for these was found
     in docs — revisit if discovered) · MiniMax-native-only surfaces (`/v1/responses`,
     `/anthropic/v1/messages`, `/v1/text/chat/completions`) · tool-calling/"thinking"-mode
     translation fidelity beyond byte-identical passthrough · exact per-model MiniMax pricing
     beyond a documented catalog price point sourced at the catalog task.

## Shared decisions & glossary deltas   (living — every task must honor these)
- MiniMax is OpenAI-wire-compatible → reuse the `OpenAIDirectProvider` adapter SHAPE (a
  base_url-parametrized OpenAI-style adapter), not a bespoke per-field translator — mirrors the
  v9 "one adapter, base_url swap" pattern already used for the direct-OpenAI provider.
- `provider="minimax"` is fixed catalog metadata, never client-specified (existing invariant,
  unchanged).
- The live-verify API key is a BYOK per-tenant secret: stored ONLY via the existing Fernet-at-rest
  `tenant_provider_key_store`, never written to `.env`/committed config, never logged.

## Shared / risky contracts (freeze these first)
- `ProviderName` Literal + `PROVIDER_VALUE_SET` gains `"minimax"` -> owning task
  `minimax-adapter-registry`
- MiniMax adapter shape (generalized/extended `OpenAIDirectProvider`) -> owning task
  `minimax-adapter-registry`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] minimax-adapter-registry   depends-on: none                       — add "minimax" to
      ProviderName/PROVIDER_VALUE_SET, adapt/generalize the OpenAI-compatible provider for
      MiniMax's base_url, wire into ProviderRegistry + BYOK tenant_provider_key_store.
- [ ] minimax-catalog-seed       depends-on: minimax-adapter-registry    — register MiniMax chat
      model(s) in the catalog with provider=minimax, modality=chat, pricing.
- [ ] minimax-live-verify        depends-on: minimax-catalog-seed        — store the supplied key
      via BYOK for a real tenant, make a real billed POST /v1/chat/completions call against
      api.minimax.io/v1, confirm exactly one usage_records row with accurate cost.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A tenant can store a MiniMax API key via BYOK (verify: test_admin_put_minimax_key_roundtrip + test_admin_put_minimax_key_upsert_replaces + test_admin_delete_minimax_key, real Fernet-at-rest round-trip)   (← minimax-adapter-registry)
- [x] The catalog lists >=3 MiniMax chat models, provider=minimax, modality=chat (verify: minimax-catalog-seed's test_full_sync_persists_minimax_rows_active_with_correct_fields, 2100/2100 full suite)   (← minimax-catalog-seed)
- [x] A real chat completion routed to a MiniMax model returns a genuine response and produces exactly one accurately-costed usage_records row (verify: minimax-live-verify MLV1-MLV4 live run, real 200 + reasoning trace + cached_tokens=128 + cost $0.00014184 matching independent recompute)   (← minimax-live-verify)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway/proxy : ProviderName/PROVIDER_VALUE_SET/BYOK_PROVIDERS gained "minimax" (7th provider);
  OpenAIDirectProvider gained a provider_name param (generalizing the previously-hardcoded
  "openai" literal across auth headers/web-search/retry-dispatch); a minimax chat adapter wired
  unconditionally in create_app(), chat-only (absent from the non-chat provider_registry);
  tenant_provider_key_store accepts minimax as a plain Bearer BYOK provider.
- gateway/catalog : new CompositeCatalogSource (layers a static seed list alongside the existing
  OpenRouter sync) + minimax_seed.py (MINIMAX_SEED_MODELS: MiniMax-M3/M2.7/M2.7-highspeed, real
  pay-as-you-go pricing/context length from MiniMax's public pricing page).
- gateway/core/config : Settings.minimax_base_url (https://api.minimax.io/v1, BYOK-only — no
  operator-level key setting, matching the dynamic-auth-byok precedent).
- dashboard : ConfigureProviderDialog/ProviderKeysSettings gained a 7th "MiniMax" row (single
  Bearer-secret form).
- tooling : untouched.
- skill   : untouched.
- book    : untouched.

### Cross-task evidence   (one row per task)
- minimax-adapter-registry : gate=PASS · tests=2090/2090 full suite + new adapter/BYOK/dashboard
  tests · residue=none (adversarial refute-read verdict EARNED)
- minimax-catalog-seed : gate=PASS · tests=2100/2100 full suite (incl. a genuine SC5 conflict
  found and fixed — a shared _upsert_model change silently broke an already-shipped no-clobber
  invariant, caught only by the full suite, not the directly-touched directory) · residue=none
  (refute-read verdict EARNED)
- minimax-live-verify : gate=PASS · tests=N/A (live-verify task, no pytest suite — evidence is the
  real run itself: MLV1-MLV4, real 200 response with a MiniMax-generated reasoning trace,
  cached_tokens=128, cost $0.00014184 independently recomputed and matched exactly) · residue=none
  (self-adversarial refute-read verdict EARNED; this run's own evidence — a flat, undiscounted
  cache-hit billing — is what motivated the follow-on catalog-pricing-fields task/milestone)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by its named task's Cross-task evidence row (all
      gate=PASS) — each Exit-criteria line above cites its own passing test/live-run evidence inline
- goal: A client can call the proxy with a MiniMax-hosted model and get a real response back,
  billed correctly via BYOK credentials, live-verified against api.minimax.io/v1 — MET:
  minimax-live-verify's real end-to-end call (BYOK key, real tenant, MiniMax-M3, billed usage row)
  proved the full chain works; the discovery that the cache-hit discount was NOT actually applied
  to that real bill directly seeded the catalog-pricing-fields task (now shipped separately in
  catalog-pricing-detail).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [x] Review the diff, commit with the mandated message format (bundled with catalog-pricing-fields
      in one commit, per Tin's explicit choice — both were sitting uncommitted together and
      catalog-pricing-fields's diff builds directly on this milestone's files), ask for PR permission.
- [ ] Open a PR from this branch; Tin reviews + merges.
- [ ] Bundle into the next release cut (release.md) — human decides timing/bundling.
