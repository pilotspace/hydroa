# TASK: Docs and blog index scaffold

slug: docs-blog-scaffold · created: 2026-06-24 · stage: production
autonomy: auto
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - NEW `apps/dashboard/app/(marketing)/docs/page.tsx` — public /docs INDEX scaffold (the landing #docs teaser + nav link target it). Structure (section cards/links), NOT full doc content.
  - NEW `apps/dashboard/app/(marketing)/blog/page.tsx` — public /blog INDEX scaffold (empty-state "no posts yet" + structure).
  - `apps/dashboard/components/marketing-shell.tsx` — footer "Company" column links /#blog (anchor) and landing links /docs (NO change).
  - `apps/dashboard/components/ui/card.tsx` — section cards.
Context: README capability list = doc category seeds; Hydroa tokens; vitest+axe.
Honors: v23/v24 a11y bar (one h1, ordered headings, landmarks via shell); public Server Component (no cookie/fetch).
Anchors the contract cites: `app/(marketing)/docs/page.tsx` · `app/(marketing)/blog/page.tsx` · the /docs route the landing links.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Docs + blog index scaffold (structure, not content)
Framings weighed: two index scaffolds reusing Card (chosen) · full MDX docs system (rejected, out of scope) · single /docs only (rejected, blog index also wanted)
Must:
<must>
  - NEW `/docs` renders inside `<main id="main">` with one h1 + >=3 doc CATEGORY cards (e.g. Quickstart · Providers · Admin API · BYOK), each a heading + short blurb (links may point to in-page anchors or "coming soon" — scaffold, not full content).
  - NEW `/blog` renders inside `<main id="main">` with one h1 + an honest empty-state ("No posts yet") OR >=1 placeholder entry; structure ready for real posts.
  - The landing #docs teaser link to /docs resolves (no dangling).
  - Public Server Components: NO cookie read, NO authed fetch; reuse tokens; WCAG-AA (one h1, ordered headings).
</must>
Reject:
<reject>
  - The landing/nav /docs link with no matching route (404) -> "dangling_docs_link"
  - More than one h1 / skipped heading level -> "heading_order_violation"
  - Any cookie read or authed fetch -> "public_route_gated"
</reject>
After:
<after>
  - /docs and /blog return 200; /docs shows >=3 category cards; /blog shows a structured index/empty-state.
  - axe clean; next build exit 0; whole vitest suite green; shell/proxy/auth untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ This is a SCAFFOLD: real doc/blog CONTENT + an MDX/content pipeline are deferred (separate future work).
    Lowest confidence: whether a static scaffold is "enough" for the milestone. Decision (auto): ship structure +
    honest "coming soon"/empty-state so nav never dangles; real content is post-milestone. If wrong: content fill-in only.
  - [x] No CMS/MDX pipeline (milestone Out-list).
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Docs index renders category cards
  Given an anonymous visitor at /docs
  When the page renders
  Then there is one h1 and >=3 doc category cards each with a heading and blurb

Scenario: Blog index renders a structured index or empty-state
  Given an anonymous visitor at /blog
  When the page renders
  Then there is one h1 and either >=1 entry or an honest "No posts yet" empty-state

Scenario: Landing docs link resolves
  Given the landing #docs teaser linking /docs
  When /docs is requested
  Then a page exists (no dangling link)

Scenario: Scaffold pages are accessible
  Given /docs (and /blog)
  When the a11y suite runs
  Then axe reports 0 serious/critical and headings are monotonic

Scenario: Reject — heading order
  Given a scaffold page's markup
  When heading levels are checked
  Then exactly one h1 and no skipped level ("heading_order_violation")
  And the marketing shell + route split remain unchanged

Scenario: Reject — public stays public
  Given a scaffold page module
  When its source is checked
  Then it reads no cookies() and does no authed fetch ("public_route_gated")
  And the gateway/BFF/cookie contract remains unchanged
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PAGES (Server Components, PUBLIC, each inside <main id="main">):
  app/(marketing)/docs/page.tsx   h1 "Documentation" + >=3 category <Card>s (h2/h3 + blurb)
  app/(marketing)/blog/page.tsx   h1 "Blog" + structured index OR honest empty-state ("No posts yet")
  A11Y: one h1; ordered headings; axe 0 serious/critical; shell landmarks preserved.
  REJECTIONS: dangling_docs_link · heading_order_violation · public_route_gated
Schema: NONE. No DB / gateway / BFF / cookie change. 2 new public index pages (scaffold only).
Least-sure flag surfaced at freeze: [spec] scaffold-not-content — real docs/blog + MDX pipeline deferred; structure
  ships now so nav resolves. If wrong: fill content later, no structural rework.
```

Status: FROZEN @ v1 — auto-frozen (autonomy: auto, fully-auto mode) 2026-06-24; low-risk scaffold pages, no security/contract surface.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: all new tests green + no dashboard vitest/build regression.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_docs_categories: render /docs -> one h1 + >=3 category cards (headings)
  - test_blog_index: render /blog -> one h1 + (>=1 entry OR "no posts" empty-state)
  - test_docs_link_resolves: app/(marketing)/docs/page.tsx exists on disk (landing /docs link target)
  - test_scaffold_a11y: axe 0 serious/critical on /docs; monotonic headings
  - test_reject_heading_order: exactly one h1, no skipped level (both pages)
  - test_reject_public_not_gated: both page sources read no cookies(), no authed fetch
</test_plan>

Tests live in: `apps/dashboard/tests/` · MUST run red before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/(marketing)/docs/` `apps/dashboard/app/(marketing)/blog/` `apps/dashboard/components/marketing/` `apps/dashboard/tests/`
Strategy (ordered batches): 1. red tests `tests/docs-blog.test.tsx` 2. build /docs (category cards) + /blog (empty-state index) 3. green: vitest + tsc + lint + next build.
Safety rule (feature-specific): content-only public scaffolds; never touch marketing-shell.tsx, proxy.ts, or auth/BFF code.
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
- [x] /docs renders one h1 + 4 category cards (h2); /blog renders one h1 + honest "No posts yet" empty-state — vitest 8/8, build shows 2 static routes
- [x] landing /docs link resolves — page module exists test green
- [x] public: no cookies()/next-headers/authed fetch (both) — source-guard tests green
- [x] one h1, no heading skip; axe 0 serious/critical — heading + axe tests green

### Deep checks
- [x] WIRING — DocsPage + BlogPage default exports resolved by next build (○ /docs, ○ /blog); CardTitle asChild h2 (no skip)
- [x] DEAD-CODE — none (tsc/lint clean)
- [x] SEMANTIC — scaffold honesty: "coming soon" + empty-state, no fabricated content; SPEC delta logged for real content/MDX

### Evidence (independently run): vitest 474/474 (57 files, +8) · tsc 0 · next build exit 0 (○ /docs, ○ /blog). Shell/proxy/auth untouched.

### GATE RECORD
Outcome: PASS
Reviewed by: orchestrator independent evidence review (autonomy: auto, low-risk scaffold pages, no security residue) · date: 2026-06-24

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch: 404s on /docs|/blog · axe regressions.

### Spec delta
- [SPEC · open] real docs content + MDX/content pipeline; real blog posts (evidence: scaffold-only "coming soon").

### Competency deltas
- [UDD · open] a shared marketing section/card pattern now recurs across landing/pricing/legal/docs — candidate for one section primitive.
