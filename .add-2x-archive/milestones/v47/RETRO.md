════════════════════════════════════════════════════════════════════════
 v47 · Realtime voice (turn-based WebSocket session)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     1/1 done           CRITERIA  1/1 met
 GATES     1 PASS             WAIVERS   none

 goal  An API key holder can hold a live voice conversation over a
       single WebSocket: stream audio in, get a transcript, an assistant
       reply, and synthesized audio back — reusing the v42 STT/TTS +
       chat pipeline with zero new dependency.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 realtime-voice              done      PASS 12†   ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 1/1 met

 LEARNINGS      none

 SPEC DELTAS    104 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v47
════════════════════════════════════════════════════════════════════════