# TASK: Boot guard: reject a configured-yet-empty upstream API key

slug: empty-key-boot-guard · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Empty-key boot guard — at gateway startup, reject a configured-yet-EMPTY upstream
API key with a clear, fail-fast boot error, instead of the opaque downstream 500 ("Illegal
header value b'Bearer '") seen live in v7 + v8. The key insight: an UNSET env var and a
SET-but-empty one both resolve to `""` in Settings, so the guard must inspect raw os.environ
to draw the milestone's distinction — env var ABSENT = provider intentionally disabled
(allowed; dev/test default), env var PRESENT-but-empty/whitespace = a misconfiguration =
boot failure. Closes the v7 open follow-up (doubly evidenced v7+v8).

Framings weighed: a pure guard `validate_upstream_keys(env: Mapping[str,str])` reading the
raw environment, called once in create_app before the app serves (chosen — only raw os.environ
can tell PRESENT-empty from ABSENT; pure + injectable env = unit-testable with no env mutation;
one fail-fast raise at boot) · a pydantic field/model validator on Settings (rejected — it sees
the RESOLVED value `""`, not whether the var was present, so it cannot distinguish absent from
empty without re-reading os.environ anyway) · a per-provider construct-time check (rejected —
openrouter is built unconditionally and the others are already `if key:`-guarded; a per-site
check is scattered and still 500s for openrouter, not a boot guard).

Must:
<must>
  - At create_app/startup, for each known upstream key env var (GATEWAY_OPENROUTER_API_KEY,
    GATEWAY_OPENAI_API_KEY, GATEWAY_ANTHROPIC_API_KEY, GATEWAY_GOOGLE_API_KEY): if the var is
    PRESENT in the environment AND its value is empty or whitespace-only → raise a clear boot
    error naming the offending VAR (never its value), BEFORE the app serves a request.
  - An ABSENT env var is allowed: that provider stays cleanly disabled (openrouter default
    behavior + dev/test) — the guard does not require any key to be set.
  - A non-empty key passes unchanged; existing wiring (openrouter always; others `if key:`)
    is byte-identical when all present keys are non-empty.
</must>
Reject:
<reject>
  - GATEWAY_<PROVIDER>_API_KEY present but "" or whitespace -> raise "ERR_EMPTY_UPSTREAM_KEY"
    (a clear boot error: "<VAR> is set but empty; unset it to disable the provider or provide
    a non-empty key") — fail-fast at startup, never an opaque per-request 500.
</reject>
After:
<after>
  - The gateway either boots with every present upstream key non-empty, or fails fast with a
    message that names the misconfigured variable and how to fix it.
  - No upstream adapter is ever constructed with an empty key (the v7 empty-bearer class is
    eliminated at the boot boundary).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The "configured-yet-empty" case is observable ONLY by reading raw os.environ (present-vs-
    absent), because Settings collapses unset and set-empty to the same `""` — lowest confidence
    that this is the right SEAM (Settings-level vs env-level); if wrong (e.g. a deployment sets
    the var indirectly): the guard still catches the live failure mode (an exported empty var)
    and never false-positives on an unset var; cost of being slightly wrong is low (the guard is
    additive and fail-fast, not on the hot path).
  - [ ] The four known env var names are the complete upstream-key set today — confirm against
    config.py (openrouter/openai/anthropic/google); a new provider must add its var to the list
    (documented in the guard).
  - [ ] Raising at create_app (not lifespan) is early enough — confirm create_app runs before
    the first request in all entrypoints (uvicorn factory + tests); it does (app is built then
    served).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: present-but-empty key fails boot
  Given env has GATEWAY_OPENROUTER_API_KEY="" (present, empty)
  When validate_upstream_keys(env) runs at create_app
  Then it raises EmptyUpstreamKeyError naming GATEWAY_OPENROUTER_API_KEY
  And the message contains no key value (it is empty anyway) and a fix hint

Scenario: whitespace-only key fails boot
  Given env has GATEWAY_GOOGLE_API_KEY="   "
  When the guard runs
  Then it raises EmptyUpstreamKeyError naming GATEWAY_GOOGLE_API_KEY

Scenario: absent key is allowed (provider disabled)
  Given env does NOT contain GATEWAY_ANTHROPIC_API_KEY
  When the guard runs
  Then it does NOT raise (the provider stays cleanly disabled)

Scenario: non-empty keys pass
  Given every present GATEWAY_*_API_KEY is non-empty
  When the guard runs
  Then it returns None and create_app proceeds unchanged

Scenario: create_app fails fast on an empty key
  Given GATEWAY_OPENROUTER_API_KEY="" in the process env
  When create_app(settings) is called
  Then it raises EmptyUpstreamKeyError BEFORE building adapters / serving
  And no upstream adapter is constructed with an empty key

Scenario: error message never leaks a secret
  Given any present-but-empty key
  When the guard raises
  Then the message contains only the VAR NAME + a fix hint, never a key value
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
gateway.core.config — boot-time guard (NOT an HTTP error; a fatal startup exception)

NEW  class EmptyUpstreamKeyError(ValueError)   # raised at boot only; no HTTP mapping

NEW  _UPSTREAM_KEY_ENV_VARS: Final[tuple[str, ...]] = (
       "GATEWAY_OPENROUTER_API_KEY", "GATEWAY_OPENAI_API_KEY",
       "GATEWAY_ANTHROPIC_API_KEY", "GATEWAY_GOOGLE_API_KEY",
     )

NEW  validate_upstream_keys(env: Mapping[str, str] | None = None) -> None
       env defaults to os.environ. For each name in _UPSTREAM_KEY_ENV_VARS:
         name in env AND env[name].strip() == ""  -> raise EmptyUpstreamKeyError(
           f"{name} is set but empty; unset it to disable the provider or provide a "
           f"non-empty key")
       name absent  -> skip (provider intentionally disabled)
       non-empty    -> skip
       Returns None when all present keys are non-empty. Pure (env injectable); the
       message contains ONLY the VAR NAME + fix hint, never a key value.

CHANGED  gateway.main.create_app(settings) — call validate_upstream_keys() at the TOP
       (before constructing any upstream adapter), so an empty key fails fast at boot.

Schema: none (no DB, no HTTP surface). Wiring unchanged when all present keys are non-empty.
Scope: boot path only. Adapter construction, request handling, and the openrouter-always /
others-`if key:` wiring are byte-identical for valid configs.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [spec] the right SEAM is env-level (raw os.environ)
rather than Settings-level — because Settings collapses unset and set-empty to `""`, only
os.environ can tell "configured-yet-empty" (boot failure) from "absent" (disabled, allowed);
why it could be wrong: a deployment might inject the var indirectly; cost if wrong: low — the
guard is additive + fail-fast, catches the exact live failure mode (an exported empty var),
and never false-positives on an unset var.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — 7 tests, one per scenario + the two create_app boot paths.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_present_empty_key_raises: env {OPENROUTER:""} → EmptyUpstreamKeyError naming the var
  - test_whitespace_only_key_raises: env {GOOGLE:"   "} → raises naming GOOGLE
  - test_absent_key_is_allowed: env without any upstream key → returns None
  - test_nonempty_keys_pass: all present keys non-empty → returns None
  - test_error_message_has_fix_hint_and_no_value: message has var + "unset", no echoed value
  - test_create_app_fails_fast_on_empty_key: monkeypatch OPENROUTER="" → create_app raises
  - test_create_app_ok_when_keys_absent: delenv all four → create_app builds normally
</test_plan>
Red result (pre-build): collection ImportError (EmptyUpstreamKeyError / validate_upstream_keys
absent) — red for the RIGHT reason (missing implementation), all 7 blocked.

Tests live in: `tests/empty_key_boot_guard/test_empty_key_boot_guard.py` · ran red before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): fail CLOSED at boot — an empty key raises before any adapter
is constructed, so the v7 empty-bearer class is eliminated at the boundary; the error names
ONLY the variable + a fix hint, never a key value (secret hygiene).
Code lives in: `apps/gateway/src/gateway/core/config.py`, `apps/gateway/src/gateway/main.py`
Constraints: do NOT change the contract; stdlib only (os, collections.abc, typing); no new
dependency; wiring byte-identical for valid configs.

Built (all green, 52/52 blast radius):
- `EmptyUpstreamKeyError(ValueError)` — fatal boot exception, no HTTP mapping.
- `_UPSTREAM_KEY_ENV_VARS` (the 4 provider key vars) + `validate_upstream_keys(env=None)` —
  pure, env-injectable; raises naming the present-but-empty var; absent/non-empty → skip.
- `create_app` calls `validate_upstream_keys()` at the TOP (before adapters).

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — empty_key_boot_guard 7/7; blast radius 52/52 (gemini_provider, anthropic_provider, provider_chat_dispatch wiring suites still green — settings-level wiring unaffected by the env-level guard)
- [x] coverage did not decrease — additive guard + one create_app line; every branch (present-empty / whitespace / absent / non-empty / create_app fail-fast / create_app ok) covered
- [x] no test or contract was altered during build — frozen §3 honored; no existing test edited (the env-level guard does not touch settings-level wiring tests)
- [x] concurrency / timing of the risky operation is safe — pure synchronous boot check, runs once before serving; no shared state, no IO
- [x] no exposed secrets, injection openings, or unexpected dependencies — error names ONLY the var + fix hint (test asserts no value echoed); stdlib only, no new dep
- [x] layering & dependencies follow CONVENTIONS.md — guard lives in core/config alongside Settings; create_app calls it at the composition root; no cross-layer leak
- [x] a person reviewed and approved the change — delegated auto mode (Tin Dang, 2026-06-13)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `validate_upstream_keys` imported + called at create_app top (line ~168); `EmptyUpstreamKeyError` raised by the guard + asserted in 5 tests; `_UPSTREAM_KEY_ENV_VARS` iterated; all exercised
- [x] DEAD-CODE (code) — no orphan: the guard is on the boot path (every create_app call), the error type is raised+caught in tests, the var tuple is the loop source
- [x] SEMANTIC (prose / non-code) — read the guard + create_app call in full: env defaults to os.environ, present-AND-empty-after-strip is the only raise condition, message carries var+hint only, called before any adapter construction (fail-fast confirmed by test_create_app_fails_fast)

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): boot-failure rate with EmptyUpstreamKeyError (a deploy
misconfig signal), and the ABSENCE of the v7/v8 "Illegal header value b'Bearer '" 500 in
live logs (the guard should make it extinct).
Spec delta for the next loop: a new upstream provider MUST add its key var to
_UPSTREAM_KEY_ENV_VARS — consider deriving the list from the Settings field metadata so it
can't drift (a follow-up; the explicit tuple is fine for 4 providers today).

### Competency deltas
- [ADD · open] some misconfigurations are observable ONLY at the raw-environment level, not
  the parsed-config level — Settings collapses unset and set-empty to "", so a boot guard that
  must tell "disabled" from "misconfigured" reads os.environ directly (evidence: the
  Settings-level wiring tests stay green while the env-level guard catches present-empty).
- [SDD · open] a fail-fast boot guard at the composition root converts an opaque per-request
  500 into a clear startup error naming the fix — the v7+v8 empty-bearer class is eliminated at
  the boundary rather than handled per-adapter (evidence: create_app raises before any adapter;
  7/7 green incl. the two create_app boot-path tests).
