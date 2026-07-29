"""Proof that the per-test reset still isolates tests.

`suite-stability` replaces a per-test `Base.metadata.drop_all` + `create_all`
(663 ms, 56 tables, 4488 times — ~500k DDL statements whose `pg_catalog`
AccessExclusiveLock is what stalls xdist) with a per-test `DELETE` sweep plus a
sequence reset (~11 ms).

Speed is not the risk. Isolation is: `drop_all` gave a trivially airtight
guarantee, and anything cheaper has to EARN the same one. These tests are that
proof, and they are deliberately written as ORDERED PAIRS in both directions —
a single "insert then assert empty" test would pass even if the reset ran only
at module scope, or only before the first test.

`--dist loadscope` keeps a module on one worker, so the pairs below really do
execute back-to-back on the same database.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Written by the first test of each pair, asserted absent by the second.
_MARKER = "suite-isolation-marker"


async def _insert_tenant(db_session: AsyncSession) -> None:
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(uuid.uuid4()), "name": _MARKER},
    )
    await db_session.commit()


async def _tenant_marker_count(db_session: AsyncSession) -> int:
    result = await db_session.execute(
        text("SELECT count(*) FROM tenants WHERE name = :name"), {"name": _MARKER}
    )
    return int(result.scalar_one())


async def test_a1_writer_commits_rows(db_session: AsyncSession) -> None:
    """Write and COMMIT — an uncommitted write would prove nothing."""
    await _insert_tenant(db_session)
    assert await _tenant_marker_count(db_session) == 1


async def test_a2_reader_sees_an_empty_table(db_session: AsyncSession) -> None:
    """The committed row from test_a1 must NOT be visible here.

    This is the whole isolation guarantee in one assertion: if the reset regresses,
    this is what catches it.
    """
    assert await _tenant_marker_count(db_session) == 0, (
        "a row committed by the previous test survived the per-test reset — test "
        "isolation is broken (§1 R:isolation_broken)"
    )


async def test_b1_reader_before_any_writer_in_this_module(db_session: AsyncSession) -> None:
    """Reverse ordering, so the pair above cannot pass by accident of run order."""
    assert await _tenant_marker_count(db_session) == 0


async def test_b2_writer_after_reader(db_session: AsyncSession) -> None:
    await _insert_tenant(db_session)
    assert await _tenant_marker_count(db_session) == 1


# Tables that legitimately carry rows at test start. `alembic_version` holds the
# single revision marker and is not test data; it exists only if something ran
# alembic against this database.
_NOT_TEST_DATA = frozenset({"alembic_version"})


async def test_c_reset_clears_every_table_in_the_database(db_session: AsyncSession) -> None:
    """Every table IN THE DATABASE must start empty — not every table on `Base.metadata`.

    Guards the drift case where a table added by a later migration is never
    added to the reset and silently accumulates rows across tests — the same
    cross-manifest drift class as the EXPECTED_TABLES manifests.

    Enumerated from `pg_tables`, DELIBERATELY not from `Base.metadata`. The
    per-test sweep in conftest iterates `Base.metadata.sorted_tables`; an earlier
    version of this test iterated the same manifest, which made it unable to fail
    by construction — the sweep deletes exactly the set the assertion then checks,
    and a table missing from the manifest was invisible to both. Reading the
    catalog is what makes "the reset does not cover every table" an observable
    outcome rather than a tautology.
    """
    rows = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )
    tables = sorted(set(rows.scalars().all()) - _NOT_TEST_DATA)
    assert tables, "no tables found in the public schema — the schema fixture did not run"

    non_empty: list[str] = []
    for table in tables:
        result = await db_session.execute(text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
        if int(result.scalar_one()):
            non_empty.append(table)
    assert not non_empty, (
        f"tables not empty at test start: {non_empty} — the per-test reset does not "
        "cover every table in the database"
    )


async def test_d1_advances_the_sequence(db_session: AsyncSession) -> None:
    """Consume the sequence so the NEXT test has something to detect.

    Without this half, the sequence assertion below is vacuous: nothing in the
    suite ever advances `vector_store_chunks_id_seq`, so it reads as pristine
    whether or not the reset touches it. Verified by attack — deleting the
    sequence reset from conftest left the single-test version green.
    """
    for _ in range(3):
        await db_session.execute(text("SELECT nextval('vector_store_chunks_id_seq')"))
    await db_session.commit()
    last = (
        await db_session.execute(text("SELECT last_value FROM vector_store_chunks_id_seq"))
    ).scalar_one()
    assert int(last) >= 3, "the sequence did not advance — this test cannot arm the next one"


async def test_d2_reset_restarts_sequences(db_session: AsyncSession) -> None:
    """Identity state must be reset too, not just rows.

    `DELETE` alone leaves sequences advanced, so a test asserting on a generated
    id would see values a freshly-created schema would never produce. Paired with
    the test above, which guarantees the sequence really was advanced first.
    """
    rows = (
        await db_session.execute(
            text("SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'")
        )
    ).scalars()
    advanced: list[str] = []
    for sequence in rows:
        state = (
            await db_session.execute(
                text(f'SELECT last_value, is_called FROM "{sequence}"')  # noqa: S608
            )
        ).one()
        if state.is_called or state.last_value != 1:
            advanced.append(f"{sequence}(last={state.last_value}, called={state.is_called})")
    assert not advanced, (
        f"sequences not reset at test start: {advanced} — the previous test advanced "
        "one, and a test asserting on a generated id would now see values a fresh "
        "schema would not produce (M6)"
    )


# ---------------------------------------------------------------------------
# M11 — the suite must not depend on the internet
# ---------------------------------------------------------------------------
def test_shared_settings_start_no_network_schedulers(settings: Any) -> None:
    """The shared `settings` fixture must not start a scheduler that calls out.

    Both knobs default to ON in production (catalog 3600s, health 60s) and their
    schedulers run an IMMEDIATE first cycle at lifespan startup against the real
    https://openrouter.ai. Measured before the fix: 50 live DNS lookups across 20
    tests. 0 is each knob's documented opt-OUT sentinel — no task is started.

    Attacked: restoring either default turns this red.
    """
    assert settings.catalog_refresh_interval_seconds == 0, (
        "the shared settings fixture must not start the catalog refresh scheduler — "
        "its first cycle hits the real openrouter.ai at lifespan startup"
    )
    assert settings.health_check_interval_seconds == 0, (
        "the shared settings fixture must not start the upstream health checker — "
        "it pings the real openrouter.ai every interval"
    )
