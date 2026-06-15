"""Azure AD client-credentials token auth (azure-aad-auth §3 FROZEN @ v1).

The one genuinely-new auth sub-system for Azure OpenAI: acquire an OAuth2
client-credentials bearer token from Azure AD and cache it, as an alternative to the
static api-key. Used by AzureCompletionUpstream (and the embeddings provider) when AAD
is configured.

Design-for-failure (CLAUDE.md):
  - timeouts on the token POST;
  - FAIL-CLOSED — a non-200 / timeout / network error raises UpstreamUnavailableError;
    we NEVER serve an expired token, fall back to api-key, or emit a blank Bearer;
  - single-flight refresh — an asyncio.Lock + double-check means a token-expiry stampede
    makes exactly ONE token request;
  - refresh-before-expiry skew so a token is renewed slightly early.

Security: client_secret (config) and the acquired token are SECRETS — client_secret is
field(repr=False); neither ever appears in a log, metric label, span attribute, URL, or
exception message (the token enters only the Authorization header; errors carry only a
status code, never a response body).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

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


def resolve_azure_ad_config(settings: object) -> AzureADConfig | None:
    """Return an AzureADConfig iff tenant_id, client_id, and client_secret are all truthy.

    Returns None otherwise (opt-in; partial AAD config disables AAD — the adapter then
    falls back to api-key auth if that is configured). ``scope`` falls back to
    DEFAULT_SCOPE.
    """
    tenant: str = getattr(settings, "azure_tenant_id", "") or ""
    client: str = getattr(settings, "azure_client_id", "") or ""
    secret: str = getattr(settings, "azure_client_secret", "") or ""
    if not (tenant and client and secret):
        return None
    scope: str = getattr(settings, "azure_ad_scope", "") or DEFAULT_SCOPE
    return AzureADConfig(
        tenant_id=tenant,
        client_id=client,
        client_secret=secret,
        scope=scope,
    )


class AzureADTokenProvider:
    """Acquire + cache + refresh an Azure AD client-credentials bearer token.

    A single instance is shared for the app lifetime. ``get_token()`` is safe under
    concurrency: a single-flight asyncio.Lock + a post-lock cache re-check means a
    stampede of expired-token requests makes exactly one IDP call.
    """

    def __init__(
        self,
        *,
        config: AzureADConfig,
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

    async def _acquire(self) -> str:
        """Acquire a fresh token from the IDP and cache it. Fail-closed on any error."""
        form = {
            "grant_type": "client_credentials",
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "scope": self._config.scope,
        }
        try:
            resp = await self._client.post(self._token_url(), data=form)
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


__all__ = [
    "DEFAULT_AUTHORITY",
    "DEFAULT_SCOPE",
    "AzureADConfig",
    "AzureADTokenProvider",
    "resolve_azure_ad_config",
]
