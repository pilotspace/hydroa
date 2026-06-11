"""Production JwksClient adapter — httpx GET to IdP JWKS endpoint.

SECURITY INVIOLABLES (§3 / §5):
  - TLS certificate verification is NEVER disabled (verify=False is a HARD-STOP).
  - JWKS endpoint URL comes exclusively from Settings (never from request input).
  - 10-second explicit timeout (design for failure).
  - STATELESS per call: no internal cache (caching lives in JwksKeyCache above this layer).
  - Tenacity bounded-jitter retry: up to 3 attempts on httpx.RequestError /
    httpx.TimeoutException; non-200 → OidcUpstreamError immediately (no retry).
"""

from __future__ import annotations

from typing import Any

import httpx
import jwt
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random

from gateway.auth.domain.errors import OidcTokenInvalidError, OidcUpstreamError


class HttpxJwksClient:
    """Implements the JwksClient port using httpx.

    STATELESS — one logical JWKS fetch per get_signing_key() call.
    The optional *transport* parameter is the J11 test seam (ASGITransport).
    It is NEVER used to weaken TLS; the seam exists solely for in-process testing.
    """

    def __init__(
        self,
        jwks_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._jwks_url = jwks_url
        self._transport = transport

    async def get_signing_key(self, kid: str | None) -> Any:
        """Fetch the JWKS and return the key object for *kid*.

        Raises:
            OidcUpstreamError: JWKS endpoint unreachable / non-200 / malformed JSON.
            OidcTokenInvalidError: kid not found in the fetched set (or kid=None with
                0 or multiple keys).
        """
        jwks_data = await self._fetch_jwks()
        return self._resolve_key(jwks_data, kid)

    @retry(
        retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_random(min=0.05, max=0.5),
        reraise=True,
    )
    async def _fetch_jwks_with_retry(self) -> dict[str, Any]:
        """GET the JWKS URL with retry on transient transport errors."""
        kwargs: dict[str, Any] = {"timeout": httpx.Timeout(10.0)}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        # SECURITY: TLS verification is the httpx default (True). Never pass verify=False.
        async with httpx.AsyncClient(**kwargs) as client:
            response = await client.get(self._jwks_url)
        if response.status_code != 200:
            raise OidcUpstreamError(
                f"JWKS endpoint returned HTTP {response.status_code} for {self._jwks_url}"
            )
        return dict(response.json())

    async def _fetch_jwks(self) -> dict[str, Any]:
        """Fetch JWKS JSON, wrapping all transport / parse errors as OidcUpstreamError."""
        try:
            return await self._fetch_jwks_with_retry()
        except OidcUpstreamError:
            raise
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            raise OidcUpstreamError(f"JWKS endpoint unreachable after retries: {exc}") from exc
        except Exception as exc:
            raise OidcUpstreamError(f"JWKS endpoint returned malformed response: {exc}") from exc

    @staticmethod
    def _resolve_key(jwks_data: dict[str, Any], kid: str | None) -> Any:
        """Parse the JWKS JSON and return the key object for *kid*.

        Uses pyjwt's PyJWKSet to parse (pyjwt 2.13 API — NOT PyJWKS).
        Returns a key object that jwt.decode() accepts.

        Raises:
            OidcUpstreamError: if JWKS JSON is malformed / unparseable.
            OidcTokenInvalidError: if kid is not in the set, or kid=None with
                0 or multiple keys.
        """
        try:
            keys_list: list[Any] = jwks_data.get("keys", [])
            jwk_set = jwt.PyJWKSet.from_dict({"keys": keys_list})
        except Exception as exc:
            raise OidcUpstreamError(f"Failed to parse JWKS: {exc}") from exc

        keys = jwk_set.keys

        if kid is None:
            if len(keys) == 1:
                return keys[0].key
            raise OidcTokenInvalidError(
                f"kid=None but JWKS has {len(keys)} keys (expected exactly 1)"
            )

        # Look up by kid
        for jwk in keys:
            key_id: str | None = getattr(jwk, "key_id", None)
            if key_id == kid:
                return jwk.key

        raise OidcTokenInvalidError(f"kid {kid!r} not found in JWKS key set")
