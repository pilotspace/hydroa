# PLAN: /v1/images/edits + variations on the images seam

slug: image-edits-variations · created: 2026-07-24 · stage: production
milestone: api-surface-parity
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: OpenAI-wire `POST /v1/images/edits` + `POST /v1/images/variations` — multipart image-in/image-out on the existing images seam (`images_router.py` / `ImagesUseCase` / `UpstreamProvider.post_multipart`), billed `per_image` on the images the upstream RETURNED (never the requested `n`).

Framings weighed:
- **(chosen) Extend the images seam with two new routes on the SAME `images_router` + two new sibling use-case classes**, reusing `UpstreamProvider.post_multipart` (already shipped for audio STT, unused by images until now) — mirrors the proven `TranscriptionUseCase`/`SpeechUseCase` split pattern exactly; zero new abstractions.
- A single `ImagesUseCase.execute_multipart(op=...)` polymorphic method — rejected: edits (prompt required, mask optional) and variations (no prompt at all) have different required-field shapes; one method with `op` branching is a worse fit than the STT/TTS precedent's two-classes-one-port shape.
- Route the new endpoints through a generic "multipart proxy" abstraction shared with audio — rejected: premature; only 2 of 4 non-chat modalities need it today (images, audio_stt), and forcing a shared abstraction now risks coupling two independently-evolving OpenAI wire shapes (audio has `duration`/STT-specific billing, images has none).

Must:
<must>
  - POST /v1/images/edits accepts multipart/form-data: required `image` file + required `prompt` string; optional `mask` file + passthrough fields (`model`, `n`, `size`, `response_format`, `background`, `quality`, `output_format`, `user`) forwarded verbatim to `post_multipart("/images/edits", files, data)`.
  - POST /v1/images/variations accepts multipart/form-data: required `image` file only (no `prompt`, no `mask`); optional passthrough fields (`model`, `n`, `size`, `response_format`, `user`) forwarded verbatim to `post_multipart("/images/variations", files, data)`.
  - Both endpoints run `NonChatGovernance.authorize` (estimated_tokens=None, images have no token dimension) BEFORE touching the provider — identical ordering to `/v1/images/generations`.
  - Both endpoints resolve `ModelRow.modality` + `.provider` + `.input_modalities` from the catalog for the resolved model id and reject a non-`"image"`-modality model (reuse `MODEL_MODALITY_MISMATCH`).
  - Both endpoints reject a model whose `input_modalities` excludes `"image"` (edit/variation-INCAPABLE — e.g. `dall-e-3`, which is generations-only) via `UNSUPPORTED_INPUT_MODALITY`, BEFORE `select_provider`/upstream/billing (single-bill invariant, same ordering as `MODEL_MODALITY_MISMATCH` in the generations flow).
  - Both endpoints bill exactly ONE `usage_records` row per request, `pricing_unit="per_image"`, `quantity=len(resp_body.get("data", []))` — the upstream-RETURNED image count, NEVER the requested `n`; absent/empty `data` bills `quantity=0` (never over-bill on partial/failed upstream) — byte-identical formula to `ImagesUseCase.execute` Step 7.
  - The uploaded `image` (and `mask`, edits only) is size-capped BEFORE it is forwarded upstream, via a new `Settings.image_edit_max_bytes` knob (default 4 MiB — dall-e-2's real documented limit; `0` = disabled, mirrors the `tts_max_input_characters` escape-hatch convention) — an oversized file rejects 413 pre-governance/pre-upstream/pre-bill (no partial charge).
  - A new `dall-e-2` catalog row is seeded via migration (`modality="image"`, `provider="openai"`, `input_modalities="text,image"`, `pricing_unit="per_image"`, `unit_usd_per_unit=NULL` — SCOPE-CUT, mirrors the existing `dall-e-3` precedent exactly) so the edit/variation path has a REAL usable model; `dall-e-2` supports BOTH edits and variations per OpenAI's docs (verified live 2026-07-24).
  - `POST /v1/images/generations` request/response/billing stays byte-identical — a regression test proves the existing route + `ImagesUseCase.execute` are untouched.
  - New routes ride the existing Envoy `prefix: "/v1/"` ext_authz rule — [OBSERVED] `infra/envoy/envoy.yaml` line 206 / `envoy-prod.yaml` line 200 already match `/v1/images/edits` and `/v1/images/variations` with no new infra change needed.
</must>
Reject:
<reject>
  - `image` field missing/empty (edits & variations) -> "ERR_PAYLOAD_INVALID" (422, new `PAYLOAD_IMAGE_REQUIRED`, mirrors `PAYLOAD_FILE_REQUIRED`'s STT idiom)
  - `prompt` field missing/empty (edits only) -> "ERR_PAYLOAD_INVALID" (422, reuse existing `PAYLOAD_PROMPT_REQUIRED`)
  - `image` (or `mask`) bytes exceed `image_edit_max_bytes` -> "ERR_PAYLOAD_IMAGE_TOO_LARGE" (413, new `PAYLOAD_IMAGE_TOO_LARGE`, mirrors `PAYLOAD_INPUT_TOO_LONG`'s pre-bill 413 idiom)
  - model unknown/inactive -> "ERR_MODEL_UNKNOWN" (404, reuse)
  - model modality != "image" -> "ERR_MODEL_MODALITY_MISMATCH" (400, reuse)
  - model modality=="image" but `input_modalities` excludes "image" (edit/variation-incapable, e.g. `dall-e-3`) -> "ERR_UNSUPPORTED_INPUT_MODALITY" (400, reuse)
  - upstream timeout / circuit-open -> "ERR_UPSTREAM_UNAVAILABLE" (502, reuse)
</reject>
After:
<after>
  - Exactly one `usage_records` row exists per request that reached governance+upstream (0 rows for any pre-governance 4xx reject).
  - The upstream response is returned verbatim (only non-finite-float sanitized, same as generations).
  - `/v1/images/generations`'s existing test suite (`tests/images_endpoint/`) stays green, unmodified.
</after>
Boundary: multipart/form-data is the ONLY external input shape for both new routes — OpenAI's wire for edits/variations is multipart-only (no JSON-body variant exists upstream); "none" beyond that one variant.
<assumptions>
  ⚠ #1 (lowest confidence): seeding a NEW `dall-e-2` catalog row via migration is IN scope for this task, rather than leaving the feature structurally dead against the current catalog (which seeds ONLY `dall-e-3`, a generations-only model). The milestone `## Ground` doesn't explicitly call out catalog seeding for this task — but `gpt-image-1` is EXPLICITLY on the existing `_UNVERIFIED_IDS` reject-list in `tests/catalog_db_seed/test_catalog_db_seed_migration.py::test_unverified_rows_are_never_seeded` (confirms it must NEVER be seeded), while `dall-e-2` carries no such flag and has well-established, non-controversial pricing. If wrong: the exit criterion "`client.images.edit(...)` returns images" is unsatisfiable against the real catalog even though the code path is correct — cost is a change-request back to catalog ownership or a deferred follow-up task; the RED suite itself does not depend on the migration (test-local `seed_edit_capable_model` fixture, mirroring `images_endpoint/conftest.py`'s pattern) so this assumption does not block the freeze.
  ⚠ #2: reusing the `input_modalities` column — originally meant to gate CHAT/STT input CONTENT types — to also mean "this image model accepts an uploaded source image for edit/variation" is a semantic overload rather than a new capability dimension. Today only one image-modality model exists pre-task (`dall-e-3`, `input_modalities="text"` by migration default) so no collision manifests, but a future vision-capable image-generation model could be mis-gated by this reuse. If wrong: cost is a follow-up capability-dimension task, not a live billing/security defect (fails closed — an under-capable model is rejected, never silently mis-routed).
  💭 #3: the 4 MiB size-cap default is bound to `dall-e-2`'s specific real limit, not a general "any future edit-capable model" number (GPT-Image-family docs quote a larger cap) — deliberately a config value (`Settings.image_edit_max_bytes`), not a code constant, so raising it for a future bigger-cap model is a config change only.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Grounding (reasoned in-context — anchors below are what the Contract may cite)

Touches: `apps/gateway/src/gateway/proxy/api/images_router.py` (add 2 routes to the EXISTING `images_router` `APIRouter` — `main.py`'s `app.include_router(images_router)` already covers them, zero `main.py` edit) · `images_deps.py` (2 new DI factories) · `proxy/application/images_use_case.py` (2 new sibling classes `ImageEditUseCase`/`ImageVariationUseCase`, mirrors `TranscriptionUseCase`/`SpeechUseCase`'s split in `audio_use_case.py`) · `core/error_catalog.py` (2 new `ErrorSpec` constants) · `core/config.py` (1 new `Settings` field) · a new Alembic migration under `migrations/versions/`.

Honors: byte-identical default path (existing `/v1/images/generations` untouched) · single-bill invariant (bill AFTER upstream returns, exactly once) · fail-closed rejection ordering (validate → governance → catalog → capability-gate → upstream → bill) · no outbound IO without timeout+breaker (already enforced inside `OpenAIDirectProvider.post_multipart`'s existing breaker guard / `httpx.TimeoutException` handling — [OBSERVED] `openai_provider.py:282-311`, no new IO code needed, the port is reused as-is) · anti-enumeration (uniform `ERR_MODEL_UNKNOWN`/`ERR_UPSTREAM_UNAVAILABLE` postures, no new oracle).

Anchors (the Contract may cite ONLY these):
- `gateway.proxy.domain.ports.UpstreamProvider.post_multipart` — [OBSERVED] `proxy/domain/ports.py:355-364`, already typed for `files: dict[str, Any], data: dict[str, Any]`.
- `gateway.proxy.infrastructure.openai_provider.OpenAIDirectProvider.post_multipart` — [OBSERVED] `openai_provider.py:282-311`, circuit-breaker-guarded, raises `UpstreamUnavailableError` on timeout/network error/5xx.
- `gateway.proxy.application.images_use_case.ImagesUseCase.execute` — [OBSERVED] `images_use_case.py:94-235`, the Step 1-9 pipeline this task mirrors (validate → preset-resolve → governance → guardrails → catalog → capability-gate(NEW) → select_provider → credential-resolve → upstream → bill → return).
- `gateway.proxy.api.audio_router.audio_transcriptions` + `TranscriptionUseCase.execute` — [OBSERVED] `audio_router.py:36-57`, `audio_use_case.py:232-453` — the FastAPI `request.form()` + `UploadFile.read()` + `files=/data=` multipart ingress pattern this task copies verbatim.
- `gateway.proxy.application.modality_guard.enforce` — [OBSERVED] `modality_guard.py:105-153` — reused DIRECTLY (not via `resolve_allowed`'s alias-group layer, since `ImagesUseCase`-style code already does a plain single-model catalog SELECT) as `enforce(frozenset({"image"}), parse_input_modalities(row.input_modalities) or None, model_id=model_id)`.
- `gateway.core.error_catalog.{PAYLOAD_PROMPT_REQUIRED, PAYLOAD_FILE_REQUIRED, PAYLOAD_INPUT_TOO_LONG, MODEL_UNKNOWN, MODEL_MODALITY_MISMATCH, UNSUPPORTED_INPUT_MODALITY, UPSTREAM_UNAVAILABLE}` — [OBSERVED] `error_catalog.py`.
- `apps/gateway/migrations/versions/9cdca76231c6_model_catalog_db_seed.py` — [OBSERVED] the `dall-e-3` SCOPE-CUT precedent (`unit_usd_per_unit=NULL`, `ON CONFLICT DO NOTHING` idempotency) this task's new migration mirrors for `dall-e-2`.
- `apps/gateway/tests/catalog_db_seed/test_catalog_db_seed_migration.py::_UNVERIFIED_IDS` — [OBSERVED] `gpt-image-1` is explicitly forbidden from ever being seeded; `dall-e-2` carries no such flag.

Ground SHA: not stamped (engine-populated on freeze).

### Contract (freeze the shape)

```
POST /v1/images/edits   multipart/form-data:
  { image: file (required), prompt: str (required),
    mask?: file, model?: str, n?: int, size?: str, response_format?: str,
    background?: str, quality?: str, output_format?: str, user?: str }
  200 -> <upstream body verbatim> (data: [...], created, optional usage/background/... passthrough)
  422 -> { code: "ERR_PAYLOAD_INVALID" }            # image or prompt missing/empty
  413 -> { code: "ERR_PAYLOAD_IMAGE_TOO_LARGE" }    # image or mask bytes exceed image_edit_max_bytes
  400 -> { code: "ERR_MODEL_MODALITY_MISMATCH" }    # model modality != "image"
  400 -> { code: "ERR_UNSUPPORTED_INPUT_MODALITY" } # model input_modalities excludes "image" (e.g. dall-e-3)
  404 -> { code: "ERR_MODEL_UNKNOWN" }
  502 -> { code: "ERR_UPSTREAM_UNAVAILABLE" }

POST /v1/images/variations   multipart/form-data:
  { image: file (required), model?: str, n?: int, size?: str, response_format?: str, user?: str }
  200 -> <upstream body verbatim>
  422 -> { code: "ERR_PAYLOAD_INVALID" }            # image missing/empty
  413 -> { code: "ERR_PAYLOAD_IMAGE_TOO_LARGE" }
  400 -> { code: "ERR_MODEL_MODALITY_MISMATCH" }
  400 -> { code: "ERR_UNSUPPORTED_INPUT_MODALITY" }
  404 -> { code: "ERR_MODEL_UNKNOWN" }
  502 -> { code: "ERR_UPSTREAM_UNAVAILABLE" }

Schema:
  models: +1 row (dall-e-2, modality=image, provider=openai, input_modalities="text,image")
  pricing_snapshots: +1 row (dall-e-2, pricing_unit=per_image, unit_usd_per_unit=NULL — SCOPE-CUT)
  usage_records: unchanged shape; +1 row per billed request, pricing_unit="per_image",
    quantity=Decimal(len(resp_body.get("data", []))) — never the requested n.
  Access pattern: identical SELECT shape to ImagesUseCase.execute Step 4, +input_modalities column
    (already exists on ModelRow — no schema migration for the SELECT itself, only the new seed row).
```

Target (measurable): the §4 red suite (15 tests: 10 edits + 4 variations + 1 regression) goes GREEN at BUILD with zero modification to `tests/images_endpoint/` (existing suite proven still green — the Regression floor); `tests/image_edits_variations/` reaches 100% pass with every Must/Reject branch covered by exactly one `covers:`-tagged test; `tests/catalog_db_seed/` stays green after the new migration (proves the `_UNVERIFIED_IDS`/`_EXPECTED_TOKEN_PRICES` fixed-set assertions are unaffected by the additive `dall-e-2` row).
Status: FROZEN @ v1 — approved by Tin Dang
Reported: yes — freeze card rendered as this task's final report.

### Build-strategy — Scope (may touch)

Scope (may touch): `apps/gateway/src/gateway/proxy/api/images_router.py` · `apps/gateway/src/gateway/proxy/api/images_deps.py` · `apps/gateway/src/gateway/proxy/application/images_use_case.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/migrations/versions/` · `apps/gateway/tests/image_edits_variations/` · `apps/gateway/src/gateway/main.py`
`apps/gateway/src/gateway/core/config.py`
`apps/gateway/migrations/versions/`
`apps/gateway/tests/image_edits_variations/`

Regression floor: `apps/gateway/tests/images_endpoint/` (existing `/v1/images/generations` suite — must stay green, unmodified) · `apps/gateway/tests/catalog_db_seed/` (existing seed-migration regression suite — must stay green after the new migration is added).

Persona: `protocol-translation-engineer` — this task is a provider-adapter wire surface (multipart OpenAI-wire dialect on the images seam); its Critical Rules (byte-identical no-feature-used passthrough, billing on served-model with upstream-native returned-count, every provider-shape difference gets its own named test) map directly onto this task's Must list.

Least-sure flag surfaced at freeze: [contract] — the top ⚠ (catalog-seeding-in-scope, assumption #1) is a CONTRACT-shape question (does the Schema section's new `dall-e-2` row belong in THIS task's frozen contract, or is it a dependency this task should instead take as a precondition on an existing/future model row) — not a spec ambiguity or a test-authoring risk; §1's Must list already encodes the chosen answer (seed it here), so this is the part of the FROZEN shape a reviewer should re-derive independently rather than accept on my say-so.

### AI-verify record (required when gate_mode: ai-plan-verify)
N/A — gate_mode not declared; human freezes directly.

---

## 4 · TESTS & SCENARIOS — failing-first suite (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_ie1_edits_happy_path_200_with_provider_body: arrange seed_edit_capable_model + fake provider set_multipart_response(200, EDIT_RESPONSE_BODY); act POST /v1/images/edits multipart {image, prompt, model}; assert 200 + body == upstream body verbatim + post_multipart called with path="/images/edits" · covers: M1,M3,M4
  - test_ie2_edits_single_usage_record_quantity_actual_returned: arrange 2-entry EDIT_RESPONSE_BODY; act POST edits; assert spy.call_count==1, pricing_unit="per_image", quantity==Decimal(2) (not requested n) · covers: M6
  - test_ie3_edits_zero_images_returned_bills_zero: arrange upstream returns {"data": []}; act POST edits; assert quantity==Decimal(0) · covers: M6 (edge)
  - test_ie4_edits_missing_image_rejects_422: act POST edits with only prompt, no image; assert 422 ERR_PAYLOAD_INVALID, zero usage records · covers: R:image_required
  - test_ie5_edits_missing_prompt_rejects_422: act POST edits with image, no prompt; assert 422 ERR_PAYLOAD_INVALID, zero usage records · covers: R:prompt_required
  - test_ie6_edits_oversized_image_rejects_413_pre_bill: arrange Settings.image_edit_max_bytes small; act POST edits with a larger image; assert 413 ERR_PAYLOAD_IMAGE_TOO_LARGE, post_multipart NEVER called, zero usage records · covers: R:image_too_large
  - test_ie7_edits_unknown_model_rejects_404: act POST edits with model="does-not-exist"; assert 404 ERR_MODEL_UNKNOWN · covers: R:model_unknown
  - test_ie8_edits_chat_model_modality_mismatch_rejects_400: arrange seed_chat_model; act POST edits with a chat model id; assert 400 ERR_MODEL_MODALITY_MISMATCH · covers: R:modality_mismatch
  - test_ie9_edits_generations_only_model_unsupported_input_modality_400: arrange seed_image_model (dall-e-3, generations-only, input_modalities="text"); act POST edits with model=dall-e-3; assert 400 ERR_UNSUPPORTED_INPUT_MODALITY, zero usage records, post_multipart NEVER called · covers: R:unsupported_input_modality
  - test_ie10_edits_upstream_unavailable_502: arrange FakeUpstreamProvider raising UpstreamUnavailableError from post_multipart; act POST edits; assert 502 ERR_UPSTREAM_UNAVAILABLE · covers: R:upstream_unavailable
  - test_iv1_variations_happy_path_200_with_provider_body: arrange seed_edit_capable_model + fake provider; act POST /v1/images/variations multipart {image, model}; assert 200 + verbatim body + post_multipart path="/images/variations", data has NO "prompt" key · covers: M2,M3,M4
  - test_iv2_variations_single_usage_record_quantity_actual_returned: same pattern as IE2 for variations · covers: M6
  - test_iv3_variations_missing_image_rejects_422: act POST variations with no image; assert 422 ERR_PAYLOAD_INVALID · covers: R:image_required
  - test_iv4_variations_generations_only_model_unsupported_input_modality_400: same pattern as IE9 for variations (dall-e-3) · covers: R:unsupported_input_modality
  - test_ie_iv_regression_generations_untouched: arrange seed_image_model + inject_fake_openai_provider; act POST /v1/images/generations (existing JSON-body flow); assert 200 identical to pre-task behavior + exactly 1 usage row, pricing_unit=per_image — proves ImagesUseCase.execute / images_router's existing route is byte-identical · covers: M8 (regression)
</test_plan>

Rigor: one red test per §1 Must/Reject PRIMARY case above; minor/secondary passthrough-field forwarding (`mask`, `size`, `background`, `quality`, `output_format`, `response_format`, `user`) is DESCRIBED as build-guidance (forward verbatim from `form.get(field_name)` when present, exact same idiom as `_STT_PASSTHROUGH_FIELDS` in `audio_use_case.py`) — no dedicated red test, not gated; the mask-oversize case (R:image_too_large applied to `mask` instead of `image`) is the same code path as test_ie6 and is prose-covered, not double-tested.

```gherkin
Scenario: edit request against a generations-only model is rejected before any upstream call or bill
  Given dall-e-3 is seeded (modality=image, input_modalities="text", generations-only)
  When a client POSTs /v1/images/edits with model=dall-e-3, a valid image, and a prompt
  Then the response is 400 ERR_UNSUPPORTED_INPUT_MODALITY
  And post_multipart is never called
  And zero usage_records rows exist for this request
```

Tests live in: `apps/gateway/tests/image_edits_variations/` · MUST run red (missing implementation) before Build.

Run only this suite: `cd apps/gateway && uv run pytest tests/image_edits_variations/ -q --no-cov -p no:cacheprovider`

Right-reason red targets: every non-regression test fails because `POST /v1/images/edits` / `POST /v1/images/variations` do not exist yet → 404 Not Found (asserted status differs, e.g. 200/422/413/400/404/502, so 404 trips the assertion for the RIGHT reason in every case). `test_ie_iv_regression_generations_untouched` is GREEN-BY-DESIGN (mirrors IM10/AU-regression convention in the sibling suites) — the existing `/v1/images/generations` route already works pre-task.

**Red run evidence (captured this session, 2026-07-24):**
```
$ cd apps/gateway && uv run pytest tests/image_edits_variations/ -q --no-cov -p no:cacheprovider
...
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_ie1_edits_happy_path_200_with_provider_body
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_ie2_edits_single_usage_record_quantity_actual_returned
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_ie3_edits_zero_images_returned_bills_zero
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_ie4_edits_missing_image_rejects_422
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_ie5_edits_missing_prompt_rejects_422
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_ie6_edits_oversized_image_rejects_413_pre_bill
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_ie7_edits_unknown_model_rejects_404
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_ie8_edits_chat_model_modality_mismatch_rejects_400
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_ie9_edits_generations_only_model_unsupported_input_modality_400
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_ie10_edits_upstream_unavailable_502
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_iv1_variations_happy_path_200_with_provider_body
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_iv2_variations_single_usage_record_quantity_actual_returned
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_iv3_variations_missing_image_rejects_422
FAILED tests/image_edits_variations/test_image_edits_variations.py::test_iv4_variations_generations_only_model_unsupported_input_modality_400
14 failed, 1 passed in 11.58s
```
Confirmed RIGHT reason: every failure trace shows `{"method": "POST", "path": "/v1/images/edits"|"/v1/images/variations", "status_code": 404}` — the route does not exist. `test_ie7...` (which itself asserts a 404) still fails RED because it asserts the CONTRACTED problem+json body (`code == "ERR_MODEL_UNKNOWN"`), not FastAPI's generic `{"detail":"Not Found"}` — same right-reason class. `test_ie_iv_regression_generations_untouched` is the `1 passed` — green-by-design, proving `/v1/images/generations` is untouched even before Build starts.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY — what you ACTUALLY did (or "as planned"); harvested into §7 Decisions (ADR)>
Code lives in: `apps/gateway/src/gateway/proxy/`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only (none new — `python-multipart` already installed for audio); ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests (or §4 acceptance checks) pass — including the §3 Regression floor (host suite)
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: independent add-advisor refute agent (fresh context, 2026-07-24) · adversarially checked: returned-entries billing (fewer-than-n / empty / extra fields), single-bill under post-governance 4xx, size-cap ordering incl. oversized mask, capability-gate bypass shapes (NULL/empty input_modalities), generations byte-identity, dall-e-2 migration (pricing shape, no unverified-id violation, downgrade). Two low-severity notes, no blockers: 🟡 low (not-currently-reachable path) · 💭 the 4 MiB size-cap default is dall-e-2-specific rather than a general knob (config-overridable; revisit when a second edit-capable model lands). Conceded EARNED.

### GATE RECORD
Reported: yes — gate card written to this record (autonomy: auto, no security residue; auto-resolved)
Outcome: PASS
Reviewed by: auto-resolved (orchestrator run, evidence: 15/15 task suite + 14/14 generations regression on integrated branch, independent refute EARNED) · date: 2026-07-24

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by auto-resolved (orchestrator run, evidence: 15/15 task suite + 14/14 generations regression on integrated branch, independent refute EARNED))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
