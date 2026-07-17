"""RED suite: M3/M5 — additive `plans.model_allowlist`/`plans.feature_flags` migration
(TASK.md §3 Schema, FROZEN @ v1). Real Alembic upgrade, mirrors
tests/plan_catalog/test_plan_catalog.py's own `_alembic_config()`/`clean_migration_db`
pattern exactly.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from tests.migrations.conftest import (  # noqa: F401 — migration_db is a transitive fixture dep
    MIGRATION_DATABASE_URL,
    MIGRATION_DSN,
    clean_migration_db,
    migration_db,
)


def _alembic_config() -> object:
    from alembic.config import Config
    from tests.migrations.conftest import ALEMBIC_INI

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


# ---------------------------------------------------------------------------
# M3/M5 — additive columns, seeded correctly for the 3 pre-existing plan-catalog rows
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_migration_seeds_feature_flags_and_leaves_model_allowlist_null() -> None:
    """M6 reconciliation (plan-tiers-and-base-fee TASK.md §3, FROZEN @ v1): at the REAL
    current `alembic head` (past that task's own migration), the catalog holds 5 rows
    (NEW `free`, `individual` renamed to `pro`) instead of 3 — the per-row
    model_allowlist/feature_flags checks below (keyed by starter/team/enterprise, which
    this task never touches) stay untouched, only len()/the name-set are updated."""
    from alembic import command

    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        rows = await conn.fetch(
            "SELECT name, model_allowlist, feature_flags FROM plans ORDER BY name"
        )
    finally:
        await conn.close()

    assert len(rows) == 5, f"expected exactly 5 plans, found {len(rows)}"
    assert {r["name"] for r in rows} == {"free", "starter", "pro", "team", "enterprise"}
    by_name = {r["name"]: r for r in rows}

    for r in rows:
        assert r["model_allowlist"] is None, f"{r['name']}: model_allowlist must stay NULL in v1"

    import json

    def _flags(raw: object) -> set[str]:
        return set(json.loads(raw) if isinstance(raw, str) else raw)

    assert _flags(by_name["starter"]["feature_flags"]) == {"logs_explorer"}
    assert _flags(by_name["team"]["feature_flags"]) == {"logs_explorer", "batch"}
    assert _flags(by_name["enterprise"]["feature_flags"]) == {
        "logs_explorer",
        "batch",
        "ml_moderation",
        "realtime",
    }


@pytest.mark.usefixtures("clean_migration_db")
async def test_migration_is_the_first_to_extend_plans_past_prior_head() -> None:
    """M3/M5 — before this migration runs, `plans` has no model_allowlist/feature_flags
    columns at all (confirms this migration is genuinely additive, not a no-op)."""
    from alembic import command

    cfg = _alembic_config()
    command.upgrade(cfg, "69cfdc584129")  # prior head, before this task's migration

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'plans'"
        )
    finally:
        await conn.close()

    names = {c["column_name"] for c in cols}
    assert "model_allowlist" not in names
    assert "feature_flags" not in names

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'plans'"
        )
    finally:
        await conn.close()
    names = {c["column_name"] for c in cols}
    assert "model_allowlist" in names
    assert "feature_flags" in names


@pytest.mark.usefixtures("clean_migration_db")
async def test_downgrade_is_additive_only_safe() -> None:
    """Downgrade note: additive-only, safe — drops both new columns cleanly."""
    from alembic import command

    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "69cfdc584129")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'plans'"
        )
        # plans table itself (and its 3 rows) must survive the downgrade untouched.
        count = await conn.fetchval("SELECT COUNT(*) FROM plans")
    finally:
        await conn.close()
    names = {c["column_name"] for c in cols}
    assert "model_allowlist" not in names
    assert "feature_flags" not in names
    assert count == 3


@pytest.mark.usefixtures("clean_migration_db")
async def test_a_new_plan_row_created_after_migration_can_set_both_columns() -> None:
    """Sanity: the new columns are genuinely writable (not just present) — mirrors
    plan-catalog's own migration-scenario coverage style."""
    from alembic import command

    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    new_id = uuid.uuid4()
    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        await conn.execute(
            "INSERT INTO plans (id, name, display_name, model_allowlist, feature_flags)"
            " VALUES ($1, 'custom', 'Custom', $2, $3)",
            new_id,
            '["gpt-4o-mini"]',
            '["batch"]',
        )
        row = await conn.fetchrow(
            "SELECT model_allowlist, feature_flags FROM plans WHERE id = $1", new_id
        )
    finally:
        await conn.close()

    import json

    def _as_list(raw: object) -> list[str]:
        return json.loads(raw) if isinstance(raw, str) else raw

    assert _as_list(row["model_allowlist"]) == ["gpt-4o-mini"]
    assert _as_list(row["feature_flags"]) == ["batch"]
