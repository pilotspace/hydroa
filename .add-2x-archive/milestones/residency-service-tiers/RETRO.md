════════════════════════════════════════════════════════════════════════
 residency-service-tiers · Data-residency & service-tier routing
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     7/7 done           CRITERIA  6/6 met
 GATES     7 PASS             WAIVERS   none

 goal  A tenant can pin inference to a region (EU via Bedrock/Vertex EU
       deployments) with a fail-closed residency policy, and buy
       priority-vs-standard service tiers with tier- and
       region-differentiated pricing — selling what Anthropic verifiably
       lacks (no first-party EU; US-pin monetized at 1.1x).
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 region-catalog-dimension    done      PASS 1†    ●●●●●●●●●
 residency-policy            done      PASS 0     ●●●●●●●●●
 region-pricing              done      PASS 0     ●●●●●●●●●
 service-tiers               done      PASS 0     ●●●●●●●●●
 residency-tiers-ui          done      PASS 0     ●●●●●●●●●
 vertex-adapter              done      PASS 2†    ●●●●●●●●●
 residency-bedrock-region-g… done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   region-catalog-dimension PASS Tin Dang <tindang.ht97@gmail.com>
   residency-policy         PASS Tin Dang <tindang.ht97@gmail.com>
   region-pricing           PASS Tin Dang <tindang.ht97@gmail.com>
   service-tiers            PASS Tin Dang <tindang.ht97@gmail.com>
   residency-tiers-ui       PASS Tin Dang <tindang.ht97@gmail.com>
   vertex-adapter           PASS Tin Dang <tindang.ht97@gmail.com>
   residency-bedrock-regio… PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS      none

 SPEC DELTAS    273 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              residency-service-tiers
════════════════════════════════════════════════════════════════════════