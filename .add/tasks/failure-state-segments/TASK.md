# TASK: Graceful error/loading/not-found segments for app + marketing routes

slug: failure-state-segments · created: 2026-06-26 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- (NONE today) — `find app -name error.tsx/loading.tsx/not-found.tsx/global-error.tsx` = EMPTY. A thrown render error or a 404 currently hits Next's default (dev overlay / bare 500). This task adds the App-Router special files.
- (NEW) `apps/dashboard/components/ui/route-error.tsx` : `RouteError({ error, reset, surface? })` Client Component — composes the existing `ErrorState`; renders GENERIC copy + an optional `error.digest`; NEVER renders `error.message`/stack (leak guard); `reset()` on the retry button.
- (NEW) `apps/dashboard/app/(app)/app/error.tsx` · `(marketing)/error.tsx` · `(auth)/error.tsx` : thin `"use client"` default-export wrappers delegating to `RouteError` (per route-group boundary).
- (NEW) `apps/dashboard/app/(app)/app/loading.tsx` : Suspense fallback using `Loading` (role=status).
- (NEW) `apps/dashboard/app/not-found.tsx` : 404 surface + a link home.
- (NEW) `apps/dashboard/app/global-error.tsx` : self-contained root boundary (renders its OWN <html><body> — root layout is unavailable here); generic copy + reset; no leak.

Context (working folder):
- `components/ui/states.tsx` (REUSE): `Loading` (role=status + aria-busy), `Empty`, `ErrorState` (role="alert", caller-supplied title + onRetry). Frozen observable markers.
- Next.js 16 App Router: error.tsx/global-error.tsx MUST be Client Components with `({ error: Error & { digest?: string }, reset: () => void })`; not-found.tsx/loading.tsx may be Server Components. Route groups `(app)/(marketing)/(auth)` — an error.tsx in a group folder wraps that group's routes.
- Tests: `tests/` + `tests-bff/` (vitest+RTL+jsdom). These are plain React components → render directly.

Honors (patterns / conventions):
- v13 state pattern (loading/empty/error rendered identically across surfaces) — segments REUSE `states.tsx`, no new visual language.
- Aurora design language (ui-fidelity): tokens/`cn`, lucide icons, focus-visible ring.
- SECURITY (the risky bit): an error boundary must NOT surface `error.message`/stack to the user (could leak internals/secrets) — show generic copy + the safe `digest` only. This is the freeze's lowest-confidence point.

Anchors the contract cites: `RouteError` (new), `ErrorState`/`Loading` (reused), the Next special-file names (`error.tsx`/`global-error.tsx`/`not-found.tsx`/`loading.tsx`), the no-leak invariant.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: App-Router failure-state segments (error / global-error / not-found / loading) reusing the state primitives
Framings weighed: shared RouteError client component + thin per-group wrappers (chosen) · duplicate full markup in each error.tsx (rejected — drift) · a single root error.tsx only (rejected — loses per-group reset granularity)
Must:
<must>
  - M1 A render error in the (app) dashboard subtree shows a graceful boundary (role="alert", generic copy, a Retry button wired to Next's `reset()`) instead of a crash/500.
  - M2 The boundary NEVER renders `error.message` or the stack to the user — only generic copy + the safe `error.digest` (leak guard). This holds for RouteError AND global-error.
  - M3 Each route group `(app)/(marketing)/(auth)` has an `error.tsx`; the root has `global-error.tsx` (self-contained <html><body>) and `not-found.tsx`; the (app) dashboard has `loading.tsx`.
  - M4 not-found.tsx renders a 404 surface with a link back home (`/`). loading.tsx renders the `Loading` primitive (role="status" + aria-busy).
  - M5 REUSE `states.tsx` (`ErrorState`/`Loading`) — no new visual language; tokens/`cn`/lucide; the Retry/home controls are keyboard-focusable.
  - M6 error.tsx/global-error.tsx are `"use client"` with the exact Next signature `({ error: Error & { digest?: string }, reset: () => void })`; no existing route/test changes.
</must>
Reject:
<reject>
  - rendering `error.message`/stack/secret to the user -> blocked: RouteError shows generic copy + digest only (M2)
  - a thrown error bubbling to a blank 500 / dev overlay in prod -> caught by the nearest group error.tsx (M1/M3)
  - a 404 dead-ending with no navigation -> not-found.tsx offers a home link (M4)
</reject>
After:
<after>
  - Every route group degrades gracefully: render errors show an on-brand, accessible boundary with a working retry and zero internal-detail leakage; 404s and slow loads have on-brand surfaces. No existing route, component, or test changes.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The no-leak invariant (M2) is the security crux — lowest confidence because Next passes the full `error` (in dev `error.message` is the real message; in prod it's redacted to a generic string + digest). We must not depend on Next's prod redaction — RouteError must NOT print `error.message` AT ALL, only generic copy + digest. If wrong (we render message): a prod stack/secret leak. Locked by a test asserting a sentinel message string is absent from the DOM.
  ⚠ error.tsx in a route-GROUP folder `(auth)/error.tsx` correctly wraps that group — confirm Next 16 applies group-level boundaries (it does; route groups are real segments for special files). If wrong: move the file into a concrete child.
  - [ ] global-error.tsx replaces the ROOT layout on a root error so it must render its own <html><body> and cannot use the font/QueryClient providers — keep it dependency-light. Confirm.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: M1/M5 dashboard error boundary renders + retries
  Given RouteError rendered with an error and a reset spy
  When it mounts
  Then it shows role="alert" with generic copy and a Retry control
  And clicking Retry calls reset() exactly once

Scenario: M2 no leak of error.message
  Given RouteError rendered with error.message = "SECRET_INTERNAL_DETAIL"
  When it mounts
  Then the DOM does NOT contain "SECRET_INTERNAL_DETAIL"
  And it DOES show the safe digest when provided

Scenario: M2 global-error also hides the message
  Given GlobalError rendered with error.message = "SECRET_INTERNAL_DETAIL"
  When it mounts
  Then the DOM does NOT contain "SECRET_INTERNAL_DETAIL" and offers reset()

Scenario: M4 not-found offers navigation home
  Given the NotFound component
  When it renders
  Then it shows a 404 surface and a link with href "/"

Scenario: M4 loading shows an accessible busy state
  Given the dashboard Loading segment
  When it renders
  Then it exposes role="status" and aria-busy

Scenario: M6 each group error.tsx is a client boundary delegating to RouteError
  Given the (app)/(marketing)/(auth) error.tsx modules
  When imported
  Then each default-exports a component that renders RouteError's alert
  And no existing route or test is modified
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// components/ui/route-error.tsx  (NEW, "use client")
export interface RouteErrorProps { error: Error & { digest?: string }; reset: () => void; surface?: string }
export function RouteError({ error, reset, surface }: RouteErrorProps): JSX.Element
//   renders <ErrorState role="alert" title=<generic> description=<generic, surface-aware>
//           onRetry={reset} /> + (error.digest ? a small "Reference: {digest}" line : null)
//   NEVER references error.message / error.stack.

// app/(app)/app/error.tsx · (marketing)/error.tsx · (auth)/error.tsx  (NEW, "use client")
export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <RouteError error={error} reset={reset} surface={<group>} />
}

// app/global-error.tsx  (NEW, "use client") — renders its OWN <html><body>
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void })
//   self-contained markup (no providers/fonts); generic copy + digest + a reset button; no message leak.

// app/not-found.tsx  (NEW) — 404 heading + body + <a href="/"> (or next/link) home.
// app/(app)/app/loading.tsx  (NEW) — <Loading label="Loading…" /> (role=status + aria-busy).
```

Schema: none — no DB, no network, no new dependency (reuses states.tsx + lucide + cn). No existing file modified.

Least-sure flag surfaced at freeze: [spec] The NO-LEAK invariant (M2) — RouteError/GlobalError must render generic copy + `digest` ONLY, never `error.message`/stack, and must not rely on Next's prod redaction. Cost if wrong: a prod internals/secret leak in the error UI. Locked by a test asserting a sentinel message is ABSENT from the DOM. · [contract] group-level `(auth)/error.tsx` boundary placement (Next 16 applies it to the group) — if it doesn't wrap, move to a concrete child (low cost, presentation-only).
Status: FROZEN @ v1 — approved by Tin 2026-06-26 (milestone approval; no-leak invariant surfaced as the freeze flag, security-relevant → refute-read at verify)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% lines on `components/ui/route-error.tsx`; segment files smoke-rendered.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_route_error_renders_and_retries: render RouteError(error, resetSpy) → role="alert" present, generic copy visible, click Retry → resetSpy called once.
  - test_route_error_hides_message: render RouteError with message "SECRET_INTERNAL_DETAIL", digest "dig-123" → DOM excludes the secret, includes "dig-123".
  - test_global_error_hides_message: render GlobalError with the sentinel message → DOM excludes it, a reset control present.
  - test_not_found_links_home: render NotFound → a 404 marker + a link/anchor with href "/".
  - test_loading_is_accessible: render the (app) loading segment → role="status" + aria-busy="true".
  - test_group_error_modules_delegate: import the 3 group error.tsx default exports, render each with (error, reset) → each shows the RouteError alert (proves the wrapper wiring).
</test_plan>

Tests live in: `./tests/` · `apps/dashboard/tests/failure-state-segments.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/route-error.tsx` `apps/dashboard/app/global-error.tsx` `apps/dashboard/app/not-found.tsx` `apps/dashboard/app/(app)/app/error.tsx` `apps/dashboard/app/(app)/app/loading.tsx` `apps/dashboard/app/(marketing)/error.tsx` `apps/dashboard/app/(auth)/error.tsx` `apps/dashboard/tests/failure-state-segments.test.tsx`
Strategy (ordered batches): 1. RouteError (the leak-guarded core). 2. group error.tsx wrappers + global-error. 3. not-found + loading. 4. green + coverage.
Safety rule (feature-specific): RouteError/GlobalError reference ONLY error.digest — never error.message/stack (no-leak invariant); retry/home controls keyboard-focusable.
Code lives in: `apps/dashboard/components/ui/` + `apps/dashboard/app/`
Constraints: do NOT change any test or the contract; allow-list packages only (reuse states.tsx/lucide/cn, NO new dep); modify NO existing route/component/test; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 542 green (66 files); +6 new failure-state tests
- [x] coverage did not decrease — new route-error.tsx fully exercised (render+retry, no-leak, digest); segments smoke-rendered
- [x] no test or contract was altered during build — only NEW files added; ZERO existing routes/components/tests touched (suite delta is purely additive: 536→542)
- [x] the green was EARNED — no-leak (M2) verified TWO ways: (1) two sentinel-absent tests (RouteError + GlobalError assert "SECRET_INTERNAL_DETAIL" absent from DOM), (2) a grep proving the code references ONLY `error.digest` — the only `error.message`/`.stack` hits are in doc-comments. Retry test asserts reset() called once (real behavior, not internals). Small fully-locked surface → focused security verification, no full subagent refute-read needed
- [x] concurrency / timing safe — N/A: pure presentation components, no IO/state/async
- [x] no exposed secrets, injection openings, or unexpected dependencies — the WHOLE POINT is the no-leak guard (verified); ZERO new deps (reuses states.tsx/lucide/cn/next-link); global-error is inline-styled (renders even if CSS fails)
- [x] layering & dependencies follow CONVENTIONS.md — segments compose the shared states.tsx primitives; group wrappers delegate to one RouteError (no drift); next/link for internal nav (eslint enforced)
- [x] a person reviewed — Tin approved the freeze (no-leak surfaced as the security flag); security property verified, no HARD-STOP; auto-gate under autonomy:auto. Owner: Tin Dang

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] A dashboard render error shows role="alert" + a working Retry (calls reset once) — confirmed by `test_route_error_renders_and_retries`
- [x] Neither RouteError nor GlobalError leaks the error message — confirmed by the two sentinel-absent tests + the grep (only error.digest referenced)
- [x] 404 offers a home link; loading exposes role=status+aria-busy — confirmed by `test_not_found_links_home` / `test_loading_is_accessible`
- [x] All 3 group error.tsx delegate to the shared boundary — confirmed by `test_group_error_modules_delegate` rendering each and finding the alert
- [x] No regression — 542-green suite, tsc 0, eslint 0, next build exit 0 (all segment files compiled into the route tree)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING — `RouteError` consumed by all 3 group error.tsx; `Loading`/`ErrorState` reused from states.tsx; `next/link` in not-found. Next auto-wires the special filenames into the route tree (build output confirms compile).
- [x] DEAD-CODE — every new symbol referenced (tsc + eslint clean); no orphan exports.
- [x] SEMANTIC — grep-confirmed the no-leak invariant: code paths reference only `error.digest`; `error.message`/`.stack` appear ONLY in SECURITY doc-comments.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (freeze) · no-leak security property verified (sentinel tests + grep) · auto-resolved under autonomy:auto · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): error-boundary hit rate per route group (a spike = a real upstream/render regression); 404 rate; the error.digest surfaced to users (correlate to server logs).

### Spec delta
- [SPEC · open] Wire a client-side error reporter (capture error.digest + route, POST to a BFF telemetry endpoint) so boundary hits are observable in prod — today the boundary is silent (evidence: no telemetry on which boundary fired).
- [SPEC · open] Add a granular (app) `loading.tsx` skeleton matching each surface (table/chart shells) instead of the single centered spinner, once the apply tasks (harden-admin) define per-page layouts (evidence: a generic spinner is a coarse fallback).
- [SPEC · seeded] Consider a 500-level `app/error.tsx` (non-group root, for the (app)+(marketing)+(auth) common ancestor) — currently global-error only fires on ROOT-LAYOUT throws; segment errors are caught per-group, which is the intended granularity.

### Competency deltas
- [UDD · open] Reusing the v13 `states.tsx` primitives made the failure segments a thin composition (one RouteError + thin wrappers) with no new visual language — the state-pattern investment pays off again (evidence: 7 files, ~all delegate to ErrorState/Loading).
- [ADD · open] A security-flagged task whose invariant is "render X, never render Y" is best verified by a sentinel-absent test PLUS a grep of the code paths — together they prove the negative more cheaply than a full subagent refute-read on a tiny surface (evidence: no-leak verified by 2 tests + grep showing only error.digest).
- [SDD · open] Next 16 special-file signature: error.tsx/global-error.tsx MUST be "use client" with `{error, reset}`; global-error renders its OWN html/body and can't use providers — keep it inline-styled/dependency-light (evidence: built + compiled into the route tree).
