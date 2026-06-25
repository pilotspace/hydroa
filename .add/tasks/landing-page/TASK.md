# TASK: Public landing page content

slug: landing-page · created: 2026-06-24 · stage: production
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
  - `apps/dashboard/app/(marketing)/page.tsx:MarketingRootPage` — the PLACEHOLDER landing built by marketing-shell
    (hero h1 + 2 CTAs inside `<main id="main">`). THIS task replaces it with the real multi-section landing CONTENT.
  - `apps/dashboard/components/marketing-shell.tsx:MarketingShell` — the FROZEN public chrome (header nav anchors
    `/#product` `/#pricing` `/#docs`, footer). Landing sections must carry matching `id`s (product/pricing/docs) so the nav anchors resolve. Do NOT modify the shell.
  - `apps/dashboard/components/ui/{card,button,badge}.tsx` — reusable primitives (Card/CardHeader/CardTitle/CardContent, Button, Badge) for the feature grid + CTAs.
  - `apps/dashboard/components/ui/index.ts` — primitive barrel export.
Context (working folder):
  - `apps/dashboard/app/globals.css` — Hydroa design tokens (--primary, --muted-foreground, --border, etc.) to reuse.
  - README.md — product positioning ("Multi-tenant AI proxy … per-tenant cost tracking, key governance, rate limiting, spend analytics, alerting, admin dashboard") = source copy for the value-prop + feature list.
  - Vitest + axe a11y harness (`tests/`), real `next build` gate.
Honors (patterns / conventions):
  - v23/v24 UI bar: WCAG-AA (one h1, ordered headings, landmarks already from shell), design tokens, Server Component (no client JS unless needed).
  - marketing-shell contract: landing is PUBLIC — no cookie, no authed fetch; renders inside `<main id="main">`.
Anchors the contract cites:
  - `app/(marketing)/page.tsx:MarketingRootPage` (the replaced landing)
  - section ids `#product` `#pricing` `#docs` (nav-anchor targets the shell already links)
  - `components/ui/card.tsx` Card primitives + `button.tsx` (feature grid + CTA building blocks)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Public landing page content (multi-section)
Framings weighed: enterprise value-prop landing reusing Card/Button primitives (chosen) · marketing-CMS (rejected, out of scope) · single-hero-only (rejected, too thin for an enterprise front door)
Must:
<must>
  - `app/(marketing)/page.tsx` renders a multi-section landing inside `<main id="main">`: HERO (one h1 + subhead + primary "Get started"→/signup + secondary "Log in"→/login), FEATURE grid (≥4 enterprise capabilities sourced from README: multi-provider routing · per-tenant cost tracking · key governance & BYOK · rate limiting & bandwidth pacing · spend analytics · alerting), a TRUST/why-enterprise band, and a final CTA band.
  - Sections carry the ids the FROZEN shell nav links to: `#product`, `#pricing`, `#docs` (anchor targets resolve — no dangling nav link).
  - Exactly ONE h1; heading order is monotonic (h1→h2→h3, no skips); shell landmarks (banner/main/contentinfo) preserved.
  - Public: NO cookie read, NO authed/react-query fetch; Server Component (no needless client JS).
  - Reuses Hydroa design tokens + the `Card`/`Button`/`Badge` primitives (no bespoke one-off styling drift).
</must>
Reject:
<reject>
  - More than one h1, or a skipped heading level -> "heading_order_violation"
  - A shell nav anchor (#product/#pricing/#docs) with no matching section id -> "dangling_nav_anchor"
  - Any cookie read / authenticated fetch on the landing -> "public_route_gated" (inherited shell invariant)
</reject>
After:
<after>
  - `/` renders the full multi-section landing; the header nav anchors scroll to real in-page sections.
  - axe reports 0 serious/critical; exactly one h1; `next build` exit 0; whole vitest suite green.
  - The marketing-shell chrome + route split are untouched (content-only change to (marketing)/page.tsx).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Pricing section: `#pricing` is a SHELL nav target but the dedicated pricing PAGE is a separate task (`pricing-page`).
    Lowest confidence: does `#pricing` resolve to an in-landing teaser section here, or only to the future /pricing page?
    Decision (project-lead, auto): render a short `#pricing` TEASER section on the landing (so the existing nav anchor
    never dangles) that links to the future /pricing page; the full tiers live in `pricing-page`. If wrong: minor
    rework moving the teaser. Same for `#docs` → a short teaser linking to the future /docs scaffold.
  - [x] Copy is sourced from README positioning (no new product claims invented).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Landing renders the hero with one h1 and both CTAs
  Given an anonymous visitor at /
  When the landing renders
  Then there is exactly one h1, a "Get started" link to /signup, and a "Log in" link to /login

Scenario: Feature grid lists enterprise capabilities
  Given the landing
  When it renders
  Then a #product section shows >=4 feature cards drawn from the README capability set

Scenario: Shell nav anchors resolve to real sections
  Given the header nav links /#product /#pricing /#docs
  When the landing renders
  Then sections with ids product, pricing, and docs all exist (no dangling anchor)

Scenario: Landing is accessible
  Given the landing
  When the a11y suite runs
  Then axe reports 0 serious/critical, headings are monotonic, and banner/main/contentinfo landmarks are present

Scenario: Reject — duplicate h1 / skipped heading
  Given the landing markup
  When heading levels are checked
  Then there is exactly one h1 and no skipped level ("heading_order_violation")
  And the marketing-shell chrome and route split remain unchanged

Scenario: Reject — dangling nav anchor
  Given a shell nav anchor #pricing
  When no section with id="pricing" exists
  Then the build/test fails ("dangling_nav_anchor")
  And the shell nav contract is honored (every anchor has a target)

Scenario: Reject — public route stays public
  Given the landing page module
  When its source is checked
  Then it neither reads cookies() nor performs an authenticated fetch ("public_route_gated")
  And the gateway/BFF/cookie contract remains unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PAGE  app/(marketing)/page.tsx  (Server Component, PUBLIC, renders inside <main id="main">)
  SECTIONS (in order), each a landmark-free <section aria-labelledby=...> with a single h2 (h1 is the hero):
    HERO        h1 + subhead + [Get started -> /signup] [Log in -> /login]
    #product    h2 "Features" + >=4 <Card> capability tiles (multi-provider · cost tracking · key governance/BYOK · rate-limit/bandwidth · spend analytics · alerting)
    #pricing    h2 teaser + link -> /pricing (full tiers owned by pricing-page task)
    #docs       h2 teaser + link -> /docs (scaffold owned by docs-blog-scaffold task)
    TRUST       h2 "Built for enterprise" band (multi-tenant isolation · audit-ready · SSO)
    CTA         h2 final call-to-action + [Get started -> /signup]
  HEADINGS: exactly one h1 (hero); h2 per section; no skipped level.
  A11Y: axe 0 serious/critical; banner/main/contentinfo preserved (from shell).
  REJECTIONS: heading_order_violation (dup h1 / skip) · dangling_nav_anchor (#product|#pricing|#docs missing id) · public_route_gated (cookie/fetch)
Schema: NONE. No DB, no gateway/BFF/cookie change. Content-only edit to (marketing)/page.tsx (+ optional small presentational components under components/marketing/).
Least-sure flag surfaced at freeze: [spec] #pricing/#docs in-landing TEASER sections (vs deferring entirely to the
  dedicated pages) — chosen so the FROZEN shell nav anchors never dangle before pricing-page/docs ship. Why riskiest:
  it's a scope-edge judgment, not a behavior; if wrong the teaser sections move to their own pages later (minor rework).
```

Status: FROZEN @ v1 — auto-frozen (autonomy: auto, fully-auto mode) 2026-06-24; low-risk content task, no security/contract surface.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: all new tests green + no dashboard vitest/build regression.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_hero: render landing → exactly one h1; "Get started"→/signup; "Log in"→/login
  - test_feature_grid: render → #product section has >=4 feature cards
  - test_nav_anchors_resolve: render → elements with id product, pricing, docs all exist
  - test_landing_a11y: axe landing → 0 serious/critical; monotonic headings
  - test_reject_heading_order: assert exactly one h1 and no skipped heading level
  - test_reject_dangling_anchor: assert every shell nav anchor (#product/#pricing/#docs) has a matching id
  - test_reject_public_not_gated: assert (marketing)/page.tsx source reads no cookies() and does no authed fetch
</test_plan>

Tests live in: `apps/dashboard/tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/(marketing)/page.tsx` `apps/dashboard/components/marketing/` `apps/dashboard/tests/`
Strategy (ordered batches):
  1. Red tests in `tests/landing-page.test.tsx` (7 per plan).
  2. Build the landing in (marketing)/page.tsx: hero + #product feature grid (Card tiles) + #pricing/#docs teasers + trust band + CTA; extract presentational sub-components into `components/marketing/` if the page grows large.
  3. Green: vitest + tsc + lint + real next build; axe clean.
Safety rule (feature-specific): content-only — never touch marketing-shell.tsx, proxy.ts, or any auth/BFF code; landing stays a pure public Server Component.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Evidence (independently re-run by orchestrator)
- vitest 446/446 green (54 files; +22 new landing-page tests, no regression) · tsc exit 0 · lint clean (1 pre-existing data-table warning) · real next build exit 0 (○ / static)
- Build-correct: page read in full — 6 sections in order, exactly one h1 (#hero-heading), h2 per section, h3 in cards (no skip), ids product/pricing/docs present (nav anchors resolve), CTAs → /signup + /login, reuses Badge/Button/FeatureCard + tokens, NO cookies()/authed fetch (public). marketing-shell/proxy/auth untouched.
- Earned-not-gamed: render+role-query tests + axe; the public-not-gated test caught a real false-positive (comment string "use client") → fixed by rewording the source comment, NOT by weakening the test. No vacuous asserts.

### GATE RECORD
Outcome: PASS
Reviewed by: orchestrator independent evidence review (autonomy: auto, low-risk content, no security/concurrency/arch residue) · date: 2026-06-24

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
