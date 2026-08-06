"""Failing-first (RED) suite — boot-time fail-closed preflight for the `vector` extension.

vector-extension-preflight PLAN.md §4. Closes release-integrity exit criterion 4 and
todo #85: today a gateway pointed at a Postgres WITHOUT the pgvector `vector` extension
boots happily and serves `/v1/vector_stores`, which 500s the first time a tenant uses it.

RED reason expected (before Build): `gateway.vector_stores.infrastructure.preflight` does
not exist, so every test here fails at import/collection.

DO NOT confuse this with `scripts/pg_preflight.py`. That is the COLLATION preflight
(todo #66) — an operator-run CLI aimed at an arbitrary target database, about musl/glibc
btree ordering. This is an in-process BOOT guard on the gateway's own connection, about a
missing extension. Different hazard, different lifecycle, deliberately separate code.

Arrange note (verified against the live dev image on 2026-08-05, not assumed): a freshly
`CREATE DATABASE`'d database on `pgvector/pgvector:pg16` reports
`SELECT count(*) FROM pg_extension WHERE extname='vector'` -> 0, and template1 reports 0
too, while `pg_available_extensions` reports 1. So a throwaway database is genuinely
vector-LESS (the arrange is real, not vacuous) AND the remedy the error advertises
(`CREATE EXTENSION vector`) would actually succeed there.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from gateway.core.config import Settings
from gateway.main import create_app
from tests import _redis_env

TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


def _admin_url() -> str:
    return _redis_env.TEST_DATABASE_URL


def _url_for(db_name: str) -> str:
    return _admin_url().rsplit("/", 1)[0] + "/" + db_name


def _settings_for(database_url: str) -> Settings:
    """Settings that reach the lifespan without dragging in unrelated startup work.

    The two schedulers are pinned OFF for the same reason the shared conftest pins them
    (suite-stability M11): both fire an immediate cycle against the real openrouter.ai at
    lifespan startup, and this suite has no interest in that traffic.
    """
    return Settings(
        database_url=database_url,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        catalog_refresh_interval_seconds=0,  # type: ignore[call-arg]
        health_check_interval_seconds=0,  # type: ignore[call-arg]
    )


@pytest.fixture
async def vectorless_database() -> AsyncIterator[str]:
    """A throwaway database with NO `vector` extension — the fault under test.

    Asserts the arrange actually holds before yielding: if a future image ever ships the
    extension in template1, every test in this file would silently stop testing anything.
    """
    db = f"vecless_{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db}"'))
    finally:
        await engine.dispose()

    probe = create_async_engine(_url_for(db))
    try:
        async with probe.connect() as conn:
            present = (
                await conn.execute(
                    text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
                )
            ).scalar_one()
        assert present == 0, (
            f"ARRANGE BROKEN: throwaway database {db!r} already has the vector extension, "
            "so this suite would pass without testing anything"
        )
    finally:
        await probe.dispose()

    try:
        yield _url_for(db)
    finally:
        engine = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)'))
        finally:
            await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# M1/M2/M3 — the gateway refuses to boot, actionably, before the worker starts
# ─────────────────────────────────────────────────────────────────────────────


async def test_lifespan_refuses_to_boot_without_vector_extension(
    vectorless_database: str,
) -> None:
    """Entering the lifespan against a vector-less database RAISES. covers: M1

    "Refuses to boot" is not observable from a request — nothing ever serves one. It is
    observable exactly here: the lifespan context manager propagates, so the ASGI server
    never emits `lifespan.startup.complete` and the process does not become ready.
    """
    from gateway.vector_stores.infrastructure.preflight import VectorExtensionMissingError

    app = create_app(_settings_for(vectorless_database))

    with pytest.raises(VectorExtensionMissingError):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover — the raise above is the assertion


async def test_error_names_database_extension_and_remedy(vectorless_database: str) -> None:
    """The error is ACTIONABLE without reading source. covers: M2

    An operator reading this at 3am needs three facts: which database was checked, what is
    missing, and the exact command that fixes it. A bare "preflight failed" would satisfy
    M1 and still leave them guessing, so M2 is asserted separately.
    """
    from gateway.vector_stores.infrastructure.preflight import VectorExtensionMissingError

    app = create_app(_settings_for(vectorless_database))
    expected_db = vectorless_database.rsplit("/", 1)[-1]

    with pytest.raises(VectorExtensionMissingError) as exc_info:
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover

    message = str(exc_info.value)
    assert exc_info.value.code == "ERR_VECTOR_EXTENSION_MISSING", (
        f"the error must carry a stable machine-readable code, got {exc_info.value.code!r}"
    )
    assert expected_db in message, (
        f"error must name the database it checked ({expected_db!r}); got: {message}"
    )
    assert "vector" in message, f"error must name the missing extension; got: {message}"
    assert "CREATE EXTENSION" in message, (
        f"error must state the remedy an operator runs; got: {message}"
    )


async def test_ingest_worker_never_starts_when_preflight_fails(
    vectorless_database: str,
) -> None:
    """ORDERING: the preflight precedes the vector-store ingest worker. covers: M3

    A preflight that ran AFTER the worker wiring would satisfy M1 while still leaving a
    live `run_forever()` task against a database it just declared unusable. Asserting the
    raise alone cannot tell those two apart — the worker attribute can.
    """
    from gateway.vector_stores.infrastructure.preflight import VectorExtensionMissingError

    app = create_app(_settings_for(vectorless_database))

    with pytest.raises(VectorExtensionMissingError):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover

    assert getattr(app.state, "vector_store_worker_task", None) is None, (
        "the ingest worker was wired before the preflight rejected the database — the "
        "preflight must run first"
    )


# ─────────────────────────────────────────────────────────────────────────────
# M4 — the healthy path is unchanged
# ─────────────────────────────────────────────────────────────────────────────


async def test_boots_normally_when_extension_present(app: Any) -> None:
    """A database WITH the extension passes the preflight and boots. covers: M4

    Uses the shared `app` fixture, whose session-scoped `_schema` runs
    `CREATE EXTENSION IF NOT EXISTS vector`. This is the regression arm: a fail-closed
    guard with a non-zero false-positive rate takes down EVERY deployment, so the happy
    path is gated too, not assumed.

    The `assert_vector_extension` call is deliberate. An earlier draft of this test only
    asserted `app is not None` — which passed before the module existed, making it a green
    that proved nothing. Driving the real probe against the real healthy database is what
    makes this arm fail if the guard ever starts rejecting a correct database.
    """
    from gateway.vector_stores.infrastructure.preflight import assert_vector_extension

    # The shared `app` fixture already drove a full lifespan startup with the preflight
    # wired in; reaching here at all means it did not raise.
    engine = app.state.engine
    assert await assert_vector_extension(engine) is None, (
        "the preflight must return None (not raise) against a database that HAS the "
        "extension — a false positive here is a total outage"
    )


# ─────────────────────────────────────────────────────────────────────────────
# R:ERR_VECTOR_PREFLIGHT_UNKNOWN — "could not check" is not "confirmed absent"
# ─────────────────────────────────────────────────────────────────────────────


async def test_unreachable_database_is_unknown_not_missing() -> None:
    """An unreachable database is UNKNOWN, never MISSING. covers: R:ERR_VECTOR_PREFLIGHT_UNKNOWN

    Same discipline `scripts/pg_preflight.py` already enforces for the collation check:
    a failure to reach the server must not be renamed into a different, confident, wrong
    diagnosis. Both still refuse the boot — the distinction is what the operator is told
    to go fix, and "run CREATE EXTENSION" is actively misleading when the real fault is a
    closed port or bad credentials.
    """
    from gateway.vector_stores.infrastructure.preflight import (
        VectorExtensionMissingError,
        VectorPreflightUnknownError,
        assert_vector_extension,
    )

    # Port 1 is reserved and never listening; bounded by the preflight's own connect timeout.
    dead = "postgresql+asyncpg://gateway:gateway@127.0.0.1:1/nope"
    engine = create_async_engine(dead)
    try:
        with pytest.raises(VectorPreflightUnknownError) as exc_info:
            await assert_vector_extension(engine)
    finally:
        await engine.dispose()

    assert not isinstance(exc_info.value, VectorExtensionMissingError), (
        "an unreachable server must not be reported as a missing extension"
    )
    assert exc_info.value.code == "ERR_VECTOR_PREFLIGHT_UNKNOWN"
