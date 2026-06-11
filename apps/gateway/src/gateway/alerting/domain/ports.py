"""Port interfaces for the alerting bounded context.

CONTRACT (FROZEN @ health-alerting v3):
  WebhookSink.post_json  — POST JSON payload to a URL; returns HTTP status code.
  UpstreamPinger.ping    — Ping the upstream health endpoint; raises on failure.
"""

from __future__ import annotations

from typing import Protocol


class WebhookSink(Protocol):
    """Deliver an event payload to a webhook URL.

    Raises on connection error (caller treats raised exception as failure).
    Returns the HTTP status code (2xx = success; non-2xx = failure).
    """

    async def post_json(self, url: str, payload: dict[str, object]) -> int:
        """POST payload as JSON to url; return HTTP status code.

        Raises on connection error (caller treats as failure).
        """
        ...


class UpstreamPinger(Protocol):
    """Ping the upstream health endpoint.

    Raises on any failure (timeout, connection error, non-2xx).
    """

    async def ping(self) -> None:
        """Ping the upstream health endpoint.

        Raises on any failure (timeout, connection error, non-2xx).
        """
        ...
