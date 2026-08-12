════════════════════════════════════════════════════════════════════════
 v17 · Hardening — clear carried follow-up debt (v13/v14/v15)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     6/6 done           CRITERIA  6/6 met
 GATES     6 PASS             WAIVERS   none

 goal  every carried follow-up from v13/v14/v15 is cleared with zero
       behavioral regression on the existing floor: tests-bff is
       tsc-clean with scoped msw handlers, the two react-hooks lint
       rules are restored to error with their violations fixed,
       role-based nav visibility + the pre-auth OIDC callback relay
       ship, the dev-toolchain advisories are cleared, and the
       real-browser a11y+viewport pass runs

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 bff-test-harness-strict-ha… done      PASS 0     ●●●●●●●●●
 react-hooks-strict-lint     done      PASS 0     ●●●●●●●●●
 nav-role-filter             done      PASS 0     ●●●●●●●●●
 oidc-callback-relay         done      PASS 0     ●●●●●●●●●
 devtool-vitest4-upgrade     done      PASS 0     ●●●●●●●●●
 realbrowser-a11y-pass       done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (9 carried)
   • TDD · open · msw `onUnhandledRequest:"error"` in Node/jsdom does
     NOT reject the fetch — the interceptor resolves a 500 Response — so
     a forgotten handler LOGS loudly but a test that doesn't assert on
     that request still PASSES; "0 test failures" is NOT "0 leaks". The
     real monitor is the stderr unhandled-request COUNT (evidence: this
     task's 13→2→0 reduction; the suite stayed green at all three
     counts) (evidence: tests-bff/strict-harness.test.ts + the
     leak-count iterations).
   • ADD · open · an adversarial refute-read catches MIS-DIAGNOSIS, not
     just cheating: I labeled 2 residual leaks "benign cross-file
     late-resolves" and was WRONG — the sonnet reviewer traced them to
     in-file forgotten teams handlers in ui-ux-verify.test.tsx (fixed →
     0). Never hand-wave a residual; trace every leak to its source file
     (evidence: the EARNED-WITH-GAPS review + the ui-ux-verify.test.tsx
     beforeEach fix that zeroed the count).
   • TDD · open · tests-bff is now tsc-clean → a standing test-tree
     `typecheck` gate is newly possible; the v16 "production type-gate
     is next build, tests-bff excluded" delta can tighten to include the
     harness (evidence: `tsc --noEmit` 18→0 over tests-bff; this task
     discharges bff-test-harness-strict-handlers).
   • TDD · open · CPU-starved `vitest run --coverage` flakes
     timing-sensitive tests (axe ≥5s, in-flight `toBeDisabled` windows):
     3 false failures vanished on isolation + a 20s testTimeout
     (evidence: 3 fail under load → 37/37 green isolated in 2.15s →
     240/240 green with --testTimeout=20000). Fold candidate: the verify
     convention should run the floor with a generous testTimeout (or
     bounded workers) so a load flake never reads as a regression —
     `make test-fast` no-DB gate already exists; add a timeout floor.
   • ADD · open · the v16 "framework new-lint-rule error→warn is
     DECLARED+TICKETED, never permanent" convention now has a worked
     DISCHARGE template: fix behavior-preservingly (floor is the proof)
     → flip to error → pin with a config-text ratchet guard test
     (evidence: this task; mirrors v17 task 1 strict-harness.test.ts).
     The ratchet test is config-text-only by design; `eslint .` 0/0 is
     the real gate.
   • TDD · open · pre-existing AppShell harness leak: tests that render
     AppShell fire `useCurrentUser` (GET /api/auth/me) with no msw
     handler → up to 7 unhandled-request logs under load (0 when
     unloaded; NOT from this task — diff confirms auth/me untouched).
     Belongs to `nav-role-filter` (which owns useCurrentUser-based nav):
     stub /api/auth/me in the shared AppShell test setup to reach a true
     0-leak.
   • UDD · open · role-based nav visibility shipped: member hides
     {models,teams,routing}; the established pattern is `minRole` tags
     on a presentational shell + a thin client wrapper feeding
     useCurrentUser().role, fail-open (evidence:
     nav-role-filter.test.tsx 5/5; the UsagePage canEdit precedent
     generalized).
   • SDD · open · PRE-EXISTING (not this task): `GET /api/auth/me`
     decodes the session JWT WITHOUT signature verification (intentional
     — UX-only endpoint; the gateway verifies + enforces on every
     proxied request; the cookie is HttpOnly+SameSite=Strict so JS can't
     tamper). A spoofed role only changes nav chrome, never access. Fold
     candidate: add a one-line comment to app/api/auth/me/route.ts
     documenting the deliberate no-verify (adversarial review flagged
     the missing rationale, not a vulnerability). Owner: a future
     auth-hardening task — NOT in nav-role-filter's scope.
   • TDD · open · still-open (carried from react-hooks-strict-lint): the
     7 `/api/auth/me` unhandled-request leaks come from UsagePage tests
     rendering useCurrentUser without a per-test stub (NOT from this
     task — confirmed identical 7-count before & after). Per the
     strict-harness "no shared fallback" rule the fix is per-test stubs
     in the usage suites, a separate harness chore. Reach a true 0-leak
     there. (`DDD · SDD · UDD · TDD · ADD`), status `open`, with
     evidence. See the `add` skill's `deltas.md`. <!-- e.g. - [DDD ·
     open] the model missed multi-tenancy (evidence: scenario_x failed)
     -->

 DECIDE NEXT  consolidate learnings + archive-milestone v17
════════════════════════════════════════════════════════════════════════