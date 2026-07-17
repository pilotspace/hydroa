"""Red suite for catalog-refresh-scheduler (catalog-celery-refresh v2) — TASK.md §4.

The mechanism change-request (Tin 2026-07-16): Celery worker+beat → in-process asyncio
lifespan sweeper (the repo runs redis 8.x; celery/kombu caps redis<6.5). This suite
exercises the CatalogRefreshScheduler directly against the test DB + a stub CatalogSource
— no broker, no worker process, no outbound HTTP.

RED before BUILD: `from gateway.catalog.application.refresh_scheduler import ...` fails with
ModuleNotFoundError until the scheduler module exists.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.application.refresh_scheduler import (
    CatalogRefreshScheduler,
    should_start_catalog_refresh,
)

from .conftest import FakeCatalogModel, FakeCatalogSource

_TEST_JWT = "test-secret-not-for-production-0123456789"


def _model(model_id: str, *, provider: str, name: str | None = None) -> FakeCatalogModel:
    return FakeCatalogModel(
        id=model_id,
        name=name or model_id,
        context_length=128_000,
        prompt_usd_per_token=1e-6,
        completion_usd_per_token=2e-6,
        provider=provider,
    )


async def _active_ids(session: AsyncSession, provider: str) -> set[str]:
    rows = (
        await session.execute(
            text("SELECT id FROM models WHERE active = true AND provider = :p"),
            {"p": provider},
        )
    ).all()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# should_start gate — M6/interval-sentinel
# ---------------------------------------------------------------------------
async def test_should_start_catalog_refresh_predicate() -> None:
    assert should_start_catalog_refresh(0) is False
    assert should_start_catalog_refresh(-1) is False
    assert should_start_catalog_refresh(1) is True
    assert should_start_catalog_refresh(3600) is True


# ---------------------------------------------------------------------------
# refresh_once happy path — reuses SyncCatalogUseCase, returns processed count
# ---------------------------------------------------------------------------
async def test_refresh_once_syncs_models_and_returns_count(
    app: Any, db_session: AsyncSession
) -> None:
    source = FakeCatalogSource(
        models=[
            _model("openrouter/a", provider="openrouter"),
            _model("openrouter/b", provider="openrouter"),
        ]
    )
    scheduler = CatalogRefreshScheduler(
        session_factory=app.state.sessionmaker, catalog_source=source
    )

    count = await scheduler.refresh_once()

    assert count == 2
    assert await _active_ids(db_session, "openrouter") == {"openrouter/a", "openrouter/b"}


# ---------------------------------------------------------------------------
# refresh_once fail-open — source down → 0, no partial write, never raises  (M5)
# ---------------------------------------------------------------------------
async def test_refresh_once_source_unavailable_returns_zero_no_write(
    app: Any, db_session: AsyncSession
) -> None:
    source = FakeCatalogSource(raise_unavailable=True)
    scheduler = CatalogRefreshScheduler(
        session_factory=app.state.sessionmaker, catalog_source=source
    )

    count = await scheduler.refresh_once()  # must NOT raise

    assert count == 0
    assert await _active_ids(db_session, "openrouter") == set()  # zero rows written


# ---------------------------------------------------------------------------
# provider-scoped deactivation — an openrouter refresh leaves other providers  (M3)
# ---------------------------------------------------------------------------
async def test_refresh_once_provider_scoped_leaves_other_providers_untouched(
    app: Any, db_session: AsyncSession
) -> None:
    # Seed one openrouter row + one minimax row via an initial sync of each source.
    seed_or = CatalogRefreshScheduler(
        session_factory=app.state.sessionmaker,
        catalog_source=FakeCatalogSource(
            models=[
                _model("openrouter/keep", provider="openrouter"),
                _model("openrouter/drop", provider="openrouter"),
            ]
        ),
    )
    assert await seed_or.refresh_once() == 2
    seed_mm = CatalogRefreshScheduler(
        session_factory=app.state.sessionmaker,
        catalog_source=FakeCatalogSource(models=[_model("minimax/chat", provider="minimax")]),
    )
    assert await seed_mm.refresh_once() == 1

    # Now refresh openrouter with a SMALLER list (drops openrouter/drop).
    refresh_or = CatalogRefreshScheduler(
        session_factory=app.state.sessionmaker,
        catalog_source=FakeCatalogSource(models=[_model("openrouter/keep", provider="openrouter")]),
    )
    assert await refresh_or.refresh_once() == 1

    # openrouter/drop deactivated; openrouter/keep still active; minimax UNTOUCHED.
    assert await _active_ids(db_session, "openrouter") == {"openrouter/keep"}
    assert await _active_ids(db_session, "minimax") == {"minimax/chat"}


# ---------------------------------------------------------------------------
# run_forever loop survives a raised cycle, cancels cleanly
# ---------------------------------------------------------------------------
async def test_run_forever_ticks_then_swallows_and_cancels(app: Any) -> None:
    ticks = 0

    class _BoomThenOkSource(FakeCatalogSource):
        async def list_models(self) -> Any:  # type: ignore[override]
            nonlocal ticks
            ticks += 1
            if ticks == 1:
                raise RuntimeError("first tick boom")  # must be swallowed
            return
            yield  # pragma: no cover — async generator

    scheduler = CatalogRefreshScheduler(
        session_factory=app.state.sessionmaker, catalog_source=_BoomThenOkSource()
    )

    task = asyncio.ensure_future(scheduler.run_forever(interval_seconds=0.01))
    for _ in range(30):
        await asyncio.sleep(0.01)
        if ticks >= 3:
            break
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert ticks >= 3, f"loop must survive a raised cycle and keep ticking; got {ticks}"


# ---------------------------------------------------------------------------
# lifespan wiring — started when interval>0, None when interval=0
# ---------------------------------------------------------------------------
async def test_wired_when_interval_positive() -> None:
    from gateway.core.config import Settings
    from gateway.main import create_app

    from tests import _redis_env

    settings = Settings(
        environment="test",
        jwt_secret=_TEST_JWT,
        database_url=_redis_env.TEST_DATABASE_URL,
        catalog_refresh_interval_seconds=3600,
    )
    app = create_app(settings)
    # Replace the real OpenRouter-backed source so the immediate boot refresh hits no network.
    app.state.catalog_source = FakeCatalogSource(models=[])
    async with app.router.lifespan_context(app):
        task = getattr(app.state, "catalog_refresh_task", None)
        assert task is not None, "interval>0 → catalog_refresh_task must be started"
        assert not task.done()
    assert task.cancelled() or task.done()
    await app.state.engine.dispose()


async def test_default_off_not_wired() -> None:
    from gateway.core.config import Settings
    from gateway.main import create_app

    from tests import _redis_env

    settings = Settings(
        environment="test",
        jwt_secret=_TEST_JWT,
        database_url=_redis_env.TEST_DATABASE_URL,
        catalog_refresh_interval_seconds=0,
    )
    app = create_app(settings)
    app.state.catalog_source = FakeCatalogSource(models=[])
    async with app.router.lifespan_context(app):
        sentinel = object()
        task = getattr(app.state, "catalog_refresh_task", sentinel)
        assert task is None, f"interval=0 → catalog_refresh_task must be None; got {task!r}"
    await app.state.engine.dispose()
