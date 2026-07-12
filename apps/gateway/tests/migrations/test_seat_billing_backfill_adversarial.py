"""Independent adversarial VERIFY probes for the seat-billing migration backfill
(seat-billing TASK.md §3 — FROZEN @ v2, M4/R5).

Lives beside `test_seat_billing_backfill.py` (same directory) so it can reuse the
`clean_migration_db` fixture from this directory's own conftest.py — pytest forbids
cross-directory fixture sharing without a `pytest_plugins` declaration in a non-root
conftest, so a sibling-directory import (as seat_billing's own suite does for its HTTP
fixtures) is not viable here; a same-directory file is the correct seam.

Written by an independent verify agent (NOT the builder) to refute the green — these
probes exercise cross-user attribution in a single mixed-population migration run, and
demonstrate a structural (data-availability, not code) limitation of the M4 backfill.

DO NOT weaken or delete any existing test to make these pass.
"""

from __future__ import annotations

import datetime as dt
import uuid

import asyncpg
import pytest

from gateway.billing.application.seat_pricer import MembershipEvent, active_days

from .conftest import ALEMBIC_INI, MIGRATION_DATABASE_URL, MIGRATION_DSN

PARENT_REVISION = "1891020e487c"

pytestmark = pytest.mark.asyncio


def _cfg() -> object:
    from alembic.config import Config  # noqa: PLC0415

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


@pytest.mark.usefixtures("clean_migration_db")
async def test_backfill_mixed_population_each_user_gets_exactly_their_own_events() -> None:
    """A single migration run backfilling several DIFFERENT pre-existing users (some
    active, some deactivated) must attribute events to the CORRECT user — never cross-
    wire one user's backfilled event onto another's row, and never double-apply."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, PARENT_REVISION)

    tenant_id = uuid.uuid4()
    active_ids = [uuid.uuid4() for _ in range(3)]
    deactivated_ids = [uuid.uuid4() for _ in range(2)]
    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, 'Mixed Backfill Co')", tenant_id
        )
        for i, uid in enumerate(active_ids):
            await conn.execute(
                "INSERT INTO users (id, tenant_id, email, password_hash, role, created_at)"
                " VALUES ($1, $2, $3, 'x', 'member', $4)",
                uid,
                tenant_id,
                f"active{i}@mixedbackfill.example",
                dt.datetime(2026, 3, 1, tzinfo=dt.UTC),
            )
        for i, uid in enumerate(deactivated_ids):
            await conn.execute(
                "INSERT INTO users"
                " (id, tenant_id, email, password_hash, role, created_at, deactivated_at)"
                " VALUES ($1, $2, $3, 'x', 'member', $4, $5)",
                uid,
                tenant_id,
                f"deact{i}@mixedbackfill.example",
                dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
                dt.datetime(2026, 4, 1, tzinfo=dt.UTC),
            )
    finally:
        await conn.close()

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        for uid in active_ids:
            rows = await conn.fetch(
                "SELECT event_type FROM seat_membership_events WHERE user_id = $1", uid
            )
            assert [r["event_type"] for r in rows] == ["joined"], (
                f"active user {uid} must backfill exactly ONE 'joined' event, got {rows}"
            )
        for uid in deactivated_ids:
            rows = await conn.fetch(
                "SELECT event_type FROM seat_membership_events WHERE user_id = $1"
                " ORDER BY occurred_at",
                uid,
            )
            assert [r["event_type"] for r in rows] == ["joined", "deactivated"], (
                f"deactivated user {uid} must backfill exactly joined+deactivated, got {rows}"
            )
        total_events = await conn.fetchval(
            "SELECT count(*) FROM seat_membership_events WHERE tenant_id = $1", tenant_id
        )
        assert total_events == 3 + 2 * 2, "no cross-wired or duplicated rows across the mixed batch"
    finally:
        await conn.close()


@pytest.mark.usefixtures("clean_migration_db")
async def test_backfill_cannot_see_a_pre_migration_deactivate_reactivate_cycle() -> None:
    """DEMONSTRATES a structural (not code) limitation: a user who was deactivated then
    reactivated BEFORE this migration ran is backfilled as if continuously active since
    created_at — `users.deactivated_at` only ever records the LATEST transition, so the
    backfill (M4) and the M5 fallback share the SAME blind spot the ledger itself exists
    to fix (R1), for any history that predates the ledger's own existence. This is a
    structural gap in the SOURCE DATA (no durable record of the historical gap exists
    anywhere pre-migration, not even in audit_events per R4), not a fixable code defect
    — but it IS a real, silent OVER-bill risk for any tenant whose true reactivation gap
    overlaps the very first post-cutover billing period. Recorded as a residual limitation
    for §6/§7, not a HARD-STOP."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, PARENT_REVISION)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        await conn.execute("INSERT INTO tenants (id, name) VALUES ($1, 'Ghost Gap Co')", tenant_id)
        # "True" history (never durably recorded anywhere): joined June 1, deactivated
        # July 3, reactivated July 8 — but users.deactivated_at is a CURRENT-STATE-ONLY
        # column, so by migration time it just reads NULL again (as if never deactivated).
        await conn.execute(
            "INSERT INTO users (id, tenant_id, email, password_hash, role, created_at, deactivated_at)"
            " VALUES ($1, $2, 'ghostgap@ghostgap.example', 'x', 'member', $3, NULL)",
            user_id,
            tenant_id,
            dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
        )
    finally:
        await conn.close()

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        rows = await conn.fetch(
            "SELECT event_type, occurred_at FROM seat_membership_events WHERE user_id = $1",
            user_id,
        )
    finally:
        await conn.close()

    # The gap is invisible: only ONE 'joined' event backfilled, never a deactivated/
    # reactivated pair for the true July 3-8 gap.
    assert [r["event_type"] for r in rows] == ["joined"], (
        "confirms the backfill has NO way to see a pre-migration deactivate/reactivate "
        "cycle — this user will be OVER-billed as continuously active across that gap "
        "on the very next invoice generation"
    )

    period_start = dt.datetime(2026, 7, 1)
    period_end = dt.datetime(2026, 8, 1)
    events = tuple(
        MembershipEvent(
            event_type=r["event_type"], occurred_at=r["occurred_at"].replace(tzinfo=None)
        )
        for r in rows
    )
    computed_days = active_days(events, period_start, period_end)
    true_days = 31 - 5  # July 3-7 truly inactive (5 days), by the "true" history above
    assert computed_days == 31, "the backfill bills all 31 July days"
    assert computed_days != true_days, (
        f"OVER-BILL CONFIRMED: backfill yields {computed_days} active days, true count was "
        f"{true_days} — a {computed_days - true_days}-day silent over-charge for this seat "
        f"in the very first post-cutover invoice, structurally unfixable from available data"
    )
