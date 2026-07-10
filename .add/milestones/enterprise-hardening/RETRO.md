════════════════════════════════════════════════════════════════════════
 enterprise-hardening · Enterprise Hardening
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     7/7 done           CRITERIA  7/7 met
 GATES     7 PASS             WAIVERS   none

 goal  Every confirmed blocking defect in the 2026-07-02
       enterprise-readiness diagnostic — revenue-integrity, resilience,
       realtime governance, and security — is fixed, tested red→green,
       and verified.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 cache-alias-billing         done      PASS 0     ●●●●●●●●●
 usage-flusher-durability    done      PASS 11†   ●●●●●●●●●
 realtime-relay-governance   done      PASS 36†   ●●●●●●●●●
 edge-input-hardening        done      PASS 0     ●●●●●●●●●
 signup-and-routing-authz    done      PASS 0     ●●●●●●●●●
 provider-circuit-breakers   done      PASS 0     ●●●●●●●●●
 tiered-rate-cards           done      PASS 1†    ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   cache-alias-billing      PASS Tin Dang <tindang.ht97@gmail.com>
   usage-flusher-durability PASS Tin Dang <tindang.ht97@gmail.com>
   realtime-relay-governan… PASS Tin Dang <tindang.ht97@gmail.com>
   edge-input-hardening     PASS Tin Dang <tindang.ht97@gmail.com>
   signup-and-routing-authz PASS Tin Dang <tindang.ht97@gmail.com>
   provider-circuit-breake… PASS Tin Dang <tindang.ht97@gmail.com>
   tiered-rate-cards        PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 7/7 met

 LEARNINGS (8 carried)
   • SDD · open · A "bound EVERY X call" Must must be verified against
     EACH call in the path, not the one the pseudocode illustrates —
     first build wrapped only XADD and left the three advisory
     `incrbyfloat` awaits bare, partially missing the frozen §1
     B4-timeout Must; caught by the pre-gate advisor review, not by
     tests (no test asserted the advisory-call timeout). Evidence:
     recorder.py advisory block.
   • TDD · open · "best-effort" and "bounded" are orthogonal properties
     — a call can be both. The §3 pseudocode's "advisory stays
     best-effort" was mis-read as "advisory is timeout-exempt". Add a
     scenario/test that asserts a hung advisory call is bounded (not
     just swallowed) so this can't regress silently. Evidence: the gap
     was invisible to the green suite.
   • DDD · open · Gemini Live re-bills the FULL cumulative context every
     turn (growing per-turn promptTokenCount is real spend, not a
     double-count bug) — fold into PROJECT.md billing-precision notes so
     a future engineer doesn't "fix" it. (evidence: live forum + docs
     re-verified at VERIFY)
   • DDD · open · A `Permission`-shaped RBAC gate cannot express
     "excludes tenant OWNER" under this matrix's own completeness guard
     (`ROLE_PERMISSIONS[Role.OWNER] == frozenset(Permission)`) — any
     genuinely operator-wide (non-tenant-scoped) resource needs a
     role-only gate (`require_superadmin` or equivalent), never a new
     `Permission` enum member, no matter how the feature request is
     worded ("a dedicated permission"). Worth stating explicitly in
     CONVENTIONS.md or authz.py's own docstring so a future task doesn't
     attempt the structurally-impossible path this draft ruled out
     (evidence: §1 Framings weighed Part B).
   • ADD · open · A previously HARD-STOP-cleared, Tin-approved security
     freeze (`routing-config-write`) can still need reversal when its
     own STATED premise (here: "single-operator/trusted-owner
     deployment") is invalidated by later, unrelated shipped work
     (multi-tenant SaaS features landing over the following two weeks).
     The SUPERSESSION pattern handled this cleanly — record the reversal
     at the new task's freeze, never silently re-edit the old frozen
     file (evidence: §0/§3 SUPERSESSION record).
   • ADD · open · a diagnostic "single X breaker" headline conflated two
     fixes with different blast radii; grounding split them — reinforces
     "ground before you size" (evidence: B3 fix#1/fix#2 split).
   • TDD · open · a green suite that holds a mutable input STATIC across
     the whole test cannot see time-of-check/time-of-settlement drift;
     for any value now made mutable by a new write API, add a "changed
     mid-flight" test (evidence: C1 was invisible to 11 scenario + 104
     regression tests).
   • SDD · open · a new self-service WRITE API silently widens the blast
     radius of PRE-EXISTING read-time semantics elsewhere (recovery
     re-resolve; env.py autogen); a build's grounding should scan "what
     does making X mutable newly expose?" not just "does X compute
     right" (evidence: C1 + C2 both pre-existing, both newly reachable
     via this task).

 SPEC DELTAS    272 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              enterprise-hardening
════════════════════════════════════════════════════════════════════════