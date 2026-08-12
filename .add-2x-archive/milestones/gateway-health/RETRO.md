════════════════════════════════════════════════════════════════════════
 gateway-health · Gateway Health
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     3/3 done           CRITERIA  4/4 met
 GATES     3 PASS             WAIVERS   none

 goal  Restore the gateway's static-quality gates and test suite to
       fully green: make lint (ruff check + format) clean, make
       typecheck (pyright) clean, and the 3 pre-existing failing tests
       (azure_embeddings credential-fixture ×2, guardrails-core stale
       table invariant) fixed — so make ci passes end-to-end once CI
       billing returns. Pre-existing debt accumulated while CI was
       billing-blocked; surfaced by the v0.6.0 e2e/harden pass.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 pyright-errors-clean        done      PASS 0     ●●●●●●●●●
 ruff-format-and-lint-clean  done      PASS 0     ●●●●●●●●●
 fix-stale-failing-tests     done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   pyright-errors-clean     PASS Tin Dang <tindang.ht97@gmail.com>
   ruff-format-and-lint-cl… PASS Tin Dang <tindang.ht97@gmail.com>
   fix-stale-failing-tests  PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS      none

 SPEC DELTAS    212 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone gateway-health
════════════════════════════════════════════════════════════════════════