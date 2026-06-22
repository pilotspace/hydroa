# TASK: Reject a non-finite or non-positive drift threshold at startup

slug: drift-threshold-validation · created: 2026-06-18 · stage: production
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
  - `apps/gateway/src/gateway/core/config.py:Settings.reconciliation_drift_threshold` — `Decimal = Decimal("0")` field (env `GATEWAY_RECONCILIATION_DRIFT_THRESHOLD`); 0 is the documented OFF default. NO validator today — Pydantic happily coerces `"inf"`/`"nan"` into `Decimal('Infinity')`/`Decimal('NaN')`.
  - `apps/gateway/src/gateway/core/config.py:Settings` (BaseSettings, line 82; env_prefix `GATEWAY_`) — siblings already use `@field_validator(...)classmethod -> ValueError("CODE: ...")` (see `_positive_weight`, `_positive_limit`, `_require_model_id`); add one here.
  - `apps/gateway/src/gateway/usage/application/drift_checker.py:should_start_drift_checker(threshold: Decimal, interval_seconds: int) -> bool` — returns `threshold > 0 and interval_seconds > 0`. The F6 bug: `Decimal('Infinity') > 0` is True → checker STARTS, but `unbilled_upstream_cost > inf` can never be True → monitor runs silently useless.
Context (working folder):
  - `apps/gateway/tests/test_config.py` — the Settings fail-fast test home (pattern: `pytest.raises(ValueError, match="GATEWAY_...")`); red test goes here.
  - `apps/gateway/tests/drift_alert/test_drift_alert.py` — existing `should_start_drift_checker` coverage (negative→off is asserted there; note negative is now caught upstream at config).
  - config.py L121-126 doc comment: "Both knobs default to the OFF position — the checker is only started when BOTH are > 0."
Honors (patterns / conventions):
  - PROJECT.md / global IO rule "design for failure": a nonsense config FAILS LOUD at startup, never silent-disables (the v27 empty-key boot-guard precedent: a misconfiguration is a startup error, not a per-request surprise).
  - CONVENTIONS.md: `ERROR_CODE: message` ValueError style on Settings validators (matches `_positive_weight` etc.).
  - 0 stays the legitimate OFF sentinel (default) — validation must NOT break the default boot.
Anchors the contract cites:
  - `Settings.reconciliation_drift_threshold` (the validated field)
  - a new `@field_validator("reconciliation_drift_threshold")` on `Settings`
  - error code `INVALID_RECONCILIATION_DRIFT_THRESHOLD`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Drift-threshold config validation — fail loud on a nonsense threshold
Source: [SPEC · open → this] drift-alert v29 §7 F6 — "reject a non-finite / ≤0 drift threshold at startup".
Framings weighed: config-level `@field_validator` on `Settings` (chosen — fails at construction = startup, one place, matches sibling validators) · a runtime guard inside `should_start_drift_checker` (rejected — silent-disables instead of failing loud, and the value still flows everywhere) · a lifespan boot check (rejected — later + further from the value than the field).
Must:
<must>
  - A finite threshold ≥ 0 is ACCEPTED: `0` stays the OFF sentinel (default boots unchanged); a positive finite value arms the monitor.
  - A non-finite threshold (`inf`, `-inf`, `nan`) is REJECTED at `Settings` construction (startup) — `inf` would pass `should_start`'s `>0` yet never fire (silently useless); `nan` silently disables. Both are nonsense.
  - A negative finite threshold (`< 0`) is REJECTED at `Settings` construction — a negative USD threshold is a typo, not a disable signal (0 is the disable signal).
</must>
Reject:
<reject>
  - threshold is non-finite (inf / -inf / nan) -> "INVALID_RECONCILIATION_DRIFT_THRESHOLD"
  - threshold is finite but < 0 -> "INVALID_RECONCILIATION_DRIFT_THRESHOLD"
</reject>
After:
<after>
  - `Settings.reconciliation_drift_threshold` is ALWAYS a finite Decimal ≥ 0 once construction succeeds.
  - `should_start_drift_checker` can never receive inf/nan/negative; the monitor is either usefully armed (`>0`) or cleanly OFF (`==0`) — never silently-useless.
  - The default boot (threshold unset → `Decimal("0")`) is byte-identical to today.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Rejecting NEGATIVE (not only non-finite) — lowest confidence because the `should_start_drift_checker` docstring today calls "zero or negative" a silent-OFF default; promoting negative to a loud boot error is a (tiny) behavior change. If wrong: an operator who set a negative value expecting silent-off now gets a startup error. Mitigation/decision: 0 remains the documented OFF; a negative USD threshold is overwhelmingly a typo → fail-loud is the correct reading of the design-for-failure rule. REJECT negative.
  - [x] `Decimal('NaN') > 0` is `False` (nan silent-disables) while `Decimal('Infinity') > 0` is `True` (inf silently-useless) — different failure modes, both nonsense; one "non-finite" clause (`not value.is_finite()`) covers both. Confirmed via Decimal IEEE semantics.
  - [x] Pydantic coerces env `"inf"`/`"nan"` into `Decimal('Infinity')`/`Decimal('NaN')` (not a parse error), so the validator must run AFTER coercion and use `.is_finite()`. Confirmed.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Default unset threshold boots OFF (byte-identical to today)
  Given no GATEWAY_RECONCILIATION_DRIFT_THRESHOLD is set
  When Settings() is constructed
  Then reconciliation_drift_threshold == Decimal("0")
  And should_start_drift_checker(0, interval) is False (monitor cleanly OFF)

Scenario: Positive finite threshold arms the monitor
  Given GATEWAY_RECONCILIATION_DRIFT_THRESHOLD="2.50"
  When Settings() is constructed
  Then reconciliation_drift_threshold == Decimal("2.50")
  And construction succeeds (no error)

Scenario: Infinite threshold is rejected at startup
  Given GATEWAY_RECONCILIATION_DRIFT_THRESHOLD="inf"
  When Settings() is constructed
  Then it raises ValueError matching "INVALID_RECONCILIATION_DRIFT_THRESHOLD"
  And no Settings object is produced (fail loud, not silent-useless)

Scenario: NaN threshold is rejected at startup
  Given GATEWAY_RECONCILIATION_DRIFT_THRESHOLD="nan"
  When Settings() is constructed
  Then it raises ValueError matching "INVALID_RECONCILIATION_DRIFT_THRESHOLD"
  And no Settings object is produced (fail loud, not silent-disable)

Scenario: Negative threshold is rejected at startup
  Given GATEWAY_RECONCILIATION_DRIFT_THRESHOLD="-1"
  When Settings() is constructed
  Then it raises ValueError matching "INVALID_RECONCILIATION_DRIFT_THRESHOLD"
  And no Settings object is produced (a negative USD threshold is a typo, not OFF)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Settings.reconciliation_drift_threshold : Decimal
  env  : GATEWAY_RECONCILIATION_DRIFT_THRESHOLD   default: Decimal("0")  (OFF sentinel — unchanged)

  @field_validator("reconciliation_drift_threshold")
  @classmethod
  def _validate_drift_threshold(cls, v: Decimal) -> Decimal:
      ACCEPT -> return v        when  v.is_finite() and v >= 0
      REJECT -> raise ValueError("INVALID_RECONCILIATION_DRIFT_THRESHOLD: "
                "must be a finite, non-negative USD amount (0 disables); got <v>")
              when  (not v.is_finite())  or  (v < 0)

  Effect: Pydantic wraps the ValueError into a ValidationError at Settings() construction
          → the gateway FAILS LOUD at startup (create_app reads Settings), never silent.

Schema: none — no DB, no migration, no HTTP surface. Pure config-construction guard.
No change to should_start_drift_checker (it keeps `threshold > 0 and interval > 0`); the
validator simply guarantees the value reaching it is always a finite Decimal ≥ 0.
```

Status: FROZEN @ v2 — approved under autonomy:auto (Tin pre-authorized "do all as recommended", 2026-06-18; low-risk, non-security)

§3 v2 mechanism clarification (behavior-preserving; observable UNCHANGED — discovered at tests-red):
  Pydantic 2.13 rejects a non-finite `Decimal` at the TYPE-coercion layer (`finite_number` error)
  BEFORE any `mode="after"` validator runs — so an after-validator never sees inf/nan and they would
  surface pydantic's generic message instead of the contracted code. To deliver the SAME observable
  (uniform `INVALID_RECONCILIATION_DRIFT_THRESHOLD` for inf/nan AND negative), the validator is
  `@field_validator(..., mode="before")`: it parses the raw value to Decimal, raises the coded
  ValueError when non-finite or < 0, else returns the value for normal coercion. The accept/reject
  conditions and the error code are exactly as frozen in v1; only the validator MODE changed.

Least-sure flag surfaced at freeze:
  ⚠ [spec] Rejecting NEGATIVE as well as non-finite (not only `inf`/`nan`). Why it could be wrong:
    `should_start_drift_checker`'s docstring treats "zero or negative" as a silent-OFF default, so this
    promotes a negative value from silent-off to a loud boot error. Cost if wrong: an operator who set a
    negative value to "disable" gets a startup failure instead. Decision: 0 remains the documented OFF;
    a negative USD threshold is a typo → fail-loud per the design-for-failure rule. (inf/nan rejection is
    unambiguous and carries no such risk.)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the new validator branches (accept · non-finite reject · negative reject).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_default_drift_threshold_is_zero_and_off: Settings() / assert ==Decimal("0") + should_start(0,60) is False
  - test_positive_drift_threshold_accepted: Settings(threshold="2.50") / assert ==Decimal("2.50") + should_start True
  - test_infinite_drift_threshold_rejected: Settings(threshold="inf") / raises ValueError match INVALID_RECONCILIATION_DRIFT_THRESHOLD
  - test_nan_drift_threshold_rejected: Settings(threshold="nan") / raises ... match INVALID_RECONCILIATION_DRIFT_THRESHOLD
  - test_negative_drift_threshold_rejected: Settings(threshold="-1") / raises ... match INVALID_RECONCILIATION_DRIFT_THRESHOLD
</test_plan>

RED result (uv run pytest tests/test_config.py): 3 failed, 4 passed — red for the RIGHT reason:
  - negative: DID NOT RAISE (accepted today — needs the validator).
  - inf/nan: raise pydantic's generic `finite_number` ValidationError, NOT the contracted code
    (the §3 v2 discovery — drives the mode="before" implementation).

Tests live in: `apps/gateway/tests/test_config.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/core/config.py`
Strategy (ordered batches): 1. add `InvalidOperation` import · 2. add `@field_validator("reconciliation_drift_threshold", mode="before")` `_validate_drift_threshold` on `Settings`.
Safety rule (feature-specific): mode="before" so the coded error precedes Pydantic's finite_number coercion; non-parseable input falls through to Pydantic's normal decimal error (no swallow); 0 stays the OFF sentinel.
Code lives in: `apps/gateway/src/gateway/core/config.py` (+ test in `apps/gateway/tests/test_config.py`)
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.
Built: import + 22-line `_validate_drift_threshold` before-validator. No new dependency (stdlib `decimal.InvalidOperation`).

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 7/7 `tests/test_config.py` green; fast no-DB suite exit 0; drift_alert no-DB unit tests 2/2 green (8 DB-bound errored on connection-refused only — env has no Postgres:5433, not a regression).
- [x] coverage did not decrease — added 5 tests covering all 3 new validator branches (accept / non-finite / negative).
- [x] no test or contract was altered during build — only `config.py` changed in build; the §3 v2 note is a behavior-preserving mechanism clarification recorded BEFORE build, not a build-time weakening.
- [x] the green was EARNED, not gamed — self refute-read (proportionate to a 22-line change): tests assert observable behavior (raises with the contracted code / value accepted / should_start flips), no fixture overfit, no vacuous asserts. Edge cases checked: `Decimal("0")` finite & not <0 → accepted (OFF preserved); `-inf` not finite → rejected; non-parseable "abc" → falls through to Pydantic's decimal error (not swallowed). Default value path: before-validators don't run on an unused default, but the default Decimal("0") is independently valid (test_default green).
- [x] concurrency / timing — N/A; pure synchronous config-construction guard, no IO/shared state.
- [x] no exposed secrets, injection openings, or unexpected dependencies — error includes only the operator's own threshold value (`{v!r}`), not a secret; stdlib `decimal.InvalidOperation` only (no new package).
- [x] layering & dependencies follow CONVENTIONS.md — sits with the sibling `Settings`/`Deployment` field_validators; same `CODE: message` ValueError style.
- [x] reviewed and approved — under autonomy:auto (Tin pre-authorized "do all as recommended"); low-risk, non-security → automated quality gate.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `_validate_drift_threshold` is registered via `@field_validator(...)` on `Settings` (pydantic invokes it; proven live by the 3 rejection tests firing). `InvalidOperation` import is used in the except clause.
- [x] DEAD-CODE (code) — no new unused/orphaned symbol; the one new method is a pydantic-invoked validator, the one new import is referenced.
- [ ] SEMANTIC (prose / non-code) — N/A (code task).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: autonomy:auto (Tin pre-authorized) · date: 2026-06-18

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): gateway boot-failure rate carrying `INVALID_RECONCILIATION_DRIFT_THRESHOLD` (a misconfigured deploy now fails loud — alert on it) · the distribution of configured threshold values.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
  - [SPEC · resolved-here] drift-alert v29 F6 "reject a non-finite / ≤0 drift threshold at startup" — CLOSED by this task (mode="before" validator on `Settings.reconciliation_drift_threshold`; inf/nan/negative → INVALID_RECONCILIATION_DRIFT_THRESHOLD; 0 stays OFF). The archived drift-alert §7 delta is the source.
  - [SPEC · open] the sibling knob `reconciliation_check_interval_seconds: int` has NO validator — a negative interval is silently treated as OFF by `should_start_drift_checker`. By the same fail-loud reasoning, consider rejecting `< 0` at startup (0 stays OFF). Lower urgency than the threshold (an int can't be inf/nan). (evidence: symmetry with this task; the interval is the other half of the same start-guard.)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
  - [ADD · folded] a frozen §3 can encode a wrong MECHANISM while its OBSERVABLE is right — the v1 pseudo-code put the guard in an after-validator, but Pydantic 2.13 rejects non-finite Decimals at type-coercion BEFORE after-validators run. TDD-red surfaced it; the fix was a behavior-preserving v2 mechanism clarification (mode="before"), NOT a contract weakening (the error code + accept/reject conditions were unchanged). A mechanism correction inside the bundle that preserves the observable is legitimate (evidence: tests-red showed pydantic's `finite_number` message instead of the contracted code). [folded foundation-version 28]
  - [TDD · folded] ground the framework's DEFAULT validation before specifying a guard against it — knowing Pydantic already rejects non-finite Decimals would have shaped the §3 mechanism up front and avoided the v2 round-trip (evidence: the §3 v2 discovery). [folded foundation-version 28]
