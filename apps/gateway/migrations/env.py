"""Alembic environment: async SQLAlchemy + asyncpg template.

Reads GATEWAY_DATABASE_URL from the environment (falls back to
sqlalchemy.url in alembic.ini for local development).

All four ORM modules are imported here so that Base.metadata is fully
populated before autogenerate scans the metadata.

compare_type is enabled via a custom callable (not simply True) so that
PostgreSQL-specific false-positive diffs are suppressed:
- TIMESTAMP(timezone=True) vs DateTime() — asyncpg reflects all timestamptz
  columns as TIMESTAMP(timezone=True); the ORM declares Mapped[datetime] which
  Alembic renders as DateTime() without explicit timezone.  Both resolve to the
  same PostgreSQL column type (timestamptz); the difference is representational.
"""

# isort: skip_file
# Deliberate import order: stdlib → third-party → ORM side-effects → gateway.core.db.
# The four gateway.*.infrastructure.orm imports MUST precede `from gateway.core.db import Base`
# so that all ORM models are registered on Base.metadata before autogenerate inspects it.
# Reordering these imports would silently drop tables from the autogenerate scan.

from __future__ import annotations

import asyncio
import concurrent.futures
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import TIMESTAMP as PG_TIMESTAMP
from sqlalchemy.ext.asyncio import create_async_engine

# ORM side-effect imports — register all tables on Base.metadata.
# These are intentional side-effect imports: each module appends its ORM class
# to Base.metadata at import time; they are never referenced by name here.
import gateway.catalog.infrastructure.orm
import gateway.keys.infrastructure.orm
import gateway.tenants.infrastructure.orm
import gateway.usage.infrastructure.orm
import gateway.usage.infrastructure.alert_events_orm  # noqa: F401 — registers AlertEventRow on Base.metadata

# model-mgmt TASK.md §3: registers TenantModelOverrideRow on Base.metadata.
from gateway.catalog.infrastructure.orm import (  # noqa: F401, E402
    TenantModelOverrideRow as _TenantModelOverrideRow,
)

# teams-core TASK.md §3: registers TeamRow and TeamMemberRow on Base.metadata.
import gateway.teams.infrastructure.orm  # noqa: F401 — registers TeamRow/TeamMemberRow on Base.metadata

# oidc-tenant-config TASK.md §3: registers OidcProviderConfigRow on Base.metadata.
import gateway.auth.infrastructure.orm  # noqa: F401 — registers OidcProviderConfigRow on Base.metadata

# provider-credential-store TASK.md §3: registers TenantProviderKeyRow on Base.metadata.
import gateway.proxy.infrastructure.orm  # noqa: F401 — registers TenantProviderKeyRow on Base.metadata

# routing-config-store TASK.md §3: registers RoutingConfigRow on Base.metadata.
import gateway.proxy.infrastructure.routing_config_orm  # noqa: F401 — registers RoutingConfigRow on Base.metadata

# audit-log-store TASK.md §3: registers AuditEventRow on Base.metadata.
import gateway.audit.infrastructure.audit_events_orm  # noqa: F401 — registers AuditEventRow on Base.metadata

# agent-oauth-grant-store TASK.md §3: registers DeviceAuthorizationRow + AgentTokenRow on Base.metadata.
import gateway.agent_oauth.infrastructure.orm  # noqa: F401 — registers agent OAuth tables on Base.metadata

from gateway.core.db import Base

# ---------------------------------------------------------------------------
# Alembic Config object (provides access to alembic.ini values)
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging if present.
# disable_existing_loggers=False: when Alembic runs IN-PROCESS (e.g. the migrations
# test-suite invoking autogenerate), the default True would set disabled=True on every
# already-imported app logger (gateway.*) — silently suppressing their warnings for the
# rest of the process and breaking downstream caplog-based assertions. False preserves them.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Target metadata for autogenerate support.
target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Database URL resolution: env var takes priority over alembic.ini placeholder.
# ---------------------------------------------------------------------------

_db_url = os.environ.get("GATEWAY_DATABASE_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)


# ---------------------------------------------------------------------------
# Type comparator: suppress representational false-positives
# ---------------------------------------------------------------------------


def _compare_type(
    context: object,
    inspected_column: object,
    metadata_column: object,
    inspected_type: object,
    metadata_type: object,
) -> bool | None:
    """Return False to suppress a detected type difference, True to report it.

    Suppresses:
      TIMESTAMP(timezone=True) vs DateTime() — asyncpg reflects timestamptz as
      PG_TIMESTAMP(timezone=True); ORM Mapped[datetime] becomes DateTime().
      Both map to the same PostgreSQL column type; no migration needed.

    Returns None to fall back to Alembic's default comparison for all other pairs.
    """
    # Both are datetime-family types — check the timezone flag mismatch only.
    if isinstance(inspected_type, PG_TIMESTAMP) and isinstance(metadata_type, DateTime):
        # If the DB has timezone=True and ORM has timezone omitted (False by
        # default in DateTime()), this is the well-known asyncpg reflection
        # false-positive.  Suppress it.
        if inspected_type.timezone:
            return False
    return None  # let Alembic decide for all other cases


# ---------------------------------------------------------------------------
# Offline mode (not used in production; kept for completeness)
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection required).

    Emits migration SQL to stdout rather than executing against a live DB.
    Useful for generating SQL scripts for manual review.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=_compare_type,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode (async)
# ---------------------------------------------------------------------------


def do_run_migrations(connection: object) -> None:
    """Execute migrations within an already-connected async connection."""
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        compare_type=_compare_type,
        # include_schemas=False keeps autogenerate focused on the public schema.
        include_schemas=False,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations inside a connection context."""
    connectable = create_async_engine(
        config.get_main_option("sqlalchemy.url"),  # type: ignore[arg-type]
        echo=False,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migration mode (called by Alembic CLI).

    Runs the async migration coroutine in a dedicated thread so that a fresh
    event loop is always available — even when called from within a running
    asyncio event loop (e.g. pytest-asyncio test suite).
    """

    def _run_in_thread() -> None:
        asyncio.run(run_async_migrations())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_in_thread)
        future.result()  # propagate any exception to the calling thread


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
