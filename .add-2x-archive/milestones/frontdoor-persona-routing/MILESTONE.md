# MILESTONE: Front-door persona routing

goal: Every visitor who arrives at Hydroa's front door reaches a live next step: self-serve signup works, and a member of an existing tenant is routed to SSO, their invite link, or a request-access path instead of a dead end.
rationale: <why this scope — the confirmed intake classification (bucket + reason)>
stage: production · status: active · created: 2026-07-20T15:12:43+00:00
release: pending

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  The public front door end-to-end — `/` (homepage CTAs + a real price anchor), `/pricing` (all five
     real tiers), `/signup` (personal self-serve behind a default-OFF flag, plus an always-present
     alt-routes panel: SSO · invite link · request access), and `/login` (a unified entry that
     classifies the typed email client-side and orders the affordances to fit). Plus one security
     closure found mid-milestone: the `/auth/oidc/login` enumeration oracle.
Out: Deferred deliberately, each with a seeded delta — the literal one-field "Continue with email"
     two-stage collapse (needs four frozen suites re-specified; Tin chose option (c) at freeze); a true
     single front door at `/start`; per-IP rate limiting on the login-init routes; retiring the
     duplicate `resolveSsoDomain`/`validateSsoDomain` pair; the `globalError` position fix.

> UI/UX in scope? Name it precisely, not "make it nice" — information architecture ·
> interaction pattern · visual hierarchy · design tokens · component states ·
> accessibility floor (WCAG AA) · responsive breakpoints · user journey
> (`.add/personas-teacher/design/`). Precise ≠ distinctive: skip generic AI-design
> defaults (cream+serif+terracotta · near-black+neon · broadsheet-hairline) and name ONE
> deliberate signature element instead (Claude Code's `frontend-design` skill). A UI
> feature also triggers DESIGN.md via the `add` skill's design.md.

## Shared decisions & glossary deltas   (living — every task must honor these)
- <cross-cutting rule, named from GLOSSARY.md>

## Shared / risky contracts (freeze these first)
- <contract name> -> owning task <slug>

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] <slug>   depends-on: none     — <one line>
- [ ] <slug>   depends-on: <slug>   — <one line>

## Exit criteria (observable; map each to the task that delivers it)
- [x] A visitor sees a real price before committing: `/pricing` renders all five live tiers from the
      catalog, and `/` carries a catalog-sourced anchor "Free to start · plans from $1/mo"
      (← pricing-tier-ladder, homepage-price-anchor)
- [x] A visitor with a personal email can create their own workspace without an invite — deferred
      creation + mailbox confirm, behind the default-OFF `public_signup_personal_enabled`
      (← scoped-self-serve-signup)
- [x] A visitor who cannot self-serve is never dead-ended: the alt-routes panel (SSO · invite link ·
      request access) renders unconditionally, first in the form, in every flag and account state
      (← signup-refusal-router)
- [x] A visitor's typed email routes them without the server being asked who they are: classification is
      a pure client-side function of the string, byte-identical panel for claimed vs unclaimed domains,
      zero network calls across a full type-in (← domain-aware-auth-routing, unified-signin-entry)
- [x] A team visitor lands on `/signup` pre-set to Business from either homepage CTA
      (← homepage-cta-intent-split)
- [x] `/login` is one entry, not three: type the email once, the SSO domain auto-fills while pristine,
      the affordances reorder to fit the visitor, and "Create a workspace" is always present
      (← unified-signin-entry)
- [x] `GET /auth/oidc/login?domain=X` no longer discloses whether X is a customer: one collapsed 404
      terminal, bodies identical incl. title, in both deployment modes (← sso-login-oracle-closure)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched
- skill   : untouched
- book    : untouched
- marketing (dashboard) : `/` gains intent-split CTAs + a catalog-sourced price anchor; `/pricing`
  renders all five live tiers.
- auth (dashboard) : `/signup` gains personal self-serve + an unconditional alt-routes panel +
  `?account_type=` / `?email=` seeds; `/login` becomes a unified, class-aware entry. New frozen
  client-side module `lib/email-domain-routing.ts` — IO-free by construction.
- auth (gateway) : personal signup with deferred creation + mailbox confirm behind a default-OFF flag;
  a new store-only `access_requests` bounded context; `/auth/oidc/login`'s enumeration oracle collapsed
  to one 404 terminal.

### Cross-task evidence   (one row per task)
- pricing-tier-ladder       : gate=PASS · residue=none · Free's null price renders "$0" (Tin-decided —
  title+price both reading "Free" made the by-text assertion ambiguous)
- homepage-integration-proof: gate=PASS · tests=17/17 refute-read · residue=none
- scoped-self-serve-signup 🔒: gate=PASS · residue=none · TWO independent adversarial verifies, both
  CLEAR. Deferred creation + uniform 202 + Argon2 timing mask + single-use SHA256-hashed confirm token
  + atomic `DELETE … RETURNING`. The global `GATEWAY_PUBLIC_SIGNUP_ENABLED` was deliberately NOT
  flipped, preserving anti-enumeration.
- signup-refusal-router 🔒  : gate=PASS · residue=none · `autonomy: conservative` → **Tin personally
  gated this one**
- domain-aware-auth-routing : gate=PASS · tests=32 new · residue=none. ⚠ Its `sensitivity:` is UNSET in
  the engine although its own §3 declares P1-P4 as SECURITY HARD-STOPs — auto-PASS was legitimate only
  because both verifies returned CLEAR with no finding. Declaring it is a carried-forward item.
- homepage-cta-intent-split : gate=PASS · tests=40/40 + 31/31 + 24/24 · residue=none
- homepage-price-anchor     : gate=PASS · tests=58/58 · residue=one test-design delta (all 28 tests
  compute their expected value with the same call they assert against, so a hardcoded literal would
  pass; only a source read catches it — the sibling `pricing-catalog-no-drift.test.ts` shares the hole)
- sso-login-oracle-closure 🔒: gate=PASS **by Tin** (conservative + security) · tests=54/54 dedicated DB,
  95/95 serial reproduced twice · residue=one operability delta (the claimed-vs-unknown distinction is
  now absent from LOGS as well as the wire). TWO independent verifies, both EARNED/CLEAR. Non-vacuity
  proven by running the new suite against pre-fix code at Ground SHA → 7 failed / 4 passed.
- unified-signin-entry      : gate=PASS · tests=21 new, 1054 legacy green · coverage LoginForm 95.45% /
  SignupForm 94.39% · residue=`globalError` renders above the reordered region instead of beside the
  Log-in button (sighted-user visual regression; AT unaffected via `aria-live`), plus a Q3 coverage
  narrowing. Both seeded as deltas.

**Full pre-merge suite, this branch, 2026-07-21 — GREEN.**
Gateway: 4 parallel chunks @`-n 6` (942 · 1097 · 1050 · 904) + the auth family run SERIAL (255) ≈ 4250.
Dashboard: legacy 1054 / 112 files + bff 723 / 77 files = 1777.
One attributed non-regression: `tests/realtime` showed 1 failed + 4 errors under `-n 6` with
`sqlalchemy` DBAPI errors, and 12/12 green when run serially — the documented shared-schema
`drop_all`/`create_all` collision, NOT a code defect. Attributed rather than dismissed as "a flake".
No cross-task drift casualties this cycle (contrast the last three milestones, each of which had at
least one) — the additive-only discipline on table manifests held.

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row: price transparency ←
  pricing-tier-ladder + homepage-price-anchor · personal self-serve ← scoped-self-serve-signup · no
  dead end ← signup-refusal-router · routing without asking the server ← domain-aware-auth-routing +
  unified-signin-entry · team intent ← homepage-cta-intent-split · one login entry ←
  unified-signin-entry · oracle closed ← sso-login-oracle-closure.
- goal: "Every visitor who arrives at Hydroa's front door reaches a live next step: self-serve signup
  works, and a member of an existing tenant is routed to SSO, their invite link, or a request-access
  path instead of a dead end." **Proven by:** `signup-refusal-router`'s alt-routes panel renders
  UNCONDITIONALLY — first in the form, before any field, in every flag state and every account state —
  so the dead end that started this milestone (a Docker e2e deploy hitting invite-only signup with no
  onward path) is now structurally unreachable rather than merely unlikely.
- ⚠ HONEST SCOPE NOTE, not a criterion failure: `unified-signin-entry` delivers the unified entry via
  emphasis + ordering + one source-of-truth email field, NOT the literal one-field "Continue with
  email" step its title suggests. Tin chose this knowingly at freeze (option c) because the literal
  collapse would require weakening four already-green frozen suites. The literal version is seeded as
  a `[SPEC · seeded]` delta and should not be allowed to age out silently.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
