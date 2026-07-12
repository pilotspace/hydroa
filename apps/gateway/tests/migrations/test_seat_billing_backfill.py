"""RED suite — seat-billing migration backfill (seat-billing TASK.md §3 — FROZEN @ v2,
M4/R5).

Upgrades to the PARENT revision (1891020e487c) first, seeds `users` rows directly via
raw SQL (simulating pre-existing tenants that predate this task's migration), THEN
upgrades to head (the seat-billing migration) and asserts the exact backfilled
`seat_membership_events` rows — never a live application code path.

RED before BUILD: revision f1ef6b05a732 does not exist yet, so `command.upgrade(cfg,
"head")` stops at 1891020e487c and the backfilled table/rows never appear — the honest
missing-implementation red.

DO NOT weaken these tests to make them pass; that is Build's job.
"""

from __future__ import annotations

import datetime as dt
import uuid

import asyncpg
import pytest

from .conftest import MIGRATION_DATABASE_URL, MIGRATION_DSN

PARENT_REVISION = "1891020e487c"

pytestmark = pytest.mark.asyncio


def _cfg() -> object:
    from alembic.config import Config  # noqa: PLC0415

    from .conftest import ALEMBIC_INI  # noqa: PLC0415

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


@pytest.mark.usefixtures("clean_migration_db")
async def test_preexisting_active_user_backfills_one_joined_event() -> None:
    """Scenario: 'Pre-existing users are backfilled with a synthetic joined event' (M4)."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, PARENT_REVISION)

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        await conn.execute("INSERT INTO tenants (id, name) VALUES ($1, 'Backfill Co')", tenant_id)
        await conn.execute(
            "INSERT INTO users (id, tenant_id, email, password_hash, role, created_at)"
            " VALUES ($1, $2, 'preexisting@backfill.example', 'x', 'owner', $3)",
            user_id,
            tenant_id,
            # `users.created_at` is a REAL TIMESTAMPTZ column in the migrated (non-
            # create_all) schema (env.py's own _compare_type docstring confirms this
            # representational gap) — bind a tz-AWARE UTC instant so asyncpg's codec
            # never falls back to interpreting a naive value as local wall-clock time.
            dt.datetime(2026, 5, 1, tzinfo=dt.UTC),
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

    assert len(rows) == 1
    assert rows[0]["event_type"] == "joined"
    assert rows[0]["occurred_at"] == dt.datetime(2026, 5, 1, tzinfo=dt.UTC)


@pytest.mark.usefixtures("clean_migration_db")
async def test_preexisting_deactivated_user_backfills_both_events() -> None:
    """Scenario: 'A pre-existing ALREADY-deactivated user backfills both events' (M4)."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, PARENT_REVISION)

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, 'Backfill Deactivated Co')", tenant_id
        )
        await conn.execute(
            "INSERT INTO users"
            " (id, tenant_id, email, password_hash, role, created_at, deactivated_at)"
            " VALUES ($1, $2, 'deactivated@backfill.example', 'x', 'member', $3, $4)",
            user_id,
            tenant_id,
            dt.datetime(2026, 4, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 5, 10, tzinfo=dt.UTC),
        )
    finally:
        await conn.close()

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        rows = await conn.fetch(
            "SELECT event_type, occurred_at FROM seat_membership_events"
            " WHERE user_id = $1 ORDER BY occurred_at",
            user_id,
        )
    finally:
        await conn.close()

    assert len(rows) == 2
    assert rows[0]["event_type"] == "joined"
    assert rows[0]["occurred_at"] == dt.datetime(2026, 4, 1, tzinfo=dt.UTC)
    assert rows[1]["event_type"] == "deactivated"
    assert rows[1]["occurred_at"] == dt.datetime(2026, 5, 10, tzinfo=dt.UTC)


@pytest.mark.usefixtures("clean_migration_db")
async def test_seed_prices_team_15_enterprise_40_starter_null() -> None:
    """DECIDED at freeze review: team $15.00 / enterprise $40.00 / starter NULL."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        rows = await conn.fetch("SELECT name, seat_price_usd_monthly FROM plans")
    finally:
        await conn.close()

    by_name = {r["name"]: r["seat_price_usd_monthly"] for r in rows}
    assert by_name["starter"] is None
    assert by_name["team"] == pytest.approx(15.00)
    assert by_name["enterprise"] == pytest.approx(40.00)
