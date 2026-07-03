"""RED->GREEN suite for superadmin-audit-foundation Part A (TASK.md §2/§3 — FROZEN @ v1).

Part A retrofits `resolve_platform_credential` (gateway.ops.api.deps) with a new
required `session_factory` param and three fire-and-forget, fail-open audit-write
branches. Part B (/admin/auth password-login integration) and Part C (OIDC-side
integration, tests/superadmin_audit_foundation/test_part_c_oidc_login_audit.py) are
covered elsewhere — this file is Part A ONLY.

RED before BUILD: `resolve_platform_credential` does not accept a `session_factory`
parameter yet -> TypeError: resolve_platform_credential() missing 1 required
positional argument: 'session_factory' (the RIGHT reason — feature genuinely absent,
not a broken test).

NOTE on count: the frozen §2 SCENARIOS block contains FIVE distinct "Scenario:" blocks
whose arrange/act use `resolve_platform_credential` (success / no-op skip / tenant-
missing / key-missing / audit-write-fail-open) — not four, despite the build brief's own
paraphrase ("4 Part-A + 4 Part-C"). §2 is the authoritative, frozen source (per the
shared-context brief's own instruction: "read it directly ... do not rely on any
paraphrase of it"), and each of the 5 blocks maps to a distinct, separately-load-bearing
Must (see TASK.md §1 <must>, especially the standalone "audit-write failure NEVER
blocks ... resolve_platform_credential's own return/raise" bullet). All 5 are built
here; flagged explicitly in the build report rather than silently trimmed to 4.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import (
    FakePlatformCredentialResolver,
    count_audit_rows,
    fetch_one_audit_row,
    seed_platform_tenant,
)

AUDIT_ACTION = "ops.platform_credential_resolve"


# ---------------------------------------------------------------------------
# Scenario 1: an ops-authenticated resolution of a configured platform credential
# is audited
# ---------------------------------------------------------------------------


async def test_successful_resolution_is_audited(
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
    decoy_tenant_id = uuid.uuid4()  # must NEVER be the id used, mirrors sibling suite
    platform_resolver.program(decoy_tenant_id, "minimax", minimax_credential)
    platform_resolver.program(platform_id, "minimax", minimax_credential)

    token = await resolve_platform_credential(
        platform_resolver, db_session, "minimax", session_factory
    )

    assert token is not None, "credential resolution outcome must be unchanged by this task"
    try:
        assert get_provider_credential() is minimax_credential
        assert (platform_id, "minimax") in platform_resolver.calls
        assert (decoy_tenant_id, "minimax") not in platform_resolver.calls
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]
    assert get_provider_credential() is None

    await asyncio.sleep(0.05)  # let the fire-and-forget audit write complete
    row = await fetch_one_audit_row(db_session, action=AUDIT_ACTION)
    assert row is not None, "Expected exactly one ops.platform_credential_resolve audit row"
    tenant_id, actor_user_id, actor_email, action, target_type, target_id, result, metadata = row
    assert tenant_id is None, "ops-side rows are system events — AuditEvent actor invariant"
    assert actor_user_id is None
    assert actor_email is None
    assert action == AUDIT_ACTION
    assert target_type == "provider"
    assert target_id == "minimax"
    assert result == "success"
    assert metadata["provider"] == "minimax"
    assert metadata["platform_tenant_id"] == str(platform_id)
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 1


# ---------------------------------------------------------------------------
# Scenario 2: a non-BYOK provider or an unwired resolver remains a silent,
# unaudited no-op
# ---------------------------------------------------------------------------


async def test_non_byok_provider_and_unwired_resolver_remain_unaudited(
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

    await asyncio.sleep(0.05)
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 0, (
        "no-op skip paths never reach platform-tenant-specific logic, hence unaudited"
    )


# ---------------------------------------------------------------------------
# Scenario 3: a missing platform tenant is audited as an error before the 500
# is raised
# ---------------------------------------------------------------------------


async def test_missing_platform_tenant_audited_as_error(
    db_session: AsyncSession,
    platform_resolver: FakePlatformCredentialResolver,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from gateway.core.errors import ProblemError
    from gateway.ops.api.deps import resolve_platform_credential

    # no seed_platform_tenant call — tenants has no kind='platform' row

    with pytest.raises(ProblemError) as exc_info:
        await resolve_platform_credential(
            platform_resolver, db_session, "minimax", session_factory
        )

    assert exc_info.value.status == 500
    assert exc_info.value.code == "ERR_PLATFORM_TENANT_MISSING"
    assert platform_resolver.calls == [], (
        "resolver must never be consulted — no fabricated tenant_id, unchanged from before"
    )

    await asyncio.sleep(0.05)
    row = await fetch_one_audit_row(db_session, action=AUDIT_ACTION)
    assert row is not None, "Expected exactly one ops.platform_credential_resolve audit row"
    tenant_id, actor_user_id, actor_email, action, target_type, target_id, result, metadata = row
    assert tenant_id is None
    assert actor_user_id is None
    assert actor_email is None
    assert action == AUDIT_ACTION
    assert result == "error"
    assert target_type == "provider"
    assert target_id == "minimax"
    assert metadata.get("provider") == "minimax"
    assert "platform_tenant_id" not in metadata, "tenant was never resolved — nothing to carry"
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 1


# ---------------------------------------------------------------------------
# Scenario 4: a missing provider credential is audited as denied before the 402
# is raised
# ---------------------------------------------------------------------------


async def test_missing_provider_credential_audited_as_denied(
    db_session: AsyncSession,
    platform_resolver: FakePlatformCredentialResolver,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from gateway.core.errors import ProblemError
    from gateway.ops.api.deps import resolve_platform_credential

    platform_id = await seed_platform_tenant(db_session)
    # nothing programmed on platform_resolver for "minimax" -> ProviderKeyMissing -> 402

    with pytest.raises(ProblemError) as exc_info:
        await resolve_platform_credential(
            platform_resolver, db_session, "minimax", session_factory
        )

    assert exc_info.value.status == 402
    assert exc_info.value.code == "ERR_PROVIDER_KEY_MISSING"

    await asyncio.sleep(0.05)
    row = await fetch_one_audit_row(db_session, action=AUDIT_ACTION)
    assert row is not None, "Expected exactly one ops.platform_credential_resolve audit row"
    tenant_id, actor_user_id, actor_email, action, target_type, target_id, result, metadata = row
    assert tenant_id is None
    assert actor_user_id is None
    assert actor_email is None
    assert action == AUDIT_ACTION
    assert result == "denied"
    assert target_type == "provider"
    assert target_id == "minimax"
    assert metadata.get("provider") == "minimax"
    assert metadata.get("platform_tenant_id") == str(platform_id)
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 1


# ---------------------------------------------------------------------------
# Scenario 5: an ops-side audit write failure never blocks credential resolution
# ---------------------------------------------------------------------------


async def test_audit_write_failure_never_blocks_credential_resolution(
    db_session: AsyncSession,
    platform_resolver: FakePlatformCredentialResolver,
    minimax_credential: Any,
    failing_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    from gateway.ops.api.deps import resolve_platform_credential
    from gateway.proxy.domain.credential_context import (
        get_provider_credential,
        reset_provider_credential,
    )

    platform_id = await seed_platform_tenant(db_session)
    platform_resolver.program(platform_id, "minimax", minimax_credential)

    with caplog.at_level(logging.WARNING):
        token = await resolve_platform_credential(
            platform_resolver, db_session, "minimax", failing_session_factory
        )

        assert token is not None, (
            "resolution must succeed identically to the healthy-audit-DB case"
        )
        try:
            assert get_provider_credential() is minimax_credential
        finally:
            reset_provider_credential(token)  # type: ignore[arg-type]

        await asyncio.sleep(0.05)  # let the fire-and-forget (failing) audit write complete

    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 0, (
        "the failing write must not have persisted a row"
    )
    assert "failed to persist audit event" in caplog.text, (
        "record_audit's existing, unchanged fail-open warning must still be logged"
    )
