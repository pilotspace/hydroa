from fastapi import APIRouter, FastAPI

internal_router = APIRouter(prefix="/internal")


@internal_router.get("/health")
async def health() -> dict[str, str]:
    # Liveness only: must never touch Postgres/Redis/OpenRouter, so a
    # dependency outage can't cascade into the fleet being marked dead.
    return {"status": "ok", "service": "gateway"}


def create_app() -> FastAPI:
    app = FastAPI(title="AI Proxy Gateway", version="0.1.0")
    app.include_router(internal_router)
    return app
