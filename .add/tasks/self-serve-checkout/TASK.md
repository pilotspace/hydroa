# TASK: PaymentProvider port (dev+stripe-shaped) + tenant-scoped checkout for plan/credits/seats + plan & credits CTAs

slug: self-serve-checkout · created: 2026-07-17 · stage: production
milestone: commercial-self-serve
sensitivity: security
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: tests   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
risk: high   <!-- privilege-boundary + money-mutation seam; keep autonomy honest at the freeze -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission` — StrEnum of capabilities; NO plan/checkout-write member exists (closest is `BUDGETS_MANAGE`, held by OWNER/ADMIN/BILLING_ADMIN — too wide). `ROLE_PERMISSIONS` maps `Role.OWNER = frozenset(Permission)` (import-time completeness guard), `Role.BILLING_ADMIN = {BUDGETS_MANAGE, USAGE_READ, OPS_READ, INVOICES_READ}`. `require_permission(perm) -> Depends` is the per-surface gate (`_check` reads `ROLE_PERMISSIONS.get(identity.role)`, raises `AUTH_FORBIDDEN`/403). `authorize_tenant_scope(identity, target_tenant_id)` = 403 unless SUPERADMIN or own tenant.
- `apps/gateway/src/gateway/tenants/domain/entities.py:BILLING_CAPABLE_ROLES` — `frozenset({Role.OWNER, Role.BILLING_ADMIN})`; `Identity` frozen dataclass (`user_id`, `tenant_id`, `email`, `role`). The exact role floor this task must enforce.
- `apps/gateway/src/gateway/keys/api/deps.py:get_identity` — the tenant-scoped auth dependency (decodes Bearer JWT → `Identity`); tenant scope is `identity.tenant_id`, NEVER a body field. `tenants/api/plan_router.py:get_plan` (`GET /admin/plan`, `Depends(get_identity)`) is the tenant-scoped router to mirror.
- `apps/gateway/src/gateway/credits/application/topup_service.py:topup(session_factory, *, tenant_id, amount_usd: Decimal, idempotency_key: str, note=None, actor_user_id=None) -> TopupResult` — THE reusable, idempotent, row-locked credit-mutation domain op (`TopupResult.created` True→new/False→replay; raises `CREDITS_IDEMPOTENCY_KEY_CONFLICT`). Checkout credit_topup reuses this verbatim.
- `apps/gateway/src/gateway/credits/infrastructure/orm.py:CreditLedgerRow` — `credit_ledger`, append-only (DB UPDATE/DELETE RULEs), `entry_type='topup'`, `amount_usd Numeric(14,8)` signed, USD-only (no currency col), keyed by caller `idempotency_key` (two unique indexes: per-tenant + global). Balance = SUM(amount_usd).
- `apps/gateway/src/gateway/tenants/api/platform_plans_router.py:put_platform_tenant_plan` — the superadmin PUT (`require_superadmin`) that mutates the plan **inline**: `tenant_row.plan_id = body.plan_id; tenant_row.seat_cap = resolved_seat_cap; await session.commit()` then `emit_platform_audit(action="platform.plan.assign")`. There is NO importable shared plan-assign use-case — only `_require_target_tenant(identity, tenant_id, session) -> TenantRow`.
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:PlanRow` — `plans` catalog: `id`, `name` (unique, free-text; seeded free/starter/pro/team/enterprise), `display_name`, `seat_cap`, `base_price_usd_monthly Numeric(12,2)|None` (seed: free=NULL, starter=1, pro=20, team=99, enterprise=NULL), `seat_price_usd_monthly`. NO `tier`/`audience`/`self_serve`/`is_enterprise` column. `TenantRow.account_type` = `'personal'|'business'` (default `'business'`, NULL on platform tenant; CHECK-constrained).
- `apps/gateway/src/gateway/audit/application/audit_writer.py:record_audit(session_factory, event: AuditEvent)` — fire-and-forget, fail-open, own session, swallows all exceptions; schedule via `asyncio.ensure_future`, never await on the hot path. `audit/domain/audit_event.py:AuditEvent` (frozen: `tenant_id`, `actor_user_id`, `actor_email`, `action`, `target_type`, `target_id`, `result`, `metadata`; tenant-scoped event MUST carry an actor). Tenant-scoped call sites inline `ensure_future(record_audit(...AuditEvent(...)))` (see `credits/api/router.py:post_credits_topup`).
- `apps/gateway/src/gateway/core/config.py:Settings` — boot-guard precedent is `@model_validator(mode="after")` `_validate_otel_config` (`if self.otel_enabled and not self.otel_export_url: raise ValueError(...)`); kill-switch precedent `credits_gate_enabled: bool`, `platform_credential_fallback_enabled: bool = True`. NO `EmptyUpstreamKeyError` class exists — the "flag-on + empty required key ⇒ boot error" idiom IS this validator.
- `apps/gateway/src/gateway/billing/application/invoice_generator.py:InvoiceGenerator._load_base_price` — reads `PlanRow.base_price_usd_monthly` at generation time and emits a `line_type="base"` invoice line (`billing/infrastructure/orm.py:InvoiceLineRow`); inert when NULL. Confirms: a plan upgrade needs NO new invoice-write code — the next invoice inherits the new base fee automatically.
- `apps/gateway/src/gateway/main.py:create_app` — routers self-mount via `app.include_router(...)`, each owns its `prefix`; tenant-self = `/admin/<thing>`, superadmin = `/admin/platform/...`.
- FE: `apps/dashboard/components/plan/PlanSeatsPage.tsx` (read-only; literal `Seat pricing coming soon.` card) · `apps/dashboard/components/credits/CreditsPage.tsx` (zero-state `"Your platform operator manages credit top-ups — contact them…"`) · `apps/dashboard/components/keys/CreateKeyDialog.tsx` + `keys/KeysPage.tsx:createKeyMutation` (dialog + `useMutation(bffPost)` + zod `.safeParse` + `BffError.status===422` field-error pattern to mirror) · `apps/dashboard/lib/bff-client.ts:bffPost` + `apps/dashboard/app/api/gw/[...path]/route.ts` (BFF proxy).

Context (working folder): `.add/milestones/commercial-self-serve/MILESTONE.md` (shared decisions — verbatim) · `.add/GLOSSARY.md` terms: Credit ledger, base fee, 5-tier catalog, account_type, Seat/Effective seat cap, billing owner/BILLING_CAPABLE_ROLES · no new pyproject deps expected for the dev adapter (stripe SDK is a config-gated allow-list item for the stripe adapter only). New Alembic migration required (checkout_sessions table + two additive `plans` columns).

Honors (patterns / conventions):
- PROJECT.md invariants: append-only ledger (never mutate credit_ledger in place) · no outbound IO without timeout + bounded retry + circuit breaker (stripe adapter) · every tenant-owned row carries `tenant_id` and every query filters `WHERE tenant_id = identity.tenant_id`.
- CONVENTIONS.md layering: domain (`typing.Protocol` port) → application (use-case) → infrastructure (adapters/repo) → api; dependencies point inward (backend-architect stance).
- Audit is fire-and-forget/fail-open (audit_writer precedent) — an audit failure NEVER fails the primary mutation.
- Additive-migration-only on `plans` (seat_price/base_price precedent — migration-seeded, no runtime plans CRUD).

Anchors the contract cites: `Permission` + `require_permission` + `authorize_tenant_scope` + `BILLING_CAPABLE_ROLES` · `get_identity`/`Identity` · `topup_service.topup` + `CreditLedgerRow` · `PlanRow` (+ new `self_serve`/`audience` cols) · `TenantRow.account_type`/`plan_id`/`seat_cap` · `record_audit`/`AuditEvent` · `Settings` boot-guard validator · `InvoiceGenerator._load_base_price` · `bffPost`/gw proxy route.

Seams consulted: none in `.add/SEAMS.md` for payments (new seam this task introduces).

Issues/Risks (→ feed §1):
- **I1 — no shared plan-assign op.** Plan mutation is inlined in the superadmin router. Honoring the milestone "SAME domain op, never a parallel write path" REQUIRES extracting the plan-assign write into a shared application use-case that BOTH the superadmin router and checkout call. This is an in-scope refactor of frozen-adjacent code (superadmin behavior must stay byte-identical).
- **I2 — enterprise vs free are indistinguishable by price.** `base_price_usd_monthly IS NULL` is true for BOTH free ($0) and enterprise (contact-sales). "Enterprise not self-serve" therefore CANNOT be derived from price alone — needs an explicit signal (new `plans.self_serve` column or a magic `name=='enterprise'`).
- **I3 — no account_type↔tier signal in data.** `plans` has no audience/personal/business column; the personal={free,starter,pro} / business={team,enterprise} split lives only in seed convention. A personal tenant self-selecting a business plan needs an explicit gate (new `plans.audience` column, or a hardcoded map, or deferral).
- **I4 — Idempotency-Key HTTP header does NOT survive the BFF.** `bffPost` sends no custom headers and the gw proxy forwards only `Authorization`+`Content-Type`. The existing credits topup takes the key as a *header* — a browser-originated checkout MUST carry `idempotency_key` in the JSON **body**, not a header (or the BFF allow-list must be widened — rejected as larger surface).
- **I5 — port is create+confirm across two HTTP calls ⇒ the session must be persisted** to be confirmable, and confirm must be idempotent + tenant-scoped-on-lookup or it becomes a cross-tenant / double-charge hole. New `checkout_sessions` table with a row lock is the seam's own state.
- **I6 — stripe adapter is real outbound IO** → timeout+retry+breaker mandatory; a provider failure must degrade cleanly with NO partial mutation.

Related intent: MILESTONE.md Exit criterion 4 ("A tenant owner upgrades the plan and tops up credits from /app/plan and /app/credits through the checkout seam without any superadmin action, mutation audit-logged, dev adapter default-on") + shared decisions (PaymentProvider port · same domain ops · checkout audit-logged · self-serve never widens privilege). GLOSSARY: makes the shipped 5-tier catalog + Credit ledger self-serve-purchasable.

Ground SHA: `102ec65` — all line refs above are symbol-cited; any line number is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Self-serve checkout — a tenant owner/billing-admin buys a plan upgrade or a credit top-up through a `PaymentProvider` port (dev default, stripe config-gated), applying the SAME domain mutations the superadmin path uses, fully audited, with zero platform-operator action.

Framings weighed: **payment-port + persisted two-step session (create→confirm), mutation orchestrated by a checkout use-case that reuses existing domain ops** (chosen) · single-call "apply immediately" endpoint (rejected — the port is create+confirm and stripe needs a redirect between them; no place to show price math) · adapter itself performs the plan/credit mutation (rejected — violates "same domain op, never a parallel write path"; the adapter is the PAYMENT abstraction only).

**Scope recommendation (orchestrator decides — surfaced, not assumed):** freeze **plan_upgrade + credit_topup** now; **DEFER seat_change** to a follow-up task. Rationale: (a) this task's Exit criterion names only plan-upgrade + credit-top-up; (b) the in-scope FE CTAs are "Upgrade plan" + "Add credits" only — no seat dialog; (c) seat purchasing pulls in proration + per-seat `line_type='seat'/'proration'` invoice lines (`seat_pricer.py`) + effective-seat-cap admission interplay — a materially larger, separately-securable surface that pushes this contract past one-task size. The `CheckoutIntent` union and `intent_type` field are designed so a `seat_change` variant is a purely **additive** future change (no reopen of this frozen shape).

Must:
<must>
  - **M1 (tenant scope from session, never body).** Every checkout endpoint derives `tenant_id`/`actor_user_id` from `get_identity` (JWT) ONLY. Request bodies carry NO `tenant_id`. `confirm`/`get` resolve the session's stored `tenant_id` and serve it only to its owning tenant.
  - **M2 (role floor = owner or billing_admin).** All checkout endpoints are guarded by `require_permission(Permission.BILLING_MANAGE)`, a NEW permission held by exactly `{Role.OWNER, Role.BILLING_ADMIN}` (OWNER auto-holds via `frozenset(Permission)`; BILLING_ADMIN added explicitly). ADMIN/OPERATOR/VIEWER/MEMBER are refused. `BUDGETS_MANAGE` is deliberately NOT reused (it would admit ADMIN).
  - **M3 (idempotent confirm).** `confirm(session_id)` transitions the session `pending→succeeded` exactly once under `SELECT … FOR UPDATE`, applying the mutation and the status flip atomically; a repeat confirm returns the stored `CheckoutResult` and re-applies nothing. Credit path additionally threads the session's `idempotency_key` into `topup_service.topup` (defense-in-depth via the `credit_ledger` unique index).
  - **M4 (idempotent create).** `create_checkout` with a repeated `(tenant_id, idempotency_key)` returns the EXISTING session, never a second one (UNIQUE(tenant_id, idempotency_key)).
  - **M5 (dev adapter marked in audit).** Every `checkout_sessions` row and every audit event records `provider` (`"dev"`/`"stripe"`); a dev-adapter mutation is unmistakable (`metadata.provider="dev"`).
  - **M6 (no card / PII stored gateway-side).** `checkout_sessions` has NO PAN/CVV/cardholder/PII columns; the stripe adapter uses hosted checkout (card entered on Stripe); the gateway persists only an opaque `provider_ref`. The dev adapter stores none.
  - **M7 (checkout is audit-logged).** Every successful confirm writes a fire-and-forget tenant-scoped `AuditEvent` (`action="checkout.plan_upgrade"|"checkout.credit_topup"`, actor = `identity.user_id`/`email`, `target_tenant_id = identity.tenant_id`, `metadata={provider, session_id, from_plan/to_plan or amount_usd, balance_after}`); an audit failure never fails the mutation.
  - **M8 (mutation via existing domain ops — never a parallel write).** credit_topup calls `credits.application.topup_service.topup(...)` verbatim; plan_upgrade calls a newly-EXTRACTED shared `assign_plan` use-case that the superadmin router is refactored to also call. Superadmin plan/credit endpoint behavior and shapes stay byte-identical.
  - **M9 (upgrades immediate; explicit price math before confirm).** On plan_upgrade confirm, `TenantRow.plan_id` (+ inherited `seat_cap`) is updated immediately; the next invoice inherits the new `base_price_usd_monthly` via `InvoiceGenerator._load_base_price` (no proration, no new invoice-write code). `create_checkout` returns a server-computed `preview` (current base, target base, delta, currency, `effective:"immediate"`) that the dialog shows before confirm.
  - **M10 (adapter selection + boot guard).** Provider chosen by config: dev is DEFAULT ON; stripe is selected only when configured. Selecting stripe with an empty/whitespace-only key = boot error (`@model_validator(mode="after")` raising `ValueError`, mirroring `_validate_otel_config`). An absent stripe key = stripe disabled, dev used.
  - **M11 (kill switch).** A `payment_checkout_enabled` kill switch (default ON) gates the endpoints; when OFF they reject cleanly with no mutation.
  - **M12 (stripe IO designed for failure).** Stripe adapter outbound calls carry timeout + bounded retry + circuit breaker; breaker-open / timeout degrades to `payment_provider_unavailable` with NO partial mutation.
</must>

Reject:
<reject>
  - caller lacks `Permission.BILLING_MANAGE` (member/viewer/admin/operator) -> "ERR_AUTH_FORBIDDEN" (403, existing)
  - confirm/get a session whose stored tenant_id != caller's tenant -> "checkout_not_found"   # never reveals cross-tenant existence
  - unknown session_id -> "checkout_not_found"
  - confirm a session not in `pending` (already succeeded is idempotent-OK; failed/expired) -> "checkout_not_confirmable"
  - target plan is not self-serve (enterprise) -> "plan_not_self_serve"
  - target plan audience != tenant.account_type -> "plan_account_type_mismatch"
  - target plan base fee <= current plan base fee, target != current (a downgrade) -> "plan_downgrade_not_self_serve"
  - target plan == current plan -> "plan_unchanged"
  - unknown target_plan_id -> "plan_not_found"
  - credit amount_usd <= 0 / non-finite / unparseable -> "amount_invalid"
  - credit amount_usd above the self-serve ceiling -> "amount_exceeds_max"
  - missing idempotency_key in body -> "idempotency_key_required"
  - reused idempotency_key with a different intent/amount/plan -> "idempotency_key_conflict"   # 409, mirrors CREDITS_IDEMPOTENCY_KEY_CONFLICT
  - checkout disabled by kill switch -> "checkout_disabled"
  - stripe unreachable (timeout / breaker open) -> "payment_provider_unavailable"   # 503, no mutation
</reject>

After:
<after>
  - On plan_upgrade success: `TenantRow.plan_id` = target (seat_cap inherited per existing rule), session `status='succeeded'`, one `checkout.plan_upgrade` audit row exists, superadmin plan endpoint unaffected, the tenant's next invoice will carry the target base fee. No credit_ledger row written.
  - On credit_topup success: exactly one `credit_ledger` `entry_type='topup'` row for `amount_usd` (balance = prior + amount), session `status='succeeded'`, one `checkout.credit_topup` audit row, keyed by the session idempotency_key.
  - On any Reject: no plan change, no ledger row, no invoice line, session either not created or left non-`succeeded`; balance and plan_id unchanged.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **A1 — account_type↔plan gating via a new `plans.audience` column.** Lowest confidence because the split lives only in seed convention today (I3) and there are three defensible answers: (a) add `plans.audience` ('personal'|'business') + reject cross-audience [chosen]; (b) hardcode a name→audience map in the checkout domain; (c) drop audience-gating in v1 and rely on FE-only filtering. If wrong: a wasted additive migration + one reject rule, OR (if (c) chosen and later needed) a personal tenant could self-upgrade onto a business plan — a billing-correctness (not privilege) defect. **Needs a ruling at freeze.**
  ⚠ **A2 — the role floor is a NEW `Permission.BILLING_MANAGE` = {OWNER, BILLING_ADMIN}, NOT a reuse of `BUDGETS_MANAGE`.** Lowest-confidence-adjacent because the milestone says "reuse existing RBAC permissions" yet no existing member fits the exact owner/billing_admin floor (BUDGETS_MANAGE admits ADMIN; INVOICES_READ is read-only). Reading "reuse the RBAC *mechanism*" and adding a member (precedent: RATE_CARDS_MANAGE, INVOICES_READ were added the same way) is the security-correct choice. If wrong (Tin wants a literal existing perm): ADMIN gains checkout, widening the money-mutation floor beyond intent. **Needs a ruling at freeze.**
  - [ ] A3 — "Enterprise not self-serve" is modeled by a new `plans.self_serve: bool` column (seed: enterprise=false, others=true), NOT the ambiguous NULL-price test (I2). Confirm or say "use name=='enterprise'".
  - [ ] A4 — Downgrades are OUT OF SCOPE (rejected), upgrades apply immediately with NO mid-cycle proration (next invoice, full period). Confirm this is the intended v1 billing behavior.
  - [ ] A5 — seat_change DEFERRED to a follow-up task (see Scope recommendation). Confirm, or ask for the smallest seat slice.
  - [ ] A6 — idempotency_key travels in the JSON **body** (not the `Idempotency-Key` header), because the BFF strips custom headers (I4). Confirm.
  - [ ] A7 — a self-serve credit ceiling exists (proposed `amount_exceeds_max` at e.g. $10,000 USD/topup) to bound fat-finger/abuse. Confirm the cap value or waive it.
  - [ ] A8 — plan-assign extraction keeps superadmin behavior byte-identical (I1); the shared use-case is `tenants/application/plan_assignment.py:assign_plan(...)`. Confirm the refactor is in-scope.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner upgrades plan through the dev adapter          # M8, M9
  Given a personal tenant on the "starter" plan and a logged-in OWNER
  And the payment provider is "dev" (default on)
  When they create a plan-upgrade checkout to "pro" and confirm it
  Then the session status is "succeeded" and the tenant's plan_id is now "pro"
  And the create preview showed current_base=1.00, target_base=20.00, currency=USD, effective="immediate"
  And no credit_ledger row was written

Scenario: Billing-admin tops up credits                        # M2, M8
  Given a tenant with balance 5.00 and a logged-in BILLING_ADMIN
  When they create a credit-topup checkout for amount_usd=25.00 and confirm it
  Then one credit_ledger entry_type='topup' row for 25.00 exists and balance is 30.00
  And the topup was written through credits.topup_service (same op as superadmin)

Scenario: Tenant scope comes from the session, never the body  # M1
  Given tenant A's OWNER
  When they create a checkout with a body that also contains tenant B's id
  Then the created session's tenant_id is tenant A (the body's tenant_id is ignored)
  And tenant B's plan and balance are unchanged

Scenario: A member cannot checkout                             # R: ERR_AUTH_FORBIDDEN
  Given a logged-in MEMBER (no BILLING_MANAGE)
  When they POST any checkout endpoint
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And no session, plan change, or ledger row is created

Scenario: An admin cannot checkout (floor is owner/billing_admin) # M2, R: ERR_AUTH_FORBIDDEN
  Given a logged-in ADMIN (holds BUDGETS_MANAGE but not BILLING_MANAGE)
  When they POST a checkout endpoint
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And no session, plan change, or ledger row is created

Scenario: Double-confirm never double-applies                  # M3 (concurrency/duplicate)
  Given a pending credit-topup session for 25.00
  When confirm is called twice (concurrently) for the same session_id
  Then exactly one credit_ledger topup row exists and balance rose by 25.00 once
  And both responses return the same succeeded CheckoutResult

Scenario: Repeated create is idempotent                        # M4 (duplicate)
  Given a plan-upgrade create with idempotency_key K
  When the same tenant creates again with idempotency_key K
  Then the same session_id is returned and no second session row exists

Scenario: Confirming another tenant's session is invisible     # M1, R: checkout_not_found
  Given a pending session created by tenant A
  When tenant B's OWNER confirms that session_id
  Then the response is 404 checkout_not_found (existence never revealed)
  And tenant A's session stays pending and unapplied

Scenario: Unknown session id                                   # R: checkout_not_found
  When confirm/get is called with a session_id that does not exist
  Then the response is 404 checkout_not_found
  And nothing is mutated

Scenario: Re-confirming a failed session                       # R: checkout_not_confirmable
  Given a session in status "failed"
  When confirm is called
  Then the response is 409 checkout_not_confirmable
  And no plan change or ledger row occurs

Scenario: Enterprise is not self-serve                         # M9, R: plan_not_self_serve
  Given a business tenant and target plan "enterprise" (self_serve=false)
  When they create a plan-upgrade checkout to "enterprise"
  Then the response is 422 plan_not_self_serve (contact sales)
  And the tenant's plan_id is unchanged

Scenario: Cross-audience plan is rejected                      # R: plan_account_type_mismatch
  Given a personal tenant and target plan "team" (audience=business)
  When they create a plan-upgrade checkout to "team"
  Then the response is 422 plan_account_type_mismatch
  And the tenant's plan_id is unchanged

Scenario: Downgrade is rejected                                # R: plan_downgrade_not_self_serve
  Given a tenant on "pro" (base 20.00) targeting "starter" (base 1.00)
  When they create a plan-upgrade checkout to "starter"
  Then the response is 422 plan_downgrade_not_self_serve
  And the tenant's plan_id is unchanged

Scenario: Upgrading to the current plan is rejected            # R: plan_unchanged
  Given a tenant on "pro" targeting "pro"
  When they create a plan-upgrade checkout
  Then the response is 422 plan_unchanged
  And nothing is mutated

Scenario: Unknown target plan                                  # R: plan_not_found
  When a plan-upgrade create names a plan_id that is not in the catalog
  Then the response is 404 plan_not_found
  And nothing is mutated

Scenario: Non-positive credit amount                           # R: amount_invalid (boundary)
  When a credit-topup create has amount_usd="0" (or "-5" / "abc")
  Then the response is 422 amount_invalid
  And the balance is unchanged

Scenario: Credit amount above the self-serve ceiling           # R: amount_exceeds_max (boundary)
  When a credit-topup create has amount_usd above the configured ceiling
  Then the response is 422 amount_exceeds_max
  And the balance is unchanged

Scenario: Missing idempotency key                              # R: idempotency_key_required
  When any create is called with no idempotency_key in the body
  Then the response is 400 idempotency_key_required
  And no session is created

Scenario: Reused idempotency key with a different amount        # R: idempotency_key_conflict
  Given a credit-topup created with idempotency_key K for 25.00
  When a create reuses K for 50.00
  Then the response is 409 idempotency_key_conflict
  And no second ledger row or session is created

Scenario: Checkout disabled by kill switch                     # M11, R: checkout_disabled
  Given payment_checkout_enabled is false
  When any checkout endpoint is called
  Then the response is 403 checkout_disabled
  And nothing is mutated

Scenario: Stripe provider unreachable degrades cleanly         # M12, R: payment_provider_unavailable
  Given provider="stripe" and the stripe API times out / breaker is open
  When a checkout is created or confirmed
  Then the response is 503 payment_provider_unavailable
  And no plan change or ledger row occurs (no partial mutation)

Scenario: Selecting stripe with an empty key fails to boot     # M10
  Given payment_provider="stripe" and payment_stripe_api_key="" (or whitespace)
  When the gateway starts
  Then startup raises a ValueError boot error naming the missing key
  And the process does not serve traffic

Scenario: Dev-adapter mutation is marked in the audit trail    # M5, M7
  Given provider="dev"
  When a plan_upgrade confirm succeeds
  Then a checkout.plan_upgrade audit row exists with metadata.provider="dev", actor=the caller, target_tenant_id=the caller's tenant

Scenario: An audit failure does not fail the checkout          # M7 (partial failure)
  Given the audit writer raises on record
  When a credit_topup confirm succeeds
  Then the balance still rose and the session is "succeeded" (audit failure swallowed)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

**Port (domain — `payments/domain/ports.py`):**
```python
class CheckoutIntent(Protocol-adjacent frozen dataclass):
    tenant_id: uuid.UUID          # server-set from Identity, NEVER a request field
    actor_user_id: uuid.UUID
    actor_email: str
    intent_type: Literal["plan_upgrade", "credit_topup"]   # "seat_change" reserved (deferred)
    idempotency_key: str
    target_plan_id: uuid.UUID | None   # plan_upgrade only
    amount_usd: Decimal | None         # credit_topup only

class CheckoutSession (frozen dataclass / row projection):
    session_id: uuid.UUID
    provider: Literal["dev", "stripe"]
    status: Literal["pending", "succeeded", "failed", "expired"]
    redirect_url: str | None           # stripe hosted-checkout URL; None for dev
    preview: CheckoutPreview

class CheckoutResult (frozen dataclass):
    session_id: uuid.UUID
    provider: Literal["dev", "stripe"]
    status: Literal["succeeded", "failed"]
    applied: dict   # {new_plan_id,...} | {ledger_entry_id, balance_after_usd}
    error: str | None

class PaymentProvider(typing.Protocol):
    async def create_checkout(self, intent: CheckoutIntent) -> CheckoutSession: ...
    async def confirm(self, session_id: uuid.UUID) -> CheckoutResult: ...
# Adapters: DevPaymentProvider (auto-succeeds, records everything, default) ·
#           StripePaymentProvider (config-gated; timeout+retry+breaker on all outbound IO).
# The adapter is the PAYMENT abstraction ONLY. Applying the plan/credit mutation is
# orchestrated by the CheckoutService use-case, which calls the EXISTING domain ops
# (topup_service.topup / the extracted assign_plan) — never the adapter.
```

**Endpoints (tenant-scoped; `get_identity` + `require_permission(Permission.BILLING_MANAGE)`; idempotency_key in body):**
```
POST /admin/checkout/plan-upgrade   body: { target_plan_id: uuid, idempotency_key: str }
  200 -> { session_id, provider, status, redirect_url?, preview: { intent:"plan_upgrade",
           current_plan, target_plan, current_base_usd, target_base_usd, delta_usd,
           currency:"USD", effective:"immediate" } }
  400 -> { error: "idempotency_key_required" }
  403 -> { error: "ERR_AUTH_FORBIDDEN" | "checkout_disabled" }
  404 -> { error: "plan_not_found" }
  409 -> { error: "idempotency_key_conflict" }
  422 -> { error: "plan_not_self_serve" | "plan_account_type_mismatch"
                 | "plan_downgrade_not_self_serve" | "plan_unchanged" }
  503 -> { error: "payment_provider_unavailable" }

POST /admin/checkout/credit-topup   body: { amount_usd: str, idempotency_key: str, note?: str }
  200 -> { session_id, provider, status, redirect_url?, preview: { intent:"credit_topup",
           amount_usd, currency:"USD", balance_before_usd, balance_after_preview_usd } }
  400 -> { error: "idempotency_key_required" }
  403 -> { error: "ERR_AUTH_FORBIDDEN" | "checkout_disabled" }
  409 -> { error: "idempotency_key_conflict" }
  422 -> { error: "amount_invalid" | "amount_exceeds_max" }
  503 -> { error: "payment_provider_unavailable" }

POST /admin/checkout/{session_id}/confirm   body: {}
  200 -> { session_id, provider, status:"succeeded", applied: {...} }
  403 -> { error: "ERR_AUTH_FORBIDDEN" | "checkout_disabled" }
  404 -> { error: "checkout_not_found" }          # incl. cross-tenant session (never revealed)
  409 -> { error: "checkout_not_confirmable" }
  503 -> { error: "payment_provider_unavailable" }

GET  /admin/checkout/{session_id}
  200 -> CheckoutSession
  404 -> { error: "checkout_not_found" }
```

**Schema:**
```
NEW table checkout_sessions (tenant-scoped; the seam's own state — NO card/PII columns):
  id uuid PK · tenant_id uuid NOT NULL · actor_user_id uuid NOT NULL
  provider text NOT NULL ('dev'|'stripe') · intent_type text NOT NULL
  intent_payload jsonb NOT NULL           # {target_plan_id} | {amount_usd}
  idempotency_key text NOT NULL
  status text NOT NULL ('pending'|'succeeded'|'failed'|'expired')
  provider_ref text NULL                  # opaque stripe session id; NULL for dev — NEVER card data
  applied_ref jsonb NULL · preview jsonb NOT NULL
  created_at timestamptz · confirmed_at timestamptz NULL · expires_at timestamptz
  UNIQUE(tenant_id, idempotency_key)       # idempotent create (M4)
  Access: every query filters WHERE tenant_id = identity.tenant_id; confirm takes SELECT…FOR UPDATE
          on the row and flips pending→succeeded in ONE transaction with the mutation (M3).

ADDITIVE columns on plans (migration-seeded only; no runtime plans CRUD):
  self_serve boolean NOT NULL DEFAULT false   # seed: free/starter/pro/team=true, enterprise=false (I2)
  audience   text NULL                         # seed: free/starter/pro='personal', team/enterprise='business' (I3)

REUSED (unchanged shape): credit_ledger (topup_service.topup) · tenants.plan_id/seat_cap/account_type
  (via extracted assign_plan) · audit_events (record_audit) · invoice_lines line_type='base'
  (InvoiceGenerator inherits the new base fee — no write here).
```

Glossary deltas:
- **PaymentProvider** (milestone-declared, defined here): the gateway-owned payment-authorization port — `create_checkout(intent) -> CheckoutSession` + `confirm(session_id) -> CheckoutResult`; adapters `dev` (default, auto-succeeds) and `stripe` (config-gated). The port authorizes PAYMENT only; the plan/credit mutation is applied by `CheckoutService` through the existing domain ops.
- **Checkout session**: a persisted `checkout_sessions` row representing one create→confirm attempt; carries `provider`, `intent_type`, `idempotency_key`, a one-way `status`, and NO card/PII data.
- **Checkout intent**: the value object (`plan_upgrade` | `credit_topup`; `seat_change` reserved-deferred) whose `tenant_id` is server-set from the JWT, never a request field.
- **Permission.BILLING_MANAGE**: the new capability held by exactly `{OWNER, BILLING_ADMIN}` (mirrors `BILLING_CAPABLE_ROLES`) gating every self-serve checkout write; distinct from `BUDGETS_MANAGE` (which admits ADMIN).
- **plans.self_serve / plans.audience**: the two additive catalog signals that make "enterprise = contact sales" and "personal vs business tier" data-driven instead of price-ambiguous or name-magic.

Status: FROZEN @ v1 — approved by orchestrator under Tin's standing full-auto directive ("kick off new milestone then implement all enhancement of it in parallel", 2026-07-17); the freeze also freezes §5 Scope + Strategy.
Reported: yes — flags A1–A8 triaged in-session; rulings below.
Decided at freeze (verbatim rulings):
- A1 CONFIRMED: `plans.audience` column ('personal'|'business') + `plan_account_type_mismatch` reject — a personal tenant must never self-upgrade onto a business plan (billing-correctness).
- A2 CONFIRMED: NEW `Permission.BILLING_MANAGE` = exactly {OWNER, BILLING_ADMIN} (mirrors shipped `BILLING_CAPABLE_ROLES`); literal reuse of BUDGETS_MANAGE would widen ADMIN onto money mutations — rejected.
- A3 CONFIRMED: `plans.self_serve` column; enterprise seeded false. No NULL-price or name-magic tests.
- A4 CONFIRMED: upgrades immediate, no proration; downgrades rejected (`plan_downgrade_not_self_serve`) in v1.
- A5 CONFIRMED: seat_change DEFERRED to a follow-up task; `CheckoutIntent.intent_type` reserves the variant additively. MILESTONE.md scope amended to match.
- A6 CONFIRMED: `idempotency_key` in the JSON body (BFF strips custom headers — verified I4).
- A7 DECIDED: self-serve top-up ceiling is config-driven — `payment_topup_max_usd: Decimal = Decimal("10000")` (GATEWAY_PAYMENT_TOPUP_MAX_USD, positive-knob validated); `amount_exceeds_max` fires above it. Lower bound stays "> 0" (no minimum).
- A8 CONFIRMED: extracting `tenants/application/plan_assignment.py:assign_plan` is in-scope; superadmin PUT behavior must be proven byte-identical (test_superadmin_plan_endpoint_unchanged).
- Boot-guard NOTE: use the LIVE `model_validator(mode="after")` ValueError idiom (`_validate_otel_config` shape) — the `EmptyUpstreamKeyError` class named in early milestone prose was deleted 2026-06-17 (stale precedent; corrected in MILESTONE.md).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_owner_plan_upgrade_dev: arrange starter tenant+OWNER / act create plan-upgrade→pro + confirm / assert plan_id==pro, preview base math, no ledger row · covers: M8,M9
  - test_billing_admin_credit_topup: arrange balance 5.00+BILLING_ADMIN / act create credit-topup 25 + confirm / assert one topup row, balance 30, via topup_service · covers: M2,M8
  - test_tenant_scope_from_session_not_body: arrange tenant A OWNER, body carries tenant B id / act create / assert session.tenant_id==A, B unchanged · covers: M1
  - test_member_forbidden / test_admin_forbidden: act MEMBER/ADMIN POST checkout / assert 403 ERR_AUTH_FORBIDDEN, nothing created · covers: M2, R:ERR_AUTH_FORBIDDEN
  - test_double_confirm_idempotent: act confirm x2 concurrently / assert one ledger row, balance +25 once, same result · covers: M3
  - test_repeated_create_idempotent: act create x2 same key / assert same session_id, one row · covers: M4
  - test_cross_tenant_confirm_not_found / test_unknown_session_not_found: assert 404 checkout_not_found, unapplied · covers: M1, R:checkout_not_found
  - test_reconfirm_failed_session: assert 409 checkout_not_confirmable, unchanged · covers: R:checkout_not_confirmable
  - test_enterprise_not_self_serve / test_cross_audience / test_downgrade / test_plan_unchanged / test_plan_not_found: assert respective 4xx + plan unchanged · covers: R:plan_*
  - test_amount_invalid / test_amount_exceeds_max: assert 422 + balance unchanged · covers: R:amount_*
  - test_idempotency_key_required / test_idempotency_key_conflict: assert 400 / 409, nothing created · covers: R:idempotency_key_*
  - test_kill_switch_disabled: assert 403 checkout_disabled, unchanged · covers: M11
  - test_stripe_unavailable_degrades: arrange stripe breaker open / assert 503 payment_provider_unavailable, no partial mutation · covers: M12
  - test_stripe_empty_key_boot_error: assert Settings construction raises ValueError · covers: M10
  - test_dev_provider_marked_in_audit: assert audit row metadata.provider=='dev', actor, target_tenant_id · covers: M5,M7
  - test_audit_failure_does_not_fail_checkout: arrange record_audit raises / assert balance rose, session succeeded · covers: M7
  - test_superadmin_plan_endpoint_unchanged: assert put_platform_tenant_plan behavior byte-identical post-extraction · covers: M8
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./src/` · `apps/gateway/src/gateway/payments/` (new module: domain/ports.py, domain/entities.py, application/checkout_service.py, infrastructure/dev_provider.py, infrastructure/stripe_provider.py, infrastructure/checkout_repo.py, api/router.py, api/schemas.py) · `apps/gateway/src/gateway/tenants/application/plan_assignment.py` (new shared assign_plan use-case) · `apps/gateway/src/gateway/tenants/api/platform_plans_router.py` (refactor PUT to call assign_plan — behavior byte-identical) · `apps/gateway/src/gateway/tenants/domain/authz.py` (add Permission.BILLING_MANAGE + BILLING_ADMIN mapping) · `apps/gateway/src/gateway/tenants/infrastructure/orm.py` (PlanRow += self_serve, audience) · `apps/gateway/src/gateway/core/config.py` (payment_* settings + boot validator) · `apps/gateway/src/gateway/main.py` (mount checkout router, wire provider) · `apps/gateway/migrations/` (checkout_sessions + plans additive cols + seed) · `apps/dashboard/components/plan/PlanSeatsPage.tsx` + `components/credits/CreditsPage.tsx` (CTAs) · `apps/dashboard/components/checkout/` (new dialogs) · `apps/dashboard/lib/` (bff calls).
Strategy (ordered batches): 1. authz Permission.BILLING_MANAGE + migration (checkout_sessions, plans cols+seed). 2. Extract assign_plan; refactor superadmin PUT onto it (prove byte-identical). 3. payments domain port + entities + dev adapter + checkout_repo. 4. CheckoutService orchestration (create/confirm, row-lock, reuse topup_service/assign_plan, fire-and-forget audit). 5. config settings + boot validator + kill switch; stripe adapter behind timeout+retry+breaker. 6. api router + schemas; mount. 7. FE dialogs (mirror CreateKeyDialog + useMutation(bffPost)), CTA wiring, price-math preview render.

Persona (required): `appsec-engineer` — privilege-boundary + no-parallel-write-path stance atop SOUL.md (assume breach; verify both failure directions; a privilege slip is HARD-STOP). Billing-precision + backend-architect are secondary advisory lenses.
Spawn isolation (default): worktree.
Known-problem fixes: I1 extract-not-duplicate (assign_plan shared) · I2 self_serve column not NULL-price test · I3 audience column not name-magic · I4 idempotency_key in body not header · I5 row-locked confirm + tenant-scoped lookup · I6 stripe timeout+retry+breaker.
Strategy actually used: Followed the 7 preferred batches in order, red-first per phase. Deviations/refinements worth recording (feed Decisions/ADR):
  - **D1 — adapter port narrowed (batch 3/4).** §3's PaymentProvider Protocol nominally returns the HTTP-shaped `CheckoutSession`/`CheckoutResult`. Built the adapter to return payment-scoped value objects instead (`ProviderSession`/`ProviderConfirmation` in domain/ports.py) so the adapter stays PAYMENT-only (M-port intent: "the adapter authorizes payment; CheckoutService applies the mutation"). CheckoutService assembles the frozen §3 HTTP bodies. External wire contract UNCHANGED — this is an internal type-seam refinement, not a contract edit.
  - **D2 — credit-path atomicity (batch 4, safety rule).** `topup_service.topup` opens its OWN transaction, so it cannot enlist in the confirm row-lock transaction. Serialization instead comes from the `checkout_sessions` row lock (`lock_for_confirm`, `with_for_update`) which serializes confirm, PLUS threading the session's `idempotency_key` into `topup_service` so `credit_ledger`'s own unique index gives a second idempotency layer. No deadlock (confirm locks the checkout row; topup locks the ledger row — disjoint).
  - **D3 — idempotency_key optional in schemas (batch 6).** Made `idempotency_key` `str | None` in the request schemas so a MISSING key yields the frozen checkout `400 {"error":"idempotency_key_required"}` (via `_require_key`) rather than FastAPI's 422 envelope.
  - **D4 — boot guard via Settings validators (batch 5).** `_validate_topup_max` (field_validator, mode="before") + `_validate_payment_provider` (model_validator) raise `ValueError` (a `ValidationError` IS a `ValueError`) so selecting stripe with an empty/whitespace key, or a non-positive ceiling, fails at construction (M10).
  - **D5 — dev-tree migration DB (env).** Shared :5433 postgres across worktrees: ran under a unique `GATEWAY_TEST_DATABASE_URL=.../gateway_test_checkout` and had to pre-create the derived `gateway_migrations_test_checkout` DB (the migrations conftest string-replaces `gateway_test`→`gateway_migrations_test` on the custom DSN, but MIGRATION_TEST_DB is a literal — documented [[shared-test-postgres-no-timeouts]] gotcha).
  - **D6 — FE upgrade-target source is an OPEN GAP (batch 7).** The frozen §3 contract scopes NO tenant-facing plans-LIST read (only `GET /admin/plan` = current plan; superadmin `GET /admin/platform/plans`). So the live "Upgrade plan" menu has no data source. Resolution WITHOUT touching the frozen contract or expanding scope: `UpgradePlanDialog` is a pure, prop-driven component (`availablePlans: UpgradePlanOption[]`) — fully unit-tested via the create→preview→confirm flow — and degrades to an honest "no options" state when empty. `AddCreditsDialog` needs no such list and is fully live-wired. Surfaced as the single open question: an additive tenant-scoped self-serve plans-list read (e.g. `GET /admin/plans`) is needed to populate live upgrade targets — a change request back to Specify, NOT silently added here.
  - **D7 — preserved a pre-existing test's copy (batch 7).** Restyling the Plan page's placeholder card, I kept the literal `Seat pricing coming soon.` string intact (a pre-existing billing-ui test asserts it) and added the upgrade guidance as a sibling line — no pre-existing test touched.
Safety rule (feature-specific): the plan/credit mutation AND the session pending→succeeded flip happen under one row lock in one transaction; the credit path also threads the session idempotency_key into topup_service (double idempotency). No card/PII ever persisted.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only (stripe SDK only for the stripe adapter); ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed (adversarial refute-read; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe (double-confirm row lock)
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [ ] a tenant OWNER upgrades starter→pro end-to-end with zero superadmin action — confirmed by an integration test + the live dashboard "Upgrade plan" dialog showing server price math then success
- [ ] double-confirm yields exactly one mutation — confirmed by the concurrent-confirm test asserting a single ledger row / single plan write
- [ ] cross-tenant confirm returns 404 (existence never revealed) — confirmed by the cross-tenant test
- [ ] superadmin plan/credit endpoints behave byte-identically post-extraction — confirmed by the unchanged-superadmin test

### Deep checks — do not skim (fill the path that applies)
- [ ] WIRING (code) — Permission.BILLING_MANAGE referenced by every checkout endpoint; assign_plan referenced by BOTH superadmin PUT and checkout
- [ ] DEAD-CODE (code) — no orphaned adapter/use-case symbol
- [ ] SEMANTIC — n/a (code task)

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [ ] every symbol §3 cites still resolves in the current tree
- [ ] any anchor moved/renamed since Ground SHA 102ec65 is named here

### Refute-read verdict — the earned-green check
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: yes — mechanical (sensitivity: security)

### GATE RECORD
Reported: <yes | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): checkout confirm success rate · per-reject-code rate (esp. plan_account_type_mismatch, payment_provider_unavailable) · double-confirm collisions on the row lock · stripe breaker open events.

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence.
- [SPEC · seeded] seat_change intent deferred — reserved in the CheckoutIntent union for a follow-up task.

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)`.
