# TASK: Entrance motion + verified field-validation/resilient-submit/failure states on auth pages

slug: harden-auth · created: 2026-06-26 · stage: production
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
- `apps/dashboard/components/ui/auth-shell.tsx` : AuthShell — the SHARED split-screen frame for /login + /signup. Its `<main>` content column renders `<div class="w-full max-w-sm space-y-6">{children}</div>`. ADD a `Reveal` wrap around `{children}` so both auth pages get a motion-safe entrance. `Reveal` from `./motion` (data-slot="reveal", task 5/8). The decorative brand panel (`data-slot="auth-brand"`, aria-hidden) and the single `<main>`/`<form>` landmark structure are UNCHANGED.
- `apps/dashboard/components/auth/LoginForm.tsx` + `SignupForm.tsx` : ALREADY implement EC8 — `fieldErrors`/`globalError`/`isSubmitting` state, labeled fields (`htmlFor`), inline field errors, resilient submit via the bff-client (task 1). This task VERIFIES that surface; it does not rewrite it.
- `apps/dashboard/app/(auth)/error.tsx` : EXISTS (task 4) — auth route-group failure boundary. `(auth)/login/page.tsx` already renders a GENERIC `ErrorState` on `?sso_error` (raw hint never shown). Verified, not edited.

Context (working folder):
- harden-auth's NET-NEW = motion (Reveal entrance shared across both auth pages) + an EC8 verification net. The resilient submit, field-level validation feedback, and failure/no-leak states are foundation-delivered (forms + task 1 bff-client + task 4 boundary).

Honors (patterns / conventions):
- Aurora language; reduced-motion safe (Reveal `motion-safe:` + global net). No copy/behaviour change. Single `<main>` + single `<form>` landmark preserved; brand panel stays aria-hidden.

Anchors the contract cites: `AuthShell` `<main>` `Reveal`, `LoginForm`/`SignupForm` (`fieldErrors`/`isSubmitting`/labeled fields), the `(auth)/error.tsx` boundary.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Entrance motion on auth pages + verified EC8 (field validation, resilient submit, no-leak failure)
Framings weighed: wrap once in the shared AuthShell main (chosen) · wrap each page/form (rejected — churn) · rebuild form validation (rejected — already shipped, would risk regressions)
Must:
<must>
  - M1 AuthShell wraps its `<main>` content in `Reveal` so both /login and /signup enter with a subtle motion-safe entrance. Children (heading + form) render unconditionally.
  - M2 EC8 field validation is present: LoginForm + SignupForm expose accessibly-labeled fields and render inline `fieldErrors` without navigation on invalid input.
  - M3 Resilient submit is present: forms disable the submit while `isSubmitting` and surface a generic `globalError` on failure — the raw upstream detail is never shown (no-leak), mirroring the `?sso_error` generic ErrorState.
  - M4 No existing auth test/behaviour changes; full suite green; single `<main>` + single `<form>` landmark preserved; brand panel stays `aria-hidden`.
</must>
Reject:
<reject>
  - motion that defers/hides the auth form -> Reveal renders children unconditionally (M1) — verify asserts the form present
  - a form field with no accessible label, or a submit that stays enabled mid-flight -> the EC8 verify test FAILS (M2/M3)
  - a second landmark / lost form / brand panel re-entering the a11y tree -> existing AuthShell a11y tests FAIL (M4)
</reject>
After:
<after>
  - Both auth pages enter with a subtle motion-safe entrance; the field-validation, resilient-submit, and no-leak failure surfaces are verifiably present; nothing structural/behavioural changed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Wrapping AuthShell children in `Reveal` doesn't perturb the AuthShell landmark/a11y/form tests — lowest confidence because AuthShell is a tested shared primitive; if wrong: reconcile (Reveal is a plain div inside the existing content div, landmark unchanged). Confirmed by the full suite.
  - [ ] The EC8 surface is genuinely already shipped (fieldErrors/isSubmitting/labeled fields) — confirmed by reading both forms + the existing login/signup tests; the verify net asserts the surface, does not re-implement it.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: M1 auth content renders inside a Reveal
  Given AuthShell rendered with a heading + form child
  When mounted
  Then the form is present inside the <main>, wrapped in [data-slot="reveal"]

Scenario: M2 invalid input shows inline field error, no navigation
  Given LoginForm rendered
  When submitted with an invalid email
  Then a fieldErrors message appears and no navigation occurs

Scenario: M3 submit disables in-flight + generic failure, no leak
  Given LoginForm / SignupForm
  When inspected
  Then the submit is disabled while isSubmitting and failures surface a generic globalError (raw detail never shown)

Scenario: M4 single landmark + aria-hidden brand preserved
  Given AuthShell rendered
  When queried
  Then exactly one <main> and one <form>, and the brand panel is aria-hidden
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// components/ui/auth-shell.tsx — inside the content column:
import { Reveal } from "./motion";
<main className="flex items-center justify-center p-6 sm:p-10">
  <Reveal className="w-full max-w-sm space-y-6">{children}</Reveal>
</main>
// Reveal replaces the plain content <div> (same classes) → motion-safe entrance, children unconditional.
// Single <main>/<form> landmark + aria-hidden brand panel unchanged. Forms (EC8) untouched — verified.
```

Schema: none — a presentational wrapper swap inside the existing landmark. No DB/network/dep/prop change. No form logic changed.

Least-sure flag surfaced at freeze: [test] swapping AuthShell's content `<div>` for `<Reveal>` (same classes) must not perturb the AuthShell landmark/a11y/form tests — verified by the full suite. · [contract] EC8 is verify-only here; if a form gap surfaces it becomes a separate change-request, not a silent edit.
Status: FROZEN @ v1 — approved by Tin 2026-06-26 (milestone approval; additive motion + EC8 verification, reduced-motion safe, no behaviour change)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — the auth entrance + EC8 surface net.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_auth_content_in_reveal: render AuthShell with a form child → form present inside main [data-slot="reveal"].
  - test_login_invalid_email_inline_error: render LoginForm, submit invalid email → fieldErrors message appears, no navigation (location unchanged).
  - test_submit_disabled_in_flight: render LoginForm, begin submit (pending fetch) → submit button disabled while isSubmitting.
  - test_single_landmark_and_hidden_brand: render AuthShell → one main, one form, brand panel aria-hidden.
</test_plan>

Tests live in: `./tests/` · `apps/dashboard/tests/auth-hardening.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/auth-shell.tsx` `apps/dashboard/tests/auth-hardening.test.tsx`
Strategy (ordered batches): 1. swap AuthShell content `<div>` for `<Reveal>` (same classes). 2. green. (Forms NOT edited — EC8 verified via the test net.)
Safety rule (feature-specific): children render unconditionally; single main/form landmark + aria-hidden brand unchanged; NO form-logic edit.
Code lives in: `apps/dashboard/components/ui/`
Constraints: do NOT change any test or the contract; NO new dep; no copy/behaviour change; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 566 green (71 files); +4 new auth-hardening tests
- [x] coverage did not decrease — additive (566 from 562); no behavioural test removed
- [x] no test or contract was altered during build — only the AuthShell content `<div>`→`<Reveal>` swap (same classes); forms untouched
- [x] the green was EARNED — the entrance test asserts the form renders INSIDE `main [data-slot="reveal"]` (red before the swap); the EC8 tests drive REAL behavior: an invalid email yields the inline `Invalid email address` error with `router.push` NOT called, and a delayed login flips the submit to a disabled "Signing in…" state. No stubbed-away logic. Presentation swap + behavior assertions → no subagent refute-read
- [x] concurrency / timing safe — the in-flight test uses msw `delay`; no app-side concurrency added
- [x] no exposed secrets, injection openings, or unexpected dependencies — ZERO new deps; M3 explicitly asserts the no-leak generic error path
- [x] layering & dependencies follow CONVENTIONS.md — Reveal (DS primitive) reused in the shared auth shell; brand panel stays aria-hidden
- [x] a person reviewed — Tin approved the freeze; additive motion + verification. Owner: Tin Dang

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] Both auth pages enter via a Reveal — confirmed: `test_auth_content_in_reveal` (form inside `main [data-slot="reveal"]`); AuthShell is shared by /login + /signup
- [x] Field validation surfaces inline without navigation — confirmed: `test_login_invalid_email_inline_error` (`Invalid email address`, no `router.push`)
- [x] Resilient submit disables in-flight — confirmed: `test_submit_disabled_in_flight` (button → disabled "Signing in…")
- [x] Single landmark + aria-hidden brand preserved — confirmed: `test_single_landmark_and_hidden_brand` + existing AuthShell a11y/redesign tests green
- [x] No regression — 566 green, tsc 0, eslint 0 errors

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING — `Reveal` imported into AuthShell, replaces the content div; consumed by the new test marker.
- [x] DEAD-CODE — no orphan; the old plain `<div>` is fully replaced (same classes carried onto Reveal).
- [x] SEMANTIC — re-read AuthShell: single `<main>`, brand panel still `aria-hidden`, classes preserved; forms not touched (EC8 verify-only).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (freeze) · auto-resolved under autonomy:auto (additive motion + verification) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): auth submit failure-rate + that the generic error path keeps holding (no raw upstream leak); real-browser focus-visible/contrast on the auth fields (jsdom can't measure).

### Spec delta
- [SPEC · open] Add inline aria-live announcement of `globalError` for screen readers on auth failure — current error is visible but not asserted to be announced (evidence: no aria-live assertion in the EC8 net).
- [SPEC · open] The OIDC callback route (`/auth/oidc/callback`, ƒ dynamic) has no Reveal/entrance — it's a transient redirect bounce; revisit if it ever renders a visible interstitial (evidence: only /login + /signup wrapped).

### Competency deltas
- [UDD · open] Sharing the entrance via AuthShell (one wrap) covers both auth pages with zero per-page churn — same shell-owns-motion pattern as the admin AppShell (evidence: 2 pages, 1 swap).
- [TDD · open] For "already-shipped" criteria (EC8), the verify net asserts the live surface (invalid-email inline error, in-flight disabled submit) rather than re-implementing — green-by-design tests still earn their keep as regression guards (evidence: 3 EC8 tests green pre-change, lock the behavior).
