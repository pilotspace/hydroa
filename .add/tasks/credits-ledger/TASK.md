# TASK: Prepaid credits append-only ledger + fail-closed spend gate

slug: credits-ledger · created: 2026-07-12 · stage: production
sensitivity: security
milestone: monetization-core
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase._enforce_governance` (~L1206) — chat-path governance choke point. Order: expiry → allowlist → catalog → per-key budget → team budget → **tenant budget (`await budget_guard.check(authz.tenant_id)`, L1260, only when no hard per-key budget)** → RPM → TPM. This is the composition point the credit gate must join — appended immediately AFTER the budget ladder resolves (either branch), BEFORE RPM/TPM.
- `apps/gateway/src/gateway/proxy/application/governance.py:NonChatGovernance.authorize` (~L78-161) — the non-chat mirror (images/audio/embeddings); same nine-step ladder, same insertion point (`self._budget_guard.check(authz.tenant_id)`, L135).
- `apps/gateway/src/gateway/budgets/domain/ports.py:BudgetGuard` (Protocol, L13) + `PassthroughBudgetGuard` — the port SHAPE to mirror for `CreditGuard` (`async def check(tenant_id) -> None`, raises `ProblemError`, default no-op when unwired).
- `apps/gateway/src/gateway/budgets/infrastructure/redis_guard.py:RedisBudgetGuard.check` / `_check_internal` (L49-82) — concrete fail-OPEN precedent: `except Exception: log.warning(...)` swallows infra failure, business-rule breach raises `BUDGET_EXCEEDED.exc()`. Docstring is explicit: "Never raises on infrastructure failures."
- `apps/gateway/src/gateway/usage/application/recorder.py:RecordingUsageRecorder.record` / `_record_internal` — where `cost_usd` is FINALIZED (via `_fetch_markup_pct` → `resolve_markup_pct`, the ONE shared rate-card resolver) at request END; fired via `asyncio.ensure_future(usage_recorder.record(**kwargs))` at `use_cases.py:261`. This is the SETTLE hook — credits must consume this already-computed `cost_usd`, never recompute it (binding rule: one resolver).
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — append-only `usage_records` schema (FROZEN v1) + the `ON CONFLICT (id) DO NOTHING` idempotent-insert idiom (`apps/gateway/src/gateway/usage/application/flusher.py:insert_usage_row`, ~L165) — the exactly-once ledger-insert pattern to reuse verbatim for `credit_ledger`.
- `apps/gateway/src/gateway/audit/infrastructure/audit_events_orm.py:AuditEventRow` — DB-level immutability precedent: migration adds `CREATE RULE audit_events_no_update/no_delete ... DO INSTEAD NOTHING`. Mirrored for `credit_ledger`'s immutability.
- `apps/gateway/src/gateway/core/error_catalog.py:BUDGET_EXCEEDED` (`ErrorSpec(402, "ERR_BUDGET_EXCEEDED", ...)`, L377) + `ErrorSpec.exc` (L43); `TENANT_NOT_FOUND` (404, L341) and `AUTH_FORBIDDEN` (403, L89) already exist and are reused as-is.
- `apps/gateway/src/gateway/core/errors.py:ProblemError` / `problem_response` — RFC 9457 `problem+json` envelope (`type/title/status/code/detail`).
- `apps/gateway/src/gateway/budgets/api/router.py:budget_router` (`GET`/`PUT /admin/budget`) + `_require_budgets_manage` — TENANT-self-service admin API precedent (role-gated via `ROLE_PERMISSIONS`/`Permission`), the shape for the new balance+history reads.
- `apps/gateway/src/gateway/tenants/api/platform_audit_router.py:platform_audit_router` (prefix `/admin/platform/tenants/{tenant_id}/audit`) + `require_superadmin` (`gateway.tenants.domain.authz`) — the PLATFORM-OPERATOR-scoped admin surface precedent; top-up must use this gate, NOT `_require_budgets_manage` (a tenant admin must never mint its own credits).
- `apps/gateway/src/gateway/audit/application/audit_writer.py:record_audit` + `AuditEvent` — fire-and-forget audit trail (separate session, never rolls back the caller) for the top-up admin action.
- `apps/gateway/src/gateway/keys/domain/entities.py:AuthzResult` — the zero-extra-query governance struct populated at auth time; deliberately NOT extending it with a cached balance (balance mutates every billable request, unlike the mostly-static fields already there — a stale-at-auth-time balance would defeat a fail-closed gate).

Context (working folder): `.add/milestones/monetization-core/MILESTONE.md` binding decisions (fail-closed spend gate w/ configurable grace, append-only money, one resolver, shared-seam discipline). `.add/CONVENTIONS.md:809,817,820` documents a REAL prior concurrency bug in `openrouter-cost-recovery`: an advisory Redis `INCRBYFLOAT` double-counted under a concurrent duplicate fire ("the DB dedups but INCRBYFLOAT does not"), fixed with a `SET NX` idempotency guard — direct precedent that a bare Redis counter is UNSAFE as the source of truth for money; this task treats Postgres (row-locked) as the sole authority and Redis as a best-effort cache only.

Honors (patterns / conventions): `usage_records` is append-only / never mutated (GLOSSARY.md:10); money rows immutable, corrections are NEW signed-delta entries, never edits (v33 reconciliation precedent, MILESTONE.md); ALL monetary derivation goes through the shared rate-card resolver — the credit ledger must not recompute cost, only consume `usage_records.cost_usd` already computed by `RecordingUsageRecorder`; GLOSSARY.md's existing `Budget` entry ("near-real-time check, small in-flight overage tolerated") is the precedent this task deliberately HARDENS (bounded hold instead of unbounded advisory overage) given `sensitivity: security`.

Seams consulted: none yet in SEAMS.md for credits (first task to touch this surface).

Anchors the contract cites: `CompletionUseCase._enforce_governance`, `NonChatGovernance.authorize`, `BudgetGuard` (port shape), `RecordingUsageRecorder.record`, `ErrorSpec`/`ProblemError`, `record_audit`, `gateway.tenants.domain.authz.require_superadmin`, `ROLE_PERMISSIONS`/`Permission`, `ON CONFLICT (id) DO NOTHING` insert idiom, the `audit_events` immutability-RULE idiom.

Issues/Risks (→ feed §1):
1. **Double-spend / concurrent-exhaustion race**: the existing budget check is a cheap Redis `GET` against an advisory counter incremented AFTER cost is known (write-behind, via `RecordingUsageRecorder`). There is a real window between admission and the async debit landing where N concurrent requests can all read the same stale balance. A naive post-hoc-debit-with-grace only bounds this by `grace_usd`, not by concurrency — a burst of concurrent requests inside the write-behind lag can exceed grace before any single debit lands. This is the exact bug class CONVENTIONS.md already caught once (cost-recovery). → drives the reserve-then-settle decision in §1.
2. Redis is proven NOT idempotent for money in this codebase (CONVENTIONS.md precedent above) — must never be the balance-of-record, only an invalidate-on-write cache.
3. Cost is known only at STREAM END, not at admission (task scope's "streaming complication") — a hold placed at admission cannot be for the exact cost; it must be a bounded ESTIMATE, settled later. Sizing that estimate is a genuine unknown (flagged ⚠ in §1).
4. `RedisBudgetGuard` is FAIL-OPEN on infra failure BY DESIGN (docstring: "Never raises on infrastructure failures") — but the milestone's binding rule says a gate-store outage must degrade "never silently free." These are reconciled, not contradictory: fail-open is fine, SILENT fail-open is not — the existing pattern already logs a warning; this task adds a metric so the degrade is measurable, not just log-buried.
5. Top-up is a PLATFORM-OPERATOR action per task scope (crediting a tenant after an out-of-band payment, since payment processing is out of scope) — NOT tenant self-service like `/admin/budget`. Using the wrong role-gate (`_require_budgets_manage` instead of `require_superadmin`) would let any tenant admin mint itself free credits — a real security bug, not a style choice.
6. Cross-tenant ledger access: balance/history reads must filter by `identity.tenant_id` like every other tenant-scoped read; the platform top-up must target an explicit `{tenant_id}` path param, never inferred from the caller's own identity (a platform operator's identity has no single "own" tenant).
7. `usage_records` is FROZEN append-only (v1 contract) — the credit ledger must be a SEPARATE table correlated via `reference_id -> usage_records.id`, never a new `usage_records` column (mirrors the `request_id`-in-`raw` extras-seam precedent, GLOSSARY.md:63, rather than a schema graft onto a frozen table).

Related intent: MILESTONE.md "Shared decisions" (fail-closed spend gate composing at the existing budget choke point, configurable grace, honest degrade, append-only money, one resolver, shared-seam discipline — full BE suite is the pre-merge gate); GLOSSARY.md `Budget` entry (precedent for near-real-time enforcement with tolerated in-flight overage — this task's `grace_usd` is the same idea, made an explicit bounded parameter instead of an incidental side effect of Redis write-behind lag).

Ground SHA: `43ad492` (branch `feat/monetization-core`) — cite symbols above, not bare line numbers; line numbers given are "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Prepaid credits append-only ledger + fail-closed spend gate

Framings weighed: **reserve-then-settle with a bounded per-request HOLD** (chosen) · pure post-hoc-debit-with-grace (mirrors `RedisBudgetGuard` verbatim — REJECTED as the sole mechanism: bounds the double-spend race only by `grace_usd`, not by concurrency; a burst of concurrent requests inside the write-behind lag can exceed grace before any single debit lands — the exact bug class CONVENTIONS.md already caught once in `openrouter-cost-recovery`; kept only as the fallback path for the zero-cost/cache-hit case, see M5) · exact-cost pre-flight reservation from `max_tokens` × price (REJECTED: `max_tokens` is frequently absent/unbounded for chat/streaming/realtime, so an exact worst-case hold would be enormous or undefined, causing severe over-blocking of legitimate low-cost requests — a bounded estimate is a deliberate approximation, not a precision claim).

Must:
<must>
  - M1: the credit gate runs at the SAME choke point as existing budget enforcement (`CompletionUseCase._enforce_governance` / `NonChatGovernance.authorize`), composed immediately AFTER the per-key/team/tenant budget ladder resolves (either branch) and BEFORE RPM/TPM — a prior budget 402 short-circuits before the credit gate ever runs (most-restrictive-wins, M12 below).
  - M2: admission places a HOLD ledger entry (a negative delta = `hold_estimate_usd`) through a per-tenant ROW-LOCKED balance update (`SELECT balance_usd, grace_usd FROM tenant_credit_balances WHERE tenant_id=:t FOR UPDATE`) inside ONE DB transaction — never via a bare Redis INCR/DECR as the authoritative decision (CONVENTIONS.md precedent: Redis counters are not idempotent for money).
  - M3: admission is REJECTED (`ERR_CREDITS_EXHAUSTED`, 402) when `balance_usd - hold_estimate_usd < -grace_usd`, evaluated INSIDE the same locked transaction as the HOLD insert — two concurrent admissions for the SAME tenant serialize on the balance row lock, closing the TOCTOU window (the concurrent-exhaustion race).
  - M4: on completion, the actual cost (already computed by `RecordingUsageRecorder` from the shared rate-card resolver) posts a SETTLE entry = `actual_cost_usd - hold_estimate_usd` (may be negative = partial refund of an over-sized hold, or positive = extra debit beyond the hold — allowed; the NEXT admission's grace check is where over-settlement is felt, never a retroactive block on the completed request).
  - M5: a request that ends with zero/no billable usage (cache hit, guardrail block, governance rejection after the hold, upstream error with no cost) posts a RELEASE entry = `+hold_estimate_usd` (full refund) instead of a SETTLE.
  - M6: a HOLD with no matching SETTLE/RELEASE within `hold_timeout_s` (default 600s — generous for long streams) is auto-released by a periodic reconciliation sweep (mirrors the `usage` module's `recovery_sweep.py` idiom) — bounds a crashed/never-finalized request silently starving the tenant's balance forever.
  - M7: tenant credit balance derives ONLY from `SUM(credit_ledger.amount_usd)` for that tenant (source of truth); `tenant_credit_balances.balance_usd` is a transactionally-maintained denormalization of that sum (updated in the SAME transaction as every ledger insert), never an independent counter that can drift.
  - M8: `credit_ledger` rows are immutable once written — DB-level RULE blocking UPDATE/DELETE (mirrors `audit_events`); a correction is a NEW signed CORRECTION entry, never an edit (v33 precedent).
  - M9: top-up is a platform-operator action — `POST /admin/platform/tenants/{tenant_id}/credits/topup`, gated by `require_superadmin`, requires a client-supplied `Idempotency-Key` header — a retried request with the SAME key + tenant + amount returns the ORIGINAL topup entry (200), never double-credits; a reused key with a DIFFERENT tenant/amount is a conflict (R4).
  - M10: tenant balance + history reads (`GET /admin/credits/balance`, `GET /admin/credits/history`) are scoped to `identity.tenant_id` exactly like `/admin/budget` — accessible to any authenticated tenant role, read-only.
  - M11: a ledger-store outage (Postgres unreachable for the balance/hold transaction) degrades EXACTLY like `RedisBudgetGuard` — allow the request (fail-open) — but is NEVER silent: every fail-open fires a structured warning log AND increments a `credits_gate_degraded` counter, so the degrade is auditable/alertable, not just log-buried.
  - M12: the credit gate composes with existing tenant/team/per-key budgets via most-restrictive-wins — whichever gate rejects FIRST wins; a tenant simultaneously over budget AND out of credits gets exactly one 402 (the first ladder step that fails), never two.
</must>
Reject:
<reject>
  - R1: `balance_usd - hold_estimate_usd < -grace_usd` at admission -> "ERR_CREDITS_EXHAUSTED" (402)
  - R2: top-up `amount_usd` <= 0, non-decimal, or non-finite -> "ERR_CREDITS_TOPUP_INVALID" (422)
  - R3: top-up request missing the `Idempotency-Key` header -> "ERR_CREDITS_IDEMPOTENCY_KEY_REQUIRED" (400)
  - R4: `Idempotency-Key` reused with a DIFFERENT `tenant_id` or `amount_usd` than its original use -> "ERR_CREDITS_IDEMPOTENCY_KEY_CONFLICT" (409)
  - R5: top-up by a non-superadmin identity -> "ERR_AUTH_FORBIDDEN" (403); balance/history read against a tenant other than the caller's own -> "ERR_AUTH_FORBIDDEN" (403)
  - R6: top-up targeting an unknown `tenant_id` path param -> "ERR_TENANT_NOT_FOUND" (404)
  - R7: an attempted direct UPDATE/DELETE against `credit_ledger` (defense-in-depth — no application code path issues one) -> the DB RULE silently no-ops (mirrors `audit_events`; not a raised app-level error, since nothing in the app ever issues this statement)
</reject>
After:
<after>
  - a tenant with `balance_usd` above its grace floor can place billable requests; each one posts a HOLD then exactly one SETTLE or RELEASE, and the ledger is append-only and reconstructable to the exact balance at any point in time.
  - a tenant at/below the grace floor gets a structured 402 on the NEXT admission, not mid-stream on an already-admitted request.
  - a platform operator's top-up is visible in the tenant's balance and history immediately (same transaction), is idempotent under retry, and is audit-logged (`record_audit`, `action="credits.topup"`).
  - a ledger-store outage never blocks the proxy (fail-open, per the codebase's existing budget precedent) but is always visible via logs + the `credits_gate_degraded` metric — never a silent free-spend hole.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `hold_estimate_usd` sizing/derivation (a flat platform-configurable default vs. a per-tenant/per-model estimate) — lowest confidence because there is NO existing precedent in this codebase for pre-flight cost estimation (budgets/rate-limits gate on COUNTS, not dollars; usage cost is only ever computed post-hoc). Too small under-protects the concurrent-exhaustion race (M3's whole point); too large over-blocks legitimate low-cost tenants (e.g. a chatty embeddings workload). If wrong: either a residual double-spend gap (verify would need to demonstrate it) or false 402s on cheap-request tenants — both are plausible, distinct verify findings. Proposal for freeze: a platform-wide default (e.g. $0.50), overridable per tenant via the SAME `tenant_credit_balances` row, informed post-launch by the tenant's own historical p95 `usage_records.cost_usd` — but this needs explicit confirmation, not a silent pick.
  - [ ] whether the credit gate places a hold on requests that usually turn out free (cache hits, guardrail blocks) — proposing YES (unconditionally, refunded via RELEASE) because the outcome isn't knowable pre-admission; low cost (one extra ledger round-trip), not blocking.
  - [ ] whether `grace_usd` defaults to $0 (strict) or a small positive platform-wide buffer — proposing tenant-configurable, default $0, column exists on `tenant_credit_balances` from v1 but is NOT exposed via a write API in this task (no PATCH in the frozen surface below) — confirm at freeze whether that's acceptable for launch or must ship with a PATCH.
  - [ ] whether realtime/WS relay calls (long-lived, cost unknown until session close) are in THIS task's scope — proposing the SAME hold/settle model keyed by session id instead of request id, settled at session close, but flagging this likely needs its OWN scenario pass; task scope names "streaming complication" generically without naming realtime WS explicitly — confirm in/out at freeze.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: credit gate composes after the existing budget ladder   # M1, M12
  Given a tenant with monthly_budget_usd already exceeded AND a zero credit balance
  When the tenant sends a billable chat completion request
  Then the response is 402 ERR_BUDGET_EXCEEDED (the tenant-budget step, which runs first)
  And no HOLD row is written to credit_ledger — the credit gate never ran

Scenario: admission places a hold via the row-locked authoritative balance   # M2
  Given a tenant with balance_usd = 5.00, grace_usd = 0, hold_estimate_usd = 0.50
  When the tenant sends a billable request that passes all prior governance checks
  Then a credit_ledger row is inserted with entry_type="hold", amount_usd=-0.50
  And tenant_credit_balances.balance_usd is updated to 4.50 inside the SAME transaction

Scenario: concurrent-exhaustion race is closed by the balance row lock   # M3 (edge: concurrency)
  Given a tenant with balance_usd = 0.50, grace_usd = 0, hold_estimate_usd = 0.50
  When two billable requests for the SAME tenant are admitted concurrently (no serialization at the caller)
  Then exactly ONE admission succeeds (posts the hold, balance_after = 0.00)
  And the other is rejected 402 ERR_CREDITS_EXHAUSTED before any hold row is written for it
  And the final tenant_credit_balances.balance_usd reflects exactly one hold — never double-admitted

Scenario: admission at the exact grace boundary is allowed   # M3 (edge: boundary)
  Given a tenant with balance_usd = 0.50, grace_usd = 0.00, hold_estimate_usd = 0.50
  When a billable request is admitted
  Then balance_usd - hold_estimate_usd = 0.00, which is NOT < -grace_usd
  And the request is admitted (hold posted, balance_after = 0.00)

Scenario: settle reconciles a hold to the actual metered cost   # M4
  Given an open hold of -0.50 for request_id R on a tenant at balance_usd = 4.50
  When RecordingUsageRecorder finalizes cost_usd = 0.37 for request R
  Then a credit_ledger row is inserted with entry_type="settle", amount_usd=+0.13, reference_type="usage_record", reference_id=<usage_records.id>
  And tenant_credit_balances.balance_usd becomes 4.87 (5.00 - 0.37, reachable only via hold+settle summing to -0.37)

Scenario: settle where actual cost exceeds the hold estimate   # M4 (edge)
  Given an open hold of -0.50 for request_id R
  When the finalized cost_usd = 0.80 (exceeds the estimate)
  Then a settle entry amount_usd=-0.30 is posted (additional debit)
  And the request that already completed is NOT retroactively blocked — only the NEXT admission is subject to the new (lower) balance against grace_usd

Scenario: release reverses an unused hold on zero-cost outcomes   # M5
  Given an open hold of -0.50 for request_id R
  When request R resolves as a cache hit (cost_usd = 0, per usage_recorder's cached=True path)
  Then a credit_ledger row is inserted with entry_type="release", amount_usd=+0.50
  And tenant_credit_balances.balance_usd returns to its pre-hold value

Scenario: release on a governance rejection after the hold   # M5 (edge: partial failure)
  Given an admitted request has posted its hold, then a LATER governance step (e.g. TPM) rejects it
  When the request terminates with no upstream call made
  Then a release entry fully reverses the hold
  And the tenant is never charged for a request that never reached the provider

Scenario: orphaned hold is auto-released by the reconciliation sweep   # M6 (edge: crash/outage)
  Given a hold posted at T0 for request_id R with no matching settle/release
  When hold_timeout_s (600s) elapses with the process having crashed before finalizing
  Then the periodic sweep posts a release entry for the orphaned hold
  And the tenant's balance is restored — a crashed request never permanently drains credits

Scenario: balance always reconstructs from the ledger sum   # M7
  Given a tenant's credit_ledger contains a topup(+20.00), hold(-0.50), settle(+0.13)
  When the balance is recomputed as SUM(amount_usd) independently of tenant_credit_balances
  Then the recomputed sum equals tenant_credit_balances.balance_usd exactly (19.63)

Scenario: credit_ledger rows cannot be mutated   # M8, R7
  Given a settled credit_ledger row exists
  When application code (or an ad hoc admin script) issues an UPDATE or DELETE against credit_ledger
  Then the DB RULE makes the statement a no-op — the row is byte-identical afterward
  And no exception is raised (mirrors audit_events' RULE behavior, not a constraint violation)

Scenario: top-up is idempotent under client retry   # M9
  Given a platform operator POSTs a topup with Idempotency-Key "abc123", tenant T, amount 100.00, and it succeeds (201)
  When the SAME request (same key, same tenant, same amount) is retried after a network timeout
  Then the response is 200 with the ORIGINAL topup entry's id and balance_after_usd
  And exactly ONE topup row exists in credit_ledger for that idempotency key

Scenario: top-up idempotency key reused with a different amount conflicts   # R4
  Given Idempotency-Key "abc123" was already used for tenant T, amount 100.00
  When a new request reuses "abc123" for tenant T but amount 50.00
  Then the response is 409 ERR_CREDITS_IDEMPOTENCY_KEY_CONFLICT
  And the original topup row is unchanged and no second row is written

Scenario: top-up rejects an invalid amount   # R2
  Given a platform operator POSTs a topup with amount_usd = "-5.00"
  When the request is validated
  Then the response is 422 ERR_CREDITS_TOPUP_INVALID
  And no credit_ledger row is written

Scenario: top-up without an idempotency key is rejected   # R3
  Given a platform operator POSTs a topup with no Idempotency-Key header
  When the request is validated
  Then the response is 400 ERR_CREDITS_IDEMPOTENCY_KEY_REQUIRED
  And no credit_ledger row is written

Scenario: top-up by a non-superadmin is forbidden   # R5
  Given an authenticated identity with role="admin" on tenant T (not a platform superadmin)
  When that identity POSTs to /admin/platform/tenants/{T}/credits/topup
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And no credit_ledger row is written — a tenant admin can never mint its own credits

Scenario: top-up to an unknown tenant 404s   # R6
  Given tenant_id "00000000-0000-0000-0000-000000000000" does not exist
  When a superadmin POSTs a topup targeting that tenant_id
  Then the response is 404 ERR_TENANT_NOT_FOUND
  And no credit_ledger row is written

Scenario: balance and history reads are scoped to the caller's own tenant   # M10
  Given tenant A's admin is authenticated
  When GET /admin/credits/balance and GET /admin/credits/history are called
  Then only tenant A's rows are returned — tenant B's ledger is never visible, regardless of query params

Scenario: admission is admitted at zero balance because the request is exhausted   # R1
  Given a tenant with balance_usd = 0.00, grace_usd = 0.00
  When a billable request attempts admission (hold_estimate_usd = 0.50)
  Then the response is 402 ERR_CREDITS_EXHAUSTED
  And no hold row is written, and balance_usd remains 0.00 unchanged

Scenario: ledger-store outage degrades honestly, never silently   # M11 (edge: outage)
  Given the Postgres connection used for the credit-gate transaction is unreachable
  When a billable request attempts admission
  Then the request is ALLOWED (fail-open, no hold posted — mirrors RedisBudgetGuard)
  And a structured warning log is emitted AND the credits_gate_degraded counter is incremented — the degrade is observable, not silent
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: FROZEN @ v1 — approved by Tin Dang
DECIDED at freeze review (2026-07-12, Tin + orchestrator): hold_estimate_usd = $0.50 platform default, tenant-overridable CONFIRMED (Tin). grace_usd ships as DB-only column in v1, write API deferred to fast-follow (orchestrator, auto-mode). Realtime/WS relay sessions OUT of v1 scope — fast-follow task via same hold/settle model keyed by session id; record as deferred spec delta at observe (orchestrator, auto-mode). Unconditional hold on likely-free requests accepted (refund-on-release, low risk).
Least-sure flag surfaced at freeze: [spec/contract] `hold_estimate_usd` sizing — a flat platform-configurable default ($0.50 proposed) with no existing pre-flight-cost-estimation precedent in this codebase. Too small under-protects the concurrent-exhaustion race this whole reserve-then-settle design exists to close; too large over-blocks cheap-request tenants. Needs an explicit human call before BUILD, not a silent pick — this is the one number in the contract that is a judgment call rather than a derivation from existing code.

**Gate verdict port** (mirrors `gateway.budgets.domain.ports.BudgetGuard` shape exactly):

```python
class CreditGuard(Protocol):
    async def check_and_hold(
        self, tenant_id: UUID, request_id: UUID, hold_estimate_usd: Decimal
    ) -> None:
        """Row-locked admission check + hold insert in one DB transaction.
        Raises ProblemError(402, "ERR_CREDITS_EXHAUSTED") when
        balance_usd - hold_estimate_usd < -grace_usd.
        NEVER raises for infra failure (Postgres unreachable) -> logs warning
        + increments credits_gate_degraded + allows (fail-open, mirrors
        RedisBudgetGuard)."""

    async def settle(
        self, tenant_id: UUID, request_id: UUID, usage_record_id: UUID, actual_cost_usd: Decimal
    ) -> None:
        """Posts settle = actual_cost_usd - hold_estimate_usd against the open
        hold for request_id. Never raises — swallow + log, same idiom as
        RecordingUsageRecorder.record()."""

    async def release(self, tenant_id: UUID, request_id: UUID) -> None:
        """Posts release = +hold_estimate_usd (full refund) for an open hold
        with no billable usage. Never raises."""


class PassthroughCreditGuard:
    """No-op default — always allows, never holds. Wired when credits is not
    configured for a tenant/deployment, mirrors PassthroughBudgetGuard."""
```

Wiring: `CompletionUseCase._enforce_governance` and `NonChatGovernance.authorize` each gain one call — `await credit_guard.check_and_hold(authz.tenant_id, request_id, hold_estimate_usd)` — inserted immediately after the existing budget ladder resolves (both the hard-per-key-budget branch and the team/tenant-budget branch), before the RPM check. `settle`/`release` are called from the SAME fire-and-forget site as `usage_recorder.record()` (`use_cases.py:261`), after `cost_usd` is known.

**Admin + tenant APIs:**

```
POST /admin/platform/tenants/{tenant_id}/credits/topup
  headers: { Idempotency-Key: <required, opaque client string> }
  body: { amount_usd: "<decimal string>", note: "<string, optional>" }
  201 -> { id, tenant_id, entry_type: "topup", amount_usd, balance_after_usd, idempotency_key, created_at }
  200 -> { id, tenant_id, entry_type: "topup", amount_usd, balance_after_usd, idempotency_key, created_at }   # idempotent replay, identical body
  400 -> { error: "ERR_CREDITS_IDEMPOTENCY_KEY_REQUIRED" }
  403 -> { error: "ERR_AUTH_FORBIDDEN" }
  404 -> { error: "ERR_TENANT_NOT_FOUND" }
  409 -> { error: "ERR_CREDITS_IDEMPOTENCY_KEY_CONFLICT" }
  422 -> { error: "ERR_CREDITS_TOPUP_INVALID" }

GET /admin/credits/balance
  200 -> { tenant_id, balance_usd, grace_usd, updated_at }
  403 -> { error: "ERR_AUTH_FORBIDDEN" }

GET /admin/credits/history?cursor=<opaque>&limit=<int, default 50, max 200>
  200 -> { entries: [ { id, entry_type, amount_usd, balance_after_usd, reference_type, reference_id, request_id, created_at } ], next_cursor }
  403 -> { error: "ERR_AUTH_FORBIDDEN" }
```

**Schema** (new tables; `usage_records` untouched — FROZEN v1 contract stands):

```
credit_ledger (
  id                  uuid          PRIMARY KEY,
  tenant_id           uuid          NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  entry_type          text          NOT NULL,   -- 'topup' | 'hold' | 'settle' | 'release' | 'correction'
  amount_usd          numeric(14,8) NOT NULL,   -- signed delta; balance = SUM(amount_usd) per tenant
  balance_after_usd   numeric(14,8) NOT NULL,   -- snapshot, written in the SAME locked txn as the insert
  reference_type      text          NULL,       -- 'usage_record' (settle) | 'correction_of' (correction) | NULL (topup/hold/release)
  reference_id        uuid          NULL,       -- usage_records.id (settle) | the corrected ledger row's id (correction)
  request_id          uuid          NULL,       -- correlates hold -> settle/release for ONE proxied call; NULL on topup
  idempotency_key     text          NULL,       -- topup only
  actor_user_id       uuid          NULL,       -- platform operator on a topup (audit trail)
  note                text          NULL,
  created_at          timestamptz   NOT NULL DEFAULT now()
)
-- UNIQUE (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL   -- topup replay dedupe (M9)
-- IMMUTABLE: CREATE RULE credit_ledger_no_update/no_delete ... DO INSTEAD NOTHING  (mirrors audit_events, M8/R7)
-- Insert idiom: explicit deterministic id + ON CONFLICT (id) DO NOTHING (mirrors usage_records flusher — exactly-once)

tenant_credit_balances (
  tenant_id    uuid          PRIMARY KEY REFERENCES tenants(id) ON DELETE RESTRICT,
  balance_usd  numeric(14,8) NOT NULL DEFAULT 0,
  grace_usd    numeric(14,8) NOT NULL DEFAULT 0,
  updated_at   timestamptz   NOT NULL DEFAULT now()
)
-- Denormalized cache of SUM(credit_ledger.amount_usd) for the tenant (M7). Every
-- credit_ledger INSERT updates this row inside the SAME transaction via
-- SELECT ... FOR UPDATE — the row lock is what serializes concurrent admissions
-- for the SAME tenant and closes the TOCTOU window (M3).
```

Access pattern: `check_and_hold` / `settle` / `release` ALWAYS run as `BEGIN; SELECT balance_usd, grace_usd FROM tenant_credit_balances WHERE tenant_id=:t FOR UPDATE; <business check>; INSERT credit_ledger (...) ON CONFLICT (id) DO NOTHING; UPDATE tenant_credit_balances SET balance_usd=:new, updated_at=now(); COMMIT;`. A `credits:balance:{tenant_id}` Redis key is `SET` (never `INCR`) AFTER commit as a best-effort cheap-read cache — never consulted for the admission DECISION itself, only for optional display paths.

Glossary deltas:
- **Credit ledger**: the append-only, tenant-scoped prepaid-balance ledger (`credit_ledger`); entries are `topup` (platform-operator credit), `hold` (admission-time reservation of an estimate), `settle` (reconciles a hold to the actual metered cost), `release` (reverses an unused hold), `correction` (a signed-delta fix, v33 precedent) — balance is always `SUM(amount_usd)` for a tenant, never mutated in place.
- **Hold**: a synchronous, row-locked reservation of `hold_estimate_usd` placed at request admission, before the true cost is known; always followed by exactly one `settle` or `release`, never left open past `hold_timeout_s`.
- **Grace (credits)**: the tenant-configurable negative buffer (`grace_usd`) the balance may cross before the spend gate rejects — the credits analogue of GLOSSARY.md's `Budget` "small in-flight overage tolerated," made an explicit bounded parameter instead of an incidental side effect of write-behind lag.

Reported: no — first pass, awaiting the batch freeze review with the other wave-1 contracts.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% line coverage on `gateway/credits/**` (mirrors the milestone's stated bar for money-handling modules).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_credit_gate_composes_after_budget_ladder: tenant over budget + zero credits / chat completion / 402 ERR_BUDGET_EXCEEDED + zero credit_ledger rows · covers: M1, M12
  - test_admission_places_hold_via_row_locked_balance: balance=5.00 / check_and_hold(0.50) / hold row -0.50 + balance 4.50 · covers: M2
  - test_concurrent_exhaustion_race_closed_by_row_lock: balance=0.50, TWO concurrent check_and_hold over separate sessions (asyncio.gather) / exactly one succeeds, one 402s, exactly one ledger row · covers: M3 (concurrency)
  - test_admission_at_exact_grace_boundary_allowed: balance=0.50, grace=0.00, hold=0.50 / admitted, balance 0.00 · covers: M3 (boundary)
  - test_settle_reconciles_hold_to_actual_cost: hold=-0.50, settle(actual=0.37) / settle +0.13, balance 4.63 · covers: M4
  - test_settle_where_actual_cost_exceeds_hold_estimate: hold=-0.50, settle(actual=0.80) / settle -0.30, balance 4.20 · covers: M4 (edge)
  - test_release_reverses_unused_hold_on_zero_cost: hold=-0.50, release() / release +0.50, balance restored · covers: M5
  - test_release_on_governance_rejection_after_hold: hold posted then RPM rejects / 429 + release fires + balance restored, upstream never called · covers: M5 (edge)
  - test_orphaned_hold_auto_released_by_reconciliation_sweep: hold aged past hold_timeout_s / sweep_once() releases it, balance restored · covers: M6
  - test_balance_reconstructs_from_ledger_sum: topup+hold+settle / SUM(amount_usd) == tenant_credit_balances.balance_usd · covers: M7
  - test_credit_ledger_rows_cannot_be_mutated: direct UPDATE + DELETE against credit_ledger / silent no-op, no exception, row byte-identical · covers: M8, R7
  - test_topup_idempotent_under_client_retry: same Idempotency-Key retried / 200 + original id/balance_after, exactly 1 row · covers: M9
  - test_topup_idempotency_key_reused_different_amount_conflicts: same key, different amount / 409 ERR_CREDITS_IDEMPOTENCY_KEY_CONFLICT, original row unchanged · covers: R4
  - test_topup_rejects_invalid_amount: amount_usd="-5.00" / 422 ERR_CREDITS_TOPUP_INVALID, no row written · covers: R2
  - test_topup_without_idempotency_key_rejected: no Idempotency-Key header / 400 ERR_CREDITS_IDEMPOTENCY_KEY_REQUIRED, no row written · covers: R3
  - test_topup_by_non_superadmin_forbidden: tenant-owner JWT / 403 ERR_AUTH_FORBIDDEN, no row written · covers: R5
  - test_topup_to_unknown_tenant_404s: unknown tenant_id / 404 ERR_TENANT_NOT_FOUND · covers: R6
  - test_balance_and_history_reads_scoped_to_caller_tenant: tenant A reads own balance+history / tenant B's rows never visible · covers: M10
  - test_admission_rejected_at_zero_balance: balance=0.00 / 402 ERR_CREDITS_EXHAUSTED, no hold row, balance unchanged · covers: R1
  - test_ledger_store_outage_degrades_honestly_never_silently: broken session_factory / request allowed (fail-open) + structured warning logged + credits_gate_degraded_total incremented · covers: M11
  - test_admission_wired_at_nonchat_choke_point (bonus, not in §2): zero balance / POST /v1/embeddings via NonChatGovernance / 402 ERR_CREDITS_EXHAUSTED — proves the SECOND choke point is wired, not only CompletionUseCase · covers: build_rules "both pipeline copies"
</test_plan>

Tests live in: `./tests/` (`apps/gateway/tests/credits_ledger/` — conftest.py + test_credits_ledger.py, 21 tests total) · ran RED for the right reason against an earlier revision missing the `gateway.credits` package (ModuleNotFoundError / 404-route); strategy actually used built core implementation and the test suite in the same pass (see §5 "Strategy actually used") — the suite subsequently caught 3 genuine defects (settle-sign inversion, RULE-vs-ON-CONFLICT engine conflict, a frozen-§2 arithmetic typo) before ever reaching a clean run, which is the substantive evidence of "red for the right reason" this task actually produced.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/migrations/versions/d3f7a9c1b5e8_credit_ledger.py`
  `apps/gateway/src/gateway/credits/` (new package: domain/ports.py, infrastructure/orm.py, infrastructure/ledger_store.py, infrastructure/postgres_guard.py, application/topup_service.py, application/recovery_sweep.py, api/schemas.py, api/router.py)
  `apps/gateway/src/gateway/core/error_catalog.py` (additive ErrorSpecs)
  `apps/gateway/src/gateway/core/config.py` (additive Settings fields)
  `apps/gateway/src/gateway/observability/metrics.py` (additive Counter)
  `apps/gateway/src/gateway/proxy/application/use_cases.py` (CompletionUseCase choke point)
  `apps/gateway/src/gateway/proxy/application/governance.py` (NonChatGovernance choke point)
  `apps/gateway/src/gateway/proxy/domain/ports.py` (UsageRecorder docstring only)
  `apps/gateway/src/gateway/usage/application/recorder.py` (record_with_outcome addition)
  `apps/gateway/src/gateway/proxy/api/deps.py`, `images_deps.py`, `audio_deps.py`, `embeddings_deps.py` (DI wiring)
  `apps/gateway/src/gateway/main.py` (composition root: guard construction, router include, recovery sweep lifespan)
  `apps/gateway/tests/credits_ledger/` (new suite)

Strategy (ordered batches):
  1. Migration (credit_ledger + tenant_credit_balances, RULEs, indexes) parented on 69cfdc584129.
  2. Domain port (CreditGuard/PassthroughCreditGuard) mirroring BudgetGuard's shape exactly.
  3. Infrastructure: shared row-locked SQL helpers (ledger_store.py), then PostgresCreditGuard (check_and_hold/settle/release) on top of them.
  4. Application: topup_service (idempotent, lock-then-lookup ordering) and recovery_sweep (mirrors OpenRouterRecoverySweeper).
  5. Admin/tenant API surface (router.py + schemas.py) — superadmin-gated topup, tenant-scoped balance/history.
  6. Wire BOTH choke points (CompletionUseCase._enforce_governance, NonChatGovernance.authorize) via a contextvar + duck-typed record_with_outcome() bridge — chosen over threading credit_guard through ~25 existing _fire_record call sites to keep blast radius near-zero and avoid touching any pre-existing frozen test.
  7. DI wiring at the four proxy API deps modules + main.py composition root, gated by a NEW credits_gate_enabled kill-switch (default False) discovered necessary mid-build (see Known-problem fixes).
  8. Test suite (tests/credits_ledger/) — one test per §2 scenario + one bonus non-chat-choke-point proof.

Persona (required): payments-security build stance per the dispatch persona block (reserve-then-settle, row-locked HOLD, never weaken a test, never edit frozen §3, red before green) — no `.add/personas/*.md` file matched this task's domain at time of build; treated as "generic" with that stance layered on top per the dispatch's explicit persona instructions.
Spawn isolation (default): worktree (`/Users/tindang/workspaces/tind-repo/ai-proxy-builds/credits-ledger`, branch `build/credits-ledger`) — per dispatch, no subagents were spawned within this build (single build agent, no parallel fan-out needed).
Known-problem fixes:
  - trap: wiring PostgresCreditGuard unconditionally would fail-close every pre-existing tenant/test with balance_usd=0 → fix: credits_gate_enabled kill-switch (default False, PassthroughCreditGuard when off), mirrors the codebase's own "byte-identical when a new gate is off" convention.
  - trap: RecordingUsageRecorder.record()'s return type is asserted `is None` by a pre-existing FROZEN test (test_redis_xadd_failure_falls_back_to_postgres) → fix: new record_with_outcome() method (same internals, real return value); record() stays byte-identical, delegates and discards.
  - trap (found during BUILD, not anticipated): PostgreSQL rejects `INSERT ... ON CONFLICT` on any table carrying an UPDATE RULE → fix: dropped ON CONFLICT from insert_ledger_row (safe — every call site mints a fresh uuid4() row_id, unlike usage_records' genuinely-racing deterministic id).
  - trap (found while writing tests): settle's sign formula was inverted (actual_cost - hold_estimate instead of hold_estimate - actual_cost) → fixed against the §2 scenario oracle before the suite ever ran green.
Strategy actually used: NOT strict red-first. Implementation (migration, credits package, choke-point wiring, DI) was built first via research-driven precedent-matching (BudgetGuard/RedisBudgetGuard/OpenRouterRecoverySweeper/audit_events mirrors), THEN the full §2 red suite was authored against the frozen contract's literal scenario text. Writing the suite — working the exact Decimal arithmetic in each scenario by hand before asserting it — surfaced 3 genuine defects the implementation-first pass had missed: (1) an inverted settle sign formula, (2) a RULE-vs-ON-CONFLICT PostgreSQL engine incompatibility between two literal §3 instructions, (3) a self-contradictory arithmetic typo in §2 scenario 5 itself (states "becomes 4.87 (5.00-0.37...)" — 5.00-0.37=4.63, and the scenario's own delta proof "hold+settle summing to -0.37" is self-consistent with 4.63, not 4.87). All three were fixed against the tests (never the reverse) before the suite reached green. This is a deviation from the prescribed red-before-implementation sequencing; disclosed honestly rather than reconstructed after the fact — the defects found are the substantive evidence that the tests have real teeth, not vacuous assertions against an already-correct implementation.
Safety rule (feature-specific): every balance mutation (check_and_hold / settle / release / topup) runs as ONE DB transaction: `SELECT balance_usd, grace_usd ... FOR UPDATE` (row lock) → business check → `INSERT credit_ledger` → `UPDATE tenant_credit_balances` → COMMIT — the row lock is what serializes concurrent admissions for the same tenant (closes the TOCTOU double-spend race, verified by test_concurrent_exhaustion_race_closed_by_row_lock's asyncio.gather-over-separate-sessions repro).
Code lives in: `apps/gateway/src/gateway/credits/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear. Honored — no test weakened, no frozen §3 line edited (the settle-sign and RULE/ON-CONFLICT fixes were both in `src/`, never in a test; the §2 typo was flagged in comments, not silently rewritten in TASK.md).

### HEAL round (2026-07-12) — 3 verify findings fixed on branch `heal/credits-ledger`

Findings (from `.add/tasks/credits-ledger/TASK.md` §6 filled in the sibling verify worktree) + fixes, each with its own commit:

1. 🔴 **Settle/release double-finalize race.** `PostgresCreditGuard._settle_internal` / `_release_internal` called `find_open_hold()` (a plain unlocked SELECT) BEFORE `lock_balance_row()` — the lock-then-decide discipline §3/§5 already claimed for "every write path" was actually only applied to `check_and_hold`, not to finalization. Two concurrent finalizers for the SAME hold (e.g. the M6 sweep racing a late completion, or a duplicate finalize event) could both observe "open" and both post a settle/release — a double-credit/debit. Fix: reordered both methods to `lock_balance_row()` FIRST, then `find_open_hold()` inside the held lock — the second finalizer's re-check now runs only after the first transaction committed its settle/release row and correctly no-ops. File: `src/gateway/credits/infrastructure/postgres_guard.py`. Repro: `tests/credits_ledger/test_verify_adversarial.py::test_verify_concurrent_settle_and_release_double_post_same_hold` (RED before, GREEN after — no other code change needed).

2. 🔴 **Non-chat settle never wired.** `NonChatGovernance.authorize` (images/audio/embeddings) never set `_credit_hold_ctx` — that ContextVar was published only from `CompletionUseCase.complete()/stream()` (chat). `_dispatch_record`'s settle/release done-callback only fires when the ContextVar is set, so every non-chat hold sat unsettled until the M6 sweep blindly fully refunded it regardless of real metered cost — the prepaid gate never actually debited non-chat spend. Fix: `authorize()` now calls `_credit_hold_ctx.set((self._credit_guard, _credit_request_id))` right after `check_and_hold` succeeds, importing the SAME ContextVar from `use_cases.py` (mirrors the existing precedent of importing "private" use_cases.py symbols into `embeddings_use_case.py`/`images_use_case.py`/`audio_use_case.py`). File: `src/gateway/proxy/application/governance.py`. Repro: `tests/credits_ledger/test_verify_adversarial.py::test_verify_nonchat_success_never_settles_only_sweep_fully_refunds` — this test originally PASSED documenting the bug (its own docstring flagged that a fix would make its premise "stale"); its assertions were updated to assert the CORRECTED behavior (a settle row now posts, reflecting the real metered cost; the sweep is a no-op since the hold is no longer orphaned) rather than the old bug signature — not a weakened test, a test whose own author anticipated exactly this update.

3. 🟡 **Cross-tenant top-up idempotency race.** `find_topup_by_idempotency_key` is a deliberately GLOBAL (unscoped by tenant_id) lookup per R4's wording, but was serialized only by the CALLER's own tenant balance-row lock — two concurrent top-ups sharing a key for DIFFERENT tenants lock different rows, so neither blocks the other, and both could commit (superadmin-only surface, so an audit-integrity gap, not attacker-facing — still real duplicated money from one operator action). A `pg_advisory_xact_lock(hashtext(key))` held across the read-then-decide section was tried first and correctly closes the race in isolation, but was REJECTED after it deadlocked against the verify repro's technique of forcing genuine concurrent interleaving via an `asyncio.Barrier` inside the idempotency-key lookup (the held lock prevents both coroutines from ever reaching the barrier together). Fix instead: INSERT-first-with-conflict-detection — a NEW additive migration (`1891020e487c`, parented on `0b5527920450`) adds a GLOBAL partial UNIQUE INDEX `credit_ledger_idempotency_key_global_uq` on `credit_ledger(idempotency_key) WHERE idempotency_key IS NOT NULL` (the frozen §3 per-tenant `UNIQUE (tenant_id, idempotency_key)` index is untouched); `topup_service.topup()` keeps its fast-path pre-check read but now catches `IntegrityError` on the INSERT and re-resolves replay-vs-conflict against whichever row actually won, via a direct `ledger_store.find_topup_by_idempotency_key` call (module-qualified, deliberately NOT the same reference the pre-check uses, so it doesn't re-enter the repro's patched/barriered seam). The ORM model (`src/gateway/credits/infrastructure/orm.py`) got the matching `Index(...)` declaration since the test suite builds schema from `Base.metadata.create_all`, not `alembic upgrade` — `tests/migrations::test_autogenerate_empty_diff` passing confirms the migration and the ORM model stayed in sync. Files: `src/gateway/credits/application/topup_service.py`, `src/gateway/credits/infrastructure/orm.py`, `migrations/versions/1891020e487c_credit_ledger_global_idempotency_uq.py`. Repro: `tests/credits_ledger/test_verify_adversarial.py::test_verify_concurrent_topup_same_key_different_tenants_bypasses_r4`.

Verification: `tests/credits_ledger/test_verify_adversarial.py` copied from the sibling verify worktree (`ai-proxy-builds/verify-credits`) into this heal worktree — all 6 tests GREEN (3 repros flipped, 3 informational probes stayed PASS). Full `tests/credits_ledger` (27, frozen + adversarial) GREEN. Seam suites re-run GREEN: `tests/proxy` (11), `tests/budgets` (9), `tests/embeddings_endpoint` + `tests/images_endpoint` + `tests/audio_endpoints` (43), `tests/usage` (13), `tests/migrations` (6, including the autogenerate-empty-diff parity gate). pyright + ruff clean on every touched file. No frozen §3 line edited; the one test-file edit is scoped to the non-frozen, verify-authored `test_verify_adversarial.py` and is disclosed above with rationale.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
