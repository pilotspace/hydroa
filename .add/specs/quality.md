# Quality — the TDD spec

project: ai-proxy · seeded: 2026-07-24 · stage: production

> Living document — how we know it works: test strategy, floors, evidence (TDD).
> Keep the sections below CURRENT (state, not history); lessons land under
> Deltas the moment they are learned: `add.py delta-append tdd "<lesson>"`.
> A delta that changes the standing picture is folded UP into the sections
> above it and marked `[folded]` — the Deltas list is the inbox, not the spec.

## Now
<the standing TDD picture — replace this placeholder as the project firms; task-delta updates, never a full re-scan>

## Decisions that bind
<the TDD-lens decisions every task must honor — one line each, with the task/ADR that set it — or leave the placeholder until the first one lands>

## Deltas (newest first)
- [open · 2026-08-07] A red suite over PURE logic cannot see which inputs the production hook actually forwards to it. suite-infra-tripwire's InfraTripwire was 12/12 green while the conftest hook fed it only 'call' reports — blind to the 2130 SETUP errors that were the entire incident. When the unit under test is fed by an untestable seam (a pytest hook, a signal handler, a framework callback), the red suite needs a companion END-TO-END probe or the green is scoped to the wrong thing. (task:suite-infra-tripwire)
- [open · 2026-08-06] Verify the ARRANGE against the live environment and ASSERT it inside the fixture. vector-extension-preflight rests on 'a throwaway CREATE DATABASE has no vector extension'; that was checked against the real image (fresh db 0, template1 0, pg_available_extensions 1) instead of assumed, and the fixture now asserts count==0 before yielding. Without that assertion, an image that ever ships the extension in template1 would silently turn all five tests into vacuous passes — the same failure shape as the 2026-08-01 date bomb. (task:vector-extension-preflight)
- [open · 2026-08-06] A regression-arm test that asserts only 'the object exists' (assert app is not None) PASSES before the implementation exists — it is a green that proves nothing, and it survives the red gate because the other arms are red for it. Caught in vector-extension-preflight §4: the M4 arm passed while the module was absent. Fix: every arm, including the happy-path/regression one, must drive the REAL code path under test. For a fail-closed guard the happy-path arm matters MORE than the failure arm — a false positive is a total outage. (task:vector-extension-preflight)
- [open · 2026-07-25] A 'suppressions must be justified' rule needs a RATCHET, not a blanket assertion. lint-type-debt-sweep's M1 said 'written reason in every case' but its own guard only checked per-file-ignores, so the builder's own 3 bare noqas slipped through (caught by independent refute, MEDIUM). The codebase has 410 pre-existing bare noqas vs 143 justified — a blanket guard would either be turned off or explode a task's scope. Guard the delta (new/changed lines), grandfather the rest, and say so out loud. (task:lint-type-debt-sweep)
- [open · 2026-07-25] A static test that asserts a CONFIG file's shape is vacuous by default. Two of three parity tests in ci-restoration passed under attack until an independent refuter probed them: a gate step neutered with 'if: false' or planted in the WRONG job still satisfied a whole-file string match, and 'evil/pgvector-but-not-really:latest' satisfied a bare substring check. Rule: for config-shape assertions, (a) scope the parse to the exact job/section that would actually execute, (b) exclude conditional steps — a gate behind an 'if:' is not a gate, (c) strip comments before matching, (d) assert EQUALITY against a pinned literal, never a substring. Then RUN the attacks as proof rather than reasoning about them. (task:ci-restoration)
- [open · 2026-07-25] A security test whose ARRANGE depends on a FAIL-OPEN production path is unsound: when the fallback fires it produces the SAME observable signature as the vulnerability. Assert the confound away FIRST, with a message that says 'TEST CONFOUND, not a security failure'. (evidence: zdr-ingest-lock-heal — an independent refuter saw the pre-heal signature 'assert 4 == 0' once and could not reproduce it in 15 runs; cause was the attach router's fail-open inline drive racing the flip, not a lock gap) (task:zdr-ingest-lock-heal)
- [open · 2026-07-24] independent adversarial refute-reads caught 4 real defects 5 green suites missed: split-usage-frame stream zeroed billing (responses), a 20MiB body-cap masking the contracted 413 range (files), fromtimestamp overflow 500 vs contracted 422 (usage), + the shared-breaker cross-tenant availability coupling (moderations, Tin HARD-STOP→CR-1) — the earned-green refute is load-bearing, not ceremony (task:responses-state-store)
<!-- prepended by `add.py delta-append tdd "<text>"` — one line per lesson, `- [open · <date>] <lesson>` + the active-task stamp; fold a delta upward, then retag [open]->[folded] -->
