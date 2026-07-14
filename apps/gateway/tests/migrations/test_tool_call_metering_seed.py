"""RED suite — tool-call-metering seed migration (tool-call-metering TASK.md §3,
FROZEN @ v1, M4/M5/M6; revision b64d469b341e).

Upgrades to the PARENT revision (5c8f3a1e9b2d) first, THEN upgrades to head (this
task's seed migration) and asserts the exact seeded `models` + `pricing_snapshots`
rows — never a live application code path. Mirrors
tests/migrations/test_seat_billing_backfill.py's own upgrade-to-parent-then-head shape.

RED before BUILD: revision b64d469b341e does not exist yet, so `command.upgrade(cfg,
"head")` stops at 5c8f3a1e9b2d and the seeded rows never appear — the honest
missing-implementation red.

DO NOT weaken these tests to make them pass; that is Build's job.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import asyncpg
import pytest

from .conftest import MIGRATION_DATABASE_URL, MIGRATION_DSN

if TYPE_CHECKING:
    from alembic.config import Config

PARENT_REVISION = "5c8f3a1e9b2d"
HEAD_REVISION = "b64d469b341e"
MODEL_ID = "mcp_tool_call"

pytestmark = pytest.mark.asyncio


def _cfg() -> Config:
    from alembic.config import Config  # noqa: PLC0415

    from .conftest import ALEMBIC_INI  # noqa: PLC0415

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


@pytest.mark.usefixtures("clean_migration_db")
async def test_upgrade_seeds_exactly_one_model_and_pricing_snapshot_row() -> None:
    """Scenario: the seed migration inserts exactly one models row + one
    pricing_snapshots row for mcp_tool_call (M4)."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, PARENT_REVISION)

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        pre_models = await conn.fetch("SELECT id FROM models WHERE id = $1", MODEL_ID)
        assert pre_models == [], "the row must not exist before this migration runs"
    finally:
        await conn.close()

    command.upgrade(cfg, HEAD_REVISION)

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        model_rows = await conn.fetch(
            "SELECT id, name, active, modality, provider, region FROM models WHERE id = $1",
            MODEL_ID,
        )
        snapshot_rows = await conn.fetch(
            "SELECT model_id, pricing_unit, unit_usd_per_unit, prompt_usd_per_token,"
            " completion_usd_per_token FROM pricing_snapshots WHERE model_id = $1",
            MODEL_ID,
        )
    finally:
        await conn.close()

    assert len(model_rows) == 1
    model = model_rows[0]
    assert model["active"] is False, "M5: active=false — must never leak into GET /catalog/models"
    assert model["modality"] == "tool_call", "M6: exempts the row from the chat sync sweep"
    assert model["provider"] == "hydroa"
    assert model["region"] == "global"

    assert len(snapshot_rows) == 1
    snap = snapshot_rows[0]
    assert snap["pricing_unit"] == "per_tool_call"
    assert Decimal(str(snap["unit_usd_per_unit"])) == Decimal("0.0025")
    assert Decimal(str(snap["prompt_usd_per_token"])) == Decimal("0")
    assert Decimal(str(snap["completion_usd_per_token"])) == Decimal("0")


@pytest.mark.usefixtures("clean_migration_db")
async def test_downgrade_removes_both_seeded_rows() -> None:
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, HEAD_REVISION)
    command.downgrade(cfg, PARENT_REVISION)

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        model_rows = await conn.fetch("SELECT id FROM models WHERE id = $1", MODEL_ID)
        snapshot_rows = await conn.fetch(
            "SELECT model_id FROM pricing_snapshots WHERE model_id = $1", MODEL_ID
        )
    finally:
        await conn.close()

    assert model_rows == []
    assert snapshot_rows == []


@pytest.mark.usefixtures("clean_migration_db")
async def test_reapplying_the_raw_seed_sql_is_idempotent() -> None:
    """Safety rule (TASK.md §5): both INSERTs are ON CONFLICT DO NOTHING — re-running
    the exact same seed SQL a second time (simulating a re-run / inconsistent version
    state) must NOT create a duplicate row or raise."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, HEAD_REVISION)

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        # Re-run the identical upgrade() SQL a second time, bypassing alembic's
        # version-table guard (which would otherwise just no-op the whole migration).
        await conn.execute(
            "INSERT INTO models (id, name, context_length, active, modality, provider, region)"
            " VALUES ('mcp_tool_call', 'MCP Tool Call', NULL, false, 'tool_call', 'hydroa', 'global')"
            " ON CONFLICT (id) DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
            "  pricing_unit, unit_usd_per_unit)"
            " VALUES ('6cb58c1d-16c1-53d1-a000-c60c06024935', 'mcp_tool_call', 0, 0,"
            "  'per_tool_call', 0.0025)"
            " ON CONFLICT (id) DO NOTHING"
        )
        model_count = await conn.fetchval("SELECT count(*) FROM models WHERE id = $1", MODEL_ID)
        snapshot_count = await conn.fetchval(
            "SELECT count(*) FROM pricing_snapshots WHERE model_id = $1", MODEL_ID
        )
    finally:
        await conn.close()

    assert model_count == 1
    assert snapshot_count == 1
