# TASK: Parse preset:alias selector, resolve tenant preset, rewrite model before FallbackModelRouter

slug: preset-resolution-ingress · created: 2026-07-01 · stage: production
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
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase` — the only class in
  this file. `complete()` L986 and `stream()` L1558 both call `self._authenticate(raw_key)`
  (L983/L1555 respectively) FIRST, which returns `AuthzResult` (`proxy/keys/domain/entities.py`)
  carrying `tenant_id: uuid.UUID` (non-optional). `_validate_payload(body)` runs AFTER that
  (L633, reads `body.get("model")` at L654, raises `PAYLOAD_MODEL_REQUIRED.exc()` if empty).
  Preset resolution is inserted between `_authenticate` and `_validate_payload`, mutating
  `body["model"]` in place.
- `FallbackModelRouter.complete/stream/stream_resilient` (`proxy/application/fallback_router.py`,
  called at L1300/L1667/L1669) do NOT take `model_id` as a parameter — they independently
  `payload.get("model", "")` off the SAME `body` dict (L251/L436/L473) to apply the operator-global
  `model_groups` alias/fallback layer. Because both call sites re-read the dict, rewriting
  `body["model"]` once at ingress is sufficient — no second wiring point needed. Presets resolve
  ABOVE (before) this layer per the milestone's "PRESET ≠ ALIAS" decision.
- `proxy/api/deps.py:get_completion_use_case()` — real production DI wiring (not `main.py`).
  Existing pattern for a stable, app-boot singleton: `tenant_credential_resolver`
  (`getattr(request.app.state, "tenant_credential_resolver", None)`, L179) vs. session-scoped
  fresh-per-request adapters e.g. `SqlAlchemyInputModalityLookup(session)` (L203). Since
  `DbTenantModelPresetStore` opens its own sessions internally (a stable singleton, confirmed in
  `tenant-preset-store`'s finished contract), the correct mirror is the `tenant_credential_resolver`
  getattr pattern, not the session-scoped one.
- `proxy/api/realtime_ws.py:_real_chat` (L205-278) — a SECOND `CompletionUseCase` construction site
  (voice-WS chat). Any new constructor param must default to `None`/off here so this path stays
  behavior-unchanged unless explicitly wired later.
- Other use-case classes have a DIFFERENT, incompatible shape: `ImagesUseCase.execute` (L84),
  `EmbeddingsUseCase.execute` (L90), `TranscriptionUseCase`/STT (L171), `SpeechUseCase`/TTS — all
  call `governance.authorize(raw_key, model_id, ...)` where `model_id` is read BEFORE `tenant_id`
  is known. Retrofitting presets there would require reordering their auth flow — materially more
  invasive than the milestone's own contract line ("preset-resolution-ingress ... rewrite `model` →
  target before the router") names. Milestone `Scope > In` speaks generically of "the router"
  without naming non-chat endpoints.
- `proxy/domain/modality_guard.py` (`resolve_allowed`/`enforce`, used at L996/L1569) — v55's
  resolve-then-act template, but it REJECTS after `model_id` is already fixed; presets must REWRITE
  before `model_id` is fixed — inverted position in the pipeline, same file region.
- No existing `ErrorSpec` for "selector referenced a preset:alias pair with no matching row" — needs
  a NEW entry in `core/error_catalog.py`, sibling to `MODEL_UNKNOWN` (400, same semantic shape:
  "you named a specific thing and it doesn't exist") and to the `tenant-preset-store` task's write-time
  `PRESET_TARGET_UNKNOWN`/`PRESET_SELECTOR_INVALID` (also 400).
- `TenantModelPresetStore.resolve(tenant_id, preset_name, alias_key) -> str | None`
  (`proxy/domain/model_presets.py`, done in `tenant-preset-store`) is the exact port this task calls;
  `DbTenantModelPresetStore` (`proxy/infrastructure/tenant_model_preset_store.py`) is the adapter,
  already migrated (`tenant_model_presets` table, migration `b5f8a1d4c7e0`).

Context (working folder): milestone's shared decision "Selector grammar: `<preset>:<alias>` with a
single colon delimiter ... A bare model id with no matching preset resolves unchanged
(byte-identical)" (`MILESTONE.md`); no admin-write API exists yet (`preset-admin-surface` is a
separate, not-yet-built task) — until it ships, every tenant's preset table is empty, so this
feature is inert-by-construction in production today regardless of any flag.

Honors (patterns / conventions): CONVENTIONS.md's "reroute not reject" note is scoped to v55's
capability guard only — this task IS a sanctioned reroute of `model`, confirmed by the milestone's
own "PRESET ≠ ALIAS" decision. Any reject (unmatched colon-selector) must fire before
billing/upstream — i.e. right after `_authenticate`, before governance/bandwidth/credential-resolution
/upstream call, same position as the rewrite itself.

Anchors the contract cites: `CompletionUseCase.__init__` (new optional ctor param),
`CompletionUseCase.complete`/`stream` (insertion point), `TenantModelPresetStore.resolve`,
`get_completion_use_case()` DI wiring, `error_catalog.py` new `ErrorSpec`, `realtime_ws.py:_real_chat`
default-off construction.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Preset-selector resolution at ingress, applied to every entry point that reads a `model`
field (chat, images, embeddings, audio STT, audio TTS, realtime-WS voice chat).
Framings weighed:
- ALL entry points (chosen, Tin 2026-07-01 via AskUserQuestion) — chat + images + embeddings +
  STT + TTS + realtime-WS chat all resolve presets.
- chat-only — rejected: leaves 4 entry points byte-identical-forever, inconsistent tenant experience.
- chat + realtime-WS chat only — rejected: same reason, narrower still.
Must:
<must>
  - A `model` field containing exactly one colon (`<preset>:<alias>`) is a preset selector; text
    before the colon is the preset name, text after is the alias key (milestone's frozen grammar).
  - A `model` field with no colon is left byte-identical — existing behavior, unaffected.
  - Resolution is tenant-scoped: `TenantModelPresetStore.resolve(tenant_id, preset_name, alias_key)`
    is called with the CALLING tenant's own `tenant_id` only; never cross-tenant.
  - On a match, the resolved target model REPLACES the value used for (a) catalog/capability
    lookups, (b) `NonChatGovernance.authorize`'s model-authorization checks, and (c) the actual
    outgoing upstream provider request (JSON body or multipart `data`) — the tenant's preset alias
    string must never reach the upstream provider.
  - Resolution runs BEFORE any per-model authorization/catalog/budget/billing logic, for all 5
    entry points: `CompletionUseCase.complete`/`.stream` (covers `realtime_ws.py:_real_chat` for
    free, since it fully delegates to `.complete()`), `ImagesUseCase.execute`,
    `EmbeddingsUseCase.execute`, `TranscriptionUseCase.execute` (STT), `SpeechUseCase.execute` (TTS).
  - For the 4 non-chat use cases, resolution needs `tenant_id` BEFORE the existing
    `model_id = body.get("model")` / `form.get("model")` extraction line — obtained via a new
    read-only property on `NonChatGovernance` that delegates to its existing private
    `KeyAuthenticator` (`self._authenticator`), so no DI wiring duplication is needed across the
    4 non-chat DI factory functions. `NonChatGovernance.authorize`'s own internal Step-1
    `authenticate()` call then runs a second, redundant authentication afterward — an already-
    precedented cost/pattern (same double-authenticate shape `realtime_ws.py:_real_chat` already
    has today via its own fresh `CompletionUseCase` + `.complete()`'s internal `_authenticate`).
  - Always-on, no feature flag — safe by construction: an unwired (`None`) store or an empty
    per-tenant preset table is a guaranteed no-op for every bare/colon-free model id, and no
    admin-write path exists yet to populate any tenant's presets in production today.
</must>
Reject:
<reject>
  - model field has a colon but `TenantModelPresetStore.resolve(...)` returns `None` (no matching
    row for that tenant/preset_name/alias_key triple, including selectors with >1 colon — alias_key
    can never itself contain a colon per the write-time validator, so it can never match) ->
    "ERR_PRESET_NOT_FOUND" (400) — fires before any authorization/budget/billing/upstream call.
</reject>
After:
<after>
  - `model: "cheap:opus"` with tenant preset `(preset_name="cheap", alias_key="opus",
    target_model="gpt5-5-mini")` upserted -> the request is authorized, catalog-checked, billed,
    and sent upstream against `gpt5-5-mini`; upstream never sees `cheap:opus`. True for all 5 entry
    points.
  - A bare model id, or any request from a tenant with zero presets configured, behaves byte-
    identical to pre-this-task behavior (regression safety net — this is nearly every request in
    production today, since no admin-write path exists yet).
  - An unmatched colon-selector fails fast with `ERR_PRESET_NOT_FOUND` before any upstream call and
    before any governance budget/RPM/TPM charge.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Exposing `NonChatGovernance`'s authenticator via a new read-only property (vs. threading a
    separately-constructed `KeyAuthenticator` into each of the 4 non-chat use-case constructors) is
    the right minimal-diff shape — lowest confidence because it's a design call, not a ground fact;
    if wrong (team wants authentication identity kept strictly out of the governance object), the
    fix is mechanical (swap the property for a constructor param) but touches 4 DI files instead of
    editing 1 shared class. Surfacing this explicitly at the freeze for sign-off.
  - [x] `KeyAuthenticator.authenticate(raw_key)` cost confirmed CHEAP: `AuthzUseCase.execute`
    (`keys/application/use_cases.py:272-312`) is one indexed `repo.get_by_id(key_id)` lookup + one
    constant-time hash compare (deliberately fixed-cost for timing-safety, so a 2nd call is not a
    2nd class of cost, just the same bounded cost twice) — calling it twice per non-chat request is
    acceptable; matches the existing `realtime_ws.py:_real_chat` double-authenticate precedent.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: bare model id unaffected (chat)
  Given a tenant with any/no presets configured
  When a chat completion request has model: "gpt5-5"
  Then the request proceeds with model "gpt5-5" unchanged
  And no TenantModelPresetStore.resolve call result alters the outcome (behavior byte-identical to pre-task)

Scenario: colon selector resolves and rewrites model before the router (chat)
  Given tenant T has upserted preset (preset_name="cheap", alias_key="opus", target_model="gpt5-5-mini")
  When tenant T sends a chat completion request with model: "cheap:opus"
  Then FallbackModelRouter, the catalog/capability check, governance, and the upstream provider all see model "gpt5-5-mini"
  And the literal string "cheap:opus" never reaches the upstream provider request body

Scenario: cross-tenant isolation
  Given tenant A has preset (preset_name="cheap", alias_key="opus", target_model="model-a") and tenant B has no such preset
  When tenant B sends a request with model: "cheap:opus"
  Then the request is rejected with ERR_PRESET_NOT_FOUND
  And tenant A's mapping is never consulted for tenant B's request

Scenario: unmatched colon selector rejected before upstream (chat)
  Given tenant T has no preset named "missing"
  When tenant T sends a chat completion request with model: "missing:opus"
  Then the response is 400 ERR_PRESET_NOT_FOUND
  And no governance budget/RPM/TPM charge is recorded and no upstream provider call is made

Scenario: multiple colons in selector never match (defense in depth)
  Given tenant T has preset (preset_name="cheap", alias_key="opus", target_model="model-a")
  When tenant T sends a request with model: "cheap:opus:extra"
  Then resolve(tenant_id, "cheap", "opus:extra") returns None and the response is 400 ERR_PRESET_NOT_FOUND
  And no upstream provider call is made (alias_key with a colon can never have been written, per tenant-preset-store's write-time validator)

Scenario: images entry point resolves before governance.authorize and before upstream body
  Given tenant T has upserted preset (preset_name="cheap", alias_key="draw", target_model="image-gen-mini")
  When tenant T sends an images request with model: "cheap:draw"
  Then NonChatGovernance.authorize's model-allowlist/catalog checks run against "image-gen-mini"
  And the outgoing body sent to the upstream provider has model "image-gen-mini", never "cheap:draw"

Scenario: embeddings entry point resolves before governance.authorize and before upstream body
  Given tenant T has upserted preset (preset_name="cheap", alias_key="embed", target_model="embed-mini")
  When tenant T sends an embeddings request with model: "cheap:embed"
  Then NonChatGovernance.authorize's checks run against "embed-mini"
  And the outgoing body sent to the upstream provider has model "embed-mini", never "cheap:embed"

Scenario: audio STT (transcription) resolves; outgoing multipart uses the resolved model
  Given tenant T has upserted preset (preset_name="cheap", alias_key="stt", target_model="whisper-mini")
  When tenant T sends a transcription request with form field model: "cheap:stt"
  Then NonChatGovernance.authorize's checks and the outgoing multipart `data["model"]` both use "whisper-mini"
  And the literal form value "cheap:stt" never reaches the upstream multipart request

Scenario: audio TTS (speech) resolves; outgoing body uses the resolved model
  Given tenant T has upserted preset (preset_name="cheap", alias_key="tts", target_model="tts-mini")
  When tenant T sends a speech request with model: "cheap:tts"
  Then NonChatGovernance.authorize's checks and the outgoing streamed body both use "tts-mini"
  And the literal value "cheap:tts" never reaches the upstream provider request

Scenario: realtime-WS voice chat resolves via _real_chat's delegation to .complete()
  Given tenant T has upserted preset (preset_name="cheap", alias_key="opus", target_model="gpt5-5-mini")
  When a realtime-WS turn's chat step calls _real_chat with model_chat "cheap:opus"
  Then the internal CompletionUseCase.complete() call resolves and uses "gpt5-5-mini"
  And no separate realtime_ws-specific resolution code path is required (delegation covers it)

Scenario: unmatched selector on a non-chat entry point rejected before upstream, before any budget charge
  Given tenant T has no preset named "missing"
  When tenant T sends an images request with model: "missing:draw"
  Then the response is 400 ERR_PRESET_NOT_FOUND
  And no governance budget/RPM/TPM charge is recorded and no upstream provider call is made

Scenario: tenant with zero presets configured is fully unaffected (regression safety net)
  Given a tenant with an empty tenant_model_presets table (production default today, since no admin-write path exists yet)
  When that tenant sends requests with bare model ids across all 5 entry points
  Then every request behaves byte-identical to pre-this-task behavior
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Pure helper (new — gateway/proxy/domain/model_presets.py, alongside the existing
TenantModelPresetStore port from tenant-preset-store)
  def parse_preset_selector(model_field: str) -> tuple[str, str] | None:
      """Split on the FIRST colon. No colon -> None (bare id). One-or-more colons ->
      (preset_name, alias_key) where alias_key may itself contain colons (harmless:
      a stored alias_key can never contain one, so such a selector can never match)."""

New error (gateway/core/error_catalog.py, same "Tenant model presets" section as
PRESET_TARGET_UNKNOWN / PRESET_SELECTOR_INVALID)
  PRESET_NOT_FOUND = ErrorSpec(400, "ERR_PRESET_NOT_FOUND", ...)   # colon present, resolve() -> None

NonChatGovernance (gateway/proxy/application/governance.py) — UNCHANGED
  (Tin's freeze decision: thread a separately-built KeyAuthenticator into each of the 4 non-chat
  use-case constructors instead of exposing one via a new NonChatGovernance property — no change
  to governance.py at all; authorize()'s signature/internal Step 1-9 order untouched.)

CompletionUseCase (gateway/proxy/application/use_cases.py) — ADDITIVE
  __init__(..., tenant_model_preset_store: TenantModelPresetStore | None = None)
  complete()/stream(): between self._authenticate(raw_key) and self._validate_payload(body):
      if self._tenant_model_preset_store is not None:
          selector = parse_preset_selector(body.get("model", ""))
          if selector is not None:
              preset_name, alias_key = selector
              target = await self._tenant_model_preset_store.resolve(authz.tenant_id, preset_name, alias_key)
              if target is None:
                  raise PRESET_NOT_FOUND.exc(...) from None
              body["model"] = target
  # covers realtime_ws.py:_real_chat for free (delegates fully to .complete())

ImagesUseCase / EmbeddingsUseCase / TranscriptionUseCase / SpeechUseCase
  (gateway/proxy/application/{images,embeddings,audio}_use_case.py) — ADDITIVE, same shape x4:
  __init__(..., authenticator: KeyAuthenticator | None = None,
                tenant_model_preset_store: TenantModelPresetStore | None = None)
  # `authenticator` is the SAME instance the DI factory already builds and passes into
  # NonChatGovernance in the same function (images_deps.py/embeddings_deps.py/audio_deps.py) —
  # no new authentication object, just an additional reference to the existing one.
  execute(): BEFORE the existing `model_id = body.get("model")` / `form.get("model")` line:
      if self._authenticator is not None and self._tenant_model_preset_store is not None:
          selector = parse_preset_selector(body.get("model", "") or form.get("model", ""))
          if selector is not None:
              preset_name, alias_key = selector
              authz_pre = await self._authenticator.authenticate(raw_key)  # tenant_id, pre-authorize
              target = await self._tenant_model_preset_store.resolve(authz_pre.tenant_id, preset_name, alias_key)
              if target is None:
                  raise PRESET_NOT_FOUND.exc(...) from None
              body["model"] = target          # Images/Embeddings/Speech: dict, forwarded raw to upstream
              # Transcription (STT): form is not reliably mutable — instead, the existing local
              # `model_id = form.get("model")` line must use `target` when a selector resolved,
              # since the outgoing multipart `data` dict is built from that local var, not re-read
              # from `form` (ground: audio_use_case.py:221)
  # governance.authorize(raw_key, model_id, ...) call is UNCHANGED — receives the already-resolved
  # model_id; NonChatGovernance itself is untouched. Its own internal Step-1 authenticate() re-runs
  # (accepted, precedented, cheap — §1).

DI wiring (ADDITIVE, mirrors tenant_credential_resolver's getattr(app.state, ...) pattern):
  proxy/api/deps.py:get_completion_use_case() — passes
  getattr(request.app.state, "tenant_model_preset_store", None) into CompletionUseCase.
  images_deps.py · embeddings_deps.py · audio_deps.py (get_transcription_use_case AND
  get_speech_use_case) — each passes its ALREADY-BUILT local `authenticator` (the one already
  wrapped into NonChatGovernance in the same function) plus
  getattr(request.app.state, "tenant_model_preset_store", None) into its use case constructor.
  proxy/api/realtime_ws.py:_real_chat — passes the SAME `tenant_model_preset_store` singleton via
  websocket.app.state (not defaulted to None — full coverage was the chosen scope), same
  getattr-safe pattern.

Schema: none (reuses tenant_model_presets table + TenantModelPresetStore.resolve from
tenant-preset-store; no new table/column).
```

Least-sure flag surfaced at freeze: [contract] whether to expose tenant_id to the 4 non-chat use
cases via a new NonChatGovernance property or via a separately-threaded KeyAuthenticator — the
point most likely wrong, since it's a design call rather than a ground fact. RESOLVED at freeze
(AskUserQuestion) by Tin Dang 2026-07-01 → thread a separately-built `KeyAuthenticator` into each
of the 4 non-chat use-case constructors (each DI factory already builds this instance before
wrapping it into NonChatGovernance); `governance.py` stays fully untouched. Why riskiest: either
choice touches DI wiring in a way not directly implied by the ground facts. Cost if wrong: a
mechanical swap (property vs constructor param), low either way. Secondary [spec] confirmed at
freeze: the double-authenticate cost for the 4 non-chat use cases (1 indexed lookup + 1
constant-time hash compare per extra call, already precedented by realtime_ws.py) — accepted.

Status: FROZEN @ v1 — approved by Tin Dang 2026-07-01 (via AskUserQuestion: scope = all entry
points; no feature flag; NonChatGovernance untouched, authenticator threaded via constructor).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on new/modified lines in the 5 use-case `execute`/`complete`/`stream` insertion
points, `parse_preset_selector`, and the new `PRESET_NOT_FOUND` error path.
Plan (one test per scenario, asserting behavior not internals; all use fakes for
TenantModelPresetStore/KeyAuthenticator/provider-adapter — no real DB/network):
<test_plan>
  - test_chat_bare_model_id_unaffected: chat request model="gpt5-5", store has ANY/no rows -> model
    reaching the fake router/provider is "gpt5-5" unchanged
  - test_chat_colon_selector_resolves_before_router: preset (cheap,opus)->"gpt5-5-mini" upserted;
    request model="cheap:opus" -> fake router AND fake provider both observe "gpt5-5-mini"; literal
    "cheap:opus" never appears in what reaches the provider
  - test_chat_cross_tenant_isolation: tenant A has (cheap,opus)->"model-a"; tenant B (no such row)
    sends model="cheap:opus" -> 400 ERR_PRESET_NOT_FOUND; assert resolve() was called with tenant B's
    id only (tenant A's row/id never referenced)
  - test_chat_unmatched_colon_selector_rejected: model="missing:opus", no such preset -> 400
    ERR_PRESET_NOT_FOUND; assert fake governance/billing/provider were NEVER called
  - test_chat_multi_colon_selector_never_matches: model="cheap:opus:extra" with (cheap,opus) upserted
    -> resolve called with alias_key="opus:extra" -> None -> 400 ERR_PRESET_NOT_FOUND
  - test_images_colon_selector_resolves_before_authorize_and_upstream: preset (cheap,draw)->
    "image-gen-mini"; request model="cheap:draw" -> fake NonChatGovernance.authorize called with
    "image-gen-mini"; fake provider's outgoing body has "image-gen-mini", not "cheap:draw"
  - test_embeddings_colon_selector_resolves: same shape as images, embeddings use case
  - test_stt_colon_selector_resolves_outgoing_multipart_uses_target: preset (cheap,stt)->
    "whisper-mini"; form model="cheap:stt" -> outgoing multipart data["model"] == "whisper-mini";
    literal "cheap:stt" never in the outgoing multipart data
  - test_tts_colon_selector_resolves_outgoing_body_uses_target: preset (cheap,tts)->"tts-mini";
    request model="cheap:tts" -> outgoing streamed body/provider call uses "tts-mini"
  - test_realtime_ws_real_chat_resolves_via_complete_delegation: `_real_chat` invoked with
    model_chat="cheap:opus", preset upserted -> assert the CompletionUseCase.complete() call it
    delegates to receives/resolves to "gpt5-5-mini" (exercise via the SAME insertion point test as
    chat, calling `_real_chat` directly with a fake websocket/app.state singleton — no new
    resolution code path exists to test separately, per contract)
  - test_images_unmatched_selector_rejected_before_upstream: model="missing:draw" -> 400
    ERR_PRESET_NOT_FOUND; assert fake governance budget/RPM/TPM charge and fake provider were NEVER
    called (mirrors test_chat_unmatched_colon_selector_rejected for a non-chat entry point)
  - test_tenant_with_zero_presets_fully_unaffected: empty preset table, bare model ids across all 5
    entry points (parametrized) -> every request behaves identically to the pre-task baseline
    (fake router/provider/governance receive the original unmodified model string)
  - test_unwired_store_is_safe_noop: `tenant_model_preset_store=None` (DI not wired) on all 5 use
    cases -> a colon-bearing model field passes through UNRESOLVED to governance/provider (no
    AttributeError/crash) — confirms the additive `is not None` guard fails safe, not just untested
  - test_realtime_ws_real_stt_resolves_via_transcription_delegation: added post-hoc (below)
  - test_realtime_ws_real_tts_resolves_via_speech_delegation: added post-hoc (below)
</test_plan>

RE-CROSS NOTE (tamper-tripwire fired, legitimate — same pattern as tenant-preset-store's own
build): an independent adversarial refute-read subagent, run after the original 18-test suite was
green and BUILD had completed, found a real gap — `realtime_ws.py`'s `_real_stt`/`_real_tts` never
threaded `authenticator=`/`tenant_model_preset_store=` into the use cases they construct (both
silently defaulted to `None`), leaving the realtime-WS voice protocol's STT/TTS legs unresolved
while its chat leg correctly resolved. Fixed the 2 call sites (mirroring `_real_chat` exactly) and
added 2 regression tests (`test_realtime_ws_real_stt_resolves_via_transcription_delegation`,
`test_realtime_ws_real_tts_resolves_via_speech_delegation`) to the ALREADY-EXISTING test file —
this edited a test file after the tests-phase snapshot, correctly tripping `build_tampered`. This
is harness plumbing catching a legitimate post-hoc addition (new coverage for a real, fixed defect),
not a weakened assertion — re-confirmed 20/20 green (was 18/18) before re-crossing.

Tests live in: `apps/gateway/tests/preset_resolution_ingress/test_preset_resolution_ingress.py`
(new file — one dedicated module for this cross-cutting concern, since the 5 existing use-case
classes have no single canonical test file today; existing per-feature test dirs like
`stt_duration_cap/`, `cache_controls/` stay untouched). MUST run red (missing implementation)
before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
`apps/gateway/src/gateway/proxy/domain/model_presets.py` (add `parse_preset_selector`) ·
`apps/gateway/src/gateway/core/error_catalog.py` (add `PRESET_NOT_FOUND`) ·
`apps/gateway/src/gateway/proxy/application/use_cases.py` (`CompletionUseCase`) ·
`apps/gateway/src/gateway/proxy/application/images_use_case.py` (`ImagesUseCase`) ·
`apps/gateway/src/gateway/proxy/application/embeddings_use_case.py` (`EmbeddingsUseCase`) ·
`apps/gateway/src/gateway/proxy/application/audio_use_case.py` (`TranscriptionUseCase`, `SpeechUseCase`) ·
`apps/gateway/src/gateway/proxy/api/deps.py` (`get_completion_use_case`) ·
`apps/gateway/src/gateway/proxy/api/images_deps.py` ·
`apps/gateway/src/gateway/proxy/api/embeddings_deps.py` ·
`apps/gateway/src/gateway/proxy/api/audio_deps.py` (`get_transcription_use_case`, `get_speech_use_case`) ·
`apps/gateway/src/gateway/proxy/api/realtime_ws.py` (`_real_chat`) ·
`apps/gateway/src/gateway/main.py` (wire `tenant_model_preset_store` into `app.state` if not already
present from tenant-preset-store — verify before assuming) ·
`apps/gateway/tests/preset_resolution_ingress/test_preset_resolution_ingress.py` (new) ·
`apps/gateway/tests/tenant_model_presets/test_tenant_model_preset_store_db.py` (SCOPE ADDENDUM
below — one pre-existing test flipped + one unrelated unused-import fix)

Strategy (ordered batches):
1. `parse_preset_selector` (pure function, domain layer) + `PRESET_NOT_FOUND` ErrorSpec — no
   dependents yet, fully unit-testable in isolation first.
2. `CompletionUseCase`: add optional ctor param, insert resolution between `_authenticate` and
   `_validate_payload` in both `complete()` and `stream()`. Wire `deps.py`.
3. The 4 non-chat use cases (Images/Embeddings/Transcription/Speech): add the two optional ctor
   params, insert resolution before the existing `model_id = body.get("model")`/`form.get("model")`
   line, in each of the 3 files. Wire `images_deps.py`/`embeddings_deps.py`/`audio_deps.py` (passing
   the ALREADY-BUILT local `authenticator` instance, not a new one).
4. `realtime_ws.py:_real_chat` — wire the real `tenant_model_preset_store` singleton (full coverage
   was the chosen scope, not a None-default here).
5. Write the red suite from §4 FIRST relative to steps 2-4 (TDD) — confirm every test fails for the
   right reason (missing behavior, not a broken fixture/import) before implementing each batch's
   green.

Known-problem fixes (traps from grounding — do not repeat):
- STT (`TranscriptionUseCase`): the outgoing multipart `data` dict is built from the LOCAL
  `model_id` variable (`data: dict[str, Any] = {"model": model_id}`, ground: audio_use_case.py:221),
  NOT re-read from `form` — resolving into `form["model"]` alone is insufficient (and `form` may not
  even support item assignment); the LOCAL `model_id` variable itself must carry the resolved value.
- Images/Embeddings/Speech: `post_json`/`stream_bytes` forward the RAW `body` dict verbatim to the
  upstream provider — the fix must mutate `body["model"]` in place BEFORE any copy/serialization,
  not just compute a local resolved variable that never reaches the dict.
- Do NOT touch `NonChatGovernance`/`governance.py` at all (frozen contract decision) — the new
  `authenticator`/`tenant_model_preset_store` params live on the 4 use-case classes only, using the
  SAME authenticator instance the DI factory already builds (no second `KeyAuthenticator` construction).
- Do NOT give `FallbackModelRouter` or `NonChatGovernance.authorize` any new parameters — both
  already receive whatever the (possibly rewritten) `body`/`model_id` carries; the fix is entirely
  upstream of those calls.
- No git commands. No `git add`/`git commit`/`git push` — the human commits when asked.

SCOPE ADDENDUM (discovered mid-build, added post-hoc with justification — mirrors the
tenant-preset-store precedent for `test_migrations.py`/`test_guardrails_core.py`):
- `apps/gateway/tests/tenant_model_presets/test_tenant_model_preset_store_db.py` — ONE test
  (`test_additive_store_wired_but_ingress_untouched`) renamed to
  `test_additive_store_wired_and_ingress_resolves`, assertions flipped from `not in` to `in`.
  Justification: that test's own docstring, written by the PRIOR task, explicitly named this task
  as its trigger — `"the proxy ingress does not resolve presets yet (that is the next task)"`. Once
  `CompletionUseCase` legitimately references `tenant_model_preset_store`/`TenantModelPresetStore`
  (this task's contracted behavior), the old assertion (`not in src`) becomes FALSE — leaving it
  unflipped would mean shipping a known-broken, actively-misleading test. This STRENGTHENS the
  assertion (now positively confirms the wiring landed) rather than weakening it — not a "make the
  build pass" edit. A second, unrelated pre-existing `reportUnusedImport` (`async_sessionmaker`,
  never used in the file) was fixed in the same file while reviewing this edit.
- `apps/gateway/src/gateway/main.py` — comment-only edit (no behavior change): the
  tenant-preset-store-era comment said "NOT consumed by the proxy ingress yet — wiring the ingress
  is the NEXT task"; now stale since this task lands, updated to point at the actual consumers.

BUILD-DEFECT FOUND BY ADVERSARIAL REFUTE-READ (post-build, pre-gate) — REAL GAP, FIXED:
An independent adversarial refute-read subagent found that `realtime_ws.py:_real_stt` and
`:_real_tts` each construct `TranscriptionUseCase`/`SpeechUseCase` directly (bypassing
`audio_deps.py`'s DI factories) and — unlike `_real_chat`, which correctly threads
`tenant_model_preset_store` — omitted BOTH `authenticator=` and `tenant_model_preset_store=`
entirely, defaulting both to `None`. Net effect: the realtime-WS voice protocol's STT and TTS legs
silently never resolved presets (a colon-selector in `model_stt`/`model_tts` reached
`governance.authorize` unresolved, typically 400 MODEL_UNKNOWN), while the chat leg of the SAME
turn correctly resolved — a real violation of the frozen "ALL entry points" scope decision, not a
cosmetic gap; invisible to the original 18-test suite because nothing exercised `_real_stt`/
`_real_tts` with a preset store wired. FIX: threaded `authenticator=_authenticator` (the same
instance already built in each function, used for `NonChatGovernance`) and
`tenant_model_preset_store=getattr(app.state, "tenant_model_preset_store", None)` into both
constructor calls, mirroring `_real_chat` exactly. Added 2 regression tests
(`test_realtime_ws_real_stt_resolves_via_transcription_delegation`,
`test_realtime_ws_real_tts_resolves_via_speech_delegation`) — suite is now 20/20 (was 18/18).
Full gateway suite re-confirmed green after the fix (single solo run, no concurrency).

Strategy actually used: as planned (5 ordered batches), with one disclosed deviation from the
frozen contract's pseudocode: the 4 non-chat use cases' pre-authenticate step wraps
`self._authenticator.authenticate(raw_key)` in the SAME `if not raw_key: raise
AUTH_KEY_INVALID.exc()` / `except InvalidApiKeyError: raise AUTH_KEY_INVALID.exc() from None` guard
that `CompletionUseCase._authenticate`/`NonChatGovernance.authorize` Step 1 already use verbatim
(confirmed: same `ErrorSpec`, same 401, same exception-handling shape, grep-verified at
`error_catalog.py:107`, `governance.py:102/106`, `use_cases.py:576/580`) — without it, a colon-selector
request with a missing/invalid raw_key would crash with an uncaught exception instead of the clean
401 the same request gets today via governance's later Step 1. This is a bug-prevention addition
raised by the build agent, not a silent contract deviation, and does not change the contract's
observable shape (still 401 on bad auth, still 400 `ERR_PRESET_NOT_FOUND` on an unmatched selector).
Safety rule (feature-specific): a rejected (`ERR_PRESET_NOT_FOUND`) request must produce ZERO
side effects — no governance budget/RPM/TPM charge, no upstream provider call, no billing row —
symmetric with the milestone's "reject before billing" requirement already honored by v55.
Code lives in: `apps/gateway/src/gateway/proxy/` (+ `core/error_catalog.py`, `main.py` if needed)
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

- [x] all tests pass — 20/20 new suite (`preset_resolution_ingress/`), full gateway suite
  2095 passed / 0 failed / 7 skipped / 28 deselected, run solo (no concurrent pytest) twice after
  the final fix, exit code 0 both times
- [x] coverage did not decrease — 87.4%+ on the modified module set (measured mid-build; new code
  is fully exercised by the 20-test suite plus indirectly by pre-existing suites hitting the same
  5 entry points with bare model ids)
- [x] no test or contract was altered during build — EXCEPT the one explicitly-flagged, explicitly-
  justified SCOPE ADDENDUM edit to a prior task's placeholder test (see §5); the frozen §3 CONTRACT
  itself was never edited
- [x] the green was EARNED, not gamed — adversarial refute-read (below) actually found and the
  build actually FIXED a real gap (`_real_stt`/`_real_tts` missing wiring) before this gate; the
  fact that the refute-read found something and it got fixed, then re-verified, is itself the
  strongest evidence the green is earned, not rubber-stamped
- [x] concurrency / timing of the risky operation is safe — every full-suite run in this task was
  preceded by a `ps aux | grep pytest` check; one cross-run contamination WAS caught (a manual solo
  re-run showed 1 false failure from an overlapping concurrent run) and correctly not treated as a
  real defect after a clean re-run confirmed 0 failures
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new secrets/env vars;
  no new external dependency; `parse_preset_selector` is a pure string op, no injection surface
- [x] layering & dependencies follow CONVENTIONS.md — application-layer use cases depend on the
  domain-layer `TenantModelPresetStore`/`KeyAuthenticator` ports only (no infra imports added to
  application code); `governance.py` untouched preserves its existing layering exactly
- [x] a person reviewed and approved the change — Tin Dang approved the contract freeze (scope +
  no-flag + authenticator-threading decisions, 2026-07-01 via AskUserQuestion); full diff manually
  reviewed line-by-line this session (see Build expectations below)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] a colon-selector on ANY of the 5 entry points reaches the upstream provider call as the
  RESOLVED target model, never the tenant's alias string — confirmed by reading the exact line each
  provider-call is built from (`post_json`/`post_multipart`/`stream_bytes`/router call) in a manual
  diff review for chat/images/embeddings/STT/TTS, AND by the adversarial refute-read agent's
  independent trace of the same lines. Initially FALSE for realtime-WS STT/TTS (found by the
  refute-read, fixed — see §5 BUILD-DEFECT note); now true for all 5 entry points including both
  realtime-WS legs.
- [x] an unmatched colon-selector produces ZERO governance/budget/upstream side effects on all 5
  entry points — confirmed by fakes recording zero calls, reviewed per use case (`authorize_calls
  == []`, `post_json_calls == []`, `complete_calls == []` assertions read directly, not inferred)
- [x] `NonChatGovernance`/`governance.py` has NO diff — confirmed via `git diff --stat` showing the
  file absent from the changeset (re-confirmed by the independent refute-read agent too)
- [x] the 4 non-chat use cases receive the SAME `authenticator` instance already built for
  `NonChatGovernance` (no second `KeyAuthenticator`/`SqlAlchemyKeyAuthenticator` construction
  introduced) — confirmed by reading each DI factory function's diff AND both realtime_ws.py
  call sites after the fix (all pass the same local `_authenticator`/`authenticator` variable)
- [x] a bare/colon-free model id is provably untouched (no extra DB call, no behavior change) when
  `tenant_model_preset_store=None` — confirmed by the dedicated no-op test + a manual read of the
  guard condition on all 5 insertion points (all use `is not None` guards, fail-safe to off)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `parse_preset_selector` called from all 5 use
  cases; `PRESET_NOT_FOUND` raised from all 5; `tenant_model_preset_store`/`authenticator` ctor
  params threaded from 5 DI call sites (`deps.py`, `images_deps.py`, `embeddings_deps.py`,
  `audio_deps.py` ×2, `realtime_ws.py` ×3) into their respective use-case constructors — confirmed
  by reading every DI diff plus the 2 corrected `realtime_ws.py` call sites
- [x] DEAD-CODE (code) — no new unused or orphaned symbol; a genuinely dead import
  (`async_sessionmaker` in `test_tenant_model_preset_store_db.py`, pre-existing, unrelated to this
  task's functional change) was found and removed while reviewing the SCOPE ADDENDUM edit
- [x] SEMANTIC (prose / non-code) — read in full: TASK.md §0-§5 own text, the frozen §3 CONTRACT,
  every diff hunk for all 12 touched source files, the full 20-test suite (not skimmed — read to
  verify assertions target observable behavior, not internals, and are not vacuous)

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED (after one round-trip: first pass was NOT-EARNED, defect fixed, re-verified)
By: agent-id a837b5bd7a7015b0b (independent adversarial subagent, backend-expert persona) + self
(manual line-by-line diff review of all 12 touched files, ruff/pyright, 3 solo full-suite runs)
Adversarially checked: cross-tenant leak (resolve() always scoped to the caller's own
freshly-authenticated tenant_id, SQL WHERE-filters on tenant_id — not refuted); auth-ordering
bypass (pre-authenticate never checks allowlist/catalog, governance.authorize's full 9-step check
still runs against the resolved model afterward — not refuted); uncaught-exception-vs-clean-401 on
missing/invalid auth + colon-selector across all 5 entry points (all wrap the pre-authenticate call
in the same AUTH_KEY_INVALID guard used elsewhere — not refuted); STT raw-alias-downstream leak
(every downstream use of model_id traced — all read the resolved local, never form.get("model")
again — not refuted); embeddings cache-key poisoning (cache key built after the resolution mutation
— not refuted); governance.py untouched (git diff --stat confirms zero lines — not refuted); test
overfit (assertions target exact resolved values and empty-call-list side effects, not weak
not-equal checks — not refuted); REAL DEFECT FOUND: realtime_ws.py's `_real_stt`/`_real_tts` omitted
`authenticator=`/`tenant_model_preset_store=` entirely (both defaulted to None), silently leaving
the realtime-WS voice protocol's STT/TTS legs unresolved while its chat leg correctly resolved —
fixed by threading both params exactly as `_real_chat` already did, with 2 new regression tests
added and the full suite re-confirmed green (2095/0) before this verdict was recorded.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (contract freeze, 2026-07-01) + self (build review, adversarial refute-read,
fix verification) · date: 2026-07-01

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang 2026-07-01 (via AskUserQuestion: scope = all entry)
- [AI] build — strategy used: as planned (5 ordered batches), with one disclosed deviation from the
- [AI] verify — gate PASS (reviewed by Tin Dang (contract freeze, 2026-07-01) + self (build review, adversarial refute-read,)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
