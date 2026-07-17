# TASK: EU AI Act readiness marketing page + docs (accurate Art. 101 figures; live before 2026-08-02)

slug: ai-act-marketing-page · created: 2026-07-14 · stage: production
milestone: eu-ai-act-readiness
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/app/(marketing)/pricing/page.tsx:PricingPage` — sibling Server Component; its
  existing "Data residency & priority routing" `Card` (lines ~164-183, landed via
  `residency-tiers-ui` M11) is the voice source this page extends and the page this page cross-links
  from (add one sentence + link, do not touch its frozen §3 v1 tier contract).
- `apps/dashboard/app/(marketing)/docs/page.tsx:DocsPage` / `CATEGORIES` — frozen §3 v1 scaffold;
  its own contract note says full doc content is a deferred SPEC delta and every category links to
  `#coming-soon`. This task adds exactly ONE real, working category entry (not another stub).
- `apps/dashboard/app/(marketing)/layout.tsx:MarketingLayout` — wraps every public route in
  `MarketingShell`; new routes inherit this automatically, no change needed here.
- `apps/dashboard/components/marketing-shell.tsx:MarketingShell` (frozen §3 v1) — `NAV_LINKS` /
  `FOOTER_COLUMNS` const arrays gate every discoverable marketing link; a new page needs exactly one
  new entry in one of these (see §1 open assumption — footer vs. nav).
- `apps/dashboard/app/(marketing)/page.tsx:MarketingRootPage` — precedent for section anatomy
  (`aria-labelledby` section + `h2` + `Card`/`Badge` reuse, `Reveal` for the hero only) this task's
  new page follows.
- `apps/dashboard/lib/seo.ts:buildMetadata` — the shared per-page SEO helper every marketing page's
  `metadata` export calls; both new pages must call it.
- `apps/dashboard/tests/marketing-seo.test.tsx:PAGES` — enumerates every marketing page's metadata
  for the sitewide "unique title+description+OG" invariant; both new pages' `metadata` exports must
  be added here to keep `test_every_marketing_page_has_unique_seo` true.
- `apps/dashboard/tests/pricing-page.test.tsx` — the render+role-query+axe+source-guard test shape
  to follow for the new page's own test file.
- `apps/gateway/src/gateway/core/error_catalog.py:RESIDENCY_NO_ELIGIBLE_REGION` (~line 1002) and
  `apps/gateway/src/gateway/proxy/application/residency.py` (raises it, 403, before any upstream
  dial) — the REAL fail-closed mechanism behind the "refuse, never silently rerouted" copy claim;
  the marketing copy must stay truthful to this, never overstate it.
- **CORRECTED (was wrong in the first draft)**: `apps/dashboard/components/settings/
  RetentionZdrSettings.tsx` — the "Data & residency" tab content (mounted by
  `apps/dashboard/components/settings/SettingsPage.tsx:SettingsPage`'s `<TabsTrigger value="retention">
  Data & residency</TabsTrigger>` / `<TabsContent value="retention">`, route
  `apps/dashboard/app/(app)/app/settings/page.tsx` → `/app/settings`) — an EXISTING, shipped,
  authed console surface (M2 `residency-service-tiers`, merged to `main` via PR #69, well before
  this branch was cut). It renders `RESIDENCY_CONSEQUENCE`'s verbatim refuse-not-reroute copy
  ("Pinning to EU/US/AP means requests that cannot run in \[region\] will be refused, not rerouted…",
  ~lines 81-85) against `GET/PUT /admin/residency-policy`. This — NOT the not-yet-built
  `compliance-report-center` — is the truthful, existing console surface this marketing page may
  reference. Caveat: `SettingsPage`'s `<Tabs defaultValue="cache">` is hardcoded and does not read a
  URL/query param, so `/app/settings` alone lands a visitor on the Cache tab, not directly on
  "Data & residency" — a plain link cannot deep-link to the tab today (feeds the M8/R4 rework below).
- Related existing surfaces confirmed shipped alongside it (same PR #69): `apps/dashboard/components/
  ui/region-badge.tsx`, `apps/dashboard/components/keys/TierSelector.tsx`,
  `apps/dashboard/components/keys/CreateKeyDialog.tsx` — not directly touched by this task, named
  here only to correct the earlier false "unmerged branch" grounding claim.

Context (working folder):
- `FEATURES.md` §"Data residency & service tiers" (lines 138-158) — region-as-first-class-dimension,
  fail-closed residency policy, `RESIDENCY_NO_ELIGIBLE_REGION` behavior.
- `FEATURES.md` §"Enterprise governance & security" — ZDR description (lines 238-242: opt-in,
  confirm-gated, irreversible, metadata-only usage records) and Audit log & compliance export
  description (lines 234-237: append-only, cursor-paginated export, export access itself audited).
- `.add/milestones/eu-ai-act-readiness/MILESTONE.md` — owning milestone: scope, shared decisions
  (accuracy floor, "evidence not compliance" copy rule), pending Glossary deltas (Art. 12 bundle,
  readiness pack), hard deadline 2026-08-02.
- `docs/roadmap/2026-07-14-enterprise-roadmap.html` (~lines 130-136, 177-182) — Tin-approved copy
  source for the Anthropic region-pinning figures and the exact Fable-5 suspension framing.
- `/Users/tindang/workspaces/tind-repo/ai-proxy/tmp/r1-design-context.md` — the verified,
  cite-precisely fact sheet for this wave; supersedes any conflicting figure found elsewhere.
- Sibling tasks: `art12-record-keeping-preset` (still `phase: ground`, contract NOT yet frozen — owns
  the Art. 12 bundle's actual manifest shape, describe in OUTCOME terms only) and
  `compliance-report-center` (still `phase: ground` — owns the FUTURE generate/download/schedule
  console surface that will EXTEND the existing `/app/settings` "Data & residency" tab; that
  extension does not exist yet, distinct from the tab itself which already ships today).

Honors (patterns / conventions):
- Every marketing page is a Server Component: no `"use client"`, no cookie read, no authenticated
  fetch (frozen invariant on every sibling page in `(marketing)/`).
- `buildMetadata` for SEO; `Card`/`CardHeader`/`CardTitle`/`CardDescription`/`CardContent`, `Badge`,
  `Button` reused from `components/ui/*` — no new visual pattern, no new design token (Aurora tokens
  only, per this task's own persona brief).
- Heading discipline: exactly one `h1`, `h2` per section with matching `aria-labelledby`, no level
  skips — checked by every sibling page's a11y test.
- WCAG 2.2 AA, axe-checked (`@/test-support/axe`), 0 serious/critical violations — the default
  requirement on every screen, not an opt-in pass.

Seams consulted: none — no `.add/SEAMS.md` entry matches a marketing-page shape.

Anchors the contract cites:
- `apps/dashboard/app/(marketing)/ai-act-readiness/page.tsx` (NEW)
- `apps/dashboard/app/(marketing)/docs/ai-act-compliance/page.tsx` (NEW)
- `apps/dashboard/app/(marketing)/docs/page.tsx:CATEGORIES` (amended, +1 entry)
- `apps/dashboard/app/(marketing)/pricing/page.tsx` (amended, +1 cross-link sentence)
- `apps/dashboard/components/marketing-shell.tsx:NAV_LINKS|FOOTER_COLUMNS` (amended, +1 entry)
- `apps/dashboard/lib/seo.ts:buildMetadata` (reused, not amended)
- `apps/dashboard/tests/marketing-seo.test.tsx:PAGES` (amended, +2 entries)
- `apps/dashboard/components/settings/RetentionZdrSettings.tsx` / `apps/dashboard/app/(app)/app/
  settings/page.tsx` → `/app/settings` (CITED, not modified — the existing console route + tab
  label this task's copy is now permitted to name/link to, per the correction below)

Issues/Risks (→ feed §1):
- **CORRECTED**: the earlier claim that "the Settings → Data & residency console panel does not
  exist on `main`" was WRONG — a `grep` over `app/` route files only missed that the real surface
  lives in `components/settings/RetentionZdrSettings.tsx`, shipped on `main` via M2
  `residency-service-tiers` (PR #69), well before this branch (`feat/agent-gateway-r1`) was cut.
  The tab is real, live, and truthfully referenceable. What is STILL not shipped is the
  `compliance-report-center` extension of it (generate/download/schedule the Art. 12 bundle from
  that same tab) — the prohibition narrows to THAT, not the tab itself → feeds the reworked R4/M8.
- Because `SettingsPage`'s `Tabs` has a hardcoded `defaultValue="cache"` with no query-param read, a
  bare `/app/settings` link cannot deep-link straight to the "Data & residency" tab today — any
  reference to it must be honest about landing on Settings generally, naming the tab by its exact
  visible label ("Data & residency") in copy rather than promising a one-click deep link.
- `art12-record-keeping-preset` (the Art. 12 bundle's real shape) is still `phase: ground`, contract
  not frozen — this page must never assert a specific manifest/field shape → feeds R6/M6.
- `docs/page.tsx`'s own frozen §3 v1 contract note explicitly defers ALL real content; adding one
  real page here is a deliberate, disclosed, scoped exception (its contract note and the other 4
  "coming soon" categories are untouched) → feeds M9/R8.
- A statically-render-friendly page cannot show a live "days until Aug 2" countdown without either a
  client component (breaks the Server-Component-only sibling pattern) or per-request dynamic
  rendering (breaks the cache-friendly requirement) → feeds M13/R7 and the signature-element choice.
- No "readiness pack" SKU/line-item exists in `pricing/page.tsx`'s `TIERS` — copy must not imply a
  priced product that isn't in the frozen pricing contract.

Related intent:
- `.add/milestones/eu-ai-act-readiness/MILESTONE.md` goal: an EU tenant can self-serve a dated,
  Art. 12-mapped evidence bundle before GPAI enforcement (2026-08-02); this task is the
  marketing/trust-signal leg of that milestone, parallel to the two console/API tasks.
- GLOSSARY deltas pending confirmation this task proposes formalizing: **Art. 12 bundle**,
  **readiness pack** (both already named in MILESTONE.md as shared decisions).
- Originates from the Tin-approved 2026-07-14 enterprise roadmap R1 (`docs/roadmap/...html`).

Ground SHA: c948576

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: EU AI Act readiness marketing page (`/ai-act-readiness`) + one real docs page
(`/docs/ai-act-compliance`) — legally accurate Art. 101 figures, the residency/ZDR/audit story, and
the Fable-5 vendor-risk narrative, live before the 2026-08-02 hard deadline.

Framings weighed:
(chosen) One new dedicated page at `/ai-act-readiness` — IA: hero → Art. 101 fact-anchor strip →
residency refuse-not-reroute → ZDR → audit/Art. 12 bundle → vendor-risk/failover → CTA — plus one
new real docs page at `/docs/ai-act-compliance`, cross-linked from `pricing`, `docs`, and
`MarketingShell`.
· (rejected) fold the AI Act story into the existing `/pricing` page as another card — `pricing`'s
  frozen §3 v1 contract scopes it to tiers + a short residency/priority teaser; a full regulatory
  narrative (5 sections + legal citations) doesn't fit that IA and would bloat an already-frozen
  contract instead of getting its own addressable, linkable page.
· (rejected) publish as a `/blog` post instead of a static evergreen page — blog is dated/
  chronological and gets buried/stale; a compliance-deadline page needs to be a durable,
  directly-linkable, nav/footer-discoverable page, not something scrolled past in a feed.

Must:
<must>
  - M1: The page renders at a stable public route `/ai-act-readiness`, Server Component, no cookie
    check, no authenticated fetch — same PUBLIC contract as every sibling marketing page.
  - M2: Every legal figure on the page is Art. 101's "3% global turnover or €15M, whichever higher"
    with enforcement date 2026-08-02, and every such figure is visually paired with its article
    citation ("Art. 101") in the SAME visible text node — never a bare number.
  - M3: The page never states or implies "AI Act compliant" / "makes you compliant" / "GPAI
    compliance" anywhere in rendered copy — every compliance-adjacent claim uses "record-keeping
    support" / "audit-readiness support" phrasing instead.
  - M4: The residency section states the fail-closed refuse-not-reroute behavior (a request with no
    eligible in-region candidate is refused, never silently rerouted), matching the shipped
    `ERR_RESIDENCY_NO_ELIGIBLE_REGION` behavior, in the same plain-language register as the existing
    pricing-page residency copy.
  - M4a **[reworked — was wrong in the first draft]**: the residency section MAY carry one real,
    honest inline link to `/app/settings` (the shipped, authed "Data & residency" tab —
    `RetentionZdrSettings.tsx`, merged via PR #69, live on `main` today), phrased as "configure your
    residency pin in Settings → Data & residency" — DESIGNER'S CALL: a real `<Link>`, not plain
    unlinked text, because the route genuinely exists and "sign in to configure this" is the same
    established pattern as `MarketingShell`'s existing `/login`/`/signup` links. The copy must name
    the exact visible tab label ("Data & residency") rather than promise a one-click deep link,
    because `SettingsPage`'s `Tabs` has a hardcoded `defaultValue="cache"` and does not read a
    query param — the link lands a signed-in visitor on Settings generally, not pre-selected on that
    tab, and the copy must not claim otherwise.
  - M5: The ZDR section states it as a tenant-facing, OPT-IN, confirm-gated, irreversible
    data-minimization control (matching `FEATURES.md`'s ZDR description) — never oversold as
    "we never see your data" absent the opt-in.
  - M6: The audit/Art. 12 section names the record-keeping bundle in OUTCOME terms only (a dated,
    Art. 12-mapped evidence export) — no specific field/section/manifest shape asserted (that shape
    belongs to the not-yet-frozen `art12-record-keeping-preset` contract).
  - M7: The vendor-risk section names the Claude Fable 5 export-control suspension (Jun 12–30, 2026)
    as the concrete precedent for the multi-provider-failover pitch, and states Anthropic's
    `inference_geo` accepts only `us`\|`global` (no first-party EU) with the 1.1× US-pin / +10%
    hyperscaler-regional figures — every figure sourced from `tmp/r1-design-context.md`, never
    invented or approximated.
  - M8 **[reworded]**: The page ends with exactly one primary CTA ("Get started" → `/signup`) plus
    one secondary CTA into `/docs/ai-act-compliance`. Distinct from those two CTAs, the page may ALSO
    carry the one M4a inline body-copy link to `/app/settings` — that is a contextual reference, not
    a third CTA. No CTA or inline link targets a console route that is actually absent from `main` at
    ground time (narrowed from the earlier draft: `/app/settings` is confirmed present; only
    `compliance-report-center`'s not-yet-built extension of it remains off-limits).
  - M9: `/docs/ai-act-compliance` exists with REAL content (not a "Coming soon →" stub) describing
    the Art. 12 bundle's purpose/scope in outcome terms, linked from the new marketing page AND from
    one new working entry in `docs/page.tsx`'s `CATEGORIES` array.
  - M10: Both new pages call the shared `buildMetadata` helper for unique title/description/canonical
    metadata, and both are added to `marketing-seo.test.tsx`'s `PAGES` enumeration so
    `test_every_marketing_page_has_unique_seo` keeps passing.
  - M11: `MarketingShell` gets exactly one new, disclosed link to `/ai-act-readiness` (nav OR footer
    — see the ⚠ open assumption below) — Aurora tokens only, no new visual pattern introduced.
  - M12: Both new pages pass axe with 0 serious/critical violations and preserve heading discipline
    (one h1 per page, monotonic h2s, `aria-labelledby` per section) — the same bar as every sibling
    marketing page.
  - M13: Both pages are statically-render-friendly (buildable with zero required client-side JS); the
    signature element is a static fact-anchor (a 3-tile stat strip — 3% / €15M / Aug 2, 2026 — each
    tile citing "Art. 101", tabular-nums styling matching the financial-document idiom named in
    MILESTONE.md) — explicitly NOT a live/ticking countdown.
</must>
Reject:
<reject>
  - R1: copy stating the Art. 99 general-infringement figure (€35M / 7%) anywhere on either page ->
    "ART99_FIGURE_LEAKED"
  - R2: copy asserting "AI Act compliant" / "makes you compliant" / "GPAI compliance" (claiming the
    provider-side compliance obligation itself) -> "COMPLIANCE_CLAIM_OVERREACH"
  - R3: a legal figure (percentage, euro amount, or enforcement date) rendered without its article
    citation in the same visible text node -> "UNCITED_LEGAL_FIGURE"
  - R4 **[narrowed — was wrong in the first draft]**: a CTA/link pointing at the not-yet-shipped
    `compliance-report-center` console extension (generate/download/schedule the Art. 12 bundle), OR
    any copy claiming `/app/settings` deep-links straight to the "Data & residency" tab (it does not
    — `Tabs defaultValue="cache"` is hardcoded) -> "DANGLING_CONSOLE_LINK". A plain link to
    `/app/settings` itself (per M4a) is explicitly PERMITTED and does NOT trigger this code — that
    route is shipped on `main` (PR #69, M2 `residency-service-tiers`).
  - R5: a claim that Hydroa or Anthropic offers first-party EU inference, or an Anthropic
    region-pricing claim omitting the 1.1×/+10% detail -> "INACCURATE_VENDOR_CLAIM"
  - R6: the Art. 12 bundle described with a specific field/section/manifest shape, pre-empting the
    sibling task's not-yet-frozen contract -> "BUNDLE_SHAPE_PREEMPTED"
  - R7: a live/client-rendered countdown timer to the enforcement date -> "CLIENT_COUNTDOWN_INTRODUCED"
  - R8: `/docs/ai-act-compliance` rendering as another "Coming soon →" stub instead of real content ->
    "DOCS_STUB_NOT_REAL_CONTENT"
</reject>
After:
<after>
  - `/ai-act-readiness` is live and linkable before 2026-08-02, discoverable from `MarketingShell`,
    `pricing`, and `docs`.
  - Every legal figure on the page traces 1:1 to `tmp/r1-design-context.md`'s verified facts, each
    with its article citation visible in the same text node.
  - `/docs/ai-act-compliance` exists with real content and is reachable from `docs/page.tsx`'s
    `CATEGORIES`.
  - `marketing-seo.test.tsx`'s `PAGES` enumeration includes both new pages; the sitewide unique-SEO
    invariant still holds.
  - Both new pages report 0 serious/critical axe violations.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Route name `/ai-act-readiness` (vs. `/eu-ai-act`, `/trust/ai-act`, `/solutions/ai-act`) — lowest
  confidence because no naming precedent exists in this flat, product-shaped marketing IA
  (`/pricing`, `/docs`, `/blog`, `/legal/*`) for a "trust/regulatory" page; if wrong: a route rename
  after go-live costs a broken external link / SEO-canonical reset — cheap to fix before launch,
  materially costlier after 2026-08-02 once third parties may have linked in.
  - [ ] Whether `/ai-act-readiness` needs its OWN top-level nav entry (a 4th link, crowding the
    existing Product/Pricing/Docs triad) vs. a footer-only link — default taken: footer-only (new
    entry under an existing or new footer column) + a prominent inline cross-link from `pricing`'s
    residency Card, to avoid unilaterally widening `MarketingShell`'s frozen nav shape; confirm at
    freeze.
  - [ ] Whether `/docs/ai-act-compliance` should be real static JSX (this task's assumption) or wait
    for a fuller MDX docs pipeline — confirmed as JSX-only for now: no MDX pipeline exists yet per
    `docs/page.tsx`'s own deferred-SPEC-delta note; flag forward if an MDX pipeline lands first.
  - [ ] Whether the Art. 12 bundle cross-link should point only at this task's own
    `/docs/ai-act-compliance` (chosen, in-scope, safe) vs. forward-referencing the
    not-yet-frozen `compliance-report-center` console route (rejected per R4/M8) — resolved: docs
    page + `/signup` only.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Public route renders with no auth surface   # M1
  Given an anonymous visitor with no session cookie
  When they request /ai-act-readiness
  Then the page renders 200 with a single <main id="main"> and exactly one h1
  And no cookie is read and no authenticated fetch occurs (source-introspection guard, matching
      pricing-page.test.tsx's public-route guard)

Scenario: Art. 101 figures always carry their citation   # M2
  Given the rendered /ai-act-readiness page
  When the stat-strip and body copy are inspected
  Then "3%", "€15M", and "2026-08-02"-equivalent copy each appear in the same visible text node as
      "Art. 101"
  And the Art. 99 figures (€35M / 7%) never appear anywhere on the page

Scenario: No compliance-claim overreach   # M3
  Given the rendered /ai-act-readiness page
  When the full text content is scanned
  Then no instance of "AI Act compliant", "makes you compliant", or "GPAI compliance" is found
  And every compliance-adjacent sentence uses "record-keeping support" or "audit-readiness support"

Scenario: Residency story states refuse-not-reroute   # M4
  Given the rendered /ai-act-readiness page's residency section
  When its copy is read
  Then it states a request with no eligible in-region candidate is REFUSED, never silently rerouted
  And the phrasing register matches the existing pricing-page residency Card (fail-closed, plain
      consequence language)

Scenario: Residency section may honestly link to the existing settings tab   # M4a (corrected)
  Given the rendered /ai-act-readiness page's residency section
  When its inline link (if present) is inspected
  Then it targets /app/settings (the shipped RetentionZdrSettings.tsx "Data & residency" tab,
      merged via PR #69) and its copy names the tab by its exact visible label
  And its copy does NOT claim the link lands pre-selected on that tab (SettingsPage's Tabs
      defaultValue is hardcoded to "cache" and reads no query param)

Scenario: ZDR story states opt-in and irreversible   # M5
  Given the rendered /ai-act-readiness page's ZDR section
  When its copy is read
  Then it states ZDR is tenant opt-in, confirm-gated, and irreversible once enabled
  And it never claims data is unseen by default absent that opt-in

Scenario: Art. 12 bundle described in outcome terms only   # M6
  Given the rendered /ai-act-readiness page's audit/Art. 12 section
  When its copy is read
  Then it describes a "dated, Art. 12-mapped record-keeping export" as an outcome
  And it names no specific field, section, or manifest shape

Scenario: Vendor-risk section cites Fable-5 suspension and accurate region figures   # M7
  Given the rendered /ai-act-readiness page's vendor-risk section
  When its copy is read
  Then it names the Claude Fable 5 export-control suspension window (Jun 12–30, 2026)
  And it states inference_geo accepts only us|global with the 1.1x US-pin and +10% hyperscaler
      regional figures, matching tmp/r1-design-context.md verbatim

Scenario: Exactly one primary CTA, one secondary CTA, at most one settings reference   # M8 (reworded)
  Given the rendered /ai-act-readiness page
  When all links/buttons are enumerated
  Then exactly one primary CTA links to /signup
  And exactly one secondary CTA targets /docs/ai-act-compliance
  And any additional link to /app/settings (the M4a residency reference) is not counted as a third
      CTA and is permitted
  And no link targets the not-yet-shipped compliance-report-center console extension

Scenario: Docs page carries real content, not a stub   # M9
  Given a visitor requests /docs/ai-act-compliance
  Then the page renders 200 with real prose describing the Art. 12 bundle's purpose and scope
  And docs/page.tsx's CATEGORIES array contains one entry linking to this page (not "#coming-soon")

Scenario: Both new pages carry unique, registered SEO metadata   # M10
  Given marketing-seo.test.tsx's PAGES enumeration
  When it is extended with the two new pages' metadata exports
  Then test_every_marketing_page_has_unique_seo passes with all titles pairwise unique
  And both new pages' og:title/og:description are non-empty

Scenario: MarketingShell exposes one disclosed link to the new page   # M11
  Given the rendered MarketingShell nav/footer
  When NAV_LINKS or FOOTER_COLUMNS is inspected
  Then exactly one new entry points at /ai-act-readiness
  And no other visual pattern in MarketingShell changed

Scenario: Both new pages are accessible   # M12
  Given the rendered /ai-act-readiness and /docs/ai-act-compliance pages
  When axe is run against each
  Then 0 serious/critical violations are reported for either page
  And each page has exactly one h1 with no heading-level skip

Scenario: Signature stat-strip is static, not a countdown   # M13
  Given the rendered /ai-act-readiness page's stat-strip
  When the page's source is inspected
  Then no "use client" directive and no client-side Date computation exists in the new page files
  And the 2026-08-02 date is rendered as a fixed string, not a computed days-remaining value

Scenario: Reject — Art. 99 figure leak   # R1
  Given a draft of /ai-act-readiness containing "€35M" or "7% of global turnover"
  When the copy-accuracy check runs
  Then it fails with "ART99_FIGURE_LEAKED"
  And the page is not considered ready to ship

Scenario: Reject — compliance-claim overreach   # R2
  Given a draft containing the phrase "AI Act compliant" or "makes you compliant"
  When the copy-accuracy check runs
  Then it fails with "COMPLIANCE_CLAIM_OVERREACH"
  And the surrounding record-keeping-support framing is left otherwise unchanged

Scenario: Reject — uncited legal figure   # R3
  Given a draft where "3%" or "€15M" appears without "Art. 101" in the same text node
  When the copy-accuracy check runs
  Then it fails with "UNCITED_LEGAL_FIGURE"
  And no other passing figure pairing on the page is altered

Scenario: Reject — dangling console link (narrowed to the still-unshipped surface)   # R4 (corrected)
  Given a draft CTA linking to the not-yet-shipped compliance-report-center console extension, OR a
      draft claiming /app/settings deep-links straight onto the "Data & residency" tab
  When the link-target check runs against routes that exist on `main`
  Then it fails with "DANGLING_CONSOLE_LINK"
  And the existing valid /signup, /docs/ai-act-compliance, and plain /app/settings links remain
      unchanged — a bare, honestly-worded /app/settings link is NOT itself a violation

Scenario: Reject — inaccurate vendor claim   # R5
  Given a draft stating Anthropic offers first-party EU inference, or omitting the 1.1x/+10% detail
  When the copy-accuracy check runs
  Then it fails with "INACCURATE_VENDOR_CLAIM"
  And the Fable-5 suspension sentence elsewhere on the page is left unchanged

Scenario: Reject — bundle shape pre-empted   # R6
  Given a draft naming a specific Art. 12 bundle field/section/manifest name
  When the copy-accuracy check runs
  Then it fails with "BUNDLE_SHAPE_PREEMPTED"
  And the outcome-only framing elsewhere in that section is left unchanged

Scenario: Reject — client countdown introduced   # R7
  Given a draft page file containing "use client" or a runtime Date-diff computation for the
      enforcement date
  When the static-render check runs
  Then it fails with "CLIENT_COUNTDOWN_INTRODUCED"
  And the rest of the page's Server Component contract is left unchanged

Scenario: Reject — docs stub not real content   # R8
  Given /docs/ai-act-compliance rendering only a "Coming soon →" link with no body prose
  When the content-completeness check runs
  Then it fails with "DOCS_STUB_NOT_REAL_CONTENT"
  And the other 4 existing "coming soon" docs categories are left unchanged (they are intentionally
      still stubs)

Scenario: Edge case — "whichever higher" wording is unambiguous   # boundary
  Given the Art. 101 stat-strip tile pairing 3% and €15M
  When its copy is read in isolation (no surrounding sentence)
  Then it explicitly states "whichever is higher" (or equivalent unambiguous wording), never leaving
      the reader to infer which figure governs

Scenario: Edge case — SEO title collision guard   # duplicate
  Given the full marketing-seo.test.tsx PAGES set including the two new pages
  When titles are compared pairwise
  Then no two pages (existing or new) share an identical title string

Scenario: Ruled out — concurrency / partial failure
  Given both new pages are pure static Server Components with zero server-side data fetch or mutation
  Then there is no concurrent-write hazard and no partial-failure mode to test for this task — the
      only "failure" surface is a wrong/uncited copy string, covered by R1-R8 above; this is a
      deliberate ruling-out, not an omission
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /ai-act-readiness              body: none (public, static Server Component — no query params)
  200 -> rendered HTML:
    hero (h1 + subhead, Badge, no Reveal-required motion)
    + Art.101 fact-anchor stat-strip (3 tiles: "3%", "€15M", "Aug 2, 2026" — each tile's visible
      text includes "Art. 101"; tabular-nums styling; static, NOT a live countdown)
    + residency section (h2; fail-closed refuse-not-reroute copy, extends pricing-page voice; MAY
      carry one inline link to /app/settings naming the shipped "Data & residency" tab by its exact
      label — corrected per coordinator: RetentionZdrSettings.tsx is live on `main` via PR #69, not
      unshipped as the first draft wrongly claimed; copy must not promise a tab-preselecting deep
      link since SettingsPage's Tabs defaultValue is hardcoded)
    + ZDR section (h2; opt-in / confirm-gated / irreversible framing)
    + audit / Art. 12 bundle section (h2; outcome-only description, no manifest shape)
    + vendor-risk / failover section (h2; Fable-5 suspension Jun 12-30 2026; inference_geo us|global;
      1.1x US-pin; +10% hyperscaler regional)
    + CTA (primary "Get started" -> /signup; secondary -> /docs/ai-act-compliance)
  (no 4xx path — public static page, no request body/params to reject; the R1-R8 "rejections" in
   §1/§2 are CONTENT-ACCURACY assertions the test suite enforces against the rendered/source copy,
   not HTTP error responses)

GET /docs/ai-act-compliance        body: none (public, static Server Component)
  200 -> rendered HTML: real prose describing the Art. 12 bundle's purpose/scope in outcome terms +
    a link back to /ai-act-readiness
  (no 4xx path, same reasoning as above)

Content-assertion codes (test-suite-level, enforced by a copy/source scan over the rendered page and
  raw page-source text — NOT HTTP status codes, since there is no request body to reject):
  ART99_FIGURE_LEAKED · COMPLIANCE_CLAIM_OVERREACH · UNCITED_LEGAL_FIGURE · DANGLING_CONSOLE_LINK ·
  INACCURATE_VENDOR_CLAIM · BUNDLE_SHAPE_PREEMPTED · CLIENT_COUNTDOWN_INTRODUCED ·
  DOCS_STUB_NOT_REAL_CONTENT

Schema: no database table touched — pure presentational Next.js pages under
  `apps/dashboard/app/(marketing)/`. Data-shape touches (not tables):
  - `docs/page.tsx`'s `CATEGORIES` const array: +1 entry (real link, not "#coming-soon")
  - `marketing-shell.tsx`'s `NAV_LINKS` or `FOOTER_COLUMNS` const array: +1 entry (see §1 ⚠)
  - `pricing/page.tsx`'s existing residency `Card`: +1 cross-link sentence (no TIERS change)
  - `marketing-seo.test.tsx`'s `PAGES` const array: +2 entries (new pages' metadata)
  - `RetentionZdrSettings.tsx` / `SettingsPage.tsx` / `/app/settings` route: CITED only, zero lines
    touched — this task only ADDS an inline `<Link href="/app/settings">` from the new marketing
    page; it does not read/write `residency-policy` or modify `SettingsPage`'s `Tabs` in any way.
```

Glossary deltas:
- **Art. 12 bundle**: the dated, Art. 12-mapped record-keeping evidence export (audit events + request
  logs metadata + usage lineage) this page and its docs page describe in OUTCOME terms only; the
  bundle's actual manifest/field shape is owned and frozen by the sibling `art12-record-keeping-preset`
  task, not this one.
- **readiness pack**: the residency + ZDR + audit/Art. 12-bundle commercial narrative this marketing
  page tells as one story; NOT a priced SKU/line-item as of this contract (no `pricing/page.tsx`
  `TIERS` entry exists for it). [folded foundation-version 53]

**Freeze decisions (Tin, 2026-07-14 — recorded at freeze, resolve the open questions above):**
- Route /ai-act-readiness CONFIRMED.
- Placement: footer link + pricing-page cross-link only — no MarketingShell nav widening.
- /docs/ai-act-compliance ships as plain JSX (no MDX pipeline exists).

Least-sure flag surfaced at freeze: [contract] Route name /ai-act-readiness has no naming precedent in this flat product-shaped marketing IA (a wrong choice costs a post-launch rename with broken external links after 2026-08-02); CONFIRMED by Tin at freeze, footer-only placement.

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — the freeze report (banner/ARC/SHAPE) renders when a human reviews this draft for the
freeze decision; it has not been rendered yet because this contract has not been approved.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

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
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-14

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

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

