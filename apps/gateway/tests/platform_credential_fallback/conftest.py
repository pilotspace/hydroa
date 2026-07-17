"""Fakes for the platform-credential-fallback RED suite (§4 TESTS plan).

All fakes are structural (satisfy the Protocol without inheriting). No DB, no Redis,
no network. asyncio_mode = "auto" (pyproject.toml) so async tests run without a marker.

The credential seam under test is `gateway.proxy.application.use_cases.resolve_provider_credential`
— it EXISTS today but WITHOUT the `platform_fallback` keyword-only param the frozen §3 contract
adds, so every test that passes `platform_fallback=...` fails RED with a TypeError until BUILD
extends the signature. The M8 signal helpers (`served_via_platform_fallback`, `mark_platform_fallback`)
and the `platform_credential_fallback_enabled` setting also do not exist yet (ImportError / AttributeError).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderCredential


# ---------------------------------------------------------------------------
# FakeResolver — structural TenantCredentialResolver keyed on (tenant_id, provider).
# Present keys resolve; absent keys raise ProviderKeyMissing (like the real store→resolver).
# Records every resolve(tenant_id, provider) call so tests can assert WHICH tenant id
# the platform lookup used (the M4 cache-key observable) and precedence (M2).
# ---------------------------------------------------------------------------
class FakeResolver:
    def __init__(self, keys: dict[tuple[uuid.UUID, str], ProviderCredential]) -> None:
        self._keys = keys
        self.calls: list[tuple[uuid.UUID, str]] = []

    async def resolve(self, tenant_id: uuid.UUID, provider: str) -> ProviderCredential:
        # Import here so the fake stays importable even if the symbol moves.
        from gateway.proxy.domain.provider_credentials import ProviderKeyMissing

        self.calls.append((tenant_id, provider))
        cred = self._keys.get((tenant_id, provider))
        if cred is None:
            raise ProviderKeyMissing(provider)
        return cred


# ---------------------------------------------------------------------------
# FakePlatformFallback — structural PlatformCredentialFallback (frozen §3 port).
# enabled = the kill-switch; platform_tenant_id() returns the reserved row id or None
# (None = row not provisioned OR DB error/timeout, fail-closed → R3). Records audits.
# ---------------------------------------------------------------------------
class FakePlatformFallback:
    def __init__(self, *, enabled: bool = True, platform_id: uuid.UUID | None = None) -> None:
        self.enabled = enabled
        self._platform_id = platform_id
        self.platform_id_calls = 0
        self.served_audits: list[tuple[uuid.UUID, str]] = []
        self.misconfig_audits: list[tuple[uuid.UUID, str]] = []

    async def platform_tenant_id(self) -> uuid.UUID | None:
        self.platform_id_calls += 1
        return self._platform_id

    async def audit_served(self, *, tenant_id: uuid.UUID, provider: str) -> None:
        self.served_audits.append((tenant_id, provider))

    async def audit_misconfig(self, *, tenant_id: uuid.UUID, provider: str) -> None:
        self.misconfig_audits.append((tenant_id, provider))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def requester_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def platform_id() -> uuid.UUID:
    return uuid.uuid4()


def bearer(secret: str) -> BearerCredential:
    return BearerCredential(secret=secret)


@pytest.fixture
def own_secret() -> str:
    return "sk-own-tenant-secret-000"


@pytest.fixture
def platform_secret() -> str:
    return "sk-platform-secret-999"
