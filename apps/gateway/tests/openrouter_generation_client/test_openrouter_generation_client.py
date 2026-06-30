"""Red suite for openrouter-generation-client (v30 t6.1) — TASK.md §4.

OpenRouterCompletionUpstream.get_generation(id) fetches the authoritative cost +
native token usage of a past generation via GET /api/v1/generation?id=... — the
read-side primitive for disconnect cost-recovery. It runs through the shared
execute_with_retry seam (timeout + bounded retry + circuit breaker), parses money
as Decimal (never float), and returns None when the generation is not available.

Fast: httpx MockTransport (no network) + a request-scoped BearerCredential.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderKeyMissing
from gateway.proxy.infrastructure.openrouter_upstream import (
    GenerationCost,
    OpenRouterCompletionUpstream,
)

pytestmark = pytest.mark.asyncio

_CRED = BearerCredential(secret="sk-or-test-key")  # noqa: S106
_BASE = "https://openrouter.ai/api/v1"
_GEN_ID = "gen-abc123"

_FULL_DATA = {
    "data": {
        "total_cost": 0.0123,
        "upstream_inference_cost": 0.0100,
        "native_tokens_prompt": 11,
        "native_tokens_completion": 5,
        "native_tokens_cached": 2,
    }
}


def _make_adapter(handler: object, *, max_retries: int = 0) -> OpenRouterCompletionUpstream:
    adapter = OpenRouterCompletionUpstream(
        base_url=_BASE, max_retries=max_retries, backoff_base=0.0
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url=_BASE,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


def _counting_handler(
    responses: list[httpx.Response], counter: list[int], *, seen_urls: list[str] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    """Return responses in order (last one repeats); count + record every request seen."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen_urls is not None:
            seen_urls.append(str(request.url))
        i = min(counter[0], len(responses) - 1)
        counter[0] += 1
        return responses[i]

    return handler


async def test_get_generation_parses_cost_and_tokens() -> None:
    counter = [0]
    adapter = _make_adapter(_counting_handler([httpx.Response(200, json=_FULL_DATA)], counter))
    token = set_provider_credential(_CRED)
    try:
        result = await adapter.get_generation(_GEN_ID)
    finally:
        reset_provider_credential(token)
    assert isinstance(result, GenerationCost)
    assert result.total_cost == Decimal("0.0123")
    assert isinstance(result.total_cost, Decimal)
    assert result.upstream_inference_cost == Decimal("0.01")
    assert result.native_tokens_prompt == 11
    assert result.native_tokens_completion == 5
    assert result.native_tokens_cached == 2
    # The id was sent as the query param.
    assert counter[0] == 1


async def test_total_cost_is_exact_decimal() -> None:
    # A JSON NUMBER (not a string) — exercises the real float->str->Decimal path.
    body = {"data": {"total_cost": 0.00000123, "native_tokens_prompt": 1}}
    adapter = _make_adapter(_counting_handler([httpx.Response(200, json=body)], [0]))
    token = set_provider_credential(_CRED)
    try:
        result = await adapter.get_generation(_GEN_ID)
    finally:
        reset_provider_credential(token)
    assert result is not None
    assert result.total_cost == Decimal("0.00000123")  # exact, never Decimal(float)


async def test_zero_cost_is_valid_not_none() -> None:
    # total_cost present and 0 = a real zero-cost (free/cached) generation, NOT 'not ready'.
    adapter = _make_adapter(
        _counting_handler([httpx.Response(200, json={"data": {"total_cost": 0}})], [0])
    )
    token = set_provider_credential(_CRED)
    try:
        result = await adapter.get_generation(_GEN_ID)
    finally:
        reset_provider_credential(token)
    assert isinstance(result, GenerationCost)
    assert result.total_cost == Decimal("0")


async def test_request_carries_id_query_param() -> None:
    seen: list[str] = []
    adapter = _make_adapter(
        _counting_handler([httpx.Response(200, json=_FULL_DATA)], [0], seen_urls=seen)
    )
    token = set_provider_credential(_CRED)
    try:
        await adapter.get_generation("gen-xyz")
    finally:
        reset_provider_credential(token)
    assert seen and "id=gen-xyz" in seen[0]
    assert "/generation" in seen[0]


async def test_not_ready_404_returns_none() -> None:
    adapter = _make_adapter(
        _counting_handler([httpx.Response(404, json={"error": "not found"})], [0])
    )
    token = set_provider_credential(_CRED)
    try:
        result = await adapter.get_generation(_GEN_ID)
    finally:
        reset_provider_credential(token)
    assert result is None


async def test_auth_4xx_raises() -> None:
    # 401/403 is a PERMANENT failure, not 'not ready' — must raise so the caller stops re-polling.
    adapter = _make_adapter(
        _counting_handler([httpx.Response(401, json={"error": "unauthorized"})], [0])
    )
    token = set_provider_credential(_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await adapter.get_generation(_GEN_ID)
    finally:
        reset_provider_credential(token)


async def test_200_without_total_cost_returns_none() -> None:
    adapter = _make_adapter(_counting_handler([httpx.Response(200, json={"data": {}})], [0]))
    token = set_provider_credential(_CRED)
    try:
        result = await adapter.get_generation(_GEN_ID)
    finally:
        reset_provider_credential(token)
    assert result is None


async def test_transient_5xx_retried_then_success() -> None:
    counter = [0]
    adapter = _make_adapter(
        _counting_handler(
            [httpx.Response(503, json={}), httpx.Response(200, json=_FULL_DATA)], counter
        ),
        max_retries=1,
    )
    token = set_provider_credential(_CRED)
    try:
        result = await adapter.get_generation(_GEN_ID)
    finally:
        reset_provider_credential(token)
    assert isinstance(result, GenerationCost)
    assert result.total_cost == Decimal("0.0123")
    assert counter[0] == 2  # one retry happened
    assert adapter._breaker._failure_count == 0  # type: ignore[attr-defined]  # success reset it


async def test_retries_exhausted_raises() -> None:
    adapter = _make_adapter(_counting_handler([httpx.Response(503, json={})], [0]), max_retries=1)
    token = set_provider_credential(_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await adapter.get_generation(_GEN_ID)
    finally:
        reset_provider_credential(token)


async def test_missing_credential_raises_before_request() -> None:
    counter = [0]
    adapter = _make_adapter(_counting_handler([httpx.Response(200, json=_FULL_DATA)], counter))
    # No set_provider_credential — the contextvar is unset.
    with pytest.raises(ProviderKeyMissing):
        await adapter.get_generation(_GEN_ID)
    assert counter[0] == 0  # no HTTP request was issued
