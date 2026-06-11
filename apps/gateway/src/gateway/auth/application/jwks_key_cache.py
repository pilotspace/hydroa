"""Application-layer JWKS key cache.

Sits ABOVE the JwksClient port — the port is stateless per call; this cache
provides TTL-based in-process caching and the kid-miss refresh (one retry on
unknown kid, to handle IdP key rotation).

One instance per app process: created in create_app when oidc_enabled, stored
at app.state.jwks_key_cache.

Safety rules (§5):
  - TTL uses time.monotonic() — never wall clock.
  - Kid-miss refresh fires at most ONCE per resolve() call (no loop).
  - OidcUpstreamError propagates immediately (adapter already retried transport).
  - OidcTokenInvalidError after the retry propagates — fail-CLOSED.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gateway.auth.domain.ports import JwksClient

_CACHE_TTL_SECONDS: float = 300.0  # hard-coded v5; v6 candidate for Settings field


class JwksKeyCache:
    """In-process cache: kid (str | None) → (key object, fetched_at monotonic timestamp).

    resolve(kid, jwks_client):
      1. Cache hit (entry present, age < TTL): return cached key. No port call.
      2. Miss / expired: call jwks_client.get_signing_key(kid).
         - OidcTokenInvalidError (kid not found): retry the port EXACTLY ONCE
           (kid-miss refresh — covers IdP key rotation), then propagate.
         - OidcUpstreamError: propagate immediately.
      3. Cache and return the resolved key.
    """

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str | None, tuple[Any, float]] = {}

    async def resolve(self, kid: str | None, jwks_client: JwksClient) -> Any:
        """Return the signing key for *kid*, using the cache when fresh."""
        from gateway.auth.domain.errors import OidcTokenInvalidError

        now = time.monotonic()
        entry = self._cache.get(kid)
        if entry is not None:
            key, fetched_at = entry
            if (now - fetched_at) < self._ttl:
                return key

        # Cache miss or expired — fetch from port.
        try:
            key = await jwks_client.get_signing_key(kid)
        except OidcTokenInvalidError:
            # Kid-miss refresh: retry EXACTLY ONCE.
            key = await jwks_client.get_signing_key(kid)  # propagates on second miss

        # Cache the resolved key with current monotonic timestamp.
        self._cache[kid] = (key, time.monotonic())
        return key
