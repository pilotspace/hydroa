"""Suite-local fixtures for margin-dashboard tests (TASK.md §4).

Real Postgres (localhost:5433, `GATEWAY_TEST_DATABASE_URL`, schema rebuilt per test) + the
root `app`/`client`/`db_session` fixtures. Superadmin/tenant identities are minted directly
via `app.state.token_service.issue(...)` — no DB user row required (mirrors
`tests/plan_catalog/test_plan_catalog.py`'s own precedent). `usage_records` rows are seeded
directly into the ledger with a controlled `created_at`/`cost_usd`/`provider_cost`/
`cost_basis`/`model_id` so the new `reconcile_by_tenant_model`/`reconcile_trend` aggregates
(and the margin router built on top of them) can be exercised deterministically. `invoices`
rows are seeded directly too (invoice-generation's own frozen shape, already merged on this
branch) for the tie-out scenarios — no need to run the real invoice generator.
"""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._polling import poll_until

# A fixed UTC window the tests seed into — July 2026 calendar month, matching the §2
# scenarios' own "July 2026" framing and the tie-out period=2026-07 examples.
#
# USE `INSIDE` ONLY WITH AN ABSOLUTE WINDOW — a request that pins the period itself,
# i.e. `start=`/`end=` query params or `period=2026-07`. For those, July 2026 is the
# window under test and a fixed date is exactly right.
WINDOW_FROM = datetime.datetime(2026, 7, 1, 0, 0, 0, tzinfo=datetime.UTC)
WINDOW_TO = datetime.datetime(2026, 8, 1, 0, 0, 0, tzinfo=datetime.UTC)
INSIDE = datetime.datetime(2026, 7, 15, 12, 0, 0, tzinfo=datetime.UTC)


def _inside_current_month() -> datetime.datetime:
    """A timestamp guaranteed to fall inside the CURRENT calendar month.

    Requests that pass a bare `window=month` (no `start`/`end`) resolve the period
    from the WALL CLOCK: `_compute_window_bounds` returns
    [first-of-this-month 00:00, first-of-next-month 00:00). Seeding such a test at the
    fixed `INSIDE` above silently stops matching the moment the real month rolls over.

    That is not hypothetical — it happened. Six tests here seeded `INSIDE`
    (2026-07-15) and queried a bare `window=month`; they passed every day through
    2026-07-31 and began failing on 2026-08-01 with `billed_total == 0`, on a tree
    nobody had touched. `make ci` went red with no code change behind it.

    Day 15 at 12:00 UTC is strictly between the two bounds for every month of every
    year (every month has a 15th), so this needs no per-month special-casing. It is
    deliberately NOT `now()`: the window is the whole calendar month, so a mid-month
    fixed point is stable against a run that straddles midnight, and it keeps the
    seeded instant reproducible within a session.
    """
    return datetime.datetime.now(datetime.UTC).replace(
        day=15, hour=12, minute=0, second=0, microsecond=0
    )


# USE THIS WITH A RELATIVE WINDOW — a bare `window=month`/`week`/`day` that the server
# resolves against the wall clock. Evaluated once at import, so every test in a session
# seeds the same instant.
INSIDE_CURRENT_MONTH = _inside_current_month()


async def seed_row(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    cost_usd: Decimal,
    created_at: datetime.datetime,
    model_id: str = "openai/gpt-4o",
    provider_cost: Decimal | None = None,
    cost_basis: str = "catalog",
    usage_source: str = "frame",
) -> None:
    """Insert one usage_records row with full reconciliation columns (controlled
    created_at/model_id) — no signup/key flow needed, key_id is a synthetic UUID (the FK
    is on tenant_id only for this ledger table's read paths under test)."""
    await session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, created_at, cost_basis, provider_cost, usage_source)"
            " VALUES (:id, :tid, :kid, :mid, 0, 0,"
            "  :cost, 200, '{}', :ts, :basis, :pcost, :src)"
        ),
        {
            "id": str(uuid.uuid4()),
            "tid": str(tenant_id),
            "kid": str(uuid.uuid4()),
            "mid": model_id,
            "cost": str(cost_usd),
            # usage_records.created_at is TIMESTAMP (naive) — strip tz like the sibling
            # reconciliation_aggregate suite's own seed helper.
            "ts": created_at.astimezone(datetime.UTC).replace(tzinfo=None),
            "basis": cost_basis,
            "pcost": str(provider_cost) if provider_cost is not None else None,
            "src": usage_source,
        },
    )
    await session.commit()


async def seed_tenant(session: AsyncSession, *, name: str) -> uuid.UUID:
    """Insert a bare kind='customer' tenant row — no signup flow needed (this suite never
    exercises auth/key-issuance, only cross-tenant aggregation)."""
    tid = uuid.uuid4()
    await session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, :name, 'customer')"),
        {"id": tid, "name": name},
    )
    await session.commit()
    return tid


async def seed_invoice(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    period_start: datetime.datetime,
    period_end: datetime.datetime,
    total_usd: Decimal,
    raw_total_usd: Decimal,
    status: str = "issued",
) -> uuid.UUID:
    """Insert one invoices row directly (invoice-generation's own frozen §3 shape, already
    merged on this branch) — no need to run the real generator for a tie-out fixture."""
    iid = uuid.uuid4()

    def _naive_utc(dt: datetime.datetime) -> datetime.datetime:
        # The fast create_all() test schema infers a NAIVE `DateTime` column from
        # `InvoiceRow.period_start`'s bare `Mapped[datetime]` annotation (matching
        # `invoice_generator.py`'s own `_as_naive_utc` write-path convention exactly) —
        # even though the real migration declares TIMESTAMPTZ.
        return dt.astimezone(datetime.UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt

    await session.execute(
        text(
            "INSERT INTO invoices"
            " (id, tenant_id, period_start, period_end, status, currency,"
            "  total_usd, raw_total_usd, tax_usd, issued_at)"
            " VALUES (:id, :tid, :ps, :pe, :status, 'USD', :total, :raw, 0, :issued_at)"
        ),
        {
            "id": iid,
            "tid": str(tenant_id),
            "ps": _naive_utc(period_start),
            "pe": _naive_utc(period_end),
            "status": status,
            "total": str(total_usd),
            "raw": str(raw_total_usd),
            "issued_at": _naive_utc(period_end) if status == "issued" else None,
        },
    )
    await session.commit()
    return iid


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id, seeding one directly when the fast create_all test
    schema has not run the seed migration (mirrors tests/plan_catalog's fixture)."""
    from gateway.tenants.infrastructure.repository import get_platform_tenant

    tenant = await get_platform_tenant(db_session)
    if tenant is not None:
        return tenant.id

    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": tid},
    )
    await db_session.commit()
    return tid


def _issue_token(app: Any, *, role: Any, tenant_id: uuid.UUID, email: str) -> str:
    """Mint a Bearer token directly via the live token service — no DB user row required."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=tenant_id, role=role, email=email
    )
    return token


@pytest.fixture
async def superadmin_token(app: Any, platform_tenant_id: uuid.UUID) -> str:
    from gateway.tenants.domain.entities import Role

    return _issue_token(
        app, role=Role.SUPERADMIN, tenant_id=platform_tenant_id, email="root@platform.internal"
    )


@pytest.fixture
async def owner_token(app: Any) -> str:
    """A valid, non-superadmin tenant credential (owner role) — R2's 403 case."""
    from gateway.tenants.domain.entities import Role

    return _issue_token(app, role=Role.OWNER, tenant_id=uuid.uuid4(), email="owner@tenant.io")


async def audit_count(session: AsyncSession, *, action: str) -> int:
    """Let the fire-and-forget audit write complete, then count matching rows."""
    await asyncio.sleep(0.05)
    result = await session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE action = :action"),
        {"action": action},
    )
    return result.scalar() or 0


async def audit_row(session: AsyncSession, *, action: str) -> dict[str, Any]:
    async def _rows() -> Sequence[Any]:
        return (
            await session.execute(
                text(
                    "SELECT tenant_id, actor_email, metadata FROM audit_events"
                    " WHERE action = :action"
                ),
                {"action": action},
            )
        ).fetchall()

    # MIXED wait: the fire-and-forget audit write must land (positive) and there must be
    # exactly ONE row (negative — a duplicate audit event is itself a defect).
    await poll_until(_rows, lambda r: len(r) >= 1)
    # NEGATIVE WAIT: the exactly-one half of `len(rows) == 1`.
    await asyncio.sleep(0.05)
    rows = await _rows()
    assert len(rows) == 1, f"expected exactly 1 audit row for action={action!r}, found {len(rows)}"
    tenant_id, actor_email, metadata = rows[0]
    return {"tenant_id": tenant_id, "actor_email": actor_email, "metadata": dict(metadata)}


async def await_audit_count(
    session: AsyncSession,
    *,
    action: str,
    expected: int,
    timeout: float = 3.0,  # noqa: ASYNC109 -- bounded poll loop, not a cancel scope
    interval: float = 0.02,
) -> int:
    """Poll until the raw audit_events count for `action` reaches `expected`, or `timeout`
    elapses. The audit write is fire-and-forget — `audit_count`'s own baked-in sleep(0.05) is
    racy under `pytest -n 12` CPU saturation. Positive assertions only; never masks a
    genuinely-absent row (returns the real count after timeout so the caller's own
    `== expected` assertion still fails honestly)."""

    async def _raw_count() -> int:
        result = await session.execute(
            text("SELECT COUNT(*) FROM audit_events WHERE action = :action"),
            {"action": action},
        )
        return int(result.scalar() or 0)

    count = await _raw_count()
    deadline = time.monotonic() + timeout
    while count < expected and time.monotonic() < deadline:
        await asyncio.sleep(interval)
        count = await _raw_count()
    return count
