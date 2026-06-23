# TASK: Reject a non-finite / non-positive drift threshold (and negative check-interval) at startup

slug: drift-threshold-validation · created: 2026-06-23 · stage: production
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

RE-GROUND (v33, 2026-06-23): the THRESHOLD half of this task ALREADY SHIPPED in v30 — `config.py:134 _validate_drift_threshold` (mode="before") rejects non-finite/<0 `reconciliation_drift_threshold` with `INVALID_RECONCILIATION_DRIFT_THRESHOLD`, covered by 5 tests in `tests/test_config.py:21-49`. The drift-alert §7 SPEC delta that seeded it was never marked resolved (stale-open). So this task's REMAINING work = the SIBLING knob: add a startup validator for `reconciliation_check_interval_seconds` (v33 exit criterion 1's "negative check-interval rejected at startup"; the open `[drift-threshold-validation]` interval delta).
Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/core/config.py:132 reconciliation_check_interval_seconds: int = 0` (env `GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS`) — 0 is the documented OFF default. NO validator today; a negative int is silently treated as OFF.
  - `apps/gateway/src/gateway/core/config.py:134 _validate_drift_threshold` — the SIBLING precedent to mirror (a finite/non-negative guard with a coded ValueError; mode="before" not needed here — an int has no inf/nan coercion problem, so a plain after-validator suffices).
  - `apps/gateway/src/gateway/usage/application/drift_checker.py:28 should_start_drift_checker(threshold, interval_seconds) -> bool` — `threshold > 0 and interval_seconds > 0`; a negative interval silently disables (the gap: nonsense config reads as a deliberate OFF). UNCHANGED by this task — the validator just guarantees interval >= 0 reaching it.
Context (working folder):
  - `apps/gateway/tests/test_config.py` — the Settings fail-fast test home; the red test goes here alongside the existing threshold tests (`pytest.raises(ValueError, match="...")`).
  - `apps/gateway/tests/drift_alert/test_drift_alert.py` — existing `should_start_drift_checker` coverage (negative→off asserted there; note negative interval is now caught upstream at config, but should_start's own contract is unchanged).
Honors (patterns / conventions):
  - PROJECT.md / global IO rule "design for failure" + the v33 shared decision: nonsense config FAILS LOUD at startup, never silent-disables. Symmetry with the already-shipped threshold validator.
  - CONVENTIONS.md: `ERROR_CODE: message` ValueError style on Settings field-validators (matches `_validate_drift_threshold`, `_positive_weight`, etc.).
  - 0 stays the legitimate OFF sentinel (default) — validation must NOT break the default boot.
Anchors the contract cites:
  - `Settings.reconciliation_check_interval_seconds` (the validated field)
  - a new `@field_validator("reconciliation_check_interval_seconds")` on `Settings`
  - error code `INVALID_RECONCILIATION_CHECK_INTERVAL`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Drift check-interval config validation — fail loud on a negative interval (symmetry with the shipped threshold guard)
Framings weighed: config-level `@field_validator` on `Settings` (chosen — fails at construction = startup, one place, mirrors the sibling `_validate_drift_threshold` and `_positive_*` validators) · a runtime guard inside `should_start_drift_checker` (rejected — silent-disables instead of failing loud, and the value still flows everywhere) · a lifespan boot check (rejected — later + further from the value than the field).
Must:
<must>
  - A non-negative integer interval is ACCEPTED: `0` stays the OFF sentinel (default boots unchanged); a positive value arms the monitor (when threshold is also > 0).
  - A negative integer interval (`< 0`) is REJECTED at `Settings` construction (startup) — a negative seconds interval is a typo, not a disable signal (0 is the disable signal), and today it silently reads as OFF via `should_start_drift_checker`'s `> 0`.
</must>
Reject:
<reject>
  - reconciliation_check_interval_seconds is a finite int < 0 -> "INVALID_RECONCILIATION_CHECK_INTERVAL"
</reject>
After:
<after>
  - `Settings.reconciliation_check_interval_seconds` is ALWAYS an int >= 0 once construction succeeds.
  - `should_start_drift_checker` can never receive a negative interval; the monitor is either usefully armed (`> 0`) or cleanly OFF (`== 0`) — never a silently-ignored negative.
  - The default boot (interval unset -> `0`) is byte-identical to today; `should_start_drift_checker`'s own `> 0` contract is UNCHANGED.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Rejecting NEGATIVE as a loud boot error (vs today's silent-OFF) — lowest confidence because `should_start_drift_checker`'s docstring treats "zero or negative" as a silent-OFF default, so this is a (tiny) behavior change. If wrong: an operator who set a negative value expecting silent-off now gets a startup error. Decision: 0 remains the documented OFF; a negative seconds value is overwhelmingly a typo -> fail-loud is the correct reading of design-for-failure, and it matches the already-shipped threshold validator's exact stance. REJECT negative.
  - [x] An `int` field cannot carry inf/nan (unlike the Decimal threshold), so a plain `mode="after"` validator suffices — no mode="before" needed; Pydantic coerces env strings to int first, and a non-int string raises Pydantic's normal int error (not swallowed). Confirmed.
  - [x] Pydantic coerces a negative env string `"-1"` to `int(-1)` without error (no built-in non-negative constraint on a bare `int`), so the validator is required to catch it. Confirmed.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Default unset interval boots OFF (byte-identical to today)
  Given no GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS is set
  When Settings() is constructed
  Then reconciliation_check_interval_seconds == 0
  And should_start_drift_checker(Decimal("1"), 0) is False (monitor cleanly OFF)

Scenario: Positive interval is accepted
  Given GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS="60"
  When Settings() is constructed
  Then reconciliation_check_interval_seconds == 60
  And construction succeeds (no error)

Scenario: Negative interval is rejected at startup
  Given GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS="-1"
  When Settings() is constructed
  Then it raises ValueError matching "INVALID_RECONCILIATION_CHECK_INTERVAL"
  And no Settings object is produced (a negative seconds interval is a typo, not OFF)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Settings.reconciliation_check_interval_seconds : int
  env  : GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS   default: 0  (OFF sentinel — unchanged)

  @field_validator("reconciliation_check_interval_seconds")   # mode="after" (default); int has no inf/nan
  @classmethod
  def _validate_check_interval(cls, v: int) -> int:
      ACCEPT -> return v        when  v >= 0
      REJECT -> raise ValueError("INVALID_RECONCILIATION_CHECK_INTERVAL: "
                "GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS must be a non-negative "
                "number of seconds (0 disables); got <v>")
              when  v < 0

  Effect: Pydantic wraps the ValueError into a ValidationError at Settings() construction
          -> the gateway FAILS LOUD at startup (create_app reads Settings), never silent.

Schema: none — no DB, no migration, no HTTP surface. Pure config-construction guard.
No change to should_start_drift_checker (it keeps `threshold > 0 and interval_seconds > 0`); the
validator simply guarantees the interval reaching it is always int >= 0. Mirrors the already-shipped
`_validate_drift_threshold` sibling (same CODE: message ValueError style).
```

Status: FROZEN @ v1 — approved under autonomy:auto (non-security, low-risk config guard; mirrors the v30-shipped threshold validator exactly)

Least-sure flag surfaced at freeze:
  ⚠ [spec] Rejecting NEGATIVE as a loud boot error rather than today's silent-OFF. Why it could be wrong:
    `should_start_drift_checker`'s docstring treats "zero or negative" as a silent-OFF default, so this
    promotes a negative value from silent-off to a loud boot error. Cost if wrong: an operator who set a
    negative value to "disable" gets a startup failure instead of silent-off. Decision: 0 remains the
    documented OFF; a negative seconds value is a typo -> fail-loud per design-for-failure, identical to the
    stance already shipped for the sibling threshold knob. (No inf/nan risk — an int can't carry them.)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the new validator branches (accept >= 0 · reject < 0).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_default_check_interval_is_zero_and_off: Settings() / assert ==0 + should_start_drift_checker(Decimal("1"),0) is False
  - test_positive_check_interval_accepted: Settings(reconciliation_check_interval_seconds="60") / assert ==60 (no error)
  - test_negative_check_interval_rejected: Settings(reconciliation_check_interval_seconds="-1") / raises ValueError match INVALID_RECONCILIATION_CHECK_INTERVAL
</test_plan>

RED result (uv run pytest tests/test_config.py): 1 failed, 9 passed — red for the RIGHT reason:
  - test_negative_check_interval_rejected: DID NOT RAISE (a negative interval is accepted today — needs the validator).
  - the two accept tests (default==0/off, positive==60) passed pre-build (the field already exists & defaults correctly).

Tests live in: `apps/gateway/tests/test_config.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/core/config.py`
Strategy (ordered batches): 1. add `@field_validator("reconciliation_check_interval_seconds")` `_validate_check_interval` on `Settings`, directly after the sibling `_validate_drift_threshold`.
Safety rule (feature-specific): plain after-validator (an int has no inf/nan coercion problem); 0 stays the OFF sentinel; a non-int env string still raises Pydantic's normal int error (not swallowed).
Code lives in: `apps/gateway/src/gateway/core/config.py` (+ tests in `apps/gateway/tests/test_config.py`)
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `tests/test_config.py` 10/10 green; full gateway suite green (single-process run, ignore tests/edge). drift_alert interval-consumer unit tests 10/10 green.
- [x] coverage did not decrease — added 3 tests covering both new validator branches (accept >=0 / reject <0).
- [x] no test or contract was altered during build — only `config.py` changed in build; the §3 contract froze v1 and was not touched (the E501 line-split in build is a formatting wrap of the SAME message, observable unchanged — match regex still hits).
- [x] the green was EARNED, not gamed — self refute-read (proportionate to a 14-line change mirroring a shipped sibling): tests assert observable behavior (raises with the contracted code / value accepted / default==0 stays OFF), no fixture overfit, no vacuous asserts. Edge cases: `0` -> accepted (OFF preserved, `0 < 0` False); a non-int env string still raises Pydantic's normal int error (validator runs after coercion, doesn't swallow); positive arms.
- [x] concurrency / timing — N/A; pure synchronous config-construction guard, no IO/shared state.
- [x] no exposed secrets, injection openings, or unexpected dependencies — error includes only the operator's own interval value (`{v!r}`), not a secret; no new import (uses the already-imported `field_validator`).
- [x] layering & dependencies follow CONVENTIONS.md — sits directly after the sibling `_validate_drift_threshold`; same `CODE: message` ValueError style.
- [x] reviewed and approved — under autonomy:auto; non-security, low-risk config guard mirroring v30-shipped code → automated quality gate.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] A negative interval (`-1`) raises a ValidationError carrying `INVALID_RECONCILIATION_CHECK_INTERVAL` at Settings() construction — confirmed by test_negative_check_interval_rejected (red→green).
- [x] The default boot (interval unset → 0) is byte-identical and reads as OFF via should_start_drift_checker — confirmed by test_default_check_interval_is_zero_and_off + the unchanged full suite.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `_validate_check_interval` is registered via `@field_validator("reconciliation_check_interval_seconds")` on `Settings` (pydantic invokes it; proven live by the rejection test firing).
- [x] DEAD-CODE (code) — no new unused/orphaned symbol; the one new method is a pydantic-invoked validator; no new import.
- [ ] SEMANTIC (prose / non-code) — N/A (code task).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: autonomy:auto · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): gateway boot-failure rate carrying `INVALID_RECONCILIATION_CHECK_INTERVAL` (a misconfigured deploy now fails loud — alert on it), alongside the sibling `INVALID_RECONCILIATION_DRIFT_THRESHOLD`.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
  - [SPEC · resolved-here] the `[drift-threshold-validation]` interval delta "reject `< 0` check_interval at startup" — CLOSED by this task (`_validate_check_interval` on `Settings.reconciliation_check_interval_seconds`; negative → INVALID_RECONCILIATION_CHECK_INTERVAL; 0 stays OFF).
  - [SPEC · resolved-stale] the threshold half (drift-alert F6) was already shipped in v30 (`_validate_drift_threshold`); its open delta was stale — fold/drop it as already-done.
  - [SPEC · open] the recovery_sweep interval knob (`should_start_recovery_sweep(interval_seconds)`) has the SAME negative-silent-OFF shape and no validator — consider the same fail-loud guard (evidence: symmetry with this task; recovery_sweep.py:50).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
  - [ADD · folded] a `phase: done` task stub that was never committed can be STALE — re-ground against the live code before reusing it: here the threshold half had already shipped in v30, so the task's real remaining scope was only the sibling interval knob (evidence: §0 RE-GROUND; config.py already had `_validate_drift_threshold` + its 5 tests). [folded foundation-version 30]
  - [TDD · folded] when extending a family of validators, mirror the sibling's EXACT error-code/message shape and assert via the same `pytest.raises(match=CODE)` pattern — the new test slotted beside the 5 existing threshold tests with zero new scaffolding (evidence: test_config.py v33 block mirrors the v30 block). [folded foundation-version 30]
