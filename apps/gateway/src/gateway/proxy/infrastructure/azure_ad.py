"""Azure AD client-credentials token auth + per-tenant provider cache.

Provides:
  - AzureADTokenProvider: Acquire + cache + refresh an OAuth2 client-credentials
    bearer token from Azure AD. Fail-closed on any IDP error.
  - AzureADTokenProviderCache: Per-tenant TTL+size-capped cache of AzureADTokenProvider
    instances, keyed by the NON-SECRET AzureADConfig identity (tenant_id, client_id,
    authority, scope). Task-3 dynamic-auth-byok §3 contract point 4.

Design-for-failure (CLAUDE.md):
  - timeouts on the token POST;
  - FAIL-CLOSED — a non-200 / timeout / network error raises UpstreamUnavailableError;
    we NEVER serve an expired token, fall back to api-key, or emit a blank Bearer;
  - single-flight refresh — an asyncio.Lock + double-check means a token-expiry stampede
    makes exactly ONE token request;
  - refresh-before-expiry skew so a token is renewed slightly early.

Security: client_secret (config) and the acquired token are SECRETS — client_secret is
field(repr=False); neither ever appears in a log, metric label, span attribute, URL,
exception message, or cache key (the cache is keyed by the NON-SECRET identity only).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from gateway.core.egress_policy import (
    DenyPrivateAndMetadataEgressPolicy,
    EgressDeniedError,
    EgressPolicy,
)
from gateway.core.error_catalog import UPSTREAM_EGRESS_DENIED
from gateway.proxy.domain.errors import UpstreamUnavailableError

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

_CONNECT_TIMEOUT = 10.0
_TOKEN_TIMEOUT = 30.0

DEFAULT_SCOPE = "https://cognitiveservices.azure.com/.default"
DEFAULT_AUTHORITY = "https://login.microsoftonline.com"


@dataclass(frozen=True)
class AzureADConfig:
    """Immutable Azure AD client-credentials config.

    ``client_secret`` is excluded from repr/str so it never leaks into logs or output.
    """

    tenant_id: str
    client_id: str
    client_secret: str = field(repr=False)
    scope: str = DEFAULT_SCOPE
    authority: str = DEFAULT_AUTHORITY


class AzureADTokenProvider:
    """Acquire + cache + refresh an Azure AD client-credentials bearer token.

    A single instance is shared per AAD identity within the app lifetime (via
    AzureADTokenProviderCache). ``get_token()`` is safe under concurrency: a single-flight
    asyncio.Lock + a post-lock cache re-check means a stampede of expired-token requests
    makes exactly one IDP call.
    """

    def __init__(
        self,
        *,
        config: AzureADConfig,
        now_fn: Callable[[], float] = time.monotonic,
        expiry_skew_s: float = 60.0,
        metrics_registry: MetricsRegistry | None = None,
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        self._config = config
        self._now_fn = now_fn
        self._skew = expiry_skew_s
        self._metrics_registry = metrics_registry
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_TOKEN_TIMEOUT,
                write=_TOKEN_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            ),
            follow_redirects=False,
        )
        # S3 SSRF/credential-exfiltration deny (edge-input-hardening §3 Part B) — the
        # client_secret is POSTed to `authority`; DEFAULTS TO THE REAL POLICY (fail-closed
        # by default); tests must EXPLICITLY opt out via AllowAllEgressPolicy.
        self._egress_policy: EgressPolicy = (
            egress_policy if egress_policy is not None else DenyPrivateAndMetadataEgressPolicy()
        )

    def _token_url(self) -> str:
        return f"{self._config.authority.rstrip('/')}/{self._config.tenant_id}/oauth2/v2.0/token"

    def _cached(self) -> str | None:
        """Return the cached token iff still valid (now < expiry - skew), else None."""
        tok = self._token
        if tok is not None and self._now_fn() < (self._expires_at - self._skew):
            return tok
        return None

    async def get_token(self) -> str:
        """Return a valid bearer token, acquiring/refreshing if needed (fail-closed)."""
        cached = self._cached()
        if cached is not None:
            return cached
        async with self._lock:
            # Double-check: another coroutine may have refreshed while we waited.
            cached = self._cached()
            if cached is not None:
                return cached
            return await self._acquire()

    async def aclose(self) -> None:
        """Close the underlying httpx client (fire-and-forget safe)."""
        await self._client.aclose()

    def set_egress_policy(self, policy: EgressPolicy) -> None:
        """Override the egress policy post-construction.

        Lets ``AzureADTokenProviderCache`` push its ONE shared, settings-derived policy
        instance into every provider it constructs via its (frozen, single-positional-arg)
        ``provider_factory`` seam without changing that seam's call signature — additive
        only; a test's fake provider without this method is simply skipped (``hasattr``
        guard at the call site).
        """
        self._egress_policy = policy

    async def _acquire(self) -> str:
        """Acquire a fresh token from the IDP and cache it. Fail-closed on any error."""
        token_url = self._token_url()
        # S3 SSRF/credential-exfiltration deny — checked FRESH on every mint, BEFORE the
        # client_secret is ever placed in the POST form body. Never cached from write time.
        try:
            await self._egress_policy.check(token_url)
        except EgressDeniedError:
            raise UPSTREAM_EGRESS_DENIED.exc() from None
        form = {
            "grant_type": "client_credentials",
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "scope": self._config.scope,
        }
        try:
            resp = await self._client.post(token_url, data=form)
        except (httpx.TimeoutException, httpx.NetworkError):
            # FAIL-CLOSED — suppress the exception chain: the httpx error carries the
            # request object whose body holds client_secret. `from None` keeps the secret
            # out of any crash-reporter / chained-traceback inspection.
            raise UpstreamUnavailableError("Azure AD token request failed") from None

        if resp.status_code != 200:
            raise UpstreamUnavailableError(f"Azure AD token endpoint returned {resp.status_code}")

        try:
            body = resp.json()
        except ValueError:
            # A 200 with a non-JSON body (e.g. a proxy login page) must fail closed too.
            raise UpstreamUnavailableError("Azure AD token response is not valid JSON") from None
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise UpstreamUnavailableError("Azure AD token response missing access_token")

        expires_in = body.get("expires_in")
        ttl = float(expires_in) if isinstance(expires_in, (int, float)) else 0.0
        self._token = token
        self._expires_at = self._now_fn() + ttl
        return token


# ---------------------------------------------------------------------------
# _ProviderEntry — internal cache entry (non-secret key + provider + created time)
# ---------------------------------------------------------------------------

_CacheKey = tuple[str, str, str, str]  # (tenant_id, client_id, authority, scope)


@dataclass
class _ProviderEntry:
    provider: Any  # AzureADTokenProvider (or test fake)
    created: float  # monotonic timestamp when the provider was created


def _make_cache_key(config: AzureADConfig) -> _CacheKey:
    """Build the NON-SECRET cache key from an AzureADConfig.

    Deliberately excludes ``client_secret`` — the key must never contain a secret
    (memory hygiene; rotation is handled as bounded staleness <= TTL).
    """
    return (config.tenant_id, config.client_id, config.authority, config.scope)


def _default_provider_factory(config: AzureADConfig) -> AzureADTokenProvider:
    """Default factory: construct a real AzureADTokenProvider from config.

    Signature is deliberately UNCHANGED (single positional arg) — the cache's egress
    policy is pushed down post-construction via ``set_egress_policy`` (see
    ``AzureADTokenProviderCache.get_or_create``) so this factory seam stays compatible
    with the frozen ``tests/dynamic_auth_byok/test_azure_ad_provider_cache.py`` fakes.
    """
    return AzureADTokenProvider(config=config)


class AzureADTokenProviderCache:
    """Per-tenant TTL+size-capped cache of AzureADTokenProvider instances.

    §3 CONTRACT (dynamic-auth-byok TASK.md) — FROZEN @ v1.

    Key = NON-SECRET AzureADConfig identity (tenant_id, client_id, authority, scope).
    ``client_secret`` is NEVER in the key — a rotated secret takes effect within TTL.

    ``get_or_create`` is SYNCHRONOUS (provider construction is non-IO; the AAD mint
    happens lazily in ``AzureADTokenProvider.get_token()``).

    Eviction policy:
    - Per-entry TTL: reuse iff (now - created) < ttl_s; else build a fresh provider
      and close the old one's httpx client fire-and-forget (asyncio.create_task).
    - Soft size cap: when len > max_size, evict + close the oldest-created entry.

    Security:
    - ``client_secret`` never enters the cache key, any log, span, or error chain.
    - Each tenant's token provider is isolated: distinct (tenant_id, client_id) pairs
      map to distinct entries → tokens are NEVER crossed between tenants.
    """

    def __init__(
        self,
        *,
        ttl_s: float,
        max_size: int,
        now_fn: Callable[[], float] = time.monotonic,
        provider_factory: Callable[[AzureADConfig], Any] = _default_provider_factory,
        metrics_registry: MetricsRegistry | None = None,
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_size = max_size
        self._now_fn = now_fn
        self._factory = provider_factory
        self._metrics_registry = metrics_registry
        # S3 SSRF/credential-exfiltration deny — when set, pushed into every provider this
        # cache constructs via set_egress_policy (see get_or_create), so ONE settings-derived
        # policy instance backs every AAD token mint sharing this cache. None (the default,
        # e.g. every existing test's fake provider_factory) leaves each constructed
        # provider's OWN default (the real, fail-closed policy) untouched.
        self._egress_policy = egress_policy
        # Ordered dict preserves insertion order for oldest-first eviction.
        self._cache: dict[_CacheKey, _ProviderEntry] = {}

    def _close_provider_fire_and_forget(self, provider: Any) -> None:
        """Schedule provider.aclose() as a background task (fire-and-forget).

        Falls back to a best-effort synchronous noop if there is no running loop
        (e.g. in sync tests that never enter an event loop).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — nothing to schedule; provider will be GC'd.
            return
        _task = loop.create_task(provider.aclose())  # noqa: RUF006 — fire-and-forget close

    def get_or_create(self, config: AzureADConfig) -> Any:
        """Return the cached provider for ``config``'s identity, constructing one if needed.

        SYNCHRONOUS — never awaited; provider construction is non-IO.

        Args:
            config: AzureADConfig whose non-secret fields form the cache key.

        Returns:
            An AzureADTokenProvider (or factory-supplied fake) for the config identity.
        """
        key = _make_cache_key(config)
        now = self._now_fn()

        entry = self._cache.get(key)
        if entry is not None and (now - entry.created) < self._ttl_s:
            # Within TTL — reuse the existing provider.
            return entry.provider

        # TTL expired or not present — build a fresh provider.
        if entry is not None:
            # Close the old provider's httpx client fire-and-forget.
            self._close_provider_fire_and_forget(entry.provider)
            del self._cache[key]

        new_provider = self._factory(config)
        if self._egress_policy is not None and hasattr(new_provider, "set_egress_policy"):
            # Push the cache's ONE shared, settings-derived policy into the newly-built
            # provider. hasattr guards a test's fake provider (no such method) — no-op
            # there, matching the frozen provider_factory single-arg contract exactly.
            new_provider.set_egress_policy(self._egress_policy)
        self._cache[key] = _ProviderEntry(provider=new_provider, created=now)

        # Enforce soft size cap — evict the oldest-created entry when exceeded.
        if len(self._cache) > self._max_size:
            oldest_key = next(iter(self._cache))
            oldest_entry = self._cache.pop(oldest_key)
            self._close_provider_fire_and_forget(oldest_entry.provider)

        return new_provider


__all__ = [
    "DEFAULT_AUTHORITY",
    "DEFAULT_SCOPE",
    "AzureADConfig",
    "AzureADTokenProvider",
    "AzureADTokenProviderCache",
]
