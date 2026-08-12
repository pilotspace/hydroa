# PLAN: Move /login's global error beside the Log in button

slug: login-global-error-position · created: 2026-07-23 · stage: production
milestone: frontdoor-polish
autonomy: auto
sensitivity: mechanical
component: dashboard
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: `/login`'s `globalError` (the failed-password-login message, e.g. "Invalid credentials")
renders INSIDE the password affordance, adjacent to the "Log in" button the visitor just clicked —
instead of at a fixed position above the reordered routes region, where the corporate/public
orderings paint it far from that button.

Framings weighed: move the JSX block into `passwordRoute` after the submit button, mirroring the
shipped M8 `ssoError`-beside-its-button pattern (chosen — one-node move, reuses an invariant the
suite already pins) · keep the fixed slot but visually restyle it (rejected — position IS the
defect; the milestone goal names "where the visitor is looking") · portal/absolute-position near
the button (rejected — gratuitous machinery for a static form).

Must:
<must>
  - M1 — On a failed password login (BFF non-2xx or thrown fetch), the error message renders inside
    `passwordRoute`'s own container, immediately ABOVE the "Log in" button (between the password
    field block and the button) — **TIN'S FREEZE DECISION, 2026-07-23**: he was offered
    after-the-button (the `ssoError` mirror, my recommendation) and chose ABOVE, so the message is
    read before the button it explains.
  - M2 — Rendering POSITION is the only change: text source (`err.problem.title ?? "An error
    occurred"` / `"An unexpected error occurred"`), `role="alert"`, `aria-live="polite"`, the
    `text-sm text-destructive` classes, and the set/clear timing (cleared at the top of
    `handleSubmit`) are all byte-unchanged.
  - M3 — Because it lives inside the `passwordRoute` subtree, the error travels WITH the password
    affordance under every `visibleClass` ordering (corporate: SSO → SAML → password; public:
    create-workspace first; unknown: password first) — adjacent to "Log in" in all three.
  - M4 — Exactly ONE such error node exists when set (the old fixed slot is REMOVED, not duplicated),
    and zero state/handler/classification changes: `handleSubmit`, `entryClass`/`visibleClass`,
    `hasTyped`, and every frozen unified-signin-entry / merge-login-email-field invariant
    (M9–M14 presence/order/purity) are untouched.
</must>
Reject:
<reject>
  - The error still renders at the fixed pre-region slot (or anywhere outside `passwordRoute`)
    -> "GLOBAL_ERROR_NOT_ANCHORED"
  - Any change to the error's text source, role/aria attributes, or set/clear timing
    -> "GLOBAL_ERROR_SEMANTICS_CHANGED"
  - The error rendered in BOTH positions (old slot left behind) -> "GLOBAL_ERROR_DUPLICATED"
  - Any affordance presence/order/classification behavior change -> "ENTRY_INVARIANTS_TOUCHED"
</reject>
After:
<after>
  - A visitor who clicks "Log in" and gets a server failure sees the message at the button they
    clicked — in the neutral order AND after a corporate email has flipped the region to SSO-first.
  - The milestone's second exit criterion ("the login error sits where the visitor is looking") is
    delivered; frontdoor-polish goal 2/2.
</after>
Boundary: the two message shapes — a BFF problem `title` (401 → "Invalid credentials") vs the
generic catch fallback ("An unexpected error occurred" on a thrown fetch) — one red test each.
<assumptions>
  ✓ 1. RESOLVED AT FREEZE (2026-07-23): placement was the ⚠ flag — Tin was offered after-the-button
     (the ssoError mirror, recommended) vs above-the-button, and chose ABOVE. Contracted in M1;
     no longer an assumption.
  - 2. A stale `globalError` surviving a subsequent SSO/SAML click stays OUT of scope — mirrors
     task-1 Assumption 5's accepted asymmetric residue; post-move the stale text sits under
     "Log in", visibly about the password action, so it is LESS misleading than before. If wrong:
     one `setGlobalError(null)` line in each SSO handler as a follow-up.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>
</scenarios>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core)

```
SERVER SURFACE — INTENTIONALLY EMPTY. Pure client-side JSX relocation in one component.

CHANGED COMPONENT — apps/dashboard/components/auth/LoginForm.tsx:LoginForm
  [OBSERVED this session, main @ 5c57d27]
  - The `{globalError && (<p role="alert" aria-live="polite" …>)}` block currently renders between
    the Email field block and the `[data-slot="login-entry-routes"]` region (fixed slot).
  - MOVE that block, byte-identical, into `passwordRoute`'s outer container, immediately BEFORE the
    `<Button type="submit">` ("Log in") — between the password-field block and the button
    (Tin's freeze decision; the after-button `ssoError` mirror was offered and declined).
  - DELETE the old fixed slot. No other line in the file changes: `globalError` state,
    `handleSubmit`'s set/clear, `entryClass`/`visibleClass`/`hasTyped`, all route subtrees and
    their per-class ordering are byte-unchanged.

Anchors: `LoginForm` · `passwordRoute` · `ssoRoute` (the M8 precedent) · `handleSubmit` ·
`globalError` · `[data-slot="login-entry-routes"]`.
Ground SHA: 5c57d27
```

Target (measurable): new suite `login-global-error-position.test.tsx` — 2 tests red before build,
green after · full dashboard vitest (legacy + bff, 1778 baseline) stays green · `next build` exit 0.
Status: FROZEN @ v1 — approved by Tin Dang, 2026-07-23 (chose ABOVE-the-button over the recommended after-button mirror)
Reported: yes — freeze card (banner/ARC/SHAPE/FLAGS/EVIDENCE) rendered before this froze; ⚠ placement flag resolved by Tin's explicit choice

Least-sure flag surfaced at freeze: [contract] ⚠ (placement — RESOLVED by Tin at freeze) the error's
position relative to the "Log in" button was the one open taste call: after-the-button (the
shipped `ssoError` mirror, recommended) vs above-the-button. Tin chose ABOVE — contracted in M1,
one assertion flipped before the freeze. Remaining biggest risk (accepted, Assumption 2): a stale
`globalError` from a failed password attempt survives a later SSO/SAML click — carried residue
from task 1's own accepted asymmetry; post-move it renders under the password affordance where it
is visibly about "Log in", so the cost of being wrong is one `setGlobalError(null)` line per SSO
handler in a follow-up, no shape change.

### Build-strategy (SOFT: preferred; the builder self-improves and records actual at verify)
Scope (may touch): `apps/dashboard/components/auth/LoginForm.tsx` `apps/dashboard/tests/login-global-error-position.test.tsx`
Regression floor: full dashboard vitest run (legacy + bff projects — includes `tests/login.test.tsx`
and `tests-bff/bff-forms.test.tsx`, which pin the error's document-level presence and stay green
across the move) + `next build` exit 0.
Persona (required): generic (frontend one-node JSX move; no persona file adds signal here)
Strategy: (1) red suite in the new file against the msw login-401 harness from `tests/login.test.tsx`;
(2) move the JSX block; (3) full suite + build green.

### AI-verify record (required when gate_mode: ai-plan-verify)
n/a — human freeze.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_failed_login_error_renders_beside_the_log_in_button: arrange msw 401 with title "Invalid
    credentials" / act fill + submit in the neutral (unknown) order / assert the alert is inside the
    "Log in" button's own container, positioned after the button, exactly one instance,
    role="alert" + aria-live="polite" byte-unchanged · covers: M1, M2, M4, R:GLOBAL_ERROR_NOT_ANCHORED,
    R:GLOBAL_ERROR_SEMANTICS_CHANGED, R:GLOBAL_ERROR_DUPLICATED
  - test_error_travels_with_the_password_route_under_corporate_reordering: arrange thrown fetch
    (generic fallback text — the second Boundary shape) + corporate email flips the region to
    SSO-first / act submit / assert the alert renders inside the region's password subtree beside
    "Log in" (not above the region), order + affordance presence unchanged · covers: M2, M3, M4,
    R:GLOBAL_ERROR_NOT_ANCHORED, R:ENTRY_INVARIANTS_TOUCHED
</test_plan>

Tests live in: `apps/dashboard/tests/login-global-error-position.test.tsx` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: as planned — red suite first (2/2 red on position containment, harness
proven by passing semantics asserts), then the one-node move (insert above the "Log in" button +
delete the fixed slot; net +11/−6 lines, all comment or relocation), then full floor green.
Code lives in: `apps/dashboard/components/auth/LoginForm.tsx`
Spawn (multi-agent): none — one-node move, built in-context; refute-read by self at verify.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests (or §4 acceptance checks) pass — new suite 2/2 green · §3 Regression floor: full
      dashboard vitest **1780/1780** (190 files; 1778 baseline + 2 new) · `next build` exit 0
      (56/56 routes) · `tsc --noEmit` clean.
- [x] coverage did not decrease — 2 tests added, none removed or weakened; every pre-existing
      globalError assertion (`tests/login.test.tsx`, `tests-bff/bff-forms.test.tsx`) green unchanged.
- [x] no test or contract was altered during build — the single assertion flip (after→above the
      button) happened BEFORE the freeze, at Tin's explicit placement decision; nothing touched after.
- [x] the green was EARNED, not gamed — see refute-read below.
- [x] concurrency / timing safe — no state, effect, or handler change; a pure JSX relocation.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no import added, no IO.
- [x] layering & dependencies follow CONVENTIONS.md — component-internal move only.
- [ ] a person reviewed and approved the change — autonomy: auto; Tin's approval recorded at the §3
      freeze (placement decision); not claimed on his behalf here.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self · adversarially checked: (a) the load-bearing containment asserts were RED before the move
and green after — the only code change is the relocation, so the green cannot be a fixture overfit;
(b) hunted for a duplicated node: `getAllByText` length-1 assert passes and the old slot's JSX is
deleted (grep: one `{globalError &&` in the file); (c) semantics diff — the moved block's
role/aria-live/className/text-source are byte-identical to the deleted one (M2), and `handleSubmit`'s
set/clear untouched; (d) invariant sweep — 1780/1780 includes the unified-signin-entry +
merge-login-email-field suites pinning presence/order/purity (ENTRY_INVARIANTS_TOUCHED would fail
there); (e) probed the corporate-reorder case with the OTHER boundary message shape (thrown fetch →
generic fallback), so both text sources are proven at the new position.

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-23

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose move the JSX block into `passwordRoute` after the submit button, mirroring the shipped M8 `ssoError`-beside-its-button pattern; rejected keep the fixed slot but visually restyle it (rejected — position IS the defect; the milestone goal names "where the visitor is looking") · portal/absolute-position near the button (rejected — gratuitous machinery for a static form).
- [human] freeze — froze §3 @ v1 (approved by Tin Dang, 2026-07-23 (chose ABOVE-the-button over the recommended after-button mirror))
- [AI] build — strategy used: as planned — red suite first (2/2 red on position containment, harness proven by passing semantics asserts), then the one-node move (insert above the "Log in" button + delete the fixed slot; net +11/−6 lines, all comment or relocation), then full floor green.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
