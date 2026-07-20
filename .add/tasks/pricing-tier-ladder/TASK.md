# TASK: Render the real 5-tier plan ladder on /pricing (surface the hidden $1 Starter and $20 Pro) — CHANGE REQUEST to plan-tiers-and-base-fee §3

slug: pricing-tier-ladder · created: 2026-07-20 · stage: production
milestone: frontdoor-persona-routing
component: dashboard
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: build   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/app/(marketing)/pricing/page.tsx:TIERS` (lines 41-93) + `PricingPage` (lines
  95-204) — currently 3 static cards (Starter/Team/Enterprise). The card named `"Starter"`
  (line 51) binds to `getPricingCatalogEntry("free")` (line 52), NOT to the catalog's own
  `starter` entry ($1). The page's own comment (lines 45-48) documents this explicitly: "a
  distinct, NOT-yet-rendered personal $1 tier." The grid container (`mx-auto mt-14 grid
  max-w-6xl grid-cols-1 gap-6 lg:grid-cols-3`, line 118) is sized for exactly 3 cards.
- `apps/dashboard/lib/pricing-catalog.ts:PRICING_CATALOG` (lines 29-35) — already holds the
  FULL 5-tier catalog: `free`(null)/`starter`(1.0)/`pro`(20.0)/`team`(99.0)/`enterprise`(null),
  plus `getPricingCatalogEntry`/`formatBasePrice` (lines 37-53). No data change needed — every
  figure this task renders already exists here, mirroring the backend migration seed exactly
  (plan-tiers-and-base-fee TASK.md §3, FROZEN @ v1). This task is a render-completeness fix,
  not a new figure.
- `apps/dashboard/tests/pricing-catalog-no-drift.test.ts` (FROZEN @ v1, M4/R4) —
  `test_pricing_page_derives_from_catalog_not_a_literal` (lines 46-68) asserts only 3 cards
  (the free-bound "Starter" card / Team / Enterprise) derive from the catalog. Read in full:
  adding 2 real cards (`starter` $1, `pro` $20) needs ADDITIVE new assertions here — extending,
  never weakening, M4/R4's own invariant ("rendered price text is a pure function of
  PRICING_CATALOG, never a re-hardcoded literal").
- `apps/dashboard/tests/pricing-page.test.tsx` (frozen a11y/shape suite, 7 describe blocks —
  v38 base + residency-tiers-ui M11 + ai-act-marketing-page additions) — read every assertion
  for 5-card compatibility: `test_three_tiers` requires headings matching `/starter/i`,
  `/team/i`, `/enterprise/i` (still exactly one match each post-rename — the free-card no
  longer matches `/starter/i` once relabeled "Free"; the NEW real Starter card is the sole
  match) + `links.length >= 3` (true at 5, in fact more). `test_tier_ctas` requires `>=2`
  `/signup` links (true at 5, in fact `>=4`). `test_reject_incomplete_tier` requires
  `cards.length >= 3` (true at 5) and every card's text matches `/\$|free|contact/i` (Free/$1/
  $20/$99/Contact us all match). CONFIRMED: none of the 7 describe blocks need edits — this
  file is NOT touched by this task.
- `.add/tasks/plan-tiers-and-base-fee/TASK.md` §3 (FROZEN @ v1) — the contract THIS task
  amends. Its Dashboard clause states verbatim: "app/(marketing)/pricing/page.tsx:
  TIERS[Starter].price / TIERS[Team].price / TIERS[Enterprise].price derive from
  PRICING_CATALOG (still 3 rendered cards — no IA change, milestone Scope is 'minimal')." This
  task changes the render-count/label-mapping clause to 5 cards. Per ADD discipline this is an
  explicit, disclosed CHANGE REQUEST back through SPECIFY/CONTRACT — I do NOT edit that frozen
  file; this task's own §3 supersedes ONLY its render-count/label-mapping clause. Every other
  clause in that contract (Schema, ORM, Invoice generation, Signup, the no-drift BINDING
  MECHANISM itself) is untouched and still governs.
- `apps/gateway/src/gateway/tenants/application/self_serve_plans.py:list_self_serve_plans`
  (self-serve-plans-catalog TASK.md §3, FROZEN @ v1, phase:done, shipped in commercial-self-
  serve PR #79) — the backend SELECT returning a tenant's self-serve UPGRADE catalog:
  `self_serve=true AND (audience IS NULL OR audience=account_type) AND id != current_plan_id`,
  ordered by `base_price_usd_monthly ASC NULLS FIRST`. Cross-checked against
  `apps/gateway/migrations/versions/b7e2c4a9f1d3_self_serve_checkout.py` (lines 15-17):
  free/starter/pro seeded `self_serve=true, audience='personal'`; team `self_serve=true,
  audience='business'`; enterprise `self_serve=false` (contact-sales only, by design).
- `apps/gateway/src/gateway/payments/application/checkout_service.py:CheckoutService.
  create_plan_upgrade`/`confirm` (self-serve-checkout TASK.md §3, FROZEN @ v1, phase:done) —
  the real purchase mechanism: idempotent create -> payment-provider auth -> row-locked
  confirm -> `assign_plan`. `_validate_plan_upgrade` (lines 339-354) rejects same-plan/non-
  self-serve/audience-mismatch/downgrade.
- `apps/dashboard/app/(app)/app/plan/page.tsx` -> `components/plan/PlanSeatsPage.tsx` (lines
  ~74-84 `selfServePlansQuery` against `GET /admin/plans`, ~119+ `<UpgradePlanDialog>`) — the
  AUTHENTICATED in-app screen where this is actually purchased. CONFIRMED LIVE by code-read
  (not assumed, correcting the orchestrator's own tentative "no dashboard route wired" read in
  the shared context): a personal tenant on `free` sees `starter`($1) and `pro`($20) as real,
  clickable upgrade options in `UpgradePlanDialog` (`components/checkout/UpgradePlanDialog.tsx`)
  today, driving `lib/checkout.ts`'s create+confirm flow end-to-end. The checkout path for the
  tiers this task is about to render publicly ALREADY EXISTS and is ALREADY WIRED — just not
  reachable FROM the unauthenticated `/pricing` page itself (nor is it reachable from Team's
  card today either — see next finding).
- `apps/gateway/src/gateway/tenants/application/use_cases.py:SignupUseCase.execute` (~lines
  36-55) — personal signups ALWAYS land on `free` (`get_plan_id_by_name("free")`) and business
  signups ALWAYS land unplanned (`plan_id NULL`) — REGARDLESS of which pricing-page CTA the
  visitor clicked. This is the EXISTING, already-shipped behavior for Team's CTA today: Team's
  "Get started" -> `/signup` does NOT itself place the tenant on Team; the actual purchase
  happens post-signup at `/app/plan`. This task's new Starter/Pro CTAs inherit the identical,
  already-accepted pattern — not a new gap this task introduces.
- `apps/gateway/migrations/versions/1e66a2cb51a6_plan_catalog.py` (lines 30-32, original seed)
  + `a7c3e9f1b2d4_account_type_discriminator.py` (lines 77-82, `individual` seed) — the REAL
  `seat_cap`/`budget_usd_monthly_default` figures each tier carries post-restructure: starter
  seat_cap=1 (was 3, changed by plan-tiers-and-base-fee M1)/budget=$50; pro(was `individual`)
  seat_cap=1/budget=$20; team seat_cap=NULL(unlimited)/budget=$500; enterprise NULL/NULL. NOTE:
  the CURRENT free-bound "Starter" card's own feature bullet reads "1 tenant, up to 3 users"
  (page.tsx:56) — now STALE, since that plan's real `seat_cap` is 1, not 3 (a pre-existing
  drift from before this task, on the SAME card this task already relabels — surfaced and
  fixed here, not left for a future pass, since it's a one-line copy touch on a card already
  being edited).

Context (working folder): `apps/dashboard/app/(marketing)/pricing/` + `apps/dashboard/lib/
pricing-catalog.ts` + `apps/dashboard/tests/{pricing-page.test.tsx,pricing-catalog-no-drift.
test.ts}` — presentation-only, no backend touch (PRICING_CATALOG's data is already correct;
only its RENDERING changes). Out of scope: the homepage CTA/anchor work (sibling DAG tasks
`homepage-price-anchor`, `homepage-cta-intent-split`, `homepage-integration-proof` own that
surface); the `/app/plan` in-app upgrade screen (already shipped, unmodified by this task);
any backend catalog/checkout/entitlement code (already shipped, unmodified).

Honors (patterns / conventions): reuse-before-invent (existing `Card`/`CardHeader`/
`CardTitle`/`CardDescription`/`CardContent`/`Badge`/`Button` primitives only; the existing
responsive-grid idiom `grid-cols-1 sm:grid-cols-2 lg:grid-cols-N` shipped elsewhere, e.g.
`apps/dashboard/components/platform/PlatformMarginView.tsx:232`,
`apps/dashboard/components/overview/OverviewPage.tsx:205`, and the `max-w-7xl` container width
shipped at `apps/dashboard/app/(marketing)/page.tsx:146`); Server-Component-only / zero-fetch
discipline (frozen invariant, `test_reject_public_not_gated`); the no-drift binding (M4) — a
tier's price text is ALWAYS `formatBasePrice(getPricingCatalogEntry(name).basePriceUsd,
nullLabel)`, never a literal; `aria-labelledby` per-card landmark pattern (existing, line 120-
122, extended not reinvented).
Seams consulted: none new — the pricing-catalog no-drift binding (plan-tiers-and-base-fee §3
M4) is the one seam this task extends, cited above rather than re-derived.
Anchors the contract cites: `PRICING_CATALOG` (unchanged), `getPricingCatalogEntry`,
`formatBasePrice`, `TIERS` (restructured to 5 entries), `PricingPage`, the 2 new no-drift test
assertions, `plan-tiers-and-base-fee` TASK.md §3 (the clause this task amends).
Issues/Risks (→ feed §1):
- ⚠ NAMING COLLISION (the one this task exists to resolve): the shipped page's FIRST card is
  literally labeled `"Starter"` (`page.tsx:51`) but is bound to catalog entry `free`; the
  catalog's OWN `starter` entry ($1) also has `displayName: "Starter"` (`pricing-catalog.ts:
  31`). Rendering both under the same visible label on one page would be a real two-different-
  things-same-name bug. RESOLVED (not left ambiguous) using the catalog's own `displayName` as
  the least-arbitrary ground truth: relabel the free-bound card's visible name from "Starter"
  to "Free" (byte-matching `PRICING_CATALOG`'s own `free.displayName`), and let the NEW real
  Starter card own the "Starter" label. This IS a customer-visible marketing-copy rename riding
  on an already-shipped, publicly-linked page — named explicitly in §1 as a proposed change,
  not silently applied, and carried as this task's ⚠ least-sure flag (see §1 Assumptions).
- PURCHASABILITY (the other one this task must address head-on): confirmed by code-read, NOT
  assumed, that Starter/Pro ARE purchasable — via the in-app `/app/plan` self-serve-upgrade
  flow (`self-serve-checkout` + `self-serve-plans-catalog`, both FROZEN + shipped,
  phase:done). A visitor cannot buy directly FROM the public `/pricing` page — but that has
  ALWAYS been true, for every tier including Team (that page is a zero-fetch Server Component
  by frozen contract; no CTA there has ever completed a purchase). So a "Get started"->`/signup`
  CTA on the new Starter/Pro cards is NOT a new trust break: it is byte-identical in kind to
  Team's existing, already-accepted pattern (signup lands everyone on `free`/unplanned
  regardless of which card was clicked; the real purchase happens post-signup at `/app/plan`).
  Disclosed explicitly here rather than silently assumed away.
- GRID LAYOUT: no shipped screen has a 5-up CARD grid (the nearest precedent, 4-up stat-tile
  grids, carries far less content per cell than a feature-list pricing card); the existing
  3-col pricing grid (`max-w-6xl grid-cols-1 lg:grid-cols-3`) cannot fit 5 without overflow —
  needs one new (token/pattern-reusing) breakpoint step, specified explicitly in §3 rather than
  left to Build's guess.
- STALE FEATURE-BULLET COPY (found incidentally, see Touches above): the free-bound card's "up
  to 3 users" bullet no longer matches that plan's real `seat_cap=1` — a pre-existing drift,
  fixed as part of this task's edit to that same card (not a new scope item, not left stale).

Related intent: shared milestone context (`FRONTDOOR-CONTEXT.md`) — "Human decisions already
made: 1. Self-serve should work... via scoped self-serve" and the explicit instruction:
"surface the real 5-tier ladder on /pricing, so the homepage can then honestly anchor on the
genuine lowest paid price. That is THIS task." `plan-tiers-and-base-fee` TASK.md (the frozen
contract amended) + its own §1 ⚠ (the no-drift binding is a hand-kept static test — still true
and still governs here, unchanged). GLOSSARY: extends the existing "5-tier catalog" term
(plan-tiers-and-base-fee) to its fully-RENDERED form on `/pricing` — no new domain term.
Ground SHA: 8daf22c (git HEAD; branch `feat/frontdoor-persona-routing` cut from this commit,
per shared milestone context) — every symbol above read live from the current tree via
`mcp__serena`, not assumed from the pre-given recon.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Render all 5 `PRICING_CATALOG` tiers as 5 pricing cards on `/pricing` (Free/Starter/
Pro/Team/Enterprise), replacing the current 3-card render that hides the real $1 Starter and
$20 Pro tiers behind a mislabeled "Starter"=Free card. Resolves the naming collision by
relabeling the free-bound card to "Free" (matching `PRICING_CATALOG`'s own `displayName`) and
adding 2 new real cards for the catalog's `starter`/`pro` entries. Amends `plan-tiers-and-
base-fee` TASK.md §3's "still 3 rendered cards" clause; every other clause in that contract
(schema/invoice/signup/no-drift MECHANISM) stays in force, untouched.
Framings weighed:
- Rename the free-bound card to "Free" + add 2 new cards for `starter`/`pro` (chosen) —
  resolves the collision using the catalog's own canonical `displayName` as ground truth, not
  an invented label; minimal diff; every figure rendered already exists in `PRICING_CATALOG`,
  so this is a pure render-completeness fix, not a new data decision.
- Leave the "Starter" card labeled Starter (still bound to `free`) unchanged, and give the new
  $1/$20 cards a DIFFERENT label, e.g. "Personal"/"Solo" (rejected) — avoids touching the
  existing shipped card, but perpetuates exactly the confusion this task exists to fix: the
  catalog's OWN `starter` entry still wouldn't be labeled "Starter" anywhere visible, and the
  substitute label has no grounding in the catalog or GLOSSARY — an invented term, not a
  resolved one.
- A headed 2-group layout ("For individuals": Free/Starter/Pro · "For teams": Team/Enterprise)
  instead of one flat 5-card grid (considered, not chosen for v1) — arguably clearer
  information architecture at 5 tiers, but no shipped precedent for a headed dual-grid pricing
  layout exists in this codebase (reuse-before-invent leans against inventing one); carried
  forward as a named v2 option in Assumptions, not silently dropped.
Must:
<must>
  - M1 — the page renders all 5 `PRICING_CATALOG` tiers as 5 cards, in ascending-price /
    personal-then-business order: Free, Starter, Pro, Team, Enterprise. Every card's price
    text derives from `formatBasePrice(getPricingCatalogEntry(name).basePriceUsd, nullLabel)`
    — never a re-hardcoded literal (extends M4's invariant from plan-tiers-and-base-fee,
    never weakens it).
  - M2 — naming-collision fix: the free-bound card's `name` becomes `"Free"` (was `"Starter"`);
    a NEW card `name: "Starter"` binds to `getPricingCatalogEntry("starter")` ($1); a NEW card
    `name: "Pro"` binds to `getPricingCatalogEntry("pro")` ($20). Every card's visible name is
    unique and matches its bound catalog entry's own `displayName`. The Free card's stale "up
    to 3 users" bullet is corrected to "up to 1 user" (matches the real seeded `seat_cap=1`).
  - M3 — Free/Starter/Pro/Team CTAs read "Get started" -> `href="/signup"` (byte-identical CTA
    shape to today's Starter/Team CTAs); Enterprise keeps "Talk to us" -> `href="/signup"`
    (unchanged). No tier's CTA performs a direct purchase on this page — the page stays a
    zero-fetch Server Component (frozen invariant, unchanged).
  - M4 — Team remains the ONE `featured` card ("Most popular" badge) — unchanged from today;
    no other card carries the badge.
  - M5 — responsive grid: `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5` inside
    `max-w-7xl` (was `max-w-6xl grid-cols-1 lg:grid-cols-3`) — 1-col at mobile (no horizontal
    overflow at any width), scaling through existing breakpoint tokens only; every card keeps
    `flex h-full flex-col` (existing pattern) so uneven feature-list lengths don't misalign
    card heights within a row.
  - M6 — every new/changed value/token is either an EXISTING Aurora/Airier design token
    (`bg-card`, `border-border`, `text-foreground`, `text-muted-foreground`, `--primary`, the
    Geist font stack) or an existing shadcn/ui primitive already used on this page — zero new
    visual pattern introduced. Both light and dark theme render correctly (token-driven, no
    hardcoded color).
  - M7 — WCAG 2.2 AA floor holds at 5 cards: exactly one h1, no skipped heading level across
    the now-5 tier h2s + the existing residency/AI-Act card h2, each card stays a labelled
    `<article>` (existing `aria-labelledby` pattern, extended to Free/Starter/Pro unchanged in
    shape), visible `focus-visible` and ≥44px hit targets on every CTA (inherited from the
    existing `Button`/`Link` primitives — no new interactive component type introduced).
  - M8 — `pricing-catalog-no-drift.test.ts` gains 2 ADDITIVE assertions (the Starter card
    renders `$1`, the Pro card renders `$20`, both derived from `PRICING_CATALOG`); the
    existing 3 assertions are retargeted to the new card names (Free/Team/Enterprise) but
    assert the SAME underlying catalog-derivation invariant — never weakened.
  - M9 — `pricing-page.test.tsx` (frozen a11y/shape suite) is NOT edited — every existing
    assertion already holds at 5 cards (confirmed in §0 Touches). Build may ADD new describe
    blocks for Free/Starter/Pro if useful, but must not touch the existing 7.
</must>
Reject:
<reject>
  - R1 — a tier card whose displayed price text does not equal `formatBasePrice(catalog entry,
    nullLabel)` (a re-hardcoded literal) -> the no-drift test fails; build cannot go green
    (extends M4/R4's existing invariant to Free/Starter/Pro).
  - R2 — two cards sharing the same visible tier name -> ambiguous `getByRole("heading",
    {name})` (throws on multiple matches) / a duplicate-labelled landmark — structurally
    prevented by M2's unique-name rule, verified by the no-drift page-render assertions each
    resolving exactly one card.
  - R3 — any card missing a name heading, a price/qualifier, or a CTA link ->
    `test_reject_incomplete_tier` fails (existing frozen assertion, unchanged, now checked
    against 5 cards instead of 3).
  - R4 — the page performing a fetch / reading cookies / importing `next/headers` to decide
    what to render -> `test_reject_public_not_gated` fails (existing frozen assertion,
    unchanged) — the 5-tier render must stay a pure function of the static `PRICING_CATALOG`
    import, zero runtime IO.
  - R5 — horizontal overflow or a broken grid below the `sm` breakpoint -> visually flagged at
    UI review; prevented structurally by M5's `grid-cols-1` base (no breakpoint below which the
    grid exceeds 1 column).
</reject>
After:
<after>
  - `/pricing` renders exactly 5 cards: Free ($0), Starter ($1), Pro ($20), Team ($99),
    Enterprise (Contact us) — every figure sourced from `PRICING_CATALOG`, zero literals.
  - The catalog-name-vs-displayed-label collision no longer exists: every card's visible name
    is unique and matches its bound catalog entry's own `displayName`.
  - `homepage-price-anchor` (sibling task) can truthfully link to `/pricing` and anchor on "$1/
    mo" — the real Starter tier is now visibly present, not hidden behind a mislabeled card.
  - `plan-tiers-and-base-fee` TASK.md §3's "3 rendered cards" clause is superseded by this
    task's own §3 for the render-count/label-mapping only; its schema/invoice/signup/no-drift-
    mechanism clauses are untouched and still govern.
  - Both existing frozen dashboard test files (`pricing-page.test.tsx`, `pricing-catalog-no-
    drift.test.ts`) still pass — the former untouched, the latter additively extended.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] RENAMING THE FREE-BOUND CARD'S VISIBLE LABEL FROM "Starter" TO "Free" — lowest
    confidence because this is customer-visible marketing copy on an already-shipped,
    publicly-linked page (both the homepage and the marketing footer link to `/pricing` today),
    decided here from the catalog's own `displayName` as the least-arbitrary ground truth
    available, but NOT explicitly pre-approved by Tin as a copy change — the frontdoor-context
    "human decisions already made" cover scoped self-serve signup and the S1 security gate, not
    this specific rename. If wrong: a returning visitor or an existing screenshot/doc that says
    "Starter = the free tier" goes stale. Low cost to reverse (copy-only, zero schema/data
    impact) but real enough to confirm explicitly at freeze rather than ship silently.
  - [ ] which tier (if any) besides Team should be visually emphasized now that there are 5 —
    ranked #2; "Team stays the only featured card, unchanged" is the lowest-diff option
    chosen; a case exists for featuring Pro instead (the highest personal tier) to upsell
    prosumers, but nothing in the repo signals that over the status quo — confirm or correct
    at freeze.
  - [ ] whether a headed 2-group ("Individuals" / "Teams") layout is preferred over one flat
    5-card grid for v1 — ranked #3; flat grid chosen for minimal diff and no invented IA
    pattern; low cost to revisit as a v2 restyle if Tin wants clearer visual grouping.
  - [x] purchasability of Starter/Pro — confirmed via code-read (`self_serve_plans.py` +
    `checkout_service.py` + `PlanSeatsPage.tsx` wiring, all three read in full): both are real,
    live, self-serve-upgradeable today through `/app/plan`. NOT a new trust break — matches
    Team's existing CTA pattern exactly (see §0 Issues/Risks).
  - [x] the naming-collision RESOLUTION MECHANISM (use `PRICING_CATALOG`'s own `displayName`
    as ground truth, not an invented label) — confirmed as the minimal, best-grounded fix; only
    the DECISION TO SHIP the resulting customer-visible rename is flagged above (⚠), not the
    mechanism used to resolve it.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: all 5 tiers render in ascending-price order   # M1
  Given PRICING_CATALOG holds free/starter/pro/team/enterprise
  When /pricing renders
  Then exactly 5 <article> cards render, in the order Free, Starter, Pro, Team, Enterprise
  And each card's price text equals formatBasePrice(that tier's basePriceUsd, its nullLabel)

Scenario: the naming collision is resolved — Free and Starter are distinct cards   # M2
  Given the free-bound card and the real starter catalog entry both had displayName "Starter" before this task
  When /pricing renders
  Then the free-bound card is named "Free" and the starter-bound card is named "Starter"
  And getByRole("heading", { name: /free/i }) and getByRole("heading", { name: /starter/i }) each resolve to exactly one card

Scenario: Free's feature bullet matches its real seat_cap   # M2
  Given the free plan's seeded seat_cap is 1
  When /pricing renders the Free card
  Then its bullet reads "up to 1 user", not the stale "up to 3 users"

Scenario: CTA hrefs and copy match the existing pattern   # M3
  Given Free/Starter/Pro/Team are self-serve, signup-first tiers
  When /pricing renders
  Then each of their CTAs reads "Get started" and links to /signup
  And Enterprise's CTA reads "Talk to us" and links to /signup, unchanged

Scenario: Team remains the sole featured card   # M4
  Given the page renders 5 cards
  When /pricing renders
  Then exactly one card (Team) carries the "Most popular" badge
  And no other card carries it

Scenario: the 5-card grid never overflows horizontally   # M5
  Given a viewport narrower than the sm breakpoint
  When /pricing renders
  Then the grid lays out as a single column (grid-cols-1)
  And no card's content is clipped or forces horizontal scroll

Scenario: every visual value traces to an existing token   # M6
  Given the dashboard's Aurora/Airier token layer
  When the 2 new cards (Starter, Pro) are audited
  Then every color/spacing/radius/shadow value used already exists as a token or shadcn/ui primitive
  And both light and dark theme render each card with correct contrast

Scenario: heading order and landmark structure hold at 5 cards   # M7
  Given the page now has 5 tier h2 headings plus the existing residency/AI-Act h2
  When /pricing renders
  Then there is exactly one h1 and no heading level is skipped
  And every card is a labelled <article> with a CTA reachable by keyboard with a visible focus ring

Scenario: the no-drift test is extended, not replaced   # M8
  Given pricing-catalog-no-drift.test.ts's 3 existing assertions
  When this task's build runs
  Then those 3 assertions still exist (retargeted to the new card names) plus 2 new ones for Starter/Pro
  And none of the original 3 assertions were deleted or loosened

Scenario: the existing pricing-page test suite passes unmodified   # M9
  Given pricing-page.test.tsx's 7 describe blocks (frozen)
  When the full dashboard suite runs after this task's build
  Then all 7 describe blocks pass with zero edits to that file

Scenario: a hand-edited literal price fails the no-drift test   # R1
  Given the Pro card's price is hand-edited to "$25" without updating PRICING_CATALOG
  When the no-drift test runs
  Then it fails
  And the build cannot go green until the figures are reconciled

Scenario: two cards can never share a visible name   # R2 (regression guard)
  Given the Free/Starter naming-collision fix (M2)
  When the page is queried by role+name for each of the 5 tier names
  Then each query resolves to exactly one card, never throwing on ambiguity
  And the underlying TIERS array has 5 distinct `name` values, structurally

Scenario: an incomplete card is rejected   # R3
  Given a card missing its price/qualifier or its CTA link
  When test_reject_incomplete_tier runs
  Then it fails
  And the card set stays at 3-required-elements-each, unchanged from today's assertion

Scenario: the page stays a zero-fetch Server Component   # R4
  Given the 5-tier render is a pure function of the static PRICING_CATALOG import
  When test_reject_public_not_gated runs
  Then it finds no cookies()/next-headers import/bffGet/useQuery/fetch(
  And the page continues to render identically for every visitor, no auth state involved

Scenario: mobile width never overflows horizontally   # R5
  Given a 320px-wide viewport
  When /pricing renders
  Then the page's scrollWidth does not exceed its clientWidth
  And every card is fully readable in a single column

Scenario: dark theme renders all 5 cards with correct contrast   # edge case
  Given the dashboard's dark-mode token set (--background/--foreground/--primary dark variants)
  When /pricing renders under prefers-color-scheme: dark or the stored theme toggle
  Then every card's text/background pairing meets the same AA floor as the light theme

Scenario: an unknown catalog name still throws defensively   # edge case (unchanged behavior)
  Given getPricingCatalogEntry's existing guard (throws on an unknown tier name)
  When a future edit introduces a typo'd catalog key
  Then the page fails to build/render loudly, never silently renders a blank or wrong price
  And this task introduces zero new lookups that bypass that guard

Scenario: keyboard-only navigation reaches every CTA in visual order   # edge case
  Given a keyboard-only user tabs through the page
  When they reach the pricing grid
  Then focus visits Free -> Starter -> Pro -> Team -> Enterprise CTAs in that order, each with a visible focus ring
  And no CTA is skipped or unreachable
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
CHANGE REQUEST to plan-tiers-and-base-fee TASK.md §3 (FROZEN @ v1):
  Superseded clause: "app/(marketing)/pricing/page.tsx: TIERS[Starter].price /
    TIERS[Team].price / TIERS[Enterprise].price derive from PRICING_CATALOG (still 3
    rendered cards — no IA change, milestone Scope is 'minimal')"
  Replaced by this task's own Dashboard clause below (5 rendered cards). Every OTHER clause
  in that frozen contract (Schema, ORM, Invoice generation, Signup, the no-drift BINDING
  MECHANISM) is UNCHANGED and still governs — this amendment touches the render-count/label-
  mapping clause only.

Dashboard (apps/dashboard) — no backend change, presentation-only:

  lib/pricing-catalog.ts: UNCHANGED (PRICING_CATALOG/getPricingCatalogEntry/formatBasePrice
    already hold/export every figure this task renders).

  app/(marketing)/pricing/page.tsx:
    TIERS: Tier[] — restructured from 3 to 5 entries, each price/qualifier still computed via
      formatBasePrice(getPricingCatalogEntry(<name>).basePriceUsd, <nullLabel>) — never a
      literal:
      1. { name: "Free",       catalog: "free",       price: formatBasePrice(free.basePriceUsd, "Free"),
           qualifier: "for evaluation", cta: {label:"Get started", href:"/signup"} }
           # was the "Starter" card; RENAMED (§1 M2, the ⚠ least-sure flag); feature bullet
           # "1 tenant, up to 3 users" corrected to "up to 1 user" (matches seat_cap=1)
      2. { name: "Starter",    catalog: "starter",    price: formatBasePrice(starter.basePriceUsd, "Free"),
           qualifier: "per month", cta: {label:"Get started", href:"/signup"} }
           # NEW card — the catalog's own real $1 tier, not previously rendered anywhere
      3. { name: "Pro",        catalog: "pro",        price: formatBasePrice(pro.basePriceUsd, "Free"),
           qualifier: "per month", cta: {label:"Get started", href:"/signup"} }
           # NEW card — the catalog's own real $20 tier, not previously rendered anywhere
      4. { name: "Team",       catalog: "team",       price: formatBasePrice(team.basePriceUsd, "Free"),
           qualifier: "per month + usage", featured: true, cta: {label:"Get started", href:"/signup"} }
           # UNCHANGED (still the sole `featured` card, §1 M4)
      5. { name: "Enterprise", catalog: "enterprise", price: formatBasePrice(enterprise.basePriceUsd, "Contact us"),
           qualifier: "custom", cta: {label:"Talk to us", href:"/signup"} }
           # UNCHANGED
      Feature-bullet wording for the 2 NEW cards (Starter/Pro): Build's to finalize, bounded by
        — no new entitlement claim not already implied by an existing card's bullet style;
        no specific $/rpm/tpm figures stated (existing cards never state one either); grounded
        in the real seat_cap=1 shared by Free/Starter/Pro (all single-user personal tiers).

    Grid container (was `mx-auto mt-14 grid max-w-6xl grid-cols-1 gap-6 lg:grid-cols-3`):
      -> `mx-auto mt-14 grid max-w-7xl grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3
          xl:grid-cols-5`
      (existing tokens/breakpoints only — grid-cols-1/sm:grid-cols-2/lg:grid-cols-3 idiom
      reused verbatim from PlatformMarginView.tsx/OverviewPage.tsx; max-w-7xl reused verbatim
      from the marketing homepage; the one NEW step is xl:grid-cols-5, added because no
      existing screen needed a 5th column)

    Every other element of PricingPage (h1/intro copy, residency+priority Card, AI-Act
    cross-link Card) is UNCHANGED.

  tests/pricing-catalog-no-drift.test.ts (FROZEN @ v1, amended ADDITIVELY):
    test_pricing_catalog_matches_seeded_backend — UNCHANGED (still asserts all 5
      PRICING_CATALOG entries against EXPECTED_SEED_BASE_PRICES; this already covers
      starter/pro today even though the page didn't render them).
    test_pricing_page_derives_from_catalog_not_a_literal — the existing 3 assertions
      (starterCard bound to "free"/teamCard/entCard) are RETARGETED to query by the tier's
      NEW visible name (Free/Team/Enterprise unchanged for Team/Enterprise, "Free" replaces
      the old "Starter" query) — same assertion shape, same invariant, new selector text.
      PLUS 2 NEW assertions: a card queried by name /starter/i renders formatBasePrice(catalog
      starter.basePriceUsd, ...) == "$1"; a card queried by name /pro/i renders "$20".
    test_reject_hand_edited_price_would_fail_no_drift — UNCHANGED (already demonstrates the
      invariant generically via the Team entry; still valid).

  tests/pricing-page.test.tsx: NOT TOUCHED (confirmed compatible in §0 Touches — all 7
    describe blocks hold at 5 cards without edits).

Reject responses (all build-time/test-time, not runtime — this is a static public page):
  R1 -> pricing-catalog-no-drift test failure (a re-hardcoded literal price)
  R2 -> ambiguous role+name query / duplicate landmark label (structurally prevented by M2)
  R3 -> test_reject_incomplete_tier failure (pricing-page.test.tsx, existing, unchanged)
  R4 -> test_reject_public_not_gated failure (pricing-page.test.tsx, existing, unchanged)
  R5 -> UI review finding (horizontal overflow below sm breakpoint) — prevented structurally
    by grid-cols-1 base, no automated gate beyond the a11y/visual review
```

Glossary deltas: none new — extends the existing "5-tier catalog" term (plan-tiers-and-base-
  fee TASK.md) to its fully RENDERED form on `/pricing`; no new domain term introduced.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

Least-sure flag surfaced at freeze: [contract] renaming the free-bound card's visible label
  from "Starter" to "Free" (§1 ⚠) — resolves the naming collision using PRICING_CATALOG's own
  displayName as ground truth, but is a customer-visible marketing-copy change on an already-
  shipped, publicly-linked page that was not explicitly pre-approved as a copy change in the
  frontdoor-context human decisions. Low cost to reverse (copy-only, zero schema/data impact)
  if the human prefers a different resolution (e.g. keeping "Starter" on the free card and
  naming the new $1 tier something else) — confirm or correct at freeze.
  RESOLVED AT FREEZE (Tin, 2026-07-20): rename the free-bound card's visible label to "Free",
  using PRICING_CATALOG's own displayName as ground truth. The shipped ladder is therefore
  Free / Starter $1 / Pro $20 / Team $99 / Enterprise. Rationale accepted: resolving the
  collision at its source beats carrying a permanent disagreement between the internal tier
  name (`starter`) and the customer-visible label — which is exactly what produced this
  confusion in the first place.
  PURCHASABILITY (orchestrator-verified independently, not taken on the draft's word):
  `apps/dashboard/app/(app)/app/plan/page.tsx`, `components/checkout/UpgradePlanDialog.tsx`
  and `lib/checkout.ts` all exist — so Starter/Pro ARE genuinely purchasable post-signup via
  the in-app upgrade flow, exactly as Team already is. Rendering their prices is therefore
  honest; this task introduces no new trust break.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% lines on the touched region of `app/(marketing)/pricing/page.tsx` (the
  `TIERS` array + grid container — the only region this task edits; `lib/pricing-catalog.ts` is
  UNCHANGED and already covered).

Persona: Frontend Engineer (`.add/personas/frontend-engineer.md`, flow: build/advisor) — design-
  token fidelity + frozen-structural-contract discipline are the two Critical Rules this suite
  enforces most directly (zero raw hex/inline-style on the 2 new cards; the frozen
  `pricing-page.test.tsx` structural assertions must survive the change, code fitting the frozen
  structure rather than the reverse).

VACUOUS-PASS DISCIPLINE applied throughout: several scenarios (M4 "Team stays sole featured", M7
  heading order, the dark-theme edge case) already hold true against TODAY's 3-card page — every
  test below is gated on a leading assertion that is FALSE today (`cards).toHaveLength(5)`, a card
  queried by its NEW name, or a catalog-derived price not present in today's render) so none can
  pass vacuously. One scenario (`getPricingCatalogEntry` throws on an unknown name) is a genuine
  `[REGRESSION PIN]` — already-shipped, unrelated behavior — labelled and excluded from the red
  count. Every expected price string is computed via `formatBasePrice(getPricingCatalogEntry(...))`
  — never a second hardcoded literal.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_all_5_tiers_render_in_ascending_order (2 tests): arrange render `/pricing` / act query all
    `<article>` / assert exactly 5, in order Free→Starter→Pro→Team→Enterprise, each price ==
    `formatBasePrice(catalog entry, nullLabel)` · covers: M1
  - test_naming_collision_resolved_free_and_starter_distinct: assert `getByRole("heading",
    {name:/^free$/i})` and `.../^starter$/i` each resolve to exactly ONE (and two DIFFERENT)
    elements · covers: M2
  - test_free_feature_bullet_matches_real_seat_cap: assert the Free card shows "up to 1 user" and
    NOT the stale "up to 3 users" · covers: M2
  - test_cta_hrefs_and_copy_match_existing_pattern: assert Free/Starter/Pro/Team CTAs are
    "Get started"→`/signup`; Enterprise is "Talk to us"→`/signup` · covers: M3
  - test_team_remains_sole_featured_card: gate on 5-card count, then assert exactly one "Most
    popular" badge, on Team · covers: M4
  - test_5_card_grid_never_overflows_horizontally: gate on 5-card count, then assert the grid
    container's exact class set (`grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5` inside
    `max-w-7xl`) — the structural proxy this jsdom harness uses in place of a real layout engine
    (same convention as `tests/billing-invoices.test.tsx`'s `toHaveClass("overflow-auto")`) ·
    covers: M5, R5
  - test_new_cards_use_only_existing_tokens: gate on catalog-derived price for Starter/Pro, then
    assert zero raw hex/`rgb()`/inline-style anywhere in either card · covers: M6
  - test_heading_order_and_landmarks_hold_at_5_cards: gate on 5-card count, then assert exactly one
    h1, no skipped heading level, every card has `aria-labelledby` + a reachable CTA link ·
    covers: M7
  - (pricing-catalog-no-drift.test.ts, amended ADDITIVELY — see below) · covers: M8
  - (pricing-page.test.tsx, NOT edited — verified compatible by static read + baseline green run,
    see evidence below) · covers: M9, R3, R4
  - test_regression_guard_no_duplicate_visible_names: assert each of the 5 tier names resolves to
    exactly one card (role+name throws on 0 OR >1 match) · covers: R2
  - test_dark_theme_renders_all_5_cards_correctly: gate on 5-card + catalog-derived price per card,
    then assert zero raw color/inline-style — STATIC/structural proxy, honestly noted in-file: no
    stylesheet is loaded in this jsdom harness and no theme toggle is simulated · covers: edge case
  - test_regression_pin_unknown_catalog_name_throws: `[REGRESSION PIN]` — already-shipped guard in
    `lib/pricing-catalog.ts`, unchanged by this task, GREEN today by design, excluded from the red
    count · covers: edge case
  - test_keyboard_nav_reaches_every_cta_in_visual_order: gate on 5-card count, then assert CTA
    labels appear in DOM order Free→Starter→Pro→Team→Enterprise — a DOM-order proxy for tab order
    (honestly noted: no real browser Tab-key simulation exists anywhere in this harness) ·
    covers: edge case
</test_plan>

Tests live in: `./tests/` · `tests/pricing-tier-ladder.test.tsx` (NEW, 13 tests: 12 red / 1
  labelled `[REGRESSION PIN]` green-by-design) + `tests/pricing-catalog-no-drift.test.ts` (FROZEN
  @ v1, AMENDED additively per M8: 4 tests total, 2 retargeted-red + 2 unchanged-green) · MUST run
  red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

RED evidence — `tests/pricing-tier-ladder.test.tsx`
  (`./node_modules/.bin/vitest run tests/pricing-tier-ladder.test.tsx --reporter=verbose`, run
  from `apps/dashboard`, 2026-07-20):
```
 Test Files  1 failed (1)
      Tests  12 failed | 1 passed (13)
```
Every failure traced to its root cause (spot-checked via
`grep -A3 "AssertionError\|TestingLibraryElementError"`): either
`expected [...] to have a length of 5 but got 3` or `Unable to find an accessible element with the
role "article"/"heading" and name /^Free$/i` (or `/^Starter$/i`/`/^Pro$/i`) — the page still renders
3 cards under the old Starter(=free)/Team/Enterprise labels. Missing implementation, the RIGHT
reason; no harness/typo failure. The 1 green test is the `[REGRESSION PIN]`.

RED evidence — `tests/pricing-catalog-no-drift.test.ts` (FROZEN @ v1, amended per M8; run from
  `apps/dashboard`, 2026-07-20):
```
 Test Files  1 failed (1)
      Tests  2 failed | 2 passed (4)
```
The 2 failures are exactly the retargeted (`/^free$/i` card not found) and new (`/^starter$/i`,
`/^pro$/i` cards not found) assertions. The 2 green tests
(`test_pricing_catalog_matches_seeded_backend`, `test_reject_hand_edited_price_would_fail_no_drift`)
are UNCHANGED by this amendment and correctly stay green — `PRICING_CATALOG`'s own data was never
wrong, only the page's rendering of it.

FROZEN-file verification — `tests/pricing-page.test.tsx` claimed by §0 to need NO edits at 5 cards.
  Verified two ways:
  1. RUN unmodified against the CURRENT (3-card) page from `apps/dashboard`, 2026-07-20 — baseline
     green, 0 files touched:
     ```
      Test Files  1 passed (1)
           Tests  12 passed (12)
     ```
  2. STATIC trace of each of its 7 describe blocks against the FROZEN §3 5-card shape (no 5-card
     implementation exists yet to run against — that empirical confirmation is deferred to
     BUILD/VERIFY, disclosed here rather than fabricated): `test_three_tiers` matches `/starter/i`
     (now the real $1 card, still exactly one match since "Free" no longer matches
     `/starter/i`)/`/team/i`/`/enterprise/i`, `links.length>=3` (true at 5, headroom to spare);
     `test_tier_ctas` needs `>=2` `/signup` links (true at 5: 4 "Get started" + 1 "Talk to us", all
     →`/signup`); `test_pricing_a11y`/`test_reject_heading_order` are structural, count-agnostic;
     `test_reject_public_not_gated` is a source-string check untouched by a data-shape change;
     `test_reject_incomplete_tier` needs `cards.length>=3` (true at 5) and every card's text to
     match `/\$|free|contact/i` (Free/$1/$20/$99/Contact us all match);
     `test_pricing_residency_and_priority_story`/`test_pricing_cross_links_ai_act_readiness` target
     the UNTOUCHED residency/AI-Act Card below the grid, independent of tier count. No edit made to
     this file, per §3.

Cross-suite sanity — this task's 2 files run together with the sibling `homepage-integration-proof`
  task's `tests/base-url-swap.test.tsx` (shared `NEXT_PUBLIC_API_BASE_URL` env-var manipulation in
  both, each restoring it in `afterEach`) plus the untouched `docs-quickstart-page.test.tsx`, from
  `apps/dashboard`, 2026-07-20:
```
 Test Files  3 failed | 4 passed (7)
      Tests  29 failed | 51 passed (80)
```
29 = this task's 12 + 2 + `homepage-integration-proof`'s 15 new/retargeted red tests (12+2+15=29) —
no unexplained failure, no cross-file contamination.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced; preferred architecture/pattern strategies; advise solution/method to resolve issues/implement features; let the named Persona's domain stance (below) shape the approach, not just architecture patterns>

Persona (required): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; name "generic" if no project persona fits yet>
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

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
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
