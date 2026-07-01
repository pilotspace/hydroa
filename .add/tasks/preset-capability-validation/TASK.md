# TASK: Apply v55 input-capability guard when a preset resolves to a target model

slug: preset-capability-validation · created: 2026-07-01 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
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
- `gateway/core/error_catalog.py:522-526` `UNSUPPORTED_INPUT_MODALITY` (v55, 400, reused verbatim, chat+STT only) — precedent naming for the NEW `MODEL_MODALITY_MISMATCH` spec this task adds.
- `gateway/proxy/application/modality_guard.py:32-134` `required_input_modalities_for_chat / resolve_allowed / enforce` — v55's fine-grained (text/image/audio) INPUT-content guard. Pure, side-effect-free, already composes correctly with presets by call order (see below) — no changes needed here.
- `gateway/proxy/application/use_cases.py:629-667` `CompletionUseCase._check_input_modalities`; confirmed call order in `complete()`/`stream()`: preset-resolve (the `model` field is rewritten to the preset target, ~line 1018-1035/1594-1611) → `_validate_payload` → `_enforce_governance` → `_check_input_modalities` — the guard always evaluates the PRESET-RESOLVED model, never the raw alias. No `ModelRow.modality` query exists anywhere in this file (confirmed via grep — zero hits); chat's provider resolution goes through `self._provider_resolver.provider_for(model_id)` (line 984), a wholly separate seam from `select_provider(modality, provider, registry)`. Chat is therefore NOT symmetric with images/embeddings/audio and is intentionally OUT of this task's new coarse check (§1 Reject).
- `gateway/proxy/application/audio_use_case.py:189-207` `TranscriptionUseCase.execute()` preset-resolve block (mirrors chat's, substitutes into the local `model_id`); `:218-246` catalog query (extended to fetch `input_modalities` when the flag is on) then `enforce(frozenset({"audio"}), ...)` BEFORE `select_provider` at line 249 — confirmed correct composition with presets, same call-order guarantee as chat.
- `gateway/proxy/application/audio_use_case.py:249` STT's `provider_adapter.post_multipart(...)` path: `OpenRouterUpstreamFacade.post_multipart` unconditionally raises `UpstreamUnavailableError` — STT can never be silently misrouted to chat, unlike post_json/stream_bytes below. This is why STT is excluded from the NEW coarse check: it is already safe by construction and already guarded on the input-content axis.
- `gateway/proxy/application/images_use_case.py:130-142` — Step 3 governance, Step 4 catalog query (`select(ModelRow.modality, ModelRow.provider)...`, line 133), Step 5 `select_provider(row.modality, row.provider, registry)` (line 142). **No modality/operation-type check exists between Step 4 and Step 5** — the new guard's insertion point.
- `gateway/proxy/application/embeddings_use_case.py:168-178` — same shape: catalog query (line 169), `select_provider` (line 178), nothing between them.
- `gateway/proxy/application/audio_use_case.py:465-475` `SpeechUseCase.execute()` (TTS) — catalog query (line 466), `select_provider` (line 475), nothing between them.
- `gateway/proxy/infrastructure/provider_registry.py:31-58` `select_provider(modality, provider, registry)` — docstring line 43-44 admits `modality` "not used in v7"; routing is a pure `provider`-name dict lookup (line 55). Confirms no modality/operation-type enforcement exists anywhere downstream either.
- `gateway/proxy/infrastructure/openrouter_upstream_provider.py:37-47,66-76` `OpenRouterUpstreamFacade.post_json`/`.stream_bytes` — BOTH ignore the `path` argument and always call `self._upstream.complete(payload)`/`.stream(payload)` (the CHAT upstream), by design (module docstring: "OpenRouter always uses /chat/completions"). Root cause of the live bug: a wrong-modality model (e.g. a chat model resolved for `/v1/images/generations`) silently gets a chat completion back (mislabeled/garbled response) and is still billed, because nothing upstream of this facade ever checked the model was actually an image model. `post_multipart` (line 49-64) instead raises loudly — no risk there (see above).
- `gateway/proxy/infrastructure/openai_provider.py:200-228` `OpenAIDirectProvider.post_json` — correctly forwards `path`, so no misrouting on this path, but passes the provider's raw, un-normalized JSON error straight through (line 228: `return status, resp.json()` for status<500) — a secondary, less severe symptom of the same root gap (no coarse check catches the mismatch before the call).
- `gateway/catalog/domain/entities.py:18-22` `Modality = Literal["chat","embedding","image","audio_stt","audio_tts"]`, `VALID_MODALITIES`. `entities.py:29-31` `InputModality = Literal["text","image","audio"]` — confirmed a SEPARATE axis from `Modality`; `default_input_modalities(modality)` (line 39+) derives a conservative `InputModality` default FROM `Modality`, but nothing derives or checks the reverse (endpoint → required `Modality`).
- `gateway/catalog/infrastructure/orm.py:34` `ModelRow.modality: Mapped[str]` (`server_default="chat"`) — every active catalog row already carries a real value; not optional/nullable data.
- `gateway/proxy/api/realtime_ws.py:189-196` `_real_stt` builds `TranscriptionUseCase(governance=..., session=..., tenant_credential_resolver=..., max_duration_seconds=..., authenticator=..., tenant_model_preset_store=...)` — omits `input_modality_guard_enabled` entirely (confirmed: `TranscriptionUseCase` has NO `input_modality_lookup` param at all — STT gets `input_modalities` from its own inline catalog query, so only the bool flag is missing here).
- `gateway/proxy/api/realtime_ws.py:256-269` `_real_chat` builds `CompletionUseCase(authenticator=..., model_checker=..., budget_guard=..., rate_limiter=..., span_emitter=..., stream_resilience_enabled=..., tenant_credential_resolver=..., provider_resolver=..., cost_recovery=..., bandwidth_bucket=..., bandwidth_max_wait_s=..., tenant_model_preset_store=...)` — omits BOTH `input_modality_lookup` and `input_modality_guard_enabled`.
- `gateway/proxy/api/deps.py:212-231` `get_completion_use_case` — the correct reference wiring: `input_modality_lookup=input_modality_lookup` (line 228, built at line 208 via `SqlAlchemyInputModalityLookup(session)`), `input_modality_guard_enabled=input_modality_guard_enabled` (line 229, from settings at 209-211).
- `gateway/proxy/api/audio_deps.py:99-109` `get_transcription_use_case` — reference wiring: `input_modality_guard_enabled=input_modality_guard_enabled` (line 106).
- `gateway/proxy/application/use_cases.py:513-514` `CompletionUseCase.__init__`: `input_modality_lookup: InputModalityLookup | None = None`, `input_modality_guard_enabled: bool = False`.
- `gateway/proxy/application/audio_use_case.py:133` `TranscriptionUseCase.__init__`: `input_modality_guard_enabled: bool = False` (no lookup param).
- `gateway/core/config.py:340` `input_modality_guard_enabled: bool = Field(default=False)` — v55's precedent: a NEW behavioral restriction defaults OFF because it can reject previously-accepted ambiguous multi-input traffic. This task's new coarse check is a different risk shape (§1 Assumptions ⚠).

Context (working folder):
- `.add/tasks/unsupported-input-guard/TASK.md:26` (v55, archived/done) — explicit scoping-out: *"Endpoints in scope: /v1/chat/completions (content-parts) + /v1/audio/transcriptions (STT, audio input). Out: /v1/embeddings, /v1/images/generations (text-only inputs — no multi-type ambiguity; can be a follow-up)"* — this task IS that follow-up, but for a DIFFERENT axis (coarse operation-type, not fine-grained input-content-type — embeddings/images/TTS have no multi-type input ambiguity, so v55's axis doesn't apply; the actual gap is a model/endpoint TYPE mismatch).
- `gateway/proxy/domain/model_presets.py:10-12` module docstring explicitly defers capability guarding to "separate, later tasks" — this task.
- `.add/milestones/v56/MILESTONE.md:25,34` — "Reuse v55's frozen `unsupported_input_modality` error verbatim when a preset target rejects the input" / task line scoping this to "on resolve, apply v55's input-capability guard." Superseded by this GROUND: v55's guard already composes correctly (chat+STT need only regression tests); the material, previously-unknown gap is the coarse operation-type check for images/embeddings/TTS, confirmed via AskUserQuestion with Tin as this task's real deliverable.
- `apps/gateway/tests/provider_seam/test_provider_seam.py:326-370` `test_ps7_non_chat_active_model_passes_catalog_check` — existing test asserting the `ModelChecker` is deliberately "modality-agnostic by construction" for ACTIVATION checks (active/tenant-override only). This task's new check is a DIFFERENT, additive gate (operation-type-vs-endpoint) layered on top, not a change to `ModelChecker` — must not regress this test.
- `apps/gateway/tests/input_modality_guard/test_input_modality_guard.py` — v55's existing guard test home; this task's new chat/STT regression tests for preset-composition live in a new sibling dir (not edits to this frozen file).

Honors (patterns / conventions):
- Design-for-failure (CLAUDE.md): the new check is a pure in-memory comparison (`row.modality != expected`) with zero new I/O, timeouts, or retry surface — it re-uses data already fetched in the SAME catalog query each use case already runs (no extra round-trip).
- Fail-closed vs fail-open precedent split: v55's INPUT-content guard fails OPEN on missing capability data (`allowed is None -> return`) because that axis is optional/nullable-ish historically-backfilled data. `ModelRow.modality` is NOT optional (`server_default="chat"`, always a real value) — so this task's check is fail-CLOSED by construction (there is no "unknown modality" state to fail open on); a `Literal` violates at the DB layer if this were ever wrong.
- `ProblemError`/`ErrorSpec` catalog pattern (`gateway/core/error_catalog.py`): every new wire error is a module-level `ErrorSpec(status, code, template)` constant with a doc-comment above it, `.exc(**kwargs)` to raise — followed exactly for the new `MODEL_MODALITY_MISMATCH` spec.
- Single-bill invariant (per-endpoint `_fire_record_with_raw`): the new check MUST run before Step "Call upstream" and before any usage-record fire, mirroring where v55's `enforce()` sits in STT (before `select_provider`, before billing) — never after.

Anchors the contract cites:
- `MODEL_MODALITY_MISMATCH` (new `ErrorSpec`, `gateway/core/error_catalog.py`)
- `images_use_case.py` Step 4→5 gap (line 137→142)
- `embeddings_use_case.py` Step 4→5 gap (line 175→178)
- `audio_use_case.py` `SpeechUseCase.execute()` Step 5→6 gap (line 472→475)
- `realtime_ws.py` `_real_chat` (line 256-269) / `_real_stt` (line 189-196) constructor calls
- `deps.py:228-229` / `audio_deps.py:106` as the reference wiring `_real_chat`/`_real_stt` must mirror

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Capability-aware validation for preset-resolved (and directly-named) models — v55's fine-grained input-content guard confirmed composing correctly with presets on chat+STT, plus a NEW coarse operation-type guard closing a live, general (non-preset-specific) misrouting/billing bug on images/embeddings/TTS, plus a realtime-WS wiring fix.
Framings weighed:
  (chosen) Three bounded sub-scopes, each independently testable: (A) chat+STT regression-only (lock in existing correct composition, zero new prod code); (B) a NEW `MODEL_MODALITY_MISMATCH` coarse check on images/embeddings/TTS, unconditional (no feature flag); (C) realtime-WS wiring fix reusing v55's existing guard params.
  · Extend v55's fine-grained `InputModality` axis (text/image/audio) to images/embeddings/TTS instead of a new coarse axis — REJECTED: those endpoints have exactly one input shape each (a prompt string, a text array, a text string) with no multi-type ambiguity; the real gap is not "what content types does this request contain" but "is this model even the right OPERATION type for this endpoint," which is what `ModelRow.modality` (chat/embedding/image/audio_stt/audio_tts) already encodes.
  · Fix the bug at `OpenRouterUpstreamFacade`/`openai_provider.py` (make `post_json`/`stream_bytes` respect `path`, normalize passthrough errors) — REJECTED as the primary fix: more invasive (touches the shared chat-upstream facade and every existing chat/embeddings/images caller), and still leaves the door open for a wrong-type model to reach the upstream at all. Rejecting BEFORE `select_provider` is strictly safer and smaller — this task does not touch the facade or `openai_provider.py`.
  · Gate the new coarse check behind a new default-OFF flag (mirroring v55's `input_modality_guard_enabled` precedent) — REJECTED (flagged ⚠ below): Tin's explicit ask was to "fix the general bug," and every catalog row already carries a real, non-optional `modality` value — there is no "unknown/absent data" state to justify an opt-in default-OFF posture the way v55's optional input-modality axis had.
Must:
<must>
  - A preset that resolves to a model whose `input_modalities` cannot satisfy a chat request's content-parts (image/text) is rejected with v55's existing `ERR_UNSUPPORTED_INPUT_MODALITY` — proven via regression test, not new code (chat already resolves the preset before running the guard).
  - A preset that resolves to a model whose `input_modalities` lacks "audio" is rejected the same way on `/v1/audio/transcriptions` — regression test only.
  - A request to `/v1/images/generations` whose (preset-resolved or directly-named) model's catalog `modality != "image"` is rejected with the NEW `ERR_MODEL_MODALITY_MISMATCH` (400), BEFORE `select_provider`, BEFORE any upstream call, BEFORE any usage-record fire.
  - Same for `/v1/embeddings` (`modality != "embedding"`) and `/v1/audio/speech` TTS (`modality != "audio_tts"`).
  - The realtime-WS `/v1/realtime` endpoint's ad-hoc `_real_chat`/`_real_stt` use-case construction wires the SAME `input_modality_lookup`/`input_modality_guard_enabled` params the HTTP DI path (`deps.py`/`audio_deps.py`) already wires, reading the SAME `settings.input_modality_guard_enabled` flag (not a hardcoded value) — so realtime-WS behaves identically to HTTP under the same config.
  - A model whose modality already matches the endpoint (the overwhelming common case) is byte-identical to today — no new latency, no new query (the check reads `row.modality`, already fetched in the SAME catalog SELECT each use case runs).
Reject:
<reject>
  - images/embeddings/TTS request resolves to a model with the wrong `modality` -> "ERR_MODEL_MODALITY_MISMATCH" (400)
  - chat/STT request (via preset or direct name) resolves to a model missing a required input content-type -> "ERR_UNSUPPORTED_INPUT_MODALITY" (400, v55, unchanged) — regression-locked, not newly introduced
</reject>
After:
<after>
  - Every one of the 5 preset-reachable entry points (chat, images, embeddings, STT, TTS) plus realtime-WS chat/STT is protected by SOME capability guard appropriate to its risk shape — no entry point silently misroutes or bills a request against a model that cannot serve it.
  - The `OpenRouterUpstreamFacade` silent-misrouting bug (wrong-type model → chat upstream → mislabeled response, still billed) can no longer occur on images/embeddings/TTS, because the request never reaches `select_provider`/the facade when the modality is wrong.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The new coarse check should be unconditional (no feature flag), unlike every prior capability-guard task in this codebase (v55's `input_modality_guard_enabled`, default OFF) — lowest confidence because it breaks a consistent 1-task-old precedent of opt-in rollout for new rejection behavior; if wrong: an operator relying on today's broken-but-silent behavior (unlikely — nothing "works" today when modality is wrong, it just fails invisibly) would see a NEW 400 with no way to opt back to the old behavior without a code change. Mitigated by presenting this explicitly at the §3 freeze for Tin's sign-off, since Tin already said "fix the general bug" (not "add another opt-in knob").
  - [ ] `_real_chat` in `realtime_ws.py` has a DB `session` variable already in local scope to construct `SqlAlchemyInputModalityLookup(session)` (confirmed present for `_real_stt`'s `TranscriptionUseCase(session=session, ...)`; unconfirmed for `_real_chat` since `CompletionUseCase` itself takes no `session` kwarg) — to confirm at BUILD; if wrong: the fix needs the connection wired in from wherever `_real_stt`/`_real_tts` get theirs first (small, mechanical, not a design change).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: preset resolves to an image-incapable model for a chat request with an image part
  Given tenant has preset "cheap:vision" -> a text-only chat model (input_modalities="text")
  And GATEWAY_INPUT_MODALITY_GUARD_ENABLED=true
  When the tenant POSTs /v1/chat/completions with model="cheap:vision" and a messages[] image_url part
  Then the response is 400 application/problem+json code=ERR_UNSUPPORTED_INPUT_MODALITY
  And no upstream call was made and no usage record was written

Scenario: preset resolves to a text-only model for an STT request
  Given tenant has preset "cheap:stt" -> a model with input_modalities lacking "audio"
  And GATEWAY_INPUT_MODALITY_GUARD_ENABLED=true
  When the tenant POSTs /v1/audio/transcriptions with model="cheap:stt"
  Then the response is 400 application/problem+json code=ERR_UNSUPPORTED_INPUT_MODALITY
  And no upstream call was made and no usage record was written

Scenario: preset resolves to a chat model for an images request
  Given tenant has preset "cheap:img" -> an active catalog model with modality="chat"
  When the tenant POSTs /v1/images/generations with model="cheap:img"
  Then the response is 400 application/problem+json code=ERR_MODEL_MODALITY_MISMATCH
  And no upstream call was made and no usage record was written

Scenario: directly-named (no preset) chat model used for embeddings
  Given an active catalog model with modality="chat" and no preset involved
  When the tenant POSTs /v1/embeddings with that model id directly
  Then the response is 400 application/problem+json code=ERR_MODEL_MODALITY_MISMATCH
  And no upstream call was made and no usage record was written
  # proves the fix is general, not preset-specific

Scenario: preset resolves to an embedding model for a TTS request
  Given tenant has preset "cheap:tts" -> an active catalog model with modality="embedding"
  When the tenant POSTs /v1/audio/speech with model="cheap:tts"
  Then the response is 400 application/problem+json code=ERR_MODEL_MODALITY_MISMATCH
  And no upstream call was made and no usage record was written

Scenario: matching modality passes through unchanged (images)
  Given an active catalog model with modality="image"
  When the tenant POSTs /v1/images/generations with that model id (direct or via a preset)
  Then the request proceeds to select_provider/upstream exactly as before this task
  And the response shape, status, and billing are byte-identical to pre-task behavior

Scenario: matching modality passes through unchanged (embeddings)
  Given an active catalog model with modality="embedding"
  When the tenant POSTs /v1/embeddings with that model id
  Then the request proceeds to select_provider/upstream exactly as before this task
  And the response shape, status, and billing are byte-identical to pre-task behavior

Scenario: matching modality passes through unchanged (TTS)
  Given an active catalog model with modality="audio_tts"
  When the tenant POSTs /v1/audio/speech with that model id
  Then the request proceeds to select_provider/upstream exactly as before this task
  And the response shape, status, and billing are byte-identical to pre-task behavior

Scenario: realtime-WS chat turn honors the input-modality guard like the HTTP path
  Given GATEWAY_INPUT_MODALITY_GUARD_ENABLED=true and a connected /v1/realtime session
  And a commit frame names a text-only chat model plus a transcript requiring image input
  When the turn resolves the chat use case
  Then the same ERR_UNSUPPORTED_INPUT_MODALITY-equivalent rejection fires as the HTTP chat path would
  And the WS turn surfaces it as a non-fatal {"type":"error","code":"chat_failed",...} frame, socket stays open

Scenario: realtime-WS STT turn honors the input-modality guard like the HTTP path
  Given GATEWAY_INPUT_MODALITY_GUARD_ENABLED=true and a connected /v1/realtime session
  And the commit frame names an STT model whose input_modalities lacks "audio"
  When the turn resolves the STT use case
  Then the same rejection fires as the HTTP /v1/audio/transcriptions path would
  And the WS turn surfaces it as a non-fatal {"type":"error","code":"stt_failed",...} frame, socket stays open

Scenario: ModelChecker activation check remains modality-agnostic (no regression)
  Given the existing test_ps7_non_chat_active_model_passes_catalog_check fixture (modality="embedding" model)
  When ModelChecker.is_active / check_for_tenant runs against it
  Then it still passes exactly as before — this task adds a NEW, separate gate; it does not touch ModelChecker
  And no existing provider-seam test's assertions change
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# No NEW endpoint. Three additive changes to EXISTING endpoints/wiring.

# A) New error — gateway/core/error_catalog.py
MODEL_MODALITY_MISMATCH = ErrorSpec(
    400, "ERR_MODEL_MODALITY_MISMATCH",
    "Model '{model_id}' does not support this operation",
)
  Raised via .exc(model_id=model_id, detail=f"model '{model_id}' has modality '{actual}', endpoint requires '{expected}'")

# B) New coarse guard — unconditional (no feature flag), inserted AFTER the existing catalog
#    query (which already SELECTs ModelRow.modality) and BEFORE select_provider(...), in:
POST /v1/images/generations   (images_use_case.py, between line 137 `row = ...` and line 142 `select_provider`)
  expected modality = "image"
POST /v1/embeddings           (embeddings_use_case.py, between line 175 `row = ...` and line 178 `select_provider`)
  expected modality = "embedding"
POST /v1/audio/speech (TTS)   (audio_use_case.py SpeechUseCase.execute, between line 472 `row = ...` and line 475 `select_provider`)
  expected modality = "audio_tts"
  -> 400 application/problem+json when row.modality != expected:
       { "type":"about:blank", "title":"Model does not support this operation",
         "status":400, "code":"ERR_MODEL_MODALITY_MISMATCH",
         "detail":"model '<model_id>' has modality '<actual>', endpoint requires '<expected>'" }
     Fired BEFORE select_provider, BEFORE upstream call, BEFORE usage record (single-bill invariant preserved).
     row.modality == expected (the common case) -> byte-identical to today, zero new I/O (reuses the SAME SELECT).
  NOT applied to: /v1/chat/completions (no ModelRow query exists in this seam — see §0 GROUND) or
  /v1/audio/transcriptions (already safe by construction — post_multipart raises loudly on misroute,
  and already guarded on the input-content axis).

# C) Wiring fix — gateway/proxy/api/realtime_ws.py (reuses v55's EXISTING guard, no new logic)
_real_stt (TranscriptionUseCase ctor, lines 189-196): add
    input_modality_guard_enabled=_settings.input_modality_guard_enabled
_real_chat (CompletionUseCase ctor, lines 256-269): add
    input_modality_lookup=SqlAlchemyInputModalityLookup(session),
    input_modality_guard_enabled=_settings.input_modality_guard_enabled
  Mirrors deps.py:228-229 (chat) and audio_deps.py:106 (STT) exactly — reads the SAME settings flag,
  never a hardcoded value. _real_tts needs NO wiring change: (B)'s check lives inside
  SpeechUseCase.execute() itself (unconditional), so it applies automatically wherever SpeechUseCase
  is constructed, including realtime_ws's _real_tts.

Schema: no migration. Reads gateway.catalog.infrastructure.orm.ModelRow.modality (existing column,
  server_default="chat", already selected by every one of the 3 use cases above in their existing
  catalog query — this task adds a comparison, not a query).
```

Status: FROZEN @ v1 — approved by Tin Dang (2026-07-01)
Least-sure flag surfaced at freeze: [spec] the new coarse check is deliberately UNCONDITIONAL (no
  feature flag), diverging from v55's default-OFF rollout precedent — surfaced and explicitly
  confirmed via AskUserQuestion ("Unconditional, no flag (Recommended)"); cost if wrong: a caller
  depending on today's silent-misroute behavior sees a new 400 with no config-level rollback
  (mitigated: no legitimate traffic can depend on a request that today either garbles/misroutes or
  raw-passes-through a provider error).
Second flag: [assumption] `_real_chat`'s access to a DB `session` to construct
  `SqlAlchemyInputModalityLookup(session)` is unconfirmed (only `_real_stt` was directly verified) —
  to resolve at BUILD; if wrong, a small mechanical wiring adjustment, not a design change.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_chat_preset_resolves_image_incapable_model_rejected: arrange tenant+preset->text-only chat model, guard flag ON / act POST /v1/chat/completions model="preset:alias" + image_url part / assert 400 ERR_UNSUPPORTED_INPUT_MODALITY + assert no usage row written (regression, no new prod code)
  - test_stt_preset_resolves_audio_incapable_model_rejected: arrange tenant+preset->model missing "audio" in input_modalities, guard flag ON / act POST /v1/audio/transcriptions model="preset:alias" / assert 400 ERR_UNSUPPORTED_INPUT_MODALITY + assert no usage row written (regression, no new prod code)
  - test_images_preset_resolves_wrong_modality_model_rejected: arrange tenant+preset->model modality="chat" / act POST /v1/images/generations model="preset:alias" / assert 400 ERR_MODEL_MODALITY_MISMATCH + assert no usage row written + assert upstream never called (mock/spy)
  - test_images_direct_model_wrong_modality_rejected: arrange active model modality="chat", NO preset involved / act POST /v1/images/generations model=<direct id> / assert 400 ERR_MODEL_MODALITY_MISMATCH + assert no usage row written — proves the fix is general, not preset-gated
  - test_embeddings_wrong_modality_model_rejected: arrange active model modality="chat" / act POST /v1/embeddings model=<direct id> / assert 400 ERR_MODEL_MODALITY_MISMATCH + assert no usage row written
  - test_tts_preset_resolves_wrong_modality_model_rejected: arrange tenant+preset->model modality="embedding" / act POST /v1/audio/speech model="preset:alias" / assert 400 ERR_MODEL_MODALITY_MISMATCH + assert no usage row written
  - test_images_matching_modality_passes_through_unchanged: arrange active model modality="image" / act POST /v1/images/generations / assert 200 + assert billed exactly as pre-task (byte-identical baseline snapshot)
  - test_embeddings_matching_modality_passes_through_unchanged: arrange active model modality="embedding" / act POST /v1/embeddings / assert 200 + assert billed exactly as pre-task
  - test_tts_matching_modality_passes_through_unchanged: arrange active model modality="audio_tts" / act POST /v1/audio/speech / assert 200 (streamed) + assert billed exactly as pre-task
  - test_realtime_ws_chat_turn_rejects_incompatible_model: arrange connected /v1/realtime session, guard flag ON, commit frame names text-only chat model + transcript requiring image / act send commit frame / assert non-fatal {"type":"error","code":"chat_failed"} frame + socket stays open (proves _real_chat wiring fix)
  - test_realtime_ws_stt_turn_rejects_incompatible_model: arrange connected /v1/realtime session, guard flag ON, commit frame names STT model missing "audio" in input_modalities / act send commit frame / assert non-fatal {"type":"error","code":"stt_failed"} frame + socket stays open (proves _real_stt wiring fix)
  - test_model_checker_activation_check_still_modality_agnostic: arrange active model modality="embedding" (mirrors existing test_ps7 fixture, constructed fresh in this task's own test file, not editing the frozen provider_seam test) / act ModelChecker.is_active + check_for_tenant / assert both pass exactly as before — proves this task's new gate is additive, not a change to ModelChecker
</test_plan>

Tests live in: `apps/gateway/tests/preset_capability_validation/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/core/error_catalog.py` (add `MODEL_MODALITY_MISMATCH`)
  `apps/gateway/src/gateway/proxy/application/images_use_case.py` (insert guard call)
  `apps/gateway/src/gateway/proxy/application/embeddings_use_case.py` (insert guard call)
  `apps/gateway/src/gateway/proxy/application/audio_use_case.py` (insert guard call in `SpeechUseCase.execute`; NO change to `TranscriptionUseCase` production logic — regression tests only)
  `apps/gateway/src/gateway/proxy/api/realtime_ws.py` (wire `input_modality_lookup`/`input_modality_guard_enabled` into `_real_chat`/`_real_stt`)
  `apps/gateway/tests/preset_capability_validation/` (NEW test dir, all 12 tests)
Strategy (ordered batches):
  1. Write all 12 tests RED first (missing `MODEL_MODALITY_MISMATCH` import + unwired realtime params -> ImportError/AttributeError or assertion failures for the right reason).
  2. Add `MODEL_MODALITY_MISMATCH` to `error_catalog.py`.
  3. Insert the coarse guard call in images/embeddings/TTS use cases (mirrors v55's `enforce()` placement: after catalog query, before `select_provider`).
  4. Wire `realtime_ws.py`'s `_real_chat`/`_real_stt` to match `deps.py`/`audio_deps.py` exactly (resolve the ⚠ `session`-availability assumption here).
  5. Run full gateway suite; confirm the 3 byte-identical scenarios plus the ModelChecker regression scenario are unaffected.
Known-problem fixes:
  trap: forgetting the single-bill invariant (guard must fire before `_fire_record_with_raw`) -> planned fix: insert strictly between the catalog SELECT and `select_provider`, verified by grep for `select_provider(` in each touched use case showing the guard call immediately precedes it.
  trap: hardcoding `input_modality_guard_enabled=True` in realtime_ws instead of reading `_settings.input_modality_guard_enabled` -> planned fix: mirror `deps.py:229`/`audio_deps.py:106` exactly, read from settings.
  trap: `_real_chat` lacking a `session` var to construct `SqlAlchemyInputModalityLookup(session)` -> planned fix: locate wherever `_real_stt` sources its `session` and reuse the same connection/scope for `_real_chat`.
Strategy actually used: as planned (RED tests → error spec → 3 guard insertions → realtime_ws wiring → full-suite confirm), plus an unplanned remediation: the build exposed 5 stale fixtures in the already-gated `preset_resolution_ingress` suite (3 `FakeSession(modality="audio",...)` TTS fixtures now correctly rejected by the new guard; 2 minimal settings stubs missing `input_modality_guard_enabled`) — fixed with Tin's explicit authorization, no assertion weakened. A post-build adversarial refute-read then surfaced a HARD-STOP-class finding (catalog sync never wrote real `modality` values in this stale worktree, which would make the new unconditional guard reject ~100% of real images/embeddings/TTS traffic) — resolved by committing all 4 v56 tasks as separate commits, then merging `origin/main` to pull in the already-shipped fix (commit 3469a1e, PR #50), then re-verifying.
Safety rule (feature-specific): the guard is a pure comparison against already-fetched data (no new I/O, no new transaction) — reject-before-any-side-effect (upstream call, credential resolution, usage record) is enforced by insertion ORDER, not a flag.
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full gateway suite post-merge: 2136 passed, 7 skipped, 28 deselected, 0 failed (up from the 2122 pre-merge baseline by exactly the 14 new openrouter-embeddings tests the merge brought in). This task's own suite: 12/12 passed (`tests/preset_capability_validation/`, 15.03s).
- [x] coverage did not decrease — line-coverage tooling under-reports post-await lines in this file family: coverage.py has no `concurrency = greenlet` configured (confirmed absent from `[tool.coverage.run]` in pyproject.toml), and SQLAlchemy's async engine runs driver calls via `greenlet_spawn`, which coverage.py's default sys.settrace tracer cannot see past. Verified this is PRE-EXISTING and project-wide, not caused by this task: running the mature, pre-existing `tests/images_endpoint/test_images_endpoint.py` suite (which asserts `status_code == 200` on the real success path) shows the IDENTICAL "missing" line range (139-193) as this task's own suite. The guard's real execution is proven behaviorally instead — every reject test asserts the exact new `MODEL_MODALITY_MISMATCH.code` + 400 + zero upstream calls + zero usage rows, which cannot pass unless the guard code actually ran. Forward-carried as a competency delta (§7) — not this task's to fix.
- [x] no test or contract was altered during build — with one named exception, explicitly authorized by Tin: 5 stale fixtures in the already-gated `preset_resolution_ingress` suite were updated (3 `FakeSession(modality="audio",...)` → `"audio_tts"`; 2 settings stubs gained `input_modality_guard_enabled = False`) because this task's new checks correctly exposed data that predated the coarse guard. No assertion was weakened — all 20 tests in that file still pass, and now against production-accurate fixtures.
- [x] the green was EARNED, not gamed — refute-read run by an independent adversarial subagent; verdict below. Two real findings surfaced, both resolved/deferred appropriately (see verdict).
- [x] concurrency / timing of the risky operation is safe — single-bill invariant confirmed by direct code read: in all 3 touched use cases the guard's `raise` sits strictly between the catalog SELECT and `select_provider(...)`/`resolve_provider_credential(...)`/upstream call/`_fire_record_with_raw(...)` — e.g. `images_use_case.py:147` (guard) vs `:156` (select_provider) vs `:180` (billing). No new I/O, no new transaction — pure comparison against already-fetched data.
- [x] no exposed secrets, injection openings, or unexpected dependencies — `MODEL_MODALITY_MISMATCH.exc()`'s `detail=` f-string interpolates `model_id` (already validated/looked-up against the catalog by this point) and `row.modality` (a catalog-controlled enum value), mirroring the existing `MODEL_UNKNOWN` pattern. Zero new third-party dependencies.
- [x] layering & dependencies follow CONVENTIONS.md — guard lives in the application layer next to the existing catalog query it reuses; no new layer violations.
- [ ] a person reviewed and approved the change — Tin approved the CONTRACT at freeze (§3, v1, 2026-07-01) and authorized every judgment call this build required (guard rollout mechanism, the 5 fixture fixes, the commit/merge remediation plan). The CODE itself has had two independent AI-driven reviews (my own line-by-line diff review + a dedicated adversarial refute-read subagent) but not yet a human line-by-line read — pending final human sign-off at PR review.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] a chat/STT preset resolving to an incapable model still 400s with ERR_UNSUPPORTED_INPUT_MODALITY, unchanged from pre-task — confirmed by `test_chat_preset_resolves_image_incapable_model_rejected` / `test_stt_preset_resolves_audio_incapable_model_rejected` passing WITHOUT any change to `use_cases.py`/`modality_guard.py`'s enforcement logic (zero diff to either file this task)
- [x] images/embeddings/TTS reject a wrong-modality model with ERR_MODEL_MODALITY_MISMATCH BEFORE any upstream call — confirmed by `fake_provider.post_json_calls`/`.stream_bytes_calls` == 0 in all 4 rejection tests (3, 4, 5, 6)
- [x] the rejection fires before any usage record — confirmed by `spy.call_count == 0` after every rejection test
- [x] a directly-named (non-preset) wrong-modality model is ALSO rejected — confirmed by `test_images_direct_model_wrong_modality_rejected` AND `test_embeddings_wrong_modality_model_rejected` (both use a bare model_id, no preset selector at all), proving the fix is general, not preset-specific
- [x] a matching-modality model is byte-identical to pre-task — confirmed by the 3 pass-through tests (7, 8, 9) asserting 200 + correct upstream-call-count + correct `pricing_unit`/`quantity` billing shape
- [x] realtime-WS chat/STT turns now honor the SAME guard config as the HTTP path — confirmed by tests 10/11 passing (both were RED before the `_real_chat`/`_real_stt` wiring fix: the guard flag defaulted to disabled at those construction sites, so the turn completed normally instead of surfacing `chat_failed`/`stt_failed`)
- [x] `ModelChecker`'s activation check is untouched — confirmed by `test_model_checker_activation_check_still_modality_agnostic` passing AND the pre-existing `tests/provider_seam/test_provider_seam.py` suite passing with zero file changes (part of the 2136-passed full run)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `MODEL_MODALITY_MISMATCH` imported+raised in `images_use_case.py:28,148`, `embeddings_use_case.py`, `audio_use_case.py` (SpeechUseCase only); `input_modality_lookup=`/`input_modality_guard_enabled=` wired into `_real_chat`/`_real_stt` in `realtime_ws.py`, confirmed by grep showing no orphaned import and by tests 10/11 flipping from RED to GREEN across exactly that wiring change.
- [x] DEAD-CODE (code) — no new unused symbol; `MODEL_MODALITY_MISMATCH` and the realtime kwargs are each referenced at ≥1 call site.
- [x] SEMANTIC (prose / non-code) — read in full: `images_use_case.py`, `embeddings_use_case.py`, `audio_use_case.py`, `realtime_ws.py` diffs (all 5 touched files); `test_preset_capability_validation.py` (all 685 lines, all 12 tests) — confirmed each test's setup/assertions match its named scenario and genuinely exercise the HTTP layer (real FastAPI routing + real Postgres session), not a stubbed-away shortcut.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: agent (adversarial refute-read subagent) + self (post-merge follow-up) · adversarially checked:
  (1) Is the single-bill invariant really preserved, or could the guard fire after a partial
      side-effect? → read all 3 call sites; guard sits strictly before select_provider/credential
      resolution/upstream/billing in every case. No issue.
  (2) Is "STT is safe by construction" a general claim or provider-specific? → CONFIRMED a real
      gap: `OpenAIDirectProvider.post_multipart` (openai_provider.py:230-259) does not raise
      `UpstreamUnavailableError` the way OpenRouter's facade does, so the doc-comment's claim is
      provider-specific, not general. Pre-existing gap, no production code in `TranscriptionUseCase`
      touched by this task — forward-carried as a SPEC delta (§7) rather than fixed inline (would
      have expanded scope beyond the frozen contract).
  (3) Does the new unconditional guard assume catalog `modality` is always real data? → CONFIRMED
      a HARD-STOP-class gap at the time it was raised: `catalog/infrastructure/repository.py`'s
      `_upsert_model` never wrote `modality` on sync in this stale worktree, meaning the guard, as
      built, would have rejected ~100% of real images/embeddings/TTS traffic. RESOLVED by merging
      `origin/main` (commit c307948) to pull in the already-shipped fix (3469a1e, PR #50) — verified
      post-merge via direct code read (`repository.py:210` now writes `modality=model.modality` on
      both insert and conflict-update) and via the full suite (2136 passed, 0 failed).
  (4) Is the "12 passed" green earned, or could coverage gaps mean the guard never really executes?
      → investigated a startling coverage.py report showing the 3 guard lines as 0%-covered; traced
      to a pre-existing, project-wide coverage.py/SQLAlchemy-async-greenlet tracing gap (no
      `concurrency = greenlet` configured) — confirmed identical on the mature, pre-existing
      `tests/images_endpoint/` suite. The guard's execution is proven correctly by behavioral
      assertions (exact new error code + zero upstream calls + zero usage rows), not by the
      (broken) instrument's line count.

### GATE RECORD
Outcome: PASS
Reviewed by: Claude (self-review of every touched file + a dedicated adversarial refute-read
  subagent); Tin Dang approved the CONTRACT at freeze and every judgment call the build required
  (rollout mechanism, fixture-fix authorization, commit/merge remediation plan) · date: 2026-07-01
  · human line-by-line code review of the final diff is the next step, at PR creation.

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang (2026-07-01))
- [AI] build — strategy used: as planned (RED tests → error spec → 3 guard insertions → realtime_ws wiring → full-suite confirm), plus an unplanned remediation: the build exposed 5 stale fixtures in the already-gated `preset_resolution_ingress` suite (3 `FakeSession(modality="audio",...)` TTS fixtures now correctly rejected by the new guard; 2 minimal settings stubs missing `input_modality_guard_enabled`) — fixed with Tin's explicit authorization, no assertion weakened. A post-build adversarial refute-read then surfaced a HARD-STOP-class finding (catalog sync never wrote real `modality` values in this stale worktree, which would make the new unconditional guard reject ~100% of real images/embeddings/TTS traffic) — resolved by committing all 4 v56 tasks as separate commits, then merging `origin/main` to pull in the already-shipped fix (commit 3469a1e, PR #50), then re-verifying.
- [AI] verify — gate PASS (reviewed by Claude (self-review of every touched file + a dedicated adversarial refute-read)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] `OpenAIDirectProvider.post_multipart` should raise the same loud upstream-unavailable
  signal `OpenRouterUpstreamFacade.post_multipart` does, instead of forwarding unconditionally — the
  "STT is safe by construction" doc claim only holds for OpenRouter today (evidence: refute-read
  finding 1, openai_provider.py:230-259; pre-existing gap, out of this task's frozen scope).
- [SPEC · dropped] coverage.py under-reports lines executed inside SQLAlchemy async-engine coroutines
  after the first `await session.execute(...)` — missing `concurrency = ["greenlet", "thread"]` in
  `[tool.coverage.run]` (evidence: identical 0%-coverage artifact reproduced on the mature, unrelated
  `tests/images_endpoint/` suite). Dropped rather than seeded: purely a measurement-accuracy issue,
  no behavioral risk — worth a dedicated tiny task if/when accurate coverage numbers become load-bearing.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · open] a task whose safety property depends on another subsystem's data invariant (here:
  catalog sync actually populating `modality`) should explicitly declare that dependency at GROUND
  time and gate on it, rather than discovering the gap only at refute-read (evidence: this task's
  guard was contract-correct but would have caused a full outage in this stale worktree until
  origin/main's prerequisite fix was merged in).
