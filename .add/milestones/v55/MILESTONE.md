# MILESTONE: Capability-aware model management

goal: Every catalog model declares the input types it accepts, and any request carrying an input type the resolved model cannot handle is rejected with a clear structured 4xx before it reaches the provider.
rationale: new-major (intake). A new theme — capability-aware model management — that no active milestone covers: v54's goal is dashboard-UI refresh, this is gateway model/catalog behavior. First of a confirmed two-milestone roadmap (v55 capabilities → v56 per-tenant presets); split because each ships independently and v56's preset-capability validation reuses v55's guard. Tin confirmed via AskUserQuestion 2026-06-30: two-milestone roadmap · reject-with-4xx on unsupported input.
stage: production · status: active · created: 2026-06-30T12:11:18+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Per-model declaration of accepted INPUT modalities (text · image · audio; `video` deferred from v55),
     additive to the existing catalog `modality`/`provider` columns; an operator-facing way to see those
     capabilities (admin catalog API + dashboard listing); a request-boundary guard that rejects a request
     whose input content-parts exceed the resolved model's declared input modalities with a structured 4xx
     (`unsupported_input_modality`) naming what the model DOES support — BEFORE any upstream call — covering
     ALL model-input surfaces: chat multimodal content-parts (image/audio in `/v1/chat/completions`) AND the
     audio STT endpoint (`/v1/audio/transcriptions`); and (Tin 2026-06-30) artifact UPLOAD validation — a
     distinct, NON-model-capability guard that rejects an artifact upload whose content-type/size violates an
     allowed-policy at the `/v1/artifacts` boundary.
Out: Per-tenant model presets / name remapping and the `name:` prefix selector (→ v56). Capability-aware
     REROUTE to a different model that supports the input (Tin chose reject, not reroute). `video` input
     modality (deferred). New output modalities or streaming-shape changes. Rewriting the frozen
     `FallbackModelRouter` seam. Provider adapters / new infra / new API keys. Changing the v54 AI-feature
     pages (vision's hard-coded "Gemini-only" gate may be noted as an observe-delta, not touched here).
     Surfacing capabilities INSIDE the v54 artifacts/voice/vision workspace UIs (that is the separate
     "AI feature depth" program, not v55).

## Shared decisions & glossary deltas   (living — every task must honor these)
- INPUT MODALITY is distinct from MODALITY: `modality` (existing) = what kind of endpoint a model serves
  (chat/embedding/image/audio_stt/audio_tts); `input_modalities` (new) = which content-part input TYPES a
  model accepts within that endpoint (text/image/audio/video). The guard reasons over `input_modalities`.
- Additive-only catalog change: new column(s) carry a server_default derived from today's `modality` so
  every existing row stays byte-identical (chat→{text}, image/audio rows seeded from their modality).
- Fail-closed guard, but DEFAULT-OFF rollout knob (design-for-failure): the guard ships behind a flag so an
  empty/under-populated capability set can never start 4xx-ing real traffic until capabilities are seeded.
- The guard rejects BEFORE the upstream call and BEFORE billing — never bill a request the proxy refused.
- Structured error shape is frozen by the guard task and reused verbatim by v56's preset validation.

## Shared / risky contracts (freeze these first)
- `input_modalities` field shape on the catalog model entity + admin catalog API response -> model-input-capabilities
- `unsupported_input_modality` error code + body shape (status, code, supported list) -> unsupported-input-guard

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] model-input-capabilities    depends-on: none                      — additive catalog `input_modalities` descriptor + migration (default from `modality`) + domain/store + sync/seed population.  DONE gate=PASS 2026-06-30 (62 green; mig c2e4a6f8b0d3).
- [x] capabilities-admin-surface  depends-on: model-input-capabilities  — surface input_modalities (read-only) on `GET /admin/catalog/models` (new AdminCatalogModelItem) + `GET /admin/models` (AdminModelItem) while keeping public `GET /v1/models` LEAN; dashboard `/app/models` + ModelCatalogTable gain an "Inputs" Badge column. DONE gate=PASS 2026-06-30 (BE 49 green; FE 25 green). Tin chose admin-only field + read-only listing; milestone-gap reconciled (/admin/models is the real operator page).
- [x] unsupported-input-guard     depends-on: model-input-capabilities  — request-boundary guard across ALL model-input surfaces (chat multimodal content-parts in `/v1/chat/completions` + audio STT `/v1/audio/transcriptions`): compare request input types to the resolved model's `input_modalities`; reject `400 ERR_UNSUPPORTED_INPUT_MODALITY` (flagged, default-off; Tin chose 400 to match the sibling guard) naming supported types, before upstream + before billing. DONE gate=PASS 2026-06-30 (91 green; alias-union resolution).
- [x] artifact-upload-validation  depends-on: none                      — (Tin 2026-06-30) DISTINCT non-model-capability guard: validate artifact uploads at `/v1/artifacts`. Size cap (`artifact_max_bytes`→413) + base64 (→422) ALREADY existed; this task added the net-new content-type allow-policy (`GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES`, default-"" = allow-all → 415 `ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED`, before storage). DONE gate=PASS 2026-06-30 (26 green/2 skip).

## Exit criteria (observable; map each to the task that delivers it)
- [x] Every catalog model exposes its accepted input modalities, default-derived from `modality` with zero behavior change for existing rows   (← model-input-capabilities)
- [x] An operator can list each model's input capabilities via the admin catalog API and the dashboard   (← capabilities-admin-surface)
- [x] A request sending an unsupported input type — an image to a text-only chat model, OR audio to a non-audio model at the STT endpoint — returns a structured 4xx (400) naming the supported types, with no upstream call and no billing, when the guard is enabled   (← unsupported-input-guard)
- [x] The guard is default-off and fail-closed only when enabled; existing traffic is byte-identical with it off   (← unsupported-input-guard)
- [x] An artifact upload with a disallowed content-type or oversize body is rejected with a structured 4xx (415 content-type / 413 size) before storage; allowed uploads are unchanged   (← artifact-upload-validation)
- [x] The full gateway + dashboard suites stay green; the frozen `FallbackModelRouter` / provider seam is untouched   (← all tasks)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway/catalog : `models.input_modalities` descriptor (TEXT col, mig c2e4a6f8b0d3) + domain helpers (parse/normalize/default) threaded through `MarkedUpModel` + the markup repository SELECT; new `AdminCatalogModelItem`/`AdminCatalogModelsListResponse`; `AdminModelItem.input_modalities`; `list_catalog_models` re-maps (no longer delegates); `get_admin_models`/`put_admin_model` carry the field. `ModelItem`/`list_models` (public /v1/models) UNCHANGED.
- gateway/proxy   : capability-aware unsupported-input guard (chat content-parts + STT) behind `GATEWAY_INPUT_MODALITY_GUARD_ENABLED` (default-off, 400, fail-open, alias-union, pre-billing) — new `modality_guard` + `InputModalityLookup`.
- gateway/artifacts: content-type allow-policy on POST /v1/artifacts behind `GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES` (default-"" allow-all, 415, pre-storage) — new `content_type_policy` module. Size cap + base64 pre-existed.
- dashboard       : read-only "Inputs" Badge column on `/app/models` (ModelsPage) + ModelCatalogTable. BFF `/api/gw` proxy = transparent passthrough (untouched).
- ADD tooling     : engine 1.13.0→1.14.0; state.json + v55/v56 milestone scaffolding. skill/book: untouched.

### Cross-task evidence   (one row per task)
- model-input-capabilities : gate=PASS · tests=62 green · residue=none (additive col, single migration head)
- unsupported-input-guard  : gate=PASS · tests=91 green · residue=none (reject proves no-upstream + no-bill)
- artifact-upload-validation : gate=PASS · tests=26 green/2 skip · residue=none (cache-poisoned scope snapshot cleared via pristine re-cross)
- capabilities-admin-surface : gate=PASS · tests=BE 49 + FE 25 green · residue=two prior-task tests updated as CONTRACT-DRIVEN (twin-vs-v1 parity rewrite + task-1 SC6 flip) — refute-read EARNED, surfaced for spot-audit

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which) — criteria 1←model-input-capabilities · 2←capabilities-admin-surface · 3,4←unsupported-input-guard · 5←artifact-upload-validation · 6←all (full suites green, FallbackModelRouter untouched in every diff)
- goal: every catalog model now declares its accepted input types AND a request carrying an unsupported input type is rejected with a clear structured 4xx before the provider — proven by the unsupported-input-guard reject tests (400, no upstream, no billing) over the input_modalities the catalog/admin surfaces expose.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] commit capabilities-admin-surface (BE+FE) — the last of 4 per-task commits on `feat/v55-model-capabilities`
- [ ] open a PR from this branch to `main`; human reviews — spot-audit the two contract-driven test edits called out above
- [ ] (optional) run the migration `c2e4a6f8b0d3` on the target DB before deploy (additive ADD COLUMN, instant)
- [ ] tag / publish / deploy — human-run per release.md (v55 may bundle into the next release cut with v56 when that ships)
