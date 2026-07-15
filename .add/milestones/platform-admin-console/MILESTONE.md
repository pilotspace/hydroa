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
- [ ] platform-tenant-directory     depends-on: none                              — superadmin-only list/search/get-one across ALL tenants; wires authorize_tenant_scope's first real caller.
- [ ] cross-tenant-config-budget    depends-on: platform-tenant-directory         — view + edit a target tenant's config and budget cross-tenant.
- [ ] cross-tenant-keys-members     depends-on: platform-tenant-directory         — view + manage a target tenant's keys (redacted) and members cross-tenant.
- [ ] admin-console-audit           depends-on: platform-tenant-directory, cross-tenant-config-budget, cross-tenant-keys-members — every cross-tenant action from the above audited, attributing the real superadmin actor.
- [ ] admin-console-ui              depends-on: admin-console-audit               — the dashboard surface itself (directory + tenant-detail tabs), Aurora-consistent, UDD design loop, wired to all four backend tasks.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A superadmin can find and open any tenant from a directory view.        (← platform-tenant-directory)
- [x] A superadmin can view AND edit any tenant's config and budget.          (← cross-tenant-config-budget)
- [x] A superadmin can view and manage any tenant's keys (redacted) and members. (← cross-tenant-keys-members)
- [x] Every cross-tenant read/write produces an audit record attributing the real superadmin actor. (← admin-console-audit)
- [x] The console is a polished, Aurora-consistent, WCAG-AA UI surface, design-confirmed before build. (← admin-console-ui)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- backend : new `/admin/platform/tenants/*` routers — platform_tenants_router (directory/get),
  platform_tenant_config_router (cache/guardrails/budget GET+PUT), platform_keys_router
  (list/create/patch/rotate/revoke), platform_users_router (list/role-assign); every cross-tenant
  read/write gated by `authorize_tenant_scope()` (tenants/domain/authz.py:169-191) + `require_superadmin`;
  `emit_platform_audit()` on every success path attributing the real superadmin actor.
- dashboard : superadmin console surface — PlatformTenantDirectory + PlatformTenantDetail tabbed shell
  (Config/Budget/Keys/Members) with PlatformSafetyBanner, per-tab queries, aria markers.
- tooling / skill / book : untouched.

### Cross-task evidence   (one row per task — all gate=PASS; independently re-verified 2026-07-15)
- platform-tenant-directory  : gate=PASS · directory list/search/get across all tenants · residue=none
- cross-tenant-config-budget : gate=PASS · view+edit target tenant config/budget cross-tenant · residue=none
- cross-tenant-keys-members  : gate=PASS · keys redacted (KeyInfoResponse has no secret field) + members role-assign · residue=none
- admin-console-audit        : gate=PASS · 23/23 new + 64/64 regression · superadmin actor attributed, target=path tenant · residue=none
- admin-console-ui           : gate=PASS · §3 frozen@v1 human-approved (Tin, 2026-07-03) before build · Aurora + a11y markers · residue=none

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: A superadmin can view and fully manage any tenant through a dedicated, fully audited cross-tenant
  admin surface — proven by the 4 `/admin/platform/tenants/*` routers (all through `authorize_tenant_scope`
  + `emit_platform_audit`) wired to the PlatformTenantDetail console. Independently verified 2026-07-15:
  all 5 exit criteria MET, no `authorize_tenant_scope` bypass, keys redacted. Shipped PR #57 + backend `67d1557`.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
