════════════════════════════════════════════════════════════════════════
 proxy-correctness · Proxy Correctness
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     1/1 done           CRITERIA  4/4 met
 GATES     1 PASS             WAIVERS   none

 goal  Every gateway provider adapter maps provider responses to the
       OpenAI-compatible wire shape faithfully per the providers'
       published API docs — closing the real docs-vs-code deltas found
       by the adapter audit (finish_reason/stop_reason completeness,
       Anthropic in-stream error surfacing, OpenAI STT passthrough),
       with the Bedrock SigV4 'CRITICAL' recorded as a verified
       false-positive.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 adapter-correctness-fixes   done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   adapter-correctness-fix… PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS      none

 SPEC DELTAS    212 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              proxy-correctness
════════════════════════════════════════════════════════════════════════