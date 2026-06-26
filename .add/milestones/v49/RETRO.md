════════════════════════════════════════════════════════════════════════
 v49 · Durable video-job processing (Redis queue + restart recovery)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     1/1 done           CRITERIA  1/1 met
 GATES     1 PASS             WAIVERS   none

 goal  A video-generation job survives a gateway restart: jobs are
       enqueued to the existing Redis, processed by an in-process
       worker, and any job orphaned by a restart is recovered and
       re-driven — with bounded retries and at-least-once idempotency —
       reusing the existing Redis with zero new infra.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 durable-video-queue         done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 1/1 met

 LEARNINGS (2 carried)
   • ADD · folded · a frozen contract can hide an off-by-one footgun the
     build implements faithfully — the `> max_retries` cap with
     increment-on-every-drive silently failed a fresh job at the (valid)
     max_retries=0 config; caught only by reading the cap against the
     codebase's "0 = unlimited" convention at the verify gate, not by
     the green suite. Lesson: at the gate, test a cap/knob at its
     boundary (0, 1) against the project's other knobs, not just the
     happy default. (evidence: test_max_retries_zero_is_unlimited, added
     in review) [folded foundation-version 37]
   • TDD · folded · the durable-worker correctness surface
     (at-least-once + recovery + retry) needed a `process_once()` TEST
     SEAM to make a concurrent BRPOP loop deterministically assertable —
     a run_forever loop is otherwise untestable without sleep-racing.
     (evidence: 8 tests drive the worker via process_once) [folded
     foundation-version 37]

 SPEC DELTAS    106 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v49
════════════════════════════════════════════════════════════════════════