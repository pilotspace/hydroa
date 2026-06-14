# MILESTONE: Dashboard feature-coverage — a surface for every backend capability

goal: a tenant owner/admin can manage every backend capability (model availability, teams & members, per-key rate/cache governance, spend breakdowns, routing health, response cache, guardrails, SSO/OIDC) through a consistent, accessible, responsive dashboard, with NO change to the gateway/BFF contracts
rationale: Intake → `sub-milestone` (confirmed by Tin 2026-06-14). A UI/UX theme slice that closes the dashboard's feature-coverage gap against the EXISTING backend — the same shape as v13 (design-system-consuming surface tasks + a verification pass). A coverage audit (file-cited) found 7 backend admin features with NO dashboard surface (model management, teams/members, routing-health, cache, guardrails, SSO/OIDC config, SSO login) and 2 PARTIAL surfaces (key-governance missing rpm/tpm/team/cache fields; spend missing group_by/key_id/breakdown). This milestone is FULL coverage (Tin: "map to EACH feature which existing in BE") of all 9. It runs NEXT (v14 Next.js-16 hardening follows). DEPTH on `apps/dashboard/` consuming the v13 design system (foundation v14) — it adds surfaces for features that already exist server-side; it adds NO new gateway data or endpoints.
stage: production · status: active · created: 2026-06-14

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Dashboard surfaces (layout + styling + a11y + responsive, all four UX lenses) mapping
     every TENANT-FACING backend admin capability, built ON the v13 design system
     (`components/ui/*` + `@theme` tokens + the four state components + AppShell), data seam
     BYTE-IDENTICAL (the BFF catch-all `/api/gw/[...path]`, same gateway endpoints/field names):
     - **Design-system EXTENSION** (freeze first): the additive primitives the new surfaces
       need — `Switch`/toggle, `Tabs`, `Textarea`, `Checkbox` (+ tokens + a11y: labelled,
       keyboard-operable, focus-visible) — so no two surfaces invent parallel toggles/tabs.
     - **Model management** (`/models`): `GET /admin/models` + `PUT /admin/models/{model_id}`
       — per-tenant enable/disable overrides on the catalog (today only the read-only
       `/v1/models` view exists on `/usage`).
     - **Teams & members** (`/teams`): full `/admin/teams*` + `/admin/teams/{id}/members*`
       — team list/create/delete, set team budget, member add/remove; surfaces the team_id
       a key can be assigned to.
     - **Routing & health** (`/routing`): `GET /admin/routing` (READ-ONLY) — retry policy,
       cooldown config, model groups, per-candidate circuit state.
     - **Tenant settings** (`/settings` hub, tabbed): cache (`GET/PUT /admin/cache`),
       guardrails (`GET/PUT /admin/guardrails` — prompt-injection + PII masking + custom
       patterns), SSO/OIDC config (`GET/PUT /admin/oidc` — client_secret write-only/redacted).
     - **Governance completion** (DEPTH on v13 surfaces): the key-governance editor gains
       `rpm_limit`/`tpm_limit`/`team_id`/`cache_enabled` (BE PATCH already accepts them); the
       spend page gains the `group_by=key_id|team_id` selector + `key_id` filter + the
       `breakdown[]` table; the login page gains a "Login with SSO" button (`/auth/oidc/login`).
     - **Verification pass**: cross-surface axe (zero serious/critical), keyboard operability,
       the four state patterns, responsive utilities, and the full behavioral floor stays green.
Out: NEW gateway data/metrics/endpoints or any gateway/BFF contract change (this is a
     presentation/coverage milestone — every endpoint it consumes ALREADY exists); the v14
     Next.js-16 dependency hardening (separate milestone, runs AFTER); a full pixel-perfect
     rebrand or design-tool (.pen) handoff (works within the v13 shadcn/token system); i18n;
     dark/light toggle beyond the dark-mode-first default; OPERATOR-only `/internal/*`
     surfaces (health/metrics/authz/catalog-sync — Envoy-guarded, never a tenant surface);
     the `/v1/*` proxy endpoints (the tenant's own runtime API, not a dashboard feature);
     a NEW gateway "list users" endpoint for member-picking unless a task proves it necessary
     (prefer existing identity/JWT-sourced user ids; if required it is a contract CHANGE-REQUEST
     back to the gateway, surfaced at that task's freeze — not silently added here).
     AMENDMENT (2026-06-14, Tin-approved CR): teams-governance-ui PROVED member-add needs a
     backend change (no list-users endpoint; add-member takes only user_id; no email in responses).
     Approved exception to "no gateway change": ADD-BY-EMAIL — POST /admin/teams/{id}/members
     accepts an `email` (server resolves via the existing tenant-scoped get_user_by_email). This is
     the ONLY sanctioned gateway change in v15; everything else stays presentation-only.

## Shared decisions & glossary deltas   (living — every task must honor these)
- Behavior-preserving / data-identical is non-negotiable (foundation v3/v13): every surface
  calls the SAME BFF route + field names; presentation/interaction only. `monthly_budget_usd`
  (per-key) ≠ `budget_usd_monthly` (tenant); `team_budget_usd` is the per-team ceiling.
- All new surfaces CONSUME the v13 design system + the four state components (loading=role=status
  · empty=Empty · error=role=alert · success=data) — no surface hardcodes a value a token covers,
  no ad-hoc toggle/tab (use the frozen extension primitives).
- Accessibility floor = WCAG 2.2 AA, enforced via the same gate as v13: axe zero serious/critical
  (color-contrast excluded in jsdom — browser residue), keyboard-operable, focus-visible. New
  interactive controls (Switch/Tabs/Checkbox) are labelled + keyboard-operable.
- RBAC visibility: owner/admin-only surfaces hide their mutating affordances from `member` (the
  `/usage` Edit-Budget precedent); SSO/OIDC config is owner-only.
- SECRET DISCIPLINE: the OIDC `client_secret` is WRITE-ONLY — never rendered back (shown as a
  stored-placeholder), never logged/echoed; same reveal-once discipline as the plaintext API key.
- Scenario observables anchor WHERE text/state appears — RTL `within(section)`, never bare global.

## Shared / risky contracts (freeze these first)
- The **design-system extension** — the additive primitive set (`Switch`, `Tabs`, `Textarea`,
  `Checkbox`) with their tokens + a11y conventions — the shared vocabulary every new surface
  consumes. Freezing it first prevents two surfaces diverging into parallel ad-hoc toggles/tabs
  → owning task `design-system-extension` (FREEZE FIRST — all surface tasks build against it).
- The **member-identity question** — teams reference `user_id`s but the gateway exposes no
  "list users" endpoint today; the teams task must resolve member-picking from existing identity
  WITHOUT a new endpoint, or raise an explicit contract CHANGE-REQUEST at its freeze → owning
  task `teams-governance-ui`. RESOLVED 2026-06-14: Tin approved the ADD-BY-EMAIL CR (see Out
  amendment) — teams-governance-ui carries this gateway change behind its own frozen contract.
- The **BFF PATCH passthrough bug** (discovered 2026-06-14 during teams grounding) — the catch-all
  `app/api/gw/[...path]/route.ts` exports GET/POST/PUT/DELETE but NOT PATCH, yet v13's
  KeyGovernanceEditor (`bffPatch /admin/keys/{id}`) AND v15 team-budget (`PATCH /admin/teams/{id}`)
  need it; in production the PATCH 405s (v13 tests passed only because msw intercepts the client
  fetch). Fix = additive `export async function PATCH` mirroring PUT → owning task
  `bff-patch-passthrough` (prerequisite for teams-governance-ui; also fixes the v13 latent bug).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] design-system-extension     depends-on: none                       — freeze the additive primitives the coverage surfaces need: Switch/toggle, Tabs, Textarea, Checkbox (+ tokens, labelled/keyboard-operable/focus-visible a11y, the four state patterns honored); prove they render + existing suites stay green. FREEZE FIRST. DONE (commit 4b13904, gate PASS).
- [x] model-management-ui         depends-on: design-system-extension     — `/models` page: list catalog models with a per-tenant enable/disable Switch (GET /admin/models, PUT /admin/models/{model_id:path}); owner/admin-only (member GET 403s → ErrorState); state patterns + responsive. DONE (commit 0b473f8, gate PASS).
- [x] bff-patch-passthrough       depends-on: none                       — FIX the discovered v13 latent bug: add `export async function PATCH` to the BFF catch-all `app/api/gw/[...path]/route.ts` (mirrors PUT); route-handler test red→green. Unblocks team-budget PATCH + fixes key-governance PATCH (405 in prod). DONE (commit f91774d, gate PASS).
- [x] teams-add-by-email          depends-on: none                       — BACKEND CR (Tin-approved): POST /admin/teams/{id}/members accepts `email` (tenant-scoped resolve to user_id via the existing UserRow lookup) in addition to `user_id`; exactly-one-of validation; unknown/cross-tenant email → 404 ERR_USER_NOT_FOUND. 5 gateway files (schemas+router+use_case+repository+domain port), no migration; pytest red→green (7 tests). DONE (gate PASS 2026-06-14, adversarial EARNED-WITH-GAPS + ISOLATION-SAFE, 2 findings closed).
- [x] teams-governance-ui         depends-on: design-system-extension, bff-patch-passthrough, teams-add-by-email — `/teams` FRONTEND page: team list/create/delete, set team_budget_usd (PATCH), team detail with member add (BY EMAIL) + remove (all /admin/teams* + members*); owner/admin only; consumes the design system + four state patterns. Presentation-only (the gateway change lives in teams-add-by-email). DONE (gate PASS 2026-06-14; 22 RTL tests, master-detail in-page, adversarial NOT-EARNED→silent-budget-PATCH DEFECT fixed red→green; 166 suite green / 92.28% cov).
- [x] tenant-settings-ui          depends-on: design-system-extension     — `/settings` tabbed hub: Cache (GET/PUT /admin/cache toggles), Guardrails (GET/PUT /admin/guardrails — injection + PII + custom patterns), SSO/OIDC (GET/PUT /admin/oidc — client_secret WRITE-ONLY/redacted); owner-only SSO; accessible tabs + forms. DONE (gate PASS 2026-06-14; 16 RTL tests, 3 lazy-mounted tab sub-forms; adversarial EARNED-WITH-GAPS, security CLEAN — write-only client_secret never enters DOM/PUT/log, now test-asserted; 2 security GAPs closed + retry:false hardening; 24 files/92.91% cov / lint clean).
- [x] routing-health-ui           depends-on: design-system-extension     — `/routing` page (READ-ONLY): retry policy, cooldown config, model groups, per-candidate circuit state (open/closed/unknown) from GET /admin/routing; clear health hierarchy + state patterns; owner/admin. DONE (gate PASS 2026-06-14; 10 RTL tests, config Cards + candidates Table with state Badge, 4-state enum incl half_open; adversarial EARNED-WITH-GAPS, ZERO DEFECTs, role-gate airtight; nav-icon aria-hidden a11y fix; 25 files/192 tests/93.08% cov / lint clean).
- [x] key-cache-enabled-fidelity  depends-on: none                       — GATEWAY BUG FIX (Tin-approved 2026-06-14): `list_keys` (keys/api/router.py:154) drops `cache_enabled` from each `KeyInfoResponse` though the domain/repository already carry the true value, so GET /admin/keys reports cache_enabled=false for ALL keys (a footgun for the editor's cache toggle: a caching-ON key shows OFF, a save could silently disable). Fix = add `cache_enabled=item.cache_enabled`; response SHAPE unchanged (field already declared, defaulted False) so it is a faithfulness bug fix, NOT a contract change. pytest red→green. Prereq for governance-completion-ui's cache toggle. DONE (gate PASS 2026-06-14; RED test_list_keys_reports_true_cache_enabled (A=true/B=false pins per-key, not constant)→1-line fix→green; full gateway suite 738 passed / 83.21% cov; ruff+pyright clean).
- [x] governance-completion-ui    depends-on: design-system-extension, teams-governance-ui, key-cache-enabled-fidelity — DEPTH on v13 surfaces (FRONTEND-only, data seam unchanged): key-governance editor gains rpm_limit/tpm_limit (clearable positive ints) + team_id (dropdown from GET /admin/teams, null=un-team) + cache_enabled (Switch) via PATCH /admin/keys/{id}; spend page gains group_by(None|key_id|team_id) + key_id filter (dropdown from GET /admin/keys) + a polymorphic breakdown[] table. SSO login split out → oidc-login-relay. DONE (gate PASS 2026-06-14; 213 suite green / 94.03% cov / lint clean; 2× adversarial sonnet refute-read caught TWO real DEFECTs: (1) KeysPage.toGovernanceKey dropped the 4 new fields → silent dense-PATCH CLEAR — fixed by wiring the ApiKey interface + adding test_list_fields_prefill_the_editor; (2) spend 422/404 wiped the prior view — fixed with keepPreviousData + last-good ref (useEffect, concurrent-safe) + breakdown !isError gate + viewData.window label. Forced touches: KeysPage.tsx (silent-clear fix, added to §5), ui-ux-verify.test.tsx (QueryClientProvider + fixture), govern.test.tsx KEY_FIXTURE type-completion — all non-weakening, frozen §3 untouched).
- [x] oidc-login-relay            depends-on: tenant-settings-ui          — SSO login initiation (Tin-approved 2026-06-14): a NEW pre-auth BFF relay route `app/api/auth/oidc/login/route.ts` that forwards the gateway's GET /auth/oidc/login 302 + Set-Cookie verbatim WITHOUT an auth check (the verbatim /api/gw/* catch-all can't — it 401s pre-auth), consistent with the existing /api/auth/{login,signup,logout,me} siblings; plus a "Login with SSO" button in components/auth/LoginForm.tsx navigating to it. NO gateway change (relay only). DONE (gate PASS 2026-06-14; 10 route+form tests, 223 suite green / 94.03% cov / lint clean; adversarial SECURITY refute-read (sonnet) = no HARD-STOP, SSRF/open-redirect/param-smuggle/response-split all traced-blocked; found D1 (verbatim 5xx body relay to a pre-auth caller) → hardened to sanitized 502 via an HONEST contract v1→v2 re-freeze (engine caught my first inline §3 edit as contract_tampered → re-opened contract, re-froze, re-snapshotted); G1 malicious-domain encode guard added).
- [x] feature-coverage-verify     depends-on: model-management-ui, teams-governance-ui, tenant-settings-ui, routing-health-ui, key-cache-enabled-fidelity, governance-completion-ui, oidc-login-relay — milestone-exit verification: axe (zero serious/critical) + keyboard + four state patterns + responsive utilities across all NEW surfaces; the full behavioral suite stays green; the browser-only residue (color-contrast + visual breakpoints) declared, not faked. Same shape as v13 ui-ux-verify. DONE (gate PASS 2026-06-14; NEW tests-bff/feature-coverage-verify.test.tsx = 8 tests consolidating AppShell a11y (skip-link first FOCUSABLE, Primary nav + main#main landmarks, 7 nav links focusable + aria-current) + axe/keyboard sweep over Models/Teams/Routing/Settings-tabs + four-state spot-check; 29 files / 231 tests green / 94.03% cov / lint clean; adversarial refute-read (sonnet) NOT-EARNED→hardened: D1 first-focusable (anchors-only→all focusable types), D2 loading-transition proof, G1 negative aria-current, G4 full switch name — all fixed via honest re-cross; D5 shared-msw-wildcard deferred as `bff-test-harness-strict-handlers` follow-up (no cheat — every surface uses explicit handlers + real fixtures). Milestone-exit suites legitimately GREEN, earned-green proven by audit not first-run RED).

## Exit criteria (observable; map each to the task that delivers it)
- [x] The additive primitives (Switch, Tabs, Textarea, Checkbox) exist in `components/ui/*`, are token-driven + keyboard-operable + labelled, and every new surface consumes them (no ad-hoc toggle/tab) (← design-system-extension) (verify: design-system primitive render+a11y tests) — DONE (commit 4b13904); Switch/Tabs/Textarea/Checkbox shipped + consumed across Models/Teams/Settings; AppShell+primitives axe-clean in feature-coverage-verify.
- [x] A tenant owner/admin can enable/disable individual catalog models for their tenant and see the change persist (← model-management-ui) (verify: model-management RTL suite hitting GET/PUT /admin/models) — DONE (commit 0b473f8); ModelsPage enable/disable Switch (GET/PUT /admin/models), optimistic+rollback, four states.
- [x] A tenant owner/admin can create/list/delete teams, set a team budget, and add/remove members (← teams-governance-ui) (verify: teams RTL suite across /admin/teams* + members*) — DONE; master-detail TeamsPage, budget PATCH, member add-by-email + remove; 22 RTL tests.
- [x] A tenant owner/admin can view + edit cache settings, guardrail config (injection/PII/patterns), and SSO/OIDC config (client_secret write-only) from a settings surface (← tenant-settings-ui) (verify: settings RTL suite across /admin/cache + /admin/guardrails + /admin/oidc; secret-redaction assertion) — DONE; tabbed SettingsPage, 16 RTL tests, client_secret write-only asserted never to enter DOM/PUT/log.
- [x] A tenant owner/admin can view routing/circuit-breaker health and config read-only (← routing-health-ui) (verify: routing RTL suite hitting GET /admin/routing) — DONE; read-only RoutingPage (retry/cooldown/groups + candidate circuit Badges), 10 RTL tests.
- [x] GET /admin/keys reports each key's true cache_enabled (← key-cache-enabled-fidelity) (verify: pytest test_list_keys_reports_true_cache_enabled) — DONE; 1-line serializer fix, RED A=true/B=false pin → green; gateway suite 738 passed.
- [x] The key-governance editor exposes rpm_limit/tpm_limit/team_id/cache_enabled; the spend page offers group_by + key_id filter + a breakdown table (← governance-completion-ui) (verify: extended keys + spend RTL suites) — DONE; editor depth fields via dense PATCH + spend group_by/key_id/breakdown; 2 real DEFECTs caught+fixed (silent-clear, prior-view-wipe).
- [x] The login page offers SSO login via a pre-auth BFF relay to GET /auth/oidc/login (← oidc-login-relay) (verify: login SSO-button test + relay route-handler test) — DONE: GET /api/auth/oidc/login forwards the gateway 302+cookies (4xx verbatim, 5xx→sanitized 502), LoginForm SSO anchor; 10 tests green.
- [x] Every new surface passes axe (zero serious/critical, color-contrast excluded), is keyboard-operable, renders the four state patterns, and uses responsive utilities; the full behavioral suite stays green; no DATA-contract change to the consumed admin endpoints (the three Tin-sanctioned touches are additive/corrective: teams-add-by-email accepts an email, key-cache-enabled-fidelity fixes a list serializer omission within the existing KeyInfoResponse shape, oidc-login-relay adds a pre-auth BFF auth-route sibling) (← feature-coverage-verify; gated by it) (verify: feature-coverage-verify axe/keyboard/state/responsive suite + full `vitest run --coverage`) — DONE; consolidated suite axe-clean across AppShell+Models+Teams+Routing+Settings, keyboard-operable controls, four-state spot-check; 231/231 green @ 94.03% cov, lint clean; the three sanctioned touches are the ONLY backend deltas (all additive/corrective).
- [x] The redesigned surfaces render correctly across desktop/tablet/mobile breakpoints (← feature-coverage-verify) (verify: responsive-utility presence in jsdom; true VISUAL rendering = the carried browser-only residue from v13, same follow-up infra task) — DONE for the jsdom-provable bar (responsive utility classes present, e.g. the lg/sm breakpoint utilities on AppShell + grids); true VISUAL breakpoint rendering = the carried browser-only residue (shared real-browser axe+viewport follow-up), declared not faked.

### Carried browser-only residue (from v13, NON-security)
axe color-contrast ratios + true visual breakpoint rendering remain browser-only (jsdom has no
canvas/layout). Same declared follow-up: a real-browser axe + viewport pass (Playwright/
agent-browser + stub gateway). New surfaces honor the jsdom-provable bar; the residue is shared
with v13's, not re-litigated per task.
