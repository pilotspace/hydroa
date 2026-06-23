"""Red suite for db-migrations (contract DRAFT — TASK.md §2).

One test per scenario. All tests MUST fail before Build because
apps/gateway/alembic.ini and apps/gateway/migrations/ do not exist yet.

Expected red reason: FileNotFoundError / alembic.util.exc.CommandError
"No config file 'alembic.ini' found" when alembic.config.Config(str(ALEMBIC_INI))
is constructed and upgrade/check is attempted.

DO NOT change these tests to make them pass — that is the Build phase's job.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app

from .conftest import ALEMBIC_INI, MIGRATION_DATABASE_URL, MIGRATION_DSN

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPECTED_TABLES = frozenset(
    {
        "tenants",
        "users",
        "api_keys",
        "models",
        "pricing_snapshots",
        "usage_records",
        "alert_events",  # added by spend-windows migration (f4a9b3c7e8d2)
        "tenant_model_overrides",  # SANCTIONED EDIT — model-mgmt TASK.md §3 manifest maintenance; disposition: additive migration e7f3b2a9c4d1 adds this table
        "teams",  # SANCTIONED EDIT — teams-core TASK.md §3 manifest maintenance; disposition: additive migration 3a7f1c9e2b5d adds this table
        "team_members",  # SANCTIONED EDIT — teams-core TASK.md §3 manifest maintenance; disposition: additive migration 3a7f1c9e2b5d adds this table
        "oidc_provider_configs",  # SANCTIONED EDIT — oidc-tenant-config TASK.md §3 manifest maintenance; disposition: additive migration a9b3c4d5e6f7 adds this table
        "tenant_provider_keys",  # SANCTIONED EDIT — provider-credential-store TASK.md §3 manifest maintenance; disposition: additive migration d8f3a1c9e5b2 adds this table
        "routing_config",  # SANCTIONED EDIT — routing-config-store TASK.md §3 manifest maintenance; disposition: additive migration a2c4e6f8b0d1 adds this table
    }
)


def _make_alembic_config() -> object:  # returns alembic.config.Config at runtime
    """Build an Alembic Config pointed at the migration test DB.

    Will raise FileNotFoundError (or alembic.util.exc.CommandError) until
    apps/gateway/alembic.ini is created in the Build phase.
    """
    from alembic.config import Config  # noqa: PLC0415 — intentional late import

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


async def _existing_tables(dsn: str) -> set[str]:
    """Return the set of user-created table names in the public schema."""
    conn: asyncpg.Connection = await asyncpg.connect(dsn=dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename != 'alembic_version'
            """
        )
        return {r["tablename"] for r in rows}
    finally:
        await conn.close()


async def _alembic_version(dsn: str) -> str | None:
    """Return the current alembic version_num, or None if table is absent/empty."""
    conn: asyncpg.Connection = await asyncpg.connect(dsn=dsn)
    try:
        try:
            row = await conn.fetchrow("SELECT version_num FROM alembic_version LIMIT 1")
            return row["version_num"] if row else None
        except asyncpg.UndefinedTableError:
            return None
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Scenario 1 — upgrade-from-empty-parity
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_upgrade_from_empty_parity() -> None:
    """alembic upgrade head on empty DB produces all six tables.

    RED reason: FileNotFoundError — alembic.ini does not exist yet.
    """
    from alembic import command  # noqa: PLC0415

    cfg = _make_alembic_config()

    # Act
    command.upgrade(cfg, "head")

    # Assert: all six tables exist
    tables = await _existing_tables(MIGRATION_DSN)
    assert tables == EXPECTED_TABLES, (
        f"Missing tables after upgrade: {EXPECTED_TABLES - tables}; "
        f"unexpected tables: {tables - EXPECTED_TABLES}"
    )

    # Assert: alembic_version row recorded
    version = await _alembic_version(MIGRATION_DSN)
    assert version is not None, "alembic_version table has no revision after upgrade head"

    # Assert: column names match ORM metadata for each table
    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        for table_name, table_obj in Base.metadata.tables.items():
            rows = await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                """,
                table_name,
            )
            db_cols = {r["column_name"] for r in rows}
            orm_cols = {c.name for c in table_obj.columns}
            assert orm_cols <= db_cols, (
                f"Table {table_name!r}: ORM columns {orm_cols - db_cols} missing in DB"
            )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Scenario 2 — autogenerate-empty-diff
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_autogenerate_empty_diff() -> None:
    """After upgrade head, alembic check detects no pending migrations.

    RED reason: FileNotFoundError — alembic.ini does not exist yet.
    """
    from alembic import command  # noqa: PLC0415
    from alembic.util.exc import CommandError  # noqa: PLC0415

    cfg = _make_alembic_config()
    command.upgrade(cfg, "head")

    # alembic check raises CommandError if there are pending migrations.
    # It should NOT raise here — no diff expected.
    try:
        command.check(cfg)
    except CommandError as exc:
        pytest.fail(
            f"alembic check reported a pending migration after upgrade head: {exc}"
        )


# ---------------------------------------------------------------------------
# Scenario 3 — second-upgrade-idempotent
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_second_upgrade_idempotent() -> None:
    """Running alembic upgrade head twice is a no-op.

    RED reason: FileNotFoundError — alembic.ini does not exist yet.
    """
    from alembic import command  # noqa: PLC0415

    cfg = _make_alembic_config()
    command.upgrade(cfg, "head")
    version_after_first = await _alembic_version(MIGRATION_DSN)

    # Act: second upgrade
    command.upgrade(cfg, "head")

    version_after_second = await _alembic_version(MIGRATION_DSN)
    tables_after_second = await _existing_tables(MIGRATION_DSN)

    assert version_after_second == version_after_first, (
        "Revision changed after idempotent re-upgrade"
    )
    assert tables_after_second == EXPECTED_TABLES, (
        "Table set changed after idempotent re-upgrade"
    )


# ---------------------------------------------------------------------------
# Scenario 4 — downgrade-baseline-drops-cleanly
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_downgrade_base_drops_cleanly() -> None:
    """alembic downgrade base drops all six tables cleanly.

    RED reason: FileNotFoundError — alembic.ini does not exist yet.
    """
    from alembic import command  # noqa: PLC0415

    cfg = _make_alembic_config()
    command.upgrade(cfg, "head")

    # Act
    command.downgrade(cfg, "base")

    tables = await _existing_tables(MIGRATION_DSN)
    assert tables == set(), f"Tables still present after downgrade base: {tables}"

    version = await _alembic_version(MIGRATION_DSN)
    assert version is None, f"alembic_version still has revision after downgrade: {version}"


# ---------------------------------------------------------------------------
# Scenario 5 — create-all-skipped-under-production-env
# ---------------------------------------------------------------------------


async def test_create_all_skipped_under_production_env() -> None:
    """FastAPI startup does NOT call create_all when environment=production.

    This test does NOT need alembic.ini — it only exercises main.py guard logic.
    GREEN at spec time: the guard in main.py already exists (lines 95-99); this
    test formalizes it as a contractual regression guard. If the guard is removed
    or inverted in Build, this test will catch the regression immediately.

    NOT red: the guard is pre-existing behavior being promoted to contract status,
    not a missing Build artifact.
    """
    prod_settings = Settings(
        environment="production",
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="prod-secret-that-is-not-the-dev-default-xyzzy",
    )

    with patch("gateway.core.db.Base.metadata") as mock_metadata:
        mock_metadata.create_all = MagicMock()
        # create_app wires the engine and registers lifespan hooks but does NOT
        # run the startup hook synchronously — it is registered via on_event.
        # We invoke the hook directly by retrieving it from the router.
        app = create_app(prod_settings)

        # Locate and call the startup hook registered via @app.on_event("startup").
        startup_handlers = app.router.on_startup
        for handler in startup_handlers:
            if asyncio_callable(handler):
                await handler()
            else:
                handler()

        mock_metadata.create_all.assert_not_called()


def asyncio_callable(fn: object) -> bool:
    """Return True if fn is a coroutine function."""
    import inspect

    return inspect.iscoroutinefunction(fn)


# ---------------------------------------------------------------------------
# Scenario 6 — parity-gate-red-on-model-change-without-migration
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_parity_gate_red_on_model_change_without_migration() -> None:
    """alembic check fails (CommandError) when an ORM model drifts from the schema.

    RED reason: FileNotFoundError — alembic.ini does not exist yet.

    After Build: upgrades to head, then monkeypatches a new Column onto
    TenantRow.__table__ to simulate drift, then asserts alembic check raises.
    """
    import sqlalchemy as sa  # noqa: PLC0415
    from alembic import command  # noqa: PLC0415
    from alembic.util.exc import CommandError  # noqa: PLC0415

    from gateway.tenants.infrastructure.orm import TenantRow  # noqa: PLC0415

    cfg = _make_alembic_config()
    command.upgrade(cfg, "head")

    # Simulate drift: add a column to the ORM metadata that has no migration.
    drift_col = sa.Column("_drift_test_col", sa.Text, nullable=True)
    TenantRow.__table__.append_column(drift_col)
    try:
        with pytest.raises(CommandError, match="(?i)(target|detected|new upgrade)"):
            command.check(cfg)
    finally:
        # Clean up: remove the drift column from metadata so other tests are unaffected.
        TenantRow.__table__._columns.remove(drift_col)  # noqa: SLF001
