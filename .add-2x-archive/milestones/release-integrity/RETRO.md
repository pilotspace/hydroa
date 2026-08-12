════════════════════════════════════════════════════════════════════════
 release-integrity · Release integrity: restore CI, close the deploy blockers, clear lint/type/suite debt
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     12/12 done         CRITERIA  6/6 met
 GATES     9 PASS 3 RISK      WAIVERS   3

 goal  Every merge to main is proven by a green CI run on the merged
       artifact — no admin-merge — and a pgvector-bearing release can be
       deployed to managed Postgres from a written runbook without index
       or extension surprises
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 ci-restoration              done      RISK 10†   ●●●●
 lint-type-debt-sweep        done      PASS 30†   ●●●●
 suite-stability             done      RISK 31†   ●●●●
 pgvector-deploy-runbook     done      RISK 12†   ●●●●
 vector-extension-preflight  done      PASS 0     ●●●●
 suite-infra-tripwire        done      PASS 12†   ●●●●
 breaker-4xx-classification  done      PASS 3†    ●●●●
 date-bomb-sweep             done      PASS 7†    ●●●●
 release-provenance          done      PASS 5†    ●●●●
 ci-timeout-and-e2e-scope    done      PASS 10†   ●●●●
 ci-flake-classification     done      PASS 10†   ●●●●
 flake-tail-burndown         done      PASS 30†   ●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   ci-restoration           RISK Tin Dang <tindang.ht97@gmail.com>
   lint-type-debt-sweep     PASS Tin Dang <tindang.ht97@gmail.com>
   suite-stability          RISK Tin Dang <tindang.ht97@gmail.com>
   pgvector-deploy-runbook  RISK Tin Dang <tindang.ht97@gmail.com>
   vector-extension-prefli… PASS Tin Dang <tindang.ht97@gmail.com>
   suite-infra-tripwire     PASS Tin Dang <tindang.ht97@gmail.com>
   breaker-4xx-classificat… PASS Tin Dang <tindang.ht97@gmail.com>
   date-bomb-sweep          PASS Tin Dang <tindang.ht97@gmail.com>
   release-provenance       PASS Tin Dang <tindang.ht97@gmail.com>
   ci-timeout-and-e2e-scope PASS Tin Dang <tindang.ht97@gmail.com>
   ci-flake-classification  PASS Tin Dang <tindang.ht97@gmail.com>
   flake-tail-burndown      PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 WAIVERS (3)
   • ci-restoration: Tin Dang · todos #68/#69 + release-integrity
     MILESTONE.md exit criteria · expires 2026-08-15
   • suite-stability: Tin Dang · todo #81 (unreproduced
     catalog_refresh_scheduler stall) + todo #80 (azure egress DNS) ·
     expires 2026-09-30
   • pgvector-deploy-runbook: Tin Dang · M3 operator walkthrough on a
     real target · expires 2026-09-30

 LEARNINGS      none

 SPEC DELTAS    2 open deltas — resolve: new-task --from-delta (or close in §7)

 DECIDE NEXT  consolidate learnings + archive-milestone
              release-integrity
              1 planned not yet scaffolded: dashboard-lint-gate
════════════════════════════════════════════════════════════════════════