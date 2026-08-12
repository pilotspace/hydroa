# MILESTONE: Platform Admin Console

goal: A superadmin can view and fully manage any tenant (config, budget, keys, members) through a dedicated, fully audited cross-tenant admin surface
rationale: sub-milestone — milestone 2 of 5 in the confirmed "Full 5, admin-first" superadmin
  roadmap (platform-identity → platform-admin-console → tenant-impersonation →
  platform-key-default → platform-access-plan), sized at the roadmap's original intake pass.
  Depends-on platform-identity (PR #56): consumes Role.SUPERADMIN, the reserved platform
  tenant, and — most directly — `authorize_tenant_scope()`, whose own docstring names this
  milestone as the task that "wires the first real caller." Extends the existing 14
  tenant-scoped `/admin/*` routers and the dashboard's `(app)/app/*` UI pattern into a
  cross-tenant variant, rather than inventing a parallel surface. Unblocks tenant-impersonation
  (milestone 3), which plugs an "act as" action into this console's tenant-detail view.
stage: production · status: active · created: 2026-07-02T15:53:53+00:00
release: 0.10.0

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  a superadmin-only directory of ALL tenants (search/paginate); opening one tenant to VIEW
     its config, budget, keys (redacted/metadata-only — never raw secret material), and
     members; EDITING that tenant's config/budget and managing its keys/members on its
     behalf — all cross-tenant reads and writes reuse the same DTOs/use-cases as the existing
     tenant-scoped `/admin/*` endpoints (parametrized by target tenant_id), gated through
     `authorize_tenant_scope()`; every cross-tenant action audited (extends the
     superadmin-audit-foundation primitive, same fire-and-forget/fail-open pattern); a new,
     dedicated dashboard surface — the first UI-facing milestone in this roadmap, run through
     ADD's UDD design loop rather than shipped as bare CRUD+table.
Out: acting AS a tenant user / impersonation sessions (→ tenant-impersonation, milestone 3);
     automatic platform-credential fallback when a tenant has no BYOK key (→
     platform-key-default, milestone 4); metered/rate-limited subscription plans (→
     platform-access-plan, milestone 5); rendering raw secret/credential material anywhere in
     the console (keys stay redacted, matching the existing BYOK key surface convention);
     any change to `authorize_tenant_scope()`'s own frozen (@v1) contract — this milestone
     is a consumer of it, not a modifier; any behavior change to the existing tenant-scoped
     `/admin/*` endpoints for non-superadmin callers, who must see byte-identical behavior.

## Shared decisions & glossary deltas   (living — every task must honor these)
- Every cross-tenant read or write is routed through `authorize_tenant_scope(identity,
  target_tenant_id)` — no endpoint hand-rolls its own superadmin bypass check.
- Reuse-over-invent: a cross-tenant endpoint parametrizes the existing tenant-scoped
  use-case/DTO by target tenant_id rather than duplicating a parallel code path.
- Glossary gap found (not yet fixed): "platform tenant" and "superadmin" are used
  extensively in code/docs but were never added to GLOSSARY.md when platform-identity
  folded — this milestone's fold should add them, plus "cross-tenant admin surface"
  (superadmin-only UI for viewing/managing any tenant) as a new term.

## Shared / risky contracts (freeze these first)
- Cross-tenant READ shape (routing: new `/admin/platform/tenants/{tenant_id}/...` paths vs.
  an optional target-tenant param on existing routes) -> owning task `platform-tenant-directory`
  (this is the riskiest decision — it decides whether every later task duplicates routers or
  parametrizes them).
- Cross-tenant audit event shape (action-name convention + metadata for "superadmin X acted
  on tenant Y") -> owning task `admin-console-audit`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] platform-tenant-directory     depends-on: none                              — superadmin-only list/search/get-one across ALL tenants; wires authorize_tenant_scope's first real caller.
- [x] cross-tenant-config-budget    depends-on: platform-tenant-directory         — view + edit a target tenant's config and budget cross-tenant.
- [x] cross-tenant-keys-members     depends-on: platform-tenant-directory         — view + manage a target tenant's keys (redacted) and members cross-tenant.
- [x] admin-console-audit           depends-on: platform-tenant-directory, cross-tenant-config-budget, cross-tenant-keys-members — every cross-tenant action from the above audited, attributing the real superadmin actor.
- [x] admin-console-ui              depends-on: admin-console-audit               — the dashboard surface itself (directory + tenant-detail tabs), Aurora-consistent, UDD design loop, wired to all four backend tasks.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A superadmin can find and open any tenant from a directory view.        (← platform-tenant-directory)
- [x] A superadmin can view AND edit any tenant's config and budget.          (← cross-tenant-config-budget)
- [x] A superadmin can view and manage any tenant's keys (redacted) and members. (← cross-tenant-keys-members)
- [x] Every cross-tenant read/write produces an audit record attributing the real superadmin actor. (← admin-console-audit)
- [x] The console is a polished, Aurora-consistent, WCAG-AA UI surface, design-confirmed before build. (← admin-console-ui)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

> Backfilled 2026-07-15 from an independent adversarial re-verification against `origin/main`
> (all 5 exit criteria re-confirmed by live, wired, tested, merged code; no authorize_tenant_scope
> bypass found; consistent with this milestone's own RETRO.md VERDICT=DONE, 5/5 gates PASS).

### Ship by domain   (what changed, per bounded context)
- tooling : untouched.
- skill   : untouched.
- book    : untouched (GLOSSARY "platform tenant"/"superadmin"/"cross-tenant admin surface" terms still owed at fold).
- gateway : 4 new platform routers — `platform_tenants_router` (list/search/get), `platform_tenant_config_router` (cache/guardrails/budget GET+PUT), `platform_keys_router` (keys, redacted `KeyInfoResponse` — no secret/hash field), `platform_users_router` (members list + role-assign); shared `emit_platform_audit` on every success path (15+ call sites, real superadmin actor + PATH target tenant); every write gated `require_superadmin` + `authorize_tenant_scope` (no hand-rolled bypass).
- dashboard : superadmin cross-tenant console — `PlatformTenantDirectory` (search/paginate) + tabbed `PlatformTenantDetail` (Config/Budget/Keys/Members) with safety banner, per-tab queries, a11y markers; PR #57, backend completion 67d1557.

### Cross-task evidence   (one row per task)
- platform-tenant-directory : gate=PASS · directory list/search/get, first authorize_tenant_scope caller · residue=none.
- cross-tenant-config-budget : gate=PASS · cache/guardrails/budget GET+PUT, 404-before-write · residue=none.
- cross-tenant-keys-members : gate=PASS · keys (redacted) + members role-assign, PATH tenant_id never caller's · residue=none.
- admin-console-audit       : gate=PASS · 23/23 new tests + 64/64 regression; fire-and-forget fail-open inherits repo convention · residue=none.
- admin-console-ui          : gate=PASS · design frozen+approved (Tin 2026-07-03) before build; Aurora tabbed shell, WCAG-AA · residue=none.

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: a superadmin can view AND fully manage any tenant (config/budget/keys/members) through a
  dedicated, fully audited cross-tenant surface — proven by the 4 PATH-tenant-parametrized routers
  all gated through `authorize_tenant_scope` with `emit_platform_audit` attributing the real
  superadmin actor on every cross-tenant read/write, fronted by the Aurora console UI (PR #57).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [x] All 5 tasks merged to main (PR #57 UI + backend completion 67d1557; present on origin/main).
- [ ] At fold: add GLOSSARY terms "platform tenant" / "superadmin" / "cross-tenant admin surface".
- [ ] Confirm release attribution row (already shipped in prior release code).
