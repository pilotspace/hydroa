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
- [ ] A tenant can store a MiniMax API key via BYOK                          (← minimax-adapter-registry)
- [ ] The catalog lists >=1 MiniMax chat model, provider=minimax, modality=chat  (← minimax-catalog-seed)
- [ ] A real chat completion routed to a MiniMax model returns a genuine response and produces
      exactly one accurately-costed usage_records row, live-verified against api.minimax.io/v1
      (← minimax-live-verify)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
