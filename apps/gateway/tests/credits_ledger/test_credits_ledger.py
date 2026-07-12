"""Failing-first (red) suite for credits-ledger (contract FROZEN @ v1, TASK.md §4).

One test per scenario in .add/tasks/credits-ledger/TASK.md §2, PLUS one bonus test
(test_admission_wired_at_nonchat_choke_point) proving the SECOND pipeline copy
(NonChatGovernance.authorize) also calls check_and_hold — build_rules explicitly
requires both choke points wired, not just CompletionUseCase.

Strategy: HTTP-level tests prove real wiring at the two governance choke points
(scenarios 1, R1, M5-edge, + the bonus non-chat test) and the admin/tenant API
surface (M9/R2-R7/M10). Direct CreditGuard-protocol calls (via the `credit_guard`
fixture — the SAME PostgresCreditGuard instance wired onto app.state) exercise the
money-mechanics scenarios (M2-M8, M11, concurrency, sweep) precisely, since
check_and_hold/settle/release ARE the public port contract, not internals.

Real Postgres row locks are exercised via asyncio.gather over separate sessions —
no mocks — for the concurrency scenario (M3).

Infrastructure:
  - Real Postgres: GATEWAY_TEST_DATABASE_URL (see tests/conftest.py)
  - Real Redis (budget-ladder composition test only): redis://localhost:6380/9
  - httpx.ASGITransport (no network)

Run only this suite:
  cd apps/gateway && uv run pytest tests/credits_ledger/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.credits_ledger.conftest import (
    _set_budget,
    assert_problem,
    auth_key,
    bearer,
    get_balance_row,
    ledger_count,
    poll_until,
    seed_balance,
)

COMPLETIONS = "/v1/chat/completions"
TOPUP_PATH = "/admin/platform/tenants/{tenant_id}/credits/topup"
BALANCE_PATH = "/admin/credits/balance"
HISTORY_PATH = "/admin/credits/history"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Minimal non-streaming fake — reused from tests/budgets pattern."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-credits-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        )
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body


# ---------------------------------------------------------------------------
# Scenario 1 — credit gate composes AFTER the existing budget ladder (M1, M12)
# ---------------------------------------------------------------------------


async def test_credit_gate_composes_after_budget_ladder(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model: str,
    redis_client: Any,
    credit_guard: Any,
) -> None:
    """Tenant over budget AND zero credits -> 402 ERR_BUDGET_EXCEEDED (budget wins,
    runs first); no HOLD row written — the credit gate never ran."""
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard

    await _set_budget(db_session, api_key["tenant_id"], "10.00")
    await redis_client.set(
        f"usage:spend:{api_key['tenant_id']}:{datetime.datetime.now(datetime.UTC).strftime('%Y%m')}",
        b"10.00",
    )
    app.state.budget_guard = RedisBudgetGuard(redis=redis_client, session_factory=app.state.sessionmaker)
    app.state.completion_upstream = FakeCompletionUpstream()
    # Tenant has NO credit balance seeded (defaults to 0) — if the credit gate ran
    # first it would ALSO reject, but with ERR_CREDITS_EXHAUSTED, not ERR_BUDGET_EXCEEDED.

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(api_key["key"]),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert await ledger_count(db_session, api_key["tenant_id"]) == 0, (
        "the credit gate must never run once the budget ladder already rejected"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — admission places a hold via the row-locked authoritative balance (M2)
# ---------------------------------------------------------------------------


async def test_admission_places_hold_via_row_locked_balance(
    db_session: AsyncSession,
    credit_guard: Any,
) -> None:
    """balance=5.00, grace=0, hold=0.50 -> hold row -0.50 inserted; balance -> 4.50."""
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()
    await seed_balance(db_session, str(tenant_id), balance_usd="5.00", grace_usd="0")

    await credit_guard.check_and_hold(tenant_id, request_id, Decimal("0.50"))

    row = (
        await db_session.execute(
            text(
                "SELECT entry_type, amount_usd FROM credit_ledger"
                " WHERE tenant_id = :t AND request_id = :r"
            ),
            {"t": str(tenant_id), "r": str(request_id)},
        )
    ).fetchone()
    assert row is not None
    assert row[0] == "hold"
    assert Decimal(str(row[1])) == Decimal("-0.50")

    balance = await get_balance_row(db_session, str(tenant_id))
    assert balance is not None
    assert Decimal(balance[0]) == Decimal("4.50")


# ---------------------------------------------------------------------------
# Scenario 3 — concurrent-exhaustion race closed by the balance row lock (M3, concurrency)
# ---------------------------------------------------------------------------


async def test_concurrent_exhaustion_race_closed_by_row_lock(
    db_session: AsyncSession,
    app: Any,
) -> None:
    """balance=0.50, grace=0, hold=0.50: two concurrent admissions for the SAME
    tenant over SEPARATE Postgres sessions -> exactly ONE succeeds, the other 402s
    BEFORE any hold row is written for it — proves the row lock, not app code,
    serializes the race."""
    from gateway.core.errors import ProblemError
    from gateway.credits.infrastructure.postgres_guard import PostgresCreditGuard

    tenant_id = uuid.uuid4()
    await seed_balance(db_session, str(tenant_id), balance_usd="0.50", grace_usd="0")

    guard_a = PostgresCreditGuard(session_factory=app.state.sessionmaker)
    guard_b = PostgresCreditGuard(session_factory=app.state.sessionmaker)
    request_a, request_b = uuid.uuid4(), uuid.uuid4()

    results = await asyncio.gather(
        guard_a.check_and_hold(tenant_id, request_a, Decimal("0.50")),
        guard_b.check_and_hold(tenant_id, request_b, Decimal("0.50")),
        return_exceptions=True,
    )

    successes = [r for r in results if r is None]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1, f"exactly one admission must succeed; got {results!r}"
    assert len(failures) == 1, f"exactly one admission must be rejected; got {results!r}"
    assert isinstance(failures[0], ProblemError)
    assert failures[0].status == 402
    assert failures[0].code == "ERR_CREDITS_EXHAUSTED"

    # exactly one hold row was ever written for this tenant
    count = await ledger_count(db_session, str(tenant_id))
    assert count == 1, f"expected exactly 1 hold row, found {count} — double-admission occurred"

    balance = await get_balance_row(db_session, str(tenant_id))
    assert balance is not None
    assert Decimal(balance[0]) == Decimal("0.00"), "balance must reflect exactly one hold"


# ---------------------------------------------------------------------------
# Scenario 4 — admission at the exact grace boundary is allowed (M3, boundary)
# ---------------------------------------------------------------------------


async def test_admission_at_exact_grace_boundary_allowed(
    db_session: AsyncSession,
    credit_guard: Any,
) -> None:
    """balance=0.50, grace=0.00, hold=0.50: balance-hold == -grace exactly -> NOT
    < -grace -> admitted; balance_after = 0.00."""
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()
    await seed_balance(db_session, str(tenant_id), balance_usd="0.50", grace_usd="0.00")

    await credit_guard.check_and_hold(tenant_id, request_id, Decimal("0.50"))  # must not raise

    balance = await get_balance_row(db_session, str(tenant_id))
    assert balance is not None
    assert Decimal(balance[0]) == Decimal("0.00")
    assert await ledger_count(db_session, str(tenant_id)) == 1


# ---------------------------------------------------------------------------
# Scenario 5 — settle reconciles a hold to the actual metered cost (M4)
# ---------------------------------------------------------------------------


async def test_settle_reconciles_hold_to_actual_cost(
    db_session: AsyncSession,
    credit_guard: Any,
) -> None:
    """hold=-0.50 on balance 5.00 -> 4.50; settle(actual=0.37) -> settle row +0.13,
    reference_type=usage_record; balance -> 4.63 (5.00 - 0.37).

    NOTE — frozen §2 contract typo found during test-writing: the scenario's headline
    text says "balance_usd becomes 4.87 (5.00 - 0.37, ...)" but 5.00 - 0.37 = 4.63, not
    4.87; the scenario's OWN worked proof ("hold+settle summing to -0.37": hold=-0.50 +
    settle=+0.13 = -0.37) is self-consistent with 4.63, not 4.87. This test asserts the
    arithmetically self-consistent value (4.63), not the headline typo — flagged for a
    frozen-contract correction at Observe, not silently "fixed" in TASK.md itself.
    """
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()
    usage_record_id = uuid.uuid4()
    await seed_balance(db_session, str(tenant_id), balance_usd="5.00", grace_usd="0")
    await credit_guard.check_and_hold(tenant_id, request_id, Decimal("0.50"))

    await credit_guard.settle(tenant_id, request_id, usage_record_id, Decimal("0.37"))

    row = (
        await db_session.execute(
            text(
                "SELECT entry_type, amount_usd, reference_type, reference_id FROM credit_ledger"
                " WHERE tenant_id = :t AND request_id = :r AND entry_type = 'settle'"
            ),
            {"t": str(tenant_id), "r": str(request_id)},
        )
    ).fetchone()
    assert row is not None
    assert Decimal(str(row[1])) == Decimal("0.13")
    assert row[2] == "usage_record"
    assert uuid.UUID(str(row[3])) == usage_record_id

    balance = await get_balance_row(db_session, str(tenant_id))
    assert balance is not None
    assert Decimal(balance[0]) == Decimal("4.63")


# ---------------------------------------------------------------------------
# Scenario 6 — settle where actual cost exceeds the hold estimate (M4, edge)
# ---------------------------------------------------------------------------


async def test_settle_where_actual_cost_exceeds_hold_estimate(
    db_session: AsyncSession,
    credit_guard: Any,
) -> None:
    """hold=-0.50; actual=0.80 (exceeds estimate) -> settle=-0.30 (additional debit);
    the already-completed request is NOT retroactively blocked — only the ledger
    reflects the extra debit for the NEXT admission's grace check."""
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()
    usage_record_id = uuid.uuid4()
    await seed_balance(db_session, str(tenant_id), balance_usd="5.00", grace_usd="0")
    await credit_guard.check_and_hold(tenant_id, request_id, Decimal("0.50"))

    await credit_guard.settle(tenant_id, request_id, usage_record_id, Decimal("0.80"))

    row = (
        await db_session.execute(
            text(
                "SELECT amount_usd FROM credit_ledger"
                " WHERE tenant_id = :t AND request_id = :r AND entry_type = 'settle'"
            ),
            {"t": str(tenant_id), "r": str(request_id)},
        )
    ).fetchone()
    assert row is not None
    assert Decimal(str(row[0])) == Decimal("-0.30")

    balance = await get_balance_row(db_session, str(tenant_id))
    assert balance is not None
    assert Decimal(balance[0]) == Decimal("4.20"), "5.00 - 0.80 actual cost"


# ---------------------------------------------------------------------------
# Scenario 7 — release reverses an unused hold on zero-cost outcomes (M5)
# ---------------------------------------------------------------------------


async def test_release_reverses_unused_hold_on_zero_cost(
    db_session: AsyncSession,
    credit_guard: Any,
) -> None:
    """hold=-0.50 -> release() -> release row +0.50; balance returns to pre-hold value."""
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()
    await seed_balance(db_session, str(tenant_id), balance_usd="5.00", grace_usd="0")
    await credit_guard.check_and_hold(tenant_id, request_id, Decimal("0.50"))

    await credit_guard.release(tenant_id, request_id)

    row = (
        await db_session.execute(
            text(
                "SELECT amount_usd FROM credit_ledger"
                " WHERE tenant_id = :t AND request_id = :r AND entry_type = 'release'"
            ),
            {"t": str(tenant_id), "r": str(request_id)},
        )
    ).fetchone()
    assert row is not None
    assert Decimal(str(row[0])) == Decimal("0.50")

    balance = await get_balance_row(db_session, str(tenant_id))
    assert balance is not None
    assert Decimal(balance[0]) == Decimal("5.00")


# ---------------------------------------------------------------------------
# Scenario 8 — release on a governance rejection after the hold (M5, edge: partial failure)
# ---------------------------------------------------------------------------


async def test_release_on_governance_rejection_after_hold(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model: str,
    credit_guard: Any,
) -> None:
    """A request is admitted (hold posted), then a LATER governance step (RPM) rejects
    it -> the hold is fully reversed via release; no upstream call is ever made; the
    tenant is never charged for a request that never reached the provider."""
    await seed_balance(db_session, api_key["tenant_id"], balance_usd="5.00", grace_usd="0")

    # RPM limit of 1, with the window already saturated — mirrors AU7's precedent.
    await client.patch(
        f"/admin/keys/{api_key['key_id']}",
        json={"rpm_limit": 1},
        headers=bearer(api_key["jwt"]),
    )
    now_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    redis = getattr(getattr(app.state, "rate_limiter", None), "_redis", None)
    assert redis is not None, "rate_limiter must be wired with a real redis client for this test"
    await redis.zadd(f"ratelimit:rpm:{api_key['key_id']}", {str(now_ms - 1000).encode(): now_ms - 1000})

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(api_key["key"]),
    )

    assert_problem(resp, 429, "ERR_RATE_LIMITED")
    assert upstream.calls == 0, "the request must never reach the provider once RPM rejects"

    async def _released() -> dict[str, Any] | None:
        row = (
            await db_session.execute(
                text(
                    "SELECT entry_type FROM credit_ledger"
                    " WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 1"
                ),
                {"t": api_key["tenant_id"]},
            )
        ).fetchone()
        return {"entry_type": row[0]} if row is not None else None

    latest = await poll_until(_released)
    assert latest["entry_type"] == "release"

    balance = await get_balance_row(db_session, api_key["tenant_id"])
    assert balance is not None
    assert Decimal(balance[0]) == Decimal("5.00"), "hold must be fully reversed — never charged"


# ---------------------------------------------------------------------------
# Scenario 9 — orphaned hold auto-released by the reconciliation sweep (M6, edge: crash)
# ---------------------------------------------------------------------------


async def test_orphaned_hold_auto_released_by_reconciliation_sweep(
    db_session: AsyncSession,
    app: Any,
) -> None:
    """A hold posted long ago with no matching settle/release is auto-released by the
    periodic sweep once hold_timeout_s elapses — a crashed request never permanently
    drains credits."""
    from gateway.credits.application.recovery_sweep import CreditHoldRecoverySweeper
    from gateway.credits.infrastructure.postgres_guard import PostgresCreditGuard

    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()
    # naive datetime: the test schema (Base.metadata.create_all) maps Mapped[datetime]
    # to a tz-naive DateTime column (same codebase-wide convention as usage_records /
    # audit_events — the migration's TIMESTAMPTZ is a prod-only distinction); mirrors
    # recovery_sweep.py's own cutoff.replace(tzinfo=None).
    old_created_at = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=700)
    ).replace(tzinfo=None)

    await seed_balance(db_session, str(tenant_id), balance_usd="4.50", grace_usd="0")
    await db_session.execute(
        text(
            "INSERT INTO credit_ledger"
            " (id, tenant_id, entry_type, amount_usd, balance_after_usd, request_id, created_at)"
            " VALUES (:id, :t, 'hold', -0.50, 4.50, :r, :ts)"
        ),
        {"id": str(uuid.uuid4()), "t": str(tenant_id), "r": str(request_id), "ts": old_created_at},
    )
    await db_session.commit()

    guard = PostgresCreditGuard(session_factory=app.state.sessionmaker)
    sweeper = CreditHoldRecoverySweeper(
        session_factory=app.state.sessionmaker, credit_guard=guard, hold_timeout_s=600
    )

    attempts = await sweeper.sweep_once()
    assert attempts == 1

    release_row = (
        await db_session.execute(
            text(
                "SELECT amount_usd FROM credit_ledger"
                " WHERE tenant_id = :t AND request_id = :r AND entry_type = 'release'"
            ),
            {"t": str(tenant_id), "r": str(request_id)},
        )
    ).fetchone()
    assert release_row is not None
    assert Decimal(str(release_row[0])) == Decimal("0.50")

    balance = await get_balance_row(db_session, str(tenant_id))
    assert balance is not None
    assert Decimal(balance[0]) == Decimal("5.00"), "orphaned hold must be fully restored"


# ---------------------------------------------------------------------------
# Scenario 10 — balance always reconstructs from the ledger sum (M7)
# ---------------------------------------------------------------------------


async def test_balance_reconstructs_from_ledger_sum(
    db_session: AsyncSession,
    credit_guard: Any,
    app: Any,
) -> None:
    """topup(+20.00), hold(-0.50), settle(+0.13) -> SUM(amount_usd) == 19.63 ==
    tenant_credit_balances.balance_usd exactly."""
    from gateway.credits.application.topup_service import topup

    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()
    usage_record_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:t, 'Ledger-Sum-Test-Co')"),
        {"t": str(tenant_id)},
    )
    await db_session.commit()

    await topup(
        app.state.sessionmaker,
        tenant_id=tenant_id,
        amount_usd=Decimal("20.00"),
        idempotency_key=str(uuid.uuid4()),
    )
    await credit_guard.check_and_hold(tenant_id, request_id, Decimal("0.50"))
    await credit_guard.settle(tenant_id, request_id, usage_record_id, Decimal("0.37"))

    ledger_sum = (
        await db_session.execute(
            text("SELECT SUM(amount_usd) FROM credit_ledger WHERE tenant_id = :t"),
            {"t": str(tenant_id)},
        )
    ).scalar()
    assert Decimal(str(ledger_sum)) == Decimal("19.63")

    balance = await get_balance_row(db_session, str(tenant_id))
    assert balance is not None
    assert Decimal(balance[0]) == Decimal(str(ledger_sum)) == Decimal("19.63")


# ---------------------------------------------------------------------------
# Scenario 11 — credit_ledger rows cannot be mutated (M8, R7)
# ---------------------------------------------------------------------------


async def test_credit_ledger_rows_cannot_be_mutated(
    db_session: AsyncSession,
    credit_guard: Any,
) -> None:
    """A direct UPDATE/DELETE against credit_ledger is a silent no-op via the DB RULE
    (mirrors audit_events) — no exception, row byte-identical afterward.

    Base.metadata.create_all (the test schema) creates the TABLE from the ORM model
    but does NOT run the migration's op.execute() RULE statements (migration-only DDL)
    — mirrors tests/audit/test_audit_store.py's immutable_audit_session precedent
    exactly. Apply the SAME two RULEs the migration creates before exercising them.
    """
    await db_session.execute(
        text("DROP RULE IF EXISTS credit_ledger_no_update ON credit_ledger")
    )
    await db_session.execute(
        text("DROP RULE IF EXISTS credit_ledger_no_delete ON credit_ledger")
    )
    await db_session.execute(
        text("CREATE RULE credit_ledger_no_update AS ON UPDATE TO credit_ledger DO INSTEAD NOTHING")
    )
    await db_session.execute(
        text("CREATE RULE credit_ledger_no_delete AS ON DELETE TO credit_ledger DO INSTEAD NOTHING")
    )
    await db_session.commit()

    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()
    await seed_balance(db_session, str(tenant_id), balance_usd="5.00", grace_usd="0")
    await credit_guard.check_and_hold(tenant_id, request_id, Decimal("0.50"))

    before = (
        await db_session.execute(
            text("SELECT id, entry_type, amount_usd FROM credit_ledger WHERE tenant_id = :t"),
            {"t": str(tenant_id)},
        )
    ).fetchone()
    assert before is not None
    row_id = before[0]

    # Neither statement may raise — the RULE makes them DO INSTEAD NOTHING.
    await db_session.execute(
        text("UPDATE credit_ledger SET amount_usd = -999 WHERE id = :id"), {"id": str(row_id)}
    )
    await db_session.execute(text("DELETE FROM credit_ledger WHERE id = :id"), {"id": str(row_id)})
    await db_session.commit()

    after = (
        await db_session.execute(
            text("SELECT id, entry_type, amount_usd FROM credit_ledger WHERE tenant_id = :t"),
            {"t": str(tenant_id)},
        )
    ).fetchone()
    assert after is not None, "the DELETE must have been a no-op — row must still exist"
    assert after[0] == before[0]
    assert after[1] == before[1]
    assert Decimal(str(after[2])) == Decimal(str(before[2])), (
        "the UPDATE must have been a no-op — amount_usd must be byte-identical"
    )


# ---------------------------------------------------------------------------
# Scenario 12 — top-up is idempotent under client retry (M9)
# ---------------------------------------------------------------------------


async def test_topup_idempotent_under_client_retry(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    api_key: dict[str, str],
) -> None:
    """Retried topup (same key, tenant, amount) -> 200 with the ORIGINAL entry's id +
    balance_after_usd; exactly ONE topup row exists in credit_ledger."""
    headers = {**bearer(superadmin_token), "Idempotency-Key": "abc123"}
    body = {"amount_usd": "100.00"}

    first = await client.post(
        TOPUP_PATH.format(tenant_id=api_key["tenant_id"]), json=body, headers=headers
    )
    assert first.status_code == 201, first.text
    first_body = first.json()

    retry = await client.post(
        TOPUP_PATH.format(tenant_id=api_key["tenant_id"]), json=body, headers=headers
    )
    assert retry.status_code == 200, retry.text
    retry_body = retry.json()

    assert retry_body["id"] == first_body["id"]
    assert retry_body["balance_after_usd"] == first_body["balance_after_usd"]

    count = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM credit_ledger"
                " WHERE tenant_id = :t AND idempotency_key = 'abc123'"
            ),
            {"t": api_key["tenant_id"]},
        )
    ).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# Scenario 13 — top-up idempotency key reused with a different amount conflicts (R4)
# ---------------------------------------------------------------------------


async def test_topup_idempotency_key_reused_different_amount_conflicts(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    api_key: dict[str, str],
) -> None:
    """Same key reused for the SAME tenant but a DIFFERENT amount -> 409; original
    row unchanged, no second row written."""
    headers = {**bearer(superadmin_token), "Idempotency-Key": "abc123"}
    first = await client.post(
        TOPUP_PATH.format(tenant_id=api_key["tenant_id"]),
        json={"amount_usd": "100.00"},
        headers=headers,
    )
    assert first.status_code == 201, first.text

    conflict = await client.post(
        TOPUP_PATH.format(tenant_id=api_key["tenant_id"]),
        json={"amount_usd": "50.00"},
        headers=headers,
    )
    assert_problem(conflict, 409, "ERR_CREDITS_IDEMPOTENCY_KEY_CONFLICT")

    rows = (
        await db_session.execute(
            text(
                "SELECT amount_usd FROM credit_ledger"
                " WHERE tenant_id = :t AND idempotency_key = 'abc123'"
            ),
            {"t": api_key["tenant_id"]},
        )
    ).fetchall()
    assert len(rows) == 1
    assert Decimal(str(rows[0][0])) == Decimal("100.00")


# ---------------------------------------------------------------------------
# Scenario 14 — top-up rejects an invalid amount (R2)
# ---------------------------------------------------------------------------


async def test_topup_rejects_invalid_amount(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    api_key: dict[str, str],
) -> None:
    """amount_usd = "-5.00" -> 422 ERR_CREDITS_TOPUP_INVALID; no ledger row written."""
    resp = await client.post(
        TOPUP_PATH.format(tenant_id=api_key["tenant_id"]),
        json={"amount_usd": "-5.00"},
        headers={**bearer(superadmin_token), "Idempotency-Key": "bad-amount-1"},
    )
    assert_problem(resp, 422, "ERR_CREDITS_TOPUP_INVALID")
    assert await ledger_count(db_session, api_key["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# Scenario 15 — top-up without an idempotency key is rejected (R3)
# ---------------------------------------------------------------------------


async def test_topup_without_idempotency_key_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    api_key: dict[str, str],
) -> None:
    """Missing Idempotency-Key header -> 400 ERR_CREDITS_IDEMPOTENCY_KEY_REQUIRED; no
    ledger row written."""
    resp = await client.post(
        TOPUP_PATH.format(tenant_id=api_key["tenant_id"]),
        json={"amount_usd": "10.00"},
        headers=bearer(superadmin_token),
    )
    assert_problem(resp, 400, "ERR_CREDITS_IDEMPOTENCY_KEY_REQUIRED")
    assert await ledger_count(db_session, api_key["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# Scenario 16 — top-up by a non-superadmin is forbidden (R5)
# ---------------------------------------------------------------------------


async def test_topup_by_non_superadmin_forbidden(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    api_key: dict[str, str],
) -> None:
    """A tenant-admin JWT (owner role, not platform superadmin) -> 403
    ERR_AUTH_FORBIDDEN; no ledger row written — a tenant admin can never mint its
    own credits."""
    resp = await client.post(
        TOPUP_PATH.format(tenant_id=api_key["tenant_id"]),
        json={"amount_usd": "10.00"},
        headers={**bearer(api_key["jwt"]), "Idempotency-Key": "self-mint-1"},
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    assert await ledger_count(db_session, api_key["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# Scenario 17 — top-up to an unknown tenant 404s (R6)
# ---------------------------------------------------------------------------


async def test_topup_to_unknown_tenant_404s(
    client: httpx.AsyncClient,
    superadmin_token: str,
) -> None:
    """Unknown tenant_id path param -> 404 ERR_TENANT_NOT_FOUND; no ledger row written
    (nothing to check — the tenant does not exist)."""
    unknown_tenant = "00000000-0000-0000-0000-000000000000"
    resp = await client.post(
        TOPUP_PATH.format(tenant_id=unknown_tenant),
        json={"amount_usd": "10.00"},
        headers={**bearer(superadmin_token), "Idempotency-Key": "unknown-tenant-1"},
    )
    assert_problem(resp, 404, "ERR_TENANT_NOT_FOUND")


# ---------------------------------------------------------------------------
# Scenario 18 — balance and history reads are scoped to the caller's own tenant (M10)
# ---------------------------------------------------------------------------


async def test_balance_and_history_reads_scoped_to_caller_tenant(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Tenant A's admin sees only tenant A's balance/history — tenant B's ledger is
    never visible, regardless of query params."""
    # Tenant A
    signup_a = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": "CreditsAlpha", "email": "owner@calpha.io", "password": "correct horse battery"},
    )
    assert signup_a.status_code == 201
    tenant_a_id = signup_a.json()["tenant_id"]
    jwt_a = (
        await client.post(
            "/admin/auth/login",
            json={"email": "owner@calpha.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]

    # Tenant B
    signup_b = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": "CreditsBeta", "email": "owner@cbeta.io", "password": "correct horse battery"},
    )
    assert signup_b.status_code == 201
    tenant_b_id = signup_b.json()["tenant_id"]

    await client.post(
        TOPUP_PATH.format(tenant_id=tenant_a_id),
        json={"amount_usd": "50.00"},
        headers={**bearer(superadmin_token), "Idempotency-Key": "topup-a"},
    )
    await client.post(
        TOPUP_PATH.format(tenant_id=tenant_b_id),
        json={"amount_usd": "7.00"},
        headers={**bearer(superadmin_token), "Idempotency-Key": "topup-b"},
    )

    balance_resp = await client.get(BALANCE_PATH, headers=bearer(jwt_a))
    assert balance_resp.status_code == 200
    balance_body = balance_resp.json()
    assert balance_body["tenant_id"] == tenant_a_id
    assert Decimal(balance_body["balance_usd"]) == Decimal("50.00")

    history_resp = await client.get(HISTORY_PATH, headers=bearer(jwt_a))
    assert history_resp.status_code == 200
    history_body = history_resp.json()
    assert len(history_body["entries"]) == 1
    assert Decimal(history_body["entries"][0]["amount_usd"]) == Decimal("50.00")
    for entry in history_body["entries"]:
        assert entry["amount_usd"] != "7.00", "tenant B's ledger must never be visible to tenant A"


# ---------------------------------------------------------------------------
# Scenario 19 — admission is rejected at zero balance (R1)
# ---------------------------------------------------------------------------


async def test_admission_rejected_at_zero_balance(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model: str,
    credit_guard: Any,
) -> None:
    """balance=0.00, grace=0.00, hold=0.50 (platform default) -> 402
    ERR_CREDITS_EXHAUSTED; no hold row written; balance stays 0.00 unchanged."""
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(api_key["key"]),
    )

    assert_problem(resp, 402, "ERR_CREDITS_EXHAUSTED")
    assert upstream.calls == 0
    assert await ledger_count(db_session, api_key["tenant_id"]) == 0

    balance = await get_balance_row(db_session, api_key["tenant_id"])
    if balance is not None:
        assert Decimal(balance[0]) == Decimal("0.00")


# ---------------------------------------------------------------------------
# Scenario 20 — ledger-store outage degrades honestly, never silently (M11, edge)
# ---------------------------------------------------------------------------


class _BrokenSessionFactory:
    """A session_factory whose __call__ raises before any DB round-trip — simulates
    Postgres being fully unreachable for the credit-gate transaction."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("Postgres unavailable (BrokenSessionFactory fake)")


async def test_ledger_store_outage_degrades_honestly_never_silently(
    app: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Postgres unreachable -> request ALLOWED (fail-open, no hold posted) AND a
    structured warning is logged AND credits_gate_degraded_total is incremented —
    the degrade is observable, never silent."""
    from gateway.credits.infrastructure.postgres_guard import PostgresCreditGuard

    metrics = app.state.metrics_registry
    before = metrics.credits_gate_degraded_total.labels(operation="check_and_hold")._value.get()

    guard = PostgresCreditGuard(session_factory=_BrokenSessionFactory(), metrics=metrics)  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="gateway.credits.infrastructure.postgres_guard"):
        await guard.check_and_hold(uuid.uuid4(), uuid.uuid4(), Decimal("0.50"))  # must NOT raise

    assert any(
        "credit_guard" in rec.message and "check_and_hold" in rec.message for rec in caplog.records
    ), "a structured warning must be emitted on fail-open degrade"

    after = metrics.credits_gate_degraded_total.labels(operation="check_and_hold")._value.get()
    assert after == before + 1, "credits_gate_degraded_total must be incremented, not silent"


# ---------------------------------------------------------------------------
# Bonus — the SECOND choke point (NonChatGovernance.authorize) is also wired.
# build_rules: "both pipeline copies, never just one." Not one of the 20 canonical
# §2 scenarios (which are written model-agnostically as "a billable request"), but
# necessary evidence that the non-chat pipeline copy was not skipped.
# ---------------------------------------------------------------------------


async def test_admission_wired_at_nonchat_choke_point(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    credit_guard: Any,
) -> None:
    """A zero-balance tenant hitting /v1/embeddings (routed through
    NonChatGovernance.authorize, NOT CompletionUseCase) also gets 402
    ERR_CREDITS_EXHAUSTED — proves the credit gate is wired at BOTH choke points,
    not only the chat path."""
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry

    model_id = "text-embedding-3-small"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 8192, true, 'embedding', 'openai')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": model_id, "name": "text-embedding-3-small"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, 0.00000002, 0.00000002, now()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()

    class _FakeEmbeddingProvider:
        name = "openai"

        async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            return 200, {
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "model": model_id,
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            }

        async def post_multipart(self, *a: Any, **kw: Any) -> tuple[int, dict[str, Any]]:
            return 200, {}

        def stream_bytes(self, *a: Any, **kw: Any) -> Any:
            async def _gen() -> Any:
                yield b""

            return _gen()

    registry = getattr(app.state, "provider_registry", None)
    if registry is not None and hasattr(registry, "_providers"):
        registry._providers["openai"] = _FakeEmbeddingProvider()  # type: ignore[attr-defined]
    else:
        app.state.provider_registry = ProviderRegistry({"openai": _FakeEmbeddingProvider()})  # type: ignore[arg-type]

    resp = await client.post(
        "/v1/embeddings",
        json={"model": model_id, "input": "hello world"},
        headers=auth_key(api_key["key"]),
    )

    assert_problem(resp, 402, "ERR_CREDITS_EXHAUSTED")
    assert await ledger_count(db_session, api_key["tenant_id"]) == 0
