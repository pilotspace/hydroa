"""Red suite for openrouter-recovery-sweep (v30 t6.3) — TASK.md §4.

The periodic backstop: find flushed client_disconnect ledger rows that carry a
provider_generation_id but have NO openrouter_recovered sibling, gate them to
provider 'openrouter', and call recovery_service.recover() for each. READ-only
against the ledger (recover() does its own append); bounded by a max-age window
and a per-cycle batch limit; never raises; default-OFF.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gateway.usage.application.recovery_sweep import (
    OpenRouterRecoverySweeper,
    should_start_recovery_sweep,
)

pytestmark = pytest.mark.asyncio

_TEST_DB_URL = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"
_TEST_JWT = "test-secret-not-for-production-0123456789"


class _SpyRecovery:
    """Records recover() calls; returns a benign outcome (the sweep ignores it)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def recover(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return None


class _FakeProviderResolver:
    """provider_for returns a per-model verdict; records lookups to prove caching."""

    def __init__(self, mapping: dict[str, str], default: str = "openrouter") -> None:
        self._mapping = mapping
        self._default = default
        self.lookups: list[str] = []

    async def provider_for(self, model_id: str) -> str:
        self.lookups.append(model_id)
        return self._mapping.get(model_id, self._default)


async def _seed_disconnect(
    db_session: Any,
    *,
    tenant_id: str,
    key_id: str,
    gid: str | None,
    model_id: str = "openrouter/sweep-test",
    cost_usd: str = "0.10",
    usage_source: str = "client_disconnect",
    age_hours: float = 0.0,
) -> None:
    """Insert a flushed ledger row directly (optionally aged into the past)."""
    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, status, raw, cost_usd,"
            "  provider_generation_id, usage_source, created_at)"
            " VALUES (:id, :t, :k, :m, 200, '{}', :c, :gid, :src,"
            "         NOW() - (:age || ' hours')::interval)"
        ),
        {
            "id": str(uuid.uuid4()),
            "t": tenant_id,
            "k": key_id,
            "m": model_id,
            "c": cost_usd,
            "gid": gid,
            "src": usage_source,
            "age": str(age_hours),
        },
    )
    await db_session.commit()


async def _row_count(db_session: Any, tenant_id: str) -> int:
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM usage_records WHERE tenant_id = :t"),
        {"t": tenant_id},
    )
    return int(result.scalar_one())


def _sweeper(
    *, app: Any, recovery: _SpyRecovery, resolver: _FakeProviderResolver
) -> OpenRouterRecoverySweeper:
    return OpenRouterRecoverySweeper(
        session_factory=app.state.sessionmaker,
        recovery_service=recovery,  # type: ignore[arg-type]
        provider_resolver=resolver,  # type: ignore[arg-type]
    )


async def test_sweep_recovers_unrecovered_openrouter_disconnect(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    tenant_id, key_id = api_key["tenant_id"], api_key["key_id"]
    await _seed_disconnect(db_session, tenant_id=tenant_id, key_id=key_id, gid="gen-s1")
    before = await _row_count(db_session, tenant_id)

    recovery = _SpyRecovery()
    resolver = _FakeProviderResolver({"openrouter/sweep-test": "openrouter"})
    attempts = await _sweeper(app=app, recovery=recovery, resolver=resolver).sweep_once()

    assert attempts == 1
    assert len(recovery.calls) == 1, f"want one recover() call; got {recovery.calls}"
    assert recovery.calls[0]["provider_generation_id"] == "gen-s1"
    assert str(recovery.calls[0]["tenant_id"]) == tenant_id
    assert recovery.calls[0]["model"] == "openrouter/sweep-test"
    # READ-only: the sweep itself appended no rows (recover() is spied) — and in particular
    # created no openrouter_recovered row of its own (that is recover()'s job, not the sweep's).
    assert await _row_count(db_session, tenant_id) == before
    recovered = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM usage_records"
            " WHERE tenant_id = :t AND usage_source = 'openrouter_recovered'"
        ),
        {"t": tenant_id},
    )
    assert int(recovered.scalar_one()) == 0


async def test_skips_already_recovered(app: Any, db_session: Any, api_key: dict[str, str]) -> None:
    tenant_id, key_id = api_key["tenant_id"], api_key["key_id"]
    await _seed_disconnect(db_session, tenant_id=tenant_id, key_id=key_id, gid="gen-s2")
    await _seed_disconnect(
        db_session,
        tenant_id=tenant_id,
        key_id=key_id,
        gid="gen-s2",
        usage_source="openrouter_recovered",
    )

    recovery = _SpyRecovery()
    resolver = _FakeProviderResolver({"openrouter/sweep-test": "openrouter"})
    attempts = await _sweeper(app=app, recovery=recovery, resolver=resolver).sweep_once()

    assert attempts == 0
    assert recovery.calls == [], f"recovered sibling → must skip; got {recovery.calls}"


async def test_skips_non_openrouter(app: Any, db_session: Any, api_key: dict[str, str]) -> None:
    tenant_id, key_id = api_key["tenant_id"], api_key["key_id"]
    await _seed_disconnect(
        db_session,
        tenant_id=tenant_id,
        key_id=key_id,
        gid="gen-s3",
        model_id="anthropic/claude",
    )

    recovery = _SpyRecovery()
    resolver = _FakeProviderResolver({"anthropic/claude": "anthropic"})
    attempts = await _sweeper(app=app, recovery=recovery, resolver=resolver).sweep_once()

    assert attempts == 0
    assert recovery.calls == [], f"non-openrouter → no recover(); got {recovery.calls}"


async def test_skips_null_generation_id(app: Any, db_session: Any, api_key: dict[str, str]) -> None:
    tenant_id, key_id = api_key["tenant_id"], api_key["key_id"]
    await _seed_disconnect(db_session, tenant_id=tenant_id, key_id=key_id, gid=None)

    recovery = _SpyRecovery()
    resolver = _FakeProviderResolver({"openrouter/sweep-test": "openrouter"})
    attempts = await _sweeper(app=app, recovery=recovery, resolver=resolver).sweep_once()

    assert attempts == 0
    assert recovery.calls == [], f"null gid → not selected; got {recovery.calls}"


async def test_skips_row_older_than_window(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    tenant_id, key_id = api_key["tenant_id"], api_key["key_id"]
    # Default max_age_hours=24 → a 48h-old row is out of the bounded scan.
    await _seed_disconnect(
        db_session, tenant_id=tenant_id, key_id=key_id, gid="gen-s4", age_hours=48.0
    )

    recovery = _SpyRecovery()
    resolver = _FakeProviderResolver({"openrouter/sweep-test": "openrouter"})
    attempts = await _sweeper(app=app, recovery=recovery, resolver=resolver).sweep_once()

    assert attempts == 0
    assert recovery.calls == [], f"aged-out row → not selected; got {recovery.calls}"


async def test_skips_frame_row_with_generation_id(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    # A non-disconnect ('frame') row that happens to carry a gid already billed the
    # authoritative usage → it must NOT be swept (guards the usage_source filter).
    tenant_id, key_id = api_key["tenant_id"], api_key["key_id"]
    await _seed_disconnect(
        db_session, tenant_id=tenant_id, key_id=key_id, gid="gen-frame", usage_source="frame"
    )

    recovery = _SpyRecovery()
    resolver = _FakeProviderResolver({"openrouter/sweep-test": "openrouter"})
    attempts = await _sweeper(app=app, recovery=recovery, resolver=resolver).sweep_once()

    assert attempts == 0
    assert recovery.calls == [], f"frame row → not selected; got {recovery.calls}"


async def test_provider_resolved_once_per_model(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    # Two disconnect rows on the SAME model → provider_for is called once (cache hit).
    tenant_id, key_id = api_key["tenant_id"], api_key["key_id"]
    await _seed_disconnect(db_session, tenant_id=tenant_id, key_id=key_id, gid="gen-c1")
    await _seed_disconnect(db_session, tenant_id=tenant_id, key_id=key_id, gid="gen-c2")

    recovery = _SpyRecovery()
    resolver = _FakeProviderResolver({"openrouter/sweep-test": "openrouter"})
    attempts = await _sweeper(app=app, recovery=recovery, resolver=resolver).sweep_once()

    assert attempts == 2
    assert len(recovery.calls) == 2
    assert resolver.lookups == ["openrouter/sweep-test"], (
        f"provider_for must be cached per model; got {resolver.lookups}"
    )


async def test_one_failing_row_does_not_abort_cycle(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    # recover() raising on one row must not stop the sweep from attempting the others.
    tenant_id, key_id = api_key["tenant_id"], api_key["key_id"]
    await _seed_disconnect(db_session, tenant_id=tenant_id, key_id=key_id, gid="gen-boom")
    await _seed_disconnect(db_session, tenant_id=tenant_id, key_id=key_id, gid="gen-ok")

    class _PartlyFailingRecovery:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def recover(self, **kwargs: Any) -> Any:
            gid = kwargs["provider_generation_id"]
            self.seen.append(gid)
            if gid == "gen-boom":
                raise RuntimeError("recover boom")
            return None

    recovery = _PartlyFailingRecovery()
    resolver = _FakeProviderResolver({"openrouter/sweep-test": "openrouter"})
    # sweep_once NEVER raises even though one recover() did.
    attempts = await OpenRouterRecoverySweeper(
        session_factory=app.state.sessionmaker,
        recovery_service=recovery,  # type: ignore[arg-type]
        provider_resolver=resolver,  # type: ignore[arg-type]
    ).sweep_once()

    # Both rows were attempted; the failing one is counted as an attempt that raised
    # (it does not increment the success counter), the healthy one succeeds.
    assert set(recovery.seen) == {"gen-boom", "gen-ok"}
    assert attempts == 1, f"only the non-raising row counts as a completed attempt; got {attempts}"


async def test_run_forever_ticks_then_swallows_and_cancels(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    # The loop must keep ticking across a raised sweep cycle and cancel cleanly.
    tenant_id, key_id = api_key["tenant_id"], api_key["key_id"]
    await _seed_disconnect(db_session, tenant_id=tenant_id, key_id=key_id, gid="gen-loop")

    ticks = 0

    class _CountingRecovery:
        async def recover(self, **kwargs: Any) -> Any:
            nonlocal ticks
            ticks += 1
            # First tick raises (must be swallowed); later ticks return.
            if ticks == 1:
                raise RuntimeError("first tick boom")
            return None

    resolver = _FakeProviderResolver({"openrouter/sweep-test": "openrouter"})
    sweeper = OpenRouterRecoverySweeper(
        session_factory=app.state.sessionmaker,
        recovery_service=_CountingRecovery(),  # type: ignore[arg-type]
        provider_resolver=resolver,  # type: ignore[arg-type]
    )

    task = asyncio.ensure_future(sweeper.run_forever(interval_seconds=0.01))
    # Let it tick a few times across the raised first cycle.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if ticks >= 3:
            break
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert ticks >= 3, f"loop must survive a raised cycle and keep ticking; got {ticks}"


async def test_default_off_not_wired() -> None:
    # Default settings (interval 0, recovery disabled) → no sweeper task started.
    from gateway.core.config import Settings
    from gateway.main import create_app

    settings = Settings(environment="test", jwt_secret=_TEST_JWT, database_url=_TEST_DB_URL)
    assert settings.openrouter_recovery_sweep_interval_seconds == 0

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)  # ASGI lifespan runs on client startup
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/health")).status_code == 200
        sentinel = object()
        sweep_task = getattr(app.state, "recovery_sweep_task", sentinel)
        assert sweep_task is None, (
            f"recovery_sweep_task must be None when interval=0 (default-off); got {sweep_task!r}"
        )
    await app.state.engine.dispose()


async def test_not_wired_when_service_absent() -> None:
    # interval > 0 but the cost-recovery service is unwired (recovery disabled) → not started.
    from gateway.core.config import Settings
    from gateway.main import create_app

    settings = Settings(
        environment="test",
        jwt_secret=_TEST_JWT,
        database_url=_TEST_DB_URL,
        openrouter_recovery_sweep_interval_seconds=900,
        openrouter_cost_recovery_enabled=False,
    )
    app = create_app(settings)
    assert getattr(app.state, "cost_recovery_service", "MISSING") is None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/health")).status_code == 200
        sentinel = object()
        sweep_task = getattr(app.state, "recovery_sweep_task", sentinel)
        assert sweep_task is None, (
            f"interval>0 but service unwired → must not start; got {sweep_task!r}"
        )
    await app.state.engine.dispose()


async def test_wired_when_enabled_and_interval_set() -> None:
    # Both gates open (interval > 0 AND recovery service wired) → sweeper task started.
    # ASGITransport does NOT run lifespan, so drive the real lifespan to observe the task.
    from gateway.core.config import Settings
    from gateway.main import create_app

    settings = Settings(
        environment="test",
        jwt_secret=_TEST_JWT,
        database_url=_TEST_DB_URL,
        openrouter_recovery_sweep_interval_seconds=900,
        openrouter_cost_recovery_enabled=True,
    )
    app = create_app(settings)
    assert getattr(app.state, "cost_recovery_service", None) is not None
    async with app.router.lifespan_context(app):  # runs startup, then shutdown on exit
        sweep_task = getattr(app.state, "recovery_sweep_task", None)
        assert sweep_task is not None, "both gates open → recovery_sweep_task must be started"
        assert not sweep_task.done()
    # Shutdown cancelled the task cleanly.
    assert sweep_task.cancelled() or sweep_task.done()
    await app.state.engine.dispose()


async def test_should_start_recovery_sweep_predicate() -> None:
    assert should_start_recovery_sweep(0) is False
    assert should_start_recovery_sweep(-1) is False
    assert should_start_recovery_sweep(1) is True
    assert should_start_recovery_sweep(900) is True
