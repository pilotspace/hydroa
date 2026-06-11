"""HttpxUpstreamPinger — production httpx adapter for UpstreamPinger Protocol."""

from __future__ import annotations

import logging

import httpx

_log = logging.getLogger(__name__)

_PING_URL = "https://openrouter.ai/api/v1/models"
_TIMEOUT = 3.0  # seconds — short as per TASK.md §1 M9


class HttpxUpstreamPinger:
    """Ping the OpenRouter upstream with a HEAD request.

    Raises on timeout, connection error, or non-2xx response.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)
        self._owns_client = client is None

    async def ping(self) -> None:
        """HEAD https://openrouter.ai/api/v1/models; raise on failure."""
        try:
            response = await self._client.head(_PING_URL)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ConnectionError(f"upstream ping returned {exc.response.status_code}") from exc

    async def aclose(self) -> None:
        """Close the underlying httpx client if owned by this instance."""
        if self._owns_client:
            await self._client.aclose()
