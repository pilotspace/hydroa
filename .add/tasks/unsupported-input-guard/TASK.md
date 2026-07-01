# TASK: Capability-aware unsupported-input guard (chat content-parts + STT)

slug: unsupported-input-guard · created: 2026-06-30 · stage: production
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
- `apps/gateway/src/gateway/proxy/application/use_cases.py` — `CompletionUseCase.complete()` (~L937 `_validate_payload`→model_id; ~L943 `_enforce_governance`) and `.stream()`. INSERTION POINT for chat guard: a new `await self._check_input_modalities(body, model_id)` right AFTER `_enforce_governance` (L944) and BEFORE bandwidth acquire (L961) / router call (L1247) — so it's post-auth, pre-upstream, pre-billing. SAME insertion in `stream()`. `body["messages"][].content` is a str (⇒ text) or a list of part dicts with `"type"` ∈ {text, image_url, input_audio, …}. `CompletionUseCase.__init__` (L493–549) holds NO AsyncSession; it does hold `_model_groups` (alias→candidates, used by `_enforce_governance`). The flag pattern to copy is `_web_search_enabled` (L549 store, L581 use).
- `apps/gateway/src/gateway/proxy/application/audio_use_case.py` — `TranscriptionUseCase.execute()` (STT): L155 `model_id = form.get("model")`; L164–170 ALREADY does `select(ModelRow.modality, ModelRow.provider).where(id==model_id, active)` → `row`; L176 `await file_field.read()`. INSERTION POINT: extend that `select(...)` to include `ModelRow.input_modalities`, then after L170 (row known) BEFORE `select_provider()` (L173) reject if the STT input modality (audio) ∉ row.input_modalities. STT input is ALWAYS audio. `SpeechUseCase.execute()` (TTS) is the same shape but its input is TEXT — guard symmetric. Both hold a direct `self._session` (L117/L291).
- `apps/gateway/src/gateway/proxy/api/deps.py` — `get_completion_use_case()` (L108) already has `session: Depends(get_session)` and builds `SqlAlchemyModelChecker(session)` (L126); this is the DI seam to wire a NEW lightweight input-modality lookup (built from the same session) + the new flag into `CompletionUseCase`. `audio_deps.py` `get_transcription_use_case`/`get_speech_use_case` (L61/L98) inject the session already.
- `apps/gateway/src/gateway/proxy/domain/ports.py` — `ModelChecker` port (is_active/check_for_tenant returns bool/ModelAccess, NOT a row). A new read is needed: a small port `InputModalityLookup.get(model_id) -> frozenset[str] | None` (None = unknown id) implemented over the catalog `models` table, OR extend ModelChecker. Reuse `gateway.catalog.domain.entities.parse_input_modalities` (task 1) to turn the stored csv into a set.
- `apps/gateway/src/gateway/core/error_catalog.py` — `ErrorSpec(status, code, title_template)` + `.exc(detail=…, **fmt)`. PRECEDENT sibling at L506: `UNSUPPORTED_CONTENT_PART = ErrorSpec(400, "ERR_UNSUPPORTED_CONTENT_PART", …)`. ADD `UNSUPPORTED_INPUT_MODALITY = ErrorSpec(400, "ERR_UNSUPPORTED_INPUT_MODALITY", "Model does not accept '{input_type}' input")`. Rendered by `core/errors.py:on_problem`→`problem_response()` as application/problem+json {type,title,status,code,detail}.
- `apps/gateway/src/gateway/core/config.py` — `Settings` (env_prefix `GATEWAY_`). ADD `input_modality_guard_enabled: bool = Field(default=False)` ⇒ env `GATEWAY_INPUT_MODALITY_GUARD_ENABLED`, mirroring `web_search_enabled` (L331). Read in deps, passed to the use-case constructors. DEFAULT-OFF (milestone rollout decision).
- task-1 outputs: `gateway.catalog.infrastructure.orm.ModelRow.input_modalities` (TEXT csv) · `gateway.catalog.domain.entities.parse_input_modalities`/`VALID_INPUT_MODALITIES` — the data + parser this guard consumes.
Context (working folder): `.add/milestones/v55/MILESTONE.md` (task 3; depends on model-input-capabilities DONE; shared decisions: guard rejects BEFORE upstream + BEFORE billing; DEFAULT-OFF flag; structured error reused verbatim by v56 preset validation). Endpoints in scope: `/v1/chat/completions` (content-parts) + `/v1/audio/transcriptions` (STT, audio input). Out: `/v1/embeddings`, `/v1/images/generations` (text-only inputs — no multi-type ambiguity; can be a follow-up), artifacts (separate task). Test home: `apps/gateway/tests/input_modality_guard/` (mirror conftest of `tests/audio_endpoints/` + `tests/observability/` for use-case wiring).
Honors (patterns / conventions): CLAUDE.md — design-for-failure (DEFAULT-OFF flag; fail-OPEN on unknown/missing capability so an under-seeded catalog never 4xx's real traffic). PROJECT.md — byte-identical seams (the frozen `FallbackModelRouter` + every existing proxy/audio test stay unchanged with the flag OFF); errors via the problem+json `ErrorSpec` catalog only (no ad-hoc JSONResponse). Never bill a refused request (guard fires before bandwidth acquire / upstream / usage record).
Anchors the contract cites: `CompletionUseCase.complete`/`.stream` (chat insertion after `_enforce_governance`) · `TranscriptionUseCase.execute`/`SpeechUseCase.execute` (audio insertion after the catalog row, extended `select`) · the new `InputModalityLookup` port + its DI wiring in `deps.py`/`audio_deps.py` · `UNSUPPORTED_INPUT_MODALITY` ErrorSpec · `GATEWAY_INPUT_MODALITY_GUARD_ENABLED` flag · `parse_input_modalities`/`ModelRow.input_modalities` (task-1 seam) · `_model_groups` alias resolution.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Capability-aware unsupported-input guard — reject a request whose input types exceed the resolved model's `input_modalities`, before upstream + before billing (default-off)
Framings weighed: a small reusable guard helper called from the existing chat + audio use cases at the pre-upstream point, fed by a lightweight `InputModalityLookup` over the catalog (chosen) · middleware that inspects every request body (rejected: can't see the resolved model / governance context, duplicates parsing) · push the check into FallbackModelRouter (rejected: router is frozen + audio doesn't go through it)
Must:
<must>
  - Behind `GATEWAY_INPUT_MODALITY_GUARD_ENABLED` (default FALSE). With it OFF, behavior is byte-identical — no lookup, no rejection, frozen router + every existing proxy/audio test unchanged.
  - The guard runs in `CompletionUseCase.complete()` AND `.stream()` (chat) and `TranscriptionUseCase.execute()` (STT), AFTER auth/governance and BEFORE any upstream call, bandwidth acquire, or usage record — a refused request is never billed.
  - REQUIRED input modalities are derived from the request: chat — `messages[].content` str ⇒ {text}; list parts map by `type` (`text`→text, `image_url`→image). `video_url` maps to the DEFERRED `video` modality → the guard SKIPS it (no validation in v55, pass-through). There is NO audio-in-chat in this codebase (`input_audio` is realtime-WS-only). Unknown/malformed parts stay the existing adapter's `ERR_UNSUPPORTED_CONTENT_PART` concern, not this guard's. STT — input is intrinsically {audio}.
  - ALLOWED modalities = the resolved model's `input_modalities` (parsed via task-1 `parse_input_modalities`). Plain model id → its catalog row. Alias (in `_model_groups`) → the UNION of its candidates' allowed sets (allow if SOME candidate can serve; reject only when NO candidate supports the required type).
  - On a required modality ∉ allowed: raise `UNSUPPORTED_INPUT_MODALITY` (problem+json, status 400, code `ERR_UNSUPPORTED_INPUT_MODALITY`) naming the offending `input_type` and the model's supported set in the detail — before upstream/billing.
  - FAIL-OPEN: an unknown model id (no catalog row, not an alias) or empty/missing capability data ⇒ ALLOW (no 4xx). The guard never blocks traffic on absent data (design-for-failure).
</must>
Reject:
<reject>
  - chat request carrying an `image_url` (or `input_audio`) part whose required modality ∉ the resolved model's allowed set, flag ON -> "ERR_UNSUPPORTED_INPUT_MODALITY" (no upstream, no bandwidth, no usage row)
  - STT request to a model whose `input_modalities` lacks `audio`, flag ON -> "ERR_UNSUPPORTED_INPUT_MODALITY" (no upstream, no usage row)
</reject>
After:
<after>
  - with the flag ON, any request whose input types exceed the resolved model's capabilities returns a clean 400 problem+json before upstream/billing, naming the unsupported type + the supported set; every in-capability request and ALL flag-OFF traffic is byte-identical; `FallbackModelRouter` and the provider seam are untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ✓ RESOLVED [spec] ALIAS-GROUP resolution = UNION of candidates (allow if SOME candidate supports the required type; reject only when NONE do) — Tin 2026-06-30. Guarantees NO false-reject; a mixed-capability alias may still emit an opaque provider error on an unlucky candidate (bounded — rare; plain ids + uniform aliases are exact). Revisit with presets (v56).
  ✓ RESOLVED [contract] status 400 + code `ERR_UNSUPPORTED_INPUT_MODALITY`, matching the sibling `ERR_UNSUPPORTED_CONTENT_PART` — Tin chose 400 over 422.
  ✓ RESOLVED [spec] FAIL-OPEN on unknown model id / empty capability (no 4xx) — decided per CLAUDE.md design-for-failure + default-off; fail-closed would 4xx un-seeded models. (Recommended + adopted; surfaced at freeze.)
  ✓ RESOLVED [spec] content-part keys VERIFIED in code (gemini_upstream `_content_to_gemini_parts`): chat parts are `text`/`image_url`/`video_url` — NO `input_audio` in chat (realtime-WS only). ⇒ chat guard validates IMAGE only (text universal; video_url skipped as deferred); AUDIO is enforced solely at the STT endpoint.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Flag OFF — byte-identical, guard never runs
  Given GATEWAY_INPUT_MODALITY_GUARD_ENABLED is false
  And a model "text-only-x" whose input_modalities is "text"
  When a chat completion with an image_url content part targets "text-only-x"
  Then the request proceeds exactly as today (no 400 from the guard)
  And no input-modality lookup occurs and the existing proxy/audio suites are unchanged

Scenario: Chat image to a text-only model is rejected (flag ON)
  Given the guard is enabled
  And "text-only-x" has input_modalities "text"
  When a chat completion targeting "text-only-x" includes an image_url content part
  Then the response is 400 with code ERR_UNSUPPORTED_INPUT_MODALITY naming input_type "image" and the supported set {text}
  And no upstream call, no bandwidth acquire, and no usage row are produced

Scenario: Chat image to a vision-capable model is allowed
  Given the guard is enabled
  And "vision-y" has input_modalities "text,image"
  When a chat completion targeting "vision-y" includes an image_url content part
  Then the guard does not reject and the request proceeds to the upstream path

Scenario: Plain-text chat is always allowed
  Given the guard is enabled
  And any active model
  When a chat completion sends string (text-only) content
  Then the guard does not reject (text is universally accepted)

Scenario: video_url part is skipped (video deferred from v55)
  Given the guard is enabled
  And "text-only-x" has input_modalities "text"
  When a chat completion targeting "text-only-x" includes a video_url part (and no image)
  Then the guard does NOT reject on video (pass-through; video is out of v55 scope)
  And the request proceeds as if the guard found nothing to enforce

Scenario: Alias group — union allows when some candidate supports the type
  Given the guard is enabled
  And alias "grp" resolves to candidates ["text-only-a" (text), "vision-b" (text,image)]
  When a chat completion targeting "grp" includes an image_url part
  Then the guard allows it (the union of candidate capabilities includes image)

Scenario: Alias group — union rejects when no candidate supports the type
  Given the guard is enabled
  And alias "grp2" resolves to candidates ["text-only-a" (text), "text-only-c" (text)]
  When a chat completion targeting "grp2" includes an image_url part
  Then the response is 400 ERR_UNSUPPORTED_INPUT_MODALITY naming input_type "image"
  And no upstream call and no usage row are produced

Scenario: STT to a non-audio model is rejected (flag ON)
  Given the guard is enabled
  And "text-only-x" has input_modalities "text"
  When POST /v1/audio/transcriptions targets "text-only-x" with an audio file
  Then the response is 400 ERR_UNSUPPORTED_INPUT_MODALITY naming input_type "audio" and the supported set {text}
  And no upstream call and no usage row are produced

Scenario: STT to an audio-capable model is allowed
  Given the guard is enabled
  And "whisper-1" has input_modalities "audio"
  When POST /v1/audio/transcriptions targets "whisper-1" with an audio file
  Then the guard does not reject and the request proceeds to the upstream path

Scenario: Fail-open on an unknown model id
  Given the guard is enabled
  And model id "ghost" has no catalog row and is not an alias
  When a chat completion targeting "ghost" includes an image_url part
  Then the guard ALLOWS the request (fail-open on missing capability data; no 400 from the guard)
  And downstream handling (existing unknown-model behavior) is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Behavioral contract (no NEW endpoint). The guard adds a 4xx branch to two EXISTING endpoints,
# gated by a default-off flag. With the flag off, every shape below is byte-identical to today.

HTTP — when GATEWAY_INPUT_MODALITY_GUARD_ENABLED=true and a required input modality is unsupported:
  POST /v1/chat/completions   (a messages[].content with an image_url part, model can't accept image)
  POST /v1/audio/transcriptions  (STT to a model whose input_modalities lacks audio)
    -> 400 application/problem+json:
       { "type":"about:blank", "title":"Model does not accept '<input_type>' input",
         "status":400, "code":"ERR_UNSUPPORTED_INPUT_MODALITY",
         "detail":"<input_type> input not supported by model '<model_id>' (supports: <sorted allowed>)" }
    Fired BEFORE upstream call, bandwidth acquire, and usage record. (flag off -> this branch never runs)

Config — apps/gateway/src/gateway/core/config.py  (Settings)
  input_modality_guard_enabled: bool = Field(default=False)   # env GATEWAY_INPUT_MODALITY_GUARD_ENABLED

Error — apps/gateway/src/gateway/core/error_catalog.py
  UNSUPPORTED_INPUT_MODALITY = ErrorSpec(400, "ERR_UNSUPPORTED_INPUT_MODALITY",
                                         "Model does not accept '{input_type}' input")

Port + impl — apps/gateway/src/gateway/proxy/domain/ports.py  +  proxy/infrastructure/
  class InputModalityLookup(Protocol):
      async def get(self, model_id: str) -> frozenset[str] | None        # None = no active catalog row
  SqlAlchemyInputModalityLookup(session): SELECT input_modalities FROM models WHERE id=:id AND active
      -> parse_input_modalities(text)  (task-1 helper) ; None when no row

Guard helper — proxy/application/ (pure, reused by chat + audio)
  required_input_modalities_for_chat(messages) -> frozenset[str]
     str content -> {"text"} ; list -> {"text" if any text part} ∪ {"image" if any image_url part}
     video_url parts IGNORED (video deferred) ; unknown parts ignored (existing ERR_UNSUPPORTED_CONTENT_PART owns them)
  resolve_allowed(model_id, lookup, model_groups) -> frozenset[str] | None
     plain id  -> lookup.get(model_id)
     alias (in model_groups) -> UNION of lookup.get(c) over candidates c  (skip None) ; None if every candidate None
     None (unknown id / no data) -> caller FAILS OPEN (no rejection)
  enforce(required, allowed) :
     allowed is None -> return (fail-open)
     missing = required - allowed - {"video"}   # video never enforced in v55
     if missing: raise UNSUPPORTED_INPUT_MODALITY.exc(
        detail=f"{first(missing)} input not supported by model '{model_id}' (supports: {sorted(allowed)})",
        input_type=first(missing))

Wiring
  CompletionUseCase.__init__: + input_modality_lookup: InputModalityLookup | None = None,
                              + input_modality_guard_enabled: bool = False
    complete() & stream(): after _enforce_governance(), if guard enabled: await self._check_input_modalities(body, model_id)
  get_completion_use_case() (deps.py): build SqlAlchemyInputModalityLookup(session) + read the flag, pass both.
  TranscriptionUseCase.execute(): extend the existing select() to add ModelRow.input_modalities;
    after row known, if flag: enforce({"audio"}, parse_input_modalities(row.input_modalities) or None) before select_provider().
  audio_deps wires the flag into the audio use cases.

UNCHANGED (byte-identical — assert; do NOT touch):
  FallbackModelRouter + every proxy/edge/audio/governance test with the flag OFF (the default)
  the success path of both endpoints when inputs are in-capability
  ERR_UNSUPPORTED_CONTENT_PART (malformed/unknown parts) stays the adapter's job, distinct from this guard
  billing / bandwidth / usage recording order (guard inserts strictly BEFORE them)
```

Least-sure flag surfaced at freeze:
  [spec] ALIAS-group resolution = UNION (allow if any candidate supports the type; reject only when none do)
  — Tin-confirmed. Residual risk: the router may still fall back to a non-supporting candidate of a MIXED alias
  and the provider returns its own error (the pre-guard status quo) — bounded to rare mixed-capability aliases;
  plain ids + the STT path are exact. Secondary [contract]: 400 (not 422) to match ERR_UNSUPPORTED_CONTENT_PART;
  and FAIL-OPEN on unknown/empty capability (never 4xx on missing data) — both Tin-confirmed / design-for-failure.

Status: FROZEN @ v1 — approved by Tin 2026-06-30 (AskUserQuestion: "Freeze v1, proceed"; union resolution + 400 + fail-open confirmed). Changing this contract now = a change request back to SPECIFY.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: changed proxy files ≥ 80% (project floor); ~10 tests, one per §2 scenario + a flag-off parity assertion.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_flag_off_parity: guard disabled → image-to-text-only chat proceeds, no lookup, no 400 (byte-identical)
  - test_chat_image_to_text_only_rejected: flag on → 400 ERR_UNSUPPORTED_INPUT_MODALITY input_type=image; assert NO upstream call + NO usage row (spy/fake upstream not invoked)
  - test_chat_image_to_vision_allowed: flag on, model input_modalities text,image → proceeds
  - test_chat_text_always_allowed: flag on, string content → proceeds
  - test_chat_video_url_skipped: flag on, text-only model + video_url part → NOT rejected (deferred)
  - test_alias_union_allows: flag on, alias→[text-only, vision] + image → proceeds
  - test_alias_union_rejects: flag on, alias→[text-only, text-only] + image → 400; no upstream/usage
  - test_stt_non_audio_rejected: flag on, STT to text-only model → 400 input_type=audio; no upstream/usage
  - test_stt_audio_allowed: flag on, STT to whisper-1 (audio) → proceeds
  - test_fail_open_unknown_model: flag on, unknown id + image → guard allows (no 400)
  - helper unit tests: required_input_modalities_for_chat / resolve_allowed (union, None) / enforce
</test_plan>

Tests live in: `apps/gateway/tests/input_modality_guard/` · MUST run red (missing flag/port/guard/error) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/tests/input_modality_guard/`   — guard helpers + InputModalityLookup port/impl in proxy/application+domain+infrastructure; chat use_cases.py + audio_use_case.py insertion; deps.py + audio_deps.py wiring; the config flag; the new ErrorSpec; the new red suite. No catalog/* changes (task 1 owns it); FallbackModelRouter untouched.
Strategy (ordered batches): 1. config flag + ErrorSpec · 2. InputModalityLookup port+SQLAlchemy impl · 3. pure guard helpers (required_input_modalities_for_chat / resolve_allowed / enforce) · 4. wire chat complete+stream · 5. wire STT execute (extend select) · 6. deps/audio_deps wiring · 7. red→green.
Known-problem fixes: guard MUST sit after _enforce_governance and before bandwidth acquire/upstream/usage (assert no-upstream/no-usage in reject tests); flag OFF path must do ZERO new work (byte-identical — run a flag-off parity test); alias union skips None candidates; fail-open when lookup returns None; reuse task-1 parse_input_modalities (don't reparse); do NOT touch FallbackModelRouter or any frozen proxy/edge/governance test.
Strategy actually used: as planned (delegated to backend-expert subagent). Key build decision: the STT `select()` is made CONDITIONAL on the flag (extends to `input_modalities` only when guard ON) so the flag-OFF path is byte-identical and the existing audio suites stay green — the subagent caught this during its own verify. SpeechUseCase/TTS NOT wired (TTS input is text = universal; contract scoped STT only). Alias-union tests exercise `_check_input_modalities` directly (HTTP+governance alias wiring needs the DB router).
Safety rule (feature-specific): guard fires AFTER governance and BEFORE bandwidth acquire / upstream / usage — a refused request is never billed (proven by reject tests asserting upstream.calls==0 AND recorder.call_count==0). Fail-open on missing capability.
Code lives in: `./src/`
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

- [x] all tests pass — 91 passed (22 new + 69 regression) in one pytest process, re-run first-hand by the orchestrator
- [x] coverage did not decrease — regression suites byte-identical with the flag OFF (default); new module covered by 22 tests
- [x] no test or contract was altered during build — only new files + additive guard wiring; no frozen proxy/audio test edited
- [x] the green was EARNED — refute-read below; reject tests assert upstream.calls==0 AND recorder.call_count==0; flag-off parity + fail-open proven
- [x] concurrency / timing safe — guard is a synchronous pre-flight check (one extra SELECT when ON); no new locks/tasks; STT reuses the existing single round-trip
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps; parameterized SQLAlchemy select; problem+json via the existing ErrorSpec catalog
- [x] layering & dependencies follow CONVENTIONS.md — pure helpers in application, port in domain, SQLAlchemy impl in infrastructure, flag in core/config; FallbackModelRouter untouched
- [ ] a person reviewed and approved the change — Tin (orchestrator auto-PASS under autonomy:auto; surfaced for spot-audit)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] flag OFF ⇒ image-to-text-only chat returns 200 and upstream is called — confirmed: test_flag_off_parity (fake_upstream.calls==1)
- [x] flag ON ⇒ chat image to text-only model returns 400 ERR_UNSUPPORTED_INPUT_MODALITY with NO upstream + NO usage — confirmed: test_chat_image_to_text_only_rejected (calls==0, spy.call_count==0)
- [x] flag ON ⇒ STT to a text-only model returns 400 input_type=audio, no upstream/usage; whisper-1 (audio) proceeds — confirmed: test_stt_non_audio_rejected / test_stt_audio_allowed
- [x] alias union allows when a candidate supports image, rejects when none do — confirmed: test_alias_union_allows / _rejects + resolve_allowed unit test
- [x] fail-open on unknown model id (guard never raises its 400) — confirmed: test_fail_open_unknown_model
- [x] guard inserts AFTER _enforce_governance, BEFORE bandwidth/upstream/usage in complete() AND stream() — confirmed: read use_cases.py diff (L991, L1564)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — InputModalityLookup port → SqlAlchemyInputModalityLookup (deps.py get_completion_use_case + audio_deps) → CompletionUseCase._check_input_modalities (complete+stream) + TranscriptionUseCase.execute; ErrorSpec + flag referenced. All read first-hand.
- [x] DEAD-CODE (code) — no orphan: helpers each used by a use case or unit-tested; lookup wired in both DI seams; the STT conditional-select branch is exercised by both flag states.
- [x] SEMANTIC — read modality_guard.py + input_modality_lookup.py + both use-case diffs + the full test file in full: logic matches frozen §3 (union, fail-open, video-skip, 400, pre-billing order).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: self (orchestrator, post-subagent independent review) · adversarially checked: (1) reject paths are NOT vacuous — they assert upstream.calls==0 AND usage recorder.call_count==0 (no billing), not just the 400; (2) flag-OFF parity is a real HTTP round-trip proving the guard never fires by default; (3) byte-identical — re-ran 69 existing proxy/audio/fallback tests first-hand (91 total green) + caught that the build correctly made the STT select CONDITIONAL (the byte-identical fix); (4) scope — git status shows no catalog/* or fallback_router changes; (5) ruff clean + pyright 0 errors on the changed modules. RESIDUE (non-blocking): a few sync helper tests carry a module-level `pytestmark = pytest.mark.asyncio` → cosmetic pytest warnings; behavior unaffected — note for a future tidy. No overfit / stubbed-away logic found.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: orchestrator (auto-PASS, autonomy:auto) · date: 2026-06-30 — surfaced to Tin for spot-audit; no security finding.

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose a small reusable guard helper called from the existing chat + audio use cases at the pre-upstream point, fed by a lightweight `InputModalityLookup` over the catalog; rejected middleware that inspects every request body (rejected: can't see the resolved model / governance context, duplicates parsing) · push the check into FallbackModelRouter (rejected: router is frozen + audio doesn't go through it)
- [human] freeze — froze §3 @ v1 (approved by Tin 2026-06-30 (AskUserQuestion: "Freeze v1, proceed"; union resolution + 400 + fail-open confirmed). Changing this contract now = a change request back to SPECIFY.)
- [AI] build — strategy used: as planned (delegated to backend-expert subagent). Key build decision: the STT `select()` is made CONDITIONAL on the flag (extends to `input_modalities` only when guard ON) so the flag-OFF path is byte-identical and the existing audio suites stay green — the subagent caught this during its own verify. SpeechUseCase/TTS NOT wired (TTS input is text = universal; contract scoped STT only). Alias-union tests exercise `_check_input_modalities` directly (HTTP+governance alias wiring needs the DB router).
- [AI] verify — gate PASS (reviewed by orchestrator (auto-PASS, autonomy:auto))

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
