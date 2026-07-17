"""Fakes for the fallback-usage-marker RED suite (§4 TESTS plan).

All fakes are structural — no DB, no Redis, no network for the dispatch-level tests;
the ONE recorder-level test uses a capturing fake Redis and drives the cached=True path
(which never opens a DB session). asyncio_mode = "auto" (pyproject.toml).

The seam under test:
  * `_credential_source_ctx` (NEW contextvar) published inside `_resolve_platform_fallback`
    right after `mark_platform_fallback()`; read in `_dispatch_record` and threaded as the
    `credential_source` extra — none of which exist at Ground SHA 3c27af5, so every test that
    imports `_credential_source_ctx` fails RED (ImportError) until BUILD, and the recorder /
    dispatch assertions fail RED (missing kwarg / missing raw key) after that.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderCredential


# ---------------------------------------------------------------------------
# FakeResolver — structural TenantCredentialResolver keyed on (tenant_id, provider).
# ---------------------------------------------------------------------------
class FakeResolver:
    def __init__(self, keys: dict[tuple[uuid.UUID, str], ProviderCredential]) -> None:
        self._keys = keys
        self.calls: list[tuple[uuid.UUID, str]] = []

    async def resolve(self, tenant_id: uuid.UUID, provider: str) -> ProviderCredential:
        from gateway.proxy.domain.provider_credentials import ProviderKeyMissing

        self.calls.append((tenant_id, provider))
        cred = self._keys.get((tenant_id, provider))
        if cred is None:
            raise ProviderKeyMissing(provider)
        return cred


# ---------------------------------------------------------------------------
# FakePlatformFallback — structural PlatformCredentialFallback (task-1 frozen §3 port).
# ---------------------------------------------------------------------------
class FakePlatformFallback:
    def __init__(self, *, enabled: bool = True, platform_id: uuid.UUID | None = None) -> None:
        self.enabled = enabled
        self._platform_id = platform_id
        self.served_audits: list[tuple[uuid.UUID, str]] = []
        self.misconfig_audits: list[tuple[uuid.UUID, str]] = []

    async def platform_tenant_id(self) -> uuid.UUID | None:
        return self._platform_id

    async def audit_served(self, *, tenant_id: uuid.UUID, provider: str) -> None:
        self.served_audits.append((tenant_id, provider))

    async def audit_misconfig(self, *, tenant_id: uuid.UUID, provider: str) -> None:
        self.misconfig_audits.append((tenant_id, provider))


# ---------------------------------------------------------------------------
# CapturingRecorder — structural UsageRecorder that records the kwargs each dispatch
# forwards, so a test can assert whether `credential_source` reached it. Declares
# `supported_extras` like the real recorder; a variant omits "credential_source" to
# exercise the capability-filter (M5/R1).
# ---------------------------------------------------------------------------
class CapturingRecorder:
    def __init__(self, *, supports_credential_source: bool = True) -> None:
        base = {
            "team_id",
            "cached",
            "request_id",
            "tags",
            "agent_principal_id",
        }
        if supports_credential_source:
            base.add("credential_source")
        self.supported_extras: frozenset[str] = frozenset(base)
        self.calls: list[dict[str, Any]] = []
        self._event = asyncio.Event()

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        self._event.set()

    async def wait(self) -> None:
        await asyncio.wait_for(self._event.wait(), 1.0)


# ---------------------------------------------------------------------------
# CapturingRedis — records XADD field dicts; drives the recorder cached-path test
# (cached=True => cost 0 => no incrbyfloat, no DB session opened).
# ---------------------------------------------------------------------------
class CapturingRedis:
    def __init__(self) -> None:
        self.xadds: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, key: str, fields: dict[str, str]) -> bytes:
        self.xadds.append((key, dict(fields)))
        return b"0-1"

    async def incrbyfloat(self, *args: Any, **kwargs: Any) -> float:  # pragma: no cover
        raise AssertionError("incrbyfloat must not be called on the cached (cost=0) path")


class ExplodingSessionFactory:
    """A session_factory that fails if opened — proves the cached path never touches the DB."""

    def __call__(self) -> Any:  # pragma: no cover
        raise AssertionError("session_factory must not be opened on the cached path")


@pytest.fixture
def requester_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def platform_id() -> uuid.UUID:
    return uuid.uuid4()


def bearer(secret: str) -> BearerCredential:
    return BearerCredential(secret=secret)
