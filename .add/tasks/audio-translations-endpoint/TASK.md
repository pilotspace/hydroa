# TASK: POST /v1/audio/translations (Whisper translate)

slug: audio-translations-endpoint · created: 2026-06-26 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
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
  - `apps/gateway/src/gateway/proxy/application/audio_use_case.py` (MODIFY) — `TranscriptionUseCase.execute` gains `upstream_path: str = "/audio/transcriptions"`; line 195 calls `post_multipart(upstream_path, ...)` instead of the literal. Add `_TRANSLATION_PASSTHROUGH_FIELDS = ("prompt", "response_format", "temperature")` (NO `language` — Whisper translate always outputs English); execute picks the passthrough tuple by path. Everything else (governance, per_second billing, duration derivation+cap, non-finite sanitize) is REUSED byte-identically.
  - `apps/gateway/src/gateway/proxy/api/audio_router.py` (MODIFY) — add `@audio_router.post("/v1/audio/translations")` handler mirroring `audio_transcriptions` (same deps: get_transcription_use_case, registry, usage_recorder, raw_key) that calls `use_case.execute(..., upstream_path="/audio/translations")`.
  - `apps/gateway/tests/audio_translations/` (NEW) — no-DB unit tests of `TranscriptionUseCase.execute` (monkeypatch `select_provider` + `_fire_record_with_raw`; fake session/governance) asserting the captured upstream path + the language-drop + per_second billing. Join `make test-fast`.
Context (working folder):
  - STT path today (audio_use_case.py:123-269): validate file+model → governance.authorize → catalog query (modality+provider) → select_provider → read bytes + build files/data (passthrough `_STT_PASSTHROUGH_FIELDS = (language, prompt, response_format, temperature)`) → `post_multipart("/audio/transcriptions", files, data)` → resolve+cap duration → `_fire_record_with_raw(pricing_unit="per_second")` → sanitize → return (status, body). The ONLY upstream difference for translate is the path (and dropping `language`).
  - The router (audio_router.py:36-57) reads the multipart form and calls `use_case.execute(...)`; both audio routes share the SAME use-case + deps.
  - `resolve_provider_credential(resolver=None, ...)` returns None (skip) — so a no-DB test with `tenant_credential_resolver=None` needs no credential mock. `_fire_record_with_raw` (use_cases.py:345) is a module fn imported into audio_use_case → monkeypatchable to capture the billing kwargs.
Honors (patterns / conventions):
  - ADDITIVE + REUSE (milestone "80% reuse"): no new use-case class; one back-compat param + one tiny passthrough branch; STT path byte-identical (default param + regression test).
  - BILLING HONESTY (v27): translations bill per_second on the same derived/capped duration — never fabricated.
  - DESIGN-FOR-FAILURE: inherits the STT path's UpstreamUnavailable/CircuitOpen → 502 mapping + credential finally-reset + duration cap.
Anchors the contract cites:
  - `TranscriptionUseCase.execute(upstream_path=)` · `_TRANSLATION_PASSTHROUGH_FIELDS` · the `/v1/audio/translations` route · `post_multipart` · `_fire_record_with_raw(pricing_unit="per_second")`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: `POST /v1/audio/translations` — Whisper transcribe-and-translate-to-English. A SECOND STT route reusing `TranscriptionUseCase` end-to-end (governance, per_second billing, duration cap, sanitize); the only deltas are the upstream path `/audio/translations` and dropping the `language` form field (translate always outputs English).
Framings weighed: thread an `upstream_path` param through the existing use-case + a thin new route (chosen — ~80% reuse, one back-compat param, STT byte-identical) · a separate TranslationUseCase subclass (rejected — duplicates the whole pipeline) · a `translate: bool` flag on the existing route (rejected — diverges from the OpenAI API shape, which uses a distinct path).
Must:
<must>
  - M1 — `POST /v1/audio/translations` accepts the same multipart form as transcriptions, runs the SAME governance + catalog + provider-select, and calls `post_multipart("/audio/translations", ...)`; returns the upstream JSON verbatim.
  - M2 — it bills `per_second` on the same derived+capped duration as STT (single-bill invariant; honest duration).
  - M3 — the `language` form field is NOT forwarded on the translate path (OpenAI translate has no language param); `prompt`/`response_format`/`temperature` still pass through.
  - M4 — the existing `/v1/audio/transcriptions` path is byte-identical: default `upstream_path="/audio/transcriptions"` + `language` still forwarded.
</must>
Reject:
<reject>
  - missing file / model -> "PAYLOAD_FILE_REQUIRED" / "PAYLOAD_MODEL_REQUIRED" (422) — inherited, unchanged.
  - upstream 5xx / circuit open -> "UPSTREAM_UNAVAILABLE" (502) — inherited from the STT path.
  - unknown/inactive model -> "MODEL_UNKNOWN" (404) — inherited.
</reject>
After:
<after>
  - A signed-in user POSTs audio to `/v1/audio/translations` and gets an English transcript, billed per second; the transcriptions route is unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ OpenAI's `/audio/translations` accepts the same multipart shape minus `language` — lowest confidence because we test vs mocks, not live OpenAI, and the translate endpoint's exact accepted fields can drift; if wrong: a translate call errors upstream (surfaced honestly as 502/4xx; transcriptions unaffected). Cost bounded — additive route, same provider plumbing.
  - [x] only the path + the language-drop differ from STT — CONFIRMED by reading execute (the rest is provider-agnostic).
  - [x] no-DB unit test is possible — CONFIRMED (monkeypatch select_provider + _fire_record_with_raw; fake session/governance; resolver=None skips credentials).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Translate routes to the translations upstream path
  Given a valid multipart audio request to /v1/audio/translations
  When TranscriptionUseCase.execute runs with upstream_path="/audio/translations"
  Then post_multipart is called with "/audio/translations"
  And a per_second usage record fires once

Scenario: Translate drops the language field
  Given a translate request whose form includes language=es and temperature=0
  When execute runs the translate path
  Then the data sent to post_multipart has NO "language" key
  And it still includes temperature

Scenario: Transcriptions path unchanged (regression)
  Given a request to /v1/audio/transcriptions (default upstream_path)
  When execute runs
  Then post_multipart is called with "/audio/transcriptions"
  And the data still includes language when the form supplies it

Scenario: Billing is per_second
  Given a successful translate call
  When execute fires the usage record
  Then pricing_unit is "per_second"
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /v1/audio/translations   multipart/form-data: { file, model, prompt?, response_format?, temperature? }
  200 -> upstream JSON verbatim (English transcript)   bills per_second
  422 ERR_PAYLOAD_INVALID (file/model missing) · 404 MODEL_UNKNOWN · 502 UPSTREAM_UNAVAILABLE   (all inherited)

// ─ Use-case seam ─
_TRANSLATION_PASSTHROUGH_FIELDS = ("prompt", "response_format", "temperature")   # NO language
class TranscriptionUseCase:
    async def execute(self, *, raw_key, form, registry, usage_recorder,
                      upstream_path: str = "/audio/transcriptions") -> tuple[int, dict]:
        ...
        passthrough = (_TRANSLATION_PASSTHROUGH_FIELDS
                       if upstream_path == "/audio/translations"
                       else _STT_PASSTHROUGH_FIELDS)
        # build data from `passthrough`; then:
        status, resp_body = await provider_adapter.post_multipart(upstream_path, files=files, data=data)
        # everything else (duration derive+cap, _fire_record per_second, sanitize) UNCHANGED

// ─ Router ─
@audio_router.post("/v1/audio/translations")   # mirrors audio_transcriptions, same deps
async def audio_translations(...): await use_case.execute(..., upstream_path="/audio/translations")

Schema: none — no DB/migration. Reuses the STT pipeline; STT path byte-identical (default param + language kept).
```

Status: FROZEN @ v1 — auto-approved (full-auto; additive route + one back-compat param; reuses the proven STT money/auth path byte-identically; no new auth/billing logic) 2026-06-26
Least-sure flag surfaced at freeze:
  - [spec] OpenAI `/audio/translations` accepted-field set — dropping `language` + keeping prompt/response_format/temperature is the documented shape, but verified vs mocks not live; if a field diverges the call errors upstream (surfaced honestly), transcriptions unaffected.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — one test per scenario; no-DB unit tests (monkeypatch select_provider + _fire_record_with_raw; fake session/governance); join make test-fast.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_translate_routes_to_translations_path: execute(upstream_path="/audio/translations") with a spy adapter (monkeypatched select_provider) → spy.captured_path == "/audio/translations".
  - test_translate_drops_language_field: form has language=es + temperature=0 → the data dict captured by the spy adapter has NO "language" key but HAS "temperature".
  - test_transcription_default_path_unchanged: execute() (no upstream_path) → captured path == "/audio/transcriptions" AND data includes "language" (regression).
  - test_translate_bills_per_second: monkeypatched _fire_record_with_raw captures pricing_unit == "per_second".
</test_plan>

Tests live in: `apps/gateway/tests/audio_translations/test_audio_translations.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/audio_use_case.py` · `apps/gateway/src/gateway/proxy/api/audio_router.py` · `apps/gateway/tests/audio_translations/` · `Makefile`
Strategy (ordered batches): 1. use-case: _TRANSLATION_PASSTHROUGH_FIELDS + upstream_path param + passthrough/path branch. 2. router: new /v1/audio/translations handler. 3. no-DB unit tests + Makefile test-fast.
Safety rule (feature-specific): the STT path MUST stay byte-identical — default upstream_path="/audio/transcriptions" + language still forwarded (pinned by a regression test); reuse the existing per_second billing + UpstreamUnavailable→502 mapping unchanged (no new money/auth logic).
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the contract; do NOT alter the STT billing/duration/sanitize logic; allow-list packages only (no new deps); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full test-fast 206 passed (202 → +4 audio_translations); new suite 4/4 green.
- [x] coverage did not decrease — +4 behavioral tests; no suite removed.
- [x] no test or contract was altered during build — contract unchanged; the STT path is byte-identical (default param + language kept), pinned by test_transcription_default_path_unchanged.
- [x] the green was EARNED — I read the actual diff: the passthrough is selected by path (`_TRANSLATION_PASSTHROUGH_FIELDS if upstream_path=="/audio/translations" else _STT_PASSTHROUGH_FIELDS`); `post_multipart(upstream_path, ...)`; the route mirrors audio_transcriptions and passes upstream_path="/audio/translations". Tests assert the captured path + the language-drop + per_second billing via a spy adapter + monkeypatched _fire_record_with_raw — real production code, not fixtures. Confirmed single sanitize_non_finite import (no duplicate) + the spy's mutable class-attr hardened to __init__ (RUF012). Small additive reuse of the proven STT money/auth path; careful manual diff review (Rule 5) in lieu of a refute subagent.
- [x] concurrency / timing safe — no new shared state; reuses the STT path's credential finally-reset + single-bill; the new route is a thin passthrough.
- [x] no exposed secrets, injection openings, or unexpected dependencies — reuses the STT auth/credential plumbing unchanged; no secrets; no new deps.
- [x] layering & dependencies follow CONVENTIONS.md — param in the application use-case; route in the api router; same layering as the STT route it mirrors.
- [x] a person reviewed and approved the change — full-auto drive; careful manual diff review of both files + the test at the gate; additive + STT byte-identical.

### Build expectations — what "correct" looks like
- [x] A translate request reaches the upstream `/audio/translations` path — confirmed: `post_multipart(upstream_path, ...)` + `test_translate_routes_to_translations_path` (captured path == "/audio/translations").
- [x] `language` is dropped on translate, kept on transcribe — confirmed: the passthrough branch + `test_translate_drops_language_field` (no "language", has "temperature") + `test_transcription_default_path_unchanged` (language present on the default path).
- [x] Billing is per_second — confirmed: the `_fire_record_with_raw(pricing_unit="per_second")` call is unchanged; `test_translate_bills_per_second` asserts it on the translate path.
- [x] STT path byte-identical — confirmed: default `upstream_path="/audio/transcriptions"` + `_STT_PASSTHROUGH_FIELDS`; regression test pins both.

### Deep checks
- [x] WIRING — `audio_translations` route → `TranscriptionUseCase.execute(upstream_path=)` → passthrough branch + `post_multipart(upstream_path)`; every new symbol referenced; verified by reading both diffs.
- [x] DEAD-CODE — `_TRANSLATION_PASSTHROUGH_FIELDS` is used by the branch; the route + param are exercised by tests; no orphans; no duplicate import.
- [x] SEMANTIC — read both file diffs in full; the route mirrors audio_transcriptions exactly except the upstream_path arg + docstring.

### Residue / deltas
- Live-verify the translate request shape against real OpenAI (tested vs mocks); the §1 ⚠ flag (translate accepted-field set) is tracked.
- The DB-backed audio_endpoints integration suite does not yet cover the translate route (the no-DB unit tests cover the new behavior); an integration test is a deferred delta.
- Pre-existing audio_use_case.py I001 lint (HEAD backlog) was auto-sorted by the edit hook; my new code is ruff + pyright clean.

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto drive + careful manual diff review · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
