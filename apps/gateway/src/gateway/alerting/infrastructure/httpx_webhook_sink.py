"""HttpxWebhookSink — production httpx adapter for WebhookSink Protocol."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

_log = logging.getLogger(__name__)

_TIMEOUT = 10.0  # seconds


def _host_only(url: str) -> str:
    """Return only the host portion of a URL for safe logging."""
    try:
        return urlparse(url).hostname or url
    except Exception:
        return "<url>"


class HttpxWebhookSink:
    """POST JSON payloads to a webhook URL using httpx.

    Raises httpx.HTTPError (or subclasses) on connection errors.
    Returns HTTP status code.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)
        self._owns_client = client is None

    async def post_json(self, url: str, payload: dict[str, object]) -> int:
        """POST payload as JSON; return HTTP status code.

        Raises on connection error (httpx.HTTPError or subclasses).
        """
        host = _host_only(url)
        _log.debug("webhook_sink: POSTing to %s", host)
        response = await self._client.post(url, json=payload)
        return response.status_code

    async def aclose(self) -> None:
        """Close the underlying httpx client if owned by this instance."""
        if self._owns_client:
            await self._client.aclose()
