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
release: pending

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
- [ ] The `plans` catalog holds the 5 Tin-locked tiers (free $0 / starter $1 / pro $20 / team $99 / enterprise NULL); personal signup lands on `free`   (← plan-tiers-and-base-fee)
- [ ] A team/enterprise/paid invoice includes a `base` line equal to the plan's `base_price_usd_monthly`, independent of usage AND seat count (a $0-usage $99 team tenant is still billed $99)   (← plan-tiers-and-base-fee)
- [ ] The marketing `/pricing` figures match the backend plan `base_price_usd_monthly` (no unwired copy — enforced by a no-drift test)   (← plan-tiers-and-base-fee)
- [ ] Every tenant has exactly one designated billing owner; invoices/credits attribute to it   (← billing-owner-of-record)
- [ ] The last billing-capable owner cannot be demoted or deactivated — the guard rejects it with a clear error   (← billing-owner-of-record)

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
- [ ] run the DB migration (account_type + base_price_usd_monthly + billing-owner columns; individual plan seed) — additive, backward-compatible; confirm existing-customer backfill = business
- [ ] open a PR from the Close ship-review above; the human reviews + merges (billing-owner-of-record is security — dual adversarial verify recorded before merge)
- [ ] document the base-fee runbook (which plans carry a base fee; how a $0-usage tenant is still billed)
- [ ] tag / publish / deploy (human-run, per release.md) — bundle with the sibling onboarding milestone + the other closed roadmap milestones into the next release cut
