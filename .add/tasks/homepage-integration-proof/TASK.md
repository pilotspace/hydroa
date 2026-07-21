# TASK: Show the base_url swap in the hero - prove drop-in compatibility

slug: homepage-integration-proof · created: 2026-07-20 · stage: production
milestone: frontdoor-persona-routing
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/dashboard/app/(marketing)/page.tsx:MarketingRootPage` — the FROZEN public landing (`landing-page` TASK.md §3 v1, restyled in-place by `landing-fidelity`). HERO is `<Reveal as="section" aria-labelledby="hero-heading">` (from `components/ui/motion.tsx:Reveal`, itself NOT a client component) containing: Badge "Multi-tenant AI proxy" → h1 → subhead `<p>` → CTA row (`Get started`→/signup, `Log in`→/login). This task mounts one new element as the LAST child of that same `<Reveal>`, after the CTA row, before the `#product` section boundary.
  - `apps/dashboard/components/marketing/feature-card.tsx:FeatureCard` — the house pattern for a new plain, presentational marketing subcomponent: no `"use client"`, typed props, Card/token classes only. The new `BaseUrlSwap` follows this exact shape.
  - `apps/dashboard/components/keys/QuickstartPanel.tsx:QuickstartPanel` (+ its module-private `pythonSnippet`, `curlSnippet`, `PLACEHOLDER_BASE_URL`) — the ALREADY-SHIPPED, tested "point your client at Hydroa" generator, mounted in `KeysPage` (real key) and reused verbatim at `/docs/quickstart` (placeholder key). Its `<pre>` container classes (`overflow-x-auto rounded-md border border-border bg-muted px-3 py-2 font-mono text-xs text-foreground`) are the exact class set this task reuses for the new sample's own code block. `PLACEHOLDER_BASE_URL` is a module-private literal (`"<configure NEXT_PUBLIC_API_BASE_URL>"`), not exported — this task duplicates the identical string rather than importing it.
  - `apps/dashboard/lib/public-api-base-url.ts:publicApiBaseUrl` — the ONLY sanctioned source for the tenant-facing base URL shown in quickstart-style material; returns `string | null` from `process.env.NEXT_PUBLIC_API_BASE_URL`, never fabricated.
  - `apps/dashboard/app/(marketing)/docs/quickstart/page.tsx:DocsQuickstartPage`, `PLACEHOLDER_KEY` — the precedent for a safe placeholder key on a PUBLIC, unauthenticated page (`const PLACEHOLDER_KEY = "sk-your-api-key";`) and for calling `publicApiBaseUrl()` directly in a Server Component (`const baseUrl = publicApiBaseUrl();`). This task reuses both verbatim.
  - `scripts/edge_smoke.sh` (steps 3-6) — the REAL, human-verified end-to-end call: signup → login → `POST $EDGE/admin/keys` (JWT-authed) returns a virtual key (`sk-`-prefixed — confirmed by grep across `apps/gateway/src/gateway/{artifacts,video,memory}/api/router.py` docstrings: `"""Dependency: authenticate Bearer sk-... key`) → `curl -X POST "$EDGE/v1/chat/completions" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{"model":...,"messages":[...]}'` returns real `choices[0].message.content` + `usage.cost`. This is the verified ground truth for the header name, path, and payload shape the new sample must match.
  - `apps/dashboard/app/globals.css` — the "Airier" token layer (Tin-locked 2026-07-17): `--border`, `--muted`, `--foreground`, `--muted-foreground`, `--success`/`--success-text` (semantic, kept separate from the azure `--primary`/`--accent-soft` family — design rule, not to be reused for the "added line" marker's accent), `--font-mono` (Geist Mono via `--font-geist-mono`), `--brand-from`/`--brand-to`. Every value is theme-aware (`.dark` block overrides all of them) — no new token needed.
  - `apps/dashboard/components/ui/badge.tsx` (via the hero's existing usage) — `<Badge variant="secondary" className="border border-accent-soft-border bg-accent-soft px-3 py-1 text-accent-soft-foreground">` already renders "Multi-tenant AI proxy" in the hero; reused verbatim for a new small eyebrow label so no new visual pattern is introduced.
Context (working folder):
  - `.add/tasks/landing-page/TASK.md` §3 v1 — the FROZEN contract this task amends ADDITIVELY: PAGE sections in order HERO/#product/#pricing/#docs/TRUST/CTA, "exactly one h1; h2 per section; no skipped level", rejections `heading_order_violation` / `dangling_nav_anchor` / `public_route_gated`.
  - `.add/tasks/landing-fidelity/TASK.md` — the precedent for a SECOND task safely amending the same frozen page in place: restyled hero/CTA/FeatureCard/AuthShell WITHOUT touching structure, guarded by the same frozen `tests/landing-page.test.tsx`. This task follows the identical discipline: additive, structure-preserving, guarded by the same frozen suite.
  - `apps/dashboard/tests/quickstart-panel.test.tsx` + `tests/docs-quickstart-page.test.tsx` — existing green coverage proving the `publicApiBaseUrl()` + placeholder-key pattern already works correctly on a public page; this task's own tests reuse the same harness (Vitest + Testing Library + axe).
  - `apps/dashboard/tests/landing-page.test.tsx` — the FROZEN structural guard (one h1, `#product`/`#pricing`/`#docs` ids, monotonic headings) this task must keep green, unedited.
Honors (patterns / conventions):
  - v23/v24 UI bar + `ui-restyle-recipe`: token utilities only, no raw hex/px, four-state + a11y intact, regression suite is the guard.
  - "Reuse before invent" / "consistency over novelty" (ui-designer persona, design.md beat 2): every visual value here already exists in `globals.css` or an existing component (`Badge`, `QuickstartPanel`'s `<pre>` container classes) — no new pattern introduced without a cited reason.
  - No new client boundary: `(marketing)/page.tsx`'s own docstring states "Server Component — no client directive, no browser-only APIs." `Reveal` and `FeatureCard` are both plain functions with no `"use client"` — the new subcomponent follows suit.
Anchors the contract cites:
  - `apps/dashboard/app/(marketing)/page.tsx` HERO `<Reveal as="section" aria-labelledby="hero-heading">` — the mount point.
  - `apps/dashboard/components/marketing/base-url-swap.tsx` (NEW) — `BaseUrlSwap({ baseUrl }: { baseUrl: string | null })`.
  - `apps/dashboard/lib/public-api-base-url.ts:publicApiBaseUrl` — base URL source.
  - `apps/dashboard/components/keys/QuickstartPanel.tsx` — `PLACEHOLDER_BASE_URL` literal + `<pre>` container-class precedent.
  - `apps/dashboard/app/(marketing)/docs/quickstart/page.tsx:PLACEHOLDER_KEY` — `"sk-your-api-key"` literal precedent.
  - `scripts/edge_smoke.sh` steps 3-6 — the verified real call shape.
Issues/Risks (→ feed §1):
  - The landing page's §3 v1 contract is FROZEN; this task must amend it ADDITIVELY (new mount point inside the existing hero, no restructuring, no new heading/section/nav-anchor) rather than reopen it — the same discipline `landing-fidelity` already proved safe on this exact file.
  - `QuickstartPanel`'s shipped, tested python snippet uses the bare model id `"gpt-4o"`; `scripts/edge_smoke.sh`'s default `SMOKE_MODEL` is the provider-prefixed `"openai/gpt-4o-mini"`. Both are illustrative example values (env/prop-overridable in the real script; a hardcoded example string in the UI), not part of the load-bearing proof — the header, path, and key-format ARE independently verified against `edge_smoke.sh`. Carried into §1 as the bundle's lowest-confidence assumption.
  - No copy-to-clipboard control on this sample (unlike `QuickstartPanel`'s "Duplicate" button) is a deliberate scope cut to avoid adding any client-side state to the frozen Server Component hero — carried into §1, not silently dropped.
Related intent: milestone `frontdoor-persona-routing` (`.add/tasks/…` shared context) — persona P2 "Marc, backend engineer" is "Partly served: the homepage claims 'drop-in OpenAI-compatible API' [in the `#docs` teaser] and never shows code." This task is the wave-1 fix for that specific gap: an accurate, minimal, hero-adjacent proof, not a sales-call substitute.
Ground SHA: `8daf22c` (branch `feat/frontdoor-persona-routing`, cut from `main` @ `8daf22c`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: One-line `base_url` swap proof in the homepage hero
Framings weighed:
  1. (chosen) A new small, static, presentational subcomponent (`BaseUrlSwap`) mounted at the END of the existing hero `<Reveal>`, rendering a single before/after diff-styled Python (OpenAI SDK) code block, sourced from the SAME `publicApiBaseUrl()` helper and the SAME placeholder-key literal already shipped at `/docs/quickstart` — additive, zero new client boundary, zero frozen-structure change.
  2. (rejected) Reuse `QuickstartPanel` verbatim in the hero (its curl/python/js Tabs + "Duplicate" button). It is a full client component (state, Clipboard API) that would put the FIRST client boundary inside the frozen Server-Component hero, and its three-tab, three-language, always-full-call framing is heavier than "prove it in one line" needs at the very top of the page — it is the right tool for `/docs/quickstart` and `KeysPage` (its two existing, tested homes), not for a hero glance.
  3. (rejected, for v1) A new dedicated homepage section (own `<section id="proof">`, own h2, own nav link) between HERO and `#product`. It reopens the FROZEN `landing-page` §3 section list AND the marketing-shell's nav anchors — a bigger, riskier surface than "show code under the hero" requires for a v1 proof; kept as the documented fallback if the human wants it promoted later (§1 assumption below).
Must:
<must>
  - The hero (`(marketing)/page.tsx`'s existing `<Reveal as="section" aria-labelledby="hero-heading">`) renders a new `BaseUrlSwap` element as its last child, after the CTA row, before the `#product` section boundary — one compact code block showing the SDK call before/after the `base_url` line is added.
  - The code sample is a real, minimal Python OpenAI-SDK diff: unchanged `from openai import OpenAI`, `client = OpenAI(`, `api_key="sk-your-api-key",`, `)` lines, with EXACTLY ONE added line — `base_url="{displayBaseUrl}/v1",` — visually marked (`+` prefix + `text-success-text`) as the only change.
  - `displayBaseUrl` is sourced ONLY from the existing `publicApiBaseUrl()` helper (`lib/public-api-base-url.ts`): the real value when `NEXT_PUBLIC_API_BASE_URL` is set, else the byte-identical placeholder string `QuickstartPanel.tsx` already shows (`"<configure NEXT_PUBLIC_API_BASE_URL>"`) — never a literal/hardcoded domain.
  - The API key placeholder is the literal `"sk-your-api-key"`, byte-identical to `docs/quickstart/page.tsx`'s `PLACEHOLDER_KEY` — never a real secret, never a differently-shaped fake key.
  - The header/path/payload shape implied by the snippet (`Authorization: Bearer <key>` via the SDK, `{base_url}/v1` + the SDK's own `/chat/completions` route) matches the REAL, human-verified call in `scripts/edge_smoke.sh` (steps 5-6).
  - `BaseUrlSwap` is a plain function component with NO `"use client"` directive and NO browser-only API — `(marketing)/page.tsx` stays a pure Server Component per its own frozen docstring.
  - The sample introduces NO new heading element (no h1/h2/h3) — the hero's existing single-h1 shape, and the landing page's overall "exactly one h1; h2 per section; no skipped level" invariant (frozen `landing-page` §3 v1), are both unchanged.
  - The code block sits in its own `overflow-x-auto` scroll container (same class set as `QuickstartPanel`'s `<pre>`) so it never forces the hero/body to scroll horizontally, including at mobile widths (≤390px).
  - Every color/spacing/radius/font value used already exists in `globals.css`'s token layer or an existing component (the hero's own `Badge` pattern for the eyebrow label; `QuickstartPanel`'s `<pre>` container classes for the code block; `--success`/`--success-text` for the added-line marker) — legible (≥4.5:1 body-text contrast) in both light and dark themes.
</must>
Reject:
<reject>
  - The `base_url` line is a literal/hardcoded domain instead of `publicApiBaseUrl()`'s return value -> "fabricated_endpoint"
  - A second h1, or any h2/h3 introduced, or a heading level skipped, anywhere in the hero as a result of this change -> "heading_order_violation"
  - The code block or its container forces horizontal scroll on the hero/page at a mobile viewport (≤390px) -> "layout_overflow"
  - The header name, path shape, key prefix, or SDK call shape shown diverges from `scripts/edge_smoke.sh`'s verified real call or `QuickstartPanel`'s shipped snippet -> "unverified_snippet"
  - `BaseUrlSwap` (or `page.tsx`) gains a `"use client"` directive or calls a browser-only API -> "unnecessary_client_boundary"
  - A raw hex/px literal, or any color/spacing/radius/visual pattern not traceable to an existing token or component -> "raw_value"
</reject>
After:
<after>
  - An anonymous visitor scanning the homepage sees, within the hero, a correct, compact proof that Hydroa is drop-in OpenAI-compatible — a real SDK call with exactly one added line — before scrolling past the fold, answering P2 Marc's question without a sales call.
  - `next build` exits 0; axe reports 0 serious/critical; the FROZEN `tests/landing-page.test.tsx` (one h1, section ids, monotonic headings) stays green, unedited; new tests for `BaseUrlSwap` pass; no horizontal scroll at 375/390px widths; the sample is legible in both light and dark themes.
  - The `#docs` teaser's existing claim ("Drop-in OpenAI-compatible API") is no longer unsupported on the homepage — the hero now backs it with real code, ahead of the full `/docs/quickstart` walkthrough.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Model id example: `QuickstartPanel`'s shipped, tested python snippet uses the bare id `"gpt-4o"`; `scripts/edge_smoke.sh`'s default `SMOKE_MODEL` is the provider-prefixed `"openai/gpt-4o-mini"`. Lowest confidence because the model-catalog seed migration I read (`9cdca76231c6_model_catalog_db_seed.py`) lists `gpt-realtime`/`gpt-5.4`-family rows, not a literal `gpt-4o` row — I could not independently confirm which form is canonical against the live catalog from static reading alone. Resolution: follow `QuickstartPanel`'s shipped convention (bare `"gpt-4o"`) for cross-page consistency (persona rule: consistency over novelty) rather than invent a third form. If wrong: a one-line string fix in `base-url-swap.tsx`; it does not touch the load-bearing part of the proof (the `base_url` line, the header, the key format), which IS independently verified against `edge_smoke.sh`.
  - [ ] No copy-to-clipboard control on this sample (unlike `QuickstartPanel`'s "Duplicate" button) — deliberate, to keep zero client-side state in the frozen Server Component hero; a visitor who wants to copy clicks through via the existing "Get started"/"Log in" CTAs or `/docs/quickstart`. If wrong: additive follow-up using the exact pattern `QuickstartPanel` already proves (extract a small client subcomponent) — cheap, non-breaking.
  - [ ] Placement is INSIDE the existing hero (last child, no new section/heading/nav-anchor) rather than a new standalone "proof" section between HERO and `#product`. Chosen to avoid reopening the FROZEN `landing-page` section-list/nav-anchor contract for a v1 proof. If wrong (the human wants it promoted to a full labeled section): a move-only rework — wrap in `<section aria-labelledby>` + h2, no content change.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Hero shows the one-line base_url diff   # M1
  Given an anonymous visitor lands on /
  When the hero renders
  Then a compact code block appears as the last element of the hero, after the CTA row and before #product
  And it shows the OpenAI SDK call with exactly one line marked as added: base_url="{displayBaseUrl}/v1"

Scenario: Call shape matches the verified real gateway surface   # M4
  Given the rendered sample
  When its header, path, and payload shape are compared to scripts/edge_smoke.sh's real call (Authorization: Bearer <key> against {base}/v1/chat/completions)
  Then they match exactly — same header name, same path convention, same sk- key-prefix format

Scenario: Configured base URL renders verbatim   # M2
  Given NEXT_PUBLIC_API_BASE_URL is set to a real origin
  When the hero renders
  Then the added line shows that real origin + "/v1", read only from publicApiBaseUrl()

Scenario: Unconfigured base URL falls back to the shared honest placeholder   # M2
  Given NEXT_PUBLIC_API_BASE_URL is unset
  When the hero renders
  Then the added line shows the same placeholder text QuickstartPanel already shows ("<configure NEXT_PUBLIC_API_BASE_URL>"), never a fabricated domain

Scenario: API key placeholder is safe and consistent   # M3
  Given the homepage is public and unauthenticated
  When the sample renders
  Then the key shown is the literal "sk-your-api-key", byte-identical to /docs/quickstart's placeholder, never a real secret

Scenario: Sample stays inside the frozen Server Component hero   # M5
  Given (marketing)/page.tsx is a frozen Server Component
  When BaseUrlSwap is added
  Then it has no "use client" directive and calls no browser-only API

Scenario: Heading structure is untouched   # M6
  Given the frozen landing contract (exactly one h1, h2 per section, no skipped level)
  When the sample is added to the hero
  Then no new h1/h2/h3 is introduced and the existing heading order is unchanged

Scenario: No horizontal scroll at mobile width   # M7
  Given a 375px-wide mobile viewport
  When the hero, including the code sample, renders
  Then the code scrolls horizontally WITHIN its own overflow-x-auto container and the page/body shows no horizontal scrollbar

Scenario: Legible in both themes using existing tokens   # M8
  Given the light theme and the dark theme
  When the sample renders in each
  Then every color used resolves to an existing CSS custom property (border-border/bg-muted/text-foreground/text-success-text) and the added-line marker meets >=4.5:1 contrast in both

Scenario: Reject — fabricated endpoint   # R1
  Given a build where the base_url line is a hardcoded literal domain instead of publicApiBaseUrl()'s return value
  When the contract check runs
  Then it is rejected as "fabricated_endpoint"
  And the sample continues to read the base URL only from publicApiBaseUrl()

Scenario: Reject — heading order violation   # R2
  Given the hero after the change
  When heading levels are checked
  Then a second h1, an introduced h2/h3, or a skipped level is rejected as "heading_order_violation"
  And the pre-existing frozen tests/landing-page.test.tsx assertions remain unedited and green

Scenario: Reject — layout overflow   # R3
  Given a 320-390px mobile viewport
  When the code sample is wider than its own scroll container
  Then it is rejected as "layout_overflow"
  And the fix stays contained to the sample's own container, never a page-wide layout change

Scenario: Reject — unverified snippet   # R4
  Given the sample's header name, path, or key-prefix format
  When compared against scripts/edge_smoke.sh's verified real call and QuickstartPanel's shipped snippet
  Then any divergence is rejected as "unverified_snippet"
  And the sample is corrected to match the verified real surface, never left as an invented shape

Scenario: Reject — unnecessary client boundary   # R5
  Given the new BaseUrlSwap subcomponent
  When it is reviewed
  Then a "use client" directive or a browser-only API call is rejected as "unnecessary_client_boundary"
  And (marketing)/page.tsx remains a pure Server Component per its frozen docstring

Scenario: Reject — raw or new visual pattern   # R6
  Given the sample's styling
  When checked against the Aurora/Airier token layer and existing component catalog
  Then a raw hex/px literal, or any uncited new visual pattern, is rejected as "raw_value"
  And every color/spacing/radius value used traces to an existing token or component (e.g. the hero's own Badge pattern, QuickstartPanel's <pre> classes)
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
SURFACE (presentation-only, no endpoint — extends the FROZEN `landing-page` §3 v1 contract on
  (marketing)/page.tsx additively; does not reopen its section list or nav anchors)

  (marketing)/page.tsx  HERO  <Reveal as="section" aria-labelledby="hero-heading"> (Server Component,
    UNCHANGED: one h1 + subhead + 2 CTAs)
    + NEW last child: <BaseUrlSwap baseUrl={publicApiBaseUrl()} />
      — mounted after the CTA button row, before the #product section boundary.
      — introduces NO new heading (h1/h2/h3) and NO new landmark.

  components/marketing/base-url-swap.tsx  (NEW FILE)
    BaseUrlSwap(props: { baseUrl: string | null })  — plain function component, NO "use client",
      NO browser-only API (mirrors components/marketing/feature-card.tsx's house shape)
    renders:
      - an eyebrow label reusing the hero's existing Badge pattern verbatim:
          <Badge variant="secondary" className="border border-accent-soft-border bg-accent-soft
            px-3 py-1 text-accent-soft-foreground">Drop-in proof</Badge>
      - ONE <pre className="overflow-x-auto rounded-md border border-border bg-muted px-3 py-2
          font-mono text-xs text-foreground"><code> block (identical container classes to
          QuickstartPanel's <pre>) containing exactly:
            from openai import OpenAI

            client = OpenAI(
          + base_url="{displayBaseUrl}/v1",   # <- the only line you add
              api_key="sk-your-api-key",
            )
        where the `+`-marked line alone carries `text-success-text` (+ a subtle `bg-success/10`
        band); every other line stays `text-foreground` — no color outside the existing
        --success/--success-text pair is introduced.
      - displayBaseUrl = baseUrl ?? "<configure NEXT_PUBLIC_API_BASE_URL>"
          (byte-identical to QuickstartPanel.tsx's own PLACEHOLDER_BASE_URL literal, duplicated
          here since that constant is module-private/not exported)
      - baseUrl is ALWAYS supplied by the page calling the EXISTING publicApiBaseUrl() helper
          (lib/public-api-base-url.ts) — the SAME call docs/quickstart/page.tsx already makes;
          NEVER a literal/hardcoded domain inside base-url-swap.tsx itself.
      - api_key placeholder is the literal "sk-your-api-key" — byte-identical to
          docs/quickstart/page.tsx's PLACEHOLDER_KEY.

  REJECTIONS (name -> what trips it)
    fabricated_endpoint          -> the base_url line is a literal/hardcoded domain, not
                                     publicApiBaseUrl()'s return value
    heading_order_violation      -> a 2nd h1, or any h2/h3 introduced/skipped by this change
                                     (extends the landing-page frozen invariant — same code,
                                     not redefined)
    layout_overflow              -> the code block or its container forces horizontal scroll on
                                     the hero/body at a mobile viewport (<=390px)
    unverified_snippet           -> header name / path shape / key prefix / SDK call shape
                                     diverges from scripts/edge_smoke.sh's verified real call or
                                     QuickstartPanel's shipped snippet
    unnecessary_client_boundary  -> BaseUrlSwap (or page.tsx) gains a "use client" directive or
                                     calls a browser-only API
    raw_value                    -> a raw hex/px literal, or any color/spacing/radius/pattern not
                                     traceable to an existing token or component

  INVARIANTS held from the FROZEN landing-page §3 v1 (unchanged by this task):
    exactly one h1 · #product/#pricing/#docs sections present in order · no cookie read / authed
    fetch · marketing-shell chrome untouched · tests/landing-page.test.tsx stays green, unedited

Schema: NONE. No DB, no gateway/BFF/cookie change. Presentation-only addition: one new file
  (components/marketing/base-url-swap.tsx) + a small edit to (marketing)/page.tsx (mount point
  only — no restructuring of existing sections) + new tests.
```

Glossary deltas: none — this is a presentation pattern (a hero code sample), not a new domain
  concept; no term added to `.add/GLOSSARY.md`.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — design span only (§0-§3); the freeze itself, and rendering the freeze report, is
  the human's decision, not drafted here.
Least-sure flag surfaced at freeze: [spec] the model-id literal ("gpt-4o", following
  QuickstartPanel's shipped convention, over edge_smoke.sh's "openai/gpt-4o-mini" default) is the
  single lowest-confidence point in this bundle — see §1 ⚠. It is cosmetic, not load-bearing: the
  header, path, and key-format ARE independently verified against edge_smoke.sh. Second-lowest:
  [spec] placement is INSIDE the existing hero rather than a new labeled section (§1, assumption
  #2) — chosen to avoid reopening the FROZEN landing-page section list; reversible as a move-only
  rework if the human wants it promoted later.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% lines on the new `components/marketing/base-url-swap.tsx` (matches
  `vitest.config.ts`'s repo-wide 80% floor on `components/**/*.tsx`, raised for this small,
  fully-covered new file — no branch is untested: configured/unconfigured baseUrl are both
  exercised).

Persona: Frontend Engineer (`.add/personas/frontend-engineer.md`, flow: build/advisor) — the
  dashboard implementation lens (BFF-trust-boundary discipline N/A here — presentation-only,
  no fetch; SSR-safety + design-token fidelity ARE in scope: this suite enforces zero
  `"use client"`, zero raw hex/inline-style, token-classes-only).

Stable lookup hooks this suite requires of Build (a selector aid, not new behavior — mirrors
  the shipped `data-testid="quickstart-panel"` precedent): `data-testid="base-url-swap"` on
  `BaseUrlSwap`'s root; `data-testid="added-line"` on the ONE `+`-marked line. Declared here
  since §3 leaves the exact DOM shape to Build while pinning which line carries which class.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_hero_shows_base_url_diff (2 tests): arrange render homepage / act query hero +
    `data-testid="base-url-swap"` / assert swap lives inside hero, after both CTA links
    (`compareDocumentPosition`), not inside `#product`; assert exactly one `data-testid="added-line"`
    containing `base_url="{origin}/v1",` · covers: M1
  - test_call_shape_matches_edge_smoke: arrange configured baseUrl / act render / assert added-line
    ends `/v1"` and the placeholder key is byte `sk-`-prefixed, cross-checked against
    `scripts/edge_smoke.sh`'s real `/v1/chat/completions` + `Authorization: Bearer` shape (read live,
    not re-typed) · covers: M4
  - test_configured_base_url_renders_verbatim: arrange `NEXT_PUBLIC_API_BASE_URL` set to a
    DISTINCT marker origin / act render / assert added-line contains that exact origin + `/v1` ·
    covers: M2
  - test_unconfigured_base_url_falls_back_to_shared_placeholder: arrange env unset / act render /
    assert added-line contains the byte-identical `QuickstartPanel` placeholder string, never a URL
    shape · covers: M2
  - test_api_key_placeholder_safe_and_consistent: assert `api_key="sk-your-api-key"` renders,
    byte-identical to `docs/quickstart/page.tsx`'s `PLACEHOLDER_KEY` (read live) · covers: M3
  - test_stays_inside_frozen_server_component_hero (2 tests): assert
    `components/marketing/base-url-swap.tsx` source has no `"use client"`/browser API (NEW-RED,
    file absent) + `[REGRESSION PIN]` `(marketing)/page.tsx` itself stays a Server Component
    (already true today, unrelated to this task — excluded from red count) · covers: M5, R5
  - test_heading_structure_untouched: existence-first (`getByTestId` throws today) then assert
    zero headings inside the swap subtree and page-wide h1 count stays 1 · covers: M6, R2
  - test_no_horizontal_scroll_at_mobile_width: assert the `<pre>` carries `overflow-x-auto` +
    `QuickstartPanel`'s exact container class set, no fixed/arbitrary width utility · covers: M7, R3
  - test_legible_in_both_themes_via_existing_tokens (2 tests): assert eyebrow/pre/added-line use
    only existing token classes (`bg-accent-soft`, `text-success-text`, `text-foreground`), zero raw
    hex/inline style; axe 0 serious/critical on the mounted subtree (existence-first, so it cannot
    vacuously pass on an absent element) · covers: M8
  - test_reject_fabricated_endpoint: arrange a distinct marker origin / assert the rendered value
    changes WITH the env var (proves derivation, not a literal) · covers: R1
  - test_reject_heading_order_violation: existence-first, then no h1/h2/h3 skip anywhere on the
    page · covers: R2
  - test_reject_unverified_snippet: assert no invented header-name literal (e.g. `X-Api-Key`) and
    the `/v1` path convention matches `edge_smoke.sh`'s real call · covers: R4
  - test_reject_raw_value: assert zero hex/`rgb()`/inline-style anywhere in the swap subtree ·
    covers: R6
  - ground-truth sanity: `scripts/edge_smoke.sh` still contains `/v1/chat/completions` +
    `Authorization: Bearer` — read once so a drift in the ground-truth script itself is caught
    loudly, not silently assumed (GREEN today by design; anchors every other assertion above).
</test_plan>

Tests live in: `./tests/` · `tests/base-url-swap.test.tsx` (NEW, 17 tests: 15 red / 1 ground-truth
  sanity green-by-design / 1 labelled `[REGRESSION PIN]` green-by-design — 0 vacuous passes) ·
  MUST run red (missing implementation) before Build.

RED evidence (`./node_modules/.bin/vitest run tests/base-url-swap.test.tsx --reporter=verbose`,
  run from `apps/dashboard`, 2026-07-20):
```
 Test Files  1 failed (1)
      Tests  15 failed | 2 passed (17)
```
Every failure is `TestingLibraryElementError: Unable to find an element by:
[data-testid="base-url-swap"]` (or a downstream query inside it) — missing implementation, the
RIGHT reason; no harness/typo failure. The 2 green tests are the ground-truth sanity check
(`scripts/edge_smoke.sh` still shapes the real call this suite pins) and the ONE test explicitly
labelled `[REGRESSION PIN]` — both correctly excluded from the 15-test red count.

FROZEN regression baseline (unedited; run together from `apps/dashboard`, 2026-07-20, BEFORE any
  edit in this task's scope): `tests/landing-page.test.tsx` (22 tests), `tests/pricing-page.test.tsx`
  (12 tests, unaffected by this task — cited for completeness), `tests/quickstart-panel.test.tsx`
  (4 tests) — **all 38 green**, 0 files touched:
```
 Test Files  3 passed (3)
      Tests  38 passed (38)
```
Confirms `tests/landing-page.test.tsx`'s frozen structural guard (one h1, monotonic headings,
`#product`/`#pricing`/`#docs` ids) is currently green and untouched by this task's test-only change
— no edit was made to that file or to `(marketing)/page.tsx`.

Cross-suite sanity (`apps/dashboard`, 2026-07-20) — this file + the sibling `pricing-tier-ladder`
  task's 3 files run together, confirming no name collision / no cross-file contamination from the
  shared `NEXT_PUBLIC_API_BASE_URL` env-var manipulation (both suites restore it in `afterEach`):
```
 Test Files  3 failed | 4 passed (7)
      Tests  29 failed | 51 passed (80)
```
29 = exactly this task's 15 + `pricing-tier-ladder`'s 12 new/retargeted red tests +
`pricing-catalog-no-drift.test.ts`'s 2 retargeted red assertions (15+12+2=29) — no unexplained
failure, no collateral damage to `docs-quickstart-page.test.tsx` (also green in this run).

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

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

