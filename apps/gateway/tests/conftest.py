"""Shared fixtures: app wired to a real Postgres (fresh schema per test).

Store isolation (test-db-isolation, v12): a global autouse fixture surgically clears the
leaked usage state in the test Redis (db 9) before each test — XTRIM usage:events (so the
flusher's consumer group SURVIVES) + DEL usage:spend:* — so a leaked undelivered stream
entry from one suite cannot be consumed by a later flusher-driving suite and INSERTed as a
usage_record against a freshly-recreated schema (the FK-violation flake). The per-test
drop_all/create_all already isolates Postgres; this closes the Redis channel that was
un-isolated, WITHOUT a blanket FLUSHDB (which would destroy the consumer group).
"""

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests.credential_stub import install_stub_resolver
from tests import _infra_guard, _rate_limit_clock, _redis_env

# ---------------------------------------------------------------------------
# suite-infra-tripwire §3 — infrastructure guards (todo #83).
# ---------------------------------------------------------------------------
# Both hooks are THIN on purpose: every decision lives in tests/_infra_guard.py, which is
# pure and directly unit-tested (tests/suite_infra_tripwire). A hook body cannot be tested,
# so nothing is decided in one.
#
# Why they exist: on 2026-08-05 `make ci` ran 2265 tests green, then the dev Postgres
# container exited, and the run finished `131 failed, 2130 errors` after 43 minutes — a
# result indistinguishable from a catastrophic code regression. `pytest_sessionstart`
# closes the stack-absent-at-start case; `pytest_runtest_logreport` closes the
# stack-died-mid-run case, which is the one that actually happened.
_INFRA_TRIPWIRE = _infra_guard.InfraTripwire()


def pytest_sessionstart(session: pytest.Session) -> None:
    """Refuse to start when the test stack is absent (M1).

    Controller-only (M5) — xdist runs this in every worker too, and 12 duplicate probes
    plus 12 copies of the message help nobody. Opt-out via the env var for `make
    test-fast`, whose suites are deliberately DB-free (M4); every other entry point,
    including a bare `uv run pytest`, is guarded — that is the path the incident took.
    """
    if os.environ.get(_infra_guard.SKIP_ENV_VAR):
        return
    if not _infra_guard.is_controller(session.config):
        return
    problems = _infra_guard.check_infra(
        database_url=_redis_env.TEST_DATABASE_URL,
        redis_hostport=_infra_guard.redis_hostport(_redis_env.TEST_REDIS_URL),
        timeout=_infra_guard.DEFAULT_TIMEOUT_SECONDS,
    )
    if problems:
        pytest.exit(
            "\nTEST INFRASTRUCTURE IS NOT AVAILABLE — no tests were run.\n"
            + "\n".join(f"  - {problem}" for problem in problems)
            + f"\nSet {_infra_guard.SKIP_ENV_VAR}=1 to bypass (only correct for the "
            "deliberately DB-free suites in `make test-fast`).\n",
            returncode=4,
        )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Abort once the infrastructure is confirmed lost mid-run (M2/M3).

    Fed from `setup` and `call`, never `teardown`. That is not arbitrary: the 2026-08-05
    incident produced 2130 ERRORS, not failures — a dead Postgres breaks the `app` /
    `_schema` fixtures, which pytest reports at `setup`. A call-only hook would have
    watched the entire incident go by. Exactly one of setup/call reports per test (a
    failed setup means no call runs), so the consecutive counter stays one-per-test;
    teardown is excluded because it can add a second report for the same test.

    The controller receives every worker's reports under xdist, so this sees the whole run
    from one place. No opt-out: it can only fire on failures that are already happening.
    """
    if report.when not in ("setup", "call"):
        return
    if report.when == "setup" and not report.failed:
        return  # the `call` report for this test carries its verdict
    _INFRA_TRIPWIRE.record(failed=report.failed, text=str(report.longrepr or ""))
    if _INFRA_TRIPWIRE.tripped:
        pytest.exit(_INFRA_TRIPWIRE.reason, returncode=4)


# Per-xdist-worker isolation lives in tests/_redis_env.py (the single source of truth):
# under `pytest -n N` each worker gets a private Postgres database + Redis logical db, so
# the per-test drop_all/create_all and the autouse Redis clear below never collide across
# workers. Non-xdist runs keep the historical gateway_test / db 9 names unchanged.
TEST_DATABASE_URL = _redis_env.TEST_DATABASE_URL
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


# ---------------------------------------------------------------------------
# suite-stability M11 — the suite must not reach the internet on its own account.
# ---------------------------------------------------------------------------
# `catalog_refresh_interval_seconds` (3600) and `health_check_interval_seconds` (60)
# both default ON, and both schedulers run an IMMEDIATE first cycle at lifespan
# startup against the REAL https://openrouter.ai. Instrumenting socket.getaddrinfo
# over the lifespan-driving modules measured 50 live DNS lookups across 20 tests —
# an internet round-trip, and an unbounded socket read, inside `make ci`. Nothing
# asserts on those responses; the egress is purely incidental. The fix is at the
# source, in the `settings` fixture below: 0 is each knob's documented opt-OUT
# sentinel, so no task is started at all.
#
# A blanket DNS block was TRIED here and REVERTED, which is worth recording so it is
# not re-attempted blind. It broke 14 tests in tests/azure_aad + tests/azure_audio,
# and for a reason that matters: `EgressPolicy.check()` resolves the target host on
# purpose, as the SSRF / DNS-rebinding control (azure_ad.py:148, checked fresh before
# the client_secret enters the POST body). Blocking or synthesising DNS makes a
# SECURITY control observe whatever the test harness decides to answer — the class of
# change that hides a real vulnerability rather than a flake. Those suites already
# resolved real names before this task, so the block added risk without fixing the
# defect that was actually measured. Residual exposure is recorded as a todo.

_PRESERVE_KEYS = (b"usage:events", "usage:events")


async def _clear_usage_leaks_if_reachable(redis_url: str) -> None:
    """Reset the test Redis (db 9) to a clean slate per test; no-op if Redis is unreachable.

    FULL cross-suite isolation, but still SURGICAL — must NOT destroy the flusher's
    consumer group: a blanket FLUSHDB deletes the `usage:events` stream together with its
    `ledger-flusher` consumer group, which breaks every flusher-driving suite (NOGROUP on
    XREADGROUP) and makes the suite 3x slower as those tests retry on broken state.

    The deterministic-test-isolation task (2026-06-30) widened this from a usage-only
    clear (XTRIM usage:events + DEL usage:spend:*) to delete EVERY key EXCEPT the
    `usage:events` stream, because the narrow clear left `resp-cache:` / `ratelimit:` /
    worker-counter keys to accumulate across suites (or inherit from a prior partial run)
    and contaminate later stateful suites — the source of the full-suite flakiness
    (response_caching / routing_config_store / openrouter_cost_recovery / pii_v2 passed in
    isolation but failed together). Net per test:
      - XTRIM usage:events to 0 → clears the UNDELIVERED backlog (the FK-violation channel)
        while PRESERVING the stream + its consumer group.
      - DEL every other key (resp-cache:, embed-cache:, ratelimit:, bandwidth:, soft_budget:,
        worker sweeps, …) → no inherited Redis state of ANY namespace bleeds across tests.
    All test fixtures are function-scoped (app recreated per test) and no suite seeds Redis
    at module/session scope, so per-test full clear is safe. Graceful degradation (no-op when
    Redis absent) keeps the no-infra `make test-fast` suites runnable.
    """
    r = aioredis.from_url(redis_url)
    try:
        await r.xtrim("usage:events", maxlen=0, approximate=False)
        leaked = [k async for k in r.scan_iter(match="*", count=1000) if k not in _PRESERVE_KEYS]
        if leaked:
            await r.delete(*leaked)
    except (RedisError, OSError):
        # redis.exceptions.ConnectionError/TimeoutError subclass RedisError, NOT the
        # builtin ConnectionError — catch RedisError so an unreachable Redis is a no-op.
        return
    finally:
        await r.aclose()


@pytest.fixture(autouse=True)
async def _isolate_stores(settings: Settings) -> AsyncIterator[None]:
    """Clear leaked usage state in the test Redis (db 9) BEFORE each test.

    Setup-only: each test starts with no inherited usage:events backlog / spend counters,
    so a leaked undelivered stream entry from one suite cannot be consumed by a later
    flusher-driving suite and INSERTed as a usage_record against a freshly-recreated
    schema (the FK-violation flake). The clear is SURGICAL (XTRIM + targeted DEL), never
    a FLUSHDB, so the flusher's consumer group survives. We do NOT cancel pending tasks
    (that kills the pytest-asyncio/anyio runner); function-scoped event loops already kill
    a test's leaked tasks at loop close.
    """
    await _clear_usage_leaks_if_reachable(settings.redis_url)
    yield


@pytest.fixture(scope="session", autouse=True)
async def _ensure_worker_database() -> AsyncIterator[None]:
    """Create this xdist worker's private Postgres database, drop it at session end.

    No-op for a non-xdist run (the base gateway_test already exists and is managed by dev
    tooling). Under `pytest -n N`, worker gwK needs gateway_test_gwK to exist before its
    per-test create_all runs. Design-for-failure: bounded connect timeout, CREATE guarded
    by an existence check AND an idempotent DuplicateDatabase catch (two workers can race),
    and a best-effort FORCE drop on teardown so runs don't accumulate stale databases.
    """
    import asyncpg  # local import: the raw driver is only needed for DB/extension setup

    idx = _redis_env._worker_index()
    if idx is None:
        # Non-xdist (serial) run: the base gateway_test DB already exists, but suites
        # with their OWN engine/create_all fixture (the *_db.py store tests) bypass the
        # `app` fixture's CREATE EXTENSION. Since vector-store-core put a Vector column on
        # the shared Base.metadata, EVERY create_all now needs the pgvector extension —
        # ensure it on the base test DB here so serial runs of those suites don't fail
        # with `type "vector" does not exist`. Idempotent, bounded timeout (design for failure).
        base_dsn = _redis_env.TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        try:
            base_conn = await asyncpg.connect(dsn=base_dsn, timeout=10)
            try:
                await base_conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            finally:
                await base_conn.close()
        except (OSError, asyncpg.PostgresError):
            # Best-effort: if the extension can't be created (e.g. non-pgvector image),
            # the suites that need it will fail loudly with a clear error, as before.
            pass
        yield
        return

    admin_dsn = _redis_env._BASE_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    dbname = _redis_env.TEST_DATABASE_URL.rpartition("/")[2]

    async def _admin() -> "asyncpg.Connection":  # type: ignore[type-arg]
        return await asyncpg.connect(dsn=admin_dsn, timeout=10)

    conn = await _admin()
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            try:
                await conn.execute(f'CREATE DATABASE "{dbname}"')
            except asyncpg.DuplicateDatabaseError:
                pass  # a sibling worker won the create race — fine, it exists now
    finally:
        await conn.close()

    # vector-store-core PLAN.md §3 provisioning plan: the extension lives PER
    # DATABASE, so each xdist worker's private database needs its own
    # CREATE EXTENSION — idempotent, bounded connect timeout (design for failure).
    worker_dsn = admin_dsn.rsplit("/", 1)[0] + f"/{dbname}"
    worker_conn = await asyncpg.connect(dsn=worker_dsn, timeout=10)
    try:
        await worker_conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await worker_conn.close()

    yield

    try:
        conn = await _admin()
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        finally:
            await conn.close()
    except (OSError, asyncpg.PostgresError):
        # Teardown is best-effort: a stale worker DB is harmless (recreated next run).
        pass


@pytest.fixture
def settings() -> Settings:
    # Redis db 9 matches every suite's redis_client fixture (flushed per test).
    # Before team-governance the app default (db 0) silently diverged from the
    # db the suites seed/inspect — v3 suites masked it by rewiring
    # app.state.budget_guard per test; aligning here removes the footgun.
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        # signup-and-routing-authz TASK.md §3 (S1): public signup now defaults OFF
        # (`Settings.public_signup_enabled`). Every OTHER suite that inherits this fixture
        # bootstraps a tenant via POST /admin/auth/signup and always assumed signup was
        # open — flip it ON here so that assumption keeps holding for everyone EXCEPT the
        # signup_routing_authz suite's flag-OFF tests, which build their own second app
        # (tests/signup_routing_authz/conftest.py `second_app_client`) with the flag
        # explicitly False. The production default itself stays OFF (config.py).
        public_signup_enabled=True,  # type: ignore[call-arg]
        # suite-stability M11: both of these default to ON in production
        # (catalog 3600s, health 60s) and their schedulers fire an IMMEDIATE cycle at
        # lifespan startup against the REAL https://openrouter.ai — measured at 50 live
        # DNS lookups across 20 tests. That put an internet round-trip, and an unbounded
        # socket read, inside `make ci`. 0 is each knob's documented opt-OUT sentinel, so
        # no task is started at all. Suites that actually exercise these schedulers build
        # their own Settings and stub the source (tests/catalog_refresh_scheduler).
        catalog_refresh_interval_seconds=0,  # type: ignore[call-arg]
        health_check_interval_seconds=0,  # type: ignore[call-arg]
    )


@pytest.fixture(scope="session")
async def _schema(_ensure_worker_database: None) -> AsyncIterator[list[str]]:
    """Build this worker's schema ONCE, and return the sequences to reset per test.

    suite-stability §3: this DDL used to run in the function-scoped `app` fixture —
    `drop_all` + `create_all` over 56 tables, for each of 4488 tests, ~500k DDL
    statements per run. Every `drop_all` takes an AccessExclusiveLock across
    pg_catalog, so N xdist workers serialise on the catalog and the run stalls near
    completion (todo #39; reproduced 2026-07-25 as a 64-minute hang at 99%).
    Measured: 663 ms/test here vs ~11 ms for the DELETE sweep below.

    Sequences are DISCOVERED from pg_sequences rather than hand-listed, so one added
    by a future migration is reset automatically instead of drifting out of a manifest.
    """
    engine = create_async_engine(_redis_env.TEST_DATABASE_URL)
    async with engine.begin() as conn:
        # vector-store-core PLAN.md §3 provisioning plan: the chunk ORM registers a
        # Vector column on Base.metadata, so EVERY create_all needs the extension —
        # idempotent, a no-op once the dev postgres image ships pgvector.
        await conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        sequences = list(
            (
                await conn.execute(
                    sa_text("SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'")
                )
            ).scalars()
        )
    await engine.dispose()
    yield sequences


@pytest.fixture
async def app(settings: Settings, _schema: list[str]) -> AsyncIterator[object]:
    application = create_app(settings)
    # credential-resolution-seam §3: override the real DbTenantProviderKeyStore-backed
    # resolver (which would need a Fernet key + a seeded per-tenant key) with a stub, so
    # these feature suites' completions resolve a credential without per-test key seeding.
    install_stub_resolver(application)
    engine = application.state.engine
    # Per-test reset: rows and identity state, NO DDL. `session_replication_role =
    # replica` disables FK triggers for the transaction so delete order is irrelevant;
    # `setval(..., 1, false)` restores sequences to the state a fresh schema has
    # (deliberately NOT `ALTER SEQUENCE ... RESTART`, which is DDL).
    async with engine.begin() as conn:
        await conn.execute(sa_text("SET session_replication_role = replica"))
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(sa_text(f'DELETE FROM "{table.name}"'))  # noqa: S608
        for sequence in _schema:
            await conn.execute(sa_text(f"SELECT setval('\"{sequence}\"', 1, false)"))
        await conn.execute(sa_text("SET session_replication_role = origin"))
    yield application
    await engine.dispose()


@pytest.fixture
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db_session(app: object) -> AsyncIterator[AsyncSession]:
    async with app.state.sessionmaker() as session:  # type: ignore[attr-defined]
        yield session


# ---------------------------------------------------------------------------
# Helios harness fixtures (agent-coding-stub-harness TASK.md §3)
# ---------------------------------------------------------------------------


@dataclass
class _UsageSnapshot:
    """Duck-type of UsageRecordRow for harness-layer recorded_usage queries.

    Field names mirror UsageRecordRow so tests can access them identically.
    """

    model_id: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    status: int = 0
    tenant_id: uuid.UUID | None = None
    key_id: uuid.UUID | None = None


class _HarnessUsageRecorder:
    """In-process usage recorder that captures records for recorded_usage reads.

    Installed at app.state.usage_recorder by the stub_upstream / recorded_usage fixtures.
    Avoids the async Redis→Postgres flush path (non-deterministic timing in tests).
    """

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        usage: dict[str, Any] | None,
        status: int,
        **_kwargs: Any,
    ) -> None:
        self.records.append(
            {
                "tenant_id": tenant_id,
                "key_id": key_id,
                "model": model,
                "usage": usage,
                "status": status,
            }
        )


@pytest.fixture
def stub_upstream(app: object) -> Any:
    """Factory fixture: builds a StubCompletionUpstream and installs it on app.state.

    Usage::

        def test_foo(stub_upstream, client, api_key, active_model):
            stub = stub_upstream(complete=(200, {...}))
            # app.state.completion_upstream is now that stub
    """
    from tests._helios_harness import StubCompletionUpstream

    def _factory(
        *,
        complete: tuple[int, dict[str, object]] | None = None,
        stream: list[bytes] | None = None,
    ) -> StubCompletionUpstream:
        stub = StubCompletionUpstream(complete=complete, stream=stream)
        app.state.completion_upstream = stub  # type: ignore[attr-defined]
        return stub

    return _factory


@pytest.fixture
def recorded_usage(app: object) -> Any:
    """Installs a HarnessUsageRecorder on app.state and returns an async callable.

    Call ``await recorded_usage()`` after a SEAM-B request to get the last
    captured usage record as a _UsageSnapshot (duck-type of UsageRecordRow).

    Usage::

        async def test_foo(client, stub_upstream, recorded_usage, api_key, active_model):
            stub_upstream(complete=(200, body))
            await client.post(...)
            row = await recorded_usage()
            assert row.prompt_tokens == 7
    """
    recorder = _HarnessUsageRecorder()
    app.state.usage_recorder = recorder  # type: ignore[attr-defined]

    async def _get(model: str | None = None) -> _UsageSnapshot | None:
        recs = recorder.records
        if model is not None:
            recs = [r for r in recs if r.get("model") == model]
        if not recs:
            return None
        r = recs[-1]
        usage: dict[str, Any] = r.get("usage") or {}
        return _UsageSnapshot(
            model_id=r.get("model", ""),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            status=int(r.get("status", 0)),
            tenant_id=r.get("tenant_id"),
            key_id=r.get("key_id"),
        )

    return _get


@pytest.fixture(autouse=True)
def _pinned_rate_limit_windows(request: pytest.FixtureRequest) -> Iterator[None]:
    """Give every test its own fixed-window rate-limit bucket (todo #111).

    Autouse and unconditional. A fixed-window limiter buckets on floor(now / 60), so a test
    firing N requests and expecting the (N+1)th to be rejected silently assumes no minute
    boundary lands between them — a bet that loses a few percent of the time under -n 12 and
    then reads as a limiter regression. Pinning the limiter's own injected clock removes the
    boundary entirely; see tests/_rate_limit_clock.py for why this is a class patch and not
    a global `time.time` freeze, and why each test gets a DISTINCT window.

    Cheap enough to apply everywhere: it wraps seven `__init__`s and restores them, with no
    IO and no import of the app. Applying it only to the ~21 tests that assert 429 today
    would leave the next such test unprotected, which is how this class of defect spreads.
    """
    yield from _rate_limit_clock.pin_fixed_windows(request.node.nodeid)
