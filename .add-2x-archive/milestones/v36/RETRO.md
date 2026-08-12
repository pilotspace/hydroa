════════════════════════════════════════════════════════════════════════
 v36 · Per-key bandwidth pacing
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     4/4 done           CRITERIA  6/6 met
 GATES     4 PASS             WAIVERS   none

 goal  Each API key's concurrent requests are paced to a configured
       tokens/sec ceiling via an aggregate Redis token-bucket with
       bounded-wait backpressure (pace, then 503 + Retry-After),
       default-OFF and fail-open.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 bandwidth-token-bucket      done      PASS 1†    ●●●●●●●●●
 stream-bandwidth-pacing     done      PASS 0     ●●●●●●●●●
 bandwidth-usage-reconcile   done      PASS 0     ●●●●●●●●●
 bandwidth-counter-view      done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (4 carried)
   • TDD · open · a bounded-wait pacing loop is made DETERMINISTIC by
     injecting an epoch-ms clock + patching asyncio.sleep to ADVANCE
     that clock — real-Redis Lua stays exercised (now_ms is an ARGV)
     while wall-time is removed (evidence: the 14-test suite runs in
     <1.3s against real Redis, no flakiness).
   • SDD · open · when a refute-read's worst-case rests on an unphysical
     assumption (a token deficit that "stays 1 forever" despite refill
     closing it each slice), FIX the underlying defect anyway if the fix
     is strictly-more-correct + harmless, but record the corrected
     severity rather than the reviewer's headline (evidence: acquire()
     actual-slept budgeting fix; 50000-iter case bounded to ~1 slice by
     refill). See the `add` skill's `deltas.md`. <!-- e.g. - [DDD ·
     open] the model missed multi-tenancy (evidence: scenario_x failed)
     -->
   • TDD · open · a fake that RECORDS the value under test makes
     assertions that mirror the impl — pin ABSOLUTE expected integers
     computed independently (here from chunk byte-lengths), or a
     systematic-scaling bug stays invisible (evidence: refute-read MAJOR
     on this task).
   • ADD · open · a mid-task contract change (Tin:
     also-reconcile-on-disconnect) must sweep ALL sections — §0 ground
     notes + §1 reject + §2 scenarios + §3 + §4, not just the must-list
     (evidence: caught stale "shed/disconnect final" §0 lines
     post-freeze). <!-- e.g. - [DDD · open] the model missed
     multi-tenancy (evidence: scenario_x failed) -->

 SPEC DELTAS    76 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v36
════════════════════════════════════════════════════════════════════════