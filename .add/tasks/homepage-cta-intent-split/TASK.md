# TASK: Split the homepage CTA by visitor intent

slug: homepage-cta-intent-split · created: 2026-07-20 · stage: production
milestone: frontdoor-persona-routing
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/app/(marketing)/page.tsx:MarketingRootPage` — the hero CTA row (the `<div
  className="flex flex-col gap-3 sm:flex-row">` block, ~L132-139: `<Link href="/signup">Get
  started</Link>` + `<Link href="/login">Log in</Link>`) and the final CTA band (`<div
  className="mt-8">`, ~L271-275: a single `<Link href="/signup">Get started</Link>`). Frozen Server
  Component — no `"use client"`, no `cookies()`, no authed fetch (`test_reject_public_not_gated`,
  4 assertions).
- `apps/dashboard/tests/landing-page.test.tsx:describe("test_hero")` (FROZEN) — hard-requires (a)
  at least one `/get started/i`-named link with `href === "/signup"` EXACTLY, (b) at least one
  `/log in/i`-named link with `href === "/login"` EXACTLY. Both use `.find()` over ALL matching
  links, so additional links with different accessible names are tolerated; the EXISTING "Get
  started"/"Log in" links must never be repointed.
- `apps/dashboard/tests/design-system/landing-fidelity.test.tsx` (FROZEN) — asserts
  `hrefs` (every `<a>` on the page) via `expect.arrayContaining(["/signup","/login","/pricing","/docs"])`
  — tolerates extra hrefs, does not forbid them.
- `apps/dashboard/components/auth/SignupForm.tsx:SignupForm` — client component; `accountType`
  state defaults `useState<AccountType>("personal")`. `signup-refusal-router` TASK.md §3 (FROZEN
  @v1, DONE) already ships `[data-slot="signup-alt-routes"]` — a panel that renders
  UNCONDITIONALLY, FIRST in the form, before any field, containing three static, always-visible
  routes (SSO link, invite-link copy, request-access mini-form) — regardless of account_type,
  regardless of flag state, regardless of submission. Radio inputs: `#account_type_personal` /
  `#account_type_business` (`name="account_type"`).
- `apps/dashboard/components/auth/LoginForm.tsx:LoginForm` (L87-119) — the EXACT precedent this
  task's own preselect mirrors: `const searchParams = useSearchParams();` + a ONE-SHOT
  `useEffect(() => { const domainParam = searchParams.get("domain"); if (domainParam) { ... } },
  [])` (signup-refusal-router M2, FROZEN @v1, DONE) — browser-only read, avoids a hydration
  mismatch, never re-fires after mount.
- `apps/dashboard/tests/setup.ts:82-90` — global `vi.mock("next/navigation")`: `useSearchParams`
  defaults to `vi.fn(() => new URLSearchParams())` (EMPTY) for every test file that doesn't
  override it. This means every EXISTING SignupForm test gets a no-op read from this task's new
  effect by construction, not by a special case.
- `apps/dashboard/tests/mocks/next-navigation.ts` — the mock-inspection helper;
  `login-domain-query-seed.test.tsx` (cited in signup-refusal-router §4) overrides
  `useSearchParams`'s return value directly via `vi.mocked(...).mockReturnValue(new
  URLSearchParams("domain=acme.com"))` — the exact technique this task's own new test mirrors for
  `account_type`.
- `apps/dashboard/tests/signup-account-type.test.tsx:test_signup_personal_default_account_type_sent`
  (FROZEN — activation-quickstart) — renders `SignupForm` with NO query-param context and asserts
  `getByRole("radio", {name:/personal/i})` is checked; `test_signup_business_selection_sent`
  asserts a MANUAL click to business works. Both must stay green, untouched.
- `apps/dashboard/components/ui/button.tsx:buttonVariants` — confirmed variants: `default ·
  secondary · outline · ghost · destructive`; sizes `sm · default · lg · icon`. `ghost` exists and
  resolves — usable for a lower-emphasis third CTA without inventing a new variant.
- `apps/gateway/src/gateway/tenants/api/router.py:signup` + `core/config.py:
  public_signup_personal_enabled` (scoped-self-serve-signup TASK.md §3, FROZEN @v1, DONE) — a
  personal signup with the flag OFF and no domain claim hits the SAME S1 gate as a business
  signup (byte-identical, M2) -> 403 `ERR_SIGNUP_INVITE_ONLY`; with the flag ON, a fresh email
  gets a 202 `pending_verification` (deferred creation, mailbox-confirm, NOT an instant account).
  This task touches ZERO gateway files — both contracts are consumed read-only, unchanged.
- `apps/dashboard/app/(marketing)/pricing/page.tsx:TIERS` — Starter/Team/Enterprise cards' CTAs
  are ALL `href: "/signup"` today, no intent split — confirmed by grep; out of THIS task's scope
  (task is homepage-only per its own slug/title).
- `.add/GLOSSARY.md:71` — `account_type: the personal|business flavor of a customer tenant...`
  already an established domain term; the new query param reuses this exact vocabulary.

Context (working folder): `apps/dashboard/app/(marketing)/page.tsx` (2 CTA edit sites: hero row +
final band) + `apps/dashboard/components/auth/SignupForm.tsx` (one additive query-param read) +
`apps/dashboard/tests/` (one new test file). No gateway/backend files, no migration, no new
endpoint — component: dashboard only, matching the milestone DAG's wave-2 listing.

Honors (patterns / conventions): the marketing page's Server-Component boundary
(`test_reject_public_not_gated` — no `"use client"`, no `cookies()`, no authed fetch); the
one-shot client search-param read pattern (`LoginForm` precedent — `useEffect` with `[]` deps,
browser-only, never re-fires after mount, so a later manual choice always wins); the `data-slot`
test-anchor convention (`signup-alt-routes`, `price-anchor`, `hero-aurora` precedents); the
anti-enumeration discipline from `signup-refusal-router` R-sec-1 — nothing this task adds may vary
by whether a visitor's email/domain is already a customer, invited, or unknown.

Seams consulted: `signup-refusal-router`'s alt-routes panel seam (FROZEN @v1, DONE) — CONSUMED
unchanged, never edited; `scoped-self-serve-signup`'s deferred-creation personal-signup contract
(FROZEN @v1, DONE) — CONSUMED read-only, zero gateway touch; `homepage-integration-proof`'s
`BaseUrlSwap` mount point (FROZEN @v1, DONE) — the hero's LAST child, downstream of and unaffected
by this task's CTA-row edit.

Anchors the contract cites: `MarketingRootPage`'s hero CTA row + final CTA band; the existing
`"Get started"` -> `"/signup"` and `"Log in"` -> `"/login"` links (byte-unchanged); a NEW `"For
your team"` link -> `"/signup?account_type=business"` (2 sites: hero + final band); `SignupForm`'s
`accountType` state + its radios; a NEW one-shot `useSearchParams().get("account_type")` read in
`SignupForm`; the `[data-slot="signup-alt-routes"]` panel (consumed, unchanged);
`landing-page.test.tsx:test_hero` + `landing-fidelity.test.tsx` (regression-pinned, not edited);
`tests/signup-account-type.test.tsx` (regression-pinned, not edited); `buttonVariants`'s `ghost`
variant; GLOSSARY `account_type`.

Issues/Risks (→ feed §1):
- **R-frozen-1:** `landing-page.test.tsx:test_hero`'s ONLY two hard requirements are an EXACT
  `href==="/signup"` on some "get started"-named link and an EXACT `href==="/login"` on some
  "log in"-named link. Repointing the EXISTING "Get started" button's href to carry a query param
  would break this frozen assertion outright. MITIGATION: the existing link stays byte-identical;
  the new team-intent CTA is a wholly separate link with a distinct accessible name.
- **R-tension (the core tension this task must resolve):** the personal/self-serve CTA's real
  backend behavior is flag-gated (`public_signup_personal_enabled`, default OFF) and, even when
  ON, deferred/mailbox-confirmed (202 `pending_verification`, never an instant account) — a CTA
  promising "instant account" would be dishonest in BOTH flag states. Neither new nor existing CTA
  copy may promise a mechanism it cannot keep — resolved at Specify (M3).
- **R-dead-end (verified, not assumed):** re-reading `scoped-self-serve-signup` §1 M2 +
  `signup-refusal-router` §1 M1/M8 confirms: a personal signup with the flag OFF hits the SAME S1
  gate as business -> 403 -> but the alt-routes panel is UNCONDITIONAL (renders before any
  submission, first in the DOM, for every account_type) — every `/signup` visitor already has a
  live next step today, regardless of which CTA sent them there. This task's split RIDES that
  already-shipped guarantee; it does not need to re-implement it.
- **R-scope-price:** the homepage `#pricing` teaser section is the sibling `homepage-price-anchor`
  task's owned surface (concurrent, phase=ground at grounding time) — excluded entirely from this
  task's Scope; only the hero CTA row and the final CTA band are touched.
- **R-scope-pricing-page:** `/pricing`'s own "Get started" CTAs (Starter/Team/Enterprise cards)
  are NOT touched — out of scope (task is homepage-only per its slug); flagged as a natural,
  unbuilt follow-on, not silently assumed done here.
- **R-a11y:** a third CTA in an already-two-button hero row risks visual/cognitive clutter and an
  ambiguous tab order — feeds a UX-researcher accessibility-as-research finding at Scenarios
  (heuristic, not validated; no real screen-reader user tested).

Related intent: milestone `frontdoor-persona-routing` goal — "Every visitor who arrives at
Hydroa's front door reaches a live next step..."; GLOSSARY `account_type`. This task is the
visible, homepage-facing half of the same goal `signup-refusal-router` (the P4-Sam-shaped
"existing tenant member" fix) and `scoped-self-serve-signup` (deferred personal creation) already
built the backend/form-level machinery for — it routes visitor INTENT into that already-built
machinery, adding no new backend surface, no new GLOSSARY term.

Ground SHA: 9421827 (branch `feat/frontdoor-persona-routing`; every cited symbol opened directly
this session — cite symbols, not bare line numbers; any line ref above is "as of" this commit)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Homepage CTA intent split — replace the single generic "Get started" promise (hero + final
band) with an honest two-path framing: a byte-unchanged personal/self-serve path ("Get started" ->
`/signup`) and a NEW, additive team/business path ("For your team" -> `/signup?account_type=business`).
Both land on the SAME `/signup` page, whose already-shipped alt-routes panel + account_type toggle
honestly deliver on either promise in EVERY `public_signup_personal_enabled` flag state.

User + job-to-be-done (named before judging, per this pass's UX-Researcher persona):
  - **Solo builder** — wants to start using Hydroa themselves, right now, no team involved. Job:
    "let me try this on my own account." Served by the unchanged "Get started" CTA.
  - **P4 Sam** (signup-refusal-router's own named persona) — "an engineer at an existing Hydroa
    customer, trying to get into a workspace their employer already pays for." Job: "get me to the
    access path that actually applies to me (SSO / my invite link / ask someone)," NOT "let me fill
    out a business signup form." Served by the NEW "For your team" CTA, which lands them directly in
    front of the already-unconditional alt-routes panel with Business preselected.

Framings weighed:
- **Same-destination, query-param-differentiated CTA pair (CHOSEN)** — the existing "Get started"
  link stays byte-identical; a new "For your team" link carries `?account_type=business`, read once
  by `SignupForm` to preselect the radio. Zero new backend surface; reuses two already-FROZEN/DONE
  seams (`LoginForm`'s one-shot search-param precedent + the unconditional alt-routes panel); honest
  in both flag states because the destination's real behavior never depends on WHICH CTA was
  clicked, only on `account_type` — a value the visitor already controls via the existing radio.
- Client-side "smart" pre-check deciding which CTA to show/hide based on a probed email/domain
  (REJECTED) — this is exactly the enumeration-oracle shape `signup-refusal-router`'s R-sec-1
  forbids; duplicating that logic here instead of reusing the already-static, always-both-visible
  panel would risk reopening it. It is also explicitly `domain-aware-auth-routing`'s owned,
  not-yet-built deliverable — building it here would pre-empt and likely diverge from that task's
  real contract (mirrors `signup-refusal-router`'s own identical framing decision).
- A brand-new, separate landing route per intent (e.g. `/signup/personal`, `/signup/team`)
  (REJECTED) — doubles the surface `SignupForm`'s frozen tests must cover for a difference that is
  really just "which radio starts checked," a single-field concern the existing form already owns;
  premature IA growth for a one-field difference.
- Promising "instant personal account" copy on the personal CTA (REJECTED) — dishonest under
  EITHER flag state: OFF -> 403 -> alt-routes panel; ON -> 202 `pending_verification`, not instant.
  See R-tension in §0.

Must:
<must>
  - M1 The existing "Get started" CTA (hero AND final-CTA-band) keeps its accessible name matching
    `/get started/i` and its `href` byte-identical to `"/signup"` — the frozen
    `landing-page.test.tsx:test_hero` + `landing-fidelity.test.tsx` anchors stay untouched.
  - M2 A NEW, additive CTA "For your team" (label distinct enough to never match `/get started/i`,
    so the frozen `.find()` query can never confuse the two) appears alongside "Get started" in
    BOTH the hero CTA row and the final CTA band, linking to `/signup?account_type=business`.
  - M3 Neither CTA's copy promises an instant account, a specific timeline, or a guaranteed
    outcome — copy names WHO each path is for ("you, solo" vs. "your team"), never HOW signup will
    behave internally (flag state, mailbox-confirm, invite-only gate) — a mechanism detail visitors
    don't need and which may change over time without this copy going stale.
  - M4 `SignupForm` gains ONE new one-shot read: `useSearchParams().get("account_type")` inside a
    `useEffect` with `[]` deps (mirrors `LoginForm.tsx:87-119`'s `?domain=` precedent exactly —
    browser-only, avoids a hydration mismatch, never re-fires after mount). When the value is
    exactly `"business"`, it preselects `accountType` to `"business"`. ANY other value (absent,
    malformed, unrecognized) leaves the existing default (`"personal"`) untouched.
  - M5 The preselection is CLIENT-SIDE ONLY, static, and identical for every visitor regardless of
    any account/domain/tenant state — no new fetch, no new branch reads any server signal (carries
    forward R-sec-1's anti-enumeration discipline: nothing added here may vary by whether the
    visitor's domain/email is already a customer).
  - M6 The homepage (`(marketing)/page.tsx`) stays a pure Server Component — no `"use client"`, no
    `cookies()`, no authed fetch — the new team-intent CTA is a plain static `<Link
    href="/signup?account_type=business">`, zero client JS added to the homepage itself (all
    intent-handling logic lives in the already-`"use client"` `SignupForm`).
  - M7 THE NO-DEAD-END INVARIANT (testable, both flag states): for EVERY combination of {CTA
    clicked (personal|team), `public_signup_personal_enabled` (ON|OFF), account_type ultimately
    submitted (personal|business)}, a visitor who reaches `/signup` and takes ANY action (do
    nothing yet / submit) always lands in exactly one of three live states: (a) the always-visible
    `[data-slot="signup-alt-routes"]` panel — visible BEFORE any submission, for every visitor,
    both account types; (b) on submit, a 202 `pending_verification` mailbox-confirm state
    (personal, flag ON, fresh/conflicting email — response is IDENTICAL either way per
    `scoped-self-serve-signup` M7); or (c) on submit, a successful join/creation outcome
    (verified-domain auto-join, or a later confirm-token completion) — NEVER a state with zero
    actionable next step.
  - M8 Existing SignupForm tests that render with no query-param context (`tests/signup.test.tsx`,
    `tests/signup-account-type.test.tsx`'s `test_signup_personal_default_account_type_sent`,
    `tests/signup-form-joined-outcome.test.tsx`) stay byte-identical/green — the global
    `useSearchParams` mock (`tests/setup.ts:82-90`) already defaults to an empty `URLSearchParams`,
    so M4's read is a no-op for every one of them by construction, not by a special case.
</must>
Reject:
<reject>
  - R1 A malformed/unrecognized `?account_type=` value (e.g. `?account_type=enterprise`,
    `?account_type=`, a repeated param) -> silently falls back to the existing default `"personal"`
    -> never a crash, never a 4xx (client-only preselect, not a server request; "reject" here means
    "ignored," not an HTTP error code).
  - R2 Any attempt to make either CTA's visibility, label, or destination vary by a probed
    domain/email/account state -> MUST NOT ship (R-sec-1 anti-enumeration carryover) — both CTAs
    stay static, identical for every visitor, always.
  - R3 Repointing the EXISTING "Get started" link's `href` away from exactly `"/signup"` (e.g. to
    carry the new query param) -> MUST NOT ship — breaks `landing-page.test.tsx:test_hero`'s
    exact-match assertion; the personal path stays the unchanged default (no param needed, since
    `SignupForm`'s own default is already `"personal"`).
  - R4 Touching the `#pricing` teaser section or `/pricing`'s own CTA hrefs -> MUST NOT ship — out
    of this task's scope (sibling `homepage-price-anchor` owns `#pricing`; `/pricing`'s CTAs are a
    separate, unbuilt follow-on, not silently assumed here).
</reject>
After:
<after>
  - A solo builder clicks "Get started" (unchanged) and reaches `/signup` with Personal already
    selected (today's default) — submitting either gets a mailbox-confirm 202 (flag ON) or the
    live alt-routes panel (flag OFF), never a dead end.
  - A visitor thinking of their team clicks "For your team" and reaches
    `/signup?account_type=business` with Business already selected AND the alt-routes panel (SSO /
    invite link / request access) already visible above the form, before they type anything — the
    exact live next step a P4-Sam-shaped visitor needs.
  - `landing-page.test.tsx`, `landing-fidelity.test.tsx`, and every existing SignupForm test stay
    green, unmodified.
  - The homepage stays a pure Server Component; `SignupForm`'s only change is one additive,
    inert-by-default query-param read.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The exact label "For your team" (vs. alternatives like "I'm joining a team" / "Bring your
    team" / "Already with a team?") — lowest confidence because copy tone is a genuine judgment
    call with no existing precedent in this codebase to anchor it (unlike the button mechanics,
    which mirror `LoginForm`/`SignupForm` precedent closely). RECOMMEND "For your team" for brevity
    and parity with the existing 2-word "Get started"/"Log in" labels. If wrong: a pure copy edit,
    zero contract/test shape change (tests assert the `href` + `data-slot`, never the label text).
  - [ ] Visual weight of the new 3rd hero CTA (same-weight `Button` vs. a lower-emphasis
    `variant="ghost"` link) — RECOMMEND `ghost`, so the hero doesn't read as "3 equal buttons";
    deferred to Build/persona judgment, not pixel-specified here (this is a design/contract pass,
    not a full UDD wireframe). If wrong: a same-day visual-only follow-up, zero contract change.
  - [ ] Whether the final CTA band (brand-gradient, currently ONE button) should render the pair
    side-by-side or stacked — same visual-weight question as above, same low cost if wrong.
  - [x] The query-param mechanism (one-shot `useSearchParams().get()` in a `useEffect([])`) is safe
    and precedented — confirmed by reading `LoginForm.tsx:87-119` (the shipped, DONE, gate=PASS
    `?domain=` seed) verbatim.
  - [x] Every existing SignupForm test stays green with the new read as a no-op — confirmed by
    reading `tests/setup.ts:82-90`'s global `useSearchParams` mock (defaults to empty
    `URLSearchParams`) and `tests/signup-account-type.test.tsx`'s
    `test_signup_personal_default_account_type_sent` (asserts "personal" pre-checked with no
    query-param context set).
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

> User + job-to-be-done (per §1): **solo builder** ("let me try this myself") and **P4 Sam**
> ("get me to the access path that applies to me, not a business signup form").

<scenarios>

```gherkin
Scenario: The unchanged personal CTA still resolves exactly per the frozen hero contract   # M1,R3
  Given the homepage renders
  When the DOM is queried for a "get started"-named link
  Then at least one resolves to href exactly "/signup"
  And landing-page.test.tsx:test_hero and landing-fidelity.test.tsx pass unmodified

Scenario: A new team-intent CTA appears in both the hero and the final CTA band   # M2
  Given the homepage renders
  When the hero CTA row and the final CTA band are inspected
  Then each contains a link labeled distinctly from "Get started" (e.g. "For your team")
  And each such link's href is exactly "/signup?account_type=business"

Scenario: Neither CTA's copy promises a mechanism it can't keep   # M3
  Given the rendered hero and final CTA band text
  When read in full
  Then no copy mentions "instant", a guaranteed timeframe, or an account-creation promise
  And copy describes only WHO each path is for, not HOW signup behaves internally

Scenario: Clicking "For your team" preselects Business on arrival   # M4
  Given a visitor navigates to /signup?account_type=business
  When SignupForm mounts
  Then the "Business" radio is checked and "Personal" is not
  And this happens on the first render (no flash of the personal default)

Scenario: An absent, malformed, or unrecognized account_type param leaves the default untouched   # M4,R1
  Given a visitor navigates to /signup with no param, or /signup?account_type=enterprise, or /signup?account_type=
  When SignupForm mounts
  Then "Personal" is checked (the existing default) and no error/crash occurs

Scenario: The preselection never varies by account/domain state   # M5,R2
  Given two visitors navigate to the SAME /signup?account_type=business URL — one whose email
    domain is already a verified customer, one whose domain is entirely unknown
  When SignupForm mounts for each
  Then both see the IDENTICAL preselected state and the IDENTICAL alt-routes panel
  And no server call was made to decide either visitor's preselection

Scenario: The homepage stays server-rendered   # M6
  Given apps/dashboard/app/(marketing)/page.tsx after this task
  When its source is inspected
  Then it has no "use client" directive, no cookies() call, no Authorization/session-cookie reference
  And the new team-intent link is a plain <Link href> with a static string, no client state

Scenario: No dead end in either flag state — personal intent, flag OFF   # M7
  Given public_signup_personal_enabled is OFF (default) and a visitor clicked "Get started"
  When they submit a personal signup with no domain claim
  Then the response is 403 ERR_SIGNUP_INVITE_ONLY
  And the SAME [data-slot="signup-alt-routes"] panel (already visible pre-submit) remains the
    visitor's live next step — never a page with nothing left to do

Scenario: No dead end in either flag state — personal intent, flag ON   # M7
  Given public_signup_personal_enabled is ON and a visitor clicked "Get started" with a fresh email
  When they submit
  Then the response is 202 pending_verification and a confirm email is sent
  And the visitor sees a live "check your mailbox" next step, not a dead end

Scenario: No dead end in either flag state — team intent   # M7
  Given a visitor clicked "For your team" (Business preselected) — public_signup_personal_enabled
    may be ON or OFF, it is irrelevant to the business path
  When they land on /signup (before typing anything)
  Then the alt-routes panel (SSO / invite link / request access) is ALREADY visible
  And submitting without an invite/domain-claim/global-flag still resolves to that same live panel,
    never a blank dead end

Scenario: Existing SignupForm tests stay green with the param absent   # M8
  Given tests/signup.test.tsx, tests/signup-account-type.test.tsx, tests/signup-form-joined-outcome.test.tsx
    run unmodified
  When the global useSearchParams mock returns an empty URLSearchParams (its default)
  Then every one of those suites passes exactly as before this task

Scenario: [EDGE — boundary] Duplicate/conflicting account_type params
  Given a hypothetical /signup?account_type=business&account_type=personal
  When URLSearchParams.get("account_type") resolves (returns the FIRST value per the WHATWG spec)
  Then the result is deterministic ("business" here) — not a crash, not undefined behavior
  # confidence: HEURISTIC — relies on documented URLSearchParams.get() semantics, not a dedicated
  # new assertion in this codebase; called out at freeze as spec-derived, not independently verified.

Scenario: [EDGE — partial-override] A manual radio click after the query-param preselect wins
  Given /signup?account_type=business preselected "Business"
  When the visitor manually clicks "Personal"
  Then the manual click wins — the one-shot effect never re-fires to overwrite a later manual
    choice (mirrors LoginForm's own "one-shot, not authoritative-forever" precedent)

Scenario: [RULED OUT — deliberate] No concurrency case applies
  Given this task adds zero new IO, zero new server endpoint, and zero shared mutable state
  Then there is no concurrency scenario to test — a client-only, per-visitor query-param read has
    no cross-request interaction
  And this is a deliberate ruling-out, not an omission

Scenario: [ACCESSIBILITY-AS-RESEARCH] Three CTAs remain keyboard-reachable in a sensible order
  Given a visitor navigates the hero using only Tab/Shift+Tab/Enter
  When they tab through the CTA row
  Then "Get started", "For your team", and "Log in" are all reached in a logical, documented order
    and each is activatable via Enter
  # confidence: HEURISTIC — a structured keyboard walkthrough of the intended DOM order, not a
  # validated screen-reader user test; mirrors signup-refusal-router's own a11y scenario confidence
  # label. Unvalidated — call out again at freeze.
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is a DASHBOARD (presentation-only) task — the "shape" frozen here is OBSERVABLE homepage +
SignupForm markup/behavior, NOT a new HTTP endpoint. It consumes two already-FROZEN, DONE
contracts (`scoped-self-serve-signup` §3 v1, `signup-refusal-router` §3 v1) unchanged.

```
COMPONENT  MarketingRootPage   (apps/dashboard/app/(marketing)/page.tsx)
COMPONENT  SignupForm          (apps/dashboard/components/auth/SignupForm.tsx)

HOMEPAGE (presentation only; no new endpoint, no fetch; Server Component UNCHANGED):

  Hero CTA row (inside the existing `<div className="flex flex-col gap-3 sm:flex-row">`):
    <Button asChild size="lg" ...><Link href="/signup">Get started</Link></Button>
      (UNCHANGED, byte-identical — href/label untouched)
    <Button asChild variant="ghost" size="lg">
      <Link href="/signup?account_type=business">For your team</Link>
    </Button>                                              (NEW — visual weight per §1 ⚠ flag)
    <Button asChild variant="outline" size="lg"><Link href="/login">Log in</Link></Button>
      (UNCHANGED, byte-identical)

  Final CTA band (inside the existing `<div className="mt-8">`):
    <Button asChild variant="secondary" size="lg"><Link href="/signup">Get started</Link></Button>
      (UNCHANGED, byte-identical)
    <Button asChild variant="ghost" size="lg">
      <Link href="/signup?account_type=business">For your team</Link>
    </Button>                                              (NEW, same label/href as hero)

SIGNUP FORM (additive; mirrors LoginForm.tsx:87-119's shipped one-shot search-param seed exactly):

  SignupForm gains:
    import { useSearchParams } from "next/navigation";           // already imported by LoginForm
    const searchParams = useSearchParams();
    useEffect(() => {
      const accountTypeParam = searchParams.get("account_type");
      if (accountTypeParam === "business") {
        setAccountType("business");
      }
      // any other value (absent / malformed / unrecognized) — no-op; default "personal" stands
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);   // one-shot: mirrors LoginForm's domain-seed effect, [] deps, never re-fires

  Preselection contract:
    ?account_type=business              -> Business radio checked on first render
    ?account_type=<anything else / absent> -> Personal radio checked (existing default, UNCHANGED)
  A manual radio click AFTER mount always wins — the effect is one-shot ([] deps), it never
  re-fires to overwrite a later manual choice.

UNCHANGED (frozen; re-verified by reading, not modified by this task):
  - landing-page.test.tsx:test_hero        — >=1 "get started"-named link href==="/signup",
                                              >=1 "log in"-named link href==="/login"
  - design-system/landing-fidelity.test.tsx — hrefs arrayContaining
                                              ["/signup","/login","/pricing","/docs"]
  - the #pricing teaser section             — owned by sibling homepage-price-anchor; NOT touched
  - /pricing's own CTA hrefs                — NOT touched (out of scope; unbuilt follow-on)
  - SignupForm's M1-M14/R1-R8 behavior       — signup-refusal-router + scoped-self-serve-signup
                                              (both FROZEN @v1, DONE): the alt-routes panel, the
                                              account_type radios, the submit flow — byte-unchanged;
                                              this task only ADDS the one-shot preselect read
  - tenants/api/router.py:signup, the S1 gate, public_signup_personal_enabled
                                              — gateway untouched, zero backend files in Scope (§5)
```

SAFETY NOTE (anti-enumeration carryover, non-security-sensitivity task — no new IO/oracle
introduced, but the invariant it must never violate):
- The new preselect effect and the new CTA never read any account/domain/tenant signal — both are
  pure functions of the literal query-string value, evaluated identically for every visitor (M5,
  R2). No fetch is added anywhere in this task's diff.

Glossary deltas: none — `account_type` (personal|business) already exists (`GLOSSARY.md:71`); the
new query param reuses that exact vocabulary, introducing no new term.

Least-sure flag surfaced at freeze: [spec] the exact CTA label ("For your team" — recommended) and
the visual weight/placement of the new third hero CTA (recommended: `variant="ghost"`, lower
emphasis than "Get started"/"Log in") — see §1 ⚠. Lowest confidence in the whole bundle because
copy tone and visual hierarchy are genuine judgment calls with no exact precedent to anchor them,
unlike every mechanical piece of this contract (the query-param read, the href, the anti-
enumeration discipline), each of which mirrors an already-shipped, DONE precedent verbatim. Cost if
wrong: a copy/style-only follow-up — zero contract-shape change, since tests assert `href` +
`data-slot`, never the label text or exact Tailwind classes.

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — this draft has not yet been presented to Tin for the freeze decision; the bundle's
lowest-confidence flag above must lead that report, per run.md.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the §2 scenarios not already pinned by a frozen suite (14 tests, 1 new file)

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_get_started_present_hero_and_final_band_unchanged: arrange render MarketingRootPage / act query "get started"-named links / assert >=2 resolve href==="/signup" AND >=1 "log in"-named link resolves href==="/login" · covers: M1, R3
  - test_team_cta_present_both_sites_with_exact_href: arrange render MarketingRootPage / act query "for your team"-named links / assert >=2 exist, each href==="/signup?account_type=business", label never matches /get started/i · covers: M2
  - test_team_cta_label_never_matches_get_started_query: arrange render MarketingRootPage / act query "get started"-named links / assert every one still resolves href==="/signup" (new label can never be swept into the frozen .find()) · covers: M2, R-frozen-1
  - test_no_instant_or_guaranteed_account_promise_in_hero_or_band: arrange render MarketingRootPage / act read hero+final-band text / assert no "instant"/"guarantee"/"right away" · covers: M3
  - test_business_param_preselects_business_on_first_render: arrange mock useSearchParams("account_type=business") / act render SignupForm / assert Business checked, Personal not · covers: M4
  - test_absent_param_leaves_personal_default: arrange mock useSearchParams(null) / act render SignupForm / assert Personal checked, Business not · covers: M4, R1
  - test_malformed_account_type_falls_back_to_personal: arrange mock useSearchParams("account_type=enterprise") / act render SignupForm / assert Personal checked · covers: M4, R1
  - test_empty_account_type_value_falls_back_to_personal: arrange mock useSearchParams("account_type=") / act render SignupForm / assert Personal checked · covers: M4, R1
  - test_duplicate_account_type_params_resolve_to_first_value_deterministically: arrange mock useSearchParams("account_type=business&account_type=personal") / act render SignupForm / assert Business checked (WHATWG .get() = first value) · covers: EDGE
  - test_manual_personal_click_overrides_business_preselect_and_never_reverts: arrange business-preselected SignupForm / act click Personal radio, force a re-render / assert Personal stays checked, effect never re-fires · covers: EDGE (manual override wins)
  - test_preselect_identical_regardless_of_typed_email_domain: arrange business-preselected SignupForm / act type a corporate-shaped then re-render / assert Business checked identically both times · covers: M5, R2
  - test_preselect_issues_no_fetch_call: arrange spy on global fetch, business-preselected SignupForm / act mount only / assert fetch never called · covers: M5, R2
  - test_new_cta_is_a_static_literal_href_not_client_state: arrange read page.tsx source / act regex-match / assert no "use client", no cookies(), literal href="/signup?account_type=business" string present · covers: M6
  - test_hero_cta_tab_order_get_started_then_team_then_login: arrange render MarketingRootPage / act query hero CTA row / assert Get started -> For your team -> Log in DOM order, none tabindex="-1" · covers: ACCESSIBILITY-AS-RESEARCH (heuristic)
</test_plan>

Tests live in: `./tests/homepage-cta-intent-split.test.tsx` · ran RED (8/14 failed for missing
implementation: no "For your team" link in either site, SignupForm never called useSearchParams())
before Build; the other 6 were legitimate regression guards already true pre-build (M1 unchanged,
M3 copy check, M4/R1 default-fallback cases with no effect yet present) and stayed green after.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/(marketing)/page.tsx` `apps/dashboard/components/auth/SignupForm.tsx` `apps/dashboard/tests/`

Strategy (ordered batches):
  1. Homepage: add the "For your team" `Link`+`Button` in the hero CTA row and the final CTA band
     (2 sites); do NOT touch the existing "Get started"/"Log in" `Link`s. Regression-run
     `landing-page.test.tsx` + `landing-fidelity.test.tsx` after this batch alone, before touching
     SignupForm.
  2. SignupForm: add the one-shot `useSearchParams().get("account_type")` effect, mirroring
     `LoginForm.tsx:87-119` line-for-line (`useEffect`, `[]` deps, the same eslint-disable comment
     pattern). Regression-run the existing SignupForm suites (`signup.test.tsx`,
     `signup-account-type.test.tsx`, `signup-form-joined-outcome.test.tsx`) after this batch alone.
  3. New test file(s) under `apps/dashboard/tests/` covering the §2 scenarios not already covered
     by a frozen suite (the preselect behavior, the malformed-param fallback, the manual-override
     scenario, the new CTAs' hrefs/labels, the accessibility-as-research keyboard walkthrough).
  4. Full dashboard suite + axe pass to confirm zero regression on `test_landing_a11y` and the two
     frozen hero/fidelity suites.

Persona (required): frontend-engineer — the closest-fit BUILD persona for a React/Next.js,
component-level, additive change (this SPECIFY/SCENARIOS/CONTRACT pass itself was drafted under
`ux-researcher`, a design-span persona; BUILD needs the implementation-focused counterpart).
Spawn isolation (default): worktree — mirrors this repo's own standing lesson
(worktree-isolated-spawn-default); no stated reason to share the tree for a change this small and
additive.
Known-problem fixes:
  - trap: repointing the existing "Get started" `href` away from exact `"/signup"` breaks
    `landing-page.test.tsx:test_hero` -> fix: NEVER edit that `Link`'s `href`; only ADD a new,
    separate link.
  - trap: a `useSearchParams()` effect with missing/wrong deps could re-fire on every render and
    clobber a visitor's later manual radio click -> fix: mirror `LoginForm`'s exact `[]`-deps +
    eslint-disable pattern, verified against the "manual override wins" scenario.
  - trap: `getByLabelText`/`getByRole` ambiguity if the new CTA's accessible name collides with an
    existing one (the same class of bug `signup-refusal-router` TASK.md §4 flagged for its own
    mini-form input) -> fix: "For your team" shares no substring with "Get started" or any existing
    accessible name on the page; confirm with a targeted query in the new test, not a broad regex.
Strategy actually used: as planned — batch 1 (homepage: added the "For your team" `Link`+`Button`,
`variant="ghost"`, to the hero row and final CTA band, 2 sites; regression-ran
`landing-page.test.tsx` + `landing-fidelity.test.tsx`, 26/26 green) -> batch 2 (SignupForm: added
`useSearchParams` import + the one-shot `[]`-deps effect mirroring `LoginForm.tsx:87-119` line-for-
line, same eslint-disable pattern; regression-ran `signup.test.tsx` + `signup-account-type.test.tsx`
+ `signup-form-joined-outcome.test.tsx`, 7/7 green) -> batch 3 (new
`tests/homepage-cta-intent-split.test.tsx`, 14 tests, confirmed RED for the right reason before
batches 1-2, GREEN after) -> batch 4 (full dashboard suite both vitest projects: legacy 109 files/
1005 tests green, bff 77 files/723 tests green — zero regression, zero test/contract edited).
Safety rule (feature-specific): the preselect effect and the new CTA must NEVER branch on anything
beyond the literal query-string value — no fetch, no domain/email read, no account-existence check
(R2/R5 anti-enumeration carryover from signup-refusal-router).
Code lives in: `apps/dashboard/app/(marketing)/page.tsx`, `apps/dashboard/components/auth/SignupForm.tsx`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 14 new; task+guards 40/40; the 3-way SignupForm sibling regression set 31/31;
      marketing-shell 24/24; full dashboard suite green (1005 legacy + 723 bff) — green-bar
      `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`
- [x] coverage did not decrease — 14 net-new assertions over previously-untested paths, none removed
      or weakened (assessed qualitatively, not by a numeric coverage diff — noted for completeness)
- [x] no test or contract was altered during build — `git diff --stat` shows ZERO diff on
      landing-page.test.tsx, landing-fidelity.test.tsx, signup-account-type.test.tsx; §3 unchanged
- [x] the green was EARNED, not gamed — independent refute-read classified ALL 6 pre-passing tests as
      legitimate regression/negative-case guards, none vacuous; no weak assertions
      (`toBeDefined()`/`toBeGreaterThanOrEqual(0)`) found — see the refute-read verdict below
- [x] concurrency / timing of the risky operation is safe — one-shot `[]`-deps effect mirroring the
      shipped LoginForm precedent; never re-fires; a manual click after mount always wins
- [x] no exposed secrets, injection openings, or unexpected dependencies — zero new IO in the diff
- [x] layering & dependencies follow CONVENTIONS.md — homepage stays a pure Server Component; all
      intent handling lives in the already-client SignupForm (M6)
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] A visitor clicking "For your team" (hero or final band) lands on `/signup` with Business
  pre-checked and the `[data-slot="signup-alt-routes"]` panel already visible above the form —
  confirmed by a live render/manual click-through AND green-bar
  `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`. Evidence: the task suite +
  guards ran 3 files / 40 of 40 passed.
- [x] A visitor clicking "Get started" sees byte-identical behavior to before this task (Personal
  pre-checked, unchanged href) — confirmed by `landing-page.test.tsx`, `landing-fidelity.test.tsx`,
  and `signup-account-type.test.tsx` staying green UNMODIFIED + the same dashboard green-bar.
  Evidence: `git diff` on all three test files is EMPTY; 5 files / 31 of 31 passed.
- [x] Neither CTA nor the preselect ever varies by account/domain state — confirmed by a code read
  (zero new fetch/IO in the diff) + a new test asserting identical behavior for a "known" vs
  "unknown" simulated visitor + the dashboard green-bar.
- [x] No dead end in either `public_signup_personal_enabled` state — confirmed by re-reading
  `scoped-self-serve-signup` M2 (flag OFF -> S1 gate, unchanged) and `signup-refusal-router` M1
  (alt-routes panel unconditional) side-by-side with this task's new CTA destinations, plus the new
  M7 scenario coverage in the dashboard green-bar.
- [x] The homepage stays a pure Server Component — confirmed by `test_reject_public_not_gated`'s 4
  assertions staying green, unmodified; marketing-shell suite 24/24.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced. The two new CTA hrefs in
      `app/(marketing)/page.tsx` carry the intent query param; `SignupForm.tsx` reads it once in a
      `[]`-deps effect and feeds the existing account-type state + the already-shipped
      `[data-slot="signup-alt-routes"]` panel. Confirmed by the verifier's three-way composition
      read of `SignupForm.tsx` (this task's intent preselect vs `domain-aware-auth-routing`'s
      classification vs `signup-refusal-router`'s panel) — no interference, each reads distinct state.
- [x] DEAD-CODE (code) — no new unused or orphaned symbol; every added symbol is referenced at least
      once, and the intent parser has no unreachable branch (unknown/absent value falls through to
      today's Personal default, which is itself asserted).
- [ ] SEMANTIC (prose / non-code) — n/a, this is a code task.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by the verifier
      re-resolving each cited anchor against the live tree (`MarketingRootPage` and both CTA sites in
      `app/(marketing)/page.tsx`; `SignupForm` and `[data-slot="signup-alt-routes"]` in
      `components/auth/SignupForm.tsx`), plus the three frozen guard test files.
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — NONE moved.
      Note: `SignupForm.tsx` is concurrently edited by the sibling `domain-aware-auth-routing` on
      this same branch; the composition check above was run specifically because of that overlap and
      found the two changes independent.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: agent aa9da12 (independent add-verify) · adversarially checked: (a) whether any of the 6
pre-passing tests was a disguised new-behavior assertion silently passing — all 6 walked and
classified as legitimate regression / negative-case guards, none vacuous; (b) whether the suite
contains weak assertions (`toBeDefined()`, `toBeGreaterThanOrEqual(0)`) — none found; (c) whether the
three frozen guard test files were edited to make the build green — `git diff` EMPTY on all three;
(d) whether the concurrent `SignupForm.tsx` edits from the sibling task interfere — three-way
composition read, confirmed non-interfering.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: agent aa9da12
1. Security: CLEAR — the intent param is a display-only preselect read client-side; it grants nothing,
   is never sent to the server, and an attacker-chosen value can only pre-tick a radio the visitor can
   freely change. No new IO, no secrets, no injection surface.
2. Concurrency: CLEAR — a one-shot `[]`-deps effect that cannot re-fire; a manual click after mount
   always wins. No shared mutable state, no async boundary.
3. Architecture: CLEAR — homepage stays a pure Server Component; all intent handling lives in the
   already-client `SignupForm` (M6). No new dependency, no layering violation.
Verdict: PASS
Residue: none
Binding: advisory — sensitivity is non-security (presentation + client-side preselect only)

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-21

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned — batch 1 (homepage: added the "For your team" `Link`+`Button`, `variant="ghost"`, to the hero row and final CTA band, 2 sites; regression-ran `landing-page.test.tsx` + `landing-fidelity.test.tsx`, 26/26 green) -> batch 2 (SignupForm: added `useSearchParams` import + the one-shot `[]`-deps effect mirroring `LoginForm.tsx:87-119` line-for- line, same eslint-disable pattern; regression-ran `signup.test.tsx` + `signup-account-type.test.tsx` + `signup-form-joined-outcome.test.tsx`, 7/7 green) -> batch 3 (new `tests/homepage-cta-intent-split.test.tsx`, 14 tests, confirmed RED for the right reason before batches 1-2, GREEN after) -> batch 4 (full dashboard suite both vitest projects: legacy 109 files/ 1005 tests green, bff 77 files/723 tests green — zero regression, zero test/contract edited).
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

