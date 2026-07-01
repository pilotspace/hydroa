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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests.credential_stub import install_stub_resolver

TEST_DATABASE_URL = os.environ.get(
    "GATEWAY_TEST_DATABASE_URL",
    "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
)
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


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


@pytest.fixture
def settings() -> Settings:
    # Redis db 9 matches every suite's redis_client fixture (flushed per test).
    # Before team-governance the app default (db 0) silently diverged from the
    # db the suites seed/inspect — v3 suites masked it by rewiring
    # app.state.budget_guard per test; aligning here removes the footgun.
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:
    application = create_app(settings)
    # credential-resolution-seam §3: override the real DbTenantProviderKeyStore-backed
    # resolver (which would need a Fernet key + a seeded per-tenant key) with a stub, so
    # these feature suites' completions resolve a credential without per-test key seeding.
    install_stub_resolver(application)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
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
