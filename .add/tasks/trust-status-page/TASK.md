# TASK: Public trust and status page

slug: trust-status-page · created: 2026-06-24 · stage: production
autonomy: auto
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - NEW `apps/dashboard/app/(marketing)/status/page.tsx` — public /status (trust + component status + SLA statement). Renders inside the FROZEN MarketingShell.
  - `apps/dashboard/components/marketing-shell.tsx:FOOTER_COLUMNS` — ADD a single "Status" link (Company column) so the page is reachable. ADDITIVE link only — does NOT change the route split or any frozen behavior.
  - `apps/dashboard/components/ui/{card,badge}.tsx` — component-status rows + status badges.
Context: README (reliability posture); existing GATED /admin/health/upstreams is the FUTURE live-wiring source (a PUBLIC summary endpoint does not exist → live wiring is a SPEC delta). Hydroa tokens; vitest+axe.
Honors: v23/v24 a11y bar; public Server Component (no cookie/fetch — must NOT call the gated health endpoint).
Anchors the contract cites: `app/(marketing)/status/page.tsx` · the additive footer "Status" link · Badge/Card primitives.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Public trust & status page
Framings weighed: presentational status + SLA statement, footer-linked (chosen) · live-wired to a NEW public health endpoint (rejected: needs BE, separate task) · third-party status page e.g. Statuspage (rejected: out of scope)
Must:
<must>
  - NEW `/status` renders inside `<main id="main">` with one h1, an overall status line, a COMPONENT STATUS list (>=3 components: Proxy/data plane · Dashboard · Upstream providers) each with a status badge, and an SLA/uptime STATEMENT section.
  - Reachable: an additive "Status" link in the shell footer (no other shell change).
  - Honest sourcing: the page is PRESENTATIONAL (static "Operational" baseline) — it must NOT call the gated /admin health endpoint from a public route; live wiring is a logged SPEC delta (needs a public health-summary endpoint).
  - Public Server Component: NO cookie read, NO authed fetch; reuse tokens; WCAG-AA (one h1, ordered headings, status conveyed by text not color alone).
</must>
Reject:
<reject>
  - Calling a gated/authed endpoint from the public page -> "public_route_gated"
  - Status conveyed by color alone (no text label) -> "status_color_only" (a11y)
  - More than one h1 / skipped heading level -> "heading_order_violation"
</reject>
After:
<after>
  - /status returns 200 with one h1, >=3 component rows each with a TEXT status label, and an SLA statement; footer "Status" link resolves.
  - axe clean; next build exit 0; whole vitest suite green; route split + auth untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Status values are STATIC ("Operational") — not live. Lowest confidence: whether static is acceptable for a "status"
    page. Decision (auto): ship presentational status + SLA statement (honest, labelled as current posture), and log a
    SPEC delta for live wiring via a NEW PUBLIC health-summary endpoint (BE work, separate task). If wrong: wire later.
  - [x] Public route cannot read the gated /admin/health endpoint — would break the public/gated invariant.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Status page renders components and an SLA statement
  Given an anonymous visitor at /status
  When the page renders
  Then there is one h1, an overall status line, >=3 component rows each with a TEXT status label, and an SLA statement section

Scenario: Footer Status link resolves
  Given the shell footer has a "Status" link to /status
  When /status is requested
  Then a page exists (the link resolves)

Scenario: Status page is accessible
  Given /status
  When the a11y suite runs
  Then axe reports 0 serious/critical, status is conveyed by text (not color alone), and headings are monotonic

Scenario: Reject — no gated calls from a public page
  Given the /status page module
  When its source is checked
  Then it reads no cookies() and calls no gated/authed endpoint ("public_route_gated")
  And the gateway/BFF/cookie contract remains unchanged

Scenario: Reject — color-only status
  Given a component status indicator
  When rendered
  Then it carries a text label, not color alone ("status_color_only")

Scenario: Reject — heading order
  Given the status markup
  When heading levels are checked
  Then exactly one h1 and no skipped level ("heading_order_violation")
  And the marketing shell route split remains unchanged
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PAGE  app/(marketing)/status/page.tsx  (Server Component, PUBLIC, inside <main id="main">)
  h1 "System status"
  overall status line (text)
  COMPONENT LIST >=3 rows: { name (h2/h3) · status <Badge> with TEXT label e.g. "Operational" }
    Proxy / data plane · Dashboard · Upstream providers
  SLA STATEMENT section (h2) — uptime target + support posture (presentational)
  A11Y: one h1; ordered headings; status text-labelled (not color-only); axe 0 serious/critical; shell landmarks preserved.
SHELL  components/marketing-shell.tsx FOOTER_COLUMNS: ADD { label: "Status", href: "/status" } (additive only).
  REJECTIONS: public_route_gated · status_color_only · heading_order_violation
Schema: NONE. No DB / gateway / BFF / cookie change. 1 new public page + 1 additive footer link.
Least-sure flag surfaced at freeze: [spec] STATIC status (not live) — live wiring needs a NEW PUBLIC health-summary
  endpoint (BE, separate task); shipping presentational now. If wrong: wire to the endpoint later, structure stays.
```

Status: FROZEN @ v1 — auto-frozen (autonomy: auto, fully-auto mode) 2026-06-24; low-risk presentational page + 1 additive footer link; public page must NOT call gated endpoints (enforced by test).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: all new tests green + no dashboard vitest/build regression.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_components_and_sla: render /status -> one h1 + >=3 component rows each with a text status label + an SLA section
  - test_footer_status_link: shell FOOTER_COLUMNS includes a /status href AND the page module exists
  - test_status_a11y: axe 0 serious/critical; monotonic headings; status text-labelled
  - test_reject_public_not_gated: page source reads no cookies(), no gated/authed fetch
  - test_reject_color_only: each status indicator has a text label (not just a color class)
  - test_reject_heading_order: exactly one h1, no skipped level
</test_plan>

Tests live in: `apps/dashboard/tests/` · MUST run red before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/(marketing)/status/` `apps/dashboard/components/marketing/` `apps/dashboard/components/marketing-shell.tsx` `apps/dashboard/tests/`
Strategy (ordered batches): 1. red tests `tests/status-page.test.tsx` 2. build /status page + add the additive footer "Status" link 3. green: vitest + tsc + lint + next build.
Safety rule (feature-specific): the public page must NOT call any gated/authed endpoint; the only shell edit is the additive footer link (no route-split or proxy change).
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the frozen route-split contract; allow-list packages only.

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
- [x] /status renders one h1 + 3 component rows (each text "Operational" + a non-essential color dot aria-hidden) + an SLA section — vitest 6/6
- [x] footer "Status" link added (additive) + page exists — footer-link test green; build shows ○ /status
- [x] public: no cookies()/next-headers/authed fetch/`/admin/` — source-guard test green (caught a /admin/ literal in a comment → reworded, not weakened)
- [x] status text-labelled (not color alone); one h1, no skip; axe 0 serious/critical

### Deep checks
- [x] WIRING — StatusPage default export resolved by next build (○ /status); reachable via additive footer link
- [x] DEAD-CODE — none (tsc/lint clean)
- [x] SEMANTIC — honest static posture (labelled "current operational posture"); the color dot is aria-hidden and redundant to the text label; SPEC delta + DDD delta logged for a PUBLIC health-summary endpoint to wire live

### Evidence (independently run): vitest 480/480 (58 files, +6) · tsc 0 · next build exit 0 (○ /status). Route split + gateway/auth untouched; only additive footer link to the frozen shell.

### GATE RECORD
Outcome: PASS
Reviewed by: orchestrator independent evidence review (autonomy: auto, low-risk presentational page + additive link; the public-not-gated invariant is test-enforced) · date: 2026-06-24

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch: 404s on /status · axe regressions.

### Spec delta
- [SPEC · open] live status — add a PUBLIC health-summary endpoint (BE) and wire /status to it (evidence: current page is static/presentational).

### Competency deltas
- [DDD · open] "public health summary" is a new domain concept (a non-authed, coarse, cache-friendly view distinct from the gated /admin health) — name it before building the live wiring.
