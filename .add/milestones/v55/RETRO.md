════════════════════════════════════════════════════════════════════════
 v55 · Capability-aware model management
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     4/4 done           CRITERIA  6/6 met
 GATES     4 PASS             WAIVERS   none

 goal  Every catalog model declares the input types it accepts, and any
       request carrying an input type the resolved model cannot handle
       is rejected with a clear structured 4xx before it reaches the
       provider.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 model-input-capabilities    done      PASS 5†    ●●●●●●●●●
 unsupported-input-guard     done      PASS 9†    ●●●●●●●●●
 artifact-upload-validation  done      PASS 0     ●●●●●●●●●
 capabilities-admin-surface  done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   model-input-capabilities PASS Tin Dang <tindang.ht97@gmail.com>
   unsupported-input-guard  PASS Tin Dang <tindang.ht97@gmail.com>
   artifact-upload-validat… PASS Tin Dang <tindang.ht97@gmail.com>
   capabilities-admin-surf… PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS      none

 SPEC DELTAS    208 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v55
════════════════════════════════════════════════════════════════════════