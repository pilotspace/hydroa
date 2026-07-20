# MILESTONE: Account tiers & billing: personal/individual + business/enterprise base fee + payer of record

goal: Hydroa sells differentiated subscription tiers — a personal/individual plan and business/enterprise plans with a recurring base fee — with all payment attributed to one designated billing owner per tenant
rationale: sub-milestone (intake 2026-07-16) — commercial half of Tin's "paid-subscription business model"
  spec. Recon confirmed the plan-catalog/invoice/credit/seat-pricing spine (monetization-core,
  platform-access-plan) is fully built and REUSABLE, but three commercial concepts are absent/partial:
  a personal/individual account tier (TenantRow.kind is only customer|platform), an enterprise recurring
  BASE fee (invoice lines are only usage|seat|proration), and a singular payer-of-record (BILLING_ADMIN
  is a diffuse role, no designated billing owner). Tin decisions (2026-07-16): (a) TWO milestones — this
  is the billing/tiers half; the identity/onboarding half is `enterprise-domain-onboarding` (queued);
  (b) DESIGNATE a payer-of-record + a never-zero-billing-owner guard; (c) personal account REUSES the
  tenant model as a 1-member OWNER tenant via a new `account_type` discriminator (no separate Account
  entity — one identity/authz/billing pipeline for both flavors).
stage: production · status: active · created: 2026-07-16T03:12:00+00:00
release: 0.11.0

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
- A tenant `account_type` discriminator (`personal` | `business`) on `TenantRow` (alongside the existing
  `kind=customer|platform`), set at signup; existing customer tenants backfilled `business`.
- A seeded `individual` plan row (personal tier: 1 seat, individual pricing/budget) in the `plans` catalog.
- An enterprise/team recurring BASE fee: a `base_price_usd_monthly` plan attribute + a NEW
  `line_type="base"` invoice line (flat, independent of usage AND seat count), mirroring the existing
  `seat_pricer.py` pattern. A $0-usage enterprise tenant is still billed its base fee.
- A designated per-tenant billing owner (payer of record): a canonical billing-owner field on the tenant,
  defaulted to the signup OWNER; invoices/credits attribute to it; a "never zero billing-capable owner"
  invariant guarding role-change / deactivation.
- Reconcile the marketing `/pricing` base-fee copy to the real backend plan `base_price` (fix the
  current unwired "$99/mo" drift).
Out:
- Enterprise email-domain routing / auto-assign / domain-claims UI — owned by the queued sibling
  milestone `enterprise-domain-onboarding`.
- Payment-processor integration (Stripe/collection/dunning) — remains external (monetization-core
  out-of-scope, unchanged).
- Superadmin CRUD for the plan catalog itself (tier definitions stay seed/migration-only, per
  monetization-core's explicit non-goal).
- Tax/VAT computation (carried only as a configurable line, unchanged).

> UI/UX in scope? Minimal — the `/pricing` marketing copy reconciliation (present the base fee honestly)
> and any plan/invoice surface that must show a base line. No new dashboard IA; the heavy UDD design loop
> lives in the sibling milestone's `domain-claims-console`. Signature element deferred to that milestone.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **account_type is a DISCRIMINATOR, not a new entity.** A personal account IS a 1-member `kind=customer`
  tenant with `account_type=personal`; the same identity/authz/billing pipeline serves both flavors.
- **BASE fee is composed like seat pricing, OUTSIDE usage.** The new `line_type="base"` rides the existing
  immutable-invoice/append-only pipeline (mirror `seat_pricer.compute_seat_lines`); it NEVER touches the
  usage rate-card path. A base line is emitted even at $0 usage / 0 seats.
- **Payer-of-record is a SINGLE designated owner, guarded.** Exactly one billing owner per tenant; the
  system refuses to leave a tenant with zero billing-capable owners (demotion/deactivation guard).
- New GLOSSARY terms — **account_type**: personal|business tenant flavor · **base fee**: flat recurring
  platform charge independent of usage+seats · **billing owner**: the single designated payer of record.

## Shared / risky contracts (freeze these first)
- The `plans` schema extension (`base_price_usd_monthly`) + the 5-tier catalog restructure + the new
  `line_type="base"` invoice-line shape -> owning task `plan-tiers-and-base-fee` (consumed by the invoice
  generator, signup routing, and the /pricing no-drift reconciliation).
- The `TenantRow.account_type` + billing-owner field shape + backfill semantics
  -> owning task `account-type-discriminator` (consumed by signup + billing-owner-of-record).

## Pricing model — Tin-locked 2026-07-16 (5 tiers; supersedes old 3-plan catalog + $0/$299/$2.5k roadmap)
| plan | display | account tier | base_price/mo | seat_cap | role |
|---|---|---|---|---|---|
| `free` | Free | personal | NULL ($0) | 1 | personal signup DEFAULT (new row) |
| `starter` | Starter | personal | $1 | 1 | upgrade (repurpose old business Free `starter`) |
| `pro` | Pro | personal | $20 | 1 | upgrade; unlimited free-models (LATER task); was `individual` |
| `team` | Team | business | $99 | ∞ | matches live /pricing "$99/mo + usage" |
| `enterprise` | Enterprise | business | NULL | ∞ | per-tenant custom ("Contact us") |
Personal signup lands on Free $0 (free-then-upgrade, NOT billable-from-signup). No base fee exists today —
billing is usage(marked-up)+seats; `line_type="base"` is the new flat recurring line, billed even at $0 usage.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] account-type-discriminator   depends-on: none                       — DONE. `account_type`
  (personal|business) on `TenantRow` + backfill existing customers `business`; signup provisions the right
  account_type. (Personal-default plan routing REPOINTED from `individual`→`free` by plan-tiers-and-base-fee.)
- [ ] plan-tiers-and-base-fee       depends-on: account-type-discriminator — BILLING. Restructure `plans`
  to the 5-tier catalog above (new `free`; repurpose `starter`→personal $1; rename `individual`→`pro` $20;
  set team/enterprise base); add `base_price_usd_monthly`; `InvoiceGenerator` emits a `line_type="base"`
  flat line (independent of usage + seats); repoint personal signup default → `free`; `/pricing` no-drift
  test binds page figures to backend base_price. (Reframes the old `enterprise-base-fee` task.)
- [ ] billing-owner-of-record       depends-on: account-type-discriminator — SECURITY (dual-verify —
  authz/role invariant). Add a canonical billing-owner field on the tenant (default = signup OWNER);
  attribute invoices/credits to it; enforce a "never zero billing-capable owner" guard on role-change +
  deactivation.
- LATER (separate milestone): `plan-model-entitlements` — Pro tier unlimited use of a configurable set of
  "free" LLM models (per-plan model entitlement + usage-metering exemption). Tin-deferred, NOT this milestone.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A personal signup creates an `account_type=personal` tenant (default plan) with 1 OWNER seat   (← account-type-discriminator; default plan repointed to `free` by plan-tiers-and-base-fee)
- [x] A business signup / existing customer tenant is `account_type=business` and can be assigned team/enterprise plans   (← account-type-discriminator)
- [x] The `plans` catalog holds the 5 Tin-locked tiers (free $0 / starter $1 / pro $20 / team $99 / enterprise NULL); personal signup lands on `free`   (← plan-tiers-and-base-fee — migration test asserts EXACTLY 5 rows {free,starter,pro,team,enterprise}; reconciled signup test asserts free id)
- [x] A team/enterprise/paid invoice includes a `base` line equal to the plan's `base_price_usd_monthly`, independent of usage AND seat count (a $0-usage $99 team tenant is still billed $99)   (← plan-tiers-and-base-fee — base line folded into raw+rounded total in the atomic invoice txn; read back through the REAL invoice-detail endpoint == $99; enterprise/unplanned byte-identical)
- [x] The marketing `/pricing` figures match the backend plan `base_price_usd_monthly` (no unwired copy — enforced by a no-drift test)   (← plan-tiers-and-base-fee — 3 no-drift dashboard vitest bind page figures to hardcoded EXPECTED_SEED + explicit drift-detection case)
- [x] Every tenant has exactly one designated billing owner; invoices/credits attribute to it   (← billing-owner-of-record + billing-owner-signup-population — nullable FK backfills existing customers; signup populates new tenants via flush-then-assign inside the one begin())
- [x] The last billing-capable owner cannot be demoted or deactivated — the guard rejects it with a clear error   (← billing-owner-of-record — FOR-UPDATE tenants-row lock serializes reassign vs demote/deactivate; barrier-forced race test proves the invariant never yields zero billing-capable owners)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway  : `plans` catalog → 5 Tin-locked tiers + `base_price_usd_monthly` column; new `line_type="base"` invoice line (folded in the atomic invoice txn, mirror of seat_pricer); `TenantRow.account_type` (personal|business) + `billing_owner_user_id` nullable FK (use_alter, circular-FK-safe) + 2 CHECKs; signup provisions account_type + repoints personal default → `free` + populates billing owner via flush-then-assign; billing-owner reassign/demote/deactivate guard (FOR-UPDATE lock, "never zero billing-capable owner"); SCIM deprovision returns 409 on the last billing owner. Migrations: `a7c3e9f1b2d4` (account_type), plan-tiers migration (5-tier restructure + base_price), `f94771e4aa7c` (billing owner).
- dashboard : `/pricing` marketing figures bound to backend `base_price_usd_monthly` via a hardcoded EXPECTED_SEED constants module + 3 no-drift vitest (drift-detection case included).
- tooling / skill / book : untouched.

### Cross-task evidence   (one row per task)
- account-type-discriminator     : gate=PASS · tests=6/6 + 4/4 migration backfill + 203 signup/tenants/plan regression green · residue=`plan_id` FK violation would mis-map to EmailAlreadyRegistered — unreachable (plans seed-only/immutable), noted non-blocking.
- plan-tiers-and-base-fee        : gate=PASS · tests=84 task + 155 invoice/seat/plan + 21 migrations BE + 3 no-drift + 12 pricing-page dashboard vitest green · residue=`starter`-repurpose silently reclassifies any live business tenant on old `starter` → personal $1 (Tin-directed; recon found zero such tenants; pre-deploy `SELECT count(*) … plan_id=starter` flagged operationally).
- billing-owner-of-record        : gate=PASS (SECURITY — dual adversarial verify: orchestrator + independent add-verify `ae80c82822877fd0e`; barrier-forced race test at the real lock site) · tests=22/22 new + 184 touched-surface regression + 526 full regression green · residue=a direct admin DB script could bypass the app-layer guard (Tin-locked residual, named in §0).
- billing-owner-signup-population : gate=PASS · tests=3/3 new (raw-SQL readback) + 5 reconciled sibling + 149+184+36+8 targeted regression green (full bulk 3922✓/32 — the 32 are pre-existing shared-:5433/Redis-contention flakes, every dir green in isolation, zero overlap with the changed code) · residue=SCIM-deprovision of the billing owner → 409 until reassigned (Tin-approved Option A).

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (criteria 1–2 ← account-type-discriminator; 3–5 ← plan-tiers-and-base-fee; 6 ← billing-owner-of-record + billing-owner-signup-population; 7 ← billing-owner-of-record — all cited inline on each criterion)
- goal: *Hydroa sells differentiated subscription tiers — a personal/individual plan and business/enterprise plans with a recurring base fee — with all payment attributed to one designated billing owner per tenant.* Proven: the 5-tier catalog ships with a `base_price_usd_monthly` that emits a real `line_type="base"` invoice line billed even at $0 usage, and every tenant (existing via backfill, new via signup) carries exactly one guarded billing owner of record. All 4 tasks gate=PASS; the security task dual-verified; code merged in PR #76.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [x] run the DB migration (account_type + base_price_usd_monthly + billing-owner columns; 5-tier plan seed) — additive, backward-compatible; existing-customer backfill = business (migrations `a7c3e9f1b2d4` + plan-tiers + `f94771e4aa7c`, merged in #76)
- [x] open a PR from the Close ship-review above; the human reviews + merges (billing-owner-of-record is security — dual adversarial verify recorded before merge) — shipped in PR #76 (admin-merge `a82d1b1`)
- [ ] document the base-fee runbook (which plans carry a base fee; how a $0-usage tenant is still billed) — deferred follow-up
- [ ] tag / publish / deploy (human-run, per release.md) — this milestone's code is already on `main` (#76) but was NOT bundled into 0.10.0 (it read `active` at cut time); it now becomes releasable and folds into the NEXT release cut
