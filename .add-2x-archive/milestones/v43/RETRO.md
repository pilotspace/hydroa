════════════════════════════════════════════════════════════════════════
 v43 · Remote sessions / conversations
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     2/2 done           CRITERIA  2/2 met
 GATES     2 PASS             WAIVERS   none

 goal  A signed-in user (and any API key holder) can create, list,
       resume, and delete persistent conversation threads — messages
       survive reload/device via a tenant-scoped gateway store, surfaced
       in the dashboard chat.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 conversations-backend       done      PASS 0     ●●●●●●●●●
 chat-history-ui             done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 2/2 met

 LEARNINGS      none

 SPEC DELTAS    104 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v43
════════════════════════════════════════════════════════════════════════