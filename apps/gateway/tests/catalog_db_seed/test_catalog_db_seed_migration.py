"""Failing-first (RED) suite for catalog-db-seed's migration scenarios (TASK.md §4, §2).

Uses the real Alembic upgrade harness from tests/migrations/conftest.py (a dedicated
gateway_migrations_test database) — Base.metadata.create_all (the standard app/db_session
fixture) never runs data migrations, so "is the catalog actually seeded" can only be proven
by really running the migration. Mirrors tests/platform_tenant_seed's pattern.

Run only this suite:
  cd apps/gateway && GATEWAY_TEST_DATABASE_URL=postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test \
    .venv/bin/python -m pytest tests/catalog_db_seed/test_catalog_db_seed_migration.py -q --no-cov -p no:randomly
"""

from __future__ import annotations

from decimal import Decimal

import asyncpg
import pytest

from tests.migrations.conftest import (  # noqa: F401 — migration_db is a transitive fixture dep
    MIGRATION_DATABASE_URL,
    MIGRATION_DSN,
    clean_migration_db,
    migration_db,
)

_PRIOR_HEAD = "f94771e4aa7c"  # billing-owner-of-record — the head before this task's migration


def _alembic_config() -> object:  # returns alembic.config.Config at runtime
    from alembic.config import Config  # noqa: PLC0415

    from tests.migrations.conftest import ALEMBIC_INI  # noqa: PLC0415

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


# Independently transcribed from the research doc (TASK.md §3 seed-set breakdown) — NOT
# imported from the migration module, so this test cannot be a tautology against itself.
# (id) -> (provider, prompt, completion, cached_input, cache_creation)
_EXPECTED_TOKEN_PRICES: dict[str, tuple[str, Decimal, Decimal, Decimal | None, Decimal | None]] = {
    "MiniMax-M3": (
        "minimax",
        Decimal("0.0000003"),
        Decimal("0.0000012"),
        Decimal("0.00000006"),
        None,
    ),
    "MiniMax-M2.7": (
        "minimax",
        Decimal("0.0000003"),
        Decimal("0.0000012"),
        Decimal("0.00000006"),
        Decimal("0.000000375"),
    ),
    "MiniMax-M2.7-highspeed": (
        "minimax",
        Decimal("0.0000006"),
        Decimal("0.0000024"),
        Decimal("0.00000006"),
        Decimal("0.000000375"),
    ),
    "MiniMax-M2.5": (
        "minimax",
        Decimal("0.0000003"),
        Decimal("0.0000012"),
        Decimal("0.00000003"),
        Decimal("0.000000375"),
    ),
    "MiniMax-M2.5-highspeed": (
        "minimax",
        Decimal("0.0000006"),
        Decimal("0.0000024"),
        Decimal("0.00000003"),
        Decimal("0.000000375"),
    ),
    "MiniMax-M2.1": (
        "minimax",
        Decimal("0.0000003"),
        Decimal("0.0000012"),
        Decimal("0.00000003"),
        Decimal("0.000000375"),
    ),
    "MiniMax-M2.1-highspeed": (
        "minimax",
        Decimal("0.0000006"),
        Decimal("0.0000024"),
        Decimal("0.00000003"),
        Decimal("0.000000375"),
    ),
    "MiniMax-M2": (
        "minimax",
        Decimal("0.0000003"),
        Decimal("0.0000012"),
        Decimal("0.00000003"),
        Decimal("0.000000375"),
    ),
    "text-embedding-3-small": ("openai", Decimal("0.00000002"), Decimal("0.0"), None, None),
    "text-embedding-3-large": ("openai", Decimal("0.00000013"), Decimal("0.0"), None, None),
    "gpt-realtime": (
        "openai",
        Decimal("0.000004"),
        Decimal("0.000016"),
        Decimal("0.0000004"),
        None,
    ),
    "gpt-5.4": ("openai", Decimal("0.0000025"), Decimal("0.000015"), Decimal("0.00000025"), None),
    "gpt-5.4-mini": (
        "openai",
        Decimal("0.00000075"),
        Decimal("0.0000045"),
        Decimal("0.000000075"),
        None,
    ),
    "gpt-realtime-2.1": (
        "openai",
        Decimal("0.000004"),
        Decimal("0.000024"),
        Decimal("0.0000004"),
        None,
    ),
    "gpt-4o-transcribe": ("openai", Decimal("0.0000025"), Decimal("0.00001"), None, None),
    "gpt-4o-mini-transcribe": ("openai", Decimal("0.00000125"), Decimal("0.000005"), None, None),
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0": (
        "bedrock",
        Decimal("0.000003"),
        Decimal("0.000015"),
        None,
        None,
    ),
    "eu.anthropic.claude-3-5-sonnet-20241022-v2:0": (
        "bedrock",
        Decimal("0.000003"),
        Decimal("0.000015"),
        None,
        None,
    ),
    "apac.anthropic.claude-3-5-sonnet-20241022-v2:0": (
        "bedrock",
        Decimal("0.000003"),
        Decimal("0.000015"),
        None,
        None,
    ),
    "us.anthropic.claude-3-5-haiku-20241022-v1:0": (
        "bedrock",
        Decimal("0.0000008"),
        Decimal("0.000004"),
        None,
        None,
    ),
    "eu.anthropic.claude-3-5-haiku-20241022-v1:0": (
        "bedrock",
        Decimal("0.0000008"),
        Decimal("0.000004"),
        None,
        None,
    ),
    "apac.anthropic.claude-3-5-haiku-20241022-v1:0": (
        "bedrock",
        Decimal("0.0000008"),
        Decimal("0.000004"),
        None,
        None,
    ),
    "eu.gemini-2.5-flash": ("vertex", Decimal("0.0000003"), Decimal("0.0000025"), None, None),
    "ap.gemini-2.5-flash": ("vertex", Decimal("0.0000003"), Decimal("0.0000025"), None, None),
    "eu.gemini-2.5-pro": ("vertex", Decimal("0.00000125"), Decimal("0.00001"), None, None),
    "ap.gemini-2.5-pro": ("vertex", Decimal("0.00000125"), Decimal("0.00001"), None, None),
    "eu.gemini-2.5-flash-lite": (
        "vertex",
        Decimal("0.0000001"),
        Decimal("0.0000004"),
        Decimal("0.00000001"),
        None,
    ),
    "ap.gemini-2.5-flash-lite": (
        "vertex",
        Decimal("0.0000001"),
        Decimal("0.0000004"),
        Decimal("0.00000001"),
        None,
    ),
    "eu.gemini-embedding-001": ("vertex", Decimal("0.00000015"), Decimal("0.0"), None, None),
    "ap.gemini-embedding-001": ("vertex", Decimal("0.00000015"), Decimal("0.0"), None, None),
    # dall-e-3/whisper-1/tts-1/tts-1-hd carry prompt=completion=0.0 — covered by
    # _EXPECTED_UNIT_PRICES below for their real (non-token) price.
    "dall-e-3": ("openai", Decimal("0.0"), Decimal("0.0"), None, None),
    "whisper-1": ("openai", Decimal("0.0"), Decimal("0.0"), None, None),
    "tts-1": ("openai", Decimal("0.0"), Decimal("0.0"), None, None),
    "tts-1-hd": ("openai", Decimal("0.0"), Decimal("0.0"), None, None),
}

assert len(_EXPECTED_TOKEN_PRICES) == 34, f"expected 34 rows, got {len(_EXPECTED_TOKEN_PRICES)}"

# id -> (pricing_unit, unit_usd_per_unit or None)
_EXPECTED_UNIT_PRICES: dict[str, tuple[str, Decimal | None]] = {
    "dall-e-3": ("per_image", None),  # Tin SCOPE-CUT — bills $0 as today
    "whisper-1": ("per_second", Decimal("0.0001")),
    "tts-1": ("per_character", Decimal("0.000015")),
    "tts-1-hd": ("per_character", Decimal("0.00003")),
}

# §1 Reject list — a research-doc-flagged UNVERIFIED/"confirm before seeding"/"do not seed"
# id that must NEVER appear in `models` after this migration.
_UNVERIFIED_IDS = (
    "amazon.titan-text-express-v1",
    "amazon.titan-text-lite-v1",
    "amazon.titan-image-generator-v1",
    "us.anthropic.claude-opus-4",  # Claude-Opus/new-Sonnet Bedrock conflict figures
    "amazon.nova-micro-v1:0",
    "amazon.nova-lite-v1:0",
    "amazon.nova-pro-v1:0",
    "meta.llama3-3-70b-instruct-v1:0",
    "gpt-5.5",
    "gpt-5.6-sol",
    "gpt-image-1",
    "gpt-image-2",
    "sora-2",
    "sora-2-pro",
    "chat-latest",
    "gpt-5.3-codex",
    "gpt-realtime-2.1-mini",
    "gpt-realtime-translate",
    "text-embedding-ada-002",
    "text-embedding-005",
    "gemini-3.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
)


@pytest.mark.usefixtures("clean_migration_db")
async def test_fresh_db_after_migration_seeds_34_models_active_with_correct_prices() -> None:
    from alembic import command  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        model_rows = await conn.fetch(
            "SELECT id, active, provider FROM models WHERE id = ANY($1)",
            list(_EXPECTED_TOKEN_PRICES),
        )
        assert len(model_rows) == 34, (
            f"expected all 34 seeded model ids present, got {len(model_rows)}: "
            f"missing {set(_EXPECTED_TOKEN_PRICES) - {r['id'] for r in model_rows}}"
        )
        for row in model_rows:
            assert row["active"] is True, f"{row['id']} must be active=true"
            expected_provider = _EXPECTED_TOKEN_PRICES[row["id"]][0]
            assert row["provider"] == expected_provider, (
                f"{row['id']}: expected provider={expected_provider!r}, got {row['provider']!r}"
            )

        snap_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (model_id) model_id, prompt_usd_per_token,
                   completion_usd_per_token, cached_input_usd_per_token,
                   cache_creation_usd_per_token
            FROM pricing_snapshots
            WHERE model_id = ANY($1)
            ORDER BY model_id, captured_at DESC
            """,
            list(_EXPECTED_TOKEN_PRICES),
        )
        assert len(snap_rows) == 34
        for row in snap_rows:
            _provider, prompt, completion, cached_input, cache_creation = _EXPECTED_TOKEN_PRICES[
                row["model_id"]
            ]
            assert Decimal(str(row["prompt_usd_per_token"])) == prompt, (
                f"{row['model_id']}: prompt mismatch"
            )
            assert Decimal(str(row["completion_usd_per_token"])) == completion, (
                f"{row['model_id']}: completion mismatch"
            )
            got_cached = (
                Decimal(str(row["cached_input_usd_per_token"]))
                if row["cached_input_usd_per_token"] is not None
                else None
            )
            assert got_cached == cached_input, f"{row['model_id']}: cached_input mismatch"
            got_cache_creation = (
                Decimal(str(row["cache_creation_usd_per_token"]))
                if row["cache_creation_usd_per_token"] is not None
                else None
            )
            assert got_cache_creation == cache_creation, (
                f"{row['model_id']}: cache_creation mismatch"
            )
    finally:
        await conn.close()


@pytest.mark.usefixtures("clean_migration_db")
async def test_non_token_models_carry_real_unit_prices_dall_e_3_scope_cut_to_null() -> None:
    from alembic import command  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (model_id) model_id, pricing_unit, unit_usd_per_unit
            FROM pricing_snapshots
            WHERE model_id = ANY($1)
            ORDER BY model_id, captured_at DESC
            """,
            list(_EXPECTED_UNIT_PRICES),
        )
        by_id = {r["model_id"]: r for r in rows}
        assert set(by_id) == set(_EXPECTED_UNIT_PRICES)
        for model_id, (expected_unit, expected_price) in _EXPECTED_UNIT_PRICES.items():
            row = by_id[model_id]
            assert row["pricing_unit"] == expected_unit, f"{model_id}: pricing_unit mismatch"
            if expected_price is None:
                assert row["unit_usd_per_unit"] is None, (
                    f"{model_id}: expected NULL unit_usd_per_unit (scope-cut), "
                    f"got {row['unit_usd_per_unit']}"
                )
            else:
                assert Decimal(str(row["unit_usd_per_unit"])) == expected_price, (
                    f"{model_id}: unit price mismatch"
                )
    finally:
        await conn.close()


@pytest.mark.usefixtures("clean_migration_db")
async def test_unverified_rows_are_never_seeded() -> None:
    from alembic import command  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        rows = await conn.fetch("SELECT id FROM models WHERE id = ANY($1)", list(_UNVERIFIED_IDS))
    finally:
        await conn.close()
    assert len(rows) == 0, f"UNSEEDED_UNVERIFIED_ROW: found {[r['id'] for r in rows]}"


@pytest.mark.usefixtures("clean_migration_db")
async def test_migration_downgrade_is_reversible_and_upgrade_reproduces_identical_set() -> None:
    from alembic import command  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        before_count = await conn.fetchval(
            "SELECT count(*) FROM models WHERE id = ANY($1)", list(_EXPECTED_TOKEN_PRICES)
        )
    finally:
        await conn.close()
    assert before_count == 34

    command.downgrade(cfg, _PRIOR_HEAD)

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        model_count = await conn.fetchval(
            "SELECT count(*) FROM models WHERE id = ANY($1)", list(_EXPECTED_TOKEN_PRICES)
        )
        snap_count = await conn.fetchval(
            "SELECT count(*) FROM pricing_snapshots WHERE model_id = ANY($1)",
            list(_EXPECTED_TOKEN_PRICES),
        )
    finally:
        await conn.close()
    assert model_count == 0, "ORPHANED_SNAPSHOT_ON_DOWNGRADE guard: models must be fully removed"
    assert snap_count == 0, "downgrade must delete all seeded pricing_snapshots rows"

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        after_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (model_id) model_id, prompt_usd_per_token, completion_usd_per_token
            FROM pricing_snapshots
            WHERE model_id = ANY($1)
            ORDER BY model_id, captured_at DESC
            """,
            list(_EXPECTED_TOKEN_PRICES),
        )
    finally:
        await conn.close()
    assert len(after_rows) == 34, "re-upgrade must reproduce the byte-identical 34-row set"
    for row in after_rows:
        _provider, prompt, completion, _cached, _cache_creation = _EXPECTED_TOKEN_PRICES[
            row["model_id"]
        ]
        assert Decimal(str(row["prompt_usd_per_token"])) == prompt
        assert Decimal(str(row["completion_usd_per_token"])) == completion
