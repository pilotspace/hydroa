════════════════════════════════════════════════════════════════════════
 enterprise-domain-onboarding · Enterprise domain onboarding: unified email-domain routing + auto-assign + domain-claims console
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     3/3 done           CRITERIA  6/6 met
 GATES     3 PASS             WAIVERS   none

 goal  A business's users are automatically routed into their company
       tenant by verified email domain across both signup and SSO login
       from one source of truth, with self-service domain management in
       the dashboard, while self-signup into a tenant stays
       invite-or-verified-domain only
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 domain-routing-unification  done      PASS 0     ●●●●●●●●●
 domain-auto-assign-login    done      PASS 0     ●●●●●●●●●
 domain-claims-console       done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   domain-routing-unificat… PASS Tin Dang <tindang.ht97@gmail.com>
   domain-auto-assign-login PASS Tin Dang <tindang.ht97@gmail.com>
   domain-claims-console    PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (3 carried)
   • TDD · open · when a redesign short-circuits the code path a legacy
     regression test targeted (claim-first bypassing the
     resolver-collision path), the assertion can stay green while going
     INERT — verify the net property coverage MOVED (here: no-500 now
     proven by test_db_oidc_resolver_deterministic), don't trust the
     green (evidence: collision_dos LAYER-2, verifier #1).
   • ADD · open · a change-request that NARROWS a frozen contract
     mid-build must reconcile the §3 contract PROSE too, not just the §1
     Must rules — the §3 code-block drifted (said env "DELETED" while M4
     said retained) until a verifier caught it (evidence: verifier #2
     CONCERN#1).
   • ADD · open · a low-effort executor (fable/low) can implement a
     well-pinned red suite AND correctly HARD-STOP on a 50-test
     cross-task-drift casualty rather than weaken tests — the tight red
     suite + explicit "do not edit tests" constraint carried the safety,
     not the model tier (evidence: v1 build STOP, then sonnet handled
     the delicate legacy reconciliation).

 SPEC DELTAS    279 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              enterprise-domain-onboarding
════════════════════════════════════════════════════════════════════════