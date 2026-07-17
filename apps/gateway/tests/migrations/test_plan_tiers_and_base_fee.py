"""RED suite — 5-tier plans catalog + base_price_usd_monthly (plan-tiers-and-base-fee
TASK.md §3, FROZEN @ v1, M1/M5/R2).

Real Alembic upgrade to `head` (mirrors tests/migrations/test_account_type_backfill.py's
own `_cfg()`/`clean_migration_db` pattern exactly). RED before BUILD: this task's migration
does not exist yet, so `command.upgrade(cfg, "head")` stops at `a7c3e9f1b2d4` and neither
`plans.base_price_usd_monthly` nor the `free` row appears — the honest missing-implementation
red. DO NOT weaken these tests to make them pass; that is Build's job.

M6 note: the sibling reconciliation of task-1's + the 2 further stale "exactly 3 plans"
migration tests lives in their OWN files (test_account_type_discriminator.py,
test_account_type_backfill.py, test_plan_catalog.py, test_plan_enforcement_migration.py) —
not duplicated here.
"""

from __future__ import annotations

from decimal import Decimal

import asyncpg
import pytest

from .conftest import MIGRATION_DATABASE_URL, MIGRATION_DSN

PARENT_REVISION = "a7c3e9f1b2d4"  # task-1's own head this migration builds on

pytestmark = pytest.mark.asyncio


def _cfg() -> object:
    from alembic.config import Config  # noqa: PLC0415

    from .conftest import ALEMBIC_INI  # noqa: PLC0415

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


@pytest.mark.usefixtures("clean_migration_db")
async def test_five_tier_catalog_seeded_with_exact_figures() -> None:
    """M1 — after upgrade to head, `plans` holds exactly the 5 Tin-locked tiers with the
    exact seat_cap/base_price figures; starter/pro(individual)/team/enterprise keep their
    EXISTING ids (repurposed in place, never deleted+reinserted)."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, PARENT_REVISION)

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        pre_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM plans")}
    finally:
        await conn.close()

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        rows = await conn.fetch(
            "SELECT id, name, display_name, seat_cap, base_price_usd_monthly FROM plans"
            " ORDER BY name"
        )
    finally:
        await conn.close()

    assert len(rows) == 5, f"expected exactly 5 plans, found {len(rows)}"
    by_name = {r["name"]: r for r in rows}
    assert set(by_name) == {"free", "starter", "pro", "team", "enterprise"}
    assert "individual" not in by_name, "individual must be renamed to pro, not coexist"

    free = by_name["free"]
    assert free["display_name"] == "Free"
    assert free["seat_cap"] == 1
    assert free["base_price_usd_monthly"] is None

    starter = by_name["starter"]
    assert starter["display_name"] == "Starter"
    assert starter["seat_cap"] == 1
    assert starter["base_price_usd_monthly"] == Decimal("1.00")
    assert starter["id"] == pre_ids["starter"], "starter must be repurposed in place, same id"

    pro = by_name["pro"]
    assert pro["display_name"] == "Pro"
    assert pro["seat_cap"] == 1
    assert pro["base_price_usd_monthly"] == Decimal("20.00")
    assert pro["id"] == pre_ids["individual"], "pro must be the renamed individual row, same id"

    team = by_name["team"]
    assert team["seat_cap"] is None
    assert team["base_price_usd_monthly"] == Decimal("99.00")
    assert team["id"] == pre_ids["team"]

    enterprise = by_name["enterprise"]
    assert enterprise["seat_cap"] is None
    assert enterprise["base_price_usd_monthly"] is None
    assert enterprise["id"] == pre_ids["enterprise"]


@pytest.mark.usefixtures("clean_migration_db")
async def test_downgrade_restores_task1_catalog() -> None:
    """M5 — downgrading this migration back to a7c3e9f1b2d4 restores EXACTLY task-1's
    post-a7c3e9f1b2d4 catalog: pro renamed back to individual, starter's display_name/
    seat_cap revert to Starter/3, the free row is gone, base_price_usd_monthly no longer
    exists as a column."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, PARENT_REVISION)

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        rows = await conn.fetch("SELECT name, display_name, seat_cap FROM plans ORDER BY name")
        col = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name = 'plans' AND column_name = 'base_price_usd_monthly'"
        )
    finally:
        await conn.close()

    by_name = {r["name"]: r for r in rows}
    assert set(by_name) == {"starter", "team", "enterprise", "individual"}, (
        "free must be gone, pro renamed back to individual"
    )
    assert by_name["individual"]["display_name"] == "Individual"
    assert by_name["starter"]["display_name"] == "Starter"
    assert by_name["starter"]["seat_cap"] == 3
    assert col == 0, "base_price_usd_monthly column should be dropped on downgrade"


@pytest.mark.usefixtures("clean_migration_db")
async def test_non_positive_base_price_rejected_by_check() -> None:
    """R2 — a write of base_price_usd_monthly <= 0 raises the ck_plans_base_price_positive
    CHECK violation (sqlstate 23514); the row is left unchanged."""
    from alembic import command  # noqa: PLC0415

    cfg = _cfg()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute("UPDATE plans SET base_price_usd_monthly = 0 WHERE name = 'team'")
        team_price = await conn.fetchval(
            "SELECT base_price_usd_monthly FROM plans WHERE name = 'team'"
        )
    finally:
        await conn.close()
    assert team_price == Decimal("99.00"), "the row must be left unchanged by the rejected write"
