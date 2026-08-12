════════════════════════════════════════════════════════════════════════
 v37 · Dashboard observability parity
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     3/3 done           CRITERIA  5/5 met
 GATES     3 PASS             WAIVERS   none

 goal  The dashboard renders the operator read-side surfaces recent
       backend milestones shipped without UI — per-key bandwidth levels,
       routing-config save feedback + validation, and SSO repeat-login
       polish — so operators see and act on existing endpoints without
       curl.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 bandwidth-panel             done      PASS 0     ●●●●●●●●●
 routing-editor-feedback     done      PASS 0     ●●●●●●●●●
 sso-login-polish            done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 5/5 met

 LEARNINGS (7 carried)
   • TDD · open · a render-only "null→unknown" cell needs a companion
     ZERO test — null and 0 are distinct truths and a `!value` falsy
     refactor silently merges them; pin both (evidence: refute-read
     MINOR on this task, added
     test_bandwidth_zero_level_is_not_unknown).
   • UDD · open · mirroring an APPROVED sibling component
     (RatelimitsPanel) collapses the UDD design loop to "reuse" —
     inherit its four-state recipe + a11y region pattern verbatim rather
     than re-deriving a design (evidence: this panel shipped with zero
     new design decisions beyond the frozen disabled-caption). <!-- e.g.
     - [DDD · open] the model missed multi-tenancy (evidence: scenario_x
     failed) -->
   • UDD · open · when a STATIC always-on hint already exists, a "saved"
     CONFIRMATION is a SEPARATE transient affordance (role=status) that
     must survive the save's own refetch-reseed but clear on the next
     user edit — gate it on user-edit handlers, NOT on a [data]-dep
     effect (evidence: refute attack-vector 1; reseed uses setGroups
     directly so saved survives).
   • TDD · open · a client-side guard test must assert BOTH the inline
     error TEXT and that the network call never fired (a spy flag) —
     asserting only "an alert appeared" passes vacuously on any
     unrelated alert (evidence: refute MINOR on
     test_routing_blank_model_blocked, strengthened). <!-- e.g. - [DDD ·
     open] the model missed multi-tenancy (evidence: scenario_x failed)
     -->
   • ADD · open · `redirect:"manual"` surfaces a configured 3xx as
     opaqueredirect (status 0) in browsers but a readable 302 under
     node/undici+msw — gate on "NOT a 4xx" (`status>=400 && <500`),
     never `status===302`, so the same code is correct in both runtimes
     (evidence: §4 NOTE; tests mock a 302 arm).
   • TDD · open · a shared component tested by TWO vitest projects
     (`|bff|` lacks a full localStorage) forces defensive storage
     accessors (typeof-guard + try/catch) — a browser-only API touched
     at mount must degrade, not throw (evidence: bff project
     `localStorage.getItem is not a function` until guarded).
   • UDD · open · a localStorage seed must read in an effect (not a lazy
     useState initializer) to stay SSR-safe; the
     `react-hooks/set-state-in-effect` lint flags it → a single-line
     scoped disable directly above the setState (multi-line directive
     misses the target line) (evidence: directive on the
     comment-continuation line read as "unused"). <!-- e.g. - [DDD ·
     open] the model missed multi-tenancy (evidence: scenario_x failed)
     -->

 SPEC DELTAS    83 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v37
════════════════════════════════════════════════════════════════════════