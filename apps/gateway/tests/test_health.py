"""Skeleton smoke test (red first): the gateway exposes a liveness endpoint.

Envoy and orchestrators probe /internal/health; it must answer without
touching Postgres, Redis, or OpenRouter so a dependency outage never makes
the fleet look dead.
"""

import httpx

from gateway.main import create_app


async def test_health_returns_ok() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "gateway"}
