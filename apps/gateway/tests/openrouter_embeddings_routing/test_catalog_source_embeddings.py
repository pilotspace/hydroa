"""Red suite for openrouter-embeddings-routing — OER6/OER6b/OER7 (TASK.md §2).

OpenRouterCatalogSource.list_embedding_models() fetches OpenRouter's separate
embeddings catalog (GET /api/v1/embeddings/models) using the SAME bounded-retry
convention as list_models(), yielding CatalogModel rows with modality="embedding".
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from gateway.catalog.domain.entities import CatalogModel
from gateway.catalog.domain.errors import CatalogSourceUnavailableError
from gateway.catalog.infrastructure.openrouter_source import OpenRouterCatalogSource

pytestmark = pytest.mark.asyncio

_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings/models"

_GEMINI_ITEM = {
    "id": "google/gemini-embedding-2",
    "name": "Gemini Embedding 2",
    "context_length": 2048,
    "pricing": {"prompt": "0.00000015", "completion": "0"},
    "architecture": {"output_modalities": ["embeddings"]},
}


def _make_source(handler: object) -> OpenRouterCatalogSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return OpenRouterCatalogSource(client)


def _counting_handler(
    responses: list[httpx.Response],
    counter: list[int],
    *,
    seen: list[str] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        i = min(counter[0], len(responses) - 1)
        counter[0] += 1
        return responses[i]

    return handler


def _always_failing_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    return handler


async def test_list_embedding_models_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.catalog.infrastructure import openrouter_source as mod

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

    counter = [0]
    seen: list[str] = []
    responses = [
        httpx.Response(503, json={}),
        httpx.Response(503, json={}),
        httpx.Response(200, json={"data": [_GEMINI_ITEM]}),
    ]
    source = _make_source(_counting_handler(responses, counter, seen=seen))

    models = [m async for m in source.list_embedding_models()]

    assert counter[0] == 3
    assert all(url == _EMBEDDINGS_URL for url in seen)
    assert len(models) == 1
    assert models[0].id == "google/gemini-embedding-2"


async def test_list_embedding_models_raises_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.catalog.infrastructure import openrouter_source as mod

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

    source = _make_source(_always_failing_handler())

    with pytest.raises(CatalogSourceUnavailableError):
        [m async for m in source.list_embedding_models()]


async def test_list_embedding_models_yields_modality_embedding_rows() -> None:
    source = _make_source(
        _counting_handler([httpx.Response(200, json={"data": [_GEMINI_ITEM]})], [0])
    )

    models = [m async for m in source.list_embedding_models()]

    assert len(models) == 1
    model = models[0]
    assert isinstance(model, CatalogModel)
    assert model.id == "google/gemini-embedding-2"
    assert model.name == "Gemini Embedding 2"
    assert model.context_length == 2048
    assert model.modality == "embedding"
    assert model.provider == "openrouter"
    # audit-remediation package C1 (CatalogModel float money): prompt_usd_per_token is
    # now Decimal (parsed exactly from the JSON price string) — pytest.approx() can't
    # subtract a bare float from a Decimal (TypeError), so the expected value is now a
    # Decimal too. Exact equality intent unchanged; Decimal parsing is exact so no
    # tolerance is even needed once both sides are Decimal.
    assert model.prompt_usd_per_token == Decimal("0.00000015")
    assert model.completion_usd_per_token == 0.0
