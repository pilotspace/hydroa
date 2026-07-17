"""RED suite — billing_owner_of_record migration: backfill + reversibility
(billing-owner-of-record TASK.md §3, FROZEN @ v1, M1/M8).

Upgrades to the PARENT revision (113ebdbe9f09, the pre-task head), seeds tenants/users
via raw SQL (simulating rows that predate this migration), THEN upgrades to head (this
task's migration) and asserts the §3 backfill rule: earliest ACTIVE owner first,
falling back to the earliest ACTIVE billing_admin, NULL if neither exists;
kind='platform' always stays NULL. Reversibility is asserted by downgrading back to
PARENT and confirming the column + CHECK are gone.

RED before BUILD: this task's migration does not exist yet, so `command.upgrade(cfg,
"head")` stops at 113ebdbe9f09 and `tenants.billing_owner_user_id` never appears — the
honest missing-implementation red. DO NOT weaken these tests to make them pass; that is
Build's job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from .conftest import MIGRATION_DATABASE_URL, MIGRATION_DSN

PARENT_REVISION = "113ebdbe9f09"

pytestmark = pytest.mark.asyncio


def _cfg() -> object:
    from alembic.config import Config  # noqa: PLC0415

    from .conftest import ALEMBIC_INI  # noqa: PLC0415

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


async def _insert_user(
    conn: asyncpg.Connection,
    *,
    tenant_id: uuid.UUID,
    email: str,
    role: str,
    created_at: datetime,
    deactivated: bool = False,
) -> uuid.UUID:
    user_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, tenant_id, email, password_hash, role, created_at, "
        "deactivated_at) VALUES ($1, $2, $3, 'dummyhash', $4, $5, $6)",
        user_id,
        tenant_id,
        email,
        role,
        created_at,
        datetime.now(UTC) if deactivated else None,
    )
    return user_id


@pytest.mark.usefixtures("clean_migration_db")
async def test_backfill_earliest_active_owner() -> None:
    """M1 — two ACTIVE owners (t1 < t2): billing_owner_user_id = the earlier one's id.
    The platform tenant's own billing_owner_user_id stays NULL."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, PARENT_REVISION)

    tenant_id = uuid.uuid4()
    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, kind) VALUES ($1, 'Acme', 'customer')", tenant_id
        )
        t1 = datetime.now(UTC) - timedelta(days=2)
        t2 = datetime.now(UTC) - timedelta(days=1)
        earliest_owner_id = await _insert_user(
            conn, tenant_id=tenant_id, email="owner-t1@acme.io", role="owner", created_at=t1
        )
        await _insert_user(
            conn, tenant_id=tenant_id, email="owner-t2@acme.io", role="owner", created_at=t2
        )
    finally:
        await conn.close()

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        owner = await conn.fetchval(
            "SELECT billing_owner_user_id FROM tenants WHERE id = $1", tenant_id
        )
        platform_owner = await conn.fetchval(
            "SELECT billing_owner_user_id FROM tenants WHERE kind = 'platform'"
        )
    finally:
        await conn.close()

    assert owner == earliest_owner_id
    assert platform_owner is None


@pytest.mark.usefixtures("clean_migration_db")
async def test_backfill_falls_back_to_active_billing_admin() -> None:
    """M1 edge — sole OWNER deactivated, one ACTIVE billing_admin: falls back to the
    billing_admin's id."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, PARENT_REVISION)

    tenant_id = uuid.uuid4()
    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, kind) VALUES ($1, 'Beta', 'customer')", tenant_id
        )
        now = datetime.now(UTC)
        await _insert_user(
            conn,
            tenant_id=tenant_id,
            email="deactivated-owner@beta.io",
            role="owner",
            created_at=now - timedelta(days=3),
            deactivated=True,
        )
        billing_admin_id = await _insert_user(
            conn,
            tenant_id=tenant_id,
            email="billing-admin@beta.io",
            role="billing_admin",
            created_at=now - timedelta(days=1),
        )
    finally:
        await conn.close()

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        owner = await conn.fetchval(
            "SELECT billing_owner_user_id FROM tenants WHERE id = $1", tenant_id
        )
    finally:
        await conn.close()

    assert owner == billing_admin_id


@pytest.mark.usefixtures("clean_migration_db")
async def test_backfill_null_when_no_eligible_user() -> None:
    """M1 edge — zero ACTIVE owner/billing_admin: the migration completes successfully
    and billing_owner_user_id is left NULL (never crashes)."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, PARENT_REVISION)

    tenant_id = uuid.uuid4()
    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, kind) VALUES ($1, 'Ghost', 'customer')", tenant_id
        )
        # Only an active MEMBER — no eligible owner/billing_admin.
        await _insert_user(
            conn,
            tenant_id=tenant_id,
            email="member@ghost.io",
            role="member",
            created_at=datetime.now(UTC),
        )
    finally:
        await conn.close()

    # Must not raise.
    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        owner = await conn.fetchval(
            "SELECT billing_owner_user_id FROM tenants WHERE id = $1", tenant_id
        )
    finally:
        await conn.close()

    assert owner is None


@pytest.mark.usefixtures("clean_migration_db")
async def test_platform_billing_owner_check_rejects() -> None:
    """Defense-in-depth: the CHECK forbids billing_owner_user_id on a platform tenant."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        platform_id = await conn.fetchval("SELECT id FROM tenants WHERE kind = 'platform'")
        assert platform_id is not None, "platform tenant seed missing"
        owner_id = await _insert_user(
            conn,
            tenant_id=platform_id,
            email="platform-owner@platform.internal",
            role="owner",
            created_at=datetime.now(UTC),
        )
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "UPDATE tenants SET billing_owner_user_id = $1 WHERE id = $2",
                owner_id,
                platform_id,
            )
    finally:
        await conn.close()


@pytest.mark.usefixtures("clean_migration_db")
async def test_downgrade_removes_column_and_check() -> None:
    """M8 — reversible: downgrade drops billing_owner_user_id + its CHECK."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, PARENT_REVISION)

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        col = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'tenants' AND column_name = 'billing_owner_user_id'"
        )
        check = await conn.fetchval(
            "SELECT count(*) FROM information_schema.check_constraints "
            "WHERE constraint_name = 'ck_tenants_platform_no_billing_owner'"
        )
    finally:
        await conn.close()

    assert col == 0, "billing_owner_user_id column should be dropped on downgrade"
    assert check == 0, "ck_tenants_platform_no_billing_owner should be dropped on downgrade"
