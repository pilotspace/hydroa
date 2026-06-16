"""Infrastructure implementation: CachedTenantCredentialResolver.

§3 CONTRACT (credential-resolution-seam TASK.md) — FROZEN @ v1.

DESIGN:
- Wraps ``TenantProviderKeyStore.get`` with an in-memory per-process TTL cache
  keyed by ``(tenant_id, provider)``.
- POSITIVE-only caching: a hit is cached for ``cache_ttl_s`` seconds; a miss
  (None from store) is NOT cached so a freshly-configured key takes effect on
  the very next request.
- Bounded ``asyncio.timeout`` on the cold DB fetch: if the store takes longer
  than ``resolve_timeout_s`` the call is cancelled and ``ProviderKeyMissing``
  is raised (fail-CLOSED — a timeout is an availability event, never an
  auth bypass).
- Store decrypt errors already raise ``from None`` (v22 floor) — they propagate
  as fail-CLOSED without being caught here.

SECURITY INVARIANTS:
- ``ProviderKeyMissing`` carries the provider name ONLY — never a secret,
  ciphertext, or tenant key.
- Cached values are domain ``ProviderCredential`` objects whose secret fields
  are ``pydantic.SecretStr`` (masked in repr/str/logs).
- The resolver itself never logs credentials or resolver cache internals.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

from gateway.proxy.domain.provider_credentials import ProviderCredential, ProviderKeyMissing

if TYPE_CHECKING:
    pass


class CachedTenantCredentialResolver:
    """TTL-cached resolver wrapping a ``TenantProviderKeyStore``.

    Constructor matches what the frozen test conftest uses::

        CachedTenantCredentialResolver(
            store=counting_store,
            cache_ttl_s=60.0,
            resolve_timeout_s=2.0,
        )

    Also accepts ``Settings``-based construction used in ``main.py``::

        CachedTenantCredentialResolver(
            store=db_store,
            settings=settings,  # reads cache_ttl_s and resolve_timeout_s from Settings
        )

    The ``settings`` kwarg is IGNORED when ``cache_ttl_s`` / ``resolve_timeout_s``
    are provided explicitly.
    """

    def __init__(
        self,
        *,
        store: Any,
        cache_ttl_s: float | None = None,
        resolve_timeout_s: float | None = None,
        settings: Any = None,
    ) -> None:
        self._store = store

        # Resolve TTL / timeout from explicit kwargs first, then settings, then defaults.
        if cache_ttl_s is not None:
            self._cache_ttl_s = float(cache_ttl_s)
        elif settings is not None:
            self._cache_ttl_s = float(
                getattr(settings, "provider_credential_cache_ttl_s", 60.0)
            )
        else:
            self._cache_ttl_s = 60.0

        if resolve_timeout_s is not None:
            self._resolve_timeout_s = float(resolve_timeout_s)
        elif settings is not None:
            self._resolve_timeout_s = float(
                getattr(settings, "provider_credential_resolve_timeout_s", 2.0)
            )
        else:
            self._resolve_timeout_s = 2.0

        # In-memory positive-result cache: key → (credential, expiry_monotonic)
        self._cache: dict[tuple[uuid.UUID, str], tuple[ProviderCredential, float]] = {}

    async def resolve(self, tenant_id: uuid.UUID, provider: str) -> ProviderCredential:
        """Return the enabled credential for ``(tenant_id, provider)``.

        Warm path (cache hit within TTL): returns the cached credential with
        zero DB calls.

        Cold path: calls ``store.get(tenant_id, provider)`` under a bounded
        ``asyncio.timeout``. A ``None`` result or a timeout raises
        ``ProviderKeyMissing`` (fail-CLOSED). A decrypt error from the store
        propagates unchanged (already ``from None`` — v22 floor).

        Raises:
            ProviderKeyMissing: absent/disabled credential, DB timeout, or
                store returning None.
        """
        key = (tenant_id, provider)
        now = time.monotonic()

        # ── Warm-path: check in-memory cache ──────────────────────────────
        cached = self._cache.get(key)
        if cached is not None:
            credential, expiry = cached
            if now < expiry:
                return credential
            # Stale entry — remove and fall through to cold path
            del self._cache[key]

        # ── Cold-path: fetch from store with bounded timeout ──────────────
        try:
            async with asyncio.timeout(self._resolve_timeout_s):
                result = await self._store.get(tenant_id, provider)
        except TimeoutError:
            raise ProviderKeyMissing(provider) from None

        if result is None:
            # Absent or disabled row — miss is NOT cached (new key takes effect immediately)
            raise ProviderKeyMissing(provider)

        # Positive hit — cache and return
        self._cache[key] = (result, now + self._cache_ttl_s)
        return result


__all__ = ["CachedTenantCredentialResolver"]
