"""Suite-local fixtures for provider-cost-reconciliation tests (TASK.md §4).

Two seams under test:

1. The recorder cost-basis selection — reuses the same FakeSession 7-tuple
   pricing row as tiered-token-billing (cost-basis rides the USAGE dict + the
   event, NOT the pricing tuple, so `_fetch_latest_pricing` stays a 7-tuple and
   the sibling double is unchanged from t1). The provider branch is what's new.

2. The OpenRouter usage-accounting opt-in knob — a CaptureTransport records the
   outbound request body so the test can assert whether `usage={"include": true}`
   was injected. The Bearer credential contextvar is set by the autouse fixture so
   `_auth_headers()` succeeds (mirrors tests/retry_policy/conftest.py).

Unit tests use FakeSession + StreamCapture (no infra). The DB persistence tests
use real Postgres (localhost:5433) + Redis (localhost:6380 db 9) via the shared
root conftest fixtures, same as tiered_token_billing.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream


# ---------------------------------------------------------------------------
# Fake DB primitives (the 7-tuple pricing row — identical to tiered_token_billing)
# ---------------------------------------------------------------------------


class FakeRow:
    """Minimal row proxy returned by FakeResult.fetchone()."""

    def __init__(self, values: tuple[Any, ...]) -> None:
        self._values = values

    def __getitem__(self, idx: int) -> Any:
        return self._values[idx]


class FakeResult:
    """Minimal query result returned by FakeSession.execute()."""

    def __init__(self, row: FakeRow | None) -> None:
        self._row = row

    def fetchone(self) -> FakeRow | None:
        return self._row


class FakeSession:
    """In-memory async session intercepting `_fetch_latest_pricing` +
    `_fetch_markup_pct`. Returns the 7-value pricing row frozen by t1:

      (id, prompt_price, completion_price, pricing_unit, unit_usd_per_unit,
       cached_input_price, reasoning_price)
    """

    def __init__(
        self,
        *,
        snapshot_id: uuid.UUID | None = None,
        prompt_price: Decimal = Decimal("0"),
        completion_price: Decimal = Decimal("0"),
        pricing_unit: str = "per_token",
        unit_usd_per_unit: Decimal | None = None,
        cached_input_price: Decimal | None = None,
        reasoning_price: Decimal | None = None,
        markup_pct: Decimal = Decimal("0"),
        has_pricing: bool = True,
    ) -> None:
        self.snapshot_id = snapshot_id or uuid.uuid4()
        self.prompt_price = prompt_price
        self.completion_price = completion_price
        self.pricing_unit = pricing_unit
        self.unit_usd_per_unit = unit_usd_per_unit
        self.cached_input_price = cached_input_price
        self.reasoning_price = reasoning_price
        self.markup_pct = markup_pct
        self.has_pricing = has_pricing

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        sql = str(stmt)
        if "pricing_snapshots" in sql:
            if not self.has_pricing:
                return FakeResult(None)
            return FakeResult(
                FakeRow(
                    (
                        str(self.snapshot_id),
                        str(self.prompt_price),
                        str(self.completion_price),
                        self.pricing_unit,
                        str(self.unit_usd_per_unit) if self.unit_usd_per_unit is not None else None,
                        str(self.cached_input_price)
                        if self.cached_input_price is not None
                        else None,
                        str(self.reasoning_price) if self.reasoning_price is not None else None,
                        None,  # cache_creation_usd_per_token (prompt-cache-passthrough §3)
                        # gpt-realtime-relay-billing extended _fetch_latest_pricing to an 11-tuple.
                        None,  # audio_prompt_usd_per_token
                        None,  # audio_completion_usd_per_token
                        None,  # audio_cached_usd_per_token
                    )
                )
            )
        if "tenants" in sql:
            return FakeResult(FakeRow((str(self.markup_pct),)))
        return FakeResult(None)

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeSessionFactory:
    """Async sessionmaker-compatible factory wrapping a FakeSession."""

    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def __call__(self) -> FakeSession:
        return self._session


# ---------------------------------------------------------------------------
# Fake Redis stream sink
# ---------------------------------------------------------------------------


class StreamCapture:
    """Minimal async Redis fake capturing xadd() fields + incrbyfloat() calls."""

    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []
        self.incr_calls: list[tuple[str, float]] = []

    async def xadd(self, stream_key: str, fields: dict[str, str]) -> bytes:
        self.events.append(dict(fields))
        return b"1234567890123-0"

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.incr_calls.append((key, amount))
        return amount

    @property
    def last_event(self) -> dict[str, str]:
        assert self.events, "No events captured — recorder was not called"
        return self.events[-1]


# ---------------------------------------------------------------------------
# OpenRouter usage-accounting knob primitives
# ---------------------------------------------------------------------------


class CaptureTransport(httpx.AsyncBaseTransport):
    """Record every outbound request body; reply 200 with a minimal chat body."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = {
            "id": "gen-pcr-1",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            content=json.dumps(body).encode(),
        )

    @property
    def last_body(self) -> dict[str, Any]:
        assert self.requests, "No request captured — complete() was not called"
        return json.loads(self.requests[-1].content)


def make_openrouter_upstream(
    transport: httpx.AsyncBaseTransport,
    *,
    usage_accounting: bool = False,
) -> OpenRouterCompletionUpstream:
    """Build an OpenRouterCompletionUpstream wired with a capture transport.

    Constructed via __new__ (like tests/retry_policy/conftest.make_upstream) so the
    test exercises complete()'s behavior directly. `_usage_accounting` is the new
    attribute the BUILD must read; setting it here keeps the knob test RED for the
    right reason (before BUILD, complete() ignores it → no usage key injected).
    """
    from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

    upstream = OpenRouterCompletionUpstream.__new__(OpenRouterCompletionUpstream)
    upstream._breaker = CircuitBreaker()
    upstream._client = httpx.AsyncClient(
        base_url="https://openrouter.ai/api/v1",
        transport=transport,
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
    )
    upstream._max_retries = 0
    upstream._backoff_base = 0.5
    upstream._retry_deadline_s = 0.0
    upstream._metrics_registry = None
    upstream._usage_accounting = usage_accounting
    return upstream


@pytest.fixture
def _inject_test_credential() -> Iterator[None]:
    """Inject a Bearer credential via the request-scoped contextvar so the
    OpenRouter adapter's _auth_headers() succeeds (no live key needed)."""
    token = set_provider_credential(BearerCredential(secret="sk-test-pcr"))
    yield
    reset_provider_credential(token)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def snapshot_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def key_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def stream_capture() -> StreamCapture:
    return StreamCapture()


# DB-backed api_key fixture (mirrors tiered_token_billing) — signup → login → key.
@pytest.fixture
async def api_key(client: Any) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "ProviderCostTest",
            "email": "provider-cost-test@example.io",
            "password": "provider cost battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "provider-cost-test@example.io", "password": "provider cost battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "provider-cost-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }
