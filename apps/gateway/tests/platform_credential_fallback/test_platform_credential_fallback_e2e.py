"""End-to-end (DB-backed) test of the WIRED default-ON platform-credential-fallback path.

Closes the VERIFY-phase residue flagged by the independent security verify: the 20 unit tests
cover the seam with fakes, but nothing exercised the REAL production wiring
(create_app → app.state.platform_credential_fallback → the real CachedTenantCredentialResolver →
the real PlatformCredentialFallbackService → a real get_platform_tenant DB read → a real Fernet
encrypt/decrypt of the platform BYOK key) serving a keyless tenant.

Unlike the shared `app` fixture (which installs a STUB resolver), this builds its own app via
create_app with a real provider_key_encryption_key so the true DbTenantProviderKeyStore-backed
resolver and the default-ON fallback service are live.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from gateway.proxy.application.platform_fallback import PlatformCredentialFallbackService
from gateway.proxy.application.use_cases import resolve_provider_credential
from gateway.proxy.domain.credential_context import (
    get_credential_tenant,
    get_provider_credential,
    reset_provider_credential,
    served_via_platform_fallback,
)
from gateway.proxy.domain.provider_credentials import BearerCredential
from tests import _redis_env


def _settings(*, fallback_enabled: bool = True) -> Settings:
    return Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret="test-secret-not-for-production-0123456789",  # noqa: S106
        redis_url=_redis_env.TEST_REDIS_URL,
        provider_key_encryption_key=Fernet.generate_key().decode(),
        platform_credential_fallback_enabled=fallback_enabled,
    )  # type: ignore[call-arg]


async def _build_app(*, fallback_enabled: bool = True) -> AsyncIterator[object]:
    app = create_app(_settings(fallback_enabled=fallback_enabled))
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield app
    finally:
        await engine.dispose()


async def _seed_platform_tenant(app: object) -> uuid.UUID:
    plat_id = uuid.uuid4()
    async with app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        await s.execute(
            text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
            {"id": plat_id},
        )
        await s.commit()
    return plat_id


async def test_e2e_default_on_serves_platform_credential_real_wiring() -> None:
    """Real create_app wiring (default-ON) serves a keyless tenant the platform BYOK credential,
    with the PLATFORM tenant id as the token-cache owner (confused-deputy) — through the real
    resolver, store, Fernet, cache, and get_platform_tenant DB read.
    """
    gen = _build_app(fallback_enabled=True)
    app = await gen.__anext__()
    try:
        # (1) main.py wiring: the service exists on app.state and is default-ON.
        fb = app.state.platform_credential_fallback  # type: ignore[attr-defined]
        assert isinstance(fb, PlatformCredentialFallbackService)
        assert fb.enabled is True

        # (2) seed the reserved platform tenant + its own openrouter BYOK key (real store/Fernet).
        plat_id = await _seed_platform_tenant(app)
        store = app.state.tenant_provider_key_store  # type: ignore[attr-defined]
        await store.upsert(
            tenant_id=plat_id,
            provider="openrouter",
            credential=BearerCredential(secret="platform-openrouter-KEY-abc"),  # noqa: S106
        )

        # (3) the real service resolves the platform tenant id via a real DB read.
        assert await fb.platform_tenant_id() == plat_id

        # (4) a keyless customer tenant is served the PLATFORM credential through the real seam.
        resolver = app.state.tenant_credential_resolver  # type: ignore[attr-defined]
        customer_id = uuid.uuid4()  # a real customer row, but NO provider key of its own
        async with app.state.sessionmaker() as s:  # type: ignore[attr-defined]
            await s.execute(
                text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Acme', 'customer')"),
                {"id": customer_id},
            )
            await s.commit()
        token = await resolve_provider_credential(
            resolver, customer_id, "openrouter", platform_fallback=fb
        )
        try:
            cred = get_provider_credential()
            assert isinstance(cred, BearerCredential)
            assert cred.secret.get_secret_value() == "platform-openrouter-KEY-abc"
            # confused-deputy: the token-cache owner is the PLATFORM id, resolved from the real DB.
            assert get_credential_tenant() == plat_id
            assert get_credential_tenant() != customer_id
            assert served_via_platform_fallback() is True
        finally:
            reset_provider_credential(token)  # type: ignore[arg-type]
        assert served_via_platform_fallback() is False

        # (5) own-key precedence through the REAL positive-only cache: the earlier keyless miss was
        # NOT cached, so once the customer configures its own key the very next resolve serves IT,
        # never a stale platform entry (M2 + M4 proven against the real cache).
        await store.upsert(
            tenant_id=customer_id,
            provider="openrouter",
            credential=BearerCredential(secret="customer-OWN-KEY-xyz"),  # noqa: S106
        )
        token2 = await resolve_provider_credential(
            resolver, customer_id, "openrouter", platform_fallback=fb
        )
        try:
            own = get_provider_credential()
            assert isinstance(own, BearerCredential)
            assert own.secret.get_secret_value() == "customer-OWN-KEY-xyz"
            assert get_credential_tenant() == customer_id
            assert served_via_platform_fallback() is False
        finally:
            reset_provider_credential(token2)  # type: ignore[arg-type]
    finally:
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()


async def test_e2e_kill_switch_off_keyless_gets_402_real_wiring() -> None:
    """With the kill-switch OFF, the real wiring is byte-identical to pre-fallback: a keyless
    tenant gets 402 ERR_PROVIDER_KEY_MISSING even though a platform key exists.
    """
    from gateway.core.errors import ProblemError

    gen = _build_app(fallback_enabled=False)
    app = await gen.__anext__()
    try:
        fb = app.state.platform_credential_fallback  # type: ignore[attr-defined]
        assert fb.enabled is False

        plat_id = await _seed_platform_tenant(app)
        store = app.state.tenant_provider_key_store  # type: ignore[attr-defined]
        await store.upsert(
            tenant_id=plat_id,
            provider="openrouter",
            credential=BearerCredential(secret="platform-openrouter-KEY-abc"),  # noqa: S106
        )

        resolver = app.state.tenant_credential_resolver  # type: ignore[attr-defined]
        customer_id = uuid.uuid4()
        with pytest.raises(ProblemError) as exc:
            await resolve_provider_credential(
                resolver, customer_id, "openrouter", platform_fallback=fb
            )
        assert exc.value.status == 402
        assert exc.value.code == "ERR_PROVIDER_KEY_MISSING"
        assert get_provider_credential() is None
        assert served_via_platform_fallback() is False
    finally:
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
