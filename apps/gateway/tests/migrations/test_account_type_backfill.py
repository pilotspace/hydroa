"""RED suite — account_type migration: backfill + individual-plan seed
(account-type-discriminator TASK.md §3, FROZEN @ v1, M5/M6).

Upgrades to the PARENT revision (the current head, 22164094fd6b), seeds a customer tenant and the
reserved platform tenant via raw SQL (simulating rows that predate this migration), THEN upgrades to
head (this task's migration) and asserts: every kind='customer' row backfilled account_type='business',
kind='platform' left NULL, and the seeded `individual` plan row present. Reversibility is asserted by
downgrading back to PARENT and confirming the column + individual plan seed are gone.

RED before BUILD: this task's migration does not exist yet, so `command.upgrade(cfg, "head")` stops at
22164094fd6b and neither the column nor the individual plan appears — the honest missing-implementation
red. DO NOT weaken these tests to make them pass; that is Build's job.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import asyncpg
import pytest

from .conftest import MIGRATION_DATABASE_URL, MIGRATION_DSN

PARENT_REVISION = "22164094fd6b"

pytestmark = pytest.mark.asyncio


def _cfg() -> object:
    from alembic.config import Config  # noqa: PLC0415

    from .conftest import ALEMBIC_INI  # noqa: PLC0415

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


@pytest.mark.usefixtures("clean_migration_db")
async def test_backfill_customer_business_platform_null() -> None:
    """M5 — customer rows backfill 'business'; the platform row stays NULL."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, PARENT_REVISION)

    # The reserved platform tenant is already seeded by an ancestral migration
    # (3fc2328e5e82_platform_tenant_seed) and `tenants_platform_kind_uidx` forbids a second
    # one — so we seed ONLY a customer row and read the pre-existing platform row back.
    customer_id = uuid.uuid4()
    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, kind) VALUES ($1, 'Acme', 'customer')", customer_id
        )
    finally:
        await conn.close()

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        cust = await conn.fetchval("SELECT account_type FROM tenants WHERE id = $1", customer_id)
        plat = await conn.fetchval("SELECT account_type FROM tenants WHERE kind = 'platform'")
    finally:
        await conn.close()

    assert cust == "business"
    assert plat is None


@pytest.mark.usefixtures("clean_migration_db")
async def test_pro_plan_renamed_from_individual_seat_cap_one() -> None:
    """M6 — the migration seeds an `individual`-lineage plan row with seat_cap=1.

    M6 reconciliation (plan-tiers-and-base-fee TASK.md §3, FROZEN @ v1): at the REAL
    current `alembic head` (past this task's own migration), the row this task-1
    migration seeded as `individual` has been renamed in place to `pro` with
    base_price_usd_monthly=20.00 — same row/id, same seat_cap=1, updated name/price.
    Was test_individual_plan_seeded_seat_cap_one."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        row = await conn.fetchrow(
            "SELECT name, seat_cap, base_price_usd_monthly FROM plans WHERE name = 'pro'"
        )
        individual_row = await conn.fetchrow("SELECT 1 FROM plans WHERE name = 'individual'")
    finally:
        await conn.close()

    assert row is not None, "pro plan (renamed from individual) not seeded"
    assert row["seat_cap"] == 1
    assert row["base_price_usd_monthly"] == Decimal("20.00")
    assert individual_row is None, "individual must no longer exist as a plan name"


@pytest.mark.usefixtures("clean_migration_db")
async def test_platform_account_type_check_rejects() -> None:
    """R2 — the defense-in-depth CHECK forbids account_type on a platform tenant."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, "head")

    # Use the pre-seeded reserved platform tenant (the single-platform unique index forbids
    # inserting our own) — the CHECK must reject setting account_type on it.
    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        platform_id = await conn.fetchval("SELECT id FROM tenants WHERE kind = 'platform'")
        assert platform_id is not None, "platform tenant seed missing"
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "UPDATE tenants SET account_type = 'business' WHERE id = $1", platform_id
            )
    finally:
        await conn.close()


@pytest.mark.usefixtures("clean_migration_db")
async def test_downgrade_removes_column_and_seed() -> None:
    """M5 — reversible: downgrade drops account_type + the individual plan seed."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, PARENT_REVISION)

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        col = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'tenants' AND column_name = 'account_type'"
        )
        plan = await conn.fetchval("SELECT count(*) FROM plans WHERE name = 'individual'")
    finally:
        await conn.close()

    assert col == 0, "account_type column should be dropped on downgrade"
    assert plan == 0, "individual plan seed should be removed on downgrade"
