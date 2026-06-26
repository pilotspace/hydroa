════════════════════════════════════════════════════════════════════════
 v48 · Video generation (async jobs)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     2/2 done           CRITERIA  2/2 met
 GATES     2 PASS             WAIVERS   none

 goal  An API key holder can submit a text-to-video generation request,
       get a job id back immediately, poll the job's status, and
       download the resulting video when it succeeds — reusing the
       artifacts store for the result and an in-process job lifecycle,
       with the real video-gen provider as a credential-gated delta.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 video-jobs-backend          done      PASS 0     ●●●●●●●●●
 video-jobs-ui               done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 2/2 met

 LEARNINGS      none

 SPEC DELTAS    104 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v48
════════════════════════════════════════════════════════════════════════