# MILESTONE: Platform Access Subscription Plan

goal: A tenant can subscribe to a metered, rate-limited, fully audited plan governing platform-tenant-backed usage
rationale: part of the "Full 5, admin-first" superadmin/platform-tenant roadmap, sequenced fifth (after `platform-key-default`). Tin instructed sizing it NOW, in parallel with `tenant-impersonation`/`team-member-invite`, ahead of its original roadmap order — see the scope-reading decision below, which exists because of that reordering.
stage: production · status: active · created: 2026-07-02T15:53:54+00:00
release: 0.10.0

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
  - ~~GENUINELY OPEN~~ ~~RESOLVED by Tin 2026-07-05: the LITERAL reading stands~~ **SUPERSEDED by Tin
    2026-07-15: the BROAD reading stands after all.** The 2026-07-05 literal reading (enforcement
    meters ONLY platform-credential-fallback usage, blocked on `platform-key-default`) was overtaken
    by what actually shipped: BOTH sibling enforcement dimensions landed the BROAD reading —
    `plan-enforcement` (milestone `monetization-core`) wired `budget_usd_monthly_default` into
    `RedisBudgetGuard` for ALL tenant usage, and `plan-seat-cap` enforces `seat_cap` at both
    provisioning entry points — neither depends on `platform-key-default`. To keep the three
    enforcement dimensions consistent, `plan-rate-enforcement` follows the same broad path: enforce
    plan rpm/tpm on ALL of a tenant's usage, composing with the existing per-key ceiling, with NO
    `platform-key-default` dependency. `platform-key-default` (milestone 4) remains a separate,
    still-unstarted concern about credential fallback, not a prerequisite for plan enforcement.

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
- ~~`platform-key-default`'s credential-fallback surface … those two tasks cannot freeze until it
  exists~~ **SUPERSEDED 2026-07-15 (broad reading):** enforcement meters a tenant's usage generally,
  not platform-credential-backed usage specifically — no `platform-key-default` dependency. Both
  enforcement tasks resolve their ceiling via the existing `ResolvedEntitlements` precedence
  (tenant override → plan default → unlimited), the same shape budget/seat-cap already use.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] plan-catalog              depends-on: none                                   — Plan/tier catalog table + tenant association + superadmin view/assign surface. DONE (PR #58). Absorbed the originally-separate `plan-assignment-admin` task's scope.
- [x] plan-admin-ui              depends-on: plan-catalog                           — Dashboard surface for the plan catalog + per-tenant plan assignment (superadmin-only). DONE (gate=PASS).
- [x] plan-seat-cap              depends-on: plan-catalog, member-invite-acceptance — Enforce `TenantRow.seat_cap` at both user-provisioning entry points (OIDC auto-provision + invite-accept). DONE (gate=PASS).
- [x] plan-budget-enforcement    depends-on: plan-catalog                           — Wire `plans.budget_usd_monthly_default` (or the tenant's override) into actual spend enforcement. SATISFIED (broad reading) by `plan-enforcement` under milestone `monetization-core` — `RedisBudgetGuard` resolves the ceiling via `ResolvedEntitlements` and raises 402 ERR_BUDGET_EXCEEDED for ALL tenant usage; no separate `plan-budget-enforcement` task was created. Exit criterion 3 met (attribution recorded here).
- [x] plan-rate-enforcement      depends-on: plan-catalog                           — Wire `plans.rpm_limit_default`/`tpm_limit_default` (+ new `TenantRow.rpm_limit`/`tpm_limit` overrides) into actual tenant-layer rate enforcement on ALL tenant usage, composing with the per-key ceiling. DONE (gate=PASS, 2026-07-15) — default-ON via main.py.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A superadmin can view the plan catalog and see/change which plan a tenant is on   (← plan-catalog)
- [x] A superadmin can do the above from the dashboard, not just the API   (← plan-admin-ui)
- [x] A tenant over its plan's budget ceiling is actually blocked or throttled, not just billed   (← plan-budget-enforcement, satisfied broad by `plan-enforcement`/monetization-core)
- [x] A tenant over its plan's rpm/tpm ceiling is actually rate-limited at the tenant layer   (← plan-rate-enforcement)
- [x] Adding a member beyond a tenant's seat cap is rejected at both provisioning entry points   (← plan-seat-cap)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

> Closed 2026-07-15 after `plan-rate-enforcement` (the last enforcement dimension) landed the broad
> reading Tin confirmed this session, unblocking exit criterion 4. Criterion 3 (budget) attribution
> reconciled to `plan-enforcement`/monetization-core; the literal→broad supersession recorded in Scope.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched.
- skill   : untouched.
- book    : untouched (GLOSSARY `Plan` usage-note for tenant-layer rate ceiling owed at fold).
- gateway : `plan-catalog` (plans table + tenant.plan_id/seat_cap + superadmin view/assign routes);
  `plan-seat-cap` (`assert_seat_available` at OIDC + invite-accept provisioning); budget enforcement
  via `plan-enforcement` (RedisBudgetGuard resolves plan ceiling, 402); `plan-rate-enforcement`
  (TenantRow.rpm_limit/tpm_limit overrides + PlanRateLimitResolver + tenant-window checks in both
  enforce seams, default-ON at boot, fail-open).
- dashboard : `plan-admin-ui` (Plans catalog page + 5th tenant-detail Plan tab, assign/clear).

### Cross-task evidence   (one row per task)
- plan-catalog          : gate=PASS · plans table + superadmin view/assign (PR #58) · residue=none.
- plan-admin-ui         : gate=PASS · catalog page + per-tenant Plan tab · residue=none.
- plan-seat-cap         : gate=PASS · seat cap at both provisioning entry points · residue=none (SCIM-reactivation todo tracked separately).
- plan-budget-enforcement : SATISFIED-BROAD by `plan-enforcement`/monetization-core (no separate task) · budget 402 for all tenant usage.
- plan-rate-enforcement : gate=PASS · full -n12 suite 3938✓/0 · residue=streaming-TPM under-count (fail-open, seeded spec delta).

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: a tenant can subscribe to a metered, rate-limited, fully audited plan governing its usage —
  proven by the plan catalog + superadmin assign surface (view), budget 402 + tenant-layer rpm/tpm 429
  + seat-cap rejection (metered/rate-limited/enforced), all via `emit_platform_audit`-audited assignment
  and the shared `resolve_entitlements` tenant→plan→None precedence across every dimension.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [x] plan-catalog/plan-admin-ui/plan-seat-cap already merged (PRs #58–#60); budget via monetization-core.
- [ ] Commit + PR `plan-rate-enforcement` (this session's uncommitted work) — human reviews + merges.
- [ ] At fold: add GLOSSARY `Plan` tenant-layer-rate-ceiling usage note; drain the 3 seeded spec deltas.
- [ ] Confirm release attribution row in the next cut (release.md).
