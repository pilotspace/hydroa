"""Suite-local fixtures for the openai-retry-parity task (TASK.md §4).

Mirrors tests/retry_policy/conftest.py's make_*_upstream pattern for OpenAIDirectProvider,
and reuses that suite's MockTransport / CountingCircuitBreaker / make_json_response doubles.
No DB, no live server.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from gateway.core.config import Settings
from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider
from tests import _redis_env

FAKE_OPENAI_BASE_URL = "https://api.openai.com/v1"
FAKE_API_KEY = "sk-openai-retry-parity"


@pytest.fixture(autouse=True)
def _inject_test_credential() -> AsyncIterator[None]:
    """Inject a Bearer credential via the contextvar so _auth_headers() succeeds
    for every test here (mirrors the retry_policy suite's autouse fixture)."""
    token = set_provider_credential(BearerCredential(secret=FAKE_API_KEY))
    yield  # type: ignore[misc]
    reset_provider_credential(token)


def make_openai_upstream(
    transport: httpx.AsyncBaseTransport,
    breaker: CircuitBreaker | None = None,
    max_retries: int = 0,
    backoff_base: float = 0.5,
    retry_deadline_s: float = 0.0,
    metrics_registry: Any | None = None,
) -> OpenAIDirectProvider:
    """Build an OpenAIDirectProvider wired with a custom transport.

    Mirrors make_anthropic_upstream: real __init__ (post-build this stores the retry
    knobs from its params), then override the client transport and set the retry knobs
    directly — so the suite is valid both before BUILD (knobs set on the instance) and
    after (knobs read by the unified executor).
    """
    upstream = OpenAIDirectProvider(
        base_url=FAKE_OPENAI_BASE_URL,
        metrics_registry=metrics_registry,
    )
    upstream._client = httpx.AsyncClient(
        base_url=FAKE_OPENAI_BASE_URL,
        transport=transport,
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
    )
    if breaker is not None:
        upstream._breaker = breaker
    upstream._max_retries = max_retries
    upstream._backoff_base = backoff_base
    upstream._retry_deadline_s = retry_deadline_s
    return upstream


def make_settings(**kwargs: object) -> Settings:
    """Minimal Settings for create_app() wiring tests (no live DB/Redis needed)."""
    defaults: dict[str, object] = {
        "database_url": _redis_env.TEST_DATABASE_URL,
        "jwt_secret": "test-secret-not-for-production-0123456789",
        "redis_url": _redis_env.TEST_REDIS_URL,
        "environment": "test",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]
