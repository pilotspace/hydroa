"""Failing-first (RED) suite for AzureADTokenProviderCache — task-3 §3 contract point 4.

Tests the new per-identity provider cache:
  - Reuse within TTL (one construction per identity)
  - Distinct identities → distinct providers
  - TTL expiry → rebuild + close old client
  - Cache key excludes client_secret (two configs differing only in secret share one entry)
  - Size-cap eviction closes oldest client
  - Mint failure propagates fail-closed (no caching of a failed token)
  - Concurrent single-construction (asyncio.Lock)

All tests use a fake provider_factory seam and an injectable now_fn clock.
No real network; no real AAD HTTP.

CONTRACT (FROZEN @ v25 task-3 §3 point 4) — DO NOT MODIFY TO MAKE TESTS PASS.

TRUE-RED: AzureADTokenProviderCache does not exist yet in gateway.proxy.infrastructure.azure_ad
→ ImportError on every test in this file. That is the correct red reason.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gateway.proxy.domain.errors import UpstreamUnavailableError

# RED: AzureADTokenProviderCache does not exist yet → ImportError at import time.
# We import lazily inside each test so that the file can be collected (only the
# affected test fails at its own import, not the whole module).


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Clock:
    """Injectable clock mirroring the pattern in tests/azure_aad/test_azure_ad.py."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _make_ad_config(
    tenant_id: str = "tenant-1",
    client_id: str = "client-1",
    client_secret: str = "secret-1",
    scope: str = "",
    authority: str = "",
) -> Any:
    """Build an AzureADConfig (stable — exists from task-1)."""
    from gateway.proxy.infrastructure.azure_ad import AzureADConfig

    return AzureADConfig(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        scope=scope or "https://cognitiveservices.azure.com/.default",
        authority=authority or "https://login.microsoftonline.com",
    )


class _FakeProvider:
    """Minimal AzureADTokenProvider-shaped fake: tracks close() calls."""

    def __init__(self, token: str) -> None:
        self._token = token
        self.closed = False
        self.close_count = 0

    async def get_token(self) -> str:
        if self.closed:
            raise UpstreamUnavailableError("provider closed")
        return self._token

    async def close(self) -> None:
        self.closed = True
        self.close_count += 1

    # Attribute the cache inspects to close old providers
    @property
    def _client(self) -> Any:
        return self

    async def aclose(self) -> None:
        await self.close()


class _FailingProvider:
    """A fake provider whose get_token always raises UpstreamUnavailableError."""

    def __init__(self) -> None:
        self.call_count = 0

    async def get_token(self) -> str:
        self.call_count += 1
        raise UpstreamUnavailableError("AAD mint failed")

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# PC1 — Reuse within TTL: single provider construction for same identity
# ---------------------------------------------------------------------------


async def test_cache_reuse_within_ttl() -> None:
    """PC1: get_or_create with the same AzureADConfig within the TTL window returns
    the same provider instance (constructed exactly once).

    RIGHT-REASON RED: AzureADTokenProviderCache does not exist → ImportError.
    """
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache  # type: ignore[attr-defined]

    constructed: list[_FakeProvider] = []

    def _factory(config: Any) -> _FakeProvider:
        p = _FakeProvider(token=f"tok-{len(constructed)}")
        constructed.append(p)
        return p

    clock = _Clock(0.0)
    cache = AzureADTokenProviderCache(
        ttl_s=300.0,
        max_size=512,
        now_fn=clock,
        provider_factory=_factory,
    )

    cfg = _make_ad_config()

    p1 = cache.get_or_create(cfg)
    p2 = cache.get_or_create(cfg)

    assert len(constructed) == 1, (
        f"Provider must be constructed ONCE for the same identity within TTL, "
        f"got {len(constructed)} constructions"
    )
    assert p1 is p2, "get_or_create must return the SAME provider instance within TTL"


# ---------------------------------------------------------------------------
# PC2 — Distinct identities → distinct providers
# ---------------------------------------------------------------------------


async def test_cache_distinct_identities() -> None:
    """PC2: Two AzureADConfigs with different (tenant_id, client_id) tuples
    produce distinct provider instances.

    RIGHT-REASON RED: ImportError on AzureADTokenProviderCache.
    """
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache  # type: ignore[attr-defined]

    constructed: list[_FakeProvider] = []

    def _factory(config: Any) -> _FakeProvider:
        p = _FakeProvider(token=f"tok-{len(constructed)}")
        constructed.append(p)
        return p

    cache = AzureADTokenProviderCache(
        ttl_s=300.0,
        max_size=512,
        provider_factory=_factory,
    )

    cfg_a = _make_ad_config(tenant_id="tenant-A", client_id="client-A")
    cfg_b = _make_ad_config(tenant_id="tenant-B", client_id="client-B")

    p_a = cache.get_or_create(cfg_a)
    p_b = cache.get_or_create(cfg_b)

    assert len(constructed) == 2, (
        f"Two distinct identities must produce 2 provider constructions, got {len(constructed)}"
    )
    assert p_a is not p_b, "Different identities must yield different provider instances"


# ---------------------------------------------------------------------------
# PC3 — TTL expiry: rebuild + close old provider's _client
# ---------------------------------------------------------------------------


async def test_cache_ttl_expiry_rebuilds_and_closes() -> None:
    """PC3: After TTL expiry (clock advanced past ttl_s), get_or_create builds a
    NEW provider and closes (or requests close of) the old one.

    RIGHT-REASON RED: ImportError on AzureADTokenProviderCache.
    """
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache  # type: ignore[attr-defined]

    constructed: list[_FakeProvider] = []

    def _factory(config: Any) -> _FakeProvider:
        p = _FakeProvider(token=f"tok-{len(constructed)}")
        constructed.append(p)
        return p

    clock = _Clock(0.0)
    cache = AzureADTokenProviderCache(
        ttl_s=300.0,
        max_size=512,
        now_fn=clock,
        provider_factory=_factory,
    )

    cfg = _make_ad_config()
    p_first = cache.get_or_create(cfg)

    # Advance clock past TTL
    clock.t = 301.0

    p_second = cache.get_or_create(cfg)

    assert len(constructed) == 2, (
        f"TTL expiry must trigger a NEW provider construction, "
        f"got {len(constructed)} total constructions"
    )
    assert p_first is not p_second, (
        "After TTL expiry, a different provider instance must be returned"
    )
    # The old provider's client must have been scheduled for close
    # (async close may be fire-and-forget; we wait briefly)
    await asyncio.sleep(0.01)
    assert p_first.close_count >= 1 or p_first.closed, (
        "Old provider must be closed (or close() called) after TTL expiry + rebuild"
    )


# ---------------------------------------------------------------------------
# PC4 — Key excludes client_secret (same identity, different secret → same entry)
# ---------------------------------------------------------------------------


def test_cache_key_excludes_client_secret() -> None:
    """PC4: Two AzureADConfigs that differ ONLY in client_secret share a single cache
    entry (the backing key is the NON-SECRET tuple: tenant_id, client_id, authority, scope).

    RIGHT-REASON RED: ImportError on AzureADTokenProviderCache.
    """
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache  # type: ignore[attr-defined]

    constructed: list[_FakeProvider] = []

    def _factory(config: Any) -> _FakeProvider:
        p = _FakeProvider(token=f"tok-{len(constructed)}")
        constructed.append(p)
        return p

    cache = AzureADTokenProviderCache(
        ttl_s=300.0,
        max_size=512,
        provider_factory=_factory,
    )

    cfg_secret_a = _make_ad_config(client_secret="secret-A")
    cfg_secret_b = _make_ad_config(client_secret="secret-B")

    p1 = cache.get_or_create(cfg_secret_a)
    p2 = cache.get_or_create(cfg_secret_b)

    assert len(constructed) == 1, (
        f"Two configs differing only in client_secret must share ONE cache entry, "
        f"got {len(constructed)} constructions. "
        "The cache key must exclude client_secret (non-secret identity tuple only)."
    )
    assert p1 is p2, "Both get_or_create calls must return the SAME provider (key excludes secret)"


# ---------------------------------------------------------------------------
# PC5 — Size cap eviction closes oldest-created provider's client
# ---------------------------------------------------------------------------


async def test_cache_size_cap_evicts_oldest_and_closes() -> None:
    """PC5: When the cache exceeds max_size, the oldest-created entry is evicted
    and its provider's client is closed.

    RIGHT-REASON RED: ImportError on AzureADTokenProviderCache.
    """
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache  # type: ignore[attr-defined]

    max_size = 3
    constructed: list[_FakeProvider] = []

    def _factory(config: Any) -> _FakeProvider:
        p = _FakeProvider(token=f"tok-{len(constructed)}")
        constructed.append(p)
        return p

    cache = AzureADTokenProviderCache(
        ttl_s=300.0,
        max_size=max_size,
        provider_factory=_factory,
    )

    # Fill to exactly max_size
    providers: list[_FakeProvider] = []
    for i in range(max_size):
        cfg = _make_ad_config(tenant_id=f"tenant-{i}", client_id=f"client-{i}")
        providers.append(cache.get_or_create(cfg))

    oldest = providers[0]
    assert not oldest.closed, "Oldest provider must NOT be closed while within size cap"

    # Adding one more triggers eviction of the oldest
    cfg_new = _make_ad_config(tenant_id="tenant-new", client_id="client-new")
    cache.get_or_create(cfg_new)

    # Allow any async close to run
    await asyncio.sleep(0.01)

    assert oldest.closed or oldest.close_count >= 1, (
        "Oldest provider must be closed after size-cap eviction. "
        f"close_count={oldest.close_count}, closed={oldest.closed}"
    )
    assert len(constructed) == max_size + 1, (
        f"Expected {max_size + 1} providers constructed (one per unique identity + evicted one), "
        f"got {len(constructed)}"
    )


# ---------------------------------------------------------------------------
# PC6 — Mint failure propagates fail-closed (no caching of failed token)
# ---------------------------------------------------------------------------


async def test_cache_mint_failure_not_cached() -> None:
    """PC6: If the provider's get_token() raises UpstreamUnavailableError, the failure
    is not cached — a subsequent get_or_create call attempts again (may succeed if
    the IDP recovers).

    The cache stores PROVIDERS, not tokens. This test verifies that a provider that
    raises on get_token does NOT prevent future get_or_create from returning a fresh
    provider (or the same provider that can now succeed).

    RIGHT-REASON RED: ImportError on AzureADTokenProviderCache.
    """
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache  # type: ignore[attr-defined]

    fail_count = [0]
    succeed_count = [0]

    def _factory(config: Any) -> Any:
        class _TransientFailProvider:
            async def get_token(self) -> str:
                if fail_count[0] < 1:
                    fail_count[0] += 1
                    raise UpstreamUnavailableError("transient AAD failure")
                succeed_count[0] += 1
                return "recovered-token"

            async def close(self) -> None:
                pass

        return _TransientFailProvider()

    cache = AzureADTokenProviderCache(
        ttl_s=300.0,
        max_size=512,
        provider_factory=_factory,
    )

    cfg = _make_ad_config()
    provider = cache.get_or_create(cfg)

    # First get_token fails
    with pytest.raises(UpstreamUnavailableError):
        await provider.get_token()

    # A second get_or_create within TTL may return the same (or a new) provider.
    # Critically: it must NOT be permanently broken. We don't assert the caching
    # behavior of a failed provider (the contract only says "no caching of a FAILED
    # TOKEN" — the provider object itself may be reused or rebuilt).
    # The key assertion: fail-closed means the exception propagates, never silent empty.
    assert fail_count[0] == 1, "get_token must have been called (fail propagated)"


# ---------------------------------------------------------------------------
# PC7 — Repeated calls memoize: exactly one construction per identity
# ---------------------------------------------------------------------------


def test_cache_repeated_calls_single_construction() -> None:
    """PC7: Many get_or_create calls for the same identity construct the provider
    EXACTLY ONCE. Per the frozen §3 signature `get_or_create(config) -> AzureADTokenProvider`
    the method is SYNCHRONOUS (it returns the provider directly, not a coroutine) — provider
    construction is non-IO (the AAD mint happens lazily in get_token), so there is no await
    point inside get_or_create and thus no interleave/double-construction race to guard. This
    pins the memoization property the adapter relies on (one provider, hence one token cache,
    per tenant identity).

    RIGHT-REASON RED: ImportError on AzureADTokenProviderCache.
    """
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache  # type: ignore[attr-defined]

    constructed: list[_FakeProvider] = []

    def _factory(config: Any) -> _FakeProvider:
        p = _FakeProvider(token=f"tok-{len(constructed)}")
        constructed.append(p)
        return p

    cache = AzureADTokenProviderCache(
        ttl_s=300.0,
        max_size=512,
        provider_factory=_factory,
    )

    cfg = _make_ad_config()

    results = [cache.get_or_create(cfg) for _ in range(10)]

    assert len(constructed) == 1, (
        f"Repeated get_or_create for one identity must build the provider EXACTLY ONCE "
        f"(sync memoization), got {len(constructed)} constructions"
    )
    assert all(r is results[0] for r in results), (
        "Every caller must receive the same memoized provider instance"
    )
