"""RED suite for ops-platform-job-identity (TASK.md §4).

`resolve_platform_credential` (gateway.ops.api.deps) composes THREE existing primitives,
adding zero new credential-resolution logic:
  - require_ops                  (ops/api/deps.py)                       — UNCHANGED
  - get_platform_tenant          (tenants/infrastructure/repository.py)  — platform-tenant-seed
  - resolve_provider_credential  (proxy/application/use_cases.py)        — UNCHANGED

RED before BUILD: `resolve_platform_credential` and `PLATFORM_TENANT_MISSING` do not
exist yet (ImportError).

require_ops's OWN denial behavior (401/403, byte-identical failure modes, default-OFF
fail-closed) is exhaustively covered by tests/operator_wide_reconciliation/ (OW1-OW9).
test_require_ops_still_denies_without_valid_cert below re-confirms only the ONE
precondition this task's new function relies on, by calling the CANONICAL, pre-existing
GET /ops/reconciliation route — no new route is registered anywhere in this suite
(CONVENTIONS.md v3: "test arranges call CANONICAL routes only").
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import FakePlatformCredentialResolver, seed_platform_tenant


# ---------------------------------------------------------------------------
# an ops-authenticated caller resolves the platform tenant's own credential
# ---------------------------------------------------------------------------
async def test_ops_authenticated_caller_resolves_platform_credential(
    db_session: AsyncSession,
    platform_resolver: FakePlatformCredentialResolver,
    minimax_credential: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from gateway.ops.api.deps import resolve_platform_credential
    from gateway.proxy.domain.credential_context import (
        get_provider_credential,
        reset_provider_credential,
    )

    platform_id = await seed_platform_tenant(db_session)
    decoy_tenant_id = uuid.uuid4()  # must NEVER be the id resolve_platform_credential uses
    platform_resolver.program(decoy_tenant_id, "minimax", minimax_credential)
    platform_resolver.program(platform_id, "minimax", minimax_credential)

    token = await resolve_platform_credential(
        platform_resolver, db_session, "minimax", session_factory
    )

    assert token is not None
    try:
        assert get_provider_credential() is minimax_credential
        assert (platform_id, "minimax") in platform_resolver.calls
        assert (decoy_tenant_id, "minimax") not in platform_resolver.calls
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]
    assert get_provider_credential() is None


# ---------------------------------------------------------------------------
# a non-BYOK provider, or no resolver wired, is a silent no-op (unchanged pass-through)
# ---------------------------------------------------------------------------
async def test_non_byok_provider_and_unwired_resolver_return_none(
    db_session: AsyncSession,
    platform_resolver: FakePlatformCredentialResolver,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from gateway.ops.api.deps import resolve_platform_credential
    from gateway.proxy.domain.credential_context import get_provider_credential

    await seed_platform_tenant(db_session)

    not_byok = await resolve_platform_credential(
        platform_resolver, db_session, "not-a-real-provider", session_factory
    )
    no_resolver = await resolve_platform_credential(None, db_session, "minimax", session_factory)

    assert not_byok is None
    assert no_resolver is None
    assert get_provider_credential() is None  # nothing was ever set


# ---------------------------------------------------------------------------
# the platform tenant has no configured credential for the provider -> REUSED 402
# ---------------------------------------------------------------------------
async def test_missing_platform_credential_raises_402(
    db_session: AsyncSession,
    platform_resolver: FakePlatformCredentialResolver,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from gateway.core.errors import ProblemError
    from gateway.ops.api.deps import resolve_platform_credential

    await seed_platform_tenant(db_session)
    # nothing programmed on platform_resolver for "minimax" -> ProviderKeyMissing

    with pytest.raises(ProblemError) as exc_info:
        await resolve_platform_credential(platform_resolver, db_session, "minimax", session_factory)

    assert exc_info.value.status == 402
    assert exc_info.value.code == "ERR_PROVIDER_KEY_MISSING"


# ---------------------------------------------------------------------------
# the platform tenant row does not exist (unmigrated) -> NEW 500 mapping, fails closed
# ---------------------------------------------------------------------------
async def test_missing_platform_tenant_raises_500(
    db_session: AsyncSession,
    platform_resolver: FakePlatformCredentialResolver,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from gateway.core.errors import ProblemError
    from gateway.ops.api.deps import resolve_platform_credential

    # no seed_platform_tenant call — tenants has no kind='platform' row

    with pytest.raises(ProblemError) as exc_info:
        await resolve_platform_credential(platform_resolver, db_session, "minimax", session_factory)

    assert exc_info.value.status == 500
    assert exc_info.value.code == "ERR_PLATFORM_TENANT_MISSING"
    assert platform_resolver.calls == []  # never reached the resolver — no fabricated tenant_id


# ---------------------------------------------------------------------------
# require_ops (the precondition this task relies on) still denies a non-operator
# caller, byte-identically to before this task; proven via the CANONICAL route.
# ---------------------------------------------------------------------------
async def test_require_ops_still_denies_without_valid_cert(client: Any, app: Any) -> None:
    from tests.operator_wide_reconciliation.conftest import OPS_RECON, recon_params

    # ops-auth default-OFF (no enable_ops call), no XFCC header sent
    resp = await client.get(OPS_RECON, params=recon_params())

    assert resp.status_code == 401
    assert resp.json().get("code") == "ERR_OPS_UNAUTHORIZED"
