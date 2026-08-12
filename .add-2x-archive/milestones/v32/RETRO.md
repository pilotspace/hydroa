════════════════════════════════════════════════════════════════════════
 v32 · Writable routing configuration
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     3/3 done           CRITERIA  3/3 met
 GATES     3 PASS             WAIVERS   none

 goal  an owner edits model-groups, routing strategy, and per-deployment
       limits from the dashboard and the changes persist (apply on
       restart)

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 routing-config-write        done      PASS 0     ●●●●●●●●●
 routing-config-store        done      PASS 5†    ●●●●●●●●●
 routing-config-editor       done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 3/3 met

 LEARNINGS      none

 SPEC DELTAS    38 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v32
              1 planned not yet scaffolded:
              routing-config-write-endpoint
════════════════════════════════════════════════════════════════════════