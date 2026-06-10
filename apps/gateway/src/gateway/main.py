from fastapi import APIRouter, FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gateway.core.config import Settings
from gateway.core.errors import register_error_handlers
from gateway.tenants.api.router import router as tenants_router
from gateway.tenants.infrastructure.argon2_hasher import Argon2PasswordHasher
from gateway.tenants.infrastructure.jwt_service import JwtTokenService

internal_router = APIRouter(prefix="/internal")


@internal_router.get("/health")
async def health() -> dict[str, str]:
    # Liveness only: must never touch Postgres/Redis/OpenRouter, so a
    # dependency outage can't cascade into the fleet being marked dead.
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

    register_error_handlers(app)
    app.include_router(internal_router)
    app.include_router(tenants_router)
    return app
