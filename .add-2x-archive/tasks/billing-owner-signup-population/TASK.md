# TASK: Populate billing_owner_user_id on signup so every new tenant has a billing owner day-one

slug: billing-owner-signup-population · created: 2026-07-16 · stage: production
milestone: account-tiers-billing
autonomy: auto
phase: done
fast: true

> Fast lane — one small task, minimal sections, filled top-to-bottom. The trust floor still
> holds: a FROZEN §3 contract · ≥1 red test before build · a recorded §6 gate (security = HARD-STOP).
> The acceptance scenario collapses into §1 `Accept:`; OBSERVE is one optional line at the gate.

---

## 0 · GROUND — the real codebase

Touches (files · symbols):
- `apps/gateway/src/gateway/tenants/infrastructure/repository.py:SqlAlchemyIdentityRepository.create_tenant_with_owner` (lines 82-113) — the sole public-signup / verified-domain-owner tenant-creation choke point. Currently inserts `TenantRow(billing_owner_user_id` unset → NULL`)` + one `UserRow(role=Role.OWNER)` in one `async with self._session.begin()` and returns `(tenant.id, user.id)`. This is the ONLY place a brand-new customer tenant + its first OWNER are minted together.
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:TenantRow.billing_owner_user_id` (lines 302-309) — the nullable FK→users.id added by billing-owner-of-record migration `f94771e4aa7c`, declared `use_alter=True` (breaks the tenants↔users circular-FK create_all cycle at the DDL layer). CHECK `ck_tenants_platform_no_billing_owner` (line 147) forbids a non-null value only when `kind='platform'` — irrelevant here (signup mints `kind='customer'`, the column default).
- The circular FK (`users.tenant_id → tenants.id` AND `tenants.billing_owner_user_id → users.id`) means the value CANNOT be set at INSERT time (the referenced user row does not exist yet) — it must be a post-INSERT UPDATE within the same transaction (insert both with billing_owner NULL → flush → assign → commit emits the UPDATE). `use_alter` is DDL-only; runtime FK checks still fire, so a pre-flush assignment would trip an immediate FK violation.
Context (working folder): none beyond the two src files above.
Honors (patterns / conventions): the existing `flush()`-before-dependent-write idiom already in this same repository (`join_verified_tenant_domain`, lines 144-151) — flush the parent INSERTs before the FK-dependent write. Atomicity: the assignment rides INSIDE the existing single `begin()` block (Safety rule §5) — tenant + owner + billing-owner pointer all commit together or none do.
Anchors the contract cites: `SqlAlchemyIdentityRepository.create_tenant_with_owner`, `TenantRow.billing_owner_user_id`.
Ground SHA: 3c27af5

---

## 1 · SPECIFY — the rules

Feature: Every newly-created customer tenant carries its founding OWNER as billing-owner-of-record from creation.
Must:
  - `create_tenant_with_owner` sets the new tenant's `billing_owner_user_id` to the OWNER user it creates, in the SAME transaction as both inserts (all-or-nothing).
  - The billing-owner pointer equals the returned `user_id` (the founding OWNER) — never NULL, for the public-signup and verified-domain-owner tenant-creation path.
  - No change to the returned `(tenant_id, user_id)` tuple, to error behavior (`EmailAlreadyRegisteredError` on duplicate email), or to the join-existing-tenant path (`join_verified_tenant_domain` mints a MEMBER, never an owner — deliberately unchanged).
Reject:
  - Duplicate email -> "ERR_AUTH_EMAIL_TAKEN" (existing behavior, unchanged — the whole transaction including the pointer rolls back).
Accept: Given no existing user with the email, When a personal or business signup creates a new tenant, Then the tenant row's `billing_owner_user_id` equals the created OWNER user's id (not NULL).
Assumptions: ⚠ that a plain post-flush column assignment inside the existing `begin()` block emits the cycle-breaking UPDATE correctly — biggest risk: SQLAlchemy INSERT ordering. Mitigated because `use_alter=True` excludes the tenants→users FK from the INSERT dependency sort, so `tenants` inserts first (billing_owner NULL, valid), `users` second, then the dirty assignment flushes as an UPDATE after both rows exist. If wrong: FK violation surfaces LOUD in the red/green test, never silently.

---

## 3 · CONTRACT — freeze the shape

```
SqlAlchemyIdentityRepository.create_tenant_with_owner(
    *, tenant_name: str, email: str, password_hash: str,
    account_type: str = "business", plan_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]        # (tenant_id, user_id) — SIGNATURE UNCHANGED

POSTCONDITION (new): the persisted TenantRow(id=tenant_id).billing_owner_user_id == user_id
  (the founding Role.OWNER), committed atomically with both inserts.

Implementation shape (frozen):
    async with self._session.begin():
        self._session.add(tenant)          # billing_owner_user_id left NULL at INSERT
        self._session.add(user)
        await self._session.flush()        # INSERT tenants (NULL) then users
        tenant.billing_owner_user_id = user.id   # dirty -> UPDATE on commit (cycle-safe)
  IntegrityError -> EmailAlreadyRegisteredError (unchanged; rolls back the whole txn).

UNCHANGED: return tuple, error mapping, join_verified_tenant_domain (MEMBER, no pointer).
```

`Least-sure flag surfaced at freeze:` [contract] the post-flush assignment relies on `use_alter` excluding the tenants→users FK from INSERT ordering so `tenants` inserts before `users`; if that ordering assumption is wrong the test goes RED with a FK violation (loud, never silent) — cost: swap to an explicit two-step (add tenant → flush → add user → flush → assign), same transaction.
Status: FROZEN @ v1 — approved by auto (project-lead); closes the billing-owner-of-record milestone-goal gap (fresh signups previously left billing_owner_user_id NULL). Low-risk column population, no authz decision, no guard weakened.

---

## 4 · TESTS — failing-first (red)

Plan: test_<accept> — assert the §1 Accept line's Then (behavior, not internals).
Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code

Scope (may touch): `/apps/gateway/src/gateway/tenants/infrastructure/repository.py`
Strategy & known-problem fixes: (1) in `create_tenant_with_owner`, after `self._session.add(tenant); self._session.add(user)`, insert `await self._session.flush()` then `tenant.billing_owner_user_id = user.id`, all inside the existing `async with self._session.begin()` block — TRAP: assigning before flush trips a runtime FK violation (user row absent) → mitigated by flush-first (mirrors `join_verified_tenant_domain`'s own flush-before-dependent-write). TRAP: breaking atomicity → mitigated by staying inside the one `begin()`. No signature/return change.
Strategy actually used: as planned — one `await self._session.flush()` + `tenant.billing_owner_user_id = user.id` inside the existing `begin()` block; use_alter confirmed on `TenantRow.billing_owner_user_id`, so INSERT ordering is tenants→users and the assignment flushes as a cycle-breaking UPDATE. NO signature/return/error change. CROSS-TASK RECONCILIATION (Tin-approved "invariant wins", 2026-07-16): populating billing_owner made the founding OWNER un-SCIM-deactivatable (parent HOOK 2 → 409), so 5 sibling FROZEN tests were reconciled WITHOUT weakening — 4 scim_provisioning deactivation-mechanics tests (`test_deactivated_owner_cannot_mint_new_scim_token_or_key`, `..._rotate_..._or_key`, `test_deactivated_user_cannot_login_with_password`, `test_already_issued_jwt_survives_deactivation_until_expiry`) now call a new `conftest.detach_billing_owner` to move the now-protected pointer aside before deactivating (intent = deactivation machinery, unchanged; mirrors the parent suite's own `test_scim_deactivates_non_owner_normally` pattern); + 1 billing_owner_of_record `test_get_billing_owner_null_when_unset` NULLs the pointer explicitly to still assert the GET nullable shape. Test files touched (reconciliation, NOT this task's own contract): `tests/scim_provisioning/{conftest.py,test_scim_provisioning.py}`, `tests/billing_owner_of_record/test_billing_owner_of_record.py`.
Code lives in: `./src/`   ·   Constraints: change no test, no contract; allow-list packages only.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no test or contract altered during build — new suite 3/3 green; the 5 reconciled sibling tests green; targeted regression 149+184+36+8 green across EVERY signup-touching surface (identity/tenants, scim_provisioning, seat_billing, account_type, plan_seat_cap, member_invite, superadmin, migrations/seat_backfill). FULL bulk suite: 3922 passed / 32 failed — the 32 are EXCLUSIVELY cross-dir Redis-contention flakes (semantic_cache, response_caching, request_log_metering_fields, saml_sso, guardrails, spend_windows, obs_callbacks) + 1 pre-existing timing-flaky WS test (realtime test_auth_timeout_closes_4408) — EVERY failing dir passes in isolation (15/14/12/30/36/16/14 ✓), zero code-path overlap with create_tenant_with_owner; the delta is run-shape (shared -n12 Redis pool), not this change (documented shared-:5433/Redis fragility — see [[gateway-health-milestone]]). This task's OWN §3 contract untouched; sibling frozen-test reconciliations are Tin-approved (Option A) and intent-preserving.
- [x] green was EARNED — the new suite asserts the persisted `tenants.billing_owner_user_id` equals the founding OWNER's id via raw SQL (not an ORM echo), incl. a "matches the sole owner row" cross-check; the reconciliations preserve each sibling test's original assertion (deactivation mechanics / GET nullable shape), only moving the now-protected pointer out of the setup.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps; parameterized SQL; no authz decision added (pure column population at creation, inside the existing atomic txn).

Build expectations (from §1 Accept + §3 CONTRACT): a fresh business OR personal signup persists `tenants.billing_owner_user_id == the created OWNER user_id` (never NULL), atomically — confirmed by `tests/billing_owner_signup_population` (3 tests, raw-SQL assertion on the tenants row) + the sole-owner cross-check.

### GATE RECORD
Outcome: PASS
Reviewed by: orchestrator (add project-lead) · date: 2026-07-16 — human sign-off PENDING Tin at PR time (held uncommitted with the account-tiers-billing milestone). Closes the billing-owner-of-record milestone-goal gap (every tenant — existing via backfill, new via signup — now carries a billing owner). Cross-task consequence (SCIM-deprovisioning of the billing owner returns 409 until reassigned) is Tin-approved (Option A, 2026-07-16). No security finding (pure column population at creation; no authz decision added). 32 full-suite failures adjudicated pre-existing Redis-contention flakes (green in isolation), NOT a regression.
OBSERVE: [SPEC · open] Product behavior now locked: a tenant's billing-owner cannot be SCIM/IdP-deprovisioned while designated — a future "force deprovision auto-reassigns to next billing-capable member" could soften enterprise IdP flows (deferred).

