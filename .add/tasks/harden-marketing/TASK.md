# TASK: Apply SEO metadata, static rendering, motion + failure UX to marketing pages

slug: harden-marketing · created: 2026-06-26 · stage: production
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
- `apps/dashboard/app/layout.tsx` : RootLayout — has NO `metadata` export today. ADD `export const metadata: Metadata` with metadataBase (env-overridable), title template (`%s · Hydroa`), default description, default openGraph (website/siteName) + twitter card + robots.
- (NEW) `apps/dashboard/lib/seo.ts` : `buildMetadata({ title, description, path, absoluteTitle? })` → a `Metadata` with title, description, `alternates.canonical`, `openGraph`, `twitter`. The single SEO shape every page reuses.
- The 8 marketing pages each have a TITLE-ONLY `export const metadata` today — extend each to `buildMetadata(...)` adding a unique description + OG: `(marketing)/page.tsx` (/) · `pricing` · `docs` · `blog` · `status` · `legal/privacy` · `legal/security` · `legal/terms`.
- `apps/dashboard/app/(marketing)/page.tsx` : landing — wrap hero/feature sections in `Reveal` (motion polish; reduced-motion already safe via the global net).

Context (working folder):
- Build output already shows marketing routes as `○ (Static)` (prerendered) — EC6's static-rendering half is met by construction (no dynamic fetch in these pages); this task adds the metadata half + verifies static.
- Foundations already cover the OTHER marketing dimensions: `(marketing)/error.tsx` (task 4 failure state), global reduced-motion net (task 5, EC7), axe coverage (marketing pages already axe-tested). So harden-marketing's NET-NEW = SEO metadata (EC6) + Reveal polish.
- `Reveal` from `@/components/ui` (task 5). `Metadata` type from `next`.

Honors (patterns / conventions):
- Aurora language; no copy/content change (milestone OUT: CMS/content). Metadata is additive.
- Next 16 App Router metadata: a page `metadata` MERGES over the root layout's; `title.template` applies to child string titles; an absolute title opts out.

Anchors the contract cites: `buildMetadata` (new), root `metadata` (new), `Reveal` (reused), per-page `metadata` (extended), `openGraph`/`alternates.canonical`/`title.template`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: SEO metadata (unique title/description/OG) + static rendering verification + motion polish for marketing
Framings weighed: shared buildMetadata helper + root defaults (chosen) · inline per-page metadata objects (rejected — drift, no OG inheritance) · a CMS-driven metadata layer (rejected — out of scope, no CMS)
Must:
<must>
  - M1 The root layout exports `metadata` with `metadataBase` (env `NEXT_PUBLIC_SITE_URL`, sane default), a title `template` `"%s · Hydroa"` + default title, a default description, default `openGraph` (type website, siteName Hydroa) + `twitter` card.
  - M2 `buildMetadata({title, description, path, absoluteTitle?})` returns a `Metadata` with title (absolute opt-out for the landing root title), description, `alternates.canonical: path`, `openGraph` (title/description/url), `twitter` — reused by every marketing page.
  - M3 All 8 marketing pages (/ · pricing · docs · blog · status · legal/privacy · legal/security · legal/terms) export a `metadata` with a UNIQUE non-empty title AND description AND openGraph; titles are pairwise distinct.
  - M4 Marketing routes remain statically rendered (`○` in `next build` output) — no dynamic API added.
  - M5 The landing page wraps its hero/feature sections in `Reveal` (progressive entrance); content renders unchanged under reduced motion (global net). No copy change.
  - M6 No existing test or non-marketing behavior changes; full suite green.
</must>
Reject:
<reject>
  - a marketing page with a missing/empty description or OG, or a duplicate title -> the metadata test FAILS (M3)
  - a marketing route flipping to dynamic rendering (lost static/ISR) -> caught by the build-output check (M4)
  - motion that hides content under reduced motion -> Reveal renders children unconditionally (M5)
</reject>
After:
<after>
  - Every marketing page emits a unique title + description + OpenGraph (inheriting site defaults), stays statically rendered, and has subtle on-brand entrance motion that vanishes under reduced-motion. No content/behavior change.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `metadataBase` needs an absolute origin — lowest confidence because the real prod URL isn't hardcoded here; use `NEXT_PUBLIC_SITE_URL` with a documented placeholder default. If wrong (default ships): OG URLs are relative to the placeholder; harmless + overridable at deploy. Logged as a deploy note.
  - [ ] Adding `Reveal` (a div wrapper) around landing sections doesn't disturb the existing landing axe/layout tests — confirm by running them; Reveal is a passthrough div, should be inert.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: M1 root layout ships metadata base
  Given app/layout.tsx metadata
  When read
  Then it has metadataBase, a title.template, a default description, and openGraph

Scenario: M2/M3 every marketing page has unique title + description + OG
  Given the 8 marketing page metadata exports
  When collected
  Then each has a non-empty title, description, and openGraph.title/description
  And all 8 titles are pairwise unique

Scenario: M2 buildMetadata shape
  Given buildMetadata({title:"X", description:"d", path:"/x"})
  When called
  Then it returns title "X", description "d", alternates.canonical "/x", openGraph.title "X"

Scenario: M4 marketing stays static
  Given next build output
  When read
  Then the marketing routes are marked Static (○), not dynamic

Scenario: M5 landing renders content (with Reveal) + no regression
  Given the landing page rendered
  When mounted
  Then the hero content is present (Reveal is a passthrough) and existing landing tests stay green
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// lib/seo.ts (NEW)
import type { Metadata } from "next";
export const SITE_NAME = "Hydroa";
export function buildMetadata(opts: {
  title: string; description: string; path: string; absoluteTitle?: boolean;
}): Metadata
//   title: absoluteTitle ? { absolute: opts.title } : opts.title
//   description, alternates: { canonical: path },
//   openGraph: { type:"website", siteName:SITE_NAME, title, description, url: path },
//   twitter: { card:"summary_large_image", title, description }

// app/layout.tsx (ADD)
export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://app.hydroa.dev"),
  title: { default: "Hydroa — AI Proxy for Enterprise Teams", template: "%s · Hydroa" },
  description: "<product one-liner>",
  openGraph: { type:"website", siteName:"Hydroa", title:"Hydroa — AI Proxy for Enterprise Teams", description:"…" },
  twitter: { card:"summary_large_image" },
  robots: { index: true, follow: true },
}

// each marketing page (extend the existing title-only export):
export const metadata = buildMetadata({ title:"Pricing", description:"…", path:"/pricing" })
//   landing uses absoluteTitle:true (its title is the brand line, not "<x> · Hydroa")

// app/(marketing)/page.tsx — wrap sections:  <Reveal as="section">…</Reveal>
```

Schema: none — metadata + a helper + a presentational wrapper. No DB/network/dep. No content change.

Least-sure flag surfaced at freeze: [contract] `metadataBase` default origin is a PLACEHOLDER (`NEXT_PUBLIC_SITE_URL` override at deploy) — cost if unset: OG absolute URLs use the placeholder; harmless, deploy-note logged. · [test] Reveal wrapper around landing sections must not break the landing axe/structure tests — verified by running them.
Status: FROZEN @ v1 — approved by Tin 2026-06-26 (milestone approval; additive SEO + polish, low-risk; metadataBase placeholder is a deploy note)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% lines on `lib/seo.ts`.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_build_metadata_shape: buildMetadata({title,description,path}) → title/description/canonical/openGraph.title/twitter present + correct.
  - test_build_metadata_absolute_title: absoluteTitle:true → title is { absolute: "X" }.
  - test_root_layout_metadata: import metadata from app/layout → has metadataBase, title.template "%s · Hydroa", description, openGraph.
  - test_every_marketing_page_has_unique_seo: import metadata from all 8 pages → each has non-empty title + description + openGraph; titles pairwise unique.
  - test_landing_renders_with_reveal: render the landing page → a hero heading/text present (Reveal passthrough); no throw.
</test_plan>

Tests live in: `./tests/` · `apps/dashboard/tests/marketing-seo.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/seo.ts` `apps/dashboard/app/layout.tsx` `apps/dashboard/app/(marketing)/page.tsx` `apps/dashboard/app/(marketing)/pricing/page.tsx` `apps/dashboard/app/(marketing)/docs/page.tsx` `apps/dashboard/app/(marketing)/blog/page.tsx` `apps/dashboard/app/(marketing)/status/page.tsx` `apps/dashboard/app/(marketing)/legal/privacy/page.tsx` `apps/dashboard/app/(marketing)/legal/security/page.tsx` `apps/dashboard/app/(marketing)/legal/terms/page.tsx` `apps/dashboard/tests/marketing-seo.test.tsx`
Strategy (ordered batches): 1. lib/seo.ts buildMetadata. 2. root layout metadata. 3. each page metadata → buildMetadata. 4. landing Reveal wraps. 5. green + verify static in build.
Safety rule (feature-specific): metadata is additive; Reveal renders children unconditionally; NO copy/content change.
Code lives in: `apps/dashboard/lib/` + `apps/dashboard/app/`
Constraints: do NOT change any test or the contract; allow-list packages only (NO new dep); change no page COPY or non-metadata behavior; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 558 green (69 files); +5 new marketing-seo tests
- [x] coverage did not decrease — lib/seo.ts exercised (shape + absolute-title); metadata covered per page
- [x] no test or contract was altered during build — additive metadata + helper + a landing Reveal wrapper; no existing test changed (553→558 additive)
- [x] the green was EARNED — the test asserts REAL uniqueness (pairwise-distinct titles across all 8 pages) + non-empty description + OG on each, and the landing renders its h1 through the Reveal wrapper. Build output confirms static rendering. Presentation/SEO, no logic to game → no subagent refute-read
- [x] concurrency / timing safe — N/A: static metadata + a presentational wrapper
- [x] no exposed secrets, injection openings, or unexpected dependencies — ZERO new deps; metadataBase origin is env-overridable (NEXT_PUBLIC_SITE_URL), placeholder default is a deploy note, not a secret
- [x] layering & dependencies follow CONVENTIONS.md — shared buildMetadata in lib/seo.ts; pages consume it; root layout owns the defaults; Reveal reused from the barrel
- [x] a person reviewed — Tin approved the freeze; additive SEO/polish, low-risk, auto-gate. Owner: Tin Dang

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] Every marketing page emits unique title + description + OG — confirmed: `test_every_marketing_page_has_unique_seo` (8 pages, pairwise-unique titles, all with description + og.title/description)
- [x] Marketing routes stay statically rendered — confirmed in `next build` output: `○` for `/`, `/pricing`, `/docs`, `/blog`, `/status`, `/legal/{privacy,security,terms}`
- [x] Root layout provides the inheritance base — confirmed: `test_root_layout_metadata` (metadataBase URL, title.template `%s · Hydroa`, default description + openGraph)
- [x] Landing motion is non-destructive — confirmed: landing h1 renders through `<Reveal as="section">` (passthrough), existing landing tests green
- [x] No regression — 558-green suite, tsc 0, eslint 0, next build exit 0

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING — `buildMetadata` imported by all 8 pages; `Reveal` used in the landing hero; root `metadata` is the inheritance base (build applies it).
- [x] DEAD-CODE — no orphan; the landing Reveal import is used; every page metadata consumed by Next.
- [x] SEMANTIC — re-read each page's description for uniqueness/accuracy + confirmed no page COPY changed (only metadata + the hero section tag).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (freeze) · auto-resolved under autonomy:auto (additive SEO/polish) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): OG/title correctness via a social-card validator post-deploy; that NEXT_PUBLIC_SITE_URL is set in prod so canonical/OG URLs are absolute.

### DEPLOY NOTE
- Set `NEXT_PUBLIC_SITE_URL` to the real public origin at deploy — the build default (`https://app.hydroa.dev`) is a placeholder; metadataBase resolves canonical + OG URLs against it.

### Spec delta
- [SPEC · open] Add per-page OG images (`opengraph-image.tsx` or static) — this task ships text OG (title/description) but no image; social cards would benefit (evidence: og.images unset; twitter card is summary_large_image awaiting an image).
- [SPEC · open] Apply `Reveal` to pricing/docs/status section content too (landing only this task) + a sitemap.ts/robots.ts for full SEO (evidence: only the landing hero is wrapped; no sitemap).
- [SPEC · seeded] Marketing pages are `○` Static today (no revalidate); add `export const revalidate` if any becomes data-backed (e.g. status from a real feed) (evidence: status page is currently static placeholder).

### Competency deltas
- [SDD · open] A shared `buildMetadata` helper + root-layout defaults gives consistent SEO with title-template inheritance — far better than per-page literal objects (no OG, drift); the title template (`%s · Hydroa`) means pages store just `"Pricing"` (evidence: 8 pages unified).
- [TDD · open] Importing the ROOT layout in a test pulls `next/font/google` (`Inter`) which throws in jsdom — `vi.mock("next/font/google", ...)` per test that needs layout metadata (evidence: "Inter is not a function" → fixed).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
