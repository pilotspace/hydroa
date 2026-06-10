import asyncio
import contextlib

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gateway.budgets.api.router import budget_router
from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard
from gateway.catalog.api.router import catalog_router, internal_catalog_router
from gateway.catalog.infrastructure.openrouter_source import OpenRouterCatalogSource
from gateway.core.config import Settings
from gateway.core.errors import register_error_handlers
from gateway.keys.api.router import admin_router as keys_admin_router
from gateway.keys.api.router import authz_router as keys_authz_router
from gateway.proxy.api.router import proxy_router
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream
from gateway.tenants.api.router import router as tenants_router
from gateway.tenants.infrastructure.argon2_hasher import Argon2PasswordHasher
from gateway.tenants.infrastructure.jwt_service import JwtTokenService
from gateway.tenants.infrastructure.orm import (
    TenantRow as _TenantRow,  # noqa: F401 — ensures budget_usd_monthly column is in ORM metadata
)
from gateway.usage.api.router import usage_router
from gateway.usage.application.flusher import UsageLedgerFlusher
from gateway.usage.application.recorder import RecordingUsageRecorder
from gateway.usage.infrastructure.orm import (
    UsageRecordRow as _UsageRecordRow,  # noqa: F401 — registers ORM metadata
)

internal_router = APIRouter(prefix="/internal")
health_router = APIRouter()


@internal_router.get("/health")
async def internal_health() -> dict[str, str]:
    # Liveness only: must never touch Postgres/Redis/OpenRouter, so a
    # dependency outage can't cascade into the fleet being marked dead.
    return {"status": "ok", "service": "gateway"}


@health_router.get("/health")
async def health() -> dict[str, str]:
    # Top-level liveness probe for docker-compose healthcheck and Envoy routing.
    # Identical contract to /internal/health — never touches Postgres/Redis/OpenRouter.
    return {"status": "ok", "service": "gateway"}


def create_app(settings: Settings | None = None) -> FastAPI:
    """Composition root: wires infrastructure adapters into domain ports."""
    settings = settings if settings is not None else Settings()
    app = FastAPI(title="AI Proxy Gateway", version="0.1.0")

    engine = create_async_engine(settings.database_url)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    app.state.password_hasher = Argon2PasswordHasher()
    app.state.token_service = JwtTokenService(settings)
    # Default catalog source — tests override via app.state.catalog_source
    app.state.catalog_source = OpenRouterCatalogSource(httpx.AsyncClient())
    # Proxy defaults — tests inject fakes via app.state
    app.state.circuit_breaker = CircuitBreaker()
    app.state.completion_upstream = OpenRouterCompletionUpstream(
        api_key=settings.openrouter_api_key
    )

    # Usage metering: wire RecordingUsageRecorder + background flusher
    # Tests inject fakes via app.state.usage_recorder AFTER app creation;
    # the default here is used in production only.
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
    app.state.redis_client = redis_client
    app.state.usage_recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )

    # Budget guard: wire RedisBudgetGuard for production;
    # tests override via app.state.budget_guard after app creation.
    app.state.budget_guard = RedisBudgetGuard(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )

    @app.on_event("startup")
    async def _start_flusher() -> None:
        # Guard: only start the background flusher when running under a real
        # ASGI server (not ASGITransport in tests, which never calls lifespan).
        # The test suite drives flush_once() directly — no timing dependency.

        # Idempotent schema bootstrap for dev/test environments (e.g. e2e compose stack).
        # In production the schema is managed by Alembic migrations; never run create_all there.
        if settings.environment in ("dev", "test"):
            from gateway.core.db import Base  # local import to avoid circular at module level

            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        flusher = UsageLedgerFlusher(
            redis=redis_client,
            session_factory=app.state.sessionmaker,
        )
        app.state.flusher_task = asyncio.create_task(flusher.run_forever())

    @app.on_event("shutdown")
    async def _stop_flusher() -> None:
        task = getattr(app.state, "flusher_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await redis_client.aclose()

    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(internal_router)
    app.include_router(internal_catalog_router)
    app.include_router(tenants_router)
    app.include_router(catalog_router)
    app.include_router(keys_admin_router)
    app.include_router(keys_authz_router)
    app.include_router(proxy_router)
    app.include_router(usage_router)
    app.include_router(budget_router)
    return app
