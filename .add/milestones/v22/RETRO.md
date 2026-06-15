════════════════════════════════════════════════════════════════════════
 v22 · Provider security & config hardening
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     2/2 done           CRITERIA  2/2 met
 GATES     2 PASS             WAIVERS   none

 goal  Every provider adapter's transport-error wrap suppresses the
       secret-bearing exception chain (from None), and Azure AD
       authority is env-configurable — closing the two v21 carried
       follow-ups; behavior-preserving, regression-guarded.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 provider-secret-chain-hard… done      PASS 0     ●●●●●●●●●
 azure-ad-authority-config   done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 2/2 met

 LEARNINGS (2 carried)
   • DDD · open · a partially-wired seam can hide for a whole milestone:
     AzureADConfig.authority + _token_url() consumed the authority since
     v21, but resolve never sourced it — the field looked configurable
     but wasn't. Evidence: the carries-authority test was the only thing
     that exposed the gap. Lesson: an end-to-end test (settings→URL)
     catches "looks wired, isn't" that a unit test on either end misses.
   • TDD · open · keeping the invariant guards (default-fallback,
     gating-unchanged) GREEN from the start while only the new-behavior
     tests go RED cleanly separates "new capability" from
     "must-not-regress" in the same suite. Evidence: 2 red / 3 green at
     the red run, all 5 green after a 2-line change.

 DECIDE NEXT  consolidate learnings + archive-milestone v22
════════════════════════════════════════════════════════════════════════