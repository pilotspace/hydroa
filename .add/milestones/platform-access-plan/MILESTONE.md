# MILESTONE: Platform Access Subscription Plan

goal: A tenant can subscribe to a metered, rate-limited, fully audited plan governing platform-tenant-backed usage
rationale: part of the "Full 5, admin-first" superadmin/platform-tenant roadmap, sequenced fifth (after `platform-key-default`). Tin instructed sizing it NOW, in parallel with `tenant-impersonation`/`team-member-invite`, ahead of its original roadmap order — see the scope-reading decision below, which exists because of that reordering.
stage: production · status: active · created: 2026-07-02T15:53:54+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.
>
> Backfilled 2026-07-05: `plan-catalog` built and shipped (PR #58) before this doc's
> Scope/Tasks/Exit-criteria sections were ever filled in (only the header existed) — that task's own
> TASK.md claims (2026-07-04) it updated this doc to retract `plan-assignment-admin`, but the file on
> disk was never actually touched. The breakdown below reconstructs from `plan-catalog`'s own §0/§1
> disclosures, not a fresh design pass — flag anything that reads wrong rather than treat it as settled.

## Scope
In:
  - A `plans` reference table (Starter/Team/Enterprise, seed-migrated) as the named unit of a
    customer tenant's usage-governance profile: seat cap, budget default, rpm/tpm defaults.
  - `TenantRow.plan_id` (nullable FK, additive) + `TenantRow.seat_cap` (additive per-tenant override
    column, `> 0` check constraint).
  - A superadmin-only cross-tenant surface to view the catalog and view/change a tenant's plan
    (`GET /admin/platform/plans`, `GET`/`PUT /admin/platform/tenants/{tenant_id}/plan`), reusing
    `authorize_tenant_scope`/`emit_platform_audit` verbatim — no parallel authz/audit primitive.
Out (deferred to sibling tasks, not `plan-catalog`'s job):
  - Actually enforcing $ budget, rate, or seat ceilings anywhere in the proxy/provisioning path —
    `plan-catalog` defines what a plan IS and lets a superadmin attach one; it does not wire any
    enforcement. That is `plan-budget-enforcement` / `plan-rate-enforcement` / `plan-seat-cap`'s job.
  - Any dashboard surface for the catalog or per-tenant plan assignment — `plan-admin-ui`.
  - ~~GENUINELY OPEN~~ **RESOLVED by Tin 2026-07-05: the LITERAL reading stands.** The goal's
    "platform-tenant-backed usage" means ONLY usage riding on the platform tenant's own credential
    fallback. `plan-catalog` had shipped under a LOOSE working default (disclosed, not silent) — its
    own catalog/schema shape is unaffected by this reversal (unchanged either way), but
    `plan-budget-enforcement`/`plan-rate-enforcement` are now correctly sequenced (below) to depend
    on `platform-key-default`, which has 0 tasks started — those two enforcement tasks are BLOCKED
    until that milestone produces something to enforce against.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Enum-vs-table, decided**: a `plans` reference table (not a hardcoded enum column) — a tier-ceiling
  change is a superadmin admin action, never a schema migration, because MILESTONE-named cases like
  "Enterprise: custom" imply per-tenant negotiated ceilings on a business cadence faster than deploys.
- **`plan-assignment-admin` is retracted** — originally envisioned as its own task, its scope (the
  superadmin view/assign/change surface) shipped directly inside `plan-catalog` instead. Any reference
  elsewhere to a separate `plan-assignment-admin` task is stale; `plan-admin-ui` is its true remaining
  successor (dashboard only, depends on `plan-catalog`).
- **Seat-cap has a cross-milestone dependency on `team-member-invite`**: `member-invite-issuance`'s
  accept-endpoint is the second user-provisioning entry point `plan-seat-cap` will need to hook (the
  first being `get_or_provision_oidc_user`) — noted here so `plan-seat-cap`'s own design doesn't have
  to rediscover it; `plan-catalog`'s schema already leaves the `seat_cap` column additive for this.

## Shared / risky contracts (freeze these first)
- `plans` table shape + `TenantRow.plan_id`/`seat_cap` columns (FROZEN @ v1) -> owning task `plan-catalog`
- `platform-key-default`'s credential-fallback surface (not yet designed) -> whatever it produces is
  what `plan-budget-enforcement`/`plan-rate-enforcement` meter against; those two tasks cannot freeze
  their own §3 until `platform-key-default` exists.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] plan-catalog              depends-on: none                                   — Plan/tier catalog table + tenant association + superadmin view/assign surface. DONE (PR #58). Absorbed the originally-separate `plan-assignment-admin` task's scope.
- [ ] plan-admin-ui              depends-on: plan-catalog                           — Dashboard surface for the plan catalog + per-tenant plan assignment (superadmin-only). UNBLOCKED — can start now.
- [ ] plan-seat-cap              depends-on: plan-catalog, member-invite-acceptance — Enforce `TenantRow.seat_cap` at both user-provisioning entry points (OIDC auto-provision + invite-accept). BLOCKED on `member-invite-acceptance` (needs its actual use-case call site to hook into; corrected 2026-07-05 from the milestone doc's original member-invite-issuance reference, which predated invite-accept being scoped as its own task).
- [ ] plan-budget-enforcement    depends-on: plan-catalog, platform-key-default     — Wire `plans.budget_usd_monthly_default` (or the tenant's override) into actual spend enforcement against platform-tenant-backed usage. BLOCKED on `platform-key-default` (literal reading, confirmed 2026-07-05).
- [ ] plan-rate-enforcement      depends-on: plan-catalog, platform-key-default     — Wire `plans.rpm_limit_default`/`tpm_limit_default` into actual rate enforcement against platform-tenant-backed usage, composing with (not replacing) the existing per-key rpm/tpm ceiling. BLOCKED on `platform-key-default` (literal reading, confirmed 2026-07-05).

## Exit criteria (observable; map each to the task that delivers it)
- [x] A superadmin can view the plan catalog and see/change which plan a tenant is on   (← plan-catalog)
- [ ] A superadmin can do the above from the dashboard, not just the API   (← plan-admin-ui)
- [ ] A tenant over its plan's budget ceiling is actually blocked or throttled, not just billed   (← plan-budget-enforcement)
- [ ] A tenant over its plan's rpm/tpm ceiling is actually rate-limited at the tenant layer   (← plan-rate-enforcement)
- [ ] Adding a member beyond a tenant's seat cap is rejected at both provisioning entry points   (← plan-seat-cap)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
