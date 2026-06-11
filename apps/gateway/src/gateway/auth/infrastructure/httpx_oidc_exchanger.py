"""Production OidcTokenExchanger — httpx POST to IdP token endpoint.

SECURITY INVIOLABLES (§3):
  - TLS certificate verification is NEVER disabled (verify=False is a HARD-STOP)
  - Token endpoint URL comes exclusively from Settings or the server-side-resolved
    per-tenant OidcProviderConfig (never from request input)
  - 10-second explicit timeout (design for failure)
  - client_secret never logged
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from gateway.auth.domain.errors import OidcUpstreamError

if TYPE_CHECKING:
    from gateway.auth.domain.entities import OidcProviderConfig


class HttpxOidcExchanger:
    """Implements OidcTokenExchanger using httpx.

    When *oidc_config* (the cookie-resolved per-tenant OidcProviderConfig) is
    supplied, the token endpoint and client credentials bind to that tenant's
    IdP — exchanging the code at the env IdP with env credentials would hand
    the tenant a foreign-issuer token that can never verify against the
    tenant's JWKS (live-verify defect C5f, 2026-06-12).
    """

    def __init__(self, settings: Any, oidc_config: OidcProviderConfig | None = None) -> None:
        # Store only the fields needed; never store client_secret in logs or repr
        if oidc_config is not None:
            self._token_endpoint = oidc_config.token_url or f"{oidc_config.issuer}/token"
            self._client_id = oidc_config.client_id
            # Store secret for use in exchange; intentionally NOT in __repr__
            self._client_secret = oidc_config.client_secret
        else:
            self._token_endpoint = f"{settings.oidc_issuer}/token"
            self._client_id = settings.oidc_client_id
            self._client_secret = settings.oidc_client_secret
        self._redirect_uri = settings.oidc_redirect_uri

    async def exchange(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """POST authorization code to IdP token endpoint.

        SECURITY: TLS verification is ALWAYS enabled (verify=True, the httpx default).
        Setting verify=False anywhere in this method is a security HARD-STOP.

        Raises:
            OidcUpstreamError: on network error, timeout, or non-200 response
        """
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                # SECURITY: verify=True is the httpx default — never override with False
                response = await client.post(
                    self._token_endpoint,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        # client_secret is never logged — it exists only in this POST body
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.TimeoutException as exc:
            raise OidcUpstreamError(f"IdP token endpoint timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise OidcUpstreamError(f"IdP token endpoint request failed: {exc}") from exc

        if response.status_code != 200:
            raise OidcUpstreamError(f"IdP token endpoint returned HTTP {response.status_code}")

        try:
            return dict(response.json())
        except Exception as exc:
            raise OidcUpstreamError(f"IdP token endpoint returned non-JSON body: {exc}") from exc
