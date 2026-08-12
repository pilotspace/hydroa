# TASK: Public marketing shell + root-route split

slug: marketing-shell · created: 2026-06-24 · stage: production
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
  - `apps/dashboard/app/page.tsx:RootPage` — async Server Component AT `/`. TODAY: reads `cookies()`,
    redirects to `/login` if no `ai_proxy_session`, else renders `<DashboardShell><OverviewPage/></DashboardShell>`.
    THIS TASK flips `/` to the PUBLIC landing entry; the authenticated Overview RELOCATES to the gated segment.
  - `apps/dashboard/proxy.ts:proxy` + `config.matcher` — Next 16 middleware (renamed from middleware.ts).
    Cookie-presence → 307 `/login`. `matcher` TODAY = `["/keys","/keys/:path*","/usage","/usage/:path*"]`
    (note: it does NOT currently cover most gated routes — they rely on per-page/BFF 401). The matcher must
    cover the relocated gated-app root after the split. THE RISKIEST CONTRACT THIS TASK FREEZES.
  - `apps/dashboard/app/(dashboard)/layout.tsx:DashboardLayout` — wraps the (dashboard) route group in
    `<DashboardShell>` (role-aware nav). This is the AUTHENTICATED segment; marketing pages must NOT use it.
  - `apps/dashboard/app/(auth)/layout.tsx` — EMPTY pass-through layout (login/signup are public, un-shelled).
  - `apps/dashboard/app/layout.tsx:RootLayout` — global Server Component: Inter font, no-flash `themeScript()`,
    `<Providers>` (theme + react-query). Marketing pages SHARE this root layout (fonts/theme) but need their own
    marketing layout (public nav/footer), distinct from DashboardShell.
  - `apps/dashboard/components/dashboard-shell.tsx:DashboardShell` — authenticated app shell (feeds user role
    into AppShell). Reference for the PARALLEL public marketing shell to build; NOT reused for marketing.
  - `apps/dashboard/components/overview/OverviewPage:OverviewPage` — the authed Overview currently at `/`;
    moves to the gated-app root (e.g. an `/app` segment) as part of the split.
Context (working folder):
  - `apps/dashboard/app/globals.css` (6.4K) — design tokens / theme vars the marketing shell must reuse.
  - `apps/dashboard/components.json` — shadcn/ui config (component source-of-truth for reuse).
  - `apps/dashboard/proxy.ts` header comment — documents the Next-16 `middleware.ts`→`proxy.ts` rename and the
    "byte-identical guard / UX-only / gateway validates JWT" invariant the split must preserve.
  - Vitest + Playwright a11y harness already present (`tests/`, `e2e-a11y/`, `playwright.config.ts`).
Honors (patterns / conventions):
  - PROJECT.md: FE route guards are UX-ONLY — auth is enforced on the gateway (JWT on every BFF call). The
    split MUST NOT weaken auth: gated routes stay cookie-gated; `proxy.ts` semantics + gateway unchanged.
  - v23/v24 UI bar (memory): WCAG-AA, design tokens, headingLevel discipline, Server Components for no-flash theme.
  - Next.js 16 App Router route groups; the guard file is `proxy.ts` exporting `proxy` (never re-introduce middleware.ts).
Anchors the contract cites:
  - `app/page.tsx:RootPage` (becomes public; no auth redirect)
  - the gated-app segment root path (relocated authenticated entry, e.g. `app/(app)/.../page.tsx`) — NAME to freeze
  - `proxy.ts:config.matcher` (the post-split matched paths)
  - the new public marketing layout component (e.g. `app/(marketing)/layout.tsx` + MarketingShell) — NAME to freeze

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Public marketing shell + `/app` route split
Framings weighed: single `/app` gated prefix + `(marketing)` public group (chosen) · per-route explicit
  gating (rejected: fragile matcher enumeration) · subdomain split www vs app (rejected: out of scope, infra change)
Must:
<must>
  - `/` serves a PUBLIC landing entry with NO auth redirect (no `ai_proxy_session` cookie required).
  - A public `(marketing)` route group with its own `MarketingShell` layout (header nav + footer) wraps all
    marketing routes; it is DISTINCT from `DashboardShell` and reuses the root layout's Inter font + theme tokens.
  - The authenticated app relocates under `/app`: the Overview formerly at `/` is now at `/app`; every existing
    gated route moves to `/app/<route>` (`/app/keys`, `/app/usage`, `/app/spend`, `/app/teams`, `/app/routing`,
    `/app/alerts`, `/app/health`, `/app/models`, `/app/settings`).
  - `proxy.ts` `config.matcher` guards `/app` + `/app/:path*` (cookie-absent → 307 `/login`), preserving the
    byte-identical UX-only guard semantics; the gateway still validates the JWT on every BFF call (no auth change).
  - Every in-app navigation target (DashboardShell nav, internal `<Link>`s, post-login redirect) resolves to its
    `/app/*` location — no link points to a 404 or a redirecting path.
  - Legacy gated URLs (`/keys`, `/usage`, …) are NOT re-routed (hard-cut to 404 — Tin dropped redirects at freeze); therefore EVERY internal reference must be rewritten to `/app/*` (no redirect safety net).
  - MarketingShell meets WCAG-AA (skip-link, header/nav/main/footer landmarks, heading discipline) and uses design tokens.
</must>
Reject:
<reject>
  - Anonymous request to `/app` or any `/app/*` (no session cookie) -> 307 `/login` (never render gated content) -> "unauthenticated_gated_access"
  - A marketing route that renders inside DashboardShell or requires a cookie -> "public_route_gated" (marketing must stay open)
</reject>
After:
<after>
  - `/` returns 200 public landing for an anonymous visitor (no redirect).
  - `/app` renders the Overview for an authenticated visitor; redirects to `/login` for an anonymous one.
  - Every former in-app link resolves to its `/app/*` location; legacy paths are GONE (404, no redirect); no nav points to a dead path.
  - `proxy.ts` guard semantics unchanged except the matched path set; gateway + BFF + cookie contract untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [RESOLVED by grep 2026-06-24] Internal refs to OLD paths are CONTAINED, not scattered: the nav array in
    `components/ui/app-shell.tsx:44-52` (9 hrefs) + post-auth `router.push("/keys")` in `LoginForm.tsx:176`
    & `SignupForm.tsx:77` + 2 test refs (`tests/design-system/enterprise-ext.test.tsx:348`, `tests/setup.ts`
    comment). With redirects DROPPED a missed ref is a hard 404 → the rewrite is load-bearing but the surface
    is now known and small. Residual risk: route-group dir moves break relative imports (verify build).
  - [x] MarketingShell visual confirmed via the wireframe at freeze (chrome only; landing CONTENT → `landing-page`).
  - [x] Legacy redirects DROPPED → hard-cut 404 (Tin's freeze decision 2026-06-24).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Anonymous visitor sees the public landing at /
  Given a visitor with no ai_proxy_session cookie
  When they request GET /
  Then they receive 200 and the public landing rendered inside MarketingShell (no redirect to /login)

Scenario: Marketing routes use MarketingShell, not DashboardShell
  Given the (marketing) route group
  When any marketing route renders
  Then it is wrapped by MarketingShell (header nav + footer) and NOT by DashboardShell
  And it performs no cookie check and no authenticated data fetch

Scenario: Authenticated app is reachable at /app
  Given a visitor WITH a valid ai_proxy_session cookie
  When they request GET /app
  Then they receive 200 and the Overview rendered inside DashboardShell

Scenario: proxy.ts guards the /app segment
  Given a visitor with no ai_proxy_session cookie
  When they request GET /app/keys
  Then proxy.ts returns 307 to /login
  And the guard semantics are byte-identical to the prior guard (UX-only; the gateway still validates the JWT)

Scenario: Every in-app link points to /app/*
  Given the DashboardShell nav and all internal links/redirects
  When the app renders after the split
  Then every navigation target resolves under /app/* and none points to a 404 or a redirecting path

Scenario: Legacy gated URL is gone (hard-cut)
  Given the OLD path /keys (now relocated under /app)
  When a visitor requests GET /keys
  Then they receive 404 (no /keys route exists; redirects were dropped at freeze)
  And no internal link in the app points to /keys (all rewritten to /app/keys)

Scenario: MarketingShell is accessible
  Given the public MarketingShell
  When the a11y suite runs against a marketing route
  Then it passes WCAG-AA (skip-link present, header/nav/main/footer landmarks, no heading-order violation)

Scenario: Reject — anonymous access to a gated route
  Given a visitor with no ai_proxy_session cookie
  When they request GET /app
  Then they are redirected 307 to /login ("unauthenticated_gated_access")
  And no gated content is rendered and no cookie/auth contract is changed

Scenario: Reject — a public route must not be gated
  Given a marketing route
  When it is defined
  Then it must NOT live under DashboardShell or require a cookie ("public_route_gated")
  And the gateway JWT validation and BFF cookie contract remain unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
ROUTE MAP (frozen)
  /                         PUBLIC  app/(marketing)/page.tsx  via MarketingShell   200, no cookie, no redirect
  /pricing /legal/* /docs   PUBLIC  app/(marketing)/*         (scaffold owned by later tasks; group + shell frozen here)
  /login  /signup           PUBLIC  app/(auth)/*              (unchanged)
  /app                      GATED   app/(app)/page.tsx        Overview via DashboardShell
  /app/{keys,usage,spend,teams,routing,alerts,health,models,settings}
                            GATED   app/(app)/<route>/page.tsx  (relocated from the (dashboard) group)

GUARD  proxy.ts
  config.matcher = ["/app", "/app/:path*"]
  cookie ai_proxy_session ABSENT  -> 307 /login
  cookie PRESENT                  -> NextResponse.next()
  (semantics byte-identical to the prior guard; UX-only; gateway still validates JWT on every BFF call)

LEGACY PATHS  (hard-cut — Tin DROPPED redirects at freeze 2026-06-24)
  /keys /usage /spend /teams /routing /alerts /health /models /settings  -> 404 (no longer routed; moved under /app)
  -> consequence: EVERY internal reference MUST be rewritten to /app/* (no redirect safety net) — the grep is load-bearing.

COMPONENTS / LAYOUTS
  app/layout.tsx              UNCHANGED (Inter font · themeScript · Providers)
  app/(marketing)/layout.tsx  NEW MarketingShell — public header nav (Product · Pricing · Docs · [Log in] [Sign up])
                              + footer (legal/links); reuses Hydroa design tokens; NO cookie, NO authed fetch
  app/(app)/layout.tsx        relocated DashboardLayout -> DashboardShell (the former (dashboard) group layout)

REJECTION RESPONSES
  unauthenticated_gated_access -> 307 /login (proxy.ts; no gated render)
  public_route_gated           -> structural invariant: a (marketing) route never mounts DashboardShell / never reads the cookie

Schema: NONE (no DB). Routing + component boundaries only. No gateway, BFF, cookie, or JWT contract change.
```

Least-sure flag surfaced at freeze: [contract/test] Scattered legacy-path references — internal links/redirects to
  `/`, `/keys`, `/usage`, … across DashboardShell nav, `<Link>`/`router.push`, post-login redirect, proxy.ts,
  BFF, and e2e/a11y specs. With redirects DROPPED a missed ref is a HARD 404 (no net). Why riskiest: the rewrite
  is load-bearing and Next route-group dir moves can break relative imports. Cost if wrong: broken nav + red e2e.
  Mitigation: exhaustive grep before build (surface mapped = contained, small) + `next build` route-map verify.
Status: FROZEN @ v1 — approved by Tin 2026-06-24 (chose: drop legacy redirects → old paths hard-cut to 404).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: all new tests green + NO dashboard vitest/a11y/build regression (v37 baseline: vitest green, real `next build` exit 0).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_root_is_public: render the (marketing) root → assert landing content present, NO redirect, no cookie read
  - test_marketing_uses_marketing_shell: render a (marketing) route → assert MarketingShell chrome (banner/contentinfo landmarks) present AND DashboardShell absent
  - test_app_overview_authed: render /app with a session cookie present → assert Overview renders inside DashboardShell
  - test_proxy_guards_app: call proxy() with a NextRequest to /app/keys, no cookie → assert 307 Location /login; with cookie → assert next()
  - test_nav_targets_under_app: render DashboardShell nav → assert every link href startsWith "/app" (none bare /keys, /usage, …)
  - test_legacy_path_gone: assert no /keys route exists (request /keys → 404) AND grep asserts zero internal refs to bare /keys (all rewritten to /app/keys)
  - test_marketing_a11y: axe a marketing route → assert 0 violations + skip-link + heading order (WCAG-AA)
  - test_reject_anon_app: proxy() to /app, no cookie → assert 307 /login (no gated render) + cookie/JWT contract unchanged
  - test_reject_public_not_gated: assert the (marketing) layout module neither imports DashboardShell nor reads cookies()
</test_plan>

Tests live in: `apps/dashboard/tests/` · `apps/dashboard/e2e-a11y/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/` `apps/dashboard/components/` `apps/dashboard/proxy.ts` `apps/dashboard/next.config.ts` `apps/dashboard/tests/` `apps/dashboard/e2e-a11y/`
Strategy (ordered batches):
  1. Create `app/(app)/` group: move the Overview to `(app)/page.tsx` + relocate the 9 (dashboard) route dirs under `(app)/`; move `(dashboard)/layout.tsx` → `(app)/layout.tsx`.
  2. Create `app/(marketing)/` group: `layout.tsx` (MarketingShell) + a placeholder `page.tsx` landing (full content → landing-page task); add `components/marketing-shell.tsx` (+ header/footer) reusing Hydroa tokens.
  3. Delete the old authed `app/page.tsx` (its redirect is gone; `/` is now the marketing page).
  4. proxy.ts: matcher → `["/app","/app/:path*"]`; drop the old `/keys`,`/usage` matcher entries (no legacy redirects — Tin's freeze choice).
  5. Rewrite EVERY internal nav/link/post-login redirect in DashboardShell + any `<Link>`/`router.push`/test/e2e spec to `/app/*`, driven by an EXHAUSTIVE grep — load-bearing: no redirect net, a missed ref is a hard 404.
Safety rule (feature-specific): the route relocation MUST preserve auth — gated routes stay cookie-guarded (now via /app matcher), the gateway/BFF/JWT/cookie contract is untouched; the marketing layout never reads the cookie.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — INDEPENDENTLY re-run: vitest 424/424 (53 files)
- [x] coverage did not decrease — +24 new marketing-shell tests; whole suite green, no prior test removed (only path strings updated to /app/*)
- [x] no test or contract was altered during build — §3 FROZEN untouched; existing tests only had /keys→/app/keys path updates (required by the relocation, not weakening)
- [x] the green was EARNED, not gamed — orchestrator refute-read: proxy tests are real NextRequest→307 behavior, a11y via axe, render+role queries; source-introspection guards (no DashboardShell import / no cookies()) are legit for route-group structure (can't render group resolution in jsdom). No vacuous asserts found.
- [x] concurrency / timing — N/A (no async/shared-state risk; pure routing + stateless Server Components; guard semantics byte-identical)
- [x] no exposed secrets / injection / unexpected deps — no new dependency; no secrets; marketing layout reads NO cookie (verified in source + test)
- [x] layering & dependencies follow CONVENTIONS.md — FE-only; auth stays gateway-enforced; proxy.ts guard unchanged except matcher path set
- [x] a person reviewed — Tin froze §3; orchestrator did independent diff review + re-ran suite/tsc/build (auto-gate on complete evidence; non-security, no residue)

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] `/` is PUBLIC (no redirect) — `next build` route map shows `○ /` (static, MarketingShell); old authed `app/page.tsx` deleted
- [x] `/app` + 9 gated routes exist under the guard — build map shows `○ /app` + `/app/{alerts,health,keys,models,routing,settings,spend,teams,usage}`; `ƒ Proxy (Middleware)` present
- [x] proxy guards /app, redirects anon — proxy.ts matcher `["/app","/app/:path*"]`; behavioral test: no-cookie /app/keys → 307 /login; cookie → next()
- [x] legacy paths GONE (404) + zero bare internal refs — independent re-grep of app/components/lib returned ZERO bare gated-path hits
- [x] MarketingShell distinct + WCAG-AA — source: skip-link first, header(banner)/nav/footer(contentinfo) landmarks, Hydroa tokens, no DashboardShell, no cookie; axe test 0 serious/critical

### Deep checks
- [x] WIRING — MarketingShell ← (marketing)/layout.tsx; MarketingRootPage at (marketing)/page.tsx; AppOverviewPage ← (app)/app/page.tsx ← OverviewPage; all referenced (confirmed by `next build` resolving every route)
- [x] DEAD-CODE — old `app/page.tsx` deleted (not orphaned); no unused new symbol (build + tsc exit 0, lint clean)
- [x] SEMANTIC — frozen contract realization note: `app/(app)/app/page.tsx` nesting is the CORRECT Next.js way to get URL `/app` (route group `(app)` is URL-transparent); build output confirms URL `/app` — contract INTENT honored, not a breach.

### Residue (non-blocking)
- Playwright real-browser a11y NOT run (needs `playwright install chromium` + a running server) — vitest axe covers structural WCAG-AA; real-browser color-contrast is standing browser-only residue (same posture as v24/v37). Not a security/concurrency/architecture residue → does not escalate the auto-gate.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (contract freeze) + orchestrator independent evidence review · date: 2026-06-24

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
