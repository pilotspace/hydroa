# MILESTONE: Frontdoor Polish

goal: The /login and /signup surfaces shipped in frontdoor-persona-routing lose their visible rough edges: one email field instead of two, and the login error sits where the visitor is looking.
rationale: task-bucket polish pass on the shipped frontdoor — extends frontdoor-persona-routing (merged #84); both edges were named by Tin from the live surface ("why we make seperate email field?" · the error renders far from the Log in button under per-class reordering).
stage: mvp · status: active · created: 2026-07-21T06:22:28+00:00
release: pending

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  the two named rough edges on `/login` — (1) merge the password-Email and SSO "Work email or
     domain" inputs into ONE field; (2) move `globalError` beside the "Log in" button so it travels
     with the password affordance under per-class reordering. Client-side only.
Out: any gateway/BFF/schema change · the `?domain=` seed's flow into `createWorkspaceHref`
     (`/signup?email=<bare domain>` residue, noted at task-1 review) · stale-`globalError`-on-SSO-click
     clearing (accepted asymmetric residue, task-2 Assumption 2) · any copy, label, or helper-line
     change beyond Tin's locked decisions (label "Email", placeholder `you@company.com`, no helper line).

## Shared decisions & glossary deltas   (living — every task must honor these)
- unified-signin-entry M9/R5 (presence invariant) + M11 (pure zero-IO classification) + M12 (zero
  network from typing) hold across both tasks — every retarget traces to a frozen register.
- M6b (task 1, Tin's freeze decision): a SEEDED value never classifies before the first keystroke —
  `visibleClass` gates render decisions; `entryClass` stays pure.
- Error-beside-its-affordance is the surface's pattern: `ssoError` inside `ssoRoute` (M8, task 1),
  `globalError` inside `passwordRoute` ABOVE the button (task 2, Tin's freeze decision).

## Shared / risky contracts (freeze these first)
- LoginForm single-field entry shape -> owning task merge-login-email-field (FROZEN @ v1, shipped)
- globalError placement -> owning task login-global-error-position (FROZEN @ v1, shipped)

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] merge-login-email-field        depends-on: none  — collapse the two email-shaped inputs into one
- [x] login-global-error-position    depends-on: none  — move globalError beside the Log in button

## Exit criteria (observable; map each to the task that delivers it)
- [x] /login renders exactly ONE email-shaped input that drives password login, SSO/SAML, and
      classification alike        (← merge-login-email-field)   (verify: cd apps/dashboard && npx vitest run tests/unified-signin-entry.test.tsx tests/sso-login.test.tsx)
- [x] a failed password login renders its error inside the password affordance, beside "Log in",
      in every visibleClass order (← login-global-error-position)   (verify: cd apps/dashboard && npx vitest run tests/login-global-error-position.test.tsx)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched
- skill   : untouched
- book    : untouched
- dashboard : `LoginForm.tsx` — one email field (−26 lines, state pair + copy bridge deleted;
  task 1, merged PR #85 `5c57d27`) · `globalError` relocated into `passwordRoute` (+11/−6; task 2).
  4 suites re-specified per task-1's frozen retarget register; 1 new suite (task 2). Also fixed
  `test_signup_page_uses_authshell`, red on main since `3159ada` (`ec13ce8`).

### Cross-task evidence   (one row per task)
- merge-login-email-field : gate=PASS · tests=1778 green (full dash suite @ merge; legacy 1055 + bff 723) · residue=createWorkspaceHref seeded-bare-domain param + window.location mock restore not try/finally (review notes, out-of-scope)
- login-global-error-position : gate=PASS · tests=1780 green (full dash suite; 2 new red→green) · residue=stale globalError after a later SSO click (accepted at freeze, Assumption 2)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: one email field instead of two (criterion 1 ← merge-login-email-field row) and the login
  error beside the button the visitor clicked (criterion 2 ← login-global-error-position row) —
  both proven by red→green suites + a live prod-build browser pass (task 1) on the shipped surface.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [x] task-1 PR #85 reviewed + merged (`5c57d27`, admin-merge on Tin's instruction, 2026-07-23)
- [ ] task-2 branch `feat/login-global-error-position` → PR, Tin reviews + merges
- [ ] fold the milestone's deltas at close; bundle into the next release cut (post-0.11.0)
