════════════════════════════════════════════════════════════════════════
 v28 · Billing & passthrough robustness
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     3/3 done           CRITERIA  3/3 met
 GATES     3 PASS             WAIVERS   none

 goal  every streamed or transcription call that consumed real upstream
       work is billed or explicitly flagged — no remaining silent $0,
       and no non-finite value can enter the ledger or the response body

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 stream-disconnect-billing   done      PASS 0     ●●●●●●●●●
 stt-duration-cap            done      PASS 2†    ●●●●●●●●●
 stt-nonfinite-passthrough   done      PASS 1†    ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 3/3 met

 LEARNINGS (8 carried)
   • TDD · open · `gen.athrow(asyncio.CancelledError)` / `gen.aclose()`
     are the DETERMINISTIC way to unit-test an async generator's
     disconnect/cancellation billing — they inject
     GeneratorExit/CancelledError at the exact suspended yield with no
     real-task race, far more reliable than create_task+cancel+sleep.
     Evidence: DC1/DC7 deterministic single-shot; the spy records during
     the injected teardown.
   • ADD · open · the freeze's lowest-confidence flag (fire-and-forget
     flushing from INSIDE GeneratorExit handling) was PROVEN by making
     the test itself the falsifier (DC1/DC4 can only go green if the
     record fires) — a "the test is the proof of the risky assumption"
     pattern, not a hand-wave. Evidence: DC1/DC4 red→green is exactly
     the timing proof.
   • ADD · open · CARRIED RESIDUE (refute-read NIT-3, untestable in
     unit): the real uvicorn loop-teardown on a production client
     disconnect is not proven by the unit suite — the independent-task
     architecture mitigates it, but an e2e/live check (disconnect a real
     stream, assert a client_disconnect ledger row) would close it.
     Evidence: refute-read 0.93 discount was entirely this scenario.
     <!-- e.g. - [DDD · open] the model missed multi-tenancy (evidence:
     scenario_x failed) -->
   • TDD · open · a WARN asserted only by event-name substring (`in
     caplog.text`) silently permits contract drift in the WARN's
     payload; assert the LogRecord's `extra` fields via `caplog.records`
     when the contract specifies them (evidence: refute-read NIT-2 —
     DCAP1/DCAP2 passed without the contracted `{model, original, cap}`
     until strengthened).
   • TDD · open · a constructor-default unit assert (`uc._max_dur… is
     None`) pins the wiring but not the execute-time behavior; pair it
     with a billed-outcome test for the same path when cheap (evidence:
     refute-read NIT-1 — DCAP7 covers the default, DCAP3/DCAP5 cover the
     no-clamp bill).
   • ADD · open · the working-tree engine added an `unflagged_freeze`
     gate requiring the literal `Least-sure flag surfaced at freeze:`
     label + a `[part]` tag; prose like "Lowest-confidence flag" no
     longer parses (evidence: tests→build refused until the §3 marker
     matched `_FLAG_LABEL_RE`).
   • ADD · open · a frozen test from a CLOSED milestone can legitimately
     go stale when a later task fixes a behavior that test DEFERRED —
     the principled handling is a tests-phase STRENGTHENING (preserve
     the true invariant, update only the stale scaffold), surfaced as a
     Spec delta, never a silent build-time weakening (evidence: v27
     test_sd8 `raises(ValueError)` → 200 + null; its docstring
     pre-authorized the follow-up).
   • TDD · open · pinning a deferred/out-of-scope concern with a test
     that asserts the CURRENT (buggy) behavior AND names the follow-up
     in its docstring turns it into an executable breadcrumb that fails
     loudly the moment the follow-up lands — forcing the update instead
     of a silent drift (evidence: test_sd8's scope note surfaced the v28
     behavior change in the full suite, not in review).

 SPEC DELTAS    4 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v28
════════════════════════════════════════════════════════════════════════