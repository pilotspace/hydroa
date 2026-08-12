════════════════════════════════════════════════════════════════════════
 v40 · Chat workspace + streaming
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     4/4 done           CRITERIA  2/2 met
 GATES     4 PASS             WAIVERS   none

 goal  A signed-in user can hold a multi-turn, live-streaming
       conversation with any catalog model from an in-dashboard chat
       workspace that shows each turn's token cost.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 streaming-bff               done      PASS 0     ●●●●●●●●●
 chat-workspace-page         done      PASS 0     ●●●●●●●●●
 chat-model-controls         done      PASS 0     ●●●●●●●●●
 chat-cost-readout           done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 2/2 met

 LEARNINGS (1 carried)
   • ADD · open · §5 Scope must declare the §4 red-test file too, not
     just src — the scope-gate reads `anchor.declared` (frozen at the
     tests→build crossing from the live §5 line), so a test-file touch
     during a verify→build heal loop reads as a scope_violation until
     you re-cross tests→build to rebirth the anchor (evidence:
     streaming-bff gate, 2 heal attempts spent).

 SPEC DELTAS    104 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v40
════════════════════════════════════════════════════════════════════════