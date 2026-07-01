"""OpenRouter HTTP adapter implementing the CatalogSource port.

Conventions (CONVENTIONS.md): explicit 10s timeout, bounded retry max 2 with
jitter on the idempotent GET only.  All failures map to CatalogSourceUnavailableError.
No real calls are ever made in tests — the port is injected via app.state.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator

import httpx

from gateway.catalog.domain.entities import CatalogModel
from gateway.catalog.domain.errors import CatalogSourceUnavailableError

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
# openrouter-embeddings-routing TASK.md §3: a structurally SEPARATE, disjoint
# catalog (zero id overlap with _OPENROUTER_MODELS_URL, verified live).
_OPENROUTER_EMBEDDINGS_MODELS_URL = "https://openrouter.ai/api/v1/embeddings/models"
_TIMEOUT = httpx.Timeout(10.0)
_MAX_RETRIES = 2
_RETRY_BASE_SECONDS = 0.5


class OpenRouterCatalogSource:
    """Fetch model catalog from OpenRouter with retry + jitter.

    Only idempotent GET retried (max 2 attempts after initial failure).
    Non-transient failures mapped to CatalogSourceUnavailableError immediately.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def list_models(self) -> AsyncIterator[CatalogModel]:
        """Stream CatalogModel instances from the OpenRouter API."""
        data = await self._fetch_with_retry(_OPENROUTER_MODELS_URL)
        for item in data:
            model = self._parse_item(item, modality="chat")
            if model is not None:
                yield model

    async def list_embedding_models(self) -> AsyncIterator[CatalogModel]:
        """Stream CatalogModel instances from OpenRouter's separate embeddings catalog.

        openrouter-embeddings-routing TASK.md §3: fetches
        _OPENROUTER_EMBEDDINGS_MODELS_URL via the SAME bounded-retry helper as
        list_models() (parameterized by URL — no bespoke second retry
        implementation), yielding rows with modality="embedding". Raises
        CatalogSourceUnavailableError on exhausted retries — symmetric with
        list_models(); this method does NOT swallow its own failure.
        """
        data = await self._fetch_with_retry(_OPENROUTER_EMBEDDINGS_MODELS_URL)
        for item in data:
            model = self._parse_item(item, modality="embedding")
            if model is not None:
                yield model

    @staticmethod
    def _parse_item(item: dict[str, object], *, modality: str) -> CatalogModel | None:
        """Parse one raw OpenRouter catalog row into a CatalogModel, or None if malformed.

        Shared by list_models() and list_embedding_models() — identical
        id/name/context_length/pricing parsing regardless of which catalog it came from.
        """
        raw_id = item.get("id")
        if not isinstance(raw_id, str) or not raw_id:
            return None
        model_id: str = raw_id
        ctx = item.get("context_length")
        raw_pricing = item.get("pricing", {})
        pricing: dict[str, object] = raw_pricing if isinstance(raw_pricing, dict) else {}
        try:
            prompt = float(str(pricing.get("prompt", "0")))
            completion = float(str(pricing.get("completion", "0")))
        except (ValueError, TypeError):
            prompt = 0.0
            completion = 0.0
        context_length: int | None = int(ctx) if isinstance(ctx, (int, float)) else None
        return CatalogModel(
            id=model_id,
            name=str(item.get("name", model_id)),
            context_length=context_length,
            prompt_usd_per_token=prompt,
            completion_usd_per_token=completion,
            modality=modality,
            provider="openrouter",
        )

    async def _fetch_with_retry(self, url: str) -> list[dict[str, object]]:
        """GET url with up to _MAX_RETRIES retries and exponential jitter."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0:
                jitter = random.uniform(0, _RETRY_BASE_SECONDS)  # noqa: S311
                await asyncio.sleep(_RETRY_BASE_SECONDS * (2 ** (attempt - 1)) + jitter)
            try:
                response = await self._client.get(url, timeout=_TIMEOUT)
                response.raise_for_status()
                payload: dict[str, object] = response.json()
                data = payload.get("data", [])
                if not isinstance(data, list):
                    raise CatalogSourceUnavailableError("Unexpected OpenRouter response shape")
                result: list[dict[str, object]] = [item for item in data if isinstance(item, dict)]
                return result
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_exc = exc
        raise CatalogSourceUnavailableError(
            f"OpenRouter unreachable after {_MAX_RETRIES + 1} attempts"
        ) from last_exc
