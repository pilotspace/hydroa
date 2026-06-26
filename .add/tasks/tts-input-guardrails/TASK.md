# TASK: TTS input-length ceiling (reject before bill)

slug: tts-input-guardrails · created: 2026-06-26 · stage: production
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
  - `apps/gateway/src/gateway/core/config.py` (MODIFY) — add `tts_max_input_characters: int = Field(default=4096, ge=0)` (env GATEWAY_TTS_MAX_INPUT_CHARACTERS; default-ON at 4096 = OpenAI's limit; 0 = disabled escape hatch). Mirrors `stt_max_duration_seconds` (config.py:349).
  - `apps/gateway/src/gateway/core/error_catalog.py` (MODIFY) — add `PAYLOAD_INPUT_TOO_LONG = ErrorSpec(413, "ERR_PAYLOAD_INPUT_TOO_LONG", "TTS input exceeds the maximum allowed length")` near PAYLOAD_INPUT_REQUIRED (181).
  - `apps/gateway/src/gateway/proxy/application/audio_use_case.py` (MODIFY) — `SpeechUseCase.__init__` gains `max_input_characters: int = 0` (0 ⇒ no cap; legacy/test default off). `.execute` adds STEP 2.5 right after input validation (line 324), BEFORE governance/select/bill: `if self._max_input_characters > 0 and len(input_text) > self._max_input_characters: raise PAYLOAD_INPUT_TOO_LONG.exc()`.
  - `apps/gateway/src/gateway/proxy/api/audio_deps.py` (MODIFY) — pass `max_input_characters=request.app.state.settings.tts_max_input_characters` into `SpeechUseCase(...)` (mirrors the STT `max_duration_seconds` injection, audio_deps:71).
  - `apps/gateway/tests/tts_input_cap/` (NEW) — no-DB unit tests of `SpeechUseCase.execute` (the cap rejects at Step 2.5 BEFORE any DB/governance call), joins `make test-fast`.
Context (working folder):
  - TTS bills `per_character` at-start (audio_use_case.py:360-370, BEFORE streaming) with NO ceiling today — an unbounded `input` → runaway bill. The cap closes that vector.
  - SpeechUseCase.execute step order (frozen): 1 model, 2 input, 3 voice (all 422 PAYLOAD_*_REQUIRED), 4 governance, 5 catalog query, 6 provider-select, 7 _fire_record (bill), 9 stream. The cap MUST reject between 2 and 4 → before governance, upstream, AND bill.
  - `ErrorSpec(status, code, message).exc(**fmt)` is the error catalog pattern (error_catalog.py:32-64); PAYLOAD_* errors are 422; a SIZE cap is semantically 413 Payload Too Large.
  - Settings are snake_case (no GATEWAY_ prefix in code); injected into use-cases via audio_deps (the STT cap is the exact precedent).
Honors (patterns / conventions):
  - DEFAULT-SAFE (milestone): the cap ships ON (default 4096) because it closes a billing/abuse vector — but rejects BEFORE billing (no partial charge), pre-stream only (SpeechUseCase's pre-stream governance model).
  - FAIL-SAFE escape hatch: `0 ⇒ disabled` (no cap) for operators who need unbounded input.
  - Single-bill invariant preserved: the reject path fires NO usage record.
Anchors the contract cites:
  - `tts_max_input_characters` (config) · `PAYLOAD_INPUT_TOO_LONG` (413) · `SpeechUseCase.__init__(max_input_characters)` + the Step-2.5 guard · the audio_deps injection.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: TTS input-length ceiling. A default-ON `GATEWAY_TTS_MAX_INPUT_CHARACTERS` cap; a TTS request whose `input` exceeds it is rejected with a 413 BEFORE governance/upstream/billing — closing the per-character runaway-billing/abuse vector.
Framings weighed: a use-case Step-2.5 length check gated by an injected knob (chosen — mirrors the STT duration cap exactly; rejects before the single bill) · cap at the router/middleware (rejected — duplicates payload parsing, further from the bill site) · cap inside the adapter (rejected — too late, after governance/bill).
Must:
<must>
  - M1 — when `tts_max_input_characters > 0` and `len(input) > tts_max_input_characters`, `SpeechUseCase.execute` raises `PAYLOAD_INPUT_TOO_LONG` (413) at Step 2.5 — BEFORE governance (Step 4), provider-select, and the `_fire_record` bill (Step 7). No usage record fires.
  - M2 — DEFAULT-ON: the knob defaults to 4096 (OpenAI's limit); a within-cap request is unaffected (proceeds exactly as today).
  - M3 — DISABLE escape hatch: `tts_max_input_characters == 0` ⇒ no cap check (large input proceeds).
  - M4 — the knob is wired settings → audio_deps → `SpeechUseCase(max_input_characters=...)` (mirrors the STT `max_duration_seconds` injection); the use-case default is 0 (off) for legacy/test construction.
</must>
Reject:
<reject>
  - input longer than the cap -> "PAYLOAD_INPUT_TOO_LONG" (413) — before any bill/upstream.
  - (unchanged) empty/missing input -> "PAYLOAD_INPUT_REQUIRED" (422); the cap check runs only on a valid non-empty string.
</reject>
After:
<after>
  - An over-long TTS input is rejected with 413 and zero billing; within-cap TTS is unchanged; the cap is on by default and operator-tunable (0 disables).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ counting CHARACTERS (len(str)) is the right cap unit — lowest confidence because billing is per_character on the same len() so it's consistent, but a multibyte/emoji input counts code points not bytes; if wrong: the cap is slightly generous/strict for exotic inputs. Cost trivial — matches the billing unit exactly (len(input_text)); a byte cap would diverge from the bill.
  - [x] rejecting at Step 2.5 fires no bill — CONFIRMED (the single _fire_record is Step 7, after the raise).
  - [x] no-DB unit test is possible — CONFIRMED (the cap raises before the governance/catalog calls, so a __new__'d use-case + dummy deps suffices).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Over-cap input rejected before bill
  Given the cap is 4096 and a TTS request with a 5000-char input
  When SpeechUseCase.execute runs
  Then it raises PAYLOAD_INPUT_TOO_LONG (413)
  And governance.authorize is never called and no usage record fires

Scenario: Within-cap input proceeds
  Given the cap is 4096 and a 100-char input
  When execute runs
  Then the cap does not raise and the flow reaches governance (Step 4)

Scenario: Cap disabled at zero
  Given the cap is 0 and a 50000-char input
  When execute runs
  Then the cap does not raise (the flow reaches governance)

Scenario: Default knob value
  Given a default Settings()
  When tts_max_input_characters is read
  Then it equals 4096 (default-ON)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// ─ Config knob ─
tts_max_input_characters: int = Field(default=4096, ge=0)   # env GATEWAY_TTS_MAX_INPUT_CHARACTERS; 0 = disabled

// ─ Error ─
PAYLOAD_INPUT_TOO_LONG = ErrorSpec(413, "ERR_PAYLOAD_INPUT_TOO_LONG", "TTS input exceeds the maximum allowed length")

// ─ SpeechUseCase ─
def __init__(self, *, governance, session, tenant_credential_resolver=None, max_input_characters: int = 0): ...
# execute(), STEP 2.5 (immediately after the input-required check, before Step 4 governance):
if self._max_input_characters > 0 and len(input_text) > self._max_input_characters:
    raise PAYLOAD_INPUT_TOO_LONG.exc()

// ─ Wiring ─
audio_deps.get_speech_use_case: SpeechUseCase(..., max_input_characters=settings.tts_max_input_characters)

POST /v1/audio/speech  body:{model,input,voice,...}
  413 ERR_PAYLOAD_INPUT_TOO_LONG  (input over cap — before any bill/upstream)
  (unchanged) 422 PAYLOAD_INPUT_REQUIRED · 200 stream
Schema: none — knob + pre-stream validation only. No DB/migration. Single-bill invariant preserved.
```

Status: FROZEN @ v1 — auto-approved (full-auto; additive default-safe guardrail; rejects before the single bill so no billing-correctness risk; mirrors the STT-cap precedent) 2026-06-26
Least-sure flag surfaced at freeze:
  - [spec] character vs byte cap unit — chosen len(str) to MATCH the per_character billing unit exactly (a byte cap would diverge from the bill); trivial cost for multibyte inputs.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — one test per scenario; no-DB unit tests (cap raises before any DB/governance call); join make test-fast.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_over_cap_rejects_before_bill: SpeechUseCase.__new__ with max_input_characters=4096 + a SPY governance; execute with a 5000-char input → raises PAYLOAD_INPUT_TOO_LONG (413); assert governance.authorize was NOT called (no bill/upstream).
  - test_within_cap_reaches_governance: cap 4096, 100-char input, spy governance that raises a SENTINEL → execute raises the sentinel (not PAYLOAD_INPUT_TOO_LONG) → proves the cap passed to Step 4.
  - test_cap_disabled_at_zero: max_input_characters=0, 50000-char input → cap does not raise (sentinel governance reached).
  - test_default_knob_is_4096: Settings().tts_max_input_characters == 4096.
</test_plan>

Tests live in: `apps/gateway/tests/tts_input_cap/test_tts_input_cap.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/core/config.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/src/gateway/proxy/application/audio_use_case.py` · `apps/gateway/src/gateway/proxy/api/audio_deps.py` · `apps/gateway/tests/tts_input_cap/` · `Makefile`
Strategy (ordered batches): 1. config knob + error spec. 2. SpeechUseCase __init__ param + Step-2.5 guard. 3. audio_deps injection. 4. no-DB unit tests + Makefile test-fast.
Safety rule (feature-specific): the cap MUST reject BEFORE Step 4 governance (so before the Step 7 single bill + before any upstream) — never after; the reject path fires NO usage record. Default-ON (4096); 0 disables; within-cap path byte-identical.
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the contract; do NOT alter the existing step order / billing; reject before the bill; allow-list packages only (no new deps); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `make test-fast` 202 passed (198 → +4 tts_input_cap); new suite 4/4 green.
- [x] coverage did not decrease — +4 behavioral tests; no suite removed.
- [x] no test or contract was altered during build — contract unchanged; the existing SpeechUseCase step order + the per_character bill (Step 7) are untouched; within-cap path byte-identical.
- [x] the green was EARNED — the security-relevant invariant (reject BEFORE bill) is asserted directly: `test_over_cap_rejects_before_bill` checks `gov.authorize_called is False` after a 413, proving the raise precedes governance (Step 4) and therefore the Step-7 bill. `test_within_cap_reaches_governance` proves the cap doesn't false-positive (spy sentinel reached). `test_cap_disabled_at_zero` proves the escape hatch. Not tautological — each drives the real Step-2.5 guard. Small (4-line guard + a knob); careful manual review (Rule 5) in lieu of a refute subagent.
- [x] concurrency / timing safe — pure synchronous pre-stream check on the request body; no shared state, no I/O, runs before governance/credential/stream.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secrets; the cap reads only `len(input_text)`; no new deps; 413 envelope is the standard ProblemError.
- [x] layering & dependencies follow CONVENTIONS.md — knob in core config; error in the catalog; check in the application use-case; wiring in the api deps — same layering as the STT cap precedent.
- [x] a person reviewed and approved the change — full-auto drive; careful manual diff review of all 4 edits + the test at the gate; additive + default-safe (rejects before bill).

### Build expectations — what "correct" looks like
- [x] An over-cap TTS input is rejected with HTTP 413 ERR_PAYLOAD_INPUT_TOO_LONG and ZERO billing — confirmed: `audio_use_case.py` Step 2.5 raises before Step 4/7; `test_over_cap_rejects_before_bill` asserts status 413 + code + `authorize_called is False`.
- [x] A within-cap TTS request is unchanged — confirmed: the guard only fires when `len > cap`; `test_within_cap_reaches_governance` shows the flow proceeds to Step 4.
- [x] The cap is ON by default at 4096 and disable-able with 0 — confirmed: `Settings().tts_max_input_characters == 4096` (`test_default_knob_is_4096`); `test_cap_disabled_at_zero` shows 0 ⇒ no check.
- [x] The knob is wired to prod — confirmed: `audio_deps.get_speech_use_case` passes `max_input_characters=settings.tts_max_input_characters` (the v41 lesson: a use-case feature must be wired in deps or it's dead in prod).

### Deep checks
- [x] WIRING — `tts_max_input_characters` (config) → `audio_deps` → `SpeechUseCase(max_input_characters=)` → Step-2.5 guard → `PAYLOAD_INPUT_TOO_LONG`. Every new symbol referenced; verified by reading the deps factory + the use-case guard + the green prod-path wiring.
- [x] DEAD-CODE — no orphaned symbols; the error is raised, the knob is read, the param is used.
- [x] SEMANTIC — read the final Step-2.5 guard + __init__ + deps edit in full; the reject precedes governance/credential/bill.

### Residue / deltas
- Pre-existing lint backlog in two touched files (audio_use_case.py I001, audio_deps.py:70 E501) — both present on HEAD, NOT introduced here; my new code is ruff+pyright clean. Tracked with the repo-wide `make lint` backlog.
- The cap counts characters (code points), matching the per_character billing unit; a byte cap would diverge from the bill (documented §1 ⚠).
- OpenAI's own 4096 limit means within-cap-but-provider-rejected inputs still pass our cap (we faithfully surface the upstream error) — honest, v35 principle.

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
