"""OIDC domain ports (protocols)."""

from __future__ import annotations

from typing import Any, Protocol


class OidcTokenExchanger(Protocol):
    """Port: POST an authorization code to the IdP token endpoint.

    The implementation is responsible for:
    - HTTP POST with Content-Type: application/x-www-form-urlencoded
    - 10-second timeout (design for failure)
    - Raising OidcUpstreamError on network/timeout/non-200 errors

    Tests inject FakeOidcExchanger via app.state.oidc_exchanger.
    """

    async def exchange(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """POST code to token endpoint; return parsed response body (includes id_token).

        Raises OidcUpstreamError on httpx.RequestError / httpx.TimeoutException
        or when status != 200.
        """
        ...
