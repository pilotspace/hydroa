# TASK: Anchor the homepage pricing section with a real number

slug: homepage-price-anchor · created: 2026-07-20 · stage: production
milestone: frontdoor-persona-routing
component: dashboard
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/app/(marketing)/page.tsx:MarketingRootPage` — the `#pricing` teaser section (the
  `<section id="pricing">` block): h2 "Simple, transparent pricing" + one paragraph + a
  `<div className="mt-8">` wrapping a single `<Button>` → `/pricing`. **No price figure anywhere in it
  today** (confirmed by a direct re-read of the live file). Frozen structure (below) requires the section
  keep its id, h2, and `/pricing` link — this task only ADDS content between the paragraph and that
  button-wrapping div.
- `apps/dashboard/lib/pricing-catalog.ts:PRICING_CATALOG, getPricingCatalogEntry, formatBasePrice` —
  FROZEN (`plan-tiers-and-base-fee` TASK.md §3 v1), UNCHANGED by `pricing-tier-ladder`. The file's own
  header names it "the no-drift binding mechanism" — a static, TEST-enforced module mirroring the backend
  migration's seed values EXACTLY, because the dashboard's tests cannot reach live Postgres. Five entries:
  `free` (`basePriceUsd: null`), `starter` (`1.0`), `pro` (`20.0`), `team` (`99.0`), `enterprise` (`null`).
  `formatBasePrice` renders `null` as the CALLER's own `nullLabel` argument — the module's own doc comment
  states it "never hardcodes which null-label applies to which tier" — a non-null value renders `$N`.
- **RE-GROUND (2026-07-21): `pricing-tier-ladder` (this task's dependency, gate=PASS) shipped and changed
  the ground under this task since its original scaffolding — every fact below supersedes that draft.**
  `apps/dashboard/app/(marketing)/pricing/page.tsx:PricingPage, TIERS` — the detail page this task's link
  points to, FROZEN v1 by `pricing-tier-ladder` TASK.md §3 (an explicit CHANGE REQUEST to
  `plan-tiers-and-base-fee`'s old "3 rendered cards" clause — every other clause there, incl. the no-drift
  MECHANISM, is unchanged and still governs). Confirmed by a direct re-read: now renders exactly 5 cards,
  ascending price, personal-then-business order, each still `formatBasePrice(getPricingCatalogEntry(<name>)
  .basePriceUsd, <nullLabel>)` — never a re-hardcoded literal:
    - Free       → `formatBasePrice(free.basePriceUsd, "$0")` → **"$0"** (card TITLED "Free"; the PRICE
      text is "$0" — Tin-decided 2026-07-21, to avoid a "Free / Free" redundant by-text match; `cta` →
      `/signup`).
    - Starter    → `formatBasePrice(starter.basePriceUsd, "Free")` → **"$1"** (nullLabel is inert —
      `starter` is never null; `cta` → `/signup`) — a NEW card; the catalog's real `starter` entry was
      never rendered anywhere before this shipped.
    - Pro        → **"$20"** (same call convention; `cta` → `/signup`) — also a NEW card.
    - Team       → **"$99"**, the sole `featured`/"Most popular" card (unchanged; `cta` → `/signup`).
    - Enterprise → **"Contact us"** (unchanged; `cta` → `/signup`, label "Talk to us").
- Raw ground truth the catalog mirrors: `apps/gateway/migrations/versions/113ebdbe9f09_plan_tiers_and_base_fee.py`
  — unchanged, still the byte-identical source `PRICING_CATALOG` mirrors (all 5 rows, incl. `starter`/`pro`,
  already existed in this migration long before `pricing-tier-ladder` — that task changed only which
  entries `/pricing` RENDERS, not the catalog or the migration). Confirmed by
  `apps/dashboard/tests/pricing-catalog-no-drift.test.ts` (read in full): asserts the catalog against
  `EXPECTED_SEED_BASE_PRICES` hand-copied from this migration, AND that `/pricing`'s rendered price text is
  a pure function of the catalog for all 5 tiers, never a re-hardcoded literal.
- **The original draft's R-b ("no customer-facing surface renders the $1 `starter` entry, anywhere") is
  now FALSE — resolved by `pricing-tier-ladder`.** `/pricing` renders `starter` ($1) and `pro` ($20)
  publicly today, each linking to `/signup`. `pricing-tier-ladder` TASK.md §3's freeze note additionally
  records (orchestrator-verified independently, not taken on the draft's word) that
  `apps/dashboard/app/(app)/app/plan/page.tsx` + `components/checkout/UpgradePlanDialog.tsx` +
  `lib/checkout.ts` all exist (confirmed present by `ls`) — Starter/Pro are genuinely purchasable
  post-signup via the same in-app upgrade flow Team already has. Rendering either figure is honest.
  `apps/dashboard/app/(app)/app/platform/plans/page.tsx` stays superadmin-only (own header comment:
  `require_superadmin` is "the sole enforcement point") — an internal ops view, irrelevant either way.

Context (working folder): `apps/dashboard/app/(marketing)/page.tsx` (the one file edited) + a read-only
import of `apps/dashboard/lib/pricing-catalog.ts` + a new sibling test file under `apps/dashboard/tests/`.
No backend, no DB, no new endpoint — a static presentational addition, matching the `component: dashboard`
declared above.

Honors (patterns / conventions): Geist / Geist Mono font tokens + azure/graphite "Airier" palette
(`--primary`, `--accent-soft`, `text-foreground`/`text-muted-foreground` utilities) from
`apps/dashboard/app/globals.css` — both the default (light) block and the `@media
(prefers-color-scheme: dark)` block already cover these utility classes, so no new token is needed.
Reuse of `formatBasePrice`/`getPricingCatalogEntry` — the `starter` figure uses the BYTE-IDENTICAL call
`/pricing`'s own Starter card uses (`formatBasePrice(getPricingCatalogEntry("starter").basePriceUsd,
"Free")`); the `free` figure uses this task's OWN `nullLabel` ("Free", not `/pricing`'s "$0") — legitimate
per the module's own documented per-caller contract, see §1/R-f. `data-slot` markers as the test-anchor
convention used elsewhere in this same milestone (`dns-verify-softeners`).

Seams consulted: the two frozen sibling contracts cited above (`pricing-catalog.ts` — CONSUMED, unmodified;
`pricing/page.tsx` — its RENDERED OUTPUT is the cross-check target, file itself unmodified) plus
`pricing-tier-ladder` TASK.md §3 (FROZEN @ v1) as the up-to-date record of what `/pricing` now shows.

Anchors the contract cites: `MarketingRootPage`'s `#pricing` section; `PRICING_CATALOG` /
`getPricingCatalogEntry` / `formatBasePrice`; catalog entries `free` and `starter`; a new
`data-slot="price-anchor"` marker; the frozen `#pricing` section id + h2 + `/pricing` button asserted by
`apps/dashboard/tests/landing-page.test.tsx` and `apps/dashboard/tests/design-system/landing-fidelity.test.tsx`
(both NOT edited by this task — confirmed by a full re-read: neither asserts `#pricing`'s exact child count
or forbids a new sibling element).

Issues/Risks (→ feed §1):
- **R-a (the defect this task fixes):** the teaser section shows zero figures today — "Simple, transparent
  pricing" over a blank promise, per the shared milestone context's observed defect.
- **R-b (SUPERSEDED — kept for the record, not carried forward as open):** the original draft rejected
  anchoring on `starter` ($1) because no customer-facing surface rendered it. That premise is now false —
  see the RE-GROUND note above. Replaced by R-e below.
- **R-c (drift risk, still live):** any anchor MUST derive from `PRICING_CATALOG` (never a re-hardcoded
  literal) or it silently drifts from the backend migration the instant either tier's price changes — the
  exact failure mode `pricing-catalog-no-drift.test.ts` exists to catch on `/pricing`; this task's new
  homepage figures need the same guarantee, and ideally the same style of test.
- **R-d (frozen anchors, still live):** the `#pricing` section's id, h2, and `/pricing` link are asserted
  by two FROZEN suites (`landing-page.test.tsx`, `design-system/landing-fidelity.test.tsx`) — any change
  here must be strictly additive within the section, never touching those anchors.
- **R-e (NEW — a genuine, still-open disagreement — feeds the §1 ⚠ flag, do NOT silently resolve):** now
  that `starter` ($1) is independently verifiable one click away (same as Team always was), is Free+$1
  (the true entry price a self-serve buyer filters on, per the persona brief) the better homepage anchor
  than the original draft's Free+Team-$99 — or does $99 stay the better anchor for a business-buyer-first
  positioning (Tin's locked tier split explicitly separates personal Free/$1/$20 from business
  Team-$99/Enterprise)? Both figures are now equally HONEST (both verifiable one click away); this is a
  POSITIONING call, not a trust-safety one — reversing the original draft's pick needs explicit human
  confirmation, not a silent re-pick either way.
- **R-f (NEW — a resolved, non-⚠ nuance, stated plainly so it is never mistaken for a drift later):**
  `/pricing`'s Free card shows PRICE text "$0" (not the word "Free") as of `pricing-tier-ladder`'s
  2026-07-21 change. This task's draft anchor still says "Free" — a plain-English render of the SAME
  `basePriceUsd === null`, using this task's own `nullLabel` choice. `pricing-catalog.ts`'s own doc comment
  states the module "never hardcodes which null-label applies to which tier," so two different, both-
  correct renders of the same null value is NOT the R1/R-c drift failure mode.

Related intent: shared milestone context `frontdoor-persona-routing` — persona P1 Priya (platform
lead/buyer) is "currently well served" once she reaches `/pricing`, but the homepage itself shows no
number before that click, and self-serve buyers filter on price before they click through (persona brief).
No new GLOSSARY term.

Ground SHA: 8daf22c (branch `feat/frontdoor-persona-routing`) is the last commit touching this task's own
scaffolding; HEAD is now `9421827` (milestone red-suite scaffolding commit) — **but `pricing-tier-ladder`'s
own code (the `/pricing` + homepage changes cited above) is currently UNCOMMITTED in the shared working
tree** (`git status`: both `page.tsx` files show `M`), consistent with this repo's parallel-task workflow
(multiple sibling tasks build in one working tree before a milestone-wide commit). Every fact above was
verified by reading the LIVE working-tree files directly, not a git ref — cite symbols, not bare line
numbers; "as of" the current working tree, re-grounded 2026-07-21.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Surface a real, catalog-sourced price anchor (Free + the true lowest-priced paid tier, `starter`
at $1/mo) in the homepage's `#pricing` teaser section, additive to the existing h2/paragraph/CTA, keeping
`/pricing` as the link for full detail.

Framings weighed:
- **Inline price-anchor line, additive to the existing teaser (CHOSEN)** — one short, catalog-sourced
  phrase ("Free to start · plans from $1/mo") between the existing paragraph and the "View pricing plans"
  button. Minimal IA change, matches the milestone Scope note ("keeping the link for detail"), reuses
  `pricing-catalog.ts` so it can never disagree with `/pricing`'s own numbers.
- Duplicate the full 5-card `/pricing` grid on the homepage (rejected) — doubles the no-drift surface to
  maintain, bloats a section whose whole job is to be a short teaser before the link, and the milestone's
  stated Scope explicitly keeps the link for detail rather than inlining the detail page.
- Single-figure "Free" stat with no paid number (rejected) — answers "can I try it" but not "what does it
  cost for real"; a self-serve buyer filters on the PAID price before clicking through (persona brief),
  so a free-only anchor leaves the actual filtering question unanswered.
- Anchor on Free + Team ($99) — the ORIGINAL draft's choice, now RECONSIDERED (§0 R-e): scaffolded when
  `starter` ($1) was not yet reachable anywhere, making $99 the only verifiable paid figure at the time.
  `pricing-tier-ladder` has since shipped `starter` onto `/pricing` publicly — $1 is now equally honest
  AND is the true entry-level price a self-serve buyer filters on first (persona brief), so this draft
  switches the default to Free+$1. Flagged ⚠ below because it reverses a previously-drafted choice — the
  human may prefer $99 for business-buyer-first positioning instead.

Must:
<must>
  - M1 The `#pricing` section renders two concrete figures sourced from `PRICING_CATALOG` (imported, never
    re-hardcoded): the `free` entry (rendered as "Free", this task's own `nullLabel` choice — §0 R-f) and
    the `starter` entry (`formatBasePrice(getPricingCatalogEntry("starter").basePriceUsd, "Free")` →
    "$1", the BYTE-IDENTICAL call `/pricing`'s own Starter card uses). Both entries are independently
    verifiable on `/pricing` today — the free tier's card (priced "$0" there — §0 R-f, same null value,
    different caller-chosen label) and the Starter card (priced "$1", identical text).
  - M2 The two figures render as one short, additive line within the existing section — between the
    current paragraph and the button-wrapping div — not a new card grid, not a new section, not a new
    heading level.
  - M3 The existing `#pricing` section id, its h2 "Simple, transparent pricing", its paragraph, and its
    "View pricing plans" → `/pricing` button all remain exactly as today (additive only).
  - M4 The new content is real text (not an image, not decorative-only), legible to a screen reader as one
    coherent phrase (a single element, not disconnected fragments needing extra `aria-label` scaffolding).
  - M5 Uses only existing design tokens/utility classes already present in this file (`text-foreground`,
    `text-muted-foreground`, the section's existing type scale) — no new color, no new font; both the
    light and dark `globals.css` blocks already cover these tokens.
  - M6 Renders as static server-side markup — `page.tsx` stays a Server Component (no `"use client"`, no
    fetch, no client-only API), consistent with the page's own frozen §3 v1 note ("PUBLIC — no cookie
    check, no authed fetch, no redirect").
  - M7 Carries a `data-slot="price-anchor"` marker so the new test suite (and any future one) has a
    stable, non-text-matching anchor.
</must>
Reject:
<reject>
  - R1 A literal `"$1"`/`"Free"` string typed directly in `page.tsx`, bypassing
    `getPricingCatalogEntry`/`formatBasePrice` -> MUST NOT ship — this is the exact failure mode
    `pricing-catalog-no-drift.test.ts` exists to catch on the sibling `/pricing` page; this task must not
    reintroduce it on the homepage.
  - R2 Any figure not currently, independently verifiable on `/pricing` -> MUST NOT ship. (§0 R-b's
    original unreachability concern is resolved — `starter`/`pro` are both on `/pricing` now — so this
    rejection is restated generically: the anchor may only cite an entry `/pricing` already renders,
    never a catalog entry `/pricing` has not shipped.) Resolved for now as Free + Starter ($1), flagged
    below (⚠) for explicit human confirmation — see §0 R-e.
  - R3 A new full pricing grid, a new section, or a new top-level heading inside `#pricing` -> MUST NOT
    ship — out of the milestone's stated Scope ("keeping the link for detail"); duplicating `/pricing`'s
    IA on the homepage is scope creep this task rejects.
  - R4 Removing, renaming, or relinking the existing "View pricing plans" button, the section id, or the
    h2 -> MUST NOT ship — both frozen suites (`landing-page.test.tsx`, `landing-fidelity.test.tsx`) assert
    these and must stay green, untouched.
  - R5 A client-fetched or client-rendered price (`"use client"`, `useEffect`, any network call) -> MUST
    NOT ship — breaks the page's own frozen "Server Component, no client directive" contract; the catalog
    is already a static import, so no fetch is ever needed.
</reject>
After:
<after>
  - A visitor scrolling the homepage sees a real number — Free to start, plans from $1/mo — before ever
    clicking through.
  - Clicking "View pricing plans" lands on `/pricing`, where the SAME `starter` figure ($1, byte-identical
    text) is already rendered, and the Free tier's null base price is independently rendered there too
    (as "$0" — §0 R-f) — no homepage/detail NUMERIC mismatch, ever, because both derive from the one
    `PRICING_CATALOG` module.
  - The section's id, heading, and existing link are unchanged — `landing-page.test.tsx` and
    `landing-fidelity.test.tsx` stay green without modification.
  - Both light and dark themes render the anchor legibly with zero new tokens.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Anchoring on Free + $1 (Starter), reversing the original draft's Free + $99 (Team) pick — lowest
    confidence in the whole task, because it is a genuine, still-open positioning disagreement (§0 R-e),
    not a judgment call to make silently. The original draft chose $99 because $1 was, at the time,
    unreachable on any customer surface (§0 R-b) — that premise no longer holds: `pricing-tier-ladder`
    shipped `starter` ($1) onto `/pricing` publicly, verifiable one click away, exactly like $99 always
    was. $1 is now BOTH the true entry-level paid price (what a self-serve buyer filters on first, per the
    persona brief) and equally honest. The counter-case for keeping $99: Tin's locked tier split treats
    Team/$99+ as the "business buyer" line distinct from personal Free/$1/$20 — a homepage that leads with
    "$1" could under-signal the product's enterprise positioning to a business-buyer visitor (persona P1
    Priya). Low cost to reverse either way (a one-line copy change, no re-plumb) — confirm at freeze.
  - [ ] Exact copy wording ("Free to start · plans from $1/mo" vs. an alternative phrasing) — confirm with
    the human at freeze; low cost if wrong (a copy-only follow-up, not a re-plumb).
  - [x] `PRICING_CATALOG` is safe to import into `page.tsx` unchanged — confirmed: it is already imported
    the same way by the sibling `/pricing` page; zero runtime cost (a static object), no fetch.
  - [x] The frozen `#pricing` section anchors (id/h2/button) are additive-safe — confirmed by reading both
    `landing-page.test.tsx` and `design-system/landing-fidelity.test.tsx` in full: neither asserts the
    section's exact child count or forbids a new sibling element, only id/h2/button presence.
  - [x] Using a DIFFERENT `nullLabel` ("Free") than `/pricing`'s own Free-card choice ("$0") for the SAME
    catalog entry is not a drift — confirmed by reading `pricing-catalog.ts`'s own doc comment: the
    module explicitly leaves the null-label choice to each caller (§0 R-f).
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Homepage renders the catalog-sourced Free + Starter figures   # M1
  Given the homepage #pricing section
  When it renders
  Then it shows formatBasePrice(getPricingCatalogEntry("free").basePriceUsd, "Free") -> "Free"
  And it shows formatBasePrice(getPricingCatalogEntry("starter").basePriceUsd, "Free") -> "$1"
  And the "$1" figure is byte-identical to what /pricing's Starter card renders
  And the "Free" figure represents the SAME basePriceUsd===null /pricing's Free card renders as "$0"
    (different nullLabel, same underlying value — not a drift, §0 R-f)

Scenario: The anchor is one additive line, not a new grid or heading   # M2
  Given the #pricing section's existing h2, paragraph, and button
  When the price anchor is added
  Then it renders as a single new element between the paragraph and the button
  And no new <h2>/<h3> is introduced
  And no card grid is introduced

Scenario: The section's frozen anchors are unchanged   # M3, R4
  Given the homepage before this task
  When the price anchor ships
  Then #pricing keeps its id, its h2 text "Simple, transparent pricing", and its "View pricing plans"
    button linking to /pricing
  And landing-page.test.tsx and design-system/landing-fidelity.test.tsx pass unmodified

Scenario: The anchor is one accessible phrase   # M4
  Given a screen reader reaching the #pricing section
  When it reads the price anchor
  Then it reads as one coherent sentence
  And no separate aria-label is required to make sense of it

Scenario: The anchor uses only existing tokens, in both themes   # M5
  Given the dark theme is active
  When the #pricing section renders
  Then the price anchor text uses text-foreground/text-muted-foreground (existing classes only)
  And no new CSS custom property or font-family is introduced

Scenario: The homepage stays a pure Server Component   # M6, R5
  Given apps/dashboard/app/(marketing)/page.tsx
  When the price anchor is added
  Then the file has no "use client" directive
  And no fetch/useEffect/client-only API is introduced
  And the catalog values are available at render time via the existing static import

Scenario: The anchor carries a stable test anchor   # M7
  Given the rendered #pricing section
  When queried by test code
  Then data-slot="price-anchor" locates the new content without a text match

Scenario: A hand-typed literal price is rejected   # R1
  Given a hypothetical implementation that types "$1" directly into page.tsx
  When a no-drift check runs (mirroring pricing-catalog-no-drift.test.ts's own pattern)
  Then it fails, because the rendered text must equal formatBasePrice(getPricingCatalogEntry("starter").basePriceUsd, ...)
  And the catalog import stays the single source of truth

Scenario: An unreachable or invented figure is rejected   # R2
  Given a hypothetical anchor citing a catalog entry /pricing does not currently render
  When the homepage anchor is authored
  Then it uses only entries (free, starter) that /pricing already renders publicly today
  And if pro/team/enterprise were ever proposed for this anchor, they would first need to already be
    reachable on /pricing (true today for all five, but this anchor deliberately stays to free+starter
    per §1 M1/§0 R-e)

Scenario: A full pricing grid on the homepage is rejected   # R3
  Given the milestone Scope note "keeping the link for detail"
  When the price anchor is authored
  Then #pricing does not gain a 3-card grid or a second heading
  And "View pricing plans" remains the only path to full detail

Scenario: The existing CTA and heading are never touched   # R4
  Given the current #pricing section markup
  When this task ships
  Then the button's label "View pricing plans" and href "/pricing" are byte-identical to before
  And the h2 text and #pricing id are byte-identical to before

Scenario: A client-rendered price is rejected   # R5
  Given a hypothetical client-fetched implementation
  When reviewed against the page's frozen "Server Component, no client directive" contract
  Then it is rejected
  And the static PRICING_CATALOG import is used instead

Scenario: Homepage and /pricing never disagree (cross-page consistency — edge case)
  Given both apps/dashboard/app/(marketing)/page.tsx and apps/dashboard/app/(marketing)/pricing/page.tsx
  When either renders its Free/Starter figures
  Then both derive from the same PRICING_CATALOG entries (free, starter)
  And the "$1" substring is byte-identical text on both pages (same call, same nullLabel)
  And the "Free" (homepage) / "$0" (pricing) substrings represent the same basePriceUsd===null by design,
    not a drift (§0 R-f) — a future price change to the migration + catalog still updates both pages'
    NUMBERS identically, no page left stale

Scenario: No async data, so no loading/error/partial-failure state applies (deliberately ruled out)
  Given the price anchor never calls a network endpoint
  Then there is no loading skeleton, no error state, and no partial-failure case to test
  And this is a deliberate ruling-out, not an omission — the catalog is a static import evaluated at
    build/render time
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is a DASHBOARD (presentation-only) task — the "shape" frozen here is OBSERVABLE homepage markup +
its test anchor, NOT a new HTTP endpoint. It consumes the FROZEN `pricing-catalog.ts` module unchanged.

```
COMPONENT  MarketingRootPage#pricing   (apps/dashboard/app/(marketing)/page.tsx)

PRICE ANCHOR (presentation only; no new endpoint, no fetch) — reads the FROZEN:
  apps/dashboard/lib/pricing-catalog.ts: PRICING_CATALOG / getPricingCatalogEntry / formatBasePrice
    getPricingCatalogEntry("free").basePriceUsd    = null -> formatBasePrice(..., "Free") -> "Free"
    getPricingCatalogEntry("starter").basePriceUsd = 1.0  -> formatBasePrice(..., "Free") -> "$1"
  (the starter call is BYTE-IDENTICAL to /pricing's own Starter-card call — starter is never null, so its
  "Free" nullLabel arg is inert, kept for call-site parity. The free call's "Free" nullLabel is THIS
  task's own choice, independent of /pricing's Free-card choice of "$0" for the same null value — both
  are correct per pricing-catalog.ts's own documented per-caller nullLabel contract; §0 R-f.)

RENDER SHAPE (additive within the existing #pricing section — id/h2/button UNCHANGED):
  <section id="pricing">                                          (existing, unchanged)
    <h2>Simple, transparent pricing</h2>                          (existing, unchanged)
    <p>From solo teams to enterprise deployments. …</p>           (existing, unchanged)
    <p data-slot="price-anchor">                                  (NEW — this task)
      Free to start · plans from $1/mo
    </p>
    <div className="mt-8">                                        (existing, unchanged)
      <Button href="/pricing">View pricing plans</Button>          (existing, unchanged)
    </div>
  </section>

Copy (LOCKED at freeze — Tin-decided 2026-07-21):
  "Free to start · plans from $1/mo"
  — both price substrings are the LIVE output of formatBasePrice(...) above, never literals; if the copy
    template changes, the two substrings must remain pure function output of that catalog call.
  — ✅ ANCHOR CHOICE RESOLVED (§1/§0 R-e) — Tin-decided 2026-07-21 at freeze: anchor on free+starter ($1),
    the builder-first / lowest-friction framing, now that starter is independently verifiable on /pricing.
    The free+team ($99) business-buyer-first alternative was considered and NOT taken. This is now a FROZEN
    contract term, not an open assumption: the anchor reads getPricingCatalogEntry("starter"), and switching
    it to ("team") later is a CHANGE REQUEST back through CONTRACT, not a copy edit.

Tokens used (existing only, no additions): text-foreground / text-muted-foreground / the section's
existing type scale — Tailwind utility classes reading --primary / --accent-soft / --font-sans, already
defined in apps/dashboard/app/globals.css in both the default (light) block and the
@media (prefers-color-scheme: dark) block.

UNCHANGED (frozen, inherited — a test guards each):
  - #pricing section id, h2 "Simple, transparent pricing", paragraph, "View pricing plans" -> /pricing
    button                                       — apps/dashboard/tests/landing-page.test.tsx,
                                                     apps/dashboard/tests/design-system/landing-fidelity.test.tsx (NOT edited)
  - PRICING_CATALOG / getPricingCatalogEntry / formatBasePrice
                                                  — apps/dashboard/lib/pricing-catalog.ts (NOT edited;
                                                     owned by plan-tiers-and-base-fee TASK.md §3 v1)
  - /pricing page's own 5-card ladder (Free/Starter/Pro/Team/Enterprise)
                                                  — apps/dashboard/app/(marketing)/pricing/page.tsx (NOT
                                                     edited; owned by pricing-tier-ladder TASK.md §3 v1)
  - the gateway plans catalog / migration        — apps/gateway (NOT touched by this task at all)
```

Glossary deltas: none new — "Free" and "Starter" are existing `PRICING_CATALOG` tier names
(`plan-tiers-and-base-fee` TASK.md's own Glossary); this task introduces no new domain term.

Least-sure flag surfaced at freeze: [spec] the SAME null `free` base price is rendered with two
different nullLabels across two surfaces this milestone touches — the homepage anchor says "Free to
start" while /pricing's Free card says "$0" (pricing-tier-ladder, Tin-decided 2026-07-21 to break the
"Free/Free" by-text ambiguity). Both are pure `formatBasePrice(getPricingCatalogEntry("free")...)`
output so DRIFT IS STRUCTURALLY IMPOSSIBLE — the risk is purely presentational consistency: a visitor
who reads "Free to start" then sees "$0" one click later. Judged acceptable (each label is the right
word for its own context, and per-caller nullLabel is pricing-catalog.ts's documented contract); if
Tin later wants one voice across both, it is a copy-only change to THIS anchor, no mechanism change.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — the freeze report has not yet been rendered; this draft carries one lowest-confidence flag
(§1/§0 ⚠ R-e — Free+$1 (Starter, this draft's pick) vs. Free+$99 (Team, the original draft's pick, now
that both are equally verifiable — a positioning call, not a trust one) for the human to resolve at the
freeze decision. Status moves to FROZEN @ v1 only by that human approval — never by this agent.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `apps/dashboard/tests/homepage-price-anchor.test.tsx` · MUST run red (missing
implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/(marketing)/page.tsx` `apps/dashboard/tests/homepage-price-anchor.test.tsx`

Strategy (ordered batches):
  1. Write the red suite first (`apps/dashboard/tests/homepage-price-anchor.test.tsx`) against §2's
     scenarios: render `MarketingRootPage`, assert `[data-slot="price-anchor"]` exists, its text equals
     "Free to start · plans from $1/mo", both price substrings equal live `formatBasePrice(...)` output
     (not literals — mirror `pricing-catalog-no-drift.test.ts`'s own pattern), `#pricing`'s id/h2/button
     are byte-identical to before, no new h2/h3, no "use client", axe has 0 new serious/critical.
  2. Confirm the suite is RED for the right reason (missing `<p data-slot="price-anchor">`, not an import
     error).
  3. Add the single `<p data-slot="price-anchor">{...}</p>` element between the existing `<p>` and the
     `<div className="mt-8">` in `MarketingRootPage`'s `#pricing` section, importing
     `getPricingCatalogEntry`/`formatBasePrice` from `@/lib/pricing-catalog` (already a dependency of the
     sibling `/pricing` page — same import path).
  4. Run `landing-page.test.tsx` + `design-system/landing-fidelity.test.tsx` to confirm they stay green,
     untouched.
  5. Run the full dashboard suite (`vitest`, ci.yml dashboard job) to confirm no cross-file regression.

Persona (required): `frontend-engineer` (`.add/personas/frontend-engineer.md`, flow: build/advisor) — the
project's own dashboard-implementation lens (Next.js App Router + shadcn/ui, SSR-safety, design-token
fidelity); directly applicable here since this whole task is a Server Component edit to `apps/dashboard`.
Layer the senior-product-designer stance this CONTRACT was drafted under (SaaS conversion surfaces,
honest-number trust framing) on top of it for the copy/placement call.
Spawn isolation (default): worktree — no explicit parallel-mode requirement here, but this repo runs
`run mode: parallel + auto` with multiple sibling tasks building in the same shared tree; isolate to avoid
picking up an uncommitted sibling's unrelated edit as this task's own diff.
Known-problem fixes: the shared working tree currently carries OTHER sibling tasks' uncommitted changes
(§0 Ground SHA note) — Build must diff against ITS OWN two scoped files only, not assume a clean tree;
do not touch `apps/dashboard/app/(marketing)/pricing/page.tsx` (frozen, owned by `pricing-tier-ladder`) even
though it is also currently uncommitted.
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into
the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): none — no transaction, no shared mutable state; pure static server render.
Code lives in: `apps/dashboard/app/(marketing)/page.tsx`
Constraints: do NOT change any test or the contract; do NOT touch `pricing/page.tsx` or
`pricing-catalog.ts` (both frozen, consumed read-only); allow-list packages only (none new needed); ask if
unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 28 new in `homepage-price-anchor.test.tsx`; the 4-file set (task suite +
      `pricing-catalog-no-drift` + `landing-page` + `landing-fidelity`) ran 58/58 in 1.55s; full
      dashboard green-bar `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`
- [x] coverage did not decrease — no numeric coverage run was performed; assessed qualitatively as a
      net-new, fully-covered presentational addition with a dedicated 28-test suite, no existing path
      removed or weakened. Recorded as an inference, not a measurement.
- [x] no test or contract was altered during build — `git diff` EMPTY on both frozen guard files
      (`tests/landing-page.test.tsx`, `tests/design-system/landing-fidelity.test.tsx`); §3 unchanged;
      `lib/pricing-catalog.ts` has zero diff (consumed read-only)
- [x] the green was EARNED, not gamed — see the refute-read below. ⚠ The load-bearing check here was a
      SOURCE READ, not the suite: all 28 tests compute the expected value with the same catalog call
      they assert against, so a hand-typed `"$1"`/`"Free"` literal in the JSX would pass all 28. The
      verifier read `page.tsx:196-203` directly and confirmed both substrings are live call
      expressions. (Same limitation exists in the already-approved sibling
      `pricing-catalog-no-drift.test.ts` — an inherited project convention, not a defect introduced
      here. Strengthening logged as a delta in §7.)
- [x] concurrency / timing of the risky operation is safe — pure synchronous render from a
      module-level immutable `const` array; no shared mutable state, no async boundary
- [x] no exposed secrets, injection openings, or unexpected dependencies — static Server Component,
      no `"use client"`, no fetch/useEffect, no user input; reads only an in-repo constant
- [x] layering & dependencies follow CONVENTIONS.md — consumes the frozen `@/lib/pricing-catalog`
      exactly as the sibling `/pricing` page already does; no new dependency
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] The homepage's `#pricing` section shows a `data-slot="price-anchor"` element reading "Free to start ·
  plans from $1/mo" — the ⚠ flag resolved to the $1 Starter (builder-first) copy, NOT the $99 alternative.
  Placed between the existing paragraph (l.191) and the button div (l.204), at `page.tsx:195-203`.
  Confirmed by rendering `MarketingRootPage` in the suite + a direct source read.
- [x] Both price substrings are the LIVE output of `formatBasePrice(getPricingCatalogEntry(...))`, never a
  literal — confirmed by reading `page.tsx:196-203` directly: both are call expressions
  (`formatBasePrice(getPricingCatalogEntry("free").basePriceUsd, "Free")` and the same for `"starter"`),
  imported at l.9 and used exactly twice. ⚠ The suite alone could NOT have caught a literal (see the
  earned-green note above) — the source read is the real evidence for this line.
- [x] `#pricing`'s id, h2 text, paragraph, and "View pricing plans" → `/pricing` button are byte-identical
  to before — confirmed by `git diff` showing ZERO changes to `landing-page.test.tsx` and
  `design-system/landing-fidelity.test.tsx`, both passing, plus a source re-read of l.180-208.
- [x] `page.tsx` remains a Server Component — confirmed by grep (no `"use client"`, no `useEffect`, no
  fetch added) and test-enforced at `test_server_component_only` / `test_reject_client_rendered_price`.
- [x] Full dashboard suite green-bar — confirmed by `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`; the focused 4-file set ran 58/58.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `formatBasePrice` / `getPricingCatalogEntry` imported at `page.tsx:9` and called
      exactly twice (l.199, l.201) inside the single new `<p data-slot="price-anchor">` (l.195-203),
      which sits exactly where §3's RENDER SHAPE specifies.
- [x] DEAD-CODE (code) — no orphaned import, no unused symbol; the new element is the only consumer and
      it renders unconditionally.
- [ ] SEMANTIC (prose / non-code) — n/a, this is a code task.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — `MarketingRootPage`
      (`page.tsx:93`), `#pricing` section (`page.tsx:180`), `data-slot="price-anchor"` (`page.tsx:196`),
      and `PRICING_CATALOG` / `getPricingCatalogEntry` / `formatBasePrice`
      (`lib/pricing-catalog.ts:29,37,51`) all resolve live. `/pricing`'s Starter card call resolves at
      `pricing/page.tsx:71` and is byte-identical to the homepage's.
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — NONE moved. The
      §0 RE-GROUND note (2026-07-21) already accounts for the concurrent `pricing-tier-ladder` shift;
      nothing further drifted since Ground SHA `8daf22c`.
      Deliberate divergence confirmed genuine, not drift: the homepage renders Free's `null` price as
      "Free" while `/pricing` renders the same `null` as "$0" — same value, different caller-chosen
      `nullLabel`, exactly as `pricing-catalog.ts`'s own doc comment intends.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: agent a14e915 (independent add-verify, appsec/frontend persona) · adversarially checked:
(a) whether either price substring is a re-hardcoded literal that only coincidentally matches — the
load-bearing check, since all 28 tests compute their expected value with the same catalog call and
therefore CANNOT distinguish a literal from a live binding; resolved by direct JSX source read, form
is genuinely a call expression; (b) whether any of the 15 pre-passing tests secretly asserts the NEW
anchor and silently pre-passed — all 15 walked against pre-build state and classified as catalog
invariants, R3/R4/R5 regression guards, or sibling-page coverage; the exact 13-failed/15-passed RED
split was reconstructed and matches the build report; (c) whether the frozen guard files were touched
— `git diff` EMPTY on both.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: agent a14e915
1. Security: CLEAR — static Server Component, no `"use client"`, no fetch/useEffect/network call
   (grep-confirmed AND test-enforced), no user input, no secrets; reads only an immutable in-repo array.
2. Concurrency: CLEAR (n/a, justified) — pure synchronous render from a module-level immutable const;
   no shared mutable state, no async boundary, no race window.
3. Architecture: CLEAR — consumes the frozen `@/lib/pricing-catalog` exactly as the sibling `/pricing`
   page does (same import path, same call convention for the shared `starter` entry); presentation-only
   addition scoped to the one file §3 names.
Verdict: PASS
Residue: none
Binding: advisory — sensitivity is non-security (presentation-only)

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
component: dashboard · expected green-bar: vitest (ci.yml dashboard job, working-directory: apps/dashboard) · verify: cd apps/dashboard && npx vitest run
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-21

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).
- [SPEC · open] **A catalog-sourced price is not actually test-enforced as catalog-sourced.** Every test
  in `homepage-price-anchor.test.tsx` computes its expected value with the SAME
  `formatBasePrice(getPricingCatalogEntry(...))` call it asserts against, so a hand-typed `"$1"`/`"Free"`
  literal in the JSX would pass all 28. The sibling already-approved `pricing-catalog-no-drift.test.ts`
  has the identical shape — this is an inherited project convention, not a defect introduced here. Fix:
  `vi.mock` `PRICING_CATALOG` with a changed price, re-render, and assert the rendered text CHANGES —
  which proves a live binding. Apply to BOTH suites. (evidence: verifier a14e915 §2; the only thing
  standing between us and a silent re-hardcode today is a human source read)

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

- [TDD · folded] **"Assert against a fixture, not against the code under test."** A test that recomputes [folded foundation-version 55]
  the expected value using the production call it is verifying can only ever prove self-consistency, never
  correctness. It looks rigorous and is vacuous in exactly one direction — the direction that matters.
  (evidence: 28 green tests that a hardcoded literal would also have passed)
