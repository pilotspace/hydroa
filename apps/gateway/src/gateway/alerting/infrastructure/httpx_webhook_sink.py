"""HttpxWebhookSink — production httpx adapter for WebhookSink Protocol.

SSRF / egress hardening (audit-remediation H3): the webhook target is an
operator-configured URL (`Settings.alert_webhook_url`), but it is still server-side
outbound HTTP dialed on a background loop with no human in the request path — the same
class of risk `core.egress_policy` (edge-input-hardening TASK.md §3 Part B, FROZEN @ v1)
already guards for BYOK-influenced adapters and the MCP dialer
(`mcp_connector/infrastructure/httpx_dialer.py`). We REUSE that policy verbatim (not
forked) rather than writing a second allow-list: `EgressPolicy.check(url)` is called
FRESH before every single dial (never cached), and fails CLOSED — metadata addresses,
loopback/link-local/private ranges are refused before the socket is ever touched. The
real `DenyPrivateAndMetadataEgressPolicy` is the default; only an explicit override (test
seam) relaxes it, mirroring every other BYOK-influenced adapter's wiring convention.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from gateway.core.egress_policy import DenyPrivateAndMetadataEgressPolicy, EgressPolicy

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
    Raises `gateway.core.egress_policy.EgressDeniedError` when the target is an
    internal/link-local/metadata address — fail CLOSED, checked fresh before every dial.
    Returns HTTP status code.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)
        self._owns_client = client is None
        # Deny-by-default production wiring — only an EXPLICIT override (tests) relaxes it.
        self._egress_policy: EgressPolicy = (
            egress_policy if egress_policy is not None else DenyPrivateAndMetadataEgressPolicy()
        )

    async def post_json(self, url: str, payload: dict[str, object]) -> int:
        """POST payload as JSON; return HTTP status code.

        Raises `EgressDeniedError` (fail CLOSED) if the target is a denied egress target —
        checked BEFORE any network I/O. Raises on connection error (httpx.HTTPError or
        subclasses).
        """
        host = _host_only(url)
        await self._egress_policy.check(url)
        _log.debug("webhook_sink: POSTing to %s", host)
        response = await self._client.post(url, json=payload)
        return response.status_code

    async def aclose(self) -> None:
        """Close the underlying httpx client if owned by this instance."""
        if self._owns_client:
            await self._client.aclose()
