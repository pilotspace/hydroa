# TASK: Anchor the homepage pricing section with a real number

slug: homepage-price-anchor · created: 2026-07-20 · stage: production
milestone: frontdoor-persona-routing
component: dashboard
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/app/(marketing)/page.tsx:MarketingRootPage` — the `#pricing` teaser section (the
  `<section id="pricing">` block): h2 "Simple, transparent pricing" + one paragraph + a single
  `<Button>` → `/pricing`. **No price figure anywhere in it today.** Frozen structure (below) requires
  the section keep its id, h2, and `/pricing` link — this task only ADDS content between the paragraph
  and the button.
- `apps/dashboard/lib/pricing-catalog.ts:PRICING_CATALOG, getPricingCatalogEntry, formatBasePrice` —
  FROZEN (`plan-tiers-and-base-fee` TASK.md §3 v1). The file's own header names it "the no-drift binding
  mechanism" — a static, TEST-enforced module mirroring the backend migration's seed values EXACTLY,
  because the dashboard's tests cannot reach live Postgres. Five entries: `free` (`basePriceUsd: null` →
  caller renders "Free"), `starter` (`1.0`), `pro` (`20.0`), `team` (`99.0`), `enterprise` (`null` →
  caller renders "Contact us").
- `apps/dashboard/app/(marketing)/pricing/page.tsx:PricingPage, TIERS` — the detail page this task's
  link points to. Renders exactly 3 cards (frozen v1, `plan-tiers-and-base-fee` §3 M4): "Starter" card
  (bound to catalog `free` → displays "Free", "for evaluation"), "Team" card (bound to catalog `team` →
  displays "$99", `featured`/"Most popular"), "Enterprise" card (bound to catalog `enterprise` →
  "Contact us"). The comment at lines 42-48 is explicit: this page's "Starter" card is the free tier's
  evaluation entry point, and is a DIFFERENT thing from the catalog's own `starter` tier — quoted verbatim:
  "a distinct, NOT-yet-rendered personal $1 tier."
- Raw ground truth the catalog mirrors: `apps/gateway/migrations/versions/113ebdbe9f09_plan_tiers_and_base_fee.py`
  — `UPDATE plans SET … base_price_usd_monthly = '1.00' WHERE name = 'starter'` (line 92),
  `= '20.00' WHERE name = 'individual'`→renamed `'pro'` (line 97-98), `= '99.00' WHERE name = 'team'`
  (line 101); a NEW `free` row inserted with `base_price_usd_monthly=NULL`; `enterprise` stays NULL.
  Byte-identical to `PRICING_CATALOG` per the frozen no-drift test
  `apps/dashboard/tests/pricing-catalog-no-drift.test.ts` (asserts the catalog against
  `EXPECTED_SEED_BASE_PRICES` hand-copied from this migration, AND that `/pricing`'s rendered price text
  is a pure function of the catalog, never a re-hardcoded literal — `test_reject_hand_edited_price_would_fail_no_drift`).
- **No customer-facing surface renders the $1 `starter` or $20 `pro` entries, anywhere, today.**
  `apps/dashboard/app/(app)/app/platform/plans/page.tsx` is superadmin-only — its own header comment
  states the gateway's `require_superadmin` dependency is "the sole enforcement point," an internal ops
  catalog view, not a customer purchase flow. A repo-wide search for a dashboard "checkout" route under
  `apps/dashboard/app/` returns nothing. `apps/gateway/src/gateway/tenants/application/self_serve_plans.py:
  list_self_serve_plans` (`ORDER BY base_price_usd_monthly ASC NULLS FIRST` — confirming `starter` IS the
  true lowest-priced row) and `.../checkout_service.py` are backend-only, exercised solely by their own
  test suites (`tests/self_serve_plans_catalog/`, `tests/self_serve_checkout/`) — no dashboard page is
  wired to either yet.

Context (working folder): `apps/dashboard/app/(marketing)/` + a read-only import of
`apps/dashboard/lib/pricing-catalog.ts` + `apps/dashboard/tests/` (new sibling suite). No backend, no DB,
no new endpoint — a static presentational addition, matching the `component: dashboard` declared above.

Honors (patterns / conventions): Geist / Geist Mono font tokens + azure/graphite "Airier" palette
(`--primary`, `--accent-soft`, `text-foreground`/`text-muted-foreground` utilities) from
`apps/dashboard/app/globals.css` — both the default (light) block and the `@media
(prefers-color-scheme: dark)` block already cover these utility classes, so no new token is needed.
Reuse of `formatBasePrice`/`getPricingCatalogEntry` exactly as `pricing/page.tsx` already calls them (the
no-drift test's own enforced convention — no re-hardcoded literal). `data-slot` markers as the test-anchor
convention used elsewhere in this same milestone (`dns-verify-softeners`).

Seams consulted: none beyond the two frozen sibling contracts cited above (`pricing-catalog.ts`,
`pricing/page.tsx`) — both CONSUMED here, neither modified.

Anchors the contract cites: `MarketingRootPage`'s `#pricing` section; `PRICING_CATALOG` /
`getPricingCatalogEntry` / `formatBasePrice`; catalog entries `free` and `team`; a new
`data-slot="price-anchor"` marker; the frozen `#pricing` section id + h2 + `/pricing` button asserted by
`apps/dashboard/tests/landing-page.test.tsx` and `apps/dashboard/tests/design-system/landing-fidelity.test.tsx`
(both NOT edited by this task).

Issues/Risks (→ feed §1):
- **R-a (the defect this task fixes):** the teaser section shows zero figures today — "Simple, transparent
  pricing" over a blank promise, per the shared milestone context's observed defect.
- **R-b (a real repo-surface disagreement — feeds the §1 ⚠ flag, do NOT silently resolve):** the catalog's
  true lowest-priced row is `starter` at $1/mo, but NO customer-reachable surface — public or authed —
  renders it (confirmed above: not on `/pricing`, no dashboard checkout route exists). The only PUBLIC
  surface, `/pricing`, skips straight from "Free" to "$99" (Team). Anchoring the homepage on $1 would cite
  a real, DB-backed number a visitor could never verify by clicking "View pricing plans" — recreating the
  exact "shows a promise nobody can find" trust break this task exists to close, just at $1 instead of at
  nothing. Anchoring on $99 stays consistent with what `/pricing` already renders, at the cost of skipping
  over the catalog's true entry-level paid price.
- **R-c (drift risk):** any anchor MUST derive from `PRICING_CATALOG` (never a re-hardcoded literal) or it
  silently drifts from the backend migration the instant either tier's price changes — the exact failure
  mode `pricing-catalog-no-drift.test.ts` exists to catch on `/pricing`; this task's new homepage figures
  need the same guarantee, and ideally the same style of test.
- **R-d (frozen anchors):** the `#pricing` section's id, h2, and `/pricing` link are asserted by two FROZEN
  suites (`landing-page.test.tsx`, `design-system/landing-fidelity.test.tsx`) — any change here must be
  strictly additive within the section, never touching those anchors.

Related intent: shared milestone context `frontdoor-persona-routing` — persona P1 Priya (platform
lead/buyer) is "currently well served" once she reaches `/pricing`, but the homepage itself shows no
number before that click, and self-serve buyers filter on price before they click through (persona brief).
No new GLOSSARY term.

Ground SHA: 8daf22c (branch `feat/frontdoor-persona-routing`) — cite symbols, not bare line numbers; any
line ref above is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Surface a real, catalog-sourced price anchor (Free + the lowest customer-visible paid price) in
the homepage's `#pricing` teaser section, additive to the existing h2/paragraph/CTA, keeping `/pricing` as
the link for full detail.

Framings weighed:
- **Inline price-anchor line, additive to the existing teaser (CHOSEN)** — one short, catalog-sourced
  phrase ("Free to start · Team plans from $99/mo") between the existing paragraph and the "View pricing
  plans" button. Minimal IA change, matches the milestone Scope note ("keeping the link for detail"),
  reuses `pricing-catalog.ts` so it can never disagree with `/pricing`'s own numbers.
- Duplicate the full 3-card `/pricing` grid on the homepage (rejected) — doubles the no-drift surface to
  maintain, bloats a section whose whole job is to be a short teaser before the link, and the milestone's
  stated Scope explicitly keeps the link for detail rather than inlining the detail page.
- Single-figure "Free" stat with no paid number (rejected) — answers "can I try it" but not "what does it
  cost for real"; a self-serve buyer filters on the PAID price before clicking through (persona brief),
  so a free-only anchor leaves the actual filtering question unanswered.

Must:
<must>
  - M1 The `#pricing` section renders two concrete figures sourced from `PRICING_CATALOG` (imported, never
    re-hardcoded): the `free` entry (formats to "Free") and the `team` entry (formats to "$99") — matching
    exactly what `/pricing` itself renders for those two tiers.
  - M2 The two figures render as one short, additive line within the existing section — between the
    current paragraph and the "View pricing plans" button — not a new card grid, not a new section, not a
    new heading level.
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
  - R1 A literal `"$99"`/`"Free"` string typed directly in `page.tsx`, bypassing
    `getPricingCatalogEntry`/`formatBasePrice` -> MUST NOT ship — this is the exact failure mode
    `pricing-catalog-no-drift.test.ts` exists to catch on the sibling `/pricing` page; this task must not
    reintroduce it on the homepage.
  - R2 Any figure not present in `PRICING_CATALOG`'s `free`/`team` entries — e.g. an invented "$0" instead
    of the catalog's own null→"Free" convention, or the dark $1 `starter`/$20 `pro` tiers surfaced here
    without also making them reachable on `/pricing` -> MUST NOT ship — see §0 R-b; resolved for now as
    Free + Team ($99), flagged below (⚠) for explicit human confirmation.
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
  - A visitor scrolling the homepage sees a real number — Free to start, $99/mo for the Team tier —
    before ever clicking through.
  - Clicking "View pricing plans" lands on `/pricing`, where the SAME two numbers (Free, $99) are already
    rendered — no homepage/detail mismatch, ever, because both derive from the one `PRICING_CATALOG`
    module.
  - The section's id, heading, and existing link are unchanged — `landing-page.test.tsx` and
    `landing-fidelity.test.tsx` stay green without modification.
  - Both light and dark themes render the anchor legibly with zero new tokens.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Anchoring on Free + $99 (Team), not Free + $1 (the catalog's true lowest-priced row, `starter`) —
    lowest confidence in the whole task, because it is a genuine repo-surface disagreement, not a judgment
    call to make silently (per the dispatching brief's explicit instruction). The catalog's raw floor is
    $1, but $1 is dark to every customer-facing surface today — not on `/pricing`, not on any dashboard
    checkout page (confirmed by search, §0 R-b). $99 is the number `/pricing` already shows, verifiable by
    a visitor immediately on click-through; $1 would be a real DB number nobody can find — the same
    failure this task exists to fix, just moved to a different price point. If wrong (the human wants $1
    surfaced instead): this task would need to ALSO add a $1 card to `/pricing` first — a scope change
    that re-enters this Specify step, not a homepage-only fix.
  - [ ] Exact copy wording ("Free to start · Team plans from $99/mo" vs. an alternative phrasing) —
    confirm with the human at freeze; low cost if wrong (a copy-only follow-up, not a re-plumb).
  - [x] `PRICING_CATALOG` is safe to import into `page.tsx` unchanged — confirmed: it is already imported
    the same way by the sibling `/pricing` page; zero runtime cost (a static object), no fetch.
  - [x] The frozen `#pricing` section anchors (id/h2/button) are additive-safe — confirmed by reading both
    `landing-page.test.tsx` and `design-system/landing-fidelity.test.tsx` in full: neither asserts the
    section's exact child count or forbids a new sibling element, only id/h2/button presence.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Homepage renders the catalog-sourced Free + Team figures   # M1
  Given the homepage #pricing section
  When it renders
  Then it shows formatBasePrice(getPricingCatalogEntry("free").basePriceUsd, "Free") -> "Free"
  And it shows formatBasePrice(getPricingCatalogEntry("team").basePriceUsd, "Free") -> "$99"
  And both figures equal exactly what /pricing renders for its Starter (free-bound) and Team cards

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
  Given a hypothetical implementation that types "$99" directly into page.tsx
  When a no-drift check runs (mirroring pricing-catalog-no-drift.test.ts's own pattern)
  Then it fails, because the rendered text must equal formatBasePrice(getPricingCatalogEntry("team").basePriceUsd, ...)
  And the catalog import stays the single source of truth

Scenario: An unreachable or invented figure is rejected   # R2
  Given the catalog's starter ($1) and pro ($20) entries are not rendered on /pricing
  When the homepage anchor is authored
  Then it does NOT surface $1 or $20 without /pricing also being updated to show them
  And it uses only the two entries (free, team) that /pricing already renders

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
  When either renders its Free/Team figures
  Then both derive from the same PRICING_CATALOG entries (free, team)
  And a future price change to the migration + catalog updates both pages identically, no page left stale

Scenario: No async data, so no loading/error/partial-failure state applies (deliberately ruled out)
  Given the price anchor never calls a network endpoint
  Then there is no loading skeleton, no error state, and no partial-failure case to test
  And this is a deliberate ruling-out, not an omission — the catalog is a static import evaluated at
    build/render time
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is a DASHBOARD (presentation-only) task — the "shape" frozen here is OBSERVABLE homepage markup +
its test anchor, NOT a new HTTP endpoint. It consumes the FROZEN `pricing-catalog.ts` module unchanged.

```
COMPONENT  MarketingRootPage#pricing   (apps/dashboard/app/(marketing)/page.tsx)

PRICE ANCHOR (presentation only; no new endpoint, no fetch) — reads the FROZEN:
  apps/dashboard/lib/pricing-catalog.ts: PRICING_CATALOG / getPricingCatalogEntry / formatBasePrice
    getPricingCatalogEntry("free").basePriceUsd = null  -> formatBasePrice(..., "Free") -> "Free"
    getPricingCatalogEntry("team").basePriceUsd = 99.0  -> formatBasePrice(..., "Free") -> "$99"
  (the "Free" nullLabel arg on the team call mirrors /pricing's own existing call convention for its
  Team card — team is never null, so the arg is inert; kept for byte-parity with that call site.)

RENDER SHAPE (additive within the existing #pricing section — id/h2/button UNCHANGED):
  <section id="pricing">                                          (existing, unchanged)
    <h2>Simple, transparent pricing</h2>                          (existing, unchanged)
    <p>From solo teams to enterprise deployments. …</p>           (existing, unchanged)
    <p data-slot="price-anchor">                                  (NEW — this task)
      Free to start · Team plans from $99/mo
    </p>
    <Button href="/pricing">View pricing plans</Button>           (existing, unchanged)
  </section>

Copy (draft — exact wording confirmed at freeze, §1 open assumption):
  "Free to start · Team plans from $99/mo"
  — both price substrings are the LIVE output of formatBasePrice(...) above, never literals; if the copy
    template changes, the two substrings must remain pure function output of that catalog call.

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
  - /pricing page's own Starter/Team/Enterprise cards
                                                  — apps/dashboard/app/(marketing)/pricing/page.tsx (NOT edited)
  - the gateway plans catalog / migration        — apps/gateway (NOT touched by this task at all)
```

Glossary deltas: none new — "Free" and "Team" are existing `PRICING_CATALOG` tier names
(`plan-tiers-and-base-fee` TASK.md's own Glossary); this task introduces no new domain term.
Status: DRAFT
Reported: no — the freeze report has not yet been rendered; this draft carries one lowest-confidence flag
(§1 ⚠ — Free+$99 vs. the catalog's true $1 floor) for the human to resolve at the freeze decision. Status
moves to FROZEN @ v1 only by that human approval — never by this agent.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

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
