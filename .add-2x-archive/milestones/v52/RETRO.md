════════════════════════════════════════════════════════════════════════
 v52 · Full-duplex realtime voice relay (provider-agnostic seam)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     5/5 done           CRITERIA  5/5 met
 GATES     5 PASS             WAIVERS   none

 goal  A browser holds a full-duplex voice session over a
       provider-agnostic /v1/realtime/relay WS that is relayed
       bidirectionally to a real provider realtime API (OpenAI Realtime
       or Gemini Live) behind a swappable seam, with auth-over-WS,
       design-for-failure on the pump, and honest-degrade when no
       provider is configured.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 realtime-relay-port         done      PASS 10†   ●●●●●●●●●
 openai-realtime-adapter     done      PASS 0     ●●●●●●●●●
 gemini-live-adapter         done      PASS 0     ●●●●●●●●●
 realtime-relay-endpoint     done      PASS 0     ●●●●●●●●●
 realtime-relay-live-verify  done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 5/5 met

 LEARNINGS      none

 SPEC DELTAS    162 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v52
════════════════════════════════════════════════════════════════════════