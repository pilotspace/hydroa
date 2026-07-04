"""Red suite for platform-tenant-seed (contract DRAFT — TASK.md §2).

One test per scenario. Schema/constraint scenarios use the standard `app`/`db_session`
fixture (Base.metadata.create_all — exercises the ORM's __table_args__). Seed/migration
scenarios use the real Alembic upgrade harness from tests/migrations/conftest.py (a
dedicated gateway_migrations_test database) — create_all never runs data migrations, so
"is the platform tenant actually seeded" can only be proven by really running the
migration.

Expected red reason: `tenants.kind` and `get_platform_tenant` do not exist yet — an
undefined-column DB error, an AttributeError, or an ImportError depending on the test.

DO NOT change these tests to make them pass — that is the Build phase's job.
"""

from __future__ import annotations

import uuid

import asyncpg
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.migrations.conftest import (  # noqa: F401 — migration_db is a transitive fixture dep
    MIGRATION_DATABASE_URL,
    MIGRATION_DSN,
    clean_migration_db,
    migration_db,
)


def _alembic_config() -> object:  # returns alembic.config.Config at runtime
    """Build an Alembic Config pointed at the dedicated migration test DB."""
    from alembic.config import Config  # noqa: PLC0415 — intentional late import
    from tests.migrations.conftest import ALEMBIC_INI, MIGRATION_DATABASE_URL  # noqa: PLC0415

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


# ---------------------------------------------------------------------------
# Scenario: existing tenant rows backfill to kind=customer
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_existing_rows_backfill_to_customer_kind() -> None:
    from alembic import command  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "326b927cf8c2")  # prior head, before this task's migration

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        pre_id = uuid.uuid4()
        await conn.execute("INSERT INTO tenants (id, name) VALUES ($1, 'Acme')", pre_id)
    finally:
        await conn.close()

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        row = await conn.fetchrow("SELECT kind, name FROM tenants WHERE id = $1", pre_id)
    finally:
        await conn.close()

    assert row is not None, "pre-existing tenant row vanished across the migration"
    assert row["kind"] == "customer"
    assert row["name"] == "Acme"


# ---------------------------------------------------------------------------
# Scenario: the platform tenant is seeded exactly once
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_platform_tenant_seeded_exactly_once() -> None:
    from alembic import command  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        rows = await conn.fetch("SELECT id, name FROM tenants WHERE kind = 'platform'")
    finally:
        await conn.close()

    assert len(rows) == 1, f"expected exactly 1 platform tenant, found {len(rows)}"
    assert rows[0]["name"] == "Platform"


# ---------------------------------------------------------------------------
# Scenario: re-running the seed migration does not duplicate the platform tenant
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_seed_reapply_does_not_duplicate_platform_tenant() -> None:
    """Directly re-applies the migration's guarded seed INSERT (not `alembic upgrade`
    again — alembic's own version tracking already no-ops a repeat `upgrade head`, which
    would not exercise this migration's own ON CONFLICT guard). This simulates the real
    risk: two app instances racing the same seed statement at startup.
    """
    from alembic import command  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, kind) VALUES (gen_random_uuid(), 'Platform', 'platform') "
            "ON CONFLICT (kind) WHERE kind = 'platform' DO NOTHING"
        )
        count = await conn.fetchval("SELECT count(*) FROM tenants WHERE kind = 'platform'")
    finally:
        await conn.close()

    assert count == 1, f"expected the guard to prevent a duplicate, found {count} platform rows"


# ---------------------------------------------------------------------------
# Scenario: a second platform-kind tenant is rejected
# ---------------------------------------------------------------------------


async def test_second_platform_tenant_insert_rejected(db_session: AsyncSession) -> None:
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": uuid.uuid4()},
    )
    await db_session.commit()

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.execute(
            text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform 2', 'platform')"),
            {"id": uuid.uuid4()},
        )
        await db_session.commit()
    assert getattr(exc_info.value.orig, "sqlstate", None) == "23505"

    await db_session.rollback()
    count = (
        await db_session.execute(text("SELECT count(*) FROM tenants WHERE kind = 'platform'"))
    ).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# Scenario: an invalid kind value is rejected
# ---------------------------------------------------------------------------


async def test_invalid_kind_value_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.execute(
            text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Bogus Co', 'bogus')"),
            {"id": uuid.uuid4()},
        )
        await db_session.commit()
    assert getattr(exc_info.value.orig, "sqlstate", None) == "23514"

    await db_session.rollback()
    count = (
        await db_session.execute(text("SELECT count(*) FROM tenants WHERE name = 'Bogus Co'"))
    ).scalar_one()
    assert count == 0


# ---------------------------------------------------------------------------
# Scenario: normal tenant signup is unaffected
# ---------------------------------------------------------------------------


async def test_signup_still_creates_customer_kind_tenant(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": "Acme", "email": "ada@acme.io", "password": "correct horse battery"},
    )

    assert resp.status_code == 201
    tenant_id = resp.json()["tenant_id"]

    kind = (
        await db_session.execute(text("SELECT kind FROM tenants WHERE id = :id"), {"id": tenant_id})
    ).scalar_one()
    assert kind == "customer"


# ---------------------------------------------------------------------------
# Scenario: the seeded platform tenant has no owner user
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_seeded_platform_tenant_has_no_owner_user() -> None:
    from alembic import command  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        platform_id = await conn.fetchval("SELECT id FROM tenants WHERE kind = 'platform'")
        user_count = await conn.fetchval(
            "SELECT count(*) FROM users WHERE tenant_id = $1", platform_id
        )
    finally:
        await conn.close()

    assert user_count == 0


# ---------------------------------------------------------------------------
# Scenario: the read helper resolves the platform tenant stably
# ---------------------------------------------------------------------------


async def test_get_platform_tenant_resolves_and_is_stable(db_session: AsyncSession) -> None:
    from gateway.tenants.infrastructure.repository import get_platform_tenant  # noqa: PLC0415

    # Decoy customer-kind row — proves get_platform_tenant actually filters by kind,
    # not merely "return whatever single row exists" (refute-read finding).
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Acme', 'customer')"),
        {"id": uuid.uuid4()},
    )

    seed_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": seed_id},
    )
    await db_session.commit()

    first = await get_platform_tenant(db_session)
    second = await get_platform_tenant(db_session)

    assert first is not None
    assert first.id == seed_id
    assert second is not None
    assert second.id == first.id


# ---------------------------------------------------------------------------
# Scenario: the read helper degrades to None on an unmigrated DB, never raises
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_get_platform_tenant_returns_none_when_unmigrated() -> None:
    """Refute-read finding: the frozen contract promises 'None if unmigrated
    (defensive, never raised)' — this proves get_platform_tenant honors that
    against a real DB that predates the kind column, not just a happy path."""
    from alembic import command  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from gateway.tenants.infrastructure.repository import get_platform_tenant  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "326b927cf8c2")  # prior head — no `kind` column yet

    engine = create_async_engine(MIGRATION_DATABASE_URL)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            result = await get_platform_tenant(session)
    finally:
        await engine.dispose()

    assert result is None
