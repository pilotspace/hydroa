# MILESTONE: OpenRouter embeddings routing

goal: A client can call POST /v1/embeddings with an OpenRouter-hosted embedding model (e.g. google/gemini-embedding-2) and get a real embedding back, billed correctly — the facade forwards /embeddings instead of hardcoding /chat/completions, and catalog sync classifies OpenRouter embedding models correctly instead of defaulting every row to modality=chat.
rationale: change-request — Tin asked to fix the OpenRouter embeddings mis-wire and wire+live-verify
  google/gemini-embedding-2. Investigation showed this touches TWO already-frozen, already-archived
  contracts (provider-seam's OpenRouterUpstreamFacade routing; model-catalog's single-endpoint,
  always-modality=chat sync) — the tooling cannot `reopen` archived tasks (state.json no longer
  tracks them), so per this codebase's own precedent (provider-seam's own "superseded ADDITIVELY,
  not edited" pattern) this is a new task that supersedes both, not a rewrite of either. One-task
  gap rule applies: doesn't fit any currently-active milestone's stated scope → new micro-milestone.
stage: production · status: active · created: 2026-07-01T04:20:38+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - OpenRouterUpstreamFacade.post_json forwards the "/embeddings" path to a real OpenRouter
    embeddings call instead of always calling .complete() (/chat/completions). All other paths
    (chat) keep today's exact behavior.
  - New OpenRouterCompletionUpstream.embed() — POST /embeddings on OpenRouter, same auth /
    circuit-breaker / timeout conventions as complete().
  - OpenRouterCatalogSource additionally fetches GET /api/v1/embeddings/models (today it only
    fetches GET /api/v1/models) and yields those rows as modality="embedding", provider="openrouter".
  - SqlAlchemyCatalogRepository._upsert_model writes `modality` on insert AND on conflict-update
    (today it writes neither — new + existing OpenRouter rows silently keep the "chat" column
    default forever, even though the source now reports the true value).
  - Live-verify: one real POST /v1/embeddings call against OpenRouter with
    model="google/gemini-embedding-2" using the OPENROUTER_API_KEY already in this environment —
    confirms a 200, a real embedding vector, and usage/billing recorded (Tin approved real spend,
    sub-cent, 2026-07-01).
Out:
  - Granite and Nomic embeddings — confirmed NOT in OpenRouter's catalog (live-checked
    2026-07-01: GET /api/v1/embeddings/models has 26 models, no granite/nomic entries;
    GET /api/v1/models has ibm-granite as CHAT-only). Dropped per Tin's decision.
  - input_modalities richness for OpenRouter embedding models — stays at the "text" default.
    The embeddings endpoint (EmbeddingsUseCase) never reads ModelRow.input_modalities, so this
    doesn't block the exit criteria; a future capability-management task can enrich it.
  - Classifying image / audio_stt / audio_tts modality for OpenRouter's chat catalog — OpenRouter
    doesn't expose separate REST endpoints for those (image/audio go through /chat/completions
    with multimodal models), so there's nothing analogous to the embeddings-models split to sync.
  - Wiring the pre-existing dead `OPENAI_SEED_MODELS` list, or building a general catalog-seeding
    path for the OpenAI/Bedrock/Azure/Google DIRECT embeddings adapters (they have no production
    catalog rows either — a real, separate, pre-existing gap noted for Tin, not fixed here).

## Shared decisions & glossary deltas   (living — every task must honor these)
- SUPERSESSION (additive, not edited — mirrors provider-seam's own JwksKeyCache/retry-policy and
  "OpenRouter as sole upstream" precedents):
  1. provider-seam TASK.md §3 OpenRouterUpstreamFacade said: "the path is accepted for interface
     consistency but OpenRouter always uses /chat/completions — the path is not forwarded."
     Superseded for the "/embeddings" path ONLY; every other path (including "/chat/completions")
     is byte-identical to before.
  2. model-catalog TASK.md's OpenRouterCatalogSource fetched exactly one URL (GET /api/v1/models)
     and CatalogModel always defaulted modality="chat". Superseded additively: a second source URL
     is now also fetched; modality is written from the source instead of silently defaulting.
- OpenRouter's POST /api/v1/embeddings request/response shape is already OpenAI-compatible
  (confirmed live against OpenRouter's own docs + a live API call) — the facade is a byte-for-byte
  relay, no translation layer, unlike the Bedrock/Azure/Google embeddings adapters.
- Confirmed live (2026-07-01, via GET https://openrouter.ai/api/v1/embeddings/models): zero overlap
  between the chat /models catalog (338 ids) and the embeddings /embeddings/models catalog (26 ids)
  — an embedding model is NEVER discoverable via the chat endpoint, and every embeddings-catalog row
  has output_modalities == ["embeddings"] (100% consistent — no fuzzy string classification needed).

## Shared / risky contracts (freeze these first)
- OpenRouterUpstreamFacade.post_json / OpenRouterCompletionUpstream.embed() -> owning task openrouter-embeddings-routing
- OpenRouterCatalogSource dual-source sync + _upsert_model modality write -> owning task openrouter-embeddings-routing

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] openrouter-embeddings-routing   depends-on: none   — facade forwards /embeddings + catalog sync classifies OpenRouter embedding models + live-verify google/gemini-embedding-2   (PASS, 2026-07-01)

## Exit criteria (observable; map each to the task that delivers it)
- [x] A client can call POST /v1/embeddings with model="google/gemini-embedding-2" (provider
      resolved as "openrouter" from the catalog) and receive a real 200 response containing an
      embedding vector, billed via the normal usage-recording path        (← openrouter-embeddings-routing;
      LIVE-verified 2026-07-01: real 200, 3072-dim vector, usage.cost=$0.0000022)
- [x] An OpenRouter catalog sync (POST /internal/catalog/sync or /admin/catalog/sync) marks
      google/gemini-embedding-2 — and any other model OpenRouter's embeddings catalog lists —
      modality="embedding" with zero manual DB edits                       (← openrouter-embeddings-routing;
      LIVE-verified: real fetch found 26 embedding models incl. google/gemini-embedding-2,
      modality="embedding" provider="openrouter"; OER7/OER10 pin the DB write)
- [x] POST /v1/chat/completions and every other existing post_json path through OpenRouter is
      byte-identical to before this milestone (regression-tested)          (← openrouter-embeddings-routing;
      OER2 + full openrouter_generation_client/provider_seam suites green + a LIVE chat call in
      the SAME adapter instance right after the live embeddings call, both succeeding independently)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway/proxy   : OpenRouterUpstreamFacade.post_json routes "/embeddings" to a new
  OpenRouterCompletionUpstream.embed() (same auth/retry/breaker seam as complete()); every other
  path unchanged.
- gateway/catalog : CatalogSource gained list_embedding_models() (OpenRouterCatalogSource fetches
  the separate GET /api/v1/embeddings/models catalog); CatalogRepository.sync_catalog gained the
  embedding_models degraded-signal kwarg; SqlAlchemyCatalogRepository._upsert_model now writes
  modality on insert+conflict-update; SyncCatalogUseCase.execute() fetches both catalogs,
  catching only the embeddings one.
- tooling : untouched.
- skill   : untouched.
- book    : untouched.

### Cross-task evidence   (one row per task)
- openrouter-embeddings-routing : gate=PASS · tests=14/14 new (OER1-OER12) + 76/76 across the
  full affected surface (openrouter_embeddings_routing, catalog, catalog_sync_trigger,
  catalog_input_modalities, openrouter_generation_client, provider_seam) + 2076/2076 whole-repo
  suite, 0 failed · residue=none (adversarial refute-read verdict EARNED; one non-blocking
  doc-nit found and fixed; one follow-up gap — list_models() lacks direct unit coverage —
  deferred to a §7 SPEC delta, not blocking)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by the openrouter-embeddings-routing Cross-task
      evidence row (gate=PASS) + its own OER13 live-verify evidence (real 200 embedding call,
      real catalog fetch, real unaffected chat call — all cited inline on each Exit-criteria line)
- goal: A client can call POST /v1/embeddings with an OpenRouter-hosted embedding model and get a
  real embedding back, billed correctly, with catalog sync correctly classifying OpenRouter
  embedding models — MET: live-verified 2026-07-01 end-to-end against production OpenRouter
  (google/gemini-embedding-2 → 200, 3072-dim vector, $0.0000022 billed usage; chat unaffected).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Review the diff (6 src files + 2 pre-existing test-fixture compatibility updates + 1 new
      test dir), commit with the mandated message format, and ask Tin for PR-creation permission.
- [ ] Open a PR from this branch; Tin reviews + merges.
- [ ] `add.py milestone-done openrouter-embeddings` to close the milestone once merged.
- [ ] Bundle into the next release cut (release.md) alongside the currently-releasable
      gateway-health milestone — human decides timing/bundling, not this task.
