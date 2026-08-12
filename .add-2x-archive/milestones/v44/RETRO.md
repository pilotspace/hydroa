════════════════════════════════════════════════════════════════════════
 v44 · Remote memory (tenant-scoped semantic memory store)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     2/2 done           CRITERIA  2/2 met
 GATES     2 PASS             WAIVERS   none

 goal  A user (and any API key holder) can store facts/notes as
       'memories' and retrieve them by semantic similarity via a
       tenant-scoped gateway store, surfaced in the dashboard — the
       second 'remote' platform capability after sessions.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 memory-backend              done      PASS 0     ●●●●●●●●●
 memory-ui                   done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 2/2 met

 LEARNINGS      none

 SPEC DELTAS    104 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v44
════════════════════════════════════════════════════════════════════════