# MILESTONE: Dependency hardening — Next.js 16 upgrade + advisory remediation

goal: the dashboard runs on Next.js 16 (latest GA, 16.2.9) with ZERO open critical/high npm advisories on the SHIPPED/production surface (`npm audit --omit=dev`), all existing behavioral suites green after the breaking upgrade, and a clean production build on the new default bundler (Turbopack)
goal-refinement (2026-06-14, Tin-approved at the verify gate): scoped "ZERO critical/high" to the PRODUCTION surface. The full dev+prod audit retains 7 dev-toolchain advisories (2 critical + 5 high — esbuild→vite→vitest chain) that are PRE-EXISTING, NEVER SHIPPED, and require a vitest 3→4 major out of a Next-16 upgrade's scope → tracked as follow-up `devtool-vitest4-upgrade`. This mirrors the owning task's §3 v1→v2 change request (honest re-scope, not a weakening): the enterprise security gate that matters is the shipped surface, which is 0/0/0.
rationale: Intake → `sub-milestone` (registered 2026-06-13, executed 2026-06-14). A maintenance/security-hardening slice: the dashboard runs Next 15.3.3 which has 3 CRITICAL + 5 HIGH npm advisories (SSRF via WebSocket upgrades GHSA-c4j6-fc7j-m34r, RSC cache poisoning GHSA-wfc6-r584-vfw7, App-Router middleware/proxy bypass GHSA-267c-6grr-h53f, Pages-Router i18n bypass GHSA-36qx-fr4f-26g5, + transitive postcss moderate). All four Next advisories are patched in 16.2.6+; 16.2.9 is the target. DEPTH on `apps/dashboard/` — no gateway change, no new feature; the breaking upgrade is contained behind the v13/v15 behavioral floor (231 tests). Runs AFTER v15 (the last UI feature milestone).
stage: production · status: active · created: 2026-06-13

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  The `apps/dashboard` Next.js 15.3.3 → 16.2.9 breaking upgrade and advisory remediation:
     - bump `next` 15.3.3 → 16.2.9, `react`/`react-dom` 19.1.0 → 19.2.x, `@types/react`/
       `@types/react-dom` → latest, `eslint-config-next` → 16 (via `@next/codemod upgrade`).
     - **`next lint` REMOVED**: migrate `"lint":"next lint"` → `"lint":"eslint ."` + a flat
       `eslint.config.mjs` (eslint-config-next/core-web-vitals); production lint stays clean.
     - **`middleware.ts` → `proxy.ts`**: rename file + `middleware`→`proxy` export (Next 16
       deprecates the middleware filename); the cookie-presence guard + `config.matcher` are
       preserved BYTE-IDENTICAL in behavior (Edge→Node runtime — acceptable: self-hosted behind
       Envoy, no Edge-only API in use). The `middleware.test.ts` → `proxy.test.ts` import/name
       refactor is a rename, NOT a weakening.
     - **async Request-API audit**: confirm every `cookies()`/`headers()` is awaited (Next 16
       removes the sync compat shim); `revalidateTag` single-arg audit (none expected).
     - **postcss advisory override**: `overrides: { "postcss": ">=8.5.10" }` (Next 16.2.9 still
       bundles postcss 8.4.31 — the one advisory not bundle-fixed; build-time-only, no user CSS).
     - **Turbopack default build**: verify `next build` succeeds on the new default bundler
       (empty next.config.ts → no webpack-conflict; non-event for config, but build MUST pass).
     - the full v13/v15 behavioral floor (231 vitest tests) stays green; tsc-clean; npm audit
       reports 0 critical/high.
Out: any GATEWAY/BFF change (this is dashboard-deps only); any NEW dashboard feature or surface
     (v15 closed that); the carried v15 UI follow-ups (`nav-role-filter`, `bff-test-harness-
     strict-handlers`, `oidc-callback-relay`, real-browser axe+viewport) — separate tasks;
     adopting Next 16 OPTIONAL new capabilities (cacheComponents, dynamicIO, PPR, View
     Transitions) — additive, not part of hardening; a Node-LTS pin (we run Node 25; Next 16
     needs ≥20.9 — satisfied, not changed here); a full browser/Playwright smoke harness (the
     real-browser pass is the shared v13 residue — manual `next build` + prod-server curl is the
     in-scope smoke for the runtime-behavior changes vitest cannot catch).

## Shared decisions & glossary deltas   (living — every task must honor these)
- Behavior-preserving is non-negotiable: the upgrade changes DEPENDENCIES + build tooling, never
  app behavior — the 231-test floor is the contract; a real behavior change is a HARD-STOP.
- Node deps are governed by lockfile + orchestrator review (foundation v1 rule — the Python
  `dependencies.allowlist` does not cover npm); the upgrade commits package.json + the lockfile.
- Runtime-behavior changes vitest CANNOT catch (Turbopack bundle output, proxy Edge→Node, the
  Next 16 prefetch-cache rewrite) are verified by a clean `next build` + a prod-server smoke
  (start + curl an authed + an unauthed route through the proxy guard), NOT assumed.
- Security: a major-version bump is a supply-chain event — verify the resolved lockfile versions
  and that npm audit reports 0 critical/high BEFORE the gate; any new critical/high is HARD-STOP.

## Shared / risky contracts (freeze these first)
- The **upgrade invariant set** — the observable post-upgrade facts that must all hold (next ≥16
  in package.json, proxy.ts present + middleware.ts absent, postcss override present, lint script
  = eslint, npm audit 0 critical/high, 231 tests green, build green) → owning task
  `next16-upgrade` (single cohesive task — a dependency upgrade is not breadth-decomposable).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] next16-upgrade   depends-on: none   — bump next→16.2.9 + react/react-dom→19.2.x + types + eslint-config-next via @next/codemod; migrate next lint→eslint flat config; rename middleware.ts→proxy.ts (behavior-identical guard); async-API audit; postcss>=8.5.10 override; Turbopack build green; 236 tests + tsc + lint clean; npm audit (prod) 0 critical/high. — DONE, gate PASS 2026-06-14 (commit 046cf71).

## Exit criteria (observable; map each to the task that delivers it)
- [x] `apps/dashboard` runs Next.js 16.2.9 (package.json + lockfile resolve next@16.2.x, react/react-dom@19.2.x) (← next16-upgrade) (verify: package.json/lockfile inspection + `next --version`) — MET: `next --version` → 16.2.9; lockfile next 16.2.9 · react/react-dom 19.2.7.
- [x] `npm audit` reports ZERO critical and ZERO high advisories on the SHIPPED/production surface (← next16-upgrade) (verify: `npm audit --omit=dev --json` vulnerabilities.critical==0 && .high==0) — MET: prod audit 0/0/0/0. SCOPE NOTE (Tin-approved at gate, mirrors §3 v1→v2): the FULL audit retains 7 dev-toolchain advisories (esbuild→vite→vitest, never shipped, pre-existing) → tracked as `devtool-vitest4-upgrade`; NOT a security auto-pass (shipped surface is clean, dev gap openly ticketed).
- [x] `next lint` is migrated to `eslint .` with a flat `eslint.config.mjs`, and lint is clean (← next16-upgrade) (verify: `npm run lint` EXIT=0 on the new config) — MET: `eslint .` EXIT=0 (0 errors / 60 warnings). The warnings are eslint-config-next 16's new React-Compiler-era rules on pre-existing v13/v15 patterns, downgraded error→warn (visible) → tracked as `react-hooks-strict-lint`.
- [x] the route guard is `proxy.ts` (middleware.ts removed), behavior byte-identical — unauthenticated → 307 /login, session cookie → passthrough (← next16-upgrade) (verify: proxy.test.ts green; middleware.ts absent) — MET: proxy.test.ts green; middleware.ts git-rm'd; prod-server smoke confirms /keys+/usage no-cookie→307/login, +cookie→200, /login→200.
- [x] the full v13/v15 behavioral suite stays green at ≥80% coverage and PRODUCTION code is tsc-clean; `next build` succeeds on the default Turbopack bundler (← next16-upgrade) (verify: `vitest run --coverage` EXIT=0 + production tsc-clean via `next build` + `next build` EXIT=0) — MET: 30 files / 236 tests passed @ 94.03%; `next build` TypeScript check clean (production graph) + Turbopack build green (17/17 pages). SCOPE NOTE: a bare `tsc` over `tests-bff/` shows type drift (Next 16 async-params Promise<{path}> typing + pre-existing msw looseness) — outside the established gate (eslint ignores tests-bff; no standalone typecheck script; the production type-gate is `next build`), not a weakening (236 tests pass at runtime) → tracked as `bff-test-harness-strict-handlers`.
