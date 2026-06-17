════════════════════════════════════════════════════════════════════════
 v26 · Provider config cleanup — v25 follow-ups
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     2/2 done           CRITERIA  2/2 met
 GATES     2 PASS             WAIVERS   none

 goal  the BYOK provider seam carries no dead config: OpenAI direct-chat
       has retry parity with the other 5 adapters, and the vestigial
       env-key boot guard is fully retired

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 openai-retry-parity         done      PASS 3†    ●●●●●●●●●
 retire-empty-key-guard      done      PASS 4†    ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 2/2 met

 LEARNINGS (2 carried)
   • TDD · open · class-level attribute defaults are the clean seam to
     extend an adapter's ctor without breaking a sibling task's
     `__new__`-built test doubles (evidence: kept frozen
     openai_chat_dispatch green).
   • ADD · open · when retiring dead code whose tests doubled as weak
     invariant guards, re-express the invariant against a live surface
     (Settings.model_fields) rather than deleting the assertion
     (evidence: this task).

 DECIDE NEXT  consolidate learnings + archive-milestone v26
════════════════════════════════════════════════════════════════════════