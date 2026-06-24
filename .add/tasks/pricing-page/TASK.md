# TASK: Public pricing page

slug: pricing-page · created: 2026-06-24 · stage: production
autonomy: auto
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - NEW `apps/dashboard/app/(marketing)/pricing/page.tsx` — public /pricing page; the landing #pricing teaser + shell footer "Pricing" link target it. Renders inside the FROZEN MarketingShell.
  - `apps/dashboard/components/ui/{card,button,badge}.tsx` — tier cards + CTAs.
  - `apps/dashboard/app/(marketing)/page.tsx` — landing teaser already links here (NO change).
Context (working folder): README positioning (usage-based, multi-tenant) = copy source; Hydroa tokens in globals.css; vitest+axe harness.
Honors: v23/v24 a11y bar (one h1, ordered headings, landmarks via shell); public Server Component (no cookie/fetch); reuse primitives + tokens.
Anchors the contract cites: `app/(marketing)/pricing/page.tsx` · Card/Button primitives · the `/pricing` route the landing+footer link.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Public pricing page (tier comparison)
Framings weighed: 3 usage-based tiers reusing Card (chosen) · interactive calculator (rejected, no billing backend) · single "contact us" (rejected, too thin)
Must:
<must>
  - NEW `app/(marketing)/pricing/page.tsx` renders inside `<main id="main">` with exactly ONE h1 and >=3 pricing TIER cards (e.g. Starter · Team · Enterprise), each with a name, price/qualifier, a feature list, and a CTA (Starter/Team -> /signup; Enterprise -> /signup or mailto contact).
  - Public Server Component: NO cookie read, NO authed fetch; reuses Card/Button/Badge + Hydroa tokens.
  - One h1; monotonic headings (h1 -> h2/h3 per tier, no skip); shell landmarks preserved; axe 0 serious/critical.
  - Pricing is PRESENTATIONAL only — no checkout/payment (out of milestone scope).
</must>
Reject:
<reject>
  - More than one h1 / skipped heading level -> "heading_order_violation"
  - Any cookie read or authenticated fetch on the page -> "public_route_gated"
  - A tier card missing a name, price, or CTA -> "incomplete_tier"
</reject>
After:
<after>
  - /pricing renders >=3 complete tier cards; the landing #pricing teaser + footer "Pricing" link both resolve here.
  - axe clean; one h1; next build exit 0; whole vitest suite green; shell/proxy/auth untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Concrete prices are placeholders (no real pricing decided). Lowest confidence: exact $ figures. Decision (auto):
    use clearly-labelled representative tiers (Starter free/low · Team $ per-seat-or-usage · Enterprise "Contact us")
    with qualifier copy, NOT hard commitments. If wrong: copy edit only (no structural rework).
  - [x] No payment/checkout (presentational) — milestone Out-list.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Pricing renders three tiers with one h1
  Given an anonymous visitor at /pricing
  When the page renders
  Then there is exactly one h1 and >=3 tier cards, each with a name, a price/qualifier, and a CTA

Scenario: Tiers route to signup / contact
  Given the tier cards
  When rendered
  Then Starter and Team CTAs link to /signup and the Enterprise CTA offers signup or contact

Scenario: Pricing is accessible
  Given the pricing page
  When the a11y suite runs
  Then axe reports 0 serious/critical and headings are monotonic

Scenario: Reject — heading order
  Given the pricing markup
  When heading levels are checked
  Then there is exactly one h1 and no skipped level ("heading_order_violation")
  And the marketing shell + route split remain unchanged

Scenario: Reject — public stays public
  Given the pricing page module
  When its source is checked
  Then it reads no cookies() and does no authed fetch ("public_route_gated")
  And the gateway/BFF/cookie contract remains unchanged

Scenario: Reject — incomplete tier
  Given a tier card
  When it lacks a name, price, or CTA
  Then the test fails ("incomplete_tier")
  And every rendered tier carries all three
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PAGE  app/(marketing)/pricing/page.tsx  (Server Component, PUBLIC, inside <main id="main">)
  h1 "Pricing"
  >=3 TIER <Card>s, each: name (h2/h3) · price + qualifier · feature <ul> · CTA <Button asChild><Link>
    Starter    -> /signup
    Team       -> /signup
    Enterprise -> /signup (or mailto contact)
  HEADINGS: one h1; h2/h3 per tier; no skip.  A11Y: axe 0 serious/critical; shell banner/main/contentinfo preserved.
  REJECTIONS: heading_order_violation · public_route_gated (cookie/fetch) · incomplete_tier (missing name/price/CTA)
Schema: NONE. No DB / gateway / BFF / cookie change. New public page only.
Least-sure flag surfaced at freeze: [spec] placeholder $ figures (no real pricing set) — representative tiers,
  copy-only risk; structure is firm. If wrong: edit copy, no rework.
```

Status: FROZEN @ v1 — auto-frozen (autonomy: auto, fully-auto mode) 2026-06-24; low-risk presentational page, no security/contract surface.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: all new tests green + no dashboard vitest/build regression.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_three_tiers: render /pricing -> exactly one h1; >=3 tier cards each with name + price + CTA
  - test_tier_ctas: Starter/Team CTAs -> /signup; Enterprise CTA present
  - test_pricing_a11y: axe 0 serious/critical; monotonic headings
  - test_reject_heading_order: exactly one h1, no skipped level
  - test_reject_public_not_gated: page source reads no cookies(), no authed fetch
  - test_reject_incomplete_tier: every rendered tier has name + price + CTA
</test_plan>

Tests live in: `apps/dashboard/tests/` · MUST run red before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/(marketing)/pricing/` `apps/dashboard/components/marketing/` `apps/dashboard/tests/`
Strategy (ordered batches): 1. red tests `tests/pricing-page.test.tsx` 2. build `app/(marketing)/pricing/page.tsx` (3 tier Cards) 3. green: vitest + tsc + lint + next build.
Safety rule (feature-specific): content-only public page; never touch marketing-shell.tsx, proxy.ts, or auth/BFF code.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations
- [x] /pricing renders one h1 + 3 tier <article> cards (Starter/Team/Enterprise), each w/ name h2 + price + CTA — vitest + read page
- [x] CTAs link to /signup; presentational only (no checkout) — page source
- [x] public: no cookies()/next-headers/authed fetch — source-guard test green
- [x] one h1, no heading skip (h1→h2 per tier; CardTitle asChild h2) — heading-order test green; axe 0 serious/critical

### Deep checks
- [x] WIRING — PricingPage default export resolved by next build (`○ /pricing`); reuses Card/Button/Badge
- [x] DEAD-CODE — none (tsc/lint clean); CardTitle asChild used to avoid h3 skip
- [x] SEMANTIC — prices are representative placeholders (flagged); no invented guarantees

### Evidence (independently run): vitest 452/452 (55 files, +6 new) · tsc 0 · lint 0 errors · next build exit 0 (○ /pricing). Shell/proxy/auth untouched.

### GATE RECORD
Outcome: PASS
Reviewed by: orchestrator independent evidence review (autonomy: auto, low-risk presentational page, no security residue) · date: 2026-06-24

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch: 404s on /pricing · axe regressions.

### Spec delta
- [SPEC · open] real pricing $ figures once commercial model is set (evidence: placeholder tiers).

### Competency deltas
- [UDD · open] marketing pages share a section/tier pattern — candidate for a reusable layout (evidence: landing+pricing repeat structure).
