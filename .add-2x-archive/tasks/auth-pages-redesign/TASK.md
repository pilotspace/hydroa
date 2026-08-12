# TASK: Enterprise login + signup (split-screen, token-styled, styled SSO)

slug: auth-pages-redesign · created: 2026-06-15 · stage: production
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

Touches (files · symbols · signatures): (addresses the user feedback "login page too simple, just blank with 4 input fields" — the forms are RAW unstyled HTML today, zero DS primitives)
- `app/(auth)/login/page.tsx` — server comp; `metadata={title:"Hydroa"}`; renders `<main><h1>Sign in to your account</h1>` + optional SSO-error `<ErrorState role=alert>` ("Single sign-on failed. Please try again or contact your administrator.", raw code hidden) + `<LoginForm/>`.
- `app/(auth)/signup/page.tsx` — server comp; `<main><h1>Create your account</h1><SignupForm/></main>`.
- `components/auth/LoginForm.tsx` — `<form aria-label="Log in">` raw `<label htmlFor=login_email>Email</label><input id=login_email type=email>` + login_password; `<p role=alert>` field/global errors; `<button>Log in/Signing in…</button>`; SSO `<a href="/api/auth/oidc/login">Sign in with SSO</a>`; Zod ("Invalid email address"/"Password is required"); POST `/api/auth/login` `{email,password}` → `router.push("/keys")`; 401→globalError problem.title; NO localStorage.
- `components/auth/SignupForm.tsx` — `<form aria-label="Sign up">` raw labels tenant_name(text)/signup_email/signup_password; Zod ("Tenant name is required"/max 120/"Invalid email address"/"Must be at least 10 characters"); POST `/api/auth/signup` `{tenant_name,email,password}` → `router.push("/keys")`; 409→fieldErrors.email "An account with this email already exists"; `<button>Sign up/Signing up…</button>`; NO SSO.
- DS primitives available: `Card`/`CardContent` (spread {...props} → can carry `data-slot`), `Input` (token input), `Button` (CVA + `asChild` via Slot + exports `buttonVariants` → style SSO `<a>` as a button), `Hexagon` (lucide brand mark, used in AppShell "Hydroa"). NO `Label` component (keep raw `<label htmlFor>` — getByLabelText resolves via htmlFor→id).

Context (working folder): legacy project (`tests/**`, jsdom; useRouter mocked via test-support/mock-cjs-navigation). Verify = `tests/login.test.tsx` + `tests/signup.test.tsx` (+ bff-forms, oidc-login-relay, oidc-callback-relay, e2e-a11y). jsdom has NO CSS engine ⇒ `hidden lg:flex` does NOT hide from the a11y tree.
Honors (patterns / conventions): v13 tokens; presentation-only (same POST routes/bodies/validation/redirects). CRITICAL landmines: (1) brand panel must be `aria-hidden="true"` + heading-free + non-interactive (else jsdom/axe see duplicate headings/landmarks + aria-hidden-focus); (2) exactly ONE `<form>` (form landmark) per page; (3) keep every frozen hook (label text, button names "Log in"/"Sign up", SSO link name /sso/ + href, role=alert, error strings, router.push("/keys")); (4) no h1→h3 skip (avoid CardTitle in the form card; keep the page `<h1>`); (5) real-Chromium e2e axe has color-contrast ENABLED — use token colors with adequate contrast.
Anchors the contract cites: `AuthShell` (NEW: split-screen decorative brand panel + content column), `LoginForm`/`SignupForm` (Card-wrapped, DS Input/Button, SSO via Button asChild), `Card` (data-slot="auth-card"), `login/page.tsx`+`signup/page.tsx`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Enterprise auth pages — split-screen brand panel + token-styled Card forms + styled SSO (presentation-only restyle of /login + /signup)
Framings weighed: split-screen brand panel + centered Card form (chosen — matches the shadcn login-03/dashboard reference and directly answers "login too simple, just blank with 4 input fields") · single centered Card on plain background (rejected: still feels bare, no brand presence) · full-bleed hero with overlaid form (rejected: harder to keep a11y-clean + overfit to login, awkward for signup)
Must:
<must>
  - Render BOTH /login and /signup through a shared `AuthShell` (split-screen): a decorative brand panel + a centered content column that renders the page's heading + form.
  - The brand panel carries the "Hydroa" wordmark + `Hexagon` lucide mark, is `aria-hidden="true"`, contains NO heading/landmark/interactive element, and is hidden on small screens (`hidden lg:flex`) — pure decoration.
  - Wrap each form's fields in a token-styled `Card` marked `data-slot="auth-card"`; render inputs via the DS `Input` primitive and the submit via the DS `Button` primitive.
  - Style the SSO entry on /login as a button via `Button asChild` wrapping the existing `<a href="/api/auth/oidc/login">` — it stays an anchor (href intact) with accessible name matching /sso/i.
  - Preserve EVERY frozen behavioral hook byte-identical: form `aria-label` ("Log in"/"Sign up"), every field label text + htmlFor→id, submit button names ("Log in"/"Sign up" + pending "Signing in…"/"Signing up…"), `role="alert"` errors, all Zod messages, the page `<h1>` text, the SSO-error `ErrorState`, the POST routes/bodies, and `router.push("/keys")`.
</must>
Reject:
<reject>
  - A brand panel that exposes a heading, landmark, or focusable child -> "auth_brand_not_decorative" (duplicate-heading / aria-hidden-focus a11y violation)
  - More than one `<form>` landmark per page -> "auth_multiple_forms"
  - A heading-level skip (page h1 → CardTitle h3 inside the form) -> "auth_heading_skip"
  - Any change to a POST route/body, validation message, redirect, or accessible name -> "auth_behavior_drift" (would break the frozen login/signup suites)
</reject>
After:
<after>
  - /login and /signup present the split-screen enterprise layout with Card-wrapped, token-styled fields + styled SSO, while `tests/login.test.tsx`, `tests/signup.test.tsx`, the bff-forms/oidc relay suites, and e2e-a11y stay green with no source-behavior change.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The brand panel can be made invisible to the a11y tree with `aria-hidden="true"` + no focusable children, satisfying BOTH jsdom (no CSS engine — `hidden lg:flex` does NOT hide it) and real-Chromium axe — lowest confidence because jsdom and Chromium disagree on visibility; if wrong: duplicate-landmark/heading or aria-hidden-focus axe failures, fix is to strip any inadvertent focusable/heading from the panel (presentation-only, no behavior risk).
  - [x] The DS `Button` `asChild` (Radix Slot) correctly forwards `buttonVariants` classes onto the SSO `<a>` while preserving href + accessible name — confirmed by reading button.tsx (Slot + buttonVariants exported); if wrong: fall back to `className={buttonVariants(...)}` on the raw anchor.
  - [x] Wrapping fields in `Card`/`CardContent` without `CardTitle` avoids any heading inside the form card — confirmed: CardTitle is opt-in, not rendered by Card/CardContent alone.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Login renders the split-screen enterprise layout
  Given the /login page
  When LoginForm renders
  Then the form's fields are wrapped in an element marked [data-slot="auth-card"]
  And the AuthShell decorative brand panel [data-slot="auth-brand"] is present and aria-hidden="true"
  And the page still exposes exactly one <form> with aria-label "Log in"

Scenario: Login submit + SSO use DS primitives without behavior drift
  Given the rendered LoginForm
  When the user reads the controls
  Then the submit control is the DS Button named "Log in"
  And the SSO control is an <a href="/api/auth/oidc/login"> styled with buttonVariants, accessible name matching /sso/i
  And the email/password labels, Zod messages, role=alert errors, POST /api/auth/login {email,password}, and router.push("/keys") are unchanged

Scenario: Signup renders the split-screen enterprise layout
  Given the /signup page
  When SignupForm renders
  Then the form's fields are wrapped in an element marked [data-slot="auth-card"]
  And the AuthShell decorative brand panel [data-slot="auth-brand"] is present and aria-hidden="true"
  And the page still exposes exactly one <form> with aria-label "Sign up" (and NO SSO control)
  And the tenant_name/email/password labels, Zod messages, POST /api/auth/signup {tenant_name,email,password}, and router.push("/keys") are unchanged

Scenario: The brand panel is decorative only (a11y)
  Given the AuthShell brand panel
  When the accessibility tree is computed
  Then the panel exposes no heading, no landmark, and no focusable child
  And the only heading on each page remains the page <h1>
  And axe (real-Chromium e2e) reports no violations

Scenario: No heading-level skip in the form card
  Given the Card-wrapped form
  When the heading outline is read
  Then there is no h1 → h3 jump (the form Card renders no CardTitle)
  And the page <h1> text ("Sign in to your account" / "Create your account") is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Presentation-only restyle — NO network/data contract changes. The "contract" is the
COMPONENT SHAPE + DOM/a11y markers (the seam the new red suite asserts). Existing
gateway POST contracts (/api/auth/login, /api/auth/signup, /api/auth/oidc/login) are
unchanged and remain frozen as-is.

```
AuthShell({ children: ReactNode }) -> JSX
  renders: <div split-screen>
             <aside data-slot="auth-brand" aria-hidden="true" class="hidden lg:flex ...">  # decorative: Hexagon + "Hydroa", NO heading/landmark/focusable
             <div content-column>{children}</div>            # the page's <h1> + <form> live here
           </div>

login/page.tsx  -> <AuthShell><h1>Sign in to your account</h1> [SSO-error ErrorState] <LoginForm/></AuthShell>
signup/page.tsx -> <AuthShell><h1>Create your account</h1> <SignupForm/></AuthShell>

LoginForm()  -> <form aria-label="Log in"> … <Card data-slot="auth-card"><CardContent> {Input×2 via label htmlFor} {role=alert errors} <Button>Log in</Button> </CardContent></Card>
                  + SSO: <Button asChild variant="outline"><a href="/api/auth/oidc/login">Sign in with SSO</a></Button>
SignupForm() -> <form aria-label="Sign up"> … <Card data-slot="auth-card"><CardContent> {Input×3 via label htmlFor} {role=alert errors} <Button>Sign up</Button> </CardContent></Card>   # NO SSO

Invariants (must hold byte-identical):
  - exactly ONE <form> per page; brand panel aria-hidden + 0 focusable + 0 heading
  - no CardTitle inside the form Card (no h1→h3 skip); page <h1> text unchanged
  - labels/ids: login_email, login_password, tenant_name, signup_email, signup_password
  - button names "Log in"/"Sign up" (+ "Signing in…"/"Signing up…"); SSO name /sso/i, href "/api/auth/oidc/login"
  - Zod messages, role=alert errors, POST routes/bodies, router.push("/keys") — UNCHANGED
Schema: none (no DB/network access added; client presentation only)
```

Status: FROZEN @ v1 — approved by Tin Dang (standing auto-mode authorization, 2026-06-16)
Least-sure flag surfaced at freeze: [scenario] the brand panel staying invisible to BOTH
jsdom (no CSS engine) and real-Chromium axe — jsdom won't hide `hidden lg:flex`, so the panel
MUST carry aria-hidden + zero focusable/heading children for both to pass; if wrong → duplicate
landmark/heading or aria-hidden-focus axe failure, fix is presentation-only (strip the offending
node), zero behavior risk. Everything else is held byte-identical by the existing frozen suites.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: lines:80 held (project floor, components/** + lib/**); the dense frozen login/signup/oidc suites remain the behavioral regression net — this RED suite asserts ONLY the v23 presentation adoption.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_auth_shell_brand_panel_decorative: render <AuthShell><h1>X</h1><form/></AuthShell> / assert [data-slot="auth-brand"] present + aria-hidden="true" + 0 focusable + 0 heading inside it + the only heading is the passed h1 + children render
  - test_auth_shell_axe_clean: render AuthShell with a child h1+form / jsdom-axe (color-contrast off) reports no violations (catches aria-hidden-focus / heading-order)
  - test_login_form_card_and_styled_sso: render <LoginForm/> / assert fields wrapped in [data-slot="auth-card"] + email/password labels resolve + submit Button named "Log in" + SSO is an <a href="/api/auth/oidc/login"> with buttonVariants class (inline-flex) and name /sso/i
  - test_signup_form_card_no_sso: render <SignupForm/> / assert fields wrapped in [data-slot="auth-card"] + tenant_name/email/password labels resolve + submit Button named "Sign up" + NO link matching /sso/i
  - test_login_page_uses_authshell: render(await LoginPage({searchParams:Promise.resolve({})})) / assert [data-slot="auth-brand"] present + exactly ONE <form> + page <h1> "Sign in to your account" unchanged + no h1→h3 skip (no h3 in form card)
  - test_signup_page_uses_authshell: render(<SignupPage/>) / assert [data-slot="auth-brand"] present + exactly ONE <form> + page <h1> "Create your account" unchanged
</test_plan>

Tests live in: `tests/design-system/auth-pages-redesign.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/auth-shell.tsx` `apps/dashboard/components/ui/index.ts` `apps/dashboard/components/auth/LoginForm.tsx` `apps/dashboard/components/auth/SignupForm.tsx` `apps/dashboard/app/(auth)/login/page.tsx` `apps/dashboard/app/(auth)/signup/page.tsx`
Strategy (ordered batches): 1. NEW `auth-shell.tsx` (split-screen: decorative `<aside data-slot="auth-brand" aria-hidden hidden lg:flex>` Hexagon + "Hydroa" + tagline, and `<main>` content column rendering children) + export from ui/index.ts. 2. Restyle `LoginForm` — wrap fields in `<Card data-slot="auth-card"><CardContent>`, swap raw `<input>`→`<Input>`, raw `<button>`→`<Button>`, SSO `<a>`→`<Button asChild variant="outline"><a …>`; keep ALL hooks/state/handlers byte-identical. 3. Restyle `SignupForm` likewise (no SSO). 4. Wire `login/page.tsx` + `signup/page.tsx` to render `<AuthShell>…children…</AuthShell>` (AuthShell now owns the `<main>`; remove the page's own `<main>`).
Safety rule (feature-specific): presentation-only — NO change to any POST route/body, Zod schema/message, error handling, redirect, label text, id, or accessible name. The `<main>` landmark moves into AuthShell so exactly one `<main>` and one `<form>` remain per page.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only (Card/CardContent/Input/Button from @/components/ui, Hexagon from lucide-react — all already deps); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `vitest run` 43 files / 329 tests PASS (incl. the 6 new auth-pages-redesign tests + frozen tests/login.test.tsx + tests/signup.test.tsx + bff-forms + oidc relay suites). `tsc --noEmit` exit 0; `eslint` clean.
- [x] coverage did not decrease — presentation-only restyle; floor lines:80 held (components/**+lib/**). The dense frozen behavioral suites still execute every code path; no logic removed.
- [x] no test or contract was altered during build — tamper tripwire intact (re-snapshot tests→advance was content-identical for the red suite + frozen §3); only the artifact re-snapshot for tsbuildinfo. §3 unchanged.
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet subagent) returned VERDICT: EARNED, all 5 checks CLEAN: zero behavioral drift (POST routes/bodies, Zod messages, 401/409 handling, router.push("/keys"), SSO href, aria-labels, button text, ids — all byte-identical via git diff), meaningful non-vacuous assertions (data-slot queries, buttonVariants `inline-flex` on the SSO `<a>`, tagName==="A", exact href, h3-count==0), color-contrast-disabled-in-jsdom is legitimate (no layout engine; e2e covers it).
- [x] concurrency / timing of the risky operation is safe — N/A (no new IO; the existing fetch→push flow is unchanged). Presentation only.
- [x] no exposed secrets, injection openings, or unexpected dependencies — refute-read CLEAN: sso_error hint still never rendered (presence→boolean only); SSO href is a string literal; deps used (Card/CardContent/Input/Button, Hexagon) are pre-existing in the allowlist.
- [x] layering & dependencies follow CONVENTIONS.md — AuthShell lives in components/ui (DS layer); pages compose it; forms consume DS primitives via the @/components/ui barrel — same layering as the other v23 surfaces.
- [x] a person reviewed and approved the change — Tin Dang, standing auto-mode authorization (2026-06-16); auto-gated on complete evidence + EARNED refute-read, no security/architecture residue to escalate.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `AuthShell` exported from `components/ui/index.ts:52`, imported+used by both `app/(auth)/login/page.tsx` and `app/(auth)/signup/page.tsx`; Card/CardContent/Input/Button used in both forms' render trees (refute-read confirmed).
- [x] DEAD-CODE (code) — no orphaned/unused symbol; refute-read confirmed no unused imports introduced.
- [x] SEMANTIC (prose / non-code) — N/A (code task); the §3 contract + scenarios were read in full and the build matches them.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (standing auto-mode authorization, 2026-06-16) · date: 2026-06-16

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): login/signup submit error-rate (401/409) unchanged; the e2e-a11y real-Chromium axe pass over /login stays at zero serious/critical (the brand-panel decision is the one to watch); SSO click-through rate to the OIDC relay.
Spec delta for the next loop: /signup is NOT covered by the e2e-a11y real-Chromium axe spec (only /login is) — a follow-up could add /signup to the AUTHED-free public scan so the brand-panel contrast/landmark decision is browser-verified on both auth surfaces.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [UDD · folded] A shared "shell" component (AuthShell) is the right seam for split-screen brand chrome: it OWNS the single `<main>` landmark so each page keeps exactly one main + one form, and the decorative panel is `aria-hidden` + heading-free + focusable-free so it satisfies BOTH jsdom (no CSS engine, so `hidden lg:flex` does not hide it) and real-Chromium axe (which skips aria-hidden subtrees incl. color-contrast) (evidence: test_auth_shell_brand_panel_decorative + jsdom-axe green; the brand panel uses a designed bg-primary/text-primary-foreground pair regardless).
- [UDD · folded] `Button asChild` (Radix Slot) is the canonical way to give a real navigation `<a>` button styling without turning it into a `<button>`: the SSO link keeps href + role=link + accessible name while gaining buttonVariants classes (evidence: test_login_form_card_and_styled_sso asserts tagName==="A" + href + `inline-flex`).
- [ADD · folded] THIRD recurrence of the gitignored-artifact scope-baseline papercut (coverage in task 4, tsbuildinfo in task 5, tsbuildinfo again here): `tsc --noEmit` between the tests→build snapshot and the gate regenerates `tsconfig.tsbuildinfo` → `scope_violation`. Workaround is delete-artifact + re-snapshot (tests→advance) + run ONLY `npm test` for the gate. Three strikes ⇒ the engine fix should ship: extend the scope-walk exclusion to gitignored build artifacts (`coverage/`, `*.tsbuildinfo`) (evidence: WARN `touched outside §5 Scope: apps/dashboard/tsconfig.tsbuildinfo`; cleared by re-snapshot, check 39/0).
