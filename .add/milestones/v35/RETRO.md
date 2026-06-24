════════════════════════════════════════════════════════════════════════
 v35 · Agent-loop error fidelity
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     4/4 done           CRITERIA  4/4 met
 GATES     4 PASS             WAIVERS   none

 goal  When an upstream provider fails (rate-limit, auth, 5xx,
       mid-stream), the proxy surfaces a faithful, actionable signal to
       the OpenAI-wire client — correct 429 + Retry-After on rate-limits
       and a well-formed terminal SSE error frame on streaming failures
       — so an agent loop (Helios) backs off or recovers instead of
       seeing a generic 502 or a hung stream.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 upstream-ratelimit-passthr… done      PASS 0     ●●●●●●●●●
 stream-upstream-error-frame done      PASS 0     ●●●●●●●●●
 error-fidelity-live-verify  done      PASS 0     ●●●●●●●●●
 stream-graceful-close-mapp… done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS (3 carried)
   • TDD · open · a realistic fake matters: the red-test fake's
     `stream()` raised INSIDE the generator body (not at the call) —
     that single fidelity choice is what forced the eager-peek design,
     because async-generator functions don't execute until iterated.
     Evidence: UC-3 could not pass on the default non-resilient path
     without the peek.
   • ADD · open · widening a frozen contract mid-freeze (Tin's "also
     handle alias-group") is a legitimate same-session change request;
     captured by re-editing §1-§5 before crossing to tests, not after.
     Evidence: alias-group musts added pre-tests.
   • SDD · open · a subclass error
     (`UpstreamRateLimitedError(UpstreamUnavailableError)`) is the clean
     way to add a NEW HTTP mapping without disturbing existing `except`
     sites — mirrors the AllDeploymentsSaturatedError→429 precedent.
     Evidence: 0 regression across 1524 tests.

 SPEC DELTAS    65 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v35
════════════════════════════════════════════════════════════════════════