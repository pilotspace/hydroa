"""Fixtures for the Alembic migration test suite.

Uses a dedicated database (gateway_migrations_test) created and dropped per
test session so it never conflicts with the per-test schema-recreating conftest
in tests/conftest.py, which operates on gateway_test via Base.metadata DDL.

Connection idiom mirrors tests/conftest.py: asyncpg DSN with the same
localhost:5433 Postgres instance used by the rest of the test suite.
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PG_ADMIN_DSN = os.environ.get(
    "GATEWAY_TEST_DATABASE_URL",
    "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
).replace("postgresql+asyncpg://", "postgresql://")

# Dedicated database so migration tests never touch gateway_test schema state.
MIGRATION_TEST_DB = "gateway_migrations_test"

# Pure-asyncpg DSN for the dedicated migration DB (no SQLAlchemy prefix).
MIGRATION_DSN = _PG_ADMIN_DSN.replace("/gateway_test", f"/{MIGRATION_TEST_DB}")

# DSN with SQLAlchemy asyncpg prefix — passed to alembic Config via env var.
MIGRATION_DATABASE_URL = MIGRATION_DSN.replace("postgresql://", "postgresql+asyncpg://")

# Absolute path to apps/gateway/ so alembic.ini can be located regardless of cwd.
GATEWAY_ROOT = Path(__file__).parents[2]  # apps/gateway/
ALEMBIC_INI = GATEWAY_ROOT / "alembic.ini"


# ---------------------------------------------------------------------------
# Session-scoped database lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def migration_db() -> asyncpg.Connection:  # type: ignore[type-arg]
    """Create gateway_migrations_test for the session; drop it afterwards.

    Uses a direct asyncpg connection to the admin DB (gateway_test) because
    CREATE DATABASE cannot run inside a transaction block.
    """
    admin_conn: asyncpg.Connection = await asyncpg.connect(dsn=_PG_ADMIN_DSN)
    try:
        await admin_conn.execute(
            f'DROP DATABASE IF EXISTS "{MIGRATION_TEST_DB}" WITH (FORCE)'
        )
        await admin_conn.execute(f'CREATE DATABASE "{MIGRATION_TEST_DB}"')
    finally:
        await admin_conn.close()

    yield  # tests run here

    cleanup_conn: asyncpg.Connection = await asyncpg.connect(dsn=_PG_ADMIN_DSN)
    try:
        await cleanup_conn.execute(
            f'DROP DATABASE IF EXISTS "{MIGRATION_TEST_DB}" WITH (FORCE)'
        )
    finally:
        await cleanup_conn.close()


# ---------------------------------------------------------------------------
# Per-test schema reset
# ---------------------------------------------------------------------------


@pytest.fixture
async def clean_migration_db(migration_db: None) -> None:  # noqa: ARG001
    """Drop and recreate the public schema between tests for isolation."""
    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
    finally:
        await conn.close()
