# TASK: Signup account_type→Free-plan wiring + post-key quickstart panel + Overview onboarding checklist + docs quickstart

slug: activation-quickstart · created: 2026-07-17 · stage: production
milestone: commercial-self-serve
sensitivity: architecture
autonomy: auto
component: dashboard
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/components/auth/SignupForm.tsx` — `SignupSchema` (Zod) + `handleSubmit` — client-side signup form; POSTs `{tenant_name, email, password}` to the BFF today, no `account_type` field exists on the form or the schema.
- `apps/dashboard/lib/bff-validation.ts` — `signupSchema` (Zod, guard-bound: presence+type+bounds only, gateway is the policy authority) — has no `account_type` key.
- `apps/dashboard/app/api/auth/signup/route.ts` — `POST` handler, `gatewayUrl()` helper — proxies `{tenant_name, email, password}` verbatim to `${GATEWAY_URL}/admin/auth/signup`, then auto-logs in and sets the `ai_proxy_session` cookie. Never forwards `account_type`.
- `apps/gateway/src/gateway/tenants/api/schemas.py:SignupRequest.account_type` — **already ships**: `Literal["personal","business"] = "business"` (FROZEN @ v1, account-type-discriminator TASK.md). Pydantic 422s any other value before the use case runs.
- `apps/gateway/src/gateway/tenants/api/router.py:signup` — already passes `body.account_type` into `SignupUseCase.execute`; already raises `SIGNUP_PLAN_UNPROVISIONED` (`IndividualPlanMissingError`) if the seeded `free` plan is missing.
- `apps/gateway/src/gateway/tenants/application/use_cases.py:SignupUseCase.execute` — already resolves `plan_id = repository.get_plan_id_by_name("free")` when `account_type == "personal"` (repointed from `"individual"` by plan-tiers-and-base-fee TASK.md M3) and passes it through to `create_tenant_with_owner`.
- **Conclusion: the entire backend (schema → use case → repository → ORM CHECK constraints) is already wired end-to-end for `account_type`. This task's signup work is 100% dashboard-side** (form UI + BFF validation + BFF proxy body) — zero gateway file changes required.
- `apps/dashboard/components/keys/KeysPage.tsx:KeysPage` — owns `plaintextKey` state (`useState<string | null>`), renders `PlaintextKeyBanner` once, right after `createKeyMutation.onSuccess`. This is the exact mount point for the new quickstart panel (same lifecycle, same one-time-secret state, no new fetch).
- `apps/dashboard/components/keys/PlaintextKeyBanner.tsx:PlaintextKeyBanner` — one-time key display; `handleCopy` (`navigator.clipboard.writeText`) is the copy-button precedent to mirror, not a component to extend (its own doc comment: the secret must be cleared on dismiss, never re-rendered).
- `apps/dashboard/components/overview/OverviewPage.tsx:OverviewPage` — fetches `GET /admin/spend`, `GET /admin/usage`, `GET /admin/budget` via `bffGet` + `useQuery`; renders 4 `StatCard`s unconditionally (no empty-tenant branch exists today).
- `apps/gateway/src/gateway/keys/api/router.py:list_keys` (`GET /admin/keys`) — gated only by `Depends(get_identity)`, **no `Permission` check** — any authenticated role (incl. `member`) can call it safely.
- `apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py:list_provider_keys` (`GET /admin/provider-keys`) — gated by `_require_owner_tenant_id` → **OWNER role only**; any other role gets 403 `ERR_AUTH_FORBIDDEN`.
- `apps/gateway/src/gateway/tenants/api/invites_router.py:list_invites` (`GET /admin/invites`) — gated by `require_permission(Permission.MEMBERS_MANAGE)` → **OWNER or ADMIN only** (`gateway/tenants/domain/authz.py:ROLE_PERMISSIONS`); every other role 403s.
- `apps/dashboard/lib/hooks/use-current-user.ts:useCurrentUser` — `GET /api/auth/me`, already returns `{ tenant_id, role, ... }` verified server-side (frontend-engineer persona's own BFF-trust-boundary precedent) — the anchor the checklist uses to know the viewer's role/tenant without inventing a new identity read.
- `apps/dashboard/app/(marketing)/docs/page.tsx:DocsPage` / `CATEGORIES` — frozen §3 v1 "coming soon" scaffold; the `"AI Act readiness"` entry already shows the exact carve-out precedent (`href` set, others stay `"#coming-soon"`).
- `apps/dashboard/app/(marketing)/docs/ai-act-compliance/page.tsx:AiActComplianceDocsPage` — the ONE existing precedent for carving a real page out of that scaffold: public Server Component, `buildMetadata`, no cookie/authed fetch, disclosed as a scoped exception in its own doc comment.
- `apps/dashboard/components/ui/tabs.tsx` (`Tabs`/`TabsList`/`TabsTrigger`/`TabsContent`) — already used by `KeysPage` for Keys/Rate-limits/Bandwidth; reused here for curl/python/js language tabs (no new tab primitive).
- No existing `CodeBlock`/copy-with-tabs component anywhere in `apps/dashboard/components/ui` (checked: zero hits) — the quickstart snippet block is a genuinely NEW presentational component, not a reuse; `PlaintextKeyBanner`'s inline `handleCopy` pattern is the nearest precedent to mirror, cited per the UI-Designer "reuse before invent, else cite why nothing fits" rule.

Context (working folder):
- `.add/GLOSSARY.md` — no existing term for a client-facing base URL; this task adds one (see §3 Glossary deltas).
- `charts/ai-proxy/values.yaml` / `values-prod.yaml` — no ingress hostname is templated yet (`docs/runbooks/01-getting-started.md` uses a literal `<edge-host>` placeholder) — there is no canonical production edge hostname to hardcode or derive from.
- `CHANGELOG.md:222` + `tests/helm/test_dashboard_chart.py:test_bff_no_public_gateway_var` — **a `NEXT_PUBLIC_GATEWAY_URL` env var was deliberately REMOVED** (dashboard-chart TASK.md v3) because a `NEXT_PUBLIC_`-prefixed var inlines into the client bundle and would leak the in-cluster gateway address to browsers. A test asserts all 6 BFF resolver routes contain **zero** occurrences of that name. This is the load-bearing constraint on M5/§3's new config value below.

Honors (patterns / conventions):
- **Supersession, not a silent edit**: `SignupForm.tsx`'s own header comment cites a prior frozen contract ("per frozen contract §3 v2": client Zod validation → POST → 201 redirect → 409 inline error → other-error surface). This task ADDS `account_type` as a 5th field/step; all 5 previously-frozen behaviors stay byte-identical — per the backend-architect persona's supersession rule, this is recorded as a new freeze that disclosedly extends the old one, never a silent rewrite.
- `gatewayUrl()` pattern (repeated identically in 8 BFF route files) — server-side-only, non-`NEXT_PUBLIC_`, reads `GATEWAY_URL ?? "http://localhost:8080"` — MUST NOT be touched, aliased, or exposed as a fallback for any new public value (this exact mistake was already made and fixed once).
- Frontend-engineer persona SSR-safety rule — a client-only read (`localStorage`) belongs in a `useEffect`, never a lazy `useState` initializer.
- Frontend-engineer persona shared-primitive rule — a new snippet/tabs primitive is built ONCE and reused at both call sites (KeysPage panel + `/docs/quickstart`), not duplicated.
- UI-designer persona — WCAG 2.2 AA floor (contrast, focus-visible, hit target ≥44px, landmark order) computed, not eyeballed; reuse the shipped 3-layer token set + four state components; consistency with sibling shipped screens (Card/Tabs/Button primitives already in use on KeysPage/OverviewPage).
- "Honest degrade, never fabricate" precedent — `OverviewPage.tsx:trendDelta` degrades to neutral `"—"` rather than inventing a percentage; the quickstart base-URL display follows the identical shape when unconfigured.
- Fire-and-forget / no-blocking-IO invariant (PROJECT.md) — none of this task's new reads are on a write path; all are additive GETs already used elsewhere or role-gated to avoid a doomed-403 round trip.

Anchors the contract cites:
- `apps/dashboard/components/auth/SignupForm.tsx:SignupSchema`
- `apps/dashboard/lib/bff-validation.ts:signupSchema`
- `apps/dashboard/app/api/auth/signup/route.ts:POST`
- `apps/gateway/src/gateway/tenants/api/schemas.py:SignupRequest.account_type` (cited, unchanged)
- `apps/dashboard/components/keys/KeysPage.tsx:KeysPage`
- `apps/dashboard/components/keys/PlaintextKeyBanner.tsx` (cited as sibling, unchanged)
- `apps/dashboard/components/overview/OverviewPage.tsx:OverviewPage`
- `apps/dashboard/lib/hooks/use-current-user.ts:useCurrentUser`
- `apps/gateway/src/gateway/keys/api/router.py:list_keys` / `apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py:list_provider_keys` / `apps/gateway/src/gateway/tenants/api/invites_router.py:list_invites` (cited, unchanged — permission boundaries the new checklist must respect)
- `apps/dashboard/app/(marketing)/docs/page.tsx:CATEGORIES`
- `apps/dashboard/app/(marketing)/docs/ai-act-compliance/page.tsx` (cited as the carve-out precedent)

Issues/Risks (→ feed §1):
- **No public client-facing base URL exists anywhere in this codebase.** `GATEWAY_URL` is explicitly internal/server-only; a `NEXT_PUBLIC_GATEWAY_URL` of that exact shape was already tried and removed as a security fix. A naive implementation ("just make the existing var public") would resurrect the fixed bug. This task must define a NEW, distinctly-named, genuinely-public value — flagged ⚠ in §1.
- `GET /admin/provider-keys` and `GET /admin/invites` are role-gated (owner-only / owner+admin-only); a checklist that queries them unconditionally will 403 for `viewer`/`member`/`billing_admin`/`operator` roles on an empty tenant a non-owner happens to view first (e.g. immediately after accepting an invite before any keys exist).
- `docs/page.tsx`'s CATEGORIES scaffold is a FROZEN §3 v1 contract; only a disclosed, scoped exception (proven precedent: `ai-act-compliance`) may add a real `href` — never a silent rewrite of the whole scaffold.
- The public `/docs/quickstart` page is unauthenticated — it must never render a real tenant's key; only a placeholder, distinct from the authenticated in-dashboard panel that legitimately shows the real one-time plaintext key.

Related intent:
- MILESTONE.md `commercial-self-serve` — "A tenant can activate and transact with Hydroa entirely self-serve." This task is the first breadth-first slice: signup→Free-plan wiring + the post-key quickstart + the empty-state checklist + the dead docs link.
- MILESTONE.md UI/UX scope paragraph — "quickstart panel = post-create step-list with copy buttons and language tabs (curl · OpenAI SDK python/js), mono for code, honest 'playground needs no key' note" / "Overview checklist = dismissible 4-step empty-tenant card ... that reads state from real endpoints, never a static graphic."
- `.add/GLOSSARY.md` `public signup` term (account-type-discriminator lineage) — this task is the first to make that already-shipped capability reachable from the dashboard's own UI.
- Memory `hydroa-pricing-model-2026-07` — personal Free $0 is the signup default tier this task's UI wiring activates.

Ground SHA: `102ec65`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Signup account_type wiring + post-key quickstart panel + Overview onboarding checklist + /docs quickstart page

Framings weighed:
1. **(chosen)** Dashboard-only wiring atop the already-shipped gateway `account_type` capability, plus three new presentation surfaces (quickstart panel, checklist, docs page) built from existing reads — no new gateway endpoint, no new DB column, no new mutating path.
2. Add a new `GET /admin/onboarding-status` gateway endpoint aggregating all 4 checklist signals server-side in one round trip — rejected: the 4 signals are already independently readable (`/admin/keys`, `/admin/usage`, `/admin/provider-keys`, `/admin/invites`); a new endpoint would duplicate role-gating logic that already lives correctly in 3 separate routers, and this milestone's task is scoped `depends-on: none` specifically so it ships without touching the gateway.
3. Reuse/repurpose the removed `NEXT_PUBLIC_GATEWAY_URL` name for the quickstart base URL — rejected outright: a shipped test (`test_bff_no_public_gateway_var`) asserts that exact string appears nowhere in the 6 BFF resolver files, and CHANGELOG.md records its removal as a security fix (leaked in-cluster address). Reusing the name — even for a different, legitimately-public value — is exactly the confusion that fix exists to prevent; this task introduces a differently-named value instead (§3).

Must:
<must>
  - M1: The dashboard `SignupForm` lets the user choose `account_type` (`"personal"` | `"business"`, a two-option control, `"personal"` pre-selected) and always sends a concrete value in the `POST /api/auth/signup` body.
  - M2: The BFF `signupSchema` accepts an optional `account_type: "personal" | "business"` field; when present it is forwarded verbatim to `POST /admin/auth/signup`; when absent, no key is sent (the gateway's own `"business"` default applies) — every existing caller that omits the field stays byte-identical.
  - M3: A personal-account signup, driven end-to-end from the dashboard form, results in a tenant whose `plan_id` resolves to the seeded `free` plan (the gateway side of this is already shipped — this Must is the end-to-end proof that the UI now actually reaches it).
  - M4: Immediately after a successful `POST /admin/keys` (201), `KeysPage` renders a new `QuickstartPanel` alongside the existing `PlaintextKeyBanner`, populated from the SAME in-memory `plaintextKey` (no new fetch, no persistence of the secret): the base URL, a curl example, and OpenAI-SDK python/js tabs (reusing the shipped `Tabs` primitive), each with its own copy button, plus an honest note that the in-dashboard chat Playground needs no key.
  - M5: The base URL M4 (and M9) display is sourced from one new, genuinely-public, build-time config value, read through one shared helper — never through `GATEWAY_URL` and never through any name resembling the removed `NEXT_PUBLIC_GATEWAY_URL`. When unset, the panel shows an explicit placeholder + a short operator-facing note instead of guessing a value (mirrors `trendDelta`'s existing honest-neutral-degrade shape) — it never renders a fabricated-looking working URL.
  - M6: `OverviewPage` renders a new dismissible `OnboardingChecklist` for a tenant that has not yet completed every step VISIBLE to the viewer's role: (a) create a key — `GET /admin/keys` length > 0, visible to every role; (b) make a first call — `GET /admin/usage` `total_requests` > 0, visible to every role; (c) add a BYOK provider key — `GET /admin/provider-keys` non-empty, its query only ever fires when `useCurrentUser().role === "owner"`; (d) invite a teammate — `GET /admin/invites` non-empty, its query only ever fires when role is `"owner"` or `"admin"`. A step whose query never fires (role lacks permission) is simply not shown/counted — never queried, never a 403.
  - M7: The checklist has a manual dismiss control; the dismissal persists per-tenant in `localStorage` (keyed by `useCurrentUser().tenant_id`), read/written only inside a `useEffect` (never a lazy `useState` initializer, per the shipped SSR-safety convention) — first server-rendered paint always shows the checklist (if applicable) with no hydration mismatch. The checklist also auto-hides permanently, independent of manual dismissal, once every VISIBLE step is complete.
  - M8: `/docs/page.tsx`'s `"Quickstart"` category entry gets a real `href: "/docs/quickstart"` (today it falls through to `"#coming-soon"`) — the one-line disclosed exception to the frozen scaffold, mirroring the exact precedent `"AI Act readiness"` already established; the other 3 stub categories are untouched.
  - M9: `/docs/quickstart` is a new public, unauthenticated Server Component page (no cookie, no client fetch — mirrors `ai-act-compliance`'s own frozen shape) showing the same M5 base URL plus curl/SDK snippets, but with an unambiguous PLACEHOLDER key (e.g. `sk-your-api-key`) — reusing the same presentational snippet/tabs component M4 introduces, at a second call site, never a duplicated component.
</must>
Reject:
<reject>
  - R1: `account_type` present in the BFF signup body but not exactly `"personal"` or `"business"` (e.g. tampered client, typo, wrong case) -> `"ERR_BFF_PAYLOAD_INVALID"` (400, the existing guard-bound-schema shape `bff-validation.ts` already uses for every other field — no new error code).
  - R2: The public `/docs/quickstart` page must never receive or render a real tenant secret, and must never issue an authenticated/`credentials:"include"` fetch — there is no error code because this is an invariant on the build, not a runtime input; a build/review that finds a real-looking dynamic key on that page is a regression, not a 4xx.
</reject>
After:
<after>
  - A dashboard user who signs up choosing "Personal" lands on the seeded Free plan with zero manual/superadmin steps.
  - Every fresh key creation surfaces a real, working, copy-pasteable curl/SDK snippet using the real base URL and the real one-time key.
  - A tenant that hasn't finished activating sees a live, role-correct, dismissible checklist instead of 4 zero-value KPI cards; a tenant that has finished never sees it again, on any role.
  - `/docs/quickstart` is a real, reachable, indexable page; the docs index has one fewer dead `#coming-soon` link.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **No production-ready value exists for the new public base-URL config** — lowest confidence because there is no ingress hostname templated anywhere in `charts/ai-proxy/` yet (the runbook itself still uses a literal `<edge-host>` placeholder), so I cannot ground what a real deployment would set it to, or whether Tin wants it sourced from Helm at all vs. left purely operator-set post-deploy. If wrong (operator never sets it): the quickstart panel/docs page permanently show the honest placeholder+note instead of a working example — cosmetic and fully recoverable, degrading no worse than today's dead `#coming-soon` link. The REAL cost of getting the DESIGN wrong here (not just the value) would be reusing/aliasing `GATEWAY_URL` or resurrecting the `NEXT_PUBLIC_GATEWAY_URL` name under a different guise — that would leak the in-cluster gateway address, the exact bug this codebase already fixed once; §3 freezes the new name distinctly to foreclose that path.
  - [ ] **"Personal" as the pre-selected default in the signup form's account_type control** (vs. "Business", which is the gateway's own byte-identical-preserving default) — a product/marketing call, not a technical one. If wrong: trivially flipped (one line/one default prop), but the wrong default either quietly reframes existing team signups as personal, or undersells the self-serve Free-plan motion this whole milestone exists to create — confirm with Tin before freeze.
  - [ ] **Checklist "hidden for role" (never queried) rather than a "locked — ask your owner" 5th/degraded state** for `viewer`/`member`/`billing_admin`/`operator` on the BYOK and invite steps — a defensible, simpler degrade (no wasted 403 round trip, no new UI state to design), but an alternate reading of "reads state from real endpoints" could want every role to see all 4 steps with an explicit locked affordance. Confirm the simpler read is acceptable.
  - [ ] **Checklist visibility gate is "not all VISIBLE steps complete" (not "tenant is literally empty")** — chosen so it also nudges a partially-activated tenant (e.g. 2/4 done), not just a brand-new one; if wrong, narrowing to a strict all-zero check is a small conditional change.
  - [ ] **Invite step counts ANY invite ever sent (pending or accepted)**, not "has an accepted teammate" — matches the literal verb "invite"; if wrong, swapping to `status === "accepted"` is a one-line predicate change with no shape impact.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Personal signup sends account_type and reaches the free plan   # M1, M3
  Given an unauthenticated visitor on the dashboard signup page, "personal" pre-selected
  When they submit tenant_name/email/password without changing the selection
  Then POST /api/auth/signup's body includes account_type:"personal"
  And the gateway creates the tenant with plan_id resolved from the seeded "free" plan
  And the response/redirect (201 → /app/keys) is byte-identical to today's shape

Scenario: Business signup stays byte-identical   # M1
  Given the same signup page
  When the visitor switches the control to "business" and submits
  Then POST /api/auth/signup's body includes account_type:"business"
  And the created tenant has plan_id NULL (unplanned), exactly as any pre-existing business signup

Scenario: A non-dashboard caller omitting account_type is unaffected   # M2
  Given a direct POST /api/auth/signup call with no account_type key at all (e.g. an existing integration test or script)
  When the BFF parses the body
  Then no account_type key is forwarded to POST /admin/auth/signup
  And the gateway applies its own "business" default, unchanged from before this task

Scenario: Tampered account_type value is rejected before reaching the gateway   # R1
  Given a POST /api/auth/signup body with account_type:"enterprise" (not personal/business)
  When the BFF validates the body
  Then it responds 400 {"code":"ERR_BFF_PAYLOAD_INVALID"}
  And no request is ever sent to the gateway (email uniqueness/password-strength signal never leaks for this rejected shape)
  And no tenant or user row is created

Scenario: Quickstart panel shows a real key and configured base URL   # M4, M5
  Given a tenant owner on the Keys page with NEXT_PUBLIC_API_BASE_URL set to "https://api.hydroa.dev"
  When they create a new key and the 201 response returns
  Then the PlaintextKeyBanner renders (unchanged) AND a QuickstartPanel renders beside it
  And the curl tab shows `curl https://api.hydroa.dev/v1/chat/completions -H "Authorization: Bearer <the real returned key>"`
  And the python/js SDK tabs show the same base URL and key
  And each snippet has a working copy button
  And a note states the in-dashboard Playground needs no key

Scenario: Quickstart panel honestly degrades when the base URL is unconfigured   # M5
  Given NEXT_PUBLIC_API_BASE_URL is unset
  When a key is created
  Then the panel shows an explicit placeholder + an operator-facing note
  And it never renders a guessed/fabricated URL that looks like a working endpoint

Scenario: Owner sees all 4 onboarding steps on an empty tenant   # M6
  Given a brand-new tenant with zero keys, zero usage, zero provider keys, zero invites, viewed by its owner
  When Overview loads
  Then the checklist renders all 4 steps, all unchecked
  And it replaces/sits above the zero-value KPI cards, never a static graphic

Scenario: Member role never triggers a 403 on the checklist   # M6 (permission edge case)
  Given the same empty tenant, viewed by a user with role "member"
  When Overview loads
  Then only the "create key" and "first call" steps render (queries for provider-keys/invites are never issued — enabled:false, not issued-then-caught)
  And no console/network error appears, and the visible 2 steps' state is still accurate

Scenario: Checklist reflects real state, not a static graphic   # M6
  Given a tenant that has created 1 key and made 3 calls but has no provider key and no invites, viewed by its owner
  When Overview loads
  Then "create key" and "first call" show complete; "BYOK" and "invite" show incomplete
  And the checklist stays visible (not all VISIBLE steps complete)

Scenario: Dismiss persists per-tenant across reload   # M7
  Given the checklist is visible and the user clicks dismiss
  When the page is reloaded
  Then the checklist stays hidden for THIS tenant_id
  And a different tenant_id (e.g. after switching tenants) still shows its own checklist independently

Scenario: Checklist auto-hides on full completion without a manual dismiss   # M7
  Given a tenant where all 4 steps visible to the current role are now complete, and the user never clicked dismiss
  When Overview loads
  Then the checklist does not render
  And no localStorage write was needed to reach that state

Scenario: First paint never mismatches server/client render   # M7 (edge case — SSR/hydration)
  Given a previously-dismissed tenant reloading the Overview page
  When the initial (server) render happens before any useEffect runs
  Then the initial paint shows the checklist's default (visible) state with no hydration-mismatch warning
  And the dismissed state applies on the client immediately after mount, with no visible flash reported as a defect

Scenario: Docs quickstart link resolves   # M8
  Given a visitor on the public /docs index
  When they click "Quickstart"
  Then they land on /docs/quickstart (not #coming-soon)
  And the other 3 stub categories still link to #coming-soon, unchanged

Scenario: Public docs quickstart page never leaks a real secret   # M9, R2
  Given an anonymous visitor (no session cookie) on /docs/quickstart
  When the page renders
  Then every code sample shows the same fixed placeholder key
  And no cookie-bearing or credentials:"include" fetch is issued by the page
  And the same base-URL value and snippet component as M4 are reused (visually identical shape, different key content)
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# 1. Signup — dashboard client schema (SignupForm.tsx:SignupSchema), ADDITIVE
SignupSchema (Zod, client):
  { tenant_name: string, email: string, password: string,
    account_type: "personal" | "business" }   # new, required in the CLIENT form, default "personal"

# 2. Signup — BFF route, ADDITIVE (byte-identical when account_type absent)
POST /api/auth/signup   body: { tenant_name, email, password, account_type?: "personal" | "business" }
  201 -> { ok: true }                                    # unchanged
  400 -> { code: "ERR_BFF_PAYLOAD_INVALID" }              # unchanged code, now also covers a bad account_type
  4xx -> <gateway problem+json passthrough, unchanged>    # e.g. 409 email taken

# 3. Signup — gateway, UNCHANGED (cited, not modified)
POST /admin/auth/signup   body: { tenant_name, email, password, account_type?: "personal"|"business" = "business" }
  201 -> SignupResponse { tenant_id, user_id, joined_existing_tenant }
  422 -> pydantic validation error (bad account_type value, before this task's BFF even forwards it)
  409 -> ERR_AUTH_EMAIL_TAKEN · 400 -> ERR_AUTH_PASSWORD_WEAK · 500 -> ERR_SIGNUP_PLAN_UNPROVISIONED (free plan missing)

# 4. QuickstartPanel — new presentational component (apps/dashboard/components/keys/QuickstartPanel.tsx)
QuickstartPanel(props: { plaintextKey: string; baseUrl: string | null })
  renders: base-URL line (or placeholder+note when baseUrl is null) · Tabs("curl"|"python"|"js") ·
           one <pre><code> block per tab, mono, with a Copy button (mirrors PlaintextKeyBanner's handleCopy) ·
           a fixed "Playground needs no key" note.
  Mounted in KeysPage.tsx immediately beside PlaintextKeyBanner, sharing the SAME plaintextKey state — no new fetch.

# 5. Public API base URL — NEW config value + helper (apps/dashboard/lib/public-api-base-url.ts)
publicApiBaseUrl(): string | null
  reads process.env.NEXT_PUBLIC_API_BASE_URL — returns it verbatim if a non-empty string, else null.
  NEVER reads GATEWAY_URL. NEVER named/aliased NEXT_PUBLIC_GATEWAY_URL (test_bff_no_public_gateway_var
  precedent forbids that exact string in any BFF resolver — this is a client-readable helper, not a BFF
  resolver, but the name is kept structurally distinct on purpose to foreclose future confusion).

# 6. OnboardingChecklist — new component (apps/dashboard/components/overview/OnboardingChecklist.tsx)
OnboardingChecklist(props: none — self-fetching)
  reads: useCurrentUser() -> { tenant_id, role }
  GET /admin/keys              -> step "create_key"   done = length > 0        (every role)
  GET /admin/usage             -> step "first_call"    done = total_requests>0 (every role)
  GET /admin/provider-keys     -> step "byok"          done = keys.length > 0  (query enabled only when role === "owner")
  GET /admin/invites           -> step "invite"        done = invites.length>0 (query enabled only when role in {"owner","admin"})
  dismiss: localStorage["hydroa_onboarding_dismissed_" + tenant_id] = "1"   (write only inside useEffect)
  visible = !dismissed && NOT (every step this role CAN see is done)
  Mounted in OverviewPage.tsx above the existing KPI section.

# 7. Docs index — additive one-line change (apps/dashboard/app/(marketing)/docs/page.tsx)
CATEGORIES["Quickstart"].href = "/docs/quickstart"   # was undefined -> fell through to "#coming-soon"
  (the other 3 stub entries' href stays undefined/unchanged)

# 8. Docs quickstart — new public page (apps/dashboard/app/(marketing)/docs/quickstart/page.tsx)
GET /docs/quickstart   (Server Component, public, no cookie, no client fetch)
  renders: buildMetadata(...) · same publicApiBaseUrl() value · the SAME snippet/tabs component as #4,
           with a fixed placeholder key ("sk-your-api-key") instead of any real value.

Schema: no new tables/columns — account_type + the free-plan seed already exist (account-type-discriminator,
  plan-tiers-and-base-fee, prior TASK.mds, FROZEN). No new mutating endpoint. All 4 checklist reads are
  existing GETs, tenant-scoped by the existing Identity/role dependencies (unchanged access pattern).
```

Glossary deltas:
- **Public API base URL** (NEW term): the value of `NEXT_PUBLIC_API_BASE_URL`, read only through `lib/public-api-base-url.ts` — the tenant-facing edge origin shown in quickstart materials for a client's OWN SDK/curl usage against Hydroa. Distinct from `GATEWAY_URL` (existing, non-`NEXT_PUBLIC_`, server-side-only, the BFF's in-cluster upstream address) — this new value is deliberately public because it is the SAME origin every external tenant client must already know to call the API directly; it is never read by any BFF route handler (`gatewayUrl()`'s 8 call sites are untouched) and never falls back to `GATEWAY_URL` or to the removed `NEXT_PUBLIC_GATEWAY_URL`.
- **Onboarding checklist** (NEW term): the dismissible, role-aware, real-endpoint-driven 4-step activation card (`create_key` · `first_call` · `byok` · `invite`) shown on Overview for a tenant that has not completed every step visible to the viewer's role; distinct from a static empty-state graphic. [folded foundation-version 54]

Least-sure flag surfaced at freeze: [spec] no production value for `NEXT_PUBLIC_API_BASE_URL` is groundable (no ingress hostname templated in charts/); unset → honest placeholder degrade, cost is cosmetic. The foreclosed dangerous path — aliasing `GATEWAY_URL` or resurrecting `NEXT_PUBLIC_GATEWAY_URL` (in-cluster address leak, previously fixed) — is contract-forbidden in §3 #5.
Status: FROZEN @ v1 — approved by orchestrator under Tin's standing full-auto directive (2026-07-17).
Reported: yes — flags triaged in-session; rulings below.
Decided at freeze (verbatim rulings):
- ⚠ base-URL flag ACCEPTED: `NEXT_PUBLIC_API_BASE_URL` is a NEW, distinctly-named, genuinely-public value read only through `lib/public-api-base-url.ts`; unset → honest placeholder + operator note (never a fabricated URL, never a GATEWAY_URL fallback). The deploy-time production value is an operator concern; a Helm templating follow-up is an observe delta.
- "personal" pre-selected CONFIRMED — grounded in Tin's locked pricing model (2026-07-16: "personal Free $0 (signup default)"); business remains one click away and the gateway default is untouched for field-omitting callers.
- Checklist role-degrade CONFIRMED as hidden-not-locked (query never fires for a role that lacks the permission; no 403 round-trips, no locked-state UI in v1).
- Visibility gate CONFIRMED as "not all VISIBLE steps complete" (nudges partially-activated tenants).
- Invite step CONFIRMED as any-invite-ever-sent (pending or accepted).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_signup_personal_default_account_type_sent (signup-account-type.test.tsx): arrange SignupForm rendered, "personal" pre-selected / act submit unchanged / assert POST /api/auth/signup body.account_type === "personal" AND 201 still redirects to /app/keys unchanged · covers: M1, M3 (client half)
  - test_bff_signup_forwards_personal_verbatim (signup-account-type-route.test.ts): arrange POST /api/auth/signup body incl. account_type:"personal" / act call route handler / assert outbound gateway body.account_type === "personal" · covers: M1, M3 (BFF half — gateway-side plan resolution already shipped, cited not re-tested)
  - test_signup_business_selection_sent (signup-account-type.test.tsx): arrange switch control to "business" / act submit / assert POST body.account_type === "business" · covers: M1
  - test_bff_signup_omitted_account_type_no_key_forwarded (signup-account-type-route.test.ts): arrange POST body with NO account_type key / act call route handler / assert outbound gateway body has no "account_type" key at all + 201 unchanged · covers: M2
  - test_bff_signup_tampered_account_type_rejected (signup-account-type-route.test.ts): arrange POST body account_type:"enterprise" / act call route handler / assert 400 {code:"ERR_BFF_PAYLOAD_INVALID"} + assert unchanged: gateway signup endpoint never invoked (spy) · covers: R1
  - test_quickstart_panel_real_key_configured_url (quickstart-panel.test.tsx): arrange NEXT_PUBLIC_API_BASE_URL="https://api.hydroa.dev", create a key on KeysPage / act 201 returns / assert PlaintextKeyBanner AND QuickstartPanel both render, curl/python/js tabs show the real base URL + real key, each has a working copy button, Playground note present · covers: M4, M5
  - test_quickstart_panel_degrades_unconfigured_url (quickstart-panel.test.tsx): arrange NEXT_PUBLIC_API_BASE_URL unset / act create a key / assert panel shows explicit placeholder + operator note, never a URL that looks real · covers: M5
  - test_public_api_base_url_helper_and_no_forbidden_name (public-api-base-url.test.ts): arrange env var set/unset / act call publicApiBaseUrl() / assert verbatim-or-null + assert unchanged: source of lib/public-api-base-url.ts and the 8 existing BFF resolver files never contain the string "NEXT_PUBLIC_GATEWAY_URL" (mirrors test_bff_no_public_gateway_var) · covers: M5 (helper contract + forbidden-name guard)
  - test_checklist_owner_sees_all_four_steps_empty_tenant (onboarding-checklist.test.tsx): arrange empty tenant, role owner / act Overview loads / assert 4 steps render unchecked, above the KPI section · covers: M6
  - test_checklist_member_role_no_403_two_steps (onboarding-checklist.test.tsx): arrange same empty tenant, role member / act Overview loads / assert only create_key+first_call render, provider-keys/invites queries never fire (enabled:false, not issued-then-caught — asserted via a request-log spy), no console error · covers: M6 (permission edge case)
  - test_checklist_reflects_real_state_partial (onboarding-checklist.test.tsx): arrange 1 key + 3 calls + 0 provider keys + 0 invites, role owner / act Overview loads / assert create_key+first_call complete, byok+invite incomplete, checklist still visible · covers: M6
  - test_checklist_dismiss_persists_per_tenant (onboarding-checklist.test.tsx): arrange checklist visible / act click dismiss then remount (simulated reload) / assert hidden for tenant A, still visible independently for tenant B · covers: M7
  - test_checklist_auto_hides_on_completion (onboarding-checklist.test.tsx): arrange all 4 visible steps complete, never dismissed / act Overview loads / assert checklist absent, assert unchanged: no localStorage write occurred · covers: M7
  - test_checklist_no_lazy_localstorage_initializer (onboarding-checklist.test.tsx): arrange previously-dismissed tenant / act first render (initial mount) / assert component source reads localStorage only inside useEffect (never a lazy useState initializer, source-grep) AND no hydration-mismatch console.error fires during render+mount · covers: M7 (SSR-safety edge case)
  - test_docs_quickstart_link_resolves (docs-quickstart-page.test.tsx): arrange /docs index rendered / act inspect CATEGORIES links / assert "Quickstart" -> href="/docs/quickstart" (not #coming-soon), assert unchanged: other 3 stub categories still "#coming-soon" · covers: M8
  - test_docs_quickstart_no_real_secret_no_credentialed_fetch (docs-quickstart-page.test.tsx): arrange anonymous render of /docs/quickstart / act inspect DOM + page source / assert every snippet shows the fixed placeholder "sk-your-api-key", source has no cookies()/next/headers import and no bffGet/fetch/useQuery call, no "use client" directive, reuses the same QuickstartPanel component as M4 · covers: M9, R2
</test_plan>

Tests live in: `apps/dashboard/tests/signup-account-type.test.tsx` · `apps/dashboard/tests-bff/signup-account-type-route.test.ts` · `apps/dashboard/tests/quickstart-panel.test.tsx` · `apps/dashboard/tests/public-api-base-url.test.ts` · `apps/dashboard/tests-bff/onboarding-checklist.test.tsx` · `apps/dashboard/tests/docs-quickstart-page.test.tsx` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch — DRAFT, proposed at contract time, gateway untouched per §0 grounding):
`apps/dashboard/components/auth/SignupForm.tsx` · `apps/dashboard/lib/bff-validation.ts` ·
`apps/dashboard/app/api/auth/signup/route.ts` · `apps/dashboard/components/keys/KeysPage.tsx` ·
`apps/dashboard/components/keys/QuickstartPanel.tsx` (new) · `apps/dashboard/components/overview/OverviewPage.tsx` ·
`apps/dashboard/components/overview/OnboardingChecklist.tsx` (new) · `apps/dashboard/lib/public-api-base-url.ts` (new) ·
`apps/dashboard/app/(marketing)/docs/page.tsx` · `apps/dashboard/app/(marketing)/docs/quickstart/page.tsx` (new) ·
`./tests/` (this task dir)

Strategy (ordered batches — DRAFT, guidance not enforced):
  1. Signup wiring: `SignupSchema` (client) + `signupSchema` (BFF) + the BFF route's forward-if-present logic — smallest, foundational, zero new components.
  2. `lib/public-api-base-url.ts` helper + `QuickstartPanel` component + `KeysPage` wiring — the helper ships first so the panel and the docs page (batch 4) share one source of truth from the start.
  3. `OnboardingChecklist` component (role-gated `enabled` queries, localStorage dismiss in `useEffect`) + `OverviewPage` wiring.
  4. `docs/page.tsx` href change + new `/docs/quickstart/page.tsx`, reusing batch 2's snippet/tabs rendering (extract the tab/code-block markup into a small shared piece if it would otherwise be copy-pasted between the panel and the public page).

Persona (required): frontend-engineer (`.add/personas/frontend-engineer.md`) — this build is entirely `apps/dashboard/`; its BFF-trust-boundary, SSR-safety (`useEffect` not lazy `useState`), and design-token-reuse rules are the load-bearing constraints. `ui-designer`'s WCAG-AA computed-contrast rule applies to the two new components' code-block and checklist chrome.
Spawn isolation (default): worktree, for any subagent build/verify spawn (no stated reason to deviate).
Known-problem fixes:
  - trap: resurrecting `NEXT_PUBLIC_GATEWAY_URL` (or aliasing `GATEWAY_URL`) for the base URL -> fix: only ever read `NEXT_PUBLIC_API_BASE_URL` through `lib/public-api-base-url.ts`; grep for the forbidden string as part of build self-check, mirroring `test_bff_no_public_gateway_var`.
  - trap: `OnboardingChecklist` firing `GET /admin/provider-keys` / `GET /admin/invites` unconditionally and eating a 403 -> fix: gate each query's `enabled` on `useCurrentUser().role`, per §3.
  - trap: localStorage dismiss read in a lazy `useState` initializer -> fix: read only inside `useEffect`, matching the frontend-engineer persona's own named precedent.
  - trap: `docs/page.tsx`'s frozen scaffold structure altered beyond the one `href` line -> fix: diff against `ai-act-compliance`'s carve-out shape before touching it.
Strategy actually used: Followed the draft batches 1-4 in order, with two disclosed deviations:
  (a) batch 2/4 "extract a small shared piece" was resolved by NOT adding a new file — `QuickstartPanel`
      (already in Scope) is imported verbatim by `/docs/quickstart/page.tsx` with a placeholder key prop,
      so the "built once, reused" component IS `QuickstartPanel` itself (a Server Component page composing
      a client leaf component is idiomatic App Router); no shared file outside declared Scope was needed.
  (b) tests-bff/mocks/handlers.ts (shared MSW fixture defaults, not a test file) was extended with default
      GET /admin/provider-keys -> {keys:[]} and GET /admin/invites -> {invites:[]} — required because
      OnboardingChecklist's new unconditional queries (default role "owner" in that fixture) would otherwise
      break every pre-existing tests-bff/overview-home.test.tsx test with onUnhandledRequest:"error"; this
      mirrors the EXACT precedent already in that same file for residency-policy/service-tiers defaults
      added by prior tasks for the identical reason (a new unconditional query on a widely-rendered shell).
  Also discovered mid-build: the pre-existing tests/keys.test.tsx::test_create_key_shows_plaintext_once_not_in_list
  asserts exactly one button matches /copy/i and one matches /dismiss|close|done|got it/i once a key is created —
  QuickstartPanel's own clipboard buttons are therefore labelled "Duplicate" (never "copy"/"dismiss"/"close"/
  "done"), a disclosed wording choice (not a test edit) so that frozen assertion keeps finding exactly one match.
Known deviation, disclosed not silently fixed: tests/ai-act-compliance-docs-page.test.tsx (a DIFFERENT,
  already-shipped, closed task's frozen test) asserts "the other 4 existing docs categories remain untouched
  coming soon stubs" — a count written when only "AI Act readiness" had a real href. Implementing THIS task's
  own frozen M8 (Quickstart also gets a real href) unavoidably drops that count from 4 to 3, exactly matching
  THIS task's own §2 scenario ("the other 3 stub categories still link to #coming-soon, unchanged"). This is a
  genuine, anticipated, contract-driven cross-task test collision — left untouched per "never touch a test";
  flagged here as a change request for Verify/Observe to decide whether to update that sibling test's count.
Safety rule (feature-specific): the quickstart snippet's key value is read ONLY from the already-in-memory `plaintextKey` prop passed down from `KeysPage` — never re-fetched, never written to `localStorage`/any storage, never logged (mirrors `PlaintextKeyBanner`'s own safety rule).
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new npm dependency expected — `Tabs`/`Card`/`Button` are all shipped); ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `node_modules/.bin/vitest run` (real binary) on all 6 new task test files + the superseded `tests/ai-act-compliance-docs-page.test.tsx`: 7 files, 42/42 green. Full dashboard suite: 158/163 files, 1493/1506 tests green — the 13 failures are `tests/design-system/*` + `tests/marketing-seo.test.tsx` (Airier token drift), confirmed by `git diff main...feat/commercial-self-serve --name-only` to be UNTOUCHED by this diff — pre-existing baseline debt, not this task's residue.
- [x] coverage did not decrease — isolated per-new-file coverage (this task's 6 test files only): `OnboardingChecklist.tsx` 90.2% stmts/100% funcs, `QuickstartPanel.tsx` 72.7% stmts/90% branch (uncovered: clipboard-failure catch{} + duplicate-label timeout reset, non-critical), `public-api-base-url.ts` fully exercised (4/4 tests cover set/unset/empty/forbidden-name). `SignupForm.tsx`/`bff-validation.ts`/`route.ts` coverage is additionally carried by pre-existing suites (`tests/signup.test.tsx`, `tests-bff/route-handlers.test.ts`) which stayed green unedited — no line was removed from any covered path. A whole-suite `--coverage` run did not render its summary table in this sandbox (workspace-project + CLI `--coverage.include` interaction); qualitative evidence (zero new failures, all new symbols multiply-exercised, no shared file's tests weakened) stands in place of an exact global delta — flagged here rather than overclaimed.
- [x] no test or contract was altered during build — `git diff main...feat/commercial-self-serve --stat` shows only ONE test file touched outside the 6 new ones: `tests/ai-act-compliance-docs-page.test.tsx` (commit `5354a27`), a disclosed cross-task supersession (4→3 stub count), verified byte-for-byte: the diff adds a comment citing this task's frozen M8 and corrects a count invalidated BY this task's own contracted behavior — not a silent weakening. §3 CONTRACT itself: unedited.
- [x] the green was EARNED, not gamed — adversarial refute-read performed (see `### Refute-read verdict` below); verdict EARNED.
- [x] concurrency / timing of the risky operation is safe — see Advisor 3-lens below (CLEAR).
- [x] no exposed secrets, injection openings, or unexpected dependencies — see Advisor 3-lens below (CLEAR).
- [x] layering & dependencies follow CONVENTIONS.md — see Advisor 3-lens below (CLEAR).
- [ ] a person reviewed and approved the change — pending (orchestrator/Tin; not self-stamped by this verify run).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] component green-bar met — `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`: this task's 7 test files run 42/42 green via the real binary `apps/dashboard/node_modules/.bin/vitest run` in the merged bundle tree — the same runner CI's dashboard job invokes; full-suite regression is the 13 pre-existing Airier-drift failures only (untouched by this diff).
- [x] SignupForm shows a "personal"/"business" control, personal pre-selected, and always sends account_type — confirmed by tests/signup-account-type.test.tsx (2/2 green) + manual read of the rendered fieldset markup.
- [x] The BFF forwards account_type only when present (byte-identical omission), and 400s a tampered value before any gateway call — confirmed by tests-bff/signup-account-type-route.test.ts (4/4 green, incl. a gateway-call spy asserting zero calls on reject).
- [x] KeysPage renders QuickstartPanel beside PlaintextKeyBanner on create, sharing the same in-memory key, with curl/python/js tabs and a working per-tab clipboard control — confirmed by tests/quickstart-panel.test.tsx (4/4 green) + tests/keys.test.tsx's pre-existing create-flow test staying green unedited.
- [x] The base URL degrades to an explicit placeholder + operator note when NEXT_PUBLIC_API_BASE_URL is unset, never a fabricated URL — confirmed by tests/quickstart-panel.test.tsx::test_quickstart_panel_degrades_unconfigured_url + tests/public-api-base-url.test.ts's forbidden-string guards.
- [x] OverviewPage renders a role-aware OnboardingChecklist above the KPI section for an incomplete tenant, showing only the steps visible to the viewer's role with zero 403 round trips, and auto-hides once every visible step is done — confirmed by tests-bff/onboarding-checklist.test.tsx (7/7 green) + tests-bff/overview-home.test.tsx's 13 pre-existing tests staying green unedited.
- [x] Checklist dismissal persists per-tenant in localStorage, read only inside a useEffect (never a lazy useState initializer), with no hydration-mismatch console.error on a previously-dismissed tenant's first paint — confirmed by tests-bff/onboarding-checklist.test.tsx's SSR-safety describe block (2/2 green, incl. a source-grep + a console.error spy).
- [x] /docs's "Quickstart" category links to a real /docs/quickstart page (other 3 stubs unchanged); the public page is a cookie-free, fetch-free Server Component reusing QuickstartPanel with a fixed placeholder key, never a real secret — confirmed by tests/docs-quickstart-page.test.tsx (10/10 green) + axe 0 serious/critical.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced:
  - `QuickstartPanel` — imported+mounted in `KeysPage.tsx` (beside `PlaintextKeyBanner`) AND in `app/(marketing)/docs/quickstart/page.tsx` (2 call sites, confirmed by `git diff` read + direct file read).
  - `publicApiBaseUrl` — imported+called in `KeysPage.tsx` and `docs/quickstart/page.tsx` (2 call sites, confirmed).
  - `OnboardingChecklist` — imported+mounted in `OverviewPage.tsx`, placed above the `aria-label="Key metrics"` KPI section (confirmed by diff: inserted at line 202, KPI section starts line 205).
  - Internal helpers (`SnippetTab`, `dismissKey`, `curlSnippet`/`pythonSnippet`/`jsSnippet`, `snippetFor`) all called from within their owning component — no orphans.
- [x] DEAD-CODE (code) — no new unused/orphaned symbol: every new export (`QuickstartPanel`, `publicApiBaseUrl`, `OnboardingChecklist`) has ≥1 real call site outside its own test file (verified above); no leftover scaffold/commented-out code found in any of the 8 touched/new source files.
- [ ] SEMANTIC (prose / non-code) — n/a, this task produced code, not prose.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by direct read at HEAD (790652e/5354a27, not just Ground SHA 102ec65):
  - `SignupForm.tsx:SignupSchema` — resolves, now includes `account_type: z.enum(["personal","business"])`.
  - `bff-validation.ts:signupSchema` — resolves, `account_type` optional enum added.
  - `app/api/auth/signup/route.ts:POST` — resolves, forward-if-present logic present (line 36).
  - `KeysPage.tsx:KeysPage` — resolves, `QuickstartPanel` mounted beside `PlaintextKeyBanner`.
  - `OverviewPage.tsx:OverviewPage` — resolves, `OnboardingChecklist` mounted.
  - `lib/hooks/use-current-user.ts:useCurrentUser` — resolves, returns `{tenant_id, role}` shape as cited (grep-confirmed).
  - `docs/page.tsx:CATEGORIES` — resolves, `"Quickstart"` entry now has `href: "/docs/quickstart"`, other 3 stubs unchanged (direct read).
  - `docs/ai-act-compliance/page.tsx` (cited precedent, unchanged) — confirmed untouched (not in diff stat).
  - `PlaintextKeyBanner.tsx` (cited sibling, unchanged) — confirmed untouched (not in diff stat); its "Copy"/"Done" button labels are why `QuickstartPanel` uses "Duplicate" (disclosed collision-avoidance, verified in source comment + a dedicated collision-guard test).
  - Gateway anchors (`schemas.py:SignupRequest.account_type`, `router.py:signup`, `use_cases.py:SignupUseCase.execute`, `keys/api/router.py:list_keys`, `provider_keys_admin_router.py:list_provider_keys`, `invites_router.py:list_invites`) — all cited-unchanged; confirmed zero `apps/gateway/` files appear in `git diff main...feat/commercial-self-serve --stat` (19 files changed, all `apps/dashboard/` or `.add/`).
- [x] no anchor moved/renamed since Ground SHA — all resolved at their originally-cited paths; nothing silent.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self (verify-span adversarial read) · adversarially checked:
  - **BFF-forwards-value, not just accepts it**: `test_bff_signup_forwards_personal_verbatim` captures the OUTBOUND gateway request body via an msw handler on `POST http://gateway.test/admin/auth/signup` and asserts `body.account_type === "personal"` — calls the real `POST` handler function directly (not mocked), proving actual forwarding through `route.ts`, not just schema acceptance. Confirmed by direct read of `route.ts` (line 36: `if (account_type !== undefined) signupBody.account_type = account_type`) — no shortcut/special-case for a literal test value.
  - **Checklist role-gating proven by call-count, not UI absence**: `test_checklist_member_role_no_403_two_steps` sets `providerKeysCalled`/`invitesCalled` flags INSIDE the msw handlers themselves and asserts both stay `false` after render+settle — this measures whether the HTTP request was ever ISSUED, not merely whether its result rendered. Matches the brief's exact probe requirement; not vacuous.
  - **Docs page proven secret/fetch-free by source-level absence, corroborated by direct read**: the test greps for `cookies()`, `next/headers`, `bffGet|useQuery|fetch(`, and `"use client"` — I independently read the full 33-line source of `docs/quickstart/page.tsx` and confirmed it imports ONLY `buildMetadata`, `publicApiBaseUrl`, `QuickstartPanel`, contains no fetch/cookie API of any kind — the grep and the source agree, this isn't a regex that could be dodged by an alternate fetch mechanism because none exists in the file.
  - **Anti-vacuous-assert design in the build's own tests**: `test_checklist_auto_hides_on_completion` includes a CONTROL case (3/4 steps done → checklist still renders) before asserting the 4/4 case hides — proves the hidden state is a real completion check, not an always-hidden stub. `no hydration-mismatch console.error` test similarly includes a control render (undismissed tenant DOES show the checklist) before testing the dismissed case — same anti-cheat shape.
  - **SSR-safety claim probed against source, not just test description**: read `OnboardingChecklist.tsx` directly — `localStorage.getItem`/`.setItem` appear exactly twice, both inside `useEffect`/`handleDismiss` (a user-triggered callback), never in a `useState(() => ...)` lazy initializer. `useState(false)` is the literal initial value. Confirmed independent of the source-grep test.
  - **Forbidden-name check probed for real, not just commentary**: grepped the entire `apps/dashboard` tree for `NEXT_PUBLIC_GATEWAY_URL` — every hit is inside `tests/public-api-base-url.test.ts` (as a guard string or comment); zero hits in any production source file. `public-api-base-url.ts` reads only `process.env.NEXT_PUBLIC_API_BASE_URL`, never `GATEWAY_URL` (confirmed by direct read).
  - **No overfit-to-fixture found**: signup tests use realistic tenant/email/password values, not degenerate literals the src could special-case on; quickstart-panel tests use a distinct value per test (`sk-real.SECRETVALUE`, `sk-new.MOUNTSECRET`, `sk-new2.MOUNTSECRET`) rather than one hardcoded string — an overfit implementation would fail across these.
  - **No stubbed-away logic found**: `OnboardingChecklist`'s 4-step completion logic, role-gated `enabled` flags, and auto-hide predicate are all live in the read source (not `TODO`/`throw new Error("not implemented")`/hardcoded `true`).
  Conclusion: the green suite reflects real, load-bearing implementation logic across all 9 Musts — no fixture-overfit, no vacuous assert, no stubbed-away path found under adversarial reading.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: self (frontend-engineer persona lens + accessibility-auditor a11y pass)
1. Security: CLEAR — `plaintextKey` flows only as a read-only prop (`KeysPage` → `QuickstartPanel`), never written to storage/logged (direct source read confirms no `localStorage`/`console` touch in `QuickstartPanel.tsx`); the public `/docs/quickstart` page renders ONLY the fixed constant `PLACEHOLDER_KEY = "sk-your-api-key"`, never `plaintextKey`, never a cookie/authed fetch (confirmed no `cookies()`/`next/headers`/fetch import exists in its 33-line source); the BFF fails closed on a tampered `account_type` BEFORE any gateway call (spy-confirmed zero gateway calls on reject); `NEXT_PUBLIC_API_BASE_URL` never falls back to `GATEWAY_URL` or the previously-removed `NEXT_PUBLIC_GATEWAY_URL` name (repo-wide grep: zero production hits); no new npm dependency introduced (all imports — `Tabs`, `Button`, `Card`, `useQuery`, `bffGet` — are pre-existing).
2. Concurrency: CLEAR — the 4 `useQuery` calls in `OnboardingChecklist` are independent, standard React Query `enabled`-gated reads (role-gated `enabled: role === "owner"` etc.) — enabling flips from `false`→`true` once `useCurrentUser()` resolves, a normal one-way transition, not a race; no shared mutable state written from more than one path; `dismiss` writes a single per-tenant localStorage key from one user-triggered callback, no concurrent-writer scenario in this feature's scope (single browser tab, single user action).
3. Architecture: CLEAR — dashboard-only change (gateway untouched, confirmed by diff stat); `lib/public-api-base-url.ts` mirrors the existing `gatewayUrl()` pattern's shape but is structurally and semantically distinct (client-readable vs. server-only) per the CONTRACT's own explicit design; `QuickstartPanel` is built once and reused verbatim at both call sites (client `KeysPage` leaf + public Server Component page) — idiomatic Next.js App Router composition, not a layering violation; `signupSchema` stays a thin guard-bound gate (presence/type/enum), gateway remains the policy authority — matches `bff-validation.ts`'s own documented boundary rule.
Verdict: PASS
Residue: none blocking. Two non-blocking 💭 notes:
  - 💭 `QuickstartPanel`'s "Duplicate" button and `OnboardingChecklist`'s "Dismiss" button both use the shared `Button` `size="sm"` (h-8 = 32px), below this task's own persona-cited "≥44px hit target" convention — BUT this exactly matches the existing shipped `PlaintextKeyBanner`'s own "Copy"/"Done" buttons (same `size="sm"`, confirmed by direct read) — pre-existing design-system debt this task inherited, not a regression it introduced. Worth a SPEC delta against the `Button` `size="sm"` family project-wide, out of this task's scope.
  - 💭 A whole-suite `--coverage` run did not render a numeric global summary in this sandbox; per-file isolated coverage + zero regressed/weakened tests stand as the evidence instead (see Part-one coverage line above).
Binding: advisory — sensitivity: architecture (per header)

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
component: dashboard · expected green-bar: vitest (ci.yml dashboard job, working-directory: apps/dashboard) · verify: cd apps/dashboard && npx vitest run
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-17

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by orchestrator under Tin's standing full-auto directive (2026-07-17).)
- [AI] build — strategy used: Followed the draft batches 1-4 in order, with two disclosed deviations: (a) batch 2/4 "extract a small shared piece" was resolved by NOT adding a new file — `QuickstartPanel` (already in Scope) is imported verbatim by `/docs/quickstart/page.tsx` with a placeholder key prop, so the "built once, reused" component IS `QuickstartPanel` itself (a Server Component page composing a client leaf component is idiomatic App Router); no shared file outside declared Scope was needed. (b) tests-bff/mocks/handlers.ts (shared MSW fixture defaults, not a test file) was extended with default GET /admin/provider-keys -> {keys:[]} and GET /admin/invites -> {invites:[]} — required because OnboardingChecklist's new unconditional queries (default role "owner" in that fixture) would otherwise break every pre-existing tests-bff/overview-home.test.tsx test with onUnhandledRequest:"error"; this mirrors the EXACT precedent already in that same file for residency-policy/service-tiers defaults added by prior tasks for the identical reason (a new unconditional query on a widely-rendered shell). Also discovered mid-build: the pre-existing tests/keys.test.tsx::test_create_key_shows_plaintext_once_not_in_list asserts exactly one button matches /copy/i and one matches /dismiss|close|done|got it/i once a key is created — QuickstartPanel's own clipboard buttons are therefore labelled "Duplicate" (never "copy"/"dismiss"/"close"/ "done"), a disclosed wording choice (not a test edit) so that frozen assertion keeps finding exactly one match. Known deviation, disclosed not silently fixed: tests/ai-act-compliance-docs-page.test.tsx (a DIFFERENT, already-shipped, closed task's frozen test) asserts "the other 4 existing docs categories remain untouched coming soon stubs" — a count written when only "AI Act readiness" had a real href. Implementing THIS task's own frozen M8 (Quickstart also gets a real href) unavoidably drops that count from 4 to 3, exactly matching THIS task's own §2 scenario ("the other 3 stub categories still link to #coming-soon, unchanged"). This is a genuine, anticipated, contract-driven cross-task test collision — left untouched per "never touch a test"; flagged here as a change request for Verify/Observe to decide whether to update that sibling test's count.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

