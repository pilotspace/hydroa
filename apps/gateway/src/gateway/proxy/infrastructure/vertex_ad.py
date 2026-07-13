"""GCP service-account JWT-bearer (RFC 7523) token auth + per-tenant provider cache.

Mirrors ``azure_ad.py`` FILE-FOR-FILE (vertex-adapter TASK.md §3), swapping Azure AD's
OAuth2 client-credentials grant for GCP's OAuth2 JWT-bearer grant (RFC 7523):

Provides:
  - VertexTokenProvider: Acquire + cache + refresh an OAuth2 bearer token minted via
    an RS256-signed JWT assertion. Fail-closed on any IDP error.
  - VertexTokenProviderCache: Per-identity TTL+size-capped cache of VertexTokenProvider
    instances, keyed by the NON-SECRET identity (client_email, project_id).

Design-for-failure (CLAUDE.md):
  - timeouts on the token POST;
  - FAIL-CLOSED — a non-200 / timeout / network / malformed-body error raises
    UpstreamUnavailableError; we NEVER serve an expired/blank token, and NEVER fall
    back to an unauthenticated Vertex call;
  - single-flight refresh — an asyncio.Lock + double-check means a token-expiry
    stampede makes exactly ONE token request;
  - refresh-before-expiry skew so a token is renewed slightly early.

Security: ``private_key`` (config) is a SECRET — field(repr=False); neither it nor the
acquired access token ever appears in a log, metric label, span attribute, URL, cache
key, or exception message. The RS256 assertion is signed via ``pyjwt`` (already an
allow-listed dependency, no new package — TASK.md §1 Framings alternative B / DECIDED
at freeze). No egress-policy check is performed here (unlike azure_ad.py's AAD
authority check): ``token_uri`` is a FIXED constant (or, for local-stub tests, an
operator/test-supplied override) — it is NEVER tenant-PUT-supplied (vertex-adapter
TASK.md §1 M7), so there is no tenant-controlled host-injection surface to guard.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import jwt

from gateway.proxy.domain.errors import UpstreamUnavailableError

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

_CONNECT_TIMEOUT = 10.0
_TOKEN_TIMEOUT = 30.0

#: RFC 7523 JWT-bearer grant type (fixed, never tenant-supplied).
_JWT_BEARER_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

#: GCP-required maximum assertion lifetime is 1 hour — the assertion's OWN exp claim,
#: distinct from the access token's expires_in returned by the token endpoint.
_ASSERTION_TTL_S = 3600

DEFAULT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 — a URL, not a credential


@dataclass(frozen=True)
class VertexServiceAccountConfig:
    """Immutable GCP service-account JWT-bearer config.

    ``private_key`` is excluded from repr/str so it never leaks into logs or output.
    """

    project_id: str
    client_email: str
    private_key: str = field(repr=False)
    private_key_id: str | None = None
    scope: str = DEFAULT_SCOPE
    token_uri: str = DEFAULT_TOKEN_URI


class VertexTokenProvider:
    """Acquire + cache + refresh a GCP OAuth2 JWT-bearer bearer token.

    A single instance is shared per GCP identity within the app lifetime (via
    VertexTokenProviderCache). ``get_token()`` is safe under concurrency: a
    single-flight asyncio.Lock + a post-lock cache re-check means a stampede of
    expired-token requests makes exactly one IDP call.
    """

    def __init__(
        self,
        *,
        config: VertexServiceAccountConfig,
        now_fn: Callable[[], float] = time.monotonic,
        expiry_skew_s: float = 60.0,
        metrics_registry: MetricsRegistry | None = None,
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

    def _build_assertion(self) -> str:
        """Sign the RFC 7523 claims JWT with the service-account RSA private key.

        iss/sub = client_email, scope = config.scope, aud = config.token_uri
        (the fixed GCP token endpoint, or a test/operator override), iat/exp per
        GCP's 1-hour maximum assertion lifetime. RS256 via pyjwt (already an
        allow-listed dependency).
        """
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self._config.client_email,
            "sub": self._config.client_email,
            "scope": self._config.scope,
            "aud": self._config.token_uri,
            "iat": now,
            "exp": now + _ASSERTION_TTL_S,
        }
        headers: dict[str, Any] | None = (
            {"kid": self._config.private_key_id} if self._config.private_key_id else None
        )
        # jwt.encode returns str with PyJWT>=2; the private_key PEM string is consumed
        # here only, never assigned to a log-adjacent variable.
        return jwt.encode(claims, self._config.private_key, algorithm="RS256", headers=headers)

    async def _acquire(self) -> str:
        """Acquire a fresh token from the IDP and cache it. Fail-closed on any error."""
        try:
            assertion = self._build_assertion()
        except (ValueError, TypeError, jwt.exceptions.PyJWTError):
            # Malformed private_key (bad PEM, wrong key type, etc.) — fail closed.
            # PyJWT raises jwt.exceptions.InvalidKeyError (a PyJWTError, NOT a
            # ValueError/TypeError) for an unparseable PEM, so PyJWTError must be
            # caught explicitly here too, or a malformed key would escape this
            # fail-closed wrapper entirely. `from None` keeps the underlying crypto
            # error (which can reference key material) out of the exception chain
            # (v22 secret-chain floor).
            raise UpstreamUnavailableError("Vertex JWT assertion signing failed") from None
        form = {
            "grant_type": _JWT_BEARER_GRANT_TYPE,
            "assertion": assertion,
        }
        try:
            resp = await self._client.post(self._config.token_uri, data=form)
        except (httpx.TimeoutException, httpx.NetworkError):
            # FAIL-CLOSED — suppress the exception chain: the httpx error carries the
            # request object whose body holds the signed assertion. `from None` keeps
            # any key-derived material out of any crash-reporter / chained-traceback.
            raise UpstreamUnavailableError("Vertex token request failed") from None

        if resp.status_code != 200:
            raise UpstreamUnavailableError(f"Vertex token endpoint returned {resp.status_code}")

        try:
            body = resp.json()
        except ValueError:
            # A 200 with a non-JSON body (e.g. a proxy login page) must fail closed too.
            raise UpstreamUnavailableError("Vertex token response is not valid JSON") from None
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise UpstreamUnavailableError("Vertex token response missing access_token")

        expires_in = body.get("expires_in")
        ttl = float(expires_in) if isinstance(expires_in, (int, float)) else 0.0
        self._token = token
        self._expires_at = self._now_fn() + ttl
        return token


# ---------------------------------------------------------------------------
# _ProviderEntry — internal cache entry (non-secret key + provider + created time)
# ---------------------------------------------------------------------------

_CacheKey = tuple[str, str, str]  # (hydroa_tenant_id, client_email, project_id)


@dataclass
class _ProviderEntry:
    provider: Any  # VertexTokenProvider (or test fake)
    created: float  # monotonic timestamp when the provider was created


def _make_cache_key(tenant_id: object, config: VertexServiceAccountConfig) -> _CacheKey:
    """Build the NON-SECRET, per-(hydroa-tenant, identity) cache key.

    The owning Hydroa ``tenant_id`` is the FIRST element and is MANDATORY for
    cross-tenant isolation (M4 CR-2): without it, a tenant reusing another tenant's
    ``(client_email, project_id)`` would be served the victim's cached bearer token
    without possessing the victim's key — a confused-deputy token theft.

    Deliberately excludes ``private_key`` — the key must never contain a secret
    (memory hygiene; rotation is handled as bounded staleness <= TTL).
    """
    return (str(tenant_id), config.client_email, config.project_id)


def _default_provider_factory(config: VertexServiceAccountConfig) -> VertexTokenProvider:
    """Default factory: construct a real VertexTokenProvider from config."""
    return VertexTokenProvider(config=config)


class VertexTokenProviderCache:
    """Per-identity TTL+size-capped cache of VertexTokenProvider instances.

    §3 CONTRACT (vertex-adapter TASK.md) — mirrors AzureADTokenProviderCache.

    Key = NON-SECRET identity (client_email, project_id). ``private_key`` is NEVER
    in the key — a rotated key takes effect within TTL.

    ``get_or_create`` is SYNCHRONOUS (provider construction is non-IO; the GCP
    mint happens lazily in ``VertexTokenProvider.get_token()``).

    Eviction policy:
    - Per-entry TTL: reuse iff (now - created) < ttl_s; else build a fresh provider
      and close the old one's httpx client fire-and-forget (asyncio.create_task).
    - Soft size cap: when len > max_size, evict + close the oldest-created entry.

    Security:
    - ``private_key`` never enters the cache key, any log, span, or error chain.
    - Each tenant's token provider is isolated: distinct (client_email, project_id)
      pairs map to distinct entries → tokens are NEVER crossed between tenants.
    """

    def __init__(
        self,
        *,
        ttl_s: float,
        max_size: int,
        now_fn: Callable[[], float] = time.monotonic,
        provider_factory: Callable[[VertexServiceAccountConfig], Any] = _default_provider_factory,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_size = max_size
        self._now_fn = now_fn
        self._factory = provider_factory
        self._metrics_registry = metrics_registry
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

    def get_or_create(self, config: VertexServiceAccountConfig, tenant_id: object | None) -> Any:
        """Return the cached provider for ``(tenant_id, config identity)``, building one if needed.

        SYNCHRONOUS — never awaited; provider construction is non-IO.

        Args:
            config: VertexServiceAccountConfig whose non-secret fields form the cache key.
            tenant_id: the owning Hydroa tenant. MANDATORY for cross-tenant isolation
                (M4 CR-2). When ``None`` (owner unknown — e.g. a non-BYOK path or a unit
                test), a FRESH per-call provider is returned and NOTHING is cached, so an
                unknown owner can never share or poison another tenant's entry (fail-safe).

        Returns:
            A VertexTokenProvider (or factory-supplied fake).
        """
        if tenant_id is None:
            # Fail-safe: never fold an unknown owner into a shared cache entry.
            return self._factory(config)
        key = _make_cache_key(tenant_id, config)
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
        self._cache[key] = _ProviderEntry(provider=new_provider, created=now)

        # Enforce soft size cap — evict the oldest-created entry when exceeded.
        if len(self._cache) > self._max_size:
            oldest_key = next(iter(self._cache))
            oldest_entry = self._cache.pop(oldest_key)
            self._close_provider_fire_and_forget(oldest_entry.provider)

        return new_provider


__all__ = [
    "DEFAULT_SCOPE",
    "DEFAULT_TOKEN_URI",
    "VertexServiceAccountConfig",
    "VertexTokenProvider",
    "VertexTokenProviderCache",
]
