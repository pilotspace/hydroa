════════════════════════════════════════════════════════════════════════
 v42 · Voice breadth (multi-provider STT/TTS)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     4/4 done           CRITERIA  4/4 met
 GATES     4 PASS             WAIVERS   none

 goal  A signed-in user can transcribe speech and synthesize voice
       through more than one provider (incl. Azure OpenAI audio), with a
       TTS input ceiling and a dashboard voice surface — beyond today's
       OpenAI-only audio.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 azure-audio-provider        done      PASS 1†    ●●●●●●●●●
 tts-input-guardrails        done      PASS 1†    ●●●●●●●●●
 audio-translations-endpoint done      PASS 0     ●●●●●●●●●
 voice-playground            done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS      none

 SPEC DELTAS    104 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v42
════════════════════════════════════════════════════════════════════════