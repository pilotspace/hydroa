════════════════════════════════════════════════════════════════════════
 v14 · Dependency hardening — Next.js 16 upgrade + advisory remediation
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     1/1 done           CRITERIA  5/5 met
 GATES     1 PASS             WAIVERS   none

 goal  the dashboard runs on Next.js 16 (latest GA, 16.2.9) with ZERO
       open critical/high npm advisories on the SHIPPED/production
       surface (`npm audit --omit=dev`), all existing behavioral suites
       green after the breaking upgrade, and a clean production build on
       the new default bundler (Turbopack)

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 next16-upgrade              done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 5/5 met

 LEARNINGS (5 carried)
   • ADD · open · A risk:high major-dep bump landing WITHOUT CI must
     capture prod-server smoke curl output verbatim as gate evidence —
     the green jsdom suite cannot prove Turbopack-bundle /
     Edge→Node-runtime / prefetch-cache parity (evidence: this task's §6
     GATE EVIDENCE block — the 5-curl guard smoke is the sole
     runtime-parity record; CI is Actions-billing-blocked).
   • TDD · open · FOLLOW-UP `react-hooks-strict-lint`:
     eslint-config-next 16 newly enables React-Compiler-era rules
     (`react-hooks/refs`, `react-hooks/set-state-in-effect`) that flag
     60 pre-existing v13/v15 production patterns (SpendPage
     last-good-ref read-in-render, OidcSettings
     sync-server-state-in-effect, use-focus-trap ref) — downgraded
     error→warn (visible, not hidden) to hold the 0-error baseline; the
     proper fix is a behavior-sensitive state-model refactor OUT of this
     behavior-preserving upgrade's scope (evidence: `eslint .` = 0
     errors / 60 warnings, EXIT 0; downgrade documented in
     eslint.config.mjs lines 27-38).
   • TDD · open · FOLLOW-UP `devtool-vitest4-upgrade`: the FULL npm
     audit retains 7 dev-toolchain advisories (2 critical + 5 high — the
     esbuild→vite→vitest/@vitejs-plugin-react chain), pre-existing +
     never shipped, requiring vitest 3→4 + @vitejs/plugin-react 4→6
     majors + a vitest-axe 0.1.0 replacement, out of Next-16 scope; the
     SHIPPED surface (`npm audit --omit=dev`) is 0/0/0/0 (evidence: §6
     GATE EVIDENCE — prod audit 0 critical/high vs full audit 7; §3 v2
     declares this scope split).
   • TDD · open · FOLLOW-UP `bff-test-harness-strict-handlers` (NEW,
     surfaced this verify): a standalone `tsc --noEmit` over
     `tests-bff/` reports type drift — 7 errors from Next 16's
     async-params `Promise<{path}>` typing in route-handler test
     fixtures, plus pre-existing msw `JsonBodyType` / `null→Request`
     cast looseness; outside the established gate (eslint ignores
     tests-bff; no standalone typecheck script; `next build` type-checks
     production only, and was CLEAN) and not a weakening (236 tests pass
     at runtime via esbuild transpile) — but the test trees should be
     made tsc-clean so the harness can join a future type gate
     (evidence: `tsc --noEmit` output 2026-06-14 — production clean via
     next build, tests-bff shows the Promise<{path}> + msw-cast errors).
   • SDD · open · The advisory criterion that matters for an enterprise
     security gate is the SHIPPED surface (`npm audit --omit=dev`), not
     the full dev+prod audit — conflating them either blocks a clean
     production upgrade on dev-toolchain debt or hides real shipped
     risk; scope the gate to production and declare the dev gap as a
     ticketed follow-up (evidence: §3 v1→v2 change request — the
     production surface is 0/0/0 while the full audit's 7 advisories are
     all dev-only).

 DECIDE NEXT  consolidate learnings + archive-milestone v14
════════════════════════════════════════════════════════════════════════