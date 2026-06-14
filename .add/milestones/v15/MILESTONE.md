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
- [ ] bff-patch-passthrough       depends-on: none                       — FIX the discovered v13 latent bug: add `export async function PATCH` to the BFF catch-all `app/api/gw/[...path]/route.ts` (mirrors PUT); route-handler test red→green. Unblocks team-budget PATCH + fixes key-governance PATCH (405 in prod). Prerequisite for teams.
- [ ] teams-governance-ui         depends-on: design-system-extension, bff-patch-passthrough — `/teams` page: team list/create/delete, set team_budget_usd, team detail with member add (BY EMAIL — Tin-approved gateway CR) + remove (all /admin/teams* + members*); owner/admin only. Includes the additive gateway add-by-email change behind its frozen contract.
- [ ] tenant-settings-ui          depends-on: design-system-extension     — `/settings` tabbed hub: Cache (GET/PUT /admin/cache toggles), Guardrails (GET/PUT /admin/guardrails — injection + PII + custom patterns), SSO/OIDC (GET/PUT /admin/oidc — client_secret WRITE-ONLY/redacted); owner-only SSO; accessible tabs + forms.
- [ ] routing-health-ui           depends-on: design-system-extension     — `/routing` page (READ-ONLY): retry policy, cooldown config, model groups, per-candidate circuit state (open/closed/unknown) from GET /admin/routing; clear health hierarchy + state patterns; owner/admin.
- [ ] governance-completion-ui    depends-on: design-system-extension, teams-governance-ui — DEPTH on v13 surfaces: key-governance editor gains rpm_limit/tpm_limit/team_id(dropdown from teams)/cache_enabled (PATCH already accepts); spend page gains group_by + key_id filter + breakdown[] table; login page gains a "Login with SSO" button (/auth/oidc/login). Data seam unchanged.
- [ ] feature-coverage-verify     depends-on: model-management-ui, teams-governance-ui, tenant-settings-ui, routing-health-ui, governance-completion-ui — milestone-exit verification: axe (zero serious/critical) + keyboard + four state patterns + responsive utilities across all NEW surfaces; the full behavioral suite stays green; the browser-only residue (color-contrast + visual breakpoints) declared, not faked. Same shape as v13 ui-ux-verify.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] The additive primitives (Switch, Tabs, Textarea, Checkbox) exist in `components/ui/*`, are token-driven + keyboard-operable + labelled, and every new surface consumes them (no ad-hoc toggle/tab) (← design-system-extension) (verify: design-system primitive render+a11y tests)
- [ ] A tenant owner/admin can enable/disable individual catalog models for their tenant and see the change persist (← model-management-ui) (verify: model-management RTL suite hitting GET/PUT /admin/models)
- [ ] A tenant owner/admin can create/list/delete teams, set a team budget, and add/remove members (← teams-governance-ui) (verify: teams RTL suite across /admin/teams* + members*)
- [ ] A tenant owner/admin can view + edit cache settings, guardrail config (injection/PII/patterns), and SSO/OIDC config (client_secret write-only) from a settings surface (← tenant-settings-ui) (verify: settings RTL suite across /admin/cache + /admin/guardrails + /admin/oidc; secret-redaction assertion)
- [ ] A tenant owner/admin can view routing/circuit-breaker health and config read-only (← routing-health-ui) (verify: routing RTL suite hitting GET /admin/routing)
- [ ] The key-governance editor exposes rpm_limit/tpm_limit/team_id/cache_enabled; the spend page offers group_by + key_id filter + a breakdown table; the login page offers SSO login (← governance-completion-ui) (verify: extended keys + spend RTL suites; login SSO-button test)
- [ ] Every new surface passes axe (zero serious/critical, color-contrast excluded), is keyboard-operable, renders the four state patterns, and uses responsive utilities; the full behavioral suite stays green; no gateway/BFF contract changed (← feature-coverage-verify; gated by it) (verify: feature-coverage-verify axe/keyboard/state/responsive suite + full `vitest run --coverage`)
- [ ] The redesigned surfaces render correctly across desktop/tablet/mobile breakpoints (← feature-coverage-verify) (verify: responsive-utility presence in jsdom; true VISUAL rendering = the carried browser-only residue from v13, same follow-up infra task)

### Carried browser-only residue (from v13, NON-security)
axe color-contrast ratios + true visual breakpoint rendering remain browser-only (jsdom has no
canvas/layout). Same declared follow-up: a real-browser axe + viewport pass (Playwright/
agent-browser + stub gateway). New surfaces honor the jsdom-provable bar; the residue is shared
with v13's, not re-litigated per task.
