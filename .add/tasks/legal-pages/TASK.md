# TASK: Public legal pages (terms/privacy/security)

slug: legal-pages · created: 2026-06-24 · stage: production
autonomy: auto
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - NEW `apps/dashboard/app/(marketing)/legal/{terms,privacy,security}/page.tsx` — the 3 routes the FROZEN MarketingShell footer "Legal" column links (`/legal/terms` `/legal/privacy` `/legal/security`). Without them those footer links dangle (404).
  - `apps/dashboard/components/marketing-shell.tsx:FOOTER_COLUMNS` — already links the 3 legal routes (NO change; this task makes them resolve).
  - Optional NEW `apps/dashboard/components/marketing/legal-page.tsx` — shared presentational wrapper (h1 + last-updated + prose sections) to avoid 3× duplication.
Context (working folder): Hydroa tokens; vitest+axe harness. No real legal counsel input — TEMPLATE copy clearly labelled.
Honors: v23/v24 a11y bar (one h1, ordered headings, landmarks via shell); public Server Component (no cookie/fetch).
Anchors the contract cites: the 3 `app/(marketing)/legal/*/page.tsx` routes · the shell FOOTER_COLUMNS legal links.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Public legal pages — Terms, Privacy, Security
Framings weighed: 3 routes + a shared LegalPage wrapper (chosen) · single combined /legal page (rejected, footer links 3 distinct routes) · external hosted policy (rejected, out of scope)
Must:
<must>
  - NEW pages at `/legal/terms`, `/legal/privacy`, `/legal/security`, each rendering inside `<main id="main">` with exactly ONE h1 (the policy title), a visible "Last updated" date, and >=2 prose sections (h2).
  - The 3 routes resolve (the shell footer "Legal" links no longer dangle).
  - Each page carries a clearly-labelled TEMPLATE/placeholder notice ("not legal advice; review by counsel before launch") — no fabricated binding legal claims.
  - Public Server Components: NO cookie read, NO authed fetch; reuse Hydroa tokens; WCAG-AA (one h1, ordered headings, landmarks via shell).
</must>
Reject:
<reject>
  - A footer legal link with no matching route (404) -> "dangling_legal_link"
  - More than one h1 / skipped heading level on any legal page -> "heading_order_violation"
  - Any cookie read or authed fetch -> "public_route_gated"
</reject>
After:
<after>
  - /legal/terms, /legal/privacy, /legal/security all return 200 with one h1 + template notice; footer links resolve.
  - axe clean; next build exit 0; whole vitest suite green; shell/proxy/auth untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Legal COPY is placeholder template text (no counsel review). Lowest confidence: wording/completeness of clauses.
    Decision (auto): ship clearly-labelled template scaffolds with the standard section skeleton; real text is a
    counsel task. If wrong: copy replacement, no structural rework. SPEC delta logged.
  - [x] 3 distinct routes (not one combined) — the shell footer links 3.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Each legal route renders with one h1 and a last-updated date
  Given an anonymous visitor at /legal/terms (and /legal/privacy, /legal/security)
  When the page renders
  Then it shows exactly one h1 (the title), a "Last updated" date, and >=2 h2 sections

Scenario: Footer legal links resolve
  Given the shell footer "Legal" column links /legal/terms /legal/privacy /legal/security
  When each route is requested
  Then a page exists for each (no dangling link)

Scenario: Template notice is present
  Given any legal page
  When it renders
  Then a clearly-labelled template/placeholder notice is visible

Scenario: Legal pages are accessible
  Given a legal page
  When the a11y suite runs
  Then axe reports 0 serious/critical and headings are monotonic

Scenario: Reject — heading order
  Given a legal page's markup
  When heading levels are checked
  Then exactly one h1 and no skipped level ("heading_order_violation")
  And the marketing shell + route split remain unchanged

Scenario: Reject — public stays public
  Given a legal page module
  When its source is checked
  Then it reads no cookies() and does no authed fetch ("public_route_gated")
  And the gateway/BFF/cookie contract remains unchanged
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PAGES (Server Components, PUBLIC, each inside <main id="main">):
  app/(marketing)/legal/terms/page.tsx     h1 "Terms of Service"
  app/(marketing)/legal/privacy/page.tsx   h1 "Privacy Policy"
  app/(marketing)/legal/security/page.tsx  h1 "Security"
  each: "Last updated <date>" · TEMPLATE notice · >=2 <section><h2> prose blocks
  shared wrapper: components/marketing/legal-page.tsx (title + lastUpdated + children)
  A11Y: one h1; h2 sections; axe 0 serious/critical; shell landmarks preserved.
  REJECTIONS: dangling_legal_link (footer link w/o route) · heading_order_violation · public_route_gated
Schema: NONE. No DB / gateway / BFF / cookie change. 3 new public pages + 1 presentational wrapper.
Least-sure flag surfaced at freeze: [spec] placeholder legal copy (no counsel) — template scaffolds clearly labelled;
  copy-only risk, structure firm. If wrong: replace text, no rework.
```

Status: FROZEN @ v1 — auto-frozen (autonomy: auto, fully-auto mode) 2026-06-24; low-risk presentational pages, no security/contract surface.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: all new tests green + no dashboard vitest/build regression.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_each_legal_renders: render each of terms/privacy/security -> one h1 + "Last updated" + >=2 h2
  - test_footer_links_resolve: every shell FOOTER_COLUMNS legal href has a matching page module on disk
  - test_template_notice: each legal page renders the template/placeholder notice
  - test_legal_a11y: axe 0 serious/critical on a legal page; monotonic headings
  - test_reject_heading_order: exactly one h1, no skipped level
  - test_reject_public_not_gated: each legal page source reads no cookies(), no authed fetch
</test_plan>

Tests live in: `apps/dashboard/tests/` · MUST run red before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/(marketing)/legal/` `apps/dashboard/components/marketing/` `apps/dashboard/tests/`
Strategy (ordered batches): 1. red tests `tests/legal-pages.test.tsx` 2. shared `components/marketing/legal-page.tsx` + the 3 route pages 3. green: vitest + tsc + lint + next build.
Safety rule (feature-specific): content-only public pages; never touch marketing-shell.tsx, proxy.ts, or auth/BFF code.
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
- [x] /legal/terms, /legal/privacy, /legal/security each render one h1 + "Last updated" + >=2 h2 + template notice — vitest 14/14, build shows 3 static routes
- [x] footer legal links resolve (no dangling) — test parses shell FOOTER_COLUMNS, asserts each /legal/* page exists
- [x] public: no cookies()/next-headers/authed fetch — source-guard test green (×3)
- [x] one h1, no heading skip; axe 0 serious/critical — heading + axe tests green

### Deep checks
- [x] WIRING — 3 page default exports + shared LegalPage/LegalSection wrapper, all resolved by next build (3 ○ routes)
- [x] DEAD-CODE — none (tsc/lint clean); wrapper used by all 3 pages
- [x] SEMANTIC — legal copy is clearly-labelled TEMPLATE (role=note notice "not legal advice"); SPEC delta logged for counsel review

### Evidence (independently run): vitest 466/466 (56 files, +14) · tsc 0 · next build exit 0 (○ /legal/{terms,privacy,security}). Shell/proxy/auth untouched.

### GATE RECORD
Outcome: PASS
Reviewed by: orchestrator independent evidence review (autonomy: auto, low-risk presentational pages, no security residue) · date: 2026-06-24

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch: 404s on /legal/* · axe regressions.

### Spec delta
- [SPEC · open] replace template legal copy with counsel-reviewed text before public launch (evidence: placeholder notice).

### Competency deltas
- [UDD · folded] marketing pages now repeat a section/prose pattern — LegalPage wrapper is the first shared extraction. [folded foundation-version 35]
