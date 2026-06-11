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

SECURITY FIX (oidc-tenant-config §3 CROSS-CONTRACT SUPERSESSION):
  Cache key is now (jwks_url, kid) tuple — prevents cross-tenant kid collisions.
  In a multi-tenant deployment, tenants A and B may both issue tokens with
  kid="key-1" from DIFFERENT JWKS endpoints. Bare-kid keying would serve
  tenant A's key for tenant B's token (security bug). The (jwks_url, kid)
  tuple namespaces the cache per JWKS endpoint, making cross-tenant collisions
  impossible regardless of kid values.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gateway.auth.domain.ports import JwksClient

_CACHE_TTL_SECONDS: float = 300.0  # hard-coded v5; v6 candidate for Settings field


class JwksKeyCache:
    """In-process cache: (jwks_url, kid) → (key object, fetched_at monotonic timestamp).

    resolve(jwks_url, kid, jwks_client):
      1. Cache hit (entry present, age < TTL): return cached key. No port call.
      2. Miss / expired: call jwks_client.get_signing_key(kid).
         - OidcTokenInvalidError (kid not found): retry the port EXACTLY ONCE
           (kid-miss refresh — covers IdP key rotation), then propagate.
         - OidcUpstreamError: propagate immediately.
      3. Cache and return the resolved key.

    Cache key: (jwks_url, kid) tuple — namespaces by JWKS endpoint.
    This prevents cross-tenant kid collisions (T11 security invariant).
    """

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        # Cache key: (jwks_url, kid) → (key, fetched_at)
        self._cache: dict[tuple[str, str | None], tuple[Any, float]] = {}

    async def resolve(self, jwks_url: str, kid: str | None, jwks_client: JwksClient) -> Any:
        """Return the signing key for *(jwks_url, kid)*, using the cache when fresh.

        jwks_url: the JWKS endpoint URL — namespaces the cache per tenant IdP.
        kid: the key ID from the JWT header (may be None for single-key JWKS sets).
        jwks_client: the JwksClient port instance to fetch from on cache miss.
        """
        from gateway.auth.domain.errors import OidcTokenInvalidError

        now = time.monotonic()
        cache_key = (jwks_url, kid)
        entry = self._cache.get(cache_key)
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
        self._cache[cache_key] = (key, time.monotonic())
        return key
