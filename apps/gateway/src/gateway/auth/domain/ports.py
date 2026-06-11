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


class JwksClient(Protocol):
    """Port: fetch the IdP JWKS and resolve the signing key for a given kid.

    STATELESS — no cache, no internal refresh; one logical fetch per call.
    Caching and kid-miss refresh live above this port in the application layer
    (JwksKeyCache).

    Tests inject FakeJwksClient via app.state.jwks_client.
    """

    async def get_signing_key(self, kid: str | None) -> Any:
        """Fetch the JWKS NOW and return the key for the given kid.

        kid=None: use the single key if the fetched set has exactly one;
        raise OidcTokenInvalidError if 0 or multiple keys (ambiguous).

        Raises:
            OidcUpstreamError: if JWKS cannot be fetched/parsed (after transport retry).
            OidcTokenInvalidError: if kid is not in the fetched set.
        """
        ...
